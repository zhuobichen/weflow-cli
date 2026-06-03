<img src="./logo.png" width="305" align=right />

<div align="center">

<h1>WeFlow CLI <img src="./weflow-cli图标.png" width="80" valign="middle" /></h1>

*本地命令行工具 — 解密微信数据库、导出聊天记录、抓取公众号文章并用 AI 整理为分类日报*

> 飘飘乎如遗世独立，羽化而登仙

![Version](https://img.shields.io/badge/version-1.3.0-blue)
![Node](https://img.shields.io/badge/node-18+-green)
![Python](https://img.shields.io/badge/python-3.10+-blue)
![License](https://img.shields.io/badge/license-MIT-yellow)

</div>

<br clear="all">

---

## 目录

- [✨ 近期更新](#-近期更新)
- [Feature](#feature)
- [Quick Start](#quick-start)
- [Command Reference](#command-reference)
- [Architecture](#architecture)
- [问题反馈](#问题反馈)
- [Thanks](#thanks)
- [License](#license)

---

## ✨ 近期更新

<details>
<summary>v1.3.1 — 消息无限制 + 图片完整提取 + CDN 代理（点击展开）</summary>

- ✨ **去掉消息长度限制** — 导出/查询默认拉取全部消息（limit=0），不再截断
- ✨ **公众号图片完整提取** — `data-src` 懒加载图片识别，图片数量提升 3-4 倍
- ✨ **公众号封面图嵌入** — 解析公众号消息 XML 提取 `thumburl`，远程下载 base64 嵌入 HTML
- ✨ **CDN 图片代理** — `fav_server.py` 新增 `/proxy` 端点，绕过微信防盗链 Referer 检查
- 🔧 **阅读器图片修复** — `article.html` 自动重写 mmbiz 图片 URL 走本地代理

</details>

<details>
<summary>v1.3.0 — 本地阅读器 + 知识管道 + MCP Server（点击展开）</summary>

- ✨ **本地阅读器** — `fav_server.py` 驱动，浏览器阅读、收藏同步、已读追踪、划词 AI 解释
- ✨ **HTML 日报** — `generate_html.py` 自动生成分类展示页面，localStorage + 服务端双持久化
- ✨ **MCP Server** — 公众号文章抓取/搜索/发布、概念图谱、学习日报、知识库统计
- ✨ **Obsidian 集成** — vault 搜索/RAG/反向链接、阅读笔记创建、知识管道 (pipeline)
- ✨ **微信读书集成** — 书架同步、笔记划线、书评浏览、阅读统计
- 🔧 **支付通知过滤** — 自动过滤微信支付/扣费/续费等非文章消息
- 🔧 **TLS 兼容** — 修复 weread.qq.com TLS 1.3 → 1.2 降级

</details>

[完整变更记录 →](https://github.com/zhuobichen/weflow-cli/commits/master)

---

## Feature

| 分类 | 功能 |
|------|------|
| 💬 **聊天** | 解密 3.x/4.x 数据库 · 导出 JSON/TXT/MD/HTML/Excel · 图片 base64 内嵌 · 公众号封面图嵌入 · 联系人昵称映射 |
| 📰 **日报** | 自动抓取公众号文章 · DeepSeek V4 摘要+分类 · 本地阅读器 · 收藏/已读同步 |
| 🧠 **知识** | Obsidian vault RAG · 语义搜索 · 微信读书同步 · 概念 Wiki 编译 |
| 🔌 **MCP** | 微信公众号抓取/发布 · 知识库搜索 · 学习日报 · Claude Code 集成 |
| 🔒 **隐私** | 密钥 AES-256-GCM 加密绑定单机 · 纯本地运行 · 零网络上传 |

---

## Quick Start

> 前置：Node.js 18+ / Python 3.10+ / 微信已登录

**1. 安装**

```bash
git clone https://github.com/zhuobichen/weflow-cli.git
cd weflow-cli
npm install && npm run build
pip install sqlcipher3 zstandard scrapling html2text
```

**2. 初始化密钥**

```bash
npm run dev -- init
```

**3. 开始使用**

```bash
npm run dev -- sessions              # 聊天列表
npm run dev -- messages <昵称>       # 查看消息
npm run dev -- export <昵称> html    # 导出 HTML
```

**4. 公众号日报**

```bash
python scripts/biz_daily.py --date YYYY-MM-DD           # 抓取+AI摘要
python scripts/generate_html.py --date YYYY-MM-DD       # 生成 HTML 页面
python scripts/fav_server.py --date YYYY-MM-DD          # 启动阅读器 → http://localhost:8765
```

> ⚠️ **DeepSeek API key**：通过 `--api-key` 传入或设环境变量 `DEEPSEEK_API_KEY`

---

## Command Reference

### CLI 命令（TypeScript）

| 命令 | 说明 |
|------|------|
| `npm run dev -- init` | 自动检测微信数据目录并提取密钥 |
| `npm run dev -- sessions` | 查看所有聊天会话（含昵称） |
| `npm run dev -- messages <昵称\|wxid\|序号>` | 查看聊天记录 |
| `npm run dev -- export <昵称> <json\|html\|txt\|excel>` | 导出聊天记录（默认全部消息，无长度限制） |
| `npm run dev -- contacts [-k 关键词]` | 查看联系人列表 |
| `npm run dev -- whitelist add\|rm\|clear` | 白名单管理 |
| `npm run dev -- report --month <YYYY-MM> --talker <昵称>` | 生成聊天月报（AI 分析） |

### Python 脚本

| 命令 | 说明 |
|------|------|
| `python scripts/biz_daily.py --date YYYY-MM-DD` | 抓取公众号 + AI 摘要分类 |
| `python scripts/generate_html.py --date YYYY-MM-DD` | 生成日报 HTML 页面 |
| `python scripts/fav_server.py --date YYYY-MM-DD` | 启动本地阅读器 |
| `python scripts/classify_daily.py --interest AI` | 后处理：广告清洗 + 深度摘要 |
| `python scripts/pipeline.py --date YYYY-MM-DD` | 完整知识管道（日报→Wiki→Review） |

---

## Architecture

<img src="./weflow-cli架构图.png" width="100%" alt="weflow-cli 架构图" />

详见 [ARCHITECTURE.md](./ARCHITECTURE.md)

---

## 问题反馈

- **[GitHub Issues](https://github.com/zhuobichen/weflow-cli/issues)** — Bug 报告 / 功能请求

提交时建议附上：操作命令、错误截图、微信版本和操作系统。

---

## Thanks

| 项目 | 用途 |
|------|------|
| [WeFlow](https://github.com/hicccc77/WeFlow) | 原始桌面应用，设计灵感与原生库来源 |
| [sqlcipher3](https://pypi.org/project/sqlcipher3/) | Python SQLCipher 绑定，NT 数据库解密 |
| [DeepSeek](https://deepseek.com/) | AI 摘要与主题分类引擎 |
| [Scrapling](https://github.com/D4Vinci/Scrapling) | 公众号文章反反爬抓取 |
| [koffi](https://koffi.dev/) | 高性能 Node.js FFI，微信进程密钥提取 |
| [ExcelJS](https://github.com/exceljs/exceljs) | Excel 导出引擎 |

---

## License

MIT License. See [LICENSE](./LICENSE) for details.

> 本工具仅供学习研究。请勿用于非法获取、泄露他人隐私信息等行为。
