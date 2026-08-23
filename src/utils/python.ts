import { execFileSync } from 'child_process'
import { delimiter, join } from 'path'
import { accessSync, constants } from 'fs'

let cached: string | null = null

const EXE_NAMES = process.platform === 'win32'
  ? ['python.exe', 'python3.exe', 'py.exe']
  : ['python3', 'python']

function isExecutable(path: string): boolean {
  try {
    accessSync(path, constants.X_OK)
    return true
  } catch {
    return false
  }
}

/** 按顺序返回 PATH 中所有可用的 Python 解释器（含命令名候选） */
function candidates(): string[] {
  const found: string[] = []
  const dirs = (process.env.PATH || '').split(delimiter).filter(Boolean)
  for (const dir of dirs) {
    for (const name of EXE_NAMES) {
      const full = join(dir, name)
      if (isExecutable(full) && !found.includes(full)) {
        found.push(full)
      }
    }
  }
  // 兜底：直接用命令名（依赖 PATH 解析，可能命中与上面相同的解释器）
  for (const name of process.platform === 'win32' ? ['python', 'python3', 'py'] : ['python3', 'python']) {
    if (!found.includes(name)) found.push(name)
  }
  return found
}

function canRun(cmd: string): boolean {
  try {
    execFileSync(cmd, ['--version'], { stdio: 'ignore', timeout: 5000 })
    return true
  } catch {
    return false
  }
}

function hasSqlcipher(cmd: string): boolean {
  try {
    execFileSync(cmd, ['-c', 'import sqlcipher3'], { stdio: 'ignore', timeout: 10000 })
    return true
  } catch {
    return false
  }
}

function detect(): string {
  // 优先选择能 import sqlcipher3 的解释器（NT 解密核心依赖），
  // 避免命中 PATH 中仅有基础库的裸 Python 环境（如被 IDE 注入的 VM Python 抢占）。
  const list = candidates()
  for (const cmd of list) {
    if (canRun(cmd) && hasSqlcipher(cmd)) return cmd
  }
  for (const cmd of list) {
    if (canRun(cmd)) return cmd
  }
  return 'python'
}

export function getPythonCommand(): string {
  cached ??= detect()
  return cached
}
