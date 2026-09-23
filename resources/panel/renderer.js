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

/** 把一条消息加进对话区。`who` 决定样式，**内容永远走 textContent** */
function addTurn(who, text) {
  const div = document.createElement('div')
  div.className = 'turn ' + who
  div.textContent = text
  log.appendChild(div)
  log.scrollTop = log.scrollHeight
  return div
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
    const mode = s.channelActive ? '微信 + 本机' : '仅本机入口'
    statusEl.textContent = mode + '｜今日 ' + s.quota.used + '/' + s.quota.limit

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
  }
}

async function ask(text) {
  addTurn('me', text)
  const pending = addTurn('it pending', '…')
  input.disabled = true
  send.disabled = true

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

if (hasShell) {
  document.body.classList.add('mode-ball')
  ball.hidden = false
  collapse.hidden = false

  ball.addEventListener('click', () => {
    document.body.classList.replace('mode-ball', 'mode-chat')
    void window.weflowPanel.setMode('chat')
    input.focus()
  })
  collapse.addEventListener('click', () => {
    document.body.classList.replace('mode-chat', 'mode-ball')
    void window.weflowPanel.setMode('ball')
  })

  // 托盘里点"展开为对话窗"时窗口已经被主进程放大了，页面得跟上换形态
  window.weflowPanel.onMode((mode) => {
    document.body.classList.remove('mode-ball', 'mode-chat')
    document.body.classList.add(mode === 'chat' ? 'mode-chat' : 'mode-ball')
    if (mode === 'chat') input.focus()
  })
} else {
  // 浏览器降级：把话说清，别让用户以为悬浮球坏了
  const note = document.createElement('div')
  note.className = 'warn'
  note.textContent = '这是浏览器窗口（没有 Electron）：给不了悬浮球、托盘和全局快捷键。'
    + '想要真·悬浮球，在电脑上跑 npm i -g electron 之后再执行 weflow-cli panel。'
  foot.appendChild(note)
}

void refreshStatus()
// 每 30 秒刷一次状态：守护进程可能被停掉，界面不该一直显示旧数字
setInterval(refreshStatus, 30000)
// 球形态下不抢焦点（抢了会把用户正在打字的窗口顶掉）
if (!hasShell || !document.body.classList.contains('mode-ball')) input.focus()
