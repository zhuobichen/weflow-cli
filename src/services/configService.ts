import { join } from 'path'
import { existsSync, readFileSync, writeFileSync, mkdirSync, renameSync } from 'fs'
import { homedir, hostname, userInfo } from 'os'
import crypto from 'crypto'
import { expandHomePath } from '../utils/pathUtils.js'
import type { ConfigData } from '../types.js'

const LOCK_PREFIX = 'lock:'

interface CliConfig {
  dbPath: string
  wxid: string
  decryptKey: string
  decryptKey3x: string
  dataVersion: string
  dbPath3x: string
  ntDbPath: string
  ntKey: string
  ntSalt: string
  contactDbPath: string
  contactKey: string
  contactSalt: string
  // 微信消息通道
  wechatOcToken: string
  wechatOcAccountId: string
  wechatOcBaseUrl: string
  wechatOcSyncBuf: string
  /** 持久化的 context_token: wxid -> token (加密 JSON 字符串) */
  wechatOcContextTokens: string
  // 白名单
  whitelist: string[]
  // 黑名单 (绝对禁止收发)
  blacklist: string[]
  /** 白名单带昵称: {wxid, displayName, addedAt}[] */
  whitelistEntries: ListEntry[]
  /** 黑名单带昵称: {wxid, displayName, addedAt, reason?}[] */
  blacklistEntries: ListEntry[]
  // Vault & Pipeline
  vaultRepo: string
  aiEngine: string
  // 微信读书
  wereadApiKey: string
  // AI 引擎 API Key
  deepseekApiKey: string
  // 朋友圈 (SNS) 数据库
  snsDbPath: string
  snsKey: string
  snsSalt: string
  // 收藏 (favorite.db)
  favDbPath: string
  favKey: string
  favPassphrase: string
  // 第二大脑助手
  /** 隐私模式: open | balanced | strict，默认 strict */
  assistantPrivacy: string
  /** 本地推理引擎模型名 (ollama/lmstudio) */
  localModel: string
  /** 自定义 AI 端点 (OpenAI 兼容中转站); 设置后云端引擎走此地址 */
  aiBaseUrl: string
  /** 自定义端点使用的模型名 */
  aiModel: string
  /** 助手白名单 (@im.wechat ID, 逗号/空格分隔); 空 = 拒绝所有人 */
  assistantWhitelist: string
  dailySources: string
  /** 公众号类别映射，JSON 对象：公众号名称或 gh_ ID -> 类别 */
  dailySourceCategories: string
}

export interface ListEntry {
  wxid: string
  displayName?: string
  addedAt?: number
  reason?: string
}

/** 需要加密存储的字段 */
const ENCRYPTED_KEYS: ReadonlySet<keyof CliConfig> = new Set([
  'decryptKey', 'decryptKey3x', 'ntKey', 'contactKey',
  'wechatOcToken', 'wereadApiKey', 'deepseekApiKey', 'snsKey',
  'favKey', 'favPassphrase',
])

const CONFIG_DIR = join(homedir(), '.weflow-cli')
const CONFIG_FILE = join(CONFIG_DIR, 'config.json')

export class ConfigService {
  private config: CliConfig = { dbPath: '', wxid: '', decryptKey: '', decryptKey3x: '', dataVersion: '', dbPath3x: '', ntDbPath: '', ntKey: '', ntSalt: '', contactDbPath: '', contactKey: '', contactSalt: '', wechatOcToken: '', wechatOcAccountId: '', wechatOcBaseUrl: '', wechatOcSyncBuf: '', wechatOcContextTokens: '', whitelist: [], blacklist: [], whitelistEntries: [], blacklistEntries: [], vaultRepo: '', aiEngine: 'deepseek', wereadApiKey: '', deepseekApiKey: '', snsDbPath: '', snsKey: '', snsSalt: '', favDbPath: '', favKey: '', favPassphrase: '', assistantPrivacy: 'strict', localModel: '', aiBaseUrl: '', aiModel: '', assistantWhitelist: '', dailySources: '', dailySourceCategories: '' }

  /** 本进程修改过、待回写的字段 (多进程并发写保护) */
  private dirty = new Set<keyof CliConfig>()

  constructor() {
    this.load()
  }

  private load(): void {
    try {
      if (existsSync(CONFIG_FILE)) {
        const data = JSON.parse(readFileSync(CONFIG_FILE, 'utf8'))
        const whitelist = Array.isArray(data.whitelist) ? data.whitelist : []
        const blacklist = Array.isArray(data.blacklist) ? data.blacklist : []
        // entries 优先; 缺失时从旧 string[] 迁移
        const whitelistEntries: ListEntry[] = Array.isArray(data.whitelistEntries)
          ? data.whitelistEntries
          : whitelist.map((wxid: string) => ({ wxid }))
        const blacklistEntries: ListEntry[] = Array.isArray(data.blacklistEntries)
          ? data.blacklistEntries
          : blacklist.map((wxid: string) => ({ wxid }))
        this.config = {
          dbPath: data.dbPath || '',
          wxid: data.wxid || '',
          decryptKey: data.decryptKey || '',
          decryptKey3x: data.decryptKey3x || '',
          dataVersion: data.dataVersion || '',
          dbPath3x: data.dbPath3x || '',
          ntDbPath: data.ntDbPath || '',
          ntKey: data.ntKey || '',
          ntSalt: data.ntSalt || '',
          contactDbPath: data.contactDbPath || '',
          contactKey: data.contactKey || '',
          contactSalt: data.contactSalt || '',
          wechatOcToken: data.wechatOcToken || '',
          wechatOcAccountId: data.wechatOcAccountId || '',
          wechatOcBaseUrl: data.wechatOcBaseUrl || '',
          wechatOcSyncBuf: data.wechatOcSyncBuf || '',
          wechatOcContextTokens: data.wechatOcContextTokens || '',
          whitelist,
          blacklist,
          whitelistEntries,
          blacklistEntries,
          vaultRepo: data.vaultRepo || '',
          aiEngine: data.aiEngine || 'deepseek',
          wereadApiKey: data.wereadApiKey || '',
          deepseekApiKey: data.deepseekApiKey || '',
          snsDbPath: data.snsDbPath || '',
          snsKey: data.snsKey || '',
          snsSalt: data.snsSalt || '',
          favDbPath: data.favDbPath || '',
          favKey: data.favKey || '',
          favPassphrase: data.favPassphrase || '',
          assistantPrivacy: data.assistantPrivacy || 'strict',
          localModel: data.localModel || '',
          aiBaseUrl: data.aiBaseUrl || '',
          aiModel: data.aiModel || '',
          assistantWhitelist: data.assistantWhitelist || '',
          dailySources: data.dailySources || '',
          dailySourceCategories: data.dailySourceCategories || '',
        }
      }
    } catch (e) {
      // 配置文件损坏，输出警告并使用默认值
      console.warn(`[weflow-cli] 配置文件解析失败 (${CONFIG_FILE}): ${e instanceof Error ? e.message : e}，将使用默认配置`)
    }
  }

  private save(): void {
    try {
      if (!existsSync(CONFIG_DIR)) {
        mkdirSync(CONFIG_DIR, { recursive: true })
      }
      // 多进程安全 (守护进程与 CLI 并发): 只回写本进程修改过的字段,
      // 其余字段以磁盘最新值保留, 避免旧内存快照覆盖他人写入 (幽灵回写)。
      let merged: Record<string, unknown> = { ...this.config }
      try {
        if (existsSync(CONFIG_FILE) && this.dirty.size > 0) {
          const onDisk: Record<string, unknown> = JSON.parse(readFileSync(CONFIG_FILE, 'utf8'))
          merged = { ...onDisk, ...this.config }
          for (const key of Object.keys(onDisk)) {
            if (this.dirty.has(key as keyof CliConfig)) continue
            merged[key] = onDisk[key]
          }
        }
      } catch { /* 磁盘副本不可读时退回整份写入 */ }
      // 原子写入: 先写临时文件再改名, 防止进程中途被杀导致配置损坏
      const tmpFile = CONFIG_FILE + '.tmp'
      writeFileSync(tmpFile, JSON.stringify(merged, null, 2), 'utf8')
      renameSync(tmpFile, CONFIG_FILE)
      this.dirty.clear()
    } catch (e) {
      console.error('保存配置失败:', e)
    }
  }

  get<K extends keyof CliConfig>(key: K): CliConfig[K] {
    const raw = this.config[key]
    if (ENCRYPTED_KEYS.has(key) && typeof raw === 'string' && raw.startsWith(LOCK_PREFIX)) {
      return this.lockDecrypt(raw) as CliConfig[K]
    }
    if ((key === 'dbPath' || key === 'dbPath3x' || key === 'ntDbPath') && typeof raw === 'string') {
      return expandHomePath(raw) as CliConfig[K]
    }
    return raw
  }

  set<K extends keyof CliConfig>(key: K, value: CliConfig[K]): void {
    if (ENCRYPTED_KEYS.has(key) && typeof value === 'string' && value) {
      this.config[key] = this.lockEncrypt(value) as CliConfig[K]
    } else if ((key === 'dbPath' || key === 'dbPath3x' || key === 'ntDbPath') && typeof value === 'string') {
      this.config[key] = expandHomePath(value) as CliConfig[K]
    } else {
      this.config[key] = value
    }
    this.dirty.add(key)
    this.save()
  }

  getAll(): ConfigData {
    return {
      dbPath: this.get('dbPath'),
      wxid: this.get('wxid'),
      decryptKey: this.get('decryptKey'),
      decryptKey3x: this.get('decryptKey3x'),
      dataVersion: (this.config.dataVersion || '4.x') as ConfigData['dataVersion'],
      dbPath3x: this.get('dbPath3x'),
      ntDbPath: this.config.ntDbPath,
      ntKey: this.get('ntKey'),
      ntSalt: this.config.ntSalt,
      contactDbPath: this.config.contactDbPath,
      contactKey: this.get('contactKey'),
      contactSalt: this.config.contactSalt,
      snsDbPath: this.config.snsDbPath,
      snsKey: this.get('snsKey'),
      snsSalt: this.config.snsSalt,
    }
  }

  getWhitelist(): string[] {
    return this.config.whitelist || []
  }

  setWhitelist(list: string[]): void {
    this.config.whitelist = list
    this.dirty.add('whitelist')
    this.save()
  }

  getWhitelistEntries(): ListEntry[] {
    return this.config.whitelistEntries || []
  }

  setWhitelistEntries(entries: ListEntry[]): void {
    this.config.whitelistEntries = entries
    // 同步 string[] 视图, 供旧代码使用
    this.config.whitelist = entries.map(e => e.wxid)
    this.dirty.add('whitelistEntries')
    this.dirty.add('whitelist')
    this.save()
  }

  getBlacklist(): string[] {
    return this.config.blacklist || []
  }

  setBlacklist(list: string[]): void {
    this.config.blacklist = list
    this.dirty.add('blacklist')
    this.save()
  }

  getBlacklistEntries(): ListEntry[] {
    return this.config.blacklistEntries || []
  }

  setBlacklistEntries(entries: ListEntry[]): void {
    this.config.blacklistEntries = entries
    this.config.blacklist = entries.map(e => e.wxid)
    this.dirty.add('blacklistEntries')
    this.dirty.add('blacklist')
    this.save()
  }

  /** 读取持久化的 context_token: wxid -> token */
  getContextTokens(): Record<string, string> {
    const raw = this.config.wechatOcContextTokens || ''
    if (!raw) return {}
    const json = raw.startsWith(LOCK_PREFIX) ? this.lockDecrypt(raw) : raw
    if (!json) return {}
    try {
      const obj = JSON.parse(json)
      return (obj && typeof obj === 'object') ? obj as Record<string, string> : {}
    } catch {
      return {}
    }
  }

  /** 持久化 context_token 集合 (加密存储) */
  setContextTokens(tokens: Record<string, string>): void {
    const json = JSON.stringify(tokens || {})
    this.config.wechatOcContextTokens = json ? this.lockEncrypt(json) : ''
    this.dirty.add('wechatOcContextTokens')
    this.save()
  }

  /** 增量更新单个 wxid 的 context_token */
  upsertContextToken(wxid: string, token: string): void {
    if (!wxid || !token) return
    const tokens = this.getContextTokens()
    if (tokens[wxid] === token) return
    tokens[wxid] = token
    this.setContextTokens(tokens)
  }

  isConfigured(): boolean {
    const has4x = !!(this.config.dbPath && this.config.decryptKey)
    const has3x = !!(this.config.dbPath3x && this.config.decryptKey3x)
    const hasNt = !!(this.config.ntDbPath && this.config.ntKey && this.config.ntSalt)
    return has4x || has3x || hasNt
  }

  clear(): void {
    this.config = { dbPath: '', wxid: '', decryptKey: '', decryptKey3x: '', dataVersion: '', dbPath3x: '', ntDbPath: '', ntKey: '', ntSalt: '', contactDbPath: '', contactKey: '', contactSalt: '', wechatOcToken: '', wechatOcAccountId: '', wechatOcBaseUrl: '', wechatOcSyncBuf: '', wechatOcContextTokens: '', whitelist: [], blacklist: [], whitelistEntries: [], blacklistEntries: [], vaultRepo: '', aiEngine: 'deepseek', wereadApiKey: '', deepseekApiKey: '', snsDbPath: '', snsKey: '', snsSalt: '', favDbPath: '', favKey: '', favPassphrase: '', assistantPrivacy: 'strict', localModel: '', aiBaseUrl: '', aiModel: '', assistantWhitelist: '', dailySources: '', dailySourceCategories: '' }
    // clear 意图是全量重置: 所有字段标记为脏, 覆盖磁盘上的全部旧值
    this.dirty = new Set(Object.keys(this.config) as (keyof CliConfig)[])
    this.save()
  }

  private lockEncrypt(plaintext: string): string {
    const machineId = this.getMachineId()
    const salt = crypto.randomBytes(16)
    const iv = crypto.randomBytes(12)
    const derivedKey = crypto.pbkdf2Sync(machineId, salt, 100000, 32, 'sha256')
    const cipher = crypto.createCipheriv('aes-256-gcm', derivedKey, iv)
    const encrypted = Buffer.concat([cipher.update(plaintext, 'utf8'), cipher.final()])
    const authTag = cipher.getAuthTag()
    const combined = Buffer.concat([salt, iv, authTag, encrypted])
    return LOCK_PREFIX + combined.toString('base64')
  }

  private lockDecrypt(stored: string): string {
    if (!stored || !stored.startsWith(LOCK_PREFIX)) return stored
    try {
      const machineId = this.getMachineId()
      const combined = Buffer.from(stored.slice(LOCK_PREFIX.length), 'base64')
      const salt = combined.subarray(0, 16)
      const iv = combined.subarray(16, 28)
      const authTag = combined.subarray(28, 44)
      const ciphertext = combined.subarray(44)
      const derivedKey = crypto.pbkdf2Sync(machineId, salt, 100000, 32, 'sha256')
      const decipher = crypto.createDecipheriv('aes-256-gcm', derivedKey, iv)
      decipher.setAuthTag(authTag)
      const decrypted = Buffer.concat([decipher.update(ciphertext), decipher.final()])
      return decrypted.toString('utf8')
    } catch {
      return ''
    }
  }

  private getMachineId(): string {
    // 使用机器名 + 用户名作为机器标识，足够绑定到单台机器
    return `${hostname()}-${userInfo().username}-weflow-cli`
  }
}

export const configService = new ConfigService()
