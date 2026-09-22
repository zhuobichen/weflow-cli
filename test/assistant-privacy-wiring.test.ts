/**
 * 隐私判据**接在配置上的那一段**：引擎选择 → 是否本地 → 是否脱敏，以及证据复盘的出网闸。
 *
 * 已有的隐私测试是拿**显式参数**调 `redactText(text, mode, localInference)`，所以"函数本身
 * 对不对"有人管；但"生产里那个 localInference 是从哪个配置项推出来的"没人管 ——
 * `PrivacyGate.isLocalInference()` 读的是 `aiEngine`，`engineConfig()` 决定请求发去哪。
 * 这一段错了不会报错：脱敏会静默失效（数据照样出境），或者反过来，本地推理被当云端而白打码。
 *
 * `reviewEvidence` 那道闸也一样：默认拒绝把聊天正文发云端，只有本地模型或显式 `--allow-cloud`
 * 才放行 —— 这条此前没有任何测试。
 *
 * 打桩 `configService.get` 与 `callLLM`；不联网、不碰真实家目录。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const HOME = mkdtempSync(join(tmpdir(), 'weflow-assistant-privacy-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME

const { AssistantService } = await import('../src/services/assistantService.js')
const { configService } = await import('../src/services/configService.js')
const { privacyGate } = await import('../src/services/assistantPrivacy.js')

const realGet = configService.get.bind(configService)

function setConfig(overrides: Record<string, string>): void {
  ;(configService as any).get = (key: string) => (key in overrides ? overrides[key] : realGet(key))
}

function service(config: Record<string, string>) {
  setConfig(config)
  const svc: any = new AssistantService()
  const sent: any[][] = []
  svc.callLLM = async (messages: any[]) => {
    sent.push(messages)
    return { choices: [{ message: { content: '## 疑似争议线索\n信息不足' } }] }
  }
  return { svc, sent }
}

const MESSAGES = [{
  localId: 1,
  createTime: 1758000000,
  isSend: false,
  senderUsername: '甲',
  content: '订单号是 13800138000，合同在 https://example.com/doc',
}] as any

test.after(() => { ;(configService as any).get = realGet })

// ------------------------------------------------------- 引擎 → 是否本地

test('只有本地引擎算本地推理', () => {
  for (const engine of ['ollama', 'lmstudio', 'local']) {
    setConfig({ aiEngine: engine })
    assert.equal(privacyGate.isLocalInference(), true, engine)
  }
  for (const engine of ['deepseek', '', 'openai-compatible']) {
    setConfig({ aiEngine: engine })
    assert.equal(privacyGate.isLocalInference(), false, engine)
  }
})

test('本地推理不脱敏，云端推理脱敏 —— 差别就在这一个值上', () => {
  const raw = '电话 13800138000'

  setConfig({ aiEngine: 'ollama' })
  assert.deepEqual(privacyGate.redact(raw), { safe: raw, redactions: 0 }, '数据不出机器就不用打码')

  setConfig({ aiEngine: 'deepseek', assistantPrivacy: 'balanced' })
  const cloud = privacyGate.redact(raw)
  assert.equal(cloud.redactions, 1)
  assert.match(cloud.safe, /\[电话\]/)
  assert.doesNotMatch(cloud.safe, /13800138000/)
})

test('自定义端点仍然算云端：脱敏不能因为"换了个中转站"就停', () => {
  setConfig({ aiEngine: 'deepseek', aiBaseUrl: 'https://relay.example/v1', assistantPrivacy: 'balanced' })
  assert.equal(privacyGate.isLocalInference(), false)
  assert.equal(privacyGate.redact('电话 13800138000').redactions, 1)
})

// ------------------------------------------------------- 请求发去哪

test('engineConfig：本地引擎指向本机端口，密钥为空', () => {
  assert.deepEqual(service({ aiEngine: 'ollama', localModel: 'qwen2.5' }).svc.engineConfig(),
    { url: 'http://localhost:11434/v1', model: 'qwen2.5', key: null, local: true })

  assert.equal(service({ aiEngine: 'lmstudio' }).svc.engineConfig().url, 'http://localhost:1234/v1')
})

test('engineConfig：自定义端点优先于默认 DeepSeek，且去掉结尾斜杠', () => {
  const cfg = { aiEngine: 'deepseek', aiBaseUrl: 'https://relay.example/v1/', aiModel: 'my-model', deepseekApiKey: 'sk-x' }
  assert.deepEqual(service(cfg).svc.engineConfig(),
    { url: 'https://relay.example/v1', model: 'my-model', key: 'sk-x', local: false })
})

test('engineConfig：什么都没配时回默认端点', () => {
  // 没配密钥时 `key` 是配置里的默认空串（不是 null）——只有本地引擎那条路才显式给 null。
  // 两者都判 falsy，所以请求不会带上空的 Authorization。
  assert.deepEqual(service({ aiEngine: 'deepseek' }).svc.engineConfig(),
    { url: 'https://api.deepseek.com/v1', model: 'deepseek-chat', key: '', local: false })
})

// ------------------------------------------------------- 证据复盘的出网闸

test('证据复盘默认拒绝把聊天正文发云端，而且一个请求都不发', async () => {
  const { svc, sent } = service({ aiEngine: 'deepseek', assistantPrivacy: 'strict' })

  await assert.rejects(() => svc.reviewEvidence('甲', MESSAGES, false),
    /默认禁止将聊天正文发送到云端/)
  assert.deepEqual(sent, [], '被拒绝就不该产生任何模型调用')
})

test('显式 allowCloud 才放行，且发出去的是脱敏后的文本', async () => {
  const { svc, sent } = service({ aiEngine: 'deepseek', assistantPrivacy: 'strict' })

  const result = await svc.reviewEvidence('甲', MESSAGES, true)

  assert.equal(result.localInference, false)
  assert.match(result.text, /疑似争议线索/)
  const transcript = sent[0][1].content as string
  assert.doesNotMatch(transcript, /13800138000/, '严格模式下正文根本不该出境')
  assert.match(transcript, /聊天正文已按严格隐私模式屏蔽/)
  assert.doesNotMatch(transcript, /甲/, '会话名与发送方同样要按严格模式屏蔽')
})

test('本地推理不需要 allowCloud，而且正文原样保留（这是本地推理的意义）', async () => {
  const { svc, sent } = service({ aiEngine: 'ollama', assistantPrivacy: 'strict' })

  const result = await svc.reviewEvidence('甲', MESSAGES, false)

  assert.equal(result.localInference, true)
  const transcript = sent[0][1].content as string
  assert.match(transcript, /13800138000/, '不出机器就不该打码，否则本地模型也没法用')
})

test('脱敏计数会报出来，方便事后知道这一份走了哪种处理', async () => {
  const { svc } = service({ aiEngine: 'deepseek', assistantPrivacy: 'balanced' })

  const result = await svc.reviewEvidence('甲', MESSAGES, true)

  assert.ok(result.redactions >= 1, `balanced 模式下应至少打码一处，实际 ${result.redactions}`)
})
