/**
 * 模型接入层：配置解析 → 请求构造 → 网络（含重试）→ 用量记录。
 *
 * 从 `assistantService.ts` 抽出来的第一个模块（2026-11-24）。
 * 抽它的理由不是"文件太长"，而是：
 *
 * 1. **这一段是纯的**——只依赖 4 个状态（配额与用量），不碰守卫、记忆、隐私判定。
 *    抽出来之后改网络行为（重试、超时、usage 口径）不必在读 1900 行的文件里定位。
 * 2. **它可以被单独测**。今天补的 `fetchWithRetry`、`recordUsage` 都是靠"取私有方法"
 *    才测到的（`proto.fetchWithRetry.call(...)`），那种测法对重构极不友好。
 * 3. pi 的 `packages/ai` 就是这一层独立成包。我们不需要那么重，但**边界是该有的**。
 *
 * **刻意没抽**的：`accessDecision`（访问判定，属配额/白名单）、`dailyQuotaLeft`（配额）、
 * `privacyStateLine`（提示词）。它们各自有别的关注点，混进"接入层"会让这个模块变成杂物间。
 */
import { configService } from './configService.js'
import { privacyGate } from './assistantPrivacy.js'
import { appendLog } from './assistantDaemon.js'

/** 用量口径（字段含义见 `usageFromEnvelope`） */
export interface UsageSnapshot {
  input?: number
  cached?: number
  output?: number
  reasoning?: number
  total?: number
}

export interface EngineConfig {
  url: string
  model: string
  key: string | null
  local: boolean
}

/**
 * 按端点挑一台本机已有的 key（只给 `WEFLOW_AI_BASE_URL` 这条路用，D-171）。
 *
 * **认不出端点就返回空串**，绝不"随便拿一个 key 试试"：那把某个厂商的凭据发给了另一个主机，
 * 是本仓库最不能接受的一类事故（凭据外泄比"没配好"严重得多）。
 */
function envKeyFor(url: string): string {
  const pick = (key: 'dashscopeApiKey' | 'deepseekApiKey' | 'typesafeApiKey' | 'openrouterApiKey') =>
    String(configService.get(key) ?? '').trim()
  if (/dashscope|aliyuncs/.test(url)) return pick('dashscopeApiKey')
  if (/deepseek/.test(url)) return pick('deepseekApiKey')
  if (/302ai|typesafe/.test(url)) return pick('typesafeApiKey')
  if (/openrouter/.test(url)) return pick('openrouterApiKey')
  return ''
}

/** 解析当前引擎配置：环境变量优先，其次配置 */
export function resolveEngineConfig(): EngineConfig {
  const engine = String(configService.get('aiEngine') || 'deepseek')
  const envBase = (process.env.WEFLOW_AI_BASE_URL ?? '').trim().replace(/\/+$/, '')
  if (envBase) {
    const model = (process.env.WEFLOW_AI_MODEL ?? '').trim() || 'deepseek-chat'
    const isLocal = /127\.0\.0\.1|localhost/.test(envBase)
    return {
      url: envBase,
      model,
      key: (process.env.WEFLOW_AI_KEY ?? '').trim() || (isLocal ? 'local' : envKeyFor(envBase)),
      local: isLocal,
    }
  }
  if (engine === 'ollama' || engine === 'lmstudio') {
    const port = engine === 'lmstudio' ? 1234 : 11434
    const model = String(configService.get('localModel') || 'llama3')
    return { url: `http://localhost:${port}/v1`, model, key: null, local: true }
  }
  const baseUrl = String(configService.get('aiBaseUrl') || '').trim().replace(/\/+$/, '')
  if (baseUrl) {
    const model = String(configService.get('aiModel') || 'deepseek-chat')
    return { url: baseUrl, model, key: configService.get('deepseekApiKey'), local: false }
  }
  return {
    url: 'https://api.deepseek.com/v1', model: 'deepseek-chat',
    key: configService.get('deepseekApiKey'), local: false,
  }
}

/**
 * 从 provider 的 `usage` 里读出用量（2026-11-24）。
 *
 * **字段口径**（与 pi 一致，容易搞错）：
 * - `prompt_tokens` **不含**缓存命中部分；`cached_tokens` 单列
 * - `completion_tokens` **包含**推理 token；推理另有一份 `reasoning_tokens`
 * 所以"总消耗"不能简单相加，要看各家口径——这里只如实记录，不做跨厂商换算。
 */
export function usageFromEnvelope(envelope: any): UsageSnapshot | null {
  const u = envelope?.usage
  if (!u || typeof u !== 'object') return null
  const pick = (...keys: string[]): number | undefined => {
    for (const k of keys) {
      const v = Number(u[k])
      if (Number.isFinite(v)) return v
    }
    return undefined
  }
  const input = pick('prompt_tokens', 'input_tokens')
  // 缓存命中字段各家位置不同：OpenAI 在 prompt_tokens_details，DeepSeek/Kimi 在顶层。
  // 三处都吞，缺就是 undefined——**不要因此报错**。
  const cached = pick('cached_tokens', 'prompt_cache_hit_tokens', 'cache_read_input_tokens')
    ?? Number(u?.prompt_tokens_details?.cached_tokens)
  const output = pick('completion_tokens', 'output_tokens')
  const reasoning = pick('reasoning_tokens') ?? Number(u?.completion_tokens_details?.reasoning_tokens)
  const total = pick('total_tokens')
  return {
    input: Number.isFinite(input as number) ? input : undefined,
    cached: Number.isFinite(cached as number) ? cached : undefined,
    output: Number.isFinite(output as number) ? output : undefined,
    reasoning: Number.isFinite(reasoning as number) ? reasoning : undefined,
    total: Number.isFinite(total as number) ? total : undefined,
  }
}

// ---------------------------------------------------------------- 网络

/** 这个网络错误"重试一次有可能好"吗 */
export function isRetryableFetchError(error: any): boolean {
  const name = String(error?.name ?? '')
  const message = String(error?.message ?? '')
  const cause = String(error?.cause?.code ?? error?.code ?? '')
  if (name === 'TimeoutError') return true
  if (name === 'AbortError') return false          // 用户取消，不重试
  if (/fetch failed/i.test(message)) return true
  if (/UND_ERR_SOCKET|ECONNRESET|ETIMEDOUT|ECONNREFUSED|EAI_AGAIN/.test(cause)) return true
  return false
}

/** 指数退避 + 抖动：第 1 次约 0.5s，第 2 次约 1s（±25% 抖动，避免多请求同拍重试） */
export function backoffMs(attempt: number): number {
  const base = 500 * 2 ** (attempt - 1)
  return Math.round(base * (0.75 + Math.random() * 0.5))
}

/** 造一个与 `AbortSignal` 同形的错误，让上层能按 `name === 'AbortError'` 识别取消 */
export function abortError(signal: AbortSignal): Error {
  const reason: any = (signal as any).reason
  const err = new Error(reason?.message ?? '请求已取消')
  err.name = 'AbortError'
  return err
}

/**
 * 读 `Retry-After`（秒数或 HTTP 日期），返回毫秒；读不到返回 `null`。
 *
 * 上限 60s：再长就不值得为一个聊天回复干等，让用户看到错误比干等更有用。
 */
export function retryAfterMs(headers: Headers | undefined | null): number | null {
  // 防御：夹具/桩可能没有 `headers`。缺头就当服务端没说，退回自带退避——**不能因此抛错**。
  if (!headers || typeof headers.get !== 'function') return null
  const raw = headers.get('retry-after')
  if (!raw) return null
  const seconds = Number(raw)
  if (Number.isFinite(seconds) && seconds >= 0) return Math.min(seconds * 1000, 60_000)
  const at = Date.parse(raw)
  if (Number.isFinite(at)) return Math.min(Math.max(at - Date.now(), 0), 60_000)
  return null
}

/**
 * 带重试的 `fetch`：**只重试"重试有可能好"的那几种**，其余立即抛出。
 *
 * - **重试**：连接层错误、超时、408/409/429/5xx（3 次、指数退避带抖动）
 * - **立即失败**：400/401/403/404（key 错、请求错、模型名错）
 * - **不重试 `AbortError`**：那可能是用户主动取消，重试等于无视取消
 * - **退避期间也可取消**：AGENTS.md 硬性门禁第 2 条（"每个工具都必须接 signal，
 *   不接就等于用户点停止没有落点"）
 */
export async function fetchWithRetry(url: string, init: RequestInit, attempts = 3): Promise<Response> {
  const outer = init.signal
  const local = new AbortController()
  const merged = outer ? AbortSignal.any([outer, local.signal]) : local.signal
  const initWithSignal: RequestInit = { ...init, signal: merged }

  const waitAbortable = async (ms: number): Promise<void> => {
    if (merged.aborted) throw abortError(merged)
    await new Promise<void>((resolve, reject) => {
      const t = setTimeout(() => { merged.removeEventListener('abort', onAbort); resolve() }, ms)
      const onAbort = () => { clearTimeout(t); reject(abortError(merged)) }
      merged.addEventListener('abort', onAbort, { once: true })
    })
  }

  let lastError: unknown = null
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      const res = await fetch(url, initWithSignal)
      if ((res.status === 408 || res.status === 409 || res.status === 429 || res.status >= 500)
          && attempt < attempts) {
        // **听服务端的**：`Retry-After` 是它自己说"等多久"
        const suggested = retryAfterMs(res.headers)
        const wait = suggested ?? backoffMs(attempt)
        appendLog(`[网络] HTTP ${res.status}，第 ${attempt} 次重试`
          + (suggested !== null ? `（服务端建议等 ${Math.round(wait / 1000)}s）` : ''))
        await res.text().catch(() => '')   // 把连接读干净再重试
        await waitAbortable(wait)
        continue
      }
      return res
    } catch (error: any) {
      lastError = error
      if (merged.aborted) throw abortError(merged)
      if (!isRetryableFetchError(error) || attempt >= attempts) throw error
      appendLog(`[网络] ${String(error?.name ?? 'error')}: ${String(error?.message ?? '').slice(0, 60)}，第 ${attempt} 次重试`)
      await waitAbortable(backoffMs(attempt))
    }
  }
  throw lastError ?? new Error('fetch 重试耗尽')
}

/** 用量记账入口：审计 + 返回快照（调用方自己累计） */
export function auditUsage(usage: UsageSnapshot): void {
  if (!usage.total && !usage.input) return
  privacyGate.audit('CLOUD_USAGE', usage.total ?? 0,
    `in=${usage.input ?? '?'} out=${usage.output ?? '?'}`
    + ` cached=${usage.cached ?? 0} reasoning=${usage.reasoning ?? 0}`)
}
