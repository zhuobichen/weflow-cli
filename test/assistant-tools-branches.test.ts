/**
 * 助手工具的**分支**执行（12 个工具里 11 个走这里）。
 *
 * 此前只有 3 个纯函数被测过，**没有任何测试真正执行过一个工具分支**——也就是说"用户问
 * 「我和某某聊了什么」，助手会读到什么、会不会把内容原样发出去"从来没被验证过。这些分支
 * 的失败方式都是静默的：格式串了、隐私遮罩没生效、坏链接被真的抓了、抛错把整轮对话打断。
 *
 * 打桩打在**导出的单例**上（`chatService` / `wereadService`），它们和 `assistantTools`
 * 拿的是同一个模块对象，所以不需要模块级 mock。`fetch` 也换掉，于是 `read_favorite` 这条
 * 唯一会出网的工具能整条跑完而不联网。
 *
 * 不覆盖：`get_todos` —— 它 spawn 一个 Python 子进程去读真实数据库，没有便宜的桩点；
 * `get_daily_report` / `search_knowledge` 读的是仓库里真实的 `output/` 目录，只断言
 * 与环境无关的那几条路径（缺参、没数据、找不到）。这几条缺口写在 PROJECT_STATE 里。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const HOME = mkdtempSync(join(tmpdir(), 'weflow-assistant-tools-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME

const { chatService } = await import('../src/services/chatService.js')
const { wereadService } = await import('../src/services/wereadService.js')
const { AssistantMemory } = await import('../src/services/assistantMemory.js')
const { executeTool } = await import('../src/services/assistantTools.js')
const { exportService } = await import('../src/services/exportService.js')
const { configService } = await import('../src/services/configService.js')

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
  svc.connect = async () => {}
  svc.listSessions = async () => []
  svc.getMessages = async () => []
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
  const calls: { script: string; args: string[]; env?: Record<string, string> }[] = []
  bridge.setScriptRunner(async (script: string, args: string[], options: any) => {
    calls.push({ script, args, env: options?.env })
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
    assert.match(out, /output\/exports\/甲-\d{12}/, '路径固定、带时间戳')
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

