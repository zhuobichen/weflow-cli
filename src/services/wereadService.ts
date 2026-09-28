/**
 * 微信读书 Agent API Gateway 封装
 *
 * 接口文档: https://weread.qq.com/r/weread-skills —— 登录后点「快速配置」，页面上给出
 *          `api-key`，并提供一个官方 skill 包（`https://cdn.weread.qq.com/skills/weread-skills.zip`）。
 *          **原来的注释指的是 `~/.claude/skills/weread-skills/`，那个目录在本机已不存在**
 *          （2026-09-28 核实），照它去找是找不到的。
 * Gateway:  https://i.weread.qq.com/api/agent/gateway
 */

import https from 'node:https'
import { configService } from './configService.js'

const GATEWAY = 'https://i.weread.qq.com/api/agent/gateway'
const SKILL_VERSION = '1.0.3'

/**
 * 从两个来源里挑出可用的 key（环境变量优先，配置项兜底）。
 *
 * **为什么是两个来源**：助手的 `get_weread` 判据读**配置项** `wereadApiKey`
 * （`assistantTools.ts` 的 `TOOL_REQUIREMENTS`），而 CLI 的 `weread` 命令一直只读
 * **环境变量** `WEREAD_API_KEY`（官方那篇配置文档给的写法就是 export 环境变量）。
 * 只认一个的后果是：用户按 CLI 的提示配好配置项，助手能用、`weread shelf` 却回你
 * "未设置 WEREAD_API_KEY" —— 2026-09-28 实测踩到。
 *
 * 环境变量优先，因为官方文档明确建议"出于安全考虑，最好不要把 api-key 直接扔给 AI"。
 * 两边都空白时返回空串，调用方按"没配"处理（`config set` 允许把值清空）。
 */
export function resolveWereadApiKey(fromEnv?: string | null, fromConfig?: string | null): string {
  return String(fromEnv || '').trim() || String(fromConfig || '').trim()
}

// weread.qq.com 服务端不支持 TLS 1.3，需要强制使用 TLS 1.2
const httpsAgent = new https.Agent({ maxVersion: 'TLSv1.2' })

export interface WereadResult<T = any> {
  ok: boolean
  data?: T
  error?: string
}

// ---- shelf ----

export interface ShelfBook {
  bookId: string
  title: string
  author: string
  cover: string
  category: string
  readUpdateTime: number
  finishReading: number
  updateTime: number
  secret: number
  isTop: boolean
  payType: number
}

export interface ShelfData {
  books: ShelfBook[]
  albums: any[]
  mp: any
  archive: { name: string; bookIds: string[]; albumIds: string[] }[]
  bookCount: number
}

// ---- readdata ----

export interface ReadDetail {
  baseTime: number
  readTimes: Record<string, number>
  readDays: number
  totalReadTime: number
  dayAverageReadTime: number
  compare?: number
  readLongest: { book?: any; albumInfo?: any; readTime: number; tags?: string[] }[]
  readStat: { stat: string; counts: string; scheme?: string }[]
  preferCategory: { categoryId: number; categoryTitle: string; readingTime: number; readingCount: number; val: number }[]
  preferCategoryWord?: string
  preferTime: number[]
  preferTimeWord?: string
}

// ---- notes ----

export interface NotebookItem {
  bookId: string
  /**
   * **书名与作者嵌在这里，条目顶层没有。** 实测（2026-09-28，`/user/notebooks` 的真实响应）：
   * 条目只有 `bookId` / `noteCount` / `bookmarkCount` / `readingProgress` / `sort` / `markedStatus`
   * 等，读 `item.title` 会安静地拿到 `undefined` —— 工具照常输出，只是每本书都没名字。
   */
  book?: { title?: string; author?: string; cover?: string }
  /** 顶层这两个字段**不是**接口给的，留作兜底：网关换版本时形状可能不同。 */
  title?: string
  author?: string
  cover?: string
  noteCount: number
  bookmarkCount: number
  readingProgress?: number
  sort?: number
}

export interface NoteDetail {
  bookId: string
  chapterUid: number
  chapterTitle: string
  markText: string
  content: string
  range: string
  createTime: number
  type: number  // 0=划线, 1=想法
}

// ---- search ----

export interface SearchResult {
  bookId: string
  title: string
  author: string
  cover: string
  rating: number
  ratingCount: number
  category: string
  wordCount: string
  intro: string
}

// ---- book ----

export interface BookInfo {
  bookId: string
  title: string
  author: string
  cover: string
  rating: number
  ratingCount: number
  category: string
  wordCount: string
  intro: string
  publisher: string
  isbn: string
  totalWords: string
  format: string
}

export interface ChapterInfo {
  bookId: string
  title: string
  chapters: { chapterUid: number; title: string; level: number; wordCount: number }[]
}

// ---- review ----

export interface BookReview {
  reviewId: string
  content: string
  rating: number
  createTime: number
  user: { name: string; avatar: string }
  likeCount: number
}

// ---- discover ----

export interface RecommendBook {
  bookId: string
  title: string
  author: string
  cover: string
  rating: number
  intro: string
}

// ---- profile ----

export interface UserProfile {
  vid: string
  name: string
  avatar: string
  gender: number
  totalReadTime: number
  totalReadDays: number
  totalBooks: number
  totalFinished: number
  totalNotes: number
  totalHighlights: number
  totalReviews: number
}


export class WereadService {
  private apiKey: string

  constructor(apiKey?: string) {
    // 不传 key 时按 `resolveWereadApiKey` 的顺序自己找：环境变量 → 配置项。
    //
    // **配置项这一路是 2026-09-28 补的，而且是端到端才发现的。** 助手的 `get_weread`：
    // 可用性判据读配置项（所以配好之后工具会出现在工具表里），执行时用的却是模块单例
    // `new WereadService()` —— 那个单例只读环境变量。于是工具"看得见、调不动"，回一句
    // "未设置 WEREAD_API_KEY"。只改 CLI 那处是不够的：同一个密钥在当时有**三种读法**。
    this.apiKey = apiKey || resolveWereadApiKey(
      process.env.WEREAD_API_KEY,
      String(configService.get('wereadApiKey') || ''),
    )
  }

  private async call<T>(apiName: string, params: Record<string, any> = {}): Promise<WereadResult<T>> {
    if (!this.apiKey) {
      return { ok: false, error: '未设置 WEREAD_API_KEY，请设置环境变量或在 CLI 中配置' }
    }

    try {
      const body = JSON.stringify({ api_name: apiName, skill_version: SKILL_VERSION, ...params })
      const json = await new Promise<any>((resolve, reject) => {
        const url = new URL(GATEWAY)
        const req = https.request({
          hostname: url.hostname,
          path: url.pathname,
          method: 'POST',
          agent: httpsAgent,
          headers: {
            'Authorization': `Bearer ${this.apiKey}`,
            'Content-Type': 'application/json',
            'Content-Length': Buffer.byteLength(body),
          },
        }, (res) => {
          let data = ''
          res.on('data', (chunk: Buffer) => data += chunk.toString())
          res.on('end', () => {
            try { resolve(JSON.parse(data)) }
            catch { reject(new Error(`Invalid JSON response: ${data.slice(0, 200)}`)) }
          })
        })
        req.on('error', reject)
        req.write(body)
        req.end()
      })

      if (json.errcode && json.errcode !== 0) {
        return { ok: false, error: `API 错误: ${json.errmsg || json.errcode}` }
      }
      return { ok: true, data: json as T }
    } catch (e: any) {
      return { ok: false, error: `请求失败: ${e.message}` }
    }
  }

  /** 书架同步 */
  async shelf(): Promise<WereadResult<ShelfData>> {
    return this.call<ShelfData>('/shelf/sync')
  }

  /** 阅读统计: mode = weekly | monthly | annually | overall */
  async readData(mode: string = 'monthly', baseTime?: number): Promise<WereadResult<ReadDetail>> {
    const params: any = { mode }
    if (baseTime !== undefined) params.baseTime = baseTime
    return this.call<ReadDetail>('/readdata/detail', params)
  }

  /** 笔记列表: 获取用户的笔记本列表 */
  async notebooks(count: number = 100, lastSort?: number): Promise<WereadResult<{ books: NotebookItem[]; synckey: number }>> {
    const params: any = { count }
    if (lastSort !== undefined) params.lastSort = lastSort
    return this.call('/user/notebooks', params)
  }

  /** 某本书的划线 */
  async bookmarks(bookId: string, count: number = 50): Promise<WereadResult<{ updated: NoteDetail[]; removed: any[]; synckey: number }>> {
    return this.call('/book/bookmarklist', { bookId, count })
  }

  /** 某本书的热门划线 */
  async bestBookmarks(bookId: string, count: number = 20): Promise<WereadResult<{ chapters: any[] }>> {
    return this.call('/book/bestbookmarks', { bookId, count })
  }

  /** 搜索书籍 */
  async search(keyword: string, count: number = 10, scope: string = 'all'): Promise<WereadResult<{ books: SearchResult[] }>> {
    return this.call('/store/search', { keyword, count, scope })
  }

  /** 书籍详情 */
  async bookInfo(bookId: string): Promise<WereadResult<BookInfo>> {
    return this.call<BookInfo>('/book/info', { bookId })
  }

  /** 章节目录 */
  async chapterInfo(bookId: string): Promise<WereadResult<ChapterInfo>> {
    return this.call<ChapterInfo>('/book/chapterinfo', { bookId })
  }

  /** 阅读进度 */
  async getProgress(bookId: string): Promise<WereadResult<{ chapterUid: number; chapterTitle: string; progress: number }>> {
    return this.call('/book/getprogress', { bookId })
  }

  /** 书评 */
  async reviews(bookId: string, count: number = 20, sort: string = 'hot'): Promise<WereadResult<{ reviews: BookReview[] }>> {
    return this.call('/book/readreviews', { bookId, count, sort })
  }

  /** 个性化推荐 */
  async recommend(count: number = 10): Promise<WereadResult<{ books: RecommendBook[] }>> {
    return this.call('/book/recommend', { count })
  }

  /** 相似推荐 */
  async similar(bookId: string, count: number = 5): Promise<WereadResult<{ books: RecommendBook[] }>> {
    return this.call('/book/recommend', { bookId, count, type: 'similar' })
  }

  /** 个人中心（聚合多个接口） */
  async profile(): Promise<WereadResult<UserProfile>> {
    const [shelfRes, readRes] = await Promise.all([
      this.shelf(),
      this.readData('overall'),
    ])

    if (!shelfRes.ok) return { ok: false, error: shelfRes.error }
    if (!readRes.ok) return { ok: false, error: readRes.error }

    const shelf = shelfRes.data!
    const read = readRes.data!

    return {
      ok: true,
      data: {
        vid: '',
        name: '',
        avatar: '',
        gender: 0,
        totalReadTime: read.totalReadTime || 0,
        totalReadDays: read.readDays || 0,
        totalBooks: shelf.books?.length || 0,
        totalFinished: shelf.books?.filter(b => b.finishReading === 1).length || 0,
        totalNotes: 0,
        totalHighlights: 0,
        totalReviews: 0,
      },
    }
  }
}

export const wereadService = new WereadService()
