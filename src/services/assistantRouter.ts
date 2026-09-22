/**
 * 单轮快路径的路由（D-035）。
 *
 * ReAct 循环对"从本地数据答一句"这类问题要两次往返：第一轮挑工具与参数，第二轮拿结果组织
 * 回答。这里在循环之前先用一次判断层调用把 `{要不要工具, 哪个能力}` 定下来，执行掉那一个
 * 工具，于是循环的第一轮就已经看得到结果——**两次往返变一次**。
 *
 * 三条边界，都是刻意的：
 *
 * 1. **只路由参数封闭或为空的工具**。"查我和某某的聊天"里的联系人、"搜收藏里的关键词"里的
 *    关键词都是自由文本，判断层给不出来（它只能从封闭集合里选）。硬拿用户整句话当参数，
 *    正好制造 D-035 点名的那个失败模式：**工具挑错，模型拿着一份不相关的结果自信地作答**。
 *    所以能力表里只有 6 个工具、9 个"能力"，其余一律回退到循环。
 * 2. **拿不准就回退**。回退路径必须与今天逐字段一致（有测试断言这一点），而不是"一个更差的
 *    新兜底"。
 * 3. **判断走本机既有的 `scripts/decide.py`**，不在 TS 里再写一个判断层客户端——同一份契约
 *    两份实现，这个仓库已经被咬过（`noul` 被当成布尔的教训）。
 *
 * 隐私：出境的是**用户这条消息本身**加一份固定的能力表说明。消息本来就会发给配置的 LLM
 * （生成回答时），所以这不是新的出境类别；但这里的 state **不含**历史对话与任何本地数据。
 */
import { runPythonJson } from './pythonBridge.js'

export type FastRouteMode = 'off' | 'log' | 'on'

/** 能力 = 工具 + 一组固定参数。名字要能在日志与审计里一眼看懂 */
export interface RouteCapability {
  name: string
  tool: string
  args: Record<string, unknown>
  /** 写进 criteria 的判据：**同时说明它假定了什么查法**（参数表就藏在这句话里） */
  when: string
}

export const NO_CAPABILITY = 'none'

export const FAST_ROUTE_CAPABILITIES: RouteCapability[] = [
  { name: 'list_sessions', tool: 'list_sessions', args: {},
    when: '用户想知道最近在和谁聊天、有哪些会话' },
  { name: 'get_stats', tool: 'get_stats', args: {},
    when: '用户问本机数据的统计概览（会话数、收藏总数）' },
  { name: 'get_daily_report', tool: 'get_daily_report', args: {},
    when: '用户问今天或最近公众号推了哪些文章、日报里有什么' },
  { name: 'sns_timeline', tool: 'get_sns', args: { mode: 'timeline' },
    when: '用户问朋友圈最近有什么动态' },
  { name: 'sns_stats', tool: 'get_sns', args: { mode: 'stats' },
    when: '用户问朋友圈的统计（发了多少、多少人发）' },
  { name: 'weread_shelf', tool: 'get_weread', args: { mode: 'shelf' },
    when: '用户问自己在读什么书、书架有什么' },
  { name: 'weread_notebooks', tool: 'get_weread', args: { mode: 'notebooks' },
    when: '用户问自己的读书笔记' },
  { name: 'todos_pending', tool: 'get_todos', args: { status: 'pending' },
    when: '用户问有什么待办、要做的事' },
  { name: 'todos_done', tool: 'get_todos', args: { status: 'done' },
    when: '用户问已经完成的事' },
]

/** 两次概率都过线才算路由成功；改这两个数等于改行为，测试会盯着 */
export const MIN_NEEDS_TOOL = 0.5
export const MIN_CONFIDENCE = 0.6

export function buildRouteRequest(message: string, capabilities = FAST_ROUTE_CAPABILITIES): unknown {
  const criteria: Record<string, string> = {}
  for (const capability of capabilities) criteria[capability.name] = capability.when
  criteria[NO_CAPABILITY] =
    '上面那些都不对：要么不需要查本机数据（闲聊、常识、写作、翻译、先问清楚），'
    + '要么需要查但不在这几种查法里（例如要点名某个人、某个关键词，而上面每一项都没有这个参数）'

  return {
    state: `用户发来的消息：${message}`,
    questions: {
      needs_local_data: {
        type: 'noul',
        instructions: '要回答这条消息，是否必须去查本机保存的数据（聊天记录、朋友圈、'
          + '读书数据、待办、公众号日报）？仅凭常识或对话本身就能回答的不算。',
      },
      capability: {
        type: 'choice',
        instructions: '假设这条消息必须查本机数据：要查的是哪一项？每一项都写明了它假定的查法；'
          + '拿不准、或消息还需要先问清楚，就选 ' + NO_CAPABILITY + '。',
        criteria,
      },
    },
  }
}

export interface RouteDecision {
  /** 定下来的能力；null 表示回退到正常循环 */
  capability: RouteCapability | null
  needsTool: number
  confidence: number
  /** 实际服务我们的判断模型版本（来自 decide.py 的返回），落进日志 */
  model: string
  /** 为什么路由 / 为什么回退——日志与审计里要看的就是这句 */
  reason: string
}

interface RouterOptions {
  /** 注入的判断调用（测试用）。默认走本机 decide.py */
  runDecide?: (request: unknown) => Promise<any>
  capabilities?: RouteCapability[]
  minNeedsTool?: number
  minConfidence?: number
}

/** 本机判断层的默认出口：request 从 stdin 进，答案从 stdout 出，路径里没有密钥。
 *
 *  走 pythonBridge（唯一的脚本调用入口）——此前这里自己 spawn 了一遍，与 get_todos 那份
 *  各写一套超时与错误处理，正是"同一件事两处实现"的老毛病。
 */
async function runDecideViaPython(request: unknown): Promise<any> {
  const result = await runPythonJson(
    'decide.py', ['--request', '-'], { stdin: JSON.stringify(request), timeoutMs: 60_000 })
  if (!result.ok) {
    // 调用方（decideRoute）用抛错来表达"判断层不可用"，这里保持那个契约
    throw new Error(`${result.error}${result.stderr ? " · " + result.stderr : ""}`)
  }
  return result.data
}

function asProbability(value: unknown): number | null {
  // `noul` 是**概率浮点**，不是布尔。两种读错都要挡：
  //   `Number(true)` 是 1 —— 布尔会被当成"概率 1.0"，于是**确定地**路由，比读成否更危险；
  //   `Number('')` 是 0 —— 空串会被当成"确定不需要"。
  // 所以只认数字与像数字的字符串，别的一律当"契约变了"，由调用方回退。
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

/**
 * 问一次：这条消息该不该走快路径。
 *
 * **任何不确定都回退**：判断层报错、概率不是数、能力名不认识、`choice` 与概率不一致——
 * 全部返回 `capability: null`，让调用方走原本的循环。回退不是降级路径，它就是今天的行为。
 */
export async function decideRoute(message: string, options: RouterOptions = {}): Promise<RouteDecision> {
  const capabilities = options.capabilities ?? FAST_ROUTE_CAPABILITIES
  const minNeedsTool = options.minNeedsTool ?? MIN_NEEDS_TOOL
  const minConfidence = options.minConfidence ?? MIN_CONFIDENCE
  const run = options.runDecide ?? runDecideViaPython

  const fallback = (reason: string, needsTool = 0, confidence = 0, model = ''): RouteDecision =>
    ({ capability: null, needsTool, confidence, model, reason })

  let response: any
  try {
    response = await run(buildRouteRequest(message, capabilities))
  } catch (error: any) {
    return fallback(`判断层不可用: ${error?.message?.slice(0, 120) ?? error}`)
  }
  if (!response || response.success === false) {
    return fallback(`判断层返回失败: ${String(response?.error ?? '未知').slice(0, 120)}`)
  }

  const answers = response.answers ?? {}
  const model = String(response.model ?? '')
  const needsTool = asProbability(answers.needs_local_data?.noul)
  if (needsTool === null) {
    return fallback('判断层没给出「要不要查本机数据」的概率', 0, 0, model)
  }
  if (needsTool < minNeedsTool) {
    return fallback(`不需要查本机数据 (${needsTool.toFixed(2)})`, needsTool, 0, model)
  }

  const capabilityAnswer = answers.capability ?? {}
  const picked = String(capabilityAnswer.choice ?? '')
  const confidence = asProbability(capabilityAnswer.confidence) ?? 0
  if (picked === NO_CAPABILITY || !picked) {
    return fallback(`判断层认为不需要工具 (${picked || '空'})`, needsTool, confidence, model)
  }
  const capability = capabilities.find(item => item.name === picked)
  if (!capability) {
    // 能力表是封闭的：出现陌生名字说明契约变了，回退而不是猜
    return fallback(`判断层给了一个不在能力表里的名字: ${picked}`, needsTool, confidence, model)
  }
  if (confidence < minConfidence) {
    return fallback(`路由置信度不足 (${confidence.toFixed(2)} < ${minConfidence})`, needsTool, confidence, model)
  }
  return {
    capability,
    needsTool,
    confidence,
    model,
    reason: `路由到 ${capability.name}（${capability.tool}，needs_tool=${needsTool.toFixed(2)}，`
      + `confidence=${confidence.toFixed(2)}）`,
  }
}
