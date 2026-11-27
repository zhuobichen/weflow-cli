import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { AGENT_VERSION } from '../src/services/agentVersion.js'

test('Agent 版本号格式合法且单一真相源可查询', () => {
  assert.match(AGENT_VERSION.version, /^\d+\.\d+\.\d+$/,
    `Agent 版本必须是 X.Y.Z，收到 ${AGENT_VERSION.version}`)
  assert.match(AGENT_VERSION.released, /^\d{4}-\d{2}-\d{2}$/)
  assert.ok(AGENT_VERSION.note.length > 10, '版本要有简短说明')
})

test('Agent 版本接进了 capabilities --json（否则用户查不到当前安装的版本）', () => {
  // 静态检查：bin 里必须 import AGENT_VERSION 且放进 capabilities 的 data。
  // 真跑 CLI --json 太重（要起整个命令树），但漏接线的后果是静默的——必须有阳性对照。
  const bin = readFileSync(new URL('../bin/weflow-cli.ts', import.meta.url), 'utf8')
  assert.match(bin, /import \{ AGENT_VERSION \} from ['"]\.\.\/src\/services\/agentVersion\.js['"]/,
    'bin 必须 import AGENT_VERSION')
  assert.match(bin, /agentVersion:\s*AGENT_VERSION/,
    'capabilities --json 必须输出 agentVersion 字段')
})
