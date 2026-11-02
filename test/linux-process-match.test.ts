import { test } from 'node:test'
import assert from 'node:assert/strict'
import { isWechatLinuxProcess } from '../src/services/wechatClient.js'

/**
 * Linux 进程识别：只认"能对应上真实二进制"的形状。
 * 起因：Arch 上 AUR / Flatpak 包装后 `comm` 不再是 wechat，
 * 旧实现会把"微信在运行"判成"没在运行"（issue #2 的最新评论）。
 */
test('comm 是 wechat / weixin 时直接认（官方包）', () => {
  assert.equal(isWechatLinuxProcess('wechat', ''), true)
  assert.equal(isWechatLinuxProcess('WeChat\n', ''), true)
  assert.equal(isWechatLinuxProcess('weixin', ''), true)
})

test('comm 是包装脚本时，看 cmdline 的第一个参数', () => {
  assert.equal(isWechatLinuxProcess('bash', '/opt/wechat/wechat\0--lang=zh-CN'), true)
  assert.equal(isWechatLinuxProcess('sh', '/usr/bin/weixin\0'), true)
})

test('Flatpak 沙箱里的可执行也算', () => {
  assert.equal(isWechatLinuxProcess('bwrap', '/app/bin/wechat\0--foo'), true)
})

test('不把名字里带 wechat 的无关进程当成微信（M-12 的假阳性）', () => {
  assert.equal(isWechatLinuxProcess('notwechat-tool', ''), false)
  assert.equal(isWechatLinuxProcess('python3', '/usr/bin/python3 wechat_scraper.py'), false)
  assert.equal(isWechatLinuxProcess('bash', '/home/u/wechat-backup.sh'), false)
})

test('空值不认', () => {
  assert.equal(isWechatLinuxProcess('', ''), false)
  assert.equal(isWechatLinuxProcess('node', ''), false)
})
