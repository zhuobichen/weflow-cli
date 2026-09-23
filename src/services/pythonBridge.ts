/**
 * 调本仓 Python 脚本的**唯一入口**：拿 JSON 回来，或者把失败说清楚。
 *
 * 为什么要有这一层：此前有两份实现——`get_todos` 用 `execFile`（参数走 argv），路由用
 * `spawn`（请求走 stdin）。两份各有一套超时、错误、stdout 解析的写法，正是"同一件事两处
 * 实现"的老毛病；而工具只会越加越多。
 *
 * 三条约定：
 * 1. **只有 JSON 出口**：脚本要么给出可解析的 JSON，要么给出错误——不解析人类可读文本
 *    （那种解析在脚本改一行格式时就悄悄失效）。
 * 2. **JSON 可以在最后一行**：有些脚本（如 route_cards）前面会打印给人看的进度行。
 *    所以先试整份 stdout，再退一步取"最后一行能解析成 JSON 的"。
 * 3. **失败要说清是什么失败**：退出码、还是没有 JSON、还是超时——三者分开报，别都变成
 *    "执行失败"。
 */
import { spawn } from 'node:child_process'
import { join } from 'node:path'
import { resolvePackageRoot } from '../utils/packageRoot.js'
import { createPythonProcessEnv } from '../utils/pythonProcessEnv.js'
import { getPythonCommand } from '../utils/python.js'

export interface ScriptRun {
  stdout: string
  stderr: string
  /** 进程退出码；被信号杀死时为 null */
  code: number | null
}

/** 注入点：测试用假 runner 驱动，不 spawn 真进程 */
export type ScriptRunner = (scriptPath: string, args: string[],
                            options: { timeoutMs: number; stdin?: string; env?: Record<string, string> }) => Promise<ScriptRun>

export interface JsonResult<T> {
  ok: boolean
  data?: T
  /** 失败原因（已分类：超时 / 退出码 / 没有 JSON） */
  error?: string
  /** 失败时的 stderr 尾巴，便于定位 */
  stderr?: string
}

const DEFAULT_TIMEOUT_MS = 30_000

function realRunner(scriptPath: string, args: string[],
                    options: { timeoutMs: number; stdin?: string; env?: Record<string, string> }): Promise<ScriptRun> {
  return new Promise((resolve, reject) => {
    const child = spawn(getPythonCommand(), [scriptPath, ...args], {
      // 用户输入走**环境变量**而不是 argv：这是仓库写进测试的隐私纪律（进程列表里看不到正文）
      env: createPythonProcessEnv(options.env ?? {}),
      stdio: ['pipe', 'pipe', 'pipe'],
    })
    let stdout = ''
    let stderr = ''
    let settled = false
    const timer = setTimeout(() => {
      if (settled) return
      settled = true
      child.kill()
      reject(new Error(`脚本超时（${options.timeoutMs}ms）`))
    }, options.timeoutMs)

    child.stdout.on('data', (chunk: Buffer) => { stdout += chunk.toString() })
    child.stderr.on('data', (chunk: Buffer) => { stderr += chunk.toString() })
    child.on('error', (error) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      reject(error)
    })
    child.on('close', (code) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolve({ stdout, stderr, code })
    })
    if (options.stdin !== undefined) child.stdin.end(options.stdin)
    else child.stdin.end()
  })
}

let runner: ScriptRunner = realRunner

/** 换掉 runner（测试用）。传 null 恢复真实实现。 */
export function setScriptRunner(next: ScriptRunner | null): void {
  runner = next ?? realRunner
}

/** 脚本路径：一律相对包根下的 `scripts/`，不给调用方自己拼路径的自由 */
export function scriptPath(name: string): string {
  return join(resolvePackageRoot(import.meta.url), 'scripts', name)
}

/** 从 stdout 里取 JSON：先试整份，再取最后一行能解析的 */
export function parseJsonFrom(stdout: string): { ok: boolean; data?: any } {
  const whole = stdout.trim()
  if (whole) {
    try { return { ok: true, data: JSON.parse(whole) } } catch { /* 落下去取最后一行 */ }
  }
  const lines = stdout.split(/\r?\n/).map(l => l.trim()).filter(Boolean)
  for (let i = lines.length - 1; i >= 0; i--) {
    if (!lines[i].startsWith('{') && !lines[i].startsWith('[')) continue
    try { return { ok: true, data: JSON.parse(lines[i]) } } catch { /* 继续往前找 */ }
  }
  return { ok: false }
}

/**
 * 跑一个脚本并解析它给的 JSON。
 *
 * **不抛异常**：调用方（工具分支）要的是"这次调用成不成功"，而不是一层 try/catch。
 * 失败的三种情形分开报——超时、退出码非 0、退出码 0 但没有 JSON。
 */
export async function runPythonJson<T = any>(
  script: string, args: string[],
  options: { timeoutMs?: number; stdin?: string; env?: Record<string, string> } = {},
): Promise<JsonResult<T>> {
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS
  let run: ScriptRun
  try {
    run = await runner(scriptPath(script), args, { timeoutMs, stdin: options.stdin, env: options.env })
  } catch (error: any) {
    return { ok: false, error: String(error?.message ?? error).slice(0, 200) }
  }

  const parsed = parseJsonFrom(run.stdout)
  const stderrTail = run.stderr.trim().split(/\r?\n/).filter(Boolean).slice(-3).join(' | ').slice(0, 300)

  if (run.code !== 0) {
    return { ok: false, error: `脚本退出码 ${run.code}`, stderr: stderrTail }
  }
  if (!parsed.ok) {
    return { ok: false, error: '脚本没有给出可解析的 JSON', stderr: stderrTail }
  }
  const data: any = parsed.data
  if (data && typeof data === 'object' && 'success' in data && data.success === false) {
    return { ok: false, error: String(data.error ?? '脚本报错').slice(0, 200), stderr: stderrTail }
  }
  return { ok: true, data: data as T }
}
