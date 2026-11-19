/**
 * `weflow-cli setup` —— 自管 Python venv。
 *
 * Issues 里最大的两类环境噪音：PowerShell 执行策略拦 `.ps1`（.cmd 包装绕开），
 * 和 PATH 上只有微软商店的 python 占位程序（一跑 exit 9009，`getPythonCommand()`
 * 兜底返回裸名 `python` 时正好交给它）。后者真正的根因是"这台机器没有一个能
 * `import sqlcipher3` 的 Python"——本命令一次性解决：在 `~/.weflow-cli/venv`
 * 建一个 venv、装上 requirements，之后 `getPythonCommand()` 优先用它。
 *
 * Windows 上 `sqlcipher3` 被替换为 `sqlcipher3-wheels`（预编译轮，import 名
 * 相同）：源码包要 OpenSSL 头文件 + MSVC，正是要消掉的那类支持负担。替换会
 * 写进计划与输出，不藏在背后。
 */
import { execFile } from 'child_process'
import { promisify } from 'util'
import { existsSync, readFileSync } from 'fs'
import { removeDirQuietly } from '../utils/quietFs.js'
import { basename, join } from 'path'
import { homedir } from 'os'
import { resolvePackageRoot } from '../utils/packageRoot.js'
import { clearPythonCommandCache, getPythonCommand } from '../utils/python.js'

const execFileAsync = promisify(execFile)

export const VENV_DIR = join(homedir(), '.weflow-cli', 'venv')

export interface SetupPlan {
  venvDir: string
  venvPython: string
  /** 用来建 venv 的解释器；null = 这台机器连可运行的 Python 都没有 */
  creator: string | null
  recreate: boolean
  requirementFiles: string[]
  /** 解析后的安装清单（注释/空行已去掉，win32 的替换已做） */
  packages: string[]
  /** 替换说明，如 `sqlcipher3 -> sqlcipher3-binary (Windows 预编译轮)` */
  substitutions: string[]
  createsUnder: '~/.weflow-cli/venv'
  network: true
}

export interface SetupStep {
  name: string
  ok: boolean
  /** 一步的补充事实（版本号、跳过原因等），不放大段日志 */
  detail?: string
}

export interface SetupResult {
  success: boolean
  steps: SetupStep[]
  venvPython: string
  /** 安装完清缓存后 getPythonCommand() 的实际选择——应等于 venvPython */
  effectivePython: string | null
  error?: string
}

/** requirements 文件 → 去注释/空行的依赖列表。 */
export function parseRequirements(text: string): string[] {
  return text
    .split('\n')
    .map(line => line.trim())
    .filter(line => line && !line.startsWith('#'))
}

/** win32 下把源码包换成预编译轮；其余平台原样。 */
export function resolvePackages(names: string[], platform: string): { packages: string[]; substitutions: string[] } {
  if (platform !== 'win32') return { packages: [...names], substitutions: [] }
  // Windows 上的预编译轮是 `sqlcipher3-wheels`（cp310+ 的 win32/amd64/arm64 都有）。
  // `sqlcipher3-binary` 只有 Linux/macOS 轮——CI 的 ubuntu 能装它，Windows 上
  // pip 直接报 "No matching distribution"（2026-10-23 实测）。
  const packages = names.map(n => (n === 'sqlcipher3' ? 'sqlcipher3-wheels' : n))
  const substitutions = packages.some(p => p === 'sqlcipher3-wheels') && names.some(n => n === 'sqlcipher3')
    ? ['sqlcipher3 -> sqlcipher3-wheels (Windows 预编译轮，import 名相同；源码包要 OpenSSL 头文件 + MSVC)']
    : []
  return { packages, substitutions }
}

/**
 * 建一个"能用来创建 venv"的解释器探测：PATH 上的 python/py 逐个 `--version`。
 * 商店占位程序在这一步就会被排掉（`--version` 以 9009 退出）。
 */
async function findCreator(): Promise<{ creator: string | null; version: string | null }> {
  const names = process.platform === 'win32'
    ? ['python', 'python3', 'py']
    : ['python3', 'python']
  for (const name of names) {
    try {
      const { stdout } = await execFileAsync(name, ['--version'], { windowsHide: true, timeout: 10_000 })
      const version = stdout.trim().split('\n')[0] || null
      return { creator: name, version }
    } catch {
      // 这个名字跑不起来（商店占位程序 9009 也会落到这里），试下一个
    }
  }
  return { creator: null, version: null }
}

export async function planSetup(opts: {
  recreate?: boolean
  withVoice?: boolean
  with3x?: boolean
} = {}): Promise<SetupPlan> {
  const pkgRoot = resolvePackageRoot(import.meta.url)
  const files = ['requirements.txt']
  if (opts.with3x) files.push('requirements-3x.txt')
  if (opts.withVoice) files.push('requirements-voice.txt')

  const names: string[] = []
  for (const f of files) {
    names.push(...parseRequirements(readFileSync(join(pkgRoot, f), 'utf-8')))
  }
  const { packages, substitutions } = resolvePackages([...new Set(names)], process.platform)
  const { creator } = await findCreator()
  const venvPython = process.platform === 'win32'
    ? join(VENV_DIR, 'Scripts', 'python.exe')
    : join(VENV_DIR, 'bin', 'python')

  return {
    venvDir: VENV_DIR,
    venvPython,
    creator,
    recreate: !!opts.recreate,
    requirementFiles: files,
    packages,
    substitutions,
    createsUnder: '~/.weflow-cli/venv',
    network: true,
  }
}

export async function runSetup(plan: SetupPlan, onLine?: (line: string) => void): Promise<SetupResult> {
  const steps: SetupStep[] = []
  const say = (line: string) => onLine?.(line)

  if (!plan.creator) {
    return {
      success: false,
      steps: [{ name: 'find-python', ok: false, detail: 'PATH 上没有可运行的 Python（商店占位程序不算）——先装一个真正的 Python 3.10+ 再运行 setup' }],
      venvPython: plan.venvPython,
      effectivePython: null,
      error: 'NO_USABLE_PYTHON',
    }
  }

  // 1. 重建或创建 venv
  if (plan.recreate && existsSync(plan.venvDir)) {
    // 用 removeDirQuietly 而不是 rmSync：本机（Windows + 沙箱）下
    // `rmSync(dir, { recursive: true, force: true })` 不抛错、目录还在，于是紧接着的
    // `existsSync(venvDir)` 仍然为真、直接跳过创建——`--recreate` 静默失效，
    // 用户以为换了新 venv，实际还是旧的。见 src/utils/quietFs.ts 的实测表。
    removeDirQuietly(plan.venvDir)
    steps.push({ name: 'remove-existing-venv', ok: true, detail: basename(plan.venvDir) })
    say(`已删除旧 venv: ${plan.venvDir}`)
  }
  if (!existsSync(plan.venvDir)) {
    try {
      await execFileAsync(plan.creator, ['-m', 'venv', plan.venvDir], { windowsHide: true, timeout: 180_000 })
      steps.push({ name: 'create-venv', ok: true, detail: `由 ${plan.creator} 创建` })
      say(`已创建 venv: ${plan.venvDir}`)
    } catch (e: any) {
      return {
        success: false,
        steps: [...steps, { name: 'create-venv', ok: false, detail: String(e?.message || e).slice(0, 200) }],
        venvPython: plan.venvPython,
        effectivePython: null,
        error: 'VENV_CREATE_FAILED',
      }
    }
  } else {
    steps.push({ name: 'create-venv', ok: true, detail: '已存在，跳过（--recreate 可重建）' })
  }

  // 2. 装依赖（走 venv 自己的 pip；PyPI 网络请求就是这一步）。
  // 失败重试一次：PyPI 的瞬时网络错误（本机 2026-10-23 实测过一次整批失败、
  // 逐包重装全过）不值得让用户从头再来——重试前说一声。
  const pipArgs = ['-m', 'pip', 'install', '--disable-pip-version-check', ...plan.packages]
  const pipDetail = (e: unknown): string => {
    const err = e as { stderr?: string; message?: string }
    const tail = (err?.stderr || '').split('\n').map(l => l.trim()).filter(Boolean).slice(-3).join(' | ')
    return (tail || String(err?.message || e)).slice(0, 300)
  }
  let installed = false
  let pipError: unknown = null
  for (let attempt = 1; attempt <= 2 && !installed; attempt++) {
    try {
      say(attempt === 1
        ? `安装 ${plan.packages.length} 个包（来自 ${plan.requirementFiles.join('、')}）...`
        : `重试安装（第 1 次失败：${pipDetail(pipError)}）...`)
      await execFileAsync(plan.venvPython, pipArgs, {
        windowsHide: true,
        timeout: 600_000,
        maxBuffer: 10 * 1024 * 1024,
      })
      installed = true
    } catch (e) {
      pipError = e
    }
  }
  if (!installed) {
    return {
      success: false,
      steps: [...steps, { name: 'pip-install', ok: false, detail: pipDetail(pipError) }],
      venvPython: plan.venvPython,
      effectivePython: null,
      error: 'PIP_INSTALL_FAILED',
    }
  }
  steps.push({ name: 'pip-install', ok: true, detail: `${plan.packages.length} 个包（${plan.requirementFiles.join('、')}）` })
  for (const s of plan.substitutions) say(`  替换: ${s}`)

  // 3. 验收：venv 里真能 import sqlcipher3（NT 解密的核心依赖）
  let verified = false
  try {
    await execFileAsync(plan.venvPython, ['-c', 'import sqlcipher3'], { windowsHide: true, timeout: 30_000 })
    verified = true
  } catch {
    verified = false
  }
  steps.push({
    name: 'verify-sqlcipher3',
    ok: verified,
    detail: verified ? 'import sqlcipher3 通过' : 'import sqlcipher3 失败——pip 报成功但模块不可用，把上面 pip 的完整输出贴到 issue',
  })

  // 4. 确认运行时真的会选它（清掉模块级缓存再看选择）
  clearPythonCommandCache()
  const effective = getPythonCommand()
  const wiringOk = existsSync(plan.venvPython) && effective === plan.venvPython
  steps.push({
    name: 'runtime-prefers-venv',
    ok: wiringOk,
    detail: wiringOk ? 'getPythonCommand() 已指向自管 venv' : `getPythonCommand() 选了 ${effective}——venv 未通过探测（sqlcipher3 不可用时按原顺序回退 PATH）`,
  })

  return {
    success: steps.every(s => s.ok),
    steps,
    venvPython: plan.venvPython,
    effectivePython: effective,
    ...(verified && !wiringOk ? { error: 'VENV_NOT_PREFERRED' } : {}),
  }
}
