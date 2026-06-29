/**
 * 白名单/黑名单服务 — 控制消息收发权限。
 * - 白名单: 允许收发的 wxid 列表
 * - 黑名单: 绝对禁止收发的 wxid 列表 (优先级高于白名单)
 * - 审计日志: 记录每次发送尝试, 便于事后追溯
 */
import { join } from 'path'
import { homedir } from 'os'
import { appendFileSync, mkdirSync, existsSync, readFileSync, statSync } from 'fs'
import { configService, type ListEntry } from './configService.js'
import { resolveTalker } from '../utils/talkerUtils.js'

const AUDIT_DIR = join(homedir(), '.weflow-cli')
const AUDIT_FILE = join(AUDIT_DIR, 'audit-send.log')

/** 单条文本消息最大字符数 (微信单条上限约 5000, 这里更保守) */
export const MAX_TEXT_LENGTH = 2000
/** 默认速率限制: 窗口内最大发送条数 */
export const DEFAULT_RATE_WINDOW_MS = 60_000
export const DEFAULT_RATE_MAX = 10

export interface SendAuditEntry {
  timestamp: number
  action: 'send'
  targetWxid: string
  targetName?: string
  kind: 'text' | 'image' | 'file'
  success: boolean
  /** 消息预览 (前 80 字符) */
  preview?: string
  /** 失败原因 */
  error?: string
}

export class WhitelistService {
  // ====== 白名单 ======

  getList(): string[] {
    return configService.getWhitelist()
  }

  /** 带昵称的白名单条目 */
  getWhitelistEntries(): ListEntry[] {
    return configService.getWhitelistEntries()
  }

  /** 查 wxid 对应的 displayName, 没有则返回 wxid */
  lookupName(wxid: string): string {
    const e = configService.getWhitelistEntries().find(x => x.wxid === wxid)
    if (e?.displayName) return e.displayName
    const b = configService.getBlacklistEntries().find(x => x.wxid === wxid)
    return b?.displayName || wxid
  }

  isAllowed(wxid: string): boolean {
    // 黑名单优先: 即使在白名单也拒绝
    if (this.isBlocked(wxid)) return false
    return configService.getWhitelist().includes(wxid)
  }

  /** 直接添加 wxid（不解析昵称），用于 CLI 确认后的最终写入 */
  addDirect(wxid: string, displayName?: string): void {
    const entries = configService.getWhitelistEntries()
    if (!entries.some(e => e.wxid === wxid)) {
      entries.push({ wxid, displayName, addedAt: Date.now() })
      configService.setWhitelistEntries(entries)
    } else if (displayName) {
      // 已存在但补全 displayName
      const idx = entries.findIndex(e => e.wxid === wxid)
      if (!entries[idx].displayName) {
        entries[idx].displayName = displayName
        configService.setWhitelistEntries(entries)
      }
    }
  }

  async add(target: string): Promise<{ success: boolean; error?: string; wxid?: string; displayName?: string }> {
    let wxid: string
    let displayName: string | undefined

    // 已知格式直接使用，否则通过 resolveTalker 解析
    if (target.startsWith('wxid_') || target.includes('@chatroom') || target.includes('@openim')) {
      wxid = target
    } else {
      try {
        wxid = await resolveTalker(target)
      } catch (e: any) {
        return { success: false, error: `无法解析 "${target}": ${e.message}` }
      }
    }

    // 黑名单中的目标禁止加入白名单
    if (this.isBlocked(wxid)) {
      return { success: false, error: `"${wxid}" 在黑名单中, 禁止加入白名单` }
    }

    const entries = configService.getWhitelistEntries()
    if (entries.some(e => e.wxid === wxid)) {
      return { success: false, error: `"${wxid}" 已在白名单中` }
    }

    entries.push({ wxid, displayName, addedAt: Date.now() })
    configService.setWhitelistEntries(entries)
    return { success: true, wxid, displayName }
  }

  remove(wxid: string): boolean {
    const entries = configService.getWhitelistEntries()
    const idx = entries.findIndex(e => e.wxid === wxid)
    if (idx < 0) return false
    entries.splice(idx, 1)
    configService.setWhitelistEntries(entries)
    return true
  }

  clear(): void {
    configService.setWhitelistEntries([])
  }

  // ====== 黑名单 ======

  getBlacklist(): string[] {
    return configService.getBlacklist()
  }

  getBlacklistEntries(): ListEntry[] {
    return configService.getBlacklistEntries()
  }

  isBlocked(wxid: string): boolean {
    return configService.getBlacklist().includes(wxid)
  }

  /** 直接添加 wxid 到黑名单 */
  blockDirect(wxid: string, displayName?: string, reason?: string): void {
    const entries = configService.getBlacklistEntries()
    if (!entries.some(e => e.wxid === wxid)) {
      entries.push({ wxid, displayName, addedAt: Date.now(), reason })
      configService.setBlacklistEntries(entries)
    }
    // 同步从白名单移除 (黑名单优先级最高)
    if (configService.getWhitelist().includes(wxid)) {
      this.remove(wxid)
    }
  }

  async block(target: string, reason?: string): Promise<{ success: boolean; error?: string; wxid?: string; displayName?: string }> {
    let wxid: string
    let displayName: string | undefined
    if (target.startsWith('wxid_') || target.includes('@chatroom') || target.includes('@openim')) {
      wxid = target
    } else {
      try {
        wxid = await resolveTalker(target)
      } catch (e: any) {
        return { success: false, error: `无法解析 "${target}": ${e.message}` }
      }
    }

    const entries = configService.getBlacklistEntries()
    if (entries.some(e => e.wxid === wxid)) {
      return { success: false, error: `"${wxid}" 已在黑名单中` }
    }

    entries.push({ wxid, displayName, addedAt: Date.now(), reason })
    configService.setBlacklistEntries(entries)
    // 同步从白名单移除
    if (configService.getWhitelist().includes(wxid)) {
      this.remove(wxid)
    }
    return { success: true, wxid, displayName }
  }

  unblock(wxid: string): boolean {
    const entries = configService.getBlacklistEntries()
    const idx = entries.findIndex(e => e.wxid === wxid)
    if (idx < 0) return false
    entries.splice(idx, 1)
    configService.setBlacklistEntries(entries)
    return true
  }

  clearBlacklist(): void {
    configService.setBlacklistEntries([])
  }

  // ====== 审计日志 ======

  /** 追加一条发送审计记录 (JSON Lines) */
  auditSend(entry: SendAuditEntry): void {
    try {
      if (!existsSync(AUDIT_DIR)) {
        mkdirSync(AUDIT_DIR, { recursive: true })
      }
      appendFileSync(AUDIT_FILE, JSON.stringify(entry) + '\n', 'utf8')
    } catch {
      // 审计失败不影响主流程
    }
  }

  /** 读取审计日志, 返回最近 limit 条 (默认全部) */
  readAudit(limit?: number, filter?: (e: SendAuditEntry) => boolean): SendAuditEntry[] {
    if (!existsSync(AUDIT_FILE)) return []
    try {
      const raw = readFileSync(AUDIT_FILE, 'utf8')
      const lines = raw.split('\n').filter(Boolean)
      let entries: SendAuditEntry[] = []
      for (const line of lines) {
        try {
          entries.push(JSON.parse(line))
        } catch {
          // 跳过损坏行
        }
      }
      if (filter) entries = entries.filter(filter)
      // 倒序 (最新在前)
      entries.reverse()
      if (limit && limit > 0) entries = entries.slice(0, limit)
      return entries
    } catch {
      return []
    }
  }

  /** 审计日志文件大小 (字节) */
  auditSize(): number {
    try {
      return existsSync(AUDIT_FILE) ? statSync(AUDIT_FILE).size : 0
    } catch {
      return 0
    }
  }

  /**
   * 速率限制检查: 指定窗口内成功发送次数是否超阈值。
   * @returns { allowed, count, windowMs, max }
   */
  checkRateLimit(windowMs: number = DEFAULT_RATE_WINDOW_MS, max: number = DEFAULT_RATE_MAX): {
    allowed: boolean
    count: number
    windowMs: number
    max: number
  } {
    const since = Date.now() - windowMs
    const recent = this.readAudit(undefined, e => e.success && e.timestamp >= since)
    return {
      allowed: recent.length < max,
      count: recent.length,
      windowMs,
      max,
    }
  }

  reload(): void {
    // configService 每次读取都从内存读取，无需主动 reload
  }
}

export const whitelistService = new WhitelistService()
