#!/usr/bin/env node
// 把 zh/zh-map.json 里的英文串替换进 LearnHarness 源码（UI 汉化）。
// 默认 dry-run；加 --write 才落盘，并先写 .zh.bak 备份。
//
// 只替换三种形态，避免误伤代码：
//   1) "Old" / 'Old'      —— 独立字符串字面量
//   2) >Old<              —— JSX 文本节点
//   3) `Old`              —— 模板字面量（整串）
//
// 用法：
//   node scripts/zh-apply.mjs --lh ../learnharness                # 预演
//   node scripts/zh-apply.mjs --lh ../learnharness --write        # 落盘

import { readdirSync, readFileSync, writeFileSync, statSync, copyFileSync } from "node:fs";
import { join, relative, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const arg = (n, d) => {
	const hit = process.argv.find((a) => a.startsWith(`--${n}=`));
	return hit ? hit.slice(n.length + 3) : d;
};
const flag = (n) => process.argv.includes(`--${n}`);
const LH = arg("lh", join(here, "..", "..", "learnharness"));
const MAP_PATH = arg("map", join(here, "..", "zh", "zh-map.json"));
const WRITE = flag("write");
const ROOTS = ["components", "app"];

const raw = JSON.parse(readFileSync(MAP_PATH, "utf8"));
const map = {};
for (const [en, zh] of Object.entries(raw)) {
	if (en.startsWith("_")) continue;
	if (typeof zh !== "string" || !zh.trim()) continue;
	map[en] = zh;
}
const entries = Object.entries(map).sort((a, b) => b[0].length - a[0].length); // 长串优先

function walk(dir, acc = []) {
	let ents;
	try { ents = readdirSync(dir); } catch { return acc; }
	for (const e of ents) {
		if (e === "node_modules" || e.startsWith(".")) continue;
		const p = join(dir, e);
		if (statSync(p).isDirectory()) walk(p, acc);
		else if (/\.(tsx|jsx|ts)$/.test(e)) acc.push(p);
	}
	return acc;
}

const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
let filesTouched = 0;
let totalHits = 0;
const perEntry = {};
const details = [];

for (const root of ROOTS) {
	for (const file of walk(join(LH, root))) {
		let src = readFileSync(file, "utf8");
		let hits = 0;
		for (const [en, zh] of entries) {
			const e = esc(en);
			const forms = [
				new RegExp(`"${e}"`, "g"), // 双引号
				new RegExp(`'${e}'`, "g"), // 单引号
				new RegExp(`>\\s*${e}\\s*<`, "g"), // JSX 文本
			];
			for (const re of forms) {
				const n = (src.match(re) || []).length;
				if (!n) continue;
				hits += n;
				perEntry[en] = (perEntry[en] || 0) + n;
				src = src.replace(re, (m) => m.replace(en, zh));
			}
		}
		if (hits) {
			filesTouched++;
			totalHits += hits;
			details.push({ file: relative(LH, file), hits });
			if (WRITE) {
				copyFileSync(file, file + ".zh.bak");
				writeFileSync(file, src, "utf8");
			}
		}
	}
}

console.log(`${WRITE ? "已写入" : "预演（未改任何文件）"}：${totalHits} 处替换，涉及 ${filesTouched} 个文件`);
for (const d of details.sort((a, b) => b.hits - a.hits).slice(0, 15)) {
	console.log(`  ${String(d.hits).padStart(3)}  ${d.file}`);
}
const unused = entries.filter(([en]) => !perEntry[en]).map(([en]) => en);
if (unused.length) console.log(`\n没命中的词条（${unused.length}）：${unused.join(" / ")}`);
if (WRITE) console.log("\n改写前的原文件已保存为同目录 *.zh.bak");
