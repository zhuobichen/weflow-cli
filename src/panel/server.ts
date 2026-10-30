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
import { readFileSync, existsSync } from 'node:fs'
import { join } from 'node:path'
import { randomBytes } from 'node:crypto'
import {
  DEFAULT_PANEL_PORT, PANEL_ENDPOINT_VERSION, PANEL_SERVICE_ID, generateToken, tokenMatches,
  writeEndpoint, type PanelChannelMode, type PanelEndpoint,
} from './endpoint.js'
import type { AssistantService, TurnOutcome } from '../services/assistantService.js'
import { describeForPanel } from '../services/assistantTrace.js'
import { privacyGate } from '../services/assistantPrivacy.js'
import { configService } from '../services/configService.js'
import { assistantScenes } from '../services/assistantScenes.js'
import { resolvePackageRoot } from '../utils/packageRoot.js'

/** 8KB 够一句问题用；超了就是有人在灌东西 */
const MAX_BODY_BYTES = 8 * 1024
/** 一轮最坏分钟级（见上），给足余量；客户端那边也有自己的超时 */
const TURN_TIMEOUT_MS = 180_000
/** 浏览器那条路的会话 cookie 名。值是 token 本身，但 `HttpOnly` —— 页面脚本拿不到它 */
const COOKIE_NAME = 'weflow_panel'
/** 一次性口令的有效期。只用来"换 cookie"这一次，换个页面就该过期了 */
const PAIR_TTL_MS = 60_000
/**
 * 静态文件**白名单**。不是"把目录挂出去"：只认这三个名字，其余一律 404。
 * 挂目录就得处理路径穿越，而这里根本不需要那个能力。
 */
const STATIC_FILES: Record<string, string> = {
  '/panel': 'index.html',
  '/panel/': 'index.html',
  '/panel/index.html': 'index.html',
  '/panel/renderer.js': 'renderer.js',
  '/panel/panel.css': 'panel.css',
  // 悬浮球上那张图（项目吉祥物）。走白名单而不是挂目录，理由同上。
  '/panel/mascot.png': 'mascot.png',
  // 被捏一下时换的那张（同一只猫，眯眼笑）。**必须一起进白名单**——漏了它，
  // 球在按下的一瞬间会闪成一张空图（`PANEL_ASSET_MISSING` 只写在响应里，页面上看不到）。
  '/panel/mascot-happy.png': 'mascot-happy.png',
  // 三个真状态的脸（忙/连不上/额度用完）。与上面那张一样，**必须一起进白名单**——
  // 漏一张，那个状态一出现球就闪成空图，而响应里的 PANEL_ASSET_MISSING 页面上看不到。
  '/panel/mascot-thinking.png': 'mascot-thinking.png',
  '/panel/mascot-sorry.png': 'mascot-sorry.png',
  '/panel/mascot-tired.png': 'mascot-tired.png',
  // 眼球跟随鼠标用的两层（2026-09-30）：`mascot-base.png` 是**把虹膜挖掉**的球面、
  // `mascot-iris.png` 是可以单独平移的虹膜层（含瞳孔与高光）。零偏移叠回去与原图**逐像素相同**
  // （脚本验过），所以换上去不会看出任何差别，只是眼睛能动了。
  // 同上：**漏一张，球在那个瞬间闪成空图**，而 PANEL_ASSET_MISSING 只写在响应里、页面上看不到。
  '/panel/mascot-base.png': 'mascot-base.png',
  '/panel/mascot-iris.png': 'mascot-iris.png',
  // 半隐时那张脸（2026-09-30）：身子藏在屏幕边后面、只把脑袋探出来。
  // 它的左边**必须是一条笔直的切边**（内容贴着画布 x=0），贴上去才跟屏幕边严丝合缝——
  // 所以半隐时球的 `border-radius` 也要去掉（圆会把那条切边剪成弧）。
  '/panel/mascot-peek.png': 'mascot-peek.png',
  // 被拎起来那套动作的 12 张（2026-10-02）：6 帧拎起来 + 6 帧落回去。
  // 静止帧就是 `mascot-base.png`，不新增。同上：**漏一张，那一段动作里球会闪成空图**，
  // 而 PANEL_ASSET_MISSING 只写在响应体里、页面上看不到。
  '/panel/mascot-lift-up-1.png': 'mascot-lift-up-1.png',
  '/panel/mascot-lift-up-2.png': 'mascot-lift-up-2.png',
  '/panel/mascot-lift-up-3.png': 'mascot-lift-up-3.png',
  '/panel/mascot-lift-up-4.png': 'mascot-lift-up-4.png',
  '/panel/mascot-lift-up-5.png': 'mascot-lift-up-5.png',
  '/panel/mascot-lift-up-6.png': 'mascot-lift-up-6.png',
  '/panel/mascot-lift-down-1.png': 'mascot-lift-down-1.png',
  '/panel/mascot-lift-down-2.png': 'mascot-lift-down-2.png',
  '/panel/mascot-lift-down-3.png': 'mascot-lift-down-3.png',
  '/panel/mascot-lift-down-4.png': 'mascot-lift-down-4.png',
  '/panel/mascot-lift-down-5.png': 'mascot-lift-down-5.png',
  '/panel/mascot-lift-down-6.png': 'mascot-lift-down-6.png',
  // 眨眼那 2 帧（2026-10-04）：眼睛以外与静止帧逐像素相同，只把眼睛带贴回来。
  '/panel/mascot-blink-1.png': 'mascot-blink-1.png',
  '/panel/mascot-blink-2.png': 'mascot-blink-2.png',
  // 挠痒痒那 4 张（2026-10-02）：鼠标在球身上晃过时循环的姿势帧。同上：漏一张就闪空图。
  '/panel/mascot-tickle-1.png': 'mascot-tickle-1.png',
  '/panel/mascot-tickle-2.png': 'mascot-tickle-2.png',
  '/panel/mascot-tickle-3.png': 'mascot-tickle-3.png',
  '/panel/mascot-tickle-4.png': 'mascot-tickle-4.png',
}
const CONTENT_TYPES: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.png': 'image/png',
}

export interface PanelServerOptions {
  service: AssistantService
  memoryBucket: string
  /** 给人看的一句话：这个桶是不是与微信共用的，以及为什么。界面要显示它 */
  memoryNote?: string
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

function cookieToken(req: IncomingMessage): string | undefined {
  const raw = req.headers['cookie']
  if (typeof raw !== 'string') return undefined
  for (const part of raw.split(';')) {
    const [name, ...rest] = part.trim().split('=')
    if (name === COOKIE_NAME) return rest.join('=')
  }
  return undefined
}

/**
 * 这一份请求是拿什么来证明自己的：CLI/Python 走 `Authorization`，**浏览器走 cookie**。
 *
 * 浏览器为什么不能也用 header：页面脚本一旦拿得到 token，就等于把 token 交给了渲染进程，
 * 而渲染进程要显示的是**助手回复**——那里面有用户的聊天内容。用 `HttpOnly` cookie，
 * 脚本就永远读不到它（防的是 XSS 拿 token 去干别的事），同时页面照常能发请求。
 */
function presentedToken(req: IncomingMessage): string | undefined {
  return bearer(req) ?? cookieToken(req)
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
  const { service, memoryBucket, memoryNote = '', channel, onLog } = options
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

  /**
   * 一次性口令 → cookie。**为什么要有这一步**：浏览器那条路（Edge `--app`）得先拿到凭据，
   * 而**不能把 token 放进 URL**——命令行对同机任何进程可见（`wmic process get commandline`），
   * 而且 URL 会进浏览器历史。所以给的是一个**一次性、60 秒、用完即废**的口令。
   */
  const pairCodes = new Map<string, number>()

  function issuePairCode(): string {
    const code = randomBytes(18).toString('base64url')
    pairCodes.set(code, Date.now() + PAIR_TTL_MS)
    // 顺手清掉过期的，别让这张表无限长
    for (const [k, exp] of pairCodes) if (exp < Date.now()) pairCodes.delete(k)
    return code
  }

  function consumePairCode(code: string): boolean {
    const exp = pairCodes.get(code)
    if (!exp) return false
    pairCodes.delete(code)          // **单次使用**：即使它被日志/历史记下来，也已经废了
    return exp >= Date.now()
  }

  function serveStatic(res: ServerResponse, fileName: string): void {
    try {
      const full = join(resolvePackageRoot(import.meta.url), 'resources', 'panel', fileName)
      if (!existsSync(full)) { json(res, 503, { ok: false, code: 'PANEL_ASSET_MISSING', file: fileName }); return }
      const ext = fileName.slice(fileName.lastIndexOf('.'))
      res.writeHead(200, {
        'Content-Type': CONTENT_TYPES[ext] ?? 'application/octet-stream',
        'Cache-Control': 'no-store',
        // CSP 走响应头而不是 <meta>：头不能被页面内容绕过
        'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; "
          + "connect-src 'self'; img-src 'self' data:; form-action 'none'; base-uri 'none'",
        'X-Content-Type-Options': 'nosniff',
        'Referrer-Policy': 'no-referrer',
      })
      res.end(readFileSync(full))
    } catch {
      json(res, 500, { ok: false, code: 'INTERNAL' })
    }
  }

  async function handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const port = (server.address() as { port: number } | null)?.port ?? wantedPort
    const url = new URL(req.url ?? '/', `http://127.0.0.1:${port}`)

    if (!isLocalOrigin(req.headers.origin as string | undefined, port)) {
      json(res, 403, { ok: false, code: 'ORIGIN_DENIED' })
      return
    }

    // ---------------------------------------------------------- 页面（浏览器那条路）
    const asset = STATIC_FILES[url.pathname]
    if (asset) {
      if (req.method !== 'GET') { json(res, 405, { ok: false, code: 'METHOD_NOT_ALLOWED' }); return }
      const code = url.searchParams.get('c')
      const authed = tokenMatches(token, presentedToken(req))
      if (!authed) {
        // 没带凭据：只接受一次性口令，并把 cookie 种上（token **不进 URL**，见 issuePairCode）
        if (!code || !consumePairCode(code)) {
          json(res, 401, { ok: false, code: 'UNAUTHORIZED', hint: '用 weflow-cli panel 打开' })
          return
        }
        res.setHeader('Set-Cookie', `${COOKIE_NAME}=${token}; HttpOnly; SameSite=Strict; Path=/`)
      }
      // 只在**页面本身**加载时记一行。不记 `/api/status`：面板每 30 秒轮询一次状态，
      // 记它会把日志灌满，而它想回答的问题（"界面开着吗、按哪条路进来的"）这一行就答了。
      if (asset === 'index.html') {
        onLog?.(`[panel] 界面已加载（${code && !authed ? '一次性口令' : 'cookie'}）`)
      }
      serveStatic(res, asset)
      return
    }

    if (!tokenMatches(token, presentedToken(req))) {
      // 不回显任何东西：错的 token 与缺的 token 得到完全一样的响应
      json(res, 401, { ok: false, code: 'UNAUTHORIZED' })
      return
    }

    if (url.pathname === '/api/pair') {
      if (req.method !== 'POST') { json(res, 405, { ok: false, code: 'METHOD_NOT_ALLOWED' }); return }
      // 换口令这一步**必须带真实 token**（上面的检查已经过了），所以本机别的东西换不到
      const code = issuePairCode()
      json(res, 200, {
        ok: true, code, expiresInMs: PAIR_TTL_MS,
        url: `http://127.0.0.1:${port}/panel?c=${code}`,
      })
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
        memoryNote,
        quota: service.quotaState(),
        aiConfigured: privacyGate.isLocalInference() || !!configService.get('deepseekApiKey'),
        // 右键"快速回复"的名单（`config set quickReplyContacts "甲,乙"`）。
        // 页面本来就在轮询这个端点，所以它不必再开一个入口——**面板那条路上不加新端点**。
        // 名字与 id 都在这里：菜单显示名字，`/api/ask` 收到的是名字（解析交给助手那一侧，
        // 重名会得到"匹配多个会话"这种可读的失败，不猜）。
        quickReplies: String(configService.get('quickReplyContacts') || '')
          .split(',').map(item => item.trim()).filter(Boolean).slice(0, 20),

      })
      return
    }

    if (url.pathname === '/api/memory') {
      // **只读**，而且这份数据**不经过模型**：它是给用户在面板里审计"你记住了什么"用的，
      // 与 `buildSystemPrompt` 里注入的那份上下文是两件事（各有各的预算与脱敏）。
      // 桶由服务端决定（上面那个 `memoryBucket`），**不接受客户端指定** —— 否则这就是一个
      // 能读别的记忆桶的口子。
      if (req.method !== 'GET') { json(res, 405, { ok: false, code: 'METHOD_NOT_ALLOWED' }); return }
      json(res, 200, { ok: true, bucket: memoryBucket, ...service.memorySnapshot(memoryBucket) })
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
      let sceneId = ''
      let hasSceneId = false
      try {
        const parsed = JSON.parse(body.text) as any
        text = String(parsed?.text ?? '')
        hasSceneId = parsed?.sceneId !== undefined
        if (hasSceneId && (typeof parsed.sceneId !== 'string' || !parsed.sceneId.trim())) {
          json(res, 400, { ok: false, code: 'INVALID_SCENE' }); return
        }
        sceneId = hasSceneId ? parsed.sceneId.trim() : ''
      } catch {
        json(res, 400, { ok: false, code: 'BAD_JSON' }); return
      }
      text = text.trim()
      if (!text) { json(res, 400, { ok: false, code: 'EMPTY_TEXT' }); return }
      if (text.length > 4000) { json(res, 413, { ok: false, code: 'TEXT_TOO_LONG' }); return }
      if (hasSceneId) {
        const selected = assistantScenes.resolveExplicit(sceneId)
        if (!selected.match) {
          json(res, 400, { ok: false, code: 'INVALID_SCENE', error: selected.why })
          return
        }
      }

      inFlight = true
      try {
        const raced = await withTimeout(service.ask(memoryBucket, text, onLog, sceneId || undefined), TURN_TIMEOUT_MS)
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
          // 顺带把这一轮的**轨迹**给页面：它折叠着显示成"思考过程"（默认收起）。
          // 与微信里发「轨迹」看到的**是同一份**（都来自 `traces`，同一个 `describeForChat` 格式，
          // 同样不含 userId）。没有轨迹就给空对象——页面据此不显示那一块。
          //
          // **它出错不许把答复弄丢**：轨迹是附加物，答复才是这一轮的结果。所以这里 catch 住——
          // 顶多少一块"思考过程"，不能因为附加物把 200 变成 500（第一版没包，测试立刻抓到：
          // 桩服务没有 `recentTrace`，于是每条 ask 都成了 500）。
          let trace = { lines: [] as string[], reasoning: '' }
          try {
            trace = describeForPanel(service.recentTrace(memoryBucket))
          } catch { /* 附加物失败不影响答复 */ }
          json(res, 200, {
            ok: true, status: outcome.status, reply: outcome.text, memoryBucket, trace,
          })
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
