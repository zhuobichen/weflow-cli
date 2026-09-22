/**
 * 快路径的路由判据（D-035）。
 *
 * `decideRoute` 的每一个出口都要**能回退**：判断层报错、概率不是数、置信度不够、能力名不在
 * 封闭表里——全部返回 `capability: null`。这不是容错洁癖：路由错时模型会拿着一份不相关的
 * 结果自信地作答，比慢一轮更坏，所以"拿不准"必须是回退，而不是挑一个最像的。
 *
 * 用注入的假判断层驱动，不联网。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

const {
  decideRoute, buildRouteRequest, FAST_ROUTE_CAPABILITIES, NO_CAPABILITY,
  MIN_CONFIDENCE, MIN_NEEDS_TOOL,
} = await import('../src/services/assistantRouter.js')

/** 造一份 decide.py 形状的返回 */
function answer(needsTool: unknown, choice: string | null, confidence: unknown, extra: any = {}) {
  return {
    success: true,
    model: 'jev-test-1.0',
    answers: {
      needs_local_data: needsTool === undefined ? {} : { noul: needsTool },
      capability: choice === null ? {} : { choice, confidence, probabilities: { [choice]: confidence } },
      ...extra,
    },
  }
}

const decide = (response: any) => decideRoute('帮我看看有什么待办', { runDecide: async () => response })

// ------------------------------------------------------------------ 问得对不对

test('问题里每个能力都写明「它假定怎么查」，且候选集是封闭的', () => {
  const request: any = buildRouteRequest('我在读什么书')
  const criteria = request.questions.capability.criteria

  assert.deepEqual(Object.keys(criteria).sort(),
    [...FAST_ROUTE_CAPABILITIES.map(c => c.name), NO_CAPABILITY].sort(),
    'criteria 的键集必须与能力表一致——两处各写一份，早晚有一份会漂')
  for (const capability of FAST_ROUTE_CAPABILITIES) {
    assert.equal(criteria[capability.name], capability.when)
  }
  assert.match(criteria[NO_CAPABILITY], /不需要查本机数据/)
})

test('state 只带用户这一句话，不带历史与本地数据', () => {
  const request: any = buildRouteRequest('朋友圈最近有啥')
  assert.equal(request.state, '用户发来的消息：朋友圈最近有啥')
})

test('问的是「要不要查本机数据」，而不是「要不要工具」', () => {
  const request: any = buildRouteRequest('你好')
  assert.equal(request.questions.needs_local_data.type, 'noul')
  assert.match(request.questions.needs_local_data.instructions, /本机保存的数据/)
})

// ------------------------------------------------------------------ 正常路由

test('概率过线时定下能力，并把工具与参数一起定下来', async () => {
  const decision = await decide(answer(0.93, 'todos_pending', 0.88))

  assert.equal(decision.capability?.name, 'todos_pending')
  assert.equal(decision.capability?.tool, 'get_todos')
  assert.deepEqual(decision.capability?.args, { status: 'pending' })
  assert.equal(decision.model, 'jev-test-1.0')
  assert.match(decision.reason, /todos_pending/)
})

test('每一次路由都带上服务我们的模型版本，方便事后对账', async () => {
  const decision = await decide(answer(0.9, 'get_stats', 0.9))
  assert.equal(decision.model, 'jev-test-1.0')
})

// ------------------------------------------------------------------ 该回退的

test('判断层说不需要查本机数据时不路由', async () => {
  const decision = await decide(answer(0.12, 'get_stats', 0.95))
  assert.equal(decision.capability, null)
  assert.match(decision.reason, /不需要查本机数据/)
})

test('边界：概率刚好等于阈值就算过线，低于一点就回退', async () => {
  const pass = await decide(answer(MIN_NEEDS_TOOL, 'get_stats', 0.9))
  assert.equal(pass.capability?.name, 'get_stats')

  const fail = await decide(answer(MIN_NEEDS_TOOL - 0.01, 'get_stats', 0.9))
  assert.equal(fail.capability, null)
})

test('判断层选 none 时不路由', async () => {
  const decision = await decide(answer(0.9, NO_CAPABILITY, 0.9))
  assert.equal(decision.capability, null)
})

test('置信度不足时回退，而不是挑一个最像的', async () => {
  const decision = await decide(answer(0.9, 'weread_shelf', MIN_CONFIDENCE - 0.01))
  assert.equal(decision.capability, null)
  assert.match(decision.reason, /置信度不足/)
})

test('能力名不在封闭表里时回退（契约变了不许猜）', async () => {
  const decision = await decide(answer(0.9, 'send_message_everywhere', 0.99))
  assert.equal(decision.capability, null)
  assert.match(decision.reason, /不在能力表里/)
})

test('概率不是数字时回退（noul 曾被当成布尔读错过，两个方向都要挡）', async () => {
  // `Number(true)` 是 1、`Number('')` 是 0：布尔会被读成"确定要"，空串会被读成"确定不要"。
  // 两种误读都不该发生——契约里它是概率浮点。
  for (const bogus of [true, false, '', null, {}, [], 'yes']) {
    const decision = await decide(answer(bogus, 'get_stats', 0.9))
    assert.equal(decision.capability, null, JSON.stringify(bogus))
  }
  // 像数字的字符串仍然认（JSON 之外的来路上会遇到）
  const decision = await decide(answer('0.91', 'get_stats', '0.88'))
  assert.equal(decision.capability?.name, 'get_stats')
})

test('判断层失败、超时、返回垃圾时都回退，而且不抛给调用方', async () => {
  const throwing = await decideRoute('x', { runDecide: async () => { throw new Error('connect ETIMEDOUT') } })
  assert.equal(throwing.capability, null)
  assert.match(throwing.reason, /判断层不可用/)
  assert.match(throwing.reason, /ETIMEDOUT/)

  const failed = await decide({ success: false, error: '鉴权失败' })
  assert.equal(failed.capability, null)
  assert.match(failed.reason, /判断层返回失败/)

  for (const junk of [null, {}, { success: true }, 'nonsense']) {
    const decision = await decide(junk)
    assert.equal(decision.capability, null, JSON.stringify(junk))
  }
})

test('回退时也报出它看到的概率，日志里能分辨"为什么没走快路径"', async () => {
  const decision = await decide(answer(0.3, NO_CAPABILITY, 0.2))
  assert.equal(decision.needsTool, 0.3)
  assert.equal(decision.capability, null)
})

test('永远不抛异常：这是常驻进程里的一条路径', async () => {
  const cases: any[] = [undefined, 42, [], { success: true, answers: { needs_local_data: { noul: 'NaN' } } }]
  for (const response of cases) {
    const decision = await decideRoute('随便什么', { runDecide: async () => response })
    assert.equal(typeof decision.capability, 'object', String(response))
    assert.equal(typeof decision.reason, 'string')
  }
})

// ------------------------------------------------------------ 能力表本身

test('能力表里没有需要自由文本参数的工具', () => {
  // 判断层只能从封闭集合里选，"联系人是谁""关键词是什么"它给不出来。硬凑参数就是
  // D-035 点名的失败模式：工具挑错 + 模型围着错结果自信作答。
  const freeTextTools = ['get_messages', 'search_favorites', 'read_favorite', 'search_knowledge', 'search_memory']
  const used = new Set(FAST_ROUTE_CAPABILITIES.map(c => c.tool))
  for (const tool of freeTextTools) {
    assert.equal(used.has(tool), false, `${tool} 需要自由文本参数，不该出现在快路径能力表里`)
  }
  assert.ok(used.has('get_todos'), '对照组：参数封闭的工具应该在表里')
})

test('每个能力的参数都是封闭集或空', () => {
  for (const capability of FAST_ROUTE_CAPABILITIES) {
    for (const [key, value] of Object.entries(capability.args)) {
      assert.ok(['mode', 'status'].includes(key), `${capability.name} 的参数 ${key} 不是封闭参数`)
      assert.equal(typeof value, 'string')
    }
  }
})
