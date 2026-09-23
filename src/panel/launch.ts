/**
 * 面板的**启动决策**：用哪个二进制、argv 是什么、URL 是什么。
 *
 * 全部是纯函数，返回路径与数组、没有副作用——**因为这一层能进 CI，而窗口不能**。
 * `resources/panel/main.cjs` 里只剩"调用 spawn / new BrowserWindow"，决策都在这儿，
 * 于是"会不会把凭据写进命令行"这种问题有地方可以钉住。
 *
 * 一条硬规矩贯穿本文件：**任何凭据都不许进 argv**。Windows 上同机任何进程都能从
 * `wmic process get commandline` 读到命令行，把 token 放进去就是一次**看不出破绽的泄漏**。
 * 所以浏览器那条路只带**一次性口令**（见 `server.ts` 的 `/api/pair`），Electron 那条什么都不带
 * （它自己去读端点文件）。
 */
import { existsSync } from 'node:fs'
import { join } from 'node:path'

export type ExistsFn = (path: string) => boolean

export interface ElectronLookup {
  /** 找 Electron 的顺序：包内 → 全局 npm 目录 → 都没有就是 null */
  candidates: string[]
  found: string | null
}

/**
 * 找 Electron。**用 `existsSync` 逐个试，不用 `where`/`which`**：
 * PATH 上可能有 shim，而 shim 的行为不比一个确定的路径更可信。
 */
export function resolveElectronBinary(env: {
  packageRoot: string
  localAppData?: string
  programFiles?: string
  exists?: ExistsFn
}): ElectronLookup {
  const exists = env.exists ?? existsSync
  const candidates = [
    // 1. 本项目里装过（npm i -D electron）
    join(env.packageRoot, 'node_modules', 'electron', 'dist', 'electron.exe'),
    // 2. 全局 npm 装过（npm i -g electron）
    env.localAppData ? join(env.localAppData, 'npm', 'node_modules', 'electron', 'dist', 'electron.exe') : '',
    // 3. 全局装到别处时的兜底
    env.programFiles ? join(env.programFiles, 'nodejs', 'node_modules', 'electron', 'dist', 'electron.exe') : '',
  ].filter(Boolean)
  return { candidates, found: candidates.find((p) => exists(p)) ?? null }
}

/**
 * Electron 的 argv。**就一个参数**：应用目录（里面的 `package.json` 指向 `main.cjs`）。
 * 什么都不多带——不带 token、不带端口、不带 URL。
 */
export function electronLaunchArgs(panelDir: string): string[] {
  return [panelDir]
}

export interface EdgeLookup {
  candidates: string[]
  found: string | null
}

/** 找 Edge；找不到再找 Chrome（Chromium 内核的 `--app=` 都能用） */
export function resolveBrowserBinary(env: {
  programFiles?: string
  programFilesX86?: string
  localAppData?: string
  exists?: ExistsFn
}): EdgeLookup {
  const exists = env.exists ?? existsSync
  const roots = [env.programFilesX86, env.programFiles, env.localAppData].filter(Boolean) as string[]
  const candidates: string[] = []
  for (const root of roots) {
    candidates.push(join(root, 'Microsoft', 'Edge', 'Application', 'msedge.exe'))
  }
  for (const root of roots) {
    candidates.push(join(root, 'Google', 'Chrome', 'Application', 'chrome.exe'))
  }
  return { candidates, found: candidates.find((p) => exists(p)) ?? null }
}

/**
 * 浏览器降级的 argv。
 *
 * - `--app=<url>`：没有地址栏、没有标签栏，看起来就是个独立小窗。**这就是降级能给的极限**：
 *   它不是悬浮球——不能无边框、不能置顶、没有托盘、没有全局快捷键。这件事必须对用户说清。
 * - `--user-data-dir=<独立目录>`：**不是可选的**。不带它，`--app` 会挤进用户日常那个浏览器实例
 *   （共享 cookie 与会话，甚至可能被并进已有窗口而不是新开一个）。代价是多一个约 50MB 的
 *   profile 目录，**它要进隐私文档的敏感路径清单**。
 * - URL 里带的是**一次性口令**（`?c=`），不是 token：口令单次使用、60 秒过期，
 *   所以它出现在命令行与浏览器历史里都不构成泄漏。
 */
export function browserLaunchArgs(options: { url: string; profileDir: string }): string[] {
  return [
    `--app=${options.url}`,
    `--user-data-dir=${options.profileDir}`,
    '--no-first-run',
    '--no-default-browser-check',
  ]
}

/** 浏览器降级专用的 profile 目录（与用户日常浏览器隔离） */
export function browserProfileDir(weflowHome: string): string {
  return join(weflowHome, 'panel-browser-profile')
}
