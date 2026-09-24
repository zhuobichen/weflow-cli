/**
 * 面板的**打包与纪律**：静态文本断言。
 *
 * 为什么用文本断言（这个仓库的既有手法，同 `test/config-keys.test.ts`）：这些东西
 * **没有运行时行为可测**——窗口在 CI 里跑不了（没有显示器），而"渲染进程绝不用 innerHTML"
 * 这种纪律，一旦被违反也不会报错，只会某天让助手回复里的一段用户聊天内容变成可执行的脚本。
 * 所以把文件当文本读，钉住几条不该松的线。格式改了它会红——**红是对的**。
 *
 * 另一半是打包：`resources/` 已在 `package.json` 的 `files` 白名单里，
 * 所以放这儿不需要改打包配置；但要确认文件**真的在**，否则用户装上就缺文件，
 * 而 CI 的 `release-consistency` 只查"不该有的东西"，不查"该有的东西"。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

const ROOT = process.cwd()
const PANEL = join(ROOT, 'resources', 'panel')

function read(name: string): string {
  return readFileSync(join(PANEL, name), 'utf8')
}

/**
 * 剥掉注释再断言。**这一步是必须的**：这些文件的注释里就在讲"绝不用 innerHTML"、
 * "拿不到 token"——直接拿词去匹配，匹配到的是解释那句话的散文，不是代码。
 * （第一版就是这么写的，两条断言都被自己的注释打红。这个仓库里同类事故已经够多了。）
 *
 * 只剥块注释与**整行**的 `//`：行尾的 `//` 会误伤 `'http://...'` 这种字符串。
 */
function code(name: string): string {
  return read(name)
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .filter((line) => !/^\s*\/\//.test(line))
    .join('\n')
}

test('面板要用的五个文件都在（少一个用户装上就缺）', () => {
  for (const name of ['index.html', 'renderer.js', 'panel.css', 'main.cjs', 'preload.cjs', 'tray.png', 'package.json']) {
    assert.ok(existsSync(join(PANEL, name)), `缺文件: resources/panel/${name}`)
  }
})

test('resources/ 在 npm 包的 files 白名单里（否则发布包里不会有面板）', () => {
  const pkg = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf8'))
  assert.ok(Array.isArray(pkg.files))
  assert.ok(pkg.files.some((f: string) => f === 'resources/' || f.startsWith('resources')),
    `files 里没有 resources/：${JSON.stringify(pkg.files)}`)
})

test('渲染进程**绝不用 innerHTML** —— 助手回复里会有用户自己的聊天内容', () => {
  // 这条是整份文件里最要紧的。用 innerHTML 就等于在那个窗口里执行回复里的内容，
  // 而窗口没有地址栏，用户看不出被导航走了。
  const renderer = code('renderer.js')
  assert.doesNotMatch(renderer, /innerHTML/, 'renderer.js 里出现了 innerHTML')
  assert.doesNotMatch(renderer, /outerHTML|insertAdjacentHTML|document\.write/)
  assert.match(renderer, /textContent/, '应该用 textContent 来放内容')
})

test('渲染进程不碰文件系统、不碰进程、不碰凭据', () => {
  const renderer = code('renderer.js')
  for (const forbidden of [/require\s*\(/, /child_process/, /\bfs\b/, /localStorage/, /token/i, /Bearer/]) {
    assert.doesNotMatch(renderer, forbidden, `renderer.js 里出现了 ${forbidden}`)
  }
})

test('preload 只暴露四个方法，且**不含**通用的 on/send', () => {
  const preload = code('preload.cjs')
  const exposed = [...preload.matchAll(/^\s{2}(\w+):/gm)].map((m) => m[1]).sort()
  assert.deepEqual(exposed, ['info', 'onMode', 'quit', 'setMode'])
  // 通用订阅才是危险的：通道名一旦由渲染进程决定，那层隔离就名存实亡
  assert.doesNotMatch(preload, /on\s*:\s*\(/, '不许暴露通用的 on(name, cb)')
  assert.doesNotMatch(preload, /send\s*:\s*\(/)
  assert.match(preload, /contextIsolation|contextBridge/, '必须走 contextBridge')
})

test('主进程不许把凭据放进命令行，也不许把 token 递给渲染进程', () => {
  const main = code('main.cjs')
  // 端口与 token 只允许来自"自己读端点文件"
  assert.match(main, /assistant_endpoint\.json/, '应当自己读端点文件')
  assert.match(main, /httpOnly:\s*true/, 'token 必须以 HttpOnly cookie 的形式交给页面')
  // 任何把 token 拼进 URL 或 argv 的写法都要挡
  assert.doesNotMatch(main, /\?t=\$\{|token=/, 'URL/argv 里不许出现 token')
  assert.doesNotMatch(main, /webPreferences[\s\S]{0,400}nodeIntegration:\s*true/, '不许开 nodeIntegration')
  assert.match(main, /contextIsolation:\s*true/)
  assert.match(main, /sandbox:\s*true/)
})

test('主进程拦住导航与新窗口：这个窗口没有地址栏', () => {
  const main = code('main.cjs')
  assert.match(main, /setWindowOpenHandler/)
  assert.match(main, /will-navigate/)
})

test('页面自己声明了 CSP 之外的收敛：无内联脚本、无外部资源', () => {
  const html = code('index.html')
  assert.doesNotMatch(html, /<script(?![^>]*\ssrc=)/, '不许有内联 script（CSP 也会挡，但别写）')
  assert.doesNotMatch(html, /https?:\/\//, '页面不许引外部资源')
  assert.match(html, /<script src="\/panel\/renderer\.js">/)
})

test('端点服务只认白名单里的那几个文件名（不是把目录挂出去）', async () => {
  const { readFileSync: rf } = await import('node:fs')
  const server = rf(join(ROOT, 'src', 'panel', 'server.ts'), 'utf8')
  assert.match(server, /const STATIC_FILES/, '静态文件必须是白名单常量')
  assert.match(server, /'\/panel\/renderer\.js'/)
  // 挂目录就要处理路径穿越；这里根本不需要那个能力，所以不该出现读 URL 路径再拼文件名的写法
  assert.doesNotMatch(server, /join\([^)]*url\.pathname/, '不许拿 URL 的路径去拼文件路径')
})

test('主进程：单实例锁、快捷键注册要检查返回值、关窗只是隐藏', () => {
  // 菜单的**内容**归 `test/panel-tray-menu.test.ts` 管（那边能真的调回调）。
  // 这条只管 main.cjs 自己的三件事，避免两处重复断言同一件事、其中一处还更弱。
  const main = code('main.cjs')
  assert.match(main, /requestSingleInstanceLock/, '没有单实例锁就会有两个球、两个托盘')
  assert.match(main, /globalShortcut\.register/, '全局快捷键')
  assert.match(main, /if \(!ok\)/, '快捷键注册失败是静默的（只返回 false），必须报出来')
  assert.match(main, /isQuitting/, '关窗要走 hide 而不是 quit：助手还在后台跑')
})

test('窗口尺寸切换要先解锁再改尺寸 —— Windows 上不可调整大小的窗口会忽略 setSize', () => {
  // 这条是真 bug 的回归测试：第一版先 setResizable(false) 再 setSize(76,76)，
  // 结果"收起"以后窗口停在对话窗的大小（实测 421x561），屏幕上就是一个巨大的球。
  const main = code('main.cjs')
  const fn = main.slice(main.indexOf('function setMode'), main.indexOf('function toggleVisible'))
  assert.ok(fn.length > 0, '应当能找到 setMode')
  const unlock = fn.indexOf('setResizable(true)')
  const shrink = fn.indexOf('setSize(BALL_SIZE')
  const lock = fn.lastIndexOf('setResizable(false)')
  assert.ok(unlock >= 0 && shrink >= 0 && lock >= 0, '三处都要在')
  assert.ok(unlock < shrink, '先解锁再改尺寸')
  assert.ok(shrink < lock, '改完尺寸最后才锁上')
})

test('ready-to-show 的监听要挂在 loadURL 之前 —— 它不会重放', () => {
  // 第二个真 bug 的回归测试：第一版在 `await loadURL` 之后才挂 `ready-to-show`，
  // 而那个事件在加载过程中就可能已经触发过、且不会重放，于是窗口永远不显示
  // （实测：76x76、无边框、置顶全都正常，只有 vis=False——光看几何看不出来）。
  const main = code('main.cjs')
  const listen = main.indexOf("once('ready-to-show'")
  const load = main.indexOf('await win.loadURL')
  assert.ok(listen >= 0 && load >= 0, '两处都要在')
  assert.ok(listen < load, 'ready-to-show 必须挂在 loadURL 之前')
})

test('调 CLI 必须用 `-e import()` 的写法 —— 直接把脚本路径当参数会被 commander 当成未知命令', () => {
  // 第三个真 bug 的回归测试。`spawn(electron, [cliEntry, 'assistant', 'stop'])` 配
  // `ELECTRON_RUN_AS_NODE=1` 会得到 `error: unknown command '…\cli.cjs'`：
  // Electron 的 Node 模式里 commander 不跳过 `process.argv[1]`。
  // 实测两种写法（前者报 unknown command、后者正常），解法记在 `bin/weflow-cli-electron.cjs` 的注释里。
  const main = code('main.cjs')
  assert.match(main, /'-e'/, '要用 -e 传一段脚本')
  assert.match(main, /import\(/, '脚本内容应当是 import(file:///…)')
  assert.match(main, /pathToFileURL/, '路径要过 pathToFileURL（本机的仓库目录是中文）')
  assert.match(main, /ELECTRON_RUN_AS_NODE/, '要electron 以 Node 模式跑')
  // 反向：不许再出现"把 cli.cjs 直接放进 argv"的写法
  assert.doesNotMatch(main, /\bspawn\(process\.execPath,\s*\[[^\]]*cli\.cjs/, '不许把脚本路径直接当参数')
})
