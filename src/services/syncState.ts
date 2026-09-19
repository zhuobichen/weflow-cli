/**
 * 同步检查点与任务记录的本地状态。
 *
 * 两个位置, 生命周期不同 (见 docs/SYNC_CONTRACT.md):
 *   ~/.weflow-cli/sync/<source>-<name>-<hash>.json  跨运行的检查点, 每会话一份
 *   ~/.weflow-cli/jobs/<jobId>.json                 单次运行的记录
 *
 * 按会话分文件而不是一个全局文件: 全局文件要求跨会话读改写并合并, 分文件则每次
 * 写入都是整文件原子替换 (沿用 assistantMemory.ts 的 tmp + rename 做法)。
 *
 * 两条本模块特有的规矩:
 *   - 遇到不认识的 schema 一律拒绝覆盖并报错, 不猜测字段含义;
 *   - 写盘前统一过一遍脱敏, 保证密钥/绝对路径不会进状态文件 (D-001 / D-002 / D-025)。
 */
import { createHash } from 'crypto'
import { existsSync, mkdirSync, readFileSync, readdirSync, renameSync, writeFileSync } from 'fs'
import { join } from 'path'
import os from 'os'

export const SYNC_SCHEMA = 'weflow-sync/v1'
export const JOB_SCHEMA = 'weflow-job/v1'
export const SYNC_SOURCE = 'wechat-nt'
export const DEFAULT_OVERLAP_SECONDS = 300

/** 单次运行的结局。partial 是本项目新增的一档——部分完成不算成功。 */
export type JobState = 'planned' | 'waiting_confirmation' | 'running' | 'paused'
  | 'completed' | 'partial' | 'failed' | 'cancelled'

/**
 * 覆盖可信度。这是对「有没有读全」的正面回答, 不是内部细节。
 *
 *  - complete   扫到的分片全部打开、打开的全读成功, 且没有更多
 *  - unverified 有分片打不开, 但打开的都读成功——已读部分可信, 未打开的分片内容无法判断
 *  - partial    已打开的分片读失败, 或还有更多, 或分页提前结束
 */
export type SyncCoverage = 'complete' | 'unverified' | 'partial'

export interface SyncShardItem {
  /** 仅 basename——绝不落绝对路径 */
  name: string
  opened: boolean
  hasTalkerTable: boolean | null
  rowsForTalker: number | null
  /** null | KEY_REJECTED | OPEN_FAILED | READ_FAILED */
  reason: string | null
}

export interface SyncState {
  schema: string
  schemaVersion: number
  source: string
  scope: string
  talker: string
  lastSuccessfulRun: string | null
  lastAttempt: string | null
  coveredFrom: string | null
  coveredTo: string | null
  checkpoint: { newestCreateTime: number | null; overlapSeconds: number }
  recordsRead: number
  recordsDeduplicated: number
  recordsReturned: number
  shardsRead: number
  shardsFailed: number
  shards: { scanned: number; opened: number; failed: number; items: SyncShardItem[] }
  coverage: SyncCoverage
  mayHaveMore: boolean
  warnings: string[]
  updatedAt: string | null
}

export interface JobRecord {
  schema: string
  jobId: string
  kind: string
  state: JobState
  createdAt: string
  updatedAt: string
  window: { from: number | null; to: number | null } | null
  progress: { completed: number; total: number } | null
  resumeable: boolean
  result: Record<string, unknown> | null
  error: string | null
}

/** 未知 schema 时不覆盖原文件, 交由调用方决定如何处理。 */
export class UnknownSchemaError extends Error {
  constructor(public readonly path: string, public readonly found: unknown) {
    super(`不认识的 schema: ${String(found)} (${path})`)
    this.name = 'UnknownSchemaError'
  }
}

const CONFIG_DIR = join(os.homedir(), '.weflow-cli')
const SYNC_DIR = join(CONFIG_DIR, 'sync')
const JOBS_DIR = join(CONFIG_DIR, 'jobs')

const FORBIDDEN_KEYS = /^(ntKey|ntSalt|decryptKey|favKey|favPassphrase|contactKey|snsKey|wechatOcToken|apiKey|password|auth)$/i
/** 密钥形状的十六进制串 (>=32 hex)。 */
const HEX_KEY = /\b[0-9a-f]{32,}\b/gi
/** Windows 盘符、UNC、以及 POSIX 绝对路径。三类都要, 早期只处理了第一类。 */
const ABS_PATH = /(?:[A-Za-z]:[\\/][^\s"']*|\\\\[^\s"']+|\/(?:Users|home|root|var|tmp|opt|mnt)\/[^\s"']*)/g

/**
 * 递归剔除不该落盘的字段。
 *
 * 状态文件是本机产物, 但一旦被日志、issue 或截图带出去就是泄露, 所以在写入
 * 这一层兜底, 而不是指望每个调用方都记得。
 */
export function sanitizeForState(value: unknown): unknown {
  if (typeof value === 'string') {
    return value.replace(HEX_KEY, '<redacted>').replace(ABS_PATH, '<path>')
  }
  if (Array.isArray(value)) return value.map(sanitizeForState)
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {}
    for (const [key, item] of Object.entries(value)) {
      if (FORBIDDEN_KEYS.test(key)) continue
      out[key] = sanitizeForState(item)
    }
    return out
  }
  return value
}

/**
 * 文件名里只放一个可读前缀和一个 hash。
 *
 * hash 后缀让 `wxid_a@chatroom` 与 `wxid_a_chatroom` 落到不同文件, 而显示名
 * 不会出现在目录列表里。
 */
export function talkerSlug(talker: string): string {
  const readable = talker.replace(/[^A-Za-z0-9._-]+/g, '_').slice(0, 60)
  const digest = createHash('sha256').update(talker).digest('hex').slice(0, 8)
  return `${readable}-${digest}`
}

function writeAtomic(path: string, payload: unknown): void {
  if (!existsSync(CONFIG_DIR)) mkdirSync(CONFIG_DIR, { recursive: true })
  const tmp = `${path}.tmp`
  writeFileSync(tmp, JSON.stringify(payload, null, 2), 'utf8')
  renameSync(tmp, path)
}

function readJson(path: string): unknown {
  try {
    return JSON.parse(readFileSync(path, 'utf8'))
  } catch {
    return null
  }
}

export function emptySyncState(
  talker: string,
  scope = talker,
  source = SYNC_SOURCE,
  overlapSeconds = DEFAULT_OVERLAP_SECONDS,
): SyncState {
  return {
    schema: SYNC_SCHEMA,
    schemaVersion: 1,
    source,
    scope,
    talker,
    lastSuccessfulRun: null,
    lastAttempt: null,
    coveredFrom: null,
    coveredTo: null,
    checkpoint: { newestCreateTime: null, overlapSeconds },
    recordsRead: 0,
    recordsDeduplicated: 0,
    recordsReturned: 0,
    shardsRead: 0,
    shardsFailed: 0,
    shards: { scanned: 0, opened: 0, failed: 0, items: [] },
    coverage: 'unverified',
    mayHaveMore: false,
    warnings: [],
    updatedAt: null,
  }
}

export class SyncStateStore {
  constructor(
    private readonly syncDir: string = SYNC_DIR,
    private readonly jobsDir: string = JOBS_DIR,
  ) {}

  statePath(source: string, talker: string): string {
    return join(this.syncDir, `${source}-${talkerSlug(talker)}.json`)
  }

  /** 读取检查点。无记录返回 null; schema 不认识则抛错, 不猜测。 */
  read(source: string, talker: string): SyncState | null {
    const path = this.statePath(source, talker)
    const raw = readJson(path)
    if (raw === null) return null
    const state = raw as SyncState
    if (state.schema !== SYNC_SCHEMA) throw new UnknownSchemaError(path, state.schema)
    return state
  }

  write(state: SyncState): void {
    const path = this.statePath(state.source, state.talker)
    if (!existsSync(this.syncDir)) mkdirSync(this.syncDir, { recursive: true })
    writeAtomic(path, sanitizeForState({ ...state, schema: SYNC_SCHEMA }))
  }

  /** 所有会话的检查点, 最近尝试在前。目录缺失或单个文件损坏都不影响其余。 */
  list(): SyncState[] {
    try {
      if (!existsSync(this.syncDir)) return []
      const states: SyncState[] = []
      for (const name of readdirSync(this.syncDir)) {
        if (!name.endsWith('.json')) continue
        const raw = readJson(join(this.syncDir, name))
        if (!raw || (raw as SyncState).schema !== SYNC_SCHEMA) continue
        states.push(raw as SyncState)
      }
      return states.sort((a, b) => String(b.lastAttempt ?? '').localeCompare(String(a.lastAttempt ?? '')))
    } catch {
      return []
    }
  }

  jobPath(jobId: string): string {
    return join(this.jobsDir, `${jobId}.json`)
  }

  writeJob(job: JobRecord): void {
    if (!existsSync(this.jobsDir)) mkdirSync(this.jobsDir, { recursive: true })
    writeAtomic(this.jobPath(job.jobId), sanitizeForState({ ...job, schema: JOB_SCHEMA }))
  }

  readJob(jobId: string): JobRecord | null {
    const path = this.jobPath(jobId)
    const raw = readJson(path)
    if (raw === null) return null
    const job = raw as JobRecord
    if (job.schema !== JOB_SCHEMA) throw new UnknownSchemaError(path, job.schema)
    return job
  }

  /** 最近的 job 记录, 最新在前。 */
  recentJobs(limit = 10): JobRecord[] {
    try {
      if (!existsSync(this.jobsDir)) return []
      const ids = readdirSync(this.jobsDir)
        .filter(name => name.endsWith('.json'))
        .map(name => name.slice(0, -'.json'.length))
        .sort()
        .reverse()
        .slice(0, limit)
      const jobs: JobRecord[] = []
      for (const id of ids) {
        const job = this.readJob(id)
        if (job) jobs.push(job)
      }
      return jobs
    } catch {
      return []
    }
  }
}

export const syncStateStore = new SyncStateStore()

/** jobId 形如 job_20260919_0001。同日按时间戳后四位递增, 保证可排序。 */
export function makeJobId(now = new Date()): string {
  const stamp = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, '0'),
    String(now.getDate()).padStart(2, '0'),
  ].join('')
  return `job_${stamp}_${String(now.getTime() % 10000).padStart(4, '0')}`
}
