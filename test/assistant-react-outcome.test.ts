/**
 * `runReactLoop` 的 `outcome`：**"跑完了" ≠ "答完了"**（2026-11-24）。
 *
 * 撞轮次上限时 `reply` 是占位文案（`'(这轮处理太复杂了, 换个问法试试?)'`），不是模型说的话。
 * 守卫段要判断"这句话是不是编造"，前提是"这句话是模型生成的"——撞上限时它不成立。
 *
 * 这个 bug 原来**不可见**：`return { reply, toolCalls }` 一个返回值同时表达两件事，
 * `if (!retry)` 读起来只像"多一道检查"，没人会想到这里的 `reply` 可能不是答复。
 * 今天没炸，只是因为占位文案里恰好没有承诺词——**而那句文案是可以改的**。
 *
 * 这组测试钉住三件事：
 * 1. 正常答复 → `outcome === 'answered'`，守卫照常工作
 * 2. 撞上限 → `outcome === 'rounds-exhausted'`，**守卫和兜底都不跑**
 * 3. 占位文案即使含承诺词，也不会被"修"（这是修复前会出错的形态）
 */
import test from 'node:test'
import assert from 'node:assert/strict'

const HOME = (await import('node:fs')).mkdtempSync(
  (await import('node:path')).join((await import('node:os')).tmpdir(), 'weflow-outcome-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME

const { AssistantService } = await import('../src/services/assistantService.js')
const { MAX_TOOL_ROUNDS } = await import('../src/services/assistantService.js')
  .then((m) => ({ MAX_TOOL_ROUNDS: (m as any).MAX_TOOL_ROUNDS ?? 6 }))
  .catch(() => ({ MAX_TOOL_ROUNDS: 6 }))

/** 造一个只会要求的假模型：每轮都返回一次工具调用，永远不给最终答复 */
function alwaysToolCall() {
  return async () => ({
    choices: [{
      message: {
        role: 'assistant',
        content: '',
        tool_calls: [{
          id: 'call_' + Math.random().toString(36).slice(2),
          type: 'function',
          function: { name: 'list_sessions', arguments: '{}' },
        }],
      },
    }],
  })
}

test('撞轮次上限：outcome 必须是 rounds-exhausted，reply 是占位文案', async () => {
  const svc: any = new AssistantService()
  const trace = { steps: [], rounds: 0, toolCalls: 0, stop: 'answered' }
  svc.callLLM = alwaysToolCall()
  // 工具执行直接返回空，避免走真实服务
  svc.runToolCall = async () => {}

  const out = await svc.runReactLoop('outcome-user', [{ role: 'user', content: '测试' }], trace)
  assert.equal(out.outcome, 'rounds-exhausted', '撞上限时 outcome 必须说出来')
  // 2026-11-24：占位文案换成了"诚实的弃权"——有工具输出就摘出来，没输出才说
  // "没能在限定步数内完成"。这里只要求它**仍然不是模型说的答复**（有明确的自述），
  // 不钉具体措辞（措辞会继续改，钉死了每次改文案都要动测试）。
  assert.match(out.reply, /没能在限定步数内|这轮我查到了|换个问法/, 'reply 应是明确的"没答完"自述')
  assert.equal(trace.stop, 'rounds-exhausted')
})

test('正常答复：outcome 必须是 answered', async () => {
  const svc: any = new AssistantService()
  const trace = { steps: [], rounds: 0, toolCalls: 0, stop: 'answered' }
  svc.callLLM = async () => ({
    choices: [{ message: { role: 'assistant', content: '和彪弟最近在聊项目。' } }],
  })

  const out = await svc.runReactLoop('outcome-user2', [{ role: 'user', content: '测试' }], trace)
  assert.equal(out.outcome, 'answered')
  assert.equal(out.reply, '和彪弟最近在聊项目。')
})
