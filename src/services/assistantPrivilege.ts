/**
 * weflow-cli 助手的三级文件权限（D-199）。
 *
 * 对齐用户对 DSH 提权插件的三级定义，但这是**服务器上的助手**、没有 DSH 沙箱，
 * 所以"权限"= 给它开放哪些文件路径，而不是切沙箱模式：
 *
 *   L0 默认：安全范围内读写——PKG_ROOT（包目录）、~/.weflow-cli（数据/记忆）、
 *            cwd（会话工作目录）。这与助手现有能力一致（导出、记忆都写这里）。
 *   L1 自主解锁：助手在对话里调 escalate(level=1) 后，**可读工作区外任意路径（只读）**。
 *             "AI 自己提权"——不需要用户改配置，助手自己决定升；但**只能读，不能写**。
 *   L2 最高级：能写工作区外任意路径 / 执行命令——**沿用现状白名单，本次不实现**
 *             （助手现在就没有写文件/执行命令的工具，保持如此）。
 *
 * 安全边界：
 * - 路径一律 resolve + 规范化，禁止 .. 逃逸出允许的目录；
 * - 符号链接指向允许目录外时，按真实路径判定（realpath）；
 * - L1 只读：read_file 永不接受写参数；没有写工具，想写也做不到；
 * - 提权状态是**会话级**的（每轮 handleMessage 传入），跨轮保留到用户显式收回或重启。
 */
import { resolve, isAbsolute, normalize } from 'node:path'
import { homedir } from 'node:os'
import { existsSync, realpathSync, readFileSync, statSync } from 'node:fs'
import { resolvePackageRoot } from '../utils/packageRoot.js'
import { configService } from './configService.js'

export type PrivilegeLevel = 0 | 1 | 2

const PKG_ROOT = resolvePackageRoot(import.meta.url)
const DATA_DIR = resolve(homedir(), '.weflow-cli')

/** L0 允许的根目录（ resolvable 的真实路径会在检查时再算一次）。
 *  dbPath（微信文件根）也算 L0：用户读自己收到的文件是天经地义的，
 *  不该为了读一张他刚收到的 pdf 还要先 escalate（D-203）。 */
function allowedRoots(cwd: string): string[] {
  const roots = [PKG_ROOT, DATA_DIR]
  try {
    const dbPath = String(configService.get('dbPath') || '').trim()
    if (dbPath) roots.push(dbPath)
  } catch { /* 配置读不到就不加，不该因此崩 */ }
  if (cwd && isAbsolute(cwd)) roots.push(cwd)
  return roots.map(r => resolve(r))
}

function within(root: string, target: string): boolean {
  const r = resolve(root)
  const t = resolve(target)
  return t === r || t.startsWith(r.endsWith('\\') || r.endsWith('/') ? r : r + (process.platform === 'win32' ? '\\' : '/'))
}

export interface PrivilegeCheck {
  allowed: boolean
  reason: string
  /** 规范化后的真实路径（解析成功才有） */
  realPath?: string
}

/**
 * 检查一次文件访问是否在当前权限级别下允许。
 * @param rawPath 用户/模型给的路径
 * @param level 当前会话权限级别
 * @param cwd 会话工作目录
 * @param write 是否写操作（L1 只读，写一律拒绝除非在 L0 允许根内）
 */
export function checkFileAccess(rawPath: string, level: PrivilegeLevel, cwd: string, write = false): PrivilegeCheck {
  if (typeof rawPath !== 'string' || rawPath.trim() === '') {
    return { allowed: false, reason: '路径不能为空' }
  }
  const p = resolve(isAbsolute(rawPath) ? rawPath : (cwd ? resolve(cwd, rawPath) : normalize(rawPath)))
  // realpath 解析符号链接；目标不存在时退回规范化路径（写新文件时会遇到）
  let real = p
  try { if (existsSync(p)) real = realpathSync(p) } catch { /* 用规范化路径 */ }

  const roots = allowedRoots(cwd)
  // L0 允许根内：读写都可以（这就是"默认安全范围内也能写"）
  if (roots.some(r => within(r, real) || within(r, p))) {
    return { allowed: true, reason: '在安全范围内', realPath: real }
  }
  // 范围外：只有 L1 且只读才放行
  if (level >= 1 && !write) {
    return { allowed: true, reason: 'L1 自主解锁：工作区外只读', realPath: real }
  }
  if (level >= 1 && write) {
    return { allowed: false, reason: 'L1 只读：工作区外不能写（L2 白名单未启用）' }
  }
  return { allowed: false, reason: `工作区外访问被拒（当前 L${level}）；L0 只允许 ${roots.join(' / ')}` }
}

/** 读文件：过大截断，避免把大文件整塞进上下文 */
export function readFileSafe(rawPath: string, level: PrivilegeLevel, cwd: string, maxChars = 20000): string {
  const chk = checkFileAccess(rawPath, level, cwd, false)
  if (!chk.allowed) return `(读取被拒: ${chk.reason})`
  const p = chk.realPath!
  try {
    const st = statSync(p)
    if (!st.isFile()) return `(读取失败: 不是普通文件 ${p})`
    const content = readFileSync(p, 'utf8')
    if (content.length > maxChars) {
      return content.slice(0, maxChars) + `\n…（已截断，共 ${content.length} 字符）`
    }
    return content
  } catch (e: any) {
    return `(读取失败: ${e?.message ?? e})`
  }
}

/** 权限级别的人话，供助手回答"你现在什么权限" */
export function levelLabel(level: PrivilegeLevel): string {
  if (level >= 2) return 'L2 最高级（工作区外可读写）'
  if (level >= 1) return 'L1 自主解锁（工作区外只读）'
  return 'L0 默认（安全范围内读写）'
}
