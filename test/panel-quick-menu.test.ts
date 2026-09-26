/**
 * 右键菜单的**内容**（`resources/panel/quick-menu.cjs`，纯模块）。
 *
 * 为什么拆出来测：菜单在真机上要"右键点出"才能看见，而 CI 里点不了。但**"有哪几项、点了
 * 各自该干什么"是我们的代码**——同 `tray-menu.cjs` 那套：把菜单内容做成纯数据 + 回调，
 * 这里把每个 `click` 真的调一遍。"两个 click 接反了"这种错，文本断言抓不到，这个抓得到。
 *
 * 菜单有两段（起草回复 / 快捷功能），所以这里的断言都走**"点一遍、看 pick 收到什么"**，
 * 而不是按位置取下标——按下标写的断言在段与段之间插入一项之后会指到别的东西上。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { join } from 'node:path'
import { readFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'

const mod = await import(pathToFileURL(join(process.cwd(), 'resources', 'panel', 'quick-menu.cjs')).href)
const {
  MAX_ITEMS, CLOSE_LABEL, REPLY_SECTION, ACTION_SECTION,
  ACTIONS, costNote, normalizeNames, quickMenuTemplate,
} = mod.default ?? mod

type Pick = { kind: string; name?: string; label?: string; prompt?: string }

/** 把菜单里所有能点的项按下去，记录 `pick` 收到了什么 */
function pressAll(items: any[]) {
  const picked: Pick[] = []
  const calls: string[] = []
  const actions = {
    pick: (item: Pick) => { picked.push(item); calls.push(`pick:${item.kind}`) },
    close: () => { calls.push('close') },
  }
  for (const item of items) if (typeof item.click === 'function') item.click()
  return { items, picked, calls, actions }
}

const build = (labels: unknown) => {
  const bag = { picked: [] as Pick[], calls: [] as string[], actions: null as any }
  bag.actions = {
    pick: (item: Pick) => { bag.picked.push(item); bag.calls.push(`pick:${item.kind}`) },
    close: () => { bag.calls.push('close') },
  }
  const items = quickMenuTemplate(labels, bag.actions)
  for (const item of items) if (typeof item.click === 'function') item.click()
  return { items, ...bag }
}

test('名单那几项点了各自回自己那个人（kind=contact）', () => {
  const { picked, calls } = build(['咸鱼梦想家', '老王'])
  const contacts = picked.filter(p => p.kind === 'contact')
  assert.deepEqual(contacts.map(p => p.name), ['咸鱼梦想家', '老王'], '点的顺序就是回的名字')
  assert.deepEqual(calls.filter(c => c === 'pick:contact'), ['pick:contact', 'pick:contact'])
})

test('每个快捷功能点了带着**自己的**标签与话术回来', () => {
  // 话术由菜单项带回来、页面不自己查表——所以"带没带"这件事必须在菜单这一侧钉住。
  const { picked } = build(['甲'])
  const actions = picked.filter(p => p.kind === 'action')
  assert.equal(actions.length, ACTIONS.length)
  assert.deepEqual(actions.map(p => p.label), ACTIONS.map((a: any) => a.label))
  for (const item of actions) {
    assert.ok(item.prompt && item.prompt.trim(), `${item.label} 没带话术`)
  }
  // 两条不同的话术是同一串 = 有一项复制粘贴改了标签没改话术
  const prompts = actions.map(p => p.prompt)
  assert.equal(new Set(prompts).size, prompts.length, '话术必须各不相同')
})

test('每句快捷话术都点名了它要用的工具', () => {
  // 助手是在 19 个工具里自己选。不点名时"谁在等我回话"这种话它可能去调别的工具凑答案。
  for (const action of ACTIONS) {
    assert.match(action.prompt, new RegExp(`\\b${action.id}\\b`),
      `${action.label} 的话术里没有点名 ${action.id}`)
  }
})

test('代价那行**从 ACTIONS 派生**，不写死', () => {
  // 写死的失败是"菜单加了功能、提示还停在旧话上"，而不准确的代价提示比没有更糟：
  // 它让人以为已经被告知了。
  const cloud = ACTIONS.filter((a: any) => a.cloud).map((a: any) => a.label)
  const note = costNote(true)
  for (const label of cloud) assert.match(note, new RegExp(label), `${label} 会调模型，提示里要写上`)
  assert.match(note, /只读本地/, '也要说清其余几项不花钱——否则用户不敢点它们')
  assert.match(costNote(false), /只读本地/)

  const items = quickMenuTemplate(['甲'], build([]).actions)
  const line = items.find((i: any) => i.label === costNote(true))
  assert.ok(line, '菜单里要有这一行')
  assert.equal(line.enabled, false, '说明行不该能点')
  assert.equal(line.click, undefined, '也不该带回调')
})

test('两段各有段头，且段头点不动', () => {
  const items = quickMenuTemplate(['甲'], build([]).actions)
  for (const label of [REPLY_SECTION, ACTION_SECTION]) {
    const header = items.find((i: any) => i.label === label)
    assert.ok(header, `缺段头：${label}`)
    assert.equal(header.enabled, false)
    assert.equal(header.click, undefined)
  }
})

test('最后一项是「关闭悬浮球」，点了调 close 而不是 pick', () => {
  const { items, picked, calls } = build(['甲', '乙'])
  const last = items[items.length - 1]
  assert.equal(last.label, CLOSE_LABEL)
  assert.ok(calls.includes('close'))
  assert.deepEqual(calls.filter(c => c === 'pick:contact').length, 2, 'close 不该被当成交付一次起草')
  assert.equal(picked.some(p => p.kind === 'contact' && p.name === CLOSE_LABEL), false)
})

test('「关闭悬浮球」的标签要写明它收得回来（从托盘）', () => {
  // 球藏起来之后任务栏没有它的位置（setSkipTaskbar）。不写出"从哪叫回来"，
  // 这一项对用户就是"点一下它永远消失了"。
  assert.match(CLOSE_LABEL, /托盘/, '要写出收回来靠托盘')
  assert.doesNotMatch(CLOSE_LABEL, /退出/, '"收起来"与"退出"是两件事，别用同一个词')
})

test('名单为空**不等于**这个菜单没用：快捷功能与关闭照常给', () => {
  // 第一版没有快捷功能时，空名单那一段之后直接就是"关闭悬浮球"——那才叫一个空框。
  const { items, picked, calls } = build([])
  assert.equal(items[0].label.replace(/\s/g, '').includes('还没配名单'), true)
  assert.match(items[1].label, /quickReplyContacts/, '要把配置键的名字写出来')
  for (const item of items.slice(0, 2)) {
    assert.equal(item.enabled, false)
    assert.equal(item.click, undefined)
  }
  assert.deepEqual(picked.filter(p => p.kind === 'contact'), [], '空名单不该回任何人')
  assert.equal(picked.filter(p => p.kind === 'action').length, ACTIONS.length, '功能一项都不能少')
  assert.ok(calls.includes('close'))
})

test('脏名单要被规整：非字符串、空白、重复都丢掉', () => {
  assert.deepEqual(normalizeNames(['  甲  ', null, '甲', '', '  ', 42, '乙']), ['甲', '乙'])
  assert.deepEqual(normalizeNames(undefined), [], '不是数组就当空')
  assert.deepEqual(normalizeNames('甲'), [], '字符串不是数组')
})

test('名单再长也只列前 20 个（菜单不该被撑成一整屏）', () => {
  const many = Array.from({ length: 30 }, (_, i) => `联系人${i + 1}`)
  const { picked } = build(many)
  const names = picked.filter(p => p.kind === 'contact').map(p => p.name)
  assert.equal(names.length, MAX_ITEMS)
  assert.equal(names[0], '联系人1')
  assert.equal(names[MAX_ITEMS - 1], `联系人${MAX_ITEMS}`)
})

test('缺 pick 或 close 回调就当场报错，而不是弹一个点了没反应的菜单', () => {
  assert.throws(() => quickMenuTemplate(['甲'], {} as any), /pick/)
  assert.throws(() => quickMenuTemplate(['甲'], { pick: () => {} } as any), /close/)
})

test('菜单只有这一份：main.cjs 必须用这个模块，而不是自己再写一份', () => {
  const main = readFileSync(join(process.cwd(), 'resources', 'panel', 'main.cjs'), 'utf8')
  assert.match(main, /require\('\.\/quick-menu\.cjs'\)/, '要 require 这个模块')
  assert.match(main, /quickMenuTemplate\(/)
  // 两处实现早晚分叉。这几个标签只许在这个模块里出现一次。
  // **只查带引号的形式**：注释里提到它（说明这一项干什么）不算第二份实现——同 tray-menu 那条。
  for (const label of [CLOSE_LABEL, REPLY_SECTION, ACTION_SECTION]) {
    for (const quote of ["'", '"', '`']) {
      assert.equal(main.includes(`${quote}${label}${quote}`), false,
        `main.cjs 里不该再出现 ${quote}${label}${quote}`)
    }
  }
})

test('话术只存一份：页面里不许再有一张 id→话术的表', () => {
  // 设计上话术随菜单项回传，页面拿 `picked.prompt` 直接用。页面要是自己按 id 查表，
  // 就有了两份映射，而漂移的后果是"菜单写着甲、点下去做了乙"——不报错。
  const renderer = readFileSync(join(process.cwd(), 'resources', 'panel', 'renderer.js'), 'utf8')
  assert.match(renderer, /picked\.prompt/, '页面要用菜单带回来的话术')
  for (const action of ACTIONS) {
    assert.equal(renderer.includes(`'${action.id}'`), false,
      `renderer.js 里不该出现 ${action.id} 这个 id——那说明页面自己也存了一份`)
  }
})

test('main.cjs 把「关闭悬浮球」接成了收起窗口，而且没有顺手退出', () => {
  // 这一项是纯主进程动作（不经过页面），所以只能在这里验接线：它调的是 hide，不是 quit。
  const main = readFileSync(join(process.cwd(), 'resources', 'panel', 'main.cjs'), 'utf8')
  const from = main.indexOf("ipcMain.handle('panel:quickMenu'")
  // 只取这一个 handler：后面 `panel:quit` 那个**本来就该**调 app.quit()，切到文件尾会误判
  const to = main.indexOf('ipcMain.handle(', from + 10)
  const handler = main.slice(from, to === -1 ? undefined : to)
  assert.match(handler, /close:\s*\(\)/, 'menu 模板要收到 close 这个动作')
  assert.match(handler, /win\.hide\(\)/, '收起 = win.hide()')
  assert.doesNotMatch(handler, /app\.quit\(\)/, '这一项不是"退出面板"——那是托盘菜单里的事')
})
