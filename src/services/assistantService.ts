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

const MAX_TOOL_ROUNDS = 6

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

  /** 底层 LLM 调用 (含出境审计) */
  private async callLLM(messages: ApiMessage[], tools?: any[], maxTokens = 800): Promise<any> {
    const { url, model, key } = this.engineConfig()
    if (!key && !this.engineConfig().local) {
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
      this.engineConfig().local ? 'local' : `${url.split('//')[1].split('/')[0]}`)
    if (!res.ok) {
      const t = await res.text().catch(() => '')
      throw new Error(`LLM ${res.status}: ${t.slice(0, 120)}`)
    }
    return res.json()
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

    this.svc.onMessage(async (msg) => {
      onLog?.(`[${new Date().toLocaleTimeString('zh-CN')}] ${msg.fromUserId.slice(0, 12)}…: ${msg.messageStr.slice(0, 40)}`)
      try {
        const reply = await this.handleMessage(msg.fromUserId, msg.messageStr, msg.messageKind)
        const ok = await this.svc!.sendText(msg.fromUserId, reply)
        onLog?.(`  → ${ok ? `已回复 (${reply.length}字)` : '回复失败'}`)
      } catch (e: any) {
        onLog?.(`  → 处理异常: ${e.message}`)
        appendLog(`[${new Date().toLocaleTimeString('zh-CN')}] 异常: ${e.message}`)
      }
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
