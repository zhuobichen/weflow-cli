#!/usr/bin/env npx tsx
/**
 * WeFlow MCP Server — 让 Claude Code 等 AI Agent 直接查询知识库。
 *
 * 启动: npx tsx mcp-server/index.ts
 * 在 CLAUDE.md 中注册后，AI 可直接搜索文章、概念、日报。
 */
import { Server } from '@modelcontextprotocol/sdk/server/index.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from '@modelcontextprotocol/sdk/types.js'
import { readFileSync, readdirSync, existsSync, statSync } from 'fs'
import { join, resolve } from 'path'
import { execFileSync } from 'child_process'

const PKG_ROOT = resolve(join(import.meta.dirname || '.', '..'))
const BIZ_DAILY = join(PKG_ROOT, 'output', 'biz-daily')
const VAULT_WIKI = join(PKG_ROOT, 'output', 'wechat-vault', 'Wiki', 'Concepts')
const VAULT_INDEX = join(PKG_ROOT, 'output', 'wechat-vault', 'Wiki', '00-Overview.md')
const REVIEWS = join(PKG_ROOT, 'output', 'reviews', 'Daily')
const MCP_BRIDGE = join(PKG_ROOT, 'scripts', 'mcp_bridge.py')

/** Call Python mcp_bridge.py and return parsed JSON. */
function callBridge(command: string, args: Record<string, any> = {}): any {
  try {
    const cmdArgs = [MCP_BRIDGE, command]
    for (const [k, v] of Object.entries(args)) {
      if (v !== undefined && v !== '') {
        cmdArgs.push(`--${k}`, String(v))
      }
    }
    const stdout = execFileSync('python', cmdArgs, {
      timeout: 30_000,
      maxBuffer: 10 * 1024 * 1024,
      encoding: 'utf-8',
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
    })
    return JSON.parse(stdout)
  } catch (e: any) {
    return { error: e.message }
  }
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
  const limit = Number(args.limit) || 20
  const top = articles.slice(0, limit)
  if (!top.length) return '未找到匹配文章'
  return top.map(a =>
    `- [${a.date}] **${a.title || '(无标题)'}** — ${a.source || ''} [${a.topic || ''}] [${(a.tags || []).join(', ')}]\n  ${(a.description || '').slice(0, 120)}`
  ).join('\n\n')
}

async function main() {
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
        name: 'wechat.get_concepts',
        description: '查看概念图谱索引',
        inputSchema: { type: 'object', properties: {} },
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
        name: 'wechat.get_review',
        description: '获取某日的 AI 学习日报',
        inputSchema: {
          type: 'object',
          properties: {
            date: { type: 'string', description: '日期 YYYY-MM-DD，默认最新' },
          },
        },
      },
      {
        name: 'wechat.get_stats',
        description: '知识库统计概览',
        inputSchema: { type: 'object', properties: {} },
      },
      // ====== 新增：聊天记录 & 统计工具 ======
      {
        name: 'wechat.search_messages',
        description: '搜索微信聊天记录（按关键词、联系人、时间范围过滤）',
        inputSchema: {
          type: 'object',
          properties: {
            keyword: { type: 'string', description: '搜索关键词' },
            talker: { type: 'string', description: '联系人名称（模糊匹配）' },
            days: { type: 'number', description: '搜索最近多少天，默认30' },
            limit: { type: 'number', description: '返回条数，默认20' },
          },
        },
      },
      {
        name: 'wechat.get_sessions',
        description: '列出最近活跃的微信联系人',
        inputSchema: {
          type: 'object',
          properties: {
            limit: { type: 'number', description: '返回数量，默认30' },
          },
        },
      },
      {
        name: 'wechat.get_todos',
        description: '查看已提取的待办事项',
        inputSchema: { type: 'object', properties: {} },
      },
      {
        name: 'wechat.get_action_suggestions',
        description: '查看公众号文章的行动建议（基于用户定位生成）',
        inputSchema: {
          type: 'object',
          properties: {
            date: { type: 'string', description: '日期 YYYY-MM-DD，默认最新' },
          },
        },
      },
      {
        name: 'wechat.get_chat_stats',
        description: '查看微信消息统计（收发量、Top联系人、活跃时段）',
        inputSchema: {
          type: 'object',
          properties: {
            period: { type: 'string', description: '周期: week | month，默认 week' },
          },
        },
      },
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
          const date = String(args.date || dates[0] || '')
          const readme = join(BIZ_DAILY, date, 'README.md')
          if (!existsSync(readme)) return { content: [{ type: 'text', text: `未找到 ${date} 的日报` }] }
          return { content: [{ type: 'text', text: readFileSync(readme, 'utf-8') }] }
        }

        case 'wechat.get_concepts': {
          if (!existsSync(VAULT_INDEX)) return { content: [{ type: 'text', text: '概念索引尚未生成，请先运行 wiki compile' }] }
          return { content: [{ type: 'text', text: readFileSync(VAULT_INDEX, 'utf-8') }] }
        }

        case 'wechat.get_concept': {
          const name = String(args.name)
          for (const ext of ['', '.md']) {
            const path = join(VAULT_WIKI, name + ext)
            if (existsSync(path)) {
              return { content: [{ type: 'text', text: readFileSync(path, 'utf-8') }] }
            }
          }
          // Fuzzy search
          if (existsSync(VAULT_WIKI)) {
            for (const f of readdirSync(VAULT_WIKI)) {
              if (f.includes(name)) {
                return { content: [{ type: 'text', text: readFileSync(join(VAULT_WIKI, f), 'utf-8') }] }
              }
            }
          }
          return { content: [{ type: 'text', text: `未找到概念: ${name}` }] }
        }

        case 'wechat.get_review': {
          const files = existsSync(REVIEWS) ? readdirSync(REVIEWS).sort().reverse() : []
          const date = String(args.date || '')
          let path: string
          if (date) {
            path = join(REVIEWS, `Daily-${date}.md`)
          } else {
            path = files.length ? join(REVIEWS, files[0]) : ''
          }
          if (!path || !existsSync(path)) return { content: [{ type: 'text', text: `未找到 ${date || '最新'} 的学习日报` }] }
          return { content: [{ type: 'text', text: readFileSync(path, 'utf-8') }] }
        }

        case 'wechat.get_stats': {
          const articles = scanArticles()
          const dates = [...new Set(articles.map(a => a.dateDir))].sort()
          const topics: Record<string, number> = {}
          articles.forEach(a => { topics[a.topic || '未知'] = (topics[a.topic || '未知'] || 0) + 1 })
          let stats = `## 知识库统计\n\n- 文章总数: ${articles.length}\n- 覆盖日期: ${dates.length} 天 (${dates[0]} ~ ${dates[dates.length-1]})\n- 主题分布:\n`
          for (const [t, c] of Object.entries(topics).sort((a, b) => b[1] - a[1])) {
            stats += `  - ${t}: ${c} 篇\n`
          }
          if (existsSync(VAULT_INDEX)) {
            const idx = readFileSync(VAULT_INDEX, 'utf-8')
            const m = idx.match(/共 (\d+) 个概念/)
            if (m) stats += `- 概念页: ${m[1]} 个\n`
          }
          return { content: [{ type: 'text', text: stats }] }
        }

        // ====== 新增：聊天记录 & 统计工具 ======
        case 'wechat.search_messages': {
          const data = callBridge('search_messages', args)
          if (data.error) return { content: [{ type: 'text', text: `错误: ${data.error}` }] }
          const results = data.results || []
          if (!results.length) return { content: [{ type: 'text', text: '未找到匹配消息' }] }
          const text = results.map((r: any) =>
            `[${r.time}] ${r.talker} > ${r.sender}: ${r.content.slice(0, 200)}`
          ).join('\n\n')
          return { content: [{ type: 'text', text: `找到 ${data.total} 条匹配消息:\n\n${text}` }] }
        }

        case 'wechat.get_sessions': {
          const data = callBridge('list_sessions', args)
          if (data.error) return { content: [{ type: 'text', text: `错误: ${data.error}` }] }
          const sessions = data.sessions || []
          if (!sessions.length) return { content: [{ type: 'text', text: '无活跃会话' }] }
          const text = sessions.map((s: any, i: number) =>
            `${i + 1}. ${s.name} — ${s.count} 条消息 (最后活跃: ${s.last_active})`
          ).join('\n')
          return { content: [{ type: 'text', text: `活跃联系人 (共 ${data.total} 个):\n\n${text}` }] }
        }

        case 'wechat.get_todos': {
          const data = callBridge('get_todos', args)
          if (data.error) return { content: [{ type: 'text', text: `错误: ${data.error}` }] }
          if (data.message) return { content: [{ type: 'text', text: data.message }] }
          const todos = data.todos || []
          const text = todos.map((t: any) =>
            `### ${t.file} (${t.count} 项)\n${t.items.join('\n')}`
          ).join('\n\n')
          return { content: [{ type: 'text', text }] }
        }

        case 'wechat.get_action_suggestions': {
          const data = callBridge('get_action_suggestions', args)
          if (data.error) return { content: [{ type: 'text', text: `错误: ${data.error}` }] }
          if (data.message) return { content: [{ type: 'text', text: data.message }] }
          return { content: [{ type: 'text', text: `## 行动建议 — ${data.date}\n\n${data.content}` }] }
        }

        case 'wechat.get_chat_stats': {
          const data = callBridge('get_chat_stats', args)
          if (data.error) return { content: [{ type: 'text', text: `错误: ${data.error}` }] }
          const periodLabel = data.period === 'week' ? '周报' : '月报'
          let text = `## 微信消息统计 (${periodLabel})\n${data.start_date} ~ ${data.end_date}\n\n`
          text += `- 总消息量: ${data.total_messages}\n`
          text += `- 活跃联系人: ${data.active_contacts} 个\n`
          text += `- 最活跃时段: ${data.most_active_hour}\n\n`
          text += `### Top 联系人\n`
          for (const t of (data.top_talkers || [])) {
            text += `- ${t.name}: ${t.count} 条\n`
          }
          return { content: [{ type: 'text', text }] }
        }

        default:
          return { content: [{ type: 'text', text: `未知工具: ${name}` }] }
      }
    } catch (e: any) {
      return { content: [{ type: 'text', text: `错误: ${e.message}` }] }
    }
  })

  const transport = new StdioServerTransport()
  await server.connect(transport)
}

main().catch(console.error)
