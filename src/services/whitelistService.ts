/**
 * 白名单服务 — 控制消息收发权限，仅白名单内 wxid 可收发消息。
 */
import { configService } from './configService.js'
import { resolveTalker } from '../utils/talkerUtils.js'

export interface WhitelistEntry {
  wxid: string
  displayName: string
}

export class WhitelistService {
  getList(): string[] {
    return configService.getWhitelist()
  }

  isAllowed(wxid: string): boolean {
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

  reload(): void {
    // configService 每次 getWhitelist 都从内存读取，无需主动 reload
  }
}

export const whitelistService = new WhitelistService()
