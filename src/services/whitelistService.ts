/**
 * 白名单/黑名单服务 — 控制消息收发权限。
 * - 白名单: 允许收发的 wxid 列表
 * - 黑名单: 绝对禁止收发的 wxid 列表 (优先级高于白名单)
 * - 审计日志: 记录每次发送尝试, 便于事后追溯
 */
import { join } from 'path'
import { homedir } from 'os'
import { appendFileSync, mkdirSync, existsSync } from 'fs'
import { configService } from './configService.js'
import { resolveTalker } from '../utils/talkerUtils.js'

export interface WhitelistEntry {
  wxid: string
  displayName: string
}

const AUDIT_DIR = join(homedir(), '.weflow-cli')
const AUDIT_FILE = join(AUDIT_DIR, 'audit-send.log')

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

  isAllowed(wxid: string): boolean {
    // 黑名单优先: 即使在白名单也拒绝
    if (this.isBlocked(wxid)) return false
    return configService.getWhitelist().includes(wxid)
  }

  /** 直接添加 wxid（不解析昵称），用于 CLI 确认后的最终写入 */
  addDirect(wxid: string): void {
    const list = configService.getWhitelist()
    if (!list.includes(wxid)) {
      list.push(wxid)
      configService.setWhitelist(list)
    }
  }

  async add(target: string): Promise<{ success: boolean; error?: string; wxid?: string }> {
    let wxid: string

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

    const list = configService.getWhitelist()
    if (list.includes(wxid)) {
      return { success: false, error: `"${wxid}" 已在白名单中` }
    }

    list.push(wxid)
    configService.setWhitelist(list)
    return { success: true, wxid }
  }

  remove(wxid: string): boolean {
    const list = configService.getWhitelist()
    const idx = list.indexOf(wxid)
    if (idx < 0) return false
    list.splice(idx, 1)
    configService.setWhitelist(list)
    return true
  }

  clear(): void {
    configService.setWhitelist([])
  }

  // ====== 黑名单 ======

  getBlacklist(): string[] {
    return configService.getBlacklist()
  }

  isBlocked(wxid: string): boolean {
    return configService.getBlacklist().includes(wxid)
  }

  /** 直接添加 wxid 到黑名单 */
  blockDirect(wxid: string): void {
    const list = configService.getBlacklist()
    if (!list.includes(wxid)) {
      list.push(wxid)
      configService.setBlacklist(list)
    }
    // 同步从白名单移除 (黑名单优先级最高)
    if (configService.getWhitelist().includes(wxid)) {
      this.remove(wxid)
    }
  }

  async block(target: string): Promise<{ success: boolean; error?: string; wxid?: string }> {
    let wxid: string
    if (target.startsWith('wxid_') || target.includes('@chatroom') || target.includes('@openim')) {
      wxid = target
    } else {
      try {
        wxid = await resolveTalker(target)
      } catch (e: any) {
        return { success: false, error: `无法解析 "${target}": ${e.message}` }
      }
    }

    const list = configService.getBlacklist()
    if (list.includes(wxid)) {
      return { success: false, error: `"${wxid}" 已在黑名单中` }
    }

    list.push(wxid)
    configService.setBlacklist(list)
    // 同步从白名单移除
    if (configService.getWhitelist().includes(wxid)) {
      this.remove(wxid)
    }
    return { success: true, wxid }
  }

  unblock(wxid: string): boolean {
    const list = configService.getBlacklist()
    const idx = list.indexOf(wxid)
    if (idx < 0) return false
    list.splice(idx, 1)
    configService.setBlacklist(list)
    return true
  }

  clearBlacklist(): void {
    configService.setBlacklist([])
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

  reload(): void {
    // configService 每次读取都从内存读取，无需主动 reload
  }
}

export const whitelistService = new WhitelistService()
