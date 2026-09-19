/**
 * One sync pass: pick a window, read it, dedupe, decide coverage, record state.
 *
 * Deliberately not a general job runner - it is the single caller of the sync
 * state contract for now (docs/SYNC_CONTRACT.md, D-029).
 */
import type { Message } from '../types.js'
import type { ShardReport } from '../core/ntCore.js'
import {
  DEFAULT_OVERLAP_SECONDS,
  SYNC_SOURCE,
  emptySyncState,
  makeJobId,
  syncStateStore,
  type JobRecord,
  type SyncCoverage,
  type SyncState,
  type SyncStateStore,
} from './syncState.js'

/** A window wider than this is worth warning about: the read is not bounded. */
export const WIDE_WINDOW_SECONDS = 90 * 24 * 3600

export class SyncRangeRequiredError extends Error {
  readonly code = 'SYNC_RANGE_REQUIRED'
  constructor() {
    super('首次同步需要 --since <日期> 或 --full')
    this.name = 'SyncRangeRequiredError'
  }
}

/**
 * Stable identity for one message.
 *
 * `serverId` wins when nonzero; otherwise `localId` is paired with
 * `createTime`. A bare `localId` is never an identity - it restarts in every
 * shard, which is the whole reason D-014 exists.
 *
 * `shard` is intentionally absent: messages do not carry it, adding one would
 * touch the fenced export contract, and overlap dedup does not need it (the
 * same message read twice yields the same serverId or the same pair).
 */
export function messageIdentity(source: string, talker: string, message: Message): string {
  const serverId = String(message.serverId ?? '').trim()
  if (serverId && serverId !== '0') return `${source}|${talker}|s:${serverId}`
  return `${source}|${talker}|l:${message.localId}|t:${message.createTime}`
}

export interface WindowOptions {
  since?: number
  full?: boolean
  overlapSeconds?: number
  now?: number
}

export interface SyncWindow {
  from: number | null
  to: number
  overlapSeconds: number
  full: boolean
}

/**
 * Work out what to read.
 *
 * Without a prior checkpoint there is nothing to resume from, so the caller
 * must say how far back to go - guessing "everything" would silently turn a
 * routine sync into a full-history read.
 */
export function computeWindow(prior: SyncState | null, options: WindowOptions): SyncWindow {
  const overlapSeconds = options.overlapSeconds ?? prior?.checkpoint.overlapSeconds
    ?? DEFAULT_OVERLAP_SECONDS
  const to = options.now ?? Math.floor(Date.now() / 1000)

  if (options.full) {
    return { from: null, to, overlapSeconds, full: true }
  }
  if (options.since !== undefined) {
    return { from: options.since, to, overlapSeconds, full: false }
  }
  const newest = prior?.checkpoint.newestCreateTime
  if (prior && typeof newest === 'number') {
    // Back up by the overlap: a second-resolution timestamp is not a cursor,
    // so the boundary messages have to be read again and deduplicated.
    return { from: Math.max(0, newest - overlapSeconds), to, overlapSeconds, full: false }
  }
  throw new SyncRangeRequiredError()
}

/** Drop repeats the overlap window re-read. */
export function dedupeMessages(
  messages: Message[], source: string, talker: string,
): { unique: Message[]; duplicateCount: number } {
  const seen = new Set<string>()
  const unique: Message[] = []
  for (const message of messages) {
    const identity = messageIdentity(source, talker, message)
    if (seen.has(identity)) continue
    seen.add(identity)
    unique.push(message)
  }
  return { unique, duplicateCount: messages.length - unique.length }
}

/**
 * Decide how much of the read we can stand behind.
 *
 * `partial` is the only value that must not be reported as success.
 *
 * A short-page truncation cannot occur here because this pass reads in one
 * call rather than paging; `collectMessagesInRangeDetailed` detects that case
 * for the paged readers and is where a future revision of this should look.
 */
export function deriveCoverage(input: {
  shards?: ShardReport
  mayHaveMore: boolean
}): SyncCoverage {
  if (input.mayHaveMore) return 'partial'
  // A shard that faulted *after* opening means its rows are only partly read.
  const readFailed = input.shards?.items.some(item => item.reason === 'READ_FAILED')
  if (readFailed) return 'partial'
  if (!input.shards) return 'unverified'
  // A shard that never opened is not knowable either way; say so rather than
  // claiming completeness or declaring failure.
  if (input.shards.failed > 0) return 'unverified'
  return 'complete'
}

export function buildNextState(input: {
  prior: SyncState | null
  talker: string
  scope: string
  source: string
  window: SyncWindow
  recordsRead: number
  recordsDeduplicated: number
  recordsReturned: number
  newestCreateTime: number | null
  oldestCreateTime: number | null
  shards?: ShardReport
  coverage: SyncCoverage
  mayHaveMore: boolean
  warnings: string[]
  now?: number
}): SyncState {
  const state = input.prior ?? emptySyncState(input.talker, input.scope, input.source,
    input.window.overlapSeconds)
  // Same form as coveredFrom/coveredTo: an offset, not a bare Z. A file that
  // mixes the two makes "which local day was this?" unanswerable.
  const stamp = isoFromSeconds(input.now ?? Math.floor(Date.now() / 1000))

  const priorFrom = state.coveredFrom
  let coveredFrom: string | null = priorFrom
  if (input.window.full) {
    // The database's true minimum is not knowable without another query, so
    // this is the oldest row we actually saw and says so in `warnings`.
    coveredFrom = input.oldestCreateTime ? isoFromSeconds(input.oldestCreateTime) : priorFrom
  } else if (input.window.from !== null) {
    const candidate = isoFromSeconds(input.window.from)
    coveredFrom = priorFrom && priorFrom < candidate ? priorFrom : candidate
  }

  return {
    ...state,
    scope: input.scope,
    talker: input.talker,
    source: input.source,
    coveredFrom,
    coveredTo: isoFromSeconds(input.window.to),
    checkpoint: {
      newestCreateTime: input.newestCreateTime ?? state.checkpoint.newestCreateTime,
      overlapSeconds: input.window.overlapSeconds,
    },
    recordsRead: input.recordsRead,
    recordsDeduplicated: input.recordsDeduplicated,
    recordsReturned: input.recordsReturned,
    shardsRead: input.shards?.opened ?? 0,
    shardsFailed: input.shards?.failed ?? 0,
    shards: input.shards
      ? { scanned: input.shards.scanned, opened: input.shards.opened,
          failed: input.shards.failed, items: input.shards.items }
      : { scanned: 0, opened: 0, failed: 0, items: [] },
    coverage: input.coverage,
    mayHaveMore: input.mayHaveMore,
    warnings: input.warnings,
    // The attempt always advances; the success does not, so a partial run is
    // never mistaken for a good one later.
    lastAttempt: stamp,
    lastSuccessfulRun: input.coverage === 'partial' ? state.lastSuccessfulRun : stamp,
    updatedAt: stamp,
  }
}

function isoFromSeconds(seconds: number): string {
  const date = new Date(seconds * 1000)
  const offsetMinutes = -date.getTimezoneOffset()
  const sign = offsetMinutes >= 0 ? '+' : '-'
  const pad = (value: number) => String(Math.floor(Math.abs(value))).padStart(2, '0')
  const local = new Date(date.getTime() + offsetMinutes * 60_000)
  const body = local.toISOString().slice(0, 19)
  return `${body}${sign}${pad(offsetMinutes / 60)}:${pad(offsetMinutes % 60)}`
}

export interface SyncRunOptions extends WindowOptions {
  source?: string
  scope?: string
  limit?: number
  /** Injected for tests; the CLI wires this to chatService. */
  read: (talker: string, limit: number) => Promise<{ messages: Message[]; shards?: ShardReport }>
  write?: boolean
  /** Defaults to the real store; tests pass one rooted in a temp directory. */
  store?: SyncStateStore
}

export interface SyncRunResult {
  success: boolean
  code?: string
  action: 'sync.messages'
  scope: string
  window: SyncWindow
  recordsRead: number
  recordsDeduplicated: number
  recordsReturned: number
  shards?: ShardReport
  coverage: SyncCoverage
  mayHaveMore: boolean
  partial: boolean
  warnings: string[]
  jobId: string | null
  state: SyncState | null
}

/**
 * Run one pass. Writes state and a job record unless `write: false`.
 *
 * A partial run still writes (the spec forbids discarding usable results) but
 * returns `success: false` with `code: 'SYNC_PARTIAL'` so nothing downstream
 * treats it as complete.
 */
export async function runSync(talker: string, options: SyncRunOptions): Promise<SyncRunResult> {
  const source = options.source ?? SYNC_SOURCE
  const scope = options.scope ?? talker
  const store = options.store ?? syncStateStore
  const prior = store.read(source, talker)
  const window = computeWindow(prior, options)

  const warnings: string[] = []
  if (window.full) warnings.push('full-scan')
  if (window.from !== null && window.to - window.from > WIDE_WINDOW_SECONDS) {
    warnings.push('wide-window')
  }

  const limit = options.limit ?? 0
  const read = await options.read(talker, limit)
  const { unique, duplicateCount } = dedupeMessages(read.messages, source, talker)

  // Without a native range pushdown the read is the whole conversation, so the
  // window is applied here. This is why the sync is correct but not cheap yet.
  const inWindow = window.from === null
    ? unique
    : unique.filter(m => Number(m.createTime) >= (window.from as number))

  const newestCreateTime = inWindow.length
    ? Math.max(...inWindow.map(m => Number(m.createTime) || 0))
    : null
  const oldestCreateTime = inWindow.length
    ? Math.min(...inWindow.map(m => Number(m.createTime) || 0))
    : null

  const mayHaveMore = limit > 0 && read.messages.length >= limit
  const coverage = deriveCoverage({ shards: read.shards, mayHaveMore })
  if (!read.shards) warnings.push('shard-report-unavailable')
  if (read.shards && read.shards.failed > 0) {
    for (const item of read.shards.items) {
      if (!item.opened) warnings.push(`shard-unverified:${item.name}`)
      else if (item.reason === 'READ_FAILED') warnings.push(`shard-read-failed:${item.name}`)
    }
  }

  const next = buildNextState({
    prior, talker, scope, source, window,
    recordsRead: read.messages.length,
    recordsDeduplicated: duplicateCount,
    recordsReturned: inWindow.length,
    newestCreateTime, oldestCreateTime,
    shards: read.shards, coverage, mayHaveMore, warnings,
    // Same clock as the window: without this the recorded attempt would come
    // from Date.now() while window.to came from the caller's `now`.
    now: options.now,
  })

  const partial = coverage === 'partial'
  let jobId: string | null = null
  if (options.write !== false) {
    jobId = makeJobId()
    const job: JobRecord = {
      schema: 'weflow-job/v1',
      jobId,
      kind: 'sync.messages',
      state: partial ? 'partial' : 'completed',
      createdAt: next.lastAttempt as string,
      updatedAt: next.lastAttempt as string,
      window: { from: window.from, to: window.to },
      progress: { completed: inWindow.length, total: read.messages.length },
      resumeable: partial,
      result: {
        records: inWindow.length,
        recordsDeduplicated: duplicateCount,
        shardsFailed: read.shards?.failed ?? 0,
        coverage,
      },
      error: partial ? 'SYNC_PARTIAL' : null,
    }
    store.write(next)
    store.writeJob(job)
  }

  return {
    success: !partial,
    code: partial ? 'SYNC_PARTIAL' : undefined,
    action: 'sync.messages',
    scope,
    window,
    recordsRead: read.messages.length,
    recordsDeduplicated: duplicateCount,
    recordsReturned: inWindow.length,
    shards: read.shards,
    coverage,
    mayHaveMore,
    partial,
    warnings,
    jobId,
    state: next,
  }
}
