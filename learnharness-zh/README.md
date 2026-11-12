# LearnHarness 中文版（lh）

把 [LearnHarness](https://github.com/amosblomqvist/learnharness)（一个"教你硬知识"的 AI 学习智能体）
包成**中国学习场景能用**的形态：中文教学口径 + 一套 CLI + 一个 MCP server + UI 汉化工具链。

**不改 LearnHarness 源码**——它照常升级，这里只是外壳。所以也不存在"分叉后跟不上上游"的问题。

---

## 1. 它解决什么问题

原版 LearnHarness 好用，但直接拿来教国内学习者有三个摩擦：

| 摩擦 | 这里的处理 |
|---|---|
| 提示词与措辞是英文语境，术语、教材口径对不上 | `zh/教学法-中文版.md` + `zh/prompts/zh-CN-教学提示词.md`，把"一次一个推理步"落到课标/考纲/教材版本/错题本这些具体动作上 |
| 只能开桌面 App 点着用，AI 调不动 | `bin/lh.mjs`：11 个命令，输出 JSON，任何 agent 都能调 |
| 界面全英文 | `zh/ui-strings.json`（自动盘点的 468 条待译串）+ `scripts/zh-apply.mjs`（可预演、可回滚的替换） |

---

## 2. 目录

```
learnharness-zh/
├─ bin/lh.mjs              CLI 入口（零依赖，Node 20+）
├─ lib/client.mjs          HTTP + SSE 客户端（超时/错误分类）
├─ lib/zh.mjs              中文话术模板（开课/追问/出题/复盘）
├─ mcp/server.mjs          MCP stdio server（7 个工具）
├─ zh/教学法-中文版.md      教学法中文重写（应试/真懂两档、错题本、间隔复习）
├─ zh/prompts/…            可覆盖进 LH 的中文提示词
├─ zh/ui-strings.json      源码里用户可见英文串的盘点（自动生成）
├─ zh/zh-map.json          英→中映射表（可持续追加）
├─ scripts/zh-scan.mjs     盘点：扫描 LH 源码，产 ui-strings.json
├─ scripts/zh-apply.mjs    应用：按映射表替换，默认预演
├─ test/smoke.mjs          假 LH 实例，验证 CLI 与 MCP 端到端
└─ dsh/SKILL.md            DSH skill：agent 怎么调这套东西
```

---

## 3. 快速开始

```bash
# 0) 先让 LearnHarness 跑起来（另开一个终端）
cd ../learnharness && npm ci && npm run plugins:setup && npm run dev   # http://localhost:3000

# 1) 探活
node bin/lh.mjs health
# → {"ok":true,"base":"http://127.0.0.1:3000","models":3}

# 2) 开一课
node bin/lh.mjs lesson --topic="导数" --goal="高考能拿分" --level="高一" --minutes=30

# 3) 接着聊（用它回给你的 chatId）
node bin/lh.mjs ask <chatId> "我没懂第二步：为什么切线斜率就是导数"
```

端口不同就设 `LH_BASE_URL`。

---

## 4. CLI 命令

| 命令 | 作用 |
|---|---|
| `lh health` | 探活 + 数模型（`models:0` 说明没配模型） |
| `lh models` / `lh list` / `lh show <id>` / `lh graph` | 只读查询 |
| `lh lesson --topic=… [--goal=… --level=… --minutes=30 --kind=practice]` | 建会话 + 中文开场 + 等首轮回复 |
| `lh ask <id> "文本"` | 发一条并等到本轮结束，返回老师回复 |
| `lh send <id> "文本"` | 只发不等 |
| `lh watch <id> [--timeout=60000]` | 只看 SSE 事件（排障用） |
| `lh drill <id> [--weak=易错点]` | 要一道题（答案在 `---` 之后） |
| `lh review <id>` | 复盘成错题本格式 |
| `lh clarify <id> "补充"` | 追问 |

统一约定：默认输出单行 JSON，`--json` 缩进；失败 `{ok:false,error,status}` 且 exit 1。

---

## 5. 接进 DSH / 其它 MCP 客户端

```jsonc
{
  "mcpServers": {
    "lh-study": {
      "command": "node",
      "args": ["<绝对路径>/learnharness-zh/mcp/server.mjs"],
      "env": { "LH_BASE_URL": "http://127.0.0.1:3000" }
    }
  }
}
```

工具：`lh_health` `lh_lesson` `lh_ask` `lh_drill` `lh_review` `lh_list` `lh_graph`。
DSH 侧照你现有 MCP 配置的写法加这一段即可（字段名以你当前版本的 MCP 配置为准）。

---

## 6. 中文化两层

**第一层：说话方式**（不需要改 LH）

`lh lesson` 把 `lib/zh.mjs` 里的规则作为开场消息发过去；更完整的一套在
`zh/prompts/zh-CN-教学提示词.md`，可以粘进 LH 的提示词段，或用 `ask` 手动发一次。

**第二层：界面文案**（改 LH 源码，可回滚）

```bash
node scripts/zh-scan.mjs --lh=../../learnharness --out=zh/ui-strings.json   # 盘点 468 条
node scripts/zh-apply.mjs --lh=../../learnharness                          # 预演 122 处
node scripts/zh-apply.mjs --lh=../../learnharness --write                   # 落盘（原文件存 *.zh.bak）
```

`zh-map.json` 目前覆盖侧栏/会话/路线图/设置等高频串；没命中的词条会在输出里列出来，说明还没翻或改了名。
全量 468 条不建议一次硬翻——先跑起来看哪些真的挡路，再往里加。

---

## 7. 测试

```bash
node test/smoke.mjs
# ✅ health 探到 2 个模型 / ✅ ask 拿到最终文本 / ✅ MCP initialize / ✅ tools/call …
```

测试自带一个假 LearnHarness（`http` 起在随机端口，SSE 回放 `message_update` + `turn-settled`），
所以不依赖真实例、不需要 key。

---

## 8. 与上游的关系 / 边界

- LearnHarness 是 **AGPL-3.0**。这里只调用它的 HTTP 接口、替换它源码里的界面文案，
  **没有把它的代码搬进本仓库**；但 `zh-apply.mjs` 改的是它的源码，分发被改过的它的源码要遵守 AGPL。
- 本目录的代码（CLI/MCP/脚本/文档）独立，MIT，与本仓库其余部分一致。
- 它**不理解你的课表**：这是通用教学流程，具体到"这周考哪一章"仍要人喂。`--level` 里写教材版本与省份是目前最省事的做法。
- `lh graph` 只是把图取回来；把它接进 weflow 的知识库（概念页互链）还没做——这是下一步。

---

## 9. 下一步（按性价比排）

1. 把 `lh graph` 的命题节点导成 weflow 概念页（复用 `search_knowledge` 的页面格式）。
2. `zh-map.json` 补到 300+ 条，加一个"未命中即报错"的 CI 检查。
3. `lh lesson` 支持 `--syllabus=人教A版/必修一`，自动把考纲边界写进开场。
4. 把"周测"做成 `lh schedule`：按错题间隔复习时间点提醒。
