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
const { app, BrowserWindow, Tray, Menu, globalShortcut, ipcMain, session, shell } = require('electron')
const { readFileSync, existsSync } = require('node:fs')
const { join } = require('node:path')
const os = require('node:os')

const ENDPOINT_FILE = join(os.homedir(), '.weflow-cli', 'assistant_endpoint.json')
const COOKIE_NAME = 'weflow_panel'
const BALL_SIZE = 76
const CHAT_SIZE = { width: 420, height: 560 }

let win = null
let tray = null
let shortCircuitFailures = 0

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
  win = new BrowserWindow({
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

  // 关窗 = 收进托盘，**不**顺手杀掉助手（用户在微信那边可能还要用）
  win.on('close', (event) => {
    if (!app.isQuitting) {
      event.preventDefault()
      win.hide()
    }
  })

  win.once('ready-to-show', () => win.show())
}

function setMode(mode) {
  if (!win) return
  const chat = mode === 'chat'
  win.setResizable(chat)
  win.setSkipTaskbar(!chat)
  if (chat) {
    win.setSize(CHAT_SIZE.width, CHAT_SIZE.height)
    win.setAlwaysOnTop(false)     // 对话时不必压着别的窗口
  } else {
    win.setSize(BALL_SIZE, BALL_SIZE)
    win.setAlwaysOnTop(true, 'floating')
  }
  return { mode: chat ? 'chat' : 'ball' }
}

function toggleVisible() {
  if (!win) return
  if (win.isVisible()) win.hide()
  else { win.show(); win.focus() }
}

function buildTray() {
  try {
    tray = new Tray(join(__dirname, 'tray.png'))
  } catch (error) {
    // 图标缺失不能静默：托盘没了，用户就只剩快捷键和窗口本身
    console.error('[panel] 托盘图标加载失败:', error.message)
    return
  }
  tray.setToolTip('第二大脑')
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: '显示 / 收起', click: () => toggleVisible() },
    { label: '展开为对话窗', click: () => { win?.show(); setMode('chat'); win?.webContents.send('panel:mode', 'chat') } },
    { type: 'separator' },
    {
      label: '退出面板（助手继续运行）',
      click: () => { app.isQuitting = true; app.quit() },
    },
    {
      label: '退出并停止助手',
      click: async () => {
        app.isQuitting = true
        try {
          const { spawn } = require('node:child_process')
          // 走 CLI 而不是自己 kill：pid 文件与端点文件的清理都在那儿
          spawn(process.execPath, [join(__dirname, '..', '..', 'cli.cjs'), 'assistant', 'stop', '--yes', '--json'],
            { stdio: 'ignore', windowsHide: true, env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' } })
        } catch { /* 停不掉也不该拦着退出 */ }
        app.quit()
      },
    },
  ]))
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
    ipcMain.handle('panel:info', () => ({ daemonRunning: !!readEndpoint(), endpointFile: ENDPOINT_FILE }))
    ipcMain.handle('panel:quit', (_event, what) => {
      if (what === 'assistant') {
        const { spawn } = require('node:child_process')
        try {
          spawn(process.execPath, [join(__dirname, '..', '..', 'cli.cjs'), 'assistant', 'stop', '--yes', '--json'],
            { stdio: 'ignore', windowsHide: true, env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' } })
        } catch { /* 同上 */ }
      }
      app.isQuitting = true
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

  app.on('will-quit', () => globalShortcut.unregisterAll())
  // 全部窗口关掉也不退出：这是个托盘常驻应用
  app.on('window-all-closed', () => { /* 故意留空 */ })
}
