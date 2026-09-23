/**
 * Agent 工具注册表 (OpenAI function-calling 格式, DeepSeek 兼容)。
 * 所有工具在本机执行; 结果经 PrivacyGate 脱敏后才进入 LLM 上下文。
 */
import { chatService } from './chatService.js'
import { runPythonJson } from './pythonBridge.js'
import { exportService } from './exportService.js'
import type { AssistantMemory } from './assistantMemory.js'
import { privacyGate } from './assistantPrivacy.js'
import { existsSync, readFileSync, readdirSync } from 'fs'
import { join } from 'path'
// 直接调进程的写法已收敛进 pythonBridge：这里不再 import child_process
import { resolvePackageRoot } from '../utils/packageRoot.js'
import { clipWithMarker } from '../utils/text.js'

const PKG_ROOT = resolvePackageRoot(import.meta.url)
const BIZ_DAILY_DIR = join(PKG_ROOT, 'output', 'biz-daily')
/** 单条聊天消息进上下文的字数上限（见 get_messages：引用消息要放得下正文+被引原文） */
const MSG_BODY_CHARS = 160
const VAULT_WIKI_DIR = join(PKG_ROOT, 'output', 'wechat-vault', 'Wiki', 'Concepts')

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

/** SSRF 防护: 仅 http(s), 拒绝内网/环回地址
 *
 * 导出是为了能测它：这是**抓取前的安全边界**，而它守着的是"别让助手被一条收藏里的链接
 * 带去访问内网"。纯函数，没有副作用。
 */
export function isSafeUrl(raw: string): boolean {
  try {
    const u = new URL(raw)
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return false
    const h = u.hostname.toLowerCase()
    if (!h) return false

    // IPv6 字面量（URL 里带方括号）。Node 会把 [0:0:0:0:0:0:0:1] 归一成 [::1]，
    // 但不碰 [fd00::1] 这类——所以下面按前缀逐个挡。
    if (h.startsWith('[')) {
      const inner = h.slice(1, -1)
      if (inner === '::' || inner === '::1') return false
      if (inner.startsWith('::ffff:')) return false      // IPv4 映射（可能是 127.0.0.1）
      if (/^f[cd]/.test(inner)) return false             // fc00::/7 唯一本地
      if (/^fe[89ab]/.test(inner)) return false          // fe80::/10 链路本地
      return true
    }

    if (h === 'localhost') return false
    // 纯数字或 0x 形式的整型主机名：内核会把它当 IPv4 用，而域名不可能是这种形状。
    // （实测 Node 对 2130706433 / 0x7f000001 会归一成 127.0.0.1，这道是双保险。）
    if (/^(0x[0-9a-f]+|[0-9]+)$/.test(h)) return false
    // 私网前缀**只在真的是点分四段时**才判：否则 `10.example.com` 这种公网域名会被
    // `^10\.` 误杀，工具会回一句「链接不安全，拒绝抓取」，而链接其实是好的。
    if (/^[0-9]{1,3}(\.[0-9]{1,3}){3}$/.test(h)) {
      if (/^(127\.|10\.|192\.168\.|169\.254\.|0\.)/.test(h)) return false
      if (/^172\.(1[6-9]|2[0-9]|3[01])\./.test(h)) return false
    }
    // 这是一份已知形态的黑名单，不是"这地址一定可公网路由"的证明：
    // NAT64（64:ff9b::/96）等映射形式不在覆盖范围内。
    return true
  } catch { return false }
}


/** 标签剥离 → 纯文本 */
export function stripTags(seg: string): string {
  return seg.replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<[^>]+>/g, '\n')
    .replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .split('\n').map(s => s.trim()).filter(Boolean).join('\n')
}

/** WAF 挑战页兜底: 正文藏在 cgiDataNew.content_noencode (JS 转义字符串) */
export function extractFromChallengePage(html: string): string | null {
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
export function extractText(html: string): string {
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

interface TalkerCandidate {
  username: string
  displayName?: string | null
}

class ToolInputError extends Error {}

export function boundedToolInteger(value: unknown, fallback: number, maximum: number, field = 'limit'): number {
  if (value === undefined || value === null || value === '') return fallback
  const parsed = Number(value)
  if (!Number.isInteger(parsed) || parsed < 1 || parsed > maximum) {
    throw new ToolInputError(`${field} 必须是 1-${maximum} 的整数`)
  }
  return parsed
}

export function resolveUniqueTalker(name: string, sessions: TalkerCandidate[]): string {
  const query = name.trim()
  if (!query) throw new ToolInputError('contact 不能为空')

  const unique = (matches: TalkerCandidate[]) => [...new Set(matches.map(item => item.username))]
  const usernameMatches = unique(sessions.filter(item => item.username === query))
  if (usernameMatches.length === 1) return usernameMatches[0]

  const exactNameMatches = unique(sessions.filter(item => item.displayName === query))
  if (exactNameMatches.length === 1) return exactNameMatches[0]
  if (exactNameMatches.length > 1) {
    throw new ToolInputError(`会话名称“${query}”对应多个会话，请改用会话 ID`)
  }

  const partialMatches = unique(sessions.filter(item => item.displayName?.includes(query)))
  if (partialMatches.length === 1) return partialMatches[0]
  if (partialMatches.length > 1) {
    throw new ToolInputError(`会话名称“${query}”匹配多个会话，请提供更完整名称或会话 ID`)
  }
  return query
}

/** 显示名 → username 解析 (会话表里两者都有) */
async function resolveTalker(name: string): Promise<string> {
  const sessions = await chatService.listSessions(undefined, 300)
  return resolveUniqueTalker(name, sessions)
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
      description: '搜索用户的微信收藏(主要是收藏的公众号文章)；**不给关键词就是最近收藏了什么**。',
      parameters: {
        type: 'object',
        properties: {
          keyword: { type: 'string', description: '搜索关键词' },
          limit: { type: 'number', description: '返回条数, 默认8' },
        },
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
      name: 'get_daily_report',
      description: '获取公众号日报: 某天推送了哪些文章(标题/来源/分类/AI摘要)。适合「今天/某天公众号推了什么」「最近有哪些AI文章」类问题。',
      parameters: {
        type: 'object',
        properties: {
          date: { type: 'string', description: '日期 YYYY-MM-DD, 默认最新一期' },
          topic: { type: 'string', description: '分类过滤: AI | 学术 | 新闻 | 文学 | 投资' },
          keyword: { type: 'string', description: '标题/摘要关键词过滤' },
          limit: { type: 'number', description: '返回条数, 默认15' },
        },
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'get_sns',
      description: '查看用户朋友圈: 最新动态时间线或统计概览(发帖数/好友数)。适合「朋友圈最近发了啥」「谁常发朋友圈」类问题。',
      parameters: {
        type: 'object',
        properties: {
          mode: { type: 'string', description: 'timeline (最新动态) / stats (统计) / users (谁发得最多), 默认 timeline' },
          limit: { type: 'number', description: 'timeline 模式条数, 默认10' },
        },
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'get_weread',
      description: '查看用户微信读书数据: 书架(shelf)、笔记(notebooks)、搜索书(search)。适合「我在读什么书」「某本书的笔记」类问题。',
      parameters: {
        type: 'object',
        properties: {
          mode: { type: 'string', description: 'shelf | notebooks | search, 默认 shelf' },
          keyword: { type: 'string', description: 'search 模式的书名关键词' },
        },
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'get_todos',
      description: '查看用户从聊天记录提取的待办任务清单(含优先级和截止时间)。适合「我最近有什么待办」「有什么紧急的事」类问题。',
      parameters: {
        type: 'object',
        properties: {
          status: { type: 'string', description: 'pending | done, 默认 pending' },
        },
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'search_chats',
      description: '在自己所有聊天记录里检索「在哪聊过某件事」。适合「上次说的那个部署方案是在哪聊的」'
        + '「谁提过这个客户」这类问题。它只匹配字面词（同义改写要靠别的路子），所以问题描述得具体些。'
        + '代价：会把你的问题与候选词发给判断模型（不发聊天正文），约 1-2 秒。',
      parameters: {
        type: 'object',
        properties: {
          question: { type: 'string', description: '要找的事，用自然语言描述' },
          per_card: { type: 'number', description: '每个会话最多回几条消息，默认 3' },
        },
        required: ['question'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'who_owes_reply',
      description: '看看谁在等你回话（对方说完就没下文的那种）。适合「我有没有漏回谁的消息」。'
        + '代价：逐会话问一次判断模型，可能要几十秒；只报谁在等，不回正文。',
      parameters: {
        type: 'object',
        properties: {
          days: { type: 'number', description: '只看最近多少天有动静的会话，默认 14' },
        },
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'search_semantic',
      description: '按**意思**找（语义/同义检索），而 search_chats 只匹配字面词。'
        + '「上次说的那个部署方案是在哪聊的」用 search_chats；「和钱有关的讨论」这种同义改写用这条。'
        + '代价：查询词会发给阿里云百炼做嵌入、候选片段会发给判断模型重排（都是仓库既有的云端路径），'
        + '需要先建过索引（weflow-cli search-index）。',
      parameters: {
        type: 'object',
        properties: {
          query: { type: 'string', description: '要找的意思，用自然语言描述' },
          top_k: { type: 'number', description: '要几条，默认 8，上限 20' },
        },
        required: ['query'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'export_chat',
      description: '把某个人的聊天记录导出成 HTML（含图片），落到 output/exports/ 下。'
        + '适合「帮我把和某某的聊天导出备份一下」。**会写文件**：每次新建一个带时间戳的目录，绝不覆盖已有的，'
        + '路径也不是你给而是固定的。',
      parameters: {
        type: 'object',
        properties: {
          contact: { type: 'string', description: '联系人显示名或备注名' },
          limit: { type: 'number', description: '最多导出多少条，默认 500，上限 5000' },
          format: { type: 'string', description: 'html(默认,含图片) / txt / json / excel' },
        },
        required: ['contact'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'search_knowledge',
      description: '搜索用户的本地知识库(从公众号文章沉淀的 Wiki 概念页与学习日报)。适合查概念解释、找之前整理过的知识。',
      parameters: {
        type: 'object',
        properties: {
          keyword: { type: 'string', description: '概念名或关键词, 如 RAG、Agent' },
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

export const MCP_READ_ONLY_TOOL_DEFS = TOOL_DEFS.filter(tool => tool.function.name !== 'save_memory')

/** 脚本类工具的失败回话：桥接层已经分好类（超时/退出码/没有 JSON/脚本自己报错），
 *  这里把它和 stderr 尾巴合起来**过一遍脱敏**再交给模型——stderr 里可能有密钥形状的东西，
 *  而工具结果是要出境的。
 */
function fail(what: string, result: { error?: string; stderr?: string }): string {
  const detail = [result.error, result.stderr].filter(Boolean).join(' · ')
  const { safe } = privacyGate.redact(detail)
  return `(${what}${safe ? ": " + safe : ""})`
}

export async function executeTool(name: string, args: Record<string, any>, ctx: ToolContext): Promise<string> {
  try {
    switch (name) {
      case 'list_sessions': {
        await ensureDb()
        const limit = boundedToolInteger(args.limit, 15, 30)
        const sessions = await chatService.listSessions(undefined, limit)
        if (!sessions.length) return '(未查到会话, 数据库可能未连接)'
        return sessions.map(s =>
          `· ${s.displayName || s.username}: ${(s.summary || '').replace(/\n/g, ' ').slice(0, 30)}`).join('\n')
      }
      case 'get_messages': {
        const contact = String(args.contact || '')
        if (!contact) return '(缺少 contact 参数)'
        const limit = boundedToolInteger(args.limit, 20, 50)
        const talker = await resolveTalker(contact)
        const msgs = await chatService.getMessages(talker, limit)
        if (!msgs.length) return `(没找到「${contact}」的消息)`
        return msgs.map(m => {
          // 非文本消息优先用 `parsedContent`：它是读取器给的**显示形态**（`[图片]`、
          // `[文件] Base.csv`、`某人 撤回了一条消息`…），而 `content` 对这类消息可能是
          // 原始 XML（含 md5 与 cdn 链接）——那是给机器看的，不该塞进模型上下文。
          const raw = m.localType === 1
            ? (m.content || m.parsedContent || '')
            : (m.parsedContent || m.content || '')
          // 每条 160 字。此前是 80，引用消息（`[引用] 回复 ｜ 引：原文`）在 80 字里
          // 引文只剩七八个字，等于白带；长文本消息也被从中间切掉。上限仍是有界的：
          // limit 最多 50 条 × 约 180 字 ≈ 9k 字符。截断留省略号——切了却看起来像
          // 说完了，模型会把半句当整句。
          const flat = raw.replace(/\n/g, ' ')
          const body = privacyGate.maskMessageBody(clipWithMarker(flat, MSG_BODY_CHARS))
          return `[${fmtTime(m.createTime)}] ${m.isSend ? '用户' : (m.senderUsername || '对方')}: ${body}`
        }).join('\n')
      }
      case 'search_favorites': {
        await ensureDb()
        // 不给关键词就是"最近收藏了什么"——这一问在聊天里很自然，没理由逼调用方编一个词
        const keyword = String(args.keyword || '')
        const limit = boundedToolInteger(args.limit, keyword ? 8 : 15, keyword ? 15 : 30)
        const r = keyword ? await chatService.getFavorites({ keyword, limit })
          : await chatService.getFavorites({ limit })
        if (!r.success || !r.favorites?.length) {
          if (r.error) return `(查询失败: ${r.error})`
          return keyword ? `(收藏中未搜到「${keyword}」)` : '(收藏是空的，或收藏库还没连上)'
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
        const maxChars = boundedToolInteger(args.max_chars, 3000, 6000, 'max_chars')
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
      case 'get_daily_report': {
        const dates = existsSync(BIZ_DAILY_DIR)
          ? readdirSync(BIZ_DAILY_DIR).filter(d => /^\d{4}-\d{2}-\d{2}$/.test(d)).sort().reverse()
          : []
        if (!dates.length) return '(还没有日报数据, 先运行 biz_daily 生成)'
        const date = String(args.date || dates[0])
        const articlesFile = join(BIZ_DAILY_DIR, date, '.articles.json')
        if (!existsSync(articlesFile)) return `(${date} 没有日报。可用日期: ${dates.slice(0, 5).join(', ')})`
        const raw = JSON.parse(readFileSync(articlesFile, 'utf8'))
        const all = Array.isArray(raw) ? raw : (raw.articles || Object.values(raw))
        let list = all as any[]
        if (args.topic) list = list.filter(a => a.topic === args.topic)
        if (args.keyword) {
          const kw = String(args.keyword).toLowerCase()
          list = list.filter(a =>
            (a.title || '').toLowerCase().includes(kw) || (a.summary || '').toLowerCase().includes(kw))
        }
        const limit = boundedToolInteger(args.limit, 15, 30)
        if (!list.length) return `(${date} 日报共 ${all.length} 篇, 过滤后无匹配)`
        const byTopic: Record<string, number> = {}
        for (const a of all) byTopic[a.topic] = (byTopic[a.topic] || 0) + 1
        const topicStat = Object.entries(byTopic).map(([t, n]) => `${t}${n}篇`).join(' ')
        return `${date} 日报共 ${all.length} 篇 (${topicStat}), 匹配 ${list.length} 篇:\n` +
          list.slice(0, limit).map(a => {
            const summary = (a.summary || '').replace(/\s+/g, ' ').slice(0, 80)
            return `· [${a.topic}] ${a.title} (${a.source})${summary ? `\n  ${summary}` : ''}`
          }).join('\n')
      }
      case 'get_sns': {
        await ensureDb()
        if (String(args.mode || 'timeline') === 'users') {
          // 谁常发朋友圈：本地把最近的时间线按发帖人聚合。**不新增出境**——数据本来就在这一条工具里。
          const r = await chatService.getSnsTimeline({ limit: 200 })
          if (!r.success || !r.timeline?.length) {
            return r.error ? `(朋友圈查询失败: ${r.error})` : '(朋友圈暂无缓存数据)'
          }
          const byAuthor = new Map<string, number>()
          for (const post of r.timeline) {
            const who = String(post.nickname || post.username || '未知')
            byAuthor.set(who, (byAuthor.get(who) ?? 0) + 1)
          }
          const ranked = [...byAuthor.entries()].sort((a, b) => b[1] - a[1]).slice(0, 10)
          const nl = String.fromCharCode(10)
          return `最近 ${r.timeline.length} 条朋友圈里，发得最多的：`
            + ranked.map(([who, n]) => `${nl}· ${who}：${n} 条`).join('')
        }
        if (String(args.mode || 'timeline') === 'stats') {
          const r = await chatService.getSnsExportStats()
          if (!r.success) return `(朋友圈统计失败: ${r.error})`
          const d = r.data!
          return `朋友圈统计: 总动态 ${d.totalPosts} 条, 发布过动态的好友 ${d.totalFriends} 人${d.myPosts != null ? `, 用户自己发过 ${d.myPosts} 条` : ''}`
        }
        const limit = boundedToolInteger(args.limit, 10, 20)
        const r = await chatService.getSnsTimeline({ limit })
        if (!r.success || !r.timeline?.length) return r.error ? `(朋友圈查询失败: ${r.error})` : '(朋友圈暂无缓存数据)'
        return `最新 ${r.timeline.length} 条朋友圈:\n` + r.timeline.slice(0, limit).map((p: any) => {
          const content = (p.content || '').replace(/\s+/g, ' ').slice(0, 100)
          return `· [${fmtTime(p.create_time)}] ${p.nickname || p.username}: ${content}${p.media_count ? ` [${p.media_count}图]` : ''}`
        }).join('\n')
      }
      case 'get_weread': {
        const { wereadService } = await import('./wereadService.js')
        const mode = String(args.mode || 'shelf')
        const apiKey = (await import('./configService.js')).configService.get('wereadApiKey')
        if (!apiKey) return '(微信读书未配置: config set wereadApiKey <key>)'
        try {
          if (mode === 'notebooks') {
            const r = await wereadService.notebooks(20)
            if (!r.ok || !r.data?.books?.length) return `(暂无读书笔记: ${r.error || ''})`
            return `有笔记的书 ${r.data.books.length} 本:\n` +
              r.data.books.slice(0, 15).map((b: any) => `· ${b.title} (${b.author}) — ${b.noteCount || 0} 条笔记`).join('\n')
          }
          if (mode === 'search') {
            const kw = String(args.keyword || '')
            if (!kw) return '(search 模式需要 keyword)'
            const r = await wereadService.search(kw, 8)
            if (!r.ok || !r.data?.books?.length) return `(没搜到「${kw}」: ${r.error || ''})`
            return `搜索「${kw}」结果:\n` +
              r.data.books.map((b: any) => `· ${b.title} (${b.author})`).join('\n')
          }
          const r = await wereadService.shelf()
          if (!r.ok || !r.data?.books?.length) return `(书架为空或读取失败: ${r.error || ''})`
          const books = r.data.books
          const reading = books.filter((b: any) => b.progress && b.progress > 0 && b.progress < 100)
          return `书架共 ${books.length} 本, 在读 ${reading.length} 本:\n` +
            (reading.length ? reading.slice(0, 10).map((b: any) =>
              `· ${b.title} (${b.author}) — 已读 ${b.progress || 0}%`).join('\n') : books.slice(0, 10).map((b: any) => `· ${b.title}`).join('\n'))
        } catch {
          return '(微信读书接口失败，请检查网络和本地配置)'
        }
      }
      case 'get_todos': {
        const status = String(args.status || 'pending')
        // 走 pythonBridge（唯一的脚本调用入口）：失败原因分三类报出来，不解析人类可读文本
        const result = await runPythonJson<any[]>('extract_todos.py', ['list', '--status', status, '--json'])
        if (!result.ok) return fail('待办查询失败', result)
        const todos = Array.isArray(result.data) ? result.data : []
        if (!todos.length) return `(没有${status === 'done' ? '已完成' : '待办'}任务)`
        return `${status === 'done' ? '已完成' : '待办'} ${todos.length} 项:\n` +
          todos.slice(0, 15).map((t: any) =>
            `· [${t.urgency || '中'}] ${t.task || t.content || t.text || t.title}${t.deadline && t.deadline !== '未提及' ? ` (截止 ${t.deadline})` : ''}`).join('\n')
      }
      case 'search_chats': {
        // 跨会话检索：本机 message_fts 索引 + 判断模型挑查询词（**只发候选词与问题，不发聊天内容**）
        const question = String(args.question || '').trim()
        if (!question) return '(缺少 question 参数)'
        const perCard = boundedToolInteger(args.per_card, 3, 10, 'per_card')
        const result = await runPythonJson<any>('route_cards.py',
          ['ask', question, '--yes', '--json', '--per-card', String(perCard)], { timeoutMs: 90_000 })
        if (!result.ok) return fail('会话检索失败', result)

        const ranked = Array.isArray(result.data?.ranked) ? result.data.ranked : []
        if (!ranked.length) {
          return `(没有会话字面命中「${question}」——检索只匹配字面词，换几个词再试)`
        }
        const lines = [`命中 ${ranked.length} 个会话（查询词：${(result.data.terms || []).join('、')}）：`]
        for (const card of ranked.slice(0, 6)) {
          lines.push(`· #${card.id} [${card.kind}] ${card.label}（该会话 ${card.messages} 条消息，最后活动 ${card.lastDaysAgo} 天前）`)
          const rows = result.data?.messages?.[String(card.id)] || []
          for (const row of rows.slice(0, perCard)) {
            // 第三方聊天正文与 get_messages **同一条纪律**：出境前按隐私档位处理
            const body = privacyGate.maskMessageBody(String(row?.text || '').replace(/\s+/g, ' ').slice(0, 60))
            lines.push(`    ${body}`)
          }
        }
        return lines.join('\n')
      }
      case 'who_owes_reply': {
        // 谁在等我回话：逐会话问一次判断模型（较慢）。**不给正文**——真要看他写了什么，
        // 用 get_messages 单独查，那条路有完整的隐私处理。
        const days = boundedToolInteger(args.days, 14, 60, 'days')
        const result = await runPythonJson<any>('reply_debt.py', ['--days', String(days), '--json'],
          { timeoutMs: 180_000 })
        if (!result.ok) return fail('欠账查询失败', result)

        const debts = Array.isArray(result.data?.debts) ? result.data.debts : []
        if (!debts.length) return `(最近 ${days} 天没有明显在等你回话的会话)`
        const lines = [`在等你回话的 ${debts.length} 个会话（最近 ${days} 天）：`]
        for (const row of debts.slice(0, 8)) {
          const prob = Number(row.waiting ?? 0)
          const urgency = row.urgencyScore === null || row.urgencyScore === undefined ? '' : ` · 紧急度 ${row.urgencyScore}`
          lines.push(`· ${row.name}（等了 ${row.days} 天 · 概率 ${prob.toFixed(2)}${urgency}${row.kind ? ' · ' + row.kind : ''}）`)
        }
        lines.push('（想看某人具体说了什么，用 get_messages 单独查；这里只报谁在等。）')
        return lines.join('\n')
      }
      case 'search_semantic': {
        // 语义/同义检索（向量相似度 + 决策模型重排）。字面词搜不到时用这条。
        // **出境说明**：查询词要发阿里云百炼做嵌入，候选片段要发判断模型重排——
        // 两者都在仓库既有的云端路径上（不是新类别），但这里如实写出来。
        const query = String(args.query || '').trim()
        if (!query) return '(缺少 query 参数)'
        const topK = boundedToolInteger(args.top_k, 8, 20, 'top_k')
        // **查询词走环境变量、不进 argv**：仓库写进测试的隐私纪律（进程列表里看不到正文）
        const result = await runPythonJson<any[]>('semantic_search.py',
          ['search', '--top-k', String(topK)],
          { env: { WEFLOW_SEARCH_QUERY: query }, timeoutMs: 90_000 })
        if (!result.ok) return fail('语义检索失败（索引可能还没建：weflow-cli search-index）', result)

        const rows = Array.isArray(result.data) ? result.data : []
        if (!rows.length) return `(语义检索没有结果「${query}」)`
        return `语义检索「${query}」前 ${rows.length} 条：` + String.fromCharCode(10) + rows.map((row: any) => {
          const title = String(row.title || row.source || '(无标题)').slice(0, 40)
          const score = typeof row.score === 'number' ? `（${row.score.toFixed(2)}）` : ''
          const text = privacyGate.maskMessageBody(
            String(row.text || row.content || '').replace(/\s+/g, ' ').slice(0, 80))
          const nl = String.fromCharCode(10)
          return `· ${title}${score}${text ? nl + '    ' + text : ''}`
        }).join(String.fromCharCode(10))
      }
      case 'export_chat': {
        // **写操作**，边界写死：只往 output/exports/ 下**新建**目录（名字带时间戳），绝不覆盖；
        // 路径由这里拼，模型给不了任意路径。用户是在对话里明确要求的，这就是那次确认。
        const contact = String(args.contact || '').trim()
        if (!contact) return '(缺少 contact 参数)'
        const limit = boundedToolInteger(args.limit, 500, 5000, 'limit')
        const format = String(args.format || 'html').toLowerCase()
        if (!['html', 'txt', 'json', 'excel'].includes(format)) {
          return `(参数错误: format 只能是 html/txt/json/excel，收到 ${format.slice(0, 12)})`
        }
        const talker = await resolveTalker(contact)

        const safeName = contact.replace(/[\/:*?"<>|]/g, '_').slice(0, 20) || 'chat'
        const stamp = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 12)
        // 目录名到秒，同一秒里导两次会撞进同一个目录——那就不是"绝不覆盖"了。撞了就往后加序号。
        // 导出根目录可用环境变量改（测试用临时目录；也方便自定义）。默认在仓库的 output/exports 下。
        const exportRoot = process.env.WEFLOW_ASSISTANT_EXPORT_ROOT || join(PKG_ROOT, 'output', 'exports')
        let outDir = join(exportRoot, `${safeName}-${stamp}`)
        for (let n = 2; existsSync(outDir) && n < 100; n++) {
          outDir = join(exportRoot, `${safeName}-${stamp}-${n}`)
        }
        const result = format === 'txt' ? await exportService.exportTxt(talker, outDir, limit)
          : format === 'json' ? await exportService.exportJson(talker, outDir, limit)
            : format === 'excel' ? await exportService.exportExcel(talker, outDir, limit)
              : await exportService.exportHtml(talker, outDir, limit, '', undefined, undefined, true)
        if (!result.success) return `(导出失败: ${String(result.error || '未知').slice(0, 100)})`
        const suffix = format === 'html' ? '，含图片' : ''
        return `已导出 ${result.count ?? 0} 条消息到 output/exports/${safeName}-${stamp}/（${format}${suffix}）`
      }
      case 'search_knowledge': {
        const kw = String(args.keyword || '')
        if (!kw) return '(缺少 keyword 参数)'
        if (!existsSync(VAULT_WIKI_DIR)) return '(知识库尚未生成, 先运行 weflow-cli wiki compile)'
        const files = readdirSync(VAULT_WIKI_DIR).filter(f => f.endsWith('.md'))
        const hits = files.filter(f => f.replace('.md', '').toLowerCase().includes(kw.toLowerCase()))
        if (!hits.length) {
          const contentHits = files.filter(f => readFileSync(join(VAULT_WIKI_DIR, f), 'utf8').toLowerCase().includes(kw.toLowerCase())).slice(0, 5)
          if (!contentHits.length) return `(知识库未收录「${kw}」, 共 ${files.length} 个概念页)`
          return `正文提及「${kw}」的概念页:\n` + contentHits.map(f => `· ${f.replace('.md', '')}`).join('\n')
        }
        const page = readFileSync(join(VAULT_WIKI_DIR, hits[0]), 'utf8')
        return `「${hits[0].replace('.md', '')}」概念页:\n${page.slice(0, 2000)}`
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
  } catch (error) {
    if (error instanceof ToolInputError) return `(参数错误: ${error.message})`
    return '(工具执行失败，请检查本地配置或运行状态)'
  }
}
