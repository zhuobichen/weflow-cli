/**
 * 配置文件的权限位：Linux 上必须是 600 / 目录 700。
 *
 * 2026-11-01 在 Ubuntu 实测：`~/.weflow-cli` 是 775、`config.json` 是 644——
 * 同机其他用户能**读**配置（里面有凭据，虽然加密但仍是敏感面），
 * 775 甚至让他们能**替换**这个文件。
 *
 * Windows 上 `mode` 基本是空操作（Node 只能映射只读位），所以这些断言只在 POSIX 上跑；
 * 反过来，Windows 上加了 mode 也不该改变行为——这条由"能正常写配置"隐含覆盖。
 */
import assert from 'node:assert/strict'
import { chmodSync, existsSync, mkdirSync, readFileSync, statSync } from 'node:fs'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'

function modeOf(path: string): number {
  return statSync(path).mode & 0o777
}

test('保存配置后：~/.weflow-cli 是 700、config.json 是 600', async (t) => {
  if (process.platform === 'win32') {
    t.skip('Windows 上 mode 不映射为 POSIX 权限位')
    return
  }
  const home = mkdtempSync(join(tmpdir(), 'wf-perm-cfg-'))
  const savedHome = process.env.HOME
  try {
    // 故意先建一个"旧版本留下的 775 目录"，确认保存时会被收紧
    mkdirSync(join(home, '.weflow-cli'), { recursive: true, mode: 0o777 })
    process.env.HOME = home

    // 用 query 重新导入，绕开模块级单例里已经读过的 HOME
    const mod = await import(`../src/services/configService.ts?perm=${Date.now()}`)
    const svc = new mod.ConfigService()
    svc.set('aiModel', 'test-model')

    const dir = join(home, '.weflow-cli')
    const file = join(dir, 'config.json')
    assert.ok(existsSync(file), '配置文件应被写出')
    assert.equal(modeOf(file), 0o600, `config.json 应为 600，实际 ${modeOf(file).toString(8)}`)
    assert.equal(modeOf(dir), 0o700, `目录应为 700，实际 ${modeOf(dir).toString(8)}`)
  } finally {
    if (savedHome === undefined) delete process.env.HOME
    else process.env.HOME = savedHome
    rmSync(home, { recursive: true, force: true })
  }
})

test('目录刻意设为只读(555)时：保存必须失败，且不许偷偷把目录改回可写', async (t) => {
  if (process.platform === 'win32') {
    t.skip('Windows 上 mode 不映射为 POSIX 权限位')
    return
  }
  const home = mkdtempSync(join(tmpdir(), 'wf-perm-ro-'))
  const savedHome = process.env.HOME
  try {
    const dir = join(home, '.weflow-cli')
    mkdirSync(dir, { recursive: true })
    process.env.HOME = home
    const mod = await import(`../src/services/configService.ts?perm-ro=${Date.now()}`)
    const svc = new mod.ConfigService()
    svc.set('aiModel', 'first')

    // 用户刻意去掉 owner 写位
    chmodSync(dir, 0o555)
    assert.throws(() => svc.set('aiModel', 'second'), /没有写进磁盘|EACCES/,
      '写不进去时必须抛错，不能静默成功')
    // 目录不能被"收紧"逻辑改回可写（那等于无视用户意图）
    assert.equal(modeOf(dir), 0o555, `目录权限被改成了 ${modeOf(dir).toString(8)}`)
    // 值也不能变
    const onDisk = JSON.parse(readFileSync(join(dir, 'config.json'), 'utf8'))
    assert.equal(onDisk.aiModel, 'first')
  } finally {
    if (savedHome === undefined) delete process.env.HOME
    else process.env.HOME = savedHome
    rmSync(home, { recursive: true, force: true })
  }
})
