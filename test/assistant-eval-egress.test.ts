/**
 * 「评测里只许有模型一个出网出口」——这条防线自己的测试。
 *
 * `installStubs` 把 `globalThis.fetch` 换成"只放行 /chat/completions，其余一律抛"，
 * 目的是保证评测**不会偷偷把用户数据发到别处**（`EVAL_UNCOVERED` 里 fetch_article /
 * search_public / search_semantic 三个工具被判"跑不了"，理由正是这条）。
 *
 * 但这条防线此前**没有任何测试**：它要是被谁顺手放宽（比如放行所有 http 开头），
 * 后果是评测里悄悄多出几个出网口——而那三个工具恰好在爬微信文章与搜狗搜索，
 * 也就是说**用户的真实数据会被发出去**，而评测照常给出分数。
 *
 * 所以这里钉的是防线本身，不是工具：装桩之后
 *   ① 非模型地址必须抛；
 *   ② 模型地址必须放行（否则整套评测都跑不了）；
 *   ③ 出网工具在评测里必须拿到"被拒"而不是"我查不到"（诚实性）。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

const { installStubs } = await import('../src/services/assistantEval.js')
const { EVAL_CASES } = await import('../src/services/assistantEval.js')

/** 取一条最普通的用例做载体（这里只关心桩，不关心它问了什么） */
const carrier = EVAL_CASES.find(c => c.id === 'sessions')!

test('装桩之后：非模型出网一律被拒，模型地址放行', async () => {
  const saved = globalThis.fetch
  const restore = await installStubs(carrier)
  try {
    // ① 三个出网工具用的都是这些域，评测里必须走不通
    for (const url of [
      'https://mp.weixin.qq.com/s/abcdef',
      'https://weixin.sogou.com/weixin?type=2&query=x',
      'https://example.com/anything',
    ]) {
      await assert.rejects(
        () => (globalThis.fetch as any)(url, {}),
        /不该有模型以外的出网/,
        `${url} 应当被评测器拒绝——放行它就意味着评测会把数据发出去`,
      )
    }
    // ② 模型那一个必须放行，否则整套评测跑不了。
    //    **不真联网**：把上一层 fetch 换成一个记录调用的假函数，再装桩——
    //    这样既证明"没被第二个出口那条拒掉"，又不会真的发请求（评测器会拿它去问模型）。
    const seen: string[] = []
    globalThis.fetch = (async (url: any) => {
      seen.push(String(url))
      return new Response('{"ok":true}', { status: 200 })
    }) as any
    await (globalThis.fetch as any)('https://api.deepseek.com/v1/chat/completions', {
      method: 'POST', body: '{}',
    })
    assert.deepEqual(seen, ['https://api.deepseek.com/v1/chat/completions'],
      '模型地址要被放行到上一层 fetch（没被拒）')
  } finally {
    globalThis.fetch = saved
    restore()
  }
})

test('还原之后：原始 fetch 回到原位（桩不许漏给下一条用例）', async () => {
  const marker = async () => new Response('original')
  const saved = globalThis.fetch
  globalThis.fetch = marker as any
  const restore = await installStubs(carrier)
  try {
    assert.notEqual(globalThis.fetch, marker, '装桩期间应当被替换')
  } finally {
    restore()
  }
  assert.equal(globalThis.fetch, marker, '还原必须装回**上一层**的 fetch，不是某个固定实现')
  globalThis.fetch = saved
})
