<div align="center">

<img src="./logo.png" width="280" alt="WeFlow CLI logo" />

# 𝗪𝗲𝗙𝗹𝗼𝘄 𝗖𝗟𝗜

**简体中文** · [English](./README.en.md)

<img src="./docs/images/poem-calligraphy.jpg" width="560" alt="今人不见古时月，今月曾经照古人" />

> 让聊天记录、公众号阅读和个人知识工作流回到你的本地电脑。

[![npm](https://img.shields.io/npm/v/weflow-cli)](https://www.npmjs.com/package/weflow-cli)
[![Node.js](https://img.shields.io/badge/Node.js-18%2B-339933)](https://nodejs.org/)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-f4b400)](./LICENSE)

</div>

WeFlow CLI 是面向 Windows 微信用户的本地优先工具：连接微信数据目录，查询和导出聊天记录；把公众号文章整理成可浏览的日报；并可将知识库能力接入 MCP 兼容的 AI 编辑器。

> 本项目仅限于处理你有权访问的数据。请遵守适用法律、微信规则和他人的隐私边界。

## 你可以做什么

| 场景 | 能力 |
| --- | --- |
| 聊天记录 | 查询会话、联系人和消息；导出 JSON、TXT、Markdown、HTML、Excel。 |
| 公众号日报 | 抓取文章、AI 摘要与分类、生成本地阅读页，保留收藏和已读状态。 |
| 个人知识库 | 同步微信读书笔记、构建 Obsidian Vault、语义搜索、RAG 问答和概念 Wiki。 |
| AI 协作 | 通过 MCP 将文章抓取、知识库检索和日报能力交给 Claude Code 等兼容客户端。 |
| 个人回顾 | 聊天月报、年度报告、待办提取与朋友圈本地缓存查询。 |

## 三分钟上手

### 1. 安装与检查

需要 Node.js 18+、Python 3.10+，以及已登录的 Windows 微信。安装后先检查本机环境：

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

**生成当天公众号日报**

```powershell
weflow-cli daily --date 2026-08-12 --api-key "你的 DeepSeek API Key"
weflow-cli daily-server --date 2026-08-12
```

阅读器默认在 `http://localhost:8765/` 提供服务。

**接入 AI 编辑器**

```powershell
weflow-cli mcp-config > .mcp.json
```

将生成的配置放到所用 MCP 客户端的配置位置后重启客户端即可。MCP 的工具列表与配置方式见 `weflow-cli mcp-config` 输出。

## 常用命令

| 目标 | 命令 |
| --- | --- |
| 检查环境与配置 | `weflow-cli check` · `weflow-cli config show` |
| 初始化或手动指定路径 | `weflow-cli init [--path <目录>]` |
| 浏览聊天数据 | `weflow-cli sessions` · `weflow-cli messages <联系人>` · `weflow-cli contacts` |
| 导出聊天记录 | `weflow-cli export <联系人> <json\|txt\|md\|html\|excel>` |
| 公众号日报与阅读器 | `weflow-cli daily` · `weflow-cli daily-server` · `weflow-cli review` |
| 朋友圈缓存 | `weflow-cli sns timeline` · `weflow-cli sns users` · `weflow-cli sns stats` |
| 微信读书 | `weflow-cli weread shelf` · `notes` · `search` · `stats` |
| 知识库 | `weflow-cli vault` · `weflow-cli wiki` · `weflow-cli search <query>` · `weflow-cli chat` |
| 总结与任务 | `weflow-cli report` · `annual-report` · `todos` |
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

![WeFlow CLI architecture](./docs/images/weflow-architecture.png)

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

## 文档与反馈

- [操作与排障手册](./OPERATIONS.md)：初始化、NT 密钥、环境问题和常见错误。
- [跨电脑部署指南](./docs/SETUP.md)：从新 Windows 电脑完成完整安装。
- [MCP 集成指南](./docs/MCP.md)：客户端配置、工具范围与安全边界。
- [详细架构说明](./ARCHITECTURE.md)：模块、数据流与实现边界。
- [贡献指南](./CONTRIBUTING.md) 与 [安全策略](./SECURITY.md)：开发、反馈和敏感问题处理。
- [变更记录](./CHANGELOG.md)：版本更新摘要。
- [GitHub Issues](https://github.com/zhuobichen/weflow-cli/issues)：请附上脱敏后的命令输出、操作系统、微信版本和复现步骤。

## 致谢与许可

项目借鉴或使用了 [WeFlow](https://github.com/hicccc77/WeFlow)、[koffi](https://koffi.dev/)、[ExcelJS](https://github.com/exceljs/exceljs)、[Scrapling](https://github.com/D4Vinci/Scrapling) 等优秀项目。

采用 [MIT License](./LICENSE) 发布。
