/**
 * 悬浮球的 Electron 外壳。**这里只做窗口与托盘**——所有"用哪个二进制、传什么参数"的决策
 * 都在 `src/panel/launch.ts`（纯函数、能进 CI），所有对话逻辑都在守护进程那边。
 *
 * 三条纪律：
 *
 * 1. **凭据不进 argv**。这个进程是被 `weflow-cli panel` 用 `electron <panel目录>` 拉起来的，
 *    命令行里一个参数都没有。端口与 token 由它**自己读** `~/.weflow-cli/assistant_endpoint.json`
 *    （Windows 上同机任何进程都能读命令行，把 token 放进去就是一次看不出破绽的泄漏）。
 * 2. **token 不进渲染进程**。读出来之后立刻种成一个 `HttpOnly` cookie，然后 `loadURL` 那个
 *    相对地址。页面脚本永远拿不到它，但它照常能发 `/api/...` 请求。
 * 3. **不加载任何远程内容，不许导航**。窗口里显示的是助手回复，而回复里有用户的聊天内容。
 */
const { app, BrowserWindow, Tray, Menu, globalShortcut, ipcMain, session, shell, screen, nativeImage } = require('electron')
const { readFileSync, writeFileSync, existsSync, mkdirSync, renameSync } = require('node:fs')
const { join } = require('node:path')
const { spawn } = require('node:child_process')
const { pathToFileURL } = require('node:url')
const os = require('node:os')
const { trayMenuTemplate } = require('./tray-menu.cjs')

const ENDPOINT_FILE = join(os.homedir(), '.weflow-cli', 'assistant_endpoint.json')
const POSITION_FILE = join(os.homedir(), '.weflow-cli', 'panel_position.json')
const COOKIE_NAME = 'weflow_panel'
const BALL_SIZE = 76
const CHAT_SIZE = { width: 420, height: 560 }
/** 球离屏幕边缘留多少：贴死边缘在 Windows 上会跟任务栏/贴边功能打架 */
const EDGE_MARGIN = 24

let win = null
let tray = null
let ballMode = true
let shortCircuitFailures = 0

/**
 * 跑一条 CLI 命令（目前只用来停助手）。**这里踩过一个坑，写法不能再简化**：
 *
 * 直接把脚本路径当参数传（`spawn(process.execPath, [cliEntry, 'assistant', 'stop', …])`，
 * 配 `ELECTRON_RUN_AS_NODE=1`）会得到 `error: unknown command '…\cli.cjs'` ——
 * Electron 的 Node 模式里 commander **不会跳过 `process.argv[1]`**，于是它把脚本路径当成了未知命令。
 * 仓库里 `bin/weflow-cli-electron.cjs` 早就记着这个坑，解法是
 * `-e "import('file:///…')" -- <参数>`。实测两种写法：前者报 unknown command，后者正常。
 *
 * 路径要过 `pathToFileURL`：本机的仓库目录就是中文（`ZhaoWen_GitHub维护`），
 * `file:///` 后面直接拼原始字符不是合法 URL。它同时负责百分号编码。
 */
function spawnCli(cliArgs) {
  try {
    const entry = join(__dirname, '..', '..', 'cli.cjs')
    if (!existsSync(entry)) return false
    const script = `import(${JSON.stringify(pathToFileURL(entry).href)})`
    spawn(process.execPath, ['-e', script, '--', ...cliArgs], {
      detached: true, stdio: 'ignore', windowsHide: true,
      env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' },
    }).unref()
    return true
  } catch (error) {
    console.error('[panel] 调 CLI 失败:', error.message)
    return false
  }
}

// 位置算术在 `ball-position.cjs`（纯函数、能被 CI 用合成布局测）；
// 这里只负责"问 Electron 要显示器列表"，然后把真实的显示器喂给它。
const { defaultBallPosition: defaultBall, clampInto, resolveStartPosition } = require('./ball-position.cjs')

function workAreas() { return screen.getAllDisplays().map((d) => d.workArea) }
function primaryWorkArea() { return screen.getPrimaryDisplay().workArea }

/** 小球该在哪儿。**默认放右下角**——从前不给 `x/y`，于是它落在 Windows 顺手给的位置
 * （本机实测 815,418，屏幕中偏左），那不叫"屏幕角落一个球"。 */
function defaultBallPosition() { return defaultBall(primaryWorkArea(), BALL_SIZE, EDGE_MARGIN) }

/** 把一个矩形挪进它所在那块屏的可见区域（不缩尺寸，只挪）。
 * 展开成对话窗时要用：球在右下角，直接按球的位置铺开一个 420x560 的窗会有一半在屏幕外。 */
function fitIntoWorkArea(x, y, width, height) {
  return clampInto(x, y, width, screen.getDisplayNearestPoint({ x, y }).workArea)
}

/** 记住的位置还能不能用（不可达就回默认角落） */
function clampToVisible(pos) {
  return resolveStartPosition(pos, workAreas(), primaryWorkArea(), BALL_SIZE, EDGE_MARGIN)
}

/**
 * 位置落盘（原子写：先写 `.tmp` 再改名，同 `configService.save()` 的手法）。
 * 记不住位置不是故障——下次回默认角落就是了，所以失败不抛。
 */
function savePosition(x, y) {
  if (!Number.isInteger(x) || !Number.isInteger(y)) return
  try {
    mkdirSync(join(os.homedir(), '.weflow-cli'), { recursive: true })
    const tmp = POSITION_FILE + '.tmp'
    writeFileSync(tmp, JSON.stringify({ x, y }), 'utf8')
    renameSync(tmp, POSITION_FILE)
  } catch { /* 见上 */ }
}

/** 读回记住的位置。不存在 / JSON 坏了 / 字段不对 —— 一律当没存过（可达性另判） */
function readSavedPosition() {
  try {
    const parsed = JSON.parse(readFileSync(POSITION_FILE, 'utf8'))
    if (Number.isInteger(parsed?.x) && Number.isInteger(parsed?.y)) return { x: parsed.x, y: parsed.y }
  } catch { /* 见上 */ }
  return null
}

/**
 * 拖动时记位置。**监听 `move` 而不是 `moved`**：`moved` 靠 `WM_EXITSIZEMOVE` 触发，
 * 而程序化的 `SetWindowPos` **不产生**它——实测"窗口确实挪到了 400,300，位置文件却没写出来"。
 * 真人的拖动会发 `WM_MOVE`，`move` 两边都收；代价是拖动过程中会连续触发，所以加防抖。
 */
let saveTimer = null
function flushPosition() {
  saveTimer = null
  if (!ballMode || !win || win.isDestroyed()) return
  const b = win.getBounds()
  savePosition(b.x, b.y)
}
function schedulePositionSave() {
  if (!ballMode) return
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(flushPosition, 400)
}

/** 读端点文件。**任何一种不可信都当没读出来**（同 `src/panel/endpoint.ts` 的纪律） */
function readEndpoint() {
  try {
    if (!existsSync(ENDPOINT_FILE)) return null
    const parsed = JSON.parse(readFileSync(ENDPOINT_FILE, 'utf8'))
    if (parsed?.version !== 1 || parsed?.service !== 'weflow-assistant') return null
    if (typeof parsed.port !== 'number' || typeof parsed.token !== 'string') return null
    return parsed
  } catch {
    return null
  }
}

function errorPage(message, hint) {
  const html = `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<style>body{margin:0;height:100vh;display:flex;flex-direction:column;justify-content:center;
padding:18px;background:#17181c;color:#e6e7ea;font:14px/1.6 "Microsoft YaHei",system-ui;
-webkit-app-region:drag}h2{font-size:15px;margin:0 0 8px}p{margin:0 0 6px;color:#9aa0aa}
code{color:#4c8bf5;font-size:12px}</style></head><body>
<h2>${message}</h2><p>${hint}</p></body></html>`
  return 'data:text/html;charset=utf-8,' + encodeURIComponent(html)
}

async function buildWindow() {
  // 上次拖到的位置（取不到或不可达就回右下角）
  const start = clampToVisible(readSavedPosition() ?? defaultBallPosition())
  win = new BrowserWindow({
    x: start.x,
    y: start.y,
    width: BALL_SIZE,
    height: BALL_SIZE,
    frame: false,
    transparent: true,
    resizable: false,
    // 悬浮球不进任务栏；展开成对话窗时再让它进去（见 setMode）
    skipTaskbar: true,
    alwaysOnTop: true,
    fullscreenable: false,
    maximizable: false,
    minimizable: false,
    show: false,
    webPreferences: {
      preload: join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webviewTag: false,
      // 页面来自 http://127.0.0.1:<port>，不是本地文件
      webSecurity: true,
    },
  })
  win.setAlwaysOnTop(true, 'floating')

  // **监听要在 `loadURL` 之前挂**：`ready-to-show` 在加载过程中就可能触发（透明窗口上确实会），
  // 而它**不会重放**——挂晚了窗口就永远不显示。第一版是 `await loadURL` 之后才挂的，
  // 于是"有时候看得到球、有时候看不到"，而窗口的尺寸与样式全都正常（实测 `vis=False`、
  // 76x76、无边框、置顶），光看几何完全看不出来。
  win.once('ready-to-show', () => { if (!win.isVisible()) win.show() })

  const endpoint = readEndpoint()
  if (!endpoint) {
    await win.loadURL(errorPage('助手没在运行', '先在终端跑一次 weflow-cli assistant start，再打开本机面板。'))
  } else {
    // token → HttpOnly cookie → 然后才 loadURL。**token 不进 URL、不进 argv、不进渲染进程。**
    await session.defaultSession.cookies.set({
      url: `http://127.0.0.1:${endpoint.port}`,
      name: COOKIE_NAME,
      value: endpoint.token,
      httpOnly: true,
      sameSite: 'strict',
    })
    await win.loadURL(`http://127.0.0.1:${endpoint.port}/panel`)
  }

  // 不许导航、不许开新窗口：这个窗口没有地址栏，导航走了用户看不出来
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//.test(url)) shell.openExternal(url)   // 外链交给系统浏览器
    return { action: 'deny' }
  })
  win.webContents.on('will-navigate', (event, url) => {
    if (!url.startsWith('http://127.0.0.1:')) {
      event.preventDefault()
      shell.openExternal(url)
    }
  })

  // 拖动时记住位置。**只记球形态的**：球是锚点，对话窗的尺寸与位置是临时的。
  win.on('move', schedulePositionSave)

  // 关窗 = 收进托盘，**不**顺手杀掉助手（用户在微信那边可能还要用）
  win.on('close', (event) => {
    if (!app.isQuitting) {
      event.preventDefault()
      win.hide()
    }
  })

  // 兜底：`ready-to-show` 可能在我们挂上监听之前就已经触发过（透明窗口上确实会），
  // 那时它不会重放，窗口就永远不显示。所以加载完成后再显式确认一次（幂等）。
  if (!win.isVisible()) win.show()
}

function setMode(mode) {
  if (!win) return
  const chat = mode === 'chat'

  // **顺序是有讲究的**：Windows 上不可调整大小的窗口会**忽略 `setSize`**。
  // 第一版先 `setResizable(false)` 再 `setSize(76,76)`，结果是"收起"以后页面回到球形态、
  // 置顶也恢复了，窗口却停在对话窗的大小（实测 421x561）——屏幕上就是一个巨大的球。
  // 所以：先解锁 → 改尺寸 → 最后再锁上。
  // 尺寸与位置**一起**用 setContentBounds 设：这个窗口上 setSize + setPosition 会互相打架
  // （setPosition 动的是外框，实测每调一次尺寸就漂 1-2px——展开后量到 421x561 而不是 420x560
  // 就是这个漂移，拖动那条路更夸张，见 panel:dragMove 的注释）。
  win.setResizable(true)
  const from = win.getContentBounds()
  if (chat) {
    // 球在右下角时，直接按它的位置铺开一个 420x560 的窗会有一半在屏幕外——挪进来
    const fitted = fitIntoWorkArea(from.x, from.y, CHAT_SIZE.width, CHAT_SIZE.height)
    win.setContentBounds({ x: fitted.x, y: fitted.y, width: CHAT_SIZE.width, height: CHAT_SIZE.height })
    win.setAlwaysOnTop(false)     // 对话时不必压着别的窗口
    win.setSkipTaskbar(false)
    win.setResizable(true)        // 对话窗允许用户自己拉大小
  } else {
    // 反过来也一样：从屏幕右下角的对话窗收回来，球要整个看得见（中心可见才抓得回来）
    const fitted = fitIntoWorkArea(from.x, from.y, BALL_SIZE, BALL_SIZE)
    win.setContentBounds({ x: fitted.x, y: fitted.y, width: BALL_SIZE, height: BALL_SIZE })
    savePosition(fitted.x, fitted.y)
    win.setAlwaysOnTop(true, 'floating')
    win.setSkipTaskbar(true)
    win.setResizable(false)
  }
  ballMode = !chat
  return { mode: chat ? 'chat' : 'ball' }
}

function toggleVisible() {
  if (!win) return
  if (win.isVisible()) win.hide()
  else { win.show(); win.focus() }
}

function buildTray() {
  try {
    // 托盘用**单独一张** `tray.png`（64x64：圆盘 + 吉祥物），不是拿 mascot.png 缩：
    // mascot.png 的背景是透明的，深色的猫直接放到深色任务栏上会糊掉——
    // 这和球面那次是**同一个问题**（当时在浅/中/深三种底上并排比过）。
    // 两张图确实要分开维护，但它们的取景本来就不同：一个是球面，一个是托盘图标。
    const icon = nativeImage.createFromPath(join(__dirname, 'tray.png'))
    if (icon.isEmpty()) throw new Error('托盘图标没读出来（tray.png 缺失或损坏）')
    tray = new Tray(icon)
  } catch (error) {
    // 图标缺失不能静默：托盘没了，用户就只剩快捷键和窗口本身
    console.error('[panel] 托盘图标加载失败:', error.message)
    return
  }
  tray.setToolTip('第二大脑')
  // 菜单的**内容**在 `tray-menu.cjs` 里（纯数据、不 require electron），所以它能被 CI 直接测；
  // 这里只负责把真实动作接上去。
  tray.setContextMenu(Menu.buildFromTemplate(trayMenuTemplate({
    toggleVisible,
    expandToChat: () => { win?.show(); setMode('chat'); win?.webContents.send('panel:mode', 'chat') },
    quitPanel: () => { app.isQuitting = true; app.quit() },
    quitAndStopAssistant: () => {
      app.isQuitting = true
      // 走 CLI 而不是自己 kill：pid 文件与端点文件的清理都在那儿（见 spawnCli 的注释）。
      // **先起进程再退出**：`app.quit()` 之后本进程就没机会 spawn 了。
      spawnCli(['assistant', 'stop', '--yes', '--json'])
      app.quit()
    },
  })))
  tray.on('click', () => toggleVisible())
}

app.isQuitting = false

// **单实例**：没有它，启动两次就有两个球、两个托盘，而第二次 `globalShortcut.register` 会静默失败
if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', () => { win?.show(); win?.focus() })

  app.whenReady().then(async () => {
    ipcMain.handle('panel:setMode', (_event, mode) => setMode(mode))

    // 拖拽：按下时记下"窗口位置 + 指针位置"，移动时按差值挪窗口。
    // 用差值而不是绝对值，是为了不受 DPI 缩放与多屏坐标原点的影响。
    let dragOrigin = null
    ipcMain.handle('panel:dragStart', (_event, point) => {
      if (!win || win.isDestroyed()) return null
      const { x: wx, y: wy } = win.getContentBounds()
      dragOrigin = { pointerX: point.x, pointerY: point.y, winX: wx, winY: wy }
      return { x: wx, y: wy }
    })
    ipcMain.handle('panel:dragMove', (_event, point) => {
      if (!win || win.isDestroyed() || !dragOrigin) return null
      // **用 setBounds 而不是 setPosition**：实测这个窗口（无边框 + 透明 + resizable:false）上
      // `setPosition` 会**把窗口一点点撑大**——每次调用宽 +2 左右，连续拖 8 次之后
      // 76x76 变成 108x84（隔离验证：完全不碰鼠标、只调这两个 IPC 也能复现）。
      // 显式把当前尺寸一起传进去就不会。
      const bounds = win.getContentBounds()
      win.setContentBounds({
        x: Math.round(dragOrigin.winX + point.x - dragOrigin.pointerX),
        y: Math.round(dragOrigin.winY + point.y - dragOrigin.pointerY),
        width: bounds.width,
        height: bounds.height,
      })
      return null
    })
    ipcMain.handle('panel:dragEnd', () => { dragOrigin = null; return null })
    ipcMain.handle('panel:info', () => ({ daemonRunning: !!readEndpoint(), endpointFile: ENDPOINT_FILE }))
    ipcMain.handle('panel:quit', (_event, what) => {
      app.isQuitting = true
      if (what === 'assistant') spawnCli(['assistant', 'stop', '--yes', '--json'])
      app.quit()
    })

    await buildWindow()
    buildTray()

    // 全局快捷键。**必须看返回值**：组合键被别的程序占了时 register 只是返回 false，
    // 而用户会以为"按了没反应"。
    const ok = globalShortcut.register('CommandOrControl+Shift+W', () => toggleVisible())
    if (!ok) {
      shortCircuitFailures++
      console.error('[panel] 全局快捷键 Ctrl+Shift+W 注册失败（可能被别的程序占用）')
    }
  })

  app.on('before-quit', () => { if (saveTimer) flushPosition() })
  app.on('will-quit', () => globalShortcut.unregisterAll())
  // 全部窗口关掉也不退出：这是个托盘常驻应用
  app.on('window-all-closed', () => { /* 故意留空 */ })
}
