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
import { decideRoute, MIN_NEEDS_TOOL, type FastRouteMode } from './assistantRouter.js'
import { selectFactsForInjection, frameLocalData } from './assistantMemory.js'
import { configService } from './configService.js'
import { AssistantMemory, type ChatTurn } from './assistantMemory.js'
import { privacyGate } from './assistantPrivacy.js'
import { TOOL_DEFS, availableToolDefs, unavailableToolReason, executeTool } from './assistantTools.js'
import type { AttachedImage, ToolContext } from './assistantTools.js'
import { producedContent } from './assistantTools.js'
import { recordTurn, describeForChat, summarizeArgs, clipReasoning } from './assistantTrace.js'
import type { StopReason, TurnTrace } from './assistantTrace.js'
import { appendLog } from './assistantDaemon.js'
import type { Message, WechatInboundMessage } from '../types.js'
import { buildEvidenceReviewInput } from './evidenceService.js'
import { evaluateAssistantAccess } from './assistantRouting.js'
import { nowLine } from '../utils/dateRange.js'
import { resolvePanelUserId, PANEL_FALLBACK_BUCKET } from '../panel/userId.js'
import { startPanelServer, type PanelServer } from '../panel/server.js'
import { clearEndpoint } from '../panel/endpoint.js'

const MAX_TOOL_ROUNDS = 6
/** 每日 LLM 处理上限 (护栏: 防 bug 死循环/异常流量烧钱; 0 = 不限制) */
const DAILY_LIMIT = 100

const SEP = String.fromCharCode(10)   // 提示词里的换行。写成常量，省得在每种写入路径上各自操心转义

/**
 * 消息通道。**不是新抽象，是把既有的隐式契约写出来**——`AssistantService` 一直只用
 * `WechatMessageService` 的这四个方法，测试也是靠替换这四个方法驱动整条循环的
 * （`test/assistant-loop.test.ts` 的 `installFakeChannel`）。写成接口是为了让"通道可以不是微信"
 * 这件事在类型上成立，而不是靠"反正 `svc` 可空"。
 *
 * `stop` 写成 `Promise<void> | void`：`WechatMessageService.stop()` 返回 Promise，
 * 而 `AssistantService.stop()` 并不 await 它（`:668`），两边都收。
 */
export interface AssistantChannel {
  onMessage(cb: (msg: WechatInboundMessage) => void): void
  startPolling(): Promise<void>
  sendText(conversationId: string, text: string): Promise<boolean>
  stop(): Promise<void> | void
  /**
   * 服务端是否已判这个 token 失效。**可选**：它是"通道实况"的一部分，而通道是可以被替身实现的
   * （测试里的假通道、将来的别的入口），没这个方法的通道一律当成没过期。
   */
  isTokenExpired?(): boolean
}

/** 一轮从哪来。**决定要不要过白名单**，见 `runTurn` */
export type TurnOrigin = 'wechat' | 'panel'

export interface TurnInput {
  sessionId: string
  text: string
  kind: string
  origin: TurnOrigin
  /** 微信来源才有：白名单判定要它的 senderId / conversationType / mentionedBot */
  message?: WechatInboundMessage
}

/**
 * 一轮的结果。**"回什么"与"往哪回"分开**：这里只说回什么，往哪回由调用方的 `deliver` 决定。
 *
 * 这么分的直接原因是修一个真 bug：泵里原先是 `this.svc!.sendText(...)`，而 `svc` 在没有
 * 微信通道时是 `null`——那个 `!` 只是给 TS 看的。于是"配额用尽"那一次会抛 TypeError，
 * 调用方永远收不到响应（挂到超时）。任何"没有通道的宿主"都会踩到。
 */
export type TurnOutcome =
  | { status: 'replied'; text: string }
  /** 微信来源且不在白名单里。**故意不回复**（回一句就等于告诉陌生人这号是活的） */
  | { status: 'denied'; reason: string }
  | { status: 'ignored'; reason: string }
  | { status: 'quota-exceeded'; text: string }
  | { status: 'not-running' }
  | { status: 'error'; message: string }

const BASE_PROMPT = `你是"第二大脑", 运行在用户自己的电脑上, 通过微信或本机面板与用户对话。
你可以调用工具查询用户本地微信数据(会话/聊天记录/收藏), 以及读写关于用户的长期记忆。

行为准则:
- 涉及用户数据的问题, 先调工具查证再回答, 不要编造
- 用户让你记住某事时, 用 save_memory 工具保存
- 回复用微信聊天风格, 简洁, 不用 markdown 符号
- 数据不足时直说需要什么, 不要瞎猜
- 回复控制在 300 字内, 列表类可放宽
- **必须真的调用过工具之后**才能对结果下结论。没调工具就说「内容被屏蔽了」「我查到了」
  都是编造, 审计里会留下 tools=0。
- 工具**失败**时(结果以括号起头, 如「(起草没跑通: …)」), 只许转述工具给出的那个原因。
  工具没写原因、或只给了退出码, 就直说「工具没给出原因」并给下一步(重试一次 / 换个问法),
  **不许替它推测**一个听起来合理的原因——「大概是模型判断不一致」「可能是网络」这类
  都是编的, 用户会当成事实。也不许把工具失败说成"查不到"、把"查不到"说成工具失败。`

interface ApiMessage {
  role: 'system' | 'user' | 'assistant' | 'tool'
  content: string
  tool_calls?: any[]
  tool_call_id?: string
  /** 多模态附件：只会由 `look_at_image` 产生，展开见 `toApiMessages` */
  images?: AttachedImage[]
}

/**
 * 把内部消息展开成请求要的形状——**图片只在这一处**变成多模态数组。
 *
 * 其余所有环节（记忆、脱敏、审计、配额）继续按文本 `content` 工作，所以加图片没有把
 * `ApiMessage.content` 变成一个到处都要判类型的联合体。
 *
 * `allowImages` 为假时图片**不发**，并在正文里留下一句说明：静默少发一张图，和"模型
 * 看不见"在下游无法区分——这是这个仓库反复踩过的那类坑。
 *
 * 纯函数，不读配置。是否允许由调用方（`callLLM`）按隐私模式判定。
 */
export function toApiMessages(messages: ApiMessage[], allowImages: boolean):
  { messages: ApiMessage[]; imagesSent: number; imagesDropped: number } {
  let imagesSent = 0
  let imagesDropped = 0

  const expanded = messages.map(m => {
    const images = m.images ?? []
    if (!images.length) return m

    if (!allowImages) {
      imagesDropped += images.length
      return { ...m, images: undefined,
        content: `${m.content}\n（另有 ${images.length} 张图片未随本轮发出：当前隐私模式不允许图片出境）` }
    }

    imagesSent += images.length
    return {
      ...m,
      images: undefined,
      content: [
        { type: 'text', text: m.content },
        ...images.map(img => ({
          type: 'image_url',
          image_url: { url: `data:${img.mime};base64,${img.b64}` },
        })),
      ] as unknown as string,
    }
  })

  return { messages: expanded, imagesSent, imagesDropped }
}

export class AssistantService {
  constructor(options: { routeDecider?: (request: unknown) => Promise<any> } = {}) {
    this.routeDecider = options.routeDecider
  }

  private svc: AssistantChannel | null = null
  private memory = new AssistantMemory()
  private running = false
  /** 消息串行队列: 保证 handleMessage 不并发交错 (记忆窗口一致性) */
  private queue: Promise<void> = Promise.resolve()
  /** 每日用量计数 (内存态, 重启重置 — 配额护栏防烧钱, 无需持久精确) */
  private dailyCount = 0
  private dailyDate = new Date().toDateString()
  /** 快路径的判断调用。留出注入点：测试用假判断层驱动，不联网、不 spawn Python */
  private routeDecider: ((request: unknown) => Promise<any>) | undefined
  /**
   * `handleMessage` 的重入护栏。
   *
   * `turnCalls` / `lastReasoning` / `lastNeedsLocalData` 是**实例字段**，它们的正确性只依赖
   * "handleMessage 被串行调用"这个不变式——而那个不变式今天只写在注释里。第二个入口
   * （本机面板）一出现，早晚有人想加一条直连的快速路径。重入就直接抛，
   * 把"记忆窗口悄悄错乱"换成一声巨响。
   */
  private inFlight = false
  /** `start()` 给的日志出口。本机入口不经过那个调用点传参，所以存一份 */
  private logSink: ((line: string) => void) | undefined
  /** 本机入口的回环端点（见 src/panel/server.ts 的信任边界） */
  private panelServer: PanelServer | null = null
  /** 本机入口用哪个记忆桶。与微信直聊同一个 id 时才是"共用一个大脑" */
  private memoryBucket: string = PANEL_FALLBACK_BUCKET

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

  /**
   * Direct messages require an explicit sender allowlist. Groups are disabled
   * until the upstream provides an explicit group ID, and then require all
   * three controls: group allowlist, sender allowlist, and an @ mention.
   */
  private accessDecision(message: WechatInboundMessage): ReturnType<typeof evaluateAssistantAccess> {
    return evaluateAssistantAccess(message, {
      directWhitelist: String(configService.get('assistantWhitelist') || ''),
      groupWhitelist: String(configService.get('assistantGroupWhitelist') || ''),
      requireGroupMention: configService.get('assistantGroupRequireMention') !== 'false',
    })
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
    // 图片出境与正文出境不是一回事：strict 模式（第三方聊天正文不出境）下图片一律不发。
    // `look_at_image` 自己也会拒绝取图，但"工具肯给"与"这道门肯放"是两件事——漏一层就是
    // 聊天图片出境。本地引擎不出机器，不受这条限制。
    const allowImages = privacyGate.mode() !== 'strict' || privacyGate.isLocalInference()
    const { messages: expanded, imagesSent, imagesDropped } = toApiMessages(messages, allowImages)
    // `images` 不是 API 的字段，展平后要去掉；其余字段原样透传。
    const payload = expanded.map(({ images, ...rest }) => rest)

    const body: Record<string, unknown> = { model, messages: payload, max_tokens: maxTokens, temperature: 0.4 }
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
    // 只记"真的出机器"的那种。本地引擎收图片时没有东西离开本机，记成 SENT 是错的说法。
    if (!local && imagesSent) {
      const bytes = messages.reduce((n, m) => n + (m.images ?? []).reduce((b, i) => b + i.b64.length, 0), 0)
      const ids = messages.flatMap(m => (m.images ?? []).map(i => i.localId)).filter(Boolean).join(',')
      privacyGate.audit('IMAGE_SENT', bytes, `n=${imagesSent}` + (ids ? ` ids=${ids}` : ''))
    }
    if (imagesDropped) privacyGate.audit('IMAGE_HELD', imagesDropped, 'strict')
    if (!res.ok) {
      const t = await res.text().catch(() => '')
      throw new Error(`LLM ${res.status}: ${t.slice(0, 120)}`)
    }
    // 推理内容（如果这个模型给的话）单独挂在 message.reasoning_content 上，不混进 content。
    // **只在这里接住**：不把它塞回对话（那是模型的独白，不是给用户的内容），只留给轨迹。
    const envelope: any = await res.json()
    const reasoning = envelope?.choices?.[0]?.message?.reasoning_content
    this.lastReasoning = typeof reasoning === 'string' ? reasoning : ''
    return envelope
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

  /** 当前的隐私状态，作为**事实**写进系统提示。
   *
   *  曾经这里是一段写死的假设句（「若因严格模式读不到正文…」），实测撞了车：模型把它当成
   *  当前状态，于是**一次工具都没调**就回用户"我调了工具，但内容被严格模式挡掉了"——
   *  审计里 `tools=0`，是编造。现在按实际档位说，且与"没调工具不许下结论"那条配套。
   */
  private privacyStateLine(): string {
    const mode = privacyGate.mode()
    const local = privacyGate.isLocalInference()
    if (local) {
      return '[隐私] 本地推理：数据不出这台机器，工具结果里的聊天正文是原文，可以如实引用。'
    }
    if (mode === 'strict') {
      return '[隐私] 当前 strict 模式：第三方聊天正文会以「[内容N字已按严格模式屏蔽]」的形式出现在'
        + '工具结果里，那时你只能看到时间与字数。遇到这种情况如实说明，并给出三条路：'
        + '①改 balanced（正文发给当前模型，电话/证件/邮箱/密钥/链接仍打码）；'
        + '②改用本地模型 aiEngine=ollama/lmstudio（数据不出机器，屏蔽自动不生效）；③保持现状。'
    }
    return `[隐私] 当前 ${mode} 模式：工具结果里的聊天正文是原文（电话/证件/邮箱/密钥/链接已打成`
      + '占位符），可以直接引用，不必声称被屏蔽。'
  }

  /** 组装系统提示: 基础人格 + 隐私状态 + L2 摘要 + L3 事实。
   *
   *  两个纪律：
   *  1. **事实按相关度取一部分**，而不是 30 条全塞——无关的那些是噪声，不只是花钱；
   *  2. 本地数据（摘要、事实）一律**加帧 + 转义框标签**：它们的内容里完全可能写着
   *     `</weflow-local-data>` 再跟一段像系统指令的话，不转义就等于让数据自己把框关上。
   */
  private buildSystemPrompt(userId: string, question = ''): string {
    // 当前时间：**没有它，任何相对时间都是猜**。"上周三""昨天""这周"要变成工具能用的
    // 日期，模型得先知道今天是几号（`get_messages` 的 since/until 就是这么用的）。
    const parts = [BASE_PROMPT, `[当前时间] ${nowLine()}（本机时区）`, this.privacyStateLine()]
    const summary = this.memory.summary(userId)
    if (summary) {
      parts.push('[此前对话摘要]' + SEP + frameLocalData('memory.summary', summary))
    }
    const { selected, withheld } = selectFactsForInjection(this.memory.facts(userId), question)
    if (selected.length) {
      const lines = selected.map(f => `· ${f.content}`).join(SEP)
      // 少给了几条要**如实说**：谎报「以下是全部记忆」比少给更糟——模型会以为自己看到了全部。
      const note = withheld > 0 ? `${SEP}（另有 ${withheld} 条与这次问题关系较远，未列出）` : ''
      parts.push('[关于用户的长期记忆]' + SEP + frameLocalData('memory.facts', lines + note))
    }
    return parts.join(SEP)
  }

  /** 白名单为空的首次配置提示是否已经打过了（只打一次，别把日志刷满） */
  private firstRunHintShown = false
  /** 最近一次路由认为「这条消息需要查本机数据」的概率（0 = 没问过） */
  private lastNeedsLocalData = 0
  /** 最近一次模型调用里**真返回**的推理内容（`reasoning_content`）。默认模型不返回，所以常是空串 */
  private lastReasoning = ''
  /** 每个用户最近一轮的轨迹：微信里发「轨迹」看的就是它（跨进程的历史在文件里，见 assistantTrace） */
  private traces = new Map<string, TurnTrace>()
  /**
   * 本轮已经调过的「工具名 + 参数」。同一条消息里重复调用**同样的参数**不会得到新信息，
   * 而实测（评测的 ambiguous-contact 用例）模型会连着调三次 list_sessions。
   * 消息是串行处理的（queue），所以实例字段在这里是安全的。
   */
  private turnCalls = new Set<string>()

  /** 白名单为空时，把"该把谁加进去"连同**完整**的发送者 ID 打一行。
   *
   *  为什么可以打完整的 ID：填白名单用的就是这个值，而它只在入站消息里出现——登录响应给的
   *  `ilink_user_id` 与它是不是同一个值**没有验证过**，所以不能拿登录来猜（猜错的后果是
   *  "白名单非空、看着配好了、却仍然拒你"）。启发式在这里正好用得上：真值自己会来。
   *
   *  三条自我约束：白名单非空时不提示（那时人已经配过了）、只对**直聊**提示（群里的
   *  sender_id 是群成员，把它加进白名单是错的）、只提示一次。
   *  `assistant log --json` 照旧只回元数据，不返回日志内容。
   */
  private maybeAnnounceWhitelistBootstrap(access: { reason: string }, msg: WechatInboundMessage,
                                          onLog?: (line: string) => void): void {
    if (this.firstRunHintShown) return
    if (String(configService.get('assistantWhitelist') || '').trim()) return
    if (access.reason !== 'direct-not-whitelisted') return
    this.firstRunHintShown = true
    const line = '[首次配置] 白名单为空，所以谁都没回。若刚才这条是你本人发的，执行：'
      + ` weflow-cli config set assistantWhitelist "${msg.senderId}"`
    onLog?.(line)
    appendLog(line)
    privacyGate.audit('WHITELIST_BOOTSTRAP_HINT', 0, 'whitelist-empty')
  }

  /** 快路径开关。默认 `off`：不改行为，直到有人愿意盯着它（D-035） */
  private fastRouteMode(): FastRouteMode {
    const raw = String(configService.get('assistantFastRoute') || '').trim().toLowerCase()
    return raw === 'on' || raw === 'log' ? raw : 'off'
  }

  /** ReAct 主循环：一轮一轮问模型，直到它给出不带工具调用的答复。
   *
   *  抽成方法是因为守卫（见 handleMessage）要在"顶回去一次"之后**再跑一遍同一条循环**——
   *  两处各写一份，早晚有一处会漏掉既有的容错（参数解析、工具审计、轮数上限）。
   */
  private async runReactLoop(userId: string, messages: ApiMessage[],
                             trace: TurnTrace): Promise<{ reply: string; toolCalls: number }> {
    let reply = ''
    let toolCalls = 0
    for (let round = 0; round < MAX_TOOL_ROUNDS; round++) {
      // 只摆这台机器**跑得了**的工具：摆了跑不了的，模型会去试、拿一个错回来
      const data = await this.callLLM(messages, availableToolDefs())
      const msg = data.choices?.[0]?.message
      if (!msg) throw new Error('LLM 返回为空')
      trace.rounds += 1
      // 模型这次真返回了推理内容的话，留一份（截断）。当前默认模型不返回，那就是空。
      if (this.lastReasoning) {
        trace.reasoningChars += this.lastReasoning.length
        if (!trace.reasoning) trace.reasoning = clipReasoning(this.lastReasoning)
      }

      if (msg.tool_calls?.length) {
        messages.push({ role: 'assistant', content: msg.content || '', tool_calls: msg.tool_calls })
        for (const tc of msg.tool_calls) {
          toolCalls++
          let args: Record<string, any> = {}
          try { args = JSON.parse(tc.function?.arguments || '{}') } catch { /* 参数容错 */ }
          await this.runToolCall(userId, messages, tc.id, tc.function?.name || '', args, trace)
        }
        continue
      }
      reply = (msg.content || '').trim() || '(空回复)'
      trace.steps.push({ kind: 'note', detail: `第 ${round + 1} 轮往返后给出答复` })
      break
    }
    if (!reply) {
      reply = '(这轮处理太复杂了, 换个问法试试?)'
      // 别把它记成"答完了"：这是撞上限，不是回答
      trace.stop = 'rounds-exhausted'
    }
    return { reply, toolCalls }
  }

  /** 执行一次工具调用：脱敏、审计、把结果塞回对话。
   *
   *  快路径与 ReAct 循环**共用这一份**：D-035 里那条不可回归的安全属性是「没有审计行就不得
   *  派发工具」，两条路各写一遍，早晚有一条会漏。
   */
  private async runToolCall(userId: string, messages: ApiMessage[], callId: string,
                            name: string, args: Record<string, any>,
                            trace: TurnTrace): Promise<void> {
    const callKey = `${name}:${JSON.stringify(args ?? {})}`
    if (this.turnCalls.has(callKey)) {
      // 同样的参数再来一次不会得到新东西。**顶回去说清楚**，而不是默默重跑一遍——
      // 实测模型会连着调三次 list_sessions，既慢又把它自己的上下文刷满。
      trace.steps.push({ kind: 'note', detail: `重复调用 ${name}（参数相同）→ 跳过` })
      messages.push({ role: 'tool', tool_call_id: callId,
        content: `(这一步与前面某次调用完全相同，结果不会变。结果已经在上面的对话里，`
          + `直接用它回答，不要再重复调用同一个工具。)` })
      return
    }
    this.turnCalls.add(callKey)
    const ctx: ToolContext = { userId, memory: this.memory }
    const raw = await executeTool(name, args, ctx)
    trace.toolCalls += 1
    trace.steps.push({
      kind: 'tool', name, args: summarizeArgs(args), bytes: raw.length,
      produced: producedContent(raw),
    })
    const { safe, redactions } = privacyGate.redact(raw)
    privacyGate.audit(`TOOL:${name}`, raw.length, redactions ? `redacted=${redactions}` : '')
    messages.push({ role: 'tool', tool_call_id: callId, content: safe })

    // 工具取来的图片作为**紧随其后的一条 user 消息**附上：`tool` 消息的 content 只能是
    // 字符串（OpenAI 的形状），塞不进多模态数组。图片本身不过 redact（它不是文本），
    // 出不出境由 `toApiMessages` 按隐私模式判定——那是唯一一道门。
    if (ctx.pendingImages?.length) {
      const n = ctx.pendingImages.length
      messages.push({
        role: 'user',
        content: `（这是你刚取来的图片${n > 1 ? `，共 ${n} 张` : ''}）`,
        images: ctx.pendingImages,
      })
    }
  }

  /** 快路径：先问一次「该查哪个能力」，把这一个工具执行掉，于是循环第一轮就看得到结果。
   *
   *  返回派发了几个工具（0 = 回退）。**任何不确定都回退**，而回退就是原样跑循环——不是
   *  「另一个更差的兜底」。`log` 模式下只算不派发，用来在真实流量上观察它本来会怎么走。
   */
  private async maybeFastRoute(userId: string, text: string, messages: ApiMessage[],
                               trace: TurnTrace): Promise<number> {
    const mode = this.fastRouteMode()
    if (mode === 'off') return 0

    let decision
    try {
      decision = await decideRoute(text, { runDecide: this.routeDecider })
    } catch (error: any) {
      appendLog(`[快路径] 路由异常，按原样回退: ${error?.message ?? error}`)
      trace.steps.push({ kind: 'route', detail: '判断层异常，直接走循环' })
      return 0
    }

    this.lastNeedsLocalData = decision.needsTool
    if (!decision.capability) {
      appendLog(`[快路径] 回退: ${decision.reason}`)
      privacyGate.audit('FASTROUTE_SKIP', 0, decision.reason.slice(0, 120))
      trace.steps.push({ kind: 'route', detail: `判断层: ${decision.reason}` })
      return 0
    }

    if (mode === 'log') {
      // 灰度期：只记「本来会走哪条」，行为一个字不改
      appendLog(`[快路径/只记] ${decision.reason}`)
      privacyGate.audit('FASTROUTE_WOULD', 0, `${decision.capability.name} ${decision.model}`)
      trace.steps.push({ kind: 'route', detail: `判断层（只记，未派发）: ${decision.reason}` })
      return 0
    }
    trace.steps.push({ kind: 'route', detail: `判断层: ${decision.reason}` })
    // 快路径是**直接派发**的（不经工具表），所以这里要自己过同一道判据
    const blocked = unavailableToolReason(decision.capability.tool)
    if (blocked) {
      trace.steps.push({ kind: 'route', detail: `快路径想派发 ${decision.capability.tool}，但${blocked}；回退成循环` })
      return 0
    }

    const callId = `fastroute-${Date.now()}`
    appendLog(`[快路径] ${decision.reason}`)
    privacyGate.audit('FASTROUTE_HIT', 0, `${decision.capability.name} ${decision.model}`)
    messages.push({
      role: 'assistant',
      content: '',
      tool_calls: [{
        id: callId,
        type: 'function',
        function: {
          name: decision.capability.tool,
          arguments: JSON.stringify(decision.capability.args),
        },
      }],
    })
    await this.runToolCall(userId, messages, callId, decision.capability.tool,
                           decision.capability.args, trace)
    return 1
  }

  /**
   * 单条消息处理: 指令路由 → ReAct 循环 → 记忆更新。
   *
   * **外部只许经由 `enqueueTurn` 调用**（它保证串行）；直接调会被 `inFlight` 挡下——
   * 见那个字段的注释。
   */
  async handleMessage(userId: string, text: string, kind: string): Promise<string> {
    if (this.inFlight) {
      throw new Error('handleMessage 重入：它依赖串行调用（记忆窗口、turnCalls 都是实例字段）')
    }
    this.inFlight = true
    try {
      return await this.handleMessageInner(userId, text, kind)
    } finally {
      this.inFlight = false
    }
  }

  private async handleMessageInner(userId: string, text: string, kind: string): Promise<string> {
    if (kind !== 'text') return '目前只支持文字消息哦'

    const t = text.trim()
    const startedAt = Date.now()
    this.turnCalls.clear()
    // 一轮的轨迹：判断了什么、调了什么、几轮、为什么停。**内置指令也要记**——
    // 不然"我发了「轨迹」却看不到刚才那一轮"这种事自己就会发生（它本身也是一轮）。
    const trace: TurnTrace = {
      at: new Date(startedAt).toISOString(), userId, questionChars: t.length, steps: [],
      rounds: 0, toolCalls: 0, reasoning: '', reasoningChars: 0, answerChars: 0,
      stop: 'answered', elapsedMs: 0,
    }
    // `stop` 不给就保留循环/异常那边已经定好的结束原因（主路径走这里收尾）。
    const finish = (reply: string, stop?: StopReason): string => {
      trace.answerChars = reply.length
      trace.elapsedMs = Date.now() - startedAt
      if (stop) trace.stop = stop
      // 内置指令**不占**内存里那一份"上一轮"：`轨迹` 本身就是一轮，占了它之后连着问两次
      // 第二次就什么都看不到了。文件里照样留档（每轮都记）。
      if (trace.stop !== 'builtin') this.traces.set(userId, trace)
      recordTurn(trace)
      return reply
    }

    if (t === '帮助' || t.toLowerCase() === 'help') {
      return ['🧠 第二大脑 Agent', '',
        '直接用自然语言问, 我会自动查本地数据回答:',
        '「我最近都在忙什么?」',
        '「总结我和XX的聊天」',
        '「收藏里有哪些AI文章?」',
        '「我最近都在读什么?」',
        '「记住: 我的项目叫weflow-cli」',
        '', '记忆: 三层 (窗口/摘要/长期事实), 重启不丢',
        '隐私: 数据库不出本机, 出境内容自动脱敏',
        '', '指令: 记忆 | 隐私 | 清空记忆 | 轨迹'].join('\n')
    }
    if (t === '轨迹' || t === '过程' || t === '思考过程') {
      // 上一轮**怎么走到那个答复的**。看的是本地那份轨迹，不是模型的自述——
      // 模型说"我查了"与它真的查了，是两件事（审计里那条 TOOL_GUARD_PUSHBACK 就是这么来的）。
      return finish(describeForChat(this.traces.get(userId)), 'builtin')
    }
    if (t === '清空记忆' || t === '重置') {
      this.memory.reset(userId)
      privacyGate.audit('MEMORY_RESET', 0, userId.slice(0, 8))
      return finish('✓ 对话记忆已清空, 重新开始', 'builtin')
    }
    if (t === '隐私' || t === 'privacy') {
      // 只读：把当前档位和改法说清楚。**不让一条微信消息直接改隐私档位**——
      // 那等于把隐私开关搬进对话里，而配置本来就是用户在自己电脑上显式设定的东西。
      const mode = privacyGate.mode()
      const local = privacyGate.isLocalInference()
      const lines = [`隐私模式: ${mode}${local ? '(本地推理, 数据不出机器)' : '(云端推理)'}`]
      lines.push(`工具拿到的聊天正文: ${local ? '原文(不出机器)'
        : mode === 'strict' ? '被屏蔽, 只剩时间与字数'
        : mode === 'open' ? '原文, 不脱敏'
        : '原文, 但电话/证件/邮箱/密钥/链接会打码'}`)
      lines.push('在电脑上改(改完要重启助手):')
      lines.push('weflow-cli config set assistantPrivacy balanced   # 正文出境, PII 打码')
      lines.push('weflow-cli config set assistantPrivacy open       # 全不打码')
      lines.push('weflow-cli config set assistantPrivacy strict     # 正文不出境')
      if (!local) {
        lines.push('或者换本地模型, 内容根本不出机器:')
        lines.push('weflow-cli config set aiEngine ollama')
      }
      return finish(lines.join(String.fromCharCode(10)), 'builtin')
    }
    if (t === '记忆') {
      const facts = this.memory.facts(userId)
      const s = this.memory.summary(userId)
      const lines = [`工作窗口: ${this.memory.workingWindow(userId).length} 条`]
      lines.push(`滚动摘要: ${s ? `${s.length}字` : '无'}`)
      lines.push(`长期事实: ${facts.length} 条`)
      if (facts.length) lines.push(facts.slice(-5).map(f => `· ${f.content}`).join('\n'))
      lines.push(`隐私模式: ${privacyGate.mode()}${privacyGate.isLocalInference() ? ' (本地推理, 不出境)' : ''}`)
      // 落盘失败要说出来：用户在这里问"你记住了什么"，如果上一次根本没存上，
      // 那份答案就是假的。`save()` 不抛异常是刻意的，代价是必须有地方把它讲出来。
      if (this.memory.lastSaveError) {
        lines.push(`⚠ 上次保存失败: ${this.memory.lastSaveError}`)
      }
      lines.push(`今日用量: ${this.dailyCount}/${DAILY_LIMIT}`)
      return lines.join('\n')
    }

    // === ReAct 主循环 ===
    this.memory.addTurn(userId, 'user', t)
    const messages: ApiMessage[] = [
      { role: 'system', content: this.buildSystemPrompt(userId, t) },
      ...this.memory.workingWindow(userId).map(turn => ({ role: turn.role, content: turn.content })),
    ]

    let reply = ''
    let toolCalls = 0
    try {
      toolCalls += await this.maybeFastRoute(userId, t, messages, trace)
      const first = await this.runReactLoop(userId, messages, trace)
      reply = first.reply
      toolCalls += first.toolCalls

      // 守卫：路由说「这条消息需要查本机数据」，而这一轮**一次工具都没调** —— 两者自相矛盾。
      // 实测撞到过两次：它回"我确实调了工具查了"，而审计里 `tools=0`、没有任何 TOOL: 行。
      // 这里不去解释原因，只把矛盾顶回去**一次**：让模型看见"你手上没有工具结果"再答。
      // 只在路由判过"需要查本机数据"时触发（那时才有这个信号），且补问失败不许把原答复弄丢。
      if (toolCalls === 0 && this.lastNeedsLocalData >= MIN_NEEDS_TOOL) {
        privacyGate.audit('TOOL_GUARD_PUSHBACK', 0,
          `needs_local_data=${this.lastNeedsLocalData.toFixed(2)}`)
        appendLog('[守卫] 路由说需要查本机数据，但这一轮没调任何工具；顶回去一次')
        try {
          messages.push({
            role: 'system',
            content: '注意：你刚才的回答没有调用任何工具，因此你手上并没有本机数据。'
              + '这个问题需要本机的真实数据。请现在就调用合适的工具；'
              + '若确实查不到，说明你调用了哪个工具、它返回了什么。',
          })
          trace.steps.push({ kind: 'note', detail: '守卫：路由说需要本机数据而这一轮没调工具，顶回去一次' })
          const second = await this.runReactLoop(userId, messages, trace)
          reply = second.reply
          toolCalls += second.toolCalls
        } catch (e: any) {
          appendLog(`[守卫] 补问失败，保留原答复: ${e?.message ?? e}`)
        }
      }
    } catch (e: any) {
      reply = `❌ 大脑暂时离线: ${e.message?.slice(0, 100)}\n(本地指令仍可用: 发「帮助」)`
      trace.stop = 'llm-error'
      trace.error = String(e?.message ?? e).slice(0, 120)
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
    finish(reply)
    return reply
  }

  /**
   * 一轮的处理。**这是唯一一处**——微信通道与本机面板都走这里。
   *
   * 抽出来的理由是这个仓库付过学费的老毛病：判定逻辑两处各写一份，早晚有一处漏掉既有的容错
   * （同样的判词写在本文件 `:319` 与 `:361` 附近的注释里）。配额、忽略非文本、异常兜底
   * 这些行为**只该有一份**。
   *
   * `origin` 决定要不要过白名单：微信来的必须过；**本机入口的门是端点 token，不是白名单**。
   * 把白名单套到"坐在机器前的人"身上是错的——空名单时会把面板自己拒掉，而且
   * `maybeAnnounceWhitelistBootstrap` 还会打出一条**教用户把自己的面板 id 加进微信白名单**的错提示。
   *
   * 本方法**不负责"往哪回"**：结果交给调用方（微信是 `sendText`，面板是 HTTP 响应）。
   */
  private async runTurn(input: TurnInput, onLog?: (line: string) => void): Promise<TurnOutcome> {
    if (!this.running) return { status: 'not-running' }
    const { sessionId, text, kind, origin } = input

    if (origin === 'wechat') {
      const msg = input.message!
      const access = this.accessDecision(msg)
      if (!access.allowed) {
        privacyGate.audit(`DENY_${access.reason.toUpperCase().replace(/-/g, '_')}`, 0, sessionId.slice(0, 12))
        onLog?.(`  → 拒绝: ${sessionId.slice(0, 12)}… ${access.reason} (未回复, 不耗 LLM)`)
        this.maybeAnnounceWhitelistBootstrap(access, msg, onLog)
        return { status: 'denied', reason: access.reason }
      }
      if (kind !== 'text') {
        onLog?.(`  → 忽略非文本消息 (${kind})`)
        return { status: 'ignored', reason: 'non-text' }
      }
      onLog?.(`[${new Date().toLocaleTimeString('zh-CN')}] ${sessionId.slice(0, 12)}…: ${text.slice(0, 40)}`)
    } else {
      // 本机入口**不往日志里写内容**，这是有意的不对称：微信那边没有别的界面，
      // 日志是唯一线索；面板有界面，日志里留长度就够查问题了。别当成不一致"修"回去。
      onLog?.(`[panel] ${text.length}字`)
    }

    if (this.dailyQuotaLeft() <= 0) {
      privacyGate.audit('DENY_DAILY_LIMIT', this.dailyCount)
      appendLog(`[配额] 今日 ${DAILY_LIMIT} 条上限已用尽, 拒绝 (via=${origin})`)
      return { status: 'quota-exceeded', text: '今日额度已用完, 明天再来吧' }
    }

    try {
      const reply = await this.handleMessage(sessionId, text, kind)
      this.dailyCount++
      // **这里不报"已回复"**：回没回得出去是投递方才知道的事（微信的 sendText 会失败）。
      // 两处都报的话，微信那条路会在日志里出现两遍"已回复"，看着像处理了两条消息。
      return { status: 'replied', text: reply }
    } catch (e: any) {
      onLog?.(`  → 处理异常: ${e.message}`)
      appendLog(`[${new Date().toLocaleTimeString('zh-CN')}] 异常: ${e.message}`)
      return { status: 'error', message: String(e?.message ?? e) }
    }
  }

  /**
   * 串行入队 + 投递。**投递在队列里做**，不在队列外——微信那边"先回复完再处理下一条"
   * 是既有行为，把它挪到队列外会让两条消息的回复有乱序的可能（前一条发出慢一点就反超）。
   *
   * 返回的 Promise 在这个请求**处理且投递完**之后 resolve，调用方可以直接 await 它去写响应。
   */
  private enqueueTurn(input: TurnInput, deliver: (outcome: TurnOutcome) => Promise<void> | void,
                      onLog?: (line: string) => void): Promise<void> {
    // 没给日志出口就用 `start()` 时那个：本机入口不经过 `start()` 的调用点传参，
    // 漏了这一步会让面板那一侧**一行日志都不留**（第一版就是这样，测试抓到了）。
    const log = onLog ?? this.logSink
    this.queue = this.queue.then(async () => {
      const outcome = await this.runTurn(input, log)
      await deliver(outcome)
    }).catch((e: any) => appendLog(`队列异常: ${e.message}`))
    return this.queue
  }

  /** 启动常驻监听 */
  async start(onLog?: (line: string) => void): Promise<void> {
    const token = configService.get('wechatOcToken')
    this.logSink = onLog
    this.running = true
    if (token) {
      this.svc = new WechatMessageService({ token })
    } else {
      // **降级启动**：没有消息通道也要起得来。
      //
      // 从前这里直接抛（"未登录消息通道, 先运行 weflow-cli login-wechat"），于是"没登录微信"
      // == 助手整个起不来——本机的实地记录就是这样（docs/PROJECT_STATE.md 那条）。
      // 代价要说清：`assistant start` 从此可能"报成功但通道是空的"，所以 status 必须能把
      // 这两者分开（`messageChannelLoggedIn` 是"配了 token 吗"，它回答不了这个问题）。
      appendLog('[通道] 未登录消息通道，以本机入口模式启动')
      onLog?.('⚠ 消息通道未登录：微信里暂时说不了话；本机面板仍然可用')
    }

    // 记忆加载出过事（版本不认识 / 文件坏了）要说出来：否则用户面对一份空记忆，
    // 只会以为「它忘了我」。原文件此时已经留档，所以这句话里带着文件名。
    if (this.memory.problem) {
      appendLog(`[记忆] ${this.memory.problem}`)
      privacyGate.audit('MEMORY_LOAD_ISSUE', 0, this.memory.problem.slice(0, 80))
      onLog?.(`⚠ 记忆: ${this.memory.problem}`)
    }
    const { local } = this.engineConfig()
    // bot 那一段只在真有通道时报：没有通道还报一个 bot 账号，是让人以为它在收消息
    onLog?.('助手已启动 (' + (token ? `bot: ${configService.get('wechatOcAccountId')}, ` : '本机入口模式, ') +
      `引擎: ${local ? '本地' : '云端'}, 记忆用户数: ${this.memory.userCount()})`)

    // 本机入口：**只绑回环** + 每次启动随机口令。悬浮窗与 `weflow-cli panel` 都走它，
    // 于是两个入口落在**同一个进程**里（配额、串行队列、记忆都只有一份）。
    const bucket = resolvePanelUserId({
      whitelist: configService.get('assistantWhitelist'),
      configured: configService.get('assistantPanelUser'),
    })
    this.memoryBucket = bucket.userId
    try {
      this.panelServer = await startPanelServer({
        service: this,
        memoryBucket: this.memoryBucket,
        memoryNote: bucket.note,
        channel: token ? 'wechat' : 'local',
        onLog,
      })
      appendLog(`[本机入口] 监听 127.0.0.1:${this.panelServer.port}`
        + ` channel=${token ? 'wechat' : 'local'} bucket=${this.memoryBucket}`)
      onLog?.(`本机入口: http://127.0.0.1:${this.panelServer.port}（记忆桶 ${this.memoryBucket}）`)
    } catch (e: any) {
      // 端点起不来不该把助手一起弄死：微信那边还能用（端口被占是唯一常见的失败）
      appendLog(`[本机入口] 启动失败: ${e.message}`)
      onLog?.(`⚠ 本机入口启动失败: ${e.message}`)
    }

    if (!this.svc) return

    this.svc.onMessage((msg) => {
      // 串行入队: 并发到达的消息按顺序处理, 记忆窗口不交错
      this.enqueueTurn(
        { sessionId: msg.conversationId, text: msg.messageStr, kind: msg.messageKind, origin: 'wechat', message: msg },
        async (outcome) => {
          if (!('text' in outcome) || !outcome.text) return
          const ok = await this.svc?.sendText(msg.conversationId, outcome.text)
          if (outcome.status === 'replied') {
            onLog?.(`  → ${ok ? `已回复 (${outcome.text.length}字, 今日 ${this.dailyCount}/${DAILY_LIMIT})` : '回复失败'}`)
          } else {
            onLog?.(`  → ${ok ? '已回复额度提示' : '回复失败'}`)
          }
        },
        onLog,
      ).catch(() => { /* enqueueTurn 内部已经把异常落进日志 */ })
    })

    await this.svc.startPolling()
  }

  /** 本机入口用：投一条消息进同一条队列，返回这一轮的结果 */
  ask(sessionId: string, text: string, onLog?: (line: string) => void): Promise<TurnOutcome> {
    let outcome: TurnOutcome = { status: 'error', message: '未取得结果' }
    return this.enqueueTurn({ sessionId, text, kind: 'text', origin: 'panel' }, (o) => {
      outcome = o
      // 投递结果由这一侧报（`runTurn` 不知道投递成没成）
      if (o.status === 'replied') {
        (onLog ?? this.logSink)?.(`  → 已回复 (${o.text.length}字, 今日 ${this.dailyCount}/${DAILY_LIMIT}, via=panel)`)
      }
    }, onLog).then(() => outcome)
  }

  /** 消息通道接上了吗（未登录时是 false）。status 要靠它区分两种"没通道" */
  /**
   * 微信通道**现在**是否可用。
   *
   * 原来是 `this.svc !== null`——那只是"启动时配了 token"，于是 token 失效之后它照样报 true，
   * 面板与状态页都在说"微信 + 本机"而通道其实已经死了（用户看不到任何异常）。
   * 现在把长轮询那边的实况算进来：服务端判过期时，这里如实变 false（面板就会显示"仅本机入口"）。
   */
  isChannelActive(): boolean { return this.svc !== null && !this.svc.isTokenExpired?.() }

  /** 配额用量。**内存态**（重启归零），所以它不是"今天一共花了多少"的账本，是护栏的余量 */
  quotaState(): { used: number; limit: number } {
    this.dailyQuotaLeft()   // 先让它处理日期翻转，否则跨零点后会报昨天的数
    return { used: this.dailyCount, limit: DAILY_LIMIT }
  }

  stop(): void {
    this.running = false
    this.svc?.stop()
    // 关掉本机入口，并**主动删掉端点文件**——这一条与"崩溃时不删"是两回事：
    // 走 `stop()` 的是有序关闭，删掉能让面板立刻知道；崩溃/SIGTERM 残留的那种，
    // 由读取端探活（`readEndpoint()` 会检查 pid）兜住。
    const closing = this.panelServer
    this.panelServer = null
    if (closing) {
      clearEndpoint()
      void closing.close()
    }
  }

  /** 本机入口的实际监听端口；没起来就是 null */
  panelPort(): number | null { return this.panelServer ? this.panelServer.port : null }

  isRunning(): boolean { return this.running }
  memoryUserCount(): number { return this.memory.userCount() }
}
