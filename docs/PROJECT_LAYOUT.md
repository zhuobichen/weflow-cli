# weflow-cli 项目结构版本记录

本文件记录**目录结构的版本快照**。每次做"文件/目录大变更"（移动、重命名、新增顶层目录、收口根目录、改分层约定）时：

1. 先把**变更前**的结构快照追加到本文（标 `layout-vN`，含日期与变更摘要）；
2. 明确了版本号规则（见下），变更提交信息里带上 `layout-vN`；
3. 不要删除历史快照——它是回滚与理解"为什么文件在这里"的依据。

## 版本号规则

- 格式：`layout-vN`（N 递增整数，从 1 开始）。
- **何时 +1**：任何影响"文件在哪一层/叫什么/如何分层"的变更——新增或删除顶层目录、移动文件跨目录、重命名模块目录、改变分层约定、收口根目录。
- **何时不变**：只在某目录内新增/修改普通文件（加一个工具、加一个测试、改文档内容），不算结构变更。
- 版本号写进：本文的快照标题、变更提交信息、必要时 `ARCHITECTURE.md` 的相应段落。

---

## layout-v1 — 2026-11-27（基线：变更前的旧结构）

> 这是本项目第一次记录目录结构。**变更摘要**：此前无版本记录；本次建立制度并做首次整理
> （清理测试临时目录 `weflow-ro-*`、`sns_probe.db` 等；不改变任何 git 跟踪文件的位置）。

### 顶层（git 跟踪的正式文件）

```
AGENTS.md                 仓库规则（Working Rules / Verification / Documentation Protocol）
ARCHITECTURE.md           模块边界与数据流
CHANGELOG.md              用户可见发布说明
CLAUDE.md                 快速维护入口
CONTRIBUTING.md
IMPLEMENTATION_PLAN.md    早期规划文档（历史）
LICENSE
OPERATIONS.md             操作与排障
README.md / README.en.md  门户
SECURITY.md
cli.cjs                   npm bin 入口（转发到 dist）
package.json / package-lock.json
tsconfig.json
requirements*.txt         Python 依赖（3 份）
logo.png / weflow-cli图标.png / weflow-cli架构图.png   资源
plan.md                   早期规划文档（历史）
```

### 顶层目录

```
bin/            CLI 命令定义与接线（weflow-cli.ts）
src/            TypeScript 源码
  core/         数据库、密钥、原生库、微信客户端集成
  services/     配置、聊天、导出、助手、隐私、白名单、消息工作流
  panel/        本机面板（守护进程 UI、球/气泡）
  utils/        日期区间、文本、路径等纯函数
  types.ts / lz4.d.ts
scripts/        Python 工作流（NT 数据库、日报、知识处理、报告、搜索/RAG、评测）
mcp-server/     stdio MCP server
test/           Node 内置回归测试（143 个 .test.ts 平铺；另有 Python *_test.py）
                ※ 2026-11-27 加 agent-version.test.ts 后为 144（此快照保留当时数值）
docs/           文档（AGENT.md / DECISIONS.md / PROJECT_STATE.md / LINUX.md / ...）
deploy/         CI/部署
examples/       示例
resources/      资源
dist/           TypeScript 编译产物（gitignore）
models/         本地模型（gitignore）
output/         运行产物：biz-daily / article-notes / chat-notes / wechat-vault(Wiki+Sources) / .semantic_index 等（gitignore）
logs/           日志（gitignore）
```

### git 忽略但重要的运行目录

```
output/wechat-vault/Wiki/Concepts/   wiki 概念页 ~48k
output/wechat-vault/Sources/         原文存档（文章/聊天两条线）
output/article-notes/                知识卡 ~18k
output/.semantic_index/              RAG 向量+BM25 混合索引
output/biz-daily/                    公众号日报归档
```

### 已知临时产物（gitignore 覆盖，运行时产生）

```
weflow-ro/          只读副本测试夹具（test/readonly_copy_cache_test.py 用）
weflow-ro-*/        测试在系统临时目录不可写时回退到 cwd 建的只读副本（进程退出不删）
tmp_* / tmp[0-9a-z_]*/  测试临时目录
output/.cache/ 等  各类缓存
```

### 当时对照的行业建议（触发本次整理的 4 条）

1. GEOFlow 项目结构：根只放入口、CLI 收口 `bin/`、数据/日志/文档各归其位——临时目录已清、正式文件未动。
2. larkcli 的 AGENTS.md：stdout 只放数据、stderr 放其它；路径校验；错误消息 AI 可解析——抽样 3 个 `--json` 命令输出均为纯 JSON，已符合。
3. survivalgan 的 .gitignore：数据/产物排除 + `.keep` 保目录——本项目用时 `mkdirSync(recursive)` 创建，不依赖预存目录，已符合。
4. Python src layout：core/services/utils 职责 + tests 镜像——本项目是 TypeScript，`src/core|services|utils` 已分层；tests 平铺 `test/`（移动风险大于收益，暂不变）。
