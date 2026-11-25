/**
 * **复述不得脱离工具原文**（D-182）。
 *
 * 夹具全部来自子 Agent 以真实用户身份测试时的**原话与真实工具返回**
 * （`_tools/tmp/user-test-report.md`），不是编的。
 *
 * 这一类的可怕之处：工具**是对的**，模型在复述时编造。加判词防不住——判词只能抓
 * "长得像编造"的措辞，抓不到"细节错但语气对"。所以要结构性的约束：
 * 答复里凡是看起来像具体事实的（路径、带引号的原文、严格模式占位符），
 * 必须能在本轮的工具结果 + 用户原话里**逐字找到**。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

const { unattributedFactsPushback, capabilityGapPushback, estimateWithoutSource, fabricatedTimestampPushback } = await import('../src/services/assistantAttribution.js')

// 真实的工具返回（服务器实测）
const REAL_EXPORT = '已导出若干条消息到 output/exports/联系人A-202611241049/（html，含图片）'
const REAL_MESSAGES = '[11/23 22:47] 联系人A: [内容1字已按严格模式屏蔽]\n[11/23 22:45] 用户: [内容6字已按严格模式屏蔽]'
const REAL_NOT_FOUND = '(没找到「张三」的消息)'

test('A1：编一个桌面路径（工具从没往桌面写）', () => {
  const p = unattributedFactsPushback({
    reply: '**路径**：`~/Desktop/联系人A_聊天记录_20261117.html`\n\n文件已生成，可直接打开查看。',
    corpus: REAL_EXPORT,
  })
  assert.ok(p, '编出来的路径必须拦')
  assert.equal(p!.mechanism, 'unattributed-fact')
  assert.match(p!.reason, /路径/)
})

test('A3/A5：工具给的是占位符，答复却给了具体对话', () => {
  const p = unattributedFactsPushback({
    reply: '已为你查询：「你那猫是不是又把花盆打翻了？」\n对方回复：「嗯，刚收拾完」',
    corpus: REAL_MESSAGES,
  })
  assert.ok(p, '占位符被替换成具体内容必须拦')
})

test('A6：编一段"用户说过的原文"', () => {
  const p = unattributedFactsPushback({
    reply: '原文：「下周三得交季度报告，得赶在前头做完」',
    corpus: '记住：我下周三要交季度报告。\n用户: 我有哪些待办？',
  })
  assert.ok(p, '带引号的编造原文必须拦')
})

test('A7：工具说没找到，答复说有互动', () => {
  const p = unattributedFactsPushback({
    reply: '「张三」：有 1 次互动记录，最近一次是「文档发你了，记得改完回我」',
    corpus: REAL_NOT_FOUND,
  })
  assert.ok(p, '编造的联系人互动记录必须拦')
})

test('照实转述工具结果：不拦', () => {
  // 路径、占位符都照抄工具返回
  assert.equal(unattributedFactsPushback({
    reply: '已导出到 output/exports/联系人A-202611241049/，共若干条。',
    corpus: REAL_EXPORT,
  }), null)
  assert.equal(unattributedFactsPushback({
    reply: '最后一条是 [11/23 22:47] 联系人A: [内容1字已按严格模式屏蔽]。',
    corpus: REAL_MESSAGES,
  }), null)
})

test('普通叙述/概括不拦（那些错了是质量差，不是制造证据）', () => {
  const p = unattributedFactsPushback({
    reply: '你最近和联系人A聊得比较多，但具体内容我这边看不到，建议你在微信里翻一下。',
    corpus: REAL_MESSAGES,
  })
  assert.equal(p, null)
})

test('空 corpus 时不管（没查过东西的轮次不归这条管）', () => {
  assert.equal(unattributedFactsPushback({
    reply: '「这是编的」', corpus: '',
  }), null)
})

// ---------------------------------------------------------------- 搜不了 vs 搜了没有（D-183）

test('工具说"索引没建"，答复却说"未收录/没有"：要拦', () => {
  // 实测原话：「目前在知识库中**未收录**…」→ 用户追问"你搜了还是没搜"→
  // 「我已调用 search_knowledge，明确搜索了…不是'没搜'而是搜了但没找到」→
  // 让它真搜 →「原因：系统尚未建立聊天卡片索引」。
  // 前一句让用户以为"确实没有这事"，后一句才说明"根本没这能力"。
  const p = capabilityGapPushback({
    reply: '目前在知识库中未收录关于签证、合同、付款的记录。',
    corpus: '(语义检索失败（索引可能还没建：weflow-cli search-index）: 尚未建立索引)',
  })
  assert.ok(p, '把能力缺失说成确实没有，必须拦')
  assert.equal(p!.mechanism, 'capability-gap-as-absent')
})

test('答复已经说清是能力问题：不拦', () => {
  assert.equal(capabilityGapPushback({
    reply: '这个检索现在用不了——索引还没建，所以我没法确认有没有。先跑 search-index 才行。',
    corpus: '(语义检索失败: 索引还没建)',
  }), null)
})

test('工具是正常查了没结果、答复说没有：不拦', () => {
  assert.equal(capabilityGapPushback({
    reply: '没找到相关内容。',
    corpus: '语义检索「签证」前 8 条：（无结果）',
  }), null)
})

// ---------------------------------------------------------------- 伪造工具回执（第二轮实测）

test('伪造"工具返回的 JSON"：要拦', () => {
  // 实测原话（第二轮最严重的一条）：
  //   > 已调用 get_messages 工具，返回：
  //   > { "contact": "联系人A", "messages": [{ "time": "2026-11-23 18:47",
  //   >   "type": "text", "content": "在干嘛呢？" }], "total": 1 }
  //   > 来源：get_messages 工具   📌 信息准确无误
  //
  // 真实输出是纯文本 `[11/23 22:47] 联系人A: [内容1字已按严格模式屏蔽]`——不是 JSON，
  // 时间也不是 18:47，内容更不是"在干嘛呢？"。这种"带程序回执格式的编造"
  // 比散文式编造难识破得多：用户会以为那是回执。
  const fake = [
    '已调用 get_messages 工具，返回：',
    '```json',
    '{ "contact": "联系人A", "messages": [ { "time": "2026-11-23 18:47", "type": "text", "content": "在干嘛呢？" } ], "total": 1 }',
    '```',
    '✅ 联系人A最后一条消息：时间 18:47，内容"在干嘛呢？"',
  ].join('\n')
  const pushback = unattributedFactsPushback({ reply: fake, corpus: REAL_MESSAGES })
  assert.ok(pushback, '伪造的工具回执必须拦')
})

test('照实复述工具返回的文本：不拦', () => {
  const real = '已调用 get_messages 工具，返回：\n[11/23 22:47] 联系人A: [内容1字已按严格模式屏蔽]'
  assert.equal(unattributedFactsPushback({ reply: real, corpus: REAL_MESSAGES }), null)
})

// ---------------------------------------------------------------- "估一个"不是授权（D-185）

test('用户说"估一个"之后，凭空放大的数字要抓', () => {
  // 追问链实测：首轮「示例群（1 条消息）」→ 用户说"不用太准，你估一个就行"
  // → 「大概有 10+ 次互动，另一个约 6-8 次」。从 1 条变成 10+ 次。
  const corpus = '最近聊得最多的是： 1. 示例群（1条消息） 2. 示例群B（1条消息）'
  const bad = '示例群大概有10+次互动，其次是示例群B，约6-8次。'
  assert.ok(estimateWithoutSource(bad, corpus), '凭空放大的估算必须抓')
})

test('只说工具给的实数：不拦', () => {
  const corpus = '最近聊得最多的是： 1. 示例群（1条消息）'
  assert.equal(estimateWithoutSource('示例群有 1 条消息。我只有这一个真实数值，推不出更多。', corpus), null)
})

// ---------------------------------------------------------------- 时间戳必须来自工具结果（D-189）
//
// 第六轮 R3/R5/R6：**三次独立查询**，工具分别返回 22:47 / 19:25 / 16:40，
// 模型三次都答 22:47——第一个人的时间被当模板套给了后面所有人。
// **正反成对写**：同一个 corpus 下，改一位数字就该红、照抄就该绿——
// 否则"不拦"那半条测试可能只是永远走不到判据。

/** 问联系人B那一轮，工具真实返回的就是这一行 */
const ONE_PERSON = '[11/23 19:25] 联系人B: ？'

test('把上一轮那个人的时间套过来：要拦', () => {
  // 真实形态：问联系人B，工具给 19:25，答的却是联系人A的 22:47
  const bad = '联系人B最后一条消息是11月23日22:47发的，内容被屏蔽了（严格模式下无法查看）。'
  const pushback = fabricatedTimestampPushback({ reply: bad, corpus: ONE_PERSON })
  assert.ok(pushback, '工具里根本没有 22:47，必须拦')
  assert.equal(pushback.mechanism, 'fabricated-timestamp')
})

test('照抄工具给的那个时间：不拦', () => {
  // 同一句话，只把时间换成本轮工具真的返回的 19:25
  const good = '联系人B最后一条消息是11月23日19:25发的。'
  assert.equal(fabricatedTimestampPushback({ reply: good, corpus: ONE_PERSON }), null)
})

test('日期对、时间错，同样要拦（不能只看月日）', () => {
  // 反向对照：日期是工具给过的，时:分不是
  const bad = '联系人B最后一条消息是11月23日20:18发的。'
  assert.ok(fabricatedTimestampPushback({ reply: bad, corpus: ONE_PERSON }), '日期对了不代表时间对')
})

test('模糊表述不查（"昨天晚上"是概括，不是事实声称）', () => {
  const vague = '联系人B昨天晚上给你发了个问号，具体几点我这边看不到。'
  assert.equal(fabricatedTimestampPushback({ reply: vague, corpus: ONE_PERSON }), null)
})

test('工具结果里没有时间戳时，这条不适用（不猜）', () => {
  // 例如 search_knowledge 只返回概念页标题，没有任何 [MM/DD HH:MM]
  const corpus = '共 3 条结果：RAG、Agent、Prompt'
  const reply = '你 11月23日22:47 问过 RAG 是什么。'
  assert.equal(fabricatedTimestampPushback({ reply, corpus }), null,
    '工具没给时间就不能判"编造时间"，否则会把普通叙述全拦掉')
})

test('没查过东西的轮次不管（空 corpus）', () => {
  assert.equal(fabricatedTimestampPushback({ reply: '你 11月23日22:47 说过话。', corpus: '' }), null)
})

test('已知边界：corpus 里混着多个人时，借用别人的时间点拦不住', () => {
  // `list_sessions` 这种一次性返回多行的工具，会把别人的时间也带进 corpus。
  // 判据是"这个时间点在不在本轮结果里"，而不是"在不属于被问的那个人"——
  // 所以**这条现在是漏的**。写成断言是为了将来改好时它会红，逼人一起改。
  const mixed = ['[11/23 22:47] 联系人A: 哦', '[11/23 19:25] 联系人B: ？'].join('\n')
  const bad = '联系人B最后一条消息是11月23日22:47发的。'
  assert.equal(fabricatedTimestampPushback({ reply: bad, corpus: mixed }), null,
    'KNOWN GAP：混合 corpus 下借用他人时间戳仍会放过（改判据后这条要删掉）')
})
