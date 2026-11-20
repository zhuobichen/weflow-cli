/**
 * 词法快路径（D-168）的表本身。
 *
 * 这一层零模型调用、默认开着，所以**它错一次就是白调一次工具**，而且没人会得到报错。
 * 因此测试分三类，缺一不可：
 *
 * 1. **该中的要中**：每条规则至少一句真实问法（问句取自评测集，不是编的）；
 * 2. **不该中的不许中**：近义说法、要自由参数的说法、问功能本身的说法；
 * 3. **表本身要健康**：空组、重名不同义、工具名写错——这些都不会报错，只会永不命中。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

const { routeByIntent, routeWithArgs, intentRulesProblem, INTENT_RULES } = await import('../src/services/assistantIntent.js')
const { TOOL_DEFS } = await import('../src/services/assistantTools.js')

const toolNames = new Set(TOOL_DEFS.map((def: any) => def.function.name))

function capabilityOf(message: string): string | null {
  const hit = routeByIntent(message)
  return hit ? hit.capability.name : null
}

test('表是健康的：没有空组、没有重名不同义', () => {
  assert.deepEqual(intentRulesProblem(), [])
})

test('每条规则指向的工具都真的存在（拼错的名字只会永不命中）', () => {
  for (const rule of INTENT_RULES) {
    assert.ok(toolNames.has(rule.capability.tool),
      `${rule.capability.name} 指向不存在的工具 ${rule.capability.tool}`)
  }
})

test('该中的要中：一句真实问法对上一个能力', () => {
  const cases: Array<[string, string]> = [
    // 问句来自评测集，改表时顺手核对一遍
    ['我最近都在和谁聊天？', 'list_sessions'],
    ['我最近在跟谁聊天？', 'list_sessions'],
    ['最近哪些公众号发得最多？', 'reading_stats'],
    ['我朋友圈最近有什么？', 'sns_timeline'],
    ['朋友圈这周谁发得最多？', 'sns_stats'],
    ['我微信读书上有笔记的那些书都叫什么？', 'weread_notebooks'],
    ['我最近有什么待办？', 'todos_pending'],
    ['哪些待办我已经做完了？', 'todos_done'],
    ['我最近的学习回顾里写了什么？', 'learning_review'],
    ['我的知识库里都存了些什么概念？', 'concepts_overview'],
    ['我的知识库有没有坏链接或者没人引用的孤儿页？', 'wiki_lint'],
    ['我的技能包有没有问题？', 'skills_check'],
    ['我装了哪些技能？', 'skills_list'],
    ['公众号排版有哪些主题可选？', 'themes_list'],
    ['我的数据现在总共有多少东西？', 'stats_overview'],
  ]
  for (const [message, expected] of cases) {
    assert.equal(capabilityOf(message), expected, `「${message}」应当路由到 ${expected}`)
  }
})

test('不该中的不许中：引号点名 → 需要自由参数，交回循环', () => {
  // 这两条在评测集里是 set_todo_status：要认「买牛奶」/「牛奶」到底是哪条，表给不出参数
  assert.equal(capabilityOf('把「买牛奶」那条待办标记成已完成。'), null)
  assert.equal(capabilityOf('把「牛奶」那条待办标记成已完成。'), null)
  // 点名联系人
  assert.equal(capabilityOf('我和「查无此人」上周聊了什么？'), null)
})

test('不该中的不许中：问功能本身、问两个问题', () => {
  assert.equal(capabilityOf('场景切换怎么用？'), null)
  assert.equal(capabilityOf('这个功能支持吗？'), null)
  assert.equal(capabilityOf('diffusion 和 transformer 有什么区别？'), null)
  // 一条消息两个问号 = 一个工具答不完
  assert.equal(capabilityOf('甲最近跟我说了什么？另外谁在等我回话？'), null)
})

test('不该中的不许中：近义说法不能被别的规则抢走', () => {
  // 「回话」不是「聊天」：它走 who_owes_reply，**不是** list_sessions
  assert.equal(capabilityOf('谁在等我回话？'), 'reply_debt')
  // 「哪些概念页」要按主题搜，不是列总览
  assert.equal(capabilityOf('关于扩散模型，我整理过哪些概念页？'), null)
  // 排版请求会被"公众号"这词带偏，必须被排版本身否决
  assert.equal(capabilityOf('把这段话排版成能直接粘进公众号的样子：今天去河边跑了五公里，风很大。'), null)
  // 收藏里搜文章：要关键词，表里没有这个参数
  assert.equal(capabilityOf('我收藏里有没有讲扩散模型的文章？'), null)
  // 闲聊与凭空问答一律不碰
  assert.equal(capabilityOf('把这句话翻成英文：今天天气不错'), null)
  assert.equal(capabilityOf('胶子凝聚是什么？'), null)
  assert.equal(capabilityOf(''), null)
})

test('规则顺序：更具体的规则排在更宽的规则前面', () => {
  // 「朋友圈…最多」同时命中 timeline 的「朋友圈」与 stats 的第一组；必须是 stats
  assert.equal(capabilityOf('朋友圈最近谁发得最多？'), 'sns_stats')
  // 「公众号发得最多」同时像日报；必须是阅读统计
  assert.equal(capabilityOf('最近哪些公众号发得最多？'), 'reading_stats')
})

test('命中理由要能看懂：写清是哪两个词撞出来的', () => {
  const hit = routeByIntent('我最近有什么待办？')
  assert.ok(hit)
  assert.match(hit!.reason, /词法命中/)
  assert.match(hit!.reason, /待办/)
})

// ---------------------------------------------------------------- 抽参数那一档

test('抽参数：人名不是照着某条例句写死的', () => {
  // 这一组的名字**都不来自评测集**：规则要是只认那一个名字，它就不是功能，是抄答案
  for (const name of ['联系人A', '小王', 'Mike']) {
    const hit = routeWithArgs(`${name}最近跟我说了什么`)
    assert.equal(hit?.capability.tool, 'get_messages', `${name} 应当能抽出来`)
    assert.equal(hit?.capability.args.contact, name)
  }
})

test('抽参数：抽不出人名就不出手（宁可交回循环）', () => {
  for (const message of [
    '跟我说说最近怎么样',       // 没有人名
    '谁在等我回话',             // 代词/疑问词，不是人名
    '那个文档的事我怎么回他比较好',
    '胶子凝聚是什么',
  ]) {
    assert.equal(routeWithArgs(message), null, `「${message}」不该被抽参数那一档接走`)
  }
})

test('抽参数：先把人名解析出来，别凭空造一个歧义', () => {
  // 「X 的 <东西> 发我看看」：真正的歧义在**名字**上（可能重名），不是在那个"东西"上
  const hit = routeWithArgs('联系人的文档发我看看'.replace('联系人', '小明'))
  assert.equal(hit?.capability.tool, 'list_contacts')
  assert.equal(hit?.capability.args.keyword, '小明')
  // 物体本身是聊天记录时，缺的就只有名字 —— 直接取消息
  const messages = routeWithArgs('甲的聊天记录发我看看')
  assert.equal(messages?.capability.tool, 'get_messages')
  assert.equal(messages?.capability.args.contact, '甲')
  // 代词不是人名
  assert.equal(routeWithArgs('把「我的」文档发我看看'), null)
  assert.equal(routeWithArgs('把他的文档发我看看'), null)
})

test('日报规则：过去时间窗不许走词法（日报只覆盖最近几天）', () => {
  // 2026-11-20 实测：「上个月公众号里讲 AI Agent 的文章」被词法派给 get_daily_report，
  // 于是拿本月 11-19 的条目当答案，还把日期说成"上个月"。
  assert.equal(routeByIntent('上个月公众号里讲 AI Agent 的文章，主要说了什么'), null)
  assert.equal(routeByIntent('去年的公众号文章讲了什么'), null)
  // 而"最近/今天"这类仍然该走日报
  assert.equal(routeByIntent('今天公众号推了什么文章')?.capability.tool, 'get_daily_report')
})
