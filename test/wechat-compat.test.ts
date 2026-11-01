/**
 * 微信版本兼容（D-130）：验证版本的单一事实源与纯比较。
 *
 * 比较函数是判定的**唯一实现**（check 的 json/report/人类输出、capabilities 全走它），
 * 这里钉它的四种判定与边界：相等 / 更新 / 更旧 / 缺段补零 / 形状不猜 / 没在跑。
 * WECHAT_VERIFIED 的形状（四段数字 + ISO 日期）也钉住——它是发布物的一部分，写坏了
 * 用户看到的"验证过的版本"就是一句瞎话。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { join } from 'node:path'
import { WECHAT_VERIFIED, compareWechatVersions, describeCompatVerdict, describeUnknownVersionReason } from '../src/services/wechatCompat.js'

test('compareWechatVersions：四种判定与边界', () => {
  assert.equal(compareWechatVersions('4.1.15.56', '4.1.15.56'), 'verified')
  assert.equal(compareWechatVersions('4.1.15.56', '4.1.15.55'), 'newer-than-verified')
  assert.equal(compareWechatVersions('4.1.15.60', '4.1.15.56'), 'newer-than-verified')
  assert.equal(compareWechatVersions('4.1.16.0', '4.1.15.56'), 'newer-than-verified', '第三段进位也是更新')
  assert.equal(compareWechatVersions('4.1.15.55', '4.1.15.56'), 'older-than-verified')
  // 缺段按 0：4.1.15 < 4.1.15.56
  assert.equal(compareWechatVersions('4.1.15', '4.1.15.56'), 'older-than-verified')
  // 形状不对不猜
  assert.equal(compareWechatVersions('v4.1.15', '4.1.15.56'), 'unknown')
  assert.equal(compareWechatVersions('4.1.x.56', '4.1.15.56'), 'unknown')
  // 微信没在跑 / 探测失败
  assert.equal(compareWechatVersions(null, '4.1.15.56'), 'unknown')
  assert.equal(compareWechatVersions(undefined, '4.1.15.56'), 'unknown')
})

test('WECHAT_VERIFIED 的形状：四段数字 + ISO 日期（它是发布物的一部分）', () => {
  assert.match(WECHAT_VERIFIED.version, /^\d+\.\d+\.\d+\.\d+$/,
    '版本号必须是四段数字——check 的报告、capabilities 都直接展示它')
  assert.match(WECHAT_VERIFIED.verifiedAt, /^\d{4}-\d{2}-\d{2}$/, '日期必须是 YYYY-MM-DD')
  assert.ok(WECHAT_VERIFIED.verifiedHow.length > 10, '验证方式必须写清（改这个常量的门槛就是真实验证）')
})

test('判不了的原因要能说准：进程视图被隔离时别说成"微信没在运行"', () => {
  // 2026-10-28 实测：`check` 在沙箱里报"未知（微信没在运行）"，而那时微信正在写库。
  // 默认措辞保留（老行为不变），但调用方知道更准的原因时必须能覆盖它。
  assert.match(describeCompatVerdict('unknown', WECHAT_VERIFIED.version),
               /微信没在运行或版本探测失败/, '不传 reason 时保持原措辞')
  const masked = describeCompatVerdict('unknown', WECHAT_VERIFIED.version,
                                       '本进程的进程视图被隔离，看不到主程序')
  assert.match(masked, /进程视图被隔离/)
  assert.doesNotMatch(masked, /微信没在运行/, '隔离视图下不能把原因说成没在运行')
  assert.match(masked, /验证过的是/, '仍要带出验证过的版本')
})

test('capabilities --json 暴露 compatibility.wechat，与单一事实源一致', () => {
  const result = spawnSync(process.execPath, [
    '--import', 'tsx', join(process.cwd(), 'bin', 'weflow-cli.ts'), 'capabilities', '--json',
  ], { cwd: process.cwd(), encoding: 'utf8' })
  assert.equal(result.status, 0, result.stderr || result.stdout)
  const body = JSON.parse(result.stdout)
  const compat = body.compatibility?.wechat
  assert.ok(compat, 'capabilities 该有 compatibility.wechat（D-130）')
  assert.equal(compat.verifiedVersion, WECHAT_VERIFIED.version, '与 wechatCompat.ts 的常量一致——两处各写一份迟早漂')
  assert.equal(compat.verifiedAt, WECHAT_VERIFIED.verifiedAt)
})

test('Linux 上版本未知不能说成"微信没在运行"（平台不支持探测是另一码事）', () => {
  // 2026-11-01 实测：Ubuntu + 微信 4.1.13.26 正在写 biz_message_0.db，
  // check 却报"未知（微信没在运行）"。版本探测只实现了 Windows，
  // 于是"探测不了"被写成了"客户端没开"——这正是 D-145 要避免的假错误。
  const linux = describeUnknownVersionReason({ platform: 'linux' })
  assert.match(linux.reason, /没找到客户端版本记录/)
  assert.match(linux.reason, /uh\.dat/)
  assert.doesNotMatch(linux.reason, /微信没在运行/, 'Linux 上不能把原因说成没在运行')
  assert.match(linux.compatReason, /不代表客户端没运行/)

  const darwin = describeUnknownVersionReason({ platform: 'darwin' })
  assert.doesNotMatch(darwin.reason, /微信没在运行/)

  // Windows 的措辞保持不变（那里确实能探测）
  const win = describeUnknownVersionReason({ platform: 'win32' })
  assert.match(win.reason, /微信没在运行/)
  assert.equal(win.compatReason, '')

  // 进程视图被隔离时，优先说"隔离"——这比平台原因更具体
  const masked = describeUnknownVersionReason({ platform: 'linux', viewFiltered: true })
  assert.match(masked.reason, /进程视图被隔离/)
  assert.doesNotMatch(masked.reason, /微信没在运行/)
})
