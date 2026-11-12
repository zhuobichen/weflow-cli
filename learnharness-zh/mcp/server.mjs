#!/usr/bin/env node
// LearnHarness 中文版 · MCP stdio server（零依赖）
// 把 lh CLI 的能力暴露成 MCP tools，DSH / Claude Code / Cursor 都能直接用。
// 协议：JSON-RPC 2.0 over stdio，一行一条消息（MCP 的 stdio 传输就是按行分帧）。

import { createInterface } from "node:readline";
import { request, followTurn, messageText } from "../lib/client.mjs";
import { lessonPrompt, practicePrompt, reviewPrompt } from "../lib/zh.mjs";

const BASE = process.env.LH_BASE_URL || "http://127.0.0.1:3000";
const PROTOCOL = "2024-11-05";

const tools = [
	{
		name: "lh_health",
		description: "检查 LearnHarness 实例是否在线，并返回可用模型数量。",
		inputSchema: { type: "object", properties: {}, additionalProperties: false },
	},
	{
		name: "lh_lesson",
		description: "开一节中文课：新建会话并用中文模板请老师先做 3 个诊断问题。返回 chatId 与老师的第一段回复。",
		inputSchema: {
			type: "object",
			properties: {
				topic: { type: "string", description: "要学的主题" },
				goal: { type: "string", description: "目标（可省略）" },
				level: { type: "string", description: "当前水平（可省略）" },
				minutes: { type: "number", description: "本次可投入分钟数" },
			},
			required: ["topic"],
			additionalProperties: false,
		},
	},
	{
		name: "lh_ask",
		description: "在已有会话里发一条消息并等待这一轮结束，返回老师的回复文本。",
		inputSchema: {
			type: "object",
			properties: { chatId: { type: "string" }, text: { type: "string" } },
			required: ["chatId", "text"],
			additionalProperties: false,
		},
	},
	{
		name: "lh_drill",
		description: "在已有会话里要一道练习题（不提前给答案）。",
		inputSchema: {
			type: "object",
			properties: { chatId: { type: "string" }, weak: { type: "string", description: "易错点（可省略）" } },
			required: ["chatId"],
			additionalProperties: false,
		},
	},
	{
		name: "lh_review",
		description: "把当前这一节复盘成可直接抄进错题本的内容。",
		inputSchema: {
			type: "object",
			properties: { chatId: { type: "string" } },
			required: ["chatId"],
			additionalProperties: false,
		},
	},
	{
		name: "lh_list",
		description: "列出所有学习会话（id、标题、是否在跑）。",
		inputSchema: { type: "object", properties: {}, additionalProperties: false },
	},
	{
		name: "lh_graph",
		description: "取知识图谱（命题节点 + 推理边），用于跨会话衔接。",
		inputSchema: { type: "object", properties: {}, additionalProperties: false },
	},
];

async function createChat(title) {
	const chat = await request("/api/chats", { method: "POST", body: { title }, base: BASE });
	const id = chat?.id || chat?.chat?.id;
	if (!id) throw new Error("创建会话失败：返回里没有 id");
	return id;
}

async function send(chatId, text) {
	await request("/api/chat", { method: "POST", body: { chatId, input: { type: "text", text } }, base: BASE });
}

async function ask(chatId, text, timeoutMs = 180_000) {
	await send(chatId, text);
	const r = await followTurn(chatId, { timeoutMs, base: BASE });
	return { text: r.text, settled: r.settled, error: r.error || null };
}

const handlers = {
	async lh_health() {
		const models = await request("/api/models", { base: BASE });
		const list = Array.isArray(models) ? models : models?.models || [];
		return { ok: true, base: BASE, models: list.length };
	},
	async lh_lesson({ topic, goal, level, minutes }) {
		const chatId = await createChat(topic);
		const r = await ask(chatId, lessonPrompt({ topic, goal, level, minutes }));
		return { chatId, ...r };
	},
	async lh_ask({ chatId, text }) {
		return { chatId, ...(await ask(chatId, text)) };
	},
	async lh_drill({ chatId, weak }) {
		return { chatId, ...(await ask(chatId, practicePrompt({ weak }))) };
	},
	async lh_review({ chatId }) {
		return { chatId, ...(await ask(chatId, reviewPrompt())) };
	},
	async lh_list() {
		return await request("/api/chats", { base: BASE });
	},
	async lh_graph() {
		return await request("/api/graph", { base: BASE });
	},
};

function reply(id, result) {
	process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id, result }) + "\n");
}
function replyErr(id, code, message) {
	process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id, error: { code, message } }) + "\n");
}

async function handle(msg) {
	const { id, method, params } = msg;
	if (method === "initialize") {
		return reply(id, {
			protocolVersion: PROTOCOL,
			capabilities: { tools: {} },
			serverInfo: { name: "lh-mcp", version: "0.1.0" },
		});
	}
	if (method === "notifications/initialized" || method?.startsWith("notifications/")) return;
	if (method === "ping") return reply(id, {});
	if (method === "tools/list") return reply(id, { tools });
	if (method === "tools/call") {
		const name = params?.name;
		const fn = handlers[name];
		if (!fn) return replyErr(id, -32601, `未知工具：${name}`);
		try {
			const data = await fn(params?.arguments || {});
			return reply(id, { content: [{ type: "text", text: JSON.stringify(data, null, 2) }] });
		} catch (err) {
			return reply(id, {
				content: [{ type: "text", text: JSON.stringify({ ok: false, error: String(err?.message || err) }) }],
				isError: true,
			});
		}
	}
	return replyErr(id, -32601, `未知方法：${method}`);
}

const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on("line", (line) => {
	const t = line.trim();
	if (!t) return;
	let msg;
	try { msg = JSON.parse(t); } catch { return replyErr(null, -32700, "JSON 解析失败"); }
	handle(msg).catch((e) => replyErr(msg?.id ?? null, -32603, String(e?.message || e)));
});
