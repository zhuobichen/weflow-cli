// 冒烟测试：起一个假的 LearnHarness，验证 CLI 与 MCP 真的能跑通。
// 运行：node test/smoke.mjs   （Node 20+，零依赖）
import { createServer } from "node:http";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const pexec = promisify(execFile);
const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");

const sse = (events) =>
	events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("") + "data: [DONE]\n\n";

const server = createServer((req, res) => {
	const url = new URL(req.url, "http://x");
	const json = (o, code = 200) => {
		res.writeHead(code, { "content-type": "application/json" });
		res.end(JSON.stringify(o));
	};
	if (req.method === "GET" && url.pathname === "/api/models") return json([{ id: "m1" }, { id: "m2" }]);
	if (req.method === "GET" && url.pathname === "/api/chats") return json({ chats: [{ id: "c1", title: "测试" }] });
	if (req.method === "GET" && url.pathname === "/api/graph") return json({ nodes: [{ id: "n1" }], edges: [] });
	if (req.method === "POST" && url.pathname === "/api/chats") return json({ id: "c1", title: "测试" });
	if (req.method === "POST" && url.pathname === "/api/chat") {
		let body = "";
		req.on("data", (c) => (body += c));
		req.on("end", () => {
			const parsed = JSON.parse(body || "{}");
			if (!parsed.chatId || !parsed.input?.text) return json({ error: "bad" }, 400);
			json({ ok: true });
		});
		return;
	}
	if (req.method === "GET" && /^\/api\/chats\/[^/]+\/stream$/.test(url.pathname)) {
		res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache" });
		res.end(
			sse([
				{ type: "message_update", message: { content: [{ type: "text", text: "先测三个问题：" }] } },
				{ type: "message_update", message: { content: [{ type: "text", text: "先测三个问题：1) 你知道 X 吗？" }] } },
				{ type: "turn-settled" },
			]),
		);
		return;
	}
	json({ error: "not found" }, 404);
});

await new Promise((r) => server.listen(0, "127.0.0.1", r));
const port = server.address().port;
const env = { ...process.env, LH_BASE_URL: `http://127.0.0.1:${port}` };
let failed = 0;
const check = (name, cond, extra) => {
	if (cond) console.log(`✅ ${name}`);
	else { failed++; console.log(`❌ ${name}`, extra ?? ""); }
};

try {
	const run = async (...args) => {
		const { stdout } = await pexec(process.execPath, [join(root, "bin/lh.mjs"), ...args], { env });
		return JSON.parse(stdout.trim());
	};

	const health = await run("health");
	check("health 探到 2 个模型", health.ok === true && health.models === 2, health);

	const list = await run("list");
	check("list 返回会话", Array.isArray(list.chats) && list.chats.length === 1);

	const ask = await run("ask", "c1", "你好");
	check("ask 拿到最终文本", ask.ok === true && ask.text.includes("1) 你知道 X 吗？"), ask);

	const lesson = await run("lesson", "--topic=导数");
	check("lesson 建会话并拿到回复", lesson.ok === true && lesson.chatId === "c1" && lesson.text.length > 0, lesson);

	// MCP：完整走一次 initialize → tools/list → tools/call
	const mcpOut = await new Promise((resolve, reject) => {
		const p = execFile(process.execPath, [join(root, "mcp/server.mjs")], { env }, (err, stdout) =>
			err ? reject(err) : resolve(stdout),
		);
		const lines = [
			{ jsonrpc: "2.0", id: 1, method: "initialize", params: {} },
			{ jsonrpc: "2.0", method: "notifications/initialized" },
			{ jsonrpc: "2.0", id: 2, method: "tools/list" },
			{ jsonrpc: "2.0", id: 3, method: "tools/call", params: { name: "lh_health", arguments: {} } },
			{ jsonrpc: "2.0", id: 4, method: "shutdown" },
		];
		p.stdin.write(lines.map((l) => JSON.stringify(l)).join("\n") + "\n");
		p.stdin.end();
		setTimeout(() => p.kill(), 5000);
	});
	const msgs = mcpOut.trim().split("\n").filter(Boolean).map((l) => JSON.parse(l));
	const init = msgs.find((m) => m.id === 1);
	const tl = msgs.find((m) => m.id === 2);
	const call = msgs.find((m) => m.id === 3);
	check("MCP initialize", init?.result?.protocolVersion === "2024-11-05", init);
	check("MCP tools/list >= 7", (tl?.result?.tools || []).length >= 7, tl?.result?.tools?.length);
	check(
		"MCP tools/call lh_health 返回模型数",
		String(call?.result?.content?.[0]?.text || "").includes('"models": 2'),
		call?.result?.content?.[0]?.text,
	);
} finally {
	server.close();
}

console.log(failed ? `\n${failed} 项失败` : "\n全部通过");
process.exit(failed ? 1 : 0);
