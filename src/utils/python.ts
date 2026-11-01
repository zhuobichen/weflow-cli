import { execFileSync } from 'child_process'
import { delimiter, join } from 'path'
import { accessSync, constants, existsSync, mkdirSync, readFileSync, unlinkSync, writeFileSync } from 'fs'
import { homedir } from 'os'

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

/** `weflow-cli setup` 建的自管 venv 的解释器路径；没建过就是 null。 */
export function managedVenvPython(): string | null {
  const exe = process.platform === 'win32'
    ? join(homedir(), '.weflow-cli', 'venv', 'Scripts', 'python.exe')
    : join(homedir(), '.weflow-cli', 'venv', 'bin', 'python')
  return existsSync(exe) ? exe : null
}

/**
 * 候选序：自管 venv 永远排最前，其余按调用方给的序。纯函数，供测试钉住
 * "建了 venv 就该优先用它"这件事——它坏了的表现是 setup 装好了包、
 * 运行时却仍在用 PATH 上的解释器，两边各自"正常"。
 */
export function orderCandidates(
  managed: string | null,
  rest: string[],
): string[] {
  return managed ? [managed, ...rest.filter(c => c !== managed)] : [...rest]
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
    execFileSync(cmd, ['--version'], { windowsHide: true, stdio: 'ignore', timeout: 5000 })
    return true
  } catch {
    return false
  }
}

function hasSqlcipher(cmd: string): boolean {
  try {
    execFileSync(cmd, ['-c', 'import sqlcipher3'], { windowsHide: true, stdio: 'ignore', timeout: 10000 })
    return true
  } catch {
    return false
  }
}

function detect(): string {
  // 自管 venv（`weflow-cli setup`）优先：它就是为了"这台机器有一个能
  // import sqlcipher3 的 Python"而存在的。venv 坏了（装完又被卸了包）会
  // 自然落到下面的 PATH 候选——行为退回 setup 之前，而不是更糟。
  const ordered = orderCandidates(managedVenvPython(), candidates())
  // 优先选择能 import sqlcipher3 的解释器（NT 解密核心依赖），
  // 避免命中 PATH 中仅有基础库的裸 Python 环境（如被 IDE 注入的 VM Python 抢占）。
  for (const cmd of ordered) {
    if (canRun(cmd) && hasSqlcipher(cmd)) return cmd
  }
  for (const cmd of ordered) {
    if (canRun(cmd)) return cmd
  }
  return 'python'
}

/**
 * 探测结果落盘：PATH 上常有多个 python，逐候选 spawn 两次（--version + import sqlcipher3）
 * 在慢机器上要一两秒，而 CLI 每次调用都是新进程——只在内存里缓存等于没缓存。
 *
 * 缓存只存"路径 + 时间"，读回时仍要过 `isExecutable`：解释器被卸载/移动后
 * 第一次调用就会发现并重新探测，不会把坏路径钉死。
 */
/** 带分隔符才说明是具体路径（裸命令名交给 detect 重新确认）。 */
function isPathLike(cmd: string): boolean {
  return cmd.includes('/') || cmd.includes(String.fromCharCode(92))
}

function cacheFile(): string {
  return join(homedir(), '.weflow-cli', 'python-cmd.json')
}

function loadFromDisk(): string | null {
  try {
    const raw = JSON.parse(readFileSync(cacheFile(), 'utf8')) as { cmd?: string }
    const cmd = String(raw.cmd || '')
    if (!cmd) return null
    // 命令名（不带分隔符）没法直接判存在性，交给 detect 重新确认
    if (!isPathLike(cmd)) return null
    return isExecutable(cmd) ? cmd : null
  } catch {
    return null
  }
}

function saveToDisk(cmd: string): void {
  try {
    const dir = join(homedir(), '.weflow-cli')
    mkdirSync(dir, { recursive: true })
    writeFileSync(cacheFile(), JSON.stringify({ cmd, at: new Date().toISOString() }), 'utf8')
  } catch { /* 缓存写不进去不影响功能（只读沙箱里就是这样） */ }
}

export function getPythonCommand(): string {
  // 先看磁盘缓存：CLI 每次调用都是新进程，只靠模块级变量等于没缓存。
  // 缓存里只有路径，读回时仍要过 isExecutable —— 解释器被卸载/移动后会重新探测。
  cached ??= loadFromDisk() ?? detect()
  if (isPathLike(cached)) saveToDisk(cached)
  return cached
}

/** setup 装完后清缓存，让同一进程里后续的 getPythonCommand 看见新 venv。 */
export function clearPythonCommandCache(): void {
  cached = null
  // 文件也删掉：setup 建好 venv 后必须让下一次调用重新探测，
  // 否则磁盘缓存会把建 venv 之前的选择一直钉住。
  try { unlinkSync(cacheFile()) } catch { /* 没有缓存文件是正常情况 */ }
}
