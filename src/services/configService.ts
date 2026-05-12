import { join } from 'path'
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'fs'
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
}

const CONFIG_DIR = join(homedir(), '.weflow-cli')
const CONFIG_FILE = join(CONFIG_DIR, 'config.json')

export class ConfigService {
  private config: CliConfig = { dbPath: '', wxid: '', decryptKey: '', decryptKey3x: '', dataVersion: '', dbPath3x: '' }

  constructor() {
    this.load()
  }

  private load(): void {
    try {
      if (existsSync(CONFIG_FILE)) {
        const data = JSON.parse(readFileSync(CONFIG_FILE, 'utf8'))
        this.config = {
          dbPath: data.dbPath || '',
          wxid: data.wxid || '',
          decryptKey: data.decryptKey || '',
          decryptKey3x: data.decryptKey3x || '',
          dataVersion: data.dataVersion || '',
          dbPath3x: data.dbPath3x || ''
        }
      }
    } catch {
      // 配置文件损坏，使用默认值
    }
  }

  private save(): void {
    try {
      if (!existsSync(CONFIG_DIR)) {
        mkdirSync(CONFIG_DIR, { recursive: true })
      }
      writeFileSync(CONFIG_FILE, JSON.stringify(this.config, null, 2), 'utf8')
    } catch (e) {
      console.error('保存配置失败:', e)
    }
  }

  get<K extends keyof CliConfig>(key: K): CliConfig[K] {
    const raw = this.config[key]
    if ((key === 'decryptKey' || key === 'decryptKey3x') && typeof raw === 'string' && raw.startsWith(LOCK_PREFIX)) {
      return this.lockDecrypt(raw) as CliConfig[K]
    }
    if ((key === 'dbPath' || key === 'dbPath3x') && typeof raw === 'string') {
      return expandHomePath(raw) as CliConfig[K]
    }
    return raw
  }

  set<K extends keyof CliConfig>(key: K, value: CliConfig[K]): void {
    if ((key === 'decryptKey' || key === 'decryptKey3x') && typeof value === 'string' && value) {
      this.config[key] = this.lockEncrypt(value) as CliConfig[K]
    } else if ((key === 'dbPath' || key === 'dbPath3x') && typeof value === 'string') {
      this.config[key] = expandHomePath(value) as CliConfig[K]
    } else {
      this.config[key] = value
    }
    this.save()
  }

  getAll(): ConfigData {
    return {
      dbPath: this.get('dbPath'),
      wxid: this.get('wxid'),
      decryptKey: this.get('decryptKey'),
      decryptKey3x: this.get('decryptKey3x'),
      dataVersion: (this.config.dataVersion || '4.x') as ConfigData['dataVersion'],
      dbPath3x: this.get('dbPath3x')
    }
  }

  isConfigured(): boolean {
    const has4x = !!(this.config.dbPath && this.config.decryptKey)
    const has3x = !!(this.config.dbPath3x && this.config.decryptKey3x)
    return has4x || has3x
  }

  clear(): void {
    this.config = { dbPath: '', wxid: '', decryptKey: '', decryptKey3x: '', dataVersion: '', dbPath3x: '' }
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
