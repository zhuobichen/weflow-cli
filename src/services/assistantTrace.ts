/**
 * 助手一轮的**决策轨迹**：它判断了什么、调了哪个工具、调了几轮、为什么停下。
 *
 * 为什么要有：助手此前对外只吐一句答复，中间过程全在审计里散成一行行事件
 * （`CLOUD_CALL` / `TOOL:x` / `TURN_DONE tools=N`）——`TURN_DONE tools=5` 只告诉你有 5 次
 * 工具调用，不告诉你是哪 5 次、按什么顺序、每次带什么参数。排查"它为什么答成这样"时，
 * 缺的正是这一层。
 *
 * ## 与审计的分工（别混）
 *
 * - **审计**（`assistant_audit.log`）：只记事件与字节量，**绝不记内容**，且是出境记录。
 * - **轨迹**（`assistant_trace.jsonl`）：记这一步是**怎么走的**，包括工具参数的摘要——
 *   所以它是本地排查用的，不是给第三方看的。参数摘要**过一遍 `privacyGate.redact`**，
 *   值截断，和别处的出境纪律一致。
 *
 * ## 思维链
 *
 * 轨迹里的 `reasoning` 是模型**真返回**的推理内容（OpenAI 兼容接口的 `reasoning_content`）。
 * 当前默认模型 `deepseek-chat` 不返回它（那是 `deepseek-reasoner` 一类模型的行为），
 * 所以这个字段平时是空的——**空就是空，不假装有**。`reasoningChars` 会如实记 0。
 */
import { appendFileSync, existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { homedir } from 'node:os'
import { privacyGate } from './assistantPrivacy.js'

const TRACE_FILE = join(homedir(), '.weflow-cli', 'assistant_trace.jsonl')
/** 单条记录里推理内容最多留多少字（它是模型的独白，不是给用户看的东西） */
const REASONING_KEEP = 800
/** 单个参数值最多留多少字 */
const ARG_VALUE_KEEP = 40
/** 文件超过这个大小就裁到最近 `TRACE_KEEP` 条 */
const TRACE_MAX_BYTES = 512 * 1024
const TRACE_KEEP = 200

export type StopReason = 'answered' | 'rounds-exhausted' | 'llm-error' | 'builtin' | 'denied'

export interface ToolStep {
  kind: 'tool'
  name: string
  /** 参数摘要（已脱敏、已截断） */
  args: string
  bytes: number
  /** 这次**有没有给出内容**。判据与边界见 `assistantTools.producedContent` */
  produced: boolean
}

export interface NoteStep {
  kind: 'route' | 'round' | 'note'
  detail: string
}

export type TraceStep = ToolStep | NoteStep

export interface TurnTrace {
  at: string
  /** 会话/用户 id。**只留在本地文件里**，微信里那条 `轨迹` 不打印它 */
  userId: string
  questionChars: number
  steps: TraceStep[]
  rounds: number
  toolCalls: number
  /** 模型真返回的推理内容；当前默认模型不返回，所以通常是空串 */
  reasoning: string
  reasoningChars: number
  answerChars: number
  stop: StopReason
  elapsedMs: number
  error?: string
}

/** 工具参数的摘要：`contact=甲, limit=20`。过脱敏、值截断。 */
export function summarizeArgs(args: Record<string, unknown>): string {
  const parts: string[] = []
  for (const [key, value] of Object.entries(args ?? {})) {
    let text: string
    if (value === null || value === undefined) text = String(value)
    else if (typeof value === 'object') text = Array.isArray(value) ? `[${value.length} 项]` : '{…}'
    else text = String(value)
    if (text.length > ARG_VALUE_KEEP) text = text.slice(0, ARG_VALUE_KEEP) + '…'
    parts.push(`${key}=${text}`)
  }
  const joined = parts.join(', ')
  return privacyGate.redact(joined).safe
}

function trimIfLarge(): void {
  try {
    if (!existsSync(TRACE_FILE) || statSync(TRACE_FILE).size <= TRACE_MAX_BYTES) return
    const lines = readFileSync(TRACE_FILE, 'utf8').split('\n').filter(Boolean)
    writeFileSync(TRACE_FILE, lines.slice(-TRACE_KEEP).join('\n') + '\n', 'utf8')
  } catch { /* 裁剪失败不该影响记录 */ }
}

/** 记一轮。失败不抛——排查用的东西不该把对话弄挂。 */
export function recordTurn(trace: TurnTrace): void {
  try {
    // 目录要先建：新机器上 `~/.weflow-cli` 可能还不存在，而 `appendFileSync` 会抛 ENOENT——
    // 被下面那个 catch 吞掉，就成了"轨迹静默地永远不落盘"（审计那边一开始就有这一步，
    // 轨迹漏了；写这条测试时被自己抓到）。
    const dir = join(homedir(), '.weflow-cli')
    if (!existsSync(dir)) mkdirSync(dir, { recursive: true })
    trimIfLarge()
    appendFileSync(TRACE_FILE, JSON.stringify(trace, null, 0) + '\n', 'utf8')
  } catch { /* 记录失败不阻断 */ }
}

/** 读最近 `n` 轮（新→旧）。读不出来就给空数组，不抛。 */
export function readTurns(n = 5): TurnTrace[] {
  try {
    const lines = readFileSync(TRACE_FILE, 'utf8').split('\n').filter(Boolean)
    return lines.slice(-n).map(line => {
      try { return JSON.parse(line) as TurnTrace } catch { return null }
    }).filter(Boolean).reverse() as TurnTrace[]
  } catch {
    return []
  }
}

export function traceFile(): string {
  return TRACE_FILE
}

/** 一轮的推理内容该留多少。截断时留个记号，别让"截断"读起来像"说完了"。 */
export function clipReasoning(text: string): string {
  const trimmed = (text ?? '').trim()
  return trimmed.length <= REASONING_KEEP ? trimmed : trimmed.slice(0, REASONING_KEEP) + '…'
}

const STEP_LABEL: Record<string, string> = {
  route: '判断层', round: '模型往返', note: '说明', tool: '工具',
}

/** 给终端看的多行描述。纯函数，好测。 */
export function describeTurn(trace: TurnTrace): string[] {
  const lines: string[] = []
  const stamp = (trace.at || '').replace('T', ' ').slice(0, 19)
  lines.push(`${stamp}  ${trace.elapsedMs}ms  往返 ${trace.rounds} 次 · 工具 ${trace.toolCalls} 次 · 结束于「${trace.stop}」`)
  for (const step of trace.steps) {
    if (step.kind === 'tool') {
      const mark = step.produced ? '' : '  （无内容）'
      lines.push(`  · ${STEP_LABEL.tool} ${step.name}(${step.args}) → ${step.bytes} 字节${mark}`)
    } else {
      lines.push(`  · ${STEP_LABEL[step.kind] ?? step.kind}: ${step.detail}`)
    }
  }
  if (trace.reasoningChars) {
    lines.push(`  · 推理 ${trace.reasoningChars} 字${trace.reasoning ? '：' : '（模型这次没有返回内容）'}`)
    for (const line of (trace.reasoning || '').split('\n')) lines.push(`      ${line}`)
  } else {
    lines.push('  · 推理: 无（当前模型不返回 reasoning_content）')
  }
  if (trace.error) lines.push(`  · 出错: ${trace.error}`)
  return lines
}

/**
 * 微信里 `轨迹` 那条要短。**不打印 userId**——它是账号标识，没必要出现在聊天里。
 */
export function describeForChat(trace: TurnTrace | undefined): string {
  if (!trace) return '还没有可看的轨迹：这条之前的记录不在本机（或这是第一条消息）。'
  const lines: string[] = [`上一轮（${(trace.at || '').slice(11, 19)}，${trace.elapsedMs}ms，往返 ${trace.rounds} 次）`]
  for (const step of trace.steps) {
    if (step.kind === 'tool') {
      lines.push(`· ${step.name}(${step.args}) → ${step.bytes} 字节${step.produced ? '' : '（无内容）'}`)
    } else {
      lines.push(`· ${step.detail}`)
    }
  }
  lines.push(`· 结束: ${trace.stop}`)
  lines.push(trace.reasoningChars
    ? `· 推理: ${trace.reasoningChars} 字（当前模型${trace.reasoning ? '有返回' : '只报了长度'}）`
    : '· 推理: 无（当前模型不返回 reasoning_content）')
  return lines.join('\n')
}
