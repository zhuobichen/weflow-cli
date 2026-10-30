/**
 * MCP 工具插件：把**外部进程**里的 MCP server 并进本项目的工具面。
 *
 * 为什么是"外部进程"而不是加载 JS：`docs/DECISIONS.md` 的 D-048 已经否决了进程内插件加载器
 * —— 进程内的插件拿得到解密的数据库路径、含全部密钥的配置、文件系统和消息通道，而"清单里声明
 * 了能力"救不了它，因为插件没有被沙箱。这里的边界靠三件事成立，不靠信任：
 *
 *   1. **进程外**：插件只能通过 stdio 的 MCP 协议说话，拿不到本进程的任何对象；
 *   2. **不喂密钥**：子进程环境是**白名单重建**的（见 `childEnv()`），不是继承 `process.env`
 *      —— 后者里有 `typesafeApiKey` 之类的一切；
 *   3. **默认要确认**：未标记 `trusted` 的插件，工具调用必须带 `confirm: true`，否则只回预览。
 *
 * 还有一条是**刻意不做**的：不假装沙箱。声明里的 `permissions` 只用于展示和提醒，宿主无法
 * 核实一个进程实际干了什么。所以 `plugin doctor` 会把声明原样列出来，并明确说它是声明而非保证
 * —— D-048 批评过"清单式能力声明"，这里不能一边引用它一边犯同样的错。
 */
import { existsSync, mkdirSync, readFileSync, statSync } from 'fs'
import { spawn } from 'child_process'
import { homedir } from 'os'
import { join, dirname, resolve } from 'path'
import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'
import { writeFileAtomic } from '../utils/atomicWrite.js'
import { resolvePackageRoot } from '../utils/packageRoot.js'
import { runPythonJson } from './pythonBridge.js'

/** 清单格式的版本。破坏性改动升 v2，并让宿主双读一个版本，否则没人敢写插件。 */
export const PLUGIN_SCHEMA = 'weflow-plugin/1'
/** 本机支持的契约版本。契约是最贵的一层：改动它要付兼容代价，所以版本号必须能谈。 */
export const PLUGIN_SCHEMA_VERSION = 1

/**
 * 契约版本协商。
 *
 * 只拒绝不解释是不够的：插件作者写 `weflow-plugin/2` 时，使用者应该看到
 * **"这个插件要求更新的 WeFlow"**，而不是一句"格式错了"——后者会让他去改清单，
 * 而正确的动作是升级宿主。这条消息的方向决定了谁会去修。
 */
function checkSchemaVersion(schema: string): void {
  const match = /^weflow-plugin\/(\d+)$/.exec(schema)
  if (!match) {
    throw new Error(`清单 schema "${schema}" 不是 weflow-plugin/<数字> 的形状（例如 ${PLUGIN_SCHEMA}）`)
  }
  const version = Number(match[1])
  if (version > PLUGIN_SCHEMA_VERSION) {
    throw new Error(
      `这个插件要求 ${schema}，而本机只支持到 ${PLUGIN_SCHEMA}；` +
      `请升级 weflow-cli（插件本身不需要改）`,
    )
  }
  if (version < PLUGIN_SCHEMA_VERSION) {
    throw new Error(
      `这个插件用的是 ${schema}，本机已经是 ${PLUGIN_SCHEMA}；` +
      `旧契约不再支持，请让插件作者升级清单`,
    )
  }
}
/** 插件工具在**本服务**里的命名空间前缀，避免和 `wechat.*` 撞名。 */
export const PLUGIN_TOOL_PREFIX = 'plugin.'

/** 清单里的 id 规则：小写字母开头，只含小写字母/数字/连字符。点号被排除，因为它是命名空间分隔符。 */
const ID_RE = /^[a-z][a-z0-9-]{1,38}$/
/** 工具名由插件自己决定，但必须是 MCP 能表达的名字；含点号会让 `plugin.<id>.<tool>` 解析有歧义。 */
const TOOL_NAME_RE = /^[A-Za-z0-9_-]{1,64}$/

const LIST_TIMEOUT_MS = 15_000
const CALL_TIMEOUT_MS = 120_000
/**
 * 握手超时。**没有它，一个起不来的插件会把整个 ListTools 挂死** —— 实测过：写了个一直等
 * stdin 的插件，`client.connect()` 永远不返回，而 `ListTools` 在 `await ready` 上排队，
 * 于是本服务的工具表永远不响应。一次挂起就够毁掉所有客户端的连接。
 * 可从环境改，好让测试用几百毫秒验证这条路径（也方便慢机器上调）。
 */
function connectTimeoutMs(): number {
  const raw = Number(process.env.WEFLOW_PLUGIN_CONNECT_TIMEOUT_MS)
  return Number.isFinite(raw) && raw >= 100 ? raw : 20_000
}

export interface PluginPermissions {
  /** 声明"会访问网络"。仅用于展示与 doctor 提示，**不是**强制。 */
  network?: boolean
  /** 声明"会写这些路径"。同上，仅声明。 */
  writes?: string[]
}

export interface PluginManifest {
  schema: string
  id: string
  name: string
  version: string
  /**
   * 两种插件，形状完全不同，所以必须分开说：
   *   - `mcp-server`：起一个 MCP server，工具并进工具表；
   *   - `knowledge-source`：起一个**采集器**，往卡片目录写 `.md`，由知识库线消费。
   * 采集器不提供 MCP 工具，MCP 工具也不写卡片——混成一种会让两边都说不清。
   */
  kind: PluginKind
  description?: string
  entry: { command: string; args: string[]; cwd?: string }
  permissions: PluginPermissions
}

export type PluginKind = 'mcp-server' | 'knowledge-source'

/** 采集器的作业：通过 **stdin** 交给插件。为什么不用 argv —— 命令行在进程列表里可见。 */
export interface KnowledgeSourceJob {
  schema: string
  job: 'collect'
  id: string
  /** 插件唯一被允许写入的目录（宿主创建并校验）。 */
  out_dir: string
  /** 可选输入目录，来自 `plugin run --input`。 */
  input_dir?: string
  since?: string
  until?: string
}

export interface PluginRecord {
  id: string
  /** 插件目录（绝对路径）。不写进 `plugin list --json` 的默认输出。 */
  path: string
  enabled: boolean
  /**
   * 用户显式表示"这个插件的工具不用每次确认"。默认 false。
   * 它是一个**人工决定**，不是对插件的评估——所以它只能由 `plugin trust` 明确设置。
   */
  trusted: boolean
  addedAt: string
  manifest: PluginManifest
}

export interface PluginRegistry {
  version: 1
  plugins: PluginRecord[]
}

export interface PluginToolDescriptor {
  /** 已经带上 `plugin.<id>.` 前缀，可直接放进 ListTools。 */
  name: string
  description: string
  inputSchema: Record<string, unknown>
  pluginId: string
  /** 插件侧的原名（`plugin call` 与代理转发都用它）。 */
  toolName: string
}

/** 连接器：真实现是 stdio 的 MCP 客户端；测试里换成假的，免得每个用例都起进程。 */
export interface PluginConnector {
  listTools(): Promise<Array<{ name: string; description?: string; inputSchema?: unknown }>>
  callTool(name: string, args: Record<string, unknown>): Promise<string>
  close(): Promise<void>
}

export type ConnectorFactory = (manifest: PluginManifest, dir: string) => PluginConnector

// ---- 路径 ----

export function pluginRoot(): string {
  // 可覆盖，**为了测试**：用例不能读写用户真实的 `~/.weflow-cli`。这与
  // `WEFLOW_ASSISTANT_SKILL_DIRS` 是同一个理由和同一个形状。
  const override = process.env.WEFLOW_PLUGIN_HOME
  return override ? resolve(override) : join(homedir(), '.weflow-cli', 'plugins')
}

export function registryFile(): string {
  const override = process.env.WEFLOW_PLUGIN_REGISTRY
  return override ? resolve(override) : join(homedir(), '.weflow-cli', 'plugins.json')
}

// ---- 清单校验 ----

function asString(value: unknown, field: string): string {
  if (typeof value !== 'string' || !value.trim()) throw new Error(`清单缺少 ${field}（或它不是非空字符串）`)
  return value.trim()
}

/**
 * 校验一份清单。**失败要说清是哪一条**：插件作者的第一次体验就是这条报错，
 * 而 "invalid manifest" 会让他去猜。
 */
export function validateManifest(raw: unknown): PluginManifest {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('清单必须是一个 JSON 对象')
  const obj = raw as Record<string, unknown>

  const schema = asString(obj.schema, 'schema')
  checkSchemaVersion(schema)

  const id = asString(obj.id, 'id')
  if (!ID_RE.test(id)) {
    throw new Error(`id "${id}" 不合法：要小写字母开头，只含小写字母/数字/连字符，长度 2-39`)
  }

  const kind = asString(obj.kind, 'kind')
  if (kind !== 'mcp-server' && kind !== 'knowledge-source') {
    throw new Error(`kind "${kind}" 不支持；这一版认 "mcp-server"（提供工具）和 "knowledge-source"（产出卡片）`)
  }

  const entryRaw = obj.entry
  if (!entryRaw || typeof entryRaw !== 'object' || Array.isArray(entryRaw)) {
    throw new Error('清单缺少 entry（形如 { "command": "node", "args": ["index.js"] }）')
  }
  const entry = entryRaw as Record<string, unknown>
  const command = asString(entry.command, 'entry.command')
  const argsRaw = entry.args === undefined ? [] : entry.args
  if (!Array.isArray(argsRaw) || argsRaw.some(a => typeof a !== 'string')) {
    throw new Error('entry.args 必须是字符串数组（没有参数就省略或写 []）')
  }
  if (entry.cwd !== undefined && typeof entry.cwd !== 'string') {
    throw new Error('entry.cwd 必须是字符串')
  }

  const permsRaw = obj.permissions
  const permissions: PluginPermissions = {}
  if (permsRaw !== undefined) {
    if (!permsRaw || typeof permsRaw !== 'object' || Array.isArray(permsRaw)) {
      throw new Error('permissions 必须是对象')
    }
    const p = permsRaw as Record<string, unknown>
    if (p.network !== undefined) {
      if (typeof p.network !== 'boolean') throw new Error('permissions.network 必须是 true/false')
      permissions.network = p.network
    }
    if (p.writes !== undefined) {
      if (!Array.isArray(p.writes) || p.writes.some(w => typeof w !== 'string')) {
        throw new Error('permissions.writes 必须是字符串数组')
      }
      permissions.writes = p.writes as string[]
    }
  }

  return {
    schema,
    id,
    name: asString(obj.name, 'name'),
    version: asString(obj.version, 'version'),
    kind,
    description: typeof obj.description === 'string' ? obj.description : undefined,
    entry: {
      command,
      args: argsRaw as string[],
      ...(typeof entry.cwd === 'string' ? { cwd: entry.cwd } : {}),
    },
    permissions,
  }
}

// ---- 注册表 ----

function emptyRegistry(): PluginRegistry {
  return { version: 1, plugins: [] }
}

/**
 * 读注册表。**坏掉的注册表按空处理并保留原文件**（不静默覆盖）：
 * 覆盖会把用户装过的插件列表一次性抹掉，而这份文件是可以手改的。
 */
export function readRegistry(): PluginRegistry {
  const file = registryFile()
  if (!existsSync(file)) return emptyRegistry()
  try {
    const data = JSON.parse(readFileSync(file, 'utf8')) as unknown
    if (!data || typeof data !== 'object' || Array.isArray(data)) return emptyRegistry()
    const obj = data as Record<string, unknown>
    if (obj.version !== 1) return emptyRegistry()
    if (!Array.isArray(obj.plugins)) return emptyRegistry()
    const plugins: PluginRecord[] = []
    for (const item of obj.plugins) {
      if (!item || typeof item !== 'object') continue
      const rec = item as Record<string, unknown>
      if (typeof rec.id !== 'string') continue
      plugins.push({
        id: rec.id,
        path: typeof rec.path === 'string' ? rec.path : '',
        enabled: rec.enabled !== false,
        trusted: rec.trusted === true,
        addedAt: typeof rec.addedAt === 'string' ? rec.addedAt : '',
        manifest: rec.manifest as PluginManifest,
      })
    }
    return { version: 1, plugins }
  } catch {
    return emptyRegistry()
  }
}

function writeRegistry(registry: PluginRegistry): void {
  const file = registryFile()
  const dir = dirname(file)
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true })
  writeFileAtomic(file, JSON.stringify(registry, null, 2))
}

export function listPlugins(): PluginRecord[] {
  return readRegistry().plugins
}

/** 从插件目录读 `weflow-plugin.json` 并校验。目录不存在时给出可操作的报错。 */
export function readManifestFrom(dir: string): PluginManifest {
  const target = resolve(dir)
  if (!existsSync(target) || !statSync(target).isDirectory()) {
    throw new Error(`不是一个目录：${target}`)
  }
  const manifestPath = join(target, 'weflow-plugin.json')
  if (!existsSync(manifestPath)) {
    throw new Error(`目录里没有 weflow-plugin.json：${target}`)
  }
  let raw: unknown
  try {
    raw = JSON.parse(readFileSync(manifestPath, 'utf8'))
  } catch (e) {
    throw new Error(`weflow-plugin.json 不是合法 JSON：${e instanceof Error ? e.message : e}`)
  }
  return validateManifest(raw)
}

/** 装一个插件。重复装同一个 id 直接报错，而不是悄悄覆盖——覆盖会让用户以为更新了。 */
export function addPlugin(dir: string): PluginRecord {
  const target = resolve(dir)
  const manifest = readManifestFrom(target)
  const registry = readRegistry()
  if (registry.plugins.some(p => p.id === manifest.id)) {
    throw new Error(`插件 "${manifest.id}" 已经装过了；先 \`weflow-cli plugin remove ${manifest.id}\` 再来`)
  }
  const record: PluginRecord = {
    id: manifest.id,
    path: target,
    enabled: true,
    trusted: false,
    addedAt: new Date().toISOString(),
    manifest,
  }
  registry.plugins.push(record)
  writeRegistry(registry)
  return record
}

export function removePlugin(id: string): boolean {
  const registry = readRegistry()
  const before = registry.plugins.length
  registry.plugins = registry.plugins.filter(p => p.id !== id)
  if (registry.plugins.length === before) return false
  writeRegistry(registry)
  return true
}

/** 改 enabled / trusted。两个开关都只影响本机行为，不动插件目录。 */
export function setPluginFlags(id: string, flags: { enabled?: boolean; trusted?: boolean }): PluginRecord {
  const registry = readRegistry()
  const record = registry.plugins.find(p => p.id === id)
  if (!record) throw new Error(`没有这个插件：${id}`)
  if (flags.enabled !== undefined) record.enabled = flags.enabled
  if (flags.trusted !== undefined) record.trusted = flags.trusted
  writeRegistry(registry)
  return record
}

// ---- 连接器 ----

/**
 * 子进程环境**白名单重建**。
 *
 * 这是整个边界里最容易写错的一行：把 `process.env` 整个传给子进程，等于把 `typesafeApiKey`、
 * `DASHSCOPE_API_KEY` 和一切路径都交给第三方代码，"进程外"就只剩个形式。
 * 只给运行必需品。
 */
export function childEnv(pluginId: string): Record<string, string> {
  const env: Record<string, string> = {
    WEFLOW_PLUGIN_ID: pluginId,
    // 插件常是 node 脚本，这两个不给就跑不起来
    PATH: process.env.PATH || process.env.Path || '',
  }
  for (const key of ['SystemRoot', 'TEMP', 'TMP', 'HOME', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'ComSpec', 'PATHEXT']) {
    const value = process.env[key]
    if (value) env[key] = value
  }
  return env
}

/** 真连接器：起一个 stdio MCP 客户端。 */
function stdioConnector(manifest: PluginManifest, dir: string): PluginConnector {
  const transport = new StdioClientTransport({
    command: manifest.entry.command,
    args: manifest.entry.args,
    cwd: manifest.entry.cwd ? resolve(dir, manifest.entry.cwd) : dir,
    env: childEnv(manifest.id),
    stderr: 'ignore',
  })
  const client = new Client({ name: `weflow-plugin-${manifest.id}`, version: '1.0.0' }, { capabilities: {} })
  // 先挂一个吞掉的 catch：连接失败时若只有 `await ready` 在读它，超时分支会留下未处理的拒绝
  const ready = client.connect(transport)
  ready.catch(() => {})

  const withTimeout = async <T>(work: Promise<T>, ms: number, what: string): Promise<T> => {
    let timer: NodeJS.Timeout | undefined
    try {
      return await Promise.race([
        work,
        new Promise<never>((_, reject) => {
          timer = setTimeout(() => reject(new Error(`${manifest.id}: ${what} 超时（${ms}ms）`)), ms)
        }),
      ])
    } finally {
      if (timer) clearTimeout(timer)
    }
  }

  const ensureReady = () => withTimeout(ready, connectTimeoutMs(), 'initialize 握手')

  return {
    async listTools() {
      await ensureReady()
      const result = await withTimeout(client.listTools(), LIST_TIMEOUT_MS, 'listTools')
      return (result.tools || []).map(tool => ({
        name: tool.name,
        description: tool.description,
        inputSchema: tool.inputSchema,
      }))
    },
    async callTool(name, args) {
      await ensureReady()
      const result = await withTimeout(client.callTool({ name, arguments: args }), CALL_TIMEOUT_MS, `callTool(${name})`)
      const parts = Array.isArray(result.content) ? result.content : []
      const text = parts
        .map(part => (part && typeof part === 'object' && (part as any).type === 'text' ? String((part as any).text ?? '') : ''))
        .filter(Boolean)
        .join('\n')
      const body = text || '（插件没有返回文本内容）'
      return (result as any).isError ? `（插件返回错误）${body}` : body
    },
    async close() {
      try { await client.close() } catch { /* 关一个已经断掉的连接不该让调用方失败 */ }
    },
  }
}

let connectorFactory: ConnectorFactory = stdioConnector

/** 仅供测试：换掉连接器工厂。返回还原函数，避免用例之间互相污染。 */
export function setConnectorFactory(factory: ConnectorFactory): () => void {
  const previous = connectorFactory
  connectorFactory = factory
  return () => { connectorFactory = previous }
}

// 连接缓存：**每个插件一条**，按需建立。进程崩溃或超时后丢掉缓存，下次重连。
const connections = new Map<string, { connector: PluginConnector; errors: number }>()

async function connectorFor(record: PluginRecord): Promise<PluginConnector> {
  const existing = connections.get(record.id)
  if (existing) return existing.connector
  const connector = connectorFactory(record.manifest, record.path)
  connections.set(record.id, { connector, errors: 0 })
  return connector
}

async function dropConnection(id: string): Promise<void> {
  const entry = connections.get(id)
  if (!entry) return
  connections.delete(id)
  await entry.connector.close()
}

/** 关掉全部连接。MCP 服务退出时调用，否则子进程会挂在后台。 */
export async function closePlugins(): Promise<void> {
  const ids = [...connections.keys()]
  await Promise.all(ids.map(id => dropConnection(id)))
}

/**
 * 列出所有**已启用**插件的工具，带 `plugin.<id>.` 前缀。
 *
 * 单个插件启动失败**不影响其它插件，也不让 ListTools 失败**：一个写坏的第三方插件
 * 不该让整个 MCP 服务不可用。失败只记在诊断里，由 `plugin doctor` 报出来。
 */
export async function listPluginTools(): Promise<PluginToolDescriptor[]> {
  // 只要 `mcp-server`：采集器没有工具，硬连它只会超时。
  const records = readRegistry().plugins.filter(p => p.enabled && p.manifest.kind === 'mcp-server')
  const out: PluginToolDescriptor[] = []
  for (const record of records) {
    try {
      const connector = await connectorFor(record)
      const tools = await connector.listTools()
      for (const tool of tools) {
        if (!TOOL_NAME_RE.test(tool.name)) continue
        out.push({
          name: `${PLUGIN_TOOL_PREFIX}${record.id}.${tool.name}`,
          description: String(tool.description || record.manifest.description || `${record.manifest.name} 提供的工具`),
          inputSchema: (tool.inputSchema as Record<string, unknown>) || { type: 'object', properties: {} },
          pluginId: record.id,
          toolName: tool.name,
        })
      }
    } catch {
      // 起不来就跳过；doctor 会说明原因
      await dropConnection(record.id).catch(() => {})
    }
  }
  return out
}

/** `plugin.<id>.<tool>` → 记录与插件侧工具名。id 里不含点，所以切两刀是确定的。 */
export function parsePluginToolName(name: string): { id: string; toolName: string } | null {
  if (!name.startsWith(PLUGIN_TOOL_PREFIX)) return null
  const rest = name.slice(PLUGIN_TOOL_PREFIX.length)
  const dot = rest.indexOf('.')
  if (dot <= 0) return null
  const id = rest.slice(0, dot)
  const toolName = rest.slice(dot + 1)
  if (!ID_RE.test(id) || !TOOL_NAME_RE.test(toolName)) return null
  return { id, toolName }
}

/**
 * 调一个插件工具。
 *
 * **确认门在这里**，因为插件是第三方代码而宿主无法核实它做什么：没被 `trusted` 的插件，
 * 调用必须显式带 `confirm: true`，否则只回一段预览。`confirm` 本身**不转发**给插件
 * —— 那是本服务的参数，不是它 schema 里的。
 */
export async function callPluginTool(
  fullName: string,
  args: Record<string, unknown>,
): Promise<string> {
  const parsed = parsePluginToolName(fullName)
  if (!parsed) return `未知工具: ${fullName}`
  const record = readRegistry().plugins.find(p => p.id === parsed.id)
  if (!record) return `插件 "${parsed.id}" 没有安装`
  if (!record.enabled) return `插件 "${parsed.id}" 已禁用（weflow-cli plugin enable ${parsed.id}）`
  if (record.manifest.kind !== 'mcp-server') {
    return `插件 "${parsed.id}" 是 ${record.manifest.kind} 类型，不提供 MCP 工具`
  }

  if (!record.trusted && args.confirm !== true) {
    return [
      `（需要确认）${record.manifest.name} 的工具 ${parsed.toolName} 会把数据交给第三方插件进程。`,
      '确认要调用请加 confirm: true；若这个插件可信，可执行 `weflow-cli plugin trust ' + record.id + '` 一次。',
      `参数：${JSON.stringify({ ...args, confirm: undefined }, null, 2)}`,
    ].join('\n')
  }

  const forwarded: Record<string, unknown> = { ...args }
  delete forwarded.confirm

  try {
    const connector = await connectorFor(record)
    return await connector.callTool(parsed.toolName, forwarded)
  } catch (e) {
    await dropConnection(record.id).catch(() => {})
    return `（插件调用失败）${e instanceof Error ? e.message : String(e)}`
  }
}

/** `plugin doctor` 的每一项：能不能起来、声明了什么、有什么风险提示。 */
export interface PluginHealth {
  id: string
  name: string
  version: string
  kind: PluginKind
  enabled: boolean
  trusted: boolean
  ok: boolean
  error?: string
  toolCount: number
  tools: string[]
  permissions: PluginPermissions
  warnings: string[]
}

export async function checkPluginHealth(record: PluginRecord): Promise<PluginHealth> {
  const warnings: string[] = []
  const permissions = record.manifest.permissions || {}
  const kind = record.manifest.kind
  if (permissions.network) {
    warnings.push('声明会访问网络；这条只是声明，宿主无法核实')
  }
  if (kind === 'mcp-server' && !record.trusted) {
    warnings.push('未信任：每次调用都需要 confirm: true')
  }
  if (!record.enabled) {
    warnings.push(kind === 'knowledge-source' ? '已禁用：插件不会运行' : '已禁用：工具不会出现在 MCP 工具表里')
  }

  // 采集器**不在 doctor 里跑**：它会抓取、会写卡片，`doctor` 是一次只读体检。
  // 想真正跑一次用 `plugin run <id>`。所以这里的 ok 只说"清单和入口没问题"。
  if (kind === 'knowledge-source') {
    if (!record.enabled) {
      return { ...baseHealth(record, warnings, permissions, kind), ok: false, error: '已禁用' }
    }
    if (!record.manifest.entry.command) {
      return { ...baseHealth(record, warnings, permissions, kind), ok: false, error: '清单缺少 entry.command' }
    }
    warnings.push('知识源插件：不会出现在 MCP 工具表里；用 `plugin run` 产出卡片')
    return { ...baseHealth(record, warnings, permissions, kind), ok: true }
  }

  let ok = false
  let error: string | undefined
  let tools: string[] = []
  if (record.enabled) {
    try {
      const connector = await connectorFor(record)
      const listed = await connector.listTools()
      tools = listed.map(t => t.name)
      ok = true
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
      await dropConnection(record.id).catch(() => {})
    }
  }

  return {
    ...baseHealth(record, warnings, permissions, kind),
    ok,
    error,
    toolCount: tools.length,
    tools,
  }
}

function baseHealth(
  record: PluginRecord,
  warnings: string[],
  permissions: PluginPermissions,
  kind: PluginKind,
): PluginHealth {
  return {
    id: record.id,
    name: record.manifest.name,
    version: record.manifest.version,
    kind,
    enabled: record.enabled,
    trusted: record.trusted,
    ok: false,
    toolCount: 0,
    tools: [],
    permissions,
    warnings,
  }
}

// ---- 知识源插件 ----

export interface SourceRunResult {
  ok: boolean
  outDir: string
  code: number | null
  stdout: string
  stderr: string
  error?: string
}

const SOURCE_OUTPUT_CAP = 200_000

function outputDirFor(id: string): string {
  return join(resolvePackageRoot(import.meta.url), 'output', `${id}-notes`)
}

/**
 * 跑一次采集器。
 *
 * 作业从 **stdin** 进去，不从 argv：命令行在进程列表里对任何本地进程可见，而作业里可能带
 * 日期范围甚至输入目录。插件只被允许往 `out_dir` 写——宿主建好它再传进去，插件拿不到
 * 别的路径，也不需要知道知识库长什么样。
 */
export async function runKnowledgeSource(
  record: PluginRecord,
  opts: { since?: string; until?: string; inputDir?: string; outDir?: string; timeoutMs?: number } = {},
): Promise<SourceRunResult> {
  if (record.manifest.kind !== 'knowledge-source') {
    return { ok: false, outDir: '', code: null, stdout: '', stderr: '', error: `插件 "${record.id}" 不是知识源类型` }
  }
  if (!record.enabled) {
    return { ok: false, outDir: '', code: null, stdout: '', stderr: '', error: `插件 "${record.id}" 已禁用` }
  }

  const outDir = opts.outDir ? resolve(opts.outDir) : outputDirFor(record.id)
  mkdirSync(outDir, { recursive: true })

  const job: KnowledgeSourceJob = {
    schema: PLUGIN_SCHEMA,
    job: 'collect',
    id: record.id,
    out_dir: outDir,
    ...(opts.inputDir ? { input_dir: resolve(opts.inputDir) } : {}),
    ...(opts.since ? { since: opts.since } : {}),
    ...(opts.until ? { until: opts.until } : {}),
  }

  const timeoutMs = opts.timeoutMs ?? 10 * 60_000
  return await new Promise<SourceRunResult>((resolvePromise) => {
    let stdout = ''
    let stderr = ''
    let settled = false
    const finish = (result: SourceRunResult) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolvePromise(result)
    }

    const child = spawn(record.manifest.entry.command, record.manifest.entry.args, {
      cwd: record.manifest.entry.cwd ? resolve(record.path, record.manifest.entry.cwd) : record.path,
      env: childEnv(record.id),
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    })

    const timer = setTimeout(() => {
      try { child.kill() } catch { /* 已经退了 */ }
      finish({ ok: false, outDir, code: null, stdout, stderr, error: `采集超时（${timeoutMs}ms）` })
    }, timeoutMs)

    child.stdout?.on('data', (chunk: Buffer) => {
      if (stdout.length < SOURCE_OUTPUT_CAP) stdout += chunk.toString('utf8')
    })
    child.stderr?.on('data', (chunk: Buffer) => {
      if (stderr.length < SOURCE_OUTPUT_CAP) stderr += chunk.toString('utf8')
    })
    child.on('error', (error: Error) => {
      finish({ ok: false, outDir, code: null, stdout, stderr, error: `启动失败：${error.message}` })
    })
    child.on('close', (code) => {
      finish({
        ok: code === 0,
        outDir,
        code,
        stdout: stdout.slice(0, SOURCE_OUTPUT_CAP),
        stderr: stderr.slice(0, SOURCE_OUTPUT_CAP),
        ...(code === 0 ? {} : { error: `插件退出码 ${code}` }),
      })
    })

    try {
      child.stdin?.end(JSON.stringify(job))
    } catch {
      finish({ ok: false, outDir, code: null, stdout, stderr, error: '作业写不进 stdin' })
    }
  })
}

export interface CardLintSkip {
  file: string
  reason: string
}

/** 卡片体检结果。`readable` 是**消费端真的读出来的**张数，不是数文件数。 */
export interface CardLint {
  ok: boolean
  cards: number
  readable: number
  links: number
  skipped: CardLintSkip[]
  error?: string
}

/**
 * 把插件产出的卡片交给**真正的消费端**读一遍（`compile_wiki.scan_articles`）。
 *
 * 为什么不在 TypeScript 里实现一遍卡片规则：那样就是第二份契约，改了 Python 那边不会红。
 * 这个仓库的规矩是"不要对着 markdown 文本断言，要断言另一端读得出来"——这里照办。
 */
export async function lintCards(dir: string): Promise<CardLint> {
  const result = await runPythonJson<{
    cards: number
    readable: number
    links: number
    skipped: CardLintSkip[]
  }>('plugin_card_lint.py', ['--cards', dir], { timeoutMs: 60_000 })
  if (!result.ok) {
    return { ok: false, cards: 0, readable: 0, links: 0, skipped: [], error: result.error }
  }
  const data = result.data || { cards: 0, readable: 0, links: 0, skipped: [] }
  return {
    ok: true,
    cards: Number(data.cards) || 0,
    readable: Number(data.readable) || 0,
    links: Number(data.links) || 0,
    skipped: Array.isArray(data.skipped) ? data.skipped : [],
  }
}
