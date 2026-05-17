<div align="center">

<img src="./logo.png" width="320" alt="weflow-cli Logo" />

# WeFlow CLI

**本地微信聊天记录命令行工具**

> 解密 · 查询 · 导出 · 公众号日报 · AI 摘要 · 概念图谱 · 消费报告

[![GitHub License][license-shield]][license-url]
[![Node.js Version][node-shield]][node-url]

</div>

<br />

## ✨ 主要特性

<div align="center">
  <table>
    <tr>
      <td align="center"><strong>💬 聊天管理</strong></td>
      <td align="center"><strong>📰 公众号日报</strong></td>
      <td align="center"><strong>🧠 AI 分析</strong></td>
    </tr>
    <tr>
      <td align="center">多格式导出（JSON/HTML/Excel/Markdown），图片内嵌</td>
      <td align="center">自动抓取今日推送，DeepSeek 摘要 + 五主题分类</td>
      <td align="center">概念图谱 · 行动建议 · 聊天月报 · 学习日报</td>
    </tr>
    <tr>
      <td align="center"><strong>📊 消费统计</strong></td>
      <td align="center"><strong>🔗 Obsidian 集成</strong></td>
      <td align="center"><strong>🤖 MCP Server</strong></td>
    </tr>
    <tr>
      <td align="center">微信支付 + 转账汇总，按日/周/月报告</td>
      <td align="center">Frontmatter + Wiki Links，Graph View 知识图谱</td>
      <td align="center">AI Agent 直接查询知识库、聊天记录、统计数据</td>
    </tr>
  </table>
</div>

<br />

## 📦 安装

### 前置要求

- **Node.js 18+**
- **Python 3.9+**（NT 数据库解密）
- **微信 PC 版**（已登录运行）

### 快速安装

```bash
# 克隆项目
git clone https://github.com/zhuobichen/weflow-cli.git
cd weflow-cli

# 安装依赖
npm install && npm run build

# Python 依赖（NT 格式必需）
pip install sqlcipher3 zstandard pymem

# 初始化密钥
npm run dev -- init
```

<br />

## 🚀 快速开始

### 1. 初始化

```bash
# 完全退出微信 → 运行 init → 重新登录微信
weflow-cli init
```

### 2. 查看聊天记录

```bash
weflow-cli sessions                   # 会话列表
weflow-cli messages <联系人> -n 20     # 查看消息
weflow-cli contacts -k <关键词>        # 搜索联系人
```

### 3. 导出聊天记录

```bash
weflow-cli export <联系人> html        # HTML（含图片）
weflow-cli export <联系人> json        # JSON
weflow-cli export <联系人> excel       # Excel
```

### 4. 公众号日报

```bash
python scripts/biz_daily.py --api-key <DeepSeek-key>
python scripts/classify_daily.py --api-key <DeepSeek-key> --interest AI
```

### 5. 个人信息消费报告

```bash
python scripts/chat_stats.py --period week           # 周报
python scripts/chat_stats.py --month 2026-04         # 历史月份
```

> ⚠️ **注意**：微信需在本机登录运行，否则查询到的是旧快照数据。

<br />

## 📖 完整文档

- **[操作手册](./OPERATIONS.md)** — 初始化、排查、AI 助手 6 步排查清单
- **[架构文档](./ARCHITECTURE.md)** — 4 层架构设计
- **[开发计划](./plan.md)** — 技术备忘 + 关键路径

<br />

## 📋 命令速查

### 聊天管理

| 命令 | 说明 |
|------|------|
| `weflow-cli init` | 自动检测微信数据目录并提取密钥 |
| `weflow-cli sessions` | 查看所有聊天会话 |
| `weflow-cli messages <联系人> -n 20` | 查看聊天记录 |
| `weflow-cli contacts -k <关键词>` | 搜索联系人 |
| `weflow-cli export <联系人> <json\|html\|txt\|excel>` | 导出聊天记录 |

### 公众号日报 & AI

| 命令 | 说明 |
|------|------|
| `python scripts/biz_daily.py --api-key <key>` | 生成今日公众号日报 |
| `python scripts/classify_daily.py --api-key <key>` | 后处理：广告清洗 + 深度摘要 |
| `python scripts/chat_report.py --month <YYYY-MM>` | 聊天月报（AI 任务分析） |
| `python scripts/generate_review.py --api-key <key>` | AI 学习日报 |

### 知识管理 & Obsidian

| 命令 | 说明 |
|------|------|
| `weflow-cli vault init` | 初始化 Obsidian Vault |
| `weflow-cli wiki compile --api-key <key>` | 编译概念图谱 |
| `weflow-cli pipeline run --api-key <key>` | 一键流水线（日报 → 分类 → 概念） |

### 统计 & 消费

| 命令 | 说明 |
|------|------|
| `python scripts/chat_stats.py --period week` | 微信消费统计（支付 + 转账） |
| `python scripts/chat_stats.py --month YYYY-MM` | 历史月份消费报告 |
| `python scripts/extract_todos.py --talker <联系人>` | 提取聊天待办事项 |

### 消息收发（实验性）

| 命令 | 说明 |
|------|------|
| `weflow-cli login-wechat` | 扫码登录微信消息通道 |
| `weflow-cli send <联系人> <消息>` | 发送消息（需白名单） |
| `weflow-cli listen` | 实时监听消息（需白名单） |
| `weflow-cli whitelist add\|rm` | 白名单管理 |

<br />

## 🏗️ 项目结构

```
weflow-cli/
├── bin/weflow-cli.ts         # CLI 入口（commander）
├── src/
│   ├── core/                  # 数据库核心（SQLCipher/NT/WCDB）
│   ├── services/              # 业务逻辑
│   └── types/                 # TypeScript 类型
├── scripts/                   # Python 脚本
│   ├── biz_daily.py          # 公众号日报
│   ├── classify_daily.py     # 后处理（广告清洗 + 深度摘要）
│   ├── compile_wiki.py       # 概念图谱编译
│   ├── chat_stats.py         # 消费报告（支付 + 转账）
│   ├── extract_todos.py      # 待办提取
│   ├── generate_review.py    # AI 学习日报
│   ├── pipeline.py           # 端到端流水线
│   ├── chat_report.py        # 聊天月报
│   └── nt_decrypt.py         # NT 数据库解密
├── mcp-server/                # MCP Server
├── output/                    # 输出文件（已 gitignore）
└── resources/                 # 原生资源文件
```

[架构图](./weflow-cli架构图.png)

<br />

## 🛡️ 安全机制

| 功能 | 说明 |
|------|------|
| **本地解密** | 密钥 AES-256-GCM 加密，绑定单机 |
| **零网络上传** | 所有数据纯本地处理 |
| **白名单** | 消息收发仅限白名单联系人 |
| **隐私输出** | `output/` 目录已 gitignore，不会提交 |

<br />

## 🎯 常见问题

### 为什么需要微信登录运行？

本地数据库依赖微信进程同步数据，微信未运行时查询到的是旧快照。

### WCDB 初始化失败？

说明 NT 密钥过期或未配置，运行 `weflow-cli init` 重新提取。

### 需要哪些 Python 依赖？

`pip install sqlcipher3 zstandard pymem`，公众号抓取额外需要 `scrapling html2text`。

### 支持哪些微信版本？

微信 3.x（传统路径）和 4.x（xwechat_files NT 路径）均支持。

### 多引擎如何切换？

`weflow-cli config set aiEngine claude`，支持 deepseek / claude / ollama。

<br />

## 💖 致谢

特别感谢以下优秀的开源项目：

- **[WeFlow](https://github.com/hicccc77/WeFlow)** — 原始桌面应用，本项目的设计灵感与原生库来源
- **[sqlcipher3](https://pypi.org/project/sqlcipher3/)** — Python SQLCipher 绑定，NT 数据库解密关键依赖
- **[DeepSeek](https://deepseek.com/)** — AI 摘要、分类、概念提取引擎
- **[koffi](https://koffi.dev/)** — 高性能 Node.js FFI 库
- **[ExcelJS](https://github.com/exceljs/exceljs)** — Excel 导出引擎
- **[obsidian-llm-wiki](https://github.com/danielma/obsidian-llm-wiki)** — 概念图谱编译参考
- **[GZHReader](https://github.com/nishoushun/gzhreader)** — 公众号抓取去重参考

感谢每一位使用和反馈的用户！

<br />

## 📄 许可证

MIT License. 详见 [LICENSE](./LICENSE)。

---

<div align="center">

> 本工具仅供学习研究。请勿用于非法获取、泄露他人隐私信息等违反法律法规的行为。

</div>

<!-- 徽章链接 -->
[license-shield]: https://img.shields.io/github/license/zhuobichen/weflow-cli?style=flat-square
[license-url]: ./LICENSE
[node-shield]: https://img.shields.io/badge/Node.js-18%2B-brightgreen?style=flat-square&logo=node.js
[node-url]: https://nodejs.org
