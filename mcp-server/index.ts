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
import { join } from 'path'
import { isAllowedWeChatArticleUrl, safeChildPath, safeDate } from '../src/utils/mcpSecurity.js'
import { resolvePackageRoot } from '../src/utils/packageRoot.js'
import { chatService } from '../src/services/chatService.js'
import { createWeFlowEnvelope } from '../src/services/messageContract.js'

// ---- 微信公众号文章抓取 ----
async function readResponseTextLimited(response: Response, maximumBytes: number): Promise<string> {
  const contentLength = Number(response.headers.get('content-length') || 0)
  if (contentLength > maximumBytes) throw new Error('RESPONSE_TOO_LARGE')
  if (!response.body) return ''

  const reader = response.body.getReader()
  const chunks: Uint8Array[] = []
  let size = 0
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    size += value.byteLength
    if (size > maximumBytes) {
      await reader.cancel()
      throw new Error('RESPONSE_TOO_LARGE')
    }
    chunks.push(value)
  }
  return Buffer.concat(chunks).toString('utf8')
}

async function fetchWeChatArticle(url: string): Promise<{
  title: string
  author: string
  description: string
  content: string
  images: string[]
}> {
  let currentUrl = url
  let resp: Response | null = null
  for (let redirectCount = 0; redirectCount <= 3; redirectCount++) {
    if (!isAllowedWeChatArticleUrl(currentUrl)) throw new Error('URL_NOT_ALLOWED')
    resp = await fetch(currentUrl, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml',
        'Accept-Language': 'zh-CN,zh;q=0.9',
      },
      redirect: 'manual',
      signal: AbortSignal.timeout(20_000),
    })
    if (resp.status < 300 || resp.status >= 400) break
    const location = resp.headers.get('location')
    if (!location) throw new Error('REDIRECT_WITHOUT_LOCATION')
    currentUrl = new URL(location, currentUrl).toString()
    resp = null
  }
  if (!resp) throw new Error('TOO_MANY_REDIRECTS')
  if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${resp.statusText}`)
  const html = await readResponseTextLimited(resp, 5 * 1024 * 1024)

  // 提取 meta 信息
  const title = html.match(/<meta[^>]*property="og:title"[^>]*content="([^"]+)"/i)?.[1]
    || html.match(/<title>([^<]+)<\/title>/i)?.[1]?.trim()
    || '(未知标题)'
  const author = html.match(/<meta[^>]*property="og:article:author"[^>]*content="([^"]+)"/i)?.[1]
    || html.match(/var\s+nickname\s*=\s*"([^"]+)"/i)?.[1]
    || '(未知作者)'
  const description = html.match(/<meta[^>]*property="og:description"[^>]*content="([^"]+)"/i)?.[1]
    || ''

  // 提取 js_content 文章正文
  const contentMatch = html.match(/<div[^>]*id="js_content"[^>]*>([\s\S]*?)<\/div>\s*<script/i)
  let content = contentMatch?.[1] || ''

  // 替换图片 data-src → src
  content = content.replace(/data-src="/g, 'src="')

  // 简单的 HTML → Markdown 转换
  content = htmlToMarkdown(content)

  // 提取图片链接
  const images: string[] = []
  const imgMatches = content.matchAll(/!\[.*?\]\((.*?)\)/g)
  for (const m of imgMatches) {
    if (m[1]) images.push(m[1])
  }

  return { title, author, description, content, images }
}

/** 简单的 HTML → Markdown 转换（处理微信文章常见结构） */
function htmlToMarkdown(html: string): string {
  let md = html
    // 移除 style/script 标签
    .replace(/<style[^>]*>[\s\S]*?<\/style>/gi, '')
    .replace(/<script[^>]*>[\s\S]*?<\/script>/gi, '')
    // 标题
    .replace(/<h1[^>]*>(.*?)<\/h1>/gi, '\n# $1\n')
    .replace(/<h2[^>]*>(.*?)<\/h2>/gi, '\n## $1\n')
    .replace(/<h3[^>]*>(.*?)<\/h3>/gi, '\n### $1\n')
    .replace(/<h4[^>]*>(.*?)<\/h4>/gi, '\n#### $1\n')
    // 粗体/斜体
    .replace(/<strong[^>]*>(.*?)<\/strong>/gi, '**$1**')
    .replace(/<b[^>]*>(.*?)<\/b>/gi, '**$1**')
    .replace(/<em[^>]*>(.*?)<\/em>/gi, '*$1*')
    .replace(/<i[^>]*>(.*?)<\/i>/gi, '*$1*')
    // 段落和换行
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<p[^>]*>/gi, '\n\n')
    .replace(/<\/p>/gi, '')
    // 列表
    .replace(/<li[^>]*>(.*?)<\/li>/gi, '- $1\n')
    // 引用
    .replace(/<blockquote[^>]*>([\s\S]*?)<\/blockquote>/gi, (_: string, content: string) => {
      return '\n> ' + content.trim().replace(/\n/g, '\n> ') + '\n'
    })
    // 图片
    .replace(/<img[^>]*src="([^"]+)"[^>]*>/gi, '![]($1)\n')
    // 链接
    .replace(/<a[^>]*href="([^"]+)"[^>]*>(.*?)<\/a>/gi, '[$2]($1)')
    // 移除剩余标签
    .replace(/<[^>]+>/g, '')
    // 解码 HTML 实体
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&nbsp;/g, ' ')
    // 清理多余空行
    .replace(/\n{3,}/g, '\n\n')
    .trim()

  return md
}
import { formatWeChatArticle, listThemes } from '../src/services/wechat-formatter.js'
import { boundedToolInteger, MCP_READ_ONLY_TOOL_DEFS, executeTool } from '../src/services/assistantTools.js'
import { AssistantMemory } from '../src/services/assistantMemory.js'

// ---- 本地微信数据工具 (复用 assistant 工具层, 微信 bot / MCP server 同源) ----
// MCP 无微信用户身份, 记忆固定挂 'mcp' 用户下; 与 assistant 守护进程共用同一持久化文件
const mcpMemory = new AssistantMemory()
const MCP_TOOL_CTX = { userId: 'mcp', memory: mcpMemory }

/** assistant 工具 (OpenAI function 格式) → MCP 工具格式; get_stats 并入现有 wechat.get_stats */
const ASSISTANT_TOOLS = MCP_READ_ONLY_TOOL_DEFS
  .filter(t => t.function.name !== 'get_stats')
  .map(t => ({
    name: `wechat.${t.function.name}`,
    description: t.function.description,
    inputSchema: t.function.parameters,
  }))

const PKG_ROOT = resolvePackageRoot(import.meta.url)
const BIZ_DAILY = join(PKG_ROOT, 'output', 'biz-daily')
const VAULT_WIKI = join(PKG_ROOT, 'output', 'wechat-vault', 'Wiki', 'Concepts')
const VAULT_INDEX = join(PKG_ROOT, 'output', 'wechat-vault', 'Wiki', '00-Overview.md')
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
        description: '统计概览: 知识库文章/概念页 + 本地微信数据(会话数/收藏总数)',
        inputSchema: { type: 'object', properties: {} },
      },
      {
        name: 'wechat.format_article',
        description: '将 Markdown 文章转换为微信公众号排版 HTML（支持 4 种主题：default/warm/minimal/green）',
        inputSchema: {
          type: 'object',
          properties: {
            content: { type: 'string', description: 'Markdown 格式的文章内容' },
            theme: { type: 'string', description: '排版主题: default | warm | minimal | green，默认 default' },
          },
          required: ['content'],
        },
      },
      {
        name: 'wechat.list_themes',
        description: '列出微信公众号排版可用的所有主题',
        inputSchema: { type: 'object', properties: {} },
      },
      {
        name: 'wechat.search_public',
        description: '搜索全网微信公众号文章（通过搜索引擎 site:mp.weixin.qq.com）',
        inputSchema: {
          type: 'object',
          properties: {
            keyword: { type: 'string', description: '搜索关键词' },
            limit: { type: 'number', description: '返回数量，默认10' },
          },
          required: ['keyword'],
        },
      },
      {
        name: 'wechat.fetch_article',
        description: '抓取单篇微信公众号文章，转换为 Markdown',
        inputSchema: {
          type: 'object',
          properties: {
            url: { type: 'string', description: '文章链接（mp.weixin.qq.com）' },
          },
          required: ['url'],
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

        case 'wechat.get_concepts': {
          if (!existsSync(VAULT_INDEX)) return { content: [{ type: 'text', text: '概念索引尚未生成，请先运行 wiki compile' }] }
          return { content: [{ type: 'text', text: readFileSync(VAULT_INDEX, 'utf-8') }] }
        }

        case 'wechat.get_concept': {
          const name = String(args.name)
          if (!name || name.includes('/') || name.includes('\\') || name.includes('..')) {
            return { content: [{ type: 'text', text: '概念名无效' }] }
          }
          for (const ext of ['', '.md']) {
            const path = safeChildPath(VAULT_WIKI, name + ext)
            if (path && existsSync(path)) {
              return { content: [{ type: 'text', text: readFileSync(path, 'utf-8') }] }
            }
          }
          // Fuzzy search
          if (existsSync(VAULT_WIKI)) {
            for (const f of readdirSync(VAULT_WIKI)) {
              if (f.includes(name)) {
                const path = safeChildPath(VAULT_WIKI, f)
                if (path) return { content: [{ type: 'text', text: readFileSync(path, 'utf-8') }] }
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
            path = safeDate(date) ? join(REVIEWS, `Daily-${date}.md`) : ''
          } else {
            path = files.length ? join(REVIEWS, files[0]) : ''
          }
          if (!path || !existsSync(path)) return { content: [{ type: 'text', text: `未找到 ${date || '最新'} 的学习日报` }] }
          return { content: [{ type: 'text', text: readFileSync(path, 'utf-8') }] }
        }

        case 'wechat.format_article': {
          const content = String(args.content || '')
          if (!content) return { content: [{ type: 'text', text: '错误: content 不能为空' }] }
          const theme = String(args.theme || 'default')
          const html = formatWeChatArticle(content, { theme: theme as any })
          const preview = html.replace(/<[^>]*>/g, '').replace(/\s+/g, ' ').slice(0, 200) + '...'
          return {
            content: [{
              type: 'text',
              text: [
                `主题: ${theme}`,
                `总长度: ${html.length} 字符`,
                `正文预览: ${preview}`,
                `---`,
                html,
              ].join('\n')
            }]
          }
        }

        case 'wechat.list_themes': {
          const themes = listThemes()
          return {
            content: [{
              type: 'text',
              text: themes.map(t => `- **${t.id}** — ${t.name}: ${t.description}`).join('\n')
            }]
          }
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
          // 追加本地微信数据统计 (会话/收藏; 数据库未配置时返回提示)
          const chatStats = await executeTool('get_stats', {}, MCP_TOOL_CTX)
          stats += `\n## 本地微信数据\n\n${chatStats}\n`
          return { content: [{ type: 'text', text: stats }] }
        }

        case 'wechat.search_public': {
          const keyword = String(args.keyword || '').trim()
          if (!keyword) return { content: [{ type: 'text', text: '错误: keyword 不能为空' }] }
          let limit = 10
          try {
            limit = boundedToolInteger(args.limit, 10, 20)
          } catch {
            return { content: [{ type: 'text', text: '错误: limit 必须是 1-20 的整数' }] }
          }

          try {
            // 使用搜狗微信搜索
            const resp = await fetch(
              `https://weixin.sogou.com/weixin?type=2&query=${encodeURIComponent(keyword)}`,
              {
                headers: {
                  'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                  'Referer': 'https://weixin.sogou.com/',
                  'Accept': 'text/html',
                'Accept-Language': 'zh-CN,zh;q=0.9',
                },
                signal: AbortSignal.timeout(20_000),
              }
            )
            const html = await readResponseTextLimited(resp, 2 * 1024 * 1024)

            // 从搜狗结果中提取标题、公众号名、摘要
            // 搜狗将结果放在 li 标签中，每篇文章包含标题链接 + 摘要
            const items: Array<{ title: string; account: string; desc: string }> = []

            // 提取文章标题（h3 标签内的文本）
            const titlePattern = /<h3[^>]*>\s*<a[^>]*>(.*?)<\/a>\s*<\/h3>/g
            const titles: string[] = []
            let tm: RegExpExecArray | null
            while ((tm = titlePattern.exec(html)) !== null) {
              const t = tm[1].replace(/<em[^>]*>/g, '').replace(/<\/em>/g, '').replace(/<!--[^>]*-->/g, '').trim()
              if (t) titles.push(t)
            }

            // 提取公众号名（在 span.all-time-y2 中）
            const acctPattern = /<span[^>]*class="[^"]*all-time-y2[^"]*"[^>]*>(.*?)<\/span>/g
            const accounts: string[] = []
            let am: RegExpExecArray | null
            while ((am = acctPattern.exec(html)) !== null) {
              const a = am[1].replace(/<[^>]+>/g, '').trim()
              if (a) accounts.push(a)
            }

            // 提取摘要（p 标签）
            const descPattern = /<p[^>]*class="[^"]*txt-info[^"]*"[^>]*>(.*?)<\/p>/g
            const descs: string[] = []
            let dm: RegExpExecArray | null
            while ((dm = descPattern.exec(html)) !== null) {
              const d = dm[1].replace(/<[^>]+>/g, '').replace(/&hellip;/g, '…').replace(/&rarr;/g, '→').replace(/&mdash;/g, '—').trim()
              if (d && d.length > 10) descs.push(d)
            }

            // 组合结果
            const count = Math.max(titles.length, accounts.length, descs.length)
            for (let i = 0; i < Math.min(count, limit); i++) {
              items.push({
                title: titles[i] || '(未知标题)',
                account: accounts[i] || '(未知公众号)',
                desc: descs[i] || '',
              })
            }

            if (!items.length) {
              return { content: [{ type: 'text', text: `未找到与"${keyword}"相关的微信公众号文章。\n\n提示：\n1. 尝试更具体的关键词\n2. 加上作者/公众号名\n3. 直接提供文章链接使用 wechat.fetch_article 抓取` }] }
            }

            const results = items.map((item, i) =>
              `${i + 1}. **${item.title}**\n   公众号: ${item.account}\n   ${item.desc}`
            ).join('\n\n')

            return {
              content: [{
                type: 'text',
                text: [
                  `搜索关键词: "${keyword}" | 找到 ${items.length} 篇:`,
                  '',
                  results,
                  '',
                  '---',
                  '提示: 在微信或搜狗微信搜索中打开看到文章后，复制链接使用 wechat.fetch_article 抓取全文。'
                ].join('\n')
              }]
            }
          } catch {
            return { content: [{ type: 'text', text: '搜索失败，请检查网络连接后重试' }] }
          }
        }

        case 'wechat.fetch_article': {
          const url = String(args.url || '').trim()
          if (!url) return { content: [{ type: 'text', text: '错误: url 不能为空' }] }
          if (!isAllowedWeChatArticleUrl(url)) {
            return { content: [{ type: 'text', text: '错误: 仅支持 HTTPS 微信公众号文章链接 (mp.weixin.qq.com)' }] }
          }

          try {
            const article = await fetchWeChatArticle(url)
            return {
              content: [{
                type: 'text',
                text: [
                  `# ${article.title}`,
                  `> 作者: ${article.author}`,
                  article.description ? `> ${article.description}` : '',
                  '',
                  article.content,
                ].join('\n')
              }]
            }
          } catch {
            return { content: [{ type: 'text', text: '抓取失败，请检查链接和网络连接。部分文章需要微信客户端环境才能访问。' }] }
          }
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

  const transport = new StdioServerTransport()
  await server.connect(transport)
}

main().catch(console.error)
