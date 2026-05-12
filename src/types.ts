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
}
