/**
 * 助手工具的**分支**执行。
 *
 * 覆盖到哪：19 个工具里 18 个被执行过——17 个在本文件，`save_memory` 在
 * `assistant-service.test.ts`（它只碰记忆，和整条消息链路一起测更贴近实际）。
 *
 * 此前只有 3 个纯函数被测过，**没有任何测试真正执行过一个工具分支**——也就是说"用户问
 * 「我和某某聊了什么」，助手会读到什么、会不会把内容原样发出去"从来没被验证过。这些分支
 * 的失败方式都是静默的：格式串了、隐私遮罩没生效、坏链接被真的抓了、抛错把整轮对话打断。
 *
 * 打桩打在**导出的单例**上（`chatService` / `wereadService`），它们和 `assistantTools`
 * 拿的是同一个模块对象，所以不需要模块级 mock。`fetch` 也换掉，于是 `read_favorite` 这条
 * 唯一会出网的工具能整条跑完而不联网。脚本类工具走 `pythonBridge.setScriptRunner`。
 *
 * 只有 `get_daily_report` 一个工具分支没被执行过：它读的目录是模块级常量
 * （`join(PKG_ROOT, 'output', 'biz-daily')`，路径由 `resolvePackageRoot` 定死），没有注入点。
 * 于是它走"还没数据"还是"有日报"取决于跑在哪台机器上（本机 `output/` 有数据，CI 干净检出没有），
 * 断言只能写成"两者皆可"的形状检查——那种测试看着像覆盖、其实什么也没钉住，所以没写。
 * 别把它和 `search_knowledge` 一起算：后者**有**两条（缺关键词、找不到）。
 *
 * 曾经这里写着「`get_todos` 没有便宜的桩点」，那是错的：`setScriptRunner` 一直是公开的
 * （这个文件里早就在用，见上面的 `stubScript`）。更糟的是——PROJECT_STATE 和 67ac103 的
 * 提交信息当时都写了"顺手把 `get_todos` 那条没覆盖的分支补上了"，**而那次只写了话、没写测试**。
 * 一句想当然的话，能让一个能读用户真实待办的工具免于被执行好几轮：注释与文档也是断言，
 * 同样要被复核。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, sep } from 'node:path'

const HOME = mkdtempSync(join(tmpdir(), 'weflow-assistant-tools-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME

const { chatService } = await import('../src/services/chatService.js')
const { wereadService } = await import('../src/services/wereadService.js')
const { AssistantMemory } = await import('../src/services/assistantMemory.js')
const { executeTool } = await import('../src/services/assistantTools.js')
const { exportService } = await import('../src/services/exportService.js')
const { configService } = await import('../src/services/configService.js')
// 顶层具名导入，而不是文件中部那个 `bridge` 命名空间：`beforeEach` 会在模块顶层 await
// 完成**之前**就跑到（node:test 不等模块求值完），引用中部的 `const bridge` 会撞 TDZ。
const { setScriptRunner } = await import('../src/services/pythonBridge.js')

const svc = chatService as any
const weread = wereadService as any

const realFetch = globalThis.fetch
let fetchCalls: string[] = []

function ctx(userId = 'u-tools') {
  return { userId, memory: new AssistantMemory() }
}

/** 每个用例前把打桩恢复到"什么都查不到"的干净状态 */
function resetStubs(): void {
  fetchCalls = []
  // 脚本类工具（get_todos / search_chats / who_owes_reply / search_semantic …）走 pythonBridge。
  // 不恢复的话，某条用例装的假 runner 会漏给后面的用例——它们会静默地"跑通"在一个假世界。
  setScriptRunner(null)
  svc.connect = async () => {}
  svc.listSessions = async () => []
  svc.getMessages = async () => []
  svc.listContacts = async () => []
  svc.getFavorites = async () => ({ success: true, favorites: [], total: 0 })
  svc.getSnsTimeline = async () => ({ success: true, timeline: [] })
  svc.getSnsExportStats = async () => ({ success: true, data: { totalPosts: 0, totalFriends: 0 } })
  weread.shelf = async () => ({ ok: true, data: { books: [] } })
  weread.notebooks = async () => ({ ok: true, data: { books: [] } })
  weread.search = async () => ({ ok: true, data: { books: [] } })
  globalThis.fetch = (async (url: string) => {
    fetchCalls.push(String(url))
    throw new Error('测试里不该真的发请求')
  }) as any
}

function run(name: string, args: Record<string, any> = {}, c = ctx()) {
  return executeTool(name, args, c)
}

test.beforeEach(resetStubs)
test.after(() => { globalThis.fetch = realFetch })

// ---------------------------------------------------------------- list_sessions

test('list_sessions 列出会话，摘要压平成一行并截断', async () => {
  svc.listSessions = async () => ([
    { displayName: '甲', username: 'wxid_a', summary: '第一行\n第二行' },
    { displayName: null, username: 'wxid_b', summary: '没有显示名' },
  ])
  const out = await run('list_sessions', {})
  assert.match(out, /· 甲: 第一行 第二行/)
  assert.match(out, /· wxid_b: 没有显示名/, '没有显示名就退回 username')
  assert.doesNotMatch(out, /\n第二行/)
})

test('list_sessions 查不到会话时如实说，而不是回一个空串', async () => {
  assert.equal(await run('list_sessions', {}), '(未查到会话, 数据库可能未连接)')
})

test('越界的 limit 变成一句可读的参数错误，而不是悄悄用默认值', async () => {
  assert.equal(await run('list_sessions', { limit: 999 }), '(参数错误: limit 必须是 1-30 的整数)')
  assert.equal(await run('list_sessions', { limit: 'abc' }), '(参数错误: limit 必须是 1-30 的整数)')
})

// ---------------------------------------------------------------- get_messages

test('get_messages 缺联系人参数时不读库', async () => {
  assert.equal(await run('get_messages', {}), '(缺少 contact 参数)')
})

test('get_messages 找到人后按时间与方向排版', async () => {
  svc.listSessions = async () => ([{ displayName: '甲', username: 'wxid_a' }])
  svc.getMessages = async () => ([
    { createTime: 1758000000, isSend: true, content: '我发的' },
    { createTime: 1758000060, isSend: false, senderUsername: '甲', content: '他回的' },
  ])
  const out = await run('get_messages', { contact: '甲' })
  assert.match(out, /用户: /)
  assert.match(out, /甲: /)
})

test('严格模式下第三方聊天正文不出境：正文被替换成字节数说明', async () => {
  svc.listSessions = async () => ([{ displayName: '甲', username: 'wxid_a' }])
  svc.getMessages = async () => ([{ createTime: 1758000000, isSend: false, senderUsername: '甲', content: '这是正文' }])
  const out = await run('get_messages', { contact: '甲' })

  assert.doesNotMatch(out, /这是正文/, '严格模式下正文不许出现在工具结果里')
  assert.match(out, /\[内容4字已按严格模式屏蔽\]/)
})

test('图片消息带可点开的句柄，strict 模式下不带（不摆出工具必定拒绝的东西）', async () => {
  const realGet = configService.get.bind(configService)
  const image = { createTime: 1758000000, isSend: false, senderUsername: '甲',
                  localType: 3, localId: 1234, content: '', parsedContent: '[图片]' }
  svc.listSessions = async () => ([{ displayName: '甲', username: 'wxid_a' }])
  svc.getMessages = async () => ([image])

  try {
    ;(configService as any).get = (k: string) => (k === 'assistantPrivacy' ? 'balanced' : realGet(k))
    assert.match(await run('get_messages', { contact: '甲' }), /\[图片 #1234\]/,
      'balanced 下要给句柄，否则 look_at_image 没有入参可用')

    ;(configService as any).get = (k: string) => (k === 'assistantPrivacy' ? 'strict' : realGet(k))
    const strictOut = await run('get_messages', { contact: '甲' })
    assert.doesNotMatch(strictOut, /#1234/, 'strict 下图片不许出境，就别给句柄')
    assert.match(strictOut, /\[图片\]/, '类型要留着——契约是"仅保留时间/方向/类型"')
  } finally {
    ;(configService as any).get = realGet
  }
})

test('用户自己打的「[图片] 开头」的正文，strict 下按正文遮，不能当标签放行', async () => {
  // 标签保留只适用于读取器认定的非文本消息。用户敲的 `[图片] 这是我拍的` 是正文，
  // 把它当标签放行，就是拿用户自己的话开了个口子。
  const realGet = configService.get.bind(configService)
  ;(configService as any).get = (k: string) => (k === 'assistantPrivacy' ? 'strict' : realGet(k))
  try {
    svc.listSessions = async () => ([{ displayName: '甲', username: 'wxid_a' }])
    svc.getMessages = async () => ([{ createTime: 1758000000, isSend: false, senderUsername: '甲',
                                      localType: 1, content: '[图片] 这是我拍的' }])
    const out = await run('get_messages', { contact: '甲' })
    assert.doesNotMatch(out, /这是我拍的/)
    assert.match(out, /\[内容10字已按严格模式屏蔽\]/)
  } finally {
    ;(configService as any).get = realGet
  }
})

test('get_messages 放得下引用消息的正文+被引原文，且截断留省略号', async () => {
  // 这个文件默认跑在 strict 下（正文会被遮罩成字数），而这条测的是渲染本身
  const realGet = configService.get.bind(configService)
  ;(configService as any).get = (k: string) => (k === 'assistantPrivacy' ? 'balanced' : realGet(k))
  try {
    // 两条线：① 160 字（此前 80，引用消息在 80 字里引文只剩七八个字，等于白带）；
    // ② 截断必须留省略号——切了却看起来像说完了，模型会把半句当整句。
    svc.listSessions = async () => ([{ displayName: '甲', username: 'wxid_a' }])
    svc.getMessages = async () => ([
      { createTime: 1758000000, isSend: false, senderUsername: '甲',
        content: '[引用] 好的 ｜ 引：' + '引'.repeat(200) },
      { createTime: 1758000060, isSend: false, senderUsername: '甲', content: '短消息' },
    ])
    const out = await run('get_messages', { contact: '甲' })

    assert.match(out, /短消息/, '短消息不该被长消息挤掉')
    const longLine = out.split('\n').find(l => l.includes('引：'))!
    const body = longLine.slice(longLine.indexOf(': ') + 2)
    assert.match(body, /…$/, '被截断的那条要以省略号结尾')
    assert.equal(body.length, 160 + 1, '正文截到 160 字，再加一个省略号')
    assert.match(body, /引：引{40}/, '引文不只是七八个字')
  } finally {
    ;(configService as any).get = realGet
  }
})

test('联系人名匹配多个会话时要求更精确，而不是随便挑一个', async () => {
  // 两个"包含"但都不等于查询的名字。若其中一个恰好叫「小明」，解析器会直接定它——
  // 那是刻意的：完全同名优先于模糊匹配（下一条钉住这个优先级）。
  svc.listSessions = async () => ([
    { displayName: '小明明', username: 'wxid_1' },
    { displayName: '小明华', username: 'wxid_2' },
  ])
  assert.match(await run('get_messages', { contact: '小明' }), /参数错误: .*多个会话/)
})

test('完全同名的会话优先于模糊匹配', async () => {
  svc.listSessions = async () => ([
    { displayName: '小明', username: 'wxid_exact' },
    { displayName: '小明明', username: 'wxid_partial' },
  ])
  let asked = ''
  svc.getMessages = async (talker: string) => { asked = talker; return [] }
  await run('get_messages', { contact: '小明' })

  assert.equal(asked, 'wxid_exact')
})

test('找不到联系人时如实说找不到', async () => {
  svc.listSessions = async () => ([{ displayName: '甲', username: 'wxid_a' }])
  assert.match(await run('get_messages', { contact: '查无此人' }), /没找到「查无此人」的消息/)
})

// ------------------------------------------------------------ search_favorites

test('search_favorites 列出条数与来源', async () => {
  svc.getFavorites = async () => ({
    success: true, total: 12,
    favorites: [{ title: '一篇文章', source_name: '某号', desc: '描述\n带换行' }],
  })
  const out = await run('search_favorites', { keyword: '文章' })
  assert.match(out, /共12条, 前1条/)
  assert.match(out, /· 一篇文章 \(某号\) — 描述 带换行/)
})

test('search_favorites 搜不到与查询失败是两句不同的话', async () => {
  assert.match(await run('search_favorites', { keyword: '没有' }), /收藏中未搜到「没有」/)
  svc.getFavorites = async () => ({ success: false, error: '库锁了' })
  assert.match(await run('search_favorites', { keyword: 'x' }), /查询失败: 库锁了/)
})

// -------------------------------------------------------------- read_favorite

test('read_favorite 拒绝抓取不安全链接，而且**一个请求都不发**', async () => {
  svc.getFavorites = async () => ({
    success: true, total: 1,
    favorites: [{ title: '收藏', link: 'http://127.0.0.1:8080/admin' }],
  })
  const out = await run('read_favorite', { keyword: '收藏' })

  assert.match(out, /链接不安全, 拒绝抓取/)
  assert.deepEqual(fetchCalls, [], '被判定不安全的链接不许进入 fetch')
})

test('read_favorite 抓不到正文时重试一次，两次都失败才报错', async () => {
  svc.getFavorites = async () => ({
    success: true, total: 1, favorites: [{ title: '收藏', link: 'https://mp.weixin.qq.com/s/x' }],
  })
  globalThis.fetch = (async (url: string) => {
    fetchCalls.push(String(url))
    return { ok: true, status: 200, text: async () => '<html><body>没有正文的页面</body></html>' }
  }) as any

  const out = await run('read_favorite', { keyword: '收藏' })
  assert.match(out, /正文提取失败/)
  assert.equal(fetchCalls.length, 2, '微信侧常见的是第一次返回验证页，所以有一个重试')
})

test('read_favorite 正常页面：取出 js_content 正文并带上来源', async () => {
  svc.getFavorites = async () => ({
    success: true, total: 1,
    favorites: [{ title: '一篇文章', source_name: '某号', link: 'https://mp.weixin.qq.com/s/y' }],
  })
  const body = '正文内容'.repeat(30)
  globalThis.fetch = (async () => ({
    ok: true, status: 200,
    text: async () => `<html><body><div id="js_content"><p>${body}</p></div></body></html>`,
  })) as any

  const out = await run('read_favorite', { keyword: '文章' })
  assert.match(out, /「一篇文章」\(某号\) 正文:/)
  assert.match(out, /正文内容/)
})

test('read_favorite 撞上 WAF 挑战页时从 content_noencode 里解出正文', async () => {
  svc.getFavorites = async () => ({
    success: true, total: 1, favorites: [{ title: '一篇文章', link: 'https://mp.weixin.qq.com/s/z' }],
  })
  const body = '正文内容'.repeat(30)
  globalThis.fetch = (async () => ({
    ok: true, status: 200,
    text: async () => `<html><body><script>var cgiDataNew = { content_noencode: '<p>${body}</p>' }</script></body></html>`,
  })) as any

  assert.match(await run('read_favorite', { keyword: '文章' }), /正文内容/)
})

test('read_favorite 对已被删除的文章给出明确说法', async () => {
  svc.getFavorites = async () => ({
    success: true, total: 1, favorites: [{ title: '旧文', link: 'https://mp.weixin.qq.com/s/d' }],
  })
  const body = '正文内容'.repeat(30)
  globalThis.fetch = (async () => ({
    ok: true, status: 200,
    text: async () => `<html><body>已被发布者删除<div id="js_content">${body}</div></body></html>`,
  })) as any

  assert.match(await run('read_favorite', { keyword: '旧文' }), /已被发布者删除/)
})

test('read_favorite 只有摘要没有链接时直接给摘要，不去抓', async () => {
  svc.getFavorites = async () => ({
    success: true, total: 1, favorites: [{ title: '无链接', desc: '就是一段摘要' }],
  })
  const out = await run('read_favorite', { keyword: '无链接' })

  assert.match(out, /就是一段摘要/)
  assert.deepEqual(fetchCalls, [])
})

// --------------------------------------------------------------------- get_sns

test('get_sns 时间线带上图片数量，统计模式给出计数', async () => {
  svc.getSnsTimeline = async () => ({
    success: true,
    timeline: [{ create_time: 1758000000, nickname: '甲', content: '今天很好', media_count: 3 }],
  })
  const timeline = await run('get_sns', {})
  assert.match(timeline, /· \[.*\] 甲: 今天很好 \[3图\]/)

  svc.getSnsExportStats = async () => ({ success: true, data: { totalPosts: 7, totalFriends: 4, myPosts: 1 } })
  const stats = await run('get_sns', { mode: 'stats' })
  assert.match(stats, /总动态 7 条/)
  assert.match(stats, /用户自己发过 1 条/)
})

test('get_sns 失败与无数据是两句不同的话', async () => {
  svc.getSnsTimeline = async () => ({ success: false, error: '库没连上' })
  assert.match(await run('get_sns', {}), /朋友圈查询失败: 库没连上/)

  svc.getSnsTimeline = async () => ({ success: true, timeline: [] })
  assert.equal(await run('get_sns', {}), '(朋友圈暂无缓存数据)')
})

// ------------------------------------------------------------------ get_weread

test('get_weread 没配 key 时明确告诉用户怎么配', async () => {
  assert.match(await run('get_weread', {}), /微信读书未配置: config set wereadApiKey/)
})

test('get_weread 书架区分在读与全部', async () => {
  const { configService } = await import('../src/services/configService.js')
  const realGet = configService.get.bind(configService)
  ;(configService as any).get = (key: string) => key === 'wereadApiKey' ? 'fake-key' : realGet(key)
  try {
    weread.shelf = async () => ({
      ok: true,
      data: { books: [{ title: '在读的书', author: '某人', progress: 42 }, { title: '没读的', author: '某人' }] },
    })
    const out = await run('get_weread', {})
    assert.match(out, /书架共 2 本, 在读 1 本/)
    assert.match(out, /· 在读的书 \(某人\) — 已读 42%/)
  } finally {
    ;(configService as any).get = realGet
  }
})

test('get_weread search 模式缺关键词时明确要求关键词', async () => {
  const { configService } = await import('../src/services/configService.js')
  const realGet = configService.get.bind(configService)
  ;(configService as any).get = (key: string) => key === 'wereadApiKey' ? 'fake-key' : realGet(key)
  try {
    assert.equal(await run('get_weread', { mode: 'search' }), '(search 模式需要 keyword)')
  } finally {
    ;(configService as any).get = realGet
  }
})

// --------------------------------------------------------- knowledge / stats

test('search_knowledge 缺关键词时不读盘', async () => {
  assert.equal(await run('search_knowledge', {}), '(缺少 keyword 参数)')
})

test('search_knowledge 找不到时给出概念页总数（与环境无关的那部分）', async () => {
  const out = await run('search_knowledge', { keyword: '绝不存在的概念xyzzy' })
  assert.ok(/知识库未收录「绝不存在的概念xyzzy」/.test(out) || /知识库尚未生成/.test(out), out)
})

test('get_stats 汇总会话数与收藏总数', async () => {
  svc.listSessions = async () => ([{ username: 'a' }, { username: 'b' }])
  svc.getFavorites = async () => ({ success: true, total: 9 })
  assert.equal(await run('get_stats', {}), '会话数: 2\n收藏总数: 9')

  svc.getFavorites = async () => ({ success: false })
  assert.match(await run('get_stats', {}), /收藏总数: 未知/)
})

// ------------------------------------------------------------ 失败不许外抛

test('工具内部抛错时返回一句可读失败，绝不把异常抛回主循环', async () => {
  svc.listSessions = async () => { throw new Error('数据库炸了') }
  const out = await run('list_sessions', {})
  assert.equal(out, '(工具执行失败，请检查本地配置或运行状态)')
})

test('工具执行结果永远是字符串（主循环会把它塞进 messages）', async () => {
  svc.listSessions = async () => { throw new Error('boom') }
  assert.equal(typeof await run('list_sessions', {}), 'string')
  assert.equal(typeof await run('search_memory', { keyword: 'x' }), 'string')
})

test('get_messages：非文本消息显示为标签，而不是原始 XML 或空白', async () => {
  // 严格模式会把正文整体遮罩（那是另一条测试的事），这里测的是**取哪个字段**。
  const realGet = configService.get.bind(configService)
  ;(configService as any).get = (k: string) => (k === 'assistantPrivacy' ? 'balanced' : realGet(k))
  try {
  // 实测：助手曾只看到"空内容"——因为读取器把非文本消息丢掉了（修在 nt_decrypt.py）。
  // 这里钉住工具侧的取正文顺序：非文本用 parsedContent，不要让原始 XML 进模型上下文。
  svc.listSessions = async () => ([{ displayName: '甲', username: 'wxid_a' }])
  svc.getMessages = async () => ([
    { createTime: 1758000000, isSend: false, senderUsername: '甲', localType: 47,
      parsedContent: '[表情]', content: '<msg><emoji md5="dd6f13ec" cdnurl="http://x"/></msg>' },
    { createTime: 1758000060, isSend: false, senderUsername: '甲', localType: 10000,
      parsedContent: '"甲" 撤回了一条消息', content: '' },
  ])

  const out = await run('get_messages', { contact: '甲' })

  assert.match(out, /\[表情\]/)
  assert.match(out, /撤回了一条消息/)
  assert.doesNotMatch(out, /emoji/, '原始 XML 不该进上下文')
  assert.doesNotMatch(out, /cdnurl/)
  } finally { ;(configService as any).get = realGet }
})

test('get_messages：文本消息仍然用原文（parsedContent 被截断过）', async () => {
  const realGet2 = configService.get.bind(configService)
  ;(configService as any).get = (k: string) => (k === 'assistantPrivacy' ? 'balanced' : realGet2(k))
  svc.listSessions = async () => ([{ displayName: '甲', username: 'wxid_a' }])
  svc.getMessages = async () => ([
    { createTime: 1758000000, isSend: false, senderUsername: '甲', localType: 1,
      content: '一段完整的文本', parsedContent: '一段完整的文本' },
  ])
  try {
    assert.match(await run('get_messages', { contact: '甲' }), /一段完整的文本/)
  } finally { ;(configService as any).get = realGet2 }
})

// ------------------------------------------------- 脚本类工具（走 pythonBridge）

const bridge = await import('../src/services/pythonBridge.js')

/** 装上假 runner，返回它收到的调用，便于断言参数 */
function stubScript(stdout: string, code = 0, stderr = '') {
  // `stdin` 也要记：有一类工具把**用户数据**走标准输入送进脚本（而不是 argv 或 env），
  // 而"送进去的到底是原文还是遮罩过的"正是要断言的东西。
  const calls: { script: string; args: string[]; env?: Record<string, string>; stdin?: string }[] = []
  bridge.setScriptRunner(async (script: string, args: string[], options: any) => {
    calls.push({ script, args, env: options?.env, stdin: options?.stdin })
    return { stdout, stderr, code }
  })
  return calls
}

test('search_chats：把命中会话与消息渲染出来，并带上查询词', async () => {
  // 这个文件默认跑在 strict 下（正文会被遮罩），而这条测的是渲染本身
  const realGet = configService.get.bind(configService)
  ;(configService as any).get = (k: string) => (k === 'assistantPrivacy' ? 'balanced' : realGet(k))
  stubScript(JSON.stringify({
    success: true, terms: ['部署', '上线'],
    ranked: [{ id: 1, kind: '群聊', label: '群A', messages: 12, lastDaysAgo: 2, hits: { 部署: 3 } },
             { id: 2, kind: '单聊', label: '甲', messages: 5, lastDaysAgo: 9, hits: { 上线: 1 } }],
    messages: { '1': [{ time: 1758000000, text: '部署脚本我改好了' }] },
  }))
  try {
    const out = await run('search_chats', { question: '上次说的部署方案' })
    assert.match(out, /命中 2 个会话/)
    assert.match(out, /部署、上线/)
    assert.match(out, /群A/)
    assert.match(out, /部署脚本我改好了/)
  } finally {
    bridge.setScriptRunner(null)
    ;(configService as any).get = realGet
  }
})

test('search_chats：严格模式下对话正文不许出现在结果里', async () => {
  const realGet = configService.get.bind(configService)
  ;(configService as any).get = (k: string) => (k === 'assistantPrivacy' ? 'strict' : realGet(k))
  stubScript(JSON.stringify({
    success: true, terms: ['部署'],
    ranked: [{ id: 1, kind: '群聊', label: '群A', messages: 3, lastDaysAgo: 1, hits: { 部署: 1 } }],
    messages: { '1': [{ time: 1758000000, text: '这是第三方聊天正文' }] },
  }))
  try {
    const out = await run('search_chats', { question: '部署' })
    assert.doesNotMatch(out, /这是第三方聊天正文/, '严格模式下正文不许出现——与 get_messages 同一条纪律')
    assert.match(out, /已按严格模式屏蔽/)
  } finally {
    bridge.setScriptRunner(null)
    ;(configService as any).get = realGet
  }
})

test('search_chats：一个都没命中时说实话，而不是回空', async () => {
  stubScript(JSON.stringify({ success: true, terms: ['xyz'], ranked: [], messages: {} }))
  try {
    assert.match(await run('search_chats', { question: 'xyz' }), /没有会话字面命中/)
  } finally { bridge.setScriptRunner(null) }
})

test('search_chats：脚本失败时给出分类过的原因', async () => {
  stubScript('', 2, '缺少 TypeSafe key')
  try {
    const out = await run('search_chats', { question: '部署' })
    assert.match(out, /会话检索失败/)
    assert.match(out, /退出码 2/)
  } finally { bridge.setScriptRunner(null) }
})

test('search_chats：脚本参数带上 --yes 与 --json（前者是它自己的出网闸门）', async () => {
  const calls = stubScript(JSON.stringify({ success: true, terms: [], ranked: [], messages: {} }))
  try {
    await run('search_chats', { question: '部署', per_card: 4 })
    assert.deepEqual(calls[0].args.slice(0, 5), ['ask', '部署', '--yes', '--json', '--per-card'])
    assert.equal(calls[0].args[5], '4')
  } finally { bridge.setScriptRunner(null) }
})

test('who_owes_reply：列出谁在等、等了多久、概率多少', async () => {
  stubScript(JSON.stringify({
    success: true, excluded_service: 2,
    debts: [{ name: '甲', days: 3.5, waiting: 0.82, urgencyScore: 2, kind: '单聊' },
            { name: '群B', days: 1.2, waiting: 0.61, urgencyScore: null, kind: '群聊' }],
  }))
  try {
    const out = await run('who_owes_reply', {})
    assert.match(out, /在等你回话的 2 个会话/)
    assert.match(out, /甲（等了 3.5 天 · 概率 0.82 · 紧急度 2 · 单聊）/)
    assert.match(out, /群B（等了 1.2 天 · 概率 0.61 · 群聊）/, '没有紧急度时不该写成 null')
    assert.match(out, /只报谁在等/)
  } finally { bridge.setScriptRunner(null) }
})

test('who_owes_reply：没人欠账时说实话', async () => {
  stubScript(JSON.stringify({ success: true, debts: [] }))
  try {
    assert.match(await run('who_owes_reply', { days: 7 }), /最近 7 天没有明显在等你回话/)
  } finally { bridge.setScriptRunner(null) }
})

// ------------------------------------------------- 语义检索与导出

test('search_semantic：查询词走环境变量，不进 argv', async () => {
  // 仓库写进测试的隐私纪律：用户输入继承环境变量而不是进程参数（ps 里看不到正文）
  const calls = stubScript(JSON.stringify([{ title: '某篇文章', source: 'x.md', score: 0.9, text: '片段' }]))
  try {
    await run('search_semantic', { query: '和钱有关的讨论' })
    assert.equal(calls[0].env?.WEFLOW_SEARCH_QUERY, '和钱有关的讨论', '查询词必须在环境变量里')
    assert.equal(calls[0].args.includes('和钱有关的讨论'), false, '查询词不许出现在 argv')
    assert.deepEqual(calls[0].args, ['search', '--top-k', '8'])
  } finally { bridge.setScriptRunner(null) }
})

test('search_semantic：结果渲染成标题+分数+片段', async () => {
  const realGet = configService.get.bind(configService)
  ;(configService as any).get = (k: string) => (k === 'assistantPrivacy' ? 'balanced' : realGet(k))
  stubScript(JSON.stringify([
    { title: '部署方案', source: 'a.md', score: 0.87, text: '先灰度再全量' },
    { title: '预算讨论', source: 'b.md', score: 0.71, text: '成本核算' },
  ]))
  try {
    const out = await run('search_semantic', { query: '上线' })
    assert.match(out, /前 2 条/)
    assert.match(out, /部署方案（0\.87）/)
    assert.match(out, /先灰度再全量/)
  } finally {
    bridge.setScriptRunner(null)
    ;(configService as any).get = realGet
  }
})

test('search_semantic：严格模式下片段被遮罩', async () => {
  stubScript(JSON.stringify([{ title: 't', score: 0.5, text: '第三方正文内容' }]))
  try {
    const out = await run('search_semantic', { query: 'x' })
    assert.doesNotMatch(out, /第三方正文内容/)
  } finally { bridge.setScriptRunner(null) }
})

test('search_semantic：没结果与失败是两句不同的话', async () => {
  stubScript('[]')
  try {
    assert.match(await run('search_semantic', { query: 'x' }), /没有结果/)
  } finally { bridge.setScriptRunner(null) }

  stubScript('', 3, '缺少 dashscopeApiKey')
  try {
    const out = await run('search_semantic', { query: 'x' })
    assert.match(out, /语义检索失败/)
    assert.match(out, /search-index/, '失败时要提示可能还没建索引')
  } finally { bridge.setScriptRunner(null) }
})

test('export_chat：导出到 output/exports/ 下的新目录，并报出条数', async () => {
  const exportRoot = join(HOME, 'exports-tmp')
  mkdirSync(exportRoot, { recursive: true })
  process.env.WEFLOW_ASSISTANT_EXPORT_ROOT = exportRoot

  const calls: any[] = []
  const realExport = exportService.exportHtml.bind(exportService)
  ;(exportService as any).exportHtml = async (talker: string, outDir: string, limit: number) => {
    calls.push({ talker, outDir, limit })
    return { success: true, path: outDir, count: 42 }
  }
  svc.listSessions = async () => ([{ displayName: '甲', username: 'wxid_a' }])
  try {
    const out = await run('export_chat', { contact: '甲', limit: 100 })
    assert.match(out, /已导出 42 条/)
    // **自定义导出根时只报目录名**：原来这条断言钉的是 `output/exports/甲-…`，而那时写的
    // 是导出根被改过的情况——于是它把"报错位置"这个 bug 一起钉住了（测试照着代码写、
    // 不是照着意图写）。默认根那条在下面另一个用例里。
    assert.match(out, /甲-\d{12}\//, '带时间戳的目录名')
    assert.doesNotMatch(out, /output\/exports\//, '自定义根时不该假称在 output/exports 下')
    assert.doesNotMatch(out, new RegExp(HOME.split(sep).pop() as string), '不报本机路径')
    assert.equal(calls[0].talker, 'wxid_a', '显示名要先解析成会话 id')
    assert.equal(calls[0].limit, 100)
    const normalized = calls[0].outDir.split(String.fromCharCode(92)).join('/')
    assert.match(normalized, /(^|\/)甲-\d{12}(-\d+)?$/, '目录是导出根下的新目录（不依赖根目录名）')
  } finally {
    ;(exportService as any).exportHtml = realExport
  }
})

test('export_chat：目录已存在时往后加序号，绝不覆盖', async () => {
  const exportRoot = join(HOME, 'exports-tmp')
  mkdirSync(exportRoot, { recursive: true })
  process.env.WEFLOW_ASSISTANT_EXPORT_ROOT = exportRoot

  const dirs: string[] = []
  const realExport = exportService.exportHtml.bind(exportService)
  ;(exportService as any).exportHtml = async (_t: string, outDir: string) => {
    dirs.push(outDir)
    // 模拟"这一秒里已经导过一次"：把目录真实建出来，逼下一次换名字
    mkdirSync(outDir, { recursive: true })
    return { success: true, path: outDir, count: 1 }
  }
  svc.listSessions = async () => ([{ displayName: '甲', username: 'wxid_a' }])
  try {
    await run('export_chat', { contact: '甲' })
    await run('export_chat', { contact: '甲' })
    assert.notEqual(dirs[0], dirs[1], '同一秒内两次导出必须落进不同目录')
    assert.match(dirs[1], /-2$/, '撞了就加序号')
  } finally {
    ;(exportService as any).exportHtml = realExport
  }
})

test('export_chat：导出失败时如实说，且不带出奇怪的东西', async () => {
  const realExport = exportService.exportHtml.bind(exportService)
  ;(exportService as any).exportHtml = async () => ({ success: false, error: '缺少 NT 密钥' })
  svc.listSessions = async () => ([{ displayName: '甲', username: 'wxid_a' }])
  try {
    assert.match(await run('export_chat', { contact: '甲' }), /导出失败: 缺少 NT 密钥/)
  } finally {
    ;(exportService as any).exportHtml = realExport
  }
})

test('export_chat：缺联系人时不写任何文件', async () => {
  assert.equal(await run('export_chat', {}), '(缺少 contact 参数)')
})

// ------------------------------------------------- 给已有工具补的参数

test('get_sns users：谁常发朋友圈（本地聚合，不加新出境）', async () => {
  svc.getSnsTimeline = async () => ({
    success: true,
    timeline: [
      { create_time: 1758000000, nickname: '甲', content: 'a' },
      { create_time: 1758000060, nickname: '甲', content: 'b' },
      { create_time: 1758000120, nickname: '乙', content: 'c' },
    ],
  })
  const out = await run('get_sns', { mode: 'users' })
  assert.match(out, /甲：2 条/)
  assert.match(out, /乙：1 条/)
  assert.ok(out.indexOf('甲') < out.indexOf('乙'), '发得多的排前面')
})

test('export_chat：格式白名单，认不出来就报参数错误（不猜）', async () => {
  svc.listSessions = async () => ([{ displayName: '甲', username: 'wxid_a' }])
  const out = await run('export_chat', { contact: '甲', format: 'pdf' })
  assert.match(out, /参数错误: format 只能是 html\/txt\/json\/excel/)
})

test('export_chat：txt 走 txt 那条导出，并在回话里说明格式', async () => {
  const used: string[] = []
  const realTxt = exportService.exportTxt.bind(exportService)
  const realHtml = exportService.exportHtml.bind(exportService)
  ;(exportService as any).exportTxt = async (_t: string, outDir: string) => {
    used.push('txt')
    mkdirSync(outDir, { recursive: true })
    return { success: true, path: outDir, count: 7 }
  }
  ;(exportService as any).exportHtml = async (_t: string, outDir: string) => {
    used.push('html')
    return { success: true, path: outDir, count: 7 }
  }
  const exportRoot = join(HOME, 'exports-tmp2')
  mkdirSync(exportRoot, { recursive: true })
  process.env.WEFLOW_ASSISTANT_EXPORT_ROOT = exportRoot
  svc.listSessions = async () => ([{ displayName: '甲', username: 'wxid_a' }])
  try {
    const out = await run('export_chat', { contact: '甲', format: 'txt' })
    assert.deepEqual(used, ['txt'], '选了 txt 就不该走 html')
    assert.match(out, /已导出 7 条/)
    assert.match(out, /（txt）/, '回话里要说清导的是什么格式')
  } finally {
    ;(exportService as any).exportTxt = realTxt
    ;(exportService as any).exportHtml = realHtml
  }
})

test('search_favorites：不给关键词就列最近的收藏', async () => {
  const asked: any[] = []
  svc.getFavorites = async (opts: any) => {
    asked.push(opts)
    return { success: true, total: 12, favorites: [{ title: '最近的收藏', source_name: '某号' }] }
  }
  const out = await run('search_favorites', {})
  assert.match(out, /最近的收藏/)
  assert.equal(asked[0].keyword, undefined, '空关键词不该往下传')
  assert.ok(asked[0].limit >= 15, '列最近时给的条数比搜索时多')
})

test('search_favorites：收藏为空与搜不到是两句不同的话', async () => {
  svc.getFavorites = async () => ({ success: true, total: 0, favorites: [] })
  assert.match(await run('search_favorites', {}), /收藏是空的，或收藏库/)
  assert.match(await run('search_favorites', { keyword: '不存在' }), /收藏中未搜到「不存在」/)
})


// ---------------------------------------------------------------- look_at_image

/** 一个能解析出联系人的会话表 */
function stubOneSession(): void {
  svc.listSessions = async () => ([{ displayName: '甲', username: 'wxid_a' }])
}

const IMAGE_OK = JSON.stringify({ success: true, b64: 'AAAA', mime: 'image/jpeg', width: 240, height: 120 })

/** 这个文件默认跑在 strict 下（图片不许出境），这几条要测的是取图那一段 */
async function withBalanced<T>(body: () => Promise<T>): Promise<T> {
  const realGet = configService.get.bind(configService)
  ;(configService as any).get = (k: string) => (k === 'assistantPrivacy' ? 'balanced' : realGet(k))
  try { return await body() } finally { ;(configService as any).get = realGet }
}

test('look_at_image：取到图时把它挂进 ctx，并在正文里说明尺寸', async () => {
  stubOneSession()
  const realGet = configService.get.bind(configService)
  ;(configService as any).get = (k: string) => (k === 'assistantPrivacy' ? 'balanced' : realGet(k))
  try {
    const calls = stubScript(IMAGE_OK)
    const c = ctx()
    const out = await run('look_at_image', { contact: '甲', image: '1234' }, c)

    assert.match(out, /已附上图片 #1234/)
    assert.match(out, /240×120/)
    assert.equal(c.pendingImages!.length, 1)
    assert.equal(c.pendingImages![0].b64, 'AAAA')
    assert.equal(c.pendingImages![0].localId, 1234)
    // talker 要解析成 username，local_id 要原样传下去
    assert.deepEqual(calls[0].args, ['--talker', 'wxid_a', '--local-id', '1234', '--json'])
    assert.match(calls[0].script, /read_image\.py$/)
  } finally {
    ;(configService as any).get = realGet
  }
})

test('look_at_image：strict 模式下拒绝，而且一个脚本都不调', async () => {
  // 关键不是"拒绝"，是**根本不去取**：取了就有一份解密后的图片落在磁盘上，
  // 而 strict 的承诺是这些内容不出本机。
  stubOneSession()
  const realGet = configService.get.bind(configService)
  ;(configService as any).get = (k: string) => (k === 'assistantPrivacy' ? 'strict' : realGet(k))
  try {
    const calls = stubScript(IMAGE_OK)
    const c = ctx()
    const out = await run('look_at_image', { contact: '甲', image: '1234' }, c)

    assert.match(out, /strict/)
    assert.match(out, /balanced/, '要告诉用户怎么才能用')
    assert.equal(calls.length, 0, 'strict 下不该调用取图脚本')
    assert.equal(c.pendingImages, undefined)
  } finally {
    ;(configService as any).get = realGet
  }
})

test('look_at_image：编号不是数字时给可读的说法，也不调脚本', async () => {
  stubOneSession()
  const calls = stubScript(IMAGE_OK)
  const out = await run('look_at_image', { contact: '甲', image: '第三张' })
  assert.match(out, /\[图片 #N\] 的编号/)
  assert.equal(calls.length, 0)
})

test('look_at_image：缺参数直接说不缺哪一半', async () => {
  assert.equal(await run('look_at_image', { contact: '甲' }), '(缺少 contact 或 image 参数)')
  assert.equal(await run('look_at_image', { image: '1' }), '(缺少 contact 或 image 参数)')
})

test('look_at_image：本机没有这张图的副本时说明白，不当成"看过了"', async () => {
  stubOneSession()
  const c = ctx()
  stubScript(JSON.stringify({ success: true, reason: '本机没有这张图的副本' }))
  const out = await withBalanced(() => run('look_at_image', { contact: '甲', image: '99' }, c))

  assert.match(out, /没取到 #99/)
  assert.match(out, /本机没有这张图的副本/)
  assert.equal(c.pendingImages, undefined)
})

test('look_at_image：一轮里看两张封顶（图片按体积算钱，也按体积算隐私）', async () => {
  stubOneSession()
  const c = ctx()
  stubScript(IMAGE_OK)
  await withBalanced(async () => {
    assert.match(await run('look_at_image', { contact: '甲', image: '1' }, c), /已附上/)
    assert.match(await run('look_at_image', { contact: '甲', image: '2' }, c), /已附上/)
    const third = await run('look_at_image', { contact: '甲', image: '3' }, c)
    assert.match(third, /已经看过 2 张图/)
  })

  assert.equal(c.pendingImages!.length, 2, '第三张不该进上下文')
})

test('look_at_image：脚本失败时带上原因，不假装看到了', async () => {
  stubOneSession()
  const c = ctx()
  stubScript('', 2, '缺少 PyCryptodome')
  const out = await withBalanced(() => run('look_at_image', { contact: '甲', image: '5' }, c))

  assert.match(out, /取图失败/)
  assert.match(out, /退出码 2/)
  assert.equal(c.pendingImages, undefined)
})

test('get_messages 有时间窗时走范围读，并把窗口说清楚', async () => {
  // 这条盯的是"上周三他说了什么"以前够不着的那件事：只按条数取，久远的日子取不到。
  const calls: any[] = []
  svc.listSessions = async () => ([{ displayName: '甲', username: 'wxid_a' }])
  svc.getMessages = async () => { calls.push({ kind: 'recent' }); return [] }
  svc.getMessagesInRange = async (talker: string, limit: number, from?: number, to?: number) => {
    calls.push({ kind: 'range', talker, limit, from, to })
    return [{ createTime: 1758000000, isSend: false, senderUsername: '甲', content: '那天的消息' }]
  }
  const realGet = configService.get.bind(configService)
  ;(configService as any).get = (k: string) => (k === 'assistantPrivacy' ? 'balanced' : realGet(k))
  try {
    const out = await run('get_messages', { contact: '甲', since: '2026-09-16', limit: 20 })
    assert.equal(calls.length, 1)
    assert.equal(calls[0].kind, 'range', '给了时间窗就该走范围读，不是取最近 N 条')
    assert.ok(calls[0].from > 0 && calls[0].to === undefined)
    assert.match(out, /共 1 条/, '要把窗口和条数说清楚')
    assert.match(out, /那天的消息/)

    // 不给时间窗：一个字节都不变，仍走原来的最近 N 条
    calls.length = 0
    await run('get_messages', { contact: '甲' })
    assert.deepEqual(calls, [{ kind: 'recent' }])
  } finally {
    ;(configService as any).get = realGet
    delete (svc as any).getMessagesInRange
  }
})

test('get_messages 看不懂的时间写成一句可读的参数错误，而不是猜一个窗口', async () => {
  svc.listSessions = async () => ([{ displayName: '甲', username: 'wxid_a' }])
  const out = await run('get_messages', { contact: '甲', since: '上周三' })
  assert.match(out, /时间看不懂/)
  assert.match(out, /3d \/ 2w \/ 12h/, '要告诉它该怎么写')
})

// ------------------------------------------------- 名字不在最近会话里时查通讯录

test('名字不在会话列表里，但通讯录里有：照样读得到（旧行为是当成一个不存在的 talker）', async () => {
  // 会话列表只取最近 300 个，通讯录是完整的。名字落在 300 之外的人（很久没聊、或对话很多
  // 的人）旧行为会被原样当成 talker → 下游读到空 → 助手说"没找到消息"，而他其实在通讯录里。
  const asked: string[] = []
  svc.listSessions = async () => ([{ displayName: '甲', username: 'wxid_a' }])
  svc.listContacts = async (keyword: string) => {
    asked.push(keyword)
    return [{ username: 'wxid_old', displayName: '老同学', remark: '老王', nickname: '小虎', alias: 'huzi' }]
  }
  svc.getMessages = async (talker: string) => {
    asked.push(`read:${talker}`)
    return talker === 'wxid_old'
      ? [{ createTime: 1758000000, isSend: false, senderUsername: '老同学', content: '好久不见' }]
      : []
  }
  const realGet = configService.get.bind(configService)
  ;(configService as any).get = (k: string) => (k === 'assistantPrivacy' ? 'balanced' : realGet(k))
  try {
    assert.match(await run('get_messages', { contact: '老王' }), /好久不见/, '备注名应当能找到人')
    assert.match(await run('get_messages', { contact: '小虎' }), /好久不见/, '昵称也认')
    assert.match(await run('get_messages', { contact: 'huzi' }), /好久不见/, '别名也认')
    assert.ok(asked.includes('read:wxid_old'), '要拿通讯录里的 username 去读，而不是原样用查询词')
  } finally {
    ;(configService as any).get = realGet
  }
})

test('通讯录里匹配到多个：不猜，照旧说没找到', async () => {
  svc.listSessions = async () => ([])
  svc.listContacts = async () => ([
    { username: 'wxid_1', displayName: '小王', remark: '王工' },
    { username: 'wxid_2', displayName: '小王', remark: '王老师' },
  ])
  // 桩要**按 talker 区分**：对谁都说"有消息"的话，这条测的就不是"有没有猜"了
  // （评测那边踩过一模一样的坑）。
  svc.getMessages = async (talker: string) => (
    talker === 'wxid_1' || talker === 'wxid_2'
      ? [{ createTime: 1758000000, isSend: false, content: '内容' }]
      : [])
  const out = await run('get_messages', { contact: '小王' })
  assert.match(out, /没找到/, '有歧义就不许挑一个——宁可让它说不出话，也不能读错人的聊天')
})

// ------------------------------------------------- 阅读统计（公众号推送 vs 日报处理）

test('get_reading_stats 列出推送最多的号，并把参数传给脚本', async () => {
  const calls = stubScript(JSON.stringify({
    success: true,
    period: { start: '2026-09-17', end: '2026-09-23', days: 7 },
    sources: [
      { name: '甲号', pushed: 248, processed: 12 },
      { name: '乙号', pushed: 156, processed: 30 },
      { name: '丙号', pushed: 9, processed: 0 },
    ],
  }))
  const out = await run('get_reading_stats', { days: 7 })

  assert.match(out, /3 个公众号有推送/)
  assert.match(out, /发得最多：甲号\(248\)、乙号\(156\)、丙号\(9\)/)
  assert.match(out, /日报处理得最多：乙号\(30\)、甲号\(12\)/, '按处理数排序，0 的不列')
  assert.deepEqual(calls[0].args, ['--days', '7', '--json'])
  assert.match(calls[0].script, /daily_stats\.py$/)
})

test('日报没在跑时直说"没在跑"，而不是让用户以为那些号没内容', async () => {
  // 实测就是这么发现的：最近 7 天 processed 全是 0，而 30 天窗口里全是非零——
  // 差别不在号上，在**日报从 09-05 起就没再跑过**。
  stubScript(JSON.stringify({
    success: true, period: { start: '2026-09-17', end: '2026-09-23' },
    sources: [{ name: '甲号', pushed: 248, processed: 0 }],
  }))
  const out = await run('get_reading_stats', { days: 7 })
  assert.match(out, /日报没有任何处理记录/)
  assert.doesNotMatch(out, /日报处理得最多/, '全是 0 就别列"处理得最多"')
  // 那句补充**是可选的**，因为它是从本机 `output/biz-daily` 扫出来的：CI 是干净检出、
  // 没有归档，那时就没有这句。第一条写成了必有的断言，CI 当场红了（这已经是第二次：
  // 上一次是测试依赖了本地装了而 CI 没装的 html2text）。所以这里钉的是**形状**，不是存在。
  assert.match(out, /日报没有任何处理记录(；最近一次有内容的日报是 \d{4}-\d{2}-\d{2}（\d+ 天前）)?$/)
})

test('脚本失败时如实说失败', async () => {
  stubScript('', 2, '需要 sqlcipher3')
  assert.match(await run('get_reading_stats', {}), /读取公众号统计失败/)
})

test('days 越界变成可读的参数错误', async () => {
  assert.equal(await run('get_reading_stats', { days: 999 }), '(参数错误: days 必须是 1-90 的整数)')
})

// ------------------------------------------------- 写工具的"写到了哪"必须是真的

test('export_chat 报的是真的落盘位置：撞名带序号，自定义根不泄露绝对路径', async () => {
  // 实测（真库验收）：第二次导出明明写进了 `…-2`，消息里报的却是基础名——因为路径是**事前拼的**，
  // 不是从 `outDir` 来的。写工具报错位置比不报更糟：用户照着找，找不到。
  const root = mkdtempSync(join(tmpdir(), 'weflow-export-branch-'))
  const realExport = exportService.exportTxt.bind(exportService)
  let calls = 0
  ;(exportService as any).exportTxt = async (_talker: string, outDir: string) => {
    calls += 1
    mkdirSync(outDir, { recursive: true })
    return { success: true, count: 3 }
  }
  process.env.WEFLOW_ASSISTANT_EXPORT_ROOT = root
  svc.listSessions = async () => ([{ displayName: '甲', username: 'wxid_a' }])
  try {
    const first = await run('export_chat', { contact: '甲', format: 'txt' })
    const second = await run('export_chat', { contact: '甲', format: 'txt' })

    assert.equal(calls, 2)
    assert.match(first, /已导出 3 条消息到 /)
    // 临时根的目录名（不含路径分隔符），出现在消息里就说明报了绝对路径
    const rootName = root.split(sep).pop() as string
    assert.doesNotMatch(first, new RegExp(rootName), '不许把绝对路径报进聊天')
    assert.doesNotMatch(first, /^\S*[A-Za-z]:/, '消息里不该出现盘符')

    const nameOf = (text: string) => text.match(/到 ([^（]+)（/)?.[1] ?? ''
    assert.notEqual(nameOf(first), nameOf(second), '第二次撞名了，报的名字就该不一样')
    assert.match(nameOf(second), /-2\/$/, `第二次要报带序号的那个目录：${nameOf(second)}`)
  } finally {
    ;(exportService as any).exportTxt = realExport
    delete process.env.WEFLOW_ASSISTANT_EXPORT_ROOT
    rmSync(root, { recursive: true, force: true })
  }
})

test('export_chat 用默认导出根时，报的是相对路径（照得着，也不泄露本机路径）', async () => {
  // 默认根 = 仓库的 output/exports。这条把上一条缺的那半补上：**默认情况下**消息里应当是
  // `output/exports/<目录名>`，用户照着找得到；而不是绝对路径。
  //
  // 桩**不建目录**：这条断言的是措辞，不该往仓库里落测试垃圾。代价是路径带不带 `-2`
  // 后缀取决于仓库里恰好有没有同名目录，所以正则两可——这正好也是被测行为。
  const realExport = exportService.exportTxt.bind(exportService)
  ;(exportService as any).exportTxt = async () => ({ success: true, count: 7 })
  delete process.env.WEFLOW_ASSISTANT_EXPORT_ROOT
  svc.listSessions = async () => ([{ displayName: '甲', username: 'wxid_a' }])
  try {
    const out = await run('export_chat', { contact: '甲', format: 'txt' })
    assert.match(out, /已导出 7 条消息到 output\/exports\/甲-\d{12}(-\d+)?\//)
    // 注意：这条断言本身踩过 heredoc——`[\\/]` 经 shell 会变成 `[\/]`，等于只挡正斜杠。
    assert.doesNotMatch(out, /[A-Za-z]:/, '不该出现盘符（正反斜杠都不行）')
  } finally {
    ;(exportService as any).exportTxt = realExport
  }
})

// ------------------------------------------------- 脚本类工具：pythonBridge 就是那个桩点

/**
 * 本文件头部原先写着"`get_todos` 没有便宜的桩点"——**那是错的**。
 * `pythonBridge.setScriptRunner` 是公开的注入点（这个文件里早就在用，见上面的 `stubScript`），
 * 而脚本类工具全部经由 `runPythonJson`；于是 `get_todos` 这个能读用户真实待办的工具，
 * 一行分支都没被执行过。那句注释不改掉的话，下一个人会照着它继续绕开。
 *
 * 夹具照 `scripts/extract_todos.py` 的**真实**返回写（`{task, urgency, deadline, status, id}`），
 * 不是照工具里的兜底链（`t.task || t.content || t.text || t.title`）猜。顺带记一笔：
 * 真库上跑过，两个 status 都返回 `[]` ——"有条目"那一支在本机数据里看不到，只能靠夹具，
 * 所以下面这几条是**唯一**盯过它的地方。
 */

/** `extract_todos.py list --json --meta` 的真实形状 */
function todosMeta(items: any[], extracted = true): string {
  return JSON.stringify({ items, extracted, count: items.length })
}

test('get_todos 空清单时说"没有"，而不是回一个空串', async () => {
  const first = stubScript(todosMeta([]))
  try {
    assert.equal(await run('get_todos', {}), '(没有待办任务)')
    assert.deepEqual(first[0].args, ['list', '--status', 'pending', '--json', '--meta'], '默认查待办')
  } finally { bridge.setScriptRunner(null) }

  const second = stubScript(todosMeta([]))
  try {
    assert.equal(await run('get_todos', { status: 'done' }), '(没有已完成任务)')
    // status 必须真的拼进 argv，否则两个分支只有文案不同、查的东西一样
    assert.deepEqual(second[0].args, ['list', '--status', 'done', '--json', '--meta'], 'status 要传下去')
  } finally { bridge.setScriptRunner(null) }
})

test('get_todos "从没提取过"不能说成"没有待办"——这是两件事', async () => {
  // 真库上就是这么一回事：`~/.weflow-cli/todos.json` 根本不存在，
  // 而提取是**要用户显式跑** `weflow-cli todos extract --yes` 的（照例有确认门）。
  // 把它说成"你没有待办"，用户会以为自己真的没事要做。
  stubScript(todosMeta([], false))
  try {
    const out = await run('get_todos', {})
    assert.doesNotMatch(out, /没有待办任务/, '没提取过 ≠ 没有待办')
    assert.match(out, /还没提取过待办/)
    assert.match(out, /todos extract/, '要告诉用户怎么让它有内容')
  } finally { bridge.setScriptRunner(null) }
})

test('get_todos 兼容老形状：拿到裸数组时退回原来的说法，而不是崩或错报', async () => {
  // `--meta` 是新增开关。万一调用的是没有它的实现（拿到的是裸数组），
  // 不能把 `undefined.items` 当成"没提取过"，也不能抛在 `.items` 上。
  stubScript('[]')
  try {
    assert.equal(await run('get_todos', {}), '(没有待办任务)')
  } finally { bridge.setScriptRunner(null) }
})

test('get_todos 列出条目：带紧急度与截止；"未提及"的截止不摆出来', async () => {
  stubScript(todosMeta([
    { id: 1, task: '交季度报表', urgency: '高', deadline: '本周五', status: 'pending' },
    { id: 2, task: '回老王的邮件', urgency: '中', deadline: '未提及', status: 'pending' },
  ]))
  try {
    const out = await run('get_todos', {})
    assert.match(out, /^待办 2 项:/)
    assert.match(out, /· \[高\] 交季度报表 \(截止 本周五\)/)
    assert.match(out, /· \[中\] 回老王的邮件$/, '没有截止就别补一个"未提及"')
    assert.doesNotMatch(out, /未提及/)
  } finally { bridge.setScriptRunner(null) }
})

test('get_todos 最多报 15 条，不把整个清单倒出来', async () => {
  const many = Array.from({ length: 20 }, (_, i) =>
    ({ id: i + 1, task: `事项${i + 1}`, urgency: '低', deadline: '未提及', status: 'pending' }))
  stubScript(todosMeta(many))
  try {
    const out = await run('get_todos', {})
    assert.match(out, /^待办 20 项:/, '条数要说全量')
    assert.equal(out.split('\n').filter(l => l.startsWith('· ')).length, 15, '明细只列 15 条')
  } finally { bridge.setScriptRunner(null) }
})

test('get_todos 脚本失败时如实说失败，且 stderr 里的密钥形状东西要打码', async () => {
  // stderr 是要出境的东西。桥接层把三段尾巴拼进消息，工具这边再走一遍脱敏——
  // 少了这一步，脚本的参数回显（例如打印配置）就会把密钥原样送进对话。
  const realGet = configService.get.bind(configService)
  ;(configService as any).get = (k: string) => (k === 'assistantPrivacy' ? 'balanced' : realGet(k))
  const FAKE_KEY = 'sk-' + 'a1b2c3d4e5f6g7h8i9j0'
  stubScript('', 1, `Traceback … reading config ${FAKE_KEY}`)
  try {
    const out = await run('get_todos', {})
    assert.match(out, /^\(待办查询失败:/)
    assert.match(out, /脚本退出码 1/, '失败原因要分类报出来')
    assert.doesNotMatch(out, new RegExp(FAKE_KEY), '密钥形状的东西不许原样出境')
    assert.match(out, /\[密钥\]/)
  } finally {
    bridge.setScriptRunner(null)
    ;(configService as any).get = realGet
  }
})

test('get_todos 脚本没给 JSON（退出码 0）时也算失败，而不是当成空清单', async () => {
  // 这个区分很重要：把"没解析到"当成"没有待办"，用户会以为自己真的没事要做。
  stubScript('Traceback …\n')
  try {
    const out = await run('get_todos', {})
    assert.match(out, /没有给出可解析的 JSON/)
    assert.doesNotMatch(out, /没有待办任务/)
  } finally { bridge.setScriptRunner(null) }
})

// ------------------------------------------------- 起草回复（判断 → 起草 → 排序）

/**
 * 这条路的输入**走 stdin**：TS 侧逐条遮罩后把对话正文交给 `draft_reply.py`。
 * 所以断言必须看 stdin 的内容——这正是上面 `stubScript` 要记 `stdin` 的原因；
 * 只看 argv 的桩会让"送进去的是原文还是遮过的"永远测不到。
 */
const DRAFT_OK = {
  success: true, gate: 'draft', ranked: true,
  judgment: { intent: 'request_action', need: 'action', action: 'give_commitment',
              should_reply: 0.82, risk: 3, money: 0.05, commitment: 0.2 },
  drafts: [{ text: '今天下班前发你', why: '给个具体时间' },
           { text: '我找找，马上', why: '低姿态' }],
}

/** 一个能起草的世界：会话在、消息按"新的在前"给（和真实读取器一致） */
function draftWorld(): void {
  svc.listSessions = async () => ([{ displayName: '老王', username: 'wxid_w' }])
  svc.getMessages = async () => ([
    { createTime: 1758601200, isSend: false, senderUsername: '老王', localType: 3,
      content: '<msg><appmsg><title>Base.csv</title></appmsg></msg>', parsedContent: '[文件] Base.csv' },
    { createTime: 1758600600, isSend: true, senderUsername: '我', localType: 1,
      content: '今天下午', parsedContent: '今天下午' },
    { createTime: 1758600000, isSend: false, senderUsername: '老王', localType: 1,
      content: '那个文件你什么时候发我', parsedContent: '那个文件你什么时候发我' },
  ])
}

// 这条路的隐私模式要钉成 balanced（这个文件默认 strict，会直接拒绝起草）；
// 复用上面 `look_at_image` 那一段的 `withBalanced`，不再写第二份同样的替换。

test('draft_reply：把候选渲染出来，判定里的英文键翻成人话', async () => {
  draftWorld()
  const calls = stubScript(JSON.stringify(DRAFT_OK))
  try {
    await withBalanced(async () => {
      const text = await run('draft_reply', { contact: '老王' })
      assert.match(text, /建议这样回（2 条，第一条是判断最合适的）/)
      assert.match(text, /1\. 今天下班前发你/)
      assert.match(text, /2\. 我找找，马上/)
      // 键名不许原样念给用户听（`request_action` 是个英文判据键，不是人话）
      assert.doesNotMatch(text, /request_action|give_commitment/)
      assert.match(text, /对方有事要你办/)
      assert.match(text, /建议给一个做得到的时间或交付/)
      assert.match(text, /风险 3\.0\/9 留神/)
      assert.match(text, /没有发送任何东西/)
      // 候选**不许**以 `(` 起头：那个前缀在本仓库里表示"工具没产出内容"
      assert.doesNotMatch(text, /^\(/)
    })
    assert.equal(calls.length, 1, '只该跑一次脚本')
    assert.deepEqual(calls[0].args, ['--stdin', '--yes', '--json', '--count', '3'])
  } finally { bridge.setScriptRunner(null) }
})

test('draft_reply：给脚本的是按时间正序、带方向标签的对话，非文本消息给类型标签', async () => {
  draftWorld()
  const calls = stubScript(JSON.stringify(DRAFT_OK))
  try {
    await withBalanced(async () => { await run('draft_reply', { contact: '老王' }) })
    const payload = JSON.parse(String(calls[0].stdin))
    assert.equal(payload.name, '老王')
    assert.equal(payload.lines.length, 3)
    // 正序：读取器给的是"新的在前"，模型要按时间读
    assert.match(payload.lines[0], /对方：那个文件你什么时候发我/)
    assert.match(payload.lines[1], /我：今天下午/)
    assert.match(payload.lines[2], /对方：\[文件\] Base\.csv/)
    assert.doesNotMatch(payload.lines[2], /appmsg/, '非文本消息不许把原始 XML 交出去')
  } finally { bridge.setScriptRunner(null) }
})

test('draft_reply：strict 下拒绝，而且一个脚本都不调（正文压根不出境）', async () => {
  // 这个文件默认就是 strict（configService 的默认值），所以这里不覆盖配置。
  // 拒绝的理由要落在"正文不出境 vs 起草必须出境"这对矛盾上，并给出两条出路。
  draftWorld()
  const calls = stubScript(JSON.stringify(DRAFT_OK))
  try {
    const out = await run('draft_reply', { contact: '老王' })
    assert.equal(calls.length, 0, '拒绝就得是**真的**拒绝：不能先发出去再说不给草稿')
    assert.match(out, /^\(当前隐私模式是 strict/)
    assert.match(out, /assistantPrivacy balanced/)
    assert.match(out, /weflow-cli draft/)
  } finally { bridge.setScriptRunner(null) }
})

test('draft_reply：本机推理引擎也不是起草的免死金牌（这条出境的是脚本自己的远程调用）', async () => {
  // 与 `look_at_image` 的关键差别：那张图确实送到本机模型，所以 `isLocalInference()`
  // 在那里是**成立的**旁路；而 `draft_reply.py` 无论 aiEngine 配成什么，都会把正文发给
  // 远端的判断模型与生成模型（`jev_client` 走 HTTP、`_utils.call_deepseek` 写死 DeepSeek）。
  // 于是"用本机引擎"这个理由在这条路上不成立——照抄那个旁路就是一个静默出境的口子。
  draftWorld()
  const realGet = configService.get.bind(configService)
  ;(configService as any).get =
    (k: string) => (k === 'assistantPrivacy' ? 'strict' : k === 'aiEngine' ? 'ollama' : realGet(k))
  const calls = stubScript(JSON.stringify(DRAFT_OK))
  try {
    const out = await run('draft_reply', { contact: '老王' })
    assert.equal(calls.length, 0, 'strict 下 ollama 也不许把正文发给远端模型')
    assert.match(out, /^\(当前隐私模式是 strict/)
  } finally {
    bridge.setScriptRunner(null)
    ;(configService as any).get = realGet
  }
})

test('draft_reply：闸门拦下时照原样报风险与建议，不当成失败', async () => {
  // 被拦是**这条最该给的回答**，所以不能以 `(` 起头——那会让主循环判成"没产出内容"。
  draftWorld()
  stubScript(JSON.stringify({
    success: true, gate: 'refused', ranked: false,
    reason: '风险 8/9：已经闹翻或涉及法律资金',
    advice: ['先翻一下更早的聊天记录', '拿不准就先确认'],
    judgment: { intent: 'vent_anger', action: 'check_history', risk: 8, money: 0.05 },
    drafts: [],
  }))
  try {
    const out = await withBalanced(async () => run('draft_reply', { contact: '老王' }))
    assert.match(out, /^不起草。/)
    assert.doesNotMatch(out, /^\(/)
    assert.match(out, /风险 8\/9/)
    assert.match(out, /· 先翻一下更早的聊天记录/)
    assert.match(out, /在发火，想被听见而不是被解决/)
  } finally { bridge.setScriptRunner(null) }
})

test('draft_reply：脚本跑不通、或没给出候选时，如实说', async () => {
  draftWorld()
  stubScript('', 2, '缺少 TypeSafe key')
  try {
    const failed = await withBalanced(async () => run('draft_reply', { contact: '老王' }))
    assert.match(failed, /^\(起草失败/)
    assert.match(failed, /退出码 2/)
  } finally { bridge.setScriptRunner(null) }

  stubScript(JSON.stringify({ success: true, gate: 'draft', drafts: [] }))
  try {
    const empty = await withBalanced(async () => run('draft_reply', { contact: '老王' }))
    assert.match(empty, /^\(起草没给出候选\)/)
  } finally { bridge.setScriptRunner(null) }
})

test('draft_reply：缺 contact 与查无此人都要说得出是哪一种', async () => {
  assert.equal(await run('draft_reply', {}), '(缺少 contact 参数)')

  svc.listSessions = async () => ([{ displayName: '老王', username: 'wxid_w' }])
  svc.getMessages = async () => []
  assert.match(await withBalanced(async () => run('draft_reply', { contact: '老王' })), /没找到「老王」的消息/)
})

test('draft_reply：count 越界变成可读的参数错误，不悄悄用默认值', async () => {
  assert.equal(await run('draft_reply', { contact: '老王', count: 9 }),
               '(参数错误: count 必须是 1-5 的整数)')
})
