/**
 * SQLCipher Core - 3.x 数据库访问层
 *
 * WeChat 3.x 使用自定义 SQLCipher 加密格式:
 * - 页大小: 4096 字节
 * - KDF: PBKDF2-HMAC-SHA1, 64000 次迭代
 * - 加密: AES-256-CBC
 * - HMAC: HMAC-SHA1 (用于验证, 解密时不需要)
 *
 * 每页结构 (文件字节布局):
 * - 文件头 16 字节: Salt (仅首页之前)
 * - 数据区: 4048 字节加密数据
 * - 保留区 48 字节: [IV(16) | HMAC-SHA1(20) | 保留(12)]
 */
import crypto from 'crypto'
import { existsSync, readFileSync, readdirSync, unlinkSync, mkdirSync, writeFileSync } from 'fs'
import { join, basename, dirname } from 'path'
import os from 'os'
import { DatabaseSync } from 'node:sqlite'
import lz4 from 'lz4'
import type { ChatSession, Message, Contact } from '../types.js'

const PAGE_SIZE = 4096
const KEY_SIZE = 32
const DEFAULT_ITER = 64000
const RESERVED_LEN = 48 // IV(16) + HMAC(20) + 保留(12)
const SQLITE_HEADER = 'SQLite format 3\x00'

export interface SqlcipherResult {
  success: boolean
  error?: string
}

export interface SessionsResult extends SqlcipherResult {
  sessions?: ChatSession[]
}

export interface MessagesResult extends SqlcipherResult {
  messages?: Message[]
}

export interface ContactsResult extends SqlcipherResult {
  contacts?: Contact[]
}

export class SqlcipherCore {
  private db: DatabaseSync | null = null
  private tempDir: string = ''
  private tempFiles: Map<string, string> = new Map()
  private pageKey: Buffer | null = null
  private opened = false
  private wxid: string = ''
  private wxDirRoot: string = ''

  /**
   * 解密数据库到临时文件
   */
  private decryptToTemp(dbPath: string, keyHex: string): string {
    const cached = this.tempFiles.get(dbPath)
    if (cached && existsSync(cached)) return cached

    const password = Buffer.from(keyHex, 'hex')
    const blist = readFileSync(dbPath)
    const salt = blist.subarray(0, 16)

    // 派生页面加密密钥 (AES-256 key)
    this.pageKey = crypto.pbkdf2Sync(password, salt, DEFAULT_ITER, KEY_SIZE, 'sha1')

    // 创建临时目录
    if (!this.tempDir) {
      this.tempDir = join(os.tmpdir(), 'weflow_3x_decrypt')
      if (!existsSync(this.tempDir)) {
        mkdirSync(this.tempDir, { recursive: true })
      }
    }

    const tempPath = join(this.tempDir, basename(dbPath) + '.decrypted.db')
    const output: Buffer[] = []

    // 写入 SQLite 文件头 "SQLite format 3\0"
    output.push(Buffer.from(SQLITE_HEADER))

    // 处理每一页
    for (let i = 0; i < blist.length; i += PAGE_SIZE) {
      if (i === 0) {
        // 第一页: 跳过 salt (16字节), 取 blist[16:4096] = 4080 字节
        const pageData = blist.subarray(16, i + PAGE_SIZE)
        this.decryptPage(pageData, output)
      } else {
        const pageData = blist.subarray(i, Math.min(i + PAGE_SIZE, blist.length))
        this.decryptPage(pageData, output)
      }
    }

    writeFileSync(tempPath, Buffer.concat(output))
    this.tempFiles.set(dbPath, tempPath)
    return tempPath
  }

  /**
   * 解密单个页面
   * pageData: 第一页 4080 字节, 其他页 4096 字节
   * 结构: [加密数据] + [IV(16) | HMAC(20) | 保留(12)]
   */
  private decryptPage(pageData: Buffer, output: Buffer[]): void {
    if (pageData.length === 0) return

    if (pageData.length <= RESERVED_LEN) {
      output.push(pageData)
      return
    }

    const encryptedLen = pageData.length - RESERVED_LEN
    const encryptedData = pageData.subarray(0, encryptedLen)
    const reserved = pageData.subarray(encryptedLen)
    const iv = reserved.subarray(0, 16)

    try {
      const decipher = crypto.createDecipheriv('aes-256-cbc', this.pageKey!, iv)
      decipher.setAutoPadding(false)
      const decrypted = Buffer.concat([decipher.update(encryptedData), decipher.final()])
      output.push(decrypted)
      output.push(reserved)
    } catch {
      // 解密失败时保持原始数据
      output.push(pageData)
    }
  }

  /**
   * 打开 3.x 数据库
   */
  async open(dbPath: string, keyHex: string, wxid: string): Promise<SqlcipherResult> {
    if (this.opened) this.close()

    if (!existsSync(dbPath)) {
      return { success: false, error: `数据库不存在: ${dbPath}` }
    }

    if (!keyHex || keyHex.length !== 64) {
      return { success: false, error: '3.x 密钥格式错误，需要 64 位十六进制字符串' }
    }

    this.wxid = wxid || ''
    // 从 dbPath 推导 WeChat Files 根目录
    // dbPath: {wxDirRoot}/{wxid}/Msg/Multi/MSG0.db
    // wxDirRoot = dirname(dirname(dirname(dbPath)))
    try {
      this.wxDirRoot = dirname(dirname(dirname(dirname(dbPath))))
    } catch {
      this.wxDirRoot = ''
    }

    try {
      const tempPath = this.decryptToTemp(dbPath, keyHex)
      this.db = new DatabaseSync(tempPath)
      this.opened = true
      return { success: true }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      return { success: false, error: `打开 3.x 数据库失败: ${msg}` }
    }
  }

  /**
   * 获取会话列表 (从 MSG 表中聚合 StrTalker)
   */
  async getSessions(): Promise<SessionsResult> {
    if (!this.db) return { success: false, error: '数据库未打开' }

    try {
      const stmt = this.db.prepare(`
        SELECT
          m.StrTalker as username,
          MAX(m.CreateTime) as lastTimestamp,
          (SELECT m2.StrContent FROM MSG m2
           WHERE m2.StrTalker = m.StrTalker
           ORDER BY m2.CreateTime DESC LIMIT 1) as summary
        FROM MSG m
        WHERE m.StrTalker != ''
        GROUP BY m.StrTalker
        ORDER BY lastTimestamp DESC
        LIMIT 500
      `)
      const rows = stmt.all() as any[]

      const sessions: ChatSession[] = rows.map((row: any) => ({
        username: row.username || '',
        type: (row.username || '').includes('@chatroom') ? 1 : 0,
        unreadCount: 0,
        summary: row.summary || '',
        sortTimestamp: row.lastTimestamp || 0,
        lastTimestamp: row.lastTimestamp || 0,
        displayName: row.username || '',
      }))

      return { success: true, sessions }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      return { success: false, error: `获取会话失败: ${msg}` }
    }
  }

  /**
   * 获取消息列表
   */
  async getMessages(
    talker: string,
    limit = 100,
    offset = 0
  ): Promise<MessagesResult> {
    if (!this.db) return { success: false, error: '数据库未打开' }

    try {
      const stmt = this.db.prepare(`
        SELECT
          localId, CAST(MsgSvrID AS TEXT) as MsgSvrID, Type, SubType, IsSender,
          CreateTime, StrTalker, StrContent, CompressContent, BytesExtra
        FROM MSG
        WHERE StrTalker = ?
        ORDER BY CreateTime DESC
        LIMIT ? OFFSET ?
      `)
      const rows = stmt.all(talker, limit, offset) as any[]

      const messages: Message[] = rows.map((row: any) =>
        this.processMessage(row)
      )

      return { success: true, messages }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      return { success: false, error: `获取消息失败: ${msg}` }
    }
  }

  /**
   * 处理单条消息，根据类型解析内容
   */
  private processMessage(row: any): Message {
    const localType = Number(row.Type) || 0
    const isSend = Number(row.IsSender) || 0
    const strContent = String(row.StrContent ?? '')
    const compressContent = row.CompressContent as any
    const bytesExtra = row.BytesExtra as any

    let parsedContent = ''
    let imageBase64: string | undefined
    let imageFileName: string | undefined
    let appTitle: string | undefined
    let appDescription: string | undefined
    let appUrl: string | undefined

    switch (localType) {
      case 1: // 文本
        parsedContent = strContent
        break

      case 3: { // 图片
        const imgInfo = this.parseImageXml(strContent)
        // 从 BytesExtra 提取图片文件名
        imageFileName = this.parseBytesExtraImageFileName(bytesExtra)
        if (imageFileName) {
          parsedContent = `[图片: ${imageFileName}]`
        } else {
          parsedContent = '[图片]'
        }
        // 尝试查找缩略图
        if (imageFileName) {
          imageBase64 = this.findThumbnail(imageFileName, row.CreateTime)
        }
        break
      }

      case 34: // 语音
        parsedContent = '[语音]'
        break

      case 43: // 视频
        parsedContent = '[视频]'
        break

      case 47: // 表情
        parsedContent = strContent ? `[表情: ${strContent}]` : '[表情]'
        break

      case 49: { // AppMsg (链接/文件/引用)
        const xml = this.decompressCompressContent(compressContent)
        if (xml) {
          const appInfo = this.parseAppMsgXml(xml)
          appTitle = appInfo.title
          appDescription = appInfo.description
          appUrl = appInfo.url
          parsedContent = this.formatAppMsg(appInfo)
        } else {
          // 部分 Type=49 消息内容在 StrContent 中
          parsedContent = strContent || '[链接/文件]'
        }
        break
      }

      case 50: // 语音通话
        parsedContent = '[语音通话]'
        break

      case 10000: // 系统消息
        parsedContent = strContent.replace(/\n/g, ' ')
        break

      default:
        parsedContent = strContent || ''
    }

    return {
      localId: Number(row.localId) || 0,
      serverId: String(row.MsgSvrID ?? ''),
      localType,
      createTime: Number(row.CreateTime) || 0,
      isSend,
      senderUsername: isSend ? '' : (row.StrTalker || ''),
      content: strContent,
      rawContent: strContent,
      parsedContent,
      imageBase64,
      imageFileName,
      appTitle,
      appDescription,
      appUrl,
    }
  }

  /**
   * 将 node:sqlite 返回的 BLOB (可能是 Uint8Array 或 Buffer) 转换为 Buffer
   */
  private toBuffer(data: any): Buffer | null {
    if (!data || data.length === 0) return null
    if (Buffer.isBuffer(data)) return data
    // Uint8Array from node:sqlite
    return Buffer.from(data.buffer, data.byteOffset, data.byteLength)
  }

  /**
   * LZ4 解压 CompressContent
   */
  private decompressCompressContent(data: any): string | null {
    const buf = this.toBuffer(data)
    if (!buf) return null

    try {
      const maxSize = buf.length * 256
      const outBuf = Buffer.alloc(maxSize)
      const actualSize = lz4.decodeBlock(buf, outBuf)
      return outBuf.subarray(0, actualSize).toString('utf8')
    } catch {
      return null
    }
  }

  /**
   * 解析 Type=3 图片 XML
   */
  private parseImageXml(xml: string): { aeskey?: string; cdnthumburl?: string } {
    try {
      const aesMatch = xml.match(/aeskey="([^"]+)"/)
      const urlMatch = xml.match(/cdnthumburl="([^"]+)"/)
      return {
        aeskey: aesMatch ? aesMatch[1] : undefined,
        cdnthumburl: urlMatch ? urlMatch[1] : undefined,
      }
    } catch {
      return {}
    }
  }

  /**
   * 从 BytesExtra (protobuf) 中提取图片文件名
   * BytesExtra 包含 <msgsource><img_file_name>uuid.png</img_file_name></msgsource> 之类的 XML
   */
  private parseBytesExtraImageFileName(data: any): string | undefined {
    const buf = this.toBuffer(data)
    if (!buf) return undefined
    try {
      const str = buf.toString('utf8')
      const match = str.match(/<img_file_name>([^<]+)<\/img_file_name>/)
      return match ? match[1] : undefined
    } catch {
      return undefined
    }
  }

  /**
   * 查找缩略图并返回 base64
   * 缩略图在 FileStorage/Cache/YYYY-MM/ 目录下，以 _t.jpg 结尾
   * 文件名基于 MD5 哈希，我们需要遍历目录查找匹配图片文件名的缩略图
   */
  private findThumbnail(imageFileName: string, createTime: number): string | undefined {
    if (!this.wxDirRoot || !this.wxid) return undefined

    try {
      // 从时间戳推断月份目录
      const date = new Date(createTime * 1000)
      const yearMonth = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`

      const cacheDir = join(this.wxDirRoot, this.wxid, 'FileStorage', 'Cache', yearMonth)
      if (!existsSync(cacheDir)) return undefined

      // 读取目录中的所有文件
      const files: string[] = readdirSync(cacheDir)

      // 图片文件名格式: {uuid}.png → 对应的缩略图可能以 _t.jpg 结尾
      // 缩略图命名是基于 MD5，而不是 UUID，所以无法直接匹配
      // 返回 undefined，让调用方只显示图片文件名
      return undefined
    } catch {
      return undefined
    }
  }

  /**
   * 解析 Type=49 AppMsg XML
   */
  private parseAppMsgXml(xml: string): { title?: string; description?: string; url?: string; type?: string } {
    try {
      const titleMatch = xml.match(/<title>([^<]*)<\/title>/)
      const desMatch = xml.match(/<des>([^<]*)<\/des>/)
      const urlMatch = xml.match(/<url>([^<]*)<\/url>/)
      const typeMatch = xml.match(/<type>(\d+)<\/type>/)

      return {
        title: titleMatch ? this.decodeXmlEntities(titleMatch[1]) : undefined,
        description: desMatch ? this.decodeXmlEntities(desMatch[1]) : undefined,
        url: urlMatch ? this.decodeXmlEntities(urlMatch[1]) : undefined,
        type: typeMatch ? typeMatch[1] : undefined,
      }
    } catch {
      return {}
    }
  }

  /**
   * 解码 XML 实体字符
   */
  private decodeXmlEntities(str: string): string {
    return str
      .replace(/&amp;/g, '&')
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&quot;/g, '"')
      .replace(/&apos;/g, "'")
  }

  /**
   * 格式化 AppMsg 为可读文本
   */
  private formatAppMsg(info: { title?: string; description?: string; url?: string; type?: string }): string {
    const parts: string[] = []

    switch (info.type) {
      case '5': // 链接分享
        if (info.title) parts.push(`[分享] ${info.title}`)
        if (info.description) parts.push(info.description)
        if (info.url) parts.push(info.url)
        break
      case '6': // 文件分享
        if (info.title) parts.push(`[文件] ${info.title}`)
        break
      case '57': // 引用回复
        if (info.title) parts.push(`[引用] ${info.title}`)
        break
      default:
        if (info.title) parts.push(`[AppMsg] ${info.title}`)
        if (info.description) parts.push(info.description)
        break
    }

    return parts.join('\n') || '[链接/文件]'
  }

  /**
   * 获取联系人列表
   */
  async getContacts(): Promise<ContactsResult> {
    if (!this.db) return { success: false, error: '数据库未打开' }

    try {
      const stmt = this.db.prepare(`
        SELECT UsrName as username
        FROM Name2ID
        ORDER BY UsrName
        LIMIT 500
      `)
      const rows = stmt.all() as any[]

      const contacts: Contact[] = rows.map((row: any) => ({
        username: row.username || '',
        displayName: row.username || '',
      }))

      return { success: true, contacts }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      return { success: false, error: `获取联系人失败: ${msg}` }
    }
  }

  /**
   * 验证密钥是否正确 (使用第一页 HMAC)
   */
  static verifyKey(keyHex: string, dbPath: string): boolean {
    try {
      const password = Buffer.from(keyHex, 'hex')
      const blist = readFileSync(dbPath)

      if (blist.length < PAGE_SIZE) return false

      const salt = blist.subarray(0, 16)
      // first = blist[16:4096] = 4080 bytes (第一页跳过salt)
      const first = blist.subarray(16, PAGE_SIZE)

      // 派生页面密钥
      const byteHmac = crypto.pbkdf2Sync(password, salt, DEFAULT_ITER, KEY_SIZE, 'sha1')

      // 派生 HMAC 密钥: mac_salt = salt ^ 0x3A
      const macSalt = Buffer.alloc(16)
      for (let i = 0; i < 16; i++) {
        macSalt[i] = salt[i] ^ 58
      }
      const macKey = crypto.pbkdf2Sync(byteHmac, macSalt, 2, KEY_SIZE, 'sha1')

      // 计算 HMAC: hmac(macKey, data[16:4064] + page_number)
      // data[16:4064] = 4048 bytes (加密数据区)
      const hmac = crypto.createHmac('sha1', macKey)
      hmac.update(blist.subarray(16, PAGE_SIZE - 32)) // bytes 16-4063 = 4048 bytes
      hmac.update(Buffer.from([1, 0, 0, 0])) // page 1, little-endian

      // first[-32:-12] = HMAC at position 4048-4067 within first = file bytes 4064-4083
      const expected = first.subarray(first.length - 32, first.length - 12)
      return hmac.digest().equals(expected)
    } catch {
      return false
    }
  }

  /**
   * 关闭数据库并清理临时文件
   */
  close(): void {
    if (this.db) {
      try { this.db.close() } catch { }
      this.db = null
    }

    for (const tempPath of this.tempFiles.values()) {
      try {
        if (existsSync(tempPath)) unlinkSync(tempPath)
      } catch { }
    }
    this.tempFiles.clear()
    this.pageKey = null
    this.opened = false
  }
}
