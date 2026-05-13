/**
 * NT Core - 4.x NT database access layer
 *
 * NT databases (xwechat_files/* /db_storage/message/message_0.db) use
 * SQLCipher 4 with plaintext_header_size=0. Decryption requires the
 * sqlcipher3 Python package. This module wraps scripts/nt_decrypt.py.
 */
import { execFile } from 'child_process'
import { promisify } from 'util'
import { existsSync } from 'fs'
import { join } from 'path'
import type { ChatSession, Message, Contact } from '../types.js'

const execFileAsync = promisify(execFile)

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
    return join(process.cwd(), 'scripts', 'nt_decrypt.py')
  }

  private async callPython(args: string[]): Promise<any> {
    try {
      const { stdout } = await execFileAsync('python', [this.scriptPath, ...args], {
        timeout: 120_000,
        maxBuffer: 50 * 1024 * 1024,
        env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
        encoding: 'utf-8',
      })

      const lines = stdout.split('\n').filter((l: string) => l.trim())
      for (let i = lines.length - 1; i >= 0; i--) {
        const trimmed = lines[i].trim()
        if (trimmed.startsWith('{')) {
          return JSON.parse(trimmed)
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
  static async scan(): Promise<NtScanResult> {
    try {
      const scriptPath = join(process.cwd(), 'scripts', 'nt_decrypt.py')
      const { stdout } = await execFileAsync('python', [scriptPath, 'scan', '--json'], {
        timeout: 300_000,
        maxBuffer: 10 * 1024 * 1024,
        env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
        encoding: 'utf-8',
      })

      const lines = stdout.split('\n').filter((l: string) => l.trim())
      for (let i = lines.length - 1; i >= 0; i--) {
        const trimmed = lines[i].trim()
        if (trimmed.startsWith('{')) {
          const result = JSON.parse(trimmed)
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
    const result = await this.callPython([
      'messages',
      '--db', this.dbPath,
      '--key', this.keyHex,
      '--salt', this.saltHex,
      '--talker', talker,
      '--limit', String(limit),
      '--offset', String(offset),
    ])
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
}
