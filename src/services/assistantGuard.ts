/**
 * 确定性守卫：**不进模型、不花钱**，专门盯"小模型最容易犯、而后果最重"的那几类错（D-169）。
 *
 * ## 为什么这件事不能交给提示词
 *
 * 提示词里早就写着「没调工具不许说"我查了"」。强模型遵守，小模型不遵守——长程那条用例实测到
 * 的就是这一种：模型**一次工具都没调**，却答「已查：甲在 3 天前发来项目文档更新，提到
 * 『等你确认』」。这类答复比"我不知道"坏得多：它带着具体细节，用户没法从文字上分辨真假。
 *
 * 原来那道守卫（`assistantService.ts` 里的 `TOOL_GUARD_PUSHBACK`）依赖**判断层**给的
 * `needs_local_data` 概率，而判断层只在 `assistantFastRoute` 打开时才跑——默认是 `off`。
 * 也就是说：那条守卫在默认配置下**永远不会触发**。这不是"弱模型才有的问题"，是守卫自己
 * 站在了一个默认不成立的信号上。
 *
 * 这里改成**从答复本身取证**：答复里出现"我查了/已查/查到"这种自述，而这一轮 `tools=0`，
 * 那就是编造，不需要任何外部信号。判断是纯字符串的，可离线测、可复现。
 *
 * ## 三条设计约束
 *
 * 1. **只顶一次，且不许把原答复弄丢**。补问失败保留原答复（调用方负责）——守卫是纠错，
 *    不是否决。
 * 2. **宁可漏，不可误伤**。自述识别只认第一人称 + 完成态的显式说法，并把否定句
 *    （"查不到""没查到""无法查"）排除在外。漏掉的代价是少救一次；误伤的代价是把一句
 *    正常的话顶回去、多花一次调用。
 * 3. **工具报错后当场提醒，不另开一轮**。工具返回"没有内容"（括号起头，见
 *    `producedContent`）时，紧接着在对话里补一句系统提醒，比事后再顶一次便宜。
 */
import { producedContent } from './assistantTools.js'

/** 这条工具结果是不是"没拿出内容"。复用 `assistantTools` 的约定，不另立一套判断 */
export function isEmptyToolResult(raw: string): boolean {
  return !producedContent(raw)
}

/**
 * "我查过了"的自述。只认**第一人称 + 完成态**：
 * 已查 / 我查了 / 我查到 / 查过了 / 我翻了 / 根据工具结果 / 记录里显示。
 *
 * 不认"查一下""可以查"这类建议句——那是给用户的提示，不是自述。
 */
const QUERY_CLAIMS: RegExp[] = [
  /(我)?(已|已经|刚刚?|刚才)(查|检索|搜|翻)(了|过|到)?/,
  /我(查|检索|搜|翻)(了|过|到)/,
  /(查|检索|搜)(到|过)了/,
  /根据(工具|查询|检索|搜索)(结果|返回|到)/,
  /(工具|记录|数据|会话|日志)(里|中)(显示|写着|查到|看到)/,
]

/** 否定或建议：出现就说明这句话**不是**在声称查过 */
const NOT_A_CLAIM: RegExp[] = [
  /查不到|没查到|没有查到|未查到|查不了|无法(查|检索|搜)|找不到|没找到|没有找到/,
  /(可以|建议|不妨|需要|要)(你)?(先)?(查|检索|搜)/,
  /如果你想|如果你要|要不要(我)?(查|搜)/,
]

/** 把答复切成句子：自述与否定必须在**同一句**里才作数 */
function sentences(text: string): string[] {
  return (text ?? '')
    .split(/[。！!？?\n；;]/)
    .map(line => line.trim())
    .filter(Boolean)
}

/**
 * 答复里有没有"我查过了"的自述。返回命中的那一句（去否定之后），没有则 null。
 *
 * 返回原句而不是布尔：顶回去的时候要把这句**原话**摆给它看，说"你刚才说『…』，
 * 但你这一轮没有调用任何工具"。模糊的指责小模型接不住，具体的引用它接得住。
 */
export function claimsQuery(reply: string): string | null {
  for (const sentence of sentences(reply)) {
    if (NOT_A_CLAIM.some(pattern => pattern.test(sentence))) continue
    if (QUERY_CLAIMS.some(pattern => pattern.test(sentence))) return sentence
  }
  return null
}

/**
 * 答复里那些**引号括起来、但在工具结果与对话里都找不到**的片段。
 *
 * 只用来当**证据**附在顶回去的那句话里，不单独触发一次补问：模型引用系统提示（场景说明、
 * 技能目录）里的文字是合法的，而 corpus 拿不到那部分。所以这里只做"能不能对上"的弱检查，
 * 措辞上也留有余地（"如果它不是你真的看到的"）。
 */
export function unsupportedQuotes(reply: string, corpus: string): string[] {
  const found: string[] = []
  const pattern = /[「『“"]([^」』”"]{4,40})[」』”"]/g
  const flat = (corpus ?? '').replace(/\s+/g, '')
  let match: RegExpExecArray | null
  while ((match = pattern.exec(reply ?? '')) !== null) {
    const quote = match[1].trim()
    if (!quote) continue
    if (flat.includes(quote.replace(/\s+/g, ''))) continue
    if (!found.includes(quote)) found.push(quote)
  }
  return found
}

/**
 * 守卫总开关（`WEFLOW_HARNESS_GUARDS=off` 关）。
 *
 * 它的用途只有一个：**A/B 测量**。要知道"这台机制到底有没有用"，只能靠同一时间窗、同一个
 * 模型、只差这一台的两次跑分。靠改 `config.json` 做对照会把用户正在用的助手一起改掉，
 * 而"临时换行为不动持久配置"是本仓库既有的口径（见 `engineConfig()` 读 `WEFLOW_AI_*`）。
 *
 * **一个开关管两处**（自述顶回去 + 工具失败提醒）：它们是同一件事的两个出口，拆成两个开关
 * 只会诱出"只关一半"的跑分，那种数据读不出结论。
 */
export function guardsEnabled(): boolean {
  return String(process.env.WEFLOW_HARNESS_GUARDS ?? '').trim().toLowerCase() !== 'off'
}

/** 一条消息里能被"引用"的最小单位：中文按二字组、西文按词、数字按两位以上 */
function contentTokens(text: string): Set<string> {
  const out = new Set<string>()
  const flat = String(text ?? '').replace(/\s+/g, ' ')
  for (const word of flat.match(/[A-Za-z][A-Za-z0-9_.+-]{2,}/g) ?? []) out.add(word.toLowerCase())
  for (const digits of flat.match(/\d{2,}/g) ?? []) out.add(digits)
  // 中文：滑动二字组。**不用词典分词**——这里要的是"有没有半点重合"，不是语义分析，
  // 而二字组足够稳、且对"把原文抄回来"这件事很敏感。
  const cjk = flat.replace(/[^\u4e00-\u9fa5]/g, ' ')
  for (const run of cjk.split(' ')) {
    for (let i = 0; i + 2 <= run.length; i++) out.add(run.slice(i, i + 2))
  }
  return out
}

/**
 * 答复**一个字都没用上刚查到的内容**。
 *
 * 这是最后剩下的一类失败：工具调对了、结果也回来了，答复却是一句概括（"他最近聊了些工作上的事"），
 * 具体的人名/原话/关键词一个都没带。用户拿不到可核对的东西，于是无从判断这是查出来的还是编的
 * ——**在这个助手里，"没法核对"和"编"是一回事**。
 *
 * 判据刻意保守，只在三条同时成立时才出手：
 * 1. 答复不算长（≤ 160 字）——更长的答复里没有重合通常是合理的概括或拒答；
 * 2. 工具结果够长（≥ 40 字）——太短的结果本身没有可引用的内容；
 * 3. 两边的内容记号**零重合**——只要带上其中任何一个词（人名、原话、数字）就不再管。
 *
 * 误伤代价可控：多跑一轮，而补问的话术本身就在要求"把具体内容带上"，那正是该做的事。
 */
export function emptyTalkPushback(input: { reply: string; toolText: string }): Pushback | null {
  const reply = (input.reply ?? '').trim()
  const toolText = (input.toolText ?? '').trim()
  // 上限放到 160 字是实测校准的：80 字那条会漏掉"一百来字但全是套话"的答复，
  // 而漏掉的代价（用户拿到没法核对的话）比多一次补问大得多。
  if (!reply || reply.length > 160) return null
  if (toolText.length < 40) return null
  // 反问/拒答/追问不是"空话"：它们在等用户补信息，本来就不该引用工具结果
  if (/(吗|呢|？|\?)$/.test(reply)) return null
  if (/(请|需要|告诉|确认|不清楚|不知道|查不到|没找到|没有)/.test(reply)) return null

  const toolTokens = contentTokens(toolText)
  if (!toolTokens.size) return null
  for (const token of contentTokens(reply)) {
    if (toolTokens.has(token)) return null
  }
  const firstLine = toolText.split(/[\n。]/).map(line => line.trim()).filter(Boolean)[0] ?? ''
  return {
    mechanism: 'answer-ignores-tool-content',
    reason: `答复（${reply.length} 字）与刚查到的内容零重合`,
    message: '注意：你刚才那句答复**一个字都没用上刚查到的内容**——用户没法核对，'
      + '也就分不清这是查出来的还是编的。刚查到的是这样的：'
      + `「${firstLine.slice(0, 80)}」。`
      + '请用上其中的具体内容（人名 / 原话 / 关键词）再答一遍，不要只给一句概括。',
  }
}

export interface PushbackInput {
  reply: string
  /** 这一轮实际派发了几次工具 */
  toolCalls: number
  /** 模型这一轮看到过的全部文本（工具结果 + 上下文），用于引文核对 */
  corpus?: string
}

export interface Pushback {
  /** 触发它的机制名，进审计 */
  mechanism: 'claimed-query-without-tool' | 'answer-ignores-tool-content' | 'asks-back-despite-data'
    | 'claimed-state-change-without-tool' | 'impossible-offer' | 'past-window-without-lookup'
    | 'offer-after-destructive' | 'reversed-privacy-direction'
  /** 为什么触发（一句话，进轨迹） */
  reason: string
  /** 顶回去时要追加的那条 system 消息 */
  message: string
}

/**
 * 该不该把这一轮顶回去。
 *
 * 唯一的硬条件：**`toolCalls === 0` 且答复自述查过**。这一条成立时那句话必然是编造的，
 * 与"模型强不强""判断层怎么说"都无关。
 */
export function pushbackFor(input: PushbackInput): Pushback | null {
  if (input.toolCalls > 0) return null
  const claim = claimsQuery(input.reply)
  if (!claim) return null
  const quotes = input.corpus ? unsupportedQuotes(input.reply, input.corpus) : []
  const quoteLine = quotes.length
    ? `你引用的这些内容在工具结果与对话里都找不到：${quotes.map(q => `「${q}」`).join('')}。`
      + '如果它们不是你真正看到的，就不要写进答复。'
    : ''
  return {
    mechanism: 'claimed-query-without-tool',
    reason: `答复自述查过（「${claim.slice(0, 40)}」），但这一轮工具调用为 0`,
    message: '注意：你刚才的回答写着「' + claim.slice(0, 60) + '」，但你这一轮**没有调用任何工具**，'
      + '手上并没有本机数据。' + quoteLine
      + '请现在就调用合适的工具取真实数据；如果确实查不到，就照实说查不到，并说明你想查什么。',
  }
}

/**
 * 工具这一步没给出内容时，紧跟着补的一句提醒。
 *
 * **不另开一轮**：它作为一条 system 消息插在工具结果后面，模型下一轮自然看得到。
 * 小模型对"括号里一句英文错误"经常当作没看见，继续按自己的假设写下去——这一句是把
 * "这一步失败了"翻译成它能接住的指令。
 *
 * **带上工具原话**（`reason`）。三个理由：
 * ① 用户要看到的是"数据库连接超时"，不是"工具执行失败"——而模型最容易做的事就是把它
 *    概括成后者（实测过的失败形态）；
 * ② 概括等于替工具推测原因，而这个仓库明令禁止推测；
 * ③ 把原话摆在它眼前，比要求它"去上面那条消息里找原因"可靠得多。
 */
export function toolFailureNudge(toolName: string, reason?: string): string {
  const said = (reason ?? '').trim().replace(/^\(/, '').replace(/\)$/, '').slice(0, 160)
  const quote = said ? `工具的原话是：${said}。` : ''
  return `（提醒：\`${toolName}\` 这一步没能取到内容。${quote}`
    + '不要在答复里说"查到了"。可以换个参数或换个工具再试一次；'
    + '**否则就照实说"这次没查到"**，并把上面那句原因转述给用户。'
    + '「没查到」和「工具跑不起来」都要说清是哪一种。）'
}

/** 反问里"要一个工具本来已经给过的东西"的那些词 */
const ASK_BACK_CUES = /(时间|日期|范围|哪天|多久|时间段|具体哪|什么时候)/
/** 反问的形状 */
const QUESTION_SHAPE = /[？?]\s*$|(吗|呢)[。！!]?\s*$/

/**
 * 明明已经查到内容，却反过来向用户要参数。
 *
 * 实测形态（qwen-flash，问「甲最近跟我说了什么」）：模型**已经调过 `get_messages` 且拿到了
 * 消息**，然后答「"最近"这个时间表述不够具体，请给个时间范围」。
 * 这既不是撒谎也不是能力不足——它是**把能回答的问题推回给用户**，而工具文档里写着"不给 since
 * 就是最近若干条"。用户被自己的助手挡在门外，感受上和"它不干活"一样。
 *
 * 判据保守：答复必须是个**反问**、且问的正是"时间/日期/范围"这类参数，而这一轮**确实有产出**。
 * 反问联系人是谁（`bad-args` 那种）不在此列——那时工具根本没产出，调用方也不会走到这里。
 */
export function asksBackData(input: { reply: string; toolText: string }): Pushback | null {
  const reply = (input.reply ?? '').trim()
  const toolText = (input.toolText ?? '').trim()
  if (!reply || !toolText) return null
  if (reply.length > 200) return null
  if (!QUESTION_SHAPE.test(reply)) return null
  if (!ASK_BACK_CUES.test(reply)) return null
  // **已经引用过内容、只是末尾又问一句**的不算推回去（"甲说文档发你了，要往前翻吗？"是正常形态）。
  // 判据与空话守卫同一套：有任何一处内容重合就说明它答过。
  const toolTokens = contentTokens(toolText)
  for (const token of contentTokens(reply)) {
    if (toolTokens.has(token)) return null
  }
  const firstLine = toolText.split(/[\n。]/).map(line => line.trim()).filter(Boolean)[0] ?? ''
  return {
    mechanism: 'asks-back-despite-data',
    reason: '已经查到内容，却只反问用户要时间范围',
    message: '注意：你**已经查到内容了**（不给 since 就是最近若干条，这已经是能回答的），'
      + '不该只反问用户要时间范围。刚查到的是：「' + firstLine.slice(0, 80) + '」。'
      + '请直接用这些内容回答；如果确实需要往前翻，答完再问要不要指定日期。',
  }
}

/**
 * 这一轮**真的会改东西**的工具。
 *
 * 别写成"所有非只读工具"：`format_article` 只把 Markdown 转成 HTML 片段、不写任何东西，
 * 而 `export_chat` 会落盘。分不清就会把"我把文章排版好了"也算成合法的状态声明，
 * 守卫就形同虚设。
 */
export const WRITE_TOOLS: ReadonlySet<string> = new Set(['save_memory', 'set_todo_status', 'export_chat'])

/**
 * "我改了某个状态"的判词。
 *
 * 为什么单立一条：2026-11-20 实测，用户说「改用balanced模式」，模型回
 * 「现在模式已切换为 balanced，可以查看完整内容了」——而 `assistantPrivacy` 从头到尾都是
 * strict，31 个工具里**没有任何一个能改它**。它没有撒谎的动机，是被提示词里那句
 * 「①改 balanced」和写给用户的 `weflow-cli config set assistantPrivacy balanced`
 * 带进了角色：把"用户该执行的命令"读成了"我能执行的操作"。
 *
 * 判词刻意收窄：必须同时有**完成标记**（已/已经）和**变更动词**，避免误伤
 * 「已保存的笔记」「已经更新的公众号」这类描述既有状态的句子。
 */
const STATE_CHANGE_VERBS =
  '切换|改为|改成|改到|设为|设置为|更新为|保存为|标记为|调整为|开启|关闭|启用|禁用|改好'

/**
 * 两条判词一起用，缺一不可：
 *
 * - A：`已/已经` + 可选"把/将" + **同一小句内**的变更动词。`[^。；\n，]` 把范围锁在一小句里，
 *   是为了不被"已把文档发你了，记得保存为 PDF"这种跨小句的句子骗到。
 * - B：`把/将` + 小句内的变更动词，用来认「我把记录保存为文件了」这种不带"已"的说法。
 */
const STATE_CHANGE_CUES = new RegExp(
  '(?:已经|已)(?:把|将)?[^。；\\n，]{0,12}?(?:' + STATE_CHANGE_VERBS + ')'
  + '|(?:把|将)[^。；\\n，]{0,12}?(?:' + STATE_CHANGE_VERBS + ')',
)

/** 答复是否在声称"我已经把某个状态改掉了" */
export function claimsStateChange(reply: string): boolean {
  const text = (reply ?? '').trim()
  if (!text) return false
  return STATE_CHANGE_CUES.test(text)
}

/**
 * 声称改了状态、而这一轮没有任何写操作工具 → 顶回去一次。
 *
 * **不否决答复**：补问失败时调用方仍保留原答复（守卫是纠错，不是拦截）。
 * 但"已经改好了"这种话宁可多顶一次——用户会照着它行事（以为隐私档位变了、
 * 以为待办改了），那个代价比多一次模型调用大得多。
 */
export function stateClaimPushback(input: {
  reply: string
  toolNames: Iterable<string>
}): Pushback | null {
  const reply = (input.reply ?? '').trim()
  if (!reply) return null
  if (!claimsStateChange(reply)) return null
  for (const name of input.toolNames) {
    if (WRITE_TOOLS.has(name)) return null
  }
  return {
    mechanism: 'claimed-state-change-without-tool',
    reason: '声称已经改了某个状态，但这一轮没有调用任何会写入的工具',
    message: '注意：你刚才说"已经切换/改为/设置/保存"了某个东西，但**这一轮你一个写入类工具都没调用**'
      + '（能真正留下改变的只有 save_memory、set_todo_status、export_chat）。所以那句话是假的。'
      + '请二选一，不要含糊：①如果这件事本来就需要用户自己在终端执行命令（比如改隐私档位 '
      + 'assistantPrivacy），就直接说"我改不了，请你在终端执行 weflow-cli config set …"，'
      + '并说明改完要重启助手；②如果你该调用工具，现在就调用，然后按工具的返回如实回答。',
  }
}

/**
 * **产品里根本不存在的能力**。提议它们等于撒谎，比"声称已经做了"更容易被信。
 *
 * 2026-11-20 实测（同一轮迭代里第二次撞到）：strict 档下它答完 "内容被严格模式屏蔽了"，
 * 紧接着问「要我帮你**解密**吗？」——它没有解密正文的工具，用户却会以为点个头就能看。
 * 前一句是"我做了什么"（`claimsStateChange` 管），这一句是"我可以为你做"（这条管）。
 *
 * 这份名单只收**这个产品里确定做不到**的事，不收录"工具没配置好"那类（那由
 * `unavailableCapabilities` 在系统提示里说清，用户可以配）。名单短是刻意的：
 * 每多收一个，就多一次把正常建议误判成承诺的风险。
 */
const IMPOSSIBLE_OFFERS: Array<{ re: RegExp; what: string; unless?: RegExp }> = [
  { re: /(解密|破解|扒出|反混淆)(一下|聊天|正文|内容|数据库)?/, what: '解密聊天正文或数据库' },
  { re: /(帮你|替你|给你)?(改|切换|调整|设置)(一下)?(隐私)?(档位|模式)/, what: '修改隐私档位' },
  { re: /(帮|替|给)你(改|调|换|设置)(一下)?(配置|设置|参数)/, what: '替你改配置' },
  // 2026-11-23 放宽：实测「当然可以，你希望我**帮你发**什么内容呢？」——"帮你"与"发"之间
  // 会插字（帮你发/替你发一条/给你发过去），原来的紧邻匹配接不住。
  { re: /(帮你|替你|给你)[^。！？\n]{0,6}(发|回复|代发|转发)(一[条个]|消息|过去|给他|给她)?(内容|消息|消息给|给他|给她)?/,
    what: '代替你发送消息',
    // 2026-11-23：**「起草」是能做的**（工具 draft_reply，strict 下它会自己拒绝并说明原因）。
    // 这条误伤原来就有——把"我可以帮你起草回复"判成"代替发送"，等于把它唯一能给的服务删了。
    unless: /起草|草稿|拟一[条个]|写一[条个]|润色/ },
  { re: /(帮你|替你)(自动)?登录/, what: '代你登录微信' },
  // 2026-11-20 实测原型：「要我现在帮你跑一遍提取吗？」——它没有执行命令/脚本的工具。
  // 2026-11-21 补：同一件事还有"跑一次提取""帮你跑一遍""跑一下"这类说法，动词在"跑"后面
  // 隔了几句才出现（"需要我帮你跑一次提取吗？"），原来的紧邻匹配接不住。
  { re: /(帮你|替我|给你|要我|需要我|我可以)[^。！？\n]{0,14}(跑|执行|运行|提取|同步|扫描|索引)/,
    what: '执行命令或脚本' },
  // 「要我帮你关注某条吗？」朋友圈是只读的。
  { re: /(帮你|替你)(关注|点赞|评论|转发|收藏)(一下)?(某条|那条|这条|动态|朋友圈)/, what: '在朋友圈做互动操作' },
  // 「要我帮你整理成一段总结发朋友圈吗？」——没有发布朋友圈的能力。
  { re: /(帮你|替你)[^。！？\n]{0,20}(发|发到|发布)(到)?(朋友圈)/, what: '发布朋友圈' },
  // 2026-11-21 实测原型：「能不能把我的收藏导出成 Excel」→「可以导出…你确认后我马上帮你操作」。
  // 只有 export_chat（导出聊天记录），**没有导出收藏**。这类"我能做，你确认我就办"的承诺最坑：
  // 用户一确认就会卡住或拿到编的结果。
  { re: /(收藏|朋友圈|读书|日历|通讯录)[^。！？\n]{0,12}(导出|备份|导出成|转成|保存成)/,
    what: '导出收藏/朋友圈/读书等没有导出能力的数据' },
  { re: /(导出|备份)[^。！？\n]{0,10}(收藏|朋友圈|读书|日历|通讯录)/,
    what: '导出收藏/朋友圈/读书等没有导出能力的数据' },
  // 2026-11-21 再补：把承诺拆成"以导出开头，细节散在后面几句"也是同一件事。
  // 实测形态：「可以导出。请确认：1. 全部还是按关键词？2. 包含哪些字段？你回复后我立即处理。」
  // —— "导出"和"收藏"隔了两句，上面两条紧邻正则都接不住。
  // 只有 export_chat 导出聊天记录，所以裸的「可以导出」本身就是超出能力的承诺；
  // **整句里出现"聊天记录"的除外**（那是真能做的）：所以负向判断放在整段的 `.test` 之前，
  // 用 grep 出来不含"聊天"才认。放过这里会让「要我把聊天记录导出吗」误伤。
  { re: /(可以|能|我来|我帮你)[^。！？\n]{0,6}(导出|备份)/,
    what: '导出（本产品只能导出聊天记录，不能导出收藏/朋友圈等其他数据）',
    unless: /聊天(记录|消息)|消息记录/ },
]

/** 答复是否在提议一件**产品里做不到**的事 */
export function impossibleOffer(reply: string): string | null {
  const text = (reply ?? '').trim()
  if (!text) return null
  // 只在"提议/承诺"的语气里才算：疑问或承诺句式（要我帮你…吗 / 我可以帮你… / 帮你…吧）
  //
  // 2026-11-21 补两次：
  // ① "可以导出。我需要确认一下…" 这种**直接肯定**也是承诺，没有"帮你/要我"字样；
  // ② 补问之后模型会把承诺拆成不带主语的半句（"你回复后我立即处理。"），
  //    这时它一个字都不提"导出/收藏"，但仍然是承诺 —— 所以"立即/马上 + 处理/动作"也算。
  const offering = /(要我|需要我|我可以|我能|帮你|替你|给你|可以(帮你|导出|备份|转成|保存)|(立即|马上|马上帮你|立即帮你)(处理|执行|生成|导出|整理)|当然可以|没问题|可以啊|好的[，,]我(来|帮))/.test(text)
  if (!offering) return null

  // **先判"它是不是在说做不到"**（2026-11-23 补）。
  //
  // 事前把"本产品做不到的事"写进提示词之后，模型开始**正确地说做不到**了，可原判词不看
  // 语义方向，只要句子里有"导出收藏"又带"你觉得可以吗"之类的语气，就当成承诺：
  //   「导出收藏功能目前不支持…需要我帮你查某篇收藏文章的内容吗？」← 前半是拒绝，
  //     后半是**做得到**的提议（search_favorites），却被整段替换掉。
  //   「我无法解密聊天正文…需要的话把图片发给我，我来帮你分析画面」← 同理。
  // 这是"守卫自己制造错误"的典型形态：它让答复从"做不到+还能做什么"退化成只剩"做不到"。
  //
  // 判据：整段里出现**明确的拒绝**（不支持/无法/做不到/没法/不能），且没有任何"我这就去办"
  // 的承诺口吻（可以/马上/立即/这就/帮你…做）才豁免。前半否定 + 后半承诺的混合形态
  // 仍然要拦——那正是最坑的一种。
  const denies = /(不支持|做不到|无法|没法|不能|没有这个|不具备|帮不了)/.test(text)
  const commits = /(可以(帮你|给你)?(导出|备份|转成|保存|整理)|我(马上|立即|这就)(处理|执行|生成|导出|整理))/ .test(text)
  if (denies && !commits) return null

  // 只有承诺口气、却完全没说清做什么的（"你回复后我立即处理。"）：单独认成"含糊承诺"
  if (!/(收藏|导出|备份|转成|保存|关注|点赞|评论|转发|发朋友圈|解密|破解|跑|执行|运行|登录|改配置)/.test(text)
      && /(立即|马上)(处理|办|弄)/.test(text)) {
    return '含糊的能力承诺（用户一确认就会发现做不了）'
  }
  for (const { re, what, unless } of IMPOSSIBLE_OFFERS) {
    if (re.test(text) && !(unless && unless.test(text))) return what
  }
  return null
}

/** 提议做不到的事 → 顶回去一次，要求它改成"做不到 + 用户能自己做什么" */export function impossibleOfferPushback(reply: string): Pushback | null {
  const what = impossibleOffer(reply)
  if (!what) return null
  return {
    mechanism: 'impossible-offer',
    reason: `提议了一件产品里做不到的事：${what}`,
    message: `注意：你刚才提议「${what}」，但**你没有这个能力**，产品里也没有这个功能。`
      + '用户会以为点头就能做到，所以这句话必须收回。不要换个说法再提一次，直接改成：'
      + '①说明这件事做不到（一句话，不解释内部实现）；②如果用户真正想要的是"看到内容"，'
      + '告诉他唯一可行的那条路（要么他自己在终端改配置 assistantPrivacy 并用 weflow-cli 自己查，'
      + '要么直接打开微信看原文）。',
  }
}

/**
 * **最后一道确定性兜底：把"提议做不到的事"那句话从答复里删掉。**
 *
 * 为什么光有 pushback 不够（2026-11-20 实测）：审计里明明记了
 * `TOOL_GUARD_PUSHBACK impossible-offer`，轨迹也写了"顶回去一次"，可补问之后它**换个说法又提
 * 了一遍**（"要我帮你关注某条吗？"→"要不要我帮你盯一下？"）。守卫是劝，劝不动就得动手。
 *
 * 做法刻意保守：只在**句级**动手（按 。！？换行 切），只删能独立命中 offer 判词的那一句；
 * 正文其余部分一个标点都不动——误删一句建议的代价，比留下一句做不到的承诺小。
 */
/**
 * **最后一道确定性兜底：把"提议做不到的事"那段换成一句实话。**
 *
 * 为什么光有 pushback 不够（2026-11-20/21 两次实测）：审计里明明记了
 * `TOOL_GUARD_PUSHBACK impossible-offer`，轨迹也写了"顶回去一次"，可补问之后它**换个说法又提
 * 了一遍**。守卫是劝，劝不动就得动手。
 *
 * 为什么不是"逐句删"：承诺常常**跨句**（"可以导出。请确认：1… 2… 你回复后我立即处理。"），
 * 逐句删会留下"可以导出"这种更糟的残句——实测就留下过一个自相矛盾的答复。
 * 所以：只要这段里出现了做不到的提议，就**整段落换掉**。
 */
export function stripImpossibleOffers(reply: string): string {
  const text = String(reply ?? '')
  if (!text.trim()) return text
  // **幂等**：已经是我们写好的实话收尾，就不要再套一遍。
  // 实测漏过一次：兜底现在每轮都跑，第二次进来把上一轮生成的实话又当"承诺"处理，
  // 于是同一段话重复了两遍。
  if (/^（这件事我做不到：/.test(text.trim())) return text
  if (text.includes('这件事我做不到：') && text.trim().split('这件事我做不到：').length > 2) {
    // 已经重复过：只保留最后一段实话
    const lines = text.split(/\n{2,}/)
    const truth = lines.filter((l) => l.includes('这件事我做不到：')).pop() || ''
    return truth
  }
  const what = impossibleOffer(text)
  if (!what) return text
  const tail = '（这件事我做不到：' + what
    + '。需要的话你可以在服务器上自己执行 weflow-cli 的相关命令。）'

  // 第一步：**先判整段**。整段带着"可以导出/我马上/立即处理"这类承诺口吻的，说明承诺贯穿全文，
  // 逐句删只会留下残句——实测留下过"你回复后我立即处理。"这种孤零零的半句。
  const wholeCommit = /可以(?=.*(导出|备份|转成|保存))|我马上|我立即|立即处理|马上处理/.test(text)
  if (wholeCommit) {
    const blocks = text.split(/\n{2,}/).filter((b) => b.trim() && !impossibleOffer(b))
    const rest = blocks.join('\n\n').trim()
    return rest ? rest + '\n\n' + tail : tail
  }

  // 第二步：逐句删——"正文 + 承诺"混在同一块里时，正文能留住（正常形态）。
  const sentences = text.split(/(?<=[。！？\n])/)
  const survivors: string[] = []
  let dropped = 0
  for (const s of sentences) {
    if (s.trim() && impossibleOffer(s)) { dropped += 1; continue }
    survivors.push(s)
  }
  const kept = survivors.join('')
    .replace(/(?:或者|还有|以及|也可以|要不要)[\s。！？]*$/, '')
    .replace(/[ \t]+$/gm, '')
    .trim()

  if (!kept || dropped === 0) {
    const blocks = text.split(/\n{2,}/).filter((b) => b.trim() && !impossibleOffer(b))
    const rest = blocks.join('\n\n').trim()
    return rest ? rest + '\n\n' + tail : tail
  }
  return kept + '\n' + tail
}

/** 答复里"顺手提议另一个动作"的句子（比 impossibleOffer 宽：包括做得到的事） */
const OFFER_SHAPE = /(要不|要么|要不要|要我|需要我|我可以|我可以帮|帮你|替你)[^。！？\n]{0,30}(一下|一下吗|吗)[？?]?/

/**
 * 破坏性请求被拒之后，把"顺手提议别的动作"那句**删掉**（确定性兜底）。
 *
 * 为什么不能只靠 pushback：2026-11-20 实测审计里记了 `TOOL_GUARD_PUSHBACK
 * offer-after-destructive`，补问之后模型**原封不动又提了一次**导出备份。
 * 与 `stripImpossibleOffers` 同一条道理——劝不动就动手，只删句、不动正文。
 */
export function stripOffersAfterDestructive(userText: string, reply: string): string {
  const text = String(reply ?? '')
  if (!text.trim()) return text
  if (!DESTRUCTIVE_REQUEST.test(userText ?? '')) return text
  const parts = text.split(/(?<=[。！？\n])/)
  const kept: string[] = []
  let removed = 0
  for (const part of parts) {
    if (part.trim() && OFFER_SHAPE.test(part)) { removed += 1; continue }
    kept.push(part)
  }
  if (!removed) return text
  const out = kept.join('').replace(/[ \t]+$/gm, '').replace(/\n{3,}/g, '\n\n').trimEnd()
  return out || text
}

/**
 * 用户这一轮要的是删除/清空/退出这类**破坏性动作**吗 */
const DESTRUCTIVE_REQUEST = /删了|删掉|删除|清空|清理掉|退出登录|注销|拉黑|解散/

/**
 * **方向说反**：把"解除限制"说成"打开限制"。
 *
 * 2026-11-24 实测：用户问「彪弟最近聊了什么」，它答「如果想了解具体内容，需要**打开严格模式**
 * 查看」——strict 是**屏蔽正文**的模式，要看到正文得**离开**它。
 *
 * 这类错误比编造更隐蔽：句子通顺、动词具体、还给了操作，用户照着做只会更看不到。
 * 最先看上去像"术语用错"，其实是**把因果说反了**——所以判词盯的是
 * 「打开/开启/启用 + 严格/strict」这个组合，而不是"严格模式"本身（后者可能出现在
 * 正确叙述里，比如"当前就是严格模式"）。
 *
 * 只有 `assistantPrivacy` 真有 strict 这个概念时才可能出这种错，但判词不依赖配置状态：
 * 说"打开严格模式就能看到正文"在任何配置下都是错的。
 */
const REVERSED_STRICT = /(打开|开启|启用|开启一下|设成|设置成)[^。！？\n]{0,6}(严格模式|严格档|strict)/

/** 同一句里如果出现这些词，说明它在讲"保护隐私"这个**正确方向**，不是在看正文 */
const PROTECTIVE_INTENT = /(保护|避免|防止|安全|不泄露|更稳|更保险|谨慎)/

/** 答复是否把"打开严格模式"当成了「能看到正文」的手段 */
export function reversedPrivacyDirection(reply: string): string | null {
  const text = (reply ?? '').trim()
  if (!text) return null
  if (!REVERSED_STRICT.test(text)) return null
  // **不误伤正确方向**（2026-11-24 自己撞出来的）：「你可以开启严格模式保护隐私」是对的，
  // 方向没错——strict 确实更保护隐私。错的只有"打开它就能**看到正文**"。
  // 实测第一版没有这道闸，把正确说法也判红了。
  if (PROTECTIVE_INTENT.test(text)) return null
  return '把"打开严格模式"说成了能看到正文的手段（方向反了）'
}

/**
 * 方向说反 → 顶回去一次。
 *
 * 这类**不适合静默改写**：正确的句子要重写整段（"离开 strict"而不是"打开 strict"），
 * 删句会删掉"怎么看正文"这条有用信息。所以只顶回、不兜底——与 `impossibleOffer`
 * 那类"直接删掉承诺句"不同。
 */
export function reversedPrivacyPushback(reply: string): Pushback | null {
  const what = reversedPrivacyDirection(reply)
  if (!what) return null
  return {
    mechanism: 'reversed-privacy-direction',
    reason: what,
    message: '注意：你把方向说反了。**strict 是"屏蔽聊天正文"的模式**——当前就是它，'
      + '所以要看到正文必须**离开** strict（改成 balanced），不是"打开严格模式"。'
      + '请重说这一句：如果用户想看到正文，告诉他改成 balanced；如果你不知道该怎么说，'
      + '就说"当前是严格模式，正文被屏蔽了"，不要给方向。',
  }
}

/**
 * 破坏性请求被拒之后，答复里**不许再顺手提议别的动作**。
 *
 * 2026-11-20 实测：「把彪弟的消息全删了吧」→「我无法帮你删除聊天记录…要不我帮你把彪弟的
 * 聊天记录**导出备份**一下？」拒绝是对的，但用户这句话要的是"毁掉"，这时把话题引向
 * "把内容搬出来一份"是在**扩大**动作范围。正确做法是拒绝 + 说明可以自己做什么，不加新动作。
 */
export function destructiveFollowUpPushback(input: {
  userText: string
  reply: string
}): Pushback | null {
  if (!DESTRUCTIVE_REQUEST.test(input.userText ?? '')) return null
  const bothers = /(导出|备份|保存|整理|转发)(一下)?(聊天|记录|消息|一份)?/
  if (!bothers.test(input.reply ?? '')) return null
  return {
    mechanism: 'offer-after-destructive',
    reason: '用户要的是破坏性动作，被拒之后又顺口提议了别的动作',
    message: '注意：用户这句话要的是删除/清空这类破坏性动作，你拒绝是对的，但**不要顺手提议'
      + '另一个动作**（比如导出、备份、整理）。这会把"毁掉"变成"搬出来一份"，扩大了用户'
      + '没要求的范围。直接拒绝 + 告诉他可以自己在微信客户端做，就够了。',
  }
}

/**
 * 用户给了**明确的过去时间窗**，而这一轮一个工具都没调 → 顶回去。
 *
 * 2026-11-20 实测：问「上个月公众号里讲 AI Agent 的文章，主要说了什么」，它答的是
 * **本月 11-19** 的两篇（视频审片 / 确实型人格）——因为前两轮对话里已经出现过"今天日报推了
 * 12 篇 AI 文章"，它把**对话历史里的旧摘要**当成答案，还把日期说成"上个月"。
 *
 * 判据只用两样：用户原话里的时间窗 + 本轮工具调用为 0。两条都成立才算，
 * 因为"上周的待办"这类问题一旦调了工具就不该拦（工具自己会处理窗口）。
 */
const PAST_WINDOW_CUES =
  /(上个月|上月|去年|上一年|上周|上上周|前年|几个月前|上上周|半个月前|半年|年前|早前)/

export function timeWindowWithoutLookup(input: {
  userText: string
  toolNames: Iterable<string>
}): Pushback | null {
  const text = (input.userText ?? '').trim()
  if (!text) return null
  const hit = text.match(PAST_WINDOW_CUES)
  if (!hit) return null
  for (const _ of input.toolNames) return null      // 调过任何工具就交给工具
  return {
    mechanism: 'past-window-without-lookup',
    reason: `用户给了过去时间窗「${hit[1]}」但这一轮没查任何工具`,
    message: `注意：用户问的是「${hit[1]}」的事，而这一轮你**一个工具都没调用**——`
      + '那就只有两种可能：你在拿前面几轮对话里的旧摘要当答案（日期还对不上），或者你在编。'
      + '现在必须真的去查：用带时间范围的工具（会话/消息/知识库/日报要看清它覆盖的日期窗口，'
      + '日报通常只覆盖最近几天，落不进「' + hit[1] + '」就别拿它充数），'
      + '然后按工具实际返回的日期回答；查不到就直说「' + hit[1] + '这块没有记录」。',
  }
}
