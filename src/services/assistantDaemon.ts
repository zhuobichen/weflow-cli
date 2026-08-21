/**
 * 助手守护进程管理 — start(后台常驻) / stop / status / run(前台)
 * pid 文件: ~/.weflow-cli/assistant.pid, 日志: ~/.weflow-cli/assistant.log
 */
import { join } from 'path'
import { existsSync, readFileSync, writeFileSync, rmSync, mkdirSync, appendFileSync } from 'fs'
import os from 'os'
import { spawn, type ChildProcess } from 'child_process'
import { fileURLToPath } from 'url'
import { dirname } from 'path'

const DIR = join(os.homedir(), '.weflow-cli')
const PID_FILE = join(DIR, 'assistant.pid')
const LOG_FILE = join(DIR, 'assistant.log')
const MARKER = 'WEFLOW_ASSISTANT_DAEMON=1'

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

export function tailLog(n = 20): string {
  try {
    const lines = readFileSync(LOG_FILE, 'utf8').trim().split('\n')
    return lines.slice(-n).join('\n')
  } catch {
    return '(暂无日志)'
  }
}

/** 后台启动守护进程 (detached, 脱离终端生命周期) */
export function startDaemon(): { started: boolean; pid?: number; error?: string } {
  const { alive, pid } = isDaemonAlive()
  if (alive) return { started: false, pid: pid!, error: `已在运行 (pid ${pid})` }

  const __dirname = dirname(fileURLToPath(import.meta.url))
  // dist/src/services -> dist/bin/weflow-cli
  const entry = join(__dirname, '..', '..', 'bin', 'weflow-cli.js')

  if (!existsSync(entry)) return { started: false, error: `入口不存在: ${entry}` }

  try {
    appendLog(`--- ${new Date().toLocaleString('zh-CN')} daemon starting ---`)
    const child: ChildProcess = spawn(process.execPath, [entry, 'assistant', 'run'], {
      detached: true,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, [MARKER]: '1' },
      windowsHide: true,
    })

    // 收集子进程输出写入日志
    child.stdout?.on('data', (d: Buffer) => appendLog(d.toString().trim()))
    child.stderr?.on('data', (d: Buffer) => appendLog('[stderr] ' + d.toString().trim()))
    child.on('exit', (code) => {
      appendLog(`--- daemon exited (code ${code}) ---`)
      try { if (existsSync(PID_FILE)) rmSync(PID_FILE, { force: true }) } catch {}
    })

    if (child.pid) {
      writeFileSync(PID_FILE, String(child.pid), 'utf8')
      return { started: true, pid: child.pid }
    }
    return { started: false, error: '无法获取子进程 pid' }
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
    appendLog(`--- ${new Date().toLocaleString('zh-CN')} daemon stopped manually ---`)
    return { stopped: true, message: `已停止 (pid ${pid})` }
  } catch (e: any) {
    return { stopped: false, message: `停止失败: ${e.message}` }
  }
}
