# Project State

> Last reviewed: 2026-09-21 (daily article classification moved to a decision model). This is the current maintenance snapshot, not a release note. Keep it factual and update it with meaningful project changes.

## Purpose

WeFlow CLI is a local-first command-line tool and MCP server for user-authorized WeChat data workflows: local chat queries and exports, official-account daily reading, favorites, knowledge workflows, and an optional local assistant.

## Current Baseline

- Source package version: take it from `package.json` -> `version` rather than from this file - a hardcoded copy drifts silently, and `package-lock.json` must always match it. The npm registry may remain on an earlier version until a maintainer publishes a release.
- Runtime: Node.js 22.13+; Python 3.10+ is required for database and daily-reading workflows. The floor is set by `src/core/sqlcipherCore.ts`, which imports `node:sqlite` at module scope; that builtin exists from 22.5.0 and stops needing `--experimental-sqlite` at 22.13.0.
- Main development command: `npm run dev -- <command>`.
- Compiled/package command: `node cli.cjs <command>`; CLI, MCP, database, export, and assistant services resolve resources from the same package root in both source and compiled layouts.
- Build check: `npm run build`.
- Regression check: `npm test`.
- Downstream data contract: local JSON export is the first compatibility boundary for `she-love-me` and future consumers; see `docs/DATA_CONTRACT.md`.
- Supported focus: Windows WeChat 4.x; Linux WeChat 4.x has an NT database path; macOS requires user-provided local access credentials for initialization.

## Verified Capabilities

| Area | Current state | Main entry points |
| --- | --- | --- |
| Local chat data | Query sessions, contacts, messages, favorites, Moments cache, and exports. HTML chat exports decode NT compressed media records, locally cached WeChat 4.x V2 image containers, entity-escaped emoji XML, and remote emoticons; app cards preserve links and embed available公众号/Bilibili covers. Media matching prefers server-message resources, exact local-message-ID plus timestamp pairs, and a unique local-ID fallback; high-bit encoded image rows are resolved through the same conversation cache. | `sessions`, `contacts`, `messages`, `export`, `fav`, `sns` |
| Initialization | Verify and reuse existing local database access by default; refresh only when needed. Missing-key tests can run without changing saved configuration. | `init`, `init --refresh`, `init --test-missing-keys`, `config forget-keys`, `check` |
| Official-account daily | Filter configured sources, preserve source categories, backfill incomplete yesterday output before an unqualified today run, fetch articles, classify them, create summaries, generate a local HTML reader, and synchronize reader favorites into local files. Topic (6-way), relevance (3-level) and "does this belong in today's report" (yes/no) are decided by TypeSafe's Jev when `typesafeApiKey` is configured - concurrently, before the serial summary loop (12 articles in 3.3s at 6 workers against ~12s one at a time), and by the previous LLM-parse path otherwise; `--classifier llm` forces the latter. The report's admission rule is applied on both loader paths and can be bypassed with `--include-all`. | `daily`, `daily favorites`, `daily-stats`, `daily-server` |
| Knowledge workflows | Wiki compilation, semantic search (two-stage: similarity, then a decision-model rerank over the top 20 candidates; `--no-rerank` reverts), RAG, WeRead sync, reviews, reading notes, and staged Vault promotion. | `wiki`, `vault`, `search`, `chat`, `weread`, `review` |
| Incremental read | `sync run` reads a time window, deduplicates against the previous checkpoint and writes coverage state; `sync status` reports it without touching the database; `sync verify` re-reads the recorded window and compares bounds and counts. Every run reports `coverage` as `complete`, `unverified` or `partial`, and a partial run never advances `lastSuccessfulRun`. The window lower bound is offered to the NT backend as a pushdown hint (`messages --from`), but `syncService` still applies the window itself, so backends without range support stay correct - the pushdown is asymptotic, and it measured no faster on a 925-message conversation. It does **not** offer a stable cursor - see D-027. | `sync run`, `sync status`, `sync verify` |
| Media coverage | HTML export writes `<prefix>_media.json` with a status and reason per media item, and reports the remote-fetch budget it used. A media miss is recorded rather than shown as a bare placeholder. | `export <talker> html` |
| MCP | Local stdio MCP server exposes bounded read and local transformation tools, including the `wechat.export_messages` versioned message contract. Publishing, messaging, memory writes, todo mutation, configuration changes, and deletion are excluded. | `mcp-config`, `mcp-server/index.ts` |
| Assistant | Optional WeChat Bot-channel assistant with local memory and privacy gates. | `login-wechat`, `assistant` |

The 117-test TypeScript regression suite covers home-path expansion, custom NT data-root discovery, bounded daily-favorites synchronization, confirmed no-AI daily generation, no-AI Vault promotion, Vault content mutation previews, Vault initialization and synchronization confirmation, MCP configuration write confirmation, source/compiled Python resource lookup, outbound PII redaction, local-inference bypass, strict message-body masking, MCP path/date/URL validation and live tool discovery, message-contract preservation, local-date export bounds, export result metadata, evidence-package safety, assistant routing and lifecycle confirmation, message-channel authentication confirmation, todo and access-control mutation confirmation, message-send preview/confirmation, local-reader port validation, redacted configuration status, access-list JSON, database-key reset confirmation, human-gated initialization and key capture, content-free account-scan and assistant-log diagnostics, confirmed Vault RAG, confirmed semantic search and RAG chat, structured todo reminders, strict knowledge limits, report previews, and cover-image signature validation. Core automation entry points now include `capabilities --json`, `config show --json`, `whitelist list --json`, `blacklist list --json`, `check --json`, `init --dry-run --json`, `scan --json`, `dbkey --dry-run --json`, `sessions --json`, `messages --json`, `contacts --json`, `export ... --json`, `fav export ... --json-result`, `sns ... --json`, `daily-stats --json`, `daily --no-ai --dry-run --json`, `daily-server --status --json`, `assistant status --json`, `assistant log --json`, `todos list --json`, `todos remind --json`, `evidence --json`, `sync run --dry-run --json`, `sync run --yes --json`, `sync status --json`, `sync verify --json`, `evidence-review --dry-run --json`, Vault mutation previews, confirmed `vault rag`, confirmed `search` and `chat`, daily-favorite previews, `search-index --dry-run --json`, `pipeline run --dry-run --json`, report-generator previews, login/logout previews, and `mcp-config --output <file> --dry-run --json-result`. CI runs three jobs: `node` (build plus the TypeScript suite on Node 22.13), `python` (the Python suites on 3.11), and `release-consistency` (package.json and package-lock.json version agreement, plus a check that the npm package carries no outputs, models, databases or keys). The jobs are separate because a Node failure previously stopped the Python tests from running at all, which let them fail unnoticed.

The daily workflow supports `dailyAiEnabled=false` for a persistent no-AI mode, or `daily --no-ai` for a single run. Both the CLI and direct Python entry points honor the setting. Fetching, HTML generation, and local indexes remain available in that mode. Machine execution requires `daily --yes --json`; it keeps progress logs on stderr and returns a machine-readable completion result on stdout after checking the required local artifacts.

Date-bounded message exports and `wechat.export_messages` page through the selected conversation before applying the result limit. Older matching dates are therefore not hidden by a newer, nonmatching first page. Versioned envelopes report requested bounds, returned count, and conservative `mayHaveMore` metadata. `sync` adds a local checkpoint on top of the same overlapping-window approach, but **a stable incremental cursor is still not exposed** - it must be supported by every relevant database backend before it is advertised (D-027), and `capabilities --json` reports `stableCursor: false` for both.

## Security Baseline

- Process only data the user owns or is explicitly authorized to access.
- Sensitive configuration fields are encrypted at rest with machine- and user-bound AES-256-GCM.
- The daily reader binds to `127.0.0.1`; its API rejects cross-origin mutations, validates local paths, and restricts image proxy requests.
- The assistant denies all senders until `assistantWhitelist` is explicitly configured. Group routing is experimental and remains denied unless the upstream explicitly supplies group metadata, the group and sender are both allowlisted, and the bot is mentioned. New or incomplete configurations use `strict` privacy mode for cloud inference.
- MCP path inputs are constrained to their expected data roots. MCP clients remain trusted local integrations and must be reviewed before configuration.
- The default MCP tool list is read-only: it excludes publishing, messaging, assistant-memory writes, todo mutation, configuration changes, and deletion. Conversation display names must resolve uniquely before chat data is read.
- Public reports and commits must not contain databases, keys, tokens, wxid values, real chat content, or unredacted logs.
- Todo status changes and deletion use a preview/confirm protocol. Machine callers receive `CONFIRMATION_REQUIRED` unless the user-approved execution includes `--yes`.
- AI and database credentials, selected-conversation identifiers, private queries and filters, export locations and display names, and NT scan roots are inherited by workers through environment variables and are not copied into child-process command arguments. Long-lived worker environments clear unrelated internal values before startup.
- `config set-env` and `fav set-key --from-env` provide command-history-safe secret input. Their JSON workflows require a read-only preview followed by explicit `--yes`, and never return secret values or discovered database paths. Bulk clearing of configuration, access lists, or audit history requires interactive confirmation or explicit `--yes`.
- Core JSON reads validate pagination before database access. `messages --start/--end` now applies its Unix-second time range before offset and limit instead of silently ignoring the options.
- `scan --json` and `assistant log --json` return metadata only. They intentionally omit local paths, account identifiers, nicknames, and log content. `evidence-review` requires preview and confirmation, and its machine result omits both analysis text and the output path.
- `init --dry-run --json` exposes a content-free plan. JSON execution returns `INTERACTIVE_REQUIRED`; database verification, directory discovery, key capture, and configuration writes remain an explicit terminal workflow.
- `vault rag` requires a read-only preview and explicit confirmation before reading local knowledge or calling the configured AI provider. The question is inherited by the Python worker through the environment rather than copied into process arguments.
- `search` and `chat` use the same preview/confirmation boundary. Queries, questions, and optional conversation restrictions are inherited through the worker environment instead of appearing in process arguments; machine callers cannot start interactive RAG chat.
- `daily-server --status --json` remains read-only. Machine startup uses `daily-server --dry-run --json` followed by `daily-server --yes --json`; the confirmed process is detached and remains bound to loopback. The legacy `fav-server` compatibility entry enforces the same preview and confirmation rules.

Documentation was synchronized with the current source baseline on 2026-09-19. Command behavior is defined by `bin/weflow-cli.ts`; detached reader startup waits for service readiness, and release packages can lag behind the GitHub source until published.

## Active Constraints

- Issue #7 remains open pending reporter verification. HTML emoji export preserves the emoji label and matches resource records only by nonzero server-message identity; local IDs are not unique across shards or conversations. WeChat 4.x V2 image containers are decoded with an account-specific media key derived from local `kvcomm` data and verified against a real cached V2 header. Remote emoticons use AES-CBC with the message key as both key and IV. Entity-escaped emoji XML is normalized before extracting MD5 and media URLs; failed encrypted downloads fall back across available thumbnail/CDN fields. Signature-only default `[打脸]` messages use the bundled official Facepalm asset when no message-specific resource is available. Forwarded app cards remain structured links with available covers, and Bilibili share pages can resolve covers through their BV metadata endpoint when no `og:image` is present. Forty-two synthetic Python regression tests (`export_chat_media_test.py`, `export_chat_media_report_test.py`) cover these paths; run `python -m unittest discover -s test -p '*_test.py' -v`. CI also runs these tests. This does not establish compatibility with every real WeChat media format.

- WeChat platform behavior, database formats, account restrictions, and terms can change without notice. Local operation is not a legal, account-safety, or platform-compatibility guarantee.
- The daily workflow can take substantial time when many configured sources publish on the same day. It fetches article bodies sequentially to reduce upstream pressure.
- The OC Bot channel is separate from a personal WeChat message stream. `send` can only reply through an already established Bot-channel conversation with a valid context token; it cannot initiate messages to existing personal contacts, post as the user's personal account, or send into ordinary personal WeChat groups.
- The current official OC/iLink payload model has not been verified to support group events or group invitations. The project does not implement client automation or protocol bypasses to add a bot to groups.
- Cloud AI workflows may transmit user-selected, privacy-filtered input to the configured provider. Local inference avoids that network transfer.
- `vault promote ideas` and `vault promote all` run without AI by default. AI generation requires explicit `--with-ai` and an API key. CLI execution also requires preview and confirmation. `vault init` creates both the existing article-sync folders and the structured directories required by promotion workflows.
- `vault init`, semantic-index construction, the knowledge pipeline, and report generators require preview and confirmation for machine execution. Preview JSON contains counts and behavior flags but omits local paths, conversation names, and generated content.
- Automatic data-directory discovery checks common user locations only. Cross-drive name search requires `init --search-drives`; structural disk search requires `init --full-scan`. Both can be slow on large, removable, or network-attached volumes.
- Dependency audit findings must be reviewed before dependency upgrades; do not run breaking `npm audit fix --force` without validation.
- The WCDB native query entry point has no parameter-binding ABI. Calls that supply parameters are rejected rather than falling back to interpolated SQL; existing internal callers are unchanged.
- The daily report classifies article topic and relevance by calling a decision model (`api.typesafe.ai`) when `typesafeApiKey` is set, so article titles and bodies leave the machine. Article text already went to the configured LLM for summarisation, so this is an additional vendor rather than an additional category of data; `daily --no-ai` is the way to keep everything local. The switch was made on the strength of a 60-article comparison against the stored labels, **not against a gold standard** - the stored labels are the previous model's own output, and a hand-labelled sample is still owed. `--classifier llm` reverts in one flag.
- Relevance in the daily report is split into 高/中/低 at cut points that are **provisional** (0.5 and 1.5 on a `[0,2]` score). The raw score is stored as `relevanceScore` so recalibrating does not require re-running a day. On a 6-article trial with the shipped criteria, nothing reached 高.
- Search reranking is fail-soft by design: no key, a network failure or an overloaded service (`HTTP 529` was observed live) all return the first-stage order with a warning. Its one real hazard is candidate numbering - a model that answers question `c3` against candidate `c5` sinks the best hit with no error at all - so every candidate is labelled in the state and a separate single-choice question cross-checks the argmax, warning on disagreement. Verified on real data before shipping, but only on one planted query.
- `build_index` had never indexed any article (`glob(' marriage*.md')` matched 0 of 188 files), so semantic search covered chat messages only. The glob is fixed; **the article corpus will enter the index on the next rebuild**, which spends embedding calls, so it is a deliberate action rather than a side effect.
- The report's admission threshold (`INCLUDE_THRESHOLD = 0.5` on `includeScore`) is provisional in the same way and, unlike the old rule, it is actually applied: both loaders call one predicate. Turning it on **reduced** what the report collects, because the primary loader previously collected everything unfiltered. On a 6-article trial the scores straddled the cut point (0.48 and 0.63 on two engineering posts), so the cut point is doing real work rather than being decorative.
- Message reads select only the columns the reader consumes, resolved per shard from what that shard actually has. `nt_decrypt` reads six (`local_id`, `server_id`, `local_type`, `real_sender_id`, `create_time`, `message_content`) and a WeChat version that renames or drops any other column of the message table therefore cannot make a shard unreadable. What is lost is reported, not swallowed: a shard lacking one of those six is read anyway and named in `missingColumns`, while a shard where none of them is present is reported as `SCHEMA_MISMATCH` and makes `coverage` partial. `export_chat_html` reads rows positionally, so there a missing column keeps its slot as `NULL` instead of shifting the fields after it. Both are verified against synthetic shards only - no future WeChat schema has been tested.

## Current Priorities

1. Keep initialization and database access compatible with supported WeChat 4.x variants, with reproducible issue diagnostics that contain no secrets.
2. Keep the daily reader reliable for configured sources and categories without widening access to local data.
3. Maintain security boundaries around local servers, assistant access, MCP tools, and cloud AI disclosure.
4. Evaluate external collaboration through read-only, synthetic-data, versioned interfaces before connecting real user data.
5. Make Bot-channel send capability and its limitations unambiguous in CLI naming and help text before extending messaging features.

## External Collaboration Position

For projects such as Yance, WeFlow CLI remains an independent local data-access layer. Early collaboration may cover read-only schemas, anonymized fixtures, and controlled MCP adapters. It must not require sharing real databases, credentials, keys, full local directories, or direct database access by an external product.

## Related Documents

- Architecture and data flow: [ARCHITECTURE.md](../ARCHITECTURE.md)
- Setup and troubleshooting: [OPERATIONS.md](../OPERATIONS.md)
- Security and reporting: [SECURITY.md](../SECURITY.md)
- MCP surface: [MCP.md](MCP.md)
- Design rationale: [DECISIONS.md](DECISIONS.md)
- Partner integration record: [PARTNERS.md](PARTNERS.md)
- AI integration surface: [AI_INTERFACE.md](AI_INTERFACE.md)
- Overall improvement and expansion plan: [ROADMAP.md](ROADMAP.md)
- DeepSeek V4 Flash施工文档: [DEEPSEEK_V4_FLASH施工文档.md](DEEPSEEK_V4_FLASH施工文档.md)
- User-visible releases: [CHANGELOG.md](../CHANGELOG.md)
