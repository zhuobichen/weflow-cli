/**
 * Talker (wxid) resolution utilities.
 * Supports: wxid / @chatroom / @openim / nickname / remark / index
 */
import inquirer from 'inquirer'
import { chatService } from '../services/chatService.js'
import type { ChatSession } from '../types.js'

/**
 * 将用户输入解析为 talker (wxid)。
 *
 * 支持: wxid_xxx / 群聊ID / 昵称 / 备注名 / 序号 [N] / 纯数字
 *
 * @throws {Error} 当输入无法解析到任何会话时
 */
export async function resolveTalker(input: string): Promise<string> {
  // 1. 已知格式直接返回
  if (input.startsWith('wxid_') || input.includes('@chatroom') || input.includes('@openim')) {
    return input
  }

  // 2. 序号匹配: [N] 或纯数字
  const numMatch = input.match(/^\[?(\d+)\]?$/)
  if (numMatch) {
    const index = parseInt(numMatch[1]) - 1
    const sessions = await chatService.listSessions()
    if (index < 0 || index >= sessions.length) {
      throw new Error(`序号 ${input} 超出范围，共 ${sessions.length} 个会话。运行 weflow-cli sessions 查看列表`)
    }
    return sessions[index].username
  }

  // 3. 昵称/备注搜索
  const sessions = await chatService.listSessions(input, 50)

  if (sessions.length === 0) {
    throw new Error(`找不到 "${input}" 对应的会话。运行 weflow-cli sessions 查看所有会话`)
  }

  // 精确匹配
  const exact = sessions.find(s =>
    s.displayName === input || s.username === input,
  )
  if (exact) return exact.username

  // 只有一个匹配
  if (sessions.length === 1) {
    return sessions[0].username
  }

  // 多个匹配 — 交互选择
  const choices = sessions.slice(0, 20).map(s => ({
    name: `${s.displayName}  (${s.username})`,
    value: s.username,
  }))

  const { selected } = await inquirer.prompt([{
    type: 'select',
    name: 'selected',
    message: `"${input}" 匹配到多个会话，请选择:`,
    choices,
    loop: false,
  }])

  return selected
}

/**
 * 批量为 wxid 列表查找 displayName。
 * 调用 chatService.listSessions 获取带昵称的会话列表。
 */
export async function lookupDisplayNames(wxids: string[]): Promise<Map<string, string>> {
  const sessions = await chatService.listSessions()
  const nameMap = new Map<string, string>()
  const sessionMap = new Map<string, ChatSession>()
  for (const s of sessions) {
    sessionMap.set(s.username, s)
  }
  for (const wxid of wxids) {
    const s = sessionMap.get(wxid)
    nameMap.set(wxid, s?.displayName || wxid)
  }
  return nameMap
}
