/**
 * 面板的渲染层。**这是整条链路上最不可信的一层**——它显示的东西来自助手的回复，
 * 而助手的回复里会有用户自己的聊天内容。所以两条死规矩：
 *
 * 1. **一律 `textContent`，绝不 `innerHTML`**。回复里出现 `<script>` 或 `<img onerror>` 之类的
 *    内容时，用 innerHTML 就等于在那个窗口里执行它。这个窗口没有地址栏，用户看不出被导航走了。
 * 2. **拿不到 token，也拿不到端口**：凭据在 `HttpOnly` cookie 里（脚本读不到），
 *    请求只发相对路径 `/api/...`。所以这个文件里不该出现任何凭据相关的东西。
 *
 * 页面本身由守护进程从回环端点递过来（`GET /panel`），CSP 走响应头：`default-src 'none'` +
 * `script-src 'self'`，所以也没有内联脚本、没有外部资源。
 */
'use strict'

const log = document.getElementById('log')
const input = document.getElementById('input')
const send = document.getElementById('send')
const statusEl = document.getElementById('status')
const foot = document.getElementById('foot')

const COPY_LABEL = '复制'
const COPIED_LABEL = '已复制'
const MANUAL_LABEL = '按 Ctrl+C'

/** 把节点内容全选上。用于剪贴板 API 用不了时的退路 */
function selectContents(node) {
  const range = document.createRange()
  range.selectNodeContents(node)
  const selection = window.getSelection()
  selection.removeAllRanges()
  selection.addRange(range)
}

/**
 * 助手回答下面的复制按钮。
 *
 * 为什么要有：起草回复那条路**只产出文本**——不碰微信窗口、不模拟按键（见 D-047），
 * 所以"把这几条候选拿到别处去用"这一步只能由用户自己做，那就得让他一键拿得走。
 * 它是**整条回答**的复制，不做"逐条候选分别复制"：候选是模型用自己的话重述过的，
 * 在客户端按行去猜哪行是候选，猜错就是把半句话复制走。
 *
 * 反馈必须落在按钮自己身上（窗口只有巴掌大，用户不会去看别处）。
 */
function addCopyButton(turn, textNode, text) {
  const button = document.createElement('button')
  button.type = 'button'
  button.className = 'copy'
  button.textContent = COPY_LABEL
  let timer = null
  const flash = (label) => {
    button.textContent = label
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => { button.textContent = COPY_LABEL }, 1500)
  }
  button.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(text)
      flash(COPIED_LABEL)
    } catch {
      // 剪贴板 API 拿不到时**不许假装复制成功**：用户会以为剪贴板里有东西，粘出来
      // 是上一次的旧内容——那比什么都不做更坏。改成把这段选上，让他自己按 Ctrl+C。
      selectContents(textNode)
      flash(MANUAL_LABEL)
    }
  })
  turn.appendChild(button)
}

/** 把一条消息加进对话区。`who` 决定样式，**内容永远走 textContent** */
function addTurn(who, text) {
  const empty = log.querySelector('.empty')
  if (empty) empty.remove()      // 第一句话一来，开头那段提示就该让位
  const div = document.createElement('div')
  div.className = 'turn ' + who
  const body = document.createElement('div')
  body.className = 'turn-text'
  body.textContent = text
  div.appendChild(body)
  // 只有助手的回答带复制按钮：用户自己发的不用复制，"正在输入"没有内容可复制，
  // 错误提示也不该被粘到别处去。
  if (who === 'it') addCopyButton(div, body, text)
  log.appendChild(div)
  log.scrollTop = log.scrollHeight
  return div
}

/**
 * 还没聊过时的开头。**空白是这里最糟的状态**：第一次打开的人看到一整片黑，
 * 既不知道它能干什么，也看不出它是活的（下面没有正在输入之类的动静）。
 * 例子做成可点的按钮——点一下就是真的问一句，走的还是同一条提交路径。
 */
const EXAMPLES = [
  '我最近都在忙什么？',
  '总结我和某某的聊天',
  '收藏里有哪些 AI 文章？',
  '我最近都在读什么？',
]

function renderEmptyState() {
  if (log.children.length) return
  const box = document.createElement('div')
  box.className = 'empty'

  const title = document.createElement('div')
  title.className = 'empty-title'
  title.textContent = '直接问就行，我会查本机数据回答'
  box.appendChild(title)

  const sub = document.createElement('div')
  sub.className = 'empty-sub'
  sub.textContent = '聊天、收藏、公众号、微信读书、待办都在本机，数据库不出这台机器。'
  box.appendChild(sub)

  for (const example of EXAMPLES) {
    const chip = document.createElement('button')
    chip.type = 'button'
    chip.className = 'chip'
    chip.textContent = example
    chip.addEventListener('click', () => {
      if (input.disabled) return          // 上一句还在飞的时候点了没用，就不该有反应
      input.value = example
      document.getElementById('composer').requestSubmit()
    })
    box.appendChild(chip)
  }
  log.appendChild(box)
}

/**
 * 球的背景跟着状态走（CSS 里 `body.busy/.offline/.quota #ball .glow`）。
 *
 * 集中在一个函数里改：之前这些类名散在 ask()/refreshStatus() 各处的话，
 * 迟早有一条分支忘了摘掉 `busy`，球就一直亮着——那种"看起来在忙其实没在忙"比不显示更糟。
 * 传进来的每一项都**整体替换**，不做增量。
 */
function setBallState(state) {
  document.body.classList.remove('busy', 'offline', 'quota')
  if (state) document.body.classList.add(state)
}

/** 服务端的状态码 → 一句人话。**别把 code 原样丢给用户** */
function explain(code, fallback) {
  switch (code) {
    case 'BUSY': return '上一句还在处理，等它说完再发。'
    case 'NOT_RUNNING': return '助手没在运行。在电脑上跑一次 `weflow-cli assistant start`。'
    case 'QUOTA_EXCEEDED': return '今天的额度用完了。'
    case 'TIMEOUT': return '这一轮太久没结束（它可能还在后台跑）。稍等一下再问。'
    case 'UNAUTHORIZED': return '凭据失效了。重新用 `weflow-cli panel` 打开这个窗口。'
    case 'ORIGIN_DENIED': return '这个来源被拒绝了。'
    default: return fallback || ('没成（' + code + '）')
  }
}

async function refreshStatus() {
  try {
    const res = await fetch('/api/status', { credentials: 'same-origin' })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      statusEl.textContent = explain(body.code, '连接异常')
      return
    }
    const s = await res.json()
    quickReplies = Array.isArray(s.quickReplies) ? s.quickReplies.filter(n => typeof n === 'string') : []
    const mode = s.channelActive ? '微信 + 本机' : '仅本机入口'
    statusEl.textContent = mode + '｜今日 ' + s.quota.used + '/' + s.quota.limit
    // 额度用尽是"今天不能再用"，值得在球上看得出来（琥珀），但**不是**错误
    if (s.quota.used >= s.quota.limit) setBallState('quota')

    // 记忆桶那句话说清"是不是同一个大脑"——这是用户最容易误解的地方
    foot.textContent = ''
    const line = document.createElement('div')
    line.textContent = s.memoryNote || ('记忆桶：' + s.memoryBucket)
    foot.appendChild(line)
    if (!s.aiConfigured) {
      const warn = document.createElement('div')
      warn.className = 'warn'
      warn.textContent = '还没配置 LLM：在电脑上跑 `weflow-cli config set deepseekApiKey "..."`'
      foot.appendChild(warn)
      input.disabled = true
      send.disabled = true
    }
  } catch {
    statusEl.textContent = '连不上本机入口'
    setBallState('offline')     // 暖色且停住流动：不动的东西才会被注意到
  }
}

/** 让助手给这个人起草一条回复。**只产文本**——发送在这条链路上结构性不可达。 */
function draftRequest(name) {
  return `用 draft_reply 给「${name}」起草 3 条候选回复，把候选原样列出来。只产文本，不要发送。`
}

/**
 * 问一句。`display` 用来把"用户看到的那句话"和"实际发出去的那句"分开：
 * 快速回复要发的是一句点名工具的请求（要模型选对工具），但那句话长得像机器指令，
 * 摆进对话里读起来不像人说的——所以对话里显示「快速回复：咸鱼梦想家」。
 */
async function ask(text, display = null) {
  addTurn('me', display || text)
  const pending = addTurn('it pending', '…')
  input.disabled = true
  send.disabled = true
  // 球收起来的时候，此前完全看不出它在干活——这是这条状态最主要的用处
  setBallState('busy')

  try {
    const res = await fetch('/api/ask', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text }),
    })
    const body = await res.json().catch(() => ({}))
    pending.remove()

    if (res.ok && body.ok) {
      addTurn('it', body.reply)
      // 配额用尽时服务端回的是一句**回答**（不是错误），所以上面照常显示；
      // 这里只是把状态条上的数字刷新一下
      void refreshStatus()
      return
    }
    addTurn('err', explain(body.code, body.error))
  } catch {
    pending.remove()
    addTurn('err', '请求没发出去（助手进程可能刚停）。')
  } finally {
    input.disabled = false
    send.disabled = false
    setBallState(null)
    void refreshStatus()      // 顺手把用量与配额状态刷新（额度用尽会换成琥珀色）
    input.focus()
  }
}

document.getElementById('composer').addEventListener('submit', (event) => {
  event.preventDefault()
  const text = input.value.trim()
  if (!text) return
  input.value = ''
  void ask(text)
})

input.addEventListener('keydown', (event) => {
  // Enter 发送，Shift+Enter 换行
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    document.getElementById('composer').requestSubmit()
  }
})

// ---------------------------------------------------------------- 球 / 对话窗
//
// 只在**有外壳**（Electron）时才起小球：浏览器那条路（Edge `--app`）给不了无边框置顶的小球，
// 硬做只会做出一个奇怪的方窗口。所以这里靠 `window.weflowPanel` 在不在来判断，
// 而且这正是 preload 唯一暴露的东西——页面拿不到凭据，也不该拿得到。

const ball = document.getElementById('ball')
const collapse = document.getElementById('collapse')
const hasShell = typeof window.weflowPanel !== 'undefined'

/**
 * 右键"快速回复"的名单。来自 `/api/status`——页面本来每 30 秒就轮询那个端点，
 * 所以这份名单**不必再开一个入口**（面板那条路上不加新端点、不加 IPC）。
 */
let quickReplies = []

/**
 * 系统关了动画没有。**这一条只有渲染进程能问**（`prefers-reduced-motion` 是媒体查询，
 * 主进程那边没有等价 API），而窗口几何的补间在主进程、CSS 管不着——所以读出来的结果
 * 要随 `setMode` 带过去。
 *
 * `typeof` 那半不是客套：`matchMedia` 在 jsdom 里**根本不存在**（测试就是拿 jsdom 跑这个
 * 文件的），直接调会在加载时就抛。
 */
function prefersReducedMotion() {
  return typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/** 气泡方位相关的类，每次切形态先全摘掉再按主进程说的挂上 */
const LAYOUT_CLASSES = ['bubble-left', 'bubble-right', 'anchor-top']

/**
 * 按主进程说的形态切 class。**这里是唯一改形态的地方**。
 *
 * 载荷带方位：`side` 是气泡在球的哪一边（球就在窗口的另一边），`anchorY` 是球贴窗口的
 * 上边还是下边（气泡跟着它竖着对齐），`bubbleHeight` 是气泡该有多高——**屏幕不够高时
 * 气泡会变矮**，那是为了让球待在窗口的角上不动（见 `ball-position.cjs` 的 `bubbleLayout`）。
 *
 * 页面自己算不出这些：它不知道自己在屏幕上的位置，也不知道工作区多大。
 */
function applyMode(payload) {
  const mode = payload && payload.mode === 'ball' ? 'ball' : 'chat'
  const side = payload && payload.side === 'right' ? 'right' : 'left'
  document.body.classList.remove('mode-ball', 'mode-chat', 'closing', ...LAYOUT_CLASSES)
  document.body.classList.add(mode === 'chat' ? 'mode-chat' : 'mode-ball')
  if (mode === 'chat') {
    document.body.classList.add(side === 'right' ? 'bubble-right' : 'bubble-left')
    if (payload && payload.anchorY === 'top') document.body.classList.add('anchor-top')
    const height = Number(payload && payload.bubbleHeight)
    document.body.style.setProperty('--bubble-height',
      `${Number.isFinite(height) && height > 0 ? Math.round(height) : 560}px`)
    input.focus()
  }
}

/**
 * 请主进程换形态。**页面自己不先换 class**：从前这条路是页面直接 `classList.replace`，
 * 而托盘那条走的是 `panel:mode`，两条路行为不一样（一边球瞬间消失、一边等主进程）。
 * 现在形态只有主进程一个来源，页面照着 `panel:mode` 做，展开与收起于是必然对称。
 */
function requestMode(mode) {
  void window.weflowPanel.setMode(mode, { animate: !prefersReducedMotion() })
}

/**
 * 点球 = 开关：站着就展开、展开了就收起。**球全程不消失**——它是"在说话的那个图标"，
 * 让它先撤下去再冒出个窗口，就是用户嫌的那股突兀劲。
 */
function toggleMode() {
  requestMode(document.body.classList.contains('mode-chat') ? 'ball' : 'chat')
}

if (hasShell) {
  // `shell` 这个类决定球在不在场（见 panel.css）：浏览器降级那条路永远不该看见球
  document.body.classList.add('shell', 'mode-ball')
  ball.hidden = false
  collapse.hidden = false

  // 球：**点一下展开，按住拖动挪位置**。
  //
  // 为什么不用 `-webkit-app-region: drag`：Windows 上拖拽区会**吞掉鼠标事件**，
  // 页面根本收不到 click——现象就是"点这个图标没反应"（实测的故障）。
  // 所以拖拽自己实现：按下时告诉主进程记住窗口与指针位置，移动时按差值挪窗口；
  // 松手时如果**指针几乎没动**，那就是一次点击。
  // 阈值 4 像素：手抖不会把点击变成拖动，而想拖的人自然会移过 4 像素。
  const DRAG_THRESHOLD_PX = 4
  ball.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return
    const startX = event.screenX
    const startY = event.screenY
    let dragging = false

    const onMove = (moveEvent) => {
      if (!dragging && Math.hypot(moveEvent.screenX - startX, moveEvent.screenY - startY) > DRAG_THRESHOLD_PX) {
        dragging = true
      }
      if (dragging) void window.weflowPanel.dragMove(moveEvent.screenX, moveEvent.screenY)
    }
    const onUp = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      void window.weflowPanel.dragEnd()
      if (dragging) return                 // 拖过了就不算点击
      toggleMode()
    }
    void window.weflowPanel.dragStart(startX, startY)
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  })
  // 右键：快速回复。**只在有外壳时接管**——浏览器降级那条路的原生菜单里有"复制"，
  // 那是用户要用的，不该被我们抢掉。
  //
  // 有外壳时用**主进程弹的原生菜单**：它画在窗口外面，所以球形态那 76x76 的窗口
  // **不用先展开**（页内菜单会被窗口裁掉）。上一版就是"先展开再弹"，用户看到的是
  // "点右键把第二大脑窗口弹出来了"——那不是快速功能该有的样子。
  document.addEventListener('contextmenu', (event) => {
    event.preventDefault()
    void (async () => {
      const picked = await window.weflowPanel.openQuickMenu(quickReplies)
      if (!picked || typeof picked !== 'object') return
      // 选中任何一项都要展开：球那个 76x76 里看不到任何回答，而这几项全都要出文字。
      // 但**右键本身不动窗口**（那是用户嫌的那一下）——展开只发生在真的选了东西之后。
      if (document.body.classList.contains('mode-ball')) requestMode('chat')
      if (picked.kind === 'contact' && typeof picked.name === 'string' && picked.name.trim()) {
        const name = picked.name.trim()
        void ask(draftRequest(name), `快速回复：${name}`)
        return
      }
      if (picked.kind === 'action' && typeof picked.prompt === 'string' && picked.prompt.trim()) {
        // 话术**由菜单项带过来**，页面不自己按 id 查表——两份映射会漂移，而漂移的后果是
        // "菜单写着甲、点下去做了乙"，不报错。
        void ask(picked.prompt, String(picked.label || '快捷功能'))
      }
    })()
  })
  document.addEventListener('click', () => closeQuickMenu())
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeQuickMenu()
  })

  collapse.addEventListener('click', () => { requestMode('ball') })

  // 形态由主进程说了算（球、收起、托盘菜单、快捷键都是这条路），收起分两步：
  //   `fadeMs > 0` → 先把气泡淡掉（窗口这会儿还是大的，淡出真看得见），**不换形态**；
  //   `done` → 主进程已经把窗口缩回球那么大，这时才把气泡摘掉。
  // 顺序不能反：提前换，76x76 的窗口里会露出一条气泡的边；不换，气泡会一直在。
  window.weflowPanel.onMode((payload) => {
    if (payload && payload.mode === 'ball' && !payload.done) {
      if (payload.fadeMs > 0 && document.body.classList.contains('mode-chat')) {
        document.body.classList.add('closing')
        return
      }
      applyMode(payload)
      return
    }
    applyMode(payload)
  })
} else {
  // 浏览器降级：把话说清，别让用户以为悬浮球坏了
  const note = document.createElement('div')
  note.className = 'warn'
  note.textContent = '这是浏览器窗口（没有 Electron）：给不了悬浮球、托盘和全局快捷键。'
    + '想要真·悬浮球，在电脑上跑 npm i -g electron 之后再执行 weflow-cli panel。'
  foot.appendChild(note)
}

renderEmptyState()
void refreshStatus()
// 每 30 秒刷一次状态：守护进程可能被停掉，界面不该一直显示旧数字
setInterval(refreshStatus, 30000)
// 球形态下不抢焦点（抢了会把用户正在打字的窗口顶掉）
if (!hasShell || !document.body.classList.contains('mode-ball')) input.focus()
