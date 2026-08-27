/**
 * 第二大脑助手 — 持久化 Agent (主流框架标准架构)。
 *
 * 架构 (参考 LangGraph Agent Loop + Mem0 三层记忆):
 *   微信(iLink) ⇄ 常驻进程
 *     ├─ ReAct 循环: LLM 决策 → 本地工具执行 → 观察回填 → 直到产出回答
 *     ├─ 三层记忆: L1 工作窗口 / L2 滚动摘要 / L3 长期事实 (本地 JSON)
 *     └─ 隐私关卡: 工具结果脱敏后才出境到云端 LLM (本地引擎则完全不出境)
 */
import { WechatMessageService } from './wechatMessageService.js'
import { configService } from './configService.js'
import { AssistantMemory, type ChatTurn } from './assistantMemory.js'
import { privacyGate } from './assistantPrivacy.js'
import { TOOL_DEFS, executeTool } from './assistantTools.js'
import { appendLog } from './assistantDaemon.js'
import type { Message } from '../types.js'
import { buildEvidenceReviewInput } from './evidenceService.js'

const MAX_TOOL_ROUNDS = 6
/** 每日 LLM 处理上限 (护栏: 防 bug 死循环/异常流量烧钱; 0 = 不限制) */
const DAILY_LIMIT = 100

const BASE_PROMPT = `你是"第二大脑", 运行在用户自己的电脑上, 通过微信与用户对话。
你可以调用工具查询用户本地微信数据(会话/聊天记录/收藏), 以及读写关于用户的长期记忆。

行为准则:
- 涉及用户数据的问题, 先调工具查证再回答, 不要编造
- 用户让你记住某事时, 用 save_memory 工具保存
- 回复用微信聊天风格, 简洁, 不用 markdown 符号
- 数据不足时直说需要什么, 不要瞎猜
- 回复控制在 300 字内, 列表类可放宽`

interface ApiMessage {
  role: 'system' | 'user' | 'assistant' | 'tool'
  content: string
  tool_calls?: any[]
  tool_call_id?: string
}

export class AssistantService {
  private svc: WechatMessageService | null = null
  private memory = new AssistantMemory()
  private running = false
  /** 消息串行队列: 保证 handleMessage 不并发交错 (记忆窗口一致性) */
  private queue: Promise<void> = Promise.resolve()
  /** 每日用量计数 (内存态, 重启重置 — 配额护栏防烧钱, 无需持久精确) */
  private dailyCount = 0
  private dailyDate = new Date().toDateString()

  private engineConfig(): { url: string; model: string; key: string | null; local: boolean } {
    const engine = String(configService.get('aiEngine') || 'deepseek')
    if (engine === 'ollama' || engine === 'lmstudio') {
      const port = engine === 'lmstudio' ? 1234 : 11434
      const model = String(configService.get('localModel') || 'llama3')
      return { url: `http://localhost:${port}/v1`, model, key: null, local: true }
    }
    // 自定义 OpenAI 兼容端点 (中转站) 优先
    const baseUrl = String(configService.get('aiBaseUrl') || '').trim().replace(/\/+$/, '')
    if (baseUrl) {
      const model = String(configService.get('aiModel') || 'deepseek-chat')
      return { url: baseUrl, model, key: configService.get('deepseekApiKey'), local: false }
    }
    return { url: 'https://api.deepseek.com/v1', model: 'deepseek-chat', key: configService.get('deepseekApiKey'), local: false }
  }

  /** 访问控制: 未配置 assistantWhitelist 时默认拒绝所有人 */
  private isAllowed(userId: string): boolean {
    const list = String(configService.get('assistantWhitelist') || '')
      .split(/[,;\s]+/).map(s => s.trim()).filter(Boolean)
    return list.length > 0 && list.includes(userId)
  }

  private dailyQuotaLeft(): number {
    const today = new Date().toDateString()
    if (today !== this.dailyDate) { this.dailyDate = today; this.dailyCount = 0 }
    return DAILY_LIMIT - this.dailyCount
  }

  /** 底层 LLM 调用 (含出境审计) */
  private async callLLM(messages: ApiMessage[], tools?: any[], maxTokens = 800): Promise<any> {
    const { url, model, key, local } = this.engineConfig()
    if (!key && !local) {
      throw new Error('未配置 LLM (config set deepseekApiKey 或切换 ollama)')
    }
    const body: Record<string, unknown> = { model, messages, max_tokens: maxTokens, temperature: 0.4 }
    if (tools?.length) { body.tools = tools; body.tool_choice = 'auto' }

    const res = await fetch(`${url}/chat/completions`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(key ? { Authorization: `Bearer ${key}` } : {}),
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(120_000),
    })
    privacyGate.audit('CLOUD_CALL', JSON.stringify(body).length,
      local ? 'local' : `${url.split('//')[1].split('/')[0]}`)
    if (!res.ok) {
      const t = await res.text().catch(() => '')
      throw new Error(`LLM ${res.status}: ${t.slice(0, 120)}`)
    }
    return res.json()
  }

  /** 在明确授权后分析本地聊天，输出证据线索而非法律结论。 */
  async reviewEvidence(talker: string, messages: Message[], allowCloud = false): Promise<{ text: string; localInference: boolean; redactions: number }> {
    const { local } = this.engineConfig()
    if (!local && !allowCloud) {
      throw new Error('默认禁止将聊天正文发送到云端；请切换本地模型，或明确使用 --allow-cloud')
    }
    const input = buildEvidenceReviewInput(talker, messages, privacyGate.mode(), local)
    const response = await this.callLLM([
      {
        role: 'system',
        content: `你是电子数据整理助手，不是律师。只根据提供的聊天原文进行线索整理，不判断“必然违法”、不判断法院必然采信，也不编造法律条文或事实。
请用中文输出以下结构：
## 疑似争议线索
逐项引用消息ID，区分原文事实和你的待核查推测。
## 可能涉及的法律主题
只写宽泛主题，例如合同履行、借贷、劳动争议、名誉侵权、隐私或个人信息；不作定性结论。
## 还需要核验的材料
列出原设备、完整上下文、转账/合同/平台记录、身份归属等缺口。
## 保全建议
提醒保留原始设备、原始数据、完整对话和导出记录。
如果信息不足，明确写“信息不足”，不要补全。${local ? '' : '\n当前不是本地推理：不要复述已被隐私模式屏蔽的聊天正文。'}`,
      },
      { role: 'user', content: input.transcript },
    ], undefined, 1800)
    return {
      text: (response.choices?.[0]?.message?.content || '信息不足，未生成分析。').trim(),
      localInference: local,
      redactions: input.redactions,
    }
  }

  /** 组装系统提示: 基础人格 + L2 摘要 + L3 事实 */
  private buildSystemPrompt(userId: string): string {
    const parts = [BASE_PROMPT]
    const summary = this.memory.summary(userId)
    if (summary) parts.push(`\n[此前对话摘要]\n${summary}`)
    const facts = this.memory.facts(userId)
    if (facts.length) {
      parts.push(`\n[关于用户的长期记忆]\n${facts.map(f => `· ${f.content}`).join('\n')}`)
    }
    return parts.join('\n')
  }

  /** 单条消息处理: 指令路由 → ReAct 循环 → 记忆更新 */
  async handleMessage(userId: string, text: string, kind: string): Promise<string> {
    if (kind !== 'text') return '目前只支持文字消息哦'

    const t = text.trim()

    if (t === '帮助' || t.toLowerCase() === 'help') {
      return ['🧠 第二大脑 Agent', '',
        '直接用自然语言问, 我会自动查本地数据回答:',
        '「我最近都在忙什么?」',
        '「总结我和XX的聊天」',
        '「收藏里有哪些AI文章?」',
        '「记住: 我的项目叫weflow-cli」',
        '', '记忆: 三层 (窗口/摘要/长期事实), 重启不丢',
        '隐私: 数据库不出本机, 出境内容自动脱敏',
        '', '指令: 记忆 | 清空记忆'].join('\n')
    }
    if (t === '清空记忆' || t === '重置') {
      this.memory.reset(userId)
      privacyGate.audit('MEMORY_RESET', 0, userId.slice(0, 8))
      return '✓ 对话记忆已清空, 重新开始'
    }
    if (t === '记忆') {
      const facts = this.memory.facts(userId)
      const s = this.memory.summary(userId)
      const lines = [`工作窗口: ${this.memory.workingWindow(userId).length} 条`]
      lines.push(`滚动摘要: ${s ? `${s.length}字` : '无'}`)
      lines.push(`长期事实: ${facts.length} 条`)
      if (facts.length) lines.push(facts.slice(-5).map(f => `· ${f.content}`).join('\n'))
      lines.push(`隐私模式: ${privacyGate.mode()}${privacyGate.isLocalInference() ? ' (本地推理, 不出境)' : ''}`)
      lines.push(`今日用量: ${this.dailyCount}/${DAILY_LIMIT}`)
      return lines.join('\n')
    }

    // === ReAct 主循环 ===
    this.memory.addTurn(userId, 'user', t)
    const messages: ApiMessage[] = [
      { role: 'system', content: this.buildSystemPrompt(userId) },
      ...this.memory.workingWindow(userId).map(turn => ({ role: turn.role, content: turn.content })),
    ]

    let reply = ''
    let toolCalls = 0
    try {
      for (let round = 0; round < MAX_TOOL_ROUNDS; round++) {
        const data = await this.callLLM(messages, TOOL_DEFS)
        const msg = data.choices?.[0]?.message
        if (!msg) throw new Error('LLM 返回为空')

        if (msg.tool_calls?.length) {
          messages.push({ role: 'assistant', content: msg.content || '', tool_calls: msg.tool_calls })
          for (const tc of msg.tool_calls) {
            toolCalls++
            let args: Record<string, any> = {}
            try { args = JSON.parse(tc.function?.arguments || '{}') } catch { /* 参数容错 */ }
            const raw = await executeTool(tc.function?.name || '', args, { userId, memory: this.memory })
            const { safe, redactions } = privacyGate.redact(raw)
            privacyGate.audit(`TOOL:${tc.function?.name}`, raw.length, redactions ? `redacted=${redactions}` : '')
            messages.push({ role: 'tool', tool_call_id: tc.id, content: safe })
          }
          continue
        }
        reply = (msg.content || '').trim() || '(空回复)'
        break
      }
      if (!reply) reply = '(这轮处理太复杂了, 换个问法试试?)'
    } catch (e: any) {
      reply = `❌ 大脑暂时离线: ${e.message?.slice(0, 100)}\n(本地指令仍可用: 发「帮助」)`
    }

    // === 记忆更新 (L2 压缩 + L3 提取) ===
    this.memory.addTurn(userId, 'assistant', reply)
    const llm = (msgs: ChatTurn[], maxTokens?: number) =>
      this.callLLM(msgs as ApiMessage[], undefined, maxTokens).then(
        (d: any) => d.choices?.[0]?.message?.content || '')
    try { await this.memory.compressIfNeeded(userId, llm) } catch { /* 压缩失败不阻断 */ }
    try { await this.memory.extractFactsIfNeeded(userId, llm) } catch { /* 提取失败不阻断 */ }
    this.memory.save()
    privacyGate.audit('TURN_DONE', reply.length, `tools=${toolCalls}`)
    return reply
  }

  /** 启动常驻监听 */
  async start(onLog?: (line: string) => void): Promise<void> {
    const token = configService.get('wechatOcToken')
    if (!token) throw new Error('未登录消息通道, 先运行 weflow-cli login-wechat')

    this.svc = new WechatMessageService({ token })
    this.running = true
    const { local } = this.engineConfig()
    onLog?.(`助手已启动 (bot: ${configService.get('wechatOcAccountId')}, ` +
      `引擎: ${local ? '本地' : '云端'}, 记忆用户数: ${this.memory.userCount()})`)

    this.svc.onMessage((msg) => {
      // 串行入队: 并发到达的消息按顺序处理, 记忆窗口不交错
      this.queue = this.queue.then(async () => {
        if (!this.running) return
        const uid = msg.fromUserId

        if (!this.isAllowed(uid)) {
          privacyGate.audit('DENY_NOT_WHITELISTED', 0, uid.slice(0, 12))
          onLog?.(`  → 拒绝: ${uid.slice(0, 12)}… 不在白名单 (未回复, 不耗 LLM)`)
          return
        }
        if (msg.messageKind !== 'text') {
          onLog?.(`  → 忽略非文本消息 (${msg.messageKind})`)
          return
        }
        if (this.dailyQuotaLeft() <= 0) {
          privacyGate.audit('DENY_DAILY_LIMIT', this.dailyCount)
          appendLog(`[配额] 今日 ${DAILY_LIMIT} 条上限已用尽, 拒绝: ${msg.messageStr.slice(0, 30)}`)
          await this.svc!.sendText(uid, '今日额度已用完, 明天再来吧').catch(() => {})
          return
        }

        onLog?.(`[${new Date().toLocaleTimeString('zh-CN')}] ${uid.slice(0, 12)}…: ${msg.messageStr.slice(0, 40)}`)
        try {
          const reply = await this.handleMessage(uid, msg.messageStr, msg.messageKind)
          this.dailyCount++
          const ok = await this.svc!.sendText(uid, reply)
          onLog?.(`  → ${ok ? `已回复 (${reply.length}字, 今日 ${this.dailyCount}/${DAILY_LIMIT})` : '回复失败'}`)
        } catch (e: any) {
          onLog?.(`  → 处理异常: ${e.message}`)
          appendLog(`[${new Date().toLocaleTimeString('zh-CN')}] 异常: ${e.message}`)
        }
      }).catch((e: any) => appendLog(`队列异常: ${e.message}`))
    })

    await this.svc.startPolling()
  }

  stop(): void {
    this.running = false
    this.svc?.stop()
  }

  isRunning(): boolean { return this.running }
  memoryUserCount(): number { return this.memory.userCount() }
}
