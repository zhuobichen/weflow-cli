/**
 * 微信读书 Agent API Gateway 封装
 *
 * 接口文档: ~/.claude/skills/weread-skills/
 * Gateway:  https://i.weread.qq.com/api/agent/gateway
 */

import https from 'node:https'

const GATEWAY = 'https://i.weread.qq.com/api/agent/gateway'
const SKILL_VERSION = '1.0.3'

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
  title: string
  author: string
  cover: string
  highlightCount: number
  noteCount: number
  bookmarkCount: number
  updateTime: number
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
    this.apiKey = apiKey || process.env.WEREAD_API_KEY || ''
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
