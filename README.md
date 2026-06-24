
<div align="center">

<h1>WeFlow CLI</h1>

**让 AI 读懂你的微信 — 一键生成公众号日报、导出聊天记录**

[![npm](https://img.shields.io/npm/v/weflow-cli)](https://www.npmjs.com/package/weflow-cli)
![Node](https://img.shields.io/badge/node-18+-green)
![Python](https://img.shields.io/badge/python-3.10+-blue)
![License](https://img.shields.io/badge/license-MIT-yellow)

</div>

---

## 一分钟上手

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

## 你能做什么

| 场景 | 操作 |
|------|------|
| 📰 **公众号日报** | 每天自动抓取所有公众号推送 → AI 分类·摘要·行动建议 |
| 🔍 **知识库搜索** | 搜索所有历史文章（支持 MCP，AI 直接调用） |
| 💬 **聊天记录导出** | 导出任意会话为 HTML/JSON/TXT/Excel |
| 📊 **统计报告** | 聊天月报、年度数字生活报告、待办提取 |
| 📖 **微信读书** | 书架同步、笔记划线、阅读统计、书评浏览 |
| 🧠 **Obsidian 集成** | RAG 问答、语义搜索、知识管道 |

---

## 环境检查

```bash
weflow-cli check    # 一键检测 Python、依赖、数据库
```

---

## 公众号日报流水线

```bash
# 生成今日日报（抓取 + AI 摘要 + 行动建议 + HTML 阅读器）
weflow-cli daily --api-key <key>

# 仅抓取某天
weflow-cli daily --date 2026-06-20 --api-key <key>

# 启动本地阅读器（浏览器浏览 + 收藏 + 笔记）
weflow-cli daily-server --date 2026-06-20
```

---

## MCP Server — 让 AI 直接操作

```bash
# 输出 MCP 配置
weflow-cli mcp-config           # 打印 JSON
weflow-cli mcp-config -o .mcp.json  # 写入文件
```

重启编辑器后，AI 自动获得以下工具：

| 工具 | 用途 |
|------|------|
| `wechat.search_articles` | 按关键词/主题/日期搜索知识库 |
| `wechat.get_daily` | 获取某天的公众号日报 |
| `wechat.get_review` | 获取 AI 学习日报 |
| `wechat.get_stats` | 知识库统计概览 |
| `wechat.get_concepts` | 概念图谱查询 |

---

## 命令速查

```bash
# 初始化
weflow-cli init               # 自动检测微信 + 提取密钥
weflow-cli check              # 环境检查

# MCP
weflow-cli mcp-config         # 输出 MCP 配置

# 公众号日报
weflow-cli daily              # 今天
weflow-cli daily --date D     # 指定日期
weflow-cli daily-server       # 启动阅读器
weflow-cli daily-server --open # 启动并自动打开浏览器

# 聊天
weflow-cli sessions           # 会话列表
weflow-cli messages <昵称>    # 查看消息
weflow-cli export <昵称> html # 导出 HTML

# 报告
weflow-cli report             # 聊天月报
weflow-cli annual-report      # 年度报告

# 微信读书
weflow-cli weread shelf       # 书架
weflow-cli weread stats       # 阅读统计
weflow-cli weread notes       # 笔记划线
weflow-cli weread search <书名>
```

---

## 常见问题

**Q: 需要哪些前置环境？**
A: Node.js 18+、Python 3.10+、微信桌面版已登录。运行 `weflow-cli check` 检查。

**Q: 会泄露隐私吗？**
A: 不会。所有数据纯本地处理，密钥 AES-256-GCM 加密绑定单机。公众号文章仅本地索引。

**Q: 图片不显示？**
A: 文章图片自动下载到 `images/` 目录，HTML 阅读器通过本地服务器代理加载，绕过微信防盗链。

**Q: 如何让 AI 自动操作？**
A: `weflow-cli mcp-config -o .mcp.json`，重启 AI 编辑器即可。

---

## Thanks

- [WeFlow](https://github.com/hicccc77/WeFlow) — 原始桌面应用
- [DeepSeek](https://deepseek.com/) — AI 摘要引擎
- [sqlcipher3](https://pypi.org/project/sqlcipher3/) — 数据库解密

MIT License · [GitHub](https://github.com/zhuobichen/weflow-cli)
