/**
 * 渲染进程与主进程之间**唯一**的那道缝。
 *
 * 刻意只暴露三件事，而且全是窗口层面的：**没有凭据、没有端口、没有文件系统、没有 URL**。
 * 页面自己用 `fetch('/api/...')` 跟守护进程说话（相对路径 + HttpOnly cookie），
 * 不需要经过主进程，所以也就没有理由把凭据递给它。
 *
 * 订阅只开**一个**，而且通道名**写死在这里**：风险从来不是"能收推送"，而是
 * "渲染进程能决定通道名"——那等于把整条 IPC 面交出去。所以不给通用的 `on(name, cb)`。
 */
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('weflowPanel', {
  /** 'ball' 收成小球，'chat' 展开成对话窗 */
  setMode: (mode) => ipcRenderer.invoke('panel:setMode', mode === 'chat' ? 'chat' : 'ball'),
  /** 'window' 只关窗（助手继续跑），'assistant' 连助手一起停 */
  quit: (what) => ipcRenderer.invoke('panel:quit', what === 'assistant' ? 'assistant' : 'window'),
  /** 主进程侧的实际状态，供界面显示"关窗后助手还在跑"这类事实 */
  info: () => ipcRenderer.invoke('panel:info'),
  /**
   * 主进程（托盘菜单）把窗口切成了对话模式时通知页面。**通道名是常量，不由调用方给**。
   * 回调只收到 'ball' / 'chat' 两个字符串。
   */
  onMode: (cb) => {
    if (typeof cb !== 'function') return
    ipcRenderer.on('panel:mode', (_event, mode) => cb(mode === 'chat' ? 'chat' : 'ball'))
  },
})
