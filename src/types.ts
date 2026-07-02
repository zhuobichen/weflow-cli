export interface WxidInfo {
  wxid: string
  modifiedTime: number
  nickname?: string
  avatarUrl?: string
}

export interface ChatSession {
  username: string
  type: number
  unreadCount: number
  summary: string
  sortTimestamp: number
  lastTimestamp: number
  displayName?: string
  avatarUrl?: string
  lastMsgType?: number
}

export interface Message {
  localId: number
  serverId: string
  localType: number
  createTime: number
  isSend: number | null
  senderUsername: string | null
  content: string
  rawContent: string
  parsedContent: string
  mediaType?: string
  mediaFileName?: string
  /** Type=3 图片: 缩略图 base64 数据 */
  imageBase64?: string
  /** Type=3 图片: 图片文件名 (UUID.png) */
  imageFileName?: string
  /** Type=49 AppMsg: 标题 */
  appTitle?: string
  /** Type=49 AppMsg: 描述 */
  appDescription?: string
  /** Type=49 AppMsg: URL */
  appUrl?: string
}

export interface Contact {
  username: string
  displayName: string
  remark?: string
  nickname?: string
  alias?: string
  avatarUrl?: string
  type?: string
}

// === 微信消息通道 ===

export interface WechatOCConfig {
  baseUrl?: string
  cdnBaseUrl?: string
  botType?: string
  token?: string
  accountId?: string
  syncBuf?: string
  contextTokens?: Record<string, string>
  longPollTimeoutMs?: number
  apiTimeoutMs?: number
  typingKeepaliveIntervalS?: number
  typingTicketTtlS?: number
}

export interface WechatLoginSession {
  sessionKey: string
  qrcode: string
  qrcodeImgContent: string
  startedAt: number
  status: 'wait' | 'confirmed' | 'expired' | 'scanned'
  botToken?: string
  accountId?: string
  baseUrl?: string
  userId?: string
  error?: string
}

export interface WechatInboundMessage {
  messageId: string
  fromUserId: string
  senderNickname: string
  timestamp: number
  timestampMs: number
  components: WechatMessageComponent[]
  messageStr: string
  messageKind: 'text' | 'image' | 'voice' | 'file' | 'video' | 'unknown'
  rawMessage: Record<string, any>
  isReply: boolean
  quotedText?: string
}

export type WechatMessageComponent =
  | { type: 'plain'; text: string }
  | { type: 'image'; filePath: string }
  | { type: 'record'; filePath: string }
  | { type: 'file'; name: string; filePath: string }
  | { type: 'video'; filePath: string }

export interface ExportOptions {
  talker: string
  format: 'json' | 'html' | 'txt' | 'excel'
  output: string
  limit?: number
  start?: string
  end?: string
}

export interface DbKeyResult {
  success: boolean
  key?: string
  error?: string
  logs?: string[]
}

export type DataVersion = '3.x' | '4.x'

export interface ConfigData {
  dbPath?: string
  wxid?: string
  decryptKey?: string
  decryptKey3x?: string
  dataVersion?: DataVersion
  dbPath3x?: string
  /** NT 格式: 主消息数据库路径 (message_0.db) */
  ntDbPath?: string
  /** NT 格式: 64位十六进制密钥 */
  ntKey?: string
  /** NT 格式: 32位十六进制盐值 */
  ntSalt?: string
  /** NT 格式: 联系人数据库路径 (contact.db) */
  contactDbPath?: string
  /** NT 格式: contact.db 64位十六进制密钥 */
  contactKey?: string
  /** NT 格式: contact.db 32位十六进制盐值 */
  contactSalt?: string
  /** NT 格式: sns.db 路径 */
  snsDbPath?: string
  /** NT 格式: sns.db 64位十六进制密钥 */
  snsKey?: string
  /** NT 格式: sns.db 32位十六进制盐值 */
  snsSalt?: string
}
