/**
 * Agent 工具注册表 (OpenAI function-calling 格式, DeepSeek 兼容)。
 * 所有工具在本机执行; 结果经 PrivacyGate 脱敏后才进入 LLM 上下文。
 */
import { chatService } from './chatService.js'
import type { AssistantMemory } from './assistantMemory.js'
import { privacyGate } from './assistantPrivacy.js'

export interface ToolDef {
  type: 'function'
  function: {
    name: string
    description: string
    parameters: Record<string, unknown>
  }
}

export interface ToolContext {
  userId: string
  memory: AssistantMemory
}

function fmtTime(ts: number): string {
  const n = Number(ts)
  const d = n > 1e12 ? new Date(n) : new Date(n * 1000)
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

/** SSRF 防护: 仅 http(s), 拒绝内网/环回地址 */
function isSafeUrl(raw: string): boolean {
  try {
    const u = new URL(raw)
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return false
    const h = u.hostname
    if (h === 'localhost' || h === '[::1]') return false
    if (/^(127\.|10\.|192\.168\.|169\.254\.|0\.)/.test(h)) return false
    if (/^172\.(1[6-9]|2\d|3[01])\./.test(h)) return false
    return true
  } catch { return false }
}

/** 标签剥离 → 纯文本 */
function stripTags(seg: string): string {
  return seg.replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<[^>]+>/g, '\n')
    .replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .split('\n').map(s => s.trim()).filter(Boolean).join('\n')
}

/** WAF 挑战页兜底: 正文藏在 cgiDataNew.content_noencode (JS 转义字符串) */
function extractFromChallengePage(html: string): string | null {
  const start = html.indexOf("content_noencode: '")
  if (start < 0) return null
  let i = start + "content_noencode: '".length
  let out = ''
  while (i < html.length && out.length < 1_000_000) {
    const ch = html[i]
    if (ch === "'") break
    if (ch === '\\' && i + 1 < html.length) {
      const n = html[i + 1]
      if (n === 'x' && i + 3 < html.length) {
        out += String.fromCharCode(parseInt(html.slice(i + 2, i + 4), 16))
        i += 4
        continue
      }
      if (n === 'n') { out += '\n'; i += 2; continue }
      if (n === 'r') { out += '\r'; i += 2; continue }
      if (n === 't') { out += '\t'; i += 2; continue }
      if (n === '\\' || n === "'" || n === '"') { out += n; i += 2; continue }
    }
    out += ch
    i++
  }
  return out.length > 100 ? out : null
}

/** 从 HTML 提取正文文本 (微信公众号 js_content 优先, 配平 div 边界; 兜底 body) */
function extractText(html: string): string {
  let seg = html
  const jc = html.indexOf('id="js_content"')
  if (jc >= 0) {
    // 配平 <div/</div> 找 js_content 完整区块; depth 从 1 起算 (自身开标签位于 jc 之前)
    let depth = 1, end = -1
    const tag = /<\/?div\b/g
    tag.lastIndex = jc
    let m: RegExpExecArray | null
    while ((m = tag.exec(html))) {
      if (m[0] === '</div') { depth--; if (depth === 0) { end = m.index; break } }
      else depth++
    }
    seg = end > jc ? html.slice(jc, end) : html.slice(jc, jc + 100_000)
  } else {
    const body = html.indexOf('<body')
    if (body >= 0) seg = html.slice(body)
  }
  return stripTags(seg)
}

/** 确保数据库已连接 (connect 幂等, 已连接时直接返回) */
async function ensureDb(): Promise<void> {
  await chatService.connect()
}

/** 显示名 → username 解析 (会话表里两者都有) */
async function resolveTalker(name: string): Promise<string> {
  const sessions = await chatService.listSessions(undefined, 300)
  const hit = sessions.find(s =>
    s.displayName === name || s.username === name ||
    (s.displayName && s.displayName.includes(name)))
  return hit ? hit.username : name
}

export const TOOL_DEFS: ToolDef[] = [
  {
    type: 'function',
    function: {
      name: 'list_sessions',
      description: '列出用户最近的微信聊天会话(显示名+最后一条消息摘要)。用于了解用户最近在和谁聊天。',
      parameters: {
        type: 'object',
        properties: {
          limit: { type: 'number', description: '返回条数, 默认15' },
        },
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'get_messages',
      description: '读取用户与某位联系人的最近聊天记录。联系人名用会话里出现的显示名。',
      parameters: {
        type: 'object',
        properties: {
          contact: { type: 'string', description: '联系人显示名或备注名' },
          limit: { type: 'number', description: '消息条数, 默认20' },
        },
        required: ['contact'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'search_favorites',
      description: '搜索用户的微信收藏(主要是收藏的公众号文章)。',
      parameters: {
        type: 'object',
        properties: {
          keyword: { type: 'string', description: '搜索关键词' },
          limit: { type: 'number', description: '返回条数, 默认8' },
        },
        required: ['keyword'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'read_favorite',
      description: '读取一篇收藏文章的正文内容。先用 search_favorites 或按关键词找到文章标题, 再用标题关键词调本工具抓取正文。适合「这篇文章讲了啥」「总结第一篇」类问题。',
      parameters: {
        type: 'object',
        properties: {
          keyword: { type: 'string', description: '文章标题中的关键词' },
          max_chars: { type: 'number', description: '返回正文字数上限, 默认3000' },
        },
        required: ['keyword'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'get_stats',
      description: '获取用户本地微信数据统计(会话数/收藏总数等)。',
      parameters: { type: 'object', properties: {} },
    },
  },
  {
    type: 'function',
    function: {
      name: 'search_memory',
      description: '搜索助手关于用户的长期记忆(此前对话中提取的持久事实)。',
      parameters: {
        type: 'object',
        properties: {
          keyword: { type: 'string', description: '搜索关键词' },
        },
        required: ['keyword'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'save_memory',
      description: '把关于用户的重要持久事实存入长期记忆(如偏好、项目背景、重要关系)。',
      parameters: {
        type: 'object',
        properties: {
          content: { type: 'string', description: '事实内容, 40字以内' },
        },
        required: ['content'],
      },
    },
  },
]

export async function executeTool(name: string, args: Record<string, any>, ctx: ToolContext): Promise<string> {
  try {
    switch (name) {
      case 'list_sessions': {
        await ensureDb()
        const limit = Math.min(args.limit || 15, 30)
        const sessions = await chatService.listSessions(undefined, limit)
        if (!sessions.length) return '(未查到会话, 数据库可能未连接)'
        return sessions.map(s =>
          `· ${s.displayName || s.username}: ${(s.summary || '').replace(/\n/g, ' ').slice(0, 30)}`).join('\n')
      }
      case 'get_messages': {
        const contact = String(args.contact || '')
        if (!contact) return '(缺少 contact 参数)'
        const limit = Math.min(args.limit || 20, 50)
        const talker = await resolveTalker(contact)
        const msgs = await chatService.getMessages(talker, limit)
        if (!msgs.length) return `(没找到「${contact}」的消息)`
        return msgs.map(m => {
          const body = privacyGate.maskMessageBody((m.content || m.parsedContent || '').replace(/\n/g, ' ').slice(0, 80))
          return `[${fmtTime(m.createTime)}] ${m.isSend ? '用户' : (m.senderUsername || '对方')}: ${body}`
        }).join('\n')
      }
      case 'search_favorites': {
        await ensureDb()
        const keyword = String(args.keyword || '')
        if (!keyword) return '(缺少 keyword 参数)'
        const limit = Math.min(args.limit || 8, 15)
        const r = await chatService.getFavorites({ keyword, limit })
        if (!r.success || !r.favorites?.length) {
          return r.error ? `(查询失败: ${r.error})` : `(收藏中未搜到「${keyword}」)`
        }
        return `共${r.total}条, 前${r.favorites.length}条:\n` +
          r.favorites.map(f => {
            const desc = (f.desc || '').replace(/\s+/g, ' ').slice(0, 60)
            return `· ${f.title || '(无标题)'} (${f.source_name || '未知来源'})${desc ? ` — ${desc}` : ''}`
          }).join('\n')
      }
      case 'read_favorite': {
        await ensureDb()
        const keyword = String(args.keyword || '')
        if (!keyword) return '(缺少 keyword 参数)'
        const maxChars = Math.min(args.max_chars || 3000, 6000)
        const r = await chatService.getFavorites({ keyword, limit: 5 })
        if (!r.success || !r.favorites?.length) {
          return `(收藏中未找到「${keyword}」)`
        }
        const art = r.favorites.find(f => f.link) || r.favorites[0]
        if (!art.link) {
          const txt = (art.desc || art.content || '').trim()
          return txt ? `「${art.title || '无标题'}」内容:\n${txt.slice(0, maxChars)}` : `(「${art.title}」没有可读的链接和内容)`
        }
        if (!isSafeUrl(art.link)) return `(链接不安全, 拒绝抓取: ${art.link.slice(0, 60)})`
        // 微信内置浏览器 UA + Referer 绕 WAF (与 biz_daily.py 同策略)
        let html = ''
        let status = 0
        for (let attempt = 0; attempt < 2; attempt++) {
          try {
            const res = await fetch(art.link, {
              headers: {
                'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.38(0x18002633) NetType/WIFI Language/zh_CN',
                'Referer': 'https://mp.weixin.qq.com/',
                'Accept': 'text/html,application/xhtml+xml',
                'Accept-Language': 'zh-CN,zh;q=0.9',
              },
              signal: AbortSignal.timeout(20_000),
            })
            status = res.status
            html = (await res.text()).slice(0, 500_000)
            // 正文验证: 必须含 js_content (否则是验证页, 重试)
            if (res.ok && html.includes('js_content')) break
          } catch { /* 超时/网络错误 → 重试 */ }
        }
        if (!html) return `(抓取失败 HTTP ${status})`
        // 路径1: 正常页面 js_content; 路径2: WAF 挑战页 content_noencode 兜底
        let text = html.includes('js_content') ? extractText(html) : ''
        if (!text || (text.match(/[\u4e00-\u9fff]/g) || []).length < 50) {
          const decoded = extractFromChallengePage(html)
          if (decoded) text = stripTags(decoded)
        }
        text = text.slice(0, maxChars)
        if (html.includes('已被发布者删除')) return `(「${art.title}」已被发布者删除)`
        if (!text.trim() || (text.match(/[\u4e00-\u9fff]/g) || []).length < 20) {
          return `(「${art.title}」正文提取失败, 可能是纯图片文章或已被删除)`
        }
        return `「${art.title || '无标题'}」(${art.source_name || '未知来源'}) 正文:\n${text}`
      }
      case 'get_stats': {
        await ensureDb()
        const sessions = await chatService.listSessions(undefined, 1000)
        const fav = await chatService.getFavorites({ limit: 1 })
        return `会话数: ${sessions.length}\n收藏总数: ${fav.success ? fav.total : '未知'}`
      }
      case 'search_memory': {
        const hits = ctx.memory.searchFacts(ctx.userId, String(args.keyword || ''))
        if (!hits.length) return '(长期记忆中无相关内容)'
        return hits.map(f => `· ${f.content}`).join('\n')
      }
      case 'save_memory': {
        const content = String(args.content || '').trim().slice(0, 60)
        if (!content) return '(内容为空, 未保存)'
        const added = ctx.memory.addFact(ctx.userId, content)
        return added ? '(已存入长期记忆)' : '(与已有记忆重复, 未保存)'
      }
      default:
        return `(未知工具: ${name})`
    }
  } catch (e: any) {
    return `(工具执行失败: ${e.message?.slice(0, 100)})`
  }
}
