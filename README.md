<!-- Logo 右对齐 -->
<!-- <img src="LOGO_URL" width="305" align=right /> -->

<div align="center">

# WeFlow CLI

*Command-line tool for WeChat chat record query & export*

> 无 GUI，纯终端操作。取出密钥，读取聊天，导出记录，一步到位。

</div>

---

## 📦 最新版本

当前版本：**v1.0.0**

- ✨ **新增** — 支持 4.x NT 格式加密数据库直接解密（sqlcipher3）
- ✨ **新增** — 内存扫描匹配密钥 + NT 数据库自动发现
- ✨ **新增** — `nt_decrypt.py` / `scan_decrypt_4x.py` Python 解密脚本
- 🔧 **优化** — 连接优先级：预解密文件 → 直接解密 → NT 格式 → WCDB API
- 🔧 **优化** — `dbPathService` 优先检测 `xwechat_files` NT 路径
- ✨ **新增** — 支持 3.x 数据库格式，消息类型解析（图片/文件/链接/语音）

[完整 Changelog](https://github.com/zhuobichen/weflow-cli/commits/master)

---

## 👋 Welcome

**English** — WeFlow CLI is a pure command-line tool to access, query, and export WeChat chat records. No GUI, no Electron window — just your terminal.

**中文** — WeFlow CLI 是一个纯命令行工具，用于查看和导出微信聊天记录。无需启动 GUI，直接在终端搞定一切。

---

## ✨ Feature

- **Zero GUI** — 脱离桌面应用，纯终端完成所有操作
- **Auto Key Extraction** — 从微信进程自动提取解密密钥，支持 WeChat 3.x & 4.x
- **NT Database Decryption** — 直接解密 4.x NT 加密数据库，无需预解密文件
- **Multi-format Export** — JSON / TXT / HTML / Excel 四种格式导出
- **Batch Operations** — 支持按关键词过滤、分页查询、时间范围筛选
- **Dual Version Support** — 同时兼容微信 3.x 和 4.x（传统 + NT）数据格式

---

## 🚀 Quick Start

```bash
git clone https://github.com/zhuobichen/weflow-cli.git
cd weflow-cli
npm install && npm run build
npm run dev -- init
```

> **首次使用提示**：确保微信已登录且正在运行，`init` 命令会自动检测数据目录并提取密钥。4.x NT 格式需要安装 Python 依赖：`pip install sqlcipher3 pycryptodome pymem`

📥 [最新 Release](https://github.com/zhuobichen/weflow-cli) &nbsp;|&nbsp; 📖 [使用教程](#-quick-start) &nbsp;|&nbsp; 🐛 [提交 Issue](https://github.com/zhuobichen/weflow-cli/issues)

---

## 🔗 Link

| 文档 | 仓库 | Issues |
|:-:|:-:|:-:|
| [![docs](https://img.shields.io/badge/README-中文-blue)](./README.md) | [![repo](https://img.shields.io/badge/GitHub-weflow--cli-black)](https://github.com/zhuobichen/weflow-cli) | [![issues](https://img.shields.io/badge/GitHub-Issues-orange)](https://github.com/zhuobichen/weflow-cli/issues) |

---

## 🙏 Thanks

- [[WeFlow]](https://github.com/hicccc77/WeFlow) — 原始桌面应用，本项目的设计灵感与原生库来源
- [[sqlcipher3]](https://pypi.org/project/sqlcipher3/) — Python SQLCipher 绑定，NT 数据库解密的关键依赖
- [[koffi]](https://koffi.dev/) — 高性能 Node.js FFI 库
- [[ExcelJS]](https://github.com/exceljs/exceljs) — Excel 导出引擎
- [[fzstd]](https://www.npmjs.com/package/fzstd) — Node.js zstd 解压，处理微信压缩消息

感谢每一位使用和反馈的用户 ❤️

---

## 📄 License

MIT © zhuobichen — 本工具仅供学习研究，请勿用于非法用途。
