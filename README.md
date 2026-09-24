<div align="center">

<h1>𝗪𝗲𝗙𝗹𝗼𝘄 𝗖𝗟𝗜 <img src="./logo.png" width="280" valign="middle" alt="WeFlow CLI logo" /></h1>

**简体中文** · [English](./README.en.md)

*今人不见古时月，今月曾经照古人*

> 夫天地者，万物之逆旅也；光阴者，百代之过客也。

> 让聊天记录、公众号阅读和个人知识工作流回到你的本地电脑。

[![npm](https://img.shields.io/npm/v/weflow-cli)](https://www.npmjs.com/package/weflow-cli)
[![Node.js](https://img.shields.io/badge/Node.js-18%2B-339933)](https://nodejs.org/)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB)](https://www.python.org/)
[![WeChat](https://img.shields.io/badge/WeChat-4.x%20tested-07C160?logo=wechat&logoColor=white)](https://github.com/zhuobichen/weflow-cli/releases)
[![Local-first](https://img.shields.io/badge/local--first-zero%20telemetry-8A2BE2)](./SECURITY.md)
[![License](https://img.shields.io/badge/License-MIT-f4b400)](./LICENSE)

</div>

**WeFlow CLI 把微信变成你的本地第二大脑**：在本机读取和查询聊天记录、导出可读 HTML；把公众号文章整理成带 AI 摘要的日报；读取微信收藏；构建可语义搜索的知识库；并通过 MCP 接入 Claude Code 等 AI 编辑器。支持 Windows 与 Linux。

> 🔒 **本地优先**：完全在你的电脑上运行——零遥测、零云上报；数据库密钥以机器绑定的 AES-256-GCM 加密存储在本机。AI 功能仅在显式配置你自己的 API Key 后启用。详见[安全与隐私声明](./SECURITY.md)。

> 本项目仅限于处理你有权访问的数据。请遵守适用法律、微信规则和他人的隐私边界。

> **⚠️ 使用边界与风险**：仅在你本人控制的设备上，处理你本人拥有或明确获授权访问的数据。请勿将本项目用于监控、跟踪或读取他人的微信数据，也不要部署到他人设备或远程静默运行。使用前请自行确认适用法律、微信服务条款及账号权限；第三方工具可能触发账号风控、限制或封禁。本项目不构成法律意见，使用者需自行承担使用责任。

## 效果一览

**微信收藏查询**（读取本地 `favorite.db`，支持类型过滤与关键词搜索）：

```text
$ weflow-cli fav list -n 3

收藏列表 (共 1042 条, 显示 3 条):

[2026/8/21 19:42:42] [文章] “豆包型人格”爆火，可千万别学
    来源: 中国研究生
    https://mp.weixin.qq.com/s/eDk_kcyd8ltuqn6J3iReVg
[2026/8/21 10:50:38] [文章] 爆火插件让DeepSeek V4 Pro 0813性能拉满，全面超越 Fable 5！
    来源: 智猩猩AI
    https://mp.weixin.qq.com/s/3GXFjmtUsq42sXJHJGgwyg
[2026/8/21 10:47:21] [文章] 250年前，一个26岁年轻人写了一本小册子，至今仍在拷问每一个文明社会
    来源: 悦读撷英
    https://mp.weixin.qq.com/s/YIkB8TyyyimPP1hmgQQPZg
```

**完整工作流，一条命令一个环节**：

```text
weflow-cli sessions                    # 会话列表
weflow-cli messages "联系人" -n 20      # 查询聊天消息
weflow-cli export "联系人" html         # 导出 HTML / Excel / TXT / JSON
weflow-cli fav list -t article -k AI   # 微信收藏: 类型过滤 + 关键词搜索
weflow-cli daily --date 2026-08-21     # 公众号日报 + AI 摘要与分类
weflow-cli daily-server                # 本地阅读器 http://localhost:8765
weflow-cli chat "RAG 的原理是什么?"     # 知识库 RAG 问答
weflow-cli mcp-config                  # 一键接入 Claude Code 等 MCP 客户端
```

## 平台支持

| 能力 | Windows | macOS | Linux |
| --- | --- | --- | --- |
| 数据目录自动发现 | ✅ | ✅ | ✅ |
| 本地数据初始化 | ✅ 自动发现/验证 | ❌ 需手动提供凭据 | ⚠️ 取决于发行版与权限 |
| 聊天记录查询（NT / 本地数据库） | ✅ | ✅ | ✅ |
| WCDB 数据服务（联系人昵称等） | ✅ | ⚠️ 依赖原生库 | ⚠️ 依赖原生库 |
| 公众号日报 / 知识库 / MCP | ✅ | ✅ | ✅ |

macOS：当前不提供自动初始化；如你已通过官方或其他经授权的方式取得本地数据访问凭据，请按 `weflow-cli init --path` 和 `config` 的帮助完成配置。

### Linux 快速上手（微信 4.x Linux 原生版）

```bash
# 1. 安装 sqlcipher 开发库（编译 sqlcipher3 需要）
sudo apt install libsqlcipher-dev

# 2. 安装 Python 依赖
pip3 install --user sqlcipher3 cryptography html2text zstandard

# 3. 安装 CLI 并初始化
npm install -g weflow-cli
weflow-cli init    # 按提示完成本地初始化
```

Linux 的自动初始化能力取决于微信发行版、Python 依赖和当前用户权限；请先运行 `weflow-cli check`，按系统安全策略配置，不要为了排障降低整机权限。

## 你可以做什么

| 场景 | 能力 |
| --- | --- |
| 聊天记录 | 查询会话、联系人和消息；导出 JSON、TXT、HTML、Excel。HTML 导出会同时给出一份媒体覆盖报告，说明哪些图片/表情没能嵌入以及原因。 |
| 公众号日报 | 抓取文章、AI 摘要与分类、生成本地阅读页，保留收藏和已读状态。 |
| 个人知识库 | 同步微信读书笔记、构建 Obsidian Vault、语义搜索、RAG 问答和概念 Wiki。 |
| AI 协作 | 通过 MCP 把文章、知识库、日报和受控的本地数据能力交给兼容客户端；工具清单以 `weflow-cli mcp-config` 和 `docs/MCP.md` 为准。 |
| 个人回顾 | 聊天月报、年度报告、待办提取与朋友圈本地缓存查询。 |
| 微信收藏 | 读取微信"收藏"内容（公众号文章、文字、图片、视频、聊天记录），支持类型过滤、关键词搜索与 Markdown/JSON 导出。 |
| 第二大脑 Agent | 在微信里和本地 AI 助手对话：自然语言查询聊天记录、收藏、朋友圈、日报、微信读书、待办与知识库；三层记忆跨会话延续，守护进程常驻后台。 |

> **⚠️ 合法使用边界**：本工具仅限访问**你自己拥有并登录的微信账号**在自己设备上的数据。未经他人明确同意，不得用它监控配偶、员工或任何第三方，也不得部署到他人设备或远程静默运行。请同时留意微信服务条款、账号风控和当地法律要求。详见 [SECURITY.md](SECURITY.md)。

## 三分钟上手

### 1. 安装与检查

> npm 包与 GitHub 发布分开进行；需要最新修复时，请先确认 npm 包版本，或直接使用 GitHub `master` 分支代码。

需要 Node.js 22.13+、Python 3.10+，以及已登录的 Windows 微信。安装后先检查本机环境：

```powershell
npm install -g weflow-cli
weflow-cli check
```

安装标准 Windows 4.x 工作流所需的 Python 依赖：

```powershell
python -m pip install -r requirements.txt
```

若仍在使用微信 3.x，再安装可选依赖：`python -m pip install -r requirements-3x.txt`。

### 2. 初始化本地数据访问

关闭微信后运行初始化，按提示启动并登录微信，CLI 会尝试发现数据目录并提取所需密钥：

```powershell
weflow-cli init
```

微信“存储位置”、账号目录和其 `db_storage` 目录都可以直接指定：

```powershell
weflow-cli init --path "D:\WeChat\xwechat_files"
```

初始化完成后，先验证是否能读取到自己的数据：

```powershell
weflow-cli sessions
weflow-cli contacts -k "关键词"
weflow-cli messages "联系人" -n 10
```

遇到密钥、数据库或 Python 环境问题，请按 [操作与排障手册](./OPERATIONS.md) 逐项检查。

### 3. 选择一条工作流

**导出一段聊天记录**

```powershell
weflow-cli export "联系人" html --output ./output
```

HTML 导出默认内嵌微信缓存里的缩略图，并尽力还原图片、表情包、系统消息和公众号卡片。远程媒体按 URL 缓存在输出目录的 `.cover-cache/`，所以**重复导出接近瞬时**（实测 1600+ 条约 2 秒），首次导出才需要联网。自定义表情包从本地缓存离线解密，seed 未配置时会自动扫描微信内存反推并提示固化命令。确需原图（5–24MB/张）时加 `--full-images`，代价是慢数倍。细节见[操作与排障手册](./OPERATIONS.md#4-导出聊天记录)。

**生成当天公众号日报**

```powershell
weflow-cli daily --date 2026-08-12 --api-key "你的 DeepSeek API Key"
weflow-cli daily-server --date 2026-08-12

# 仅预览指定日期的文章，不调用 AI、不写入日报
weflow-cli daily --date 2026-08-12 --dry-run

# 单次生成日报时关闭所有 AI，仍保留抓取、HTML 和本地索引
weflow-cli daily --date 2026-08-12 --no-ai

# 查看最近 30 天各公众号的推送与日报处理频率
weflow-cli daily-stats --days 30 --limit 30

# 只生成指定公众号的日报（可重复，也可用逗号分隔）
weflow-cli daily --source "公众号A" --source "公众号B"
weflow-cli daily --source "公众号A,公众号B"

# 持久化日报来源；留空则恢复为全部公众号
weflow-cli config set dailySources "公众号A,公众号B"

# 持久化关闭日报 AI；恢复为 true 即重新启用
weflow-cli config set dailyAiEnabled false
weflow-cli config set dailyAiEnabled true

# 不在日报里出现的主题（展示层开关：照常抓取归档，只是不展示；改主意不用重抓）
# 焦点主题 AI 不能排除，写错的主题名会被忽略并打 WARN
weflow-cli config set dailyExcludeTopics "新闻,投资,学术"

# 为来源设置固定类别；已分类来源只生成摘要，主题用你配的、标签取该类别（不会有真标签）
weflow-cli config set dailySourceCategories '<JSON object: source name or gh_ ID -> AI/政治/学术/新闻/文学/投资>'

# 文章主题与相关度改由 Jev 决策模型判断（可选；不配则沿用 LLM 解析路径）
weflow-cli config set typesafeApiKey "..."
```

阅读器默认在 `http://localhost:8765/` 提供服务。

**接入 AI 编辑器**

```powershell
weflow-cli mcp-config > .mcp.json
```

将生成的配置放到所用 MCP 客户端的配置位置后重启客户端即可。MCP 的工具列表与配置方式见 `weflow-cli mcp-config` 输出。

除知识库类工具外，MCP 也暴露受控的只读本地微信数据能力（会话、聊天记录、收藏、朋友圈、日报、微信读书、待办、知识库与助手记忆查询）。默认 MCP 不发送消息、不发布文章，也不修改待办、记忆或配置。工具清单以当前代码和 [MCP 集成指南](./docs/MCP.md) 为准，不固定写死数量。

**在微信里挂一个本地 AI 助手（第二大脑）**

```powershell
weflow-cli config set deepseekApiKey "sk-..."   # 或任意 OpenAI 兼容端点: aiBaseUrl + aiModel
weflow-cli login-wechat        # 扫码绑定消息通道 (微信里会出现 ClawBot 联系人)
weflow-cli assistant start     # 后台守护进程常驻
weflow-cli panel               # 打开本机面板窗口 (不扫码也能用)
```

`weflow-cli panel` 打开的是一个常驻小窗，它跟微信里问的**是同一个大脑**（同一份记忆、
同一条每日配额）。**不登录微信也能用**——消息通道是可选的。窗口只是客户端：消息走守护进程
开在 `127.0.0.1` 上的入口，所以两个入口不会各写一份记忆。悬浮球形态需要 Electron
（`npm i -g electron`）；没装的话会降级成 Edge/Chrome 的 `--app` 小窗，界面一样，
但没有无边框置顶、没有托盘与快捷键。

之后在手机微信的 ClawBot 对话里直接说话，助手会自动查询本地聊天记录和收藏作答，并具备跨会话记忆：

- 「我最近都在忙什么？」— 自动检索会话与聊天记录综合回答
- 「总结我和 XX 的聊天」— 定向读取与某联系人的消息
- 「收藏里有哪些关于 AI 的文章？」— 搜索微信收藏
- 「这篇收藏讲了什么？」— 抓取收藏文章正文并总结
- 「最近朋友圈都发了啥？」「谁最爱发朋友圈？」— 朋友圈时间线与统计
- 「今天公众号推了什么？有哪些 AI 类的？」— 日报查询，按分类/关键词过滤
- 「我在读什么书？」「XX 这本书的笔记」— 微信读书书架与笔记本
- 「我有什么待办？有急事吗？」— 待办清单（按紧急度排序）
- 「知识库里怎么讲 RAG 的？」— 概念 Wiki 检索
- 「记住：我的项目叫 weflow-cli」— 写入长期记忆

运行机制：守护进程通过微信官方 Bot 通道（iLink）长轮询收发消息，Agent 循环、三层记忆（工作窗口 / 滚动摘要 / 长期事实）、数据库查询全部在本机执行；仅最终提问与回复文本会发送给所配置的 LLM，工具输出中的电话/邮箱/链接等 PII 默认自动脱敏（`config set assistantPrivacy strict` 可加强，`ollama` 本地引擎则完全不出网）。会话 24 小时未活跃需重新扫码，单窗口内主动回复有官方条数限制。
- 「帮我回老王那句话」— 起草回复：先判断对方要什么、风险和该不该给实质内容，再给几条候选。
  **只产出文本，不会替你发送**；涉钱或风险很高时不给草稿，只说明该先确认什么

成本护栏：内置每日 100 条处理上限（防异常流量烧钱，微信内发「记忆」可查用量）；助手默认拒绝所有来信，必须明确配置白名单后才会触发 AI 调用：

```powershell
weflow-cli config set assistantWhitelist "你的@im.wechat ID"   # 未设置 = 拒绝所有人
```

群聊功能目前取决于微信官方 Bot 通道是否返回明确的群事件；本项目不会通过客户端自动化或非官方协议强行入群。若上游未来提供群 ID、发送成员 ID 与 @ 提及字段，群消息仍默认拒绝，必须同时设置群白名单、成员白名单并保留 @ 门槛：

```powershell
weflow-cli config set assistantGroupWhitelist "群聊ID"
weflow-cli config set assistantGroupRequireMention true
```

仅当官方通道实际送来群事件时，上述配置才会生效；当前 ClawBot 私聊能力不受影响。

常用管理命令：`weflow-cli assistant status` / `log` / `stop`；微信内发送「帮助」查看助手指令。

## 常用命令

| 目标 | 命令 |
| --- | --- |
| 检查环境与配置 | `weflow-cli check` · `weflow-cli config show` |
| 初始化或手动指定路径 | `weflow-cli init [--path <目录>]` |
| 浏览聊天数据 | `weflow-cli sessions` · `weflow-cli messages <联系人>` · `weflow-cli contacts` |
| 谁在等我回话 | `weflow-cli awaiting --dry-run` · `weflow-cli awaiting --yes` |
| 本机判断层 | `weflow-cli decide --request <file> --dry-run` · `--yes` |
| 导出聊天记录 | `weflow-cli export <联系人> <json\|txt\|html\|excel>` |
| 同步检查点 | `weflow-cli sync run <联系人> --since <日期>` · `sync status` · `sync verify` |
| 帮我起草回复（只出文字，不发送） | `weflow-cli draft <联系人> --dry-run` · `--yes` |
| 公众号日报与阅读器 | `weflow-cli daily` · `weflow-cli daily-server` · `weflow-cli review` |
| 朋友圈缓存 | `weflow-cli sns timeline` · `weflow-cli sns users` · `weflow-cli sns stats` |
| 微信收藏 | `weflow-cli fav list` · `weflow-cli fav export markdown` · `weflow-cli fav set-key` |
| 微信读书 | `weflow-cli weread shelf` · `notes` · `search` · `stats` |
| 知识库 | `weflow-cli vault` · `weflow-cli wiki` · `weflow-cli search <query>` · `weflow-cli chat`（语义检索需先 `config set dashscopeApiKey`）|
| 总结与任务 | `weflow-cli report` · `annual-report` · `todos` |
| 第二大脑助手 | `weflow-cli assistant start` · `status` · `log` · `stop` |
| 本机面板 | `weflow-cli panel`（悬浮窗；`--status` 只看状态，`--ask "…"` 在命令行里问）|
| AI 编辑器集成 | `weflow-cli mcp-config` |

运行 `weflow-cli <命令> --help` 可以查看某个命令的完整参数。例如：

```powershell
weflow-cli export --help
weflow-cli daily --help
```

## 从源码运行

```powershell
git clone https://github.com/zhuobichen/weflow-cli.git
cd weflow-cli
npm install
npm run build
npm run dev -- check
npm run dev -- init
```

主要 Python 工作流：

```powershell
# 文章抓取 -> AI 分类 -> HTML 阅读页 -> Wiki / 学习日报
python scripts/pipeline.py --date YYYY-MM-DD --api-key "你的 API Key" --engine deepseek

# 分步运行
python scripts/biz_daily.py --date YYYY-MM-DD --api-key "你的 API Key"
python scripts/generate_html.py --date YYYY-MM-DD
python scripts/fav_server.py --date YYYY-MM-DD
```

## 架构

![WeFlow CLI architecture](./docs/images/weflow-architecture-gpt-image-2.png)

项目分为四个边界清晰的部分：

| 目录 | 职责 |
| --- | --- |
| `bin/` | Commander CLI 入口与交互流程。 |
| `src/core/` | 微信数据目录发现、密钥、NT/WCDB/SQLCipher 数据库访问。 |
| `src/services/` | 聊天、联系人、导出、配置、消息通道等业务能力。 |
| `scripts/` | 公众号日报、阅读器、知识管道、报告和搜索等 Python 工作流。 |
| `mcp-server/` | 供 MCP 客户端调用的 stdio 服务。 |

查看更完整的模块关系与数据流，请阅读 [ARCHITECTURE.md](./ARCHITECTURE.md)。

## 隐私与安全

- 数据库、密钥配置和导出文件默认保留在本机；密钥字段以机器绑定的加密形式存储。
- 涉及 DeepSeek、公众号抓取、微信读书或 MCP 的联网请求仅在你执行相应工作流时发生。
- 使用 `whitelist`、`blacklist` 和 `audit` 管理或审计消息发送；发送前请确认目标联系人与内容。
- 升级微信、切换账号或迁移电脑后，可能需要重新初始化或扫描 NT 密钥。
- `init` 会先验证已有本地配置；验证通过时不再重复捕获密钥。仅在迁移、切换账号或访问失败后使用 `weflow-cli init --refresh`。
- 测试首次初始化或密钥失效时，优先使用 `weflow-cli init --test-missing-keys`；它只在本次运行模拟密钥缺失，不改动已保存配置。实际密钥失效时可使用 `weflow-cli config forget-keys`，该命令仅清除数据库访问密钥并要求确认。
- 不指定日期运行 `weflow-cli daily` 时，会先检查昨天的日报产物；昨天缺少 `README.md`、文章索引或 `index.html` 时，会先补齐昨天，成功后才生成今天。指定 `--date` 或使用 `--dry-run` 时不执行补日报。

## 文档与反馈

- [操作与排障手册](./OPERATIONS.md)：初始化、NT 密钥、环境问题和常见错误。
- [跨电脑部署指南](./docs/SETUP.md)：从新 Windows 电脑完成完整安装。
- [MCP 集成指南](./docs/MCP.md)：客户端配置、工具范围与安全边界。
- [详细架构说明](./ARCHITECTURE.md)：模块、数据流与实现边界。
- [项目维护状态](./docs/PROJECT_STATE.md)：当前能力、限制、优先级与交接入口。
- [技术决策记录](./docs/DECISIONS.md)：长期维护中的关键设计取舍。
- [分支与合并记录](./docs/BRANCHES.md)：分支用途、合并时间线与并行开发的对齐约定。
- [发布清单](./docs/RELEASING.md)：版本号、打包敏感内容检查、npm 凭据、国内镜像同步。
- [npm 发布速查](./docs/NPM-PUBLISH.md)：404 / 409 / ETARGET 是失败还是在传播，按症状查表。
- [定期体检](./docs/HEALTH-CHECK.md)：一条命令查出版本漂移、分片漏读、会话不更新与导出失败。
- [电子数据证据指南](./docs/EVIDENCE_GUIDE.md)：证据保全、法律边界与使用注意事项。
- [贡献指南](./CONTRIBUTING.md) 与 [安全策略](./SECURITY.md)：开发、反馈和敏感问题处理。
- [变更记录](./CHANGELOG.md)：版本更新摘要。
- [GitHub Issues](https://github.com/zhuobichen/weflow-cli/issues)：请附上脱敏后的命令输出、操作系统、微信版本和复现步骤。

## 致谢与许可

项目借鉴或使用了 [WeFlow](https://github.com/hicccc77/WeFlow)、[koffi](https://koffi.dev/)、[ExcelJS](https://github.com/exceljs/exceljs)、[Scrapling](https://github.com/D4Vinci/Scrapling) 等优秀项目。

采用 [MIT License](./LICENSE) 发布。
