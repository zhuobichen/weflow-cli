/**
 * NT Core - 4.x NT database access layer
 *
 * NT databases (xwechat_files/* /db_storage/message/message_0.db) use
 * SQLCipher 4 with plaintext_header_size=0. Decryption requires the
 * sqlcipher3 Python package. This module wraps scripts/nt_decrypt.py.
 */
import { execFile } from 'child_process'
import { promisify } from 'util'
import { existsSync, openSync, readSync, closeSync } from 'fs'
import { join, dirname } from 'path'
import { fileURLToPath } from 'url'
import crypto from 'crypto'
import { configService } from '../services/configService.js'
import { getPythonCommand } from '../utils/python.js'
import type { ChatSession, Message, Contact } from '../types.js'

const execFileAsync = promisify(execFile)
const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

function getPackageRoot(): string {
  const candidates = [
    join(__dirname, '..', '..', '..'), // dist/src/core
    join(__dirname, '..', '..'),       // src/core (tsx)
  ]
  return candidates.find((candidate) => existsSync(join(candidate, 'scripts', 'nt_decrypt.py')))
    || candidates[0]
}

export interface NtResult {
  success: boolean
  error?: string
}

export interface NtSessionsResult extends NtResult {
  sessions?: ChatSession[]
}

export interface NtMessagesResult extends NtResult {
  messages?: Message[]
}

export interface NtContactsResult extends NtResult {
  contacts?: Contact[]
}

export interface NtScanResult {
  success: boolean
  error?: string
  keys?: Array<{ key: string; salt: string }>
  databases?: Array<{ path: string; name: string; salt: string; size: number; wxid: string }>
  matched?: Array<{ path: string; name: string; salt: string; size: number; wxid: string; key: string }>
}

export class NtCore {
  private dbPath: string
  private keyHex: string
  private saltHex: string
  /** contact.db 路径 (用于显示备注名/昵称) */
  contactDbPath: string | null = null
  /** contact.db 解密密钥 */
  contactKey: string | null = null
  /** contact.db 盐值 */
  contactSalt: string | null = null

  constructor(dbPath: string, keyHex: string, saltHex: string) {
    this.dbPath = dbPath
    this.keyHex = keyHex
    this.saltHex = saltHex
  }

  /** 尝试自动发现并连接 contact.db */
  autoDetectContactDb(): boolean {
    // 从 message_0.db 路径推导 contact.db
    // message_0.db:  <xwechat>/<wxid>/db_storage/message/message_0.db
    // contact.db:    <xwechat>/<wxid>/db_storage/contact/contact.db
    const msgDir = this.dbPath.replace(/\\/g, '/')
    const parts = msgDir.split('/')
    // 找到 db_storage 位置
    const dbStorageIdx = parts.lastIndexOf('db_storage')
    if (dbStorageIdx < 0) return false

    const wxidDir = parts.slice(0, dbStorageIdx).join('/')
    const contactDbPath = `${wxidDir}/db_storage/contact/contact.db`

    if (existsSync(contactDbPath)) {
      this.contactDbPath = contactDbPath
      return true
    }
    return false
  }

  private get scriptPath(): string {
    return join(getPackageRoot(), 'scripts', 'nt_decrypt.py')
  }

  private async callPython(args: string[]): Promise<any> {
    try {
      const { stdout } = await execFileAsync(getPythonCommand(), [this.scriptPath, ...args], {
        timeout: 120_000,
        maxBuffer: 50 * 1024 * 1024,
        env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
        encoding: 'utf-8',
      })

      const lines = stdout.split('\n').filter((l: string) => l.trim())
      for (let i = lines.length - 1; i >= 0; i--) {
        const trimmed = lines[i].trim()
        if (trimmed.startsWith('{"')) {
          try {
            return JSON.parse(trimmed)
          } catch {
            // Try next line
          }
        }
      }
      return { error: 'No valid JSON output from Python script' }
    } catch (e: any) {
      const msg = e?.message || String(e)
      if (msg.includes('ENOENT') || msg.includes('python')) {
        return { error: 'Python not found. Install Python and sqlcipher3: pip install sqlcipher3' }
      }
      if (msg.includes('ETIMEDOUT') || msg.includes('killed')) {
        return { error: 'NT database query timed out' }
      }
      return { error: `NT database query failed: ${msg}` }
    }
  }

  /**
   * Scan memory for NT keys and match to databases.
   * Requires WeChat to be running.
   */
  static async scan(root?: string): Promise<NtScanResult> {
    try {
      const scriptPath = join(getPackageRoot(), 'scripts', 'nt_decrypt.py')
      const scanArgs = root ? ['scan', '--json', '--root', root] : ['scan', '--json']
      const { stdout } = await execFileAsync(getPythonCommand(), [scriptPath, ...scanArgs], {
        timeout: 300_000,
        maxBuffer: 10 * 1024 * 1024,
        env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
        encoding: 'utf-8',
      })

      const lines = stdout.split('\n').filter((l: string) => l.trim())
      for (let i = lines.length - 1; i >= 0; i--) {
        const trimmed = lines[i].trim()
        if (trimmed.startsWith('{"')) {
          let result: any
          try { result = JSON.parse(trimmed) } catch { continue }
          if (result.error) {
            return { success: false, error: result.error }
          }
          return {
            success: true,
            keys: result.keys || [],
            databases: result.databases || [],
            matched: result.matched || [],
          }
        }
      }
      return { success: false, error: 'Scan produced no valid output' }
    } catch (e: any) {
      const msg = e?.message || String(e)
      if (msg.includes('ENOENT') || msg.includes('python')) {
        return { success: false, error: 'Python not found. Install Python and add to PATH.' }
      }
      return { success: false, error: `NT scan failed: ${msg}` }
    }
  }

  /**
   * 验证 key+salt 能否真实打开数据库 (读取 sqlite_master 触发解密)。
   * cannotVerify=true 表示环境问题 (Python/sqlcipher3 缺失) 而非密钥错误。
   */
  static async verifyDbKey(dbPath: string, keyHex: string, saltHex: string): Promise<{
    success: boolean
    error?: string
    tables?: number
    cannotVerify?: boolean
  }> {
    const scriptPath = join(getPackageRoot(), 'scripts', 'nt_decrypt.py')
    let stdout = ''
    try {
      const r = await execFileAsync(getPythonCommand(), [scriptPath, 'verify', '--db', dbPath, '--key', keyHex, '--salt', saltHex], {
        timeout: 60_000,
        maxBuffer: 1024 * 1024,
        env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
        encoding: 'utf-8',
      })
      stdout = r.stdout
    } catch (e: any) {
      // 缺 sqlcipher3 等场景: 脚本打印 JSON 后 exit 1
      stdout = e?.stdout || ''
      if (!stdout.trim()) {
        return { success: false, cannotVerify: true, error: `Python 调用失败: ${String(e?.message || e).split('\n')[0]}` }
      }
    }
    const lines = stdout.split('\n').filter((l: string) => l.trim())
    for (let i = lines.length - 1; i >= 0; i--) {
      const trimmed = lines[i].trim()
      if (trimmed.startsWith('{"')) {
        try {
          const r = JSON.parse(trimmed)
          if (r.success === undefined) {
            // 只有 error 字段 = 环境问题 (如 "需要 sqlcipher3")
            return { success: false, cannotVerify: true, error: r.error }
          }
          return { success: !!r.success, tables: r.tables, error: r.error }
        } catch { /* try next line */ }
      }
    }
    return { success: false, cannotVerify: true, error: 'verify 无有效输出' }
  }

  async getSessions(keyword?: string): Promise<NtSessionsResult> {
    const args: string[] = [
      'sessions',
      '--db', this.dbPath,
      '--key', this.keyHex,
      '--salt', this.saltHex,
    ]
    if (keyword) args.push('--keyword', keyword)
    if (this.contactDbPath && this.contactKey && this.contactSalt) {
      args.push('--contact-db', this.contactDbPath)
      args.push('--contact-key', this.contactKey)
      args.push('--contact-salt', this.contactSalt)
    }
    const result = await this.callPython(args)
    if (result.error) {
      return { success: false, error: result.error }
    }
    return { success: true, sessions: result.sessions || [] }
  }

  async getMessages(talker: string, limit = 100, offset = 0): Promise<NtMessagesResult> {
    const args: string[] = [
      'messages',
      '--db', this.dbPath,
      '--key', this.keyHex,
      '--salt', this.saltHex,
      '--talker', talker,
      '--limit', String(limit),
      '--offset', String(offset),
    ]
    if (this.contactDbPath && this.contactKey && this.contactSalt) {
      args.push('--contact-db', this.contactDbPath)
      args.push('--contact-key', this.contactKey)
      args.push('--contact-salt', this.contactSalt)
    }
    // Pass own wxid from config for self-message detection
    const ownWxid = configService.get('wxid')
    if (ownWxid) args.push('--own-wxid', ownWxid)
    const result = await this.callPython(args)
    if (result.error) {
      return { success: false, error: result.error }
    }
    return { success: true, messages: result.messages || [] }
  }

  async getContacts(keyword?: string, limit = 200): Promise<NtContactsResult> {
    const args: string[] = [
      'contacts',
      '--db', this.dbPath,
      '--key', this.keyHex,
      '--salt', this.saltHex,
      '--limit', String(limit),
    ]
    if (keyword) args.push('--keyword', keyword)
    if (this.contactDbPath && this.contactKey && this.contactSalt) {
      args.push('--contact-db', this.contactDbPath)
      args.push('--contact-key', this.contactKey)
      args.push('--contact-salt', this.contactSalt)
    }
    const result = await this.callPython(args)
    if (result.error) {
      return { success: false, error: result.error }
    }
    return { success: true, contacts: result.contacts || [] }
  }

  // ====== SNS (朋友圈) ======

  /** 查询 SNS 朋友圈时间线 */
  async getSnsTimeline(snsDbPath: string, snsKey: string, snsSalt: string, opts: {
    limit?: number; offset?: number; usernames?: string[];
    keyword?: string; startTime?: number; endTime?: number;
  } = {}): Promise<{ success: boolean; timeline?: any[]; error?: string }> {
    const args: string[] = [
      'sns-timeline',
      '--db', snsDbPath,
      '--key', snsKey,
      '--salt', snsSalt,
      '--limit', String(opts.limit ?? 20),
      '--offset', String(opts.offset ?? 0),
    ]
    if (opts.usernames?.length) args.push('--usernames', JSON.stringify(opts.usernames))
    if (opts.keyword) args.push('--keyword', opts.keyword)
    if (opts.startTime) args.push('--start-time', String(opts.startTime))
    if (opts.endTime) args.push('--end-time', String(opts.endTime))
    const result = await this.callPython(args)
    if (result.error) return { success: false, error: result.error }
    return { success: true, timeline: result.timeline || [] }
  }

  /** 获取朋友圈中有动态的用户列表 */
  async getSnsUsernames(snsDbPath: string, snsKey: string, snsSalt: string): Promise<{ success: boolean; usernames?: string[]; error?: string }> {
    const args: string[] = [
      'sns-usernames',
      '--db', snsDbPath,
      '--key', snsKey,
      '--salt', snsSalt,
    ]
    const result = await this.callPython(args)
    if (result.error) return { success: false, error: result.error }
    return { success: true, usernames: result.usernames || [] }
  }

  /** 获取朋友圈统计信息 */
  async getSnsExportStats(snsDbPath: string, snsKey: string, snsSalt: string, myWxid?: string): Promise<{ success: boolean; data?: { totalPosts: number; totalFriends: number; myPosts: number | null }; error?: string }> {
    const args: string[] = [
      'sns-stats',
      '--db', snsDbPath,
      '--key', snsKey,
      '--salt', snsSalt,
    ]
    if (myWxid) args.push('--my-wxid', myWxid)
    const result = await this.callPython(args)
    if (result.error) return { success: false, error: result.error }
    return { success: true, data: result.data }
  }

  // ====== 收藏 (favorite.db) ======

  /**
   * 从全库共用 passphrase 派生某库的 raw key。
   * 微信 4.x 各库密钥 = PBKDF2-HMAC-SHA512(passphrase, 库salt, 256000轮, 32字节)
   */
  static deriveRawKey(passphraseHex: string, saltHex: string): string {
    const passphrase = Buffer.from(passphraseHex, 'hex')
    const salt = Buffer.from(saltHex, 'hex')
    return crypto.pbkdf2Sync(passphrase, salt, 256000, 32, 'sha512').toString('hex')
  }

  /** 读取数据库文件前 16 字节盐值 */
  static readDbSalt(dbPath: string): string | null {
    let fd: number
    try {
      fd = openSync(dbPath, 'r')
    } catch {
      return null
    }
    try {
      const buf = Buffer.alloc(16)
      const n = readSync(fd, buf, 0, 16, 0)
      return n === 16 ? buf.toString('hex') : null
    } catch {
      return null
    } finally {
      closeSync(fd)
    }
  }

  /** 查询收藏列表 (keyHex 为派生后的 raw key) */
  async getFavorites(favDbPath: string, keyHex: string, opts: {
    limit?: number; offset?: number; keyword?: string; favType?: number;
  } = {}): Promise<{ success: boolean; favorites?: any[]; total?: number; error?: string }> {
    const args: string[] = [
      'fav-list',
      '--db', favDbPath,
      '--key', keyHex,
      '--limit', String(opts.limit ?? 100),
      '--offset', String(opts.offset ?? 0),
    ]
    if (opts.keyword) args.push('--keyword', opts.keyword)
    if (opts.favType != null) args.push('--type', String(opts.favType))
    const result = await this.callPython(args)
    if (result.error) return { success: false, error: result.error }
    return { success: true, favorites: result.favorites || [], total: result.total ?? 0 }
  }
}
