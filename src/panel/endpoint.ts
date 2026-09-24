/**
 * 本机面板的**发现 + 凭据**：一个文件解决两件事。
 *
 * 悬浮窗要跟助手说话，需要知道两样东西——**端口**和**口令**。分开存会让"端口写死但服务没起来"
 * 变成一类难查的故障，所以合在一个文件里，由守护进程**原子写**（先写 `.tmp` 再 `rename`，
 * 照抄 `configService.save()` 的手法）。
 *
 * ## 三个必须处理的失效模式
 *
 * 1. **崩溃残留**：文件还在，进程没了 → 用 `process.kill(pid, 0)` 探活
 *    （照抄 `assistantDaemon.isDaemonAlive()` 的写法）。
 * 2. **端口被别的进程占了**：文件里的端口是准的，但回应的人不是我们 → 拿到之后
 *    **必须打一次 `/api/status`**，校验 `service` 与 `startedAt` 都对得上
 *    （照抄 `getDailyReaderStatus()` 里那三行；它就是为这个存在的）。
 * 3. **退出时不删文件**：**不要指望清理**。`stopDaemon()` 发的是 SIGTERM，而 Node 默认不为它跑
 *    清理逻辑，所以文件**一定会残留**。"退出时删掉它"看起来太像一个能用的方案了，
 *    所以这里明说：正确做法是探活，不是删。
 *
 * ## 关于 Windows 上的文件权限
 *
 * token 写在 `~/.weflow-cli/` 下。**不要声称"权限已收紧"**：Windows 上 `fs.chmod` /
 * `mode: 0o600` 基本无效（Node 只能映射只读位）。真正起作用的是 `%USERPROFILE%\.weflow-cli\`
 * 的默认 ACL（只有本人 + SYSTEM + Administrators）——**那是既成事实，不是我们做的事**。
 * `mode: 0o600` 仍然传，因为在 Linux/macOS/WSL 上有意义；在 Windows 上它是空操作。
 *
 * 所以这道 token 的真实作用是：挡住**本机其它程序与任意网页**（它们读不到这个文件）。
 * 它**挡不住**以同一用户身份运行、且愿意翻 `~/.weflow-cli/` 的程序——那种程序本来就能直接读库。
 * 这是这条设计的真实边界，别把它说成比这更强。
 */
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'fs'
import { writeFileAtomic } from '../utils/atomicWrite.js'
import { randomBytes, timingSafeEqual } from 'crypto'
import { join } from 'path'
import os from 'os'

/** 端点文件的格式版本。读不出来或不认识就**当作不存在**，不猜、不迁移（同记忆文件的纪律） */
export const PANEL_ENDPOINT_VERSION = 1
/** `/api/status` 的身份字段：防止把别人的端口当成助手（`service !== 它` 就不认） */
export const PANEL_SERVICE_ID = 'weflow-assistant'
/**
 * 默认端口。**固定**而不是随机：便于本人手工 `curl` 排查，也让文档能写一个确定的地址。
 * 端口被占时**大声失败**，不静默换一个——静默换端口会让"文件里写着 A、实际在 B"成为可能。
 */
export const DEFAULT_PANEL_PORT = 8766

export type PanelChannelMode = 'wechat' | 'local'

export interface PanelEndpoint {
  version: number
  service: string
  port: number
  token: string
  pid: number
  startedAt: string
  channel: PanelChannelMode
  /** 这个进程给本机入口用的记忆桶（与微信直聊同一个 id 时才是"共用一个大脑"） */
  memoryBucket: string
}

export function weflowHome(): string {
  return join(os.homedir(), '.weflow-cli')
}

export function endpointFile(): string {
  return join(weflowHome(), 'assistant_endpoint.json')
}

/** 每进程一份，**每次启动重新生成**。32 字节 base64url。 */
export function generateToken(): string {
  return randomBytes(32).toString('base64url')
}

function isEndpoint(value: any): value is PanelEndpoint {
  return !!value && typeof value === 'object'
    && value.version === PANEL_ENDPOINT_VERSION
    && value.service === PANEL_SERVICE_ID
    && typeof value.port === 'number' && Number.isInteger(value.port) && value.port > 0 && value.port < 65536
    && typeof value.token === 'string' && value.token.length > 0
    && typeof value.pid === 'number'
    && typeof value.startedAt === 'string'
    && (value.channel === 'wechat' || value.channel === 'local')
    && typeof value.memoryBucket === 'string'
}

/** 原子写：先 `.tmp` 再 rename。失败不抛——端点起不来不该把助手一起弄死，调用方会记日志 */
export function writeEndpoint(endpoint: PanelEndpoint): boolean {
  try {
    mkdirSync(weflowHome(), { recursive: true })
    // `mode` 在 Windows 上是空操作，见文件头注释；不是为了"安全"，是为了别的平台。
    // 用 writeFileAtomic：rename 在被占用时会 EPERM，重试+兜底由它负责
    writeFileAtomic(endpointFile(), JSON.stringify(endpoint, null, 2), { mode: 0o600 })
    return true
  } catch {
    return false
  }
}

/**
 * 读并校验。**任何一种不可信都返回 `null`**，绝不返回一个"大概是它"的对象：
 * 文件不存在、JSON 坏了、版本不认识、字段缺、**进程已经没了**。
 */
export function readEndpoint(): PanelEndpoint | null {
  try {
    const file = endpointFile()
    if (!existsSync(file)) return null
    const parsed = JSON.parse(readFileSync(file, 'utf8'))
    if (!isEndpoint(parsed)) return null
    if (!isProcessAlive(parsed.pid)) return null
    return parsed
  } catch {
    return null
  }
}

/** 探活。照抄 `assistantDaemon.isDaemonAlive()`：`kill(pid, 0)` 不真发信号，只做权限/存在性检查 */
export function isProcessAlive(pid: number): boolean {
  try {
    process.kill(pid, 0)
    return true
  } catch {
    return false
  }
}

/**
 * 删除端点文件。**只在"确认要停"时用**（例如 `assistant stop`），
 * 不要指望它在崩溃/被 SIGTERM 时被调用——见文件头第 3 条。
 */
export function clearEndpoint(): void {
  try { rmSync(endpointFile(), { force: true }) } catch { /* 删不掉不影响正确性：读取端会探活 */ }
}

/**
 * token 比较。长度不等时 `timingSafeEqual` 会**抛**，所以先比长度。
 * 用 `timingSafeEqual` 是为了不把"前几个字符对了"这件事通过耗时泄漏出去。
 */
export function tokenMatches(expected: string, given: unknown): boolean {
  if (typeof given !== 'string' || given.length !== expected.length) return false
  try {
    return timingSafeEqual(Buffer.from(expected, 'utf8'), Buffer.from(given, 'utf8'))
  } catch {
    return false
  }
}
