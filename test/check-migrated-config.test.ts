/**
 * 从另一台机器拷来的配置：`check` 必须说"数据目录在本机不存在"。
 *
 * 2026-11-01 在 Linux 上用一份 Windows 的 config.json（路径全是 `C:\…`）实测：
 * `check` 照样报 `✓ 已初始化 — 可以运行 weflow-cli`，因为判据只看"配置里有 key 吗"。
 * 迁移过来的用户会以为一切就绪，然后所有命令都读不到东西。
 *
 * 同一份输出里还有第二个假错误：一个库都没找到时它说"密钥可能不对"——
 * 真因是路径/权限。两条都在这条用例里钉住。
 */
import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'
import { test } from 'node:test'

// 用 fileURLToPath：`.pathname` 在 Windows 上会留下 `/D:/…` 这种带前导斜杠的串
const ROOT = fileURLToPath(new URL('..', import.meta.url))

function runCheck(home: string) {
  const entry = join(ROOT, 'bin', 'weflow-cli.ts')
  const tsx = import.meta.resolve('tsx')
  return spawnSync(process.execPath, ['--import', tsx, entry, 'check'], {
    encoding: 'utf8',
    env: { ...process.env, HOME: home, USERPROFILE: home },
    timeout: 180_000,
  })
}

test('迁移配置（Windows 路径）：必须说"数据目录在本机不存在"，且不把"没找到库"说成密钥错', () => {
  const home = mkdtempSync(join(tmpdir(), 'wf-migrated-'))
  try {
    mkdirSync(join(home, '.weflow-cli'), { recursive: true })
    writeFileSync(join(home, '.weflow-cli', 'config.json'), JSON.stringify({
      dbPath: 'C:\\Users\\someone\\xwechat_files',
      ntDbPath: 'C:\\Users\\someone\\xwechat_files\\wxid_x\\db_storage\\message\\message_0.db',
      ntKey: '11'.repeat(32),
      ntSalt: '22'.repeat(16),
    }), 'utf8')

    const proc = runCheck(home)
    const out = (proc.stdout || '') + (proc.stderr || '')
    assert.equal(proc.status, 0, `check 应正常退出，实际 ${proc.status}：${out.slice(-500)}`)
    assert.match(out, /已配置，但数据目录在本机不存在/,
      '数据目录不存在时必须明说，而不是"✓ 已初始化"')
    assert.doesNotMatch(out, /✓ 已初始化/, '路径不存在时不能说已初始化')
    assert.match(out, /没找到可读的数据库/,
      '一个库都没有时不能说成"密钥可能不对"')
    assert.doesNotMatch(out, /密钥可能不对/, '没有数据库 ≠ 密钥错')
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})
