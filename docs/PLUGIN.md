# 插件（MCP 工具）

WeFlow 可以把**外部进程里的插件**并进来，目前有两种：

| `kind` | 插件做什么 | 怎么用 |
| --- | --- | --- |
| `mcp-server` | 提供一组 AI 工具 | 工具以 `plugin.<id>.<tool>` 出现在 MCP 工具表里 |
| `knowledge-source` | 采集材料，写成知识库卡片 | `weflow-cli plugin run <id>`，产出落在 `output/<id>-notes/` |

> 这一页讲的是**进程外**插件。加载 JavaScript 到 WeFlow 进程里这件事**没有**、也不打算做：
> 见 [DECISIONS.md](DECISIONS.md) 的 D-048。原因是进程内的插件拿得到解密的数据库路径、
> 含全部密钥的配置和文件系统，而"清单里声明了能力"并不能修好这一点——插件没有被沙箱。

## 30 秒上手

```powershell
# 仓库里带了两个能跑的最小示例
weflow-cli plugin add examples\plugins\hello          # 提供工具
weflow-cli plugin add examples\plugins\plain-notes    # 产出卡片
weflow-cli plugin doctor

# 工具类：在你的 MCP 客户端里看工具表，会多出 plugin.hello.echo
# 知识源类：跑一次，看它产出的卡片能不能被消费端读出来
weflow-cli plugin run plain-notes
```

接自己的插件：

```powershell
weflow-cli plugin add <你的插件目录>   # 目录里要有 weflow-plugin.json
weflow-cli plugin doctor               # 逐个启动、报告能不能连通、有哪些工具
weflow-cli plugin trust <id>           # 可选：之后调用不再逐次确认
```

## 插件长什么样

一个目录，一份清单，一个能被 `command` 启动的 MCP server：

```text
my-plugin/
├── weflow-plugin.json
└── index.mjs        # 普通的 MCP server，不依赖 WeFlow 的任何代码
```

`weflow-plugin.json`：

```json
{
  "schema": "weflow-plugin/1",
  "id": "my-plugin",
  "name": "我的插件",
  "version": "0.1.0",
  "kind": "mcp-server",
  "description": "一句话说明它是干什么的",
  "entry": { "command": "node", "args": ["index.mjs"] },
  "permissions": { "network": false, "writes": [] }
}
```

| 字段 | 规则 |
| --- | --- |
| `schema` | 目前只认 `weflow-plugin/1`。**版本会协商**：填了更高的版本，报错会说"请升级 weflow-cli"而不是让你改清单；填了更低的会说"旧契约不再支持" |
| `id` | 小写字母开头，只含小写字母/数字/连字符，2–39 字符。**不能含点号**（它是 `plugin.<id>.<tool>` 的分隔符） |
| `kind` | `mcp-server`（提供工具）或 `knowledge-source`（产出卡片）。两种形状完全不同，混成一种会让两边都说不清 |
| `entry.command` / `args` | 怎么启动你的 server。`args` 省略即空数组 |
| `entry.cwd` | 可选，相对插件目录 |
| `permissions` | **声明**，见下 |

插件本身就是一个**普通的 MCP server**（stdio 传输）。用官方 SDK、用别的语言、甚至手写
JSON-RPC 都行——WeFlow 只按协议说话。注意：从 stdin 读、往 stdout 写协议流，
**调试输出请走 stderr**，打印到 stdout 会污染协议。

## 知识源插件怎么产出卡片

`plugin run` 起你的进程，把一份**作业从 stdin** 交进去（不放命令行：命令行在进程列表里可见）：

```json
{ "schema": "weflow-plugin/1", "job": "collect", "id": "my-source",
  "out_dir": "…/output/my-source-notes", "input_dir": "…", "since": "2026-10-01" }
```

你只需要往 `out_dir` 写 `.md` 卡片，然后退出（退出码 0 = 成功）。**`out_dir` 是你唯一被允许写的
地方**——插件不需要知道知识库在哪，也不需要 WeFlow 的任何配置。

卡片必须能被知识库的消费端读出来。最小形状：

```markdown
---
title: 卡片标题
source: 来源
topic: AI
tags: [AI]
---

# 卡片标题

## AI 摘要

一段摘要。

## 概念

- [[RAG]] — 检索增强生成
- [[向量索引]] — 用向量找相似内容
```

### 最容易踩的坑：没有 wikilink 的卡片会被**静默跳过**

消费端的第一条规则是"正文里没有 `[[链接]]` 的卡片不参与建页"。这不是报错——文件在、目录在、
什么都没红，概念页就是没有。所以 `plugin run` 会用**真正的消费端**（`compile_wiki` 自己的解析函数）
把你产出的卡片读一遍，并说清楚哪张被跳过、为什么：

```text
卡片 2 张，消费端读出 2 张（共 5 条概念链接）
  ⚠ 03-empty.md：正文里没有 [[wikilink]]：消费端会跳过这张卡，概念页不会有它的任何内容
```

这里**不重新实现一遍卡片规则**——那样就是第二份契约，Python 那边改了也不会红。宿主直接调用消费端。

## 工具怎么出现

插件里的每个工具会以 `plugin.<id>.<工具名>` 出现，例如 `plugin.hello.echo`。
没装插件时工具表与以前**逐字相同**——插件是纯增量。

插件起不来只让它自己的工具消失，不会让整张工具表报错；`plugin doctor` 会说明原因。
握手也有超时：一个永远不说 MCP 的进程不会把工具表挂死。

## 权限与安全：哪些是保证，哪些只是声明

这一节是这个机制里唯一值得逐字读的部分。**别把声明当保证。**

真正成立的（宿主强制）：

- **进程外**。插件只能通过 stdio 的 MCP 协议说话，拿不到 WeFlow 的任何对象、数据库路径或配置。
- **不喂密钥**。子进程的环境是白名单重建的，不是继承 `process.env`——你的 API key 不会顺路漏给插件。
- **默认每次确认**。没 `plugin trust` 过的插件，工具调用必须带 `confirm: true`，否则只返回一段预览，
  **参数不会转发出去**。禁用（`plugin disable`）的插件即使带 `confirm` 也不执行。
- **可以单独关掉**。`plugin disable <id>` 只影响这一个插件。

**不成立的**（别误以为有）：

- `permissions.network` / `permissions.writes` 是**声明**，用于 `plugin list` / `plugin doctor` 的展示，
  宿主**无法核实**一个进程实际干了什么。它不是沙箱。
- 插件仍然是一个跑在你机器上的普通进程，以你的身份运行。它能不能读文件、连网络，取决于你的操作系统，
  不取决于这份清单。

所以 `plugin add` 的语义是"我看了这个插件并愿意在它身上做一次人工决定"，不是"它被隔离了"。
`plugin trust` 更是如此——那是你的判断，不是对插件的评估。

## 一个能拷的骨架

两个示例都是完整可运行的，写自己的插件时直接拷对应那个：

- `examples/plugins/hello/index.mjs` — `mcp-server`，一个 `echo` 工具。改三处：`serverInfo`、
  `tools/list` 的定义、`tools/call` 的处理。
- `examples/plugins/plain-notes/index.mjs` — `knowledge-source`，把一目录纯文本转成卡片。
  换 RSS / Readwise / Kindle 划线时，只是"怎么拿到内容"不同，产出卡片的形状完全一样；它不依赖
  任何第三方库，也不联网。

## 还没有的东西（写在这里，免得你以为是漏了）

- **`capabilities --json` 不列插件工具**。那是一条只读的自省命令，列插件工具就得把每个插件都启动一遍，
  把"看一眼我有什么"变成"拉起一堆第三方进程"。机器可读的插件信息用 `weflow-cli plugin list --json`
  和 `plugin doctor --json`。
- **没有中心化插件市场**。目前靠 GitHub topic `weflow-plugin` 和文档里的清单发现。中心化注册表意味着
  为别人的代码背书，维护成本和安全责任都不划算，等有足够多的插件再说。
- **没有版本兼容矩阵**。只有 `schema` 的版本号，没有"插件声明兼容哪些 WeFlow 版本"的机制。
- **没有知识源类插件的"自动进知识库"**。`plugin run` 只产出卡片并体检；把它们并进概念页仍然要跑
  既有的 `compile_wiki`（`--cards output/<id>-notes`）。没有替你把这一步串起来，是因为一条线一条线地建
  是刻意的（两条知识线分开），自动串会把这个边界弄糊。
