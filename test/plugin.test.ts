/**
 * MCP 工具插件（D-137）：清单校验、注册表、命名空间、确认门、密钥不外泄。
 *
 * 这个文件刻意**不启动真进程**：连接器是可注入的，用例用假连接器就能覆盖"插件怎么被并进来、
 * 出错时会不会拖垮别人、confirm 有没有真的拦住"。起真进程的那部分由 `plugin doctor` 在本机验，
 * 不适合放进跑在 CI 里的单元测试（它依赖 node 可执行文件与被测插件的存在）。
 *
 * 两件与"怎么写"有关的事，都是踩出来的：
 *   - 环境变量必须在**第一次读注册表之前**设好，否则用例会读写用户真实的 `~/.weflow-cli`；
 *   - 用例**必须串行**。注册表是一份全局文件，`reset()` 与 `addPlugin()` 交错时会互相看见对方
 *     的插件，报错长得像"插件已经装过了"——那不是产品缺陷，是测试自己没排队。所以全部用例
 *     包在一个 `describe` 里（子测试默认串行），而不是散落成顶层 `test`。
 */
import test, { after, describe } from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmdirSync, unlinkSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import {
  PLUGIN_SCHEMA,
  addPlugin,
  callPluginTool,
  checkPluginHealth,
  childEnv,
  closePlugins,
  lintCards,
  listPluginTools,
  listPlugins,
  parsePluginToolName,
  registryFile,
  removePlugin,
  runKnowledgeSource,
  setConnectorFactory,
  setPluginFlags,
  validateManifest,
  type PluginConnector,
  type PluginManifest,
} from '../src/services/pluginService.js'
import { setScriptRunner } from '../src/services/pythonBridge.js'

// 落到仓库根而不是 %TEMP%：实测这台机器的 `%TEMP%` 对 node 子进程是 EPERM，
// 仓库里其它测试也走过同一条回退路径（`.gitignore` 的 `/tmp[0-9a-z_]*` 就是为它留的）。
const SANDBOX = mkdtempSync(join(process.cwd(), 'tmp_plugin_'))
process.env.WEFLOW_PLUGIN_REGISTRY = join(SANDBOX, 'plugins.json')
process.env.WEFLOW_PLUGIN_HOME = join(SANDBOX, 'plugins')

/**
 * 删目录树。**刻意不用 `rmSync`**：在开发这台机器上实测 `rmSync`（文件和 `recursive` 目录）
 * 是**静默空操作**——调用不报错、文件还在——而 `unlinkSync` / `rmdirSync` 正常。
 * 用 `rmSync` 写 `reset()` 会让用例互相看见对方的插件，报错长得像"插件已经装过了"，
 * 排查方向会完全跑偏（这一次就跑了三轮）。
 */
function rmTree(dir: string): void {
  if (!existsSync(dir)) return
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const target = join(dir, entry.name)
    if (entry.isDirectory()) rmTree(target)
    else unlinkSync(target)
  }
  rmdirSync(dir)
}

after(async () => {
  await closePlugins()
  rmTree(SANDBOX)
})

/** 每个用例都从空注册表开始：注册表是模块级文件，不重置就会互相看见对方的插件。 */
async function reset(): Promise<void> {
  await closePlugins()
  const file = registryFile()
  if (existsSync(file)) unlinkSync(file)
}

function makePlugin(id: string, overrides: Record<string, unknown> = {}): string {
  const dir = join(SANDBOX, 'src', id)
  mkdirSync(dir, { recursive: true })
  const manifest = {
    schema: PLUGIN_SCHEMA,
    id,
    name: `示例插件 ${id}`,
    version: '0.1.0',
    kind: 'mcp-server',
    entry: { command: 'node', args: ['index.js'] },
    ...overrides,
  }
  writeFileSync(join(dir, 'weflow-plugin.json'), JSON.stringify(manifest, null, 2), 'utf8')
  return dir
}

/** 假连接器：记录被调了什么，让"确认门有没有真的拦住"可以被断言，而不是靠读代码相信。 */
function fakeConnector(calls: Array<{ tool: string; args: Record<string, unknown> }>, tools = ['echo']) {
  return (): PluginConnector => ({
    async listTools() {
      return tools.map(name => ({
        name,
        description: `${name} 工具`,
        inputSchema: { type: 'object', properties: {} },
      }))
    },
    async callTool(tool, args) {
      calls.push({ tool, args })
      return `ok:${tool}`
    },
    async close() {},
  })
}

describe('MCP 工具插件', () => {
  test('清单：最小可用的一份会被补默认值', async () => {
    const manifest = validateManifest({
      schema: PLUGIN_SCHEMA,
      id: 'demo',
      name: '示例',
      version: '0.1.0',
      kind: 'mcp-server',
      entry: { command: 'node' },
    })
    assert.equal(manifest.id, 'demo')
    assert.deepEqual(manifest.entry.args, [], 'args 省略时应补成空数组，而不是 undefined')
    assert.deepEqual(manifest.permissions, {}, 'permissions 省略时补空对象')
  })

  test('清单：每条拒绝的理由都要说清是哪一条（插件作者的第一次体验）', async () => {
    const base = {
      schema: PLUGIN_SCHEMA,
      id: 'demo',
      name: '示例',
      version: '0.1.0',
      kind: 'mcp-server',
      entry: { command: 'node' },
    }

    // 版本协商：方向决定谁会去修 —— 说"要升级宿主"，不是"你格式错了"
    assert.throws(() => validateManifest({ ...base, schema: 'weflow-plugin/2' }), /升级 weflow-cli/)
    assert.throws(() => validateManifest({ ...base, schema: 'weflow-plugin/0' }), /旧契约不再支持/)
    assert.throws(() => validateManifest({ ...base, schema: 'weflow-plugin/next' }), /不是 weflow-plugin\/<数字> 的形状/)
    // 点号会破坏 `plugin.<id>.<tool>` 的解析，必须拒绝，而不是"后面再处理"
    assert.throws(() => validateManifest({ ...base, id: 'a.b' }), /不合法/)
    assert.throws(() => validateManifest({ ...base, id: 'Demo' }), /不合法/)
    assert.throws(() => validateManifest({ ...base, kind: 'worker' }), /不支持/)
    assert.throws(() => validateManifest({ ...base, entry: undefined }), /entry/)
    assert.throws(() => validateManifest({ ...base, entry: { command: 'node', args: 'oops' } }), /字符串数组/)
    assert.throws(() => validateManifest(null), /JSON 对象/)
  })

  test('注册表：装、列、禁用、信任、移除都能往返', async () => {
    await reset()
    const dir = makePlugin('alpha')
    const record = addPlugin(dir)
    assert.equal(record.id, 'alpha')
    assert.equal(record.enabled, true, '新装上的插件默认启用')
    assert.equal(record.trusted, false, '新装上的插件默认**不**信任')

    assert.deepEqual(listPlugins().map(p => p.id), ['alpha'])

    assert.throws(() => addPlugin(dir), /已经装过/, '重复装同一个 id 应当报错，而不是悄悄覆盖')

    const disabled = setPluginFlags('alpha', { enabled: false })
    assert.equal(disabled.enabled, false)
    const trusted = setPluginFlags('alpha', { trusted: true })
    assert.equal(trusted.trusted, true)

    // 落盘的必须就是内存里的那份
    const onDisk = JSON.parse(readFileSync(registryFile(), 'utf8'))
    assert.equal(onDisk.version, 1)
    assert.equal(onDisk.plugins[0].trusted, true)

    assert.equal(removePlugin('alpha'), true)
    assert.equal(removePlugin('alpha'), false, '移除一个不存在的 id 返回 false，不抛')
    assert.deepEqual(listPlugins(), [])
  })

  test('注册表：坏文件按空处理，且**不**覆盖原文件（那是可以手改的）', async () => {
    await reset()
    mkdirSync(SANDBOX, { recursive: true })
    writeFileSync(registryFile(), '{ not json', 'utf8')
    assert.deepEqual(listPlugins(), [])
    assert.equal(readFileSync(registryFile(), 'utf8'), '{ not json', '坏文件不该被静默重写掉')
  })

  test('命名空间：`plugin.<id>.<tool>` 能被确定地解析，坏形状返回 null', async () => {
    assert.deepEqual(parsePluginToolName('plugin.demo.echo'), { id: 'demo', toolName: 'echo' })
    assert.equal(parsePluginToolName('wechat.get_daily'), null)
    assert.equal(parsePluginToolName('plugin.demo'), null, '没有工具名')
    assert.equal(parsePluginToolName('plugin..echo'), null, 'id 为空')
    assert.equal(parsePluginToolName('plugin.Demo.echo'), null, 'id 必须小写')
  })

  test('并表：只并入已启用的插件，且名字带前缀', async () => {
    await reset()
    addPlugin(makePlugin('on'))
    addPlugin(makePlugin('off'))
    setPluginFlags('off', { enabled: false })
    const restore = setConnectorFactory(fakeConnector([]))
    try {
      const tools = await listPluginTools()
      assert.deepEqual(tools.map(t => t.name), ['plugin.on.echo'])
    } finally {
      restore()
      await closePlugins()
    }
  })

  test('隔离：一个插件起不来，不让别的插件和整张工具表一起消失', async () => {
    await reset()
    addPlugin(makePlugin('good'))
    addPlugin(makePlugin('bad'))
    const restore = setConnectorFactory((manifest: PluginManifest): PluginConnector => {
      if (manifest.id === 'bad') {
        return {
          async listTools() { throw new Error('spawn 失败') },
          async callTool() { throw new Error('spawn 失败') },
          async close() {},
        }
      }
      return fakeConnector([])(manifest, '')
    })
    try {
      const tools = await listPluginTools()
      assert.deepEqual(tools.map(t => t.name), ['plugin.good.echo'], '坏插件只应让它自己消失')
    } finally {
      restore()
      await closePlugins()
    }
  })

  test('确认门：未信任的插件不确认只回预览，而且**不会**真的调用它', async () => {
    await reset()
    addPlugin(makePlugin('demo'))
    const calls: Array<{ tool: string; args: Record<string, unknown> }> = []
    const restore = setConnectorFactory(fakeConnector(calls))
    try {
      const preview = await callPluginTool('plugin.demo.echo', { text: 'hi' })
      assert.match(preview, /需要确认/)
      assert.equal(calls.length, 0, '预览阶段绝不能已经把参数发给第三方进程')

      const result = await callPluginTool('plugin.demo.echo', { text: 'hi', confirm: true })
      assert.equal(result, 'ok:echo')
      assert.equal(calls.length, 1)
      assert.deepEqual(calls[0].args, { text: 'hi' }, 'confirm 是本服务的参数，不该转发给插件')
    } finally {
      restore()
      await closePlugins()
    }
  })

  test('确认门：信任之后直接调用，不再要求 confirm', async () => {
    await reset()
    addPlugin(makePlugin('demo'))
    setPluginFlags('demo', { trusted: true })
    const calls: Array<{ tool: string; args: Record<string, unknown> }> = []
    const restore = setConnectorFactory(fakeConnector(calls))
    try {
      assert.equal(await callPluginTool('plugin.demo.echo', { text: 'hi' }), 'ok:echo')
      assert.equal(calls.length, 1)
    } finally {
      restore()
      await closePlugins()
    }
  })

  test('确认门：禁用的插件即使带了 confirm 也不执行', async () => {
    await reset()
    addPlugin(makePlugin('demo'))
    setPluginFlags('demo', { enabled: false, trusted: true })
    const calls: Array<{ tool: string; args: Record<string, unknown> }> = []
    const restore = setConnectorFactory(fakeConnector(calls))
    try {
      assert.match(await callPluginTool('plugin.demo.echo', { confirm: true }), /已禁用/)
      assert.equal(calls.length, 0)
    } finally {
      restore()
      await closePlugins()
    }
  })

  test('密钥不外泄：子进程环境里没有宿主的环境变量', async () => {
    process.env.WEFLOW_TEST_SENTINEL_KEY = 'sk-should-not-leak'
    try {
      const env = childEnv('demo')
      const leaked = Object.entries(env).filter(([, value]) => value === 'sk-should-not-leak')
      assert.deepEqual(leaked, [], '把 process.env 整个传给子进程，等于把 API key 交给第三方代码')
      // 正控制：白名单里的变量必须**在**里面，否则"没泄漏"可能只是把环境整个传空了
      assert.ok(Object.keys(env).length > 0, '白名单不该是空的')
      assert.equal(env.WEFLOW_PLUGIN_ID, 'demo')
    } finally {
      delete process.env.WEFLOW_TEST_SENTINEL_KEY
    }
  })

  test('握手超时：永远不做 initialize 的插件不会把工具表挂死', async () => {
    await reset()
    // 一个真实存在的进程，但它永远不说 MCP —— 这是"插件写坏了"的最坏形状：
    // 进程活着、握手不完成。实测没有超时的话 `client.connect()` 永不返回，
    // 而 ListTools 在它后面排队，整个服务的工具表就不响应了。
    addPlugin(makePlugin('hang', {
      entry: { command: process.execPath, args: ['-e', 'setInterval(() => {}, 1000)'] },
    }))
    const previous = process.env.WEFLOW_PLUGIN_CONNECT_TIMEOUT_MS
    process.env.WEFLOW_PLUGIN_CONNECT_TIMEOUT_MS = '400'
    try {
      const started = Date.now()
      const tools = await listPluginTools()
      const elapsed = Date.now() - started
      assert.deepEqual(tools, [], '握不上手的插件应当被跳过，而不是让整张表卡住')
      assert.ok(elapsed < 10_000, `listPluginTools 用了 ${elapsed}ms，说明超时没有生效`)

      const [record] = listPlugins()
      const health = await checkPluginHealth(record)
      assert.equal(health.ok, false)
      assert.match(String(health.error), /超时/, 'doctor 要把原因说成"超时"，而不是一句空错误')
    } finally {
      if (previous === undefined) delete process.env.WEFLOW_PLUGIN_CONNECT_TIMEOUT_MS
      else process.env.WEFLOW_PLUGIN_CONNECT_TIMEOUT_MS = previous
      await closePlugins()
    }
  })

  test('知识源：不进 MCP 工具表，doctor 说"就绪"而不是"可连通"', async () => {
    await reset()
    addPlugin(makePlugin('notes', { kind: 'knowledge-source' }))
    const restore = setConnectorFactory(fakeConnector([], ['should-not-appear']))
    try {
      assert.deepEqual(await listPluginTools(), [],
        '采集器没有工具，硬连它只会超时；它不该出现在工具表里')
      const [record] = listPlugins()
      const health = await checkPluginHealth(record)
      assert.equal(health.kind, 'knowledge-source')
      assert.equal(health.ok, true)
      assert.equal(health.toolCount, 0)
      assert.ok(health.warnings.some(w => w.includes('plugin run')), '要告诉用户用哪条命令跑它')
      // 即使客户端硬调，也不能转发给一个不是 MCP server 的进程
      assert.match(await callPluginTool('plugin.notes.x', { confirm: true }), /不提供 MCP 工具/)
    } finally {
      restore()
      await closePlugins()
    }
  })

  test('知识源：作业从 stdin 进去，产出落在 out_dir', async () => {
    await reset()
    // 一个真的采集器：读作业、写一张卡。用 `node -e` 起，省掉再造一个插件目录。
    const script = [
      "let s=''",
      "process.stdin.on('data',d=>s+=d).on('end',()=>{",
      'const j=JSON.parse(s)',
      "require('fs').writeFileSync(require('path').join(j.out_dir,'card.md'),'# 卡片')",
      "process.stdout.write(JSON.stringify({success:true,cards:1,input:!!j.out_dir}))",
      '})',
    ].join(';')
    addPlugin(makePlugin('notes', {
      kind: 'knowledge-source',
      entry: { command: process.execPath, args: ['-e', script] },
    }))
    const outDir = join(SANDBOX, 'out-notes')
    const [record] = listPlugins()
    const run = await runKnowledgeSource(record, { outDir, since: '2026-10-01' })
    assert.equal(run.ok, true, '退出码 0 才算成功')
    assert.equal(run.outDir, outDir)
    assert.match(run.stdout, /"cards":1/, 'stdout 里是插件给结构化结果的地方')
    assert.ok(existsSync(join(outDir, 'card.md')), '产出的卡片应该真的落到 out_dir')
  })

  test('知识源：起不来时把原因带回来，而不是抛异常', async () => {
    await reset()
    addPlugin(makePlugin('notes', {
      kind: 'knowledge-source',
      entry: { command: join(SANDBOX, 'no-such-binary'), args: [] },
    }))
    const [record] = listPlugins()
    const run = await runKnowledgeSource(record, { outDir: join(SANDBOX, 'out2') })
    assert.equal(run.ok, false)
    assert.match(String(run.error), /启动失败|退出码/)
  })

  test('知识源：MCP 工具插件不能被当成采集器跑', async () => {
    await reset()
    addPlugin(makePlugin('toolsonly'))
    const [record] = listPlugins()
    const run = await runKnowledgeSource(record)
    assert.equal(run.ok, false)
    assert.match(String(run.error), /不是知识源类型/)
  })

  test('卡片体检：结果来自消费端脚本，解析失败要报出来而不是当成 0 张', async () => {
    const restore = setScriptRunner(async () => ({
      stdout: JSON.stringify({ success: true, cards: 3, readable: 2, links: 5, skipped: [{ file: 'b.md', reason: '没有 wikilink' }] }),
      stderr: '',
      code: 0,
    }))
    try {
      const lint = await lintCards('whatever')
      assert.deepEqual(
        { ok: lint.ok, cards: lint.cards, readable: lint.readable, links: lint.links },
        { ok: true, cards: 3, readable: 2, links: 5 },
      )
      assert.equal(lint.skipped[0].file, 'b.md')
    } finally {
      setScriptRunner(null)
    }

    // 失败路径：脚本报错时 ok=false，**不能**悄悄变成"0 张、看起来没事"
    setScriptRunner(async () => ({ stdout: '', stderr: 'boom', code: 2 }))
    try {
      const lint = await lintCards('whatever')
      assert.equal(lint.ok, false)
      assert.ok(lint.error, '失败必须带原因')
    } finally {
      setScriptRunner(null)
    }
  })

  test('doctor：连不上时给出错误与提示，而不是抛异常', async () => {
    await reset()
    addPlugin(makePlugin('broken'))
    const restore = setConnectorFactory((): PluginConnector => ({
      async listTools() { throw new Error('命令不存在') },
      async callTool() { throw new Error('命令不存在') },
      async close() {},
    }))
    try {
      const [record] = listPlugins()
      const health = await checkPluginHealth(record)
      assert.equal(health.ok, false)
      assert.match(String(health.error), /命令不存在/)
      assert.equal(health.toolCount, 0)
      assert.ok(health.warnings.some(w => w.includes('confirm')), '未信任必须作为提示出现')
    } finally {
      restore()
      await closePlugins()
    }
  })
})
