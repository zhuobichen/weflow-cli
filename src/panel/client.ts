/**
 * 端点客户端：`weflow-cli panel` 用它读状态、换一次性口令、问一句话。
 *
 * 和悬浮窗走的是**同一个端点、同一套规矩**——所以"面板与微信共用一个大脑"这件事，
 * 可以在命令行上被端到端地测（`panel ask` 就是"面板减去像素"）。
 *
 * 凭据从不进日志、不进错误信息：出错时只说状态码与分类，不回显 token。
 */
import type { PanelEndpoint } from './endpoint.js'

export type ClientResult<T> =
  | { ok: true; data: T }
  | { ok: false; code: string; error?: string }

async function request(
  endpoint: PanelEndpoint, path: string, init: RequestInit = {}, timeoutMs = 10_000,
): Promise<ClientResult<any>> {
  try {
    const res = await fetch(`http://127.0.0.1:${endpoint.port}${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${endpoint.token}`,
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
        ...(init.headers ?? {}),
      },
      signal: AbortSignal.timeout(timeoutMs),
    })
    const body: any = await res.json().catch(() => ({}))
    if (!res.ok) return { ok: false, code: String(body?.code ?? `HTTP_${res.status}`), error: body?.error }
    return { ok: true, data: body }
  } catch (error: any) {
    // 连不上：进程可能刚被停掉，或端点文件是残留的。**分开说**，别都说成"失败"
    if (error?.name === 'TimeoutError') return { ok: false, code: 'TIMEOUT' }
    return { ok: false, code: 'UNREACHABLE' }
  }
}

export function panelStatus(endpoint: PanelEndpoint): Promise<ClientResult<any>> {
  return request(endpoint, '/api/status')
}

/** 换一个一次性口令，用来拼浏览器那条路的 URL（口令不是 token，可以进命令行） */
export function panelPair(endpoint: PanelEndpoint): Promise<ClientResult<{ url: string; expiresInMs: number }>> {
  return request(endpoint, '/api/pair', { method: 'POST' })
}

export function panelAsk(endpoint: PanelEndpoint, text: string): Promise<ClientResult<any>> {
  return request(endpoint, '/api/ask', { method: 'POST', body: JSON.stringify({ text }) }, 200_000)
}
