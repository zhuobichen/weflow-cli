/**
 * 右键菜单的**内容**：纯数据 + 回调，不 require electron。
 *
 * 为什么单独一个文件：菜单里的点击在 CI 里点不了，但**"有哪几项、点了各自该干什么"是我们的代码**，
 * 而 `Menu.buildFromTemplate` + `popup` 那一步才是 Electron 的。拆开之后这个函数能被测试直接
 * require 进来、把每个 `click` 真的调一遍——比拿正则去匹配源码文本结实得多（同 `tray-menu.cjs`）。
 *
 * **为什么球形态下用原生菜单而不是页内那个**：原生菜单画在**窗口外面**，所以球那 76x76 的
 * 窗口不用先展开成对话窗——用户的原话是"右键应该算是快速功能"，而"顺手把窗口弹出来"恰好
 * 是他要避免的那一下。页内菜单留给浏览器降级那条路（那条路没有 Electron，也就没有原生菜单，
 * 而它的窗口是正常的浏览器窗，画得下）。
 *
 * 菜单有两段：**起草回复**（联系人名单）与**快捷功能**（固定动作）。第二段是后加的，
 * 加它的判据只有一条：**这个动作能不能用一句固定的话做完**。要打字才能用的（搜索、导出、
 * 指定某条消息）做不成一键，不许往这里塞——塞进来只会做出一个点了没反应的菜单项。
 *
 * 一条不能松的语义：**代价写在菜单里**，而且**逐项说清**（见 `costNote()`）。点一下 = 一次
 * 出境这件事必须在菜单里看得见，而不是点完才知道；反过来，"这几项其实只读本地"也该看得见，
 * 否则用户会不敢点本地那几项。
 *
 * 最后一项是**「关闭悬浮球」**，它是这个菜单里唯一不碰 AI 的一项。三条取舍：
 * - 它是 `win.hide()`（收起来），**不是退出**。退出面板 / 退出并停止助手属于"退出"那一类，
 *   在托盘菜单里（`tray-menu.cjs` 把它们和"显示 / 展开"分隔开了），这里塞进来就把两类混了。
 * - 所以它**可逆**，而收回来的路（托盘图标）必须写在标签里——球藏起来以后任务栏没有它的位置
 *   （`setSkipTaskbar(true)`），不写清楚就成了"点一下它永远消失了"。
 * - **任何情况下它都在**：名单没配过、快捷功能全不可用时，用户照样得有个办法把球收起来。
 */
'use strict'

/** 菜单最多列几个人。窗口小、名单可能很长，超出部分不该把菜单撑成一整屏 */
const MAX_ITEMS = 20

/** 名单为空时菜单显示的两行：说清没配，并给出该跑的那条命令 */
const EMPTY_HINT = [
  '还没配名单',
  'weflow-cli config set quickReplyContacts "某人,另一人"',
]

/**
 * 收起来那一项的标签。括号里那句**不是凑字数的**：球藏起来之后任务栏里没有它
 * （`setSkipTaskbar(true)`），不写出"托盘图标能再打开"，这一项看上去就像"永久关掉"。
 */
const CLOSE_LABEL = '关闭悬浮球（托盘图标能再打开）'

/** 两段的段头。**不可点**：它们是分组，不是按钮 */
const REPLY_SECTION = '起草回复'
const ACTION_SECTION = '快捷功能'

/**
 * 快捷功能。每一项 = 一句固定的话 + 一句给人看的标签。
 *
 * `prompt` **随菜单项一起回传**（经 `panel:quickMenu` 的返回值到页面），页面不另存一份
 * id→话术的映射：两份映射一定会漂移，而漂移的那种失败是"菜单写着甲、点下去做了乙"，
 * 不报错、只有读代码才发现。
 *
 * `cloud` 只用来生成提示（`costNote()`），不影响行为——但这些话术**要么点了就调模型**，
 * 要么**纯本地**，没有中间态，所以一个布尔值够用。
 *
 * 措辞里点名工具（"用 who_owes_reply 看一遍"）是必要的：助手是在 19 个工具里自己选，
 * 不点名时"谁在等我回话"这种话它可能去调 `search_chats` 凑答案。**这仍是靠模型选对工具**，
 * 不是硬绑定——真出现选错的情况再谈加参数。
 */
const ACTIONS = [
  {
    id: 'who_owes_reply',
    label: '谁在等我回话',
    cloud: true,
    prompt: '用 who_owes_reply 逐会话看一遍：谁在等我回话？只列真的欠着回复的，每条写清是对方和该回什么。',
  },
  {
    id: 'get_todos',
    label: '我的待办',
    cloud: false,
    prompt: '用 get_todos 把我的待办列出来，按紧急程度排。',
  },
  {
    id: 'get_daily_report',
    label: '今日日报',
    cloud: false,
    prompt: '用 get_daily_report 说说最新一期日报里有什么。',
  },
  {
    id: 'get_stats',
    label: '最近统计',
    cloud: false,
    prompt: '用 get_stats 说说我最近的聊天统计。',
  },
  {
    id: 'get_reading_stats',
    label: '阅读统计',
    cloud: false,
    prompt: '用 get_reading_stats 说说我的阅读情况。',
  },
  {
    id: 'list_sessions',
    label: '最近会话',
    cloud: false,
    prompt: '用 list_sessions 列出我最近的会话。',
  },
  {
    id: 'get_sns',
    label: '朋友圈',
    cloud: false,
    prompt: '用 get_sns 说说我朋友圈最近有什么。',
  },
  {
    id: 'get_weread',
    label: '微信读书',
    cloud: false,
    prompt: '用 get_weread 说说我微信读书的最近情况。',
  },
]

/** 把名单规整成菜单项用的数组：只要非空字符串、去重、有上限 */
function normalizeNames(labels) {
  const seen = new Set()
  const names = []
  for (const raw of Array.isArray(labels) ? labels : []) {
    if (typeof raw !== 'string') continue
    const name = raw.trim()
    if (!name || seen.has(name)) continue
    seen.add(name)
    names.push(name)
    if (names.length >= MAX_ITEMS) break
  }
  return names
}

/**
 * 代价那一行。**从 ACTIONS 派生，不写死**——写死的那种失败是"菜单加了功能、提示还停在旧话上"，
 * 而不准确的代价提示比没有更糟：它让人以为已经被告知了。
 *
 * 起草回复（联系人名单）也要算进去：它是这个菜单里唯一**会把聊天正文发出去**的一项。
 */
function costNote(hasContacts) {
  const cloud = []
  if (hasContacts) cloud.push(REPLY_SECTION)
  for (const action of ACTIONS) {
    if (action.cloud) cloud.push(action.label)
  }
  if (!cloud.length) return '这些项都不调云端模型，只读本地'
  return `会调云端模型的只有：${cloud.join('、')}；其余只读本地，不花钱`
}

/**
 * @param {unknown} labels 名单（联系人名）
 * @param {{
 *   pick: (item: {kind: 'contact', name: string} | {kind: 'action', label: string, prompt: string}) => void,
 *   close: () => void,
 * }} actions 选中了哪一项 / 选了"收起来"。
 *   关掉菜单这件事由调用方的 `popup({ callback })` 负责，这里不管。
 *
 * 两项都必填：少给一个就当场抛，而不是弹出一个点了没反应的菜单。
 */
function quickMenuTemplate(labels, actions) {
  if (typeof actions?.pick !== 'function') {
    throw new Error('quickMenuTemplate 需要一个 pick 回调')
  }
  if (typeof actions?.close !== 'function') {
    throw new Error('quickMenuTemplate 需要一个 close 回调')
  }
  const names = normalizeNames(labels)
  const closeItem = { label: CLOSE_LABEL, click: () => actions.close() }

  const items = []
  if (names.length) {
    items.push({ label: REPLY_SECTION, enabled: false })
    for (const name of names) {
      items.push({ label: name, click: () => actions.pick({ kind: 'contact', name }) })
    }
  } else {
    // 没配名单**不等于这个菜单没用**：下面那八个快捷功能照常给出，所以这里只是少了一段，
    // 不是"空框"（第一版没有快捷功能时，这一段之后就直接是收起来，那才叫空框）。
    items.push(...EMPTY_HINT.map(label => ({ label, enabled: false })))
  }

  items.push({ type: 'separator' }, { label: ACTION_SECTION, enabled: false })
  for (const action of ACTIONS) {
    items.push({
      label: action.label,
      click: () => actions.pick({ kind: 'action', label: action.label, prompt: action.prompt }),
    })
  }

  items.push(
    { type: 'separator' },
    { label: costNote(names.length > 0), enabled: false },
    { type: 'separator' },
    closeItem,
  )
  return items
}

module.exports = {
  MAX_ITEMS, EMPTY_HINT, CLOSE_LABEL, REPLY_SECTION, ACTION_SECTION,
  ACTIONS, costNote, normalizeNames, quickMenuTemplate,
}
