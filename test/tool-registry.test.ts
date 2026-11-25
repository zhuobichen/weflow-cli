/**
 * 工具表的**一致性**：一张声明表和四个消费它的地方，必须说的是同一批工具。
 *
 * 为什么值得单独一个文件：这个仓库里"加一个工具"是改**好几处**手写的字面量（声明表、`executeTool`
 * 的 switch、可用性规则、MCP 排除表、文档表格），而这五处**互相之间没有任何断言**。最先坏掉的那种
 * 方式是静默的——往声明表里加一条、忘了加 `case`，模型照样看得见它、调它，然后收到一句
 * `(未知工具: x)`；反过来只加 `case` 忘了声明，它就是个谁也调不到的死代码。两种都不会让别人红。
 *
 * 这个文件就是照这个仓库吃过的亏写的（2026-09-25：`capabilities` 里的 `requiresConfirm` 少报了
 * 三个工具，而它的断言是照着字段的值抄的，于是字段和测试一起错、谁都不响）。所以这里的做法一律是
 * **从源码里数**，不是把当前的值再抄一遍。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { MCP_EXCLUDED, MCP_TOOL_DEFS, TOOL_DEFS, TOOL_REQUIREMENTS } from '../src/services/assistantTools.js'

const read = (...parts: string[]) => readFileSync(join(process.cwd(), ...parts), 'utf8')
const toolsSource = read('src', 'services', 'assistantTools.ts')
const mcpSource = read('mcp-server', 'index.ts')
const mcpDoc = read('docs', 'MCP.md')

const declared = TOOL_DEFS.map(tool => tool.function.name)

/**
 * `executeTool` 里出现的工具名。
 *
 * **2026-11-24 改法**：原来是从 `assistantTools.ts` 的源码文本里正则抓 `case 'x':`。
 * 那个依赖很脆——拆文件、换写法、加注释都可能让它误判，而且误判的方向有两种：
 * 正则没抓到 → 误报"缺 case"（红得莫名其妙）；正则抓到注释里的 case → 漏报。
 *
 * 现在用运行时自报：把所有声明的工具名逐个丢给 `executeTool`，它分发前会把名字记进
 * `HANDLED_TOOL_NAMES`。**与代码怎么组织无关**——拆成多少个 handler、换成查表分发，
 * 这个集合都照旧成立。
 *
 * 注意：跑一遍会真的执行工具分支。参数给空对象，让每个分支走"缺参数"早退路径
 * （它们都以 `(缺少 x 参数)` 之类返回，不碰数据库、不出网）。有副作用的几个
 * （写待办、导文件、存记忆）另有专门的测试覆盖，这里只要"它认得这个工具名"。
 */
const handled = await (async () => {
  const { executeTool, HANDLED_TOOL_NAMES } = await import('../src/services/assistantTools.js')
  for (const name of declared) {
    try {
      await executeTool(name, {}, { userId: '__registry_probe__', memory: null } as any)
    } catch { /* 抛错也算"认得了这个名字"——它进了分支才会抛 */ }
  }
  return [...HANDLED_TOOL_NAMES]
})()

/** `mcp-server` 里手写的那批（`wechat.` 前缀） */
const handWritten = [...mcpSource.matchAll(/name: '(wechat\.[a-z_]+)'/g)].map(match => match[1])

test('声明表与 executeTool 的 case 一一对应 —— 两个方向都要对上', () => {
  const missingCase = declared.filter(name => !handled.includes(name))
  const missingDecl = handled.filter(name => !declared.includes(name))
  assert.deepEqual(missingCase, [],
    '这些工具声明了却没有 case：模型看得见、调得到，然后收到一句"(未知工具)"')
  assert.deepEqual(missingDecl, [],
    '这些 case 没有对应的声明：模型永远看不到它，等于一段谁也调不到的死代码')
  assert.equal(new Set(declared).size, declared.length, '工具名不许重复')
})

test('PROJECT_STATE 里报的工具数 = 声明表的长度（加工具时那句话必须跟着动）', () => {
  // 方向是"拿声明表去比文档"，不是反过来抄一遍 —— 这个文件的规矩（见文件头：字段和测试一起抄，
  // 就会一起错、谁都不响）。
  // 为什么值得钉：那一行写的是"31 local tools … **all 31 tool branches**"，**后一句是关于测试覆盖
  // 的声明**，而"加了一个工具"正是该重审它的时候。数不跟着动，那句"全分支覆盖"就会静默变假 ——
  // 那一行自己的历史注释里就记着：早先的版本**在测试存在之前**就宣称过这覆盖。
  // ⚠️ 只钉**当前声称**（`local tools` / `tool branches`）。同一份文档里还有一句
  // "31 tools were walked one by one against the case file" —— 那讲的是过去某次审计，硬让它跟着
  // 今天的数走就是**篡改历史**，所以不钉它。
  const state = read('docs', 'PROJECT_STATE.md')
  const claims = [...state.matchAll(/(\d+)\s+(local tools|tool branches)/g)]
  assert.ok(claims.length >= 2, `没找到那几句工具数声明（改写了？只找到 ${claims.length} 处）`)
  for (const [, number, phrase] of claims) {
    assert.equal(Number(number), declared.length,
      `PROJECT_STATE 里写的是 "${number} ${phrase}"，而声明表里是 ${declared.length} 个`)
  }
})

test('每条声明都是可用的：有描述、参数是 object、名字是小写下划线', () => {
  for (const tool of TOOL_DEFS) {
    const { name, description, parameters } = tool.function
    assert.equal(tool.type, 'function')
    assert.match(name, /^[a-z][a-z_]*$/, `${name} 不是小写下划线形状`)
    assert.ok(description.trim().length >= 10, `${name} 的描述太短，模型只能靠它决定要不要调`)
    assert.equal((parameters as any).type, 'object', `${name} 的参数不是 object`)
    assert.ok((parameters as any).properties, `${name} 缺 properties`)
  }
})

test('可用性规则与 MCP 排除表引用的都是真名字 —— 写错一个就是"这条规则永远不生效"', () => {
  // 这两张表的**值**是原因说明，写错名字不会报错，只会静默失效：
  // 前置条件永远满足不了（或永远满足），排除表排掉一个不存在的工具。
  for (const name of Object.keys(TOOL_REQUIREMENTS)) {
    assert.ok(declared.includes(name), `TOOL_REQUIREMENTS 里的 ${name} 不是真工具`)
  }
  for (const name of Object.keys(MCP_EXCLUDED)) {
    assert.ok(declared.includes(name), `MCP_EXCLUDED 里的 ${name} 不是真工具`)
  }
  assert.notDeepEqual(TOOL_REQUIREMENTS, {}, '这张表被清空过？那可用性过滤就形同虚设了')
  // 声明表里每个键都要真被读过：原因串不能是空的（空串会静默地报一句空话给用户）
  for (const [name, need] of Object.entries(TOOL_REQUIREMENTS)) {
    assert.ok(need.key.trim() && need.why.trim(), `${name} 的前置条件缺 key 或 why`)
  }
})

test('MCP 可见的那张表 = 声明表减去排除表，且排除的确实不在里面', () => {
  assert.deepEqual(MCP_TOOL_DEFS.map(tool => tool.function.name),
    declared.filter(name => !(name in MCP_EXCLUDED)))
  for (const name of Object.keys(MCP_EXCLUDED)) {
    assert.equal(MCP_TOOL_DEFS.some(tool => tool.function.name === name), false, `${name} 应当被排除`)
  }
})

test('`docs/MCP.md` 的工具表 = 真正服务的那一批，一行一个、不重不漏', () => {
  // 这张表是**用户/AI 读到的安全说明**，而且它漂过：2026-09-25 那次 `wechat.who_owes_reply`
  // 重复了两行，删掉的那一行（留着没删的那份）**恰恰是没写"聊天正文会离开本机"的旧行**——
  // 也就是说漂移的方向是"安全说明变松"。所以这里按名字对齐，重复本身就是错误。
  const documented = [...mcpDoc.matchAll(/^\| `(wechat\.[a-z_]+)` \|/gm)].map(match => match[1])
  assert.equal(new Set(documented).size, documented.length, '文档表格里同一行不该出现两次')
  // 服务面 = 手写的 11 个 ∪ 从声明表派生的那些（`get_stats` 两边都有，取并集正好去重）
  const served = [...new Set([...handWritten, ...MCP_TOOL_DEFS.map(tool => `wechat.${tool.function.name}`)])]
  assert.deepEqual([...documented].sort(), [...served].sort(),
    '文档表格与真正服务的工具对不上——多一行是虚报，少一行是漏报')
})

test('MCP 服务端的手写工具与文档里那批一致（派生那部分由上一条件保证）', () => {
  // 2026-09-29：7 个手写工具交给助手实现（工具名不变、由 TOOL_DEFS 派生），
  // 所以手写只剩 4 个：get_daily / get_concept / search_articles / export_messages
  assert.equal(handWritten.length, 4, '手写工具数变了就要同步文档表格')
  for (const name of handWritten) {
    assert.ok(mcpDoc.includes(`| \`${name}\` |`), `${name} 服务着却没写进 docs/MCP.md`)
  }
})
