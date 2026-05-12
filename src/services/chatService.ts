import { join } from 'path'
import { existsSync } from 'fs'
import { WcdbCore } from '../core/wcdbCore.js'
import { SqlcipherCore } from '../core/sqlcipherCore.js'
import { configService } from './configService.js'
import type { ChatSession, Message, Contact, DataVersion } from '../types.js'

// 创建单例
const wcdbCore = new WcdbCore()
const sqlcipherCore = new SqlcipherCore()

export class ChatService {
  private connected = false
  private activeVersion: DataVersion | null = null
  /** 4.x 是否通过原始 SQLite 连接 (而非 WCDB API) */
  private useRawSqlite4x = false

  /**
   * 检测数据版本: 3.x 或 4.x
   */
  private detectVersion(): DataVersion | null {
    const version = configService.get('dataVersion')
    if (version === '3.x' || version === '4.x') return version as DataVersion

    // 自动检测: 检查已配置的路径
    const dbPath4x = configService.get('dbPath')
    const dbPath3x = configService.get('dbPath3x')
    const key4x = configService.get('decryptKey')
    const key3x = configService.get('decryptKey3x')

    if (dbPath3x && key3x) return '3.x'
    if (dbPath4x && key4x) return '4.x'

    return null
  }

  async connect(): Promise<{ success: boolean; error?: string }> {
    if (this.connected) return { success: true }

    const version = this.detectVersion()
    if (!version) {
      return { success: false, error: '请先运行 weflow-cli init 完成配置' }
    }

    if (version === '4.x') {
      return this.connect4x()
    } else {
      return this.connect3x()
    }
  }

  private async connect4x(): Promise<{ success: boolean; error?: string }> {
    const dbPath = configService.get('dbPath')
    const wxid = configService.get('wxid')
    const decryptKey = configService.get('decryptKey')

    if (!dbPath || !decryptKey) {
      return { success: false, error: '4.x 配置不完整，请运行 weflow-cli init' }
    }

    // 优先使用已解密的 MSG0_decrypted.db (纯 SQLite)
    const rawMsg0Path = join(dbPath, wxid || '', 'Msg', 'Multi', 'MSG0_decrypted.db')
    if (existsSync(rawMsg0Path)) {
      const result = await sqlcipherCore.openRaw(rawMsg0Path, wxid || '')
      if (result.success) {
        this.connected = true
        this.activeVersion = '4.x'
        this.useRawSqlite4x = true
        return { success: true }
      }
    }

    // 尝试 WCDB API 连接 (需要 db_storage/session.db 结构)
    const resourcesPath = join(process.cwd(), 'resources')
    wcdbCore.setPaths(resourcesPath, '')

    const ok = await wcdbCore.open(dbPath, decryptKey, wxid || '')
    if (ok) {
      this.connected = true
      this.activeVersion = '4.x'
      this.useRawSqlite4x = false
      return { success: true }
    }
    return { success: false, error: '4.x 数据库连接失败，请确保 MSG0_decrypted.db 存在或 db_storage 目录结构正确' }
  }

  private async connect3x(): Promise<{ success: boolean; error?: string }> {
    const dbPath = configService.get('dbPath3x')
    const wxid = configService.get('wxid')
    const decryptKey = configService.get('decryptKey3x')

    if (!dbPath || !decryptKey) {
      return { success: false, error: '3.x 配置不完整，请运行 weflow-cli init' }
    }

    const ok = await sqlcipherCore.open(dbPath, decryptKey, wxid || '')
    if (ok.success) {
      this.connected = true
      this.activeVersion = '3.x'
      return { success: true }
    }
    return { success: false, error: ok.error || '3.x 数据库连接失败' }
  }

  async listSessions(keyword?: string, limit = 50): Promise<ChatSession[]> {
    const conn = await this.connect()
    if (!conn.success) return []

    let sessions: ChatSession[] = []

    if (this.activeVersion === '4.x') {
      if (this.useRawSqlite4x) {
        const result = await sqlcipherCore.getSessions()
        if (!result.success || !result.sessions) return []
        sessions = result.sessions
      } else {
        const result = await wcdbCore.getSessions()
        if (!result.success || !result.sessions) return []
        sessions = result.sessions
      }
    } else if (this.activeVersion === '3.x') {
      const result = await sqlcipherCore.getSessions()
      if (!result.success || !result.sessions) return []
      sessions = result.sessions
    }

    if (keyword) {
      const kw = keyword.toLowerCase()
      sessions = sessions.filter(s =>
        (s.username || '').toLowerCase().includes(kw) ||
        (s.displayName || '').toLowerCase().includes(kw) ||
        (s.summary || '').toLowerCase().includes(kw)
      )
    }

    return sessions.slice(0, limit)
  }

  async getMessages(
    talker: string,
    limit = 100,
    offset = 0
  ): Promise<Message[]> {
    const conn = await this.connect()
    if (!conn.success) return []

    if (this.activeVersion === '4.x') {
      if (this.useRawSqlite4x) {
        const result = await sqlcipherCore.getMessages(talker, limit, offset)
        if (!result.success || !result.messages) return []
        return result.messages
      }
      const result = await wcdbCore.getMessages(talker, limit, offset)
      if (!result.success || !result.messages) return []
      return result.messages
    } else if (this.activeVersion === '3.x') {
      const result = await sqlcipherCore.getMessages(talker, limit, offset)
      if (!result.success || !result.messages) return []
      return result.messages
    }

    return []
  }

  async listContacts(keyword?: string, limit = 200): Promise<Contact[]> {
    const conn = await this.connect()
    if (!conn.success) return []

    let contacts: Contact[] = []

    if (this.activeVersion === '4.x') {
      if (this.useRawSqlite4x) {
        const result = await sqlcipherCore.getContacts()
        if (!result.success || !result.contacts) return []
        contacts = result.contacts
      } else {
        const result = await wcdbCore.getContactsCompact()
        if (!result.success || !result.contacts) return []
        contacts = result.contacts
      }
    } else if (this.activeVersion === '3.x') {
      const result = await sqlcipherCore.getContacts()
      if (!result.success || !result.contacts) return []
      contacts = result.contacts
    }

    if (keyword) {
      const kw = keyword.toLowerCase()
      contacts = contacts.filter(c =>
        (c.username || '').toLowerCase().includes(kw) ||
        (c.displayName || '').toLowerCase().includes(kw) ||
        (c.remark || '').toLowerCase().includes(kw) ||
        (c.nickname || '').toLowerCase().includes(kw)
      )
    }

    return contacts.slice(0, limit)
  }

  disconnect(): void {
    if (this.activeVersion === '4.x') {
      if (this.useRawSqlite4x) {
        try { sqlcipherCore.close() } catch { }
      }
      try { wcdbCore.close() } catch { }
    } else if (this.activeVersion === '3.x') {
      try { sqlcipherCore.close() } catch { }
    }
    this.connected = false
    this.activeVersion = null
    this.useRawSqlite4x = false
  }
}

export const chatService = new ChatService()
