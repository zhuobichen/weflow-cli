/**
 * 助手守护进程管理 — start(后台常驻) / stop / status / run(前台)
 * pid 文件: ~/.weflow-cli/assistant.pid, 日志: ~/.weflow-cli/assistant.log
 */
import { join } from 'path'
import { existsSync, readFileSync, writeFileSync, rmSync, mkdirSync, appendFileSync, statSync } from 'fs'
import os from 'os'
import { spawn, type ChildProcess } from 'child_process'
import { resolvePackageRoot } from '../utils/packageRoot.js'
import { createPythonProcessEnv } from '../utils/pythonProcessEnv.js'
import { clearEndpoint } from '../panel/endpoint.js'

const DIR = join(os.homedir(), '.weflow-cli')
const PID_FILE = join(DIR, 'assistant.pid')
const LOG_FILE = join(DIR, 'assistant.log')

/** 子进程存活观察窗口(ms)：这么短时间内就退出 = 启动失败 */
const SETTLE_MS = 700

export interface StartDaemonOptions {
  /** 注入的 spawn，测试用；默认 child_process.spawn */
  spawnImpl?: typeof spawn
  /** 存活观察窗口(ms)，默认 700 */
  settleMs?: number
}

export function isDaemonAlive(): { alive: boolean; pid: number | null } {
  if (!existsSync(PID_FILE)) return { alive: false, pid: null }
  try {
    const pid = parseInt(readFileSync(PID_FILE, 'utf8').trim())
    // Windows: process.kill(pid, 0) 对不存在进程抛错; 对存在进程返回 undefined
    process.kill(pid, 0)
    return { alive: true, pid }
  } catch {
    rmSync(PID_FILE, { force: true })
    return { alive: false, pid: null }
  }
}

export function appendLog(line: string): void {
  try {
    if (!existsSync(DIR)) mkdirSync(DIR, { recursive: true })
    appendFileSync(LOG_FILE, line + '\n', 'utf8')
  } catch { /* 日志失败不阻断 */ }
}

/** 日志轮转: 超过 1MB 保留尾部 256KB, 防止常驻进程日志无限增长 */
export function rotateLogIfNeeded(): void {
  try {
    if (!existsSync(LOG_FILE)) return
    if (statSync(LOG_FILE).size < 1024 * 1024) return
    const buf = readFileSync(LOG_FILE)
    const tail = buf.subarray(Math.max(0, buf.length - 256 * 1024))
    const cut = tail.indexOf('\n') // 对齐到行首
    writeFileSync(LOG_FILE, `--- rotated at ${new Date().toLocaleString('zh-CN')} ---\n` + tail.subarray(cut + 1))
    appendLog(`[轮转] 日志已截断 (原 ${statSync(LOG_FILE).size} 字节)`)
  } catch { /* 轮转失败不阻断 */ }
}

export function tailLog(n = 20): string {
  try {
    const lines = readFileSync(LOG_FILE, 'utf8').trim().split('\n')
    return lines.slice(-n).join('\n')
  } catch {
    return '(暂无日志)'
  }
}

/** 后台启动守护进程 (detached, 脱离终端生命周期)
 *
 * 子进程带 `--yes`：**确认发生在这一层**——人敲 `assistant start`，或机器用
 * `--json --yes`——而 `assistant run` 自己那层确认要读 stdin，守护进程给它的 stdin
 * 是 `ignore`，没人能回答那个提示。少了这个参数，子进程会死在确认提示上，而父进程
 * 照样报「✓ 已启动」。（那个曾经「负责」这件事的 WEFLOW_ASSISTANT_DAEMON 环境变量
 * 没有任何代码读它，而且它被当成了变量名里带 `=` 的键——已删。）
 *
 * 启动后**观察一小段时间再回报**：子进程立刻退出时（例如消息通道没登录），报出来的
 * 是退出码与日志尾部，而不是一句假的成功。
 */
export async function startDaemon(options: StartDaemonOptions = {}): Promise<{
  started: boolean; pid?: number; error?: string
}> {
  const spawnImpl = options.spawnImpl ?? spawn
  const settleMs = options.settleMs ?? SETTLE_MS
  const { alive, pid } = isDaemonAlive()
  if (alive) return { started: false, pid: pid!, error: `已在运行 (pid ${pid})` }

  const packageRoot = resolvePackageRoot(import.meta.url)
  const compiledEntry = join(packageRoot, 'dist', 'bin', 'weflow-cli.js')
  const sourceEntry = join(packageRoot, 'bin', 'weflow-cli.ts')
  const entry = existsSync(compiledEntry) ? compiledEntry : sourceEntry

  if (!existsSync(entry)) return { started: false, error: `入口不存在: ${entry}` }

  try {
    appendLog(`--- ${new Date().toLocaleString('zh-CN')} daemon starting ---`)
    const entryArgs = entry === sourceEntry ? ['--import', 'tsx', entry] : [entry]
    const child: ChildProcess = spawnImpl(process.execPath,
      [...entryArgs, 'assistant', 'run', '--yes'], {
      detached: true,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: createPythonProcessEnv(),
      windowsHide: true,
    })

    // spawn 失败（入口不存在、权限）是**异步**的 'error' 事件：没有监听器时它会让
    // 进程直接抛未捕获异常。收下来，等观察窗口结束一起报。
    // 用持有对象而不是裸变量：回调里的赋值 TS 的控制流分析看不见，
    // 裸变量会被收窄成 null，检查处就变成 never。
    const state: { spawnError: Error | null } = { spawnError: null }
    child.on('error', (error: Error) => {
      state.spawnError = error
      appendLog('[spawn error] ' + error.message)
    })

    // 收集子进程输出写入日志
    child.stdout?.on('data', (d: Buffer) => appendLog(d.toString().trim()))
    child.stderr?.on('data', (d: Buffer) => appendLog('[stderr] ' + d.toString().trim()))
    child.on('exit', (code) => {
      appendLog(`--- daemon exited (code ${code}) ---`)
      try { if (existsSync(PID_FILE)) rmSync(PID_FILE, { force: true }) } catch {}
    })

    await new Promise<void>((resolve) => setTimeout(resolve, settleMs))
    if (state.spawnError) {
      return { started: false, error: `子进程无法启动: ${state.spawnError.message}` }
    }
    if (child.exitCode !== null || child.signalCode) {
      // 关键：这里**不写** pid 文件。写了就等于对外宣称它在运行，而进程已经没了。
      return {
        started: false,
        error: `子进程启动后立即退出 (code ${child.exitCode ?? child.signalCode})；`
               + `日志尾部: ${tailLog(6)}`,
      }
    }
    if (!child.pid) return { started: false, error: '无法获取子进程 pid' }
    writeFileSync(PID_FILE, String(child.pid), 'utf8')
    return { started: true, pid: child.pid }
  } catch (e: any) {
    return { started: false, error: e.message }
  }
}

export function stopDaemon(): { stopped: boolean; message: string } {
  const { alive, pid } = isDaemonAlive()
  if (!alive) return { stopped: false, message: '守护进程未在运行' }
  try {
    // Windows 下 SIGTERM/SIGKILL 等价 terminate
    process.kill(pid!, 'SIGTERM')
    rmSync(PID_FILE, { force: true })
    // 端点文件也删掉：子进程收的是 SIGTERM，**不会**跑到 `AssistantService.stop()` 里去清理，
    // 所以这里替它删。删得掉最好，删不掉也不影响正确性——读取端会探活（见 panel/endpoint.ts）。
    clearEndpoint()
    appendLog(`--- ${new Date().toLocaleString('zh-CN')} daemon stopped manually ---`)
    return { stopped: true, message: `已停止 (pid ${pid})` }
  } catch (e: any) {
    return { stopped: false, message: `停止失败: ${e.message}` }
  }
}
