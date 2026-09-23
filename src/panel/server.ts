/**
 * 助手守护进程的本机入口：**只绑回环**的 HTTP 端点，给悬浮窗（和 `weflow-cli panel`）用。
 *
 * 为什么要有它：助手的配额计数器、串行队列、被那条队列保护的实例字段都是**进程内状态**，
 * 所以"两个入口"必须落在**一个宿主进程**里。悬浮窗自己 `new AssistantService()` 会变成
 * 第二个大脑：配额各算各的、两进程并发写同一个 userId 的记忆（`save()` 虽是合并写，
 * 但同一个 userId 仍是后写覆盖先写）。**面板只做客户端。**
 *
 * ## 信任边界（D-045）
 *
 * 这条端点能读到用户的聊天数据，所以照着 `docs/DECISIONS.md` 的 D-004（阅读器只绑回环）
 * 往下走，但**必须比它严**：
 *
 * - **绑 `127.0.0.1`，不可配置**。不是 LAN 服务。
 * - **每个请求都要 token**，`GET /api/status` 也不例外。阅读器（`scripts/fav_server.py`）
 *   根本没有 token，而且它的 Origin 校验**不带 Origin 就放行**——那是给"本机阅读器"的设计，
 *   不能照抄到这个能替用户读聊天记录的端点上来。
 * - **Origin 是第二道**：带了 Origin 但不在白名单 → 403，**即使 token 正确**。
 *   反过来，**不带** Origin 要放行：`file://` 加载的 Electron 渲染进程与 `curl` 都是这种形态，
 *   硬要求 Origin 会把面板自己挡在门外。
 * - **POST 必须 `Content-Type: application/json`**：挡掉"跨站简单请求"那种不发 preflight 的形态。
 * - **同时只允许一轮在飞**：单轮最坏是分钟级（6 轮工具 + 守卫一轮 + 压缩/抽取各一次 LLM），
 *   用户在转圈时连按回车会往队列里挂一串——每一个都烧配额。第 2 个直接 429，不入队。
 *
 * 与助手出站那个 `isSafeUrl()`（`assistantTools.ts`）**不是一回事**：它管的是"助手去抓网页"，
 * 回答的是另一个问题（那边**拒绝**回环地址）。别把两者"统一"起来。
 */
import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http'
import {
  DEFAULT_PANEL_PORT, PANEL_ENDPOINT_VERSION, PANEL_SERVICE_ID, generateToken, tokenMatches,
  writeEndpoint, type PanelChannelMode, type PanelEndpoint,
} from './endpoint.js'
import type { AssistantService, TurnOutcome } from '../services/assistantService.js'
import { privacyGate } from '../services/assistantPrivacy.js'
import { configService } from '../services/configService.js'

/** 8KB 够一句问题用；超了就是有人在灌东西 */
const MAX_BODY_BYTES = 8 * 1024
/** 一轮最坏分钟级（见上），给足余量；客户端那边也有自己的超时 */
const TURN_TIMEOUT_MS = 180_000

export interface PanelServerOptions {
  service: AssistantService
  memoryBucket: string
  channel: PanelChannelMode
  port?: number
  onLog?: (line: string) => void
}

export interface PanelServer {
  port: number
  endpoint: PanelEndpoint
  close: () => Promise<void>
}

function json(res: ServerResponse, status: number, payload: unknown): void {
  const body = JSON.stringify(payload)
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
  })
  res.end(body)
}

/** 只认本机来源。带 Origin 就必须对得上；不带 Origin 放行（curl / file:// 的 Electron） */
export function isLocalOrigin(origin: string | undefined, port: number): boolean {
  if (!origin) return true
  return origin === `http://127.0.0.1:${port}` || origin === `http://localhost:${port}`
}

function bearer(req: IncomingMessage): string | undefined {
  const header = req.headers['authorization']
  if (typeof header !== 'string') return undefined
  const match = /^Bearer\s+(.+)$/i.exec(header.trim())
  return match ? match[1] : undefined
}

/**
 * 读请求体，超限就**立刻**给结论，但**不掐连接**。
 *
 * 第一版在这里 `req.destroy()` 了：结果是客户端看到"连接被重置"而不是 413
 * ——`fetch` 直接抛 `fetch failed`，调用方拿不到那个状态码。所以超限之后**丢弃余下的字节**
 * 而不是断开（回环端点上的这点带宽代价，比"给不出 413"划算得多）。
 */
function readBody(req: IncomingMessage): Promise<{ ok: true; text: string } | { ok: false; code: string }> {
  return new Promise((resolve) => {
    let size = 0
    let tooLarge = false
    const chunks: Buffer[] = []
    req.on('data', (chunk: Buffer) => {
      if (tooLarge) return                    // 已经下过结论了，余下的丢掉
      size += chunk.length
      if (size > MAX_BODY_BYTES) {
        tooLarge = true
        resolve({ ok: false, code: 'PAYLOAD_TOO_LARGE' })
        return
      }
      chunks.push(chunk)
    })
    req.on('end', () => { if (!tooLarge) resolve({ ok: true, text: Buffer.concat(chunks).toString('utf8') }) })
    req.on('error', () => resolve({ ok: false, code: 'BODY_READ_FAILED' }))
  })
}

/**
 * 端口的环境变量覆盖。**只给测试用**（`WEFLOW_PANEL_PORT=0` 让内核分配一个临时端口），
 * 与 `WEFLOW_ASSISTANT_EXPORT_ROOT` 是同一个先例。
 *
 * 为什么需要它：测试里 `AssistantService.start()` 会真的把这个端点起起来，
 * 于是**每个用到 store 的测试都会去抢生产端口 8766**——真守护进程在跑时就是 EADDRINUSE，
 * 而且测试之间也会互相抢。非法值一律当没给（不猜、不静默用一个奇怪的端口）。
 */
function portFromEnv(): number | undefined {
  const raw = process.env.WEFLOW_PANEL_PORT
  if (raw === undefined || raw === '') return undefined
  const parsed = Number(raw)
  if (!Number.isInteger(parsed) || parsed < 0 || parsed > 65535) return undefined
  return parsed
}

/**
 * 起端点。返回**实际**监听的端口——调用方要把它写进端点文件，不要用请求的那个值。
 */
export async function startPanelServer(options: PanelServerOptions): Promise<PanelServer> {
  const { service, memoryBucket, channel, onLog } = options
  const wantedPort = options.port ?? portFromEnv() ?? DEFAULT_PANEL_PORT
  const token = generateToken()
  const startedAt = new Date().toISOString()
  let inFlight = false

  const server: Server = createServer((req, res) => {
    void handle(req, res).catch(() => {
      if (!res.headersSent) json(res, 500, { ok: false, code: 'INTERNAL' })
      else res.end()
    })
  })

  async function handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const port = (server.address() as { port: number } | null)?.port ?? wantedPort
    const url = new URL(req.url ?? '/', `http://127.0.0.1:${port}`)

    if (!isLocalOrigin(req.headers.origin as string | undefined, port)) {
      json(res, 403, { ok: false, code: 'ORIGIN_DENIED' })
      return
    }
    if (!tokenMatches(token, bearer(req))) {
      // 不回显任何东西：错的 token 与缺的 token 得到完全一样的响应
      json(res, 401, { ok: false, code: 'UNAUTHORIZED' })
      return
    }

    if (url.pathname === '/api/status') {
      if (req.method !== 'GET') { json(res, 405, { ok: false, code: 'METHOD_NOT_ALLOWED' }); return }
      json(res, 200, {
        ok: true,
        service: PANEL_SERVICE_ID,
        startedAt,
        channel,
        channelActive: service.isChannelActive(),
        memoryBucket,
        quota: service.quotaState(),
        aiConfigured: privacyGate.isLocalInference() || !!configService.get('deepseekApiKey'),
      })
      return
    }

    if (url.pathname === '/api/ask') {
      if (req.method !== 'POST') { json(res, 405, { ok: false, code: 'METHOD_NOT_ALLOWED' }); return }
      const contentType = String(req.headers['content-type'] ?? '')
      if (!contentType.toLowerCase().startsWith('application/json')) {
        json(res, 415, { ok: false, code: 'UNSUPPORTED_MEDIA_TYPE' })
        return
      }
      if (inFlight) {
        // 不入队：入队意味着它**一定会被执行**，也就一定会烧配额
        json(res, 429, { ok: false, code: 'BUSY', error: '上一句还在处理' })
        return
      }
      const body = await readBody(req)
      if (!body.ok) { json(res, body.code === 'PAYLOAD_TOO_LARGE' ? 413 : 400, { ok: false, code: body.code }); return }
      let text = ''
      try { text = String((JSON.parse(body.text) as any)?.text ?? '') } catch {
        json(res, 400, { ok: false, code: 'BAD_JSON' }); return
      }
      text = text.trim()
      if (!text) { json(res, 400, { ok: false, code: 'EMPTY_TEXT' }); return }
      if (text.length > 4000) { json(res, 413, { ok: false, code: 'TEXT_TOO_LONG' }); return }

      inFlight = true
      try {
        const raced = await withTimeout(service.ask(memoryBucket, text, onLog), TURN_TIMEOUT_MS)
        if (raced.kind === 'timeout') {
          // 超时**不等于**这一轮没跑完：它可能还在队列里跑着。所以这里只是不再等它，
          // 那一轮之后仍会落记忆、仍会占配额。日志里说清，别让人以为它被取消了。
          onLog?.('  → 本机入口不再等待这一轮（它可能仍在队列里跑）')
          json(res, 504, { ok: false, code: 'TIMEOUT' })
          return
        }
        if (raced.kind === 'failed') {
          onLog?.(`  → 本机入口抛错: ${raced.message}`)
          json(res, 500, { ok: false, code: 'TURN_FAILED' })
          return
        }
        const outcome = raced.value
        if (outcome.status === 'replied' || outcome.status === 'quota-exceeded') {
          json(res, 200, { ok: true, status: outcome.status, reply: outcome.text, memoryBucket })
          return
        }
        if (outcome.status === 'not-running') {
          json(res, 503, { ok: false, code: 'NOT_RUNNING' })
          return
        }
        // `denied` / `ignored` 对本机入口不该出现（它不过白名单）。真出现就是接线错了，
        // 记在本机日志里，对客户端只说"这一轮没成"，**不回显内部细节**。
        onLog?.(`  → 本机入口异常结果: ${outcome.status}`)
        json(res, 500, { ok: false, code: 'TURN_FAILED' })
      } finally {
        inFlight = false
      }
      return
    }

    json(res, 404, { ok: false, code: 'NOT_FOUND' })
  }

  await new Promise<void>((resolve, reject) => {
    server.once('error', reject)
    // **只绑回环**。这个字面量就是 D-004 那条决策的落点，不要做成可配置项。
    server.listen(wantedPort, '127.0.0.1', () => {
      server.removeListener('error', reject)
      resolve()
    })
  }).catch((error: any) => {
    if (error?.code === 'EADDRINUSE') {
      throw new Error(`本机端点端口 ${wantedPort} 已被占用（换一个：--port，或先停掉占用的程序）`)
    }
    throw error
  })

  const actualPort = (server.address() as { port: number }).port
  const endpoint: PanelEndpoint = {
    version: PANEL_ENDPOINT_VERSION,
    service: PANEL_SERVICE_ID,
    port: actualPort,
    token,
    pid: process.pid,
    startedAt,
    channel,
    memoryBucket,
  }
  writeEndpoint(endpoint)

  return {
    port: actualPort,
    endpoint,
    close: () => new Promise<void>((resolve) => {
      server.close(() => resolve())
      // 长连接挂着时 close 不会回调；本机服务没有长连接，但给个兜底
      setTimeout(resolve, 500).unref?.()
    }),
  }
}

type Raced<T> =
  | { kind: 'done'; value: T }
  | { kind: 'timeout' }
  | { kind: 'failed'; message: string }

/**
 * 等一轮，但不超过 `ms`。**超时与失败必须分开报**：把"抛错了"也说成"超时"，
 * 用户会去查网络，而真正的原因在日志里被掩掉了。
 */
function withTimeout(promise: Promise<TurnOutcome>, ms: number): Promise<Raced<TurnOutcome>> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve({ kind: 'timeout' }), ms)
    promise.then(
      (value) => { clearTimeout(timer); resolve({ kind: 'done', value }) },
      (error: any) => { clearTimeout(timer); resolve({ kind: 'failed', message: String(error?.message ?? error) }) },
    )
  })
}
