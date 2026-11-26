/**
 * Jev 多工具判断（D-191）：**一次调用**问"这条消息可能要用到哪些工具"，
 * 每个工具一道独立的 `noul` 判断题，返回过线的工具集合。
 *
 * 与 `assistantRouter`（fastRoute，单选 `choice`）的区别：
 * - fastRoute 只从封闭集合选**一个**工具，且要参数封闭（因为自己执行）；
 * - 这里每个工具独立打分，**支持一次选多个**，且不执行、不填参数——只把
 *   "Jev 认为要用这些"作为**保底信号**交给大模型，由它做第二次判断并填参执行。
 *
 * 为什么用 noul 而不是 choice：`choice` 返回互斥概率（和≈1、强制 argmax），
 * 只能单选；`noul` 是独立 0~1 概率，N 道题可同时为高。实测一次调用：
 *   "整理甲乙聊天 + 看待办 + 今天公众号推了什么" →
 *   get_messages 0.90 / get_todos 0.95 / get_daily_report 0.90 / get_stats 0.09
 * 成本 ~$0.000018，延迟几乎不随问题数增长（项目实测 2 题 0.84s / 12 题 0.91s）。
 *
 * 隐私：交给 Jev 的只有用户这一句话 + 固定的工具说明，不含历史与本机数据。
 * 复用 `pythonBridge` 调 decide.py——不在 TS 里另写判断客户端（同一契约两份实现
 * 是本项目反复踩的坑，见 assistantRouter 头部）。
 */
import { runPythonJson } from './pythonBridge.js'

/** 参与保底判断的工具：只列"查本机数据"的读类工具，写类/闲聊类不进这个集合 */
export interface MultiTool {
  name: string
  when: string
}

export const MULTI_TOOLS: MultiTool[] = [
  { name: 'get_messages', when: '查某个具体联系人或群的聊天记录（内容、时间、原文）' },
  { name: 'search_chats', when: '按关键词或语义在聊天里找某件事、某句话' },
  { name: 'list_sessions', when: '看最近在和谁聊天、有哪些会话' },
  { name: 'get_todos', when: '看待办清单' },
  { name: 'get_daily_report', when: '看公众号最近推了哪些文章、日报内容' },
  { name: 'get_sns', when: '看朋友圈动态或统计' },
  { name: 'get_weread', when: '看书架、读书笔记' },
  { name: 'get_stats', when: '看本机数据统计概览（会话数、收藏数等）' },
  { name: 'search_knowledge', when: '查本地知识库/概念页里的知识：问某个概念是什么、之前整理过什么、知识库里怎么讲X' },
  { name: 'search_memory', when: '查长期记忆：问"你记得我说的X吗""之前记住的Y是什么""我的偏好/项目背景"' },
  { name: 'read_document', when: '读本地文件内容：PDF/Word/Excel/PPT，或"看下这个文件""这个pdf讲了什么"' },
  { name: 'web_search', when: '查实时/外部公开信息：今天的新闻、某个外部事实、联网搜索' },
]

export const DEFAULT_MULTI_THRESHOLD = 0.5

/** 组装一次 decide.py 请求：N 个独立 noul 问题 */
export function buildMultiRequest(message: string, tools = MULTI_TOOLS): unknown {
  const questions: Record<string, unknown> = {}
  for (const tool of tools) {
    questions[tool.name] = {
      type: 'noul',
      instructions: `要回答用户这条消息，是否需要调用 ${tool.name}？${tool.when}。`
        + '仅凭常识或对话本身就能回答的不算；不需要就给接近 0 的概率。',
    }
  }
  return { state: `用户发来的消息：${message}`, questions }
}

export interface MultiDecision {
  /** 过线的工具名（概率 ≥ threshold） */
  selected: string[]
  /** 每个工具的原始概率，便于日志/审计与阈值调整 */
  scores: Record<string, number>
  /** 判断层实际模型版本（落日志，不写死 jev-latest） */
  model: string
  /** 没选上/判断失败的原因 */
  reason: string
  /** 判断层是否可用（失败时 false，调用方按原样回退） */
  ok: boolean
}

function asProb(v: unknown): number | null {
  if (typeof v === 'number' && Number.isFinite(v)) return v
  return null
}

/** 从 decide.py 的返回解析出多个工具概率。任何契约异常 → ok:false 回退 */
export function parseMultiResponse(response: any, threshold = DEFAULT_MULTI_THRESHOLD): MultiDecision {
  const fail = (reason: string): MultiDecision =>
    ({ selected: [], scores: {}, model: '', reason, ok: false })
  if (!response || response.success === false) {
    return fail(`判断层返回失败: ${String(response?.error ?? '未知').slice(0, 120)}`)
  }
  const answers = response.answers ?? {}
  const model = String(response.model ?? '')
  const scores: Record<string, number> = {}
  for (const tool of MULTI_TOOLS) {
    const p = asProb(answers[tool.name]?.noul)
    if (p === null) return fail(`判断层没给 ${tool.name} 的概率（契约变了）`)
    scores[tool.name] = p
  }
  const selected = MULTI_TOOLS.filter(t => scores[t.name] >= threshold).map(t => t.name)
  return { selected, scores, model, ok: true,
    reason: selected.length ? `建议工具: ${selected.join(', ')}` : '这一轮不需要查本机数据' }
}

/** 默认出口：调 decide.py，复用 pythonBridge 的唯一脚本入口 */
async function runMultiViaPython(request: unknown): Promise<any> {
  const result = await runPythonJson(
    'decide.py', ['--request', '-'], { stdin: JSON.stringify(request), timeoutMs: 60_000 })
  if (!result.ok) throw new Error(`${result.error}${result.stderr ? ' · ' + result.stderr : ''}`)
  return result.data
}

/** 问一次 Jev：这条消息可能要用哪些工具。异常一律回退（保底坏了不能让助手停摆） */
export async function decideMultiTools(input: {
  message: string
  runDecide?: (request: unknown) => Promise<any>
  threshold?: number
  tools?: MultiTool[]
}): Promise<MultiDecision> {
  const run = input.runDecide ?? runMultiViaPython
  const threshold = input.threshold ?? DEFAULT_MULTI_THRESHOLD
  try {
    const response = await run(buildMultiRequest(input.message, input.tools))
    return parseMultiResponse(response, threshold)
  } catch (error: any) {
    return { selected: [], scores: {}, model: '', ok: false,
      reason: `判断层不可用: ${String(error?.message ?? error).slice(0, 120)}` }
  }
}
