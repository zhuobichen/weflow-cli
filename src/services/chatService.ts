import { join } from 'path'
import { existsSync } from 'fs'
import { WcdbCore } from '../core/wcdbCore.js'
import { SqlcipherCore } from '../core/sqlcipherCore.js'
import { NtCore } from '../core/ntCore.js'
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
  /** NT 格式数据库访问 (通过 Python sqlcipher3) */
  private ntCore: NtCore | null = null

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

    // 方案 1: 使用已解密的 MSG0_decrypted.db (纯 SQLite)
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

    // 方案 2: 尝试直接解密 MSG0.db (需要正确的密钥和盐值)
    const msg0Path = join(dbPath, wxid || '', 'Msg', 'Multi', 'MSG0.db')
    if (existsSync(msg0Path) && decryptKey) {
      const result = await sqlcipherCore.open4x(msg0Path, decryptKey, wxid || '')
      if (result.success) {
        this.connected = true
        this.activeVersion = '4.x'
        this.useRawSqlite4x = true
        return { success: true }
      }
    }

    // 方案 3: 尝试 NT 格式数据库 (xwechat_files/*/db_storage/message/message_0.db)
    const ntDbPath = configService.get('ntDbPath')
    const ntKey = configService.get('ntKey')
    const ntSalt = configService.get('ntSalt')
    if (ntDbPath && ntKey && ntSalt && existsSync(ntDbPath)) {
      this.ntCore = new NtCore(ntDbPath, ntKey, ntSalt)
      // 自动发现 contact.db 路径
      if (this.ntCore.autoDetectContactDb()) {
        const allConfig = configService.getAll()
        if (allConfig.contactKey && allConfig.contactSalt) {
          this.ntCore.contactKey = allConfig.contactKey
          this.ntCore.contactSalt = allConfig.contactSalt
        }
      }
      // 快速验证: 尝试获取会话列表
      const testResult = await this.ntCore.getSessions()
      if (testResult.success) {
        this.connected = true
        this.activeVersion = '4.x'
        return { success: true }
      }
      this.ntCore = null
    }

    // 方案 4: 尝试 WCDB API 连接 (需要 db_storage/session.db 结构，仅 3.x 兼容)
    const resourcesPath = join(process.cwd(), 'resources')
    wcdbCore.setPaths(resourcesPath, '')

    const ok = await wcdbCore.open(dbPath, decryptKey, wxid || '')
    if (ok) {
      this.connected = true
      this.activeVersion = '4.x'
      this.useRawSqlite4x = false
      return { success: true }
    }

    // 所有方案失败，给出明确的指导
    const msgDir = join(dbPath, wxid || '', 'Msg', 'Multi')
    return {
      success: false,
      error: [
        '4.x 数据库连接失败，请尝试以下方案之一：',
        '',
        '方案 A - 使用解密工具 (推荐):',
        `  python scripts/scan_decrypt_4x.py --db "${msg0Path}" --out "${rawMsg0Path}"`,
        '',
        '方案 B - NT 格式数据库 (微信 4.x 新版):',
        '  1. 确保微信正在运行',
        '  2. 运行: weflow-cli init (将自动扫描 NT 数据库)',
        '',
        '方案 C - 重新提取密钥 (需要重启微信):',
        '  1. 关闭微信 4.x (Weixin.exe)',
        '  2. 运行: weflow-cli dbkey --timeout 120000',
        '  3. 启动微信并登录，密钥将在登录时自动捕获',
        '  4. 保存密钥: weflow-cli config set decryptKey <64位密钥>',
        '',
        '方案 D - 手动指定已解密数据库路径:',
        `  将已解密的数据库放到: ${rawMsg0Path}`,
      ].join('\n'),
    }
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

    if (this.ntCore) {
      const result = await this.ntCore.getSessions(keyword)
      if (!result.success || !result.sessions) return []
      sessions = result.sessions
    } else if (this.activeVersion === '4.x') {
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

    if (this.ntCore) {
      const result = await this.ntCore.getMessages(talker, limit, offset)
      if (!result.success || !result.messages) return []
      return result.messages
    } else if (this.activeVersion === '4.x') {
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

    if (this.ntCore) {
      const result = await this.ntCore.getContacts(keyword, limit)
      if (!result.success || !result.contacts) return []
      contacts = result.contacts
    } else if (this.activeVersion === '4.x') {
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
    if (this.ntCore) {
      this.ntCore = null
    }
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
