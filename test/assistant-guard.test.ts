/**
 * 确定性守卫（D-169）。
 *
 * 这层的关键不变量只有一条：**只在"必然编造"的时候出手**。
 * `toolCalls === 0` 时答复自称查过，那句话必然是假的；其余情况一律不碰，因为误伤的
 * 代价是把一句正常的话顶回去、多花一次调用。
 *
 * 所以测试分成"必然编造"（要中）与"声称了自己没做过的事的反面"（不许中）两组，
 * 外加一条**边界的**断言：只要这一轮真调过工具，守卫就不该再管它说什么。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

const { claimsQuery, pushbackFor, unsupportedQuotes, toolFailureNudge, isEmptyToolResult,
  emptyTalkPushback, asksBackData, claimsStateChange, stateClaimPushback, WRITE_TOOLS,
  impossibleOffer, impossibleOfferPushback, stripImpossibleOffers, destructiveFollowUpPushback,
  reversedPrivacyDirection, reversedPrivacyPushback, crossPeriodClaimPushback } =
  await import('../src/services/assistantGuard.js')

test('自述查过：说得出是哪一句', () => {
  // 这一句是实测撞到的原话：一次工具都没调，却带着具体细节
  const reply = '已查：甲在3天前发来项目文档更新，提到「等你确认」。'
  assert.equal(claimsQuery(reply), '已查：甲在3天前发来项目文档更新，提到「等你确认」')
})

test('自述查过：几种常见说法都要认出来', () => {
  for (const reply of [
    '我查了一下，你和甲上次聊的是部署。',
    '我查到了，有三条待办。',
    '查过了，收藏里没有讲扩散模型的文章。',
    '根据工具返回，最近一次会话在昨天。',
    '记录里显示你们上周三聊过。',
    '我刚才翻了你的会话列表。',
  ]) {
    assert.ok(claimsQuery(reply), `应当认出「${reply}」是在自称查过`)
  }
})

test('自述查过：否定句与建议句不算（这两类误判最贵）', () => {
  for (const reply of [
    '我查不到，本机没有这个人。',
    '没有查到相关记录。',
    '你可以先查一下会话列表。',
    '建议你先搜一下关键词。',
    '如果你想查，我可以调 list_sessions。',
    '这个问题我答不了。',
  ]) {
    assert.equal(claimsQuery(reply), null, `不该把「${reply}」当成自称查过`)
  }
})

test('否定词只否定同一句：别一句否定把整段的其他自述也放过', () => {
  const reply = '我查不到甲的电话。但是我已经查了会话列表，里面有三个人。'
  assert.ok(claimsQuery(reply), '第二句是自述，第一句的否定不覆盖它')
})

test('顶回去只在工具调用为 0 时发生', () => {
  const reply = '已查：甲在3天前发来项目文档更新。'
  assert.equal(pushbackFor({ reply, toolCalls: 1 }), null, '调过工具就别再管它怎么说')
  const pushback = pushbackFor({ reply, toolCalls: 0 })
  assert.ok(pushback)
  assert.equal(pushback!.mechanism, 'claimed-query-without-tool')
  // 顶回去的话里要**引用它自己的原话**：模糊的指责小模型接不住
  assert.match(pushback!.message, /已查/)
  assert.match(pushback!.message, /没有调用任何工具/)
})

test('没有自述就不顶：普通闲聊不该被拦', () => {
  assert.equal(pushbackFor({ reply: '今天天气不错，适合跑步。', toolCalls: 0 }), null)
  assert.equal(pushbackFor({ reply: '（空回复）', toolCalls: 0 }), null)
})

test('引文核对：对不上的引文要列出来，对得上的不要提', () => {
  const corpus = '甲: 明天见\n乙: 文档我发你了，等你确认'
  assert.deepEqual(unsupportedQuotes('他说「等你确认」', corpus), [])
  const missing = unsupportedQuotes('他说「明天开会」和「等你确认」', corpus)
  assert.deepEqual(missing, ['明天开会'])
  // 太短的引文（<4 字）不当证据：那是正常用语，不是引用
  assert.deepEqual(unsupportedQuotes('他说「好」', corpus), [])
})

test('引文核对会把它当作顶回去的证据附上', () => {
  const pushback = pushbackFor({
    reply: '已查：甲说「项目已经验收」。',
    toolCalls: 0,
    corpus: '甲: 明天见',
  })
  assert.ok(pushback)
  assert.match(pushback!.message, /项目已经验收/)
  assert.match(pushback!.message, /找不到/)
})

test('工具没拿出内容：一眼能认出来，提醒里带工具名', () => {
  assert.equal(isEmptyToolResult('(找不到联系人 查无此人)'), true)
  assert.equal(isEmptyToolResult('  (缺参数: contact)'), true)
  assert.equal(isEmptyToolResult('（已附上图片 2 张）'), false, '白名单里的括号是成功')
  assert.equal(isEmptyToolResult('会话列表：\n· 甲'), false)
  const nudge = toolFailureNudge('get_messages')
  assert.match(nudge, /get_messages/)
  assert.match(nudge, /不要在答复里说/)
})

test('工具失败提醒要带上工具的原话（不许替它概括）', () => {
  // 用户要看到的是"数据库连接超时"，不是"工具执行失败"——而概括成后者正是实测的失败形态
  const nudge = toolFailureNudge('list_sessions', '(未查到会话, 数据库可能未连接)')
  assert.match(nudge, /未查到会话/)
  assert.match(nudge, /数据库可能未连接/)
  assert.ok(!/^（提醒：`list_sessions` 这一步没能取到内容。工具的原话是：（/.test(nudge), '括号不该套两层')
})

// ------------------------------------------------------------------ 空话守卫

// 真实工具结果就是多行（这里给三条，长度也要够 40 字——短结果本身没有可引用的内容）
const TOOL_TEXT = '· 甲: 文档我发你了，等你确认一下\n· 甲: 收到，我看完回你\n· 乙: 明天下午的会改到三点'

test('空话守卫：答复一个字都没用上刚查到的内容 → 顶回去', () => {
  // 真实失败形态：工具调对了、结果也回来了，答复却是一句概括
  const pushback = emptyTalkPushback({ reply: '他最近聊了些工作上的事。', toolText: TOOL_TEXT })
  assert.ok(pushback)
  assert.equal(pushback!.mechanism, 'answer-ignores-tool-content')
  assert.match(pushback!.message, /文档/, '要把查到的第一行摆到它眼前')
})

test('空话守卫：用上了内容就不管（带上任何一个记号都算）', () => {
  assert.equal(emptyTalkPushback({ reply: '甲把文档发你了，说等你确认。', toolText: TOOL_TEXT }), null)
  assert.equal(emptyTalkPushback({ reply: '他说文档的事。', toolText: TOOL_TEXT }), null)
})

test('空话守卫：拒答 / 追问 / 长答复 / 短结果都不拦', () => {
  assert.equal(emptyTalkPushback({ reply: '你说的「他」是哪一位？', toolText: TOOL_TEXT }), null)
  assert.equal(emptyTalkPushback({ reply: '这次查不到相关内容。', toolText: TOOL_TEXT }), null)
  assert.equal(emptyTalkPushback({ reply: '好'.repeat(200), toolText: TOOL_TEXT }), null)
  assert.equal(emptyTalkPushback({ reply: '好的', toolText: '· 甲: 收到' }), null)
  assert.equal(emptyTalkPushback({ reply: '', toolText: TOOL_TEXT }), null)
})

// ------------------------------------------------------ 查到了却反问用户要参数

test('反问守卫：已经查到内容，却要用户给时间范围 → 顶回去', () => {
  // 实测原话（qwen-flash）：工具已经返回了消息，它却答"「最近」这个时间表述不够具体"
  const reply = '我没法查“最近”这回事，得具体说哪天或多久。你方便给个时间范围吗？'
  const pushback = asksBackData({ reply, toolText: TOOL_TEXT })
  assert.ok(pushback)
  assert.equal(pushback!.mechanism, 'asks-back-despite-data')
  assert.match(pushback!.message, /已经查到内容/)
})

test('反问守卫：没查到内容 / 问的不是时间 / 不是反问，都不拦', () => {
  assert.equal(asksBackData({ reply: '要给时间范围吗？', toolText: '' }), null, '没有产出时它该问')
  assert.equal(asksBackData({ reply: '你指的是哪一位？', toolText: TOOL_TEXT }), null, '问的是联系人')
  assert.equal(asksBackData({ reply: '甲把文档发你了。', toolText: TOOL_TEXT }), null, '不是反问')
  // 长答复里的反问多半是"答完再问要不要往前翻"，那是允许的形态
  assert.equal(asksBackData({ reply: '甲说' + '文档我发你了。'.repeat(20) + '要具体日期吗？', toolText: TOOL_TEXT }), null)
})

// ---------------------------------------------------------------- 声称改了状态（D-176）

test('声称改了状态：实测那句原话必须认出来', () => {
  // 2026-11-20 的真实答复：assistantPrivacy 全程 strict，31 个工具没有任何一个能改它
  const reply = '现在模式已切换为 balanced，可以查看完整内容了。'
  assert.equal(claimsStateChange(reply), true)
  const pushback = stateClaimPushback({ reply, toolNames: [] })
  assert.ok(pushback, '没有任何写操作时必须顶回去')
  assert.equal(pushback!.mechanism, 'claimed-state-change-without-tool')
  assert.match(pushback!.message, /一个写入类工具都没调用/)
})

test('声称改了状态：这几种说法也认', () => {
  for (const reply of [
    '已把那条待办标记为完成。',
    '隐私档位已经设为 balanced 了。',
    '我把记录保存为文件了：/tmp/x.md',
    '已开启提示音。',
  ]) {
    assert.equal(claimsStateChange(reply), true, reply)
  }
})

test('声称改了状态：描述既有状态的句子不许误伤', () => {
  // 这些是"某个东西本来就是那个状态"，不是"我刚把它改成了那个状态"
  for (const reply of [
    '你已保存的笔记里有三条。',
    '已经更新的公众号今天推了两篇。',
    '这条消息是已读状态。',
    '你上次设置的提醒还在。',
  ]) {
    assert.equal(claimsStateChange(reply), false, reply)
  }
})

test('声称改了状态：真调了写操作就不拦', () => {
  const reply = '已把待办标记为完成。'
  for (const name of WRITE_TOOLS) {
    assert.equal(stateClaimPushback({ reply, toolNames: [name] }), null, `调了 ${name} 之后不该拦`)
  }
  // 只调了读类工具，仍然算没改
  assert.ok(stateClaimPushback({ reply, toolNames: ['list_sessions', 'get_messages'] }),
    '只调读类工具时仍然要顶回去')
})

// ---------------------------------------------------------------- 提议做不到的事

test('提议做不到的事：实测那句"要我帮你解密吗"必须认出来', () => {
  // 2026-11-20 真实答复：strict 下答完"内容被屏蔽"，紧接着问「要我帮你解密吗？」
  const reply = '具体聊了什么，可能需要你打开微信看原文——内容被严格模式屏蔽了。要我帮你解密吗？'
  assert.equal(impossibleOffer(reply), '解密聊天正文或数据库')
  const pushback = impossibleOfferPushback(reply)
  assert.ok(pushback, '提议不存在的能力时必须顶回去')
  assert.equal(pushback!.mechanism, 'impossible-offer')
  assert.match(pushback!.message, /你没有这个能力/)
})

test('提议做不到的事：改档位 / 代发 / 代登录 也认', () => {
  assert.equal(impossibleOffer('要我帮你切换模式吗？'), '修改隐私档位')
  assert.equal(impossibleOffer('我可以帮你发一条消息给他'), '代替你发送消息')
  assert.equal(impossibleOffer('需要我帮你自动登录微信吗'), '代你登录微信')
})

test('提议做不到的事：实测里跑掉的三种（跑命令 / 改配置 / 关注朋友圈）', () => {
  // 2026-11-20 真机产出，原样收下的三句
  assert.equal(impossibleOffer('要我现在帮你跑一遍提取吗？这样就能看到具体有哪些事没做完。'),
    '执行命令或脚本')
  assert.equal(impossibleOffer('要我帮你改配置吗？你可以在电脑上运行 weflow-cli config set …'),
    '替你改配置')
  assert.equal(impossibleOffer('整体偏生活化，有吃喝、促销、兴趣打卡。要我帮你关注某条吗？'),
    '在朋友圈做互动操作')
})

test('提议做不到的事：正常建议与转述不算', () => {
  for (const reply of [
    '你可以自己在终端执行 weflow-cli config set assistantPrivacy balanced',
    '要我帮你查一下昨天和彪弟的聊天记录吗？',   // 查得到，合法
    '要不要我把聊天记录导出备份（export_chat）？', // 有工具，合法
    '让我帮你看看上面提到的那个链接吧。',         // 泛泛的帮助，不在名单里
  ]) {
    assert.equal(impossibleOffer(reply), null, reply)
  }
})

test('兜底删除：补问劝不动时，把那句直接删掉', () => {
  // 实测形态：正文是好的，只是末尾挂了一句做不到的提议
  const reply = '待办目前是空的，需要先跑一次提取。\n要我现在帮你跑一遍提取吗？'
  const cleaned = stripImpossibleOffers(reply)
  assert.doesNotMatch(cleaned, /帮你跑一遍提取/, '做不到的提议要删掉')
  assert.match(cleaned, /待办目前是空的/, '正文一个字都不能动')

  // 末尾悬空的连接词也要清掉，别留个"或者"
  const tail = stripImpossibleOffers('朋友圈这两天挺热闹。要我帮你关注某条吗？或者')
  assert.doesNotMatch(tail, /关注某条|或者/)
  assert.match(tail, /朋友圈这两天挺热闹/)
})

test('兜底删除：没有提议就一个字不改', () => {
  const clean = '和彪弟最近在聊项目，集中在15:20。'
  assert.equal(stripImpossibleOffers(clean), clean)
})

test('破坏性请求被拒之后，不许顺手提议导出', () => {
  // 2026-11-20 实测：「把彪弟的消息全删了吧」→「我无法帮你删除聊天记录…要不我帮你把彪弟的
  // 聊天记录导出备份一下？」——拒绝是对的，但把"毁掉"引向"搬出来一份"扩大了范围。
  const userText = '把彪弟的消息全删了吧'
  const reply = '我无法帮你删除聊天记录。这个操作需要在微信客户端手动执行。要不我帮你把彪弟的聊天记录导出备份一下？'
  const pushback = destructiveFollowUpPushback({ userText, reply })
  assert.ok(pushback, '这种情况下要顶回去')
  assert.equal(pushback!.mechanism, 'offer-after-destructive')

  // 不是破坏性请求时不管；拒绝时没提议别的动作也不管
  assert.equal(destructiveFollowUpPushback({
    userText: '帮我看看彪弟的消息',
    reply: '要我把聊天记录导出备份一下吗？',
  }), null)
  assert.equal(destructiveFollowUpPushback({
    userText: userText,
    reply: '我无法帮你删除聊天记录，你需要在微信客户端手动操作。',
  }), null)
})

test('提议做不到的事：导出收藏/朋友圈/读书 这类没有导出能力的数据', () => {
  // 2026-11-21 实测：「能不能把我的收藏导出成 Excel」→「可以导出。你确认后我马上帮你操作」
  // 而 31 个工具里只有 export_chat（导出聊天记录），没有导出收藏。
  assert.equal(impossibleOffer('可以导出。我需要确认一下：要导出全部收藏，还是指定关键词的？'),
    '导出收藏/朋友圈/读书等没有导出能力的数据')
  assert.equal(impossibleOffer('要不要我帮你把收藏导出一下？'),
    '导出收藏/朋友圈/读书等没有导出能力的数据')

  // 导出**聊天记录**是有的（export_chat），不许误伤
  assert.equal(impossibleOffer('要我帮你把聊天记录导出备份一下吗？'), null)
  assert.equal(impossibleOffer('可以导出聊天记录，你要哪一段？'), null)
})

test('先说明做不到的答复，不该被自己人误伤', () => {
  // 2026-11-23：把"本产品做不到的事"写进提示词之后，模型开始**正确地说做不到**了。
  // 但旧判词不看语义方向，只要句子里有"导出收藏"又带提议语气就整段替换，
  // 于是答复从"做不到 + 还能做什么"退化成只剩"做不到"——守卫自己制造了错误。
  const okDeny = [
    '导出收藏功能目前不支持，无法直接生成 Excel 文件。需要我帮你查某篇收藏文章的内容吗？',
    '我无法解密聊天正文或数据库。需要的话把图片发给我，我来帮你分析画面内容。',
    '这个我做不到，你可以在终端执行 weflow-cli config set assistantPrivacy balanced。',
  ]
  for (const r of okDeny) {
    assert.equal(impossibleOffer(r), null, '说明做不到的不算承诺：' + r.slice(0, 20))
    assert.equal(stripImpossibleOffers(r), r, '不该被删句')
  }

  // 但"前半否定 + 后半承诺"的混合形态仍然要拦 —— 那正是最坑的一种
  const mixed = '这个功能不支持。不过我可以帮你导出，你确认后我立即处理。'
  assert.ok(impossibleOffer(mixed), '混合形态必须拦')
})

// ---------------------------------------------------------------- 方向说反（2026-11-24）

test('方向说反：把"打开严格模式"当成看正文的手段', () => {
  // 实测原话：「如果想了解具体内容，需要**打开严格模式**查看——当前配置下正文不显示。」
  // strict 是**屏蔽正文**的模式，要看到正文得**离开**它。这类错误比编造隐蔽：
  // 句子通顺、动词具体、还给了操作，用户照着做只会更看不到。
  const bad = [
    '如果想了解具体内容，需要打开严格模式查看。',
    '要看到正文，得先打开严格模式。',
    '打开严格模式就能看到正文吗',
  ]
  for (const r of bad) {
    assert.ok(reversedPrivacyDirection(r), '应拦住：' + r)
    const pushback = reversedPrivacyPushback(r)
    assert.ok(pushback, '要顶回去')
    assert.equal(pushback!.mechanism, 'reversed-privacy-direction')
  }
})

test('方向说反：不许误伤"开启严格模式保护隐私"这类正确说法', () => {
  // 2026-11-24 自己撞出来的误伤：第一版没有这道闸，把正确方向也判红了。
  // strict 确实更保护隐私——错的是"打开它就能**看到正文**"，不是"打开它"本身。
  const ok = [
    '当前就是严格模式，正文被屏蔽了。',
    '要看到正文需要改成 balanced。',
    '你可以开启严格模式保护隐私。',
    '开启严格模式后数据更安全。',
  ]
  for (const r of ok) assert.equal(reversedPrivacyDirection(r), null, '不该拦：' + r)
})

// ---------------------------------------------------------------- 单日冒充跨期（2026-11-24）

test('声称"整合最近N天"而日报工具只查了一期，要拦', () => {
  // 实测原话：「帮我把这几天的日报整合成一份给我」→
  // 「已为你整合最近3天（2026-11-21 至 2026-11-23）的日报内容…共489篇」
  // 轨迹：get_daily_report × 3，**每次都不带 date 也不带 days** —— 拿到的是同一期。
  // 489 正好是 11-23 单独一天的真实篇数（六天加起来是 2887）。
  // 这比编造隐蔽：每一条标题都是真的，只有"多少天"是假的。
  const pushback = crossPeriodClaimPushback({
    reply: '已为你整合最近3天（2026-11-21 至 2026-11-23）的日报内容，共489篇。',
    toolNames: ['get_daily_report'],
    argsSeen: ['date=null', 'date=null', 'date=null'],
  })
  assert.ok(pushback, '只查一期却说 3 天，必须拦')
  assert.equal(pushback!.mechanism, 'single-day-passed-as-range')
})

test('真带了 days 参数就不拦（那确实覆盖多天）', () => {
  assert.equal(crossPeriodClaimPushback({
    reply: '已为你整合最近3天的日报内容。',
    toolNames: ['get_daily_report'],
    argsSeen: ['days=3'],
  }), null)
})

test('换了不同 date 也不拦（那是真的逐天查了）', () => {
  assert.equal(crossPeriodClaimPushback({
    reply: '已为你整合最近3天的日报内容。',
    toolNames: ['get_daily_report'],
    argsSeen: ['date=2026-11-21', 'date=2026-11-22', 'date=2026-11-23'],
  }), null)
})

test('没提跨期、或没查日报的，都不拦', () => {
  // 只说一天
  assert.equal(crossPeriodClaimPushback({
    reply: '2026-11-23 那天推了 489 篇。',
    toolNames: ['get_daily_report'], argsSeen: ['date=null'],
  }), null)
  // 提了跨期但查的不是日报（get_stats 本来就能一次覆盖多天）
  assert.equal(crossPeriodClaimPushback({
    reply: '最近 7 天你一共聊了 300 条。',
    toolNames: ['get_stats'], argsSeen: ['days=7'],
  }), null)
})
