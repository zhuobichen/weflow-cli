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
        const keyword = String(args.keyword || '')
        if (!keyword) return '(缺少 keyword 参数)'
        const limit = Math.min(args.limit || 8, 15)
        const r = await chatService.getFavorites({ keyword, limit })
        if (!r.success || !r.favorites?.length) {
          return r.error ? `(查询失败: ${r.error})` : `(收藏中未搜到「${keyword}」)`
        }
        return `共${r.total}条, 前${r.favorites.length}条:\n` +
          r.favorites.map(f => `· ${f.title || '(无标题)'} (${f.source_name || '未知来源'})`).join('\n')
      }
      case 'get_stats': {
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
