/**
 * 托盘菜单：**真的把每一项的 `click` 调一遍**，看它叫的是哪个回调。
 *
 * 这是"点击测不了"的解法：菜单内容被拆成了 `resources/panel/tray-menu.cjs`（纯数据、不 require
 * electron），于是 CI 能把它 require 进来——`Menu.buildFromTemplate` 那一步才是 Electron 的代码，
 * 而"有哪几项、点了各自该干什么"是我们的。拿正则去匹配源码也能测，但那种测法抓不到
 * "把两个 click 接反了"这种错。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { join } from 'node:path'
import { readFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'

// `import()` 一个带盘符的绝对路径不是合法 ESM specifier（Windows 上会报
// ERR_UNSUPPORTED_ESM_URL_SCHEME）——要过 `pathToFileURL`，和 main.cjs 里同一个理由。
const mod = await import(pathToFileURL(join(process.cwd(), 'resources', 'panel', 'tray-menu.cjs')).href)
const { trayMenuTemplate } = mod.default ?? mod

/** 装一个记录器，返回 { calls, actions } */
function recorder() {
  const calls: string[] = []
  return {
    calls,
    actions: {
      toggleVisible: () => calls.push('toggleVisible'),
      expandToChat: () => calls.push('expandToChat'),
      quitPanel: () => calls.push('quitPanel'),
      quitAndStopAssistant: () => calls.push('quitAndStopAssistant'),
    },
  }
}

test('四项、顺序、分隔线都对，而且每一项都有 click', () => {
  const { actions } = recorder()
  const items = trayMenuTemplate(actions)
  assert.deepEqual(items.map((i: any) => i.label ?? `[${i.type}]`), [
    '显示 / 收起', '展开为对话窗', '[separator]', '退出面板（助手继续运行）', '退出并停止助手',
  ])
  for (const item of items) {
    if (item.type === 'separator') continue
    assert.equal(typeof item.click, 'function', `${item.label} 没有 click`)
  }
})

test('每一项点的都是它自己那件事 —— 挨个调一遍', () => {
  // 这条才是有价值的部分：它抓得到"两个 click 接反了"，而文本断言抓不到。
  const { calls, actions } = recorder()
  const items = trayMenuTemplate(actions)
  for (const item of items) if (item.click) item.click()
  assert.deepEqual(calls, ['toggleVisible', 'expandToChat', 'quitPanel', 'quitAndStopAssistant'])
})

test('"退出面板"与"退出并停止助手"是两个不同的动作，不许合并', () => {
  // 这条语义很容易被"顺手统一"掉：关掉窗口**不该**停掉助手（微信那边可能还在用）。
  const { calls, actions } = recorder()
  const items = trayMenuTemplate(actions)
  const byLabel = new Map(items.filter((i: any) => i.label).map((i: any) => [i.label, i]))
  byLabel.get('退出面板（助手继续运行）').click()
  assert.deepEqual(calls, ['quitPanel'], '只关面板')
  byLabel.get('退出并停止助手').click()
  assert.deepEqual(calls, ['quitPanel', 'quitAndStopAssistant'], '这一项才停助手')
  assert.notEqual(byLabel.get('退出面板（助手继续运行）').click, byLabel.get('退出并停止助手').click)
})

test('少给一个回调就当场抛，不静默做出一个点了没反应的菜单', () => {
  const { actions } = recorder()
  for (const missing of Object.keys(actions)) {
    const partial: any = { ...actions }
    delete partial[missing]
    assert.throws(() => trayMenuTemplate(partial), new RegExp(missing),
      `少了 ${missing} 应当抛，而且要说清缺哪个`)
  }
})

test('两份菜单内容不许分叉：main.cjs 必须用这个模块，而不是自己再写一份', () => {
  const main = readFileSync(join(process.cwd(), 'resources', 'panel', 'main.cjs'), 'utf8')
  assert.match(main, /require\('\.\/tray-menu\.cjs'\)/, '要 require 这个模块')
  assert.match(main, /trayMenuTemplate\(/)
  // 标签只许在这个模块里出现一次；main.cjs 再抄一遍就是"同一件事两处实现"
  for (const label of ['显示 / 收起', '展开为对话窗', '退出面板（助手继续运行）']) {
    assert.equal(main.includes(label), false, `main.cjs 里不该再出现「${label}」`)
  }
})
