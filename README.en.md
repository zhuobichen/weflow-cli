<div align="center">

<h1>𝗪𝗲𝗙𝗹𝗼𝘄 𝗖𝗟𝗜 <img src="./logo.png" width="280" valign="middle" alt="WeFlow CLI logo" /></h1>

[简体中文](./README.md) · **English**

*"People today have never seen the moon of ancient times, yet today's moon once shone upon the people of old." — Li Bai*

> Bring your chat history, official-account reading, and personal knowledge workflows back to your own computer.

[![npm](https://img.shields.io/npm/v/weflow-cli)](https://www.npmjs.com/package/weflow-cli)
[![Node.js](https://img.shields.io/badge/Node.js-18%2B-339933)](https://nodejs.org/)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-f4b400)](./LICENSE)

</div>

WeFlow CLI is a local-first tool for Windows WeChat users: it connects to your WeChat data directory to query and export chat history, turns official-account articles into browsable daily digests, and exposes knowledge-base capabilities to MCP-compatible AI editors.

> This project is intended only for data you are authorized to access. Please obey applicable laws, WeChat's terms, and the privacy of others.

## What You Can Do

| Scenario | Capability |
| --- | --- |
| Chat history | Query sessions, contacts, and messages; export to JSON, TXT, Markdown, HTML, Excel. |
| Official-account digest | Crawl articles, AI summarization and classification, generate a local reading page, keep favorites and read states. |
| Personal knowledge base | Sync WeRead notes, build an Obsidian vault, semantic search, RAG Q&A, and a concept wiki. |
| AI collaboration | Expose article crawling, knowledge-base retrieval, and digest capabilities to MCP-compatible clients such as Claude Code. |
| Personal review | Monthly chat reports, annual reports, todo extraction, and local Moments cache queries. |

## Quick Start in Three Minutes

### 1. Install and Check

Requires Node.js 18+, Python 3.10+, and a signed-in Windows WeChat. After installation, check your environment first:

```powershell
npm install -g weflow-cli
weflow-cli check
```

Install the Python dependencies required by the standard Windows 4.x workflow:

```powershell
python -m pip install -r requirements.txt
```

If you are still on WeChat 3.x, also install the optional dependencies: `python -m pip install -r requirements-3x.txt`.

### 2. Initialize Local Data Access

Close WeChat and run the initialization. Follow the prompts to start and sign in to WeChat; the CLI will try to discover the data directory and extract the required keys:

```powershell
weflow-cli init
```

The WeChat "storage location", account directory, and its `db_storage` directory can all be specified directly:

```powershell
weflow-cli init --path "D:\WeChat\xwechat_files"
```

Once initialization finishes, verify that your own data is readable:

```powershell
weflow-cli sessions
weflow-cli contacts -k "keyword"
weflow-cli messages "contact" -n 10
```

For key, database, or Python environment issues, follow the [Operations & Troubleshooting Guide](./OPERATIONS.md) step by step.

### 3. Pick a Workflow

**Export a chat history**

```powershell
weflow-cli export "contact" html --output ./output
```

**Generate the official-account digest for a day**

```powershell
weflow-cli daily --date 2026-08-12 --api-key "Your DeepSeek API Key"
weflow-cli daily-server --date 2026-08-12
```

The reader serves at `http://localhost:8765/` by default.

**Connect an AI editor**

```powershell
weflow-cli mcp-config > .mcp.json
```

Place the generated configuration wherever your MCP client expects it and restart the client. See `weflow-cli mcp-config` output for the tool list and configuration details.

## Common Commands

| Goal | Command |
| --- | --- |
| Check environment & config | `weflow-cli check` · `weflow-cli config show` |
| Initialize or specify paths | `weflow-cli init [--path <dir>]` |
| Browse chat data | `weflow-cli sessions` · `weflow-cli messages <contact>` · `weflow-cli contacts` |
| Export chat history | `weflow-cli export <contact> <json\|txt\|md\|html\|excel>` |
| Digest & reader | `weflow-cli daily` · `weflow-cli daily-server` · `weflow-cli review` |
| Moments cache | `weflow-cli sns timeline` · `weflow-cli sns users` · `weflow-cli sns stats` |
| WeRead | `weflow-cli weread shelf` · `notes` · `search` · `stats` |
| Knowledge base | `weflow-cli vault` · `weflow-cli wiki` · `weflow-cli search <query>` · `weflow-cli chat` |
| Reports & tasks | `weflow-cli report` · `annual-report` · `todos` |
| AI editor integration | `weflow-cli mcp-config` |

Run `weflow-cli <command> --help` for the full options of any command. For example:

```powershell
weflow-cli export --help
weflow-cli daily --help
```

## Running from Source

```powershell
git clone https://github.com/zhuobichen/weflow-cli.git
cd weflow-cli
npm install
npm run build
npm run dev -- check
npm run dev -- init
```

Main Python workflows:

```powershell
# Article crawling -> AI classification -> HTML reading page -> Wiki / learning digest
python scripts/pipeline.py --date YYYY-MM-DD --api-key "Your API Key" --engine deepseek

# Run step by step
python scripts/biz_daily.py --date YYYY-MM-DD --api-key "Your API Key"
python scripts/generate_html.py --date YYYY-MM-DD
python scripts/fav_server.py --date YYYY-MM-DD
```

## Architecture

![WeFlow CLI architecture](./docs/images/weflow-architecture.png)

The project is split into four clearly bounded parts:

| Directory | Responsibility |
| --- | --- |
| `bin/` | Commander CLI entry and interactive flows. |
| `src/core/` | WeChat data directory discovery, keys, NT/WCDB/SQLCipher database access. |
| `src/services/` | Chat, contacts, export, config, message channel, and other business capabilities. |
| `scripts/` | Python workflows: official-account digest, reader, knowledge pipelines, reports, search. |
| `mcp-server/` | stdio server for MCP clients. |

For a more complete view of modules and data flow, read [ARCHITECTURE.md](./ARCHITECTURE.md).

## Privacy & Security

- Databases, key configuration, and exported files stay on your machine by default; key fields are stored machine-bound and encrypted.
- Network requests involving DeepSeek, article crawling, WeRead, or MCP happen only when you run the corresponding workflow.
- Use `whitelist`, `blacklist`, and `audit` to manage or audit message sending; always confirm the target contact and content before sending.
- After upgrading WeChat, switching accounts, or migrating computers, you may need to re-initialize or re-scan NT keys.

## Documentation & Feedback

- [Operations & Troubleshooting Guide](./OPERATIONS.md): initialization, NT keys, environment issues, common errors.
- [Cross-machine Deployment Guide](./docs/SETUP.md): full installation on a new Windows PC.
- [MCP Integration Guide](./docs/MCP.md): client configuration, tool scopes, security boundaries.
- [Detailed Architecture](./ARCHITECTURE.md): modules, data flow, implementation boundaries.
- [Contributing Guide](./CONTRIBUTING.md) and [Security Policy](./SECURITY.md): development, feedback, and sensitive issues.
- [Changelog](./CHANGELOG.md): release summaries.
- [GitHub Issues](https://github.com/zhuobichen/weflow-cli/issues): please attach sanitized command output, OS, WeChat version, and reproduction steps.

## Acknowledgments & License

This project draws on or uses fine projects such as [WeFlow](https://github.com/hicccc77/WeFlow), [koffi](https://koffi.dev/), [ExcelJS](https://github.com/exceljs/exceljs), and [Scrapling](https://github.com/D4Vinci/Scrapling).

Released under the [MIT License](./LICENSE).
