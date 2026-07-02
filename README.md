<img src="./logo.png" width="305" align=right />

<div align="center">

<h1>WeFlow CLI <img src="./weflow-cli图标.png" width="80" valign="middle" /></h1>

*本地命令行工具 — 解密微信数据库、导出聊天记录、抓取公众号文章并用 AI 整理为分类日报*

> 飘飘乎如遗世独立，羽化而登仙

[![npm](https://img.shields.io/npm/v/weflow-cli)](https://www.npmjs.com/package/weflow-cli)
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

<details open>
<summary>v1.5.0 — 暗色模式 + 阅读器优化 + 朋友圈缓存（点击展开）</summary>

- 🌙 **暗色模式** — 文章页 / 列表页支持暗色切换，`J`/`K` 键盘导航浏览文章
- 🎯 **整卡点击进入** — 点击卡片任意位置进入阅读，手型指针，阅读完成自动标记已读
- 🎛️ **按钮布局统一** — 阅读全文 / 标记已读 / 收藏三个按钮等比例设计，紧凑排列
- 🔍 **字体加大** — 全局字号 +2px，容器加宽至 1300px，阅读体验大幅提升
- 📊 **AI 阅读日报** — `generate_review.py` 自动提炼当日公众号内容生成学习日报
- 💬 **朋友圈本地缓存** — 查看本地缓存的朋友圈动态（`sns timeline` / `users` / `stats`）
- 🔧 **模板保护** — 文章模板移至 `.template/` 目录 Git 保护，防止意外覆盖
- 🔧 **Pipeline 修复** — 默认引擎 `local` → `deepseek`，解决全部分类为"学术"的问题
- 🔧 **多引擎支持** — `biz_daily`/`classify_daily` 支持 `--engine` 参数切换 AI 引擎

</details>

<details>
<summary>v1.4.0 — 图片本地化 + 远程保底（点击展开）</summary>

- ✨ **图片本地化** — 公众号文章图片自动下载到 `images/` 目录，微信 CDN 不再丢图
- ✨ **远程优先 + 本地保底** — HTML 阅读器优先加载远程图片，失败时自动切换本地备份
- 🔧 **图片 fallback 机制** — `generate_html.py` 添加 `onerror` 事件，CDN 不可用时无缝降级
- 🔧 **LICENSE 文件** — 补充 MIT 许可证文件

</details>

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

## 一分钟上手（npm）

```bash
# 1. 安装
npm install -g weflow-cli

# 2. 初始化（自动提取微信密钥）
weflow-cli init

# 3. 让 AI 接管
weflow-cli mcp-config > .mcp.json

# 4. 生成今日公众号日报
weflow-cli daily --api-key <你的DeepSeek密钥>
```

重启 AI 编辑器后，你的 AI 助手就可以自动搜索、浏览、分析你的微信知识库。

---

## Feature

| 分类 | 功能 |
|------|------|
| 💬 **聊天** | 解密 3.x/4.x 数据库 · 导出 JSON/TXT/MD/HTML/Excel · 图片 base64 内嵌 · 公众号封面图嵌入 · 联系人昵称映射 · 微信消息收发 |
| 📰 **日报** | 自动抓取公众号文章 · DeepSeek AI 摘要+分类 · 图片本地化 · 远程优先+本地保底 · 本地阅读器 · 收藏/已读同步 · 行动建议 |
| 🧠 **知识** | Obsidian vault RAG · 语义搜索 · 微信读书同步 · 概念 Wiki 编译 · 阅读笔记自动创建 |
| 🔌 **MCP** | 微信公众号抓取/发布 · 知识库搜索 · AI 学习日报 · Claude Code 集成 |
| 💬 **朋友圈** | 本地缓存时间线查看 · 发布者统计 · 关键词搜索 |
| 🔒 **隐私** | 密钥 AES-256-GCM 加密绑定单机 · 纯本地运行 · 零网络上传 |

---

## Quick Start（开发版）

> 前置：Node.js 18+ / Python 3.10+ / 微信已登录

```bash
# 1. 克隆
git clone https://github.com/zhuobichen/weflow-cli.git
cd weflow-cli

# 2. 安装依赖
npm install && npm run build
pip install sqlcipher3 zstandard scrapling html2text

# 3. 初始化密钥
weflow-cli check   # 环境检查
npm run dev -- init # 自动提取密钥

# 4. 开始使用
npm run dev -- sessions              # 聊天列表
npm run dev -- messages <昵称>       # 查看消息
npm run dev -- export <昵称> html    # 导出 HTML
```

### 公众号日报

```bash
# 一键流水线（推荐）：抓取 → AI 摘要分类 → 图片本地化 → HTML → 阅读笔记
python scripts/pipeline.py --date YYYY-MM-DD --api-key <key> --engine deepseek

# 或分步执行
python scripts/biz_daily.py --date YYYY-MM-DD --api-key <key>  # 抓取+AI摘要
python scripts/generate_html.py --date YYYY-MM-DD               # 生成 HTML
python scripts/fav_server.py --date YYYY-MM-DD                  # 启动阅读器

# npm 用户
weflow-cli daily --date YYYY-MM-DD --api-key <key>              # 一键日报

# AI 学习日报（提炼当日核心）
python scripts/generate_review.py --date YYYY-MM-DD --api-key <key>
```

> 💡 **阅读器访问** `http://localhost:8765/`（注意是 `/` 不是 `/index.html`）
>
> 💡 **键盘快捷键**: `J`/`K` 上下导航卡片，`Enter` 打开，`F` 收藏，`M` 标记已读
>
> 💡 **阅读状态按日期隔离**，切换日期不影响其他日期的已读/收藏

---

## Command Reference

### CLI 命令（TypeScript）

| 命令 | 说明 |
|------|------|
| `weflow-cli init` | 自动检测微信数据目录并提取密钥 |
| `weflow-cli check` | 一键环境检查（Python、依赖、数据库） |
| `weflow-cli sessions` | 查看所有聊天会话（含昵称） |
| `weflow-cli messages <昵称\|wxid>` | 查看聊天记录 |
| `weflow-cli export <昵称> <json\|html\|txt\|excel>` | 导出聊天记录（默认全部消息，无长度限制） |
| `weflow-cli contacts [-k 关键词]` | 查看联系人列表 |
| `weflow-cli whitelist add\|rm\|clear` | 白名单管理 |
| `weflow-cli daily [--date YYYY-MM-DD]` | 一键公众号日报（npm 版本） |
| `weflow-cli daily-server [--date YYYY-MM-DD]` | 启动本地阅读器 |
| `weflow-cli review [--date YYYY-MM-DD]` | 生成 AI 学习日报 |
| `weflow-cli report --month YYYY-MM --talker <昵称>` | 聊天月报 |
| `weflow-cli annual-report` | 年度数字生活报告 |
| `weflow-cli todos extract\|list\|done\|undone` | 待办提取与追踪 |
| `weflow-cli sns timeline\|users\|stats` | 朋友圈本地缓存查询 |
| `weflow-cli send <目标> <消息>` | 发送微信消息 |
| `weflow-cli weread shelf\|stats\|notes\|search` | 微信读书助手 |
| `weflow-cli vault wiki\|pipeline\|search\|qna` | Obsidian 知识管道 |
| `weflow-cli mcp-config` | 输出 MCP 配置（AI 编辑器集成） |

### Python 脚本

| 命令 | 说明 |
|------|------|
| `python scripts/pipeline.py --date YYYY-MM-DD --api-key <key>` | 完整知识管道（抓取→分类→Wiki→Review） |
| `python scripts/biz_daily.py --date YYYY-MM-DD --api-key <key>` | 抓取公众号 + AI 摘要分类 + 图片本地化 |
| `python scripts/classify_daily.py --date YYYY-MM-DD --engine deepseek` | 后处理：广告清洗 + 兴趣分类 |
| `python scripts/generate_html.py --date YYYY-MM-DD` | 生成分类展示 HTML 页面 |
| `python scripts/generate_review.py --date YYYY-MM-DD --api-key <key>` | 生成 AI 学习日报 |
| `python scripts/fav_server.py --date YYYY-MM-DD` | 启动本地阅读器（收藏/已读同步） |
| `python scripts/compile_wiki.py` | 编译概念图谱 Wiki 页 |
| `python scripts/chat_report.py --month YYYY-MM` | 聊天月报 |
| `python scripts/semantic_search.py <关键词>` | 语义搜索知识库 |

---

## Architecture

<img src="./weflow-cli架构图.png" width="100%" alt="weflow-cli 架构图" />

```
weflow-cli/
├── bin/weflow-cli.ts       # CLI 入口（commander）
├── src/
│   ├── core/               # 数据库层（SQLCipher / NT / WCDB）
│   │   ├── ntCore.ts       # NT 格式（xwechat_files/*/db_storage/）
│   │   ├── wcdbCore.ts     # WCDB DLL 接口（4.x + SNS 朋友圈）
│   │   └── sqlcipherCore.ts # 纯 SQLite / SQLCipher 直连
│   └── services/           # 业务逻辑（聊天、联系人、配置）
├── scripts/                # Python 脚本
│   ├── biz_daily.py        # 公众号文章抓取 + AI 摘要
│   ├── pipeline.py         # 端到端知识管道
│   ├── fav_server.py       # 本地阅读器服务器
│   ├── generate_html.py    # HTML 日报生成
│   ├── generate_review.py  # AI 学习日报
│   ├── nt_decrypt.py       # NT 数据库解密核心
│   └── ...
├── mcp-server/             # MCP Server（AI 编辑器集成）
└── output/biz-daily/       # 日报输出目录
    ├── .template/          # 文章/列表模板（Git 保护）
    └── YYYY-MM-DD/         # 每日日报 + HTML 阅读器
```

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
