/**
 * 微信长轮询的两件事：**失败怎么分类**与**失败之后怎么对待**。
 *
 * 这块此前没有任何测试，而它的坏法全是静默的（2026-09-26 对着厂商实现核对时发现）：
 * - 本仓查的是 `-1 || 401`，而**厂商实现里 `-14` 才是 token 失效**
 *   （`third-party/WeKnora/internal/im/wechat/longpoll.go:151`）——查错码不会报错，
 *   只会把"过期"当普通错误，于是每 5 秒重试一次、永远重试下去；
 * - 重试间隔写死 5000ms，没有退避：日志被刷成噪音，而噪音等于没说；
 * - 消息回调写成 `try { cb(msg) } catch {}`：助手处理消息时抛的异常**全部消失**，
 *   用户发了消息、助手什么都没发生、日志里一个字都没有。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  POLL_BACKOFF_MS,
  WechatMessageService,
  classifyPollResult,
  nextPollDelay,
} from '../src/services/wechatMessageService.js'
import { AssistantService } from '../src/services/assistantService.js'

test('失败分类：-14 是 token 失效，不是普通错误', () => {
  // 这一条就是对着厂商实现定下来的事实
  assert.equal(classifyPollResult({ ret: 0, errcode: -14 }), 'token-expired')
  assert.equal(classifyPollResult({ errcode: -14 }), 'token-expired')
  assert.equal(classifyPollResult({ ret: 0, errcode: 0 }, 401), 'token-expired',
    'HTTP 401 同样是"重试不会成功"')
})

test('失败分类：正常与可重试分得开', () => {
  // 长轮询的空响应会带 0 或干脆没有这两个字段——那是**正常**，不许当成错误
  for (const ok of [{}, { ret: 0 }, { errcode: 0 }, { ret: 0, errcode: 0 }, null, undefined]) {
    assert.equal(classifyPollResult(ok as any), 'ok', JSON.stringify(ok))
  }
  // 其余一律可重试（网络抖动、语义不明的码）
  assert.equal(classifyPollResult({ ret: -1 }), 'retryable')
  assert.equal(classifyPollResult({ errcode: -1 }), 'retryable',
    '-1 不是厂商定义的过期码：**提示可以，但不能据此放弃通道**')
  assert.equal(classifyPollResult({ ret: 100 }), 'retryable')
})

test('重试间隔：翻倍、封顶 30 秒、首次 1 秒', () => {
  assert.equal(nextPollDelay(1), 1000)
  assert.equal(nextPollDelay(2), 2000)
  assert.equal(nextPollDelay(4), 8000)
  assert.equal(nextPollDelay(99), 30000, '再失败也不许无限增长')
  assert.equal(nextPollDelay(0), POLL_BACKOFF_MS[0], '传 0 也当成第一次')
})

test('长轮询实况：过期要说出来一次、状态要如实、异常不许被吞', async () => {
  const svc: any = new WechatMessageService({})
  const logged: string[] = []
  const realError = console.error
  console.error = (...args: any[]) => { logged.push(args.map(String).join(' ')) }

  let round = 0
  svc.client = {
    requestJson: async () => {
      round += 1
      if (round === 1) return { ret: 0, errcode: -14 }        // 服务端说 token 失效
      if (round === 2) return { msgs: [{ from_user_id: 'u@im.wechat' }] }  // 通了一轮 + 一条消息
      svc.shutdownFlag = true
      return {}
    },
  }
  svc.onMessage(() => { throw new Error('boom') })

  try {
    await svc.startPolling()
  } finally {
    console.error = realError
  }

  assert.equal(round, 3, '循环应当跑到 stub 让它停为止')
  const expiry = logged.filter(line => line.includes('token 已失效'))
  assert.equal(expiry.length, 1, `失效只喊一次（喊成噪音等于没说），实际 ${expiry.length} 次`)
  assert.match(expiry[0], /login-wechat/, '要给出怎么办，而不只是"出错了"')
  assert.ok(logged.some(line => line.includes('消息回调异常') && line.includes('boom')),
    '回调里抛的异常必须被记下来——吞掉它等于消息静默丢失')
  assert.equal(svc.isTokenExpired(), false, '第 2 轮通了，状态要恢复')
})

test('通道实况：token 被判失效时，面板看到的那一位要说 false', () => {
  // `isChannelActive` 原来是 `svc !== null`，也就是"启动时配了 token"——token 死了它照样报 true，
  // 于是面板显示"微信 + 本机"而通道其实已经没了。这条钉住新的语义。
  const svc: any = new AssistantService()
  const fakeChannel = {
    onMessage() {}, startPolling: async () => {}, sendText: async () => true, stop() {},
    isTokenExpired: () => true,
  }
  svc.svc = fakeChannel
  assert.equal(svc.isChannelActive(), false, '过期了还在报"微信 + 本机"，就是把死通道报成活的')
  fakeChannel.isTokenExpired = () => false
  assert.equal(svc.isChannelActive(), true)
  // 没这个方法的老通道当成没过期：它是可选能力，不该让别的实现挂掉
  delete (fakeChannel as any).isTokenExpired
  assert.equal(svc.isChannelActive(), true)
})
