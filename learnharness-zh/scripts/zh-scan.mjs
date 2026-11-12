#!/usr/bin/env node
// 中文化盘点：扫一遍 LearnHarness 源码里"用户能看见的英文串"，产出清单。
// 只读、不改进程外的任何文件（除了 --out 指定的 JSON）。
// 用法：node scripts/zh-scan.mjs --lh ../learnharness --out zh/ui-strings.json

import { readdirSync, readFileSync, writeFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const arg = (name, fallback) => {
	const hit = process.argv.find((a) => a.startsWith(`--${name}=`));
	return hit ? hit.slice(name.length + 3) : fallback;
};
const LH = arg("lh", "../learnharness");
const OUT = arg("out", "zh/ui-strings.json");
const ROOTS = ["components", "app"];

function walk(dir, acc = []) {
	let ents;
	try { ents = readdirSync(dir); } catch { return acc; }
	for (const e of ents) {
		if (e === "node_modules" || e.startsWith(".")) continue;
		const p = join(dir, e);
		const st = statSync(p);
		if (st.isDirectory()) walk(p, acc);
		else if (/\.(tsx|jsx)$/.test(e)) acc.push(p);
	}
	return acc;
}

// JSX 文本节点：>文字<（排除只有符号/空白的）
const reText = />\s*([A-Z][^<>{}`\n]{2,70}?)\s*</g;
// 常见可访问性/占位属性
const reAttr = /\b(placeholder|aria-label|title|alt|label)\s*=\s*"([^"{}]{2,70})"/g;
// 纯展示用的字符串常量（toast/setError 之类）
const reCall = /\b(?:toast|notify|setError|alert)\(\s*"([^"{}]{4,70})"/g;

const skip = /^(className|href|src|id|key|data-|type=|https?:)/;
const hits = [];
let scanned = 0;

for (const root of ROOTS) {
	for (const file of walk(join(LH, root))) {
		scanned++;
		const src = readFileSync(file, "utf8");
		const rel = relative(LH, file);
		const push = (line, kind, text) => {
			const t = text.trim();
			if (!t || skip.test(t)) return;
			if (!/[A-Za-z]{2}/.test(t)) return;
			if (/^[a-z-]+$/.test(t) && t.includes("-")) return; // css 类名残留
			hits.push({ file: rel, line, kind, text: t });
		};
		src.split("\n").forEach((line, i) => {
			let m;
			while ((m = reText.exec(line))) push(i + 1, "jsx-text", m[1]);
			while ((m = reAttr.exec(line))) push(i + 1, m[1], m[2]);
			while ((m = reCall.exec(line))) push(i + 1, "call", m[1]);
		});
	}
}

const byFile = {};
for (const h of hits) byFile[h.file] = (byFile[h.file] || 0) + 1;

const report = {
	generatedAt: new Date().toISOString(),
	source: LH,
	scannedFiles: scanned,
	totalStrings: hits.length,
	files: Object.entries(byFile)
		.sort((a, b) => b[1] - a[1])
		.map(([file, count]) => ({ file, count })),
	strings: hits,
};

writeFileSync(OUT, JSON.stringify(report, null, 2) + "\n", "utf8");
console.log(
	`扫了 ${scanned} 个 tsx/jsx，找到 ${hits.length} 条待翻译串，覆盖 ${report.files.length} 个文件 → ${OUT}`,
);
for (const f of report.files.slice(0, 10)) console.log(`  ${String(f.count).padStart(4)}  ${f.file}`);
