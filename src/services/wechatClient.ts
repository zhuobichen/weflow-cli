/**
 * **这台机器上的微信客户端，运行时到底加载的是哪一份。**
 *
 * 为什么需要它：`check` / `health_check` 一直在报"数据版本 4.x"（那是**数据库格式**），
 * 而"客户端是哪个版本"从来没人报过 —— 出问题时（数据读不出来、客户端起不来）这恰恰是
 * 第一个要问的。
 *
 * **判据来自一份实测的启动器逆向分析**（报告在仓库外），它纠正了一个很自然的错法：
 *
 * - `Weixin.exe` 里**确实**硬编码着一个版本字面量，而且**恰好与本机的版本目录同名** ——
 *   看起来"读它就能知道版本"。但控制流显示它只是**兜底**：主路径读
 *   `%APPDATA%\Tencent\xwechat\update\update.data` 里记的目录，只有那次失败才会用到它。
 *   ⇒ **读字面量判版本是错的**，哪怕它在这台机器上碰巧对。（报告里那条"2/2 实测"就是先
 *   写错、后被控制流推翻的。）
 * - 正确的判据是**活进程命令行里的 `--user-lib-dir`** —— 那是真的被加载的那个目录。
 * - **但 4.1.15.53 起主进程不再带这个参数**（2026-10-18 本机实测：命令行只有
 *   `"D:\WeiXin\Weixin.exe"`，一个开关都没有）。于是加一条**同一件事的第二种观测**：
 *   进程实际映射的 `Weixin.dll` 在哪个目录 —— 那同样是"它现在加载了哪个"，只是从
 *   进程模块表看而不是从命令行看。命令行优先（那是客户端自己说的），取不到才看模块表。
 * - 而"磁盘上有个版本目录"**不代表它能用**：本机 `D:\WeiXin\4.1.15.11\` 是升级残留，
 *   只剩 `crashpad_handler.exe` 一个文件（**本机实测** 1 个文件 / 2.1 MB；报告 §2.3 记了同一件事）。
 *
 * 所以这个模块只做两件事，且都**不猜**：用上面两种观测取实际目录；把一个安装根下的版本目录
 * 分成"能用/不能用"（判据是**有没有 `Weixin.dll`** —— 启动器加载的就是它）。
 * 两种观测都取不到就是 `null` + 一句 `note`，不拿"磁盘上只有一个版本目录"这种推断顶替。
 */
import { existsSync, readFileSync, readdirSync, realpathSync, statSync } from 'node:fs'
import { homedir } from 'node:os'
import { join, win32 } from 'node:path'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'

const execFileAsync = promisify(execFile)

/** 一个 `Weixin.exe` 进程的画像（只留判版本要用的两个字段）。 */
export interface WechatClientProcess {
  pid: number
  exe: string
  /** 命令行里的 `--user-lib-dir`；进程没带就是 null（**逆向报告实测**：`--type=xplayer` 那个不带）。 */
  loadedDir: string | null
  /** 命令行里的 `--wechat-files-path`：客户端自己说数据目录在哪（小程序那组进程会带）。 */
  dataDir: string | null
  /** 命令行里的 `--client_version`：客户端自己的版本 token（十进制，如 `0xF2541F0D`）。 */
  versionToken: string | null
  /**
   * 进程实际映射的 `Weixin.dll` 路径。**命令行没带 `--user-lib-dir` 时的第二观测**
   * （4.1.15.53 起主进程就不带了）；读不到（权限/进程刚起）就是 null。
   */
  dllPath: string | null
}

/** 版本的判据与结论 —— 只跟"哪个进程加载了哪个目录"有关，读不到**别的来源**。 */
export interface WechatVersionFacts {
  /**
   * 本机有没有在跑的 `Weixin.exe`（没跑 ⇒ 版本未知，不是"没问题"）。
   *
   * **进程视图被隔离时这里是 `false`，但那只代表"看不见"**（见 `note` 与 `viewFiltered`）。
   * 这一支能确定的只有"无法判断"，不要把它读成"客户端没在运行"。
   */
  running: boolean
  /**
   * 本进程的进程视图被隔离（连 `System`/`lsass` 都看不到）。
   *
   * 为 `true` 时 `running: false` **不能**当作"客户端没在运行"的证据 —— 2026-10-28 实测：
   * 微信在跑（库正在被写），而本进程一个系统进程都看不到。
   */
  viewFiltered: boolean
  /** 去重后的实际加载目录，按出现顺序。 */
  loadedDirs: string[]
  /** 版本号 = 加载目录的名字；**恰好一个**目录时才给，否则 null。 */
  version: string | null
  /** `version` 为 null 时说清为什么。 */
  note: string
  /** 安装根下**不能用的**版本目录（没有 `Weixin.dll`）。 */
  unusableVersionDirs: Array<{ dir: string; files: number }>
}

export interface WechatClientFacts extends WechatVersionFacts {
  /** 客户端自报的构建 token（`--client_version`，如 `0xF2541F0D`）—— 与目录名并列的**第二个来源**。 */
  versionTokens: string[]
  /** 进程命令行里的 `--wechat-files-path`：**客户端自己说**数据目录在哪。 */
  dataDirs: string[]
  /** `update\update.data` 在不在（在 ⇒ 加载路径可能不由命令行决定；见 `caveats`）。 */
  updateData: { path: string; present: boolean }
  /** 客户端崩溃记录：只报条数与最近时间，**不读内容**。 */
  crashRecords: { dir: string; reports: number; records: number; newest: string | null }
  /** 读这个版本时**必须一起说出去**的不确定（目前只有一条：`update.data` 在场）。 */
  caveats: string[]
}

/** 版本目录名：`4.1.15.13` 这种。别的一律不当版本目录（不猜）。 */
const VERSION_DIR = /^\d+(\.\d+)+$/

/**
 * PowerShell 一行取所有 `Weixin.exe` 的 pid / 可执行路径 / 命令行。
 *
 * 与 `keyService.isProcessRunning` 用同一种调法（`-NoProfile -NonInteractive`）：这台机器上
 * 那是仓库既有做法，不引第二种。`ConvertTo-Json` 在只有一个结果时会给对象而不是数组，
 * 所以下面 `parseProcessRows` 两种都收。
 */
// **两个镜像名都要问**：`--user-lib-dir` 在 `Weixin.exe` 那组上，而 `--wechat-files-path`
// 只在 `WeChatAppEx.exe`（小程序运行时那组）的命令行上（**本机实测**：拿它去筛 `Weixin.exe` 会一条都取不到，
// 于是"客户端自报数据目录"这一项**永远是空的**而看不出来）。版本那一支只认 `Weixin.exe`（见 `summarize`）。
// `WeixinDll` 是**同一件事的第二观测**：Win32_Process 只有命令行，模块表要问进程本身。
// 每条都单独 try —— 一个进程读不到模块不该让整张表为空（那会把"读不到"讲成"客户端没在跑"）。
export const PS_LIST = "$rows = Get-CimInstance Win32_Process -Filter (\"Name='Weixin.exe' or Name='WeChatAppEx.exe'\") | ForEach-Object { $dll = $null; try { $dll = (Get-Process -Id $_.ProcessId -ErrorAction Stop).Modules | Where-Object { $_.ModuleName -eq 'Weixin.dll' } | Select-Object -First 1 -ExpandProperty FileName } catch {}; [pscustomobject]@{ ProcessId = $_.ProcessId; ExecutablePath = $_.ExecutablePath; CommandLine = $_.CommandLine; WeixinDll = $dll } }; if ($rows) { [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes(($rows | ConvertTo-Json -Compress))) }"

/**
 * 本进程的**进程视图是否被隔离**。
 *
 * 为什么要问这个：在某些受限环境里（沙箱/AppContainer/容器），
 * `Get-CimInstance Win32_Process` 只返回一小撮进程，**连 `System`/`lsass`/`svchost`
 * 都看不到**。此时"进程表里没有 `Weixin.exe`"完全不能推出"微信没在运行"。
 * 2026-10-28 实测就踩了：`check` 报"主程序 Weixin.exe 没在运行"，而同一时刻
 * `general.db` / `contact.db` 正在被写（微信显然在跑），本进程可见的进程名只有 16 个、
 * 系统进程一个不剩。
 *
/**
 * 判据刻意用"系统进程一个都看不到"——那不是普通权限不足能造成的：
 * 普通用户照样看得见这些进程的名字。
 *
 * **输出必须走 base64**（与 `PS_LIST` 同一条理由，而且是踩过的）：`defaultRunner` 对
 * 非 JSON 的输出一律按 base64(UTF-16LE) 解码，裸字符串 `filtered` 会被解成乱码、
 * 于是这条探针**永远返回"没被隔离"** —— 单元测试照样绿，因为测试注入的 runner 不解码。
 */
export const PS_VIEW_FILTERED = "$n = @('System','lsass','svchost','csrss') | Where-Object { Get-Process -Name $_ -ErrorAction SilentlyContinue } | Measure-Object | Select-Object -ExpandProperty Count; $v = if ($n -eq 0) { 'filtered' } else { '' }; if ($v) { [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($v)) }"

export function parseViewFiltered(raw: string): boolean {
  return String(raw || '').trim() === 'filtered'
}

export function parseProcessRows(raw: string): WechatClientProcess[] {
  const text = String(raw || '').trim()
  if (!text) return []
  let data: unknown
  try {
    data = JSON.parse(text)
  } catch {
    return []
  }
  const rows = Array.isArray(data) ? data : [data]
  const out: WechatClientProcess[] = []
  for (const row of rows) {
    if (!row || typeof row !== 'object') continue
    const r = row as Record<string, unknown>
    const pid = Number(r.ProcessId)
    if (!Number.isFinite(pid)) continue
    const commandLine = String(r.CommandLine || '')
    out.push({
      pid,
      exe: String(r.ExecutablePath || ''),
      loadedDir: userLibDir(commandLine),
      dataDir: wechatFilesPath(commandLine),
      versionToken: clientVersionToken(commandLine),
      dllPath: String(r.WeixinDll || '').trim() || null,
    })
  }
  return out
}

/**
 * 从一条命令行里取 `--user-lib-dir` 的值。**两种写法都认**（`=` 与空格、带引号与不带），
 * 取不到返回 null。
 *
 * 注意这个参数**不在 `Weixin.exe` 的字符串表里**（**逆向报告实测**：0 命中）—— 它是被加载的 DLL
 * 自己解析的。也就是说：只有**进程**能告诉我们它，二进制里搜不到。
 */
export function userLibDir(commandLine: string): string | null {
  const m = /--user-lib-dir\s*=\s*(?:"([^"]+)"|'([^']+)'|(\S+))/.exec(String(commandLine || ''))
  if (!m) return null
  const value = (m[1] || m[2] || m[3] || '').trim().replace(/["']/g, '')
  return value || null
}

/**
 * 从命令行里取 `--wechat-files-path`：**客户端自己说数据目录在哪**。
 *
 * 用处在排查：它和配置里的数据目录不一致时，说明两边指向的不是同一份库（本机数据目录
 * 还有 D 盘软链，同一份库两条路径）。只报出来，**不拿它去改配置**。
 */
export function wechatFilesPath(commandLine: string): string | null {
  const m = /--wechat-files-path\s*=\s*(?:"([^"]+)"|'([^']+)'|(\S+))/.exec(String(commandLine || ''))
  if (!m) return null
  const value = (m[1] || m[2] || m[3] || '').trim().replace(/["']/g, '')
  return value || null
}

/**
 * 命令行里的 `--client_version`：客户端**自己的版本 token**（十进制）。
 *
 * 为什么要报它：它是"哪个构建"的**另一个独立来源** —— 版本目录名（`4.1.15.13`）是装载位置，
 * 而这个是客户端自报的构建标识，两者来自不同的地方。顺带：实测本机这个值 `4065599245`
 * 正是 `0xF2541F0D`，也就是那份逆向报告从三处（反编译 / 插件 manifest / UA）定下来的版本号。
 *
 * ⚠️ **不给"版本号"**：低 16 位已解出（byte3 = 第四段；byte2 − 0x10 = 第三段 ——
 * `0xF2541F0D`→`4.1.15.13`、`0xF2541F35`→`4.1.15.53` 等三个样本都吻合），但高 16 位在
 * 所有样本里都是 `F254`、而它们都是 `4.1.x`，**主次版本分不开**。所以它只作为并列来源报出去，
 * 不拿它去推版本号（模块表已经直接给出加载目录，没有理由再走一条半解码的路）。
 */
export function clientVersionToken(commandLine: string): string | null {
  const m = /--client_version\s*=\s*(\d+)/.exec(String(commandLine || ''))
  if (!m) return null
  const value = Number(m[1])
  if (!Number.isFinite(value) || value <= 0) return null
  return '0x' + value.toString(16).toUpperCase().padStart(8, '0')
}

/** 客户端崩溃记录：只数文件、只取最近时间，**不读任何内容**。 */
export function crashSummary(root: string): WechatClientFacts['crashRecords'] {
  const reports = countFiles(join(root, 'reports'))
  const records = countFiles(join(root, 'records')) + countFiles(join(root, 'metadata'))
  let newest: string | null = null
  try {
    for (const sub of ['reports', 'records', 'metadata']) {
      const dir = join(root, sub)
      if (!existsSync(dir)) continue
      for (const name of readdirSync(dir)) {
        const at = statSync(join(dir, name)).mtime
        if (!newest || at > new Date(newest)) newest = at.toISOString()
      }
    }
  } catch {
    // 读不到时间就给 null —— 不拿当前时间顶替（那会造出一个假的"最近崩溃"）
  }
  return { dir: root, reports, records, newest }
}

function countFiles(dir: string): number {
  try {
    return readdirSync(dir).filter(name => {
      try {
        return statSync(join(dir, name)).isFile()
      } catch {
        return false
      }
    }).length
  } catch {
    return 0
  }
}

/** 两条路径是不是同一个位置。**按真身比**：本机数据目录有 D 盘软链，同一份库有两条路径。 */
export function samePath(a: string, b: string): boolean {
  try {
    return realpathSync(a).toLowerCase() === realpathSync(b).toLowerCase()
  } catch {
    return false
  }
}

/** `%APPDATA%\Tencent\xwechat` —— 崩溃记录与 `update.data` 都在这棵树下。 */
export function xwechatRoot(appData: string | undefined = process.env.APPDATA): string | null {
  return appData ? join(appData, 'Tencent', 'xwechat') : null
}

/**
 * `update.data` 在场时**必须一起说出去**的那句话。
 *
 * 报告把这条明确留成未解："若该文件存在，exe 侧第一次 `LoadLibraryExW` 就会用它，
 * 根本不会看命令行 —— 两条路谁优先【未知】"。所以这个函数的存在意义是：
 * 别让新报出去的那个版本在那种机器上**悄悄变成错的**。
 */
export function updateDataCaveats(present: boolean): string[] {
  if (!present) return []
  return ['update\\update.data 存在：加载路径**可能**由它决定，命令行给的版本未必是实际加载的那个'
    + '（那份逆向没解开它的内部格式，也没判定两条路谁优先）']
}

/** 一个版本目录能不能用：**有没有 `Weixin.dll`**（启动器加载的就是它）。 */
export function versionDirFiles(dir: string): string[] | null {
  try {
    if (!statSync(dir).isDirectory()) return null
    return readdirSync(dir)
  } catch {
    return null
  }
}

/** 把一堆目录名分成"能用的版本目录 / 不能用的 / 与版本无关的"。纯函数，便于测。 */
export function classifyVersionDirs(entries: Array<{ name: string; files: string[] }>): {
  usable: string[]
  unusable: Array<{ dir: string; files: number }>
} {
  const usable: string[] = []
  const unusable: Array<{ dir: string; files: number }> = []
  for (const entry of entries) {
    if (!VERSION_DIR.test(entry.name)) continue
    if (entry.files.includes('Weixin.dll')) usable.push(entry.name)
    else unusable.push({ dir: entry.name, files: entry.files.length })
  }
  return { usable, unusable }
}

/** 安装根（`D:\WeiXin`）→ 它的版本目录分类。读不出来就当空（不猜）。 */
export function versionDirsUnder(installRoot: string): ReturnType<typeof classifyVersionDirs> {
  let names: string[]
  try {
    names = readdirSync(installRoot)
  } catch {
    return { usable: [], unusable: [] }
  }
  const entries = names.map(name => ({ name, files: versionDirFiles(join(installRoot, name)) || [] }))
  return classifyVersionDirs(entries)
}

export function summarize(processes: WechatClientProcess[],
                          unusable: Array<{ dir: string; files: number }>,
                          viewFiltered = false): WechatVersionFacts {
  // 去重按**规范形**（去掉尾部分隔符、大小写折叠 —— Windows 路径大小写不敏感）：
  // 同一个目录写成 `D:\WeiXin\4.1.15.13` 与 `…\4.1.15.13\` 是**一个**目录，
  // 不折算的话会被当成"两个进程指向不同目录"从而拒绝给出版本号。
  // **版本只跟 `Weixin.exe` 有关**：`WeChatAppEx.exe`（小程序那组）与它在同一张进程表里
  // （后者的命令行上带着 `--wechat-files-path`），但它不加载 `Weixin.dll` —— 拿它的
  // "没带 `--user-lib-dir`"去说事会把"客户端在跑、版本取得到"讲成"取不到"。
  // 用 `win32` 而不是平台 `path`：这里拆的**永远是 Windows 路径**（进程表/命令行给的），
  // 而平台 `basename` 在别的平台上会把 `D:\WeiXin\4.1.15.13\Weixin.exe` 当成**一个文件名**
  // —— 于是过滤出 0 个启动器、版本退回"未知"，在 Windows 上还看不出来（CI 的 ubuntu 上红了 4 条）。
  // 在 Windows 上 `win32.*` 与平台版完全等价，所以目标机器的行为一点没变。
  const launchers = processes.filter(p => win32.basename(p.exe).toLowerCase() === 'weixin.exe')
  // 规范形去重（去尾分隔符、大小写折叠）：同一个目录的两种写法是**一个**目录，
  // 不折算会被当成"两个进程指向不同目录"从而拒绝给出版本号。
  const canonical = (dir: string | null): string | null => {
    const shown = String(dir || '').replace(/[\\/]+$/, '')
    return shown || null
  }
  const dedupe = (dirs: Array<string | null>): string[] => {
    const seen = new Map<string, string>()
    for (const dir of dirs) {
      const shown = canonical(dir)
      if (!shown) continue
      const key = shown.toLowerCase()
      if (!seen.has(key)) seen.set(key, shown)
    }
    return [...seen.values()]
  }
  // ① 命令行（客户端自己说的）。② 进程实际映射的 `Weixin.dll`（4.1.15.53 起只有这个可用）。
  // **两种观测量的是同一件事**，所以先在①里找，①空了才看② —— 混在一起算会把"命令行说 A、
  // 模块表说 A"这种一致情形误当成两个目录，反而把版本号弄丢。
  let loadedDirs = dedupe(launchers.map(p => p.loadedDir))
  let dirSource: 'flag' | 'dll' | null = loadedDirs.length ? 'flag' : null
  if (!loadedDirs.length) {
    loadedDirs = dedupe(launchers.map(p => (p.dllPath ? win32.dirname(p.dllPath) : null)))
    if (loadedDirs.length) dirSource = 'dll'
  }
  let version: string | null = null
  let note = ''
  if (!launchers.length) {
    // **`running` 与这句话必须说同一件事**：以前 `running` 看的是"进程表里有没有任何微信进程"，
    // 而 note 看的是"有没有 Weixin.exe"，于是"只有 WeChatAppEx（小程序容器）在跑"时
    // 输出会是"运行中: 是 / 客户端没在运行" —— 两句话互相打脸（2026-10-19 在 `check --report`
    // 的实跑里一眼看到）。对用户来说"客户端在跑"就是主程序在跑，所以两个判据统一到 launchers。
    // **视图被隔离时不许下结论。** 这时"进程表里没有 Weixin.exe"只说明本进程看不到，
    // 不说明客户端没在跑（2026-10-28 实测：库正在被写，而这句会报"没在运行"）。
    // 用"可能"而不是"没在运行"：能确定的只有"看不见"。
    note = viewFiltered
      ? (processes.length
        ? `本进程的进程视图被隔离（连 System/lsass 都看不到），只看到 ${processes.length} 个 WeChatAppEx 辅助进程；主程序在不在无法判断 ⇒ 版本未知（这不代表客户端没在运行）`
        : '本进程的进程视图被隔离（连 System/lsass 都看不到）⇒ 无法判断客户端是否在运行，版本未知（这不代表客户端没在运行）')
      : processes.length
        ? `只看到 ${processes.length} 个辅助进程（WeChatAppEx 等），主程序 Weixin.exe 没在运行 ⇒ 版本未知`
        : '客户端没在运行 ⇒ 版本未知（`--user-lib-dir` 只有进程知道）'
  } else if (!loadedDirs.length) {
    note = `在跑 ${launchers.length} 个进程，但命令行里都没有 --user-lib-dir（也读不到已加载的 Weixin.dll）`
  } else if (loadedDirs.length > 1) {
    // **不猜**：多个目录各有各的来源（子进程可以指到不同的库），挑一个报出去就是编。
    // 目录本身照旧列出来（那是实测到的），只是不给"版本号"这个单一结论。
    note = `有 ${loadedDirs.length} 个不同的目录在使用，不挑一个报版本`
  } else {
    version = win32.basename(loadedDirs[0])
    // 版本拿到了，但要说清它**从哪来** —— 两个来源都成立时结论一样，来源不同却影响排查。
    if (dirSource === 'dll') {
      note = '命令行里没有 --user-lib-dir，版本取自进程实际加载的 Weixin.dll'
    }
  }
  return {
    // 主程序在不在（与上面的 note 同一判据）。辅助进程不算"客户端在运行"：读库、取版本都靠 Weixin.exe。
    running: launchers.length > 0,
    // 视图被隔离时 `running: false` 只是"看不见"，调用方别再自己推出"客户端没在运行"
    viewFiltered,
    loadedDirs,
    version,
    note,
    unusableVersionDirs: unusable,
  }
}

/** 默认的执行器：`Get-CimInstance`。测试里换掉它，别真去问系统。 */
export type PowershellRunner = (command: string) => Promise<string>

/**
 * **只要那一句话**：客户端自报的数据目录。
 *
 * 给 `init` 的数据目录发现用（`DbPathService.autoDetect({ hintedRoots })`）—— 它是唯一
 * **来自客户端自己**的来源，别的都是按目录名猜的。找不到就返回空数组（调用方走老路），
 * **不抛**：这只是个线索，不该让初始化失败。
 */
export async function reportedDataDirs(run: PowershellRunner = defaultRunner): Promise<string[]> {
  if (process.platform !== 'win32') return []
  let raw = ''
  try {
    raw = await run(PS_LIST)
  } catch {
    return []
  }
  const dirs = parseProcessRows(raw).map(p => p.dataDir).filter((d): d is string => !!d)
  return [...new Set(dirs.map(d => d.replace(/[\\/]+$/, '')).filter(Boolean))]
}

const defaultRunner: PowershellRunner = async (command) => {
  const { stdout } = await execFileAsync('powershell.exe',
    ['-NoProfile', '-NonInteractive', '-Command', command], { timeout: 15_000 })
  const raw = String(stdout || '').trim()
  // PowerShell 5.1 往 stdout 写的是**控制台代码页**（中文 Windows 上是 GBK 936），
  // 而 Node 按 UTF-8 解码 —— 于是 `D:\微信\xwechat_files` 变成 `D:\΢��\xwechat_files`，
  // 接着把"与配置是同一份"判成"否"，报告就会给出错误的迁移建议（2026-10-19 实测）。
  // 让 PowerShell 先 base64（UTF-16LE）再输出，就与代码页彻底无关了。
  if (!raw || raw.startsWith('{') || raw.startsWith('[')) return raw   // 兼容注入的 runner / 老输出
  try {
    return Buffer.from(raw, 'base64').toString('utf16le').trim()
  } catch {
    return raw
  }
}

/**
 * 汇总：进程画像 + 每个安装根下"不能用的版本目录"。
 *
 * 非 Windows 直接返回"平台不支持"（`Get-CimInstance` 是 Windows 的），**不是**返回"没问题"。
 */
/**
 * Linux 上判断"微信在不在跑、版本是多少"。
 *
 * 与 Windows 不同，这里没有 WMI/命令行里的 `--user-lib-dir`，所以走两条确定的观测：
 *   1. **运行状态**：扫 `/proc/<pid>/comm`，只认 `wechat`/`weixin`（主程序）；
 *      `wechatappex` 是小程序容器，存在不等于主程序在跑。
 *   2. **版本**：读客户端自己的记录 `~/.xwechat/uh/uh.dat`（protobuf 里带 `x.y.z.w`）。
 *      这不是猜——文件里就有版本字面量，2026-11-01 在 4.1.13.26 上核对过。
 *
 * 为什么不复用 Windows 那条：那边依赖 PowerShell 的 `Win32_Process` 与
 * `Weixin.dll` 映射信息，Linux 上都没有。
 */
/**
 * 判断一个 Linux 进程是不是微信主程序。
 *
 * 为什么不只看 `comm`：各家发行版的打包方式差别很大——
 * - deb/rpm 官方包：`comm` 就是 `wechat`，没问题；
 * - **AUR / 手动装的 tarball**：可能通过包装脚本启动，`comm` 是 `bash`/`sh`；
 * - **Flatpak**：外层是 `bwrap` 沙箱，`comm` 不是微信；
 * - 有些构建把它改名成 `weixin`。
 *
 * 所以在 `comm` 之外还看 cmdline 的第一个参数 / 常见安装路径。
 * 只加"能对应上真实二进制"的判据，不用"名字包含 wechat"这种宽匹配——
 * 那会把 `notwechat-tool` 也算成微信（M-12 记的那个假阳性）。
 */
export function isWechatLinuxProcess(comm: string, cmdline: string): boolean {
  const name = (comm || '').trim().toLowerCase()
  if (name === 'wechat' || name === 'weixin') return true

  const argv = (cmdline || '').split('\0')
  const argv0 = (argv[0] || '').trim().toLowerCase()
  const base = argv0.split('/').pop() || ''
  if (base === 'wechat' || base === 'weixin') return true

  // 官方安装位置（Arch AUR / 手动解包都常见这个前缀）
  if (/\/opt\/wechat\/wechat$/.test(argv0)) return true
  // Flatpak：可执行在沙箱内，仍然能从这里看出来
  if (/\/app\/bin\/wechat$/.test(argv0)) return true
  return false
}

export function detectLinuxClient(homeDir: string = homedir()): WechatClientFacts {
  const root = join(homeDir, '.xwechat')
  const facts: WechatClientFacts = {
    running: false, viewFiltered: false,
    loadedDirs: [], version: null, note: '', unusableVersionDirs: [],
    versionTokens: [],
    dataDirs: [],
    updateData: { path: join(root, 'update', 'update.data'), present: false },
    crashRecords: { dir: join(root, 'crashinfo'), reports: 0, records: 0, newest: null },
    caveats: [],
  }

  // 1) 运行状态
  let procReadable = true
  try {
    for (const entry of readdirSync('/proc')) {
      if (!/^\d+$/.test(entry)) continue
      let comm = ''
      try {
        comm = readFileSync(`/proc/${entry}/comm`, 'utf8').trim().toLowerCase()
      } catch { continue }
      let cmdlineForMatch = ''
      try {
        cmdlineForMatch = readFileSync(`/proc/${entry}/cmdline`, 'utf8')
      } catch { /* 读不到就用 comm 判断 */ }
      if (!isWechatLinuxProcess(comm, cmdlineForMatch)) continue
      facts.running = true
      // 数据目录：客户端命令行里自带（`--wechat-files-path=`）
      try {
        const m = cmdlineForMatch.match(/--wechat-files-path=([^\0\s]+)/)
        if (m) facts.dataDirs.push(m[1])
      } catch { /* 读不到不影响结论 */ }
      break
    }
  } catch { procReadable = false /* /proc 不可读（hidepid 等） */ }

  // 2) 版本：客户端自己的记录
  try {
    const raw = readFileSync(join(root, 'uh', 'uh.dat')).toString('latin1')
    const m = raw.match(/(\d+\.\d+\.\d+\.\d+)/)
    if (m) facts.version = m[1]
  } catch { /* 文件不存在就留空 */ }

  facts.note = facts.version
    ? `版本取自客户端自身记录 ~/.xwechat/uh/uh.dat（Linux 无 WMI/模块表可查）`
    : facts.running
      ? `客户端在跑，但读不到 ~/.xwechat/uh/uh.dat 里的版本记录 ⇒ 版本未知`
      : procReadable
        ? `客户端没在运行，也没有版本记录 ⇒ 版本未知`
        // /proc 读不到时说"我看不见"，而不是断言"微信没在运行"——后者是假错误
        : `读不到 /proc 进程列表（可能被 hidepid 限制）⇒ 无法判断微信是否在运行`
  return facts
}

export async function detectWechatClient(run: PowershellRunner = defaultRunner): Promise<WechatClientFacts> {
  const root = xwechatRoot()
  const nothing: WechatClientFacts = {
    running: false, viewFiltered: false,
    loadedDirs: [], version: null, note: '', unusableVersionDirs: [],
    versionTokens: [],
    dataDirs: [], updateData: { path: root ? join(root, 'update', 'update.data') : '', present: false },
    crashRecords: { dir: root ? join(root, 'crashinfo') : '', reports: 0, records: 0, newest: null },
    caveats: [],
  }
  if (process.platform === 'linux') {
    // Linux 有自己的两条观测（/proc + uh.dat），不再一律报“未知”。
    // macOS 仍只有“未知”：那里连数据目录都要用户自己给，没有可复用的记录。
    return detectLinuxClient()
  }
  if (process.platform !== 'win32') {
    return { ...nothing, note: `macOS 上不做版本探测（当前平台 ${process.platform}）` }
  }
  let raw = ''
  try {
    raw = await run(PS_LIST)
  } catch {
    raw = ''
  }
  const processes = parseProcessRows(raw)
  // 只在"进程表里没有主程序"时才多问一次"这个视图本身可不可信"：
  // 那一支正是会把"看不见"讲成"没在运行"的地方，而它本来就不常见，多一次 PowerShell 不值一提。
  let viewFiltered = false
  if (!processes.some(p => win32.basename(p.exe).toLowerCase() === 'weixin.exe')) {
    try {
      viewFiltered = parseViewFiltered(await run(PS_VIEW_FILTERED))
    } catch {
      viewFiltered = false
    }
  }
  // 安装根从**启动器**的可执行路径推（**别去猜**：注册表与常见路径都不可靠 ——
  // 注册表那条空的是**我们自己**的旧结论，出处是 `docs/DECISIONS.md` 里那句"`WeChat Files`
  // 与注册表 InstallPath 都为空"（讲的是 3.x）；**别把它记成那份逆向报告的结论**，
  // 报告里没有注册表这一段）。只取 `Weixin.exe` 的目录：小程序的目录是插件目录，
  // 那里的版本号子目录不是客户端版本。
  const roots = [...new Set(processes
    .filter(p => win32.basename(p.exe).toLowerCase() === 'weixin.exe')
    .map(p => win32.dirname(p.exe))
    .filter(dir => dir && existsSync(dir)))]
  const unusable = roots.flatMap(r => versionDirsUnder(r).unusable)
  const version = summarize(processes, unusable, viewFiltered)

  // `update.data` 在场 ⇒ **它可能才是决定加载路径的那个**，命令行给的版本就未必是加载的那个。
  // 报告把这条明确留成未解（"两条路谁优先 —— 【未知】"），所以这里**不猜**：只说存在，
  // 并把不确定性跟着版本一起带出去（否则新报的这个版本会在那种机器上悄悄变成错的）。
  const updateFile = nothing.updateData.path
  const present = !!updateFile && existsSync(updateFile)
  return {
    ...version,
    // 客户端自报的构建 token（与"目录名"并列的第二来源）——**不拿它推版本号**，编码规则没人解出来
    versionTokens: [...new Set(processes.map(p => p.versionToken).filter((t): t is string => !!t))],
    dataDirs: [...new Set(processes.map(p => p.dataDir).filter((d): d is string => !!d))],
    updateData: { path: updateFile, present },
    crashRecords: crashSummary(nothing.crashRecords.dir),
    caveats: updateDataCaveats(present),
  }
}
