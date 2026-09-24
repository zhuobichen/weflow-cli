/**
 * 托盘菜单的**内容**：纯数据 + 回调，不 require electron。
 *
 * 为什么单独一个文件：菜单的"点击"在 CI 里点不了，但**"有哪几项、点了各自该干什么"是我们的代码**，
 * 而 `Menu.buildFromTemplate` 那一步才是 Electron 的。拆开之后 `trayMenuTemplate` 能被测试直接
 * require 进来、把每个 `click` 真的调一遍——比拿正则去匹配源码文本结实得多。
 *
 * 一条不能松的语义（写在标签里，也写在测试里）：
 * **"退出面板"与"退出并停止助手"是两件事**。前者只关面板，助手继续跑（微信那边可能还要用）；
 * 后者才停它，而且走的是 `assistant stop`（pid 文件的清理、端点文件的删除都在那儿，
 * 自己 kill 会漏掉收尾）。
 */
'use strict'

/**
 * @param {{
 *   toggleVisible: () => void,
 *   expandToChat: () => void,
 *   quitPanel: () => void,
 *   quitAndStopAssistant: () => void,
 * }} actions
 */
function trayMenuTemplate(actions) {
  const need = ['toggleVisible', 'expandToChat', 'quitPanel', 'quitAndStopAssistant']
  for (const name of need) {
    if (typeof actions?.[name] !== 'function') {
      throw new Error(`trayMenuTemplate 需要一个 ${name} 回调`)
    }
  }
  return [
    { label: '显示 / 收起', click: () => actions.toggleVisible() },
    { label: '展开为对话窗', click: () => actions.expandToChat() },
    { type: 'separator' },
    // 这两个必须分开放在分隔线之后：它们是"退出"这一类，而不是"显示/展开"
    { label: '退出面板（助手继续运行）', click: () => actions.quitPanel() },
    { label: '退出并停止助手', click: () => actions.quitAndStopAssistant() },
  ]
}

module.exports = { trayMenuTemplate }
