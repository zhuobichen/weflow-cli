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

const PKG_ROOT = resolve(join(import.meta.dirname || '.', '..'))
const BIZ_DAILY = join(PKG_ROOT, 'output', 'biz-daily')
const VAULT_WIKI = join(PKG_ROOT, 'output', 'wechat-vault', 'Wiki', 'Concepts')
const VAULT_INDEX = join(PKG_ROOT, 'output', 'wechat-vault', 'Wiki', '00-Overview.md')
const REVIEWS = join(PKG_ROOT, 'output', 'reviews', 'Daily')

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
