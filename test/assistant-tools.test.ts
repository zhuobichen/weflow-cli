import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'
import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'
import {
  boundedToolInteger,
  MCP_READ_ONLY_TOOL_DEFS,
  resolveUniqueTalker,
} from '../src/services/assistantTools.js'

test('MCP assistant-tool subset contains no write operation', () => {
  const names = MCP_READ_ONLY_TOOL_DEFS.map(tool => tool.function.name)
  const mcpSource = readFileSync(join(process.cwd(), 'mcp-server', 'index.ts'), 'utf8')
  assert.equal(names.includes('save_memory'), false)
  assert.equal(names.includes('send'), false)
  assert.equal(names.includes('publish_article'), false)
  assert.doesNotMatch(mcpSource, /name:\s*['"]wechat\.publish_article['"]/)
  assert.doesNotMatch(mcpSource, /case\s+['"]wechat\.publish_article['"]/)
})

test('conversation resolution requires a unique display-name match', () => {
  const sessions = [
    { username: 'session-a', displayName: '项目讨论' },
    { username: 'session-b', displayName: '项目讨论' },
    { username: 'session-c', displayName: '项目讨论二组' },
  ]

  assert.equal(resolveUniqueTalker('session-a', sessions), 'session-a')
  assert.throws(() => resolveUniqueTalker('项目讨论', sessions), /对应多个会话/)
  assert.throws(() => resolveUniqueTalker('项目', sessions), /匹配多个会话/)
  assert.equal(resolveUniqueTalker('二组', sessions), 'session-c')
  assert.equal(resolveUniqueTalker('unlisted-session-id', sessions), 'unlisted-session-id')
})

test('tool limits accept bounded integers and reject coercion hazards', () => {
  assert.equal(boundedToolInteger(undefined, 15, 30), 15)
  assert.equal(boundedToolInteger('20', 15, 30), 20)
  assert.throws(() => boundedToolInteger(0, 15, 30), /1-30/)
  assert.throws(() => boundedToolInteger(-1, 15, 30), /1-30/)
  assert.throws(() => boundedToolInteger(1.5, 15, 30), /1-30/)
  assert.throws(() => boundedToolInteger('not-a-number', 15, 30), /1-30/)
  assert.throws(() => boundedToolInteger(31, 15, 30), /1-30/)
})

test('capability discovery declares the default MCP surface read-only', () => {
  const result = spawnSync(process.execPath, ['--import', 'tsx', 'bin/weflow-cli.ts', 'capabilities', '--json'], {
    cwd: process.cwd(),
    encoding: 'utf8',
  })
  assert.equal(result.status, 0, result.stderr || result.stdout)
  const capabilities = JSON.parse(result.stdout)
  assert.equal(capabilities.safety.mcpDefaultReadOnly, true)
})

test('generated MCP guidance lists the real read-only tool surface', () => {
  const result = spawnSync(process.execPath, ['--import', 'tsx', 'bin/weflow-cli.ts', 'mcp-config'], {
    cwd: process.cwd(),
    encoding: 'utf8',
  })
  assert.equal(result.status, 0, result.stderr || result.stdout)
  assert.match(result.stdout, /wechat\.export_messages/)
  assert.doesNotMatch(result.stdout, /wechat\.publish_article/)
  assert.doesNotMatch(result.stdout, /wechat\.save_memory/)
})

test('live MCP tools/list exposes only the intended read-only surface', { timeout: 15_000 }, async () => {
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: ['--import', 'tsx', join(process.cwd(), 'mcp-server', 'index.ts')],
    stderr: 'pipe',
  })
  const client = new Client({ name: 'weflow-test-client', version: '1.0.0' })

  try {
    await client.connect(transport)
    const result = await client.listTools()
    const names = result.tools.map(tool => tool.name)
    assert.equal(new Set(names).size, names.length)
    assert.equal(names.includes('wechat.export_messages'), true)
    assert.equal(names.includes('wechat.publish_article'), false)
    assert.equal(names.includes('wechat.save_memory'), false)
    assert.equal(names.includes('wechat.send'), false)

    const rejectedUrl = await client.callTool({
      name: 'wechat.fetch_article',
      arguments: { url: 'https://mp.weixin.qq.com.evil.test/s/example' },
    })
    assert.match(String((rejectedUrl.content[0] as { text?: string }).text), /仅支持 HTTPS/)

    const rejectedLimit = await client.callTool({
      name: 'wechat.search_public',
      arguments: { keyword: 'synthetic', limit: 1000 },
    })
    assert.match(String((rejectedLimit.content[0] as { text?: string }).text), /1-20/)

    const rejectedDate = await client.callTool({
      name: 'wechat.get_daily',
      arguments: { date: '../latest' },
    })
    assert.match(String((rejectedDate.content[0] as { text?: string }).text), /日期无效/)
  } finally {
    await client.close()
  }
})
