#!/usr/bin/env node
// lh —— LearnHarness 中文版命令行（零依赖，Node 20+）
// 设计目标：每个动作都能被 AI 直接调用，输出默认 JSON，--json 永远可用。

import { request, followTurn, baseUrl, LhError } from "../lib/client.mjs";
import { lessonPrompt, clarifyPrompt, practicePrompt, reviewPrompt } from "../lib/zh.mjs";

const argv = process.argv.slice(2);
const flags = {};
const positional = [];
for (const a of argv) {
	if (a === "--json") flags.json = true;
	else if (a === "--text") flags.text = true;
	else if (a.startsWith("--")) {
		const [k, ...rest] = a.slice(2).split("=");
		flags[k] = rest.length ? rest.join("=") : true;
	} else positional.push(a);
}

const cmd = positional.shift();
const out = (obj) => {
	process.stdout.write(JSON.stringify(obj, null, flags.json ? 2 : 0) + "\n");
};
const fail = (msg, extra = {}) => {
	out({ ok: false, error: msg, ...extra });
	process.exit(1);
};

async function api(path, opts) {
	try {
		return await request(path, { ...opts, base: flags.base });
	} catch (err) {
		if (err instanceof LhError) fail(err.message, { status: err.status, body: err.body });
		throw err;
	}
}

async function createChat({ title, kind }) {
	const chat = await api("/api/chats", { method: "POST", body: { title, ...(kind ? { kind } : {}) } });
	const id = chat?.id || chat?.chat?.id;
	if (!id) fail("创建会话失败：返回里没有 id", { body: chat });
	return { id, raw: chat };
}

async function send(chatId, text) {
	await api("/api/chat", { method: "POST", body: { chatId, input: { type: "text", text } } });
}

async function turn(chatId, ms) {
	const r = await followTurn(chatId, { timeoutMs: Number(ms || flags.timeout || 180_000), base: flags.base });
	return r;
}

const commands = {
	async health() {
		const models = await api("/api/models");
		const list = Array.isArray(models) ? models : (models?.models || []);
		out({ ok: true, base: baseUrl(flags.base), models: list.length, sample: list.slice(0, 5) });
	},

	async models() {
		out(await api("/api/models"));
	},

	async list() {
		out(await api("/api/chats"));
	},

	async show(id) {
		if (!id) fail("用法：lh show <chatId>");
		out(await api(`/api/chats/${encodeURIComponent(id)}`));
	},

	async graph() {
		out(await api("/api/graph"));
	},

	/** 新建一条会话（不发言） */
	async new(_unused, rest) {
		const title = flags.title || rest.join(" ") || "新会话";
		const { id } = await createChat({ title, kind: flags.kind });
		out({ ok: true, chatId: id, title });
	},

	/** 往会话里发一条文本（不等回复） */
	async send(id, rest) {
		const text = rest.join(" ");
		if (!id || !text) fail('用法：lh send <chatId> "你要说的话"');
		await send0(id, text);
		out({ ok: true, chatId: id, sent: text.length });
	},

	/** 发一条并等到本轮结束，返回助手文本 —— 最常用 */
	async ask(id, rest) {
		const text = rest.join(" ");
		if (!id || !text) fail('用法：lh ask <chatId> "你要说的话"');
		await send0(id, text);
		const r = await turn(id, flags.timeout);
		if (r.error) fail(r.error, { chatId: id, settled: r.settled });
		out({ ok: true, chatId: id, text: r.text, settled: r.settled, events: flags.json ? r.events : undefined });
	},

	/** 只看一条会话的 SSE（诊断用） */
	async watch(id) {
		if (!id) fail("用法：lh watch <chatId> [--timeout=60000]");
		const r = await turn(id, flags.timeout);
		out({ ok: !r.error, chatId: id, settled: r.settled, text: r.text, error: r.error || null, events: r.events });
	},

	/** 一条龙：开课 —— 建会话 + 用中文模板发开场 */
	async lesson(_unused, rest) {
		const topic = flags.topic || rest.join(" ");
		if (!topic) fail('用法：lh lesson --topic "主题" [--goal "目标"] [--level "水平"] [--minutes 30] [--kind practice]');
		const { id } = await createChat({ title: topic, kind: flags.kind });
		await send0(id, lessonPrompt({ topic, goal: flags.goal, level: flags.level, minutes: flags.minutes }));
		const r = await turn(id, flags.timeout);
		if (r.error) fail(r.error, { chatId: id });
		out({ ok: true, chatId: id, topic, text: r.text, settled: r.settled });
	},

	/** 练一题：practicePrompt */
	async drill(id, rest) {
		const weak = flags.weak || rest.join(" ");
		if (!id) fail('用法：lh drill <chatId> [--weak "易错点"]');
		await send0(id, practicePrompt({ topic: flags.topic, weak }));
		const r = await turn(id, flags.timeout);
		if (r.error) fail(r.error, { chatId: id });
		out({ ok: true, chatId: id, text: r.text, settled: r.settled });
	},

	/** 复盘 */
	async review(id) {
		if (!id) fail("用法：lh review <chatId>");
		await send0(id, reviewPrompt());
		const r = await turn(id, flags.timeout);
		if (r.error) fail(r.error, { chatId: id });
		out({ ok: true, chatId: id, text: r.text });
	},

	async clarify(id, rest) {
		const q = rest.join(" ");
		if (!id || !q) fail('用法：lh clarify <chatId> "补充情况"');
		await send0(id, clarifyPrompt(q));
		const r = await turn(id, flags.timeout);
		out({ ok: !r.error, chatId: id, text: r.text, error: r.error || null });
	},

	help() {
		process.stdout.write(
			[
				"lh —— LearnHarness 中文版 CLI",
				"",
				"  lh health                         探活 + 看有几个模型",
				"  lh models                         模型目录",
				"  lh list                           会话列表",
				"  lh new --title=主题 [--kind=practice]",
				"  lh ask <chatId> \"文本\"            发一条并等回复（最常用）",
				"  lh send <chatId> \"文本\"           只发不等",
				"  lh watch <chatId> [--timeout=60]  只看 SSE",
				"  lh lesson --topic=主题 [--goal=..] [--level=..] [--minutes=30]",
				"  lh drill <chatId> [--weak=易错点]  出一道题",
				"  lh review <chatId>                复盘成错题本",
				"  lh clarify <chatId> \"补充\"        追问",
				"  lh show <chatId> | lh graph",
				"",
				"环境变量：LH_BASE_URL（默认 http://127.0.0.1:3000）",
				"输出：默认单行 JSON；--json 缩进。",
			].join("\n") + "\n",
		);
	},
};

// send 与命令同名，单独包一层避免自递归
async function send0(chatId, text) {
	await api("/api/chat", { method: "POST", body: { chatId, input: { type: "text", text } } });
}

const run = commands[cmd] || (cmd ? null : commands.help);
if (!run) fail(`未知命令：${cmd}（试试 lh help）`);
try {
	await run(positional[0], positional.slice(1));
} catch (err) {
	fail(err?.message || String(err));
}
