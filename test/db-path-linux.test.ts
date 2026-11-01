/**
 * Linux 数据目录发现：Flatpak / Snap / XDG。
 *
 * 为什么要有这组测试：Linux 上微信不只有 .deb 一种装法。Flatpak 会把家目录重定向到
 * `~/.var/app/<id>/`，Snap 在 `~/snap/<pkg>/` 下——之前只列了四个"常规"路径，
 * 这类用户跑 `init` 会直接得到"找不到数据目录"，看起来像工具坏了。
 *
 * 2026-11-01 在 Ubuntu 24.04 上用 `HOME=/tmp/fakehome` 实测：
 * `✓ 检测到数据目录: /tmp/fakehome/.var/app/com.tencent.wechat/data/xwechat_files`。
 */
import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'

import { DbPathService } from '../src/core/dbPathService.js'

/** 造一个只含指定布局的假家目录，返回 (home, 清理函数)。 */
function fakeHome(...segments: string[]): { home: string; root: string; cleanup: () => void } {
  const home = mkdtempSync(join(tmpdir(), 'wf-home-'))
  const root = join(home, ...segments)
  const account = join(root, 'wxid_demo', 'db_storage', 'message')
  mkdirSync(account, { recursive: true })
  writeFileSync(join(account, 'message_0.db'), '')
  return { home, root, cleanup: () => rmSync(home, { recursive: true, force: true }) }
}

/** 在给定的 HOME 下跑一次自动检测（homedir() 在 POSIX 上认 HOME）。 */
async function detectUnder(home: string): Promise<string | null> {
  const saved = { HOME: process.env.HOME, USERPROFILE: process.env.USERPROFILE }
  process.env.HOME = home
  process.env.USERPROFILE = home
  try {
    const service = new DbPathService()
    const found = await service.autoDetect({})
    // autoDetect 返回的是 { success, path, error, source }，**不是**字符串
    // （第一版把它当字符串，CI 的 ubuntu job 上 4 条全红）。
    return found.success ? (found.path ?? null) : null
  } finally {
    if (saved.HOME === undefined) delete process.env.HOME
    else process.env.HOME = saved.HOME
    if (saved.USERPROFILE === undefined) delete process.env.USERPROFILE
    else process.env.USERPROFILE = saved.USERPROFILE
  }
}

test('Flatpak 布局（~/.var/app/<id>/data/xwechat_files）能被发现', async (t) => {
  if (process.platform !== 'linux') {
    t.skip('只在 Linux 上验证（路径语义不同）')
    return
  }
  const { home, root, cleanup } = fakeHome('.var', 'app', 'com.tencent.wechat', 'data', 'xwechat_files')
  try {
    const found = await detectUnder(home)
    assert.equal(found, root, 'Flatpak 的数据目录必须被认出来')
  } finally {
    cleanup()
  }
})

test('Flatpak 的未预料 app id 也能靠扫描兜底发现', async (t) => {
  if (process.platform !== 'linux') {
    t.skip('只在 Linux 上验证')
    return
  }
  const { home, root, cleanup } = fakeHome('.var', 'app', 'org.example.somefork', 'data', 'xwechat_files')
  try {
    const found = await detectUnder(home)
    assert.equal(found, root, '扫 ~/.var/app/*/ 的兜底分支必须生效')
  } finally {
    cleanup()
  }
})

test('Snap 布局（~/snap/<pkg>/common/xwechat_files）能被发现', async (t) => {
  if (process.platform !== 'linux') {
    t.skip('只在 Linux 上验证')
    return
  }
  const { home, root, cleanup } = fakeHome('snap', 'wechat', 'common', 'xwechat_files')
  try {
    const found = await detectUnder(home)
    assert.equal(found, root, 'Snap 的数据目录必须被认出来')
  } finally {
    cleanup()
  }
})

test('数据目录是符号链接时也能发现（Linux 常见：软链到另一块盘）', async (t) => {
  if (process.platform !== 'linux') {
    t.skip('只在 Linux 上验证')
    return
  }
  const home = mkdtempSync(join(tmpdir(), 'wf-link-'))
  try {
    const real = join(home, 'real', 'xwechat_files')
    mkdirSync(join(real, 'wxid_demo', 'db_storage', 'message'), { recursive: true })
    writeFileSync(join(real, 'wxid_demo', 'db_storage', 'message', 'message_0.db'), '')
    symlinkSync(real, join(home, 'xwechat_files'))

    const found = await detectUnder(home)
    assert.ok(found, '符号链接的数据目录必须能被发现')
    // 报软链路径或真实路径都算对——判据是"能落到同一个目录"
    assert.equal(realpathSync(found), realpathSync(real))
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('同机多账号：两个 wxid 目录都要列出来', async (t) => {
  if (process.platform !== 'linux') {
    t.skip('只在 Linux 上验证')
    return
  }
  const { home, root, cleanup } = fakeHome('xwechat_files')
  try {
    mkdirSync(join(root, 'wxid_second', 'db_storage', 'message'), { recursive: true })
    writeFileSync(join(root, 'wxid_second', 'db_storage', 'message', 'message_0.db'), '')
    const found = await detectUnder(home)
    assert.ok(found)
    const accounts = new DbPathService().findAccountDirs(found)
    assert.deepEqual(accounts.sort(), ['wxid_demo', 'wxid_second'])
  } finally {
    cleanup()
  }
})

test('家目录含中文与空格时也能发现（Linux 用户名常见）', async (t) => {
  if (process.platform !== 'linux') {
    t.skip('只在 Linux 上验证')
    return
  }
  const base = mkdtempSync(join(tmpdir(), 'wf-cn-'))
  const home = join(base, '测试 用户')
  try {
    const root = join(home, 'xwechat_files')
    mkdirSync(join(root, 'wxid_demo', 'db_storage', 'message'), { recursive: true })
    writeFileSync(join(root, 'wxid_demo', 'db_storage', 'message', 'message_0.db'), '')
    const found = await detectUnder(home)
    assert.equal(found, root, '中文+空格路径不能被截断或转义坏')
  } finally {
    rmSync(base, { recursive: true, force: true })
  }
})

test('反例：空家目录不应凭空"发现"一个数据目录', async (t) => {
  if (process.platform !== 'linux') {
    t.skip('只在 Linux 上验证')
    return
  }
  const home = mkdtempSync(join(tmpdir(), 'wf-empty-'))
  try {
    const found = await detectUnder(home)
    assert.equal(found, null, '没有任何 xwechat_files 时必须是 null，不能瞎猜')
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})
