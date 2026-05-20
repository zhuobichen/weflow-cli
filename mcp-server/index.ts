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
import { readFileSync, readdirSync, existsSync, statSync, writeFileSync, mkdirSync } from 'fs'
import { join, resolve } from 'path'
import { deflateSync } from 'zlib'

// ---- 微信公众号文章抓取 ----
async function fetchWeChatArticle(url: string): Promise<{
  title: string
  author: string
  description: string
  content: string
  images: string[]
}> {
  const resp = await fetch(url, {
    headers: {
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
      'Accept': 'text/html,application/xhtml+xml',
      'Accept-Language': 'zh-CN,zh;q=0.9',
    },
    redirect: 'follow',
  })
  if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${resp.statusText}`)
  const html = await resp.text()

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

// ---- 微信公众号 API 辅助 ----
const WECHAT_APP_ID = process.env.WECHAT_APPID || ''
const WECHAT_APP_SECRET = process.env.WECHAT_APPSECRET || ''

interface TokenCache {
  token: string
  expiresAt: number
}
let _tokenCache: TokenCache | null = null

async function getWeChatToken(): Promise<string> {
  if (_tokenCache && Date.now() < _tokenCache.expiresAt - 60000) {
    return _tokenCache.token
  }
  if (!WECHAT_APP_ID || !WECHAT_APP_SECRET) {
    throw new Error('请设置环境变量 WECHAT_APPID 和 WECHAT_APPSECRET')
  }
  const resp = await fetch(
    `https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid=${WECHAT_APP_ID}&secret=${WECHAT_APP_SECRET}`
  )
  const data = await resp.json() as any
  if (data.errcode) {
    throw new Error(`微信 Token 获取失败: ${data.errmsg} (${data.errcode})`)
  }
  _tokenCache = {
    token: data.access_token,
    expiresAt: Date.now() + (data.expires_in - 60) * 1000,
  }
  return _tokenCache.token
}

async function uploadWeChatImage(token: string, filePath: string): Promise<string> {
  const buf = readFileSync(filePath)
  const boundary = `----WeFlow${Date.now()}`
  const filename = filePath.replace(/^.*[\\/]/, '')
  const ext = filename.split('.').pop()?.toLowerCase() || 'png'
  const mimeType = ext === 'jpg' || ext === 'jpeg' ? 'image/jpeg' : 'image/png'

  const header = [
    `--${boundary}`,
    `Content-Disposition: form-data; name="media"; filename="${filename}"`,
    `Content-Type: ${mimeType}`,
    '', '',
  ].join('\r\n')
  const trailer = `\r\n--${boundary}--\r\n`

  const headerBytes = new TextEncoder().encode(header)
  const trailerBytes = new TextEncoder().encode(trailer)
  const body = new Uint8Array(headerBytes.length + buf.length + trailerBytes.length)
  body.set(headerBytes, 0)
  body.set(buf, headerBytes.length)
  body.set(trailerBytes, headerBytes.length + buf.length)

  const resp = await fetch(
    `https://api.weixin.qq.com/cgi-bin/material/add_material?access_token=${token}&type=image`,
    {
      method: 'POST',
      headers: { 'Content-Type': `multipart/form-data; boundary=${boundary}` },
      body,
    }
  )
  const data = await resp.json() as any
  if (data.errcode) {
    throw new Error(`上传封面图失败: ${data.errmsg} (${data.errcode})`)
  }
  return data.media_id
}

async function createWeChatDraft(token: string, articles: Array<{
  title: string
  author?: string
  digest?: string
  content: string
  thumb_media_id?: string
}>): Promise<string> {
  const resp = await fetch(
    `https://api.weixin.qq.com/cgi-bin/draft/add?access_token=${token}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ articles }),
    }
  )
  const data = await resp.json() as any
  if (data.errcode) {
    throw new Error(`创建草稿失败: ${data.errmsg} (${data.errcode})`)
  }
  return data.media_id
}

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

/** 自动生成封面图（纯色主题渐变 PNG，无外部依赖） */
function generateCoverImage(title: string, theme: string): string {
  const colors: Record<string, [number, number, number]> = {
    warm: [230, 126, 34],
    default: [52, 152, 219],
    minimal: [51, 51, 51],
    green: [39, 174, 96],
  }
  const [r, g, b] = colors[theme] || colors.default
  const w = 900, h = 500

  function crc32(buf: Buffer): number {
    let c = 0xffffffff
    for (let i = 0; i < buf.length; i++) {
      c ^= buf[i]
      for (let j = 0; j < 8; j++) c = (c >>> 1) ^ (c & 1 ? 0xedb88320 : 0)
    }
    return (c ^ 0xffffffff) >>> 0
  }
  function chunk(type: string, data: Buffer): Buffer {
    const head = Buffer.alloc(8)
    head.writeUInt32BE(data.length, 0)
    head.write(type, 4)
    const crcBuf = Buffer.alloc(4)
    crcBuf.writeUInt32BE(crc32(Buffer.concat([head.slice(4, 8), data])), 0)
    return Buffer.concat([head, data, crcBuf])
  }

  const signature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4)
  ihdr[8] = 8; ihdr[9] = 2

  const raw = Buffer.alloc(h * (1 + w * 3))
  for (let y = 0; y < h; y++) {
    const offset = y * (1 + w * 3)
    raw[offset] = 0
    for (let x = 0; x < w; x++) {
      const px = offset + 1 + x * 3
      const darken = 0.9 + 0.1 * (y / h)
      raw[px] = Math.floor(r * darken)
      raw[px + 1] = Math.floor(g * darken)
      raw[px + 2] = Math.floor(b * darken)
    }
  }

  const png = Buffer.concat([
    signature,
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(raw)),
    chunk('IEND', Buffer.alloc(0)),
  ])

  const tmpDir = join(PKG_ROOT, 'output', '.tmp')
  if (!existsSync(tmpDir)) mkdirSync(tmpDir, { recursive: true })
  const path = join(tmpDir, `cover-${Date.now()}.png`)
  writeFileSync(path, png)
  return path
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
        name: 'wechat.publish_article',
        description: '排版并发布 Markdown 文章到微信公众号草稿箱。需要设置环境变量 WECHAT_APPID 和 WECHAT_APPSECRET',
        inputSchema: {
          type: 'object',
          properties: {
            title: { type: 'string', description: '文章标题（≤64字符）' },
            content: { type: 'string', description: 'Markdown 格式的文章内容' },
            author: { type: 'string', description: '作者名（≤8字符），默认 "AI Assistant"' },
            theme: { type: 'string', description: '排版主题: default | warm | minimal | green，默认 default' },
            cover_image: { type: 'string', description: '封面图本地路径（可选）' },
            preview_only: { type: 'boolean', description: '仅排版预览不发布草稿，默认 false' },
          },
          required: ['title', 'content'],
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

        case 'wechat.publish_article': {
          const title = String(args.title || '').slice(0, 64)
          const content = String(args.content || '')
          const author = String(args.author || 'AI Assistant').slice(0, 8)
          const theme = String(args.theme || 'default')
          const coverImage = args.cover_image ? String(args.cover_image) : ''
          const previewOnly = args.preview_only === true || args.preview_only === 'true'

          if (!title || !content) {
            return { content: [{ type: 'text', text: '错误: title 和 content 不能为空' }] }
          }

          // 1. 排版
          const html = formatWeChatArticle(content, { theme: theme as any })
          const wordCount = html.replace(/<[^>]*>/g, '').replace(/\s+/g, '').length

          if (previewOnly) {
            return {
              content: [{
                type: 'text',
                text: [
                  `[仅预览] 标题: ${title}`,
                  `作者: ${author} | 主题: ${theme} | 字数: ${wordCount}`,
                  `---`,
                  html,
                ].join('\n')
              }]
            }
          }

          // 2. 发布到草稿箱
          try {
            const token = await getWeChatToken()

            // 上传封面图（如果提供，否则自动生成）
            let thumbMediaId = ''
            if (coverImage && existsSync(coverImage)) {
              thumbMediaId = await uploadWeChatImage(token, coverImage)
            } else {
              // 自动生成封面图
              const autoCover = generateCoverImage(title, theme)
              thumbMediaId = await uploadWeChatImage(token, autoCover)
            }

            // 创建草稿
            const mediaId = await createWeChatDraft(token, [{
              title,
              author,
              digest: html.replace(/<[^>]*>/g, '').replace(/\s+/g, ' ').slice(0, 120),
              content: html,
              thumb_media_id: thumbMediaId || undefined,
            }])

            return {
              content: [{
                type: 'text',
                text: [
                  `✅ 已发布到公众号草稿箱`,
                  `media_id: ${mediaId}`,
                  `标题: ${title}`,
                  `作者: ${author} | 主题: ${theme} | 字数: ${wordCount}`,
                  `封面图: ${thumbMediaId ? '已上传' : '未提供（使用默认）'}`,
                  '',
                  `请登录 mp.weixin.qq.com 草稿箱查看和正式发布。`,
                ].join('\n')
              }]
            }
          } catch (e: any) {
            return { content: [{ type: 'text', text: `发布失败: ${e.message}\n\n——以下为排版预览——\n\n${html}` }] }
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
          return { content: [{ type: 'text', text: stats }] }
        }

        case 'wechat.search_public': {
          const keyword = String(args.keyword || '').trim()
          if (!keyword) return { content: [{ type: 'text', text: '错误: keyword 不能为空' }] }
          const limit = Number(args.limit) || 10

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
              }
            )
            const html = await resp.text()

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
          } catch (e: any) {
            return { content: [{ type: 'text', text: `搜索失败: ${e.message}` }] }
          }
        }

        case 'wechat.fetch_article': {
          const url = String(args.url || '').trim()
          if (!url) return { content: [{ type: 'text', text: '错误: url 不能为空' }] }
          if (!url.includes('mp.weixin.qq.com')) {
            return { content: [{ type: 'text', text: '错误: 仅支持微信公众号文章链接 (mp.weixin.qq.com)' }] }
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
          } catch (e: any) {
            return { content: [{ type: 'text', text: `抓取失败: ${e.message}\n\n提示: 部分文章需要微信客户端环境才能访问，可尝试在微信中打开后复制链接。` }] }
          }
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
