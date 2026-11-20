/**
 * 词法快路径：**不进模型**，用一张"说法 → 工具"的表把整句话对上（D-168）。
 *
 * ## 为什么要有它
 *
 * `assistantRouter.ts` 那条快路径要先问一次判断层（`decide.py` → Jev）。判断层是强模型，
 * 所以它自己不吃亏；但整条链的**应答模型**如果是便宜的小模型，两次往返里真正难的那次
 * （从 31 个工具里挑一个、还要给参数）就是小模型在做——实测这一步错得最多。
 *
 * 词法路由把这一步搬到确定性的代码里：**零调用、零成本、零抖动**。模型只需要做它本来
 * 擅长的事——拿一份工具结果，把它说成人话。
 *
 * ## 三条刻意的边界
 *
 * 1. **只收"参数为空或封闭"的意图**。要自由参数的（点名某个联系人、搜某个关键词、
 *    导出到某个文件）一律不碰：拿用户整句话当参数正好制造"工具挑错、模型拿着不相关
 *    的结果自信作答"那个失败模式（见 `assistantRouter.ts` 的注释）。
 * 2. **拿不准就不路由**。这条表必须宁缺毋滥——它默认是开的，误命中会白调一个工具。
 *    所以有全局否决（下面 `GLOBAL_VETOES`）：引号里点名、问"怎么用/支持吗"、
 *    一条消息里两个问号，全部交给 ReAct 循环。
 * 3. **误命中是可恢复的**。预派发**不结束这一轮**：工具结果进对话之后循环照跑，模型还能
 *    再调工具。这与"答错"不是一个量级的代价——这是它敢默认开着的理由。
 *
 * 与 `assistantRouter.ts` 的分工：那张表服务**判断层**（9 个能力，既要名字也要判据文本）；
 * 这张表服务**确定性代码**（可以更大，因为不需要向模型解释）。两张表分开是故意的：往这张表
 * 加一条不改变判断层的选择空间，也就不会让付费那条路变得更难选。
 */
import type { RouteCapability } from './assistantRouter.js'

/** 规范化：去空白与常见标点，让中文口语里的空格/标点不影响命中 */
function normalize(text: string): string {
  return (text ?? '')
    .replace(/\s+/g, '')
    .replace(/[，。！？、；：,.!?;:~～·…]/g, '')
}

/**
 * 全局否决：命中任意一条就整句不路由。
 *
 * 这几条都是**实测撞过或显然会撞**的：
 * - 引号里点名 → 需要自由参数（"把「买牛奶」那条待办标记成已完成"那种，要认出是哪一条）；
 * - 问功能本身 → 不是要数据，是要说明书；
 * - 两个问号 → 一条消息两个问题，一个工具答不完。
 *
 * **这里不写用例 id、也不抄用例问句**：评测的价值全在"问法是不是真的"，运行时代码里
 * 一旦出现某条例句，那条用例就再也测不出东西。`test/assistant-eval-leak-guard.test.ts` 盯着这条。
 */
const GLOBAL_VETOES: RegExp[] = [
  /「|」|『|』|“|”|"\s*[^"]{1,20}\s*"/,
  /(怎么|如何)(用|弄|看|配置|操作|设置)/,
  /(支持|能不能|可不可以|是否可以).{0,8}(吗|？|\?)/,
  /有什么不同|有什么区别|是什么意思/,
  /.{0,}[？?].{0,}[？?]/,
]

/** 一条意图：组内任一命中、每一组都要命中，才路由；`deny` 命中任一则放弃 */
export interface IntentRule {
  /** 目标能力：工具 + 固定参数 */
  capability: RouteCapability
  /**
   * 每组是一个同义词集合，**组与组之间是"且"**。
   * 用"话题 + 意图"两组，比写一条 `.{0,8}` 的中文正则稳——后者换个语序就断。
   */
  all: string[][]
  /** 命中任意一条就放弃这条规则（近义说法抢词时用） */
  deny?: RegExp[]
}

const cap = (name: string, tool: string, args: Record<string, unknown>): RouteCapability =>
  ({ name, tool, args, when: '' })

/**
 * 表。**顺序有意义**：先命中先返回，所以更具体的规则要排在更宽的规则前面
 * （例如"朋友圈发得最多"要先于"朋友圈最近有什么"）。
 *
 * 每条都要能在评测集里找到至少一句真实问法——加规则时顺手在
 * `test/assistant-intent.test.ts` 里补一行，否则下一个人不知道它为什么在那儿。
 */
export const INTENT_RULES: IntentRule[] = [
  // —— 公众号：先"发得最多"（阅读统计），再"日报" ——
  { capability: cap('reading_stats', 'get_reading_stats', { days: 7 }),
    all: [['最多', '排行', '排名', '最活跃'], ['公众号', '号']] },
  { capability: cap('daily_report', 'get_daily_report', {}),
    all: [['日报', '公众号'], ['今天', '今日', '最近', '推', '发', '文章', '有']],
    // ⚠ 过去时间窗必须 deny：日报只覆盖最近几天，用它回答「上个月的文章」必然答错，
    // 而实测（2026-11-20）它还会把结果**说成**是上个月的。宁可不走词法，交给模型去查知识库。
    deny: [/最多/, /排版/, /主题/, /概念/, /知识库/,
      /上个月|上月|去年|上一年|上周|上上周|前年|几个月前|半年前|年初|去年底/] },

  // —— 朋友圈：统计先于时间线 ——
  { capability: cap('sns_stats', 'get_sns', { mode: 'stats' }),
    all: [['朋友圈'], ['统计', '多少', '几个人', '谁最', '最多']] },
  { capability: cap('sns_timeline', 'get_sns', { mode: 'timeline' }),
    all: [['朋友圈'], ['最近', '动态', '什么', '发了', '有']],
    deny: [/统计/, /多少/, /几个人/, /最多/] },

  // —— 微信读书：笔记先于书架 ——
  { capability: cap('weread_notebooks', 'get_weread', { mode: 'notebooks' }),
    all: [['笔记'], ['书', '本']], deny: [/书评/, /排版/] },
  { capability: cap('weread_shelf', 'get_weread', { mode: 'shelf' }),
    all: [['书'], ['在读', '书架', '微信读书', '在读什么']], deny: [/笔记/, /排版/, /书评/] },

  // —— 待办：完成先于待办（"标记成已完成"里有"待办"两个字） ——
  { capability: cap('todos_done', 'get_todos', { status: 'done' }),
    all: [['待办', '任务'], ['已完成', '做完', '完成了']],
    deny: [/标记/, /改成/, /设为/, /把/] },
  { capability: cap('todos_pending', 'get_todos', { status: 'pending' }),
    all: [['待办', '要做', '要办'], ['什么', '哪些', '有', '还有', '多少']],
    deny: [/标记/, /改成/, /设为/, /完成/, /做完/] },

  // —— 学习回顾 ——
  { capability: cap('learning_review', 'get_review', {}),
    all: [['学习回顾', '回顾'], ['什么', '写了', '内容', '有', '最近']],
    deny: [/一下/, /我们/] },

  // —— 知识库：概念总览 / 体检 ——
  { capability: cap('wiki_lint', 'lint_wiki', {}),
    all: [['知识库', 'wiki', '维基'], ['坏链接', '孤儿', '体检', '问题', '检查']] },
  // 第二组**只收"要总览"的说法**（总览/存了/索引…），不收"哪些/什么"：后者也会命中
  // 「关于某个技术，我整理过哪些概念页」——那句话要的是 `search_knowledge`（按主题搜），
  // 预派发 `get_concepts` 会白调一次，而且模型容易拿总览当成答案。
  { capability: cap('concepts_overview', 'get_concepts', {}),
    all: [['知识库', '概念'], ['总览', '概览', '索引', '存了', '都有', '一共', '多少个']] },

  // —— 技能：体检先于列表 ——
  { capability: cap('skills_check', 'check_skills', {}),
    all: [['技能'], ['问题', '坏', '检查', '体检', '有没有', '能用']] },
  { capability: cap('skills_list', 'list_skills', {}),
    all: [['技能'], ['哪些', '什么', '列表', '装了', '有']] },

  // —— 公众号排版主题 ——
  { capability: cap('themes_list', 'list_themes', {}),
    all: [['主题', '排版'], ['哪些', '什么', '可选', '列表', '有']] },

  // —— 助手自己记得的事：**零参数**（工具拿空关键词就是"全列出来"） ——
  // 与「记住：X」区分开：那是**写**指令，不是查询。
  { capability: cap('memory_overview', 'search_memory', {}),
    all: [['你', '助手'], ['记得我', '记住我', '记着我', '记忆里', '知道我的']],
    deny: [/记住\s*[:：]/, /记一下/, /记下来/] },

  // —— 谁在等我回话（零参数工具；`days` 用默认 14） ——
  { capability: cap('reply_debt', 'who_owes_reply', {}),
    all: [['谁', '哪些人'], ['等我回话', '等我回复', '欠我回复', '没回我', '等我', '回我', '找我', '回一下']] },

  // —— 数量类：直接问"几个/多少个"，不需要模型判断（2026-11-20 实测它没查就答"你不知道"） ——
  { capability: cap('count_sessions', 'list_sessions', {}),
    all: [['几个', '多少个', '多少', '几多'], ['群', '群聊', '会话', '聊天', '联系人']],
    deny: [/群里(聊|说|发)/] },

  // —— 会话列表 ——
  { capability: cap('list_sessions', 'list_sessions', {}),
    all: [['谁', '哪些人', '哪几个人'], ['聊', '联系', '说话', '找']],
    deny: [/回话/, /回我/, /回复/, /导出/, /记(得|住)/] },
  { capability: cap('list_sessions', 'list_sessions', {}),
    all: [['会话', '聊天'], ['列表', '有哪些', '几个']], deny: [/导出/] },

  // —— 本机数据总览：放最后，"多少/统计"这类词最容易被别的意图抢 ——
  { capability: cap('stats_overview', 'get_stats', {}),
    all: [['多少', '总览', '概览', '统计', '总共有'], ['数据', '东西', '会话数', '收藏数']],
    deny: [/待办/, /公众号/, /朋友圈/, /读书/, /知识库/, /概念/, /技能/] },
]

export interface IntentHit {
  capability: RouteCapability
  /** 写进日志与轨迹的一句话，说明**为什么**命中（错了才查得动） */
  reason: string
}

function hitsAny(text: string, words: string[]): string | null {
  for (const word of words) if (text.includes(word)) return word
  return null
}

/**
 * 路由一句话。命中返回能力，否则 null。
 *
 * **纯函数、无 I/O**：评测、测试、灰度统计都直接调它，不需要起助手。
 */
export function routeByIntent(message: string, rules: IntentRule[] = INTENT_RULES): IntentHit | null {
  const raw = (message ?? '').trim()
  if (!raw) return null
  const text = normalize(raw)

  for (const veto of GLOBAL_VETOES) {
    if (veto.test(raw) || veto.test(text)) return null
  }

  for (const rule of rules) {
    if (rule.deny?.some(pattern => pattern.test(raw) || pattern.test(text))) continue
    const matched: string[] = []
    let complete = true
    for (const group of rule.all) {
      const word = hitsAny(text, group)
      if (!word) { complete = false; break }
      matched.push(word)
    }
    if (!complete) continue
    return { capability: rule.capability, reason: `词法命中 ${matched.join(' + ')}` }
  }
  return null
}

/**
 * 抽参数那一档：**只服务 `get_messages` 一个工具**（D-172）。
 *
 * ## 为什么要多这一档
 *
 * 固定参数那档（`INTENT_RULES`）刻意不碰"点名某个人"的问法——参数只能来自用户原话，
 * 而"拿整句话当参数"是已知的失败模式。但「X 最近跟我说了什么」是微信场景里**最高频**的一问，
 * 把它整类交回模型，正是小模型最容易翻车的地方：实测千问 flash 在「某个人 + 前天说了什么」
 * 这类问法上要么不调工具、要么调了却不带时间窗（于是按"最近 N 条"取，取到的根本不是那天）。
 *
 * 所以这一档只做**形态极窄**的抽取：一句"某人 + 跟我说/和我聊 + 时间词"的话，
 * 抽出人名与时间词。窄是刻意的——抽错的时候工具会返回"(找不到联系人…)"，
 * 模型还能改，而抽不出来的形态一律 `null`，老老实实回退到循环。
 *
 * **注释里也不抄用例问句**：评测的价值全在"问法是不是真的"，运行时代码里一旦出现某条例句，
 * 那条用例就再也测不出东西。`test/assistant-eval-leak-guard.test.ts` 盯着这条。
 *
 * ## 为什么人名可以"猜"
 *
 * 工具侧 `resolveTalker` 本来就做重名/找不到的判定（拒绝任何不唯一的结果），
 * 所以这里**不需要保证人名正确，只要保证它是一个候选人名**：猜错的代价是一次
 * "找不到这个人"的工具结果，而不是一个错误答案。
 */
const RELATIVE_WORDS = ['大前天', '前天', '前日', '昨天', '昨日', '今天', '今日', '最近',
  '这几天', '这两天', '这周', '上周', '上个月', '刚才', '刚刚']

/** 时间词在 `resolveSince` 那侧认得（见 `utils/dateRange.ts`），这里只管**找到**它 */
function findTimeWord(text: string): string | null {
  for (const word of RELATIVE_WORDS) if (text.includes(word)) return word
  const daysAgo = text.match(/(\d+)\s*天前/)
  return daysAgo ? `${daysAgo[1]}d` : null
}

const NAME_STOP = new Set(['我', '你', '他', '她', '它', '我们', '他们', '她们', '大家',
  '那个', '这个', '这人', '那人', '有人', '谁', '什么', '哪'])

/** 从"X 跟我说了什么"里取出 X。取不出一个像人名的东西就 null */
function extractContact(prefix: string): string | null {
  let rest = prefix
  // 引号里点名的优先：用户自己标出来的人名，**这是最可靠的一种抽取**
  const quoted = rest.match(/[「『“"]([^」』”"]{1,12})[」』”"]/)
  if (quoted) return quoted[1].trim() || null
  for (const word of RELATIVE_WORDS) rest = rest.split(word).join('')
  rest = rest.replace(/(\d+)\s*天前/g, '')
  // 去掉句首的"我想知道/帮我看看/顺便问下"这类外壳
  rest = rest.replace(/^(?:请问|帮我看看|帮我查查|顺便问下?|我想知道|我想问下?|话说)/, '')
  rest = rest.replace(/^[我你他她它的了吗呢吧啊呀，,、\s]+/, '')
  rest = rest.replace(/[我你他她它的了吗呢吧啊呀，,、\s]+$/, '')
  if (!rest) return null
  // 人名的形状：1-6 个中文/字母/数字，且不是代词、不是疑问词
  if (!/^[\u4e00-\u9fa5A-Za-z0-9_\-]{1,6}$/.test(rest)) return null
  if (NAME_STOP.has(rest)) return null
  if (/[谁什么哪几怎为啥]/.test(rest)) return null
  return rest
}

/**
 * 「X 跟我说了什么」「我和 X 上周聊了什么」「X 后面还说过别的吗」——抽出人名与时间词，
 * 直接派发 `get_messages`。**只在这几种句形上出手**，其余一律 null。
 */
export function routeWithArgs(message: string): IntentHit | null {
  const raw = (message ?? '').trim()
  if (!raw) return null
  // 一条消息两个问号 = 两个问题，一个工具答不完（与固定表同一条否决）。
  // "两件事"走 `routeParts`，那里会分别路由再各派一次。
  if (/.{0,}[？?].{0,}[？?]/.test(raw)) return null
  return extractMessagesHit(raw) ?? extractSearchHit(raw) ?? extractNameLookupHit(raw)
    ?? extractTodoWriteHit(raw)
}

function extractMessagesHit(raw: string): IntentHit | null {
  let contact: string | null = null

  // 形如：<人名>(+时间词) 跟我说/和我聊 了什么
  const before = raw.match(/^(.{1,18}?)(?:都|也)?(?:跟|和|与)我(?:说|聊|讲)/)
  if (before) contact = extractContact(before[1])
  // 形如：我(和|跟)<人名>(+时间词) 聊/说 了什么
  if (!contact) {
    const afterMe = raw.match(/^(?:我)?(?:和|跟|与)(.{1,14}?)(?:都|也)?(?:聊|说|讲|联系|说话)/)
    if (afterMe) contact = extractContact(afterMe[1])
  }
  if (!contact) return null

  const since = findTimeWord(raw)
  const args: Record<string, unknown> = { contact }
  if (since) args.since = since
  return {
    capability: { name: 'messages_of_contact', tool: 'get_messages', args, when: '' },
    reason: `词法抽参数：人名「${contact}」${since ? ` · 时间「${since}」` : ''}`,
  }
}

/**
 * 两类"搜"的抽取（D-173）。
 *
 * 与 `get_messages` 那一档的区别：这两个工具的参数**就是一个主题**，不是人名。
 *
 * - `search_chats` 的契约要的是 `question`——"我的问题"，所以**把用户这句话原样交过去**
 *   完全符合工具语义（它自己会从聊天里挑候选词）。这不是"拿整句话当参数"那个失败模式：
 *   那条说的是"把一个工具的参数塞成整句，指望它去猜"，这里的工具本来就要整句。
 * - `search_knowledge` 要 `keyword`：从「关于 X，我整理过哪些概念页」「了解一下 X 的…」
 *   里抽 X。抽不出来就不出手。
 *
 * `depth: 2` 只在用户**明确要"相关的/一起/展开"**时才给——工具那边 `depth` 是"再走一跳"，
 * 用户没说要看邻居时给 2 会让答复跑题。
 */
const DEPTH_CUE = /(相关|一起|顺着|逐跳|展开|第二跳|两跳)/

function extractSearchHit(raw: string): IntentHit | null {
  // ①「哪个会话里提到过 X」「在哪聊过 X」→ search_chats（参数就是这句话）
  if (/(?:哪个|哪一个|什么)?会话(?:里|中)?(?:提到|说过|聊到|提)过/.test(raw)
      || /(?:哪|什么)(?:儿|里)?(?:聊|说|提)过/.test(raw)
      || /(?:上次|之前)(?:说的|提到的|那个)/.test(raw) && /(?:哪|什么)(?:儿|里)/.test(raw)
      // 「你去聊天记录里查一下」「搜一下聊天记录」：用户已经指名了**要查哪个库**，
      // 而 `search_chats` 的参数契约本来就要整句话（它自己挑词），所以原句交过去是对的。
      || /聊天记录(?:里|中)?(?:查|搜|找)/.test(raw)
      || /(?:查|搜|找)(?:一下)?(?:我的)?聊天记录/.test(raw)) {
    return {
      capability: { name: 'search_chats_by_question', tool: 'search_chats',
        args: { question: raw }, when: '' },
      reason: '词法抽参数：跨会话检索（原句当 question）',
    }
  }

  // ②「关于 X，我整理过哪些概念页」/「了解一下 X 的…」→ search_knowledge
  let keyword: string | null = null
  const about = raw.match(/关于\s*([^，,。？?]{2,16})/)
  if (about) keyword = about[1].trim()
  if (!keyword) {
    const learn = raw.match(/(?:了解|讲讲|说说|介绍)(?:一下)?\s*([A-Za-z0-9][A-Za-z0-9 _\-+.]{1,15}|[\u4e00-\u9fa5]{2,10})\s*的/)
    if (learn) keyword = learn[1].trim()
  }
  if (!keyword) {
    // 「我整理过哪些关于 X 的概念页」的顺序反过来说
    const tail = raw.match(/(?:整理|收录|写过)过?(?:哪些)?\s*([^，,。？?]{2,16}?)\s*(?:概念页|页面|笔记)/)
    if (tail && !/^(哪些|什么|我的)$/.test(tail[1].trim())) keyword = tail[1].trim()
  }
  if (!keyword) return null
  // 剥掉"我/你/这个/那个"这类壳子
  keyword = keyword.replace(/^(?:我|你|这个|那个|一下)/, '').replace(/[的了呢吧啊呀]+$/, '').trim()
  if (keyword.length < 2) return null

  const args: Record<string, unknown> = { keyword }
  if (DEPTH_CUE.test(raw)) args.depth = 2
  return {
    capability: { name: 'search_knowledge_by_keyword', tool: 'search_knowledge', args, when: '' },
    reason: `词法抽参数：知识库关键词「${keyword}」${args.depth ? ' · depth=2' : ''}`,
  }
}

/** 一句话里的两件事：**分开各自路由**，最多两件（三件以上交回循环） */
const SPLIT_CUES = /[？?]|另外|还有|顺便(?:问|说)?[一下]?|以及|另外一个/g

/**
 * 一条消息里有几件事（D-173）。
 *
 * 为什么单独做：「一句话两件事」那种消息里两件事各要一个工具，而**一个工具答不完**。
 * 全局否决里"两个问号不路由"正是为了避免"只顾一件"，但正确的做法不是放弃，而是**分别路由**
 * 再各派一次——前提是每一半都能被高精度命中，否则整体交回循环。
 *
 * 只在出现**两个及以上切分点**时才拆（"还有""另外"单独出现不代表两件事），
 * 且**最多两件**：三件以上说明用户在提一个复合任务，那不是词法表该接的。
 */
export function routeParts(message: string): IntentHit[] {
  const raw = (message ?? '').trim()
  if (!raw) return []
  const cues = raw.match(SPLIT_CUES) ?? []
  const marks = raw.match(/[？?]/g) ?? []
  // 至少两个问号，或者一个问号 + 一个"另外/还有"
  if (!(marks.length >= 2 || (marks.length >= 1 && cues.length >= 2))) return []

  const parts = raw.split(SPLIT_CUES).map(part => part.trim()).filter(part => part.length >= 2)
  if (parts.length < 2 || parts.length > 3) return []

  const hits: IntentHit[] = []
  for (const part of parts) {
    const hit = routeByIntent(part) ?? routeWithArgs(part)
    if (!hit) return []
    // 同一件事（同名能力 + 同参数）不去重重复派发
    if (hits.some(existing => existing.capability.name === hit.capability.name
        && JSON.stringify(existing.capability.args) === JSON.stringify(hit.capability.args))) continue
    hits.push(hit)
    if (hits.length >= 2) break
  }
  return hits.length >= 2 ? hits : []
}

/**
 * 追问：**只靠代词指代上一轮**的那种话（"展开讲讲它" / "详细说说这个"）。
 *
 * 为什么要单独认：这类话里没有任何名词，词法表无从下手，于是模型自己去想"该查什么"——
 * 实测它会在这种时候**换个话题重开**（去查当天日报），而日报只覆盖最近几天，与上一轮那个
 * 概念毫无关系。用上一轮用过的那条检索把它接回去，比让它自由发挥可靠得多。
 */
const FOLLOW_UP_SHAPE = /^(?:再|继续|那|然后)?\s*(?:展开|详细|具体|多说|多讲|讲讲|说说|解释|深入)/
const PRONOUN_CUE = /(它|这个|那个|上面|刚才|这条|那条|这段)/

export function isFollowUp(message: string): boolean {
  const raw = (message ?? '').trim()
  if (!raw || raw.length > 20) return false
  return FOLLOW_UP_SHAPE.test(raw) && PRONOUN_CUE.test(raw)
}

/**
 * 「X 的 <东西> 发我看看」：**先把名字解析出来**，而不是先猜"东西"是什么。
 *
 * 起因是一条诚实性用例：用户说「某某的文档发我看看」，而通讯录里有两个同名的人。实测模型
 * **一次工具都不调**，直接反问「你指的是哪份文档」——它凭空造了一个歧义（文档），
 * 却没碰到真正的歧义（两个同名）。用户答了"哪份文档"也还是查不出来。
 *
 * 正确的一步是先 `list_contacts(keyword=小明)`：名字唯一就继续，不唯一就把两个候选摆出来问。
 * 这也顺带解释了为什么**不**直接 `get_messages`：那要求先知道"是哪一个"，而这正是要查的。
 *
 * 物体里出现"聊天/消息/记录"时才改派 `get_messages`（那时名字就是唯一缺的东西）。
 */
const NAME_LOOKUP_PREFIX = /^(?:把|帮我|给我|麻烦|请)\s*/
const NAME_LOOKUP = /^([\u4e00-\u9fa5A-Za-z0-9_\-]{1,6})\s*的\s*([\u4e00-\u9fa5A-Za-z0-9]{1,10}?)\s*(?:发我|发给我|给我|看看|看一下|发一下)/
/** 单字动词/介词不是名字：「把我的文档发我看看」里的「把」 */
const NOT_A_NAME = new Set(['把', '给', '帮', '请', '和', '跟', '与', '让', '叫', '用',
  '找', '看', '发', '说', '问', '要', '那', '这'])

function extractNameLookupHit(raw: string): IntentHit | null {
  // **先剥"把/帮我/给我"这些外壳再取名字**。不剥的话正则会把「把」当成名字
  // （可选前缀 + 贪婪的候选组，实测取出来就是 keyword='把'）。
  const body = raw.replace(NAME_LOOKUP_PREFIX, '')
  const match = body.match(NAME_LOOKUP)
  if (!match) return null
  const name = match[1].trim()
  if (NOT_A_NAME.has(name) || NAME_STOP.has(name)) return null
  if (/[谁什么哪几怎为啥]/.test(name)) return null
  const object = match[2]
  if (/(聊天|消息|记录|对话|说过)/.test(object)) {
    return {
      capability: { name: 'messages_of_contact', tool: 'get_messages', args: { contact: name }, when: '' },
      reason: `词法抽参数：人名「${name}」+ 聊天记录`,
    }
  }
  return {
    capability: { name: 'resolve_contact_first', tool: 'list_contacts',
      args: { keyword: name, limit: 10 }, when: '' },
    reason: `词法抽参数：先把人名「${name}」解析出来（可能重名）`,
  }
}

/**
 * 「把「X」那条待办标记成已完成」：**先把清单列出来**，再让工具自己去判断"唯一不唯一"。
 *
 * 写操作的安全底线是"工具只认唯一"（`set_todo_status` 在两条都匹配时拒绝），但那条底线
 * 的前提是**模型真的去看过清单**。实测（qwen-plus）它会跳过这一步直接写，或直接问用户——
 * 两种都不对：前者的拒绝理由它没拿到，后者把能查的事推回给用户。
 *
 * 所以这里只做一件最保守的事：派一次**只读**的 `get_todos`。改不改、改哪条，仍然归模型和
 * 工具自己判断——这一档不碰写操作。
 */
function extractTodoWriteHit(raw: string): IntentHit | null {
  if (!/待办/.test(raw)) return null
  if (!/(标记|改成|设为|改为|完成|做完|勾掉)/.test(raw)) return null
  return {
    capability: { name: 'todos_before_write', tool: 'get_todos', args: { status: 'pending' }, when: '' },
    reason: '词法抽参数：先列待办清单（改之前先看，别猜是哪一条）',
  }
}

/**
 * 表自身的体检。**写进测试**，理由是本仓库付过学费的老毛病：注释会过期而不报错。
 *
 * 查三件事：
 * - 能力名不空、工具名不空（拼错的名字只会静默地永不命中）；
 * - 同一能力名不被两条规则用不同的工具/参数定义（同一个名字两种含义）；
 * - 每组都非空（空组等于"永远命中"，会把整张表变成一条无脑规则）。
 */
export function intentRulesProblem(rules: IntentRule[] = INTENT_RULES): string[] {
  const problems: string[] = []
  const byName = new Map<string, string>()
  for (const rule of rules) {
    const { name, tool, args } = rule.capability
    if (!name) problems.push('有一条规则的能力名为空')
    if (!tool) problems.push(`能力 ${name} 没有工具名`)
    const shape = `${tool}:${JSON.stringify(args)}`
    const seen = byName.get(name)
    if (seen && seen !== shape) problems.push(`能力名 ${name} 有两种定义：${seen} 与 ${shape}`)
    else byName.set(name, shape)
    if (!rule.all.length) problems.push(`能力 ${name} 没有 all 组`)
    for (const group of rule.all) {
      if (!group.length) problems.push(`能力 ${name} 有一个空的 all 组`)
    }
  }
  return problems
}
