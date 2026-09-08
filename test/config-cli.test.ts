import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'

function runCli(home: string, args: string[], extraEnv: NodeJS.ProcessEnv = {}) {
  return spawnSync(process.execPath, [
    '--import', 'tsx', join(process.cwd(), 'bin', 'weflow-cli.ts'), ...args,
  ], {
    cwd: process.cwd(),
    encoding: 'utf8',
    env: { ...process.env, HOME: home, USERPROFILE: home, ...extraEnv },
  })
}

test('configuration secrets can be stored from environment and clear requires confirmation', () => {
  const home = mkdtempSync(join(tmpdir(), 'weflow-config-'))
  const configFile = join(home, '.weflow-cli', 'config.json')
  const secret = 'synthetic-config-secret'
  const run = (args: string[], extraEnv: NodeJS.ProcessEnv = {}) => runCli(home, args, extraEnv)

  try {
    const stored = run(
      ['config', 'set-env', 'deepseekApiKey', 'WEFLOW_TEST_API_KEY'],
      { WEFLOW_TEST_API_KEY: secret },
    )
    assert.equal(stored.status, 0, stored.stderr || stored.stdout)
    assert.equal(stored.stdout.includes(secret), false)
    assert.equal(existsSync(configFile), true)
    assert.equal(readFileSync(configFile, 'utf8').includes(secret), false)

    const refused = run(['config', 'clear', '--json'])
    assert.equal(refused.status, 1)
    assert.equal(JSON.parse(refused.stdout).code, 'CONFIRMATION_REQUIRED')
    assert.equal(existsSync(configFile), true)

    const cleared = run(['config', 'clear', '--yes', '--json'])
    assert.equal(cleared.status, 0, cleared.stderr || cleared.stdout)
    assert.deepEqual(JSON.parse(cleared.stdout), { success: true, action: 'config.clear' })
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('bulk security-state deletion requires explicit confirmation', () => {
  const home = mkdtempSync(join(tmpdir(), 'weflow-security-state-'))
  const commands = [
    ['whitelist', 'clear'],
    ['blacklist', 'clear'],
    ['audit', 'clear'],
  ]

  try {
    for (const command of commands) {
      const preview = runCli(home, [...command, '--dry-run', '--json'])
      assert.equal(preview.status, 0, preview.stderr || preview.stdout)
      assert.equal(JSON.parse(preview.stdout).dryRun, true)

      const refused = runCli(home, [...command, '--json'])
      assert.equal(refused.status, 1, refused.stderr || refused.stdout)
      assert.equal(JSON.parse(refused.stdout).code, 'CONFIRMATION_REQUIRED')

      const confirmed = runCli(home, [...command, '--yes', '--json'])
      assert.equal(confirmed.status, 0, confirmed.stderr || confirmed.stdout)
      assert.equal(JSON.parse(confirmed.stdout).success, true)
    }
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('machine-readable configuration and access lists are available without exposing secrets', () => {
  const home = mkdtempSync(join(tmpdir(), 'weflow-status-'))
  const secret = 'synthetic-hidden-secret'

  try {
    const stored = runCli(home, ['config', 'set-env', 'deepseekApiKey', 'WEFLOW_TEST_API_KEY'], {
      WEFLOW_TEST_API_KEY: secret,
    })
    assert.equal(stored.status, 0, stored.stderr || stored.stdout)

    const statusResult = runCli(home, ['config', 'show', '--json'])
    assert.equal(statusResult.status, 0, statusResult.stderr || statusResult.stdout)
    const status = JSON.parse(statusResult.stdout)
    assert.equal(status.schema, 'weflow-config-status/v1')
    assert.equal(status.ai.configured, true)
    assert.equal(statusResult.stdout.includes(secret), false)
    assert.equal('dbPath' in status, false)
    assert.equal('wxid' in status, false)

    for (const list of ['whitelist', 'blacklist']) {
      const result = runCli(home, [list, 'list', '--json'])
      assert.equal(result.status, 0, result.stderr || result.stdout)
      assert.deepEqual(JSON.parse(result.stdout), { success: true, entries: [] })
    }
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('database-key reset requires explicit machine confirmation', () => {
  const home = mkdtempSync(join(tmpdir(), 'weflow-forget-keys-'))

  try {
    const preview = runCli(home, ['config', 'forget-keys', '--dry-run', '--json'])
    assert.equal(preview.status, 0, preview.stderr || preview.stdout)
    assert.equal(JSON.parse(preview.stdout).preservesNonDatabaseSettings, true)

    const refused = runCli(home, ['config', 'forget-keys', '--json'])
    assert.equal(refused.status, 1, refused.stderr || refused.stdout)
    assert.equal(JSON.parse(refused.stdout).code, 'CONFIRMATION_REQUIRED')

    const confirmed = runCli(home, ['config', 'forget-keys', '--yes', '--json'])
    assert.equal(confirmed.status, 0, confirmed.stderr || confirmed.stdout)
    assert.deepEqual(JSON.parse(confirmed.stdout), { success: true, action: 'config.forget-keys' })
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('access-control mutations require preview and explicit confirmation', () => {
  const home = mkdtempSync(join(tmpdir(), 'weflow-access-mutation-'))
  const whitelistId = 'wxid_test_whitelist'
  const blacklistId = 'wxid_test_blacklist'

  try {
    const previewAdd = runCli(home, ['whitelist', 'add', whitelistId, '--dry-run', '--json'])
    assert.equal(previewAdd.status, 0, previewAdd.stderr || previewAdd.stdout)
    assert.equal(JSON.parse(previewAdd.stdout).dryRun, true)
    assert.deepEqual(JSON.parse(runCli(home, ['whitelist', 'list', '--json']).stdout).entries, [])

    const refusedAdd = runCli(home, ['whitelist', 'add', whitelistId, '--json'])
    assert.equal(refusedAdd.status, 1, refusedAdd.stderr || refusedAdd.stdout)
    assert.equal(JSON.parse(refusedAdd.stdout).code, 'CONFIRMATION_REQUIRED')

    const added = runCli(home, ['whitelist', 'add', whitelistId, '--yes', '--json'])
    assert.equal(added.status, 0, added.stderr || added.stdout)
    assert.equal(JSON.parse(added.stdout).changed, true)
    assert.equal(JSON.parse(runCli(home, ['whitelist', 'list', '--json']).stdout).entries.length, 1)

    const refusedRemove = runCli(home, ['whitelist', 'rm', whitelistId, '--json'])
    assert.equal(refusedRemove.status, 1, refusedRemove.stderr || refusedRemove.stdout)
    assert.equal(JSON.parse(refusedRemove.stdout).code, 'CONFIRMATION_REQUIRED')

    const removed = runCli(home, ['whitelist', 'rm', whitelistId, '--yes', '--json'])
    assert.equal(removed.status, 0, removed.stderr || removed.stdout)
    assert.equal(JSON.parse(removed.stdout).changed, true)

    const previewBlock = runCli(home, ['blacklist', 'add', blacklistId, '--dry-run', '--json'])
    assert.equal(previewBlock.status, 0, previewBlock.stderr || previewBlock.stdout)
    assert.equal(JSON.parse(previewBlock.stdout).dryRun, true)

    const blocked = runCli(home, ['blacklist', 'add', blacklistId, '--yes', '--json'])
    assert.equal(blocked.status, 0, blocked.stderr || blocked.stdout)
    assert.equal(JSON.parse(blocked.stdout).changed, true)
    assert.equal(JSON.parse(runCli(home, ['blacklist', 'list', '--json']).stdout).entries.length, 1)

    const unblocked = runCli(home, ['blacklist', 'rm', blacklistId, '--yes', '--json'])
    assert.equal(unblocked.status, 0, unblocked.stderr || unblocked.stdout)
    assert.equal(JSON.parse(unblocked.stdout).changed, true)
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('JSON configuration writes require preview and confirmation without echoing values', () => {
  const home = mkdtempSync(join(tmpdir(), 'weflow-config-mutation-'))
  const secret = 'synthetic-machine-secret'
  const env = { WEFLOW_TEST_API_KEY: secret }

  try {
    const preview = runCli(home, ['config', 'set-env', 'deepseekApiKey', 'WEFLOW_TEST_API_KEY', '--dry-run', '--json'], env)
    assert.equal(preview.status, 0, preview.stderr || preview.stdout)
    assert.equal(JSON.parse(preview.stdout).dryRun, true)
    assert.equal(preview.stdout.includes(secret), false)

    const refused = runCli(home, ['config', 'set-env', 'deepseekApiKey', 'WEFLOW_TEST_API_KEY', '--json'], env)
    assert.equal(refused.status, 1, refused.stderr || refused.stdout)
    assert.equal(JSON.parse(refused.stdout).code, 'CONFIRMATION_REQUIRED')

    const written = runCli(home, ['config', 'set-env', 'deepseekApiKey', 'WEFLOW_TEST_API_KEY', '--yes', '--json'], env)
    assert.equal(written.status, 0, written.stderr || written.stdout)
    assert.equal(JSON.parse(written.stdout).changed, true)
    assert.equal(written.stdout.includes(secret), false)

    const invalid = runCli(home, ['config', 'set', 'unknownKey', 'value', '--dry-run', '--json'])
    assert.equal(invalid.status, 1, invalid.stderr || invalid.stdout)
    assert.equal(JSON.parse(invalid.stdout).code, 'INVALID_CONFIG_KEY')
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('MCP configuration file writes require preview and explicit confirmation', () => {
  const home = mkdtempSync(join(tmpdir(), 'weflow-mcp-config-home-'))
  const outputDir = mkdtempSync(join(tmpdir(), 'weflow-mcp-config-output-'))
  const output = join(outputDir, '.mcp.json')

  try {
    const preview = runCli(home, ['mcp-config', '--output', output, '--dry-run', '--json-result'])
    assert.equal(preview.status, 0, preview.stderr || preview.stdout)
    assert.equal(JSON.parse(preview.stdout).dryRun, true)
    assert.equal(existsSync(output), false)
    assert.equal(preview.stdout.includes(output), false)

    const refused = runCli(home, ['mcp-config', '--output', output, '--json-result'])
    assert.equal(refused.status, 1, refused.stderr || refused.stdout)
    assert.equal(JSON.parse(refused.stdout).code, 'CONFIRMATION_REQUIRED')
    assert.equal(existsSync(output), false)

    const written = runCli(home, ['mcp-config', '--output', output, '--yes', '--json-result'])
    assert.equal(written.status, 0, written.stderr || written.stdout)
    assert.equal(JSON.parse(written.stdout).changed, true)
    assert.equal(existsSync(output), true)
    assert.equal(written.stdout.includes(output), false)

    const obsoletePort = runCli(home, ['mcp-config', '--port', '9000'])
    assert.equal(obsoletePort.status, 1, obsoletePort.stderr || obsoletePort.stdout)
    assert.match(obsoletePort.stderr, /unknown option/)
  } finally {
    rmSync(home, { recursive: true, force: true })
    rmSync(outputDir, { recursive: true, force: true })
  }
})

test('message-channel login stays interactive and logout requires confirmation', () => {
  const home = mkdtempSync(join(tmpdir(), 'weflow-channel-home-'))

  try {
    const loginPreview = runCli(home, ['login-wechat', '--dry-run', '--json'])
    assert.equal(loginPreview.status, 0, loginPreview.stderr || loginPreview.stdout)
    const loginData = JSON.parse(loginPreview.stdout)
    assert.equal(loginData.interactiveRequired, true)
    assert.equal(loginData.replacesExistingSession, false)

    const machineLogin = runCli(home, ['login-wechat', '--yes', '--json'])
    assert.equal(machineLogin.status, 1, machineLogin.stderr || machineLogin.stdout)
    assert.equal(JSON.parse(machineLogin.stdout).code, 'INTERACTIVE_REQUIRED')

    const snsPreview = runCli(home, ['sns', 'capture-key', '--dry-run', '--json'])
    assert.equal(snsPreview.status, 0, snsPreview.stderr || snsPreview.stdout)
    assert.equal(JSON.parse(snsPreview.stdout).scansProcessMemory, true)

    const machineCapture = runCli(home, ['sns', 'capture-key', '--yes', '--json'])
    assert.equal(machineCapture.status, 1, machineCapture.stderr || machineCapture.stdout)
    assert.equal(JSON.parse(machineCapture.stdout).code, 'INTERACTIVE_REQUIRED')

    const logoutPreview = runCli(home, ['logout-wechat', '--dry-run', '--json'])
    assert.equal(logoutPreview.status, 0, logoutPreview.stderr || logoutPreview.stdout)
    assert.equal(JSON.parse(logoutPreview.stdout).clearsContextTokens, true)

    const refusedLogout = runCli(home, ['logout-wechat', '--json'])
    assert.equal(refusedLogout.status, 1, refusedLogout.stderr || refusedLogout.stdout)
    assert.equal(JSON.parse(refusedLogout.stdout).code, 'CONFIRMATION_REQUIRED')

    const logout = runCli(home, ['logout-wechat', '--yes', '--json'])
    assert.equal(logout.status, 0, logout.stderr || logout.stdout)
    assert.equal(JSON.parse(logout.stdout).action, 'wechat-channel.logout')
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})
