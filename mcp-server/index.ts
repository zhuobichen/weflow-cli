#!/usr/bin/env npx tsx
/**
 * WeFlow MCP Server — 让 Claude Code 等 AI Agent 直接查询知识库。
 *
 * 启动: npx tsx mcp-server/index.ts                          （stdio，默认）
 *      npx tsx mcp-server/index.ts --http --token=<随机串>    （HTTP，只绑回环）
 *
 * 两条传输的工具集**完全一样**（同一张 `TOOL_DEFS` 派生表），变的只是搬运动作的方式。
 * HTTP 那条的边界（必须带令牌、只绑回环、DNS 重绑定防护）在 `httpConfig.ts` / `http.ts`，
 * 理由与用法写在 docs/MCP.md 的「HTTP 传输」一节。
 */
import { Server } from '@modelcontextprotocol/sdk/server/index.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from '@modelcontextprotocol/sdk/types.js'
import { readFileSync, readdirSync, existsSync, statSync } from 'fs'
import { join } from 'path'
import { pathToFileURL } from 'node:url'
import { safeChildPath, safeDate } from '../src/utils/mcpSecurity.js'
import { resolvePackageRoot } from '../src/utils/packageRoot.js'
import { chatService } from '../src/services/chatService.js'
import { createWeFlowEnvelope } from '../src/services/messageContract.js'
import { resolveHttpOptions } from './httpConfig.js'
import { startHttpServer } from './http.js'

// ---- 微信公众号文章抓取 ----


/** 简单的 HTML → Markdown 转换（处理微信文章常见结构） */
import { boundedToolInteger, MCP_TOOL_DEFS, executeTool } from '../src/services/assistantTools.js'
import { AssistantMemory } from '../src/services/assistantMemory.js'

// ---- 本地微信数据工具 (复用 assistant 工具层, 微信 bot / MCP server 同源) ----
// MCP 无微信用户身份, 记忆固定挂 'mcp' 用户下; 与 assistant 守护进程共用同一持久化文件
//
// `requiresConfirm` 是**这条路独有的边界**：MCP 客户端是别人家的进程，它那边谁也看不到
// 这一轮是在什么上下文里决定调用的。所以凡是"会把用户数据送出去"的工具（目前是
// `draft_reply`：把一段对话发给两个云端模型），机器调用默认只给预览，要显式带
// `confirm: true` 才真出境。面板/微信那条路不置这一位。
const mcpMemory = new AssistantMemory()
const MCP_TOOL_CTX = { userId: 'mcp', memory: mcpMemory, requiresConfirm: true }

/** assistant 工具 (OpenAI function 格式) → MCP 工具格式; get_stats 并入现有 wechat.get_stats */
// 手写表曾经有一份 `get_stats`（把知识库统计与本地数据拼起来），它由助手那边的同名工具
// 一并承担了，所以这里不再排除派生项——两处各报一半正是"同一个名字两个答案"的来源。
const ASSISTANT_TOOLS = MCP_TOOL_DEFS
  .map(t => ({
    name: `wechat.${t.function.name}`,
    description: t.function.description,
    inputSchema: t.function.parameters,
  }))

const PKG_ROOT = resolvePackageRoot(import.meta.url)
const BIZ_DAILY = join(PKG_ROOT, 'output', 'biz-daily')
// **两个概念目录**：`Wiki/Concepts` 是公众号文章线的，`Chat/Concepts` 是聊天线的
// （用户要求两条线分开）。这里必须两个都带上——只留一个的话，另一半概念页会**静默地查不到**，
// 工具照样返回"未找到概念"。与 `src/services/assistantTools.ts` 的 `VAULT_WIKI_DIRS`、
// 以及 `scripts/_utils.py` 的 `CONCEPT_DIRS` 是同一份清单，`test/concept-dirs-agreement.test.ts` 钉住。
const VAULT_WIKI_DIRS = [
  join(PKG_ROOT, 'output', 'wechat-vault', 'Wiki', 'Concepts'),
  join(PKG_ROOT, 'output', 'wechat-vault', 'Chat', 'Concepts'),
]
const VAULT_WIKI = VAULT_WIKI_DIRS[0]
const VAULT_INDEX = join(PKG_ROOT, 'output', 'wechat-vault', 'Wiki', '00-Overview.md')
const VAULT_CHAT_INDEX = join(PKG_ROOT, 'output', 'wechat-vault', 'Chat', '00-Overview.md')
const REVIEWS = join(PKG_ROOT, 'output', 'reviews', 'Daily')
const MCP_MESSAGE_LIMIT_DEFAULT = 100
const MCP_MESSAGE_LIMIT_MAX = 1000

function parseMessageDate(value: unknown, label: string): number | undefined {
  if (value === undefined || value === null || value === '') return undefined
  const date = safeDate(value)
  if (!date) throw new Error(`${label} 必须是有效的 YYYY-MM-DD 日期`)
  const [year, month, day] = date.split('-').map(Number)
  const start = new Date(year, month - 1, day)
  if (start.getFullYear() !== year || start.getMonth() !== month - 1 || start.getDate() !== day) {
    throw new Error(`${label} 必须是有效的日历日期`)
  }
  return Math.floor(start.getTime() / 1000)
}

async function resolveMcpTalker(input: unknown): Promise<string> {
  const contact = String(input || '').trim()
  if (!contact) throw new Error('contact 不能为空')
  if (contact.startsWith('wxid_') || contact.includes('@chatroom') || contact.includes('@openim')) return contact

  const sessions = await chatService.listSessions(contact, 20)
  const exact = sessions.filter(session => session.username === contact || session.displayName === contact)
  if (exact.length === 1) return exact[0].username
  if (exact.length > 1 || sessions.length > 1) throw new Error('contact 匹配到多个会话，请使用会话 ID')
  if (sessions.length === 1) return sessions[0].username
  throw new Error('未找到对应会话，请使用会话 ID 或准确的会话名称')
}

async function exportMessagesForMcp(args: Record<string, any>): Promise<string> {
  const limit = args.limit === undefined ? MCP_MESSAGE_LIMIT_DEFAULT : Number(args.limit)
  if (!Number.isInteger(limit) || limit < 1 || limit > MCP_MESSAGE_LIMIT_MAX) {
    throw new Error(`limit 必须是 1-${MCP_MESSAGE_LIMIT_MAX} 的整数`)
  }
  const from = parseMessageDate(args.from, 'from')
  const toStart = parseMessageDate(args.to, 'to')
  const to = toStart === undefined ? undefined : toStart + 24 * 60 * 60 - 1
  if (from !== undefined && to !== undefined && from > to) throw new Error('from 不能晚于 to')

  const talker = await resolveMcpTalker(args.contact)
  const messages = await chatService.getMessagesInRange(talker, limit, from, to)
  return JSON.stringify(createWeFlowEnvelope(messages, new Date().toISOString(), {
    requestedFrom: from,
    requestedTo: to,
    requestedLimit: limit,
  }), null, 2)
}

function parseFrontmatter(text: string): Record<string, any> {
  if (!text.startsWith('---')) return {}
  const end = text.indexOf('---', 3)
  if (end === -1) return {}
  const fm: Record<string, any> = {}
  for (const line of text.slice(3, end).split('\n')) {
    const idx = line.indexOf(':')
    if (idx === -1) continue
    const key = line.slice(0, idx).trim()
    let val = line.slice(idx + 1).trim()
    if (val.startsWith('[') && val.endsWith(']')) {
      fm[key] = val.slice(1, -1).split(',').map(v => v.trim().replace(/['"]/g, ''))
    } else if (val.startsWith('"') && val.endsWith('"')) {
      fm[key] = val.slice(1, -1)
    } else {
      fm[key] = val
    }
  }
  return fm
}

function scanArticles(): any[] {
  const results: any[] = []
  if (!existsSync(BIZ_DAILY)) return results
  for (const dateDir of readdirSync(BIZ_DAILY).sort().reverse()) {
    const dp = join(BIZ_DAILY, dateDir)
    if (!statSync(dp).isDirectory()) continue
    for (const topic of readdirSync(dp)) {
      const tp = join(dp, topic)
      if (!statSync(tp).isDirectory()) continue
      for (const file of readdirSync(tp)) {
        if (!file.endsWith('.md') || file === 'README.md') continue
        const content = readFileSync(join(tp, file), 'utf-8')
        const fm = parseFrontmatter(content)
        results.push({
          ...fm,
          file: join(dateDir, topic, file),
          dateDir,
        })
      }
    }
  }
  return results
}

function searchArticles(args: Record<string, any>): string {
  let articles = scanArticles()
  if (args.keyword) {
    const kw = String(args.keyword).toLowerCase()
    articles = articles.filter(a =>
      (a.title || '').toLowerCase().includes(kw) ||
      (a.source || '').toLowerCase().includes(kw) ||
      (a.tags || []).some((t: string) => t.toLowerCase().includes(kw))
    )
  }
  if (args.topic) {
    articles = articles.filter(a => a.topic === args.topic)
  }
  if (args.date) {
    articles = articles.filter(a => a.dateDir === args.date)
  }
  const limit = boundedToolInteger(args.limit, 20, 100)
  const top = articles.slice(0, limit)
  if (!top.length) return '未找到匹配文章'
  return top.map(a =>
    `- [${a.date}] **${a.title || '(无标题)'}** — ${a.source || ''} [${a.topic || ''}] [${(a.tags || []).join(', ')}]\n  ${(a.description || '').slice(0, 120)}`
  ).join('\n\n')
}

/** 建一个 MCP Server（工具表与处理器）。stdio 与 HTTP 两条传输都用它，**每次都新建**无状态。 */
export function buildServer(): Server {
  const server = new Server(
    { name: 'weflow-mcp', version: '1.0.0' },
    { capabilities: { tools: {} } }
  )

  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: [
      {
        name: 'wechat.search_articles',
        description: '搜索知识库文章（可按关键词/主题/日期过滤）',
        inputSchema: {
          type: 'object',
          properties: {
            keyword: { type: 'string', description: '搜索关键词（标题/来源/标签）' },
            topic: { type: 'string', description: '主题: AI | 学术 | 新闻 | 文学 | 投资' },
            date: { type: 'string', description: '日期 YYYY-MM-DD' },
            limit: { type: 'number', description: '返回数量，默认20' },
          },
        },
      },
      {
        name: 'wechat.get_daily',
        description: '获取指定日期的公众号日报',
        inputSchema: {
          type: 'object',
          properties: {
            date: { type: 'string', description: '日期 YYYY-MM-DD，默认最新' },
          },
        },
      },
      {
        name: 'wechat.get_concept',
        description: '获取某个概念的详细 Wiki 页',
        inputSchema: {
          type: 'object',
          properties: {
            name: { type: 'string', description: '概念名，如 RAG、Agent' },
          },
          required: ['name'],
        },
      },
      {
        name: 'wechat.export_messages',
        description: '以 weflow-message/v1 JSON 返回指定会话的本地消息。只读，不返回数据库路径、密钥或配置。',
        inputSchema: {
          type: 'object',
          properties: {
            contact: { type: 'string', description: '会话 ID，或唯一匹配的会话名称' },
            limit: { type: 'number', description: '返回上限，默认 100，最大 1000' },
            from: { type: 'string', description: '起始日期 YYYY-MM-DD（含当天）' },
            to: { type: 'string', description: '结束日期 YYYY-MM-DD（含当天）' },
          },
          required: ['contact'],
        },
      },
      // ---- 本地微信数据 (与 weflow-cli assistant / 微信 bot 共享工具层) ----
      ...ASSISTANT_TOOLS,
    ],
  }))

  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const { name, arguments: args = {} } = request.params

    try {
      switch (name) {
        case 'wechat.search_articles':
          return { content: [{ type: 'text', text: searchArticles(args) }] }

        case 'wechat.get_daily': {
          const dates = existsSync(BIZ_DAILY) ? readdirSync(BIZ_DAILY).sort().reverse() : []
          if (args.date && !safeDate(args.date)) return { content: [{ type: 'text', text: '日期无效，请使用 YYYY-MM-DD' }] }
          const date = safeDate(args.date) || dates.find(d => /^\d{4}-\d{2}-\d{2}$/.test(d)) || ''
          const readme = safeChildPath(BIZ_DAILY, join(date, 'README.md'))
          if (!readme || !existsSync(readme)) return { content: [{ type: 'text', text: `未找到 ${date} 的日报` }] }
          return { content: [{ type: 'text', text: readFileSync(readme, 'utf-8') }] }
        }


        case 'wechat.get_concept': {
          const name = String(args.name)
          if (!name || name.includes('/') || name.includes('\\') || name.includes('..')) {
            return { content: [{ type: 'text', text: '概念名无效' }] }
          }
          // **两条线各留一张同名页是设计**（用户要求两个概念命名空间分开）。只回第一条
          // 会让人以为另一条没有这个概念 —— 所以同名的都要说出来，并指明回的是哪一张。
          const found: string[] = []
          for (const dir of VAULT_WIKI_DIRS) {
            for (const ext of ['', '.md']) {
              const path = safeChildPath(dir, name + ext)
              if (path && existsSync(path)) { found.push(path); break }
            }
          }
          if (found.length) {
            const text = readFileSync(found[0], 'utf-8')
            if (found.length === 1) return { content: [{ type: 'text', text }] }
            // 用**目录名**说清是哪条线（`Wiki/Concepts` / `Chat/Concepts`）：这里不再自己
            // 写一份"文章线/聊天线"的名单 —— 那份在 `_utils.LINE_LABELS` 与助手里各有一份，
            // 已经有测试钉着两边一致，再来第三份就是三处要一起改。
            const rel = (p: string): string => {
              const dir = VAULT_WIKI_DIRS.find(d => p.startsWith(d))
              return dir ? dir.split(/[\\/]/).slice(-2).join('/') : p
            }
            const where = found.map(rel)
            return { content: [{ type: 'text', text:
              `（这个概念两条线都有：${where.join('、')}；下面是第一条 ${where[0]}）\n\n${text}` }] }
          }
          // Fuzzy search
          for (const dir of VAULT_WIKI_DIRS) {
            if (!existsSync(dir)) continue
            for (const f of readdirSync(dir)) {
              if (f.includes(name)) {
                const path = safeChildPath(dir, f)
                if (path) return { content: [{ type: 'text', text: readFileSync(path, 'utf-8') }] }
              }
            }
          }
          return { content: [{ type: 'text', text: `未找到概念: ${name}` }] }
        }







        case 'wechat.export_messages': {
          return { content: [{ type: 'text', text: await exportMessagesForMcp(args) }] }
        }

        default: {
          // assistant 工具层统一分发 (wechat.list_sessions / wechat.get_messages / ...)
          if (name.startsWith('wechat.')) {
            const text = await executeTool(name.slice('wechat.'.length), args, MCP_TOOL_CTX)
            return { content: [{ type: 'text', text }] }
          }
          return { content: [{ type: 'text', text: `未知工具: ${name}` }] }
        }
      }
    } catch {
      return { content: [{ type: 'text', text: '工具执行失败，请检查输入、本地配置或运行状态' }] }
    }
  })

  return server
}

/**
 * 传输在这里选。**默认 stdio**（与以前逐字相同）；给 `--http` 或 `WEFLOW_MCP_HTTP` 才走 HTTP。
 * 判定的规则在 `httpConfig.ts`（纯函数、有测试）：必须带令牌、只绑回环、默认不开。
 */
async function main() {
  const opts = resolveHttpOptions(process.env, process.argv.slice(2))
  if (!opts) {
    await buildServer().connect(new StdioServerTransport())
    return
  }
  const { url } = await startHttpServer(opts, buildServer, (line) => process.stderr.write(line + '\n'))
  // **令牌一个字符都不打印**：日志会被翻、会被贴进 issue。只报地址与"用什么连"。
  process.stderr.write(
    `WeFlow MCP 已监听 ${url}\n`
    + `  只绑 ${opts.host}（回环），需要 Authorization: Bearer <令牌>\n`
    + '  客户端配置见 docs/MCP.md 的「HTTP 传输」一节\n',
  )
}

// 只在**被当脚本跑**时启动。被 `import`（测试要 `buildServer`）就只拿工厂，别顺手起一个服务 ——
// 否则 `npm test` 会挂在一个等 stdin 的 stdio 服务上。
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    process.stderr.write(`启动失败: ${error?.message ?? error}\n`)
    process.exit(1)
  })
}
