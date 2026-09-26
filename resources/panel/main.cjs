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
const { quickMenuTemplate } = require('./quick-menu.cjs')

const ENDPOINT_FILE = join(os.homedir(), '.weflow-cli', 'assistant_endpoint.json')
const POSITION_FILE = join(os.homedir(), '.weflow-cli', 'panel_position.json')
const COOKIE_NAME = 'weflow_panel'
/** 收起时气泡淡出多久。页面照着淡、主进程照着等——**只此一处**，两边不会走散。
 *  另外它还是缩窗口的**下限延迟**：页面得先收到通知把气泡摘掉，窗口再缩，否则会闪出一条边。 */
const CLOSE_FADE_MS = 130

let win = null
let tray = null
let ballMode = true
let shortCircuitFailures = 0
/**
 * 球挂在窗口的哪一侧、竖向离窗口顶多远。展开时由 `bubbleLayout` 定下来（页面照它把球钉住），
 * 收起时用它把球从窗口里算回来——**不能假定是窗口的左上角**。
 */
let ballAnchor = { side: 'left', anchorY: 'bottom' }

/**
 * 跑一条 CLI 命令（目前只用来停助手）。**这里踩过一个坑，写法不能再简化**：
 *
 * 直接把脚本路径当参数传（`spawn(process.execPath, [cliEntry, 'assistant', 'stop', …])`，
 * 配 `ELECTRON_RUN_AS_NODE=1`）会得到 `error: unknown command '…\cli.cjs'` ——
 * Electron 的 Node 模式里 commander **不会跳过 `process.argv[1]`**，于是它把脚本路径当成了未知命令。
 * 仓库里 `bin/weflow-cli-electron.cjs` 早就记着这个坑，解法是
 * `-e "import('file:///…')" -- <参数>`。实测两种写法：前者报 unknown command，后者正常。
 *
 * 路径要过 `pathToFileURL`：仓库目录**可能是中文/带空格的**（本机就是），
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
// 球的尺寸与气泡的尺寸/间距**只有一份**（就在那里）。这里原本又写了一遍 `76` 和一个
// `CHAT_SIZE`——每样东西两处各写一个数字，改一处就等着它们分家；`panel.css` 那边有对应的
// `--ball-size` / `--bubble-width` / `--bubble-gap`（CSS 拿不到 JS 的值），那几份由
// `test/panel-packaging.test.ts` 断言与本模块的这些值相等。
const { BALL_SIZE, EDGE_MARGIN, BUBBLE_SIZE, bubbleLayout, ballRectInWindow,
        defaultBallPosition: defaultBall, clampInto, resolveStartPosition } = require('./ball-position.cjs')

function workAreas() { return screen.getAllDisplays().map((d) => d.workArea) }
function primaryWorkArea() { return screen.getPrimaryDisplay().workArea }

/** 小球该在哪儿。**默认放右下角**——从前不给 `x/y`，于是它落在 Windows 顺手给的位置
 * （本机实测 815,418，屏幕中偏左），那不叫"屏幕角落一个球"。 */
function defaultBallPosition() { return defaultBall(primaryWorkArea(), BALL_SIZE, EDGE_MARGIN) }

/** 这个点落在哪块屏的可见区域里。**问 Electron 的那一半只此一处**。 */
function workAreaAt(x, y) {
  return screen.getDisplayNearestPoint({ x, y }).workArea
}

/** 把一个矩形挪进它所在那块屏的可见区域（不缩尺寸，只挪）。
 * 收回球形态时要用：气泡拖到屏幕角落，直接从那儿缩回 76x76 会有一半在屏幕外。 */
function fitIntoWorkArea(x, y, width, height) {
  return clampInto(x, y, width, workAreaAt(x, y))
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

/**
 * 展开/收起。三件事决定了它的形状：
 *
 * 1. **球不能撤**（用户原话："气泡在它旁边展开，像这个图标在说话，点击收起"）；
 * 2. **球一个像素都不许动**——所以窗口的矩形由 `bubbleLayout`（`ball-position.cjs`，
 *    有测试）算成"球 + 空隙 + 气泡"的并集，并把球在窗口里的偏移一并返回，页面照着钉；
 * 3. **窗口只改一次大小**。从前那版是逐帧补间窗口几何，用户实测"闪好多才弹出"——
 *    透明窗口每 resize 一次 DWM 就要重新合成一次，一秒钟里改七八次就是七八次闪。
 *    现在：窗口一次到位，**动效全交给页面那边**（气泡的淡入与缩放是合成器做的，不重新布局）。
 *
 * 两次 resize 都发生在**看不见的时刻**：展开时窗口先长出来（那一大片是透明的，页面上
 * 什么都还没显示，随后气泡淡入）；收起时页面先把气泡淡掉，淡完这里才缩窗口。所以任何一帧
 * 里"可见的东西"都没有跟着窗口跳过。
 *
 * **形态与方位由这里说了算**：页面只请求，照着 `panel:mode` 做。收起分两步
 * （`fade` → 页面开始淡出；`done` → 页面换成球形态），因为窗口要等淡完才能缩，而页面
 * 要等窗口缩完才能把气泡摘掉——晚一帧就会在 76x76 的窗口里看见一条气泡的边。
 */
function setMode(mode, opts) {
  if (!win) return
  const chat = mode === 'chat'

  // **顺序是有讲究的**：Windows 上不可调整大小的窗口会**忽略 `setSize`**。
  // 第一版先 `setResizable(false)` 再 `setSize(76,76)`，结果是"收起"以后页面回到球形态、
  // 置顶也恢复了，窗口却停在对话窗的大小（实测 421x561）——屏幕上就是一个巨大的球。
  // 所以：先解锁 → 改尺寸 → 最后再锁上。
  // 尺寸与位置**一起**走 setContentBounds（详见 `panel:dragMove` 那条注释）。
  // 顺带更正一句旧注释：它把"展开后量到 421x561 而不是 420x560"记成了 setContentBounds 的漂移。
  // 2026-09-24 用探针分开量过（显示缩放 150%）：**紧挨着 setContentBounds 调 getContentBounds，
  // 读回来可能是上一拍的值**（实测要 420x560 读回 421x560，只差宽 1px），隔一拍再读就是
  // 420x560，20 次往返也没有任何累积漂移。也就是说那是**读得早**，不是它漂。
  win.setResizable(true)
  const from = win.getContentBounds()

  if (chat) {
    const layout = bubbleLayout(from, BUBBLE_SIZE, workAreaAt(from.x, from.y))
    ballAnchor = { side: layout.side, anchorY: layout.anchorY }
    win.setAlwaysOnTop(false)     // 对话时不必压着别的窗口
    win.setSkipTaskbar(false)
    win.setContentBounds(layout.window)
    win.setResizable(true)        // 展开后可调大小
    // **展开之后要自己走到前面来。** 对话形态按设计不置顶，而"谁在最前面"此前完全靠运气：
    // 右键那条路尤其明显——球是置顶的、看得见，可原生菜单一关，Windows 可能把焦点还给了
    // 别的窗口，于是候选出在一个被压在后面的窗口里，用户的原话是"不知道消息返回到哪里了"。
    if (!win.isVisible()) win.show()
    win.moveTop()                 // 先抬到 z 序顶（这一步不依赖前台锁）
    win.focus()                   // 再夺焦点：Windows 偶尔会拒绝它，所以上面那步不能省
    ballMode = false
    win.webContents.send('panel:mode', {
      mode: 'chat', side: layout.side, anchorY: layout.anchorY, bubbleHeight: layout.bubbleHeight,
    })
    return { mode: 'chat' }
  }

  // 收起：页面先把气泡淡掉（窗口这会儿还是大的，所以淡出真看得见），淡完再缩。
  const fadeMs = opts && opts.animate === false ? 0 : CLOSE_FADE_MS
  const shrink = () => {
    if (!win) return
    // **球的落点从当前窗口与其锚反推**，不是窗口的左上角（球在侧边上）
    const ball = ballRectInWindow(win.getContentBounds(), ballAnchor, BALL_SIZE)
    const fitted = fitIntoWorkArea(ball.x, ball.y, BALL_SIZE, BALL_SIZE)
    savePosition(fitted.x, fitted.y)
    win.setContentBounds({ x: fitted.x, y: fitted.y, width: BALL_SIZE, height: BALL_SIZE })
    win.setAlwaysOnTop(true, 'floating')
    win.setSkipTaskbar(true)
    win.setResizable(false)
    ballMode = true
    win.webContents.send('panel:mode', { mode: 'ball', done: true })
  }
  win.webContents.send('panel:mode', { mode: 'ball', fadeMs })
  // 至少等一帧：页面先把气泡摘掉，这里再缩窗口。反过来那一帧里 76x76 的窗口会露出气泡的一条边
  setTimeout(shrink, Math.max(fadeMs, 16))
  return { mode: 'ball' }
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
    // 形态通知由 `setMode` 自己发（不再由调用方发一遍：两处发同一个事件，早晚有一处忘）
    expandToChat: () => { win?.show(); setMode('chat') },
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
    ipcMain.handle('panel:setMode', (_event, mode, opts) => setMode(mode, opts))

    // 右键"快速回复"。**用原生菜单**：它画在窗口外面，所以球那 76x76 的窗口不用先展开
    // （页内菜单做不到这一点——它会被窗口裁掉）。选中回一个名字，关掉没选回 null。
    ipcMain.handle('panel:quickMenu', (_event, labels) => new Promise((resolve) => {
      if (!win || win.isDestroyed()) { resolve(null); return }
      let picked = null
      // 先记下来、关菜单时再 resolve：`click` 与 `popup` 的 callback 谁先到不该决定结果
      const menu = Menu.buildFromTemplate(quickMenuTemplate(labels, {
        // 回传的是**结构化的一项**（{kind:'contact'|'action', ...}），不是光一个名字：
        // 菜单现在有两段，"点的是哪一段"必须跟着回来，否则页面只能靠"名字长得像不像动作"来猜。
        // 动作的话术（`prompt`）也由菜单项带回来——id→话术的映射只存一份，不存两处。
        pick: (item) => { picked = item },
        // 菜单最后那一项「关闭悬浮球」：**只把窗口收起来**，不退出（退出那一类在托盘菜单里）。
        // 收起来是可逆的——托盘图标、托盘菜单的「显示 / 收起」、Ctrl+Shift+W 都叫得回来，
        // 所以标签里写明了从哪叫回来。收起之后 `picked` 仍是 null，页面那边什么都不会发。
        close: () => { picked = null; if (win && !win.isDestroyed()) win.hide() },
      }))
      menu.popup({ window: win, callback: () => resolve(picked) })
    }))

    // 拖拽：按下时记下"窗口位置 + 指针位置"，移动时按差值挪窗口。
    // 用差值而不是绝对值，是为了不受 DPI 缩放与多屏坐标原点的影响。
    let dragOrigin = null
    ipcMain.handle('panel:dragStart', (_event, point) => {
      if (!win || win.isDestroyed()) return null
      const from = win.getContentBounds()
      // **尺寸也在这里记一次**（不只是位置）。理由见 dragMove 那段注释：
      // 每次移动去读"当前尺寸"会在**可缩放**的窗口上让尺寸跟着位移一起长。
      dragOrigin = {
        pointerX: point.x, pointerY: point.y, winX: from.x, winY: from.y,
        width: from.width, height: from.height,
      }
      return { x: from.x, y: from.y }
    })
    ipcMain.handle('panel:dragMove', (_event, point) => {
      if (!win || win.isDestroyed() || !dragOrigin) return null
      // **用 setContentBounds 而不是 setPosition**：实测这个窗口（无边框 + 透明）上
      // `setPosition` 会**把窗口一点点撑大**——每次调用宽 +2 左右，连续拖 8 次之后
      // 76x76 变成 108x84（隔离验证：完全不碰鼠标、只调这两个 IPC 也能复现）。
      // 显式把尺寸一起传进去就不会。
      //
      // **尺寸必须在 dragStart 记一次、之后一直用它，不能在每次移动时读"当前尺寸"。**
      // 那是个读-改-写的坑，只有在窗口**可缩放**时才露出来：实测同一个拖动（30 步、位移
      // 30x24）在 `resizable: false` 下尺寸纹丝不动，在 `resizable: true`（对话形态就是）下
      // **尺寸漂了 30x24——正好等于这次位移**。于是"在对话形态下拖球"会让窗口每拖一次大一圈，
      // 而窗口比"气泡 + 间距 + 球"宽出来的部分，全都变成气泡与球之间的那段空档：
      // 用户的原话是"挪动悬浮气泡时气泡和窗口之间的间距越来越远"（重新展开/收起会精确设回
      // 尺寸，所以他看到"点一下又复位了"）。
      win.setContentBounds({
        x: Math.round(dragOrigin.winX + point.x - dragOrigin.pointerX),
        y: Math.round(dragOrigin.winY + point.y - dragOrigin.pointerY),
        width: dragOrigin.width,
        height: dragOrigin.height,
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
