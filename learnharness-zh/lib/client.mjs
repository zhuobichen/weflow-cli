// LearnHarness 中文版 · HTTP 客户端（零依赖，Node 20+）
// 只做三件事：带超时的 fetch、SSE 解析、把一串事件收敛成"这一轮的助手回复"。

const DEFAULT_BASE = process.env.LH_BASE_URL || "http://127.0.0.1:3000";

export class LhError extends Error {
	constructor(message, { status, body } = {}) {
		super(message);
		this.name = "LhError";
		this.status = status;
		this.body = body;
	}
}

export function baseUrl(base) {
	return (base || DEFAULT_BASE).replace(/\/+$/, "");
}

/** 带超时的 JSON 请求；非 2xx 抛 LhError（带 status 与原始 body，便于 AI 判断） */
export async function request(path, { method = "GET", body, timeoutMs = 20_000, base } = {}) {
	const url = baseUrl(base) + path;
	const ctrl = new AbortController();
	const timer = setTimeout(() => ctrl.abort(new Error(`timeout ${timeoutMs}ms`)), timeoutMs);
	try {
		const res = await fetch(url, {
			method,
			headers: body === undefined ? undefined : { "content-type": "application/json" },
			body: body === undefined ? undefined : JSON.stringify(body),
			signal: ctrl.signal,
			redirect: "manual",
		});
		const text = await res.text();
		let json;
		try { json = text ? JSON.parse(text) : null; } catch { json = null; }
		if (!res.ok) throw new LhError(`HTTP ${res.status} ${url}`, { status: res.status, body: json ?? text });
		return json;
	} catch (err) {
		if (err?.name === "AbortError" || /timeout/.test(String(err?.message))) {
			throw new LhError(`请求超时（${timeoutMs}ms）：${url}`);
		}
		if (err instanceof LhError) throw err;
		throw new LhError(`连不上 LearnHarness：${url}（${err?.message || err}）。先 ` +
			`cd learnharness && npm run dev，或确认 LH_BASE_URL 指向正在运行的实例。`);
	} finally {
		clearTimeout(timer);
	}
}

/** 逐条产出 SSE 的 data 负载（字符串）。终止于 [DONE] 或流结束。 */
export async function* sseChunks(path, { timeoutMs = 180_000, base } = {}) {
	const url = baseUrl(base) + path;
	const ctrl = new AbortController();
	const timer = setTimeout(() => ctrl.abort(new Error("timeout")), timeoutMs);
	try {
		const res = await fetch(url, { signal: ctrl.signal, headers: { accept: "text/event-stream" } });
		if (!res.ok || !res.body) throw new LhError(`stream HTTP ${res.status} ${url}`, { status: res.status });
		const reader = res.body.getReader();
		const dec = new TextDecoder();
		let buf = "";
		while (true) {
			const { value, done } = await reader.read();
			if (done) break;
			buf += dec.decode(value, { stream: true });
			let idx;
			while ((idx = buf.indexOf("\n\n")) >= 0) {
				const raw = buf.slice(0, idx);
				buf = buf.slice(idx + 2);
				const data = raw
					.split("\n")
					.filter((l) => l.startsWith("data:"))
					.map((l) => l.slice(5).trimStart())
					.join("\n");
				if (!data) continue;
				if (data === "[DONE]") return;
				yield data;
			}
		}
	} finally {
		clearTimeout(timer);
	}
}

/** 从 AgentMessage 里抠出纯文本（content 可能是字符串或 part 数组） */
export function messageText(message) {
	if (!message) return "";
	const c = message.content;
	if (typeof c === "string") return c;
	if (!Array.isArray(c)) return "";
	return c
		.filter((p) => p && (p.type === "text" || typeof p.text === "string"))
		.map((p) => p.text || "")
		.join("");
}

/**
 * 跟着一条会话的 SSE 走到本轮结束，收集助手最终文本。
 * 返回 { text, events, settled, error }。
 */
export async function followTurn(chatId, { timeoutMs = 180_000, base, onEvent } = {}) {
	let text = "";
	const events = [];
	let settled = false;
	let error = null;
	for await (const data of sseChunks(`/api/chats/${encodeURIComponent(chatId)}/stream`, { timeoutMs, base })) {
		let ev;
		try { ev = JSON.parse(data); } catch { events.push({ raw: data }); continue; }
		events.push(ev);
		onEvent?.(ev);
		if (ev.type === "message_update" && ev.message) {
			const t = messageText(ev.message);
			if (t) text = t;
		}
		if (ev.type === "error") error = ev.errorText || "unknown error";
		if (ev.type === "turn-settled") { settled = true; break; }
	}
	return { text, events, settled, error };
}
