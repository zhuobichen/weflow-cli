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
import { decideRoute, MIN_NEEDS_TOOL, type FastRouteMode, type RouteCapability } from './assistantRouter.js'
import { decideMultiTools } from './assistantJevMulti.js'
import { routeByIntent, routeWithArgs, routeParts, isFollowUp, type IntentHit }
  from './assistantIntent.js'
import { pushbackFor, emptyTalkPushback, asksBackData, toolFailureNudge, isEmptyToolResult, guardsEnabled,
         stateClaimPushback, impossibleOfferPushback,
         timeWindowWithoutLookup, stripImpossibleOffers, destructiveFollowUpPushback,
         stripOffersAfterDestructive, reversedPrivacyPushback, crossPeriodClaimPushback,
         unfulfilledJevSuggestionPushback }
  from './assistantGuard.js'
import { hasConcreteLocalClaim } from './assistantAttribution.js'
import { selectFactsForInjection, frameLocalData } from './assistantMemory.js'
import { configService } from './configService.js'
import { AssistantMemory, explicitRememberIntent, type ChatTurn } from './assistantMemory.js'
import { privacyGate } from './assistantPrivacy.js'
import { coerceToolArgs } from './assistantToolArgs.js'
import { resolveEngineConfig, fetchWithRetry, usageFromEnvelope, auditUsage } from './assistantEngine.js'
import { unattributedFactsPushback, capabilityGapPushback, estimateWithoutSource,
         argErrorAsAbsentPushback, fabricatedTimestampPushback } from './assistantAttribution.js'
import { TOOL_DEFS, availableToolDefs, unavailableCapabilities, unavailableToolReason,
         executeTool } from './assistantTools.js'
import type { AttachedImage, ToolContext } from './assistantTools.js'
import { producedContent } from './assistantTools.js'
import { recordTurn, describeForChat, summarizeArgs, clipReasoning } from './assistantTrace.js'
import { appendTurn as appendTranscriptTurn } from './assistantTranscript.js'
import { reconcileFacts } from './assistantReconcile.js'
import type { StopReason, TurnTrace } from './assistantTrace.js'
import { appendLog } from './assistantDaemon.js'
import type { Message, WechatInboundMessage } from '../types.js'
import { buildEvidenceReviewInput } from './evidenceService.js'
import { scanSkills, skillCatalogueLines } from './assistantSkills.js'
import { assistantScenes, parseSceneCommand, renderScene, type Scene } from './assistantScenes.js'
import { evaluateAssistantAccess } from './assistantRouting.js'
import { nowLine } from '../utils/dateRange.js'
import { resolvePanelUserId, PANEL_FALLBACK_BUCKET } from '../panel/userId.js'
import { startPanelServer, type PanelServer } from '../panel/server.js'
import { clearEndpoint } from '../panel/endpoint.js'

const MAX_TOOL_ROUNDS = 6

/**
 * `runReactLoop` 这一轮**是怎么结束的**。
 *
 * 存在的理由：`reply` 有两种来源，而调用方必须分开对待。
 *
 * - `'answered'`：模型真的给出了最终答复。
 * - `'rounds-exhausted'`：用完了 `MAX_TOOL_ROUNDS`，`reply` 是占位文案。
 *
 * 为什么不用 `ok: boolean`：布尔值会被写成 `if (ok) … else …` 两条支路，而"撞上限"
 * 和"LLM 报错"（走 catch 里的兜底文案）处理不同——前者还有一份半成品答复。
 * 合成一个布尔，下次加状态时就会悄悄丢信息；可判别联合会让编译器指着所有 switch 让你改。
 */
type ReactOutcome = 'answered' | 'rounds-exhausted'





/** 追问接续只对这些"幂等的读类检索"生效（见 `lastRetrieval` 的注释） */
const RETRIEVAL_TOOLS = new Set(['search_knowledge', 'search_chats', 'search_memory'])
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
  /** 本机入口可指定一个仅对本轮生效的场景 */
  sceneId?: string
  /** 微信来源才有：白名单判定要它的 senderId / conversationType / mentionedBot */
  message?: WechatInboundMessage
}

/**
 * 来信里**已落盘**的图片文件（`wechatMediaDownload` 的下载侧产物，D-128）。
 * 只收图片：语音/视频/文件的消费各自是新的出境决策，不搭车。没开下载或下载失败
 * （`filePath` 为空）时这里为空——那种图片消息照旧整条忽略，行为不变。
 */
function inboundImageFiles(msg?: WechatInboundMessage): Array<{ filePath: string }> {
  if (!msg?.components?.length) return []
  const out: Array<{ filePath: string }> = []
  for (const c of msg.components) {
    if (c.type === 'image' && c.filePath) out.push({ filePath: c.filePath })
  }
  return out
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
你可以调用工具查询用户本地微信数据(会话/聊天记录/收藏), 读写关于用户的长期记忆,
还能读本机文件与联网：read_document 能解析 PDF/Word/Excel/PPT/HTML 的内容,
read_file 读纯文本, web_search 搜实时网页。用户问"能不能看某个文件"时,
先看工具表里有没有对应工具, 别凭印象说做不到。

行为准则:
- 涉及用户数据的问题, 先调工具查证再回答, 不要编造
- 用户让你记住某事时, 用 save_memory 工具保存
- 回复用微信聊天风格, 简洁, 不用 markdown 符号
- 数据不足时直说需要什么, 不要瞎猜
- 回复控制在 300 字内, 列表类可放宽
- **必须真的调用过工具之后**才能对结果下结论。没调工具就说「内容被屏蔽了」「我查到了」
  都是编造, 审计里会留下 tools=0。
- **但"查过了"就够了, 不要为了保险再查一个无关的工具。** 已经拿到内容之后, 相关工具再调一次
  是浪费; 调一个**跟这个问题没关系**的工具(问某个概念展开, 却去翻当天日报)更是浪费——
  它不但帮不上忙, 还会让回答跑题。判断标准: 这次调用能改变我的答案吗? 不能就不调。
- 前一条有个**具体形态**: 想找"原文/出处"时, 先看手上已有的日期能不能落进那个工具的窗口。
  比如概念页记的来源是几个月前的某篇文章, 而日报只覆盖最近若干天——**那就不要拿日报去捞**,
  这次调用注定返回空。直接把"按现有材料能讲到哪里"讲清楚, 再问用户要不要给链接。
  明知查不到还去查, 用户等的时间是白等的。
- 多轮对话里, 用户说"它 / 这个 / 刚才那个"时, 指的是**上一轮的主题**, 沿着它继续就行;
  不要因为代词模糊就换个工具重新找话题。
- 工具**失败**时(结果以括号起头, 如「(起草没跑通: …)」), 只许转述工具给出的那个原因。
  工具没写原因、或只给了退出码, 就直说「工具没给出原因」并给下一步(重试一次 / 换个问法),
  **不许替它推测**一个听起来合理的原因——「大概是模型判断不一致」「可能是网络」这类
  都是编的, 用户会当成事实。也不许把工具失败说成"查不到"、把"查不到"说成工具失败。
- **"不知道"是一个合格的答案, 而且常常是唯一合格的答案。**
  2026-11-24 两次真实用户实测, 31 次追问里模型**一次"不知道"都没说过**——每次被戳穿,
  它就再给一个新的 JSON、一个新的时间、一个新的文件名。用户的原话是:
  "它每被我戳穿一次, 就换一个新的编造来补, 补得还很自然"。
  所以: 查不到就说查不到; 读不到就说读不到; **不要给一个"大概是这样"的具体值**
  (时间/文件名/条数/原文), 哪怕用户催你"猜一个"、"随便说个大概"。
  用户催得越急, 越说明他在等一个**能核对的**数字——给编的比给不出更糟。
- **不许伪造工具输出。** 不要写「已调用 X 工具, 返回:」后面跟一段 JSON 或代码块,
  除非那段内容**逐字**来自工具结果。真实的工具结果是纯文本逐行排列的, 不是 JSON。
  写成 JSON 会让用户以为那是程序回执而深信不疑——这是实测里最难识破的一种编造。
- **不许替用户定义他自己的事**: 谁是同事、谁发过什么文件、用户说过哪句话。
  这些如果工具结果里没有, 就说"这个我不知道"。
- **用户说"估一个""猜一个""大概就行"不是编造的授权。**
  2026-11-24 追问实测: 首轮答「示例群 1 条消息」, 用户说"不用太准, 你估一个就行",
  它就答「大概有 10+ 次互动, 另一个约 6-8 次」——**从 1 条变成 10+ 次**。
  这类"估算"看起来无害(用户自己说不用太准), 但它给的是一个**看起来可核对**的数字,
  用户会记住"老表群聊了 10 次"。
  正确做法: 要说清"我只有 X 这一个真实数值, 推不出更多";
  或者只在**工具结果里真有数据**的基础上做算术(求和/比较), 不做凭空的量级估算。`

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

/**
 * 按端点挑一台本机已有的 key（只给 `WEFLOW_AI_BASE_URL` 这条路用，D-171）。
 *
 * **认不出端点就返回空串**，绝不"随便拿一个 key 试试"：那把某个厂商的凭据发给了另一个主机，
 * 是本仓库最不能接受的一类事故（凭据外泄比"没配好"严重得多）。空串会让 `callLLM`
 * 抛一句明确的"未配置"，那比 401 好读，也比泄出去安全。
 *
 * 值都是加密存盘的，`get()` 会解密；这里**只读不打印**。
 */
function envKeyFor(url: string): string {
  const pick = (key: 'dashscopeApiKey' | 'deepseekApiKey' | 'typesafeApiKey' | 'openrouterApiKey') =>
    String(configService.get(key) ?? '').trim()
  if (/dashscope|aliyuncs/.test(url)) return pick('dashscopeApiKey')
  if (/deepseek/.test(url)) return pick('deepseekApiKey')
  if (/302ai|typesafe/.test(url)) return pick('typesafeApiKey')
  if (/openrouter/.test(url)) return pick('openrouterApiKey')
  return ''
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
  /** 今日累计 token（仅供观测，不参与配额判定——配额口径是消息条数，见 DAILY_LIMIT） */
  private dailyTokens = 0
  /** 最近一次模型调用的用量（字段口径见 recordUsage 的注释） */
  private lastUsage: { input?: number; cached?: number; output?: number; reasoning?: number; total?: number } = {}
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
    const { url, model, key, local } = resolveEngineConfig()
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

    // 温度：**默认 0.4**（助手说话要有变化），但允许环境变量覆盖（D-174）。
    // 评测把它固定成 0：那时量的是 harness，不是采样噪声；而线上默认值一个字没改。
    const rawTemperature = Number(process.env.WEFLOW_AI_TEMPERATURE ?? '')
    const temperature = Number.isFinite(rawTemperature) && String(process.env.WEFLOW_AI_TEMPERATURE ?? '').trim() !== ''
      ? rawTemperature : 0.4
    const body: Record<string, unknown> = { model, messages: payload, max_tokens: maxTokens, temperature }
    if (tools?.length) { body.tools = tools; body.tool_choice = 'auto' }

    // **可重试的失败要重试，坏的立即失败**（2026-11-24）。
    //
    // 评测连跑三轮，绕不开的失败全是这一类：
    //   `❌ 大脑暂时离线: The operation was aborted due to timeout`
    //   `❌ 大脑暂时离线: fetch failed`
    // 它们**与"模型不会用工具"完全无关**，却把整条用例判成失败——而这类错误重试一次就好。
    //
    // 区分（与 pi 同一纪律）：
    // - **可重试**：网络层错误（fetch failed / timeout）、429、5xx。
    // - **立即失败**：401/403（key 不对）、400（请求本身错）、404（模型名错）——
    //   这些重试只会把同一个错误重复三次，还多花时间。
    const res = await fetchWithRetry(`${url}/chat/completions`, {
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
    // 用量记账（2026-11-24 抽到 assistantEngine）：只做"进审计 + 累计"，
    // **不改配额口径**——配额按消息条数计，那是产品决定。
    const usage = usageFromEnvelope(envelope)
    if (usage) {
      this.lastUsage = usage
      this.dailyTokens += usage.total ?? ((usage.input ?? 0) + (usage.output ?? 0))
      auditUsage(usage)
    }
    const reasoning = envelope?.choices?.[0]?.message?.reasoning_content
    this.lastReasoning = typeof reasoning === 'string' ? reasoning : ''
    return envelope
  }

  /** 在明确授权后分析本地聊天，输出证据线索而非法律结论。 */
  async reviewEvidence(talker: string, messages: Message[], allowCloud = false): Promise<{ text: string; localInference: boolean; redactions: number }> {
    const { local } = resolveEngineConfig()
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
      // 2026-11-20 两次实测的教训，都写在这里：
      // ① 原来这段写着「给出三条路：①改 balanced…」，模型把它当成**自己**的选项，回了一句
      //    "现在模式已切换为 balanced"——档位一次都没动（D-176）。
      // ② 改老实之后又变成另一种烦人：每答一句就推销一次"要我帮你改模式吗？"，用户原话是
      //    "默认不要设什么balanced模式了…………"。所以现在**不给菜单、不主动提议**，
      //    只在用户真的问"怎么看正文"时，一句话说清它是配置项、要自己改。
      return '[隐私] 当前 strict 模式：第三方聊天正文会以「[内容N字已按严格模式屏蔽]」的形式'
        + '出现在工具结果里，你只能看到时间、方向与字数。'
        + '**不要主动提议改档位，也不要问"要我帮你改吗"**——那不是你能做的操作，'
        + '反复提议只会打扰用户。正常按你手上有的信息回答（谁、何时、多少字、什么类型）。'
        + '只有当用户明确问"怎么才能看到正文"时，才用一句话说明：这是配置项 '
        + 'assistantPrivacy，需要他自己在终端执行 weflow-cli config set assistantPrivacy balanced '
        + '并重启助手；你没有修改它的工具。'
        // 2026-11-24 实测：用户问「彪弟最近聊了什么」，它答「如果想了解具体内容，**需要打开
        // 严格模式**查看」——方向说反了。strict 是**限制**模式（正文被屏蔽），要看到正文得
        // **离开** strict。它知道该做什么，却把"解除限制"说成了"打开限制"。
        // 这类错误比编造更隐蔽：句子通顺、动词具体，用户照着做只会更看不到。
        + '**说方向时必须准确**：strict 是"屏蔽正文"的模式，想看到正文要**离开**它'
        + '（改成 balanced），**绝不能**说"打开/开启严格模式"——那会让用户以为开了就能看见，'
        + '实际正好相反。'
    }
    return `[隐私] 当前 ${mode} 模式：工具结果里的聊天正文是原文（电话/证件/邮箱/密钥/链接已打成`
      + '占位符），可以直接引用，不必声称被屏蔽。'
  }

  /** 组装系统提示: 基础人格 + 隐私状态 + 本机能力 + 本会话场景 + L2 摘要 + L3 事实。
   *
   *  三个纪律：
   *  1. **事实按相关度取一部分**，而不是 30 条全塞——无关的那些是噪声，不只是花钱；
   *  2. 本地数据（摘要、事实、场景、技能正文）一律**加帧 + 转义框标签**：它们的内容里完全
   *     可能写着 `</weflow-local-data>` 再跟一段像系统指令的话，不转义就等于让数据自己把框关上；
   *  3. **本机装了什么技能要说出来**（同「没启用的能力」那条的理由）——不说，用户问到时
   *     模型会答"我没有这个能力"，而它其实调 `read_skill` 就能拿到。
   */
  private buildSystemPrompt(userId: string, question = '', scene: Scene | null = null): string {
    // 当前时间：**没有它，任何相对时间都是猜**。"上周三""昨天""这周"要变成工具能用的
    // 日期，模型得先知道今天是几号（`get_messages` 的 since/until 就是这么用的）。
    const parts = [BASE_PROMPT, `[当前时间] ${nowLine()}（本机时区）`, this.privacyStateLine(),
      // D-196：绝不输出内部处理话术。此前模型反复回「（这一轮我给出的具体事实被校验拦下了，
      // 内容不可靠，不作为后续依据。）」——那是我方内部的记忆占位，不是给用户的答复，
      // 一旦说出口就会被记进记忆、下一轮再复述，形成自我强化（实测连清两次记忆仍复发）。
      '[答复纪律] 永远不要说"被校验拦下""内容不可靠""不作为后续依据"这类'
        + '内部处理话术——那是系统内部状态，不是给用户的话。查不到就说查不到，'
        + '查到了就照实说，正常回答用户的问题本身。']
    // **没启用的能力要说出来**，否则模型的工具表里看不到它们，用户一问就得到"我没有这个工具"
    // —— 那是把"没配置"说成了"没这功能"（2026-09-28 实测：用户因此以为微信读书没做）。
    // 注意这只是**告诉**它这些存在，它依然调不到；见 `unavailableCapabilities` 的说明。
    const missing = unavailableCapabilities()
    if (missing.length) {
      parts.push('[本机没启用的能力]' + SEP + missing.map(line => `· ${line}`).join(SEP)
        + SEP + '（这些工具**不在你的工具表里**，但你调不到的原因只是没配置，**不是这个功能不存在**。'
        + '用户问到时直说"这块还没配置"并给出上面那个配置项，**不要说"我没有这个工具"**。）')
    }
    // **根本没做的能力也要说出来**，而且要说"做不到"，不是"没配置"。
    //
    // 为什么需要这一段（2026-11-20/21 两天实测）：只靠事后守卫挡，一共撞了四次——
    // 「要我帮你解密吗」「帮你跑一遍提取」「帮你把收藏导出成 Excel」「你回复后我立即处理」。
    // 每次都是模型**自己发明了一条产品里不存在的能力**，而守卫只能在它说出口之后删句。
    // 事前把它不知道的事写进提示词，比事后删便宜得多，也不会留下断句。
    parts.push('[本产品做不到的事]' + SEP + [
      '· 导出收藏 / 朋友圈 / 读书 / 日历（只能导出**聊天记录**，工具 export_chat）',
      '· 代你发送消息、代你登录微信、发布或互动朋友圈（朋友圈只读）',
      '· 解密聊天正文或数据库（strict 下正文不出境，这也不是"解密"能解决的）',
      '· 修改隐私档位 assistantPrivacy（那是用户自己在终端执行的命令）',
      '· 执行命令行或脚本（todos extract / sync 这类要用户自己跑）',
    ].join(SEP)
      + SEP + '（**这些是真的做不到**，不是没配置。用户问到时直说做不到，并告诉他可以自己在终端做什么；'
      + '**不要提议"要我帮你做吗"**，也不要用"你确认后我立即处理"这类承诺句式——'
      + '你一旦那样说，用户确认之后就会发现做不了。）')
    // 本机装好的技能包。**只列名字/说明/版本，正文按需 read_skill 读** ——
    // 27 个技能的正文全塞进每轮提示词，既贵又会把真正的问题挤掉。
    const skillScan = scanSkills()
    const skillLines = skillCatalogueLines(skillScan)
    if (skillLines.length) {
      parts.push('[本机已安装的技能]' + SEP + skillLines.join(SEP)
        + SEP + '（技能是一份说明文档。要用某个技能，先用 `read_skill` 读它的正文再照着做；'
        + '**不要凭名字猜它怎么用**。）')
    }
    const sceneLines = scene ? renderScene(scene) : ''
    if (sceneLines) {
      parts.push('[本会话的场景]' + SEP + `场景「${scene!.name}」已生效，按它的要求来做。` + SEP + sceneLines)
    }
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
    } else if (withheld > 0) {
      // **一条都没选中，但记忆里确实有东西**（2026-11-24 补）。
      //
      // 原来这里什么都不说，于是模型只看到"没有记忆段"——它不知道那是"没选出来"还是
      // "真没有"，只能凭工作窗口里的残留硬答。实测：22 轮的任务型长程用例里，
      // 早期细节早已滚出 16 条窗口，它仍然**一次工具都没调**就作答，答的是窗口残留。
      // 这比编造更隐蔽：答案看起来对，但是靠运气。
      //
      // 现在明确告诉它"有记忆但没列出来，需要就查"，把选择权交回去。
      parts.push('[关于用户的长期记忆]' + SEP
        + `（记忆里有 ${withheld} 条，但与当前问题关系较远，这次没有列出来。`
        + '**如果你需要回忆用户之前说过的事，先调 `search_memory` 查一下再回答**，'
        + '不要凭工作窗口里的残留硬答——窗口只有最近几条，更早的事已经滚出去了。）')
    }
    return parts.join(SEP)
  }

  /** 白名单为空的首次配置提示是否已经打过了（只打一次，别把日志刷满） */
  private firstRunHintShown = false
  /** 最近一次路由认为「这条消息需要查本机数据」的概率（0 = 没问过） */
  private lastNeedsLocalData = 0
  /** 本轮 Jev 多工具保底判断建议的工具（D-191）。空 = 没建议或没开 */
  private turnJevSuggested: string[] = []
  /** 本会话的文件权限（D-199）：L0 安全范围 / L1 自主解锁只读跨目录 / L2 白名单不在此开放。
   *  用可变容器，工具改 level 后下一轮 ctx 拿到新值；重启进程归零（不持久化提权）。 */
  private privilege = { level: 0 as 0 | 1 | 2 }
  /** 本轮按「记住：…」直接落盘的那条事实（没有就 null）。用来跳过同轮的 `save_memory` */
  private turnExplicitFact: string | null = null
  /** 本轮**有产出**的工具结果（截断）。守卫判断"答复有没有用上刚查到的内容"要靠它 */
  private turnToolText: string[] = []
  /**
   * 本轮触发过的守卫机制名。
   *
   * 用途只有一个：判断"这段答复该不该进记忆"。被反编造类守卫拦过的答复一旦进了工作窗口，
   * 下一轮模型会**照着复述自己的旧话**——实测同一句话，脏桶答"19:42 某条消息内容"，
   * 干净桶答"22:47，内容被屏蔽"。
   */
  private turnGuardMechanisms = new Set<string>()
  /**
   * 本轮**检查过几道**守卫（分母）。
   *
   * 只记"触发"的话，"0 次触发"既可能是"模型没犯错"，也可能是"守卫路径根本没走到"——
   * 两种在记录里一模一样。有了分母，前一种才成立。
   */
  private turnGuardChecks = 0
  /**
   * 本轮实际执行过的工具名。守卫靠它分辨"说改了"和"真的调了写操作"（D-176）：
   * 答复里出现「已切换/已改为/已保存」，而这份集合里没有 `WRITE_TOOLS` 的任何一个 → 顶回去。
   */
  private turnToolNames = new Set<string>()
  /**
   * 每个用户**上一轮用过的那条检索**（工具 + 参数）。追问（"展开讲讲它"）靠它接回主题。
   *
   * 只收检索类（知识库/跨会话/记忆）——它们幂等、可重复读；`get_messages` 之类不在内，
   * 因为"再讲一遍上一条消息"没有意义，重派一次只是白花一次调用。
   */
  private lastRetrieval = new Map<string, { tool: string; args: Record<string, unknown> }>()
  /**
   * 每个用户**上一轮按名字查过的那个人**。用于「查一下他的聊天记录」这种代词收尾句：
   * 这句话里没有人名，而"他"指谁只有 harness 知道（上一轮的 `get_messages.contact`）。
   */
  private lastContact = new Map<string, string>()
  /** 本轮原话（闸门要看"用户到底提没提日报"）。每轮覆盖 */
  private turnText = ''
  /** 本轮知识库给过内容没有。反跑题闸门用（见 `get_daily_report` 那一段） */
  private turnKnowledgeHit = false
  /** 最近一次模型调用里**真返回**的推理内容（`reasoning_content`）。默认模型不返回，所以常是空串 */
  private lastReasoning = ''
  /** 每个用户最近一轮的轨迹：微信里发「轨迹」看的就是它（跨进程的历史在文件里，见 assistantTrace） */
  private traces = new Map<string, TurnTrace>()

  /**
   * 最近一轮的轨迹，给**本机面板**用（`/api/ask` 回答完之后，面板把"思考过程"折叠着挂上去）。
   *
   * 与微信里那条「轨迹」是**同一个来源**（`this.traces`），所以两处不会各说各话。
   * 只在本进程内存里：跨进程的历史在 `assistantTrace` 的文件里，面板要的是"刚刚那一轮"。
   */
  recentTrace(userId: string): TurnTrace | undefined {
    return this.traces.get(userId)
  }
  /**
   * 本轮已经调过的「工具名 + 参数」。同一条消息里重复调用**同样的参数**不会得到新信息，
   * 而实测（重名联系人那类用例里）模型会连着调三次 list_sessions。
   * 消息是串行处理的（queue），所以实例字段在这里是安全的。
   */
  private turnCalls = new Set<string>()
  /**
   * 本轮**来信自带**的图片文件（D-128）。消息串行处理，实例字段安全（同 `turnCalls`）。
   * `runToolCall` 建 ctx 时带上，`look_at_image` 的 `#信N` 句柄从这里取。
   */
  private turnInboundImages: Array<{ filePath: string }> = []

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

  /** Jev 双重判断模式（D-191）。默认 off：拿不到端到端收益就不改行为 */
  private jevDoubleCheckMode(): 'off' | 'log' | 'on' {
    const raw = String(configService.get('assistantJevDoubleCheck') || '').trim().toLowerCase()
    return raw === 'on' || raw === 'log' ? raw : 'off'
  }

  /**
   * Jev 工具建议（D-198）：一次 decide.py 调用问"这条可能要用哪些工具"（N 个独立 noul，
   * 支持多选）。它只是**给模型的参考信号**，不执行、不填参、不强制。
   *
   * 返回一条可塞进对话的 system 消息；判断层不可用/没给出建议时返回 null——
   * **静默跳过，不报错、不注入**（用户要的"没有就不经过"）。
   */
  private async maybeJevDoubleCheck(userId: string, text: string, trace: TurnTrace): Promise<string | null> {
    const mode = this.jevDoubleCheckMode()
    if (mode === 'off') return null
    try {
      const decision = await decideMultiTools({ message: text, runDecide: this.routeDecider as any })
      trace.steps.push({ kind: 'route', detail: `Jev 工具建议[${mode}]: ${decision.reason}`
        + (decision.ok ? ` (${Object.entries(decision.scores).map(([k, v]) => `${k}=${v.toFixed(2)}`).join(' ')})` : '') })
      appendLog(`[Jev工具建议] ${decision.reason}`)
      privacyGate.audit('JEV_MULTI', 0,
        `${mode} ${decision.selected.join(',') || 'none'} model=${decision.model || '?'}`)
      if (mode === 'on' && decision.ok) this.turnJevSuggested = decision.selected
      // 只有真的建议了工具才注入；没有/失败 → null，主流程照常，用户看不到任何变化
      if (mode !== 'on' || !decision.ok || !decision.selected.length) return null
      return '【工具选择参考】判断层认为这条消息可能需要：'
        + decision.selected.join('、')
        + '。回答本机数据的问题前，先调一个工具取真实数据再作答——'
        + '调错工具可以再调，但不能一个工具都不调就下结论。参数自己从问题里取。'
    } catch (error: any) {
      // 判断层坏了是"不经过"，不是"报错"——绝不让它影响正常对话
      appendLog(`[Jev工具建议] 判断异常，静默跳过: ${error?.message ?? error}`)
      trace.steps.push({ kind: 'route', detail: 'Jev 工具建议异常，静默跳过' })
      return null
    }
  }

  /** ReAct 主循环：一轮一轮问模型，直到它给出不带工具调用的答复。
   *
   *  抽成方法是因为守卫（见 handleMessage）要在"顶回去一次"之后**再跑一遍同一条循环**——
   *  两处各写一份，早晚有一处会漏掉既有的容错（参数解析、工具审计、轮数上限）。
   *
   *  **返回的 `outcome` 说清 `reply` 是什么**：`'answered'` 才是模型真的说出口的话；
   *  `'rounds-exhausted'` 时 `reply` 只是占位文案，调用方**不许**拿它跑"这句话是不是编造"
   *  一类的守卫（2026-11-24）。
   */
  private async runReactLoop(userId: string, messages: ApiMessage[],
                             trace: TurnTrace): Promise<{ reply: string; toolCalls: number; outcome: ReactOutcome }> {
    let reply = ''
    let toolCalls = 0
    let jevForced = false   // Jev 建议过、但模型想零工具作答时，只强制一次（防无限重跑）
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
        // **并行 tool_calls 的协议约束**（D-192）：OpenAI 兼容端点要求 assistant(tool_calls)
        // 后面**紧跟且连续** N 条 tool 消息，中间不能夹 system/user。工具失败提醒与取图
        // 这两类"附加消息"必须先攒着，等这一批 tool 消息全部 push 完再统一追加——
        // 否则长链条里一轮 6 个工具、其中几个失败时，序列变成
        // `assistant(6tc) tool system tool system ...`，服务端直接 400
        // "An assistant message with 'tool_calls' must be followed by tool messages"。
        const deferred: ApiMessage[] = []
        for (const tc of msg.tool_calls) {
          toolCalls++
          let args: Record<string, any> = {}
          try { args = JSON.parse(tc.function?.arguments || '{}') } catch { /* 参数容错 */ }
          await this.runToolCall(userId, messages, tc.id, tc.function?.name || '', args, trace, deferred)
        }
        messages.push(...deferred)
        continue
      }
      reply = (msg.content || '').trim() || '(空回复)'
      // Jev 建议了工具，但模型一轮工具都没调就想作答 —— 强制回炉一次（D-202）。
      // 用户的取舍：Jev 说要查就必须查，调多调少/调对调错以后再研究，但不能不查就下结论。
      // 这与守卫总开关无关（那个默认关）：这是 Jev 建议自带的硬约束，属于红线不是防御。
      if (!jevForced && toolCalls === 0 && this.turnJevSuggested.length && round + 1 < MAX_TOOL_ROUNDS) {
        jevForced = true
        reply = ''
        trace.steps.push({ kind: 'note', detail: 'Jev 建议了工具但零调用 → 强制回炉补查一次' })
        messages.push({ role: 'assistant', content: msg.content || '' })
        messages.push({ role: 'user', content:
          `你这一轮**没有调用任何工具**就作答了，但判断层认为这条消息需要查本机数据`
          + `（建议：${this.turnJevSuggested.join('、')}）。请先调用工具取真实数据，`
          + '再基于工具结果回答。调错工具可以再调，但不能不调就下结论。' })
        continue
      }
      trace.steps.push({ kind: 'note', detail: `第 ${round + 1} 轮往返后给出答复` })
      break
    }
    if (!reply) {
      // **撞上限时给一句"诚实的弃权"，而不是干巴巴的错误提示**（2026-11-24）。
      //
      // 原来这里是一句固定文案「(这轮处理太复杂了, 换个问法试试?)」——它的问题不是
      // 措辞难听，而是**信息量为零**：用户不知道我刚才查到了什么、卡在哪。
      //
      // 我们手上其实有材料：`turnToolText` 里是这一轮**所有工具返回的原文**。
      // 用它拼一句"我查到了这些，但没整理完"——**不新增任何 LLM 调用**，
      // 也让用户拿到已经查到的部分，而不是白等一轮。
      //
      // 这**不违反**「rounds-exhausted 不跑守卫」那条纪律：它不是编造，是把已有的
      // 工具输出**原样摘出来**；`outcome` 仍然是 `'rounds-exhausted'`。
      const clues = this.turnToolText
        .flatMap((t) => String(t).split('\n'))
        .map((l) => l.trim())
        .filter((l) => l && !/^\(/.test(l) && !/^（/.test(l))   // 跳过"错/空"那类括号行
        .slice(0, 4)
      reply = clues.length
        ? `这轮我查到了这些，但没能在限定步数内整理完：\n`
          + clues.map((l) => `· ${l.slice(0, 80)}`).join('\n')
          + '\n\n如果要更完整的结论，可以让我就其中一条继续查。'
        : '(这轮没能在限定步数内完成，换个问法或者把问题缩小一点试试？)'
      // 别把它记成"答完了"：这是撞上限，不是回答
      trace.stop = 'rounds-exhausted'
    }
    // **`outcome` 把"跑完了"和"答完了"拆开**（2026-11-24，借 pi 的
    // `agent_end` ≠ `agent_before_settle` 那条纪律，ext2.md:665）。
    //
    // 原来 `return { reply, toolCalls }` 一个返回值同时表达两件事：
    //   ① 这一轮跑完了（模型给了最终答复，或者用完了轮次）
    //   ② 这一轮答完了（`reply` 是模型真的说出口的话）
    // 95% 的情况下两者是同一件事，所以写在一起看着天经地义——但它们不是：
    // 撞上限时 `reply` 是上面那句**占位文案**，不是模型说的话，却和真答复走同一条路
    // 进了 `handleMessage` 的守卫段。守卫要判断"这句话是不是编造"，前提是"这句话是
    // 模型生成的"；撞上限时它不成立。今天没炸，只是因为占位文案里恰好没有承诺词——
    // 而那句文案是可以改的，改一个词就会去"修"一句没人说过的话。
    //
    // **特意不是 `ok: boolean`**：布尔值会被写成 `if (ok) … else …` 两条支路，而
    // "撞上限"和"LLM 报错"的处理不同（前者还有一份半成品答复），合成一个布尔会在
    // 下次改动时丢信息。用可判别联合，加状态时编译器会指着所有 switch 让你改。
    const outcome: ReactOutcome = trace.stop === 'rounds-exhausted' ? 'rounds-exhausted' : 'answered'
    return { reply, toolCalls, outcome }
  }

  /** 执行一次工具调用：脱敏、审计、把结果塞回对话。
   *
   *  快路径与 ReAct 循环**共用这一份**：D-035 里那条不可回归的安全属性是「没有审计行就不得
   *  派发工具」，两条路各写一遍，早晚有一条会漏。
   */
  private async runToolCall(userId: string, messages: ApiMessage[], callId: string,
                            name: string, args: Record<string, any>,
                            trace: TurnTrace, deferred: ApiMessage[] = []): Promise<void> {
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
    // 本轮的显式记忆已经在进模型之前就落盘了（见 `explicitRememberIntent`）。
    // 这时 `save_memory` 再写一次只会造出**两条近义事实**（用户原话一条、模型改写一条），
    // 而记忆是按条数占预算、按条数注入提示的，重复条目会挤掉真正有用的那条。
    // 这里不执行、但把话说清楚：模型看到的是"已经记下了"，不是"工具坏了"。
    if (name === 'save_memory' && this.turnExplicitFact) {
      trace.steps.push({ kind: 'note', detail: 'save_memory 跳过：本轮已按「记住：…」直接落盘' })
      messages.push({ role: 'tool', tool_call_id: callId,
        content: `（这句已经写进长期记忆了：「${this.turnExplicitFact}」。不用再写一次。）` })
      return
    }
    // 「这一轮已经拿到知识库内容、用户又没提日报，却还去查日报」——跳过，不执行（D-175）。
    //
    // 实测（qwen-flash，4 次里 1 次）：追问「展开讲讲它」时它先查了知识库，接着又去翻当天日报。
    // 日报只覆盖最近几天，与那个主题毫无关系，于是答复跑题、还多花一次调用。系统提示里早就写着
    // 「明知查不到还去查，用户等的时间是白等的」，但那是概率的；**这道闸门只在两个条件同时成立时
    // 触发**（本轮知识库有产出 + 用户话里没有日报/公众号这类词），所以它不会拦住真的想看日报的人。
    if (name === 'get_daily_report' && guardsEnabled() && this.turnKnowledgeHit
        && !/(日报|公众号|推文|推送|订阅号)/.test(this.turnText)) {
      trace.steps.push({ kind: 'note', detail: 'get_daily_report 跳过：本轮已有知识库内容，用户没提日报' })
      messages.push({ role: 'tool', tool_call_id: callId,
        content: '（跳过：这一轮已经从知识库拿到内容，用户也没有提到日报/公众号。'
          + '日报只覆盖最近几天，这次调用帮不上忙，还会让答复跑题。直接用上面查到的内容回答。）' })
      return
    }
    const ctx: ToolContext = { userId, memory: this.memory,
      privilegeLevel: this.privilege.level, cwd: process.cwd() } as ToolContext & { _privilege?: any }
    // 同一个可变容器传给工具：escalate 改它 → 下一轮 handleMessage 的 ctx 拿到新 level
    ;(ctx as any)._privilege = this.privilege
    if (this.turnInboundImages.length) ctx.inboundImages = this.turnInboundImages
    // **按 schema 抹平参数类型**（2026-11-24，借 pi 的宽松强转，D-178）。
    //
    // 为什么在 `executeTool` 之前而不是在各个工具里：`args.confirm !== true` 这种严格比较
    // 遇到字符串 `"true"` 会**静默**判成"没确认"，`args.with_text === true` 遇到 `"true"`
    // 会判成 false——两处都是"出错了却没有任何提示"，比报错难查得多。
    // 在每个工具里各写一遍强转，早晚漏一处；在这一层做，只做一次。
    //
    // 它**不负责"帮模型改对"**：只抹平同一个值的不同 JSON 写法（`"true"`→`true`、
    // `"20"`→`20`、单值→`[]`、可选 `null`→丢掉）。转不动的原样留着，
    // 让工具自己报 `(参数错误: …)`——那比"我猜你是想要 0"清楚。
    const coerced = coerceToolArgs(name, args)
    const raw = await executeTool(name, coerced, ctx)
    this.turnToolNames.add(name)
    trace.toolCalls += 1
    trace.steps.push({
      kind: 'tool', name, args: summarizeArgs(args), bytes: raw.length,
      produced: producedContent(raw),
    })
    const { safe, redactions } = privacyGate.redact(raw)
    privacyGate.audit(`TOOL:${name}`, raw.length, redactions ? `redacted=${redactions}` : '')
    messages.push({ role: 'tool', tool_call_id: callId, content: safe })
    // 收一份"这一轮查到过什么"：守卫要靠它判断答复有没有用上刚查到的内容（D-174）。
    // 只收**有产出**的、截断后的正文；失败的括号消息另有 `toolFailureNudge` 那条路。
    if (!isEmptyToolResult(raw) && this.turnToolText.length < 6) {
      this.turnToolText.push(safe.slice(0, 2000))
    }
    // 记下"上一轮用过的检索"，供追问接续（D-175）。只收幂等的读类检索
    if (RETRIEVAL_TOOLS.has(name)) {
      this.lastRetrieval.set(userId, { tool: name, args: { ...args } })
    }
    // 记下"上一轮查过谁"，供代词收尾句接续（D-175）。只在真的取到东西时记：
    // 查不到的失败不该被当成"他指的是这个人"。
    if (name === 'get_messages' && !isEmptyToolResult(raw)) {
      const contact = String(args.contact ?? '').trim()
      if (contact) this.lastContact.set(userId, contact)
    }
    // 本轮从知识库拿到过内容：反跑题闸门要靠它判断（见 get_daily_report 那一段）
    if (name === 'search_knowledge' && !isEmptyToolResult(raw)) this.turnKnowledgeHit = true
    // 工具这一步**没拿出内容**时紧跟着提醒一句（D-169）。不另开一轮：这条 system 消息与
    // 工具结果在同一批里进对话，模型下一轮自然看得到。小模型常把括号里那句错误当没看见，
    // 继续按自己的假设往下写——这一句是把"这一步失败了"翻译成它能接住的指令。
    if (guardsEnabled() && isEmptyToolResult(raw)) {
      deferred.push({ role: 'system', content: toolFailureNudge(name, raw) })
    }

    // 工具取来的图片作为**紧随其后的一条 user 消息**附上：`tool` 消息的 content 只能是
    // 字符串（OpenAI 的形状），塞不进多模态数组。图片本身不过 redact（它不是文本），
    // 出不出境由 `toApiMessages` 按隐私模式判定——那是唯一一道门。
    if (ctx.pendingImages?.length) {
      const n = ctx.pendingImages.length
      deferred.push({
        role: 'user',
        content: `（这是你刚取来的图片${n > 1 ? `，共 ${n} 张` : ''}）`,
        images: ctx.pendingImages,
      })
    }
  }

  /** 词法快路径开关。**默认 on**：它不进模型、不花钱，而且误命中是可恢复的（见下）。
   *
   *  `WEFLOW_LEXICON_ROUTE` 优先于配置，与 `engineConfig()` 读 AI 设置同一个口径：
   *  **临时换行为不该改用户的持久配置**。这一条是为了 A/B——"这台机制到底有没有用"只能靠
   *  同一时间窗、同一个模型、只差这一台的两次测量回答；靠改配置做对照，会把用户的助手一起改掉。
   */
  private lexiconRouteMode(): FastRouteMode {
    const env = String(process.env.WEFLOW_LEXICON_ROUTE ?? '').trim().toLowerCase()
    const raw = (env || String(configService.get('assistantLexiconRoute') ?? '')).trim().toLowerCase()
    if (raw === 'off' || raw === 'log') return raw
    return 'on'
  }

  /**
   * 预派发一个能力：把它写成一条 assistant 的 tool_call，再走**同一条** `runToolCall`。
   *
   * 抽出来是因为现在有三条路要用它（判断层快路径、词法快路径，将来可能还有场景）。
   * 这个仓库付过学费的老毛病在这里格外贵：审计、脱敏、重复调用去重、图片回填都长在
   * `runToolCall` 里，绕开它自己拼一次就等于少一道门。
   *
   * 返回派发了个数（0 = 没派发，调用方照常回退到循环）。
   */
  private async dispatchCapability(userId: string, capability: RouteCapability,
                                   messages: ApiMessage[], trace: TurnTrace): Promise<number> {
    // 预派发是**直接派发**的（不经工具表），所以这里要自己过同一道判据
    const blocked = unavailableToolReason(capability.tool)
    if (blocked) {
      trace.steps.push({ kind: 'route',
        detail: `想预派发 ${capability.tool}，但${blocked}；回退成循环` })
      return 0
    }
    const callId = `route-${capability.name}-${Date.now()}`
    messages.push({
      role: 'assistant',
      content: '',
      tool_calls: [{
        id: callId,
        type: 'function',
        function: {
          name: capability.tool,
          arguments: JSON.stringify(capability.args),
        },
      }],
    })
    await this.runToolCall(userId, messages, callId, capability.tool, capability.args, trace)
    return 1
  }

  /**
   * 词法快路径（D-168 / D-172）：用固定表把整句话对到"工具 + 参数"，**一次模型调用都不花**。
   *
   * 它服务的是**便宜的小模型**：两次往返里真正难的那次（从 31 个工具里挑一个、还要给参数）
   * 由确定性代码做掉，模型只需要把拿到的数据说成人话。命中之后**循环照跑**——预派发不结束
   * 这一轮，所以哪怕这条路挑错了，模型还能再调别的工具，最坏代价是白调一次。
   *
   * 两档：`routeByIntent`（固定参数，精度高）先，`routeWithArgs`（抽人名 + 时间词）后。
   */
  private async maybeLexiconRoute(userId: string, text: string, messages: ApiMessage[],
                                  trace: TurnTrace): Promise<number> {
    const mode = this.lexiconRouteMode()
    if (mode === 'off') return 0

    // 一句话两件事：**分别路由、各派一次**（D-173）。一个工具答不完那种消息，
    // 旧做法是整条交回循环——而模型经常只顾一件。两件都能被高精度命中才拆，否则交回循环。
    const parts = routeParts(text)
    if (parts.length >= 2) {
      if (mode === 'log') {
        privacyGate.audit('LEXICON_WOULD', 0, parts.map(h => h.capability.name).join('+'))
        trace.steps.push({ kind: 'route',
          detail: `词法（只记，未派发）: 两件事 ${parts.map(h => h.capability.name).join(' + ')}` })
        return 0
      }
      let dispatched = 0
      for (const hit of parts) {
        const n = await this.dispatchCapability(userId, hit.capability, messages, trace)
        if (!n) continue
        dispatched += n
        appendLog(`[词法/两件事] ${hit.reason}`)
        privacyGate.audit('LEXICON_HIT', 0, `${hit.capability.name} ${hit.capability.tool}`)
        trace.steps.push({ kind: 'route', detail: `词法: ${hit.reason}` })
      }
      if (dispatched) {
        this.lastNeedsLocalData = 1
        return dispatched
      }
      return 0
    }

    const hit = routeByIntent(text) ?? routeWithArgs(text)
    if (!hit) {
      // 「查一下他的聊天记录」：**代词指代上一轮那个人**（D-175）。
      // 这句话里没有人名，词法表抽不出来；而"他"在上一轮刚被查过——harness 手里本来就有这个
      // 事实（`lastContact`），拿它去补参数比让模型猜可靠。实测模型在这种收尾句上会**一次工具
      // 都不调**就作答，于是"确认我到底回没回他"变成了一句没有依据的话。
      const pronounContact = /(他|她|对方)/.test(text) && /(聊天记录|消息|记录|说过)/.test(text)
        ? this.lastContact.get(userId) : null
      if (pronounContact) {
        const capability: RouteCapability = {
          name: 'messages_of_pronoun', tool: 'get_messages',
          args: { contact: pronounContact }, when: '',
        }
        if (mode === 'log') {
          privacyGate.audit('LEXICON_WOULD', 0, `${capability.name}:${pronounContact}`)
          trace.steps.push({ kind: 'route', detail: `词法（只记，未派发）: 代词接回「${pronounContact}」` })
          return 0
        }
        const n = await this.dispatchCapability(userId, capability, messages, trace)
        if (n) {
          this.lastNeedsLocalData = 1
          appendLog(`[词法/代词] 接回上一轮那个人：${pronounContact}`)
          privacyGate.audit('LEXICON_HIT', 0, `${capability.name} get_messages`)
          trace.steps.push({ kind: 'route', detail: `词法: 代词接回「${pronounContact}」` })
          return n
        }
      }
      // 追问（"展开讲讲它"）：把**上一轮用过的那条检索**接回来（D-175）。
      // 这类话里没有名词，词法表无从下手；实测模型在这时会换个话题重开（去查当天日报），
      // 而日报只覆盖最近几天，与上一轮那个概念毫无关系。用它自己上一轮用过的工具再接一次，
      // 比让它自由发挥可靠；而且这一步**不结束这一轮**，它仍可以改主意。
      if (isFollowUp(text)) {
        const last = this.lastRetrieval.get(userId)
        if (last) {
          const args = { ...last.args }
          // "展开"就是要看邻居，知识库那条给 depth=2（工具自己的参数语义）
          if (last.tool === 'search_knowledge') args.depth = 2
          const followUp: IntentHit = {
            capability: { name: `${last.tool}_follow_up`, tool: last.tool, args, when: '' },
            reason: `词法接续：追问接回上一轮的 ${last.tool}`,
          }
          if (mode === 'log') {
            privacyGate.audit('LEXICON_WOULD', 0, followUp.capability.name)
            trace.steps.push({ kind: 'route', detail: `词法（只记，未派发）: ${followUp.reason}` })
            return 0
          }
          const n = await this.dispatchCapability(userId, followUp.capability, messages, trace)
          if (n) {
            this.lastNeedsLocalData = 1
            appendLog(`[词法/接续] ${followUp.reason}`)
            privacyGate.audit('LEXICON_HIT', 0, `${followUp.capability.name} ${last.tool}`)
            trace.steps.push({ kind: 'route', detail: followUp.reason })
            return n
          }
        }
      }
      return 0
    }

    if (mode === 'log') {
      // 灰度：只记「本来会走哪条」，行为一个字不改
      privacyGate.audit('LEXICON_WOULD', 0, `${hit.capability.name} ${hit.reason}`)
      trace.steps.push({ kind: 'route', detail: `词法（只记，未派发）: ${hit.reason}` })
      return 0
    }

    const dispatched = await this.dispatchCapability(userId, hit.capability, messages, trace)
    if (!dispatched) return 0
    // 走过词法路径就当"这条消息要查本机数据"：这句话本来就是从"查什么"的表里来的
    this.lastNeedsLocalData = 1
    appendLog(`[词法] ${hit.reason}`)
    privacyGate.audit('LEXICON_HIT', 0, `${hit.capability.name} ${hit.capability.tool}`)
    trace.steps.push({ kind: 'route', detail: `词法: ${hit.reason}` })
    return dispatched
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
    const dispatched = await this.dispatchCapability(userId, decision.capability, messages, trace)
    if (dispatched) {
      appendLog(`[快路径] ${decision.reason}`)
      privacyGate.audit('FASTROUTE_HIT', 0, `${decision.capability.name} ${decision.model}`)
    }
    return dispatched
  }

  /**
   * 单条消息处理: 指令路由 → ReAct 循环 → 记忆更新。
   *
   * **外部只许经由 `enqueueTurn` 调用**（它保证串行）；直接调会被 `inFlight` 挡下——
   * 见那个字段的注释。
   */
  async handleMessage(userId: string, text: string, kind: string,
                      inboundImages: Array<{ filePath: string }> = [], sceneId?: string): Promise<string> {
    if (this.inFlight) {
      throw new Error('handleMessage 重入：它依赖串行调用（记忆窗口、turnCalls 都是实例字段）')
    }
    this.inFlight = true
    try {
      return await this.handleMessageInner(userId, text, kind, inboundImages, sceneId)
    } finally {
      this.inFlight = false
    }
  }

  private async handleMessageInner(userId: string, text: string, kind: string,
                                   inboundImages: Array<{ filePath: string }> = [], sceneId?: string): Promise<string> {
    // kind==='image' 只有在**带着已落盘图片**时才是 D-128 那条新路；裸的图片消息
    // （runTurn 已经过滤掉的那种）走到这里仍按老规矩拒绝。
    if (kind !== 'text' && !(kind === 'image' && inboundImages.length)) {
      return '目前只支持文字消息哦'
    }

    let t = text.trim()
    const explicitScene = sceneId === undefined ? null : assistantScenes.resolveExplicit(sceneId)
    if (explicitScene && !explicitScene.match) {
      throw new Error(explicitScene.why ?? `无法使用场景「${sceneId}」`)
    }
    // 每轮重置（空数组也覆盖）：上一轮的图片不能漏到这一轮的句柄空间里。
    this.turnInboundImages = inboundImages
    // 来信图片的句柄指路牌：**能看才摆**（strict 且非本地推理不摆——与 get_messages 的
    // `#N` 同一规则，不摆出一个工具必定会拒绝的东西）。文件本身挂在工具 ctx 上，
    // 这里只告诉模型"有图、看的话用哪个句柄"。
    if (inboundImages.length && (privacyGate.mode() !== 'strict' || privacyGate.isLocalInference())) {
      const handles = inboundImages.map((_f, i) => `#信${i + 1}`).join('、')
      t += `（这张来信带 ${inboundImages.length} 张图片：${handles}。要看就用 look_at_image，不看也可以直接聊。）`
    }
    const startedAt = Date.now()
    this.turnCalls.clear()
    // 每轮清零：`lastNeedsLocalData` 由这一轮的判断层/词法路径各自写。**不清零就会漏到下一轮**
    // ——词法路径把它设成 1 之后，下一句纯闲聊（"今天天气不错"）会带着上一轮的信号被守卫顶一次。
    this.lastNeedsLocalData = 0
    this.turnJevSuggested = []
    this.turnToolText = []
    this.turnGuardMechanisms.clear()
    this.turnGuardChecks = 0
    this.turnToolNames.clear()
    this.turnText = t
    this.turnKnowledgeHit = false
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
      lines.push('下面是**用户自己在终端执行**的命令（你没有执行它们的工具，只能转述；'
        + '**只在用户问起时才提，不要主动提议**）：')
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

    // 场景：**命令要认，但只认真的存在的东西**。`parseSceneCommand` 只在后半段是已存在的
    // 场景 id 时才当指令，所以「场景切换怎么用」这种问话不会被吃掉（见那边的注释）。
    // 位置在内置指令之后、ReAct 之前：场景是这一轮的上下文，不是要模型去执行的任务。
    const sceneCmd = sceneId ? null : parseSceneCommand(t, assistantScenes)
    if (sceneCmd) {
      if (sceneCmd.kind === 'list') {
        const scenes = assistantScenes.list()
        if (!scenes.length) {
          return finish('还没有任何场景。用电脑加一个：\n'
            + 'weflow-cli scene add --id 日报 --keywords 日报,今天推文 --instruction "按主题分组" --dry-run', 'builtin')
        }
        const active = assistantScenes.binding(userId)
        const lines = scenes.map(s => `${s.id === active ? '▶ ' : '  '}${s.id}（${s.name}）${s.enabled ? '' : '[已停用]'}`
          + (s.keywords.length ? ` 关键词：${s.keywords.join('、')}` : ''))
        lines.push('', `本会话当前：${active ? `绑定「${active}」` : '未绑定（按关键词或上次用过的挑）'}`)
        lines.push('绑定：场景 <id>　解绑：场景 无')
        return finish(lines.join(String.fromCharCode(10)), 'builtin')
      }
      if (sceneCmd.kind === 'unbind') {
        assistantScenes.bind(userId, null)
        assistantScenes.save()
        return finish('✓ 已解绑，这个会话回到按关键词自动挑', 'builtin')
      }
      const bound = assistantScenes.bind(userId, sceneCmd.id as string)
      if (!bound.ok) return finish(`✗ ${bound.reason}`, 'builtin')
      assistantScenes.save()
      const scene = assistantScenes.get(sceneCmd.id as string)
      return finish(`✓ 已给这个会话绑定场景「${scene?.name ?? sceneCmd.id}」。之后每轮都按它来做，发「场景 无」解绑。`, 'builtin')
    }

    // === ReAct 主循环 ===
    this.memory.addTurn(userId, 'user', t)
    // L1 底账：原话按天追加（best-effort，失败不影响本轮）
    appendTranscriptTurn({ ts: Date.now(), role: 'user', content: t, session: userId })
    // 显式场景只覆盖这一轮，不写入绑定或最近使用；否则沿用绑定 → 关键词 → 上次用过的。
    const picked = explicitScene ?? assistantScenes.resolve(userId, t)
    const scene = picked.match?.scene ?? null
    if (picked.match) {
      trace.steps.push({ kind: 'note', detail: `场景「${picked.match.scene.name}」· 命中：`
        + ({ explicit: '本轮指定', binding: '绑定', keyword: '关键词', lastUsed: '上次用过的' }[picked.match.how]) })
      if (!sceneId && assistantScenes.lastUsedFor(userId) !== picked.match.scene.id) assistantScenes.save()
    } else if (picked.why) {
      // **多义要留痕**：不然用户只看到"这次没带场景"，不知道是因为撞车了
      trace.steps.push({ kind: 'note', detail: picked.why })
    }
    const messages: ApiMessage[] = [
      { role: 'system', content: this.buildSystemPrompt(userId, t, scene) },
      ...this.memory.workingWindow(userId).map(turn => ({ role: turn.role, content: turn.content })),
    ]

    // 显式「记住：X」**直接落盘**，不等模型抽取（与 D-169 同族的确定性兜底）。
    // 理由见 `explicitRememberIntent` 的注释：这是全系统最高精度的意图，交给一个会抽不出来的
    // 模型是白送的失败——用户说了"记住"，记忆里却没有，下次照样给出错误的建议。
    // 落盘之后**告诉模型一次**，它就不必再调 `save_memory`，也不会声称"我记下了"却什么都没写。
    const explicitFact = explicitRememberIntent(t)
    this.turnExplicitFact = null
    if (explicitFact && this.memory.addFact(userId, explicitFact, { sourceQuote: t })) {
      this.turnExplicitFact = explicitFact
      trace.steps.push({ kind: 'note', detail: `显式记忆：直接落盘（${explicitFact.slice(0, 30)}）` })
      privacyGate.audit('MEMORY_RULE_WRITE', explicitFact.length, 'explicit-remember')
      messages.push({
        role: 'system',
        content: `（这句话里的「${explicitFact}」已经写进长期记忆了，不必再调 save_memory；`
          + '直接确认你记下了它就行。）',
      })
    }

    let reply = ''
    let toolCalls = 0
    try {
      toolCalls += await this.maybeLexiconRoute(userId, t, messages, trace)
      toolCalls += await this.maybeFastRoute(userId, t, messages, trace)
      const jevHint = await this.maybeJevDoubleCheck(userId, t, trace)
      if (jevHint) messages.push({ role: 'system', content: jevHint })
      const first = await this.runReactLoop(userId, messages, trace)
      reply = first.reply
      toolCalls += first.toolCalls

      // ⚠️ 2026-11-24：**守卫与兜底只在"模型真的说了这句话"时才跑**。
      //
      // 撞轮次上限时 `reply` 是占位文案（`outcome === 'rounds-exhausted'`），它**不是模型的
      // 输出**。拿它去跑 `impossibleOffer` / `emptyTalkPushback` 是在"修"一句没人说过的话。
      // 今天侥幸没炸，只是因为占位文案里恰好没有承诺词——而那句文案是可以改的。
      //
      // 这与 pi 的 `agent_end` ≠ `agent_before_settle` 是同一件事（ext2.md:665）：
      // "这一轮跑完了"不等于"这一轮答完了"。**边界要显式说出来，不能让调用方去猜。**
      const answered = first.outcome === 'answered'

      // 这一轮每次工具调用的**参数串**。给"声称覆盖多期、实际只查一期"那条守卫用
      // （2026-11-24：`get_daily_report × 3` 都不带 date/days，拿到的是同一期，
      // 答复却说"整合了最近 3 天"）。从本轮轨迹里读，不另建一套记账。
      const turnArgs: string[] = (trace.steps ?? [])
        .filter((s): s is Extract<typeof s, { kind: 'tool' }> => s.kind === 'tool')
        .map((s) => String(s.args))

      // 守卫（D-169）：**从答复本身取证**，不再依赖判断层。
      //
      // 老版本只在 `lastNeedsLocalData >= MIN_NEEDS_TOOL` 时触发，而这个信号由判断层给，
      // 判断层又只在 `assistantFastRoute=on` 时跑——默认 `off`，于是那条守卫在默认配置下
      // **永远不会触发**。实测撞到的是最坏的一种：模型一次工具都没调，却答「已查：甲在 3 天前
      // 发来项目文档更新，提到『等你确认』」，带着具体细节，用户没法从文字上分辨真假。
      //
      // 现在两个信号并列，任一成立就顶回去**一次**：
      // ① 答复自述查过、而这一轮工具调用为 0 —— 纯字符串判定，成立即编造；
      // ② 判断层说"要查本机数据"、而这一轮没调工具 —— 老信号，留着不冲突。
      // 补问失败一律保留原答复（守卫是纠错，不是否决）。
      const corpus = messages.map(m => typeof m.content === 'string' ? m.content : '').join('\n')
      // 一次只顶回一种：先认"编造"（最严重），再认"答复没用上刚查到的内容"，最后才是老信号。
      // 两件事都犯的时候先修编造——那一句补问里已经要求它去查、去引用。
      let retry: { mechanism: string; reason: string; message: string } | null =
        answered && guardsEnabled() ? pushbackFor({ reply, toolCalls, corpus }) : null
      // 双重判断（D-191）：Jev 保底说要用工具，模型却没调就给了具体本机事实 → 顶回去。
      // 顺序紧随"自述查过"那条——都是"没工具却给事实"家族。诚实拒绝不拦（见守卫函数注释）。
      if (answered && !retry && guardsEnabled() && this.turnJevSuggested.length) {
        this.turnGuardChecks += 1
        retry = unfulfilledJevSuggestionPushback({
          reply, toolCalls, suggested: this.turnJevSuggested,
          hasConcreteClaim: hasConcreteLocalClaim(reply),
        })
      }
      // 紧接着认"编造做过某件事"：用户会照着它行事（以为档位/待办改了），代价比多一次调用大。
      if (answered && !retry && guardsEnabled()) {
        this.turnGuardChecks += 1
        retry = stateClaimPushback({ reply, toolNames: this.turnToolNames })
      }
      // 再认"提议做不到的事"（"要我帮你解密吗？"）：它没做也没改，但用户会点头等一个结果。
      if (answered && !retry && guardsEnabled()) {
        this.turnGuardChecks += 1
        retry = impossibleOfferPushback(reply)
      }
      // 破坏性请求被拒之后，不许顺手提议导出/备份（把"毁掉"变成"搬出来一份"）。
      if (answered && !retry && guardsEnabled()) {
        this.turnGuardChecks += 1
        retry = destructiveFollowUpPushback({ userText: t, reply })
      }
      // 方向说反：「需要打开严格模式才能看到正文」——strict 是屏蔽模式，方向正好相反。
      // 这类不静默改写（正确的话要重写整段），只顶回一次。
      if (answered && !retry && guardsEnabled()) {
        this.turnGuardChecks += 1
        retry = reversedPrivacyPushback(reply)
      }
      // 把单日冒充成跨期汇总：声称"整合了最近 N 天"，而日报类工具只查到同一期。
      // 比编造隐蔽——每一条标题都是真的，只有"多少天"是假的。
      if (answered && !retry && guardsEnabled()) {
        this.turnGuardChecks += 1
        retry = crossPeriodClaimPushback({
          reply, toolNames: this.turnToolNames, argsSeen: turnArgs,
        })
      }
      // **复述不得脱离工具原文**（D-182）：答复里的路径 / 带引号的原文 / 严格模式占位符，
      // 必须能在本轮的工具结果 + 用户原话里逐字找到。
      //
      // 这是用户视角实测暴露的最严重一类：工具**是对的**，模型在复述时编造——
      // 工具返回「已导出到 output/exports/…」，答复说「路径 ~/Desktop/…，文件已生成」；
      // 工具返回「[内容1字已按严格模式屏蔽]」，答复说「你那猫是不是又把花盆打翻了？」。
      // 加判词抓不到"细节错但语气对"，只能靠这条结构性约束。
      if (answered && !retry && guardsEnabled()) {
        this.turnGuardChecks += 1
        retry = unattributedFactsPushback({ reply, corpus: this.turnToolText.join('\n') + '\n' + t })
      }
      // **"搜不了"不等于"搜了没有"**（D-183）：工具报的是索引没建/服务连不上，
      // 答复却说成"未收录/没有相关记录"——对用户是两种完全不同的结论。
      if (answered && !retry && guardsEnabled()) {
        this.turnGuardChecks += 1
        retry = capabilityGapPushback({ reply, corpus: this.turnToolText.join('\n') })
      }
      // **时间戳必须来自工具结果**（D-189）：答复里的"11月23日22:47"这类完整时间，
      // 必须能在本轮工具结果 `[11/23 16:40]` 里对上。实测：问联系人C，工具给 16:40，
      // 模型答的是上一个人（联系人A）的 22:47——时间戳最容易被当成"系统字段"，而它恰恰是编的。
      if (answered && !retry && guardsEnabled()) {
        retry = fabricatedTimestampPushback({ reply, corpus: this.turnToolText.join('\n') })
      }
      // **"参数错了"不等于"不存在"**（D-188）：工具报 `(参数错误: limit 必须是 1-30…)`，
      // 答复却说"该联系人不在会话列表中，不存在或名称有误"——这是**反向编造**：
      // 真实存在的好友被说成不存在，用户会以为被删了。比凭空编造更难防。
      if (answered && !retry && guardsEnabled()) {
        this.turnGuardChecks += 1
        retry = argErrorAsAbsentPushback({ reply, corpus: this.turnToolText.join('\n') })
      }
      // **"估一个"不是授权编造**（D-185）：用户说"不用太准，估一个就行"，
      // 它就从"1 条消息"编成"大概 10+ 次互动"。这类估算看起来无害，
      // 但它给的是一个看起来可核对的数字。
      if (answered && !retry && guardsEnabled()) {
        this.turnGuardChecks += 1
        const est = estimateWithoutSource(reply, this.turnToolText.join('\n'))
        if (est) {
          retry = {
            mechanism: 'capability-gap-as-absent',
            reason: `估算里出现了工具结果里没有的数字（${est}）`,
            message: '注意：你写的是「' + est + '」，但**工具结果里没有这个数字**，也没有能推出它的量。'
              + '用户说"不用太准、估一个"**不是编造的授权**——他要的是一个能记住的数字，'
              + '编出来的比给不出更糟。请改成：说清"我只有 X 这一个真实数值，推不出更多"。',
          }
        }
      }
      // 最后认"给了过去时间窗却一个工具都没查"：拿前面几轮对话里的旧摘要当答案，日期还对不上。
      if (answered && !retry && guardsEnabled()) {
        this.turnGuardChecks += 1
        retry = timeWindowWithoutLookup({ userText: t, toolNames: this.turnToolNames })
      }
      if (answered && !retry && toolCalls === 0 && this.lastNeedsLocalData >= MIN_NEEDS_TOOL) {
        retry = {
          mechanism: 'needs-local-data',
          reason: '路由说需要查本机数据，但这一轮没调任何工具',
          message: '注意：你刚才的回答没有调用任何工具，因此你手上并没有本机数据。'
            + '这个问题需要本机的真实数据。请现在就调用合适的工具；'
            + '若确实查不到，说明你调用了哪个工具、它返回了什么。',
        }
      }
      if (answered && !retry && guardsEnabled()) {
        this.turnGuardChecks += 1
        // 顺序：先"答复没用上刚查到的内容"，再"已经查到了却反问用户要参数"。
        // 两条都是"查到了却没交出去"，前者是没带内容，后者是干脆推回去。
        const toolText = this.turnToolText.join('\n')
        retry = emptyTalkPushback({ reply, toolText }) ?? asksBackData({ reply, toolText })
      }
      if (retry) {
        privacyGate.audit('TOOL_GUARD_PUSHBACK', 0,
          `${retry.mechanism} needs_local_data=${this.lastNeedsLocalData.toFixed(2)}`)
        this.turnGuardMechanisms.add(retry.mechanism)
        appendLog(`[守卫] ${retry.reason}；顶回去一次`)
      }
      // **记"检查了几道"**（2026-11-24，读 pi 的 telemetry 后补的）。
      //
      // 原来只记"触发了哪道"（上面的 PUSHBACK）。于是"0 次触发"有两种完全不同的解释，
      // 而在记录里**一模一样**：
      //   · 好消息：跑了 N 轮，每轮都检查过，模型没犯这个错；
      //   · 坏消息：守卫路径根本没走到（开关关着 / 早退 / 代码删了没发现）。
      // 这正是本仓 AGENTS.md 那条「负向结论要有阳性对照」在观测上的版本。
      //
      // 现在每轮都记一条 `GUARD_CHECKED`，带上检查了几道、命中了几道。
      // 分母有了，"这道守卫从来没触发过"才能被读成结论。
      privacyGate.audit('GUARD_CHECKED', this.turnGuardChecks,
        `fired=${this.turnGuardMechanisms.size} answered=${answered ? 1 : 0}`
        + (this.turnGuardMechanisms.size ? ` mech=${[...this.turnGuardMechanisms].join(',')}` : ''))
      if (retry) {
        try {
          messages.push({ role: 'system', content: retry.message })
          trace.steps.push({ kind: 'note', detail: `守卫（${retry.mechanism}）：顶回去一次` })
          const second = await this.runReactLoop(userId, messages, trace)
          // **补问撞上限时不能覆盖原答复**（2026-11-24，读 pi 的 core loop 时发现的）。
          //
          // 原来这里无条件 `reply = second.reply`。但 `second.outcome` 可能是
          // `'rounds-exhausted'`——那时 `second.reply` 是占位文案
          // （「这轮处理太复杂了…」），拿它覆盖一句**已经说出口的真答复**，
          // 等于为了纠正一个小毛病，把整个回答换成了错误提示。
          //
          // 守卫的原则一直是"纠错，不是否决"（见上面那句注释）。补问没跑完整，
          // 就说明这次纠错没成功——**保留原答复**才是符合这条原则的做法。
          if (second.outcome === 'answered') {
            reply = second.reply
          } else {
            appendLog(`[守卫] 补问撞上了轮次上限，保留原答复（${reply.slice(0, 30)}…）`)
            trace.steps.push({ kind: 'note', detail: '守卫补问撞上限：保留原答复' })
          }
          toolCalls += second.toolCalls
        } catch (e: any) {
          appendLog(`[守卫] 补问失败，保留原答复: ${e?.message ?? e}`)
        }
      }
      // **确定性兜底：无条件执行**，不依赖上面是否命中 pushback。
      //
      // 这一段原来写在 `if (retry)` 里面，于是漏掉了一种最关键的形态：模型补问后把承诺
      // 换了措辞（"你回复后我立即处理"），那句话**不含**"导出/收藏"这类关键词，
      // 下一次 impossibleOffer 判 null → 整个答复被当成"没有承诺"原样放行。
      // 教训：兜底逻辑挂在"前面那道有没有命中"下面，就等于把它的有效性交给了她要修的那件事。
      //
      // 但它仍然要求 `answered`：撞轮次上限时 `reply` 是占位文案（2026-11-24），
      // 拿它去"删掉做不到的提议"是在修一句没人说过的话。
      if (answered && guardsEnabled()) {
        const cleaned = stripOffersAfterDestructive(t, reply)
        if (cleaned !== reply) {
          appendLog('[守卫] 删掉了破坏性请求之后的多余提议')
          trace.steps.push({ kind: 'note', detail: '守卫：删掉拒绝后的多余提议' })
          reply = cleaned
        }
        const cleaned2 = stripImpossibleOffers(reply)
        if (cleaned2 !== reply) {
          appendLog('[守卫] 删掉了做不到的提议')
          trace.steps.push({ kind: 'note', detail: '守卫：删掉做不到的提议' })
          reply = cleaned2
        }
      }
    } catch (e: any) {
      reply = `❌ 大脑暂时离线: ${e.message?.slice(0, 100)}\n(本地指令仍可用: 发「帮助」)`
      trace.stop = 'llm-error'
      trace.error = String(e?.message ?? e).slice(0, 120)
    }

    // === 记忆更新 (L2 压缩 + L3 提取) ===
    //
    // **被守卫判为编造的答复不进工作窗口**（2026-11-24，第四轮实测）。
    //
    // 这是"记忆桶被自己的编造污染"的根源：`wxmirror-sidebar-2` 桶里存着前几轮
    // "联系人A 19:42 某条消息内容"这类答复，模型在后续轮次**照着复述自己的旧话**——
    // 实测同一句话，脏桶答"19:42 某条消息内容"，干净桶答"22:47，内容被屏蔽"。
    // `agent.mjs` 的注释早就记过这个坑（"模型会照着复述自己的旧话"，
    // 2026-11-20 为此换过一次桶），但换桶只是清场，**没堵住入口**。
    //
    // 现在：这一轮如果触发了**反编造类**守卫（`unattributed-fact` 等），
    // 就把一条**不带具体内容**的占位写进窗口，而不是那段编造。
    // 用户仍然看得到那段答复（守卫已经顶回去过一次），但**记忆里不再留毒**。
    // **默认拦，而不是列举该拦的**（2026-11-24，读 pi 的会话模型后改的）。
    //
    // 原来这里是一个**白名单**：
    //   m === 'unattributed-fact' || m === 'capability-gap-as-absent'
    //   || m === 'single-day-passed-as-range'
    // 但 `assistantGuard.ts` 的联合类型里还有 7 个机制，其中
    // **`claimed-query-without-tool`（"我已查过"但一次没查）恰恰是最典型的编造**，
    // 还有 `claimed-state-change-without-tool` / `impossible-offer` /
    // `past-window-without-lookup` / `answer-ignores-tool-content`——
    // 这些编造**照样进窗口**。白名单的毛病在于：**将来新加的守卫默认不拦，
    // 除非有人记得回来改这张名单**。而没人会记得。
    //
    // 现在反过来：只有 `TRUST_PRESERVING` 里这几类**明确无害**的才放行，其余一律拦。
    // 这样新增守卫**默认就是拦的**——方向对了，漏的是"少拦一个无害的"而不是"放走一个编造"。
    const TRUST_PRESERVING = new Set<string>([
      // 这条只是"追问时用的代词接不回上一轮"，跟事实真假无关，答复本身可以留作上下文
      'asks-back-despite-data',
    ])
    const polluted = [...this.turnGuardMechanisms].some((m) => !TRUST_PRESERVING.has(m))
    this.memory.addTurn(userId, 'assistant', polluted
      ? '（这一轮我给出的具体事实被校验拦下了，内容不可靠，不作为后续依据。）'
      : reply)
    appendTranscriptTurn({ ts: Date.now(), role: 'assistant', content: reply, session: userId })
    const llm = (msgs: ChatTurn[], maxTokens?: number) =>
      this.callLLM(msgs as ApiMessage[], undefined, maxTokens).then(
        (d: any) => d.choices?.[0]?.message?.content || '')
    try { await this.memory.compressIfNeeded(userId, llm) } catch { /* 压缩失败不阻断 */ }
    try { await this.memory.extractFactsIfNeeded(userId, llm) } catch { /* 提取失败不阻断 */ }
    // 提取之后顺手对账：拿 sourceQuote 回原始流水找原话，给每条事实标「有没有依据」。
    // 只读本地文件、纯子串查找，几十毫秒级；失败同样不阻断这一轮。
    try { reconcileFacts(this.memory, userId) } catch { /* 对账失败不影响答复 */ }
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
    const { sessionId, kind, origin } = input
    // D-128：带**已落盘**图片的来信从"整条忽略"变成一轮（文本占位）；没落盘的
    // （没开下载 / 下载失败）照旧忽略——文件不在，句柄无从谈起。
    const inboundImages = inboundImageFiles(input.message)
    const imageAsTurn = kind === 'image' && inboundImages.length > 0
    const text = imageAsTurn ? (input.text.trim() || '（发来一张图片）') : input.text

    if (origin === 'wechat') {
      const msg = input.message!
      const access = this.accessDecision(msg)
      if (!access.allowed) {
        privacyGate.audit(`DENY_${access.reason.toUpperCase().replace(/-/g, '_')}`, 0, sessionId.slice(0, 12))
        onLog?.(`  → 拒绝: ${sessionId.slice(0, 12)}… ${access.reason} (未回复, 不耗 LLM)`)
        this.maybeAnnounceWhitelistBootstrap(access, msg, onLog)
        return { status: 'denied', reason: access.reason }
      }
      if (kind !== 'text' && !imageAsTurn) {
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
      const reply = await this.handleMessage(sessionId, text, kind, inboundImages, input.sceneId)
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
    // 场景文件同理：读不出来时用户面对的是"我加过的场景全没了"，而原因在被留档的文件名里
    if (assistantScenes.problem) {
      appendLog(`[场景] ${assistantScenes.problem}`)
      privacyGate.audit('SCENES_LOAD_ISSUE', 0, assistantScenes.problem.slice(0, 80))
      onLog?.(`⚠ 场景: ${assistantScenes.problem}`)
    }
    const { local } = resolveEngineConfig()
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
  ask(sessionId: string, text: string, onLog?: (line: string) => void,
      sceneId?: string): Promise<TurnOutcome> {
    let outcome: TurnOutcome = { status: 'error', message: '未取得结果' }
    return this.enqueueTurn({ sessionId, text, kind: 'text', origin: 'panel', sceneId }, (o) => {
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

  /**
   * 面板里那个"记忆"按钮要的：**它记住了什么**。只读，且**不经过模型**。
   *
   * 为什么不复用微信里那条 `记忆` 指令的文本：聊天那条路给的是"最近 5 条"的摘要（那是给对话用的），
   * 面板要按时间列全、还要看得出每条是从哪句话来的，所以带上 `sourceQuote`。两边读的是同一份 memory，
   * 只是呈现不同 —— 不各存一份。
   *
   * 桶（`userId`）由**调用方**决定：面板那条路传的是服务端自己那个 `memoryBucket`，不接受客户端指定。
   */
  memorySnapshot(userId: string): {
    facts: { content: string; ts: number; sourceQuote: string }[]
    summary: string
    workingTurns: number
    saveError: string
  } {
    return {
      facts: this.memory.facts(userId).map(f => ({
        content: f.content, ts: f.ts, sourceQuote: f.sourceQuote ?? '',
      })),
      summary: this.memory.summary(userId),
      workingTurns: this.memory.workingWindow(userId).length,
      // 落盘失败要传出去：用户问"你记住了什么"时，若上次根本没存上，那份答案就是假的
      saveError: this.memory.lastSaveError ?? '',
    }
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
