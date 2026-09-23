# AI 接口

WeFlow CLI 的 AI 接入分为三层：

1. **能力探测**：先运行 `weflow-cli capabilities --json`，确认当前版本支持的读取、导出和分析入口。
2. **结构化 CLI**：读取型命令优先使用 `--json`，避免解析表格、颜色和自然语言日志。
3. **MCP**：本地 Agent 可使用 stdio MCP；消息数据优先使用 `wechat.export_messages`，返回 `weflow-message/v1`。

默认 MCP 仅提供有界读取和本地转换能力，不提供消息发送、公众号发布、长期记忆写入、待办修改、配置变更或删除。需要副作用的自动化必须改用具备项目自身预览和确认协议的显式 CLI 工作流。
Agent 可先调用 `capabilities --json`，并检查 `safety.mcpDefaultReadOnly`，再决定是否使用 MCP。

## 推荐调用顺序

```powershell
weflow-cli capabilities --json
weflow-cli config show --json
weflow-cli whitelist list --json
weflow-cli blacklist list --json
weflow-cli sessions --json --limit 30
weflow-cli messages "<session-id>" --json --limit 100
weflow-cli check --json
weflow-cli daily-stats --json
weflow-cli daily --no-ai --dry-run --json
weflow-cli daily --no-ai --yes --json
weflow-cli sns timeline --json --limit 20
weflow-cli sns users --json
weflow-cli sns stats --json
weflow-cli daily-server --status --json
weflow-cli evidence "<session-id>" --json --non-interactive --limit 100
weflow-cli evidence-review "<session-id>" --dry-run --json --limit 100
weflow-cli export "<session-id>" json --contract weflow-v1 --output "<local-output>"
```

Add `--json` to an export command when the caller needs a machine-readable operation result. This changes only stdout status reporting, not the selected export file format. The result contains `success`, `format`, `contract`, `path`, and `count`. The `weflow-v1` file additionally includes conservative `coverage` metadata (`requestedFrom`, `requestedTo`, `requestedLimit`, `returned`, `mayHaveMore`, and returned time bounds); it does not expose a stable incremental cursor yet. `--date` applies to every export format using the machine's local calendar day; `--date` cannot be combined with `--from` or `--to`.

Favorite exports use `--json-result` because `json` is already a positional file format: `fav export json --json-result --output <local-file>`. The result reports only status, path, format, and count; exported favorite content remains in the local file.

## Two-phase todo mutations

Agents must preview a todo mutation before asking the user to approve it. The preview is read-only. Execution requires the explicit `--yes` flag; JSON mode without it returns `CONFIRMATION_REQUIRED` and leaves the todo file unchanged.

```powershell
weflow-cli todos done <id> --dry-run --json
weflow-cli todos done <id> --yes --json
weflow-cli todos undone <id> --dry-run --json
weflow-cli todos rm <id> --dry-run --json
```

These todo commands authorize only the previewed todo mutation. Messaging, access control, configuration, assistant lifecycle, publishing, and other side effects require their own explicit protocol.

Access-control changes use the same preview/confirm pattern and return local sensitive identifiers:

```powershell
weflow-cli whitelist add <target> --dry-run --json
weflow-cli whitelist add <target> --yes --json
weflow-cli whitelist rm <session-id> --dry-run --json
weflow-cli blacklist add <target> --dry-run --json
weflow-cli blacklist rm <session-id> --yes --json
```

Message sending is available only through the existing official Bot channel and also requires two phases:

```powershell
weflow-cli send <session-id> <message> --dry-run --json
weflow-cli send <session-id> <message> --yes --json
```

The preview contains message content and local identifiers, so it must remain local. This command cannot initiate a personal-account message or send into an ordinary personal WeChat group.
Image and file previews contain only the base file name and byte size, not the full local path. The selected media must be a readable, non-empty regular file, and `--image` cannot be combined with `--file`.

Starting the assistant may create a background process, handle incoming Bot-channel messages, and consume AI quota. Agents must use `assistant start --dry-run --json` before a user-approved `assistant start --yes --json`. Stopping follows the same protocol. Status remains read-only through `assistant status --json`.

The local panel follows that lifecycle too, with one extra consequence to state: `panel --dry-run --json` reports `willStartDaemon` and `keepsRunningAfterClose` before anything happens, and `panel --yes --json` may start the assistant daemon and open a local window. Closing that window does **not** stop the daemon, so an agent that opened it must not report the assistant as stopped. `panel --status --json` is read-only and does not open a window; it returns the loopback port, the quota used, and the **memory bucket** the panel is writing to. `panel --ask "<text>" --yes --json` sends one real turn through the same endpoint as the window: it consumes quota, may call a cloud model, and must be treated as user-approved work rather than a status check. The panel writes nothing to a command line: the endpoint token is stored in `~/.weflow-cli/assistant_endpoint.json` and must never be echoed, copied into a reply, or passed as an argument.

The local daily reader follows the same lifecycle pattern. `daily-server --status --json` only checks loopback status. Starting it requires `daily-server --dry-run --json` followed by user-approved `daily-server --yes --json`; the confirmed JSON startup waits for the loopback service to report the requested date and returns a failure code if the date output is missing or the process does not become ready. Add `--open` only when the user also wants a browser window opened. Human-oriented non-JSON startup remains compatible. The older `fav-server` command is a compatibility entry and applies the same machine-mode confirmation rule.

Message-channel authentication is human-gated. Agents may inspect `login-wechat --dry-run --json`, but actual login uses `login-wechat --yes` in an interactive terminal because the user must scan a QR code. `login-wechat --yes --json` returns `INTERACTIVE_REQUIRED` without starting login. Logout uses `logout-wechat --dry-run --json` followed by user-approved `logout-wechat --yes --json`.

Initialization is also human-gated because it may verify private databases, search local disks, capture keys, and update encrypted configuration. Agents may call `init --dry-run --json`; `init --json` returns `INTERACTIVE_REQUIRED` without scanning or writing. Actual initialization remains the interactive `init` command.

朋友圈密钥捕获同样需要用户在微信客户端中触发。Agent 只能调用 `sns capture-key --dry-run --json` 查看要求；实际捕获使用交互终端中的 `sns capture-key --yes`，JSON 执行会返回 `INTERACTIVE_REQUIRED`，不会扫描进程。

`listen` 和 `assistant run` 会在当前终端持续运行并处理消息内容，因此也属于人工前台流程。Agent 可调用对应的 `--dry-run --json` 预览，但实际启动必须使用非 JSON 的 `--yes`，常驻自动化应优先使用已确认的 `assistant start --yes --json`。

Vault Git synchronization also uses two phases because it commits local files and pushes them to a remote repository:

```powershell
weflow-cli vault sync --dry-run --json
weflow-cli vault sync --yes --json
```

The preview reports only the number of changed file states and whether the Vault is already a Git repository. It does not return file names or the remote URL.

Vault initialization can create directories and overwrite its four managed template files. Agents must inspect the overwrite count before execution:

```powershell
weflow-cli vault init --path <local-directory> --dry-run --json
weflow-cli vault init --path <local-directory> --yes --json
```

Knowledge indexing, pipelines, and generated reports also use two phases. Their previews do not read chat data, call AI services, or write files:

```powershell
weflow-cli search-index --dry-run --json
weflow-cli search-index --yes --json
weflow-cli pipeline run --no-ai --source <source-name> --dry-run --json
weflow-cli pipeline run --no-ai --source <source-name> --yes --json
weflow-cli report --no-ai --dry-run --json
weflow-cli annual-report YYYY --skip-ai --dry-run --json
weflow-cli review --dry-run --json
weflow-cli evidence-review "<session-id>" --dry-run --json
```

Remove `--no-ai` or `--skip-ai` only after the user approves sending the described content to the configured provider. `search-index` always sends collected text to the configured embedding provider. After approval, `evidence-review` uses `--yes --json`; cloud processing additionally requires `--allow-cloud`. JSON results omit selected conversation names, report content, and local output paths.

Vault enrichment, reading-note creation, AI tagging, WeRead synchronization, promotion workflows, consumption statistics, and daily-favorite changes follow the same rule:

```powershell
weflow-cli vault enrich --date YYYY-MM-DD --dry-run --json
weflow-cli vault notes --date YYYY-MM-DD --dry-run --json
weflow-cli vault tag --date YYYY-MM-DD --dry-run --json
weflow-cli vault sync-weread --dry-run --json
weflow-cli vault promote ideas --dry-run --json
weflow-cli wiki compile --dry-run --json
weflow-cli chat-stats --dry-run --json
weflow-cli todos extract --days 7 --dry-run --json
weflow-cli daily favorites add <article> --dry-run --json
```

After approval, replace `--dry-run` with `--yes`. Multiple daily-favorite articles use repeated `--article <path>`. Preview and result JSON report counts and behavior only, not article names, book names, report data, or paths.

Writing an MCP client configuration follows the same protocol. Reading the generated configuration needs no confirmation; writing a file does:

```powershell
weflow-cli mcp-config
weflow-cli mcp-config --output .mcp.json --dry-run --json-result
weflow-cli mcp-config --output .mcp.json --yes --json-result
```

Secrets should be persisted from an existing environment variable so their values do not appear in command arguments:

```powershell
weflow-cli config set-env deepseekApiKey DEEPSEEK_API_KEY --dry-run --json
weflow-cli config set-env deepseekApiKey DEEPSEEK_API_KEY --yes --json
weflow-cli fav set-key --from-env WEFLOW_FAV_KEY --dry-run --json
weflow-cli fav set-key --from-env WEFLOW_FAV_KEY --yes --json
```

Human-oriented configuration commands keep their direct behavior. JSON configuration writes require explicit `--yes`; their preview and result contain the key and environment-variable name but never the value.

Bulk deletion is destructive and follows the same two-phase protocol: preview `config clear`, `whitelist clear`, `blacklist clear`, or `audit clear` with `--dry-run --json`, then execute the approved command with `--yes --json`. Previews return only counts or impact flags, never entries or log content. A JSON-mode call without confirmation returns `CONFIRMATION_REQUIRED`. Individual whitelist and blacklist additions or removals also require `--yes` in JSON mode.

Database-key reset follows the same rule: preview with `config forget-keys --dry-run --json`; `config forget-keys --json` returns `CONFIRMATION_REQUIRED`; only a user-approved `config forget-keys --yes --json` clears saved database access keys. `config show --json` is intentionally redacted and reports status flags and counts rather than paths, account identifiers, or secret values. Access-list JSON contains local identifiers and must remain local.

Process-memory key capture is human-gated. An Agent may inspect `dbkey --force --dry-run --json`, but JSON execution returns `INTERACTIVE_REQUIRED`; actual capture requires `dbkey --force --yes` in an interactive terminal. `scan --json` reports only whether candidates exist and their count. `assistant log --json` reports log availability and line counts without returning log text.

Vault RAG can send selected local knowledge to the configured AI provider, so it also uses two phases:

```powershell
weflow-cli vault rag "<question>" --dry-run --json
weflow-cli vault rag "<question>" --yes --json
```

The preview does not read the Vault or call AI and does not echo the question. Execution passes the question to the bundled worker without placing it in child-process arguments. `todos remind --json` is the structured read form of local todo reminders.

Semantic search and chat-backed RAG also require explicit approval because they may send a query or selected local context to cloud services:

```powershell
weflow-cli search "<query>" --dry-run --json
weflow-cli search "<query>" --yes --json
weflow-cli chat "<question>" --talker "<session-id>" --dry-run --json
weflow-cli chat "<question>" --talker "<session-id>" --yes --json
```

Previews omit the query, question, and conversation restriction. These values are inherited by the Python worker rather than placed in process arguments. `chat --yes --json` without a question returns `INTERACTIVE_REQUIRED`; interactive chat must be started by a person in a terminal.

AI 应优先使用会话 ID，不应依赖昵称猜测。`messages --json` 默认采用非交互解析；`messages`、`export` 和 `evidence` 也支持显式 `--non-interactive`，名称匹配不唯一时会返回错误而不是等待人工选择。
`messages --start` and `--end` accept Unix timestamps in seconds and are applied before offset and limit. Core read pagination rejects negative, fractional, nonnumeric, or excessive values with `INVALID_ARGUMENT`.

## 数据与权限

- 读取和导出默认在本地完成，不自动调用 AI。
- `daily --no-ai` 可生成不含 AI 处理的日报。
- `daily --json` 需要用户批准后的 `--yes`，将流水线日志写到 stderr，只在 stdout 输出最终状态，并逐项报告日报、文章索引和 HTML 阅读器产物是否完整。
- `evidence-review`、`report`、`review`、`annual-report`、`search-index`、`pipeline`、`chat` 和其他分析任务需要显式选择 AI，并遵守隐私设置。报告、索引和流水线的 JSON 写操作还需要 `--yes`；预览不会读取本地内容或调用 AI。
- `send`、配置修改、密钥捕获、发布和删除属于副作用操作，AI 不应在没有用户确认时执行。
- 任何下游项目都不得接收数据库路径、解密密钥、完整配置或无限制文件系统权限。

## 当前边界

并非所有命令都已经提供 JSON 输出。目前已结构化的基础入口包括能力探测、脱敏配置状态、访问控制列表、环境检查、会话、消息、联系人、朋友圈时间线与统计、阅读器状态、证据包、收藏、待办、日报频率、Vault 初始化和同步、语义索引、知识流水线及报告生成。尚未结构化的命令应通过 MCP 或现有文本接口临时调用，并将其列为兼容性限制，而不是猜测文本格式。未知消息类型必须保留原始类型码并安全降级为 `other`。
