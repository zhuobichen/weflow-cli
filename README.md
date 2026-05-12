<!-- <img src="LOGO_URL" width="305" align=right /> -->

<div align="center">

# WeFlow CLI

*Command-line tool for WeChat chat record query & export — pure terminal, no GUI.*

> 无 GUI，纯终端操作。取出密钥，读取聊天，导出记录，一步到位。

</div>

---

## ✨ v1.0.0 — NT Database Decryption

- ✨ **NT 格式支持** — 直接解密 4.x NT 加密数据库（sqlcipher3），支持内存扫描匹配密钥
- ✨ **NT 数据库自动发现** — `xwechat_files` 路径优先检测，自动匹配密钥与数据库
- ✨ **Python 解密脚本** — `nt_decrypt.py` / `scan_decrypt_4x.py` NT 解密与扫描工具
- ✨ **3.x 双模式支持** — 预解密文件读取 + PBKDF2 直接解密，消息类型完整解析
- ✨ **多格式导出** — JSON / TXT / HTML / Excel 四种格式，支持时间范围和关键词过滤
- 🔧 **连接优先级优化** — 预解密文件 → 直接解密 → NT 格式 → WCDB API

[完整变更记录 →](https://github.com/zhuobichen/weflow-cli/commits/master)

---

## Welcome

**WeFlow CLI** is a pure command-line tool to access, query, and export WeChat chat records locally — no GUI, no Electron window, just your terminal. It supports WeChat 3.x and 4.x (NT) data formats with automatic key extraction.

**WeFlow CLI** 是一个纯命令行工具，用于本地查看和导出微信聊天记录。无需打开 GUI，直接在终端完成密钥提取、数据库解密和聊天记录导出。同时兼容微信 3.x 和 4.x（NT）数据格式。

---

## Feature

- **Auto Key Extraction** — 从微信进程自动提取解密密钥，支持 3.x 传统格式和 4.x NT 格式
- **NT Database Decryption** — 直接解密 4.x NT 加密数据库（sqlcipher3），无需预解密文件
- **Multi-format Export** — 支持 JSON / TXT / Markdown / HTML / Excel 导出，含图片 base64 内嵌
- **Batch & Filter** — 按关键词过滤、分页查询、时间范围筛选、批量导出
- **Dual Version Support** — 同时兼容微信 3.x（传统路径）和 4.x（xwechat_files 路径）
- **Zero GUI** — 纯终端交互，所有操作一条命令完成

---

## Quick Start

> 前置要求：Node.js 18+，微信已登录并运行。NT 格式需安装 Python 依赖。

```bash
git clone https://github.com/zhuobichen/weflow-cli.git
cd weflow-cli
npm install && npm run build
```

```bash
npm run dev -- init                    # 自动检测并配置
npm run dev -- sessions               # 查看聊天列表
npm run dev -- messages <wxid>        # 查看聊天记录
npm run dev -- export <wxid>          # 导出为 JSON
npm run dev -- contacts               # 查看联系人
```

> ⚠️ **NT 格式使用前请安装 Python 依赖**：`pip install sqlcipher3 pycryptodome pymem`

---

## Command Reference

| 命令 | 说明 |
|------|------|
| `npm run dev -- init` | 自动检测微信数据目录并提取密钥 |
| `npm run dev -- sessions` | 查看所有聊天会话列表 |
| `npm run dev -- messages <wxid> [-n 20]` | 查看聊天记录（支持分页） |
| `npm run dev -- export <wxid> [--format json\|md\|html]` | 导出聊天记录 |
| `npm run dev -- contacts [--limit 50]` | 查看联系人列表 |
| `npm run dev -- search <关键词>` | 全库搜索消息 |
| `npm run dev -- config show \| set` | 查看/修改配置 |

---

## Architecture

<img src="./weflow-cli架构图.png" width="100%" alt="weflow-cli 架构图" />

---

## Link

| ![docs] | ![repo] | ![issues] |
|:-:|:-:|:-:|
| ![license] | | |

[docs]: https://img.shields.io/badge/Docs-README.md-blue?style=flat-square
[repo]: https://img.shields.io/badge/GitHub-weflow--cli-black?style=flat-square&logo=github
[issues]: https://img.shields.io/badge/GitHub-Issues-orange?style=flat-square
[license]: https://img.shields.io/badge/License-MIT-yellow?style=flat-square

---

## Thanks

- **[WeFlow](https://github.com/hicccc77/WeFlow)** — 原始桌面应用，本项目的设计灵感与原生库来源
- **[sqlcipher3](https://pypi.org/project/sqlcipher3/)** — Python SQLCipher 绑定，NT 数据库解密的关键依赖
- **[koffi](https://koffi.dev/)** — 高性能 Node.js FFI 库，微信进程内存密钥提取
- **[ExcelJS](https://github.com/exceljs/exceljs)** — Excel 导出引擎
- **[fzstd](https://www.npmjs.com/package/fzstd)** — Node.js zstd 解压，处理微信压缩消息
- 感谢每一位使用和反馈的用户

---

## License

MIT License. See [LICENSE](./LICENSE) for details.

> 本工具仅供学习研究。请勿用于非法获取、泄露他人隐私信息等违反法律法规的行为。
