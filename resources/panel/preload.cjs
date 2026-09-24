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
  /**
   * 'ball' 收成小球，'chat' 展开成对话窗。
   *
   * `opts.animate === false` 表示"别做动画，直接到位"——页面读 `prefers-reduced-motion`
   * 之后传进来。**为什么让页面来读**：那个媒体查询只有渲染进程有，主进程这边没有等价 API；
   * 而窗口几何的补间在主进程，CSS 管不着它。默认是**动画**（不传就是动画）。
   */
  setMode: (mode, opts) => ipcRenderer.invoke('panel:setMode',
    mode === 'chat' ? 'chat' : 'ball',
    { animate: !opts || opts.animate !== false }),
  /** 'window' 只关窗（助手继续跑），'assistant' 连助手一起停 */
  quit: (what) => ipcRenderer.invoke('panel:quit', what === 'assistant' ? 'assistant' : 'window'),
  /**
   * 拖拽。**为什么不是 `-webkit-app-region: drag`**：在 Windows 上拖拽区会**吞掉鼠标事件**，
   * 页面收不到 click——球就点不开了（这正是实测到的故障）。所以拖拽改成自己实现：
   * 主进程记下按下时的窗口位置与指针位置，移动时按差值 setPosition。
   */
  dragStart: (x, y) => ipcRenderer.invoke('panel:dragStart', { x, y }),
  dragMove: (x, y) => ipcRenderer.invoke('panel:dragMove', { x, y }),
  dragEnd: () => ipcRenderer.invoke('panel:dragEnd'),

  /** 主进程侧的实际状态，供界面显示"关窗后助手还在跑"这类事实 */
  info: () => ipcRenderer.invoke('panel:info'),
  /**
   * 主进程把窗口切成了球形态/气泡形态时通知页面。**通道名是常量，不由调用方给**。
   *
   * 载荷在这里**收窄**过再交出去：`mode` 只认 'ball'/'chat'，`side` 只认 'left'/'right'，
   * `anchorY` 只认 'top'/'bottom'，`bubbleHeight` 夹进 1..10000，`fadeMs` 夹进 0..2000，
   * `done` 只认布尔。跟通道名同一个道理——不把主进程给的东西原样透传。
   */
  onMode: (cb) => {
    if (typeof cb !== 'function') return
    const clampInt = (value, max, min = 0) => {
      const n = Math.round(Number(value))
      return Number.isFinite(n) ? Math.min(Math.max(n, min), max) : min
    }
    ipcRenderer.on('panel:mode', (_event, payload) => cb({
      mode: payload && payload.mode === 'ball' ? 'ball' : 'chat',
      side: payload && payload.side === 'right' ? 'right' : 'left',
      anchorY: payload && payload.anchorY === 'top' ? 'top' : 'bottom',
      bubbleHeight: clampInt(payload && payload.bubbleHeight, 10000, 1),
      fadeMs: clampInt(payload && payload.fadeMs, 2000),
      done: !!(payload && payload.done),
    }))
  },
})
