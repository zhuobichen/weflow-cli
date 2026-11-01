/**
 * `weflow-cli import qchat` —— 读 qchat-cli 的导出文件，归一化落进本地存储（D-129）。
 *
 * 边界是**文件契约**，不是进程耦合：qchat-cli 一行不用改，我们读它 `qce export --format json`
 * 产出的那份 JSON（形状见 `parseQchatExport`）。落盘后的存储（`output/imports/qq/sessions/`）
 * 是 weflow 这边的私有形状 `weflow-import/qq-v1`，字段语义与 `weflow-message/v1` 对齐
 * （serverId/createTime/parsedContent/rawContent/13 个稳定 messageType）——一套词汇表两边用。
 *
 * QQ 的 `messageId` 是服务端全局唯一（OneBot 语义），所以去重/合并就靠它，不需要微信侧那套
 * 重叠时间窗；这一点对 D-027 也成立：qq 来源天然满足 stable-identity 判据。
 *
 * 隐私边界：导入的是用户本人授权的 QQ 数据（qchat-cli 侧靠 NapCatQQ/OneBot 授权），落盘后
 * 继承 weflow 的纪律——QQ 号与 wxid 同级敏感，不进公开输出；下游管道（搜索/知识卡）各自
 * 已有的隐私门对它照常生效。
 */
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'fs'
import { join } from 'path'
import { resolveOutputRoot } from '../utils/outputRoot.js'

/** 存储根。`WEFLOW_IMPORT_ROOT` 可整体指到别处（测试用，Python 侧读同一个变量）。 */
export function importRoot(): string {
  if (process.env.WEFLOW_IMPORT_ROOT) return process.env.WEFLOW_IMPORT_ROOT
  // 走可写输出根：全局安装时包目录属于 root，写进去就是 EACCES
  // （2026-11-01 在 Linux 上实测：`import qchat --yes` 直接崩在 storeRoot）。
  return join(resolveOutputRoot(), 'imports', 'qq')
}

function sessionsDir(): string {
  return join(importRoot(), 'sessions')
}

export const IMPORT_SCHEMA = 'weflow-import/qq-v1'

/** 与 weflow-message/v1 的 messageType 同一张表（13 个稳定名）。 */
export type MessageType =
  | 'text' | 'image' | 'voice' | 'card' | 'video' | 'emoji' | 'location'
  | 'link' | 'call' | 'system' | 'quote' | 'other'

export interface ImportedQqMessage {
  /** qchat 的 messageId——全局唯一，去重/合并就靠它 */
  serverId: string
  messageSeq?: number
  /** unix 秒（qchat 的 timestamp 字段） */
  createTime: number
  /** 仅当导入时给了 --own-id 才有；不给就是未知，消费方按"未知"处理而不是猜 */
  isSend?: boolean
  /** 发送者 QQ 号（敏感，同 wxid 级） */
  senderUsername: string
  /** 群名片优先，其次昵称，最后落回 QQ 号 */
  senderName: string
  messageType: MessageType
  /** qchat 的 content（已拍平的文本） */
  parsedContent: string
  /** qchat 的 rawMessage */
  rawContent: string
}

export interface ImportedQqSessionFile {
  schema: typeof IMPORT_SCHEMA
  session: { id: string; name: string; type: string }
  importedAt: string
  sourceFiles: string[]
  messages: ImportedQqMessage[]
}

export interface QchatFilePlan {
  file: string
  ok: boolean
  reason?: string
  sessions: Array<{ id: string; name: string; type: string; messages: number }>
}

export interface ImportPlan {
  files: QchatFilePlan[]
  sessionsFound: number
  messagesTotal: number
  storeRoot: string
}

export interface ImportOutcome {
  success: boolean
  sessions: Array<{ id: string; name: string; added: number; updated: number; unchanged: number; total: number }>
  filesOk: number
  filesSkipped: number
  storeRoot: string
}

/** OneBot 消息段 → weflow 的 13 个稳定类型。纯函数，映射表只有这一份。 */
export function qqSegmentMessageType(segments: unknown[]): MessageType {
  if (!Array.isArray(segments) || !segments.length) return 'text'
  const priority: Array<[string, MessageType]> = [
    ['record', 'voice'],
    ['video', 'video'],
    ['image', 'image'],
    ['reply', 'quote'],
    ['location', 'location'],
    ['json', 'card'],
    ['xml', 'card'],
    ['contact', 'card'],
    ['file', 'card'],
    ['face', 'emoji'],
  ]
  const kinds = new Set(segments.map(s => String((s as any)?.type || '')))
  for (const [seg, type] of priority) {
    if (kinds.has(seg)) return type
  }
  return 'text'
}

/**
 * qchat 导出 JSON 的形状识别与解析。判据是结构性的：顶层 `session` 对象 + `messages`
 * 数组、且消息带 `messageId` 与 `timestamp`——认不出来就报错并给出原因，绝不猜。
 */
export function parseQchatExport(text: string, file: string): QchatFilePlan {
  const fail = (reason: string): QchatFilePlan => ({
    file, ok: false, reason, sessions: [],
  })
  let data: any
  try {
    data = JSON.parse(text)
  } catch (e: any) {
    return fail(`不是合法 JSON（${String(e?.message || e).slice(0, 80)}）`)
  }
  const session = data?.session
  const messages = data?.messages
  if (!session || typeof session !== 'object' || !Array.isArray(messages)) {
    return fail('不是 qchat 导出形状（缺 session/messages；应为 `qce export --format json` 的产物）')
  }
  const id = String(session.id ?? '')
  if (!id) return fail('session.id 为空')
  let count = 0
  for (const m of messages) {
    if (m && (m.messageId !== undefined) && (m.timestamp !== undefined)) count += 1
  }
  if (!count && messages.length) {
    return fail(`${messages.length} 条消息里没有一条带 messageId+timestamp——大概是别的格式的 JSON`)
  }
  return {
    file, ok: true,
    sessions: [{
      id,
      name: String(session.name || id),
      type: String(session.type || 'private'),
      messages: messages.length,
    }],
  }
}

/** 一条 qchat 消息 → 存储形状。字段映射只有这一处。 */
export function normalizeQchatMessage(raw: any, ownId?: string): ImportedQqMessage | null {
  if (!raw || raw.messageId === undefined || raw.timestamp === undefined) return null
  const senderId = String(raw?.sender?.id ?? '')
  const senderName = String(raw?.sender?.card || raw?.sender?.name || senderId)
  const msg: ImportedQqMessage = {
    serverId: String(raw.messageId),
    ...(raw.message_seq !== undefined ? { messageSeq: Number(raw.message_seq) } : {}),
    createTime: Number(raw.timestamp),
    ...(ownId ? { isSend: senderId === ownId } : {}),
    senderUsername: senderId,
    senderName,
    messageType: qqSegmentMessageType(raw.message),
    parsedContent: String(raw.content ?? ''),
    rawContent: String(raw.rawMessage ?? ''),
  }
  return msg
}

function safeSessionFile(id: string): string {
  const safe = id.replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 80)
  return `${safe || 'session'}.json`
}

function readStoreSession(id: string): ImportedQqSessionFile | null {
  const path = join(sessionsDir(), safeSessionFile(id))
  if (!existsSync(path)) return null
  try {
    const data = JSON.parse(readFileSync(path, 'utf-8'))
    if (data?.schema === IMPORT_SCHEMA && Array.isArray(data?.messages)) return data
  } catch { /* 损坏的存储文件按"没有"处理，重导入会覆盖修复 */ }
  return null
}

/** 收集输入路径下的候选文件（单文件或目录里的 *.json）。 */
function candidateFiles(input: string): string[] {
  if (!existsSync(input)) return []
  // readdirSync 只对目录成立：文件会抛，正好当探测用
  try {
    const entries = readdirSync(input)
    return entries.filter(f => f.toLowerCase().endsWith('.json'))
      .map(f => join(input, f))
  } catch {
    return [input]
  }
}

export function planImportQchat(input: string): ImportPlan {
  const files = candidateFiles(input)
  const plans: QchatFilePlan[] = []
  if (!files.length) {
    return {
      files: [{ file: input, ok: false, reason: '输入不存在，或目录里没有 .json 文件', sessions: [] }],
      sessionsFound: 0, messagesTotal: 0, storeRoot: sessionsDir(),
    }
  }
  for (const file of files) {
    let plan: QchatFilePlan
    try {
      plan = parseQchatExport(readFileSync(file, 'utf-8'), file)
    } catch (e: any) {
      plan = { file, ok: false, reason: `读不了（${String(e?.message || e).slice(0, 80)}）`, sessions: [] }
    }
    plans.push(plan)
  }
  const sessionIds = new Set(plans.flatMap(p => p.ok ? p.sessions.map(s => s.id) : []))
  const messagesTotal = plans.flatMap(p => p.sessions).reduce((n, s) => n + s.messages, 0)
  return {
    files: plans,
    sessionsFound: sessionIds.size,
    messagesTotal,
    storeRoot: sessionsDir(),
  }
}

export function runImportQchat(input: string, ownId?: string): ImportOutcome {
  const plan = planImportQchat(input)
  const okFiles = plan.files.filter(f => f.ok)
  if (!okFiles.length) {
    return { success: false, sessions: [], filesOk: 0, filesSkipped: plan.files.length, storeRoot: plan.storeRoot }
  }
  mkdirSync(sessionsDir(), { recursive: true })
  const merged = new Map<string, { existing: ImportedQqSessionFile | null; byId: Map<string, ImportedQqMessage>; sourceFiles: Set<string>; meta: { id: string; name: string; type: string } }>()
  for (const f of okFiles) {
    let data: any
    try {
      data = JSON.parse(readFileSync(f.file, 'utf-8'))
    } catch {
      continue // plan 已验证过可解析；这里的失败按跳过处理（计数在 filesSkipped）
    }
    const id = String(data.session.id)
    let entry = merged.get(id)
    if (!entry) {
      entry = {
        existing: readStoreSession(id),
        byId: new Map((readStoreSession(id)?.messages ?? []).map(m => [m.serverId, m])),
        sourceFiles: new Set(readStoreSession(id)?.sourceFiles ?? []),
        meta: { id, name: String(data.session.name || id), type: String(data.session.type || 'private') },
      }
      merged.set(id, entry)
    }
    entry.sourceFiles.add(f.file)
    for (const raw of data.messages ?? []) {
      const msg = normalizeQchatMessage(raw, ownId)
      if (msg) entry.byId.set(msg.serverId, msg)
    }
  }

  const sessions = []
  for (const [id, entry] of merged) {
    const existing = entry.existing
    const before = new Map((existing?.messages ?? []).map(m => [m.serverId, m]))
    const messages = [...entry.byId.values()].sort((a, b) => a.createTime - b.createTime || a.serverId.localeCompare(b.serverId))
    const file: ImportedQqSessionFile = {
      schema: IMPORT_SCHEMA,
      session: entry.meta,
      importedAt: new Date().toISOString(),
      sourceFiles: [...entry.sourceFiles],
      messages,
    }
    writeFileSync(join(sessionsDir(), safeSessionFile(id)), JSON.stringify(file, null, 1), 'utf-8')
    let added = 0, updated = 0, unchanged = 0
    for (const m of messages) {
      const old = before.get(m.serverId)
      if (!old) added += 1
      else if (JSON.stringify(old) !== JSON.stringify(m)) updated += 1
      else unchanged += 1
    }
    sessions.push({ id: entry.meta.id, name: entry.meta.name, added, updated, unchanged, total: messages.length })
  }

  return {
    success: true,
    sessions,
    filesOk: okFiles.length,
    filesSkipped: plan.files.length - okFiles.length,
    storeRoot: plan.storeRoot,
  }
}

export interface ImportListEntry {
  id: string
  name: string
  type: string
  messages: number
  oldestCreateTime: number | null
  newestCreateTime: number | null
  importedAt: string
}

export function listImports(): ImportListEntry[] {
  const dir = sessionsDir()
  if (!existsSync(dir)) return []
  const out: ImportListEntry[] = []
  for (const f of readdirSync(dir)) {
    if (!f.endsWith('.json')) continue
    try {
      const data: any = JSON.parse(readFileSync(join(dir, f), 'utf-8'))
      if (data?.schema !== IMPORT_SCHEMA) continue
      const times = (data.messages ?? []).map((m: any) => Number(m.createTime) || 0).filter(Boolean)
      out.push({
        id: String(data.session?.id ?? ''),
        name: String(data.session?.name ?? ''),
        type: String(data.session?.type ?? ''),
        messages: (data.messages ?? []).length,
        oldestCreateTime: times.length ? Math.min(...times) : null,
        newestCreateTime: times.length ? Math.max(...times) : null,
        importedAt: String(data.importedAt ?? ''),
      })
    } catch { /* 单个损坏文件不挡住清单 */ }
  }
  return out.sort((a, b) => b.newestCreateTime! - a.newestCreateTime!)
}

/** 读回一个会话（验证导入用）。limit=0 是全部。 */
export function readImportedSession(id: string, limit = 0): ImportedQqSessionFile | null {
  const data = readStoreSession(id)
  if (!data) return null
  if (limit > 0) return { ...data, messages: data.messages.slice(-limit) }
  return data
}
