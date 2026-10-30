# Technical Decisions

> Record decisions that affect long-term maintenance. Each entry explains the chosen direction and the reason, not every implementation detail.

> 编号说明：`D-009` 从未使用（D-008 直接跳到 D-010），保留为空号；
> 历史上曾有两条决策共用 `D-016`，其中「architecture visuals」一条改号为 `D-028`。
> `D-056`~`D-058` 原本写在**另一条分支** `feat/assistant-skills-scenes-tool-surface` 上（技能包/场景/工具面那三条），
> 而 `D-059`（悬浮球的眼睛跟随）写在 `feat/panel-eye-follow` 上。本分支当时为避免合并时撞号，直接从 `D-055` 跳到 `D-059`。
> 两条分支已在同一次合并里并入 `master`（2026-09-30），所以现在 `D-055`~`D-059` 五个号都在，没有空号。
> **证据分级（2026-10-11 起）**：新条目的每条结论请标 **【实测】/【推断】/【未知】**，被推翻的结论就地标注
> 而不是删掉 —— 这是借自一份实测的启动器逆向报告的做法（它靠这一栏把"两处自我更正"留在了原地，
> 那两处都是**先写错、后被自己的证据推翻**的）。旧条目没有回溯补标。

## D-001: Local-first data handling

**Status:** Active

Local WeChat data, configuration, exports, and knowledge outputs remain on the user's machine by default. Network access occurs only in explicitly selected workflows such as article retrieval, configured cloud AI, or WeRead integration.

**Reason:** The project handles highly sensitive personal communications. A local default reduces unnecessary data exposure without claiming legal or platform safety.

## D-002: Separate data access from higher-level agents

**Status:** Active

Database access, MCP integration, local assistant behavior, and external products are separate trust boundaries. External projects may use constrained, read-only interfaces; they must not receive direct database paths, keys, credentials, or unrestricted filesystem access.

**Reason:** This keeps `weflow-cli` independently maintainable and limits the blast radius of an MCP client, agent, or collaboration.

## D-003: Default-deny assistant access

**Status:** Active

The optional WeChat assistant denies incoming users unless `assistantWhitelist` is explicitly configured. A new or incomplete configuration defaults to strict cloud-inference privacy behavior.

**Reason:** An empty allowlist must not accidentally expose local chats, favorites, or memories to an unexpected sender.

## D-004: Reader stays loopback-only

**Status:** Active

The daily reader binds to `127.0.0.1`; mutation endpoints enforce local-origin checks. Reader path handling and image proxying are restricted to reduce path traversal, cross-origin access, and SSRF exposure.

**Reason:** The reader serves personal reading history, notes, favorites, and locally generated article data. It is not a LAN service.

## D-005: Configured sources take precedence over article classification

**Status:** Active

When an official-account source has a configured category, the daily workflow keeps that source category and only produces the required summary. Automatic article-topic classification remains a fallback for uncategorized sources.

**Reason:** Source-level categories are more stable and prevent unnecessary model calls or conflicting article labels.

## D-006: Documentation has separate roles

**Status:** Active

`README.md` and `CHANGELOG.md` describe the product and release-visible changes. `docs/PROJECT_STATE.md` records the current engineering baseline. This file records durable rationale. `AGENTS.md` is the entry point for automated contributors.

**Reason:** Release notes, architecture descriptions, and current maintenance context change at different rates. Combining them caused stale plans to look current.

## D-007: Group routing stays upstream-gated and default-deny

**Status:** Active

The assistant only recognizes a group when the upstream Bot payload explicitly identifies one. A recognized group requires an explicit group allowlist entry, an existing sender allowlist entry, and an @ mention by default. The project does not use client automation, injected code, or undocumented protocol bypasses to join groups.

**Reason:** The current official OC/iLink integration is verified for direct sessions, not group invitations or group-message metadata. Treating ambiguous payloads as groups or adding non-official automation would broaden privacy and account risk without a reliable permission model.

## D-008: Bot-channel send is not personal-account messaging

**Status:** Active

The current `send` implementation uses the official OC/iLink Bot channel. It may only send into a previously established Bot conversation with a valid context token. It is not a facility for operating the user's personal WeChat account, initiating a message to a personal contact, or posting into an ordinary personal WeChat group.

**Reason:** Local contact lookup and the Bot transport are separate systems. Resolving a local contact ID does not grant the Bot channel permission or protocol context to message that contact. Presenting the command as generic personal-WeChat sending would be misleading and could lead users to assume unsupported access exists.

**Consequences:** Future messaging work must retain this boundary in command names, help text, and diagnostics. Any personal-account automation or undocumented protocol route requires a separate security and platform review; it is not an implicit extension of `send`.

## D-010: Reuse verified local access before initialization

**Status:** Active

`init` first verifies an existing local database configuration and exits without a new key-capture attempt when access works. A user must explicitly pass `--refresh` to reinitialize. `dbkey` similarly avoids duplicate capture unless `--force` is supplied. `init --test-missing-keys` provides a non-persistent missing-key test; `config forget-keys` is reserved for an actual, confirmed reset without clearing unrelated settings.

**Reason:** Repeated capture depends on client lifecycle timing and is unnecessary when a valid local configuration already exists. A verification-first workflow reduces account disruption and makes recovery steps deliberate.

## D-011: Backfill incomplete yesterday before today

**Status:** Active

An unqualified `daily` run checks yesterday's required local artifacts and completes yesterday first when `README.md`, `.articles.json`, or `index.html` is missing or empty. Explicit-date and dry-run invocations remain single-date operations.

**Reason:** A daily reader should not silently leave a gap when a previous scheduled run was interrupted. The required-artifact check is local, deterministic, and does not treat a directory alone as proof of a complete report.

## D-012: Staged data-directory discovery

**Status:** Active

Initialization checks common user locations by default. Cross-drive searches for standard directory names require `init --search-drives`; deep structural searches require `init --full-scan`.

**Reason:** Broad recursive scans can block the CLI for a long time and unnecessarily enumerate metadata from unrelated volumes. The staged commands retain recovery options for custom locations while making the cost and privacy scope explicit.

**Consequences:** Support guidance should first request an explicit `--path`, then `--search-drives`, and only finally `--full-scan`. Diagnostic output must not include account identifiers, salts, keys, or full local paths unless a user intentionally inspects them locally.

## D-013: Explicit AI for Vault promotion

**Status:** Active

`vault promote ideas` and `vault promote all` generate deterministic local indexes by default. AI generation is available only through explicit `--with-ai` plus a supplied API key or environment variable.

**Reason:** Knowledge-promotion outputs can be created locally, while AI promotion has monetary and privacy implications. An opt-in avoids an unexpected network request when a user is organizing notes.

**Consequences:** `vault init` must include the structured reading-note directories used by promotion. The promotion scripts must safely handle an empty or newly initialized Vault.

## D-014: Match exported media using reliable message identity

**Status:** Active

HTML export selects message-resource records by nonzero server message ID. It does not use unscoped local message IDs or server ID zero to associate media. Content MD5 and source URL lookup remain available when a server mapping is absent.

**Reason:** Local IDs can repeat across conversations and database shards. An ambiguous fallback can attach unrelated media to a chat message.

**Consequences:** WeChat 4.x V2 media keys are derived from local `kvcomm` data, verified against a real cached V2 header, and used only in memory during export. Remote emoticons are decrypted with message-provided keys; entity-escaped XML is normalized; and URL fallbacks cover encrypted, thumbnail, CDN, and external fields. Structured app cards keep their links and use cached or resolved covers when available. Some messages remain placeholders when reliable identity or source media is unavailable. Synthetic tests cover these paths; reporter verification is still needed for issue #7.

## D-015: Keep documentation synchronized with the source baseline

**Status:** Active

User-facing setup and troubleshooting documents describe supported commands and guarantees only when they are present in the current CLI and scripts. The project state and decision log remain the handoff source for agents; architecture explains boundaries and data flow; the README stays a short entry point.

**Reason:** The project has both a GitHub source workflow and a separately published npm package. Stale examples, hard-coded tool counts, or old compatibility claims can cause users to run the wrong code or expose sensitive data while troubleshooting.

**Consequences:** When command options, platform support, data flow, security boundaries, or verification status changes, update the relevant document in the same change. Validate examples against `--help` and keep generated local output out of commits.

## D-016: Establish a stable downstream data boundary

**Status:** Active

The first integration with `she-love-me` uses the existing local `export <contact> json` interface. Its field meanings and privacy requirements are documented in `docs/DATA_CONTRACT.md`. A future MCP interface may expose the same read-only contract, but must not bypass the local authorization and privacy boundary.

**Reason:** Reusing the existing interface gives the first downstream consumer immediate compatibility while keeping `weflow-cli` general-purpose. A documented contract lets future tools build on the data layer without coupling their internal analysis schema to database implementation details.

**Consequences:** Changes to exported field semantics require a compatibility review. Downstream consumers must preserve unknown fields, handle missing identities, and keep raw exports local unless the user explicitly authorizes a privacy-filtered cloud workflow.

## D-017: Expose the data contract through read-only MCP

**Status:** Active

The MCP server exposes `wechat.export_messages` as a bounded, read-only transport for the same `weflow-message/v1` envelope used by CLI exports. It accepts a session ID or an unambiguous display name, validates inclusive date filters, and caps the result size.

**Reason:** External projects need a stable integration boundary without receiving database paths, keys, configuration, or direct database access. Keeping MCP and CLI on the same contract avoids divergent message semantics.

**Consequences:** MCP clients must handle unknown message types and missing identities. The tool does not invoke AI or provide write/messaging operations. Any future broader access requires a separate security review.

## D-018: Add machine-readable capability discovery

**Status:** Active

The CLI exposes `capabilities --json` as the first call for automation. Read-oriented commands progressively provide `--json` output, while interactive initialization and side-effect operations remain explicitly marked as requiring user participation or confirmation.

**Reason:** An AI client needs to discover the current implementation and safety limits before choosing a command. A capability document is more reliable than inferring support from human-oriented help text.

**Consequences:** New user-facing commands should be added to the capability response and should declare whether they read local data, invoke AI, or cause side effects. This is an additive interface and does not change existing human-readable output.

## D-019: Require preview and confirmation for side effects

**Status:** Active

Machine-facing write operations use a two-phase protocol: first return a read-only preview, then execute only after explicit user approval. Todo completion, reopening, and deletion are the first commands implementing this rule.

**Reason:** An agent needs structured write access without gaining silent authority to alter local state. A stable preview and `CONFIRMATION_REQUIRED` response lets clients present the exact action before requesting approval.

**Consequences:** JSON-mode todo, access-control, message-send, configuration, destructive clear, assistant lifecycle, and local-reader startup operations require `--yes`; `--dry-run --json` never writes, deletes, or starts a process. Clear previews expose counts or impact flags rather than entries. Human access-control removals also ask for confirmation. Publishing and future side-effect interfaces must define equivalent confirmation boundaries before being advertised as agent-ready.

## D-020: Keep secrets out of child-process arguments

**Status:** Active

The TypeScript CLI and Python pipeline pass AI credentials, database credentials, account or conversation identifiers, private queries and filters, export locations and display names, and sensitive scan roots to worker processes through environment variables. They do not append these values to child-process argument arrays.

**Reason:** Command-line arguments can be visible in process inspection tools and can be repeated in runtime errors. Environment inheritance narrows accidental exposure while preserving existing CLI, environment, and encrypted-configuration workflows.

**Consequences:** Python entry points keep explicit arguments for direct compatibility but also accept the internal `WEFLOW_*` environment variables. Long-lived workers clear unrelated internal variables before startup. New subprocess wrappers require a regression check before they may accept credentials, account identifiers, private questions, or local data roots.

## D-021: Keep the default MCP surface read-only

**Status:** Active

The default MCP server exposes bounded read and local transformation tools only. It does not expose assistant-memory writes, official-account publishing, message sending, todo mutation, configuration changes, or deletion.

**Reason:** MCP clients can autonomously select tools and inherit the user's local permissions. Client-side approval prompts are not a stable substitute for the project's own preview and confirmation protocol.

**Consequences:** Write capabilities remain available only through explicit CLI or assistant workflows that implement an appropriate confirmation boundary. Conversation names must resolve uniquely, and MCP-facing limits reject invalid or excessive values instead of silently selecting or coercing them.

## D-022: Keep source and compiled resource lookup equivalent

**Status:** Active

CLI, MCP, database, export, and assistant services resolve scripts, native resources, generated-output roots, and runtime entries from one shared package-root resolver rather than assuming a fixed source or `dist` directory depth.

**Reason:** Development runs execute `bin/weflow-cli.ts`, while installed and packaged runs execute `dist/bin/weflow-cli.js` through `cli.cjs`. Relative traversal that works in one layout can point at the parent repository or `dist/scripts` in the other layout.

**Consequences:** New commands and services must use the shared package-root resolver for bundled scripts, native libraries, runtime entries, and package-owned output. Both source and compiled module layouts require regression coverage when resource lookup changes.

## D-023: Bound Agent-initiated network reads

**Status:** Active

MCP article fetching accepts only credential-free HTTPS URLs on the exact `mp.weixin.qq.com` host. Redirects are followed manually only while every destination remains allowed, with a finite redirect count, request timeout, and response-size limit. Public article search also validates its result limit and bounds the downloaded response.

**Reason:** Read-only tools can still reach external networks. Substring host checks, unrestricted redirects, and unbounded response bodies can enable SSRF, unexpected internal access, hangs, or memory exhaustion.

**Consequences:** New network-facing Agent tools must define an allowlist or equivalent public-network validation, finite time and size budgets, and tests that reject crafted URLs before network access.

## D-024: Confirm Vault publication

**Status:** Active

`vault sync` uses `--dry-run --json` for a content-free preview and requires explicit `--yes --json` for machine execution. Preview and result JSON omit file names and remote URLs.

**Reason:** A Git push discloses local files outside the machine even when the Vault itself is local. The old change check also ignored untracked files, making first-run behavior unreliable.

**Consequences:** The command detects tracked and untracked changes with `git status --porcelain`, does not initialize a repository during preview, and does not expose credential-bearing remote URLs in output.

## D-025: Confirm local generation and cloud analysis

**Status:** Active

Commands that create or replace local knowledge artifacts, build semantic indexes, or send selected content to an AI provider use a read-only preview followed by explicit confirmation. This applies even when no remote publication occurs.

**Reason:** Local file writes can overwrite user-managed material, while AI and embedding workflows can disclose selected content and consume quota. Treating only network publication as a side effect leaves Agent-driven local generation insufficiently controlled.

**Consequences:** Vault content mutations, Vault RAG, semantic search, RAG chat, evidence review, Wiki compilation, todo extraction, daily-favorite changes, semantic indexing, knowledge pipelines, and report generators expose `--dry-run --json` and require `--yes --json` for machine execution. Interactive key capture and interactive RAG chat expose previews but reject JSON execution. Preview mode does not read private content, call external services, scan processes, or write files; structured status results omit local paths, selected names, logs, and generated content. Queries, questions, and conversation restrictions passed to Python workers use environment inheritance instead of process arguments.

## D-026: Reject unsupported WCDB query parameters

**Status:** Active

The bundled WCDB DLL exposes a raw SQL query ABI without parameter binding. `execQuery` rejects non-empty parameter arrays until the native interface can be upgraded and verified.

**Reason:** Silently ignoring parameters and executing the original SQL would create a misleading and unsafe API contract for future callers.

**Consequences:** Existing fixed internal queries are unchanged. A future parameterized implementation requires a native ABI change, compatibility testing, and a separate security review.

## D-027: Add conservative coverage metadata to the versioned message contract

**Status:** Active

`weflow-message/v1` may include query coverage metadata such as requested bounds, returned count, returned time bounds, and a conservative `mayHaveMore` flag. The legacy `raw` export remains a top-level message array and does not gain this metadata.

**Reason:** Downstream data consumers need enough information to checkpoint time-window synchronization without treating a timestamp as a globally unique cursor or assuming that a database shard is complete.

**Consequences:** Consumers can perform overlapping time-window reads and deduplicate locally. A stable incremental cursor remains a separate future change and must be supported by all relevant database backends before it is advertised.

## D-028: Use GPT-image-2 for future architecture visuals

**Status:** Active

When a new architecture diagram visual is requested, use GPT-image-2 for the visual asset. Keep the diagram's structure and labels aligned with the source documentation, validate the final dimensions and legibility, and retain a maintainable source representation when practical.

**Reason:** The project owner wants architecture visuals to use the project's image-generation workflow while keeping technical documentation understandable and reviewable.

**Consequences:** Do not silently substitute an unrelated image-generation model. Do not treat generated pixels as the source of truth; `ARCHITECTURE.md` and the code remain authoritative.

## D-029: Keep the sync and media-coverage work additive

**Status:** Active

`weflow-sync/v1` and `weflow-job/v1` are new local state contracts written under
`~/.weflow-cli/`. The `sync` command and the export media report are additive: no existing command
changes its behaviour, its stdout contract, or its JSON schema. `export <talker> html` gains one
side file (`<prefix>_media.json`) and one extra key in its trailing stdout JSON, both of which the
only consumer ignores; `--media-report 0` restores the previous file set exactly.

**Reason:** D-016 fences the exported message contract and D-027 fences coverage metadata, so a
change that redefines existing fields would need a compatibility review this work does not warrant.
The gaps being closed - shard read failures dropped silently, media counters computed and discarded
- are observations about what already happened, not redefinitions.

**Consequences:**

- Sync state is **one file per conversation** (`sync/<source>-<name>-<hash>.json`), so every write is
  a whole-file atomic replace and no cross-conversation read-modify-write merge is needed. The hash
  suffix keeps `wxid_a@chatroom` and `wxid_a_chatroom` distinct without putting display names in a
  directory listing.
- `lastAttempt` and `lastSuccessfulRun` advance separately. A run that is `partial` still writes its
  state and its job record, but must not advance `lastSuccessfulRun` and must not return success
  (the spec's rule that partial completion is not success).
- The message identity keeps `serverId` when nonzero and otherwise uses `localId` + `createTime`.
  It **omits `shard`**, deviating from the spec's §4.2 sketch: messages do not carry a shard field,
  adding one would touch the fenced export contract, and overlap deduplication does not need it.
  Add it only if a cross-shard collision is actually observed.
- `sync retry` is **not** implemented. With no local index and no partial writes it would be exactly
  the same operation as re-running the window, and the failed-shard list is already in `sync status`.
  A command that is a synonym is worse than no command.
- `sync` is the only writer of `weflow-job/v1` in this round. If a single consumer is judged not to
  justify the file, the reversal is: delete the job store and fold `lastJob` into each
  `weflow-sync/v1` file. Nothing else reads it, so that is a one-file change.
- Still not advertised: a stable incremental cursor. D-027 stands - overlapping windows plus local
  deduplication is what this exposes.

## D-030: Adapt message reads to the shard's actual columns, and refuse a window rather than approximate it

**Status:** Active

Three changes to message reads, all about a read that cannot be done as asked.

1. The SELECT is built per shard from `PRAGMA table_info` against the six columns the reader
   actually consumes (`local_id`, `server_id`, `local_type`, `real_sender_id`, `create_time`,
   `message_content`). The previous SELECT named fourteen columns while `_message_dict` read six,
   so a version renaming any of the other eight made every shard raise and the conversation came
   back **empty with no error** unless `--report-shards` was used. A shard missing one of the six is
   now read anyway, with the loss named in `missingColumns`; a shard where none of the three anchor
   columns (`create_time`, `local_id`, `server_id`) survives is reported `SCHEMA_MISMATCH`.
2. `messages` gains `--from`/`--to` (unix seconds, inclusive) pushed into SQL. A shard whose table
   has no `create_time` is reported `WINDOW_UNAVAILABLE` and skipped rather than returning
   out-of-range rows.
3. `export_chat_html.fetch_messages` keeps its ten-column positional projection, but a column the
   shard does not have is selected as `NULL AS <name>` instead of being dropped. The rest of that
   file reads rows by index (`row[4]` is the sender, `row[5]` the time), so dropping a column would
   shift every later field by one. A `--date` export on a shard without `create_time` raises rather
   than widening itself to the whole conversation.

**Reason:** A read failure that is indistinguishable from "nothing here" is the worst failure mode
this codebase has had - it cost a real investigation into an allegedly truncated export. Columns
nobody reads must not be able to cause one, and in the exporter a shifted column would be worse
still: silently wrong output that no error surfaces. For the window, the caller of a windowed read is
tracking what it has covered: returning rows it did not ask for lets it record a range it never
read, and silently returning fewer rows is the same failure in the other direction. Refusing is the
only answer that stays honest, and it is visible because every in-tree window caller also passes
`shard_report`.

**Consequences:**

- `coverage: 'partial'` now means "an opened shard carries a `reason`", not "a shard reported
  `READ_FAILED`". `SCHEMA_MISMATCH` and `WINDOW_UNAVAILABLE` join it: all three describe rows that
  were not read. A shard that never opened still yields `unverified`, unchanged - nothing is known
  about its rows, which is a different claim from knowing they were missed.
- `missingColumns` is **not** a coverage failure. The rows came back; only fields were lost. It is
  reported as a `shard-columns-missing:<shard>:<columns>` warning and leaves `coverage: complete`.
  Treating the two alike would make every sync on such a database report `partial` forever and never
  advance `lastSuccessfulRun`.
- The window pushdown is an **optimisation, never the correctness guarantee**. `syncService` still
  filters by the window itself, because the non-NT backends have no pushdown. It measured no faster
  (519 ms with no window vs 514 ms from the newest message on a 925-message conversation): the cost
  is process start plus 256000-round PBKDF2 per shard, not row transfer. Do not advertise it as a
  speed-up.
- The `--from`/`--to` pushdown is a prerequisite for a stable cursor, not a cursor. D-027 stands.
- Unknown columns are ignored rather than selected, so a future schema needs no code change to stay
  readable - but no future schema has been tested. Synthetic shards in
  `test/nt_decrypt_shards_test.py` are the only evidence.
- The exporter keeps its own copy of both the shard discovery and the projection. They were **not**
  unified with `nt_decrypt` here; only the failure mode was fixed on both sides. The two copies are
  pinned together by a test, so the next person to change one is told about the other.

## D-031: Judgement goes to a decision model, generation stays with the LLM

**Status:** Active

The daily pipeline's article **topic** (6-way) and **relevance** (3-level) are now decided by
TypeSafe's Jev (`POST https://api.typesafe.ai/v1/systemone`, `scripts/jev_client.py`), a model that
returns typed answers with probabilities instead of text. The LLM still **generates** the summary,
tags and concepts - that is what it is for, and Jev cannot generate at all. When no TypeSafe key is
configured, the previous "prompt for a format then parse it with two regexes" path runs unchanged.

**Reason:** the old path was not merely inaccurate, it was inert. Measured over the 2201 stored
articles, `relevance` is the default `中` in **2199** of them - and the code says why:
`biz_daily.py` assigned it on only two of five paths, the `category_hint` path hard-coded `'中'`,
and the exception and short-content paths never assigned it at all, leaving it to the writer's
`fm.get('relevance', '中')`. So `generate_ai_report.py:123`'s gate
(`if topic != FOCUS_TOPIC and relevance != '高': continue`) had only ever filtered on topic;
the "relevance" dimension had never once admitted an article. **That sentence was itself too
generous, and a later check corrected it**: the gate lived only in the markdown-scan
*fallback* loader. The primary loader (`.articles.json`, which exists on every normal run)
returned every article unfiltered, so the documented rule was not being applied at all. Topic fared little better: it is
`学术` in **zero** articles on a normal day and in **100%** of them on 2026-09-04/05.

*That last attribution was wrong, and the data said so.* It called 09-04 the signature of the
`except` branch's `topic = source_category or '学术'` fallback - but that branch would have left
`topic: 学术` and `tags: ['学术']` **in the JSON**, and 09-04's JSON has neither key populated
(`""` and `[]` for all 178 articles). Article dicts with no `topic` and no `tags` at all mean the
classification phase never ran: no API key, or `--no-ai`. The `学术` a reader sees on that day
comes from the *write* path - the grouping step's default and the md frontmatter's `tags`
default - which is precisely the divergence the follow-up pass fixed (see below).

A 60-article comparison was run before switching (`scripts/jev_probe.py`, stratified across topics
and days, sent state = title + body only): agreement with the stored labels was 58.6%, and the
disagreements ran **against** the stored labels almost everywhere - `习近平向第八届中俄能源商务论坛
致贺信` stored as 学术 against Jev's 政治, a `人民日报·夜读` cooking essay stored as 政治 against
Jev's 文学 (the body was read to confirm), a `Nature Climate Change` paper stored as AI against
Jev's 学术. **This is not an accuracy measurement** - the labels are the DeepSeek output, not a gold
standard - which is exactly why the switch is reversible and why the raw score is kept.

**Consequences:**

- `--classifier {auto,llm,jev}` (default `auto`) keeps one-flag rollback and lets anyone re-run a
  date both ways. `auto` means "Jev if a key is configured, otherwise the old path".
- **The LLM prompt is deliberately unchanged.** It still asks for `【主题】`/`【相关度】` that we now
  ignore. That keeps the fallback *byte-identical to the old behaviour* rather than a new, worse
  fallback; the cost is roughly 20 wasted output tokens per article. Slimming the prompt is a
  follow-up, not this change.
- Two **additive** frontmatter keys: `relevanceScore` (raw) and `topicConfidence` (0-1). Downstream
  compares `relevance` as a literal string and ignores unknown keys. Without these the probability -
  the entire new information - would be discarded at the write step.
- The three-level cut points (`<0.5` 低, `<1.5` 中, else 高) are **provisional and uncalibrated**.
  They are derived from the zero-indexed score scale, and the raw score is stored so recalibrating
  does not require re-running a day's report.
- **Classification runs before the generation loop, concurrently.** The two stages have no
  dependency in either direction, so their ordering was only ever historical. Measured: 12
  real articles in 3.3s at 6 workers against ~12s one at a time. The dependency that *does*
  exist is positional - `decisions[k]` must belong to `articles[k]` - so the eligibility test
  in the concurrent stage is kept character-for-character identical to the loop's, and any
  article that fails still occupies its own key with a `None` rather than being absent (an
  absent key would let the loop treat a neighbour's judgement as its own). Concurrency is
  kept at 6 deliberately: the service is new enough to return `529` under load, and raising
  the fan-out buys retries rather than throughput.
- The client is **fail-loud** (`JevError` with the HTTP status and a 400-character truncated body,
  never the key); the caller is **fail-soft** (per article, printing a WARN and falling back). One
  unclassifiable article must not abort a day's report - but a client must not disguise a failure
  as a plausible-looking answer either.
- **New data egress**: article titles and bodies now also go to `api.typesafe.ai`. Article text
  already went to DeepSeek for summarisation, so this is a new vendor rather than a new category,
  but it is a vendor that did not exist before 2026-09-15. Configuring `typesafeApiKey` is the
  opt-in; `daily --no-ai` remains the way to keep everything local.
- The key is a first-class config field (`typesafeApiKey`, in `ENCRYPTED_KEYS`, settable via
  `config set`), and Python reads it through `_utils.get_typesafe_key()`. That function returns an
  empty string rather than raising when the ciphertext cannot be decrypted - `configService`'s
  `lockDecrypt()` silently returns `''` in the same situation, and a config copied from another
  machine must degrade to the old path rather than crash the daily run.
- **The taxonomy is one table, and both paths read it.** `TOPICS` had been copied into six
  files and `TOPIC_CRITERIA` lived separately inside `jev_client`; both now sit in `_utils` and
  every consumer imports them. The trigger was a concrete contradiction: `TOPIC_PROMPT`
  generated its "must be one of" line from `TOPICS` (six categories, including 政治) while
  hardcoding **five** in its two reminders and omitting 政治 from the judging rules - a category
  holding 26% of the corpus had no definition, in a prompt whose whole job is to be strict about
  the list. Prose enumerations drift; a table does not. The criteria table is shared rather than
  mirrored precisely so that "falling back to the LLM path" means judging by the same standards
  it always did. Pinned by identity in `test/topic_taxonomy_test.py`, because a copy that happens
  to be correct passes an equality check.
- **The report's admission question is now asked directly.** `worth_including` (a `noul`) rides
  along in the same request - measured at 0.91s for 12 questions versus 0.84s for 2, with `state`
  dominating the tokens, so the marginal question is essentially free - and lands in frontmatter as
  `includeScore`. The gate used to be `relevance != '高'`, which asks "how useful is this to the
  reader" and then reads the answer as "put it in today's report". Those are different questions and
  no threshold tuning can reconcile them. The two scores do diverge in practice: an award
  announcement scored `relevance` 0.79 but `includeScore` 0.03 (related to the field, nothing to
  use today), and two engineering posts landed at 0.48/0.63 - close enough to the cut point to show
  the probability is not saturated at the ends.
- **Both loaders now share one predicate** (`admits()`), with `--include-all` to revert to
  collecting everything. This is a real behaviour change on the primary path: the report will
  contain fewer non-focus articles than before, because before it contained *all* of them. Articles
  written before `includeScore` existed fall back to the old `relevance == '高'` rule, so
  regenerating an old date does not silently swap its article set.
- An unused question is worse than no question. `is_research_paper` was added in the first cut of
  this change and never read by anything - the same "compute it and throw it away" shape as the
  exporter's old `COVER_STATE` counters. It was removed and replaced by `worth_including`.
- **Not done, deliberately**: the other eight "ask the LLM then parse the text" call sites (assistant
  tool routing, long-term memory extraction, todo urgency, monthly-report task detection, ...).
  That group was never measured on Chinese, so switching it would be a bet rather than a change.
- **Superseded by later work, kept so the reasoning stays readable**: this decision listed `tags` and
  "`TOPICS` duplicated across five files" as deliberately-not-done. `TOPICS` and `TOPIC_CRITERIA` are
  now single-sourced in `_utils` (the prompt path and the decision-model path read one table), and
  `test/topic_taxonomy_test.py` pins every consumer by identity. The four `TOPIC_ORDER` copies in
  `auto_tag` / `create_reading_notes` / `enrich_backlinks` / `generate_html` remain, because two of
  those files have no `_utils` import edge and whether an import resolves would then depend on the
  caller's working directory - so a test asserts the four copies still equal `TOPICS` instead, turning
  a silent future drift (adding a seventh category would drop a section from the report) into a
  failure.
- **A follow-up pass found the root cause behind the 2199-of-2201 number and fixed it.** The default
  was not merely applied too often - it was applied **inconsistently by the two writers**: the JSON
  writer defaulted a missing topic to `''` and the grouping step that names the folder and writes the
  md frontmatter defaulted it to `学术`, seven lines apart, so a single run could emit `topic: 学术` in
  the md and `topic: ""` in the JSON with nothing reported either way. The 2026-09-04 output is that
  failure in full (178 articles, all under `学术/` in the md, all `""` in the JSON, two of them
  "OpenAI 深夜发布 GPT-6 Astra"). The reachable trigger is ordinary: `daily --no-ai`, or a run with
  no API key, skips classification entirely, leaving every article without a `topic` key while the
  write phase still runs. Both writers now read one normalised value (`_utils.DEFAULT_TOPIC`, applied
  by `biz_daily._group_by_topic`), the check is **membership in `TOPICS`** rather than emptiness, and
  the run prints how many articles fell back so a whole-batch fallback cannot read as a normal
  classification. The same shape sat in `tags` and was fixed the same way.

  **The `tags` question this decision left open is now answered, and the answer is "not a bug".**
  Measured over the 1584 articles across the 11 stored days: 1396 carry exactly `[topic]`, 182 carry
  none, and **6 carry real tags** - and those 6 are the whole corpus built without configured source
  categories. The mechanism is deliberate, in the prompt: when a source has a configured category,
  `biz_daily` sends a summary-only prompt that says *不要输出主题、标签、相关度或概念字段* and sets
  `tags = [category]` itself. Every source in those runs had a configured category, so no article
  ever reached the path that asks for tags. The signature is unambiguous and checkable: **on all 11
  days every source maps to exactly one topic** (36/40/15/43/41/27/35/36/3/36/6 sources, zero
  exceptions) - which is what topic-from-config looks like and per-article classification does not.
  A live call through the production prompt on one stored article returns
  `AI与数学, 人类数学家, 数学共同体, 人机关系, 学术人文`, so the extraction works whenever that path is
  used. Two consequences worth keeping: real tags appear only for sources *without* a configured
  category, and the earlier "tags never persist" reading was wrong - it described the
  configured-category path, not a defect.
- **Contract hardening, added later and verified against the live API** rather than
  against a source reading. Two capabilities this client was not using: `instructions`
  accepts a **structured object** (`{"goal": …, "rules": […]}`) as well as a string, and
  every response carries a **`model` field naming the served version** (`jev-1.13.0`) as
  against the requested alias (`jev-latest`). The served name is now recorded
  (`usage['model']`, `JevClient.last_model`, and a `decisionModel` key in the daily's
  `.articles.json`) because an alias can drift while every result still looks correct -
  the same shape as the bug this decision's own history keeps producing: a plausible
  value that is silently not the one you think. Every `choice` answer is now validated
  before use (probabilities present, key set equal to the criteria, values in `[0,1]`,
  sum ≈ 1, `choice` == argmax); the argmax rule is the load-bearing one, since a
  non-argmax choice looks exactly like a normal answer. The daily's prompts stay as
  strings: switching them to the object form would change model behaviour, and the
  existing cut points were calibrated against the string form.

## D-032: Use the decision model as a reranker - one request per pass

**Status:** Active

`semantic_search.rerank()` takes the top 20 hits from the first-stage search, asks one
`noul` question per candidate in a **single** request, and reorders by the returned
probabilities. `search` calls it before slicing to `top_k`; `--no-rerank` restores the
previous behaviour exactly.

**Reason:** the retrieval stage had no second pass at all. `search()` scored by cosine
similarity and took `argsort[:top_k]` - one line, no reranking. Embeddings answer "is this
semantically near the query", not "does this actually answer it", and the keyword fallback
(used whenever embeddings are unavailable or return zero vectors) answers something cruder
still. Reranking is the purest form of what this model does, and the cost model makes it
viable: measured 0.84s for 2 questions against 0.91s for 12, since `state` dominates the
token count. **One pass over 20 candidates is ~1 second, not 20 round trips.**

**Consequences:**

- **Numbering is the hazard.** If question `c3` gets answered against candidate `c5`, the
  most relevant hit sinks to the bottom and the result looks like an ordinary "the model
  thought it was irrelevant" outcome. Nothing errors. So every candidate carries an
  explicit `【候选k】` label in the `state`, and the request also asks a `choice` for
  "which candidate best answers this" purely as a **cross-check**: if the single choice
  disagrees with the argmax of the per-candidate scores, a warning is printed. The check
  only warns - it does not reorder, because one wrong answer should not be able to swap
  the whole list.
- Verified live before shipping: the relevant article was planted at position 4 of 8 and
  came out first at 0.94 against 0.01-0.04 for the rest, with the self-check agreeing.
- `score` keeps its meaning (cosine similarity or keyword count) and the new value goes to
  `rerankScore` - the same additive-key rule as `relevanceScore` and `includeScore`.
- A candidate the model did not score is ordered **after** the scored ones and carries no
  `rerankScore`: "unknown" and "irrelevant" are different answers, and collapsing them
  would silently reorder on a partial response.
- Fail-soft: no client, fewer than two candidates, or any error returns the original order.
  Reranking is an improvement to search, not a precondition for it.
- **Transient failures are retried.** The client got a live `HTTP 529 system_overloaded`
  while this was being built; TypeSafe's own reference implementation retries transient
  provider failures twice by default. `JevClient` now retries 429/5xx/529 with a
  deliberately short backoff (worst case ~2.4s), because the daily pipeline calls it once
  per article and a long backoff would stretch an already overloaded run into tens of
  minutes - time the caller should be spending on its fallback path.

## D-033: Every machine judgement shown to a person carries its evidence

**Status:** Active

`scripts/reply_debt.py` asks, once per conversation, whether the thread is sitting on a reply
the user owes (`waiting`), how urgent it is, whether a promise is outstanding, whether money or
delivery is involved, and what kind of conversation it is. Results are ranked and printed with
their probabilities. Two rules came out of building it, and both are general:

1. **A message with no text must be labelled, not left blank.** WeChat stores images, voice
   notes, videos and stickers with an empty `message_content`. Unlabelled, they entered the
   state as a line reading `对方：` with nothing after it - and the model returned a confident
   `0.48` for "is this person waiting on me" **from an empty line**. Every such message now
   carries its type (`[图片]` / `[语音]` / `[非文本 localType=N]`). Measured effect: that same
   conversation dropped to `0.40` and moved out of the result set. A model asked to judge
   nothing will still answer; supplying the type is what stops it.
2. **A judgement shown to a human must carry how thin its evidence was.** "Waiting 0.69" derived
   from a two-character last message is not the same claim as one derived from a full
   explanation, and the model cannot tell the difference - it only sees text. So each row prints
   `证据：对方末条 N 字 · 对方实质发言 M 条`, and anything resting on fewer than five characters
   is marked as too thin to act on.

**The report now states its own uncertainty.** `generate_ai_report.py` appends a `我拿不准的`
section listing entries whose `includeScore` sits inside the band (both admitted and excluded)
plus those whose `topicConfidence` is low enough that the section they were filed under may be
wrong. It is computed locally from frontmatter - asking a model to describe its own uncertainty
is asking it to generate more prose, which is the failure this whole line of work exists to
avoid. When the articles predate the probability fields, the section says so explicitly: an
absent section must not be readable as "everything here was certain".

**Measured prevalence, which is what the threshold actually trades against.** Over a
topic-balanced 160-article candidate pool: **86.1%** of articles score below 0.2 on
`includeScore`, 4.4% in 0.20-0.35, 0.6% in 0.35-0.50, and **8.9% at or above 0.50** (3.8% +
1.9% + 3.2%). So the 0.5 threshold admits roughly one article in eleven of a balanced mix -
and the real daily mix is 新闻-heavy, which scores low, so the true figure is likely lower.
An earlier guess in this document ("about 3%") was not derived from anything; this one is.
The sampler also had to change to produce a usable curve: sampling by topic alone yielded a
50-article sample in which 45 items sat below 0.2, so labelling 50 bought the information of
labelling 4. It now draws from a larger scored pool, stratified **by probability band**, which
put 22 of 50 in the bands that matter - and reports the pool's band shares separately, because
a band-stratified sample is not a natural distribution and must not be read as one.

**How the "no gold standard" gap finally gets closed.** `scripts/quality_eval.py sample`
draws a stratified sample and scores each article through the **production** path at sample
time, then writes blank `topic` / `include` fields for a person to fill in; `score` prints
topic agreement (for Jev *and* for the stored labels), a calibration table by probability
bucket, and a threshold sweep naming the cut point that best matches the human. Two design
points are load-bearing:

- **The sample is re-scored instead of reusing stored values.** No real artifact on disk
  carries `includeScore` - the six files in the 2026-09-05 output directory were written by a
  run of the code from *before* that field existed, so they have `relevanceScore` and
  `topicConfidence` only. And the stored `topic` is the very thing under test: it labelled 364
  articles as 学术 in one day. So the (probability, human label) pairs have to be produced
  fresh, which also means the corpus never has to be re-run and `output/` is only read.
- **A blank label is "not sure", not "wrong".** Items left `null` are excluded from every
  number rather than counted as misses, because collapsing the two would make accuracy look
  worse the more honest the labeller is.

**A field written on one path and not the other is a field that does not exist.** The three
probability fields were added to the markdown frontmatter but not to `.articles.json`, and the
report loader prefers the JSON. The new section therefore worked in the fallback path and was
dead on the primary one - which is how it was found, by running a real dry-run and watching it
report "no probability fields" while the frontmatter plainly carried them. The same shape has
now appeared five times in this codebase: two loaders, two shard-discovery copies, argv-vs-env
secrets, the admission gate living only in the fallback, and now serialization. **When adding a
field whose value is consumed by another script, add it to every representation and test the one
the consumer actually reads.**

**Measured: what you feed sets the ceiling, not the model.** The same 132 questions (44 scripts x
does-it-write / does-it-network / does-it-spawn) were asked twice, changing only the evidence in
the state. Given each file's first 14 lines: **77.3%** agreement with a regex baseline. Given its
first 1500 characters: **88.6%**, with subprocess at **100%** and file-writing at 97.7%, for 2.3s
and $0.0011. The ceiling moved 11 points without touching a single question.

**And the disagreements are not automatically the model's fault.** Of the remaining `network`
mismatches, five of the six checked by hand call `call_ai` / `create_engine`, which reaches
`urllib.request.urlopen` inside `_utils` - so those scripts *do* egress and the model was right
while the regex baseline (which only looked inside each file) was wrong. **Two imperfect
instruments agreeing 88.6% of the time is not an accuracy figure for either of them.** Treating
the baseline as truth would have produced a confidently wrong conclusion about which instrument
to trust.

**Also:** `waiting` has one mechanically checkable failure mode - claiming the other side is
waiting while the last message in the transcript is the user's own. That contradiction is
detected and reported. It is the only part of this output that can be falsified without reading
the messages by hand, which is exactly why it is worth checking.

**Honest limitations:** there is no gold standard, so every number is a prompt, not a fact. On
the machine this was developed on the tool reports **two** candidates at 0.54 and 0.62 out of 25
recent conversations, with nine more between 0.30 and 0.45 marked uncertain and three filtered
out as customer-service or marketing - a modest result, and the right one for someone who
answers promptly. It is not evidence that the model would be accurate on someone else's data.
Debt age is measured from the **other side's** last message, not the conversation's last
activity: if the user replied most recently the debt is zero, and using session activity would
have flattened exactly the case worth surfacing.

## D-034: Expose the decision model as a local primitive, but not over MCP

**Status:** Active

`weflow-cli decide --request <file>` (and `scripts/decide.py`, which also reads stdin) takes a
caller-supplied `{state, questions}` and returns `{answers, usage, costUsd}` from one decision
request. The command reads **no local data of its own** - the entire state is whatever the
caller hands it.

**Reason:** the value is not that this judges better than the caller's own model; it is that a
batch of judgements becomes affordable. Measured: ~1s for a request regardless of whether it
carries 2 questions or 12, since `state` dominates the token count, and 20 candidates cost
around two ten-thousandths of a cent more than 1. **That ~1s is an idle-service figure.** It is
independent of state size (200 characters and 4000 characters both measured ~1.1s), but not of
concurrency: a 158-article pass at 6 workers averaged **5.7s per call**, with a 10.1s outlier.
The cost model is still what makes batch judging viable - it just means wall-clock for a large
batch should be estimated at seconds per call, not at one. So "label 200 items across six dimensions"
stops being a token-budget decision. Two further properties come from it being non-generative:
the answers arrive **typed with probabilities** rather than as prose to be parsed, and nothing
is being *written*, so it is safe to place inside control flow where generated text would be
unwanted.

**Consequences:**

- **Batch mode exists because the primitive was unusable without it.** Judging 44 files across
  3 questions by hand meant writing 132 question blocks; the first real use of `decide` was a
  throwaway loop that built them. `--over <glob> --ask <text>` now does that expansion, and the
  response carries a `batch` mapping from question name to file and question, so a caller never
  has to parse names - file names contain spaces, `|` and CJK punctuation, and encoding them into
  question names would make every reader a string-parsing exercise.
- **The evidence size is an argument, and it is reported back.** `--max-chars` defaults to 1500
  and the response echoes it, because the measurement above says the evidence you feed sets the
  ceiling. A tool that decides that number silently would invite the conclusion that its output
  means more than it does.
- **Requests are validated locally.** The service returns `422` for a malformed request, but
  that error can only say which field is invalid - not what the caller meant. Local validation
  names the intent: "a `score` needs at least two ordered levels", "a `choice` needs a non-empty
  criteria object", "a `score` with one level is a constant zero and carries no information".
- **Deliberately not exposed over MCP this round.** An MCP client - explicitly a separate trust
  boundary per D-002 - would be able to drive local outbound calls carrying arbitrary text of
  its choosing. That is a new egress surface and it needs its own decision, not a side effect of
  adding a convenience tool. `capabilities --json` records `mcpExposed: false` with the reason,
  so the omission is visible rather than inferred.
- Follows the `search`/`awaiting` discipline: `--dry-run` validates and echoes the shape with
  **no egress**, `--yes` runs, and neither given means confirm. `--dry-run` needs no key, on the
  same reasoning as `awaiting`: a preview exists to answer "is this worth spending?".
- Honest limitation: this is a *different* model, not a strictly better one, and there is no gold
  standard behind its numbers. For a single one-off judgement the caller's own model is fine and
  this is just an extra hop. It pays off at scale, where the alternative is many calls and prose
  to parse.

## D-035: The assistant's single-round fast path: default-off, and how it got there

**Status:** Active (shipped 2026-09-22 behind a default-off switch; see the update at the end)

The assistant's ReAct loop costs two LLM round-trips for the common "answer from local data"
question: round 1 picks a tool and its arguments, round 2 phrases the reply from the result. A
decision-model pre-router could resolve `{needs_tool, tool, closed-set arguments}` in one ~1s
call before the loop, execute that tool, and let the very first `callLLM` see the result - turning
two round-trips into one. Measured elsewhere in this repo, the call model supports it: 12
questions cost 0.91s against 0.84s for two, so the routing decision is nearly free.

**Why it is not implemented here:** `handleMessage` is a privacy-audited, daemon-resident path.
It carries the access-control gate, `privacyGate.audit` on every tool call, the daily limit, and
three-tier memory - and it can only be exercised end to end against a **live** WeChat channel
(bind by QR, whitelist a sender, run the daemon). None of that is reproducible in the environment
this was built in, so the change would have shipped unverified into a path that handles the user's
real messages.

The failure mode is not a crash, which is what makes it worth writing down: if the router picks
the wrong tool, the model receives a result that does not answer the question and will phrase a
**confident** reply around it. That is worse than being slow.

**What it would take to land it safely:**

- an opt-in switch on the model of `--classifier` / `--no-rerank` / `--include-all`, **default
  off**, so live behaviour is unchanged until someone watches it;
- a routing-confidence threshold that falls back to the normal loop, plus an assertion that the
  fallback path is bit-identical to today's behaviour;
- a synthetic harness that drives `handleMessage` with a stubbed `callLLM` and a stubbed tool
  executor, covering: routing correct, routing wrong (must degrade), routing uncertain (must
  degrade), and **no tool dispatched without the audit line** - the last one is the security
  property that must not regress;
- a way to observe real traffic before trusting it, e.g. run it in log-only mode ("would have
  routed to X") alongside the normal loop for a week and compare.

**Until then** the loop stays as it is. One extra LLM round-trip is not worth an unverifiable
change to the path that mediates the user's messages.

**A reference implementation exists, and one of its tricks is directly transferable.** The
`browser-use` × TypeSafe demo repo (`jev_ultrafast`, MIT, ~1950 lines) ships this pattern in the
browser domain and publishes its measurements. Its key design point is that a **speculative
second question cannot see the first answer** - model calls inside one request are mutually
blind - so the target-selection question has to **state the operation it is assuming** in its own
instructions ("assuming the operation is TYPE_TEXT, which element is the target"). Each operation's
target is computed anyway and only the matching one is consumed, which is how "two decisions, one
round trip" works. That is the same move as writing the judging rule into the criteria text, one
level up: not just multiple questions, but questions with a **dependency** spelled out.

Its numbers are worth having as a shape rather than a baseline: 17 model requests for one flight
search, 90,558 input / 6,325 output tokens, 178 ms median latency, and the browser-protocol call
count dropping 1092 → 101 when one full snapshot replaced hundreds of round trips. It also states
the boundary this decision already assumed: **DONE is not evidence of success** - their version
keeps an independent post-check, and a failed development attempt (8.697 s) is recorded rather
than dropped.

**Its browser path is not portable to this repo's scenarios**, and the design doc says why:
shadow roots, iframes, canvas, file uploads, new tabs and nested scrolling are unsupported - which
is most of what the official-account console, n8n and conference-site work consists of. It also
drives its own stack (browser-harness + CDP + a reused Chrome profile), separate from whatever
browser automation this repo has. The transferable parts are the **question organisation** above
and the discipline of publishing measurement boundaries with the numbers, not the transport.

### Update 2026-09-22: shipped behind a default-off switch

The four prerequisites above are met, so the fast path now exists in
`src/services/assistantRouter.ts` and is wired into `handleMessage`. It is **off by
default**: `assistantFastRoute` accepts `off` (default, byte-identical to before), `log`
(decide, record "would have routed to X", dispatch nothing) and `on`.

What changed versus the deferred design:

- **Only tools with closed or empty arguments are routable.** The design assumed
  `{tool, arguments}` could be resolved in one call; the arguments cannot, because a
  `choice` can only pick from a closed set and "which contact", "which keyword" are free
  text. Nine capabilities over six tools (`list_sessions`, `get_stats`, `get_daily_report`,
  `get_sns` ×2, `get_weread` ×2, `get_todos` ×2) carry their arguments inside the option
  text; everything else falls back. Passing the user's whole message as the argument would
  have manufactured the exact failure this decision was written to avoid.
- The routing question is a `noul` ("must this be answered from local data?") plus a
  `choice` over those nine capabilities **plus `none`**, whose criterion covers both "no
  local data needed" and "local data needed, but not by any of these ways" - without the
  second half the model is forced to pick among nine wrong options.
- **Every uncertain exit falls back**, and falling back *is* today's behaviour, not a
  weaker new one: judge failure, non-numeric probability, sub-threshold confidence, an
  unknown capability name. `asProbability` refuses booleans (`Number(true)` is `1`, so a
  `noul: true` would have routed *certainly* - the same misread as the earlier client,
  in the opposite direction).
- The dispatch and audit are now **one implementation** (`runToolCall`) shared by the loop
  and the fast path, because "no tool dispatched without the audit line" is exactly the
  kind of invariant that rots when written twice.

**Measured** (`scripts/assistant_route_probe.ts`, 17 representative questions, real
decision layer, 2026-09-22, free period): 15 matched the expectation I wrote in the probe,
**0 routed to a wrong capability**, 10 routed concretely, ~1.33 s per decision (two
outliers at 2.6 s and 4.2 s). The two mismatches fell back rather than routing: 我明天该干嘛
(needs_tool 0.40) and 最近有没有什么书可以读 (0.17) - and for the second one the *probe's*
expectation is the arguable one, since it asks for a recommendation rather than for what is
being read. The expectations are mine, not a human-labelled gold standard.

**Still not done, deliberately:** the log-only week on real traffic. That needs the
daemon running against a live channel, which is the user's to start; `log` mode exists for
exactly that. Free-text-argument tools remain unroutable until something can select a
value rather than a label - the same job `route_cards.py` does for conversation search.

### Update 2026-09-22 (later): a contradiction check, because the cause could not be found

Two live answers claimed a lookup had been made and the bodies masked, while the audit read `tools=0`.
Forty real calls across four prompt variants (current prompt, the conditional-only variant, a window
seeded with the model's own earlier "blocked" answers, and the complete pre-change prompt) **called the
tool 40/40 times**, so the first explanation written here - that the prompt's conditional was read as the
current state - is wrong, and the cause of those two turns is unknown.

What ships instead is a guard that does not depend on knowing the cause: when the router says the message
needs local data and the turn produced no tool call, the model is told it holds no tool result and the loop
runs once more. That adds one switch whose semantics differ from `assistantFastRoute`: the guard is armed
whenever the router ran, **including `log`**, because `log`'s promise is about routing rather than about
safety, and an observation period is when a fabricated lookup is most likely to be noticed. `off` remains
bit-identical because the router never runs and there is no signal to check.

## D-036: Search your own conversations with the decision model selecting query terms, not ranking sessions

**Status:** Active

`scripts/route_cards.py` answers "which conversation was I talking about X in". Two stages:
the decision model picks the real query words out of the candidate n-grams the question
produces, and **ranking is local**, by how many messages in each session literally contain
those words. Retrieval reads WeChat's own `message_fts.db`.

**Reason:** the obvious design - one card per conversation, ask the model which are
relevant - **was measured and does not work**. As a per-card `noul` every candidate scored
0.50-0.51, putting a one-message coupon group in the same band as the conference group with
213 hits and leaving out the session most worth opening; as a per-card `score` (0/1/2) all
20 candidates scored 1.74-1.76 whether they had 247 hits or 8. Local sorting by hit count
ranked the same data correctly. The card carries too little for the judgement being asked -
the model has never seen the conversation - so the shipped split gives ranking to the
objective local signal and leaves the model the job it demonstrably does well: choosing
words from a closed candidate list (it kept 会议 at 0.77 and rejected seven fragments at
0.11-0.40, consistently across runs). That is also the boundary of what it can do at all -
it cannot generate the search terms, only select them.

**Consequences and boundaries:**
- **New egress surface, deliberately small**: only the candidate words and the user's
  question leave the machine. No message text, and - unlike the first design - no
  conversation names, counts or timestamps either, since the model no longer sees cards.
  `--keyword` skips the model entirely and keeps the whole path local.
- Ranking by literal match is the known limit: "聊过上线的事" will not find a conversation
  that says 部署. That is what the embedding path (`search`) is for; the two are
  complementary recall paths, and the tool says so instead of pretending to have found
  nothing relevant.
- The article-favourite reading of `message_fts.db` is the enabling fact: `acontent` is
  plaintext and `session_id` is the rowid of that database's own `name2id` table (595
  sessions), so retrieval needs no index of our own. `MATCH` is unusable
  (`no such tokenizer: MMFtsTokenizer`), but `LIKE` over 60k rows is instant.
- A `noul` answer is a **probability float**, not a boolean (measured: 0.98 true, 0.01
  false). Reading it as a boolean makes every question look answered "no" while the output
  blames the model - this happened, and the tool now separates "the answer shape changed"
  from "the score is low" in what it prints.

## D-037: Topic exclusion lives in the display layer, not in front of the fetch

**Status:** Active

`dailyExcludeTopics` / `--exclude-topics` stop a topic from appearing in the daily report and
the reader page. Articles are still fetched, judged and archived; the topic is not shown. The
focus topic (AI) cannot be excluded - asking for it prints a warning and ignores that entry.

**Reason:** the feature was asked for as "decide before fetching whether I want this article".
That was measured and rejected: from a source name, a title and the platform digest - all that
exists before a fetch - the judgement agreed with the source-level configuration on **60%** of
217 articles, and where it disagreed it was mostly wrong in the direction that drops articles
worth keeping. There is no gold standard either way, so "the model said 新闻" cannot justify a
dropped fetch. The asymmetry decides it: an unwanted article costs a little space and attention,
while a wrongly skipped one is **gone** - it was never archived, and re-fetching a 公众号 page
weeks later is not the same article. The display layer also buys a cheaper correction path:
changing your mind is a regeneration, not a refetch.

**Consequences and boundaries:**
- Exclusion is **orthogonal**: it is checked before `--include-all` and before the inclusion
  score, so no flag combination brings an excluded topic back. All three lists in **我拿不准的**
  (near-threshold admitted, near-threshold not admitted, low topic confidence) are filtered too.
  This is not cosmetic: the "not admitted" list is populated by exactly the articles being
  excluded, so passing the exclusion set to `admits()` there does the opposite and makes them
  *more* likely to be listed. One predicate (`is_excluded_topic`) serves both directions.
- The focus topic is protected because excluding it does not produce an empty report, it
  produces a misleading one: with nothing left, the report exits with "未找到文章，请先运行
  biz_daily.py" and blames the wrong thing. Unknown names and refused names both print a WARN;
  silently ignoring either would make an ineffective filter look like a working one.
- Both views take the same set, and `generate_html.collect_articles` skips an excluded topic
  directory outright rather than filtering after the scan, so the page and the report cannot
  disagree about what was excluded.
- The key is registered in all four `configService` literals plus `bin`'s `configurableKeys`;
  `test/config-keys.test.ts` asserts every key in that list is declared, defaulted, reset and
  read back.

## D-038: The source-level prior is recorded from real judgements, and acts on nothing

**Status:** Active

Every daily run appends `(source, topic)` counts for the articles it archived to
`~/.weflow-cli/source_topics.json`. The run reports which sources are now stable and which of
them publish a topic you exclude. It does **not** skip anything.

**Reason:** the per-article judgement D-037 rejected is unreliable because its input is thin.
There is a second signal, but only after the fact: every archived article has a topic decided
from its **body**, so a source's history answers what a title cannot. That table has to be
measured rather than authored - a hand-written source-to-topic map would be a guess wearing a
table's clothes, and D-037 exists because that guess was already measured at 60%. Recording is
therefore a prerequisite for ever making the skip decision, not the decision itself.

**Consequences and boundaries:**
- Counts are **append-only**: cumulative counts are the only evidence for "this account is
  stable", and pruning them destroys the basis for a later decision. A failed write reports the
  OS error and leaves the run alone - the table is auxiliary data and must not fail a day's
  daily - but it does report, since a silent failure looks exactly like a table that is growing.
- Only articles that landed on disk are recorded (matched against `written_urls`), so the table
  and `output/` describe the same corpus and can be reconciled later. This was a real trap:
  `topic_groups` is rebuilt for the briefing as topic-to-strings before the end of the run, so
  recording from it produces an empty table.
- The file holds 公众号 names, so it lives outside the repository; a test asserts the path is
  under the home directory and not under the repo. It follows `CONFIG_PATH` at call time, so
  tests - and any future multi-profile setup - can point it at a temporary directory.
- `stable_source_topic` returns `None` for "not enough history yet" as distinct from "no topic".
  The distinction is the point: `None` read as a default topic would skip a source precisely
  because it is unknown, which is the worst failure this feature has.
- Thresholds (8 samples, 80% share) are **provisional and uncalibrated**, chosen so the first
  weeks of data cannot produce a confident wrong answer. Whether a stable source is ever skipped
  before the fetch remains the user's decision; the numbers are printed each run so it can be
  made on data rather than on a promise.

## D-039: The daemon confirms at the parent, and reports a child that dies

**Status:** Active

`assistant start` spawns `assistant run` detached. The spawn now passes `--yes`, and
`startDaemon` observes the child for a short window before reporting success. A dead env marker
(`WEFLOW_ASSISTANT_DAEMON`) was deleted.

**Reason:** the child was spawned without `--yes`, while `assistant run` confirms through
`inquirer` - and the daemon gives it `stdin: 'ignore'`, so nobody could ever answer. The child
died on the prompt (or later, on the missing channel login) while the parent printed
「✓ 守护进程已启动 (pid …)」 and wrote the pid file. On the machine this was developed against,
`~/.weflow-cli/` had **no `assistant.log` and no `assistant.pid` at all**: the documented
`assistant start` flow had never once completed. The env marker that appeared to handle this
was read by nothing, and was constructed as an env *key* containing `=` (`'…=1'`), so it could
not have worked even if something read it - a dead mechanism that made the path look guarded.

**Consequences and boundaries:**
- Confirmation stays **at the parent**: a human typed `assistant start` (or a machine passed
  `--json --yes`). The child inherits that decision as an explicit argument, not as an ambient
  env var - an env-var-based bypass would be inherited by every grandchild process, which is
  exactly the shape the repo's permission rules avoid.
- The failure is now **observed, not assumed**: the child is watched for `SETTLE_MS` (default
  700 ms) and a child that has already exited is reported with its exit code and the tail of the
  daemon log, and **no pid file is written** - writing one would be a claim that it is running.
- A spawn `'error'` event is now handled; without a listener Node turns it into an uncaught
  exception in the caller.
- Verified live: `assistant start` on this machine now says
  `子进程启动后立即退出 (code 1)；日志尾部: Error: 未登录消息通道, 先运行 weflow-cli login-wechat`.
  The fix did not break the daemon - it made a broken state visible.
- The daemon runs `dist/bin/weflow-cli.js` **when it exists**, so source changes need
  `npm run build` before the daemon reflects them. That precedence is a live operational trap,
  now written down in OPERATIONS.md.
- Untested and left untested: the daily quota (`100 条/天`) and the whole `start()` message
  loop, because both require a logged-in WeChat channel. The per-message logic is covered by a
  synthetic harness instead (stubbed `callLLM`, local-only tools).

## D-040: The fetch guard is a denylist of known forms, and it says so

**Status:** Active

`assistantTools.isSafeUrl` decides whether the assistant may fetch a URL found in a favourite.
It is now covered by tests, and two real holes were found and closed: IPv6 forms were not
handled at all (`[::ffff:127.0.0.1]` - an IPv4-mapped loopback - as well as `[fd00::1]` and
`[fe80::1]` were **allowed**), while the private-range regexes were matched against any
hostname, so a legitimate public domain such as `10.example.com` was refused.

**Reason:** the guard runs immediately before a `fetch` made by a process that also holds the
local database handle, and its failure mode is silent in both directions - a bypass reaches
loopback or a cloud metadata address, and an over-block merely looks like a broken link
(`(链接不安全, 拒绝抓取: …)`). Neither raises. Testing it was the only way to find out which
direction it was wrong in; the measurement also settled an assumption: Node's URL parser
normalises integer IPv4 forms (`2130706433`, `0x7f000001`) to `127.0.0.1` **before** this
function sees them, so the dotted-quad check already covered them - that is now a test rather
than a belief.

**Consequences and boundaries:**
- IPv6 is handled by prefix: `::`, `::1`, `::ffff:` (IPv4-mapped), `fc00::/7` (unique-local),
  `fe80::/10` (link-local) are refused. `[0:0:0:0:0:0:0:1]` arrives already normalised to
  `[::1]`.
- The dotted-quad private-range checks now apply **only when the hostname really is four
  dotted octets**, which is what stops `10.example.com` from being caught by `^10\.`.
- This is a denylist of known forms, **not a proof that an address is publicly routable**.
  NAT64 (`64:ff9b::/96`) and similar mappings are not covered. The comment in the code says so,
  so nobody reads the guard as a stronger guarantee than it is.
- The pure helpers (`isSafeUrl`, `stripTags`, `extractText`, `extractFromChallengePage`) are
  exported for testing. They take strings and return strings - no side effects, no configuration.

## D-041: The memory file carries a version, and an unknown version is refused rather than migrated

**Status:** Active

`~/.weflow-cli/assistant_memory.json` now starts with `version: 1` and puts every conversation under a
`users` key. A file without a `version` is the previous shape (v0, conversations at the top level) and is
**migrated**; a file whose version is anything else is **refused**, renamed to
`assistant_memory.json.unreadable-<timestamp>`, and the assistant starts with an empty memory and says so
in the log and the audit.

**Reason:** the format had no version at all, and the repo freezes schemas for its other artifacts
(`docs/SYNC_CONTRACT.md`, the `reconstructed` provenance block in `.articles.json`) — memory was the
exception, and the cost of that lands on the first migration. The write criterion came from the same
audit as the rest of this change: `deepseek-harness` pins its session format at `0` with the note that
**no compatibility is implied and no migration is provided**, and rejects anything else at load. That
combination (field + written criterion + refuse-don't-guess) is cheap and removes the "we will figure it
out later" debt.

**Consequences and boundaries:**
- **What counts as a structural change** (this criterion is part of the format, not an afterthought):
  renaming or removing a field, changing a field's meaning or units, or changing the key space (the
  `users` layer). **Adding an optional field does not** — readers ignore fields they do not know, and a
  test pins that a same-version file with unknown fields still loads.
- v0 is migrated because **we wrote it** and know its exact shape. Unknown versions are refused because we
  do not: reading another producer's fields by guess is how silent corruption gets in.
- Refusing must not destroy data: the rename to `unreadable-<timestamp>` is mandatory and the startup log
  names the file. A file that cannot be parsed at all takes the same path — it may be the user's only copy.
- The same audit produced two other changes here rather than the file format: the compression **retains by
  ratio, not by a fixed turn count** (a fixed count is window-independent and goes wrong the moment the
  model changes), and the summariser prompt is a **fixed eight-section skeleton** with an explicit merge
  law ("keep what is still true, drop what is stale, never copy the previous summary verbatim"). The two
  gates differ in what they retain, deliberately: the turn gate keeps about half, the budget gate keeps
  what fits 16% of the budget and lets the character bound win over the turn floor, because six 6,000
  character turns are 36,000 characters and no turn floor justifies exceeding the input budget.

## D-042: The assistant looks at one image per request, and the picture is what must not leak

**Status:** Active

The assistant gained a `look_at_image` tool. `get_messages` renders an image message as
`[图片 #<local_id>]`, the model calls `look_at_image {contact, image}`, the reader decrypts that one
image **locally** and the service attaches it to the next request as an OpenAI `image_url` content part.

Images ride a side channel (`ToolContext.pendingImages`) and are turned into multimodal content in
**exactly one place** (`toApiMessages`). Memory, redaction, quota and the audit keep operating on text
`content`; nothing else in the codebase has to know that content can be an array.

**Reason:** 15.5% of the messages in one measured 30-day archive are images (242 of 1564), and the model
saw `[图片]` and nothing else - the largest remaining capability gap, and one no amount of prompt work
closes. The alternative that was measured first got rejected on the same evidence: voice is 0.8% (12
messages a month), so a transcriber on the read path would never pay for itself.

**Consequences and boundaries:**
- **An image is egress, and it is treated as egress.** `strict` mode ("third-party chat text does not
  leave the machine") holds images back at **two** layers: the tool refuses to fetch *and* refuses to
  hand anything back, and `toApiMessages` drops whatever got through anyway. The tool layer is the cheap
  one; the request layer is the one that has to be right, because "the tool was willing" and "the gate
  let it out" are different statements. A drop is **stated in the body it attaches to**, never silent: a
  request that quietly lost an image looks to the model exactly like one that never had it.
- `get_messages` shows the `#N` handle **only when the mode allows images out**. Advertising a handle
  that the tool will certainly refuse is worse than not mentioning it.
- Looking is **on demand, one image per call, at most two per turn**. That is not a technical limit: both
  cost and exposure scale with pixels, and "the model chose to look at this picture" is a decision that
  belongs in the audit. Local engines (`ollama`, `lmstudio`) are exempt from the hold, and no
  `IMAGE_SENT` line is written for them - that line records **egress**, and writing it for a call that
  never left the machine would make the log lie about where data went.
- Audit gained `IMAGE_SENT <bytes> n=<count> ids=<local_ids>` and `IMAGE_HELD <count> strict`. Byte
  counts and ids only, **never content**, like every other audit line.
- Images never enter memory. Turns are stored as text, so a later turn sees the sentence the model wrote
  about the picture, not the picture. That is both the honest behaviour and the cheap one.
- **Where the time goes, and the one cache.** Building the media index is the entire cost of a read, and
  it scales with the conversation: measured 0.9 s for a direct chat and **18.9 s for a group with 30,315
  messages** (that group's local media is thumbnails only, 290×435). The resolved, downscaled bytes are
  therefore cached at `output/.cache/read-image/<md5(talker)>/<local_id>.json`, which makes a second look
  at the same picture cheap. **Only hits are cached** - a miss may be "WeChat is not running" (see below),
  which is a state that changes, and caching it would make it permanent. The timeout is 90 s (≈4× the
  worst measured) and a timeout is reported as a failure rather than silently retried: the assistant
  handles messages serially, so one stuck read blocks every later message.
- **"This picture is not here" and "I cannot see it right now" are different answers.** The V2 thumbnail
  key is derived from a file WeChat maintains while running; with WeChat closed the derivation returns
  nothing and most `.dat` thumbnails drop out of the index **silently**. A miss therefore carries a hint
  saying which of the two it is, instead of reporting a state that may be false.
- What this does **not** do: no describe-once-and-cache, so looking at the same picture twice still costs
  two vision calls (only the local bytes are cached); no vision on stickers (type 47) or video (43), only
  type 3 images; no OCR fallback when the model cannot read something.
- Known limit: an image whose local copy is genuinely gone returns a stated reason rather than a picture.
  The tool says so; it does not pretend to have looked. Note the honest scope of that claim: across 380
  sampled messages every image resolved, so the miss path was exercised by removing index entries, not by
  observing a real eviction.

## D-043: The assistant records a per-turn trace, and the audit stays what it was

**Status:** Active

Every assistant turn now writes one record to `~/.weflow-cli/assistant_trace.jsonl`: the fast-route's
decision, each tool call with a **redacted argument summary**, how many model round trips it took, why
it stopped, and how much reasoning the model returned. Two ways to read it: `weflow-cli assistant trace
[-n N]` on the machine, and the in-chat command `轨迹` for the last turn (no `userId` in that one).

**Reason:** the assistant's only output used to be the reply itself. Everything in between lived in the
audit as separate event lines - `TURN_DONE tools=5` says five tools were called and nothing about which
five, in what order, or with what arguments. When the answer looked wrong there was no way to see which
step went wrong, and the one time this mattered (a turn that called `search_favorites` twice and
`read_favorite` twice) the arguments were not recorded, so the question could not be answered from the
log at all.

**Consequences and boundaries:**
- **The audit is unchanged and must stay narrow.** It records events and byte counts and **never
  content**, because it is the egress record. The trace is a *local* debugging artifact and does contain
  redacted argument summaries - that is the whole point of it, and it is why it is a separate file with
  a separate reader rather than more fields on the audit line.
- Argument summaries go through `privacyGate.redact` and truncate each value at 40 characters. The
  in-chat rendering (`轨迹`) is the only path where they leave the machine, which is no more than the
  reply itself already does; `userId` is deliberately omitted there.
- **Reasoning is whatever the model returns**, surfaced from `reasoning_content` and clipped at 800
  characters. The default model (`deepseek-chat`) does not return it, so the field is usually empty and
  the trace says "无" - it does not pretend. Switching to a reasoning model fills it in; nothing else
  changes. The reasoning text is **not** fed back into the conversation: it is the model's monologue,
  not content for the user.
- **"Did this step produce anything" is a convention, not a field.** The rule is "the result starts
  with `(`", which is how this codebase already writes every "could not do it" reply (`fail()` and the
  per-branch misses); the single exception, a success that also starts with `(`, is a whitelist entry
  guarded by a test that scans the tool source. Measuring the honesty of this: a wrong answer here
  costs one extra or missing "（无内容）" marker in a debugging view. The alternative - threading an
  outcome through roughly forty return sites - was priced and not done; if a future feature needs a
  machine-readable outcome (say, retrying differently on failure), that is the change to make, and this
  convention should then be deleted rather than extended.
- The trace is capped at 512 KB, trimmed to the most recent 200 turns. It is not a metrics store and
  there is no aggregation over it: `assistant trace` prints turns, it does not compute rates. The
  behaviour eval (D-042's sibling work) is where aggregate numbers come from, because it asserts
  conditions instead of dumping history.
- Writing a trace can never break a turn (failures are swallowed), which is the same posture
  `AssistantMemory.save` takes - with the same obligation: because it is swallowed, `recordTurn`
  creates `~/.weflow-cli` if it is missing, or a fresh machine would silently record nothing. That was
  caught by the feature's own tests.

## D-045: The local panel is a token-gated loopback endpoint on the existing daemon, and the panel is only a client

**Status:** Active

The assistant gained a second entrance: a small always-on-top panel window on the same machine, so
talking to it does not require logging into the WeChat channel. It is implemented as a
**loopback-only HTTP endpoint inside the assistant daemon** (`src/panel/server.ts`), plus a client
(`weflow-cli panel`, the page under `resources/panel/`, and an Electron shell). The panel holds no
assistant of its own: every turn goes through the daemon's existing `runTurn` — the same allowlist
path for WeChat, the same daily quota counter, the same serial queue, the same memory file.

**Reason:** the quota counter, the serial queue and the instance fields it protects (`turnCalls`,
`lastReasoning`) are **in-process state**. A panel that built its own `AssistantService` would get a
second quota (each entrance 100/day, total unbounded), a second queue protecting nothing, and two
processes writing the same user's memory window (the file's read-merge-write protects *other* users,
but for the same `userId` it is still last-writer-wins). The cheapest correct shape was therefore one
host process with two entrances.

**Consequences and boundaries:**
- **Loopback only, not configurable** (continuing D-004), plus two gates the reader does not have.
  `scripts/fav_server.py` has **no token at all** and its origin check **allows a request with no
  Origin header**; copying that here would hand an endpoint that reads chat data to any local program.
  So: **every request carries a token** (`/api/status` included), Origin is a second gate (present but
  not allowlisted → 403 even with a correct token; **absent → allowed**, because `file://` renderers
  and `curl` look like that), and POSTs must be `application/json`.
- **The token is per-run and lives in a file next to the pid file** (`assistant_endpoint.json`, atomic
  write, read-merge not needed). It is **not** claimed to be protected by file permissions: on Windows
  `fs.chmod` / `mode: 0600` do essentially nothing, and what actually helps is the default ACL on
  `%USERPROFILE%\.weflow-cli\`. The token's real job is to stop **other local programs and any web
  page**, not a same-user process willing to read that directory — such a process can already read the
  database. **Cleanup is not relied on**: `stopDaemon` sends SIGTERM and Node does not run handlers for
  it, so the file is expected to be left behind; readers probe `kill(pid, 0)` and then verify the
  `service` + `startedAt` fields before trusting a port.
- **The panel's identity is a memory bucket, and the bucket decides whether it is "one brain".**
  Memory facts are stored per `userId`, so sharing means using the same string as the WeChat DM. The
  bucket resolves to the **single** entry of `assistantWhitelist` when there is exactly one; with zero
  or several it does **not guess** — it falls back to a `panel` bucket and the interface says so, and
  `assistantPanelUser` pins the choice. Note what is inference and what is observed: the chain
  (DM `conversationId` == `senderId` == the allowlist value) is sound in the code, but it has **never
  been observed on a real inbound WeChat message** — the channel on this machine has never completed a
  login. The panel displays its resolved bucket so the first real message can be compared by eye.
- **Credentials never enter a command line.** Windows exposes any process's argv to any local user
  (`wmic process get commandline`), so the Electron path passes **no** arguments and reads the
  endpoint file itself, then installs the token as an `HttpOnly` cookie before loading the page. The
  browser fallback cannot do that, so it uses a **one-time, 60-second, single-use code** from
  `/api/pair` — a code in argv or in browser history is spent, a token there would not be.
- **`AssistantService.start()` no longer refuses to start without a channel.** It used to throw
  `未登录消息通道` before doing anything, so "not logged into WeChat" meant "no assistant at all"
  (recorded in PROJECT_STATE as a field observation). Now the channel is optional and the local
  entrance still comes up. The cost is that `assistant start` can now report success with an empty
  channel, so `assistant status` gained `channelActive` / `mode` / `panelPort` / `memoryBucket` —
  `messageChannelLoggedIn` only ever meant "is a token configured".
- **The panel authenticates by token, not by the WeChat allowlist.** Reusing `evaluateAssistantAccess`
  would deny the panel when the allowlist is empty (i.e. by default) and, worse, print the
  "run `config set assistantWhitelist <id>`" bootstrap hint telling the user to add **their own panel
  identity** to the WeChat allowlist. The allowlist answers "who may talk to me in WeChat"; it is not
  the right gate for the person sitting at the machine.
- **Verified on Windows 11 with Electron 42** (the binary had to be downloaded first - it was
  declared but not installed, so the browser fallback was the only path for a while): the ball window
  measures 76x76 with no caption (frameless) and `WS_EX_TOPMOST` set, the renderer loads the page and
  authenticates **through the cookie** (visible in the daemon log as `[panel] 界面已加载（cookie）`),
  and the ball is visible on screen - confirmed by eye, because **GDI screen capture does not capture a
  transparent layered window**, so a screenshot showing nothing at that spot is a false negative.
  The interactive paths were then driven **without** a click: over the DevTools protocol the ball was
  really clicked (the window measured 421x560, always-on-top dropped) and collapsed again (77x76,
  always-on-top restored); the global hotkey was verified differentially - a second Electron app
  registering the same chord gets `false` while the panel runs and `true` once it stops, so the panel
  genuinely holds it. Driving it that way found **two real bugs that a screenshot could never have
  shown**: `setMode` locked the window non-resizable *before* resizing it, and Windows ignores
  `setSize` on a non-resizable window, so collapsing left the window at chat size (a giant ball); and
  the `ready-to-show` listener was attached *after* `await loadURL`, so when that event fired during
  the load it was never replayed and the window stayed hidden - geometry and style all measured
  correct, only `IsWindowVisible` was false. A third bug came out of exercising what the tray items *do* rather than clicking them: the
  "quit and stop the assistant" action spawned the CLI as `spawn(electron, [cli.cjs, …])`, which
  fails with `error: unknown command '…\cli.cjs'` because commander in Electron's Node mode does not
  skip `process.argv[1]`. The repository already knew this (`bin/weflow-cli-electron.cjs` documents
  it); the working form is `-e "import('file:///…')" -- <args>`, with the path passed through
  `pathToFileURL` because this checkout's directory name is non-ASCII. Verified by running that exact
  shape: the daemon stops and both the pid file and the endpoint file are cleaned up. The one thing
  still unverified is a **click on the tray menu itself** - its contents are pinned by static
  assertions, and each item's effect has been exercised directly.
- **Placement and drag were then closed too, and they were not merely untested - they were wrong.**
  The window was created without `x`/`y`, so it landed wherever Windows put it (measured 815,418,
  mid-left) - not "a ball in the corner". It now defaults to the primary work area's bottom-right
  corner, **remembers where it was dragged** (`~/.weflow-cli/panel_position.json`, atomic write), and
  falls back to the corner when the remembered spot is unreachable (a monitor was unplugged, or the
  ball was dragged off-screen). Expanding to the chat window and collapsing back both re-fit into the
  work area, because a 420x560 window opened at a bottom-right corner would otherwise hang off the
  screen. Two details worth keeping: the listener is `move`, not `moved` - `moved` fires on
  `WM_EXITSIZEMOVE`, which a programmatic move never produces (measured: the window moved and the
  position file was not written) - and only the **ball's** position is saved, since the ball is the
  anchor and the chat window's geometry is transient.
  The arithmetic lives in `resources/panel/ball-position.cjs` so it can be tested in CI with
  **synthetic monitor layouts** - negative-x second displays, a removed display, a rect larger than
  the work area - because **this machine has one monitor and cannot produce those**. What that does
  not cover: a real second display. The code path is exercised with synthetic coordinates; plugging
  in a second monitor is still an unperformed experiment.

## D-044: "You have no todos" and "todos were never extracted" are different answers

**Status:** Active

`extract_todos.py list` now reports whether the todo file exists, and every reader keeps the two cases
apart. The bare-array shape of `list --json` is unchanged; the distinction rides on a new `--meta` flag
(`{items, extracted, count}`). The assistant tool calls `list --json --meta` and, when `extracted` is
false, answers "these have never been extracted - run `weflow-cli todos extract --days 7 --yes` first;
until then this list is empty, which does not mean you have nothing to do" instead of "no pending
todos". The terminal output of `list` and `remind` says the same thing.

**Reason:** Extraction is a deliberate, confirmed action (`todos extract` carries its own `--yes` gate),
so on a machine where it has never been run the list is empty for a reason that has nothing to do with
the user's workload. Reporting that emptiness as "nothing to do" asserts a state that was never
established - the same failure the `(未查到会话, 数据库可能未连接)` wording avoids. It was not
hypothetical: on this machine `~/.weflow-cli/todos.json` does not exist at all, and the assistant's
`get_todos` was answering `(没有待办任务)`.

**Consequences and boundaries:**
- The `--json` array shape stays because it is a published capability (`bin/weflow-cli.ts` maps
  `todos: { cli: 'todos list --json' }`), so a marker inside that array would have been a compatibility
  break dressed up as a fix. The tool also tolerates the old shape: if it receives an array rather than
  the meta object, it falls back to the previous wording rather than reading `undefined.items` as
  "never extracted".
- The MCP side (`mcp_bridge.py`) has always separated these two cases - it already said "no todo file
  yet, run extract_todos.py first". The assistant tool was the one implementation that dropped the
  information, which is what made this look like a tool defect rather than a missing feature.
- Not fixed: nothing here makes extraction happen. The pipeline that would run it on a schedule is the
  same open question as the daily report's (no scheduled task is registered on this machine).

## D-046: Atomic writes retry a busy target, and fall back to writing in place

**Status:** Active

`writeFileAtomic` (`src/utils/atomicWrite.ts`) writes `<target>.tmp` and renames it over the target, as
before - but when the rename fails with `EPERM` / `EACCES` / `EBUSY` it retries five times with a short
synchronous backoff, and if the target is still busy it **writes the target directly** instead of giving
up. Both `AssistantMemory.save()`, `configService.save()` and the panel's endpoint file go through it.

**Reason:** on Windows, `renameSync` over a file that **another handle has open** fails with `EPERM` -
measured: a second handle opening the target read-only is enough, and the rename succeeds the moment that
handle closes. Antivirus, search indexers and other readers produce that state briefly and routinely. The
callers all swallow the failure (they record a reason and carry on), so the result was a **silent lost
write**: one full-suite run here saved the memory file and the file came back without its `version` field,
with the test itself green the other three times. "Saved" and "never saved" must not look the same.

**Consequences and boundaries:**
- The fallback gives up **atomicity** for that one write, not correctness of the bytes: the same string is
  written either way, so a reader never sees a half-written file. What is lost is the guarantee that the
  replacement is instantaneous.
- If the other handle holds the file with a deny-write share mode, the direct write fails too and the error
  propagates, exactly as before. The fallback is best-effort, not a guarantee - do not describe it as one.
- Deciding to fix this was not cosmetic: this repository already treats a silently failed save as a defect
  (there is a CHANGELOG entry about `MEMORY_SAVE_FAILED`), and the flake was the same class of thing
  arriving from the filesystem instead of the code.

## D-047: Drafted replies produce text and stop there, the risk gate rides on the binary questions, and the assistant path masks before the script sees anything

**Status:** Active

`draft` (CLI) and the assistant tool `draft_reply` ask a decision model seven typed questions about one
conversation (intent / need / action / should this get substance / risk 0-9 / money / unfulfilled
commitment), refuse when the answer is money **or** risk >= 7, otherwise generate N candidate replies and
rank them. The output is **text only** - there is no path from here into WeChat, no input-box filling, no
key simulation, no focus stealing. `capabilities --json` declares `sendsNothing: true` alongside
`writesNothing`.

**Reason:** the pattern comes from reading `jev-chat-jarvis` (an Android assistant that reads the screen
through accessibility, decides, and drafts three replies into the input box without ever sending). Two
things it does *not* do are the two things worth doing here, and both were read from its source rather than
assumed: its danger level (0-9) is consumed **only** as a badge colour - it never refuses a draft, degrades
or blocks - and its judgement result is **not** in the drafting prompt (`ReplyClient.draft` takes no
`Analysis`), so "draft with the judgement's sense of proportion" is missing. Its "never send" is its
boundary; here sending was already structurally unreachable (`TOOL_DEFS` has no send tool, D-008), so the
risk this feature adds is not a mis-send but **making it easier to send a message the user will regret** -
which is what the gate is for.

**Consequences and boundaries:**
- **The gate rides on the binary questions, not on the 0-9 score.** `money` is a hard refusal; `risk >= 7`
  is a refusal whose advice is taken from the `action` answer ("go read the earlier history first"). The
  0-9 band is calibrated in wording but its **threshold is picked, not calibrated**, so it explains
  ("risk 8/9: it has already turned into a fight") and does not decide alone. `commitment` deliberately
  does **not** refuse by default - "I promised and went quiet" is exactly when a draft is most useful - it
  constrains the prompt instead; `--gate-commitment` makes it a hard refusal, which is the trade this
  record exists so someone can reverse.
- **The English criteria are borrowed, and that choice is unverified here.** The question set follows the
  reference implementation, which was calibrated against a 30-conversation Chinese set in its own
  (intimate-relationship) domain; the criteria are English because its `TASK.md` states the model's main
  training language is English. `reply_debt.py` still asks its own money/commitment questions in **Chinese**
  and those two are reused **verbatim**, so this repo now runs two wording conventions against one model
  with **no A/B run**. Recorded as an open question, not as a settled choice.
- **Two input paths, and the mask asymmetry is deliberate.** The CLI reads the database itself and does
  **not** mask - the user ran it explicitly and the preview states how many characters go to which models
  (same as `awaiting`). The assistant tool masks every message through `privacyGate` and hands the result
  to the same script **over stdin**; Python has no redaction implementation at all (`scripts/` contains
  only two unrelated bit-masks), and the one implementation lives in TypeScript. `strict` mode **refuses**
  the tool outright rather than drafting from a body masked into "content N characters", because that
  produces a fluent answer built on nothing.
- **`isLocalInference()` is deliberately *not* an escape hatch here**, unlike `look_at_image`. That image
  really does go to the local model, so "local inference" is a valid bypass there; drafting's egress is the
  **script's own** two remote calls (`jev_client` over HTTP, `_utils.call_deepseek` hard-wired to DeepSeek),
  which `aiEngine` does not touch. The first implementation copied the bypass from `look_at_image` and a
  test caught it: with `aiEngine=ollama` + `assistantPrivacy=strict` the transcript was handed to the
  script. Verified by measurement, then removed.
- **Clicking a name in the panel's quick-reply menu *is* the consent.** That menu (right-click; the list comes from
  the `quickReplyContacts` config key) turns one click into a draft for one person - which means that conversation goes
  to two cloud models. So the cost is written **inside the menu** rather than discovered afterwards, and the menu
  deliberately does not exist in the browser fallback, where the native right-click menu (with Copy) belongs to the
  user. The menu is a **native** one for the same reason: a page-drawn menu cannot leave the window and the ball's is
  76x76, so the first attempt expanded the window to make room - which turned a quick action into "right-click opens the
  chat window" (the user's words). A native popup draws outside the window, so nothing has to move - and because the
  chat window is deliberately *not* always-on-top, expanding it now also raises and focuses it: closing a native
  popup can hand focus back to some other window, and the next complaint was "I do not know where the answer
  went" (it went into a window that was behind something else). The conversation shows a readable turn
  ("快速回复：<name>") while what is actually sent is the explicit tool-naming request. Nothing here sends a
  message: the chain is structurally text-only, same as everywhere else. The menu's **last item is not a consent at
  all** - it hides the ball and reaches no model - and it is the one item that survives an empty contact list, because
  a menu of two grey lines with nothing clickable gives that user no way to dismiss the ball. It **hides rather than
  quits**: the exit actions stay in the tray menu, which already separates them from show/expand, and hiding is the
  reversible choice. The label therefore has to name the way back (`关闭悬浮球（托盘图标能再打开）`) - the ball runs with
  `setSkipTaskbar(true)`, so a hidden ball leaves nothing on the taskbar to click, and a label saying only "关闭" would
  read as "it is gone for good".
- **The transcript line has one shape, and a wxid is not a speaker label.** The line the judgement and drafting steps
  read (`[09-23 12:20] 老王：…`) is rendered in two places - `assistantTools.transcriptLine` on the tool path (mask in
  TypeScript, then send the rendered lines over stdin) and `reply_debt.format_line` on the CLI path (which reads the
  database itself) - and they had drifted in four ways with nothing failing. The rule that came out of fixing it is the
  second half of the title: `senderUsername` is a raw column holding the sender's **wxid**, not a name, so neither side
  may use it as a label; a resolved display name (`senderDisplay`) is used when there is one, and `对方` otherwise.
  This was first recorded as a trade-off, on the assumption that the alternative to a wxid was `对方` and that
  collapsing a group's speakers would cost the ability to answer "who said that". **Measuring retired that
  assumption**: across 8 sessions and 179 messages from other people, `senderDisplay` held a name 174 times,
  `senderUsername` was guid-shaped 176 times, and the two were never equal. So the name keeps speaker distinction
  *and* removes the identifier, and `get_messages` was changed to use the same rule rather than left on the raw
  column. The rule is one function (`speakerLabel`), because "who is speaking" is the kind of thing that grows a
  second implementation.
- **The judgement step is degradable now, and the degradation says so.** Jev's free period ended on
  2026-09-25; when that call fails (expired, bad key, or a dropped connection - the first failure seen here was
  an `http.client` read error) the tool no longer refuses to draft. It falls back to DeepSeek alone: three
  candidates, no ranking, and **no gate** - because the gate (money, or risk >= 7 -> no draft) exists only
  because a judgement exists. That is a deliberate trade the user asked for, and it is only acceptable because
  the output **states it**: `judged: false` plus a `judgeNote` travel with the result, the CLI prints
"这次没有判断…闸门没生效" before the candidates, and the assistant tool prints the same. A fallback that
  looked like a normal run would be the worst outcome here, and the tests pin both directions (with a
  judgement `evaluate_gate` is consulted exactly once; without one it is never consulted). The consequence
  for availability follows: `deepseekApiKey` is now the only required key, and a missing `typesafeApiKey` no
  longer hides the tool - hiding something that can run would be its own kind of lie.
- **The MCP surface gets a stricter boundary than the panel does, on purpose, and it applies to all four tools that egress.** `draft_reply` is on the
  MCP tool list (that list is *derived* from the assistant tool table minus `save_memory` and `look_at_image`, so
  anything added there appears there too, and a tool that cannot work on that transport has to be excluded
  explicitly - `look_at_image` hands its image to the assistant's model through a side channel that MCP never
  reads, so it used to answer "you can see it now" while shipping nothing) and an MCP call that does not pass `confirm: true` returns a **preview only** -
  how many messages, how many characters, which two models - with nothing leaving the machine. The panel and
  the WeChat bot do not need that flag: their boundary is the sender allowlist plus a user asking in a
  conversation only they can see. The same gate covers `who_owes_reply` (every conversation's text, one
  request each), `search_chats` (the question plus words pulled from the chats) and `search_semantic` (the
  query, then the hits) - one shared message shape in `confirmPreview()`, with each tool filling in what it
  actually sends. Two of them can produce real numbers for the preview by running their script's own
  `--dry-run`; the other two say it from their arguments, because `route_cards.py --dry-run` prints plain
  text rather than JSON and `semantic_search.py` has no `--dry-run` at all. An MCP client is a **third-party process** whose session nobody here can
  observe, so its default is "show me the cost first". The tool description states this, because a calling
  model that does not know it cannot ask its user.
  Two claims next to this were wrong and are now corrected rather than quietly dropped: `capabilities --json`
  reported `safety.mcpDefaultReadOnly: true` (false - that surface has contained a file writer and a
  model-calling tool since `export_chat` and `look_at_image`), and an earlier revision of this record said
  drafting was deliberately kept off MCP. It was never off; it had simply never been *checked*, because the
  tool names are generated at runtime (`wechat.${name}`) and a grep for the literal `name: 'wechat.` cannot
  see them.
- **The panel only gets a copy button**, on the assistant's turns. It copies the whole reply rather than
  per-candidate lines: the visible reply is the model's own rendering, so guessing which lines are
  candidates would sometimes copy half a sentence. A failed clipboard write says so and selects the text
  instead of claiming success.
- Group chats are out of scope (the reference implementation documents the same limit, and local group
  semantics are not reliable here), as is any automatic labelling: there is no gold standard for "is this
  draft right", so the feature records what it judged and says so rather than claiming calibration.

## D-126: 排障输出必须**判定过**且**可整段复制**（`check --report`），脱敏是硬约束

**Status:** Active（起因：同一个根因在 2026-08～10 被报成了四种不同问题 —— issue #3「无法通过步骤4获取key」、
#9「密钥解不开了」、#10「读不到近期记录」，以及 2026-10-18 那封「sessions 找不到会话」的邮件）

**决定。** `weflow-cli check --report` 输出一段给人（和 AI）读的文本，包含环境、微信客户端、配置三项事实，
以及**结论**：每条结论是「卡在哪一步 + 下一步运行什么」。三条约束：

1. **判定，不是罗列。** 只把 `sqlcipher3: false` 打出来，用户仍然不知道这意味着什么；报告必须说
   「缺 sqlcipher3，运行 `pip install sqlcipher3`」，并且**不要把"缺 sqlcipher3"报成"Python 不可用"**
   （那会让人去重装 Python，问题不变 —— 有测试钉住这两条不能混）。
2. **脱敏是硬约束。** 报告里出现的东西只有：布尔状态、版本号、路径（用户名换成 `<用户>`）、
   命令名、依赖名。**密钥与口令的值连读都不读**（只传 `messageKeyConfigured: boolean`），
   聊天内容从不进入；`wxid` 与密钥形态字符串由 `redactForReport` 兜底替换。原因是这份文本的
   目的地是公开 issue / 第三方 AI —— AGENTS.md 明令禁止把这些放进去。
3. **纯函数生成。** `renderDiagnosticReport(input)` 不读文件、不碰配置，所以「结论对不对」「脱敏漏没漏」
   都能用单元测试钉；`--json`（给机器）与 `--report`（给人）共用**同一份探测结果**，避免两个分支
   出现「JSON 说可用、报告说不可用」。

**没做的（明写）**：不自动上传、不联网、不写入文件；不做通用 PII 扫描（过度打码会把
"路径里有中文目录"这种真线索删掉）。报告不会替代日志 —— 具体命令的报错原文仍要用户附上，
报告末尾也是这么写的。

## D-125: 运行时版本的第二观测：命令行没 `--user-lib-dir` 时看**实际映射的 `Weixin.dll`**

**Status:** Active（2026-10-18 本机实测：4.1.15.13 → 4.1.15.53 后主进程不再带该参数）

**Decision.** D-107 的判据保留（命令行 `--user-lib-dir` 是**首选**：那是客户端自己说的），
但**它不再是唯一来源**。命令行取不到时，取 `Weixin.exe` 进程实际映射的 **`Weixin.dll` 所在目录**；
两者都没有 ⇒ `version: null` + `note`，仍然**不**拿"磁盘上只有一个版本目录"去推断。
`note` 必须写清版本来自哪个观测（`命令行里没有 --user-lib-dir，版本取自进程实际加载的 Weixin.dll`）。

**Reason.【实测】** 本机升到 **4.1.15.53** 后，`Weixin.exe` 主进程的命令行**只有一个可执行路径**
（`"D:\WeiXin\Weixin.exe"`，没有任何开关），而 4.1.15.13 时至少有一个进程带 `--user-lib-dir`。
结果是 D-107 的判据在**新版本上恒返回"版本未知"**——`check` 看不出用户装的是哪一版，
而这正是它存在的理由（排障第一个要问的）。模块表给出的是同一件事的**另一种观测**：
`Weixin.dll` 此刻确实映射在 `D:\WeiXin\4.1.15.53\Weixin.dll`。改后 `check --json` 报
`version: 4.1.15.53`、`loadedDirs: [D:\WeiXin\4.1.15.53]`，并附一句来源说明。

**两条判据为什么要分开记来源。** 命令行与模块表量的是**同一个量**，不是两个独立证据：
命令行说 A、模块表也说 A 时，把它们混在一起去重会得到"两个目录指向不同位置"的假象，
版本号反而会丢。所以顺序是**先①后②**，且只在①为空时才看②；`note` 记录实际用的是哪个。

**边界（不猜的地方一个没少）。**
- 模块表要**同用户、活进程**才读得到；读不到（权限/进程刚起）就是 `null`，不退回磁盘推断。
- 模块表出现**多个**目录时照样不挑一个报版本（与命令行那条同一规则）。
- 仍不读 `Weixin.exe` 里硬编码的版本字面量（D-107 的理由没变）。
- 旁证一条，**不拿来推版本**：`--client_version` 的 token 从 `0xF2541F0D`（.13）变成
  `0xF2541F35`（.53）。这与 PROJECT_STATE 里 2026-10-11 解出的**低 16 位**规则一致
  （byte3 = 第四段 `0x35`=53；byte2 − 0x10 = 第三段 `0x1F−0x10`=15），是第三个吻合的样本；
  但高 16 位仍是 `F254`、三个样本都是 `4.1.x`，**主次版本分不开**，所以代码照旧不拿 token
  推版本号（模块表已经直接给出目录名，没有理由再走一条半解码的路）。

## D-124: `judgeProvider=chat` 必须尊重 `aiEngine` —— 本地引擎的用户不许被静默送出境外

**Status:** Active（2026-10-18；7 条单测覆盖分派，**未**对真实 Ollama/LM Studio 跑过端到端）

**Decision.** `chat` 通路先读 `aiEngine` 再决定往哪发：
`local`/`ollama` → `_utils.detect_local_engine()` 找到的本地服务（`provider='chat-local'`）；
`claude` → **明确拒绝**（这条通路只支持 OpenAI 兼容端点，要用云端判断请改 `judgeProvider=openrouter`）；
`deepseek` 或未配置 → 维持原样（`aiBaseUrl` 中转站或直连 DeepSeek）。
**本地服务检测不到时返回 `None`，绝不退回云端**，只打一行 WARN 说明这批判断会走 LLM 路径。

**Reason.** D-120 引入 `judgeProvider=chat` 时，那条分支无条件读 `deepseekApiKey`。
它的语义是"用**配置里的**对话模型"，而"配置里的"到底指谁只有 `aiEngine` 说得清。
所以对 `aiEngine=local` 的用户，一开 `judgeProvider=chat` 就把文章正文发去了 DeepSeek ——
**界面上什么都没变**，这不是多花点钱，是用户明确选了不出境之后又被送出境。
这与仓库既有的边界（"云端 AI 要保留显式权限边界"）冲突，属于必须改的缺陷而不是偏好。

**Consequences / limits.**
- 本地引擎的 `chat()` 只回字符串、没有 usage：`ChatJevClient` 在 `engine` 路径上把 `usage.model`
  记为引擎自己的 `model`，契约校验（`_check_choice`/`_check_score`）与云端逐字同一条。
- `claude` 不自动换成 `openrouter`：换厂商要用户自己按 key 与地区限制决定（D-120 的实测结论是
  OpenRouter 的地区限制逐模型生效，不能替用户默认）。
- 单测用假 engine 验证分派与契约；**真实本地服务的时延与 JSON 稳定性未测**，
  所以 OPERATIONS 里写的是"可用"，不写"已验证质量"。

## D-123: 重排的池子**不降级**（干净 A/B 否掉了"用池 20 换 5 倍速度"），并记一次混淆教训

**Status:** Active（2026-10-18 实测：五组 × 40 题；每组同题同种子）

**Decision.** `RERANK_CANDIDATES` 保持 **100**。重排那句 LLM 调用是单次查询 10 秒里的 ~9 秒，
但它是全流程**最大的质量杠杆**，把那 9 秒当作"可优化开销"是错的。

**Reason.【实测】** 五组结果（同 40 题、同种子）：

| 组 | 池 | 重排机制 | hit@1 | recall@5 | recall@10 | MRR | nDCG |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A | — | **不重排** | 0.550 | 0.650 | 0.750 | 0.594 | 0.630 |
| D | 20 | llm | 0.675 | 0.800 | 0.825 | 0.733 | 0.756 |
| **E** | **100** | llm | **0.825** | **0.925** | **0.950** | **0.863** | **0.884** |
| B | 20 | llm（契约先失败） | 0.675 | 0.775 | 0.825 | 0.726 | 0.750 |
| C | 100 | 契约（DeepSeek 满足） | 0.800 | 0.925 | 0.950 | 0.849 | 0.873 |

- **重排本身值很多**：A → E 是 hit@1 0.55 → 0.825、recall@5 0.65 → 0.925、nDCG 0.63 → 0.884。
- **同一机制下池 100 全面胜出**（D → E：+0.15 / +0.125 / +0.128），所以**不能拿池子换速度**。
- **B≈D、C≈E**：两种重排机制（noul 契约 / JSON 打分）在**同池**下几乎等价 —— 决定因素只有池子。

**这次差点犯的错（值得单记）：** 第一轮 A/B 里 B=`llm`、C=`typesafe`，我差点把 B→C 的差距
读成"池子从 20 到 100 的收益"。实际那个差距**同时混了两个变量**：C 的契约恰好被
`judgeProvider=chat` 的 DeepSeek 满足（`provider` 字段写的是契约名，不是谁在服务），而 B 的契约
概率和 0.94 被判失败、退了 llm。
⇒ **对照实验里，先把机制钉住再比参数**；状态字段要能区分"意图"与"事实"（`transport` 因此而生，见 D-122）。
另外我此前对用户说过"池 100 下重排没有可测增益"——那是把"契约段失败的 WARN"当成了"没重排"，
**同一类误读**；本次 A vs E 证明重排的增益很大。

**Consequences.**
- 查询延迟（~10 s）以重排为主，**这是买质量的钱，不要动**。真要提速只能换更快的模型/更小的提示
  （清洗摘录已做过），而不是砍候选。
- `--rerank-pool` 仍可手动调小（离线批处理、赶时间时），但**默认值不许改回 20**；
  `PoolTests` 钉着默认值，改它要带新数据。
- 记录的口径更新：`OPERATIONS.md` 的"重排效果"一节换成上表（旧数字是池 20 时代，
  "hit@1 0.55 → 0.65"那组已经不适用）。

## D-122: 检索的失败必须**显式**：`vector.used` / `vector.fallback` 进返回，`--mmr` 不再能崩

**Status:** Active（2026-10-18 代码审阅时读出来的两处，当天修 + 8 条测试 + 变异检查；全量 1067 绿）

**Decision.** `search()` 里那个**裸 `except: pass`** 换成 `except Exception as error`，并把
"这次到底走没走向量、没走是什么原因"写进返回的 `vector` 字段（`{used, fallback}`）；
`vectors` 在 `try` 之前初始化为 `None`，fallback 里的 MMR 判空；每线条数在 `search()` 里
**顺手算一次**（`_counts_from_meta`），`main()` 不再重复解析 meta。

**Reason.** 三件事是一起读出来的，性质不同：

1. **静默降级是最贵的一类失效。** 向量路径任何异常（embedding 上游失败、numpy 不可用、meta 坏掉）
   都会被那个裸 `except` 变成"只搜关键词"，而返回的 envelope 里照样写着 `hybrid: true` ——
   **两种质量完全不同的结果在输出里长得一模一样**。这还让所有"快慢/好坏"的对比都可能落在
   不同路径上（2026-10-18 我在性能普查里就差点基于它下结论）。
2. **一个真 bug**：`vectors` **只在 `try` 里赋值**，而 fallback 里的 `mmr_select(results, vectors, …)`
   用它。异常发生在 `np.load` 之前时（`require_numpy()` 就炸），`--mmr` 会 **UnboundLocalError**。
3. **一处 1.2% 的浪费 + 一个漂移风险**：`search()` 已经解析了 meta，`main()` 又调 `line_counts()`
   解析一遍（实测 69 + 56 ms）。两份解析同一份文件迟早会漂，而漂的表现是"条数对不上但谁都不报错"。

**Consequences.**
- 裸 `except` 还会吞掉 `KeyboardInterrupt` —— 一个跑 30 分钟以上的日报里，Ctrl+C 可能不生效。
  这次只改了最要紧的这一处，**其余 78 处没动**（数量上够做一条静态守卫，见 D-123 之后再说）。
- **`--mmr` 在向量失败时现在直接跳过 MMR**（它需要矩阵），而不是崩；`fallback` 里会写明原因。
- 测试纪律（这次最值得记的一条）：第一版回归测试让 `get_embeddings` 抛错——**那时 `vectors` 已经赋过值**，
  把 `vectors = None` 拿掉测试照样全绿。**变异检查当场证明了它是"绿着但没盯住东西"**，
  于是补了一条在 `require_numpy()` 处失败的用例，再变异就红了。凡是要钉"未绑定/未初始化"这类问题，
  失败点必须落在**赋值之前**。
- `vector` 是**只增字段**，`hybrid` 的含义不变（"融合开关是开的"），两者一起读才能判断这次的真实路径。
- **`rerank.transport` 是同一类修正**：`provider` 只说明"走的是 Jev 那套契约"，不说明**谁在服务**。
  2026-10-18 的 A/B 里就撞上了：`judgeProvider=chat` 时契约由 DeepSeek 满足，状态里却写着
  `provider=typesafe`，差一点被读成"原生通了"（同一批查询的另一半退了 `llm`）。

  ⇒ 凡是"状态里记了某件事"的字段，要问一句**它记的是意图还是事实**；记意图的字段在换通路之后
  会安静地撒谎（同 D-034 记 `last_model` 的动机：实际服务的模型要与请求的别名分开）。

## D-121: 第三条腿是"原样定位"（FTS5/trigram），不是又一个相似度打分

**Status:** Active（2026-10-17 本机实测：8 次探针定形状；真语料 15,995 条建索引 10.7 s / 96.9 MB；
6 条真查询；离线测试 19 条）

**Decision.** 新增 `search --phrase "<文本>"` 与 `search-index --phrase`。用 SQLite **FTS5 + `trigram`**
建一条**精确短语**索引（`output/.phrase_index/phrase.sqlite`），查询按**字面量**匹配，结果按
（账号/会话 × 日期）分组给出——即"**哪个号在哪天原样写过这句话**"。索引**从 `meta.json` 派生**
（语义索引的记录清单 + `load_record_text` 取读者看到的那份正文），不自己重收集语料。

**Reason.【实测】前两条腿都是"相似"，答不了"原样"**：bigram+BM25 按词项计分、向量按语义近似。
专有名词、代码名（`std::vector`）、政策原文、引文核对这类问句要的是**原样**，把"查不到原样"
混进相似度排序里，用户看不出区别——那是比排错更坏的失效方式。四个实测结论决定了形状：

1. **默认分词 `unicode61` 对中文完全不可用**（整句一个 token：`沙箱逃逸` 命中 **0**）⇒ 必须 `trigram`。
2. **trigram 是连续子串，不是词项 AND**：`光伏补贴政策` 只命中**原样连续**的那篇，而
   `补贴政策` 命中"的补贴政策""补贴政策解读"。这正是"原样定位"要的语义，**也正是它不能替关键词腿的原因**
   ——那条腿要的是"分开出现也能排上分"（实测 `光伏补贴政策` 在 1 万篇语料上是 0 命中）。
3. **trigram 有 3 字下限**：`光伏`（2 字）恒空 ⇒ 短查询必须**明说"查不了"**，绝不能回报"0 命中"
   （把"查不了"说成"没有"是这条腿最坏的失效方式）。
4. **查询里的标点会被当语法**：`PM2.5` → `syntax error near "."`、`GPT-4` → `no such column: 4`。
   整串加引号当字面量（内部 `"` 翻倍）后，`PM2.5` / `C++` / `GPT-4` 全部正常，且 ASCII 大小写不敏感。

**"每篇文章一个表"被否掉（同一次实测）**：15,995 条 × FTS5 每表约 5 张影子表 ≈ **9.6 万张表**；
更要紧的是 `bm25()` 的**语料级统计**（df / avgdl）在单文档表上退化 —— 每个表只有 1 行，
排名失去意义；查询还得 N 次 MATCH 再自己合并名次，等于把倒排索引唯一的好处丢掉。
正解是**一条表装整个语料**（`rowid` 挂我们的记录 id，另存 line/label/date/title 几个 UNINDEXED 列），
这也是参考项目自己的做法（它的 architecture.md 写的就是一条 FTS 索引）。

**为什么从 `meta.json` 派生**：再写一套记录枚举，两个索引的语料迟早分叉，而"两个索引覆盖的不是同一批"
这种错**不会报**，只会让结果看起来少。代价是它依赖语义索引存在（没有就报错并给命令）。

**真语料实测**（15,995 条）：`沙箱逃逸` 2 条（跨两个号）、`PM2.5` **58 条**（含聊天线，
"孙天乐 2026-10-13 16:53 …那个 PM2.5 的"）、`腾讯混元` 7 条（跨 7 个号）、`分布式光伏` 1 条；
`std::vector` 与 `光伏补贴政策` 都是 **0 条** —— 与"原样"的预期一致（语料里真没这么写过）。

**Consequences.**
- **纯本地**：不联网、不花钱、**不需要 key、不需要 `--yes`**（确认门是为出境设的）。CLI 预览里
  写明了 `mayUseCloudEmbedding: false` —— 别让人以为"本地检索也要花 vector 的钱"。
- 索引是**派生**产物（96.9 MB / 全量重建 10.7 s），**它自己不排期**：语义索引变了（新文章入库）
  它就得跟着重建。这与"索引新鲜度"是同一个未决问题，不是两个。
- `--from` 与向量那条路**同一口径**（文件名子串）：所以命中的 `label` 可能与筛选词不同
  （号名常出现在标题里）。要精确按号，同一条 WHERE 里已经包含 `label` 精确匹配。
- **明说边界：查不到原样 ≠ 没写过**（同义改写、全角/半角、正文里的断行都会漏）。这条腿回答
  "原样"，语义那两条腿回答"意思上"——三者的分工要写在使用处，而不是让用户自己猜。
- **未测的已知限制**：全角/半角不互认（`PM２.5` 查不到 `PM2.5`）；大小写折叠只对 ASCII。

## D-120: 决策模型的三条通路：原生 / 国内对话模型 / OpenRouter —— 默认原生，国内那条是给被地区封锁的机器的

**Status:** Active（2026-10-17 本机实测：key 有效性、8 次真实调用、契约用仓库自己的 `_check_choice`
验过；离线测试 34 条；`judgeProvider=chat` 已在本机打开）

**Decision.** 新增配置 `judgeProvider`，取值三条：

| 取值 | 走哪 | 需要什么 | 本机可用性【实测】 |
| --- | --- | --- | --- |
| `native`（**默认**） | `api.typesafe.ai` 的 `systemone` | `typesafeApiKey` | ❌ **HTTP 451 地区封锁** |
| **`chat`**（推荐给本机） | 配置里**已有的**对话模型：`aiBaseUrl` 中转站 / `aiModel` / `deepseekApiKey` | 无需新厂商、新 key | ✅ `deepseek-chat` **1.0 s/篇**、契约通过 |
| `openrouter` | OpenRouter 的 chat/completions | `openrouterApiKey` + `openrouterJudgeModel` | ⚠️ 契约通过但**该账号 0 额度**，调用被 402 拒 |

外加 `openrouterApiKey`（加密落盘）与 `openrouterJudgeModel`（默认 `typesafe/jev-router`）。
`create_client()` 按 `judgeProvider` 分派，两条 chat 路共用同一个 `ChatJevClient`（`base_url` 可换），
它与 `JevClient` 是**同一个鸭子类型**，所以**一处开关替掉整层**：日报判断、检索重排、草稿回复、
欠话台账、路由选词共用它。**默认 `native`，行为与引入之前完全一致。**

**Reason.【实测】**
- **原生这条路在本机是死的**：`api.typesafe.ai` 返回 HTTP 451 "not available in your region"。
  症状不是报错而是**静默退化**：最近 40 个完整天 12,542 篇里 **0 篇**带 `relevanceScore`、
  `decisionModel` 全空；同层的重排/欠话/草稿跟着一起退化。
- **"中国地区可用的类 Jev"就在配置里**：拿同一份提示词与同一套校验问三个国内模型，**契约全过**——
  `deepseek-chat`（1.0 s、1649/69 tokens）、`deepseek-reasoner`（7.2 s）、`qwen-plus`（2.3 s）。
  `qwen-turbo` 撞的是账号速率限制（429），不是地区。所以这条路**不需要新厂商、新 key**，
  一句话 `config set judgeProvider chat` 就打开；成本按 tokens 估是 **¥1/天量级**（500 篇，见下）。
- **⚠️ 契约一致 ≠ 判断一致**：同一篇文章 `deepseek-chat` 给 relevance=1 / 收录概率 0.15，
  `qwen-plus` 给 **2 / 0.85**。下游阈值（0.5/1.5、0.50）是对着 Jev 拍的，所以换判断器要当成
  **换了一把尺子**——这也是 `relevanceScore`/`includeScore`/`usage.model` 都要落盘的用处。
- **实际服务的模型可能与请求的别名不同【实测】**：请求 `deepseek-chat`，`usage.model` 回的是
  **`deepseek-flash`**。这正是 D-034 那条"落盘实际服务的模型"在起作用——不然"线上跑的不是测过的那个"
  永远不可见。
- **地区限制是逐模型的 —— "能调一个"不等于"能调所有"**：OpenRouter 上
  `typesafe/jev-router` 与 `:free` 模型能通，而 `openai/gpt-4o-mini` 返回
  **403 "This model is not available in your region."**（失败在路由的 "Gate Endpoints with Geo Restrictions"）。
  换模型必须在本机重测一次——这条规矩是被那次 403 换来的。
- **契约改由我们自己保证，这是换通路真正的代价**：原生是服务端直接给结构化答案
  （`{probabilities, choice}` / `{score}` / `{noul}`），chat 模型要**被要求**才会给，所以提示词写死
  输出形状、客户端逐条验。四条概率校验复用原生那套 `_check_choice`，另加 `_check_score`
  与 `_check_noul`——**noul 是概率不是布尔**，这个坑仓库踩过（把浮点当布尔读，每问都读成"否"）。
- **OpenRouter 的成本【实测】与路由的不确定性**：一次调用路由到 `x-ai/grok-4.7`，花 **$0.014**，
  其中 `reasoning_tokens` **1535**（比正文还长）；另一次路由到 `z-ai/glm-5.3-flash`。
  **路由到谁每次可能不同**，所以只能按最坏估：**一天 500 篇 ≈ $7/天**，
  而且 `max_tokens` **管不住**它（实测设 600、实际返回 1598）。
- **那个 OpenRouter key 的账号没有额度【实测】**：`/credits` 报 `total_credits: 0`，请求被 402 拒
  （"you can only afford 6666 tokens"）。所以 **OpenRouter 那条现在打不通**，接好了但没打开。

**Consequences.**
- 一个开关同时改五处消费者，所以默认必须是 `native`；打开前要知道成本量级
  （`chat` ≈ ¥1/天，`openrouter` 最坏 ≈ $7/天）。
- 四种失败的处置完全不同，所以报错分开说：**402=额度**、**403+"region"=地区（换模型才有用，不是换 key）**、
  **401=鉴权**、**空正文=推理吃光预算（调大 `OPENROUTER_MAX_TOKENS`）**。
  混在一起会让人去换 key，而那条路上换 key 一点用都没有。
- 契约漂移**当场抛** `JevError`（不放行、不给默认值）：失效方式若是"形状变了但看着正常"，
  下游读到的就是错的判断。
- **不许做的推断**：不能说任两条通路的判断一致——原生在本机调不通**无法对拍**，而两条 chat 路之间
  实测就已经不一致（见上）。能声明的只有"**契约成立、分数落盘、谁判的可查**"。
- **判断质量仍未定标**：换了尺子之后，"这个 relevance 值对不对"没有金标准可校。要做的是
  `quality_eval.py sample` 抽 50 篇人工标一次（欠着的旧账），而不是拿三家模型互相投票当答案。
- 另一条教训（与代码无关但值得留）：那封自称给 $25 额度的推广邮件里，产品（Drex / nace.ai）
  在 OpenRouter 的 **466 个模型里不存在**，账号里也**没有**积分。核实靠官方接口，不靠邮件。

## D-119: 日报的排期形状：跑"昨天"、允许补跑、抓之前并发预热正文缓存

**Status:** Historical schedule superseded by D-136（2026-10-16 落地；原本机任务每日 00:05/登录补跑昨天；运行“昨天”的原则仍用于 D-136 的登录补跑）

**Decision.** 四件事一起定，它们是同一个问题的四个面：
1. 计划任务跑**昨天**（新旗标 `--yesterday`），不是"今天"；
2. 用 `Register-ScheduledTask` + `-StartWhenAvailable` 注册，**不用** `schtasks /create`；
3. 抓取之前先**并发预热正文缓存**（新旗标 `--prefetch-bodies [n]`，**默认关**）；
4. 任务的动作指向 `scripts/pipeline.py`，**不指向** `weflow-cli` 这个 npm 包。

**Reason.**
- **根因是"没人跑"，所以这不是优化而是补缺。** 【实测】本机 2026-10-16 复核 372 个日期目录：
  **13 天有网页/图片**（`08-24`~`09-05` 的 12 天 + 本次刚产出的 `10-15`）+ **358 天只有 `.articles.json`**。
  即**完整日报这条线从 2026-09-05 起暗了 40 天**：`10-07`~`10-13` 那些"看着有"的天都是回填形态
  （README 自己写着"未下载图片、未调用模型"，没有 `index.html`），而 `10-14`~`10-16` 连目录都不存在。
  计划任务里除了两个 watcher（且都已 Disabled）没有任何跑日报的任务——与 D-011 那条"不跑就不会发生"
  完全一致。**这里更正一处旧口径**：09-28 曾被当成"最后完整的一天"，其实它也是回填形态；
  "有 `.articles.json` + README"正是 **D-060** 警告过的"文件存在 ≠ 这天完整"。
- **为什么是"昨天"**：公众号发到 24:00 才停，所以**当晚跑的那一天永远不全**，而且以后没人回头补它；
  反过来"跑昨天"这个动作**与时刻无关** —— 00:00 之后任何时间跑，读到的都是同一天的完整数据。
  于是最常见的漏跑（机器在睡觉）只要任务允许补跑，醒来补上就**结果完全相同**。
  【实测】22:26 手动触发 `--yesterday`，它产出的是 `2026-10-15`。
- **为什么不是 `schtasks /create`**：它无法表达"错过就尽量补跑"（那在任务 XML 里是
  `<StartWhenAvailable>`）。没有它，机器 08:20 在睡觉 = 这天**静默**没有日报。本机现成的
  反例就在眼前：`PilgrimIntelDaily` 是 `StartWhenAvailable=True` 且 `LastTaskResult=0`，
  而另两条任务的 `LastTaskResult=2`（**找不到文件**）—— 仓库那次搬迁之后没人看过它们的上次结果。
- **为什么指向 `pipeline.py` 而不是 CLI**：npm 全局那个包是**发布版**、滞后于仓库；更要紧的是
  它一旦是构建产物，"改完代码没 `npm run build`"就变成"任务跑的还是旧逻辑"，属于静默失败。
  python 层没有构建步骤，是同一份源码。
- **并发预热的取舍**：抓取阶段串行且未命中缓存时 `sleep` 8~12 秒（节流是为了少打扰上游），
  450 篇约 75 分钟；而**命中缓存既不联网也不 sleep**。预热把这段换成"先按 8 并发抓一遍"，
  对上游的**请求次数完全不变**，只是从"一个多小时里慢慢发"变成"更短时间里发完"（瞬时并发更高）。
  正因为它改变了"日报故意串行节流"这条既有边界（D-108 记过），所以**默认关、只能显式打开**。
  【实测 2026-10-16 首次真跑（523 篇的那天）】预热 **516 篇 10 分钟**（前 6 分钟约 75 篇/分钟，
  最后 8 篇慢的拖了 4 分钟），整天 `biz_daily` **15 分 49 秒**（22:26:21 → 22:42:10）；同样篇数
  串行 + 节流要一个多小时。也就是说这一步的收益是**约 90 分钟 → 16 分钟**。
- **为什么不用 `backfill_articles --prefetch-all` 来预热**：那个开关会**产出同一天的文件**
  （md/`.articles.json`/README），与 `daily` 抢同名，而两条路径的产物形态并不相同。写过、测过、
  **撤掉了**：预热这事该由"要用这份缓存的那一方"自己做（`biz_daily.prefetch_bodies`），
  否则就是给一个不冲突的需求加一个会冲突的工具。
- **别把缓存读成检索收益**：让索引吃完整正文实测**没有增益**（D-118），这份缓存只服务展示路径
  （`search --with-text`）与日报自己的耗时。

**Consequences.**
- 任务动作里**没有** `--yes`（确认门在 CLI 层，python 层没有）。也就是说**能改这个仓库的人**
  就能让日报在 08:20 跑起来并调云端模型：单用户机器上可接受，换机器要重新评估。
- 与 D-011 不冲突：那条兜底是"不带日期时先补昨天"，`--yesterday` 是显式指定，不走兜底。
- `--date` 与 `--yesterday` 同时给会**当场报错退出**，而不是静默选一个（两个答案并存时"哪一天"
  没有唯一解）。
- 排期这一节的运行方法写在 `OPERATIONS.md`「让它每天自己跑」，含怎么读 `LastTaskResult`
  （`267009` = `0x41301` = **正在运行**，不是错误）。

## D-118: 索引**不**吃完整正文 —— 我提过改，量完撤回了（自造题裁判不了这件事）

**Status:** Active（2026-10-16 实测；102 条自造题 + 一次 40 篇的离线重嵌）

**Decision.** 建索引继续吃**日报 md 那份 500 字摘录**（`collect_articles` 里 `body[:500]`）；
正文缓存只服务**展示路径**（`--with-text` / `--fetch-missing`）。`best_article_body` 这个"取更长那份"
的函数**只给展示路径用**，索引不碰它 —— 并有一条**反向**测试（`IndexBodyTests`）钉住这件事。

**Reason 【实测】: 加厚正文对检索没有可测的增益。**
- 背景：日报 md 里正文中位 **259 汉字**，而日报**自己抓到的**完整正文缓存里是 **2,063 汉字**；
  索引嵌的是前者。于是很自然地怀疑"素材薄 ⇒ 检索差"，并打算重建一次索引、让单条预算从 500 提到 2,000。
- 做法（不动索引、不花钱重建）：挑出"当前文本薄、但本地已有更厚缓存"的文章，只用完整正文**重嵌一遍**
  （旧中位 229 字 → 新中位 2,020 字），在**同一批目标**上比新旧名次（102 条题）。
- 结果：唯一**中立**的一组（标题 —— 两份文本里都有它）**持平**：recall@1/@5/@10 = 35/39/40 → 33/40/40。
  两组碎片题各自跟着**自己的偏置**走、方向相反：碎片取自薄文本时 16/21/29 → **7/16/18**（大跌），
  取自厚文本时 2/4/8 → **4/7/8**（微升）。逐题 17 好 / 31 差 / 54 平。
- **方法上的结论比数字本身重要**：题是从**被索引的那份文本**里抠出来的，所以**自造题裁判不了"索引吃哪份文本"**
  —— 换了索引文本，题也跟着换。要裁判它需要一份与索引无关的手写问题（`--queries-file` 已经支持），
  而且问题要问到"正文里才有、标题和摘要里没有"的东西。这个电池**这次没做**。
- 【未知】因此**不能**说"完整正文对检索有害"；能说的只是"**没有可测的增益**"，
  而它不足以为此付一次完整重建（≈¥2.8、20 分钟）外加索引内部不再一致（有缓存的厚、没缓存的薄）。

**Consequences.**
- 批量预填正文缓存（`backfill_articles --prefetch-all`）**不再为索引而做**。为展示而预填是另一回事，
  但 9,163 篇 × 约 4 小时买不到索引收益，所以**不做全量预填**；展示走 `--fetch-missing` 按需抓一次 + 永久缓存。
- 撤销 2026-10-16 我提出的"让索引用完整正文"这条建议（同 D-110 那次"砍综合新闻号"，
  也是我先提、量完自己撤回）。D-109/D-111 里的召回数字都是在**薄正文**索引上量的，**不需要重测**。
- `IndexBodyTests` 反向钉住这条：它红了 = 有人又改了索引吃哪份文本，那时必须带上新的证据（手写问题电池）。

## D-117: MMR 做了，但**默认关** —— 它压掉的"重复"里有相当一部分是有用的

**Status:** Active（2026-10-16 实测；同批 40 题看排序，另 10 题 × 5 条走判官看准确率）

**Decision.** 加 `--mmr`（Maximal Marginal Relevance，**λ=0.7**，照 WeKnora 的取值）与配套的多样性指标，
但**默认关**。理由是量出来的，而且方向与预期相反。

**Reason: 在这份语料上，"多样性"与"有用"不是一回事。**
- 排序侧（同批 40 题、池 100）：多样性确实改善 —— 最终 top-10 两两余弦均值 **0.627 → 0.559**；
  recall@10 不变（0.95），hit@1 0.72 → 0.75（噪声量级），**recall@5 0.93 → 0.90**（一格一个用例）。
- **准确率侧（判官，10 题 × top-5 = 50 条）：掉了。** 严格相关 **40% → 30%**，宽松（含 partial）**68% → 64%**。
- 为什么：这份语料里的"重复"常常是**多个公众号转载同一件事**（多源确认，不是噪声）—— MMR 压掉的正是
  这些条目，而判官把它们算作相关。所以它治不了"top-5 里邻居太多"这个抱怨：那个问题的另一面是
  "同一件事被几个号发过"，而**那一半已经由"同文本折叠"（D-114）吃掉了**（折叠是免费的、不损失信息）。
  再往前压就得开始删有用的东西 —— 实测正是如此。

**Consequences.**
- `--mmr` 与 `--no-dedup` 是两个层次：折叠同文本（默认开）、压近似（默认关）。
- λ 不是这里的问题：λ 调大就退化成纯相关性（等于不 MMR），调小只会删得更多。**方向不对，不是权重不对**，
  所以没有"再调一调"的余地 —— 要它有用，得先有"同一件事的不同角度值得区分"的证据，而这份语料里没有。
- 判官口径本身有边界：它逐条独立判、看不到重复，所以"多源确认"在它眼里和"一条新信息"等价。
  这个偏差对 MMR 不利，但也**不改变结论的方向**（宽松口径同样掉）。

## D-116: `--fetch-missing` 按 url 补正文，复用日报的缓存与判据

**Status:** Active（2026-10-16 实测：`XGBoost-SHAP 陕西 PM2.5` 的第一条 201 字 → **965 字**）

**Decision.** `--with-text` 之外再加 `--fetch-missing [N]`（默认关，`const=3` = 不写字数时最多抓 3 条）：
**本地正文短于 300 字、且有 `url`** 的那些，按 url 抓一次；抓到的写回 `fullText` 并标 `fetchedFromUrl`。
抓取**复用日报那套** `biz_daily.fetch_article_cached`（同一份缓存 `output/.cache/fetch/<md5(url)>.md`、
只缓存成功、"先看缓存再上网"），不另写一份。抓失败就保留本地那份，并把条数报出来。

**Reason: 薄的正文不该逼用户自己放弃，但也不该默认去打扰上游。** 实测那条的日报 md 只有 201 字
（好文回顾类文章 md 本身就短），抓回来是完整摘要与结论（2013—2022 年陕西 PM2.5 年均浓度从 56.02 µg/m³ 降下来、
主控因子按高程/相对湿度/温度/人口密度排序）。三条边界是**故意的**：
- **默认关**：检索不该默认产生对上游的请求（`docs/NEXT_ACTIONS.md` 点过名的规矩）；
- **有上限**：一次最多抓 3 条，避免"一次检索打十个请求"。与日报的 8–12 秒批量节流不同 ——
  那是几百篇的礼貌，这是几条的按需，**这个差异是刻意的**，不是漏了节流；
- **只抓薄的**：本地已经 3,020 字的那条在这趟里没被碰（实测验过）。

**Consequences.**
- 同一份缓存被日报/回填/检索共用：检索抓过的，日报重跑时直接命中；反过来也一样。
- 与日报一样，抓来的正文只有"当时那一版"——源站改稿不会反映到缓存里（要新就删那条缓存重抓）。
- 顺手把"预览时标签不可点/微信扫一扫/关注该公众号"这类尾巴补进了整行套话清单：抓回来的那篇结尾挂着它们。
  清单只删**整行**，不动正文句子。

## D-115: `search --with-text` 返回**正文**，而不是"用来匹配的那段摘录"

**Status:** Active（2026-10-16 用户指出：只返回标题/分数/摘录"没什么具体意思"，要具体到某篇的内容）

**Decision.** `search` 加 `--with-text [N]`（N = 每条最多多少字，默认 4000，`0` = 不开）。打开后每条结果多三个字段：
`fullText`（**能读的正文**）、`sourceFile`（本地日报 md 的路径）、`url`（原文链接）。正文有两处来源，取**更长**的那份：
- 日报 md：`output/biz-daily/<id 里那段相对路径>`，正文用同一套 `extract_article_body` 取；
- **正文缓存**：`output/.cache/fetch/<md5(url)>.md` —— 日报/回填抓来的完整正文，通常比 md 更全。
聊天记录没有这两层：**索引里那段本来就是消息全文**。

**默认不开的理由是上下文不是小气**：助手、MCP、RAG 走的是同一个 `search()`，10 条 × 4000 字会把这些通路
一次撑进 2 万字；要看正文就显式开（`--top-k 3 --with-text` 是最常用的组合）。

**读不出来要说出来。** `load_record_text` 对读不到的文件返回 `None`（不是空串），`attach_full_text` 把条数
数出来；`sourceFile` 仍然给出路径 —— 用户能自己去开那个文件，而不是以为"这篇没有正文"。

**`readable_body` 与重排那份摘录的区别（别混）**：`cleanse_passage` 是喂模型的（压成一整行、截 300 字）；
`readable_body` 是给人看的（去加粗标记、去图片语法、把链接留成文字、删"点击蓝字/在小说阅读器读本章"这类
**整行**套话），但**保留段落**。原始文件路径在 `sourceFile` 里，想核对"有没有被我改过"就打开它对比。

**实测（`用卫星遥感估算 PM2.5` 的第一条）**：日报 md 11.7 KB → 正文 1,393 汉字、正文缓存 1,462 汉字；
整理后 `fullText` 1,723 字，含标题、作者期刊、引用格式、三条亮点、导读、**研究结论**（三重交叉验证
R² 0.87/0.86/0.83、AOD 特征重要性 40.63%）与相关文章列表。去图片语法前那一版是 3,860 字，其中约 2,100 字
是 `![](https://mmbiz.qpic.cn/…)` 拼成的一整行 —— 所以必须去。

## D-114: 结果侧折叠同文本转载（默认开），而索引侧**不**去重

**Status:** Active（2026-10-16 实测：20 条问句的 top-5 里 7% 是同文本转载，6/20 条问句的 top-5 出现过重复）

**Decision.** 取出结果时按**正文文本**折叠：同一段文本只留排名最高的那条，其余按条数记进
`duplicateCount`（用户该知道"同一篇被几个号发过"，而不是以为丢了什么）；`--no-dedup` 关掉。空文本的
记录各算各的（空串之间不是"同一段"）。

**Reason: 转载占的是名额，不是信息；但索引侧不能去重。** 实测两次都指向同一件事：
- 2026-10-15 那次量到索引里 15,995 条只有 15,324 段不同文本（671 条重复）；
- 2026-10-16 在新默认（重排 + 池 100）下重测：100 个 top-5 名额里有 **7 个**是重复文本，**6/20 条问句**
  的 top-5 出现过重复（例如"环境监测新规"那句，五部门通稿被 3 个号发、推进会也被 3 个号发 → 一次检索
  里 4 个名额是副本）。
而**不在建索引时去重**的理由是具体的：`--from <公众号>`（D-109 之后那条属性筛）靠的是 `id` 里的文件名，
把被折叠的账号从索引里删掉，按号查就会缺记录。所以：**索引留着全部，折叠只发生在展示**。
所以这一条**不改变 recall 指标**（ground truth 是内容，副本折叠不影响首条的 rank），只让"实际看到的东西"
变多。这也是为什么它是纯展示层改动、不需要重建索引。

**Consequences.**
- 每条结果多一个 `duplicateCount` 字段（只增字段，不改既有字段语义）；CLI JSON 里也带 `dedup` 开关状态。
- 与 `--dedup`（评测台的那个）**不是一回事**：评测台的 `--dedup` 是**排名前**折叠，用来量"去重后的名次"；
  生产这一条是**取出结果时**折叠。两者顺序不同、目的不同，别混。
- 想回到"原样返回所有副本"：`--no-dedup`。

## D-113: 真正的杠杆是"重排能看到多少候选" —— 池子 20 → 100，并更正 D-112 那处倒退

**Status:** Active（2026-10-16 实测；同一批 40 题、同 seed，只改重排候选池）

**Decision.** 重排候选池（`RERANK_CANDIDATES`）默认从 **20 提到 100**，并把它做成可调：Python 侧
`--rerank-pool`、CLI 侧同名旗标（**没传就不转发**，默认值只在 Python 侧定义一处）。曲线：

| 池子 | recall@10 | recall@5 | hit@1 | MRR@10 | nDCG@10 | 碎片/文章 recall@10（最弱那格） |
| --- | --- | --- | --- | --- | --- | --- |
| 20（旧默认） | 0.82 | 0.82 | 0.65 | 0.72 | 0.75 | 0.70 |
| 50 | 0.88 | 0.88 | 0.68 | 0.75 | 0.78 | 0.80 |
| **100（新默认）** | **0.95** | **0.93** | **0.72** | **0.80** | **0.83** | **1.00** |
| 200 | 0.95 | 0.93 | 0.75 | 0.82 | 0.85 | 1.00 |

**Reason: 重排只能重排它拿到的那些。** 之前的提升假象是"融合 + 重排"，而这两个都只在**召回交上来的候选**
里做文章：池子 20 意味着正确答案只要在融合排名 21 名开外就永远进不来。把池子放到 100 之后，recall@10
就到 **0.95**、MRR **0.80** —— 对照公开的生产参考阈值（Recall@10 ≥ 0.90、MRR ≥ 0.75），这一项**越过了**。
100 → 200 只剩噪声级增益（hit@1 +0.03、@10 不动），所以取 100，并且**不许**拿这一批题继续往上调。

**代价**：每次重排的提示从 ~2.5k tokens 涨到 ~20k（deepseek 系约 ¥0.01/次，几秒延迟）。
嫌贵就把池子调小（`--rerank-pool 20`），这比 `--no-rerank` 温和。

**更正 D-112 的一处记录。** D-112 记了"标题/文章那格 recall@5 从 1.00 掉到 0.90，一处倒退"。重跑同一批题后
那一格是 **1.00**（hit@1 也有 0.80/0.90 两次数值不同）——**那是噪声**：重排温度虽为 0，模型仍不完全确定，
而每格只有 10 条题，一条就是 0.10。结论不变（"不能据此说重排有害"），但当时那句话说重了：**该格没有倒退**。
教训写在这里：n=10/格的分格指标不要用来判单点得失，要看整体与最弱格的方向。

**踩到的坑（值得记）**：CLI 侧先用 `parseCliInteger(opts.rerankPool, ...)` 把没传的选项也解析了一遍 ——
commander 对未提供的选项给 `undefined`，于是**用户不传这个旗标就会被自己的校验判成非法参数**
（`rerank-pool 必须是 5-500 的整数`，搜索直接不工作）。改成"没传就不转发"。这也说明：**默认值只该有一处**。

## D-112: 把"召回 + 重排"的第二段点亮 —— 重排改用本机可用的模型，并给出降级状态

**Status:** Active（2026-10-15 实测：同一批题加上重排，hit@1 0.55→0.65、nDCG@10 0.63→0.74）

**Decision.** 重排这一段**恢复工作**，但换一个真正能连上的提供方：先按原契约（Jev/Typesafe）试，
失败或不可用则退到**配置里的对话模型**（OpenAI 兼容，本机是 deepseek 系）；两条都失败就保持原序，
并把 `RERANK_STATE`（`provider` / `degraded` / `error` / `scored`）**带进 JSON 输出**。同时借 WeKnora
的做法，在送进模型前做**段落清洗**（去 `**加粗**`、公众号套话、署名行、图片/链接语法）——
候选摘录本来只有 300 字，不清洗会有一大半注意力花在噪声上。

**Reason: 主流管线是两段，而我们的第二段一直是黑的。** 本机 Typesafe 返 `HTTP 451 not available in your
region`，于是"重排"只在 stderr 打一行 WARN（CLI 还不转发子进程 stderr）——查不到、也没生效。参考口径：
- MS MARCO nDCG@10：BM25 **0.184** → 稠密 **0.396** → 稠密+重排 **0.452**；
- 一个公开的 retrieve&rerank 例子（NanoNFCorpus）：混合 RRF nDCG@10 **32.95** → 混合 RRF+重排 **36.16**，
  MRR 50.19 → 55.77；
- 中文域：bge-reranker-large 把 Top-10 准确率从 80.7% 提到 **91.2%**；
- 生产参考阈值（腾讯云）：HitRate@3 ≥ 90%、Recall@10 ≥ 0.90、MRR ≥ 0.75、nDCG@5 ≥ 0.80。
- 反面也记一笔：有文献报告重排器**会倒退**（jina-reranker-v2-base 在 BM25 形状的候选池上 −7.5pp），
  所以"加了重排"不是无条件更好，必须用同一批题量。

同一批题（40 条：碎片/标题各 20，seed 固定）实测：

| 组 | 指标 | 不重排 | **加重排** |
| --- | --- | --- | --- |
| 全部 | hit@1 | 0.55 | **0.65** |
| 全部 | recall@5 | 0.65 | **0.80** |
| 全部 | MRR@10 | 0.59 | **0.72** |
| 全部 | nDCG@10 | 0.63 | **0.74** |
| 碎片/文章（最弱那格） | recall@5 | 0.30 | **0.70** |
| 碎片/文章 | MRR@10 | 0.32 | **0.53** |
| 标题/聊天 | hit@1 | 0.60 | **0.80** |
| 标题/文章 | recall@5 | 1.00 | 0.90 ⚠️ |

**一处倒退如实记下**：标题/文章那格 recall@5 从 1.00 掉到 0.90 —— 一个用例被重排挤出了前五（候选池 20，
重排认为别的更相关）。n=10 时一个用例就是 0.10，**不能据此说重排有害**，但也不能装作没发生：
如果后续在这格上重复出现，对策是"扩大候选池"或"保住召回侧的第 1 名"，**这两条都还没做**。

**Consequences.**
- **每次检索现在会多花一次模型调用**（约 20 条候选 × 300 字 ≈ 2–3k tokens，deepseek 系约 ¥0.004/次）
  和几秒延迟；`search`、助手的 `search_semantic`、MCP 的 `search_knowledge`、`chat` 走的是同一个
  `search()`，所以它们**一起变了**。要省就 `--no-rerank`。
- 重排仍然"不该让检索失败"：没有 key、网络断、模型返回的编号不齐（这是重排最危险的失效方式）都
  **保持原序**并标 `degraded`；区别是现在这件事**看得见**了。
- 借 WeKnora 的两条还没做：**重排后的 MMR 去冗余**（它用 λ=0.7）与**候选不足时迭代扩池**。
  MMR 与"单答案召回"指标是相冲的（它故意压掉相似项），要量的话得用"去重后的名次"或人工判据。

## D-111: 召回有了评测台，门控后的关键词融合因此翻成默认

**Status:** Active（判据是 2026-10-15 用新评测台跑的三组 A/B；本条同时更正 D-110 的结论）

**Decision.** 两件事一起定：
1. **留一个能复跑的评测台** `scripts/recall_eval.py`。指标全部用业界标准口径，**不自创**：Recall@1/5/10、
   MRR@10、nDCG@10、Hit@1（中文规范《人工智能 检索增强生成系统测评规范》用的是"命中率"这个词），
   外加**覆盖（coverage）**——这是这类语料特有的那一项：答案在库里根本不存在时任何排序都救不了，
   把它算进召回率是在诬赖排序，所以每题先判"库里有没有"。标准答案来自两处：从记录**自己正文中段**
   取的 12 字干净片段（`frag`，唯一性已校验）与标题/开头 30 字（`title`），都在运行时生成、**不把语料
   写进仓库**；手写问句走 `--queries-file`（仓库外的 TSV：问题＋锚串）。
   对标过的同类做法：BEIR/MTEB（nDCG@10 为主指标）、C-MTEB（35 个中文数据集）、MS MARCO（MRR@10）、
   RAGAS（生成侧）；最接近的线下实现是 PsyRAG-Lab（中文，BM25 vs dense vs RRF vs cross-encoder，
   报 Hit@K/Recall@K/MRR/nDCG/MAP）——它也是**先比再定**而不是假定 RRF 有用。没有现成基准能覆盖
   "个人微信语料 + 文章/聊天双线 + 多号转载"这三件事，所以语料层我们自建，指标层不另立。
2. **关键词侧加门控后，把它翻成默认开**（`--no-hybrid` 关）。门控两条，缺一不可：只让**命中稀有词**
   （`df/N ≤ 5%`）的文档投票；并且它们的 BM25 分数要**接近最高分**（≥ 0.5×）。问句里一个稀有词都
   没有（"怎么用/有没有"这类）⇒ 关键词侧**整个弃权**。

**Reason: 让关键词侧起作用的是门控，不是融合本身。** 同一批题（frag 40 / title 40 / 手写问句 12）：

| 排名方式 | 碎片 recall@10 | 碎片 recall@5 | 碎片 hit@1 | 标题 hit@1 | 手写问句 |
| --- | --- | --- | --- | --- | --- |
| 只用向量 | 0.91 | 0.89 | 0.60 | 0.83 | 0.75 |
| 无门控 RRF（D-110 的版本） | 0.91 | 0.89 | 0.60 | 0.83 | 0.75 |
| **门控 RRF（0.05 / 0.5，现行默认）** | **1.00** | **0.97** | **0.80** | **0.86** | 0.75 |

无门控版在**同一批题上是零增益**——与纯向量一模一样；这从另一面解释了 D-110 为什么量出"有害"：
问题的根源是虚词投票，不是融合这个动作。门控把它修好之后，三个台子**都不掉**，最弱那格（碎片召回，
"文里那句话在哪篇"）从 0.91/0.60 提到 1.00/0.80。阈值敏感性也量了：宽松 (0.10/0.3) 略弱、紧的
(0.02/0.7) 略好，**平台是平的** ⇒ 这个数字不承重，取中间值，并且**不许**再拿这一批题去微调它
（避免把偶然结果固化）。

**Consequences.**
- `search` 的默认排序与 1.12.0 之前不同：`score`（余弦）**不再单调**，顺序来自融合名次。想回到纯向量：
  `--no-hybrid`。助手 / MCP / RAG 走的是同一个 `search()`，所以它们也一起变了。
- 评测台要花钱（每题一次 embedding，十几 tokens）且**问句出境**，所以它和 `search-index` 一样要求
  `--yes`；它**不写任何文件**，报告只打到 stdout（JSON 走 `--json`）。
- `--dedup`（同文本合并再排名）实测只值 +0.03 左右的 recall@10：多号转载确实吃名额（实测 50 条 top-5
  里有 4 条是同文本），但不是主因。它的正确位置是**建索引时**（省向量、省名额），不是每次检索时做。
- 这一条**不影响** D-110 记的"评测要把'没命中'与'库里没有'分开"那条纪律——评测台的 `coverage` 字段
  就是把它落到代码里的样子；`HYSPLIT` 那类问句（库里 0 条记录）因此不会被算成排序失败。

## D-110: 混合检索做成了开关而不是默认 —— 量出来它只在一种问句上赢

**Status:** Active（判据来自 2026-10-15 的两组 A/B：同一份索引、同一批查询，只换排名方式）

**Decision.** 保留"向量名次 + 本地 BM25 名次用 RRF 合成"这条路，但**默认关**，由 `search --hybrid` 打开
（CLI 与 Python 两侧同名同义，默认值两边一致）。关键词那一侧完全本地：中文按**字符二元组**切词（没有分词器，
也就不需要词表，代价是虚词也成词——那交给 BM25 的 idf 自己去压），一遍扫过所有记录算分，**不建额外索引文件**，
所以不存在"关键词索引与正文不同步"这种失效。实测每次 31 ms（中位，16k 条）。

**Reason: 默认值要挑"多数问句更好"的那个，而融合只在少数问句上更好。**
- 逐字碎片查询（16 条，从记录正文中段取 12 字原话）：BM25 单独 **16/16**、向量单独 9/16、RRF 12/16。
  关键词侧近乎满分，融合反被向量侧拖低。
- 真实问句（5 条）：RRF 在 **1 条**上明显更好 ——「卫星遥感反演气溶胶光学厚度」把目标（基于遥感的 PM2.5 反演）
  从第 3 提到第 2；在另外 4 条上**都不优于**向量单独，其中 2 条更差 ——「有没有讲遥感或卫星数据用在空气质量
  监测上的文章」的正确首条被挤出前五，「后向轨迹 HYSPLIT 怎么用」被"怎么用"这类虚词带偏。
- 机理清楚：RRF 给两侧**同等票权**，而中文问句里的虚词会让 BM25 侧变成纯噪声。

**Consequences.**
- 开 `--hybrid` 后输出的 `score`（余弦）**可能不再单调** —— 顺序来自 RRF，而 `score` 仍按原义报（没有改它的含义，
  改的是顺序）。看排序别只看这一列。
- 想在真实问句上默认开，先解决虚词投票（按 idf 设阈、只让稀有词投票），并用**同一套 A/B** 复核；
  不许拿这 5 条查询调阈值 —— 那正是这个仓库点过名的"把偶然结果固化"。
- 这条路不花钱、不出境：关键词侧全离线，查询嵌入那一次调用与原来完全相同。

## D-109: 语义检索的 embedding 走阿里云，出境的是每条素材的截断片段

**Status:** Active（做法与成本来自 2026-10-15 的真机实测：按默认窗口建了一份 16,027 条的索引，并跑了真实检索）

**Decision.** 语义检索（`weflow-cli search` / `chat`、助手的 `search_semantic`）的向量由阿里云百炼
（DashScope）的 `text-embedding-v4` 算，1024 维，密钥走 `dashscopeApiKey`（机器绑定加密保存）。**出境的是每条
素材的一小段纯文本**：文章 = 标题 + 正文前 500 字（`semantic_search.py` 的 `collect_articles`），聊天 = 消息内容
前 500 字（同文件的 `collect_chat_messages`）。**不出的**：数据库与密钥、图片与文件本体、文件路径、好友名/群名，
以及任何一条没被截进那 500 字的正文。索引与元数据都留在本地（`output/.semantic_index/{vectors.npy,meta.json}`），
查询时才把**这一次的问句**发出去一次。

**Reason: 本地没有能用的中文向量，而它必须是可选且看得见的。** 默认不建索引——没配 key 时
`search`/`chat` 直接说缺 key，不拿空 key 去请求。写死 500 字是因为"全文出境"是另一档暴露面与另一档账单：
实测这份索引累计送了 **587 万字 ≈ 391 万 tokens ≈ ¥2.0**（估算按 1.5 字/token、¥0.514/百万 token），
而单次查询只送 12 tokens ≈ **¥0.00001**——真正贵的是建索引那一次，它只在窗口变化时才需要重跑。

**代价要一起写下来 —— 而且 2026-10-15 当天把主因查清了。** 当时"截断"只是可疑方向；同一天实测最近 30 个日期目录的
10,759 篇日报 md，**含 `## 正文` 的有 0 篇**（真实结构是 `frontmatter → # 标题 → > 来源 → --- → ## AI 摘要 → --- →
重复标题 → 正文`），而取正文的判据只有一句 `startswith('## 正文')`，于是每一篇都退回 `content[:1000]`：送去 embedding
的是 YAML 头（抽样 300 篇：100% 含 `title:`，汉字占比中位 **0.16**）。所以"文章线只能靠标题排序"的原因是**正文压根没进索引**，
不是截断。已改成按真实结构取正文（同一批 300 篇：汉字占比中位 0.16 → **0.80**，299/300 篇改变），并配了一条
**会因"退回老写法"而变红**的测试（`test/semantic_article_body_test.py`，夹具照真实结构写、要断言的那句话放在第 1000 字之后）。

还有两件**没做**，一起记在这里：
- **要 `--full` 重建索引才生效**：增量建索引只嵌"新 id"，提取器改了而老记录不动 —— 也就是说"修好了"和"索引里还是旧数据"
  可以同时成立。这是本轮实测最容易误判的一处。
- **素材本身的厚度**：日报 md 中位 912 字（汉字 259），而正文缓存是 2,063、Vault 笔记是 422。要不要给文章线换更厚的素材
  是个独立取舍（更厚 = 更多出境与成本），本轮没动。
同一批实测还留下一个读数：**原始余弦分跨问句不可比**（同一个库上，一个问句的 0.58 不必比另一个问句的 0.50 更"像"）。

**Consequences.**
- 索引是**派生物**：删掉它只影响耗时与再次出境的成本，不影响任何原文。
- 重排是**第二段出境**（候选片段发给决策模型）。本机实测 Typesafe 返回 **HTTP 451 "not available in your
  region"**，所以重排在这台机器上一直不生效、等同 `--no-rerank`。这是**已知边界**，不是"决定不重排"。
- 想完全不出境只有不用语义检索：`vault search` 是纯本地关键词检索，不联网、不调模型。
- 上面所有金额都是**估算**（token 按 1.5 字/token、单价取百炼公开价），不是账单读数。

## D-108: 回填与日报共用一份正文缓存，但两条流水线仍然是两条

**Status:** Active（判据来自 2026-10-14 的真机实测：一天的回填把缓存从 57 个文件灌到 202 个）

**Decision.** 历史回填 `backfill_articles.py` 的正文抓取改为调用**日报自己那个** `biz_daily.fetch_article_cached`：
成功结果写进 `output/.cache/fetch/<md5(url)>.md`（与日报同一份、仍是"只缓存非空成功"），命中缓存时不联网；
两边都有 `--no-fetch-cache`，**同名同义**（跳过"读"，成功的仍然写回，所以它同时是"刷新旧缓存"）。两条流水线
**本身不合并**：输出形态不同（纯文本 vs HTML+图片）、抓取策略不同（回填并发且不节流，日报串行且刻意 8–12 秒
节流以少打扰上游）——共用缓存 ≠ 共用流程。节流的 `sleep` 在日报的**调用循环**里、不在抓取函数里，所以命中
缓存的篇既不联网也不睡。

**Reason: 让"补历史"与"正式产出"少重抓一遍。** 实测 2026-10-13 那天：回填灌了 **145 条缓存**
（57 → 202 个文件，与"尝试 152、成功 145"逐条对上），而这 145 篇正是回填认为"正文太短、必须联网取"的那批。

**但接力是部分的，这一点别读多。** 日报的 Phase 1 对**每篇有 url 的文章**都抓（它要完整正文来写摘要），
而回填只抓"本地正文短于 100 字"的那些、其余的连请求都不发。实测同一天：485 篇里**只有 145 篇在缓存里，
日报重跑仍需联网 340 篇**（按 8–12 秒节流约 55 分钟）。所以要"把这一天正式产出"仍然要付大部分抓取成本，
省下的是那 145 篇。反向那一半是成立的：日报跑过的天，回填重跑时不再打扰上游。

想让它真的全命中，得让回填**也**去取那些本不需要取的正文——那是**增加对上游的请求**，而
`docs/NEXT_ACTIONS.md` 明确说过不要"通过改变默认行为造成额外请求"，所以默认不这么做；要做就做一个显式开关
（例如 `--prefetch-all`），由使用者决定。**这项没做**，留在这里当已知边界。

**Consequences.**
- 缓存只代表"曾经成功抓到过"，**不代表正文是最新的**：源站改稿不会反映到缓存里。要刷新只能显式
  `--no-fetch-cache`；默认行为不为"可能过期"付费（这是 `docs/NEXT_ACTIONS.md` 点名的取舍）。
- `output/.cache/fetch/` 从此是两条线共用的状态。删掉它只影响耗时（各自退回"要重新抓"），不影响产出内容。
- **抓取失败与"正文本来就短"从此分开记**：`.articles.json` 多一个可选键 `fetchFailed`（该轮抓取失败的篇数），
  `day_done` 据此判那天**没做完** ⇒ 下次重跑只重试失败的篇，成功的那批走缓存。它与另两个标记
  （`topicFilter`、`truncated`）有一点不同：**不是 sticky 的**——一次零失败的全量跑说明（含上轮失败的）都补齐了，
  标记就该消失，否则那天会被永远重做。两种情况的区别是有代价的：把它们混成一句"跳过 N 篇（正文太短）"时，
  一次网络抖动会被写成"这几篇本来就没有正文"，而那些篇再也不会被重试（同仓库量过：84 次失败里 28 次重试可得）。

## D-107: 报"客户端运行时版本"，判据是活进程，不是 exe 里的字面量

**Status:** Active（做法来自一份**实测的启动器逆向报告**——报告在本仓库外，含真实的开发者用户名与
厂商内部构建路径，按仓库规矩**不进 `docs/`**；这里只落它的结论与判据）

**决定。** `check` / `health_check` 新增一项**微信客户端版本**【实测】：版本取自**活进程命令行的
`--user-lib-dir`**（`check --json` 多一个 `wechatClient` 块：`running` / `version` / `loadedDirs` /
`note` / `unusableVersionDirs`）。

> **2026-10-18 修订（D-125）：** 4.1.15.53 起主进程命令行**不再带** `--user-lib-dir`。命令行仍是首选，
> 取不到时改看进程实际映射的 `Weixin.dll` 所在目录；两者都没有才报未知。下面三条判据不变。

三条判据写死：

1. **不读 `Weixin.exe` 里的版本字面量。**【实测·报告】它确实硬编码着、而且在本机**恰好与版本目录
   同名** —— 但它只是**兜底**（控制流显示只有主路径加载失败才会用到它；主路径读
   `%APPDATA%\Tencent\xwechat\update\update.data`，本机没有该文件）。报告里那条"2/2 实测"就是
   先据此下了结论、后被控制流推翻的：**"字面量存在且与目录同名"与"它决定加载哪条路径"是两个命题。**
2. **"磁盘上有版本目录" ≠ "它能用"。** 判据是**有没有 `Weixin.dll`**。本机 `D:\WeiXin\4.1.15.11\`
   实测【实测】只剩 **1 个文件**（`crashpad_handler.exe`），指向它的加载会失败；`4.1.15.13\` 是
   29 个文件 / 867.8 MB。
3. **取不到就说未知，不算通过。** 客户端没在跑、或进程都没带这个参数 ⇒ `version: null` + 一句
   `note`；`health_check` 那一项仍算 OK 但**明细里写"未知"**（客户端没开不是故障）。而
   **`wechatClient` 字段整个不在 ⇒ 判不通过**【实测】：那意味着 `cli.cjs`/`dist` 是**没重建**的旧版本 ——
   这条规则在它第一次真跑时**就抓到了自己**（没 `npm run build` 之前，体检正是这么红的）。

**为什么值得一条决策**：这是"一个看着会对的错判据"的标本 —— 字面量在本机 2/2 对得上，谁都不会怀疑；
而它错的方式是**静默**的（报一个不是实际加载的那个版本）。同一个坑还有第二个面：目录**存在**不等于
**能用**，而"存在"是最容易被当成"没问题"的那种证据。三条判据合起来是一句话：**要问的就一个 ——
它现在到底加载了哪个**，别的来源都是替身。

**同一个检查顺带答另外两个排查问题**（都在同一份进程表上，不额外起进程）：

- **客户端自报的数据目录**（`--wechat-files-path`）与配置里那个数据库推出来的根是不是同一份。
  只比结论、**不把路径放进机器可读输出**（`weflow-check/v1` 一直不含路径），且**按 realpath 比**——
  本机 `C:\Users\Administrator\xwechat_files` 实测是 `D:\微信\xwechat_files` 的软链（同一份库两条路径），
  不比真身会永远报"不是同一份"。
- 而且**这条线索现在被用起来了**：`init` 的目录发现（`DbPathService.autoDetect`）会把它作为
  `hintedRoots` **最先**试，并回报 `source` 让用户知道"这是客户端自己说的 / 还是扫出来的"。
  理由：发现数据目录的另外两条路（常见位置、全盘扫目录名）**都是按名字猜**，而这一条来自客户端本身；
  线索**照样要过 `resolveDataRoot` 的结构验证**（找到账户目录才算数）——拿到一个路径 ≠ 它能用。
  【实测】本机：自报 `D:\微信\xwechat_files` → `{success: true, source: "client"}`，没走扫描。
- **客户端崩溃记录**（`%APPDATA%\Tencent\xwechat\crashinfo\`）：**只报条数与最近时间，不读内容**【实测】。
  "数据读不出来 / 客户端起不来"时这是一条独立线索。
- **`update.data` 在场时给一句 caveat**【推断】：报告把它留成未解（"若该文件存在，exe 侧第一次
  `LoadLibraryExW` 就不会看命令行 —— 两条路谁优先【未知】"），所以**上面那个版本可能不是加载的那个**。
  这句话必须在那种机器上跟着版本一起出现，否则新报的版本会**悄悄变成错的**。

**验证到什么程度**【实测】：真机 `check` / `health_check` 跑出来
`4.1.15.13（D:\WeiXin\4.1.15.13）` + `4.1.15.11(1 个文件)` 被独立判成残留（与报告 §2.3 各自独立量到同一件事）
+ 自报数据目录与配置**按真身比是同一条**（不报假警告）+ 崩溃记录 1 份 / 2026-09-05；
`test/wechat-client.test.ts` 13 条、`test/health_check_test.py` 7 条；变异检查：把"字段缺失 ⇒ 不通过"去掉 → 红。

**这一轮我踩的两个"看着合理的错"（都当场被自己抓住，记在这里）**：
① `--wechat-files-path` **不在 `Weixin.exe` 上**，它只在 `WeChatAppEx.exe`（小程序运行时那组）的命令行上
—— 我第一版只筛 `Weixin.exe`，于是这一项**永远是空的**且看不出来；② 从 `message_0.db` 推到数据根要**四**层
`dirname`（→ message → db_storage → wxid → xwechat_files），只去三层就退到 `<wxid>` 目录，于是
"不是同一份"这条警告**每次都报**（假警报）。两条都不是数据的问题，是判据的问题 —— 与报告 §10.4
"判据错了，不是数据的问题"是同一回事。

**没做的（明写）**：**没有**去解析 `update.data`（报告说它的**内部格式完全没解** ⇒ 不猜）；
非 Windows 直接报"只做了 Windows"（不是"没问题"）；**没有**读 exe 的字面量（故意的，见判据 1）。

**怎么自己复核这几条**（照那份报告的附录 A 的规矩：结论要能直接复现，别只信写的人）：

```powershell
# ① 运行时加载的是哪一份（判据是进程命令行，不是 exe 里的字面量）
weflow-cli check --json          # 看 wechatClient.version / loadedDirs / unusableVersionDirs
Get-CimInstance Win32_Process -Filter "Name='Weixin.exe'" | Select-Object ProcessId, CommandLine

# ② 客户端自报的数据目录（在小程序那组进程上；`Weixin.exe` 上没有）
Get-CimInstance Win32_Process -Filter "Name='WeChatAppEx.exe'" | Select-Object -ExpandProperty CommandLine |
  Select-String 'wechat-files-path'

# ③ 残留版本目录：**有版本目录 ≠ 能用**，判据是有没有 Weixin.dll
Get-ChildItem D:\WeiXin -Directory | ForEach-Object {
  "{0,-14} 文件 {1,4}  Weixin.dll:{2}" -f $_.Name,
    (Get-ChildItem $_.FullName -File | Measure-Object).Count,
    (Test-Path (Join-Path $_.FullName 'Weixin.dll'))
}

# ④ 崩溃记录（**只数那三个子目录** —— 与代码同一口径；只数条数，不读内容）
Get-ChildItem "$env:APPDATA\Tencent\xwechat\crashinfo\reports",
              "$env:APPDATA\Tencent\xwechat\crashinfo\records",
              "$env:APPDATA\Tencent\xwechat\crashinfo\metadata" -File -ErrorAction SilentlyContinue |
  Measure-Object
```

`4.1.15.13` + `4.1.15.11`（1 个文件）这两条就是上面 ①③ 跑出来的；数据目录那条的"按真身比"是因为
`C:\Users\<你>\xwechat_files` 实测是 `D:\微信\xwechat_files` 的**软链**（`③` 之外再 `realpath` 一下即可）。
②里那条命令行上还带着 `--client_version=4065599245`（= `0xF2541F0D`）—— 客户端**自报的构建 token**，
`check` 会把它与目录名**并列**报出来（它是"哪个构建"的第二个独立来源）。

**编码规则：低 16 位已经读完，高 16 位还没有**（2026-10-11 复核两对已知值）。按大端展开：
`4.1.15.13 → F2 54 1F 0D`、`4.1.13.12 → F2 54 1D 0C`，于是

- **byte 3 = 第四段**（`0D`=13、`0C`=12，逐字对上）；
- **byte 2 = 第三段 + 0x10**（13+16=29=`1D`、15+16=31=`1F`，等价于 `0x10 | 第三段`）。

**高 16 位两版都是 `F254`**，而这两版都是 `4.1.x` ⇒ 拿这两对**分离不出** major/minor（它可能在
高 16 位里，也可能根本不在 token 里）。**n=2，所以这只能说是"两对都吻合"，不是解出来了**；可判的
预测是 `4.1.15.11 → 0xF2541F0B`（本机那个残留版本目录上取不到 token，得等另一台机器或一个新版本）。

⇒ 结论不变：两者**谁也不推谁**，`check` 仍然只并列报出。本机 `update\download` 与 `update\patch`
两个目录都是空的（连隐藏项也没有），所以从这里拿不到更多配对。

**同一天还试过一条"顺手拿配对"的路子：客户端的日志 —— 不通，而且这是个容易骗过自己的地方。**
`%APPDATA%\Tencent\xwechat\log\` 有 53 个 `.xlog`（合计 **373 MB**），拿 `client_version=\d+` /
`F254[0-9A-Fa-f]{4}` / `\b4\.1\.\d{1,2}\.\d{1,2}\b` 去 grep：**0 命中**。但这**不是证据** ——
那些文件不是文本（头 16 字节 `07 0A 02 17 00 F7 D5 00 00 …`，Tencent 的 xlog 容器），grep 看不见
里面任何东西，这是**假阴性**。再往下探了一层：载荷也不是"单字节异或 + zlib/zstd" ——
256 个单字节密钥 × 4096 个偏移里"找到"52 个像 zlib 签名的位置，而**随机期望约 48 个**，
即那 52 个是噪声、不是流；Mars 默认 pubkey（`0000000000000000`）异或后同样没有签名。
⇒ **解 xlog 是一件独立的事**，不是拿更多 (构建, token) 配对的捷径。若将来要拿，别再从 grep 开始。

## D-106: 同库内消歧 —— 跨线同名的链接按名字带路径，而不是把两条线拆成两个库

**Status:** Active（补上 D-104 明确留下的那个口子：那条说"二义**报出来**而不是回避"，这条说**怎么修**）

**决定。** 命名空间分开之后，两条线各有一张同名页 ⇒ 裸 `[[DeepSeek]]` 在 Obsidian 里是二义的
（官方判据：会 deterministic 解析，但**不保证**命中你想要的那张）。两条修法摆在用户面前
（2026-10-10）：① 两条线各自一个根 = 各自一个 Obsidian 库；② 留在同一个库里，把指向同名的链接
**按名字带上路径**。用户选了 ②：**库结构不动**，链接写成 `[[Chat/Concepts/DeepSeek|DeepSeek]]`
（`|` 后面保住显示文本）。

**只改"两条线都有"的名字。** 只属于一条线的名字，裸写法本来就确定了；全量重写 4.9 万张页换不来
任何解析上的差别，而那种 diff 没人看得完。实测要改的：**36 个同名概念、477 张页、539 条链接**
（概念页 462 + 索引页 36 + 聊天卡 41；`001_Daily` / `002_Literature` / `Sources/WeChat` **一条都没有**，
所以不必扫其余层）。

**`## 来源` 那一段不许动。** 它的链接指的是**卡片**（`- [[会话-甲]] — 标题`），把它们改成概念页
是另一回事。实测这两段的二义链接分布是"相关概念 462 / 来源 0" —— 所以这条边界**没有割掉任何
该改的东西**，但它挡住了"改错对象"这一类。

**渲染端也要跟着改，否则下次编译就漂回去。** 三处渲染（模型建页、零模型建页、索引表）加
`chat_notes` 的卡片，统一用 `_utils.LinkNamer`（认线用**路径尾两段** `Chat/Concepts`，所以
测试的临时库、换过库根的场景都认得出；认不出就按老行为渲染裸名字）。

**解析端只做两件事，而且这两件都是量出来的教训。** `_utils.link_target_name` 切掉 `|显示文本`、
并对带路径的写法去掉前缀 —— 别的**一律原样返回**：

- **不切 `#`**：这个库里的概念名**真的带 `#`**（`#GPT6`、`Erdős#1026`）。顺手切了它们会被当成
  别的名字，指向它们的边整条消失 —— 实测图谱少了 **4 条边**，而"图里少一条边"不报错。
- **不切 `.md`**：`## 来源` 的卡片链接就写成 `[[名字.md]]`，`wiki_lint.resolve` 靠这个后缀把它
  分流到卡片目录；切了那一支就再也不走（`test/wiki_link_qualify_test.py` 与
  `test/wiki_lint_test.py` 一起钉住）。

**验证：批量重写后做的是反向一致性审计，不是正向抽样。** 改之前取体检底数、改之后逐项对回来：
`ambiguousLinks` **462 → 0**，而 `pages` / `broken` / `orphans` / `empty` / `duplicateTitles` /
`crossLineSameName` / `aspirational` **一项都没动**（49,956 / 9 / 0 / 1 / 0 / 36 / 10,324）。图谱
三种取值也与改动前**一字不差**（48,429/144,628、1,527/878、49,956/145,795）—— 这正是"限定形式
必须能被解析回同一个名字"的端到端证明。

**改名的那条规则，对"走图"的读者同样适用。** 路径消歧只解决"链接指向哪一张"，而读者自己也要选：
`assistantTools.conceptSubgraph`（`search_knowledge` 用它给出"这个概念周围的一小片"）原来每一步都用
"第一个有这个文件的目录"，于是**起点永远落在文章线**——刚说完"另一条线也有这个概念"就把它领到文章线
那张去。改成 **起点按调用方指明的那条线、之后沿 BFS 继承（本线优先，本线没有才回退到另一条）**——
与图谱的解析规则是同一条。回退**不能去掉**：聊天线的页会链到只存在于文章线的概念（实测 277 条），
那是有信息量的边，报成"缺页"等于把它丢了。

**没解决的：新的同名。** 两条线将来又出现同名概念时，新写的链接还是裸的（除非重跑
`wiki compile --qualify-links`）。兜底是**体检**：`ambiguousLinks` 那一节会列出来并给出修法。
把判断交给一个会报出来的检查，而不是假装它不会发生 —— 这是这条的边界，写在这里而不是等人发现。

## D-105: 检索也按线分：一个 `--line`、两层默认值，且默认写在输出里

**Status:** Active（接着 D-104：那条把**概念**分成两个命名空间，这条把**检索**分成两条线）

**决定。** 四个读者（`search` 语义检索、`chat` RAG、`vault search`、`vault rag`）加上助手的
`search_semantic` / `search_knowledge` 都接受 `line`：`wiki` = 文章线、`chat` = 聊天线、
`all` = 两条都看。**用户看到的默认是文章线**（两条线分开是用户的决定），而输出里**每一处都
写明这一趟搜了哪条线**，并在空结果时给出"怎么放宽范围"。

**两层默认值，方向是故意反的。** 函数层（`semantic_search.search`、`vault_search.collect_files`、
`vault_rag.collect_context`、`rag_chat.query_rag`）默认 **`all`**；只有 CLI/工具参数那一层默认
**`wiki`**。理由：调用方忘了传 `line` 时，"多搜一条线"只是结果多余，"少搜一半"是**静默的错答案**。
这一条与 D-051/D-104 记的是同一类失效——读一半而没有任何东西会红。

**掩码必须在取候选之前。** 语义检索那边按 `type` 过滤（索引是**一份**：两条线在同一次嵌入里
算，靠记录上的 `type` 分，所以不需要两份索引），做法是把不属于这条线的相似度压成 `-inf`
**再** `argsort`。反过来的"先取 top-k 再过滤"看着更简单，但会错两次：条数会少（`--top-k 10
--line chat` 只回 3 条，而没人会说为什么），以及 `pool_size` 截断之后**返回另一条线的结果**
（实测变异：`--line chat` 回了两条**文章**，还标着"聊天线"）。`test/knowledge_line_scope_test.py`
用一个人造索引钉住这个方向。

**输出形状变了一次，而且是**报错**地变。** `semantic_search.py search` 的 stdout 从**裸数组**
变成一个对象（`line` / `lineLabel` / `results` / `indexCounts`，有时 `note`）——范围与
"为什么是空的"只有进程边界那一侧知道。助手那边的解析因此从 `Array.isArray(data) ? data : []`
（旧形状会被静默降级成"没有结果"）改成**形状不对就报出来**：读不到和搜不到在聊天里长得
一模一样，这个仓库一直在防这种静默。

**为什么 `search-index build` 不加 `--line`。** 索引一份，按线的过滤在查询时做。分线建索引
只会让 `--line all` 变成**假的 all**（另一条线的记录根本不在索引里，而界面上看不出来）。
要省钱请用 `--days` / `--article-days` 收窗口。

**已知限制（没验过的那一半）。** 语义检索的**向量路径在本机从未用真索引跑过**——索引从来没建过
（`output/.semantic_index` 不存在，建它要 `dashscopeApiKey` 并花用户的嵌入费用）。所以掩码、
`line_counts`、"索引里没有这条线"这些是拿**人造索引**测的；真索引上的行为**没有实测**。
关键词回退那条路（本机实际会走的那条）在真库上验过：`vault search --line chat` 只回聊天卡与
聊天概念、`--line wiki` 只回文章。

## D-104: 两条线各有一个概念命名空间，而"一个名字全库只有一张页"这条规则撤掉了

**Status:** Active（**取代 D-051 里「A name belongs to exactly one page」那一段**；D-051 的其余部分——同一个库、
那份五个读者要对齐的目录清单、目录一致性测试——全部仍然成立）

**决定。** 概念名变成**带线限定**的：文章线与聊天线可以各有一张同名页；`compile_wiki` 不再因为
"另一条线已有同名页"而跳过；凡是把概念名当键的地方都改用 `_utils.concept_key(线, 名字)`
（形如 `wiki:DeepSeek`）。图谱节点的 id 就是它。`wiki lint` 把跨线同名放进新的一节
`crossLineSameName`（**报出来，但不当问题**），`duplicateTitles` 从此只管**同一条线内**撞名。

分隔符是 `:`：**概念页的文件名里不可能有它**（Windows 禁止，`compile_wiki` 也会把它换成 `_`），
而主键要能被原样切回 `(线, 名字)` —— 换成 `-` 或 `_` 都会把 `AI-Agent` 这类名字切错，症状是
"图谱里两个节点凭一个错名字连上了"，不报错。

**为什么必须撤。** 用户 2026-10-10 要求两条线在**概念命名空间、目录、检索/RAG** 三层上都分开，
同时保留"合起来看/查"的显式入口。而"一个概念名在库里只能有一张页"正好是"两个命名空间"的反面。

**那条规则原本挡的是什么，它现在还在不在。** D-051 当年否决"两条线各留一张同名页"的理由是
Obsidian 里 `[[DeepSeek]]` 会二义。这个理由**是真的**：官方文档给的判据是"有同名文件时，裸链接会
deterministic 地解析，但**未必命中你想要的那一张**"，消歧手段就是带路径的链接 `[[文件夹/名字]]`。
**而且它早就在库里发生了**：2026-10-10 实测，两棵目录里有 **36 个**同名概念各有一张页
（`DeepSeek`、`MiniMax`…，聊天页建于 09-27~28、文章页建于 10-01 那次批量编译），文章线一侧有
**539 条**链接指向这些名字。也就是说这条约束**在库里的数据上并不成立**，守卫只挡住了 09-27 之后
**新建**的同名页。
所以现在把二义**如实报出来**（lint 的 `crossLineSameName` 那一节），而不是靠"不建那张页"回避。
彻底解决它有两条路，都留给后面：① 两条线各自一个根（各自一个 Obsidian 库，名字各自唯一）；
② 给指向同名的链接按名字加路径。**本条目不预先选定**——所以今天在 Obsidian 里点一个同名链接，
命中的哪一张仍是不确定的：这是**已知代价**，写在这里而不是等人来发现。

**顺手修掉的一个真 bug。** 图谱节点 id 原来就是文件 stem ⇒ 那 36 组是**同一个键**。前端
`new Map(NODES.map(n => [n.id, n]))` 只留一个，症状很轻：节点总数看着对，其中 36 个点不到、
边连着错的那一张。实测（2026-10-10，直接调 `graph_3d.build_graph`）：
`--line all` 49,956 个节点，**主键唯一数从 49,920 升到 49,956（0 个撞键）**；边 145,794 → **145,795**；
289 条跨线边里"本来能在本线解析、却跑到另一条线"的是 **0**。同一改动下文章线单独跑是
48,429 点 / 144,628 边，与改动前**一字不差**。

**验证到什么程度（这条撤掉的是一个安全性质，所以要写清）。**
- 2D 视图在 jsdom + 假 canvas 上**真跑**：跨线同名的夹具下两个主键都在、图上写的是名字不是主键、
  搜这个名字会说"两条线都有"（`test/graph-2d-cli.test.ts`）；
- 3D 视图要 WebGL，jsdom 跑不了 ⇒ 源码形状那条测试（`test/graph-3d-cli.test.ts`）仍然在。
  **2026-10-11 补上了"真跑"**：`scripts/graph3d_check.mjs` 用 CDP 在**真 GPU 的 Edge** 里打开
  生成的页面，读它自己的钩子 `window.__viz` —— 判据是 `renderer.info.render` 的**计数器**
  （GPU 实际提交了多少 points / lines），**不是像素**（WebGL canvas 帧外永远黑，截图证明不了）、
  **也不是帧率**（页面按需渲染，空转的 rAF 量出来是 144 fps 那种假数字）。实测
  （`output/knowledge-graph-3d-core.html`，3825 点 / 12337 边，渲染器 ANGLE + RTX 5060 / D3D11）：
  **3 次 draw call、3938 个点、12337 条线**（`triangles` 恒为 0 —— 球与线是 `Points`/`LineSegments`，
  拿它当"画出来了没"的判据是错的），布局 68–172 ms，GL 提交中位 0.1 ms。
  ⚠️ **这条不在 CI 里**（要一个装了 GPU 浏览器的机器）⇒ 它是"本机可复现的验证"，不是回归守卫。
- 三条断言做过**变异检查**（主键退回 stem、`n.name` 退回 `n.id`、改一个标签字）——都真的会红；
- MCP 的 `wechat.get_concept` 用**真库**手工调过：36 个同名概念每个都会说出"两条线都有"并指明
  给的是哪一张；1491 个只在聊天线有的名字**不多嘴**（这条没有变成常驻测试——那些目录是模块级
  常量、不可注入，所以测试用不了夹具）。

**没改的东西。** 两条线**仍然共用一个库根** `output/wechat-vault`（物理分开是后面一步）；
`compile_wiki` 仍然一次只写一条线；`--output` 之外的地方一个都没动。

## D-103: `messages` 的退出码不动，但把"这个 ID 谁都不认识"说出来

**Status:** Active（补 `docs/HEALTH-CHECK.md` 里那行"未知 talker 退出码不一致"）

1. **不改退出码。** 拼错的 `wxid_…` 与"这条会话在窗口内确实没有消息"此前给出**一模一样**的输出：`未找到消息`、退出 0、`success:true`。但"没有消息"不是错误，脚本与 MCP 依赖这一点 ⇒ **退出码与 `success` 都不动**。
2. **只把歧义说出来。** 输入是 `wxid_` / `@chatroom` / `@openim` 形状（`resolveTalker` 对这三种**原样放行**，见 `src/utils/talkerUtils.ts`）、且**会话与联系人名单里都没有它**时，人看的输出补一句并**回显那个 ID**；`--json` 多一个可选 `note` 字段。
3. **判定做成纯函数**（`emptyMessagesNote` / `looksLikeTalkerId`，`src/services/messageQuery.ts`）：名单由调用方传进去，于是四种情形（认识的 ID、只在联系人里、拼错的 ID、名字形状）都能被单测钉住；另加一条**接线守卫**盯着"函数写了却没接上"——这类缺口本仓从来没有任何东西会报错。
4. 真实命令跑过：`messages wxid_zzz_not_a_real_id --json` → `{"success":true,…,"note":"这个 ID 既不在会话里、也不在联系人里：…"}`，退出码 0；人看的输出同样多那一行。

**Consequences:** `bin/weflow-cli.ts`（messages 命令里接线）、`src/services/messageQuery.ts`（两个纯函数）、`test/message-query.test.ts`（+3 条）、`CHANGELOG.md`、`docs/HEALTH-CHECK.md`（把那行从"没自动修"改成"退出码刻意不动、歧义已说明"）。无破坏性变更——JSON 只多了可选的 `note`。

## D-102: 入站媒体落地**默认关**；文件名与上限由本地定，失败不留半个文件

**Status:** Active（新能力 + 三条不变量；字段与优先级照厂商实现）

1. **能力**：官方通道（ilink）的入站图片/语音/文件/视频现在能下载并解密落盘。下载与解密的本事本来就有（`WechatClient.downloadMedia`），缺的只是把它接上 `parseInboundMessage` 这条路——此前那四项只产出 `filePath: ''`，助手拿不到图。与 D-042（`look_at_image`）合起来，"助手能看图"在两个通道上都通了。
2. **默认关**：新增 `wechatMediaDownload`，默认 `false`。打开它就是一次显式的网络访问，而本仓的取向是"网络访问要显式"（与 D-001/D-003 同族），所以这里是开关而不是默认行为。**关着时不发任何请求**——这条有测试钉住（不是"大概不会发"）。
3. **文件名由本地决定**：只取服务端名字的 basename，再过滤控制字符与路径字符、去掉前导点、限长。理由很直接：这是"外部输入变成本地路径"的唯一入口，`../` 必须在这一层被吃掉，不能指望调用方记得。
4. **上限 25 MB**；失败/空结果/超限一律 `path: ''` + 一行日志写明原因，**不写半个文件**。半个文件比没有更难查：下游会把它当成"有这张图"。
5. **字段与优先级照厂商实现**（`third-party/WeKnora/internal/im/wechat/longpoll.go`）：图片先用 `image_item.aeskey`（**hex 字符串**），再退 `media.aes_key`（base64）；语音/文件/视频只有 `media.aes_key`。**用错 key 不报错，只会解出乱码**，所以这条优先级本身就是一条要测的事实，而不是实现细节。
6. 目录默认 `output/wechat-media/`（`output/` 已在 .gitignore 里），可用 `WEFLOW_WECHAT_MEDIA_DIR` 改——测试因此只用临时目录，从不写仓库。

7. **它是"谁发来的都下"，不是"只下白名单的"**：下载发生在长轮询解析消息那一步，比"助手要不要回答"的判断**更早一层**（访问控制住在 `assistantService` 里）。所以白名单管的是**回不回答**，管不到**下不下载**；只要有人能给这个机器人发消息，他发的媒体就会落盘。这不是疏漏而是层次顺序的后果，所以写在这里而不是悄悄改行为；要收紧的话，正确做法是把"允许谁"的判据上移到解析层，而不是把下载搬到访问控制之后。

**Consequences:** 新配置键 `wechatMediaDownload`（五处齐全，`test/config-keys.test.ts` 会拦）；新模块 `src/services/wechatMedia.ts`；`parseInboundMessage` 变为 async（唯一调用点已 `await`）；`CHANGELOG.md`、`docs/PROJECT_STATE.md`、`OPERATIONS.md` 同步；测试 +10（`test/wechat-media.test.ts`，含"默认关不发请求""名字逃逸被吃掉""四种失败不留文件"与真实解密链路的假 fetch 用例）。**已在真实入站消息上验过**（2026-10-10，重新 `login-wechat` 之后）：先收到一条文字并被回答（证明通道与访问控制都活着），再收到一张真图 —— **27,637 字节、PNG 魔数、PIL 打开成功（798×513 RGB）**。这一条比"目录里有文件"强得多：乱码是打不开的，能打开才说明解密正确。同一时刻日志里的 `忽略非文本消息 (image)` 也印证了"文件落盘、但助手不把它喂给模型"这条设计。下一步若要把图交给模型，走 D-042 的 egress 决策，不要在这里顺手放开。

## D-101: `business/xweb/` 是 MMKV 加密存储：本地读不出来，这条线不进能力面

**Status:** Active（负结论 + **同日更正**：一处"缺口"是我自己的夹具坏了；现在部分存储已能解开）**为什么值得写下来**：它是"看起来该做、量完发现做不了"的那一类，不写下来会被反复重提（同 D-099）；而第 4、7 条记的是**两次把方法失败误当成对象性质**，这种错会以"有正对照"的样子出现，最值得留着。

1. **格式已钉死，可复核。** 账号数据目录下的 `business/xweb/`（含其 `mmkv/` 子目录）里是**成对的 MMKV 存储**：`<id>` + `<id>.crc`。客户端二进制里带 `MMKV.cpp` / `initializeMMKV` / `v2.4.0`，实现是厂商开源的那套。实测布局与源码一致：
   - 数据文件 = `[u32 actualSize][AES-CFB 密文]`，密文长度恰为 `actualSize`，其后是陈旧字节与零填充；
   - `.crc` = `MMKVMetaInfo`：`crc32 | version(=4) | sequence | IV[16] | actualSize | lastActualSize | lastCRCDigest | …`；
     ⚠️ **密文段长度要取 meta 里那个 `actualSize`，不要用数据文件头（偏移 0）那个 u32** —— 实测
     `db_storage/MMKV/f2541f00tinfo.mmkv` 的头部是 **0**，而 meta 里是 192；`crc32(偏移 4 起 192 字节)`
     恰好等于 meta 的 `crc32`（2026-10-11）。明文里的记录布局是 **4 字节前缀 + `(klen)(key)(vlen)(value)`**
     —— 这两处 2026-10-11 我本人都先搞错过，两次都是**正对照**（拿一个已知可读的存储跑）当场拦下的。
   - **`crc32` 等于 `zlib.crc32(密文段)`** —— 在两个互相独立的存储上核对通过（这条同时钉住了"密文从偏移 4 开始、长度就是头部那个数"）。
2. **加密 = AES-128/256-CFB，IV 每次全量写回随机生成并写进 meta**（源码：`doFullWriteBack` → `fillRandomIV` → `resetIV` → `writeActualSize(..., newIV, ...)`）。⇒ **IV 是已知的；唯一的未知量是密钥**。
3. **密钥不在任何能读的文件里。** 以下都试过，且都带正对照（自造 MMKV 明文 → 加密 → oracle 必须能解出、错 key 必须解不出）：
   - 数据库 key、配置里的密钥、passphrase、wxid 派生、md5/sha 变体（34 族 × 16/32 字节）× 各存储的 meta IV / 全零 / key[:16]；
   - 客户端二进制里所有 16/32 字节的**字符串型**候选（341,794 个，含 UTF-16LE）；
   - Weixin.dll 解出的**字符串池**（1,493 个定长候选）；
   - 运行进程内存里 **IV / 密文尾锚点 ±256 字节**内的所有窗口；
   - 从运行进程内存里**扫出的 892 把真 AES key**（按密钥扩展表的代数关系取值并整表核对过）× 16 个存储 × 8 种 IV。
4. **更正（同日追查，重要）**：上面第 3 条里"**AES-NI 字节交换布局没被覆盖**"这个结论**是错的** —— 它的正对照本身就是坏的（`bytes(x)[::-1] for x in flat` 遍历的是**单个字节**，造出来的是垃圾串），所以当时"红"的是夹具，不是方法。修正后**两种布局、四种情形全部通过**，扩展表扫描确实能用。
5. **实测：16 个非空存储里 13 个可读**，key 按 **MMKV 根**分两把（IV 一律取 `.crc` 里那 16 字节 —— "IV 存在 meta 里"至此由实测确认，不再只是源码推断）：
   - `config/` 根（key = 随机 16 字节）：`rs_config`、`file_config_2024v2`、`file_config_2025v2`、`file_config_2026v2`、`login_configv2`、`newinit_status`。解出来的键名是 `sync_11` / `2025-10…doc` / `SnsWeiShangSessionData` / `predownload_tasks` 这类。
   - `db_storage/MMKV/` 根（key = 字符串常量 `xwechat_crypt_key` 的**前 16 字节**，ASCII 可见 `xwechat_crypt_ke`）：`brandprivatemsg@hardcode`（`unreadcount29` / `red_dot`）与 6 个 `f254*tinfo.mmkv` —— 后者解出 25/107/139/145/185/187 条记录，且**整段恰好走完**（`185/2772`、`187/2802` 字节），这是最强的确认。注意它们的**键名与值都是二进制**。
6. **仍锁着 3 个**：`XEditorData` 与 `business/xweb/` 那两个（第三种 key）——这条负结论**逐条被验过**，不是"没试出来"：
   - **补记（2026-10-11）**：本机还有一个**这份清单没覆盖过的 MMKV 根** ——
     `%APPDATA%\Tencent\xwechat\radium\mmkv\`（小程序运行时的），4 个存储，**每个自己一个目录**
     （`<名字>\<名字>` + `<名字>.crc`，与数据目录那种"文件平铺"的布局不同）：`codecache_appid_dict`、
     `emu_pkg_appid_dict`、`xweb_config_storage`、`xweb_global_storage`。实测：格式与上面一致
     （3 个的 `crc32` 前置条件成立；`codecache_appid_dict` 的 meta 说 `actualSize=0`，是个空存储），
     而**我们能自己构造出来的那把 key（`xwechat_crypt_key` 前 16 字节）打不开它们**（同一次运行里
     正对照 13 条记录 / 100%、随机 key 0 条 ⇒ 探针本身是好的）。**结论只到这里**：它们用的是
     我们没拿到的那把（很可能就是 `config/` 根那把随机 16 字节），所以"仍需注入才能拿到 key"
     这个结论**不变**。**没有**尝试任何注入。
     ——这条根是**另一条线**（微信启动器逆向）点出来的，不是本线枚举到的；两线扫的不是同一处。
   - **顺带一个口径提醒**：存储数量不是常数。2026-10-11 按"数据文件非空"数：`xwechat_files` 两个
     根（去重后）共 **38** 对，其中 `db_storage/MMKV/` 下已有 **13** 个 `f254*tinfo`（上面第 5 条写的是
     当时那次运行里的 6 个）。⇒ "16 个"是**那天的口径**，别当常量用。
   - 不是某个**能读的存储**里的东西：把 13 个已解开存储的明文全部取出（本地落盘、不入库），在里面按"原始 16/32 字节高熵窗口"和"以它为种子做 md5/sha256 派生"生成 **356,160** 个候选 ⇒ 0 命中（判据有正对照，已知可读的存储被判"可读 ✓"）；
   - 不是二进制里**常量样字符串**的截断：补上了"更长常量的前/后缀"这个缺口（此前只测过长度正好 16/32 的、以及含 crypt/xwechat/mmkv 的），`[A-Za-z0-9_]{16,64}` 的全部前后缀共 **278,190** 个候选 ⇒ 0 命中；
   - **也不是"扫得不够勤"**：一个只扫私有可写区域的持续扫描器跑了 **90 分钟、189 轮**（每轮 24-28 秒、1.18 GB），累计 **2170 把真 key** ⇒ 仍未命中。把窗口量出来就明白为什么：该存储每 ~5 分钟被写一次（`.crc` 的 mtime 在变），而它的 AESCrypt 只在那次调用内活毫秒级 ⇒ 90 分钟里堆被"看"了约 5300 秒、窗口总量约十几毫秒 ⇒ **撞上的概率量级 1e-6**，不是运气问题。
   - 顺带纠正一个我自己的读法：`.crc` 的 **mtime 变而 sequence 不变** = MMKV 的 `writeCRCAndActualSizeOnly`（只写 crc 与 actualSize、不推 sequence）⇒ "mtime 变了"不等于"全量写回了一次"。
   - 也排除：没有 >512MB 的大区域被跳过（实测 0 MB）；key 不是根目录名/存储名/已知 key 串的哈希族（1761 个派生候选全灭）。
   - 也不是**注册表或别处的小文件**：`HKCU/HKLM` 下 `Tencent` 相关键几乎是空的（13 个子键、1 个值，没有名字像密钥的）；账户目录 + `%APPDATA%/%LOCALAPPDATA%\Tencent` + 安装目录下 **8923 个 ≤8KB 的小文件**里取出 **14,850** 个候选（hex 串、以及"名字像密钥的文件"里的 16 字节窗口）⇒ 0 命中。
   - **剩下的唯一入口是注入进程**（调试器断在 `CryptGenRandom` 或 `AES_set_encrypt_key` 上、或挂钩写入路径）。它与"只读内存"不是一个风险等级：可被客户端检测、可能影响其稳定性与账号安全 ⇒ **本仓不做**，也不属于这个项目的能力面。真要走，应当是一次**由用户明确授权、单独进行**的实验，并在动手前把"会被检测"这件事说清楚。
7. **判据本身错了五次，最后定下一套可验证的判据。** 落在最后的是三条同时成立（且**必须在 crc32 自洽的一对上判**）：
   - 每条记录：键 ≤ 200 字节、值 ≤ 256 KB（否则一条 `vlen=整段` 的"记录"就能把走过比例凑到 99%）；
   - 记录数 ≥ 8（实测真命中是 25 / 185 / 1229 条；随机 key 只凑得出 3-4 条）；
   - 走过的字节 ≥ 90%（真命中都是 100%）。
   于是它**同时**挡掉了两种错：太松会把随机走完当命中（第 5 条那几步就是在修这个），太严会把带二进制内容的能解存储判死（第 3、4 条）。而且用三个已知可解的存储做正对照，**判据不过就停**。
   `.crc` 的 `crc32 == zlib.crc32(密文段)` 这一条是前置条件：这些存储一直在被写（实测 `login_configv2` 的 seq 两分钟内从 1253 变 1254），读到半写状态拿到的是"密文对不上 meta"，会把能解的判成解不开。其它任何单一指标都**只能用来排序给人看**。
8. **别按块熵跳过**：为了让扫描快些，我一开始对每个块算"不同字节数"、太低就整块跳过 —— 这会把"**小对象落在几乎全新的堆页里**"整块丢掉（`AESCrypt` 只有 72 字节）。反退化判据放进预筛里就够了，扫描本来就不慢。
9. **即便解开，也未必是"浏览足迹"。** 客户端自带字符串显示这些存储的键是 `mmkv_key_use_sys_browser_key` / `mmkv_key_force_no_embedding` / `mmkv_key_dynamic_config`，加上存储名 `xweb_open_stat` —— 更像**内置浏览器的设置与统计**，不是页面访问史。投入之前应先定"要的是不是它"。
10. 本轮读进程内存：只在本机、只读、不落盘、不入库；上面所有数字都是计数，没有标题、键值或路径。

**Consequences:** 无代码改动、无 CHANGELOG（非用户可见行为）。`docs/CONTACT_DB_SCHEMA.md` 不动（那份文档管 contact.db）。若将来要做，需要一个独立的"内存里定位 AESCrypt 对象"工具，并且必须先补上第 4 条的缺口。

## D-100: 两轮新否证（`#8` 不是键位图、字段×特征无判别式）；`fav_notes` 的"本地正文优先"已量

**Status:** Active（补 D-095 / D-099；一半是否证，一半是"查过没坏"）

1. **两轮否证**（都写进 §7 的"别重跑"清单）：
   - **字段值 × "该号有什么"**（`external_info` 每个键的有/无、`type`/`child_type`/`brand_flag`、有没有 `Msg_` 表）——
     **没有任何"某取值下完全恒定"的判别式**。唯一部分信号：`#8` ↔ 有 `WxaAppInfo`（其 23 档里 3 档 P=1.0、3 档 P=0.0，
     其余 0.2~0.96）——**不是判别式**。
     ⚠️ **一个混杂项**：`ScanQRCodeType` / `IsShowHeadImgInMsg` / `IsHideInputToolbarInMsg` **三个键总同时出现**，
     于是在很多字段上刷出 0.7~0.86 的"最大分离度" —— 那描述的是"**资料更全的号**"，不是这个字段的含义。
   - **`#8` 是不是"哪些键存在"的位图** → **双向否掉**：同一个键集合对应多个 `#8`（51 个键集合里），
     同一个 `#8` 对应多个键集合（15 档里 11 档）。
2. **`fav_notes` 的"本地正文优先"量过（因为用户问"能不能看收藏文章的划线"而顺带查的）**：
   - `MIN_ARTICLE_CHARS = 200`；本机 1,234 篇文章里 **1,227 条走抓取、7 条**因网页描述 ≥200 字而用"自带正文"——
     那 7 条用的是 **`weburlitem/pagedesc`（网页摘要，不是正文）**；门槛挡住了短噪声：
     那 20 条 `type=1` 的 `desc` 只有 **49 字且是路径式字符串**（含 `/` `\` `:`）⇒ 全部落 `none` ✓。
   - **不改行为**（"本地优先、不联网"是它的卖点，且 `--dry-run` 的承诺就是只读本地），
     只把 `fav_notes.py` 里"文章类只有 `link`"改成"**基本**只有"，并把量出来的数字写进注释
     （那条注释原本是断言，现在是带分母的观察）。
3. **顺带（回答"划线"那条问题的依据）**：**本机收藏库里没有任何正文** —— 全部 1,259 条的文本节点里，
   最长的 1,177 字是**网页描述**，**>1,000 字的只有 2 个** ⇒ `fav-notes` 在文章上几乎全靠抓取，与设计一致。

**Consequences：** `scripts/fav_notes.py`（docstring 那句改准 + 写上数字）、
`docs/CONTACT_DB_SCHEMA.md` §7（宽扫清单由三轮扩成五轮）。无 CHANGELOG（非用户可见行为变化）。

## D-099: `contact-schema` **不**进 `capabilities --json`（那是策展子集）；小字段"出现情况位图"假设出局

**Status:** Active（补 D-088 / D-095；两条都是"量过之后不做"）

1. **`capabilities --json` 是人工策展的读接口表**（顶层键：`read` / `primitives` / `workflows` / `safety` / `schema` / `version`），
   `read` 里只有主流读面（sessions / messages / contacts / exports / favorites / configuration / accessControl…），
   `daily-stats`/`sns`/`stats` **同样不在**。⇒ 这是**子集，不是穷举**，所以 `contact-schema`（含新加的 `--contacts`）**缺席不是缺口**，
   **不加**；`docs/AI_INTERFACE.md` 的"推荐调用顺序"同理（也是一份子集）。
2. **一条同族的否证**：假设"某个小字段 = 该行可选字段的出现情况（计数或位图）"——
   拿 `#2/#8/#11/#12/#17/#18/#19/#22/#24/#37` 与"8 个可选字段里出现了几个"比：
   **命中 0~25/941（偶然水平）**，`#8` 的"置位数 == 个数"只 8 行 ⇒ **出局**（写进 §7 的宽扫清单）。
3. **这两条都不改代码**，但值得记：它们是"看起来该做、量完发现不该做"的那一类，不写下来就会被反复重提。

**Consequences：** `docs/CONTACT_DB_SCHEMA.md` §7（宽扫清单加第三轮）。无代码改动、无 CHANGELOG。

## D-098: `#6`/`#7` 不在客户端的字符串存储里（更像服务端标签）；短值搜大语料必须要求"独立成串"

**Status:** Active（补 D-096 / D-097；含一处**差点写错的"命中"**）

1. **两处搜过**：①混淆字符串池（41,922 条 / 去重 35,950）—— `#6` 的 51 个、`#7` 的 120 个取值 **0 命中**；
   ②`Weixin.dll`（192 MB）逐字节 —— 表面上 `#6` **51/51**、`#7` **119/120** "命中"。
2. **精度检查推翻了②**：要求"**独立成串**"（前后不是字母/数字/下划线）之后，`#6` 只剩 **9/51**、`#7` 只剩 **2/120**，
   其余都**嵌在更长的标识符里**。短英文词在 192 MB 二进制里**必然**作为子串出现 ⇒ 那些"命中"是**撞车**。
   **正对照通过**（`micromsg.ContactExtData` / `Weixin.exe` / `wcdb` 各命中 1 处）⇒ 这个否定是硬的。
3. **结论**：`#6`/`#7` 的取值**既不在混淆池里、也不作为独立字面量存在于二进制里** ⇒ 更像**服务端下发的标签**
   （客户端只显示/透传）。本机没有这份词表 ⇒ UNKNOWN，并写进 §7 的"别再找"。
4. **规则升级（写进 `AGENTS.md`）**：**在任何大语料里搜短值，必须要求"独立成串"，并说明搜的是哪个语料** ——
   混淆池里的否定**不能**推广成"二进制里没有"（那是另一个存储区，反之亦然）。
   这是本会话**第五次**同类（子串）撞车：`#9` 在消息表里的子串命中、"2 字节的 `#5`"、自/他对照缺一不可、
   以及这次的"短词嵌在长标识符里"。
5. **与 D-097 不冲突**：`#26` 的命名来自"**数据里就写着键名**"，不是"在二进制里找到了它"。

**Consequences：** `docs/CONTACT_DB_SCHEMA.md` §7（`#6`/`#7` 两行补"不在字符串存储里"；新增那一段两处搜过的记录）、
`AGENTS.md`（Verification 补"短值搜大语料要独立成串"）。无代码改动、无 CHANGELOG。

## D-097: 第四条定名路 = 字段内容自带键名；`#26` = `<bizuserattr>`（上次登录/发言/加好友时间）

**Status:** Active（补 D-081 / D-096；**第四个被命名的字段**）

1. **结果**：`#26` 的内容是 XML，**键名是客户端自己写的英文标识符** ——
   根标签 `<bizuserattr>`、内含 `<userop>`，三个属性 **`lastlogintime` / `lastmsgoptime` / `lastaddroptime`**，
   115 行全有、取值都是 **epoch 秒且落在 2013–2015**。
   ⇒ 读成"**我对该号的用户侧属性：上次登录 / 上次发消息 / 上次加好友的时间**"。
   **这个名字不是我起的，是数据里就写着的**（与 `#4.#2` 的 JSON 键 `custom_info` 同一类）。
2. **方法（第四条路，写进 §7）**：**先问"字段内容里有没有自带键名"**（XML 属性名 / JSON 的键）。
   全量扫大 proto 的所有 ld 字段 ⇒ **只有 `#26` 是**（没有别的 XML、没有顶层 JSON）。
   ⇒ 至此定名路四条：**(a) 跨表整值 join**、**(b) 自/他对照的内容刻画**、**(c) 公开码表 + 不依赖码表的预测**、
   **(d) 内容自带键名**。遇到未命名字段，按这四条依次问一遍，别急着说"拿不到"。
3. **边界**：`#26` 只有 **115/940** 行非空，且时间戳停在 2013–2015（"很久没动"的记录）⇒ 它**不改变**别的结论；
   也**没有**加进 `contact-schema --contacts`（那个命令只放日报线要用的三个字段，见 D-088）——
   要加是一条独立决定。
4. **仍未命名的**（各自缺什么写清）：`#6`/`#7` 像"英文词形标签"但**本机无词表**；`#19` 四族解释已否（D-083/D-095）；
   `#8` 像位掩码；`#25` 样本只 12 行。

**Consequences：** `docs/CONTACT_DB_SCHEMA.md` §7（`#26` 那行改成带名字的结论；新增"第四条定名路"块、
并列出四条路）。无代码改动、无 CHANGELOG。

## D-096: `#6`/`#7` 是"分类标签"、`#26` 是 XML；要名字只能靠外部标签表

**Status:** Active（补 D-085 / D-095；一条口径 + 一组新结论）

1. **两步一问**（这条口径值得沿用）：先问"**它是不是 message**"（判据：形状在同一批行里**全量一致**，D-085），
   再问"**不是 message 的话它是什么**"（长度 / utf8 / 可打印 / JSON / 去重数 / 首字符）。
   本轮把 `#6/#7/#26/#25` 与五个空壳一起过了一遍，结果见 `docs/CONTACT_DB_SCHEMA.md` §7 那张表。
2. **`#6` / `#7` = 分类标签**：非空取值是 **5~19 字节、全 UTF-8 可打印、纯 ASCII 字母**，去重只有 ~36~51 / ~120 个
   ⇒ 是**英文词形的标签**，不是消息、不是逐行唯一的值。
3. **已经排除的路**（别再试）：不是 `external_info`/`brand_info` 的任何叶子（前一轮全叶子比过）、
   不等于同行的 22 列、**也不由 `type`/`child_type`/`brand_flag` 决定**（只有 18/36 个取值唯一对应）。
4. ⇒ **要给它名字，只能像 `#5` 那样找一份外部标签表**（D-081 的第三条路：公开码表 + 一条不依赖码表的预测）。
   本机没有这份表 ⇒ 记 UNKNOWN，**不再在本机数据里找对应列**。
5. **顺带确认**：`#26` 是 **XML 片段**（100~118 字节、115 个互异值、首字符 `<`）；`#25` 是 31 字节、首字符为数字的串
   （12 个样本，判不了）；`#15/#20/#21/#28/#36` 与 `#33` 的壳性质再次确认。

**Consequences：** `docs/CONTACT_DB_SCHEMA.md` §7（新增"不是 message 的 ld 字段"表 + "别再找"的结论）。
无代码改动、无 CHANGELOG。

## D-095: `#19` 的第三、第四族解释也被否；平凡命中已数到五种，筛子要写进判据函数

**Status:** Active（补 D-087；本轮的价值全在**否证**与一条方法）

1. **`#19`（0~10）又否掉两族**：①用"同一个号在 `biz_message_0.db` 里的 `Msg_` 表行数"（本地文章数）比 —— 只对上 6 行；
   ②与 `type` / `child_type` / `VerifyBizType` 这些**分类**字段交叉 —— 都不确定。
   加上 D-083 否掉的"菜单按钮数"与"子字段重复条数"，**一共四族**。
   ⚠️ 但它现在的分布值得记：**0 占 667、1~10 各 20~37（平坦）** ⇒ **不像计数（计数会衰减），像枚举/序号**。
2. **两轮宽扫都判负**（写进文档，省后人重跑）：把十个小字段与"同行 biz 元数据里的十来种计数"逐个比 —— 无一对上；
   与"253 个非 biz 行才有的特征"（类别/`local_type`/加密名/是否会话/是否在 `chat_room`/有无那个 URL）交叉 ——
   没有任何"某取值下恒定"。**`#2/#12/#17/#22/#24` 在 biz 行上非零行为 0**（D-087），所以"用 biz 计数测它们"天然无效。
3. **平凡命中数到五种，于是规则升级**：①A 常量 ②B 常量 ③A 取值近乎唯一 ④**双方同时为 0**（第四种本轮才遇到：
   `#2=0` 与"小程序条目数=0"⇒ 假 676/688）⑤**筛子只写在调用方** —— 第四种的教训刚写进文档，
   **我新写的脚本就再犯了一次**（`RegisterSource` 那列整列不存在 ⇒ B 恒为常量 ⇒ 报"完全确定"）。
   ⇒ 结论写进 `AGENTS.md`：**排除常量的判断要写在判据函数内部**，不能指望每次调用时记得。

**Consequences：** `docs/CONTACT_DB_SCHEMA.md` §7（`#19` 那行补四族否证与"平坦分布"的观察；新增"两轮宽扫"与
"平凡命中五种形态"两块）、`AGENTS.md`（Verification 那条补"筛子写进函数里"）。无代码改动、无 CHANGELOG。

## D-094: 全量集成核对揪出 `--contacts` 的分类缺陷；库是活的，判解码对不对要看逐行关系

**Status:** Active（补 D-088；一条口径修正 + 一条方法）

1. **做法**：把刚发布的 `contact-schema --contacts` 在**全量**数据上跑一遍，与文档的断言逐条对
   （kind 分布 / region 数 / updatedAt 数 / **`#13` 与 `biz_info.type` 逐行相等**）。
2. **揪出的缺陷**：`#13 == biz_info.type` 在 **688/688** 行上成立（0 处不一致，这是"解码对不对"的硬判据）；
   但 kind 分布是 **944 / 43**，文档是 **940 / 44** ⇒ 差 1 行。根因：我的判据是"字段号全 ≤ 9 **且**有 `#1`"，
   而那一族里**确有一行没有 `#1`**（B3 当年就报过 `#1` 只有 43 行）⇒ 它被错判成联系人。
   修法：判据只留"**字段号全 ≤ 9**"（大 proto 一定有 `#2..#38`，不可能全 ≤ 9）⇒ 复测 **943 / 44**，与文档的 44 一致。
   合成测试补了"没有 `#1` 的小形状"两条，把边界钉住。
3. **一条方法**：**绝对值对不上不等于解码错**。同一时刻的复测里 987 vs 986、718 vs 717、566 vs 564 都是**库在变**
   （这段时间多了一个账号、有行被更新过 ⇒ `#41` 从 0 变成时间戳）。所以判解码正确性要用**逐行关系**
   （`#13 == type` 这类），**不要用绝对计数** —— 文档 §0 已把这条写成提示。
4. **这也是"先发布、再全量核对"的收益**：合成单测抓不到口径缺陷 —— 我当时的用例**恰好都带 `#1`**，
   于是判据里那个多余条件一直没被触发。

**Consequences：** `scripts/contact_schema.py`（kind 判据）、`test/contact_schema_test.py`（+2 条边界用例）、
`docs/CONTACT_DB_SCHEMA.md` §0（"库是活的"提示）、`CHANGELOG.md`（给未发布的 `--contacts` 那条补一句口径修正）。

## D-093: 一条"已修却没标"的限制行（`dashscopeApiKey`）；文档协议补一条

**Status:** Active（补 D-015 那条"文档要跟源码同步"；含一处**非破坏性验证**的示范）

1. **发现**：`docs/HEALTH-CHECK.md` 的"目前已知、但**未修**的问题"里仍写着
   "`dashscopeApiKey` 无法通过 `config set` 写入 …… 需要决定是否扩大密钥配置面"。但它**早就修了**：
   键在 `CliConfig` 里、在 `configurableKeys` 里、也在 `ENCRYPTED_KEYS`（加密存储）里；
   `test/config_keys_declared_test.py` 这个测试**就是为它建的**；而 `docs/PROJECT_STATE.md` 早写着
   "Both are settable now"，`README.md` / `OPERATIONS.md` 也都在教用户 `config set dashscopeApiKey`
   ⇒ **四处口径里有一处没跟上**，而它偏偏是"限制清单"——最容易被读者当成现状的那一处。
2. **验证方式（值得抄）**：`weflow-cli config set dashscopeApiKey sk-test… --dry-run --json`
   → `{"success":true,"dryRun":true,"action":"config.set","key":"dashscopeApiKey",…}` ——
   既证明该键被接受，又因为 `--dry-run` **不落盘**、没碰用户配置。
3. **修**：那一行改成"**已修** + 守卫测试名"（同一节里另一行早就是这种写法）；该节引言补一句"修好的行留在表里并标注"。
4. **协议补一条**（`AGENTS.md` 的 Documentation Protocol）：**若某次改动修好了某份文档的"已知未修 / 局限 / 尚未实现"
   清单里的某一项，必须在同一次改动里更新那张清单** —— 跳过它**不会有任何测试变红**，这正是它会烂的原因：
   代码那侧会长出测试（`config_keys_declared_test.py` 就是），紧挨着它的散文会漂。
5. **给用户的可执行结论**：本机语义检索那条线**卡在两步**上（设 `dashscopeApiKey` + 建语义索引；`PROJECT_STATE`
   记着"这台机器两样都没有"）。补上之前，`search_semantic` 这个工具**根本不会摆给模型**（设计如此，见 D-058 一族）。

**Consequences：** `docs/HEALTH-CHECK.md`（那一行 + 该节引言）、`AGENTS.md`（Documentation Protocol 加一条）、本条目。
无代码改动、无 CHANGELOG。

## D-092: 文本模式的 `open()` 必须写明 `encoding=`（已钉住）；判据别把 `Image.open` 当内置 `open`

**Status:** Active（补 D-090；**不是修 bug，是把一条已经普遍执行的规矩钉住**）

1. **审计结论（配了正对照）**：60 个脚本里文本模式的 `open()/write_text()/read_text()` **186 处全部带 `encoding=`、
   0 处遗漏** ⇒ 读写文件这一侧本来就是干净的。漏了 `encoding=` 时中文 Windows 会按 **GBK** 读写，
   而下游（HTML / Vault / 日报里）按 UTF-8 解 —— **静默乱码、不报错**，所以值得钉住。
2. **判据踩的坑（值得记）**：第一版按"函数名是 `open`"匹配，把 **PIL 的 `Image.open()`** 也算成文本读 ⇒
   **10 处误报**。它是**二进制图片解码器**，给它 `encoding=` 才是错的。修法：只认**内置** `open`（`ast.Name`），
   属性调用只认 `write_text`/`read_text`；并把 `Image.open` / `tarfile.open` / `zipfile.open` 三个"不该报"写进对照用例。
   （又一次证明：**审计脚本自己也要有对照**，见 AGENTS.md。）
3. **顺带 3 处真的例外**：`nt_decrypt.py` 里 Linux 分支读 `/proc/<pid>/{comm,statm,maps}`（内容是 ASCII、Linux locale
   也是 UTF-8，**不算真风险**），仍补上 `encoding='utf-8'` —— 让规矩**没有例外**，省得以后在判据里开白名单。
4. **守卫落点**：同一个 `test/script_stdout_encoding_test.py`（CI 的 `unittest discover` 自动收），
   连同 stdout 那半一起；"0 漏"后面跟着正对照（数不到 186 就报扫描器有问题）。

**Consequences：** `scripts/nt_decrypt.py`（3 行）、`test/script_stdout_encoding_test.py`（新增文件编码那一类 + 2 条用例）、
`docs/EXTENDING.md`（Recipe E 指向同一份测试，说明它同时管 stdout 与文件）。无 CHANGELOG（不是用户可见的行为变化）。

## D-091: "每个命令的旗标都必须转发给脚本"这条规则**不推广**（量过了，20 个命中绝大多数是误报）

**Status:** Active（记录**已搜范围**，同 D-072 的做法；结论是"不加这条测试"）

1. **动机**：D-088 给 `contact-schema` 立了一条守卫 —— 声明的每个 `.option` 都必须在 `.action` 之后**被转发给脚本**。
   很自然想问：能不能推广到整个 CLI？于是用同样的源码扫描把 `bin/weflow-cli.ts` 里 **114 个命令块**量了一遍。
2. **结果：20 个块命中，但逐条看下来基本是误报**，原因三类（都不是 bug）：
   - **`--dry-run` / `--yes` / `--json` 由 TS 层自己消费**（两阶段确认门与机器可读输出；`opts.dryRun` / `opts.yes`
     在文件里出现几十处），本来就不该转发给脚本；
   - **`--api-key` 是故意不进 argv 的**（密钥走配置/环境；进程列表可见，`test/privacy-paths.test.ts` 也守着这条）；
   - **我的"块切分"对带子命令的父命令是错的**：`.command('pipeline')` 把几十个子命令并成一个块，
     于是"声明"列里出现十几遍 `--dry-run,--yes,--json` —— 命中数里有一部分纯是切分造成的。
   - 另有 `opts.path` / `opts.month` / `opts.talker` / `opts.open` 这类"在 TS 层用掉、或按别的方式传给脚本"的。
3. **决定：不加** repo-wide 版本。`contact-schema` 那条守卫保留 —— 它的每个声明旗标**确实都是字面转发**
   （已做变异检查：把转发改成 `[]` 立刻变红），所以那条规则在**那个命令上**是硬事实，在别的命令上不是。
4. **顺带把"旗标的三种归宿"记下来**：**转发给脚本** / **TS 自己消费** / **故意不进 argv（密钥）**。
   以后要立类似规矩，得先按这三种归宿分类，**不能按"命令块里有 `.py`"一刀切**。

**Consequences：** 无代码改动（这轮的价值是"别再试一遍"）。`contact-schema` 的守卫不变。

## D-090: 两个脚本在"重定向"下会崩（已修）；这类 bug 被环境变量盖住，所以守卫必须是静态的

**Status:** Active（补 D-076 / D-089；含一处口径收紧）

1. **把"有风险"收紧成"可证明会崩"**：审计 `scripts/*.py` 时，第一版判据是"会打印中文却没有 `reconfigure`" ⇒
   **13 个**文件命中；但真正的崩点是"**打出 GBK 编不出来的字符**"（纯中文在 GBK 下只是乱码，不崩）⇒
   逐字测 GBK 可编码性后只剩 **2 行**：`quality_eval.py` 的 `⚠️`、`wechat_emoticon.py` 的 `✓`。
   （又一次"先说清数的是什么"——见 AGENTS.md 新加的那条。）
2. **复现（这一步同时解释了第一次为什么没崩）**：本机与桥接都注入 `PYTHONIOENCODING=utf-8`，
   于是 `python scripts/x.py > out.txt` **在这里永远不崩**；用 `python -E`（忽略 PYTHON* 变量）才看得见真实处境：
   重定向时 `sys.stdout.encoding = gbk`（locale cp936），`print('✓')` →
   `UnicodeEncodeError: 'gbk' codec can't encode character '✓'`、**退出码 1**。
   ⇒ **同一个变量既会伪造 bug（D-076 那条），也会掩盖 bug（这条）**。
3. **修法**：给这两个脚本加模块级 `sys.stdout.reconfigure(encoding='utf-8', errors='replace')`
   （`wechat_emoticon.py` 顺带补 `import sys`），与已在这么做的 39 个脚本一致 ⇒
   **脚本不再依赖调用方有没有设环境变量**（这正是 Recipe E 的原话）。
4. **守卫写成静态的（这是本条的重点）**：新增 `test/script_stdout_encoding_test.py`，
   用 `ast` 查"stdout 字面量里有没有 GBK 编不出的字符 ∧ 文件里有没有 `reconfigure`"，**不 spawn 子进程** ——
   因为 spawn 版本在本机是**假阴性**（见 2）。它自带 4 条正对照（该报的报、纯 ASCII 与纯中文不报、
   有 reconfigure 就不报、反"空扫"的计数下限），CI 的 `unittest discover -s test -p '*_test.py'` 自动收。
   **先跑红（精确报出那两行）再修**，修完全绿（Python 全量 834 条）。
5. **`docs/EXTENDING.md` Recipe E** 补了这条两面性，并指向新测试。

**Consequences：** `scripts/quality_eval.py`、`scripts/wechat_emoticon.py`（各加一行 reconfigure）、
`test/script_stdout_encoding_test.py`（新）、`docs/EXTENDING.md`、CHANGELOG 一条（**崩溃修复算用户可见**）。

## D-089: 负结论必须配"写进脚本"的正对照（同族错误第三次）；`contact` 的群行；`#2` 与会话

**Status:** Active（规则升级到 `AGENTS.md`；补 D-076 / D-087）

1. **同族错误第三次，所以规则升级**：本轮查"`#2` 与有没有会话"时，第一次得到"**253 个非 biz 行全都不是会话**"——
   假的：`contact` 那条连接设了 `text_factory = bytes`（返回 bytes），消息库那条没设（返回 str），
   **bytes ∩ str 恒为空集**，而"没有交集"长得**就像一个发现**。第一次（D-076）是同一个坑，当时只在文档里记了；
   这次把规则写进 **`AGENTS.md` 的 Verification 一节**（未来所有会话/Agent 先读的那个文件）：
   - **负结论必须先跑正对照，而且正对照要写进脚本**（控制组为空就让脚本自己报"这次比较无效"，别报 0）——
     本次的正确做法就长这样：先断言 `Name2Id ∩ contact.username = 587/617` 非空，再谈别的。
   - **"相等"类结论强不强，看两个错格**（`bit3 ⟺ 在 biz_info` 之所以能写下来，是因为两个不一致的格子都是 0）。
   - **报"有多少种"时必须说清数的是什么**（长度 / 取值 / 行 —— 这三个在 `#27.#2` 上就差过一回，D-086）。
2. **两条新事实**：
   - **`contact` 里 78 个 `@chatroom` 行，77 个就是 `chat_room` 的群**（76 个 `local_type = 2`），
     另有 1 个"像群但不在群表里"的行 ⇒ 非 biz 的 253 行里的"78 个群"就是表里同一批群。
   - **`#2` 与"有没有会话"相关但不等价**（`Name2Id.is_session`，正对照 587/617）：`#2 = 0` 的 109 个非 biz 行里
     **75 个是会话**（那 78 个群基本都在），`#2 = 1` 36/98、`#2 = 2` 14/46 ⇒ `#2 = 0` 偏"有会话"、
     `#2 ∈ {1,2}` 偏"没会话"，而后者同时也与那个 URL 强相关（123/144）。`#2` 仍 UNKNOWN，但有两个相关项。
3. **文档协议的一处自我纠正**：`AGENTS.md` 写着"`CHANGELOG.md` 只记**用户可见**的发布说明，别当工程日记"。
   前几轮给纯文档/研究轮次也加了 CHANGELOG 条目，**不符合这条**；从本轮起，纯研究/文档轮次**不加**，
   只有动到 CLI/行为（如 D-088 的 `--contacts`）才加。

**Consequences：** `AGENTS.md`（Verification 加两条规则）、`docs/CONTACT_DB_SCHEMA.md` §7（新增"群行 + `#2` 与会话"块、
含那次假负结论的复盘）。**本轮不加 CHANGELOG**（按第 3 条）。

## D-088: `contact-schema --contacts`：把 `contact.extra_buffer` 那三个已验证字段做成出口（并说明为什么不输出 `#4`/`#9`）

**Status:** Active（**取代 D-081 里"代码这边不用动"那句**）

1. **为什么改主意**：D-081 当时写的是"没有任何代码读 `contact.extra_buffer` 的字段 ⇒ 这次命名只进文档，不进实现" ——
   那是**命名只有一个字段时**的取舍。到 D-084 已经有三个字段被验证（`#5` 地区码、`#13` = `biz_info.type`、
   `#41` = 资料最近更新时间），再让它们只躺在文档里，就成了"**库里有值而工具不说**"。⇒ 加 `--contacts` 放出来。
2. **放出来的三个**（与文档同一口径）：`region`（`#5`，**只在"恰好两个大写字母"时给**，否则留空、原值仍以长度留在 `unrecognized`）、
   `bizType`（`#13`）、`updatedAt`（`#41`；**`0` 按"显式未设置"处理，不当 1970**）。
3. **故意不输出的**（写进代码注释与测试，免得后人以为是漏了）：
   - **`#4`/`#9`**：已定性为"该账号自身资料文本的汇集"（含主体名/菜单按钮名）—— 它是**内容不是字段名**，
     打进 CLI 输出等于把账号资料漏出去 ⇒ 只以**长度**出现在 `unrecognized` 里；测试**断言内容不在输出里**。
   - OpenIM 那 44 行的小形状：只报 `kind='openim'`，不套联系人那三个字段、也不输出它的 `#1`/`#2`。
   - 其余 30 来个字段：未定名 ⇒ 原样进 `unrecognized`（**D-068 不变**）。
4. **顺手补的一条守卫（这才是本轮"需要修复"的那处）**：TS 层是**手写转发旗标**的，而此前**没有任何测试守它** ——
   少写一行 `--contacts`，`commander` 照样接受、脚本照样跑，**只是安静地去读了群**（错的是"读什么"，不是"报错"）。
   `test/contact-schema-cli.test.ts` 现在加两条：①**声明了 `.option(...)` 的每个旗标都必须在 `.action(` 之后的片段里
   被转发**（通用断言，以后新加旗标一样生效；**已做变异检查**：把转发改成 `[]` 立刻变红）；
   ②`--contacts` 必须是命令行认得的旗标（不是被 commander 拒掉的死开关，判据是"同样走到脚本再失败"）。
   判据写清楚：**旗标声明在 TS、解析在 Python，中间那一步是手写的，所以它必须被测试盯住**。

**Consequences：** `scripts/contact_schema.py`（`decode_contact` / `load_contacts` / `--contacts`）、
`bin/weflow-cli.ts`（声明 + 转发）、`test/contact_schema_test.py`（6 条合成测试，含"内容不出现在输出里"）、
`test/contact-schema-cli.test.ts`（2 条新的守卫测试）、`docs/CONTACT_DB_SCHEMA.md` §5、`OPERATIONS.md` §3、
`docs/PROJECT_STATE.md`（测试数 856 → 860、入口清单加一条）、`docs/EXTENDING.md`（守卫表加一行）同步。
**测试数**：`npm test` 858 → **860**（新增 2 条 TS）；Python 单测该文件 4 → **10** 条。

## D-087: blob 内部两两扫描：真结果三条 + "决定"的三类平凡形态

**Status:** Active（补 D-082 / D-086；方法更正为主）

1. **做了什么**：940 行的 `ContactExtData` 主体里，遍历所有"trait 对"（15 个 varint 字段的取值 + 17 个 ld 字段的"有没有值" + `#41` 的年份），
   问"**A 的取值是否唯一决定 B 的取值**"。**未筛前 398 对**，筛掉平凡情形后 **16 对**，其中真信息三条：
   - **biz 行（`#13` 存在、687 行）上，`#2`/`#12`/`#17`/`#22`/`#24` 恒 0、`#25` 恒缺** ⇒ 这批"小旗"**只在非 biz 行**出现。
   - **`#10 = 0 ⟺ #11 = 0`** 被这套独立方法**再确认一次**（`v11 → v10` 的映射表就是它）。
   - **`#2` 与那个 URL 强相关**：非 biz 的 253 行里 **78 行是 `@chatroom`（群、全 `#2=0`）**、余 175 行为个人；
     **`#2 ∈ {1,2}` 的 144 行里 123 行带 `#27.#2` 的 URL（85%）**，`#2 = 0` 的 109 行只 18 行带（**17%**）。
     ⇒ **相关但不等价**，`#2` 的含义仍 UNKNOWN（这条把 `#2` 从"纯未知"推进到"与某个 URL 的在场强相关"）。
2. **"决定"关系的三类平凡形态（本轮全踩了）**：① **A 是常量**；② **B 是常量**（本轮第一版就是栽在这 —— 
   `has15` 恒真、`v16`/`v23` 恒 0，于是"398 对完全确定"里绝大多数是噪声）；
   ③ **A 的取值几乎唯一**（`#41` 有 565 个取值，每个只对应一两行 ⇒ "决定"必然成立）。
   ⇒ **判据必须加两条**：A 与 B **都**得有 ≥2 种取值；**A 的每个取值都要有 ≥5 行**。
   这与 D-076/D-079/D-080/D-085 是同一条纪律的第四次应用：**任何"关系"都要先问"不成立时会是什么样"**。

**Consequences：** `docs/CONTACT_DB_SCHEMA.md` 的 §7（新增"blob 内部两两扫描"块：真结果三条 + 三类平凡形态 + 两条判据要求）。

## D-086: `#27` 的 `#2` 是 URL（141 行、3 个 host）；"长度种数"别读成"取值数"；到形状为止

**Status:** Active（补 D-085；含一处**读数更正**与一条**止损**）

1. **形状**：`#27.#2` 只在 **141/940** 行有值，**141/141 是 `http://` 的 URL**（长度众数 107~110 字节、
   **只有 3 个 host**），承载行**全不在 `biz_info` 里**（`local_type` 1 占 97、0 占 44）。
   这 3 个 host 在 `contact` / `biz_info` / `chat_room_info_detail` 的**任何列里都不出现**
   （对照：`big_head_url` 3914 行非空、`brand_icon_url` 691 行，命中 **0**）⇒ **不是本机已知的任何 CDN**；
   与同行列也不同（0 行含该行 `encrypt_username`，仅 4 行含昵称/拼音）。**含义 UNKNOWN。**
2. **读数更正（我自己的）**：先前那句"`#2` 只有 14 种"是**长度的种数**，不是**取值数** ——
   `min/max/种数` 这一栏本来就是长度口径，我把它读成了取值口径。**报"种数"时必须写清是"什么"的种数。**
3. **止损（这条也是决定）**：URL 这条线**到形状为止**。再往下解就要碰 URL 的路径内容，
   而它既不解锁别的字段、又涉及本机账号的主页地址，**收益与隐私都不划算** ⇒ 记 UNKNOWN，不挖。

**Consequences：** `docs/CONTACT_DB_SCHEMA.md` 的 §7（`#27` 那行补 `#2` 的口径 + 新增"那个 URL 单独记一笔"）。

## D-085: "走线能走通"不是"它是 message"的判据；据此更正两处旧写法

**Status:** Active（更正 §3 与 A3 报告里两处；补 D-068）

1. **一条判据更正**：把所有 ld 字段再走一层之后发现，**"能走通"的行数**并不能说明它是 message ——
   文本字段 `#4` / `#5` / `#9` 也各有 **294 / 252 / 393** 行"走通"（随机字节撞成合法 tag）。
   **正确判据是"形状在同一批行里全量重复"**：`#14` **830/830** 一致（`{#1: 0/1, #2: 13 字节}`）、
   `#27` **937/937** 一致（5 个字段）⇒ 这两个是 message；而 `#6`(555/940)、`#7`(473/940)、`#26`(825/940)
   **达不到全量** ⇒ 不算（至少不能当"形状固定的 message"）。
   ⇒ 据此**更正** §7 那张形状目录里的一行（"`#4 #5 #6 #7 #14 #15` 可解析成嵌套消息" —— 它是**照 `stranger` 的 1 行**
   记下来的，套到 `contact` 的 940 行不成立），以及 A3 报告里同源的表述。
2. **顺带拿到的形状**：`#14` = `{#1: 0/1, #2: 13 字节}`；`#27` = `{#1: 0/1, #2: 84~141 字节长文本（仅 14 种）,
   #3: 142 种取值, #4: 11 种, #5: 掩码 {0,72,720,4320,-1}}`（`72/720/4320` = 72×1/×10/×60，形似**时长**，
   **无对照源 ⇒ 不命名**）；`#33` / `#36` / `#15` / `#20` / `#21` / `#28`（以及多数的 `#26`）
   **是空壳标记** —— 它们"存在"本身就是信息（例如 `#33` ⟺ `#36` ⟺ `#27.#3` 那组同现）。
3. **这条纪律与 D-076/D-079/D-080 同族**：**任何"看起来像 X"的判据，都要先问"不成立时会是什么样"** ——
   文本撞 tag 就是"走通"的假阳性版本。

**Consequences：** `docs/CONTACT_DB_SCHEMA.md` 的 §7（形状目录里"可解析成嵌套消息"那行加删除线 + 更正）、
§7（新增 ld 内层形状表 + 判据更正 + `#27.#5` 的"不命名"说明）、§9（新增复跑 22：**形状全量重复**才是判据）。

## D-084: `#41` = 该行资料的"最近更新时间"；`#43` 不是 id 而是个小结构；行属性那条线是负的

**Status:** Active（补 D-082 / D-083）

1. **第三个定性的字段**：`#41` = **该行资料的最近更新时间（epoch 秒）**。证据三条：
   ① epoch 秒、691 行出现（127 行显式 0）、非零 564 行、样本 2019–2026 且 70% 在 2026；
   ② **与 `contact.id` 的 Spearman = 0.945**（n=564），且 564 个值**互不重复**；
   ③ **"创建时间"被反例否掉** —— **40 行是"`id < 1500` 却带 2025/2026 时间戳"**（创建时间不可能这样）。
   ⇒ 读成"被更新过"；多数行没改过，所以看起来≈创建时间（这正是 ② 高而不到 1 的原因）。
   口径写明：这是**本地推断**（22 列里没有任何时间列能对应），不是厂商命名。
2. **`#43` 的旧描述（"22 字节的 id 样值"）是错的**：内层解开是
   `{#1 ∈ {0,1}, #2 = {1:1, 2:∈{1,3,4}, 3:3, 4: repeated {1:10/11/12}}}` —— **只有 10 种变体**、
   最大一种占 448/628 ⇒ 更像"**一组能力/范围编号**"，**不是不透明 id**。形态高置信，含义 UNKNOWN。
3. **一条负结果（省后人一轮）**：把 12 个小 varint 字段逐个与**行级属性**（账号类别 / `local_type` / `flag` /
   `verify_flag≠0` / `is_in_chat_room` / `chat_room_type` / `chat_room_notify` / 有无 `encrypt_username`）
   做交叉，找"某属性在某取值下完全恒定"的干净关系 —— **一个都没有**（唯一"干净"的是 `#13`，那是同义反复）。
   ⇒ **这批字段不挂在行的这些属性上**，别再沿这条线找名字。
4. ⚠️ 我自己那轮扫描有个设计缺陷（如实记）：把"是大 proto 行"这个**恒定属性**也放进了候选判别式，
   于是每个字段都"在 big 上完全确定" —— 输出里那一堆 `big=True` 全是噪声。**候选属性里不能有常量。**

**Consequences：** `docs/CONTACT_DB_SCHEMA.md` 的 §0（`contact.extra_buffer` 行补"`#41` 已定性"）、
§7（`#41` 那条改写并给出判据、`#43` 那条改写为"嵌套小结构"、新增"小字段不挂在行属性上"的负结果、
候选名表两行更新）、§9（新增复跑 21：`#41` 的 Spearman + 反例计数）。

## D-083: `#10 = 0 ⟺ #11 = 0` 已验；`#19` 的"重复条数"假设被否

**Status:** Active（补 D-082；两条都属"把值域表继续做厚"）

1. **上一条留的线头验了**：`#10 = 0` 与 `#11 = 0` **是同一批 129 行** —— 940 行里 `(F,F) 811 / (T,T) 129`，
   **两个错格都是 0** ⇒ 两者**共用一个"未设置"状态**（与 `#10`/`#38` 的 `0 / -1` 哨兵同一族写法），
   **不能当两个独立标志读**。
2. **`#19`（0~10 的计数形态）的"= 某个子字段的重复条数"假设被否**：把 blob 里 10 个 ld 字段的子字段计数
   全量算出来逐个对，最好的候选（子字段 `#1` 的条数）也只对上 **104/940** ⇒ 排除掉一整类解释，仍 **UNKNOWN**。
3. **记账方式**：§7 那张"取值域表"**本身就是资产** —— 每补一条（今天两条：一条命名、一条耦合）与
   **每排除一条**（`#19` 的两类），后人的搜索空间就小一块。别只在"解出来了"时动笔。

**Consequences：** `docs/CONTACT_DB_SCHEMA.md` 的 §7（`#11` 行补耦合、`#19` 行补排除项、"未验线头"改成"已验"）。

## D-082: `#13` = `biz_info.type`（第二个被命名的字段）；取值域也要量，别只量"有没有值"

**Status:** Active（补 D-073 / D-081）

1. **第二个命名的字段**：`contact.extra_buffer` 主体的 **`#13` = 该行 `biz_info.type`（账号服务类型）** ——
   **687/687 逐行相等、零反例**，五个取值（0/1/2/3/5）一个不差。它还**顺带解释了** §3 那条一直挂着的硬事实：
   `#13` 出现 ⟺ `verify_flag ≠ 0` ⟺ 在 `biz_info` 里 —— 因为 blob 里存的就是**服务类型的副本**，
   非 biz 行**根本没有这个值可写**（不是"没查到"，是"不写"）。
2. **取值域这次是全量补的**（此前文档多处只写"值域未测"，等于留白）：`#2` 三值、`#11` 四值、
   `#12`/`#17`/`#18`/`#22`/`#24` 是布尔/极稀有的小枚举、**`#10` 与 `#38` 是 `0 / -1` 两态哨兵**
   （`4294967295` 就是 -1 的无符号写法）、`#8` 的 23 个取值**形态像位掩码**、
   **`#19` 是 0~10 的 11 个值、像某个"计数"**（但已排除"菜单按钮数"）、`#37` 只用到 bit8/bit11、
   `#16`/`#23` 恒 0（显式序列化默认值）。⇒ **`#19` 是一个新的、形状明确的待解项**（"像计数"）。
3. **方法（值得单记）**：**"字段值域"和"字段有没有值"是两件事**。前几轮一直在量"出现与否"（`#13`/`#14`/`#27`/`#36`
   都是这么解的），而这次把 varint 的**取值分布**一次性铺开，立刻捞出 `#13`（可命名）与 `#19`（形状清楚）。
   ⇒ 遇到"结构清楚、名字不知道"的字段，**先把取值域量出来再谈命名**。
4. 留一个**未验**的线头（写在文档里，不当作结论）：`#10 = 0` 与 `#11 = 0` 的行数**都是 129**，疑似同一批行。

**Consequences：** `docs/CONTACT_DB_SCHEMA.md` 的 §0（`contact.extra_buffer` 行补"`#13` 已命名"）、
§7（新增整张 varint 取值域表 + `#13` 的解释 + 未验线头）、§9（新增复跑 20：`#13` 的逐行比对，
并强调"相等"要双向看）。

## D-081: 定名的第三条路 = 公开码表 + 一条不依赖该码表的预测；`#5` = 国家/地区代码

**Status:** Active（补 D-068 / D-080；这是 37 个字段里**第一个被命名**的）

1. **结论**：`contact.extra_buffer` 主体（`ContactExtData`）的 **`#5` = 国家/地区代码（ISO 3166-1 alpha-2）**。
   形态是"恰好 2 个大写 ASCII 字母"、717 行有值（另 223 行显式空串）、25 个去重值。三条证据：
   ① **25/25 全部落在 ISO 3166-1 的 249 个 alpha-2 码里** —— 随机 2 大写字母的命中率是 249/676 ≈ 0.37，
   全中概率 **≈ 1.4e-11**；② **众数就是 `CN`、占 94.0%** —— 这是对 ① 的**独立**确认；
   ③ 按 **ISO 639-1 语言码**比只有 **15/25**（语言码还是小写）⇒ 是**地区**不是**语言**。
2. **方法（这条值得单独记）**：**用外部码表当判据时，必须再给一条"不依赖该码表"的预测**。
   因为码表是**我硬编码**的（249 条，与官方数量一致，但可能有个别错），只靠它得到的"25/25 命中"会把
   **裁判自身的错**算成结论。②（众数应为 `CN`）就是为了挡这一层：它只依赖"本机账号多在中国"这个**本机事实**。
   ⇒ 至此，blob 字段"能定名"的路有三条：**(a) 跨表整值 join**（那 44 行的 `#1`/`#2`）、
   **(b) 自/他对照的内容刻画**（`#4`/`#9`）、**(c) 公开码表 membership + 不依赖码表的预测**（`#5`）。
3. **一个必须点名的陷阱**：`#5` 这个字段号在两张表里是**两回事** ——
   `chat_room.ext_buffer` 的顶层 `#5` 是"成员 status 位 11 的投影"（D-070），
   `contact.extra_buffer` 的 `#5` 才是地区码。**报"#5"时必须先说清是哪张表。**
4. **代码这边不用动**：D-068 的"只解已验证的那部分"针对的是 `chat_room` 那条链（`contact-schema`），
   而**没有任何代码读 `contact.extra_buffer` 的字段** ⇒ 这次命名只进文档，不进实现。

**Consequences：** `docs/CONTACT_DB_SCHEMA.md` 的 §0（`contact.extra_buffer` 行补"`#5` 已命名"）、
§7（`#5` 那条从"❓ UNKNOWN"改成带三条证据的结论；候选名表里那行标注为"已是结论"）、
§9（新增复跑 19：**码表 + 不依赖码表的预测**）。

## D-080: `#4`/`#9` 是"该账号自身资料文本的汇集"；短值/子串命中必须配对照

**Status:** Active（补 D-068 / D-076 / D-079；这是"同对象"纪律第一次**产出正面结果**）

1. **正面结果**：把 `biz_info` 的 `external_info` + `brand_info` 递归展成叶子（16,294 条去重叶子），
   再看大 proto 的字段里含不含**同一行自己的**叶子。**本行 336 vs 换成下一行 7（48×）** ⇒ 不是撞车。
   分工：**`#9` ↔ 主体名/认证描述**（`RegisterSource.RegisterBody` 147、`VerifySource.Description` 67）、
   **`#4` ↔ 菜单按钮名**（`MMBizMenu.button_list[].name` 31 + 子按钮名 21），两者都还含商标名/认证人名。
   ⇒ **`#4` / `#9` 定性为"该账号自身资料文本的汇集"**（像检索用文本 / 资料摘要）。**这不是名字**，
   具体的组织形式（拼接 / JSON / 键值堆）**未测**，边界写清楚：包含 ≠ 相等。
2. **方法（三次误报换来的）**：**子串命中必须配对照**。"本行 vs 换一行"这一个对照就能把撞车筛掉 ——
   本机 `#5`（2 字节）那 22 条"含菜单值"在对照下**全部消失**；只有 ≥8 字节的叶子才立得住。
   这与 D-079 的"同对象判据"（防**假阳性**）和 D-077 的"地址口径"（防**假阴性**）是同一族的纪律：
   **任何"命中"都要能说出一个"不该命中却可能命中"的对照情形**。
3. 顺带把 `#9` 的"半命名"数字收紧：整值相等 **214** 行（148 归 `RegisterBody`、67 归 `Description`），
   而 `#4` 整值相等只有 **1** —— 这个不对称说明 `#9` 确实承载主体名，但也再次说明**它只是"有时"**。

**Consequences：** `docs/CONTACT_DB_SCHEMA.md` 的 §0（`contact.extra_buffer` 行补"`#4`/`#9` 已定性"）、
§7（`#9` 那条改写为"资料文本汇集"+ 自/他对照 + `#5` 的撞车教训）、§9（新增复跑 18：**必用自/他对照 + 长度门槛**）。

## D-079: `#3`/`#4` 被三条独立否证围死；而"搜第二份"必须用"同对象"严判据

**Status:** Active（补 D-071 / D-078；含一条**假阳性**教训）

1. **收口**：`#3`/`#4` 这对每房计数器现在有**三条独立否证**，方向各不相同 ——
   ① **不是服务端 seq**（D-071：42/42 房的值低于本房最小非零 `server_seq`，差 8.7e8；
   而且 `server_seq ∈ [1e4, 8e8)` 在全库 84,279 行里**一条都没有**）；② **不是成员变更事件**（D-078）；
   ③ **本地没有第二份**（本轮：24 个库、207,156 行按"同对象"判据搜 `#3` 与其 offset，**0 条有意义命中**）。
   ⇒ "一步是什么"仍是 **UNKNOWN**，但现在**本地已无路可走**，只剩**机外对照源**（另一台机器 / 另一个版本 / 一个更早的库）。
2. **本轮的方法规则（会反复用到）**：判"某个值在别处有没有第二份"，**必须限定"同一对象"** ——
   先找该行的**键列**（`username`/`room_id`/`chat_name`）命中目标对象，**再看这一行的其它列**。
   否则会得到"命中一堆、全是撞车"。本机实例：第一版我拿"候选整数 `IN (...)`"全库扫 → **279 条命中**，
   逐条看**全部是小整数撞车**（0/1/2/10 这些值到处都是，而 `#3 = 0` 的房本来就在贡献 0 这个候选）；
   加上"同对象"这一层筛子后剩 46 条，其中**有意义的仍是 0 条**。
   ⇒ **报"命中"之前先问"撞车的概率有多大"**（这是 D-076 那条"报命中率要问没命中的是什么"的**镜像版**）。
3. **报"0 命中"要配三样东西**（这次的格式固定下来）：**正对照**（同一次扫描里有没有本该命中的东西命中了 ——
   本机是 `#3 = 0` 的房命中 0 列、`session.last_msg_type = 10000` 命中整数列）、**跳过了什么**
   （本机跳过 735 张 `Msg_*` 表 + 用哈希作键的表）、**范围有多大**（24 个库 / 20.7 万行）。
4. **一个巧合，写下来防止被当成对应关系**：`session.SessionTable.last_msg_type == 10000` 与**底数 10000** 相同，
   而 `10000` **同时也是成员变更系统消息的类型码**（D-078）。三个无关的 10000。

**Consequences：** `docs/CONTACT_DB_SCHEMA.md` 的 §0（`#3`/`#4` 那行改为"三条独立否证"）、§6.1（新增 ⑩：范围、
0 命中、46 条撞车、正对照、范围限制、与 ⑦⑨ 合并成三条否证）、§9（新增复跑 17：**必须用同对象判据**、
别用 `IN` 全库扫）。

## D-078: `#3` 的"一步"不是成员变更事件；而"需要第二时点"这个措辞本身太窄了

**Status:** Active（补 D-071 / D-072；含一处**自我收窄**）

1. **一件事之前被记错了性质**：`#3`/`#4` 的"一步是什么"一直在文档里挂着"**需要第二时点**"。
   但**库里自己就有一份带时间戳的事件日志** —— 群消息里的成员变更通知（基础 `local_type == 10000`，全库 1,764 条）。
   ⇒ 少了的那步不是"再拿一个快照"，而是"**先问库里有没有自带事件序列**"。凡"要外部对照源"的结论，**都该先过一遍这道筛**。
2. **这条路的结论是"否"，而且是硬否**（三种候选读法全部否掉，n = 71）：`off = #3 − 底数` 与
   **事件条数**（秩相关 0.168、差 0 的房 3/71）、**加入人数**（0.143、4/71）、**加入−退出**（0.144、4/71）都对不上；
   只有与**当前成员数**（0.911，既有结论）对得上。
3. **关键是我复核过的那个反例**：5 个 **2026-08 之后才建**的房，整段历史都在保留窗口内 ——
   其中 `chat_room.id = 3906`（2026-08-31 → 2026-09-28、36 人）**全部**类型 10000 的消息只有 **48** 条
   （子 Agent 按"成员变更"归类后 9 条），而 `off = 104`。**用最大的分母也不够** ⇒ "一步 = 进一个人 / 净变化"
   **不是被小样本或窗口截断蒙蔽的假否证**。差额跟**成员数**同向（0.897）、跟窗口天数只 0.363。
4. **"净变化"那条读法其实从数据侧就不成立**：主动退群通知 `left` = **0 / 1,622**（微信不给普通成员发退群通知）、
   `removed` 全库 1 条 ⇒ "减"的那一半本来就看不见。**下"两边同权"的判据前，先确认两边的数据都存在。**
5. **两处独立复核对上的**：`off = 0` 的房**恰好 7 个**（2024/2887/3348/3601/3668/3729/4025，与 ⑧ 的"成员表没动过"吻合），
   子 Agent 与我的两套数一致；`#3 = 0` 的 3 个房里 **2007 有一条事件（群主解散群聊）却仍为 0** ⇒ 未初始化的房不随事件走。
6. **一处口径差如实记下**：子 Agent 的每房"事件条数"是**按成员变更归类后**的子集，我报的是**全类型 10000** 的条数
   （全库一致：1,764 = 它的 1,622 + 对不上房间的 142）。两套都对 —— 写进文档是为了**别让后人以为两边矛盾**。

**Consequences：** `docs/CONTACT_DB_SCHEMA.md` 的 §0（那行从"需要第二时点"改写成"事件日志这条路已试并否 + 剩余唯一办法"）、
§6.1（新增 ⑨：三条否证、决定性反例、`left`=0、7 个 `off`=0 房、口径差的调和）、§9（新增复跑 16）。
"一步"的语义仍是 **UNKNOWN**，但这次是**有否证的 UNKNOWN**。

## D-077: blob 字段名的"读二进制"这条路封口了；根因是一条地址口径；负结论必须先确认口径

**Status:** Active（补 D-068 / D-071 / D-076；这是一条**封路**的决定，目的是别让后人重跑）

2026-10-06 试了"用已知字段号的消息去标定客户端那份明文名字组，再给 `ContactExtData` 的 37 个字段对号"这条路。**断在第 1 步，而且是结构性的**（我的复核数与子 Agent 一致）：

1. **没有"每个 message 一份字段名表"这种东西。** `micromsg.ContactExtData` / `ChatroomExtData` /
   `ChatroomMemberLocalData` / `WeclawExternalInfo` 的**全文件 4 字节引用数都是 0**（只有 `ChatroomDetailInfoExtData`
   有 2 处）；类型名那一片只有同族类型名 + `local_proto\local_contact.pb.cc` 编译路径；类型名后的 `(VA,1)` 指针表
   指向的要么是 96 字节密文块、要么是代码。⇒ **"类型名 → 字段表"这一层没有可静态恢复的形式。**
2. **全文件穷举也没有我们要的组**：43,411 个"对齐 + NUL 结尾"名字槽 → 1,729 个像字段名的连续串，
   **没有 4 个成员字段的组、也没有 ~37 个 contact 字段名的组**。
3. **硬负结果：连续挨着 ≠ 同一组**。`Announcement|InfoVersion|AnnouncementEditor|AnnouncementPublishTime|ChatRoomStatus`
   这 5 个名字挨在一起，但后两者**全文件引用各为 0** ⇒ 组的边界由描述符记录的槽决定，**不能按相邻性划**。
4. **地址口径（本轮最有复用价值的一条）**：本版 `Weixin.dll` 是 **64 位**（imagebase `0x180000000`）、
   **每节 `VA ≠ PRAW`**，而**描述符表里存的是"文件偏移"**：按文件偏移搜 4 字节能命中（`InfoVersion` 4 处），
   按**真 RVA 或 VA 搜全是 0**。⇒ 文档 2026-10-04 那次"全文件搜这些名字的指针引用都是 0"**就是这条口径错造成的假阴性**，
   当时的解释（"file-offset ↔ RVA 错位"）方向对但没说到点上；现在说法固定为：**表存文件偏移，用 RVA 搜必得 0**。
   **规则**：任何"0 命中/不被引用"的负结论，**先确认自己用的是哪套地址口径**，并跑一条**正对照**
   （同一个查询换一个已知该命中的对象 —— 本机就是 `InfoVersion` 的 4 处）。
5. **半条正面结果（但不足以支撑命名）**：客户端里那种 snake_case 名字数组**在 SQL 列这一层是保序的**
   （`chat_room_info_detail` **8/8**、`contact` 连续 **18/18** 与 `PRAGMA table_info` 逐项相等）——
   但 `biz_info` 的数组与列序**不匹配**（`sync_version` 在表里是最后一列、在数组里排第 2），
   同一片 `.rdata` 里"保序列名数组"和"不保序的别的数组"外形无法区分 ⇒ **不许**拿"名字在第几位"去推 proto 字段号。
   **D-071 的撤回维持**（唯一能测的 proto 组只有"缺项消隙"一种读法能复现两个锚点，却要求一个不可信的巧合）。
6. **交付里有三个候选名（`#41`→`UpdateTime`、`#43`→共享 id、`#5`→地区/语言码），全部标注"纯推测"并各带证伪办法，
   不进代码。** `#2`…`#38` 继续只报字段号 —— 与 D-068 一致。
7. **一处未消的 1 之差**：`AnnouncementPublishTime` 的引用数子 Agent 报 1、我量 0（其余名字两边一致）。
   不影响结论（1 与 0 都远低于"被正常引用"的量级），但要如实留在文档里，别让它假装一致。

**Consequences：** `docs/CONTACT_DB_SCHEMA.md` 的 §0（"名字"那行改写为三条路封口 + 唯一可用机制是跨表 join）、
§3.3（地址口径写进正文；"4 字节 RVA"改称文件偏移；新增"封口"四条）、§7（新增三个标注"纯推测"的候选名 + 证伪办法）、
§9（新增复跑 15：二进制搜索**必须先确认地址口径**）。

## D-076: `verify_flag` 解出来了（它根本不是"好友验证"）；`#9` 的命名要降级；两处"看着对其实错"的脚本坑

**Status:** Active（补 D-073 / D-075；含一次"对子 Agent 结论的降级"）

2026-10-06 同一轮里另外三条，**每条都经我独立复算**：

1. **`contact.verify_flag` 解出来了 —— 它跟"加好友/待验证"没关系。** 它是**位掩码**、9 种取值
   （此前清单漏了 3 个单例 `56/264/1053`）。逐位（全部我的数）：
   - **`bit3`（值 8）⟺ 该行 `username` 在 `biz_info` 里** —— **687/687 双向、两个错格都是 0**；
     而且所有非零值都带 bit3 ⇒ "8" 就是"非零"本身。
   - **`bit4`（16）⟺ 有 `VerifySource`**（机构认证）：265/265，反向 2 例（0.75%）；
     **`bit8`（256）⟺ 有 `PersonVerifyInfo.VerifyDesc`**：12/12；**`bit9`（512）⟹ 有 `PersonVerifyInfo`**：65/65，反向 7 例；
     **`bit4 ⊥ bit9`（0 行）** ⇒ 机构认证与个人认证互斥。
   - **一条副产品**：`contact.extra_buffer` 出现 `#13` ⟺ `verify_flag ≠ 0` ⟺ 在 `biz_info` 里 —— **三条是同一批 687 行的三面**，
     且 `verify_flag ≠ 0` 的行**没有一个是非大 proto**。此前那条"`verify_flag != 0` ⟹ 不在任何群"由此有了原因
     （都是 biz/系统号）。⇒ **"位 = 好友关系"整类读法判死**（能证伪的计数：`≠0 且不在 biz_info = 0`、
     `≠0 且有 remark = 0`、`≠0 且是群成员 = 0`、2946 个 `wxid_` 里 2941 个为 0、`room_verify_application` 是空表）。
2. **`chat_room_info_detail.ext_buffer_` 的 `#1` 翻案：不是"一坨二进制"，是两层嵌套 message**
   （`DetailList → Entry`，`Entry` 字段号 {1..7} 8/8）。72/77 空、5 行非空；`Entry.#2` 是成员 `username`
   （8/8 命中 `contact`，其中 **7 个正是该房 `owner`**）、`#5` 是合法 UTF-8 自由文本。⇒ 文档里"少数是一大块二进制"作废。
   顺带把三张 `openim_*` 的 `ext_buffer` 分清：前两张是「`#1` + 若干 `{key, value}` 对」，
   `acct_type.ext_buffer#1` **逐字节 == 该行 `acc_type_id`**，而 **`openim_wording.ext_buffer` 21/21 全 0 字节**
   （"3~4 个字段"那格把三张表并一起了，错）。
3. **对子 Agent 的"已命名"结论做降级（这条是流程要求，不是结论）。** 一个 Agent 报「`#9` 已命名 = 主体名」
   （213/567 逐字节等于该行 `RegisterSource.RegisterBody`）。复算后：这 213 是**真的**、且 `#4` 只有 **1/673**（不对称 ⇒ 非撞车），
   但另有 **21 行只是把主体名当子串含住**、**333 行与主体名无关** ⇒ 合计 **234/567 = 41%**。
   ⇒ 只能写"**`#9` 有时含认证主体名**"，**不能写"`#9` = 主体名"**（58% 的行会给出别的东西）。
   **规则**：子 Agent 报"命中率 p"时，母 Agent 要问 p 的另一面 —— **不命中的那些是什么**；
   只有"另一个字段几乎不命中"这种**不对称**才能支撑"命名"，单看一个比例不能。
4. **两条"看着对其实是错"的脚本坑（我这轮自己踩的两个 + 子 Agent 报的两个）**：
   - **bytes vs str 混比 → 假 0 命中**：blob 里取出的值是 `bytes`，从表里读回来的可能是 `str`，
     `set` 比较直接给 0 命中 —— 而"0 命中"看起来正好像一个**负结论**。我因此把一条已经验过的关系（`wording_id` 全覆盖）
     判成"0 命中"，两者输出长得一样。**修法**：比之前统一成 `bytes`（`text_factory = bytes`），并给"0 命中"配一条**正对照**。
   - **同一个键被两个循环各加一次 → 恰好 2×**：`VerifyDescribe` 我量到 128、真实是 64。**恰好两倍**是这类错的指纹。
   - 子 Agent 报的两条同族：`D:\tmp_eyetrack\numbers.py` **遮蔽标准库 `numbers`**（会让 `import statistics` 崩在 `_decimal`，
     且顶层 `print` 会混进别的分析的输出里）；**宽 `except Exception` 包 `json.loads` 会吞掉 `NameError: json 未导入`**，
     把"代码没跑起来"打成"所有键都为空"的**业务事实**，而所有计数照样出得来。

**Consequences：** `docs/CONTACT_DB_SCHEMA.md` 的 §0（`contact` 列 / `detail #1` / `openim_*` 三行升级）、
§1.1（`external_info` 其余键全量清点；`contact.description` 改为"**全表** 4014 行全空"；`VerifyDescribe`/`VerifyIdentity`
的 68 串位更正）、§3（那行加 `#9` 与 openim 形状）、§6（`verify_flag` 整块重写 + 三面合一）、§7（`#9` 半命名 + 子串门槛）、
§9（新增复跑 13 / 14）同步。

## D-075: `contact.extra_buffer` 是「一列两型」；字段名不止二进制一条路；行为足迹打位域基本落空

**Status:** Active（补 D-068 / D-074；含两条口径更正）

2026-10-06 三个子 Agent 从互不重叠的方向推，**三条头条我都用独立脚本量过**（这是本条的流程要求）：

1. **`contact.extra_buffer` 是「一列两型」，按 `local_type` 分。** 主体 940 行是 `ContactExtData` 骨架；
   另 **44 行是 OpenIM 专属**：`#1` = `openim_appid.app_id`、`#2` = `openim_wording.wording_id`
   （各 **43/44** 逐字节命中）、`#4.#2` 的 JSON 键**恒为 `custom_info`**（43/43）；承载行 42/44 的 username
   以 `@openim` 结尾、`local_type ∈ {5,6}`，且**全库 `local_type ∈ {5,6}` 的 42 行恰好全在这族**（双向）。
   ⇒ **更正**：文档里的"46 行小形状"按字段号看真值是 **44 行**（另 2 行是字段号 `#10`/`#40` 的大 proto 残行）；
   并且 §3.1 把 `stranger.extra_buffer` 挂在 `OpenIMContactExtData` 上**挂错了**（它与 `contact` 主体同型 ⇒ `ContactExtData`），
   真正的 OpenIM blob 是那 44 行。
2. **"跨表外键"是解 blob 字段名的一条独立于二进制的路。** 那 44 行的 `#1`/`#2` 之所以能定含义，靠的是
   **在别的表里逐字节命中**（`openim_appid` / `openim_wording`），不是读二进制、也不是猜。此前把
   "字段名拿不到"记成 ❌（D-068）时，**没有把"表间比对"这条算进去** —— 现在它有了一个成功案例。
   ⇒ 以后遇到有值可查的 blob 字段，先做**跨表 join**，再谈"名字拿不到"。
3. **行为足迹（该成员在这个群里发过言吗）打成员 `status`，基本落空。** 唯一稳的是 **bit13**：
   房内载体**系统性更沉默**（P(sent) **0.087 vs 0.208**、房内 MH OR **0.40**、19/20 房同向）——
   所以 D-074 里"bit13 成员级没找到"**作废**。同时被削的：**bit4 ↔ 有群昵称** raw OR 6.19 → **MH 2.05**
   （≈2/3 是"昵称房"混杂，且 **bit3 房内一样强**）、**"bit4 偏老成员"房内不成立**（0.353 vs 0.333, p=0.42）、
   **bit3 的"更活跃"是房间级**（房内符号 15/13/11、均值差 −0.002）、**G 的"更爱发言"全来自一个房**
   （10 个发过言的 G 载体 8 个在同一房）。⇒ **"某位 = 某种人"整类读法在本轮没有被任何一条支持住。**
4. **两条"方法别踩"**：
   - **两个看似独立的代理可能同一个**：`chatroom_member.rowid` 分位与 blob 成员位置分位 **76/77 房完全同序**
     ⇒ 此前把它们并列成两条证据是**重复计数**。
   - **小样本的取值窗口会骗人**：`#41` 的年份，一个 Agent 从部分样本得"2021–2024"，我量完整分布是
     **2019–2026、70% 在 2026**（691 行里还有 127 行显式写 0）⇒ 结论从"老数据"翻成"最近更新时间"。
     凡"取值范围"类断言，**先报分母与全量分布**。
5. **一处不裁决**：`bit0` 的性质（"该房的行状态" vs "人的属性 93% 一致率"）在上一轮报告里**自相矛盾**，
   本轮的行为足迹**帮不上**（缺位者 74/100 在无消息表的房里 ⇒ 量不到）⇒ **如实记 UNKNOWN，不硬裁**。

**Consequences：** `docs/CONTACT_DB_SCHEMA.md` 的 §0（状态表：`contact.extra_buffer` 升级为"一列两型"、
`openim_appid`/`openim_wording` 升 ◐、`openim_acct_type` 的 `ext_buffer` 仍 ❓）、§3（那行重写）、
§3.1（`stranger` 的类型名更正）、§6（bit13 补脚印 + 三条削档）、§7（新增"逐字段取值轮廓"块）同步。
`#3`/`#4` 仍只报字段号。

## D-074: `status` 是"字段"不是"一堆标志"；`contact.extra_buffer` 主体是 37 字段 proto（含两处数字更正与一条"更正之更正"）

**Status:** Active（补 D-068 / D-073；本轮的价值一半在"错了什么、数从哪来"）

2026-10-05 又一轮多 Agent + 复核，五件事：

1. **成员 `status` 的取值语法 = 5 个不重叠区间**：`status = bit0 | A<<3 | bit11 | bit13 | G<<20`。
   **A（bits3–4）是 2-bit 字段**（bit3&bit4 同现 **46**、独立期望 19.7 ⇒ 2.3×，不是两个 flag），最强倾向是
   "有群昵称"（A=0→3 单调 0.26→0.78），**但 31% 载体没昵称、逐房集合相等 0/33 ⇒ 只到"强倾向"，不是定义**。
   **G（bits20–22）是"有强制位的 3-bit 组"**：取值只出现 {2,3,6,7} ⇒ **G≠0 时 bit21 恒置位**
   （bit20&bit22 同现 **18**、独立期望 0.12 ⇒ ~150×）。**两条硬排斥我逐位复核过（各 0/4151）**：
   `bit4 ⊥ bit13`、`bit11 ⊥ {13,20,21,22}`。⚠️ `bit4 ⊥ bit13` **不是**"同一字段的两个取值"——
   两者外延**相反**（bit4 ↔ 多有昵称、偏老房；bit13 ↔ 少昵称、偏 openim / 大房）。
   ⚠️ 并更正一处错值：本节此前写 bits20–22"取值范围只有 `1/3/5/7`"，那是**当独立标志读出的错值**
   （`1` = 只 bit20、`5` = bit20+bit22 本机**不存在**）。
2. **`contact.extra_buffer` 的形状被记歪了，已更正**：初版写"`#3`(varint)，2 字节上下"——那是**46 行小形状**的样子；
   **主体 940 行是一张 37 字段（`#2`…`#38`）、长度中位约 115 字节的 proto**。旧的两个判据现在对得上：
   **984 行有顶层 `#3`**，其中 **942 行是 varint**（940 大 + 2 小）、42 行 ld、2 行无 ⇒ 旧文的"942 / 44"**就是**
   「`#3` 是不是 varint」，与形状切法（940 / 46）差的正是那 2 行小形状。**别再把小形状当整列的样子。**
3. **"骨架同型"是这轮的真收获**：`contact.extra_buffer` 主体那 940 行与 `stranger.extra_buffer`（本机仅 1 行）
   **逐字段号 + 逐线格式完全一致**（我复核）⇒ 两者是**同一张 proto**（contact 主体另多 `#41` / `#43`），
   于是 §7 那张"只有 1 行样本"的形状目录**可以依 940 行给取值范围**。顺带两条新事实：`#41` 是 **epoch 秒**
   （691 行、样本 2021–2024，而 22 列里**没有任何时间列**）、`#43` 是跨行高度重复的 22 字节 id 样值（628 行、仅 11 种）。
4. **"值对齐"给不出名字（判负，独立复核）**：对**全部 986 行**、每个字段、6 种编码（raw/utf8/md5/sha1/b64/utf-16le）
   与 22 列逐一比，**只 2 处稀疏命中**（`nick_name` == `#4` 8/716、== `#9` 12/953），哈希 / URL / wxid 级 **0 命中**
   ⇒ 判为**偶发撞车，不据以命名**。⇒ blob 的字段名**本机 + 公开渠道两头仍拿不到**（D-068 边界不变）。
5. **一条"更正之更正"**：初版 §1.1 写"33 个号**有消息表**却不在 `biz_info`"，我先前复核成"两处都复现不出"——
   现在定出**那个 33 是真数，但来源不同**：它是**订阅号库自己的 `Name2Id`（573 行）里 33 个 `gh_` 不在 `biz_info`**，
   而这 33 个**没有消息表、也不在 contact-db 的 `name2id`** ⇒ 准确说法是"**被订阅号库登记过名字、但既无会话也无消息**"。
   ⇒ **"复现不出"要先分清"数不存在"还是"我把条件记错了"**（这次是后者 —— 别急着把数一起撤掉）。
   同类还把缺口 B（10 个 `gh_` 在 biz / name2id 而 `contact` 里没有）的读法改了：**8/10 `sync_version` 为空、9/10 无消息表**
   ⇒ 数据**更支持"只被登记过、从未真正关注"**，"取关残留"只是相容的另一读法，**本机判不了**。

**Consequences：** `docs/CONTACT_DB_SCHEMA.md` 的 §1.1（两个缺口）、§3（`contact.extra_buffer` 行重写、
`stranger` 行 **38 → 37** 并注明同型）、§6（`status` 字段表 + 两条硬排斥 + 错值更正）、§7（取值目录改依 940 行）、
§9（新增复跑 11 / 12）同步。`#3` / `#4` 仍只报字段号。

## D-073: 位域要逐位做、`biz_info` 已测绘、以及"恒/从不"类断言必须先核分母

**Status:** Active（补 D-068 的字段边界；含一条方法更正）

2026-10-05 继续把 `contact.db` 剩下的部分做完，三件事值得记：

1. **位域必须逐位做列联，别拿整值做。** 早前对 `contact.extra_buffer` 的 `#3` 是拿**整值**
   （0/3/9/1/515）去跟 21 列做列联表，于是写下"取值本身无从判定"——**那是方法错**：位域里两个位会互相抵消。
   逐位重做后立刻有了两条：`bit0 ⟺ 值 ≠ 0`（恒等）、**`bit1 = 1` 的 423 行全部是 `local_type = 1`
   且不在任何群里**（423/423 单向干净）。同一次也把房间级 `chat_room_status_` 的**位 19 解出来了**：
   **⟺「该房已初始化（`status ≠ 0`）且与 openim 不互通」77/77**（我独立复核过）——它此前被记成"只是众数"。
   ⇒ 以后遇到 ❓ 的位域字段，**先逐位**，再谈"取值判不了"。
2. **`biz_info`（公众号表）已首次测绘**，并明确它对日报线的增量与隐私边界（详见
   `docs/CONTACT_DB_SCHEMA.md` §1.1）：代码**从来没读过**它，而里面有 `RegisterSource.RegisterBody`
   （主体名称，93.1%）、`VerifySource.*`（认证类型）、`ServiceType`、`brand_icon_url`（头像直链）等；
   同时 `sync_version` 列**声明 TEXT 实为二进制 blob**（`SELECT *` 会崩，要 `text_factory=bytes`），
   且 `PersonVerifyInfo.*` 含**认证人真名**、`ServicePhone`、经纬度 ⇒ **只取需要的字段，别整包带出**。
3. **子 Agent 报的"全表恒 X / 从来没有 Y"，母 Agent 必须先核分母。** 本轮一个子 Agent 报
   "`contact` 四列全表恒 0"，直接查库即否（只有 `delete_flag` 真全 0）。根因**不是查错列**，
   而是**统计时把"成员子集"当成了全表** —— 这类断言的错法恰恰是分母被悄悄换掉，而它的下游结论看着毫无破绽。
   把它唤醒复核后，结论不变，还顺手多挖出一条真结论（`verify_flag != 0 ⟹ 不在任何群`）。
   ⇒ 复核要它**把当时的查询原样贴出来**，并要求**在交付文件里如实记下这次更正**。

**Consequences：** `docs/CONTACT_DB_SCHEMA.md` 的 §1.1（`biz_info`）、§3（`contact.extra_buffer` 那行
把空行数 **3072 更正为 3028**、并补上非空 986 的三态划分）、§6（逐位结论）同步；
`#3`/`#4` 仍只报字段号（D-068 边界不变）。

## D-072: `#3`/`#4` 的边界到此为止 —— 把"已经搜过的范围"写下来，别重搜

**Status:** Active（补 D-068 / D-071 的边界；这条的价值在**范围**，不在结论）

2026-10-04 用多智能体并行把"还能从哪拿到答案"逐条走完，结论是**本机这一侧已经无路**。
把范围记下来，免得后人（或下一轮的我）重搜一遍：

**语义（已收窄到能写进文档的程度）**：`#3 − 底数` 是"**每房、从底数起算、随该房成员表历史变更累计**"
的计数器。三条硬否定（不是服务端 seq / 不是唯一序号 / 不是"曾经出现过的人数"）+ 一条正面证据
（**offset=0 的 7 个房，其 `chatroom_member` 恰好只有一段连续 rowid、段长 = 成员数、且无成员变动系统消息
与公告** ⇒ 计数器初值=底数）。**分不开的**：每次 +1 对应"一次成员增删"还是"一次成员表更新"。

**已经搜过、别再搜的范围**：

- **二进制**：本机**四个版本目录**（`4.1.15.11` / `4.1.15.13` / `4.1.12.26` / `4.1.13.12`；其中
  `4.1.15.11\` 2026-10-11 实测**只剩 1 个文件**、不是能跑的版本 —— 见 `CONTACT_DB_SCHEMA.md` 那节）
  加其余腾讯系程序文件，共 **84 个二进制** —— 没有任何 `RoomData`/`RoomDataUser` 字段名组；
  三版 `Weixin.dll` 的明文名字组一字不差，我们那三个类型的注册块逐字相同。企业微信主程序已卸载。
- **公开圈**：拿类型名（`ChatroomExtData` / `ChatroomMemberLocalData` / `win_local_define`）做代码搜索
  —— 只命中本仓库或 0 命中。
- **数据**：全机只有一个真库；`Backup` / `msg\migrate` / `business\migrate` / `temp` / `config` 无副本或导出；
  企业微信只剩数据（自定义加密、无 `chat_room`/`ext_buffer`）；D 盘上一个名字像"WeChat 数据分析"的旧目录
  只是源码仓库，不含任何真实库。
- **时间维度**：`contact.db-wal` 的帧按边界截断（拿不到 `chat_room` 的改动）、`.material` 不是页快照、
  工作副本只差 35 分钟且窗口内无成员变更。

**要再往前只有一条路**：换一个**参照实例**（另一台机器的库 / 另一个微信版本，理想是 V3 时代、
或者能拿到同一房间两个时点的快照）。在那之前，`#3`/`#4` **继续只报字段号、不编名字**（D-068 的边界不变）。

**一条方法教训**：不要把 `name2id.rowid` 当"成员进群先后"的代理 —— 它是"此人进**我的联系人库**"的时刻，
控制规模后的偏相关 ≈ 0，会得出"只跟当前人数走"的**错结论**；要用 `chatroom_member.rowid`。
这条是本轮子 Agent 纠正我的，一并记下。

## D-071: `#3`/`#4` 是"本地每房计数器"，不是服务端 seq、不是唯一序号；`InfoVersion` 候选撤回

**Status:** Active（收窄 D-069，并撤掉它留下的名字候选）

2026-10-04 用多智能体并行复核（三个方向各自独立、互为对照），把这对字段又钉了两步、撤掉一个候选：

1. **它不是服务端 seq（硬）** —— 三条互相印证的证据：42/42 个 `10000` 族房满足
   `#3 < 该房自己 MIN(非零 server_seq)`（差值中位 **8.72e8**）；全库 **84,279 行**消息里
   `server_seq ∈ [1e4, 8e8)` 的有 **0 行**；2024~2026 年建的房仍是 `10000+x`，而这个账号的
   服务端 seq **2021-11 就已 8.41e8**（收藏侧 `update_seq`）。
2. **它不是唯一序号（硬）** —— `#3` **精确等于 `10000` 的房有 7 个**（另有其它重号）。
   ⇒ 与"**本地从常量 `10000` 起算、随本地事件递增**"一致；被证伪的是"服务端每房唯一版本号"。
   底数 `7×10^8` 也不是"某个计数器的历史读数"（收藏侧 seq 2021-11 已是 8.41e8，而公告 2024-05 的房
   底数仍是 `7×10^8`），更像**固定号段底**。
3. **撤回 `InfoVersion` 候选。** 客户端里确实有一组明文名字含 `InfoVersion`，且同组的
   `AnnouncementPublishTime` 与我们**已独立验证**的 `#6` 吻合 —— 但两条独立证据否掉它：
   (a) **名字顺序 ≠ 字段号**（读成 1..5 会让 `AnnouncementPublishTime` 落在 `#4`，实际在 `#6`）；
   (b) **行为对不上**（detail 的 `#3` 跟成员侧 ρ 0.889/0.773、跟公告侧 ≤0.55，控制成员后公告侧 ≈0；
   唯一"多一次变更"的对照——`#3 = #4+1` 的 5 个房——正好是成员 +1 的那 5 个房且**零公告**；
   另有 325 人房 `#3=9` 这种**双向反例**，说明它不是当前状态的函数而像事件累计计数）。

**Consequences：** 文档 §6.1 / §3.3 同步；`#3`/`#4` 仍然只报字段号、不编名字（D-068 的边界不变）。
另一条留给后人的方法教训：**看到"明文名字恰好和某字段对得上"时，先验证名字顺序能否对上字段号** ——
对不上就只能当线索，不能当名字（这条来自本轮把 `InfoVersion` 撤掉的过程）。

## D-070: `#5` 不是"附加参与者"；`chat_room_info_detail` 的配对语义不能搬到 `chat_room` 上

**Status:** Active（更正 D-067 / D-068 里的一处**事实错误**）

这两条是 2026-10-03 用多智能体并行复核时**先由子 Agent 提出、我再独立复核**的：

1. **`#5` 的真实身份**：`chat_room.ext_buffer` 顶层 `#5` 的 id 集合，与"该群成员里
   `status & 2048`（位 11）的那批人"**逐群完全相等（77/77）**；全库 4151 个成员里带 bit11 的正好
   28 个，与 `#5` 的 28 条目**一一对应**。⇒ `#5` 是成员列表的**投影**，**没有独立信息**。
   D-067 / D-068 里"`#5` 是附加参与者 id（库里的表没有这一串）"**是错的**。
   **后果**：`contact-schema` 的输出键由 `extraIds` 改为 `statusBit11Ids`，人读标签改成
   「`#5`（status 位 11 的成员）」。这是一次**对外可见的改名**，但留着旧名字等于在输出里
   继续断言一个已被证伪的含义。
2. **"配对差值 = 未落库"只对 `chat_room_info_detail` 成立**：`chat_room` 自己的 `#3`/`#4`
   **77/77 恒等**，在那 5 个"成员比关系表多 1 人"的群里**仍然恒等**。把 detail 的配对语义搬到
   chat_room 上，本机数据**不支持**；"三个存储落后程度不同"是**同样相容的另一种读法**，
   一次快照区分不了。文档 6.1 的 ①/② 据此改写。
3. 另外两条已验的位含义（都 77/77）：`chat_room_info_detail.chat_room_status_` 的**位 17 ⟺
   本群与企业微信/openim 互通**（置位 6 群全有 `@openim` 成员/群主，不置位 71 群全无）；
   `524288`（位 19）**只是众数**（51/77）**不是常量** —— 文档里"77 行全是 524288"一并更正。
4. **`chatroom_seq` 这条名字候选降级**：客户端字符串池里确实有它，但两轮独立追查都指向它是
   **账号级的"群列表游标"**（出现在 InitContact 的收尾日志里，日志里**没有任何房间标识**）。
   ⇒ 不采用，`#3`/`#4` 照旧不编名字。

**Reason：** 记"我们对外说过的话里哪些是错的"比只记新增发现更重要——`#5` 那句已经进了命令输出
和 npm 包的说明文字。

**Consequences：** `docs/CONTACT_DB_SCHEMA.md` §2/§5/§6/§6.1/3.2 与 `contact-schema` 的措辞同步；
`#3`/`#4` 仍然只报字段号（D-068 的边界不变）。

## D-069: `#3`/`#4` 定性到"一对版本号、跟着成员走"，但**仍然不给它名字**

**Status:** Active（补充 D-068 的"语义未定"）

2026-10-03 又追了一轮，把这对字段从"完全未定"推到**行为可描述**（细节与可复跑步骤见
`docs/CONTACT_DB_SCHEMA.md` 6.1）：

- 它是**同一个计数器的两份** —— 最硬的证据在结构相同的 `chat_room_info_detail.ext_buffer_` 里：
  同一对字段号在 5 行上 `#3` 比 `#4` 大 1；
- 那 5 个群**正好**是 `chat_room.ext_buffer` 的成员比 `chatroom_member` 多 1 个人的 5 个群
  （一次已到达、还没落到关系表的成员变更）—— 两个标志互为证据，不是巧合；
- 去掉底数后的大小与**成员数**秩相关 0.92 ~ 0.94，与消息数几乎无关（0.04 ~ 0.40）；
- 底数有两种（`10000` 47 个群 / `7×10^8` 24 个群），与记录进库的先后相关；`owner` 为空的
  3 个群值为 0；另有 3 个群是第三种来源（1254/1304/2021），样本不够；
- 它**会变**（同一天实测 +2），所以不是静态 id。
- **类型名有，字段名没有**：从客户端二进制里读到 `chat_room.ext_buffer` 其实是
  `micromsg.ChatroomExtData`、成员子消息是 `micromsg.ChatroomMemberLocalData`、
  `contact.extra_buffer` 是 `micromsg.ContactExtData`（`docs/CONTACT_DB_SCHEMA.md` 3.1）；
  但**字段名不以明文出现** —— 明文只到"表的列名"这一层（`nick_name / … / extra_buffer`），
  本地 message 的字段名是二进制的、不可读。**（更正：早前一版说"字段名被打包成 `<长度><hex>`
  记录"是错的 —— 那批记录是客户端的字符串池，见 3.2。）**
- **顺手破了客户端的一层字符串混淆**（3.2）：`0xa1 <小端 uint16 的 4 位 ASCII hex> 0xb1 <等长 hex>`
  这种记录，载荷是**明文 XOR 一条固定的逐位密钥**（判据：解出来每一位的可打印率 86%~100%，
  随机密钥只有 ~37%）。解出来是客户端的符号/文件名/日志串。**对本文的用处只有一条**：
  日志键里有 **`chatroom_seq`**（与 `contact_seq` 并排，在 `init_contact_manager.cc` 的流程里）——
  它跟 `#3`/`#4` 的行为对得上，但**没有对照，只能算名字候选**。

**没有改的一件事：名字。** 上面是**行为**证据，不是厂商给的字段名；本机也没有对照源
（没有 V3 数据、没有第二台机器、同一房间只有一个时点）。所以 `contact-schema` 继续把
`#3`/`#4` 放在 `unrecognized` 里、只报字段号 —— **D-068 的边界不变**，变的只是文档里
"能说到哪"的那一段，以及"blob 比关系表新"这条可以直接用的事实。

**Consequences：** 想给它们命名，得先拿到对照源（另一台机器 / 另一个微信版本 / 同一房间的
两个时点快照）。在那之前，任何"这是群号 / 成员数 / 时间戳"的说法都只算假设。

## D-068: 只解 `ext_buffer` 里**已验证**的字段，未定的原样标注

**Status:** Active（细化 D-067 的"解析代码不进"）

D-067 定的是"测绘进仓库、解析不进"。随后的问题是"解析完并纳入功能了吗" —— 于是把**已经验证过**
的那部分做成能力：`weflow-cli contact-schema`（`scripts/contact_schema.py`，只读本地）输出成员的
userName / displayName / status（位标志）/ inviter，加上 `#5`。（`#5` 当时被叫成"附加参与者 id"；
2026-10-03 更正：它等于成员 `status` 位 11 的投影，见 D-070。）

**边界写在输出里，不写在注释里。** 顶层 `#3`/`#4` 语义未定（三次证伪：不是成员 id、不是房间 id、
不在消息库任何 id 列；**2026-10-03 更新**：行为已定性到「一对跟着成员走的版本号」，见 D-069
与 `CONTACT_DB_SCHEMA.md` 6.1 —— 但**名字照旧不编**），所以它们进 `unrecognized`、键就是字段号，**不给它们编名字**；成员里出现
没见过的小字段同样照原样带出（`members[].unrecognized`）。编了名字，下游就会拿猜出来的含义写逻辑。

**解不动就报错，绝不返回空成员表。** "解不动"和"这个群没人"在数据上是相反的两件事，
静默把前者写成后者是最坏的一种错。`test/contact_schema_test.py` 钉住这两条 —— 其中"声明长度
超出缓冲区"那个用例是**变异检查补出来的**：第一版测试只用了"长度的 varint 被截断"，
压根走不到 `i + ln > len(buf)` 那条检查，把那个 raise 改成 `return []` 测试照样绿。

**Consequences：** 要素在 blob 里而库里的表没有的那两样（status / inviter）时，
用这个命令；其余字段与群昵称仍以 DLL 那条路为准。

**`#3` 的边界（2026-10-03 追到尽头）**：24 个大值 `#3` 在 `db_storage` 下**全部 24 个库**的每一列里
命中 0（大整数不会撞车），另加三条独立排除（不是本地行号、不是群号、不是消息表名哈希的输入 ——
后两条分别由厂商 DLL 的 SQL 与 139 张 `Msg_*` 表反推）。所以它只活在这个 blob 里。
这就是"不猜"的底气：不是懒得查，是查到了尽头。

**为什么"找对应列"这条路注定失败**：这类字段多半是**位标志**（群成员 `status` 实测 8 个位、
`contact.extra_buffer` 的 `#3` 实测 4 个位）—— 位标志的集合不会与任何单一列镜像。
先定性到"它是位标志"再谈别的，比拿列联表硬找有意义。另外**本机没有 V3 的数据**
（`WeChat Files` 与注册表 InstallPath 都为空），所以"拿另一个版本对照"这条在这里走不通。

**`#5` 的更正（2026-10-03）**：本文（以及 D-067 引用的那段）把顶层 `#5` 叫"附加参与者 id"是**错的** ——
实测它等于"成员里 `status` 位 11 置位的那批 id"（77/77 集合相等）。见 **D-070**。密钥只从配置读、**不做成命令行参数**
（进程列表可见，同 EXTENDING 的 "state goes in over stdin, not argv"）。

## D-067: `ext_buffer` 的结构已测绘，但**不**在仓库里解析它

**Status:** Active

`contact.db` 里那几列二进制（`chat_room.ext_buffer`、`chat_room_info_detail.ext_buffer_`、
`contact.extra_buffer`、`stranger.extra_buffer`、`openim_*`）没有官方文档。2026-10-03 把
`chat_room.ext_buffer` 解出来了：它是 protobuf，成员段与上游
[chatlog-export](https://github.com/Wing900/chatlog-export) 的 `roomdata.proto` **一致**，
但那份定义**不全也不准**（它写的 `int32 roomCap = 5` 实测是长度分隔的字符串列表；顶层还多
`#3`/`#4`/`#6`）。整库 17 张表逐列测绘记在 `docs/CONTACT_DB_SCHEMA.md`。

**决定：测绘进仓库，解析代码不进。** 群昵称现在走原生 DLL
（`wcdbCore.ts` 的 `wcdb_get_chat_room_ext_buffer`）已经够用；而 blob 里最诱人的那个 `#3`
（每群一个数、与 `#4` 恒相等）**语义没定** —— 实测排除了"成员 id / 房间 id / 消息侧 id"
三种解释，剩下最像"V4 新 id 空间里的某个会话标识"，但本机数据不足以判定。

**Consequences：** 拿一个语义未定的字段写逻辑，等于把猜测当事实。真需要 blob 里那三样
（成员的 `status` 位标志、`inviter`、`#5` —— 库里的表都没有）时，回来读那份
笔记，先把 `#3` 定死或明确只用已验字段。笔记里也记了**证伪过的猜测**，省得后人再试一遍。

## D-066: The tickle no longer tilts - rotation belongs to the lift alone

**Status:** Active (supersedes the computed-tilt half of D-065)

The tickle used to do two things at once: cycle four generated poses, and rotate the ball by up to 4 degrees
computed from the pointer's speed, leaning away from the direction you swept. The rotation is **gone** as of
2026-10-02; the four poses stay.

**Why:** the tilt and the lift share one visual channel - both are a `rotate` on `#ball` - so a pointer merely
crossing the ball produced a small version of the take-off. The user read it exactly that way: "why does it sway
when I move the mouse across it, instead of only swaying in the lift state". Two features competing for one
transform is also why the older code had to keep them mutually exclusive in JS (`canTickle()` excludes
`ball-lift`, and a press settles the tickle first).

**Consequences:** `--tickle-deg` is no longer written and its CSS rule is gone, so `body.ball-tickle` now only
carries the pose frames and the hidden iris layer. The rest of the behaviour is unchanged: a pose every 110 ms,
settled 260 ms after movement stops, skipped under `prefers-reduced-motion`, while peek-hidden, and while the
ball is lifted. Two guards keep it from creeping back: `test/panel-tickle.test.ts` asserts the tickle writes
**no** angle at all, and `test/panel-lift-frames.test.ts` asserts that no `body.ball-tickle #ball` rule carries a
`transform`. **Not yet verified live** - as with D-065, nobody has watched it on a real window.

## D-065: The tickle is a computed tilt plus four poses, and it rotates about the centre because that is what the circular clip allows

**Status:** Superseded by D-066 (the computed tilt was removed on 2026-10-02; the four poses stay)

Running the pointer across the floating ball makes it squirm. Two things are decided here.

**The motion is computed, the poses are drawn.** Four generated poses (`mascot-tickle-1..4.png`) carry the
expression and the paw detail, but the **tilt is computed in the renderer** from the pointer's horizontal
direction and its speed (maximum 4 degrees at 900 px/s, settling 260 ms after movement stops). The split is
forced: a fixed set of frames can only replay one amplitude, and the whole point of a tickle is that it answers
how much you are moving. The direction is inverted on purpose - sweeping right makes it lean left, the way being
pushed would.

**Rotation, not translation, and about the centre.** The ball is clipped to a circle, and its art already uses
125.2 of the available 125.44 radius (the remaining 2% is reserved for the hover `scale(1.02)`). Rotating about
the centre preserves every pixel's distance from that centre, so no pixel can be pushed past the clip - a
translation of the same visual magnitude would immediately clip the ears. `transform-origin` defaults to the
centre, so this holds without extra CSS, and **that default is now load-bearing**: changing it would silently
start cutting the ball. The four poses stay at scale 1.0 so the ball does not change size while being tickled.

**Consequences:** the tickle is skipped under `prefers-reduced-motion` and while the ball is peek-hidden (that
artwork has a straight cut edge, which a rotation turns into a visible seam), and a press settles it first so the
lift owns the ball. The IRIS layer is hidden while a tickle pose is showing, for the same reason as the other
painted-eye faces. No new IPC. **Not yet verified live** - the geometry, the wiring and the settle-on-stop
behaviour are covered by `test/panel-lift-frames.test.ts` and `test/panel-tickle.test.ts`, but nobody has watched
it on a real window yet.
## D-064: The ball's lift animation is generated art, and the contract that keeps its frames registered is now executable

**Status:** Active

Pressing the floating ball plays a twelve-frame sequence - six frames up while it is held, six back down on release -
and the twelve new frames are produced by the project's image-generation model rather than drawn by hand. Four things are
decided here.

**The frames may only express the lift through pose.** The ball is `border-radius: 50%` and the art is normalized to the
inscribed circle, with the remaining 2% of the radius reserved for the hover `scale(1.02)`. A frame that simply drew the
cat higher would have its ears clipped by that circle, so the lift is carried by the pose (paws hanging, ears pressed
back, eyes wide) plus a per-frame shrink to 0.975 / 0.945 / 0.920 of the resting cat width. Relaxing the clip, growing
the window during the gesture, and adding a shadow or a halo were each rejected: the shadow and the halo were removed at
the user's request on 2026-09-29 and may not come back.

**The normalization contract is executable now, not folklore.** Its parameters previously lived only in a comment in
`panel.css`, which is why the same quantity was measured two different ways in one afternoon: from the canvas centre the
existing art reads 125.88 ("4 pixels outside the circle"), from the content centre 124.47, and only **canvas centre with
solid = alpha >= 128** reproduces the documented 125.25. `scripts/panel_frames.py` applies the contract and
`test/panel-lift-frames.test.ts` asserts it; `--check-raw` gates a raw generation's silhouette before it is accepted.

**Two invariants are pinned because their absence is silent.** The suite asserts that **no two frames are the same
image** - comparing **all pairs, not just adjacent ones**: the first version compared neighbours only, and a mutation that
copied one frame over another passed green, which is how the hole was found. It also asserts that the **largest connected
component** keeps its width and aspect ratio, because a regenerated frame can stop being the same character while every
geometry assertion still passes. The first generation did exactly that: the hood became a pointed cone and the cat's
aspect ratio moved from 0.794 to 0.650 (-18%).

**No new IPC, and the squint face keeps a job.** The action is entirely renderer-side; a new IPC method would have to touch
the pinned method list in `preload.cjs` and three test doubles for no benefit. `mascot-happy.png` is not retired - it is
the **reduced-motion press face**, which is what it is for once an animation exists.

**Consequences:** the frames are generated art, so they are re-rollable but not hand-editable, and the resting frame stays
`mascot-base.png` (it is pixel-pinned to the iris layer, so regenerating it would break the eye-follow). **Not yet verified
live** - the wiring, the geometry and the frame sequence are covered by tests, but the animation has not been watched on a
real window.

## D-063: Sending stays unreachable from the model, and becomes reachable from a human who names the candidate

**Status:** Active

**Decision.** `weflow-cli draft <talker>` already did the hard half - judge the intent, draft three deliberately
different candidates, rank them with the decision model - and ended by saying the candidates are **only text** and that
sending is the user's call. It now also carries that decision out, under three rules:

1. **`--send` requires `--pick N`.** There is no "draft three, let it choose, let it send" path. The human names the
   candidate; the model never selects what goes out.
2. **Two-phase.** Without `--yes` there is no send: JSON mode returns `CONFIRMATION_REQUIRED` **with the text in the
   payload**, interactive mode asks "确定把第 N 条发给 X？". Both non-`--yes` paths are covered by tests that also assert
   **no send attempt reached the audit log**.
3. **One send policy, not two.** Blacklist → whitelist → rate limit → audit now lives in `src/services/outboundSend.ts`
   and is called by both `send` and `draft --send`. Before this, that chain existed only inside the `send` command's
   action; a second copy is how a "send" path ends up quietly skipping the whitelist.

**Why the model stays out of it.** `docs/EXTENDING.md` states that sending is *structurally unreachable from a
model-driven path*, and that is not a mood: the assistant and the MCP surface have no send tool, and
`test/draft-send-cli.test.ts` now asserts that `send`, `draft`, `send_message` and `publish_article` are absent from
`TOOL_DEFS` (while `draft_reply` stays present - drafting is not sending). What changes here is only the **human's**
side: after reading the candidates, the user no longer has to copy one into another terminal.

**Constraints that fell out of the measurements.** (a) `--pick` out of range and `--send` without `--pick` are
**usage errors, so they are reported before any model call** - the first version validated them after the confirmation
gate, which meant the model ran, and was paid for, before the command said "there is no candidate 9". (b) `--dry-run`
means **zero egress**, and candidates can only come from a model, so `--dry-run` cannot preview "what candidate 2 would
send"; it reports the character count the script would send and says why, rather than pretending. (c) Sending still
requires the other side's `context_token` - the channel needs a message from them first - so "send to anyone" was never
available, and the whitelist remains the real boundary.

**Consequences.** The tests do not call a model and do not need one: refusing early is exactly what they assert. The
audit file (`~/.weflow-cli/audit-send.log`) is what they read, and the positive control that proves that read is not
vacuous does **not** come from a real send - with a temporary home the channel is not logged in, so `send --yes` exits
at `MESSAGE_CHANNEL_NOT_LOGGED_IN` before the audit line. It uses the **blacklist** branch instead, which audits before
that check. That detail cost one debugging round and is written here because the next person will hit it too.

## D-062: Relationship temperature stays on the command line, and its "who counts as a person" rule flags rather than drops

**Status:** Active

**Decision.** `scripts/bonds.py` reports three things from the local message database: relationships that are cooling
(substantial history, then silence), relationships heating up (the last 7 days well above the long-run rate), and
contacts whose history never mentions another channel (phone, email, meeting up) - i.e. the ones reachable *only*
here. It is **read-only, local, and calls no model**. Two rules are pinned:

1. **It is not in the tool table.** It is not exposed to the assistant, to MCP, or to any cloud model - not as a tool
   and not as context. Everything else this project computes about the user is either explicitly shareable or has a
   confirmation gate; this one is neither. Its output is an **inference about third parties** (which relationships the
   user is neglecting, who they would lose), derived from private messages, and the person it could most embarrass is
   not the person running it.
2. **"Who counts as a person" flags, it does not drop.** The rule is the **shape of the id** - anything containing
   `@` (`@chatroom` groups, `@openim` service accounts, `@weclaw` bots), `filehelper`, or a `gh_` official account, is
   not a person; everyone else is. Accounts that are structurally indistinguishable from a person but whose *name*
   looks like a shop or a hotline (普华口腔门诊部 is one on this machine, with `local_type = 1` and its own alias) are
   listed under "flagged, you decide", with a skip file (`output/bonds-skip.txt`) and `--skip` to act on it. And every
   account the run excluded is printed with the reason, so a filter can never quietly remove two friends.

**Reason.** Three measurements killed three simpler designs. (a) Ranking by message volume produced a leaderboard
with no information - the people you message most are not news to you. (b) "Never mentions another channel" was
**inverted**: it is trivially true of bots, so the first version of the irreplaceability ranking had 星巴克小助手,
天天神券福利君, 串掌门（南村店）and 文件传输助手 at the top. (c) `flag` looked like the clean human/bot marker - real
friends mostly carry `flag = 3` - until the distribution was read: of 38 people, three differ, and only one of those
(`微信ClawBot`, 2049) is a bot; 彪弟 (2051) and 平凡的世界 (2563) are people whose bit was set by however they were
added. Shipping `flag == 3` would have silently deleted two friends, which is the failure this project keeps meeting:
a filter that is 95% right and reports nothing.

**Consequences.** The thresholds are constants with tests and mutation checks (`COOL_DAYS`, `HOT_RATIO`, the minimum
history required for "cooling"), because they are the whole judgement. The skip file lives under `output/` (gitignored,
never committed). Adding it as a command in `bin/weflow-cli.ts` is fine and expected; adding it to the assistant or MCP
tool tables is not, and that is the part of this decision worth keeping written down.

## D-061: The MCP server can speak HTTP - so the boundary had to be written down, not just implemented

**Status:** Active

**Decision.** The MCP server serves its tools over **Streamable HTTP** as well as stdio, and the second transport is
**off unless asked for** (`--http`, or `WEFLOW_MCP_HTTP`). The tool table is the same one (`MCP_TOOL_DEFS` derived from
`TOOL_DEFS`), so the two transports cannot drift; only the carrying changes. Four rules are enforced in
`mcp-server/httpConfig.ts` - pure functions with offline tests - rather than left to the caller:

1. **Off by default.** No flag means stdio, byte for byte as before.
2. **A token is mandatory, with no default.** Missing or under 24 characters, the process refuses to start.
3. **Loopback only.** `0.0.0.0` and every other non-loopback address are rejected with the tunnel command in the
   error message.
4. **DNS-rebinding protection on.** The SDK's default is `false`; the realistic attack on a loopback server is a web
   page the user happens to visit reaching `127.0.0.1`, so the `Host` header must match and any `Origin` must be
   allowlisted.

**Why HTTP at all.** stdio can only be spoken by a client that is able to spawn a process. Serving HTTP is the norm
in this ecosystem - `chatlog` is the de-facto standard and does exactly this - and it is what lets a resident
service, a browser-side client, or several clients at once use the same query surface.

**Why the boundary is written down rather than only coded.** This is the first time this project opens a **listening
socket that serves the user's private messages to whoever can reach it**. The failure mode is not a crash: it is a
port that stays open, bound wider than the author intended, or opened with a token that looks configured and is not.
So each rule is (a) in a pure function with its own test, (b) mutation-checked - disabling the rebinding protection,
removing the token check, widening the loopback list, lowering the token floor, or making the check return `true`
each turn the suite red - and (c) stated in `docs/MCP.md` where a user meets it.

**Why no remote bind.** The tempting convenience - `--host 0.0.0.0` so a laptop can reach the desktop - is exactly
the change that moves private chat data onto the network. A tunnel is one command and keeps the exposure local; if
someone later wants a real remote surface, that is a separate decision with its own threat model, not a flag here.

**Consequences.** `mcp-server/index.ts` now exports `buildServer()` and starts only when run as a script (importing
it must not open a listener - the tests import it). The token is never logged. Requests are stateless (one server per
request, no session id), so there is no cross-client state to leak. Nothing about the stdio path changed: an existing
client configuration keeps working untouched.

## D-060: A backfilled day records which topics it holds, because "the file exists" is not "the day is done"

**Status:** Active

**Decision.** `backfill_articles.py` can fetch one topic instead of a whole day (`--topic AI`), and a day written
that way is **partial and says so**. It uses the classifier the script already had - `_guess_topic`, which reads only
the title and the account name and never the article body - so filtering first selects exactly the articles a full run
would have filed under that topic. There is no second criterion. What is new is the bookkeeping: `.articles.json`
carries `topicFilter`, the README states which topics are missing, and `day_done` refuses to call that day complete,
so a later full run still covers it.

**Reason: one truncation prevented, and one found already live.** Without the marker, a day holding only its AI
articles looks finished - the md files exist, `.articles.json` exists, and `day_done` asked nothing more than "is the
list non-empty". The other five topics would never be fetched and **nothing would report it**, which is the failure
shape this repository has already paid for once: `--vault-sync` was keyed off a `backfilled` marker that only one of
its two writers sets, so 12 days were never copied *and* never reported as skipped (2026-09-28, 187 → 199 days).
Checking for this one turned up the same defect already shipped in `--limit-per-day`: the day is sliced, written, and
then reads as done, so everything past the limit was silently out of scope forever. It is recorded in the same marker
(`truncated`) because it is the same mechanism, and it is a **behaviour change** - a limited day is now redone on the
next run rather than skipped.

The filtering is applied **inside `write_day`** as well as at the call site. If only the caller filtered, a caller
that forgot to would write all six topic directories and stamp "only AI" on them; a marker that lies is worse than no
marker, and this is the only way to make the two structurally unable to disagree.

**Consequences.** `.articles.json` gains two optional keys; a full run's output is byte-identical to before, so no
existing output needs migrating, and `day_done` reads a missing `topicFilter` as "complete" - which is also what the
days already on disk mean. `--topic` changes no criterion: over 2025-09-05 ~ 2026-03-02 the script's own classifier
puts **1,758 of 9,207** in-scope articles in `AI` (5-day sample: 300 → 66, the same share the daily pipeline reports
over the 3,498 articles already backfilled). That figure is the tool's **keyword** view and not the daily line's model view -
these articles have never been through a model, which is why the script can pre-filter at all, and why the count is
labelled as the keyword's. The scope is official accounts only (`gh_%`), which is what the script queries - an earlier
count over every session in `Name2Id` read 10,880 and was wrong by 1,673 articles that live in non-official
conversations and that this path never fetches. Anything scripted against a partially backfilled day must read `topicFilter` rather than
infer completeness from the presence of `.articles.json`. An unknown `--topic` is rejected before the database is
opened, since a typo would otherwise select 0 articles and report "nothing to do" - indistinguishable from a day that
genuinely had none.

**A third marker joined them on 2026-10-14**: `fetchFailed` records a body fetch that failed *in that run*, so one
network hiccup is no longer written down as "skipped N (body too short)" with the day still counted as done - which
is how those articles used to disappear for good. Unlike `topicFilter` and `truncated` it is **not** sticky: a full
run with zero failures proves the earlier ones were recovered, so the marker clears (and the cache makes that retry
cheap). See D-108.

## D-059: The ball's eyes follow the mouse - and that makes the cursor position a data flow, so it is written down

**Status:** Active

**Decision.** The mascot's irises track the cursor. The position comes from the **main process**
(`screen.getCursorScreenPoint()`, sampled every 120 ms, pushed **only when the coordinates change**), because the
renderer cannot see it: the ball is 96x96 and the pointer is outside the window almost all of the time, so
`mousemove` never fires. The page eases toward the pushed point and **stops its rAF loop the moment it settles** -
a permanently-running 60 fps transform would be a compositor cost paid for decoration, on a window that is always
open.

**Why there are now two images instead of one.** The mascot PNG has its eyes **drawn into** it, so nothing in it can
move. `mascot-base.png` is the same art with the iris region repainted in the sclera colour, and `mascot-iris.png` is
the iris (with its pupil and highlight) as its own layer. Composited at zero offset the two are **pixel-identical**
to `mascot.png` - a test decodes all three PNGs and asserts that pixel by pixel, because every weaker check (same
size, present in the whitelist, present in `PANEL_FILES`) stays green if somebody re-cuts them one pixel off, and a
one-pixel misalignment is visible to a human and to nothing else. `mascot.png` stays in the repo as the source the
other four expressions are generated from, but it is **no longer what the ball shows** - pointing the ball's face at
it while an iris layer moves on top would show two pupils, one of them stationary.

**The travel limits are measured, not chosen.** Scanning the source pixels along the iris: to its left there are
about 7 px of sclera before the dark rim, and above it there is none at all (the upper lid sits on the iris). At the
ball's 96 px that is roughly 2.6 px of usable travel sideways and essentially zero upward, so the clamp is an
**ellipse** (2.6 sideways / 0.8 up / 2.6 down) rather than a circle. Past that the iris separates from the rim and
the eye reads as a sticker sliding across the face. The constants are written for the 96 px ball and are scaled by
the element's measured width, so changing `--ball-size` does not require re-deriving them.

**The eyes move only on the two faces that have any to move.** Four of the five faces have their eyes baked in;
stacking a moving iris on those shows two pupils. The iris layer is therefore hidden for `busy` / `offline` /
`quota` / `ball-happy`. That is deliberate, not a limitation: those are precisely the "not idle" states, and they
already speak with the whole face.

**Why a data-flow entry.** Cursor coordinates are the panel's second input that does not come from the daemon (the
first is the window geometry from `ball-position.cjs`). They travel **in-process only** - main process to this
window's renderer - are narrowed in `preload.cjs` (two clamped integers; malformed payloads are dropped rather than
handed to the page) along the same rule as every other channel, are never logged and never leave the machine, and
the feed stops entirely while the window is hidden. "The app knows where your mouse is" is a data flow even when it
never crosses the machine boundary, so it is recorded rather than left implicit.

**Not yet verified live.** The wiring (the feed, the clamp, the reduced-motion path, the pixel identity) is covered
by the suite, but the effect itself has not been run on a real window - that window only exists on a machine with
Electron, via `weflow-cli panel`. The amplitudes come from the pixel measurements above; whether 2.6 px reads as
"looking at you" at 96 px is a judgement only a person in front of the screen can make.

## D-058: The assistant reaches the MCP ceiling minus the exclusions - and two of the new capabilities carry a residual risk that is written down here rather than discovered later

**Status:** Active

**Decision.** The assistant's tool table was brought up to the same ceiling the MCP surface already had, minus
everything the project has decided stays out. Three surfaces had drifted apart: the CLI exposes ~93 command
declarations, `TOOL_DEFS` had 21 tools, and `mcp-server/index.ts` additionally hand-wrote 11 tools that the chat path
could not reach at all (`get_review`, `get_concepts`, `format_article`, `list_themes`, `fetch_article`,
`search_public`, plus the merged `get_stats`). Ten tools were added - `list_contacts`, `get_review`, `get_concepts`,
`fetch_article`, `search_public`, `format_article`, `list_themes`, `lint_wiki`, `check_skills`, `set_todo_status` -
the seven that existed only on the MCP side were **moved** into `TOOL_DEFS` (same tool names, same argument names,
single implementation, so the hand-written table shrank from 11 entries to 4), and four existing tools were widened
rather than duplicated: `get_daily_report` gained `full` (the human-readable daily) and cross-date search (which is
what the MCP-only `search_articles` did), `get_todos` gained the item id and `group_by`, `get_weread` gained five
`mode`s, `get_stats` gained the knowledge-base half it had been missing while the MCP copy had it.

**The direction is safe because it is not a new kind of access.** Every capability added here was already reachable
by an MCP client; the change removes an inconsistency rather than widening what the project exposes. What did *not*
move is the ceiling itself: sending, publishing, configuration, access-list writes, `evidence-review`, `vault promote`,
scene reads and writes, `login-wechat`/`logout`/`listen`/`assistant run`, `sns capture-key`, `decide` and `init`/`dbkey`
all stay unreachable from a model-driven path, each with the decision or comment that says so.

**Two capabilities carry a residual risk, and both were chosen deliberately by the user rather than slipped in.**

*Network egress from the chat path.* `fetch_article` and `search_public` are now callable from a WeChat message. The
user was shown the asymmetry - the MCP path is documented as a trusted local integration, while a chat message is
untrusted input that can carry a prompt injection - and chose to expose them **without** a confirmation gate, matching
MCP. What each actually is: `fetch_article` accepts only credential-free HTTPS URLs on the exact `mp.weixin.qq.com`
host and **re-checks that allowlist on every redirect hop** (the default `follow` would validate only the entry URL;
the test asserts the `redirect: 'manual'` option, because a stubbed fetch cannot observe undici's internal follow -
that hole was found by a mutation check). `search_public` is the weaker one and is recorded as such: it scrapes a
third-party search page, its URL is a constant so no allowlist applies, and **the model's query leaves the machine
with no preview**. Anyone reviewing that decision should read it as: a chat-driven model can cause an arbitrary string
to be sent to a search engine, and that is accepted. The mitigation that exists is not a gate but the shape of the
data: the query is a search phrase, never chat content.

*A bounded local write.* `set_todo_status` flips one existing todo between `done` and `pending`. It **cannot create,
delete or edit** anything else - there is no `todos rm`, no `daily add/remove`, no `todos extract` in the tool table,
and a test pins that (`工具表里没有任何删除待办的工具`). It carries **no** `ctx.requiresConfirm` gate, following the
`export_chat` precedent (a write tool with no gate, because the gate set is machine-checked by
`test/assistant-tools.test.ts` to be exactly `callsCloudModels` - adding a gate to a tool that does not send user data
would either break that equality or require writing a false claim into `capabilities --json`). The residual risk is
therefore stated plainly: **an MCP client does not get this tool at all** (`MCP_EXCLUDED`, because "mutating todos
stays out" of that surface), while the chat/panel path can flip a status with no preview. Ambiguity is refused rather
than resolved: a task-text match must be unique, and the reason is that a wrong guess looks like success while
changing the wrong row.

**Reason.** Adding tools is not free: it makes the model choose among more near-duplicates, which is a failure mode
this project has already measured once (`get_reading_stats`'s description had to be rewritten after it stole WeRead's
question space and answered a reading question with official-account push counts). So the widenings in this entry are
mostly *replacements*: cross-date article search went into `get_daily_report` rather than a second article-finder,
`todos remind`'s grouping went in as a parameter rather than a tool, per-book WeRead data went in as `mode` values
rather than five tools, and `get_concept` was deliberately **not** added because `search_knowledge` already returns
the page plus its neighbour subgraph and a second, weaker lookup is a net loss for routing.

**Consequences.** Two injection points were added - `WEFLOW_ASSISTANT_BIZ_DAILY_DIR` and
`WEFLOW_ASSISTANT_REVIEWS_DIR` - following `WEFLOW_ASSISTANT_EXPORT_ROOT`, and both are read **per call** rather than
frozen at import so a test can swap fixtures inside one process. That closed the last uncovered tool branch: the test
suite's coverage note had said for months that `get_daily_report` was never executed because its directory was a
module-level constant with no injection point, and that the only possible assertion was a "either there is data or
there is not" shape check - which is not an assertion. It is now asserted against a fixture, with its three branches
(date, cross-date search, prose). The coverage claim is therefore "**all 31 tools are executed**" and the note says
where each group lives. Locally-held identifiers were the other thing to get right: `list_contacts` returns names
only (remark / display name / nickname - never `username`, never the avatar URL), because keeping wxids out of the
model's context is a standing rule of this repository.

**Added 2026-09-30.** A third injection point, `WEFLOW_ASSISTANT_VAULT_DIR`, was added the same way and for the
same reason: `get_concepts` read a module-level constant, so in the behaviour eval's temporary home it could only
ever report "the knowledge base has not been compiled yet" - there was no fixture that could change that. With the
root injectable it has a real case, as does `get_review` (its variable already existed, but the eval harness had no
way to set environment variables at all, so it too had no case). **"That closed the last uncovered tool branch"
above was true of the deterministic test suite, not of the behaviour eval** - the eval still had ten tools with no
case at all, and those are what the two 2026-09-30 passes went after.

## D-057: A scene is bound to a conversation by the one id that already exists, it is picked by three tiers that refuse to guess, and the model cannot write one

**Status:** Active

**Decision.** A scene is a named prompt preset - keywords, an extra instruction, an output spec, a required-skill list,
an enabled flag - stored in its own versioned file (`~/.weflow-cli/assistant_scenes.json`, schema `weflow-scenes/v1`,
unknown version quarantined rather than migrated, atomic write, same discipline as the memory file). A turn picks at
most one scene through three tiers in order: an explicit **binding**, then a **keyword** hit in the incoming text, then
the **last scene used** in that conversation. No tier matching means no scene section in the prompt - the default
behaviour is byte-identical. When two scenes match a keyword of the same length the turn carries **no** scene and the
trace records why, following `resolveUniqueTalker` and `resolvePanelUserId`: ambiguous input is refused, never guessed.

**The binding key is `userId`, which is the conversation on the path that has conversations.** On the WeChat path
`resolveInboundRouting` sets `conversationId` to the `@chatroom` id for a group and the peer's wxid for a direct chat,
and `assistantService` passes exactly that through as `userId`; so binding by `userId` *is* binding by conversation, and
it also gives the panel path (one stable bucket) something coherent to bind to. The cost is written down rather than
discovered later: the MCP path collapses every caller into the literal `'mcp'`, so **MCP has no conversation to bind a
scene to** - the same collapse that already applies to memory. Keying scenes per `userId` inside the *memory* file was
rejected outright: that file is rewritten by an LLM during compression and fact extraction, and it can be quarantined
wholesale, neither of which is an acceptable fate for something the user authored by hand.

**The model cannot touch scenes, and that is the point.** There is no assistant tool that lists, reads, creates or
binds them; scenes are administered by `weflow-cli scene …` and by one built-in chat command (`场景 <id>`, `场景 无`,
`场景`). This is the same position the `隐私` built-in takes - a chat message must not be able to change configuration -
and it is why `capabilities --json` reports `modelWritable: false` for the workflow. The built-in command is also the
only prefix-matching command in the assistant, which is precisely the shape that silently eats ordinary sentences, so
it is narrowed twice: the argument must name an existing scene id, and anything else (`场景切换怎么用`) falls through to
the model untouched.

**Reason:** the roadmap's P3 item asks for per-conversation control of the assistant rather than one global
personality, and the three tiers are ordered by how much the user meant them: a binding is an explicit statement, a
keyword is an inference from one message, and "last used" is only a convenience that keeps a habit from decaying. The
refusal to break a tie is the same rule the codebase already applies to names - a coin flip between two scenes looks
like the feature working while producing an answer nobody asked for.

**Consequences:** `scene add`/`remove`/`bind`/`unbind`/`enable`/`disable` are two-phase (`--dry-run` then `--yes`),
matching every other local mutation. The scene section enters the prompt wrapped by `frameLocalData`, because a
scene's instruction is user-authored text that can contain `</weflow-local-data>`; scene ids are therefore validated
against quotes and angle brackets before they reach the label, and sanitised again at render time. Verified end to end
through the resident service with a stubbed model: the scene section appears in the system prompt, the trace carries a
`note` step naming the scene and the tier that matched, a keyword tie produces no section plus an explanatory note, and
a turn with no match produces no section at all.

## D-056: A skill is material, not a plugin - it is referenced by path, it grants no tools, and a broken one is reported rather than skipped

**Status:** Active

**Decision.** The assistant can use skill packages: directories containing a `SKILL.md` with `name`/`description`
frontmatter, scanned from a configurable list of roots (`skillDirs`, default `~/.claude/skills` and
`~/.weflow-cli/skills`, overridable per-process by `WEFLOW_ASSISTANT_SKILL_DIRS`). Two read-only tools expose them -
`list_skills` returns the catalogue (id, name, one-line description, version, state) and `read_skill` returns one
body wrapped in `frameLocalData` - and the system prompt carries the catalogue so the model knows what exists. Skills
are **not installed anywhere**: the prompt tells the model to read the file at its path, which is what the reference
implementation does and is far cheaper than writing an installer per target agent. A referenced skill that is missing,
disabled or unreadable is rendered as exactly that state, and the instruction is to continue and say which part was
not done - never to silently drop the reference, and never to invent the capability.

**This is the constrained form of the roadmap's plugin/adapter item (P4), and it is deliberately the smallest one that
is still useful.** Its acceptance criteria were already written down - extensions must not read configuration,
databases or arbitrary files, and must be individually disableable - and a prompt-time document satisfies all of them by
construction: `read_skill` can only return a file named `SKILL.md` that a scan already produced, so an id like
`../config.json` is not a path at all, and disabling is one config key (`skillDisabled`) or one line in the skill's own
frontmatter (`enabled: false`). Nothing here executes skill content, and installing a skill does not add a tool to the
model's table. D-048 stands: the only way to add a *capability* is still to edit this source tree.

**Two rules were taken from real data rather than from the specification.** First, the id rule is **wider than
Anthropic's**: that spec allows only lowercase letters, digits and single hyphens, which would reject five of the 28
skills on the development machine (`clz_docx_to_mp`, `agent-dialog_management`, `clz_wechat_mp_ops`,
`ylx_onehub_usage_monitor`, `ylx_research_evidence_synthesis`). Those are skills the user actually uses, so a
non-conforming id is **warned about and kept**, and only the quoting characters that could escape a prompt label are
refused. Second, the parser reads **only the leading `---` block** and understands block scalars: the same 28 files
carry `categories:`/`created:`/`tags:` inside their bodies, so a whole-file scan reads body text as fields, and
`description: >-` with indented continuation lines is how several of them are actually written - a naive line reader
returns the two characters `>-` as the description.

**Consequences:** symlinked skill directories are **followed** (a deliberate choice, not an oversight): developing a
skill in a git checkout and linking it into `~/.claude/skills` is a normal workflow, the link is placed by the user and
not by an extension, and the only file reachable through it is one named `SKILL.md`. A skill with no frontmatter block
is reported as unreadable by `skill check` instead of being skipped - a skill that vanishes silently is indistinguishable
from one that was never installed. Verified against the real 28 skills with `skill check`: 0 unreadable, 0 collisions, 5
spec-deviation warnings, 1 self-disabled.

## D-055: The Vault's copy of an article is cleaned, the fetched original is not - and "which days does the copy run cover" was being decided by a marker that only one writer sets

**Status:** Active

**Decision.** `copy_to_vault` now writes the Vault's `Sources/WeChat/<day>/` copy through
`strip_wx_ads_document`, which removes the known WeChat interface phrases (`继续滑动看下一个`, `轻触阅读原文`, the
`在小说阅读器中沉浸阅读` family, eight in total) while leaving **two things untouched**: the frontmatter and any
fenced code block. `output/biz-daily/` keeps the fetched text byte-for-byte.

**Where the cleaning lives is the whole point, not an implementation detail.** Cleaning in the fetch step would
have been simpler and would have made the two copies identical - but `biz-daily` is the only copy of the fetched
bodies, so a cleaner with a bug there destroys material that cannot be re-derived. Cleaning only inside the copy
function keeps the original recoverable, at the cost of the two trees no longer matching - and that cost has to be
paid **inside the copy**, because this step is re-runnable. It is not hypothetical: on 2026-09-28 a plain re-sync
silently restored the residue on 15,731 files, because an earlier one-off pass had cleaned the Vault copies while
the copy function was still dumb. Any future path that writes article text into the Vault must go through this
function or it will undo the cleaning again.

**Two protections, each because the obvious version is silently wrong.** The phrases are short words:
`去阅读` appears inside the title `如何去阅读一本书`, and the title is the only alignment key between a reading note
and its source, so the frontmatter is never cleaned (measured: 0 of 35,060 files currently need it - the guard is
for the future, and is written down as such rather than presented as a fix). `strip_wx_ads` also normalises
whitespace, which is harmless for the summaries it was written for and destroys indentation in code blocks:
20 files carry fences and **10 of them would have been altered** (HTML/JS samples).

**What it deletes is more than the phrases, and that has to be stated rather than glossed.** It is
`strip_wx_ads`, the function the reading notes already use, so it also drops `______` runs, `javascript:void(0)`
links, the `原创 <账号名>` byline WeChat repeats several times, and repetitions of the same token. The byline rule
alone fires on **9,334 of 35,463 files**. Classifying every diff over a 300-article sample found no reworded prose
- that is the part that was verified, and it is the reason it was defensible to point the cleaner at a whole
article body. An earlier draft of this entry said the pass touched only phrases and whitespace; measuring the
corpus disproved it, and the correction is recorded here rather than quietly edited away. The byline is page
furniture and `source:` in the frontmatter still carries the account name, so the trade is acceptable - but it is
text removal, and the pristine copy lives in `output/biz-daily/`.

**A gate that had been wrong from the start.** `--vault-sync` selected days by the `backfilled` marker in
`.articles.json`. That key is written by the backfill path only, so the 12 days produced by the `daily` path (which
writes `articles` but not `backfilled`) were never copied **and were never reported as skipped** - including two
days, 08-24 and 08-25, with no directory in the Vault at all. The gate is now "the day has at least one `.md`
besides `README.md`", which is the condition the flag was always meant to express. Measured: 187 → 199 days,
33,232 → 35,463 files. The 1,755 files that kept their residue after the first cleaning pass were exactly this
population.

**Why the notes were never affected.** Every reading note points at `output/biz-daily/...` through `local_source`,
not at the Vault copy - verified across five months, 1,144 of 1,144. So none of this created a broken link, and
`wiki lint` reports the same result before and after.

## D-054: A page can be assembled from material that was already paid for, and "no model call" is not the same as "no model wrote it"

**Status:** Active

**Decision.** `wiki compile --pages-from-cards` builds pages for concepts that have none, locally and without any
API call: the definition is the `desc` the card already carries, related concepts are the other concepts from the
same article (co-occurrence, median 3), the source is that article. 17,383 pages, ¥0. It was written after the
model-based route was priced at **¥31** and the user called that expensive - which is the right call, because
every one of those candidates is mentioned by exactly **one** article, so there is nothing to synthesise and a
model would only be rewording a single sentence.

**The honest description is "reusing output already paid for".** The `desc` lines were written by
`article_notes` when it extracted concepts from articles, so calling this "no model involved" would be wrong:
the quality is that line's quality. Most read like definitions, a few are meta-descriptions about where the term
appeared in the article (`免疫生态位` - "the other thing the title splits out alongside cell types"). Pages carry
`summary_by: card` for exactly this reason, following the `summary_by: model` convention `user_notes`
established: text that came from somewhere else must not be readable as "this is what the page's subject is".

**Why this is not the same trade as the earlier ¥32 rejection.** The 500 trial pages built with a model are
better pages - they have a synthesised definition and a "key points" section - and they cost ¥0.9 for 500. The
difference is not quality, it is what the money buys: with one source per concept, it buys prose polish that a
reader cannot tell from the original sentence. That trade is worth making per page only when the user asks for it;
it is not worth making 17,383 times by default.

**A guard that had to be added, and why the first version was wrong.** `--refresh-sources` deliberately scans
**all** card directories (it fills in edges on existing pages, so more is strictly better). Reusing that same
default for page building would have put chat-line concepts into `Wiki/Concepts` - the two lines are two
knowledge bases, which the user asked for explicitly on 2026-09-27. The flag now requires explicit `--cards` and
prints the exact commands for each line when it is missing.

## D-053: One concept is one node, and duplicates are merged through `aliases` rather than by rewriting links

**Status:** Active

**Decision.** `wiki compile --merge-duplicates` finds concept pages whose names are the same under a normal form
(strip spaces, hyphens and punctuation; lowercase; drop an English plural tail) and merges each group into one
page. It was prompted by the user's own reading of the graph - "some of them are the same thing but drawn as
different ones ... I want the graph to be effective, not piled up like garbage" - and the measurement backed it:
74 groups, 80 redundant pages, in shapes like `GPT 5.6` / `GPT-5.6` / `GPT5.6`, `AI skill` / `AI skills`, and
`AI 工具` / `AI工具`.

**The other names become `aliases`, and that is the whole reason this is cheap.** 98 links in the vault pointed
at names that would be deleted. Writing those names into the surviving page's `aliases` means Obsidian resolves
every one of them - so **nothing else in the vault has to be rewritten**. Doing it the obvious way, by rewriting
every referencing page, would have touched files across both knowledge bases for a graph-shape change, and the
next merge would touch them again. Verified both directions: all 80 dropped names are aliases somewhere, and all
98 links are covered.

**Which page survives is decided by source count, not by name.** `claude code` (411 sources) absorbs `ClaudeCode`
(7). Sorting by name instead would keep the emptier page - and a test now pins this specifically, because the
first version of the fixture happened to make both rules agree, so the mutation "sort by name" passed.

**The criterion is not the lint's.** `wiki_lint` calls two titles near-duplicates when they share a 5-character
run covering 40% of the shorter one, which reports `Claude Code` and `Claude 4.8` as a pair. The merge does not
use that rule and does not merge them: a false merge destroys a real concept, a missed merge just leaves a
duplicate. `wiki_lint` now reads `aliases` as well - otherwise it would report the merged-away names as "not
built yet, run compile with a higher limit", which is not a warning but a wrong instruction (that count fell
9,421 -> 9,384).

**Not done, deliberately.** Nothing automatic: it is a flag, `--dry-run` shows the list first, and it is meant to
be run when the user notices the graph getting noisy rather than on a schedule. Nor does it try to merge the
looser "similar name" set - deciding whether `Claude Code` and `Claude 4.8` are one thing is a judgement, and
that is where a decision model rather than a string rule would be needed. **That last one was tried on
2026-09-28, and Jev cannot do it either** ($0.17): of the 13,367 loose pairs, 741 came back "the same
concept", and sampling them (the most confident 25, then a random 12) showed essentially all are wrong
(`Token 节省` vs `Token计费`, `Agentic RAG` vs `Agent面试准备`). Rewriting the instructions with
counter-examples fixed those and broke the true ones in the same run (`AI skill` vs `AI skills` came back
"related but different"). The question here is **literal** - same string modulo spacing, case, plurals - and
Jev is a semantic model; the normal form was the right tool all along. Narrowing the 741 to "one name is an
"extension of the other" left 27, and inspecting each of them by its actual definition left **two** that
are genuinely the same (`TRAE IDE Linux 版` / `TRAE IDE Linux 版本`, and
`100 万 token 上下文窗口` / `100万token上下文`) - merged by hand, aliased, 0 broken links. The other
twenty-five were rejected on meaning: "日均Token调用量" is a rate and "Token调用量" is a total;
"2026世界人工智能大会" is one year's event and "世界人工智能大会" is the series; "Token 节省" is a
concept and "Token 节省技巧" is a set of methods. So the loose set stays untouched, and that is a finding
rather than a gap: the graph's name-level duplication was already nearly exhausted by the 74 groups the
rule could settle, and the 741 Jev proposed contained two.
untouched, and that is a finding rather than a gap: the graph's name-level duplication was already nearly
exhausted by the 74 groups the rule could settle.

## D-052: Concept pages accumulate on their own schedule, and the body's source list deliberately diverges from `sources:`

**Status:** Active

**Decision.** `wiki compile --refresh-sources` is a local, model-free step that rewrites the `## 来源` section
of **existing** concept pages so they carry every source the cards currently support. It landed because the
user asked how the graph grows when new articles arrive, and the honest answer turned out to be "only new
nodes": `build_jobs` skips concepts that already have a page, so an article mentioning `DeepSeek` adds nothing
to `DeepSeek.md`. `OPERATIONS.md` had been claiming the pages were "a ledger" while the implementation made
them a snapshot; one run added 3,164 edges across 324 article-line pages and 148 across 34 chat-line pages.

**Three things it does not do, each because the obvious version is silently wrong.**

*It does not sync the frontmatter.* The body and `sources:` carry **deliberately different** names, and the
live example is a chat page: `sources: [24环境专硕班]` (the card name, which is what `source_kinds_for`
looks up under `output/chat-notes/`) versus `- [[会话-24环境专硕班]]` in the body (the Vault name, which is
what Obsidian resolves under `Sources/Chat/`). Writing either name into the other place breaks something
without erroring: put the body's name into `sources:` and 100% of chat-line tag inference stops (measured),
while `relabel_pages` only ever *adds* `来源/*` tags - so the page keeps looking correct. This is a case of
"the same fact in two places" where the two places have genuinely different consumers, so the rule is to say
so out loud rather than to unify them.

*It only adds.* 322 article-line source lines have no card behind them any more (the card was rewritten, or
no longer mentions the concept). They stay. That makes the page's source count **larger** than the index's
citation count on 195 concepts - and the earlier reading of that gap, that the two numbers *should* match,
was wrong: the index says what the cards say now, the page holds every reference it ever had. When this was
measured, all 195 differed in that one direction and every extra line resolved to a real file.

*It skips names that cannot be written.* Six contain square brackets (Obsidian cannot resolve those either),
and one has a title that itself ends in `.md`. That last one is worth remembering: `source_link_target` peels
one suffix and yields `….md`, while `wiki_lint.resolve` sees the `.md` and looks for a *file* by that name -
so the write would have turned the lint's "0 broken links" into 1. The card's real name is `….md.md`.

**Ordering is a spec, not a history.** Sources are written `sorted()`. The first draft appended new lines after
the existing ones, which makes the order a function of when each line arrived - so the diff never settles and
an insert lands in a different place on every machine. Measured cost of normalising: 29 extra pages.

**What was NOT changed.** `generate_concept` still truncates the model's *reference material* to five entries
(`build_ref_lines`, a prompt-budget decision, tested), so a page may now list 301 sources while the model that
wrote it saw 5. That is a real asymmetry and it is stated here rather than left to be discovered - the
alternative, feeding 301 sources into the prompt, is a different change with its own cost.

## D-051: One Vault, two knowledge bases - and a directory list that five readers have to agree on

**Status:** Active

**Decision.** The article line and the conversation line are kept **apart on disk and together in the graph**:
`Wiki/Concepts` (+ `Wiki/00-Overview.md`) is the article knowledge base, `Chat/Concepts` (+ `Chat/00-Overview.md`)
is the chat one, and the conversation cards live at `Sources/Chat/会话-<name>.md` next to `Sources/WeChat/`.
Both are inside the same Obsidian vault, because the request was to keep the two knowledge bases apart, not to
make the person browse two vaults - and the graph is the one place where their 4% overlap is information rather
than noise. The split is the user's call (2026-09-27); the parts below are the consequences that had to be paid
for it.

**The list cannot be shared, so five declarations are pinned to one.** The set of concept directories is needed
in Python (`_utils.CONCEPT_DIRS`), in `src/services/assistantTools.ts`, in `bin/weflow-cli.ts`'s `vault init`
preview, and in the separately-packaged `mcp-server/index.ts` - four languages- and package-boundaries that a
single constant cannot cross. Following the precedent already in this repo for the same situation
(`config-keys.test.ts`, the `requiresConfirm` list), the declarations stay separate and
`test/concept-dirs-agreement.test.ts` fails if any of them drifts. The two scripts that `import` the constant
instead (`vault_search`, `vault_rag`) cannot drift, so what is asserted about them is the other failure -
importing it and then not iterating it.

**Why this deserved a decision rather than a commit message: reading one directory of two is silently wrong, not
broken.** Every reader here degrades by *losing half its answers* - fewer search hits, fewer concepts for the
assistant, "未找到概念" from MCP - while continuing to exit 0 and print a plausible result. Nothing in the repo
would have gone red. That is the same failure class this project keeps recording (a path written in two places;
a directory declared and never written), and it is the reason the agreement test exists rather than a comment.

**A name belongs to exactly one page, and that had to be enforced in code.** Splitting the directories created
11 concepts with a page on both sides on the very first run (`deepseek.md` in `Wiki/` and in `Chat/`). Two files
with the same stem make a bare `[[DeepSeek]]` ambiguous: Obsidian silently picks one and the other is reachable
only by its full path, which is the same failure the `会话-` prefix was introduced for on the cards - and, as
there, **nothing errors**. So `build_jobs` skips a name that already has a page on the other line, and the run
reports the two skip reasons separately (a re-run versus a deliberate non-duplicate), because a single "skipped
N" line would read as if all of them were re-runs. The alternative - keeping both pages, one per line - was
rejected: a browseable graph is the thing the user asked for, and an ambiguous link is not a link.

**Superseded on 2026-10-10 by D-104.** The user asked for the two concept **namespaces** to be separate, so both
pages are kept and that skip was removed. The Obsidian facts quoted above are still true and are quoted again in
D-104 - what changed is the remedy: the ambiguity is now *reported* (`wiki lint`'s `crossLineSameName` section)
rather than prevented by not creating the page. 36 such pairs already existed in the vault when this was written,
so the invariant this paragraph describes was never true of the data on disk.

**A migrated parameter is a silent failure too.** `conceptNeighbors(pageName, wikiDirs)` took one directory and
now takes a list; a caller still passing a string is not a type error at runtime, and `for (const dir of
wikiDirs)` walks the string **one character at a time**, so the function answers "no neighbours" with full
confidence. `tsc` covers `src/**` but not `test/`, which is where the stale call was. It now throws on a string:
a wrong answer that looks like an empty answer is worse than a crash, and the same reasoning already governs the
"no data" cases elsewhere in this file.

**What was NOT changed.** `output/wechat-vault` still holds both, and the chat side is still produced by
`compile_wiki --source output/chat-notes --output output/wechat-vault/Chat/Concepts` **explicitly** rather than by
teaching `compile_wiki` to iterate `CONCEPT_DIRS` - it produces one line's pages per run, and a default that
writes both would mix the two corpora into whichever directory ran last. `user_notes` is unchanged: it reads a
whitelist of the user's own layers, so the new `Chat/` directory is out of scope by construction rather than by
an added exception.

## D-050: The WeChat channel's failures are classified, retried with backoff, and reported where the user looks

**Status:** Active

**Decision:** `wechatMessageService.startPolling` classifies every failure before acting on it. `errcode: -14`
(or HTTP 401) means the token is invalid - retrying cannot succeed - so it is reported once, in a single
unmissable line naming the remedy, and `isChannelActive()` turns false so the panel stops claiming the channel
works; the loop keeps polling rather than exiting, because the local entrance is unaffected and a restart is the
user's decision. Everything else is `retryable` and retried with 1s→30s exponential backoff, reset by any healthy
poll. Exceptions thrown by registered message callbacks are logged rather than swallowed.

**Reason:** this was fixed by checking the vendor's own implementation instead of guessing -
`third-party/WeKnora/internal/im/wechat/longpoll.go:151` states `ErrCode == -14 → ErrTokenExpired`, while this
repository checked `-1 || 401`. The consequence of a wrong code is not an error: it reclassifies "expired" as
ordinary, so the loop retried every 5 seconds forever and the user saw nothing at all. Two further differences
were closed at the same time (fixed 5 s interval → backoff; `try { cb(msg) } catch {}` → logged), and one earlier
claim was **retracted**: the long-poll timeout is 40 s here and 40 s in the vendor's `longPollHTTPTimeout`, so
that item from the 2026-09-17 comparison was not a divergence. The reason the *reporting* half is in this
decision rather than the code: the old `isChannelActive()` meant "a token was configured at startup", so every
status surface kept saying the channel was fine - silence, not breakage, was the real defect.

**Consequences:** `isChannelActive()` now means "the channel is usable", which is what the panel actually wants to
show; a fake channel without `isTokenExpired` is treated as healthy (the method is optional on the interface). A
re-login does **not** heal a running daemon - it still holds the old token in memory - so OPERATIONS.md says to
restart it, and the log line says so too. `assistant status --json` still derives `channelActive` from the
endpoint file rather than from the live loop, so after an expiry it can disagree with the panel for as long as
the daemon runs: recorded here as a known gap rather than fixed quietly, because publishing live state into that
file is its own change. The live expiry path has never been observed end to end (it needs the server to send
`-14`); what is tested is the classification, the backoff and a driven loop.

## D-049: Conversations become knowledge notes locally, the model call is the only egress, and it is the user's call

**Status:** Active

**Decision:** `chat-notes` turns recent conversations into knowledge cards (`output/chat-notes/<会话>.md`), and
`wiki compile --source ./output/chat-notes` aggregates them into concept pages through the pipeline that already
exists. It is two-phase in the same shape as the rest of the project: `--dry-run` reports how many conversations,
messages and characters would be sent (reading only local data), and nothing leaves the machine without `--yes` -
one model call per conversation, which is the only egress. What it writes is local markdown; it never sends a
message.

**Reason:** the knowledge base had exactly one source - the article line, `output/biz-daily` - while conversations
could be searched but never condensed into anything, which is the half of "a personal WeChat knowledge base" that
was missing. Reusing `compile_wiki`'s input contract (frontmatter + `[[wikilinks]]`, `--source` already supported)
means one concept format, one backlink mechanism, and no second aggregator to keep in step - the three shapes asked
for (per-conversation cards, topic pages, people/timeline pages) fall out of one producer plus the aggregator that
was already there. Two constraints follow from that choice rather than from taste: **the wikilinks are rendered by
our code**, never by the model, because `scan_articles` collects every wikilink in a body and cannot tell who wrote
it - a stray one would invent a concept, so free text from the model has `[[...]]` stripped; and **thin
conversations get no card at all**, because a three-line chat yields a card that says "they said two things" while
costing a model call and adding one contentless source to a concept page.

**Consequences:** running it over a wide window sends a lot of chat text - measured on this machine, 30 days is 37
conversations / 2913 messages / 105,607 characters - so the preview, not a limit, is what the user steers by; the
number is also why this is not offered as an assistant tool or over MCP (a model-initiated call of this size would
be an egress nobody asked for). Cards are rolling snapshots (one file per conversation, rewritten with its recorded
window) while concept pages accumulate: cards are the snapshot, concepts are the ledger. Fabrication is constrained
in the prompt (compiler not author, contradictions stay side by side) rather than by post-hoc verification of every
claim - the project keeps its judgement in a separate layer and a second implementation of "is this claim
supported" was not wanted. Not copied on purpose from the reference implementation this borrowed its phrasing from
(Tencent/WeKnora, read for this work): page revisions. The other half - a check over the produced pages - was recorded
here as a known gap and **closed the same day** by `wiki lint` (`scripts/wiki_lint.py`, surfaced as `weflow-cli wiki
lint`): it reports broken links (a `[[card.md]]` whose card is gone), orphan pages, empty pages and duplicate titles,
locally and with no model calls. One judgement in it is worth keeping: it **separates "a concept the pages link to
that has no page yet" from a break** - on the first real run that was 38 expansion candidates against 0 breaks -
because reporting both as "40 dead links" teaches the reader to ignore the tool. A second judgement came from the
first run too: card-side links count as inbound links, otherwise every page is reported as an orphan (the pages
mostly link forward to concepts that do not exist yet, so page-to-page inlinks are nearly empty by construction).

## D-048: There is no in-process plugin loader; extension is in-repo, and the boundary for outside code is MCP

**Status:** Active

**Decision:** Code that extends this project is added to this repository (fork + PR). Code kept outside it reaches the
project only through MCP or the CLI's `--json` surface. No plugin directory, no adapter registry, no public import
surface (`package.json` has `bin` and nothing else). `docs/EXTENDING.md` is the checklist for the in-repo path and states
this position rather than leaving it to be inferred.

**Reason:** An in-process plugin would hold exactly the reach of the code it joins - the decrypted database path, the
configuration (which contains every API key), the filesystem, and the message channel. The standing rule for this
project is that sending, the assistant, MCP and cloud AI each keep an *explicit* permission boundary; a plugin loader
removes the boundary by construction rather than crossing it, and no amount of manifest-declared "capabilities" repairs
that, because the plugin is not sandboxed. MCP is the alternative that does keep a boundary: it is enumerable
(`capabilities --json` lists what is exposed), scoped (the four tools that send user data to cloud models return a
preview unless the caller passes `confirm: true`), and switchable off without touching this repository. The cost is
accepted knowingly: third-party code cannot add a tool, and anything a plugin loader would have made convenient now
needs a PR. The roadmap keeps a plugin/adapter mechanism as a future item with its own constraint already written down
(extensions must not read configuration, databases or arbitrary files directly, and must be individually disableable) -
so the door is not nailed shut, but nothing about it is implemented, and `docs/EXTENDING.md` lists it under "what does
not exist yet" so a reader cannot mistake the gap for an oversight.

**Consequences:** Adding a capability means editing this source tree, which is why the registries were made declarative
and guarded (`test/tool-registry.test.ts`, `test/config-keys.test.ts`) - a framework that can only be extended by
editing it had better make "did I edit all the places?" answerable. The last unguarded extension point - the CLI's
interactive menu, where a menu entry with no `switch` case does nothing when picked and `runCmd` silently no-ops on a
renamed command - was covered on 2026-09-25 by `test/cli-menu.test.ts`, so every recipe in `docs/EXTENDING.md` now
names the test that catches a missed step. Should a loader be built later, these guards are the parts that would have to
be re-derived for the plugin path rather than deleted.

## Decision Template


```markdown
## D-XXX: Short title

**Status:** Proposed | Active | Superseded

State the decision in one or two sentences.

**Reason:** Explain the constraint, trade-off, and why alternatives were not selected.

**Consequences:** List compatibility, migration, security, or documentation follow-up when relevant.
```

## D-127: 决策模型可以走本机（`judgeProvider=local`），概率默认当作未校准

**Decision.** 新增第四条判断通路 `judgeProvider=local`：`LocalJevClient` 把本机决策模型服务
（`decide.py serve`，APUS Reflex 4B + llama-server 的 `/decide`）当 Jev 用，**不需要 key、不联网**。
`score` 没有连续分，改用 `choice` 在有序档位上选、把选中档位的下标当分数（下游 `score_to_relevance`
本来就是四舍五入取整，语义等价）。

**Reason.** 原生 `api.typesafe.ai` 在本机被地区封锁（D-120），`chat` 与 `openrouter` 都要把文章正文
发给第三方。本机 4B 决策模型在官方 Frozen80 上 **66/80 = 82.50%**、**0.3 s/题**（2026-10-22 本机实测，
RTX 5060 8GB），对"主题 / 相关度 / 该不该收录"这类**窄口径、可验证**的判断够用，而正文不出机器。

**Consequences.**
- 本机服务的概率是**候选集上的相对 softmax、未校准**（APUS 契约原文）。`confidence` 只作参考，
  不能直接当自动放行阈值；要用阈值先在自有样本上标定。
- **一个问题一次 HTTP**：原生 `systemone` 一次答一组，本机 `/decide` 一次只答一个 primitive。
  日报 3 个问题 ⇒ 3 次调用，实测整篇 0.3–0.5 s（本机回环）。
- `score` 用 **n-1 次阈值判断**（`达到「中」或「高」吗？`）合成档位，不是三选一：
  同一批 44 篇（2026-10-16~21，Jev 实判）上，三选一对 Jev 的一致率 45.5%（`低→中` 23 次，
  系统性偏高），阈值问法 **72.7%**、平均差 0.55 → 0.27 档。**措辞影响巨大**（同一模型同一批样本：
  纯判据句 59.1%、带主语 + 档位 + 判据 72.7%），改这句话要重测。
- 服务没起时抛 `JevError`（调用方按既有降级路径退到 LLM 路径），**不静默改用云端**。
- 新增配置键 `judgeLocalUrl`（默认 `http://127.0.0.1:8090`），已登记进 `CliConfig` 与 `config set`。
- **按主题的相关度下限（实测校准）**：211 篇同题对比（2026-10-16~21，Jev 实判）显示本机模型对
  `AI` 类文章的相关度**系统性低估一档**（该类一致率 20.0%，`低→中` 33 次；政治/文学/新闻 90%+）。
  `LocalJevClient.TOPIC_RELEVANCE_FLOOR = {'AI': 1}` 只把 `AI` 的 `低` 抬到 `中`：
  该类 77.5%、总体 73.9% → 84.4%（68 篇复测 80.9%）。统一 +1 只有 82.9% 且会越界，故不采用。
  这张表是**数据产物**：换语料/换模型要重测，`usage.calibration` 会落盘，便于事后核对。
- 收录阈值：Jev 0.5 时收录 32/211；本机同阈值收录 30 篇、判定一致 86.7%；降到 0.35 收录 32 篇、
  一致率不变 —— 要与 Jev 的"收录量"对齐就把阈值降到 0.35，要更保守就留 0.5。


## D-128: 入站图片可以给助手看——它是一类新的出境，门与 D-042 同构

**Status:** Active

**Decision.** Bot 通道收到、且已按 `wechatMediaDownload=true` 落盘解密（D-042 的下载侧）的**图片**，
助手可以用 `look_at_image` 的 `#信N` 句柄查看。门与 D-042 看聊天图片的那道门**同构、双层**：
strict 模式（且非本地推理）下工具拒绝取图、`toApiMessages` 再拦一层；balanced / open 放行；
`IMAGE_SENT` / `IMAGE_HELD` 审计与 `MAX_IMAGES_PER_TURN` 预算**共用**，不分来源。语音、视频、
文件的消费**不在本决策内**——各自需要转写 / 压缩 / 解析，是另外的出境类别，不搭车。

**Reason.** 图片消息此前整条被忽略（日志里的「忽略非文本消息 (image)」），而文件其实已经在
本机磁盘上、下载本身已是显式 opt-in（`wechatMediaDownload` 默认 false）。但 D-042 立过的规矩
仍然成立：「一句关于聊天的话」与「聊天里的那张照片」不是同一种披露——入站图片是**别人发给
用户的照片**，比聊天截图又多一层：出境的内容甚至不是用户自己写的。所以决策先行、范围收窄：
仅图片、仅 balanced / open（strict + 本地推理等于不出机器，照放）、句柄只在能看时展示
（与 `get_messages` 的 `#N` 同一规则——不摆出一个工具必定会拒绝的东西）。

**Consequences.**
- 一条**只有图片**的来信从此会产生一轮（此前被忽略）：文本占位「（发来一张图片）」，白名单、
  配额、串行队列照常走；未开下载或下载失败（`filePath` 为空）的图片消息**仍被忽略**，行为不变。
- `#信N` 只指本轮来信自带的图片；`#N` 仍指聊天记录里的 `local_id`。两个句柄空间不混——
  前者是磁盘上现成的文件，后者要跑 `read_image.py` 建媒体索引，成本差一个数量级。
- 图片**不缩放**，超过 4 MB 直接说「太大」：入站文件是证据形态，缩了再给模型看，模型说的
  就不是那张图（导出路径做缩放是因为那是**给人看**的页面，两者方向相反）。
- MIME 从文件头魔数判定（png / jpeg / webp），不信任文件名扩展名——文件名来自服务端报文。
- MCP 面不变：`look_at_image` 本来就不在 MCP 表里（文本传输接不到侧信道的图）。
- 审计 `IMAGE_SENT` 的 `ids=` 对入站图片为空（没有 local_id）——`n=` 照记，能对上"这轮发了几张"。


## D-129: qchat-cli 的消息从**文件契约**进来，不做进程耦合，也不当第五个消息后端

**Status:** Active

**Decision.** `weflow-cli import qchat <文件|目录>` 读 qchat-cli `qce export --format json`
的产物，归一化落到本地私有存储 `output/imports/qq/sessions/`（schema `weflow-import/qq-v1`，
字段语义与 `weflow-message/v1` 对齐：serverId/createTime/parsedContent/rawContent 与 13 个稳定
messageType）。下游两条已落地的管道各开一个**显式开关**：`chat-notes --source qq|all` 产 QQ
知识卡（卡片名带 `QQ-` 前缀，与微信卡同一形状、同一消费端），`search-index --include-imports`
把 QQ 会话收进语义索引（记录 id 带 `chat:qq:` 段，与微信记录天然不撞；短语索引从语义索引
派生，自动跟上）。默认行为逐字不变。

**Reason.** 两个仓库的数据获取层性质不同（weflow 直读本地加密库、离线可查；qchat 走
NapCatQQ/OneBot 在线服务）。让 weflow 直连 OneBot 当第五个 `chatService` 后端，等于把 qchat
的整个客户端层复刻进 weflow、两个工具抢同一个轮询——且 `chatService` 的四个后端全是"解本地
库"的，混进一个在线 API 客户端会改变这层的性质。文件契约让边界可枚举、可版本化，也正是
ROADMAP P1 给 she-love-me 预留的同一条路；qchat-cli 因此**一行不用改**。

**Consequences.**
- QQ 的 `messageId` 是服务端全局唯一：去重/合并按它做，**不需要**微信侧那套重叠时间窗；
  对 D-027 而言 qq 来源天然满足 stable-identity 判据。
- `isSend` 只在导入时给了 `--own-id` 才写；缺省就是未知，消费方按未知处理（知识卡的行
  标签落到群名片/昵称）。不为"好看"猜方向。
- 存储是 **weflow 的私有形状**，不承诺稳定；对外承诺的边界是 `weflow-message/v1` 那套
  字段语义。将来 qchat 侧产出中立契约（如 `im-message/v1`）时，读取器多认一个 schema 即可。
- 查询层（`sessions`/`messages` 按 QQ 会话直查、per-source 路由）**明确不在本决策内**：
  等真实需求出现再做，届时 QQ 号与 wxid 的命名空间隔离（`qq:` 前缀已备好）是第一件事。
- 隐私边界对齐：导入的是用户本人授权的 QQ 数据（qchat 侧靠 OneBot 授权），落盘后继承
  weflow 的纪律——QQ 号与 wxid 同级敏感；知识卡与索引沿用各自既有的出境确认门。
- `--source all` 在微信库不可用时**降级**为只处理 QQ（说一声，不是静默）；`build
  --include-imports` 在没有微信库的机器上也能建索引（纯 QQ 用户是真实场景）。

## D-130: 验证过的微信版本有单一事实源，check 主动比对，发布必须记录

**Status:** Active

**Decision.** 本包"最后用真实数据验证过的微信版本"只写在 `src/services/wechatCompat.ts` 的
`WECHAT_VERIFIED` 一处（版本 + 日期 + 验证方式）。三条接线全部只读它：`capabilities --json`
的 `compatibility.wechat`（机器可读）、`check`（含 `--json` / `--report` / 人类输出）拿本机
**实际加载**的版本与之比对给出四态判定（verified / newer-than-verified / older-than-verified /
unknown——"微信没在跑"是 unknown，不是"没问题"）、`docs/RELEASING.md` 的发布清单要求每个
CHANGELOG 版本段记录验证版本（不涉及微信数据读取的版本写明"本版不涉及"）。

**Reason.** 微信 4.x 几周一更，而"这版验证过哪个微信"原来只活在 PROJECT_STATE 的一段散文里：
CHANGELOG 不记、机器读不到。用户升级微信后读不出数据时，第一个该被回答的问题（"你的微信
比我们验证过的新吗"）没有任何面能回答——issue 里的反复沟通就是这么来的。check 本来就在探测
实际加载的版本（D-107/D-125），差的只是把两边的数字放到一起说一句话。

**Consequences.**
- 更新 `WECHAT_VERIFIED` 的门槛是**一次真实数据验证**（不只是"进程起来了"），验证日期与
  读了什么写进 `verifiedHow`；没验证不许改——这正是常量存在的意义。
- `compareWechatVersions` 是判定的唯一实现（纯函数：四段数字逐段比、缺段补零、形状不猜），
  测试钉住四种判定与边界。
- `newer-than-verified` 在 `check --report` 里升为 warning（它就是"读不出数据"的第一嫌疑人），
  人类输出与 report 的措辞共用 `describeCompatVerdict` / 渲染器里的一份，不各写一套。
- 判定是**对"本机此刻"的陈述**，不是兼容性承诺：说"verified"只是"这个组合验证过"，
  "older"一般兼容但没有保证——措辞已按此收敛。
## D-131: PDF 走本机 OCR 再出境；卡片形状与其它四条线一致

**Status:** Active

**Decision.** 新增 `pdf-notes` 命令与 `scripts/pdf_notes.py`：**PDF 本身不出境**，先在本机用
Unlimited-OCR-2（`ocr2/`，默认 `--ocr-root D:\github_project\ZhaoWen_GitHub维护\ocr2`）转成
Markdown；**只有 OCR 后的文字**（截断到 3000 字，与文章线同量级）发给 DeepSeek，一次调用同时问出
`summary` 与 `concepts`。产出的卡是 `type: pdf-card`，形状与 `article-card` 同款（frontmatter +
`## AI 摘要` + `## 概念`），`wiki compile --source output/pdf-notes` 直接能吃。OCR 引擎不可用时
**如实报错**并提示 `--ocr none`（走 PDF 内嵌文本），不静默降级。

**Reason.** 其它四条线（文章/聊天/收藏/自己的笔记）的材料**已经是文字**，出境的只有文字；PDF 多
一步"变文字"。若把 PDF 直接丢给云端视觉模型，**整份文档出境**——与本项目"只发必要内容"的边界
冲突，而本机已经有能读几百页 PDF 的 OCR 引擎，正好补上这条边界。卡片的形状必须一致，否则
`compile_wiki` 要为 PDF 单独开一条分支，而它现在对四条线一视同仁。

**Consequences.**
- 摘要是**模型写的**（PDF 没有"原笔记"可抄），所以 frontmatter 标 `summary_by: model`，
  与收藏线一致；`text_via` 记下正文来自 `local-ocr` 还是 `embedded-text`。
- OCR 引擎是**外部依赖**：`--ocr-root` 指向它，缺目录/权重时命令失败并说清怎么修，不假装能跑。
- 多页 OCR 必须用 gundam 切块（`ocr2_pdf.py` 传 `crop_mode=True`）：base 模式把整页压成
  1024×1024，A4 小字会糊——实测只读出每页第一行。这是 OCR 脚本内部的默认差异，写在这里免得
  下次重新踩。
- 一次模型调用同时问摘要与概念（收藏线的形状），**不**分两次问：多一次调用就多一次编造机会，
  而摘要能从概念里得到骨架。
- 卡片目录沿用 `output/*-notes` 通配（`output/pdf-notes`），体检与聚合不用改。
**追加（2026-10-27，同日）：OCR 结果要缓存，且页面图片不能跨 PDF 复用。**
两件事都是**实测撞出来的**，不是设计时想到的：

1. **缓存**。OCR 按页线性增长（一页约 1.5 秒），而模型调用是固定几秒——实测同一份 PDF：
   首次 30.9 s、复用 2.3 s（**13 倍**）。书越大差越多，所以"换提示词重出卡""某篇调用失败
   重跑"这两件常事不该把识别重做一遍。缓存键含 **PDF 绝对路径 + dpi + window + pages**
   （参数变了算另一份，否则换了 dpi 却读到旧文本），判据是**缓存文件不早于 PDF**
   （重新导出过的 PDF 会重做）。`--no-reuse-ocr` 关掉复用。
   缓存目录名必须用 `hashlib`：原来写的是内置 `hash(str)`，而 Python 对 `str` 的哈希
   **每进程都不同**（`PYTHONHASHSEED`），于是每次运行都新建目录——既留垃圾，缓存也永远不命中。
2. **页面图片会跨 PDF 串**。`ocr2_pdf.py` 原来把页面 PNG 固定放 `ocr2/pages/`，且
   `if not p.exists()` 跳过渲染；第二个 PDF 于是**静默复用第一个 PDF 的页面图**——
   输出的文字是 A 的、文件名却写着 B，不报错、看结果也不一定看出来。现在默认用每次唯一的
   临时目录，`--keep-pages` 时放在**自己的输出目录**下并要求图片比 PDF 新。
   这类"错的输入、对的格式"只能靠对比两次运行才发现，所以缓存那条测试顺带钉住了它。
**追加（2026-10-27，同日）：`--ocr-only` 把"识别"与"出卡"拆成两步。**
两者代价极不对称：识别按页线性增长（一页约 1.5 秒，300 页七八分钟）且**不出网**，出卡是固定几秒
但要出境。拆开之后，几百页的书可以**挂着过夜只跑识别**（零花费、零出境、不需要 `--yes` 与 API key），
白天再出卡；识别结果同时落进缓存，重出卡不会重做识别。文字写到 `output/pdf-text/`，与卡片目录
**分开**——纯文本没有 `[[概念]]`，混进 `output/*-notes` 会让 `compile_wiki` 把它当成一份坏卡扫。
`--ocr-only` 的确认门是关闭的：它不发任何东西出去，弹确认只会让人以为要出境。
## D-132: PDF 文字进语义索引，归文章线，按页切块

**Status:** Active

**Decision.** `search-index` 默认收 `output/pdf-text/`（`pdf-notes --ocr-only` 的产物）：
按 `### 第 N 页` 切页、按 ~1200 字合并成块，每块一条记录（`id = pdf:<文件名>#<块号>`，
`type = 'pdf'`）。`LINE_TYPES['wiki']` 因此从 `('article',)` 改成 `('article', 'pdf')`。
`--pdf-chunks N` 给"书太大先试水"留闸门，`--no-pdf` 整条关掉。

**Reason.** 索引此前只覆盖公众号文章与聊天，**用户手上的 PDF 完全搜不到**——而 PDF 恰恰是
"存了就再也找不着"的那类材料。补上之后 `search` 能把合同条款和公众号文章一次排出来
（实测：问"逾期付款要赔多少违约金"，`pdf:采购合同#0` 与三篇文章同列）。

**Consequences.**
- **按页切块、页标记留在块正文里**：一本书十几万字，截 500 字进向量等于只剩开头；
  一页一条又太贵（300 页 = 300 次 embedding，每次批量仅 10 条）。合并到 ~1200 字后，
  命中还能从正文里的 `### 第 N 页` 说清在第几页。一页本身超上限时不拆（拆了页码对不上）。
- **`pdf` 归文章线而不是新开一条线**：它和"读过的材料"是同一类，用户按内容找它；
  聊天线是"谁说过什么"。不加进白名单的后果实测过——PDF 明明进了索引，`search` 一条都不返回，
  而且不报错（白名单静默过滤）。
- `load_record_text` 认 `pdf:` 前缀，直接返回命中那一块（不读整本），`sourceFile` 指回
  `output/pdf-text/<名>.md`。
- 测试会把真实的 `output/pdf-text` 读进来：`semantic_rerank_test` 里那条"窗口传没传到"的用例
  必须同时打桩 `collect_pdf_text`，否则本机只要有一份 PDF 文字，它就会走进向量层并红掉
  （2026-10-27 实际发生）。
**追加（2026-10-27，同日）：`--ocr auto` 本地优先、本地不在才上云端。**
加了云端 OCR（阿里百炼 `qwen3.5-ocr`，复用 `dashscopeApiKey`；实测同一张 1600px 图：
`qwen3.5-ocr` 0.8s / `qwen-vl-ocr` 0.8s / `qwen3-vl-plus` 1.8s，三个都读对了）。
**选路是用户明确要的顺序**：`auto` = 本机引擎在就走本地 → 不在就用云端 → 都没有退回 PDF 内嵌文本。
`--ocr local|cloud|none` 强制指定时**不偷偷换路**（做不到就报错）——静默换路会让"我以为没上传、
其实页面图片出境了"这种事发生，那正是隐私边界最不能含糊的一处。

**代价对比（实测）**：本地 1.5 s/页且占 6.9 GB 显存；云端 0.8 s/页、约 ¥0.002/页（200dpi A4 约
4000–5000 input tokens，按 ¥0.43/M 计），不占显存但**页面图片出境**。所以对"偶尔读几份 PDF"的机器，
云端是更划算的默认；本地留给"文档多、要批量、或不能出境"。

实现上三件事值得记：
- **按页调用**，不是整份文档一次。DashScope 对话接口一次一张图，而且单页失败只影响那一页
  （本地那套是整份文档一个进程，中途挂了全丢）。
- **缓存键含模型**：换模型就是另一份文字，不能拿旧模型的输出冒充新的。
- **`read_text_by_source` 一处分发**，两个调用点（`--ocr-only` 与出卡）共用。抄两遍 if/else 的
  症状是"一个走云端、另一个还走本地"，而两条路都能跑通、不报错。
- `sendsImages` / `ocrSource` 出现在 `--dry-run --json` 与结果里：`auto` 的结果必须报出来，
  否则用户会以为一直没上传。
**追加（2026-10-27，同日）：PDF 卡也有了六类主题，不再一律 `文档`。**
原来所有 PDF 卡写死 `topic: 文档`，而 `compile_wiki` 的「来源」栏是 `来源 · 主题`——
于是 PDF 和文章永远归不进同一个主题分组。现在提示词顺手问一个 `topic`（取值就是
`_utils.TOPICS` 那六类，与文章线同一份），模型给不出合法值就退到 `文档`（它是"未分类"，
不是第七类）。实测一份 KVMem 技术白皮书被正确判成 `AI`。

**为什么不在本地硬分：** 主题靠的是"这份文档在讲什么"，不是文件名或路径能猜出来的；
而多问一个字段不增加调用次数（还是那一次 `summary + concepts`）。`topic` 不合法时**只丢主题、
不丢卡**——摘要与概念才是卡的主体，为一个分类词把整张卡废掉不值。
**追加（2026-10-27，同日）：`--from-chat` 收聊天里别人发过的 PDF。**
用户要的场景是"别人在微信里发给我一份 PDF"。数据源不是扫 `msg/` 目录，而是微信自己的
`hardlink.db`（`file` 表：`md5 → (dir1, dir2, file_name)`）——转发的副本、被改过名的也认，
扫目录会漏。本机实测：**1,978 条文件记录 / 279 个 .pdf / 199 个本地还在**（80 个已被微信清理）。
排序按文件 mtime 倒序：hardlink 表里没有"收到时间"，mtime 是能拿到的最接近代理
（**已知限制**：把聊天里的 PDF 和"谁发的、什么时候"对起来要另外join appmsg 消息，这一版没做）。

同时修掉一个落点错误：云端 OCR 的缓存原来写在 `output/pdf-notes/.cache/cloud-ocr/`，
而 `compile_wiki --source output/pdf-notes` 会把那个目录下的 `.md` 当卡片扫——缓存就成了
一张"没有概念的坏卡"。现在统一放 `output/.cache/`（`CACHE_ROOT`，与卡片、文字两个目录都不重叠）。

**实测对比（同一份 5 页招聘简章）**：本地 OCR 270 s（54 s/页，crop 切块在 8 GB 卡上很慢）、
云端 6.1 s（1.2 s/页），**44 倍**，且云端多读出 608 字（第 1 页的二维码联系方式只有它读到了）。
`auto` 仍是本地优先（用户定的顺序），但这条数据说明：**批量处理聊天 PDF 时 `--ocr cloud` 更实际**。
**反转（2026-10-27 晚，用户决定）：`auto` 改成云端优先、本地兜底。**
同一天早些时候按"有本地就本地"实现，几小时后用真实的聊天 PDF 一跑就反了——依据是
**同一份 5 页招聘简章**：本地 270 s（54 s/页，8 GB 卡上 crop 切块把显存吃满），
云端 6.1 s（1.2 s/页），**44 倍**；而且云端多读出 608 字（第 1 页二维码旁的联系方式只有它读到）。
成本约 ¥0.002/页，199 个聊天 PDF 全跑一遍是几毛钱。

所以最终顺序是 **cloud → local → 内嵌文本**，本地降级为"没有 key 时的后备"。
`--ocr local` 仍可强制（不出网、0 成本、要批量且不在乎时间的场景）。
`--ocr local|cloud|none` 强制时不换路的纪律不变。

**这次反转本身值得记下来**：顺序该由**实测量出来的代价**定，不由"本地更私有"这种先验定。
先验在这里给错了方向——本地确实不出网，但 8 GB 卡上的 54 s/页让它实际上不可用于批量。
**追加（2026-10-27 深夜）：出卡时也写 `pdf-text` 文字副产物。**
原先只有 `--ocr-only` 落 `output/pdf-text`，卡片模式不落。后果实测：跑完 **177 张卡**去
`search`，**一条 PDF 都搜不到**——因为 `search-index` 收的正是那个目录。文字本来就在内存
（OCR 缓存也有），多写一个文件是零成本，于是两处都写（`write_text_sidecar` 一处分发）。

补建索引后：23,899 条（新增 2,589 个 PDF 块），`search` 能把 PDF 与文章混排，**且带页码**
（`pdf:ESE-D-26-01401R1_revised#24` → 「第 25 页」）。

**这一批 177 张卡的规模与代价**：3,371 页 / 400 万字，OCR 约 ¥5.1 + embedding 约 ¥1.5，
耗时 1h43m（含一本 537 页的书）。主题分布最终是 **文档 99 / 学术 70 / AI 7 / 政治 1**——
聊天里的 PDF 大多是通知、名单、简章、发票，它们**本来就不属于那六个公众号主题**；
把「学术」当兜底（第一版提示词）会得到 145 张"学术"的假分类，加了「其他」出口才诚实地
落到「文档」。**六类主题是为公众号文章设计的，PDF 需要自己的分类法**（合同/简历/论文/通知/票据）——
这是下一步，不是这一版的缺陷。
**撤回（2026-10-27 深夜，用户决定）：PDF 不再参与那六类主题，统一 `topic: 文档`。**
上一版让模型给 PDF 也判 AI/学术/新闻/文学/投资/政治，理由是"概念页的「来源 · 主题」能对齐"。
但实测 177 张聊天 PDF 说明这条路是错的：**用户的原话是"那是公众号的分类"**——
六类是为了给公众号文章分流设计的，而聊天里的 PDF 是通知、名单、简章、发票、报销单、论文稿，
硬套只会产出假分类：

| 提示词版本 | 结果 |
| --- | --- |
| "判断不了就用学术" | 145/175 判成「学术」（志愿者招募、发票、备考手册全在内） |
| 加「其他」出口 | 99/175 落到「其他」 |

两版都不比"统一写文档"更有信息量，却都要多花判断成本、还引入一个假的区分。
所以：提示词**不问主题**，`parse_note` 就算收到 `topic` 也不采纳，卡片固定 `topic: 文档`；
已有的 177 张卡本地改回统一值（只改 frontmatter，不重新调模型）。`compile_wiki` 照常消费
（概念页「来源」栏写"文档"，如实）。

## D-133: 面板 CLI 的场景覆盖只作用于一轮

**Status:** Active

**Decision.** `panel --ask --scene <id>` selects an existing, enabled scene for that CLI turn only. The panel API validates it before dispatch; the service uses it to build the prompt without changing binding or last-used state. Without the option, binding → keyword → last-used resolution is unchanged, including for WeChat-origin turns. The desktop panel UI and status payload remain unchanged.

**Reason.** A one-off task may need a different prompt without permanently changing how a conversation is handled. Reusing the authenticated loopback endpoint and serialized assistant queue preserves the shared quota and memory path, while an explicit per-turn field avoids mutating conversation configuration.

**Consequences.**
- Unknown, disabled, or malformed scene ids fail before a turn is queued.
- The trace labels the selected scene `本轮指定`; the next turn uses normal resolution.
- Regression coverage pins prompt selection, non-mutation, API validation, and CLI forwarding.

## D-134: Document distinct local secret and database cache boundaries

**Status:** Active

**Decision.** Documentation distinguishes AES-GCM secret fields in `config.json` from the separate `keys.enc` cache and the optional plaintext SQLite database cache. The config key derives from a salt and machine/account names and is not protected by an OS secret store. The key-cache fallback is reversible XOR with its key stored beside the data. `WEFLOW_CACHE_ENABLED=1` enables a cache containing plaintext SQLite data. Local processing is not described as eliminating local compromise, account, legal, or platform-compatibility risks.

**Reason.** Calling every local key cache "machine-bound AES-256-GCM" overstated protection and ignored the plaintext cache. Accurate, bounded claims help users understand the storage boundary without publishing process-memory or key-extraction details.

**Consequences.** `SECURITY.md`, the README summary, and [PROJECT_STATE.md](PROJECT_STATE.md) distinguish these protections and residual risks.

## D-135: Official-account email delivery is incremental

**Status:** Active

**Decision.** The noon and end-of-day mail jobs pass `--incremental`. `daily_mail.py` filters article records using SHA-256 fingerprints of canonical URLs (falling back to source/title/time when no URL exists), and atomically records only articles actually rendered in a successfully accepted SMTP send in that date's `.mail-sent.json`. Common WeChat tracking parameters are excluded from URL identity. Failed SMTP sends and dry-runs do not advance state (incremental dry-run still reads the ledger so it previews the true delta); absent or malformed state fails open to a full send rather than silently suppressing content. Items omitted by compact-topic display limits are not marked sent. Manual full resend remains available by invoking the script without `--incremental`.

**Reason.** The noon and end-of-day jobs read the same cumulative `.articles.json`, so the later email repeated content from the first. A date-local delivery ledger makes the second send a true delta while retaining retry behavior and not mistaking fetched-but-not-mailed articles for delivered content.

**Consequences.** The state file contains hashes only, not article titles, URLs, bodies, or recipient details. SMTP success precedes the atomic ledger update; a crash between those two operations can cause a retry duplicate, which is preferred to losing mail. `daily_mail_test.py` covers identity normalization, failure and retry boundaries, malformed state, privacy, and display-limit coverage.

## D-136: Daily reports run at 13:00 and 23:30 with logon catch-up

**Status:** Active

**Decision.** Keep the 13:00 noon report, move the night task to 23:30 and process the current day, and add a logon-only catch-up task for yesterday. The three modes use separate completion markers so catch-up cannot suppress the current day's night run. Noon, night, and catch-up share `.mail-sent.json`, so each email contains only articles not previously sent successfully for that date.

**Reason.** The user requested runs at 13:00 and 23:30. Keeping the old "process yesterday" semantics at 23:30 would send the wrong date; removing logon recovery would leave posts after 23:30 and missed sleep-time runs unhandled. Separate same-day night and yesterday catch-up paths preserve both meanings.

**Consequences.** The night email covers new articles collected by 23:30. Articles arriving in the final half-hour are appended to yesterday's delivery delta by the next logon catch-up. If there is no further logon, those articles remain in the local daily output until a later catch-up. `scripts/install_tasks.ps1` registers the noon, night, and catch-up tasks.

## D-137: Third-party tools arrive as out-of-process MCP plugins, not as a loader

**Status:** Active

**Decision.** A plugin is a directory with `weflow-plugin.json` and a command that starts a **standard MCP server over stdio**. WeFlow spawns it, lists its tools, and re-exports them as `plugin.<id>.<tool>` alongside the existing `wechat.*` tools. The registry lives at `~/.weflow-cli/plugins.json` (`WEFLOW_PLUGIN_REGISTRY` overrides it for tests). Management is `weflow-cli plugin add|list|remove|enable|disable|trust|untrust|doctor`.

Three boundaries are enforced by the host, not by trusting the manifest:

1. **Out of process.** The plugin receives no WeFlow object, database path, or configuration value; the only channel is MCP over stdio.
2. **No inherited environment.** Child processes get a rebuilt whitelist (`childEnv()`), not `process.env` — which would otherwise hand over every API key.
3. **Confirmation by default.** An untrusted plugin's tools return a preview unless the caller passes `confirm: true`; `confirm` is stripped before forwarding. Disabled plugins do not run even with `confirm`.

A handshake timeout (20 s, `WEFLOW_PLUGIN_CONNECT_TIMEOUT_MS`) is part of the decision, because it is the difference between "one plugin is broken" and "the tool table never answers".

**Reason.** D-048 rejected an in-process loader because such a plugin would hold exactly the reach of the code it joins. This keeps that rejection intact while making the door it left ajar usable: everything a plugin can reach is what the host chooses to hand it over one pipe. D-056 (skills as material) was the smallest useful form of the roadmap's P4 item; this is the first form that adds a *capability* without editing this repository, which is what the roadmap's acceptance criteria demanded — no direct access to configuration, databases, or arbitrary files, and individually disableable.

**Consequences.** `permissions.network` / `permissions.writes` are **declarations for display, not enforcement**, and `docs/PLUGIN.md` says so explicitly rather than implying a sandbox we do not have — repeating the mistake D-048 criticised would be worse than not shipping the field. A plugin still runs as an ordinary process under the user's identity; `plugin trust` is a human decision, not an assessment. A plugin that fails to start or never completes the handshake is skipped (its tools simply do not appear) while other plugins and the built-in tools keep working — `test/plugin.test.ts` pins that isolation and the timeout. With no plugins registered the served tool table is byte-for-byte what it was before, so the defaults of every existing client are unchanged. Still absent, and named in `docs/PLUGIN.md` rather than left to be discovered: a central registry and a compatibility matrix beyond the `schema` version. (The knowledge-source kind this decision listed as absent was added by D-138.)

## D-138: A knowledge-source plugin is validated by the real consumer, not by a second copy of the rules

**Status:** Active

**Decision. D-137 gains a second plugin kind.** `kind: "knowledge-source"` names a command that receives a job as JSON **on stdin** (`out_dir`, optional `input_dir`/`since`/`until`), writes `.md` cards into `out_dir`, and exits. `weflow-cli plugin run <id>` runs it, then hands the produced cards to the **actual consumer** — `scripts/plugin_card_lint.py` imports `compile_wiki` and calls its own `parse_frontmatter` / `concept_links` / `article_topic` — and reports how many cards the consumer could read and why the rest were skipped. Knowledge-source plugins are deliberately excluded from `listPluginTools` (they have no tools) and from `doctor`'s connection check (they are not run there, because `doctor` is a read-only check).

**Reason.** The card contract already exists (`docs/EXTENDING.md` Recipe F, four producers), and its most expensive failure mode is silent: `scan_articles` skips any card whose body has no `[[wikilink]]` — the file exists, the directory exists, nothing errors, and the concept page simply never appears. A plugin author debugging that by eye will look at a file that looks correct. Two things follow and both are enforced here rather than documented: the job travels on **stdin** (a command line is visible in the process list and may carry dates or paths), and the validation is performed by **the consumer's own code** rather than re-implemented in TypeScript — a second implementation would be a second contract that stays green when the Python side changes.

**Consequences.** A knowledge-source plugin cannot write anywhere except the `out_dir` the host creates and passes in, and it never learns a knowledge-base path or a configuration value — the same boundary D-137 draws for tools, expressed as a directory instead of a pipe. The host does **not** chain `compile_wiki` after `run`: building concept pages stays a separate, per-line command on purpose (the two knowledge lines are kept apart), so `plugin run` reports readability and stops there. `test/plugin.test.ts` covers the exclusion from the tool table, the stdin job, the failure paths (missing binary, wrong kind), and that a failed lint is reported as a failure rather than as "0 cards". `plugin_card_lint.py` is subject to `test/script_stdout_encoding_test.py` like every other script.

## D-139: A low-volume day is reported, and the session table is used to tell "not delivered" from "not read"

**Status:** Active

**Decision.** When a day's article count falls below 30% of the median of the previous seven days (median ≥ 20, at least 3 days of history), `biz_daily.py` records `lowVolume: {today, median, ratio, sample, sessionToday, sessionPrevDay}` in `.articles.json`, prints a warning, and `daily_mail.py` puts the same sentence **at the top of the email**. `sessionToday` comes from a second, independent source: the client's own `SessionTable` (`session_activity()` reads `last_timestamp` for `gh_%` accounts), so the report can distinguish "the pushes never reached this machine" from "we failed to read them".

**Reason.** On 2026-10-29 the daily report quietly delivered 6 articles while the user could see roughly 200 in WeChat's 订阅号 view. Three independent local layers agreed there was nothing more to read: the message tables held 29 rows for that day (248 the day before), `SessionTable` had **14** 公众号 sessions updated today against **148** yesterday, and the article the user pasted was absent from every `.db` under the account directory — verified with zstd decompression and a positive control that found two other articles through the identical path. So WeChat 4.2 renders 订阅号 content from the network and does not persist those pushes; re-login, opening the 订阅号 folder, and favouriting an article (which *does* write `favorite.db`) all left `biz_message_0.db` untouched at its 22:38 mtime. **No reader change can recover content that never reached storage**, and the expensive part of that day was not the missing data — it was that nothing said so. Before this change the only signal was a small number inside an email that looked normal.

**Consequences.** The failure is now loud in three places (log, `.articles.json`, email header) and the session count makes the diagnosis one line instead of an afternoon. Thresholds are 30% / median ≥ 20 / 3 days deliberately: measured daily volume ranges 84–526, so a looser threshold would fire on genuinely quiet days and the warning would stop being read. `sessionToday` is only meaningful for the day just ended (a session's `last_timestamp` is its *latest* message, so older days look small by construction) — the field is therefore compared against the previous day only, and the docs say so rather than inviting a wrong reading. What this does **not** do: recover the missing articles, or fetch 订阅号 content over the network. If the client is not persisting pushes, the report for that day is structurally short, and the honest behaviour is to say so.

## D-140: WeChat 4.2.0.11 stopped writing official-account pushes into `biz_message_0.db` at the moment it was installed

**Status:** Active — observed on one machine, cause not yet confirmed by the vendor

**Finding.** On the development machine, 公众号 push rows in `biz_message_0.db` collapsed **at 13:16 on 2026-10-28**, the minute `D:\WeiXin\Weixin.exe` was replaced (file mtime 13:16:47, version 4.2.0.11; the repo verifies against 4.1.15.56 per D-130). Splitting each day at 13:16 makes the break unambiguous, and the three preceding days act as the control — for them the afternoon half is *larger* than the morning half, so the ratio, not the clock, is what changed:

| day | rows before 13:16 | rows after 13:16 |
| --- | --- | --- |
| 2026-10-25 | 191 | 231 |
| 2026-10-26 | 251 | 295 |
| 2026-10-27 | 237 | 297 |
| **2026-10-28** | **240** | **30** |
| 2026-10-29 | 14 | 15 |

Three independent local records agree that the content never arrived, so this is not a reader defect: message rows (29 for 10-29 against 270 the day before), `SessionTable` (14 公众号 sessions with a last message that day against 148), and `SessionUnreadListTable_1` (13 entries against 73). An article the user could see in the 订阅号 view was searched for across every `.db` under the account directory — with zstd decompression and a positive control that found two other articles through the identical path — and it was **absent**. Re-login, opening the 订阅号 folder, and favouriting an article all left `biz_message_0.db` at its pre-existing mtime; favouriting did write `favorite.db`, which shows the client can still persist when asked.

**Reason for recording it rather than treating it as "a quiet day".** `WECHAT_VERIFIED` exists precisely so that "newer than verified" is a warning instead of a surprise, and this is the first measured case where newer-than-verified came with a real data loss. It also cost hours to diagnose, because a short report and a genuinely quiet day look identical from the outside.

**Consequences.** No public 4.2.0 release note mentions 公众号 or 订阅号 — the official text is "修复一些已知问题", and the reported changes are text-selection translation, the emoji panel, and call-window behaviour ([IT之家](https://www.ithome.com/1/018/634.htm)). So the mechanism change is **undocumented**; this entry records the measurement, not a vendor explanation. The practical guidance is therefore: if 公众号 volume collapses right after a client update, **verify against the previous client build before blaming the reader**, and treat the daily numbers as suspect until that is settled. D-139's low-volume warning plus the session cross-check is what makes this visible within one report instead of an afternoon. Still open: whether downgrading restores delivery (the decisive experiment), and whether the break is 4.2.0.11-specific or 4.2-general — the user reports 4.2 was already running the previous evening, which is consistent with the 10-28 afternoon already being broken.

## D-141: The 4.x 订阅号 view is web-rendered, and the embedded browser's history is a second article source

**Status:** Active — opt-in, `biz_daily.py --browser-history`

**Finding.** 微信 4.x renders 订阅号 in an embedded Chromium (`%APPDATA%\Tencent\xwechat\radium\web\profiles\multitab_*\History`, a plain SQLite `urls(url, title, last_visit_time)` table). An article the user opened is recorded **there** and nowhere in WeChat's `.db` files: the article quoted on 2026-10-29 was found in `History` as `10-29 23:57 | 新西兰大选在即，"谁上台都绕不开中国" | https://mp.weixin.qq.com/s/slTht3jANnQkNuqYYLUoLQ` while being absent from every `.db` under the account directory (zstd-decompressed search with a positive control). This also answers the "do I have to click for the database to update?" question: clicking does leave a record — just not in the database WeFlow was reading.

**Decision.** `browser_history_articles(date_str, tz)` reads those profiles **read-only**, keeps only `mp.weixin.qq.com` URLs whose last visit falls inside the requested day, and returns them in the same shape as push articles. It is wired to an **opt-in** flag, `--browser-history`, rather than on by default: it is a different kind of data (what *you opened*, not what was *pushed*), and pulling someone's browsing history into a daily report without asking is not a default anyone should have to discover.

**Reason.** After the 4.2 change the push path can silently under-report (D-140), and the only local record of the missing articles was this history. On 2026-10-29 it recovered **9** opened articles that the message tables never contained. Without it, the honest options were "say the day is short" or "change the client version"; with it, part of the gap is fillable from data already on the machine.

**Consequences.** The flag is documented as *supplementary*: history holds opened articles only (4–19/day measured), so it narrows the gap and cannot replace pushes. Reading is confined to `%APPDATA%\Tencent\xwechat\radium\web\profiles\*` and to `mp.weixin.qq.com` rows; the other browsing records in that same database are not touched, and `test/daily_articles_merge_test.py` pins both the "other domains excluded" and "other days excluded" filters, plus a quiet-empty return when the directory is absent. Chromium's HTTP cache was checked first and is not usable — 227 MB of compressed responses yielded **zero** article ids, so the history database is the only plaintext source here. Nothing in this path contacts the network; fetching article bodies still goes through the existing cached fetcher.

**A rejected hypothesis, recorded because it nearly shipped.** The same profile carries `Share Data`, whose `share_data_table(url, real_url, author, share_data)` holds article metadata — 3934 rows, of which **2028 were absent from `History`**, which looked like "the feed was rendered but not opened" and would have been a complete push list. That inference was wrong: `History` only goes back to **2026-08-01** on this machine, so older rows cannot appear in it. Once the articles were dated by fetching their pages (`ori_create_time` — the pages do expose it), the correction was unambiguous: of 413 dated articles, 209 were published after 2026-08-01, and **only 2 of those were never opened**. `share_data_table` is a metadata cache for articles that *were* opened (possibly on another device), not a record of what was pushed, and it has no timestamp of its own. `--feed-snapshot` and `--feed-dated` were written, tested against this table, and then **removed** once the measurement disproved their premise; keeping them would have meant shipping a feature whose stated purpose was false. The reusable part — `publish_time_from_html()` — went with them, since its only consumer was the disproven path.

## D-142: The 4.x 订阅号 view is a WMPF applet, and its store is `radium/users/<hash>/applet/data/8/applet.db`

**Status:** Corrected 2026-10-30 — the file is the WMPF **applet registry**, not the feed; the feed is not stored locally at all

**Finding (the measurement — still valid).** A whole-profile differential was taken while the 订阅号 view was opened and scrolled (33,346 files snapshotted before and after, 90 s apart). Exactly one new writer appeared that is not the browser cache: `%APPDATA%\Tencent\xwechat\radium\users\<profile-hash>\applet\data\8\applet.db` plus a **6.3 MB `-wal`**, both updating during the scroll. The same window wrote only images (WebP/JPEG) and two ~3.5 MB gzip **article pages** into the Chromium cache — no feed payload. The conclusion is that 订阅号 is no longer a chat session at all: it is a **mini-program (WMPF applet)**, which is why nothing about it reaches `biz_message_0.db` (D-140) and why the session/unread counters stay near zero.

**Correction (2026-10-30): the store was read, and it is not the feed.** 该库的表结构后来读了出来（`sqlite_master` 是明文，不必解开数据页）：只有 **6 张表** —— `PrefetchStat`、`WaPackageInfo`、`WaLaunchInfo`、`WaBizAttrVer`、`WaSubPackage`、`WxaAttr`，全部是 WMPF 自己的**小程序包 / 启动信息 / 预取统计 / 小程序资料**元数据（六条建表语句正对应 `flue\utility\ilink_service\applet\storage\` 下六个 Storage 类），没有任何 article / feed / msg / title / url 表。所以差分里"滚动订阅号时 `applet.db` 在写"是真的，写进去的是小程序启动与包信息；原文"这个文件就是 feed"那一步推论错了，而且错在最容易被差分证据说服的地方：**一个文件在正确的时间被写，不等于它写了你在找的内容**。

**Where the pushes are not.** 同日实测（2026-10-30）：`biz_message_0.db` 共 553 张 `Msg_*` 表，全天合计 **14 行**（与 D-139 记的 14 一致）；applet 的 74 个 MMKV 文件（含 4 MB 的 `usrmmkvstorage0`）里 **0** 个 `mp.weixin.qq.com` URL、**0** 处「公众号/订阅号/article/feed」文本；Chromium 侧只有 `History`（8133 处文章 URL，打开过的）、`Favicons`、`Share Data` 与 mpxworker 模板 HTML。四类落点合起来指向同一个结论：**列表是联网现拉的，客户端只留"你打开过什么"**。这同时解释了 D-140（推送不再进消息库）与 D-141（历史里只有打开过的）为什么都对。

**Why the key is no longer the question.** 该库确实加的是 SQLCipher 4（页 4096、`reserve` 80、salt = 文件前 16 字节，判据在合成库上以正/负对照验证过），密钥由 WMPF 运行时算出后交给库层，`kdf_iter` 为 256000 —— 也就是说它和 WeChat 主库的钥匙体系不是一套，原文列的排除项因此都为真。但即便把这一层解开，得到的也只是上面那 6 张表。**这条路不通向日报目标**，继续投入的边际收益为零。

**What this entry is for now.** 它记录一次被证伪的推论，而不是一条待办：不要再去 `applet.db` 里找推送，也不要再为了"读推送"去攻它的加密。下一个遇到"4.2 订阅号为什么没有本地记录"的人，应该直接从"列表没有落盘"开始，而不是从"某个文件被加密了"开始。

**What blocked reading it (kept for the record).** `applet.db` is encrypted with a scheme that is not the WeChat database one. Exhausted at the time, all against the file header as salt and against a zero salt: PBKDF2-HMAC-SHA1 / SHA256 / SHA512 at 1000 / 4000 / 64000 / 256000 iterations; `cipher_compatibility` 2 / 3 / 4 with both a passphrase-form and a raw-key-form key; and a raw zero key. Every attempt returns `file is not a database`. The header is not `SQLite format 3`, the file is a clean multiple of 4096 bytes, and opening the live file returns `database is locked` unless it is copied together with its WAL. (2026-10-30 复核：格式确为 SQLCipher 4；上述排除项成立，只是它们指向的那个目标不是推送。)

**Consequences (rewritten 2026-10-30).** WeFlow 的诚实位置：**4.2 上"当天推送了哪些文章"没有本地数据源**，这不是读法或密钥问题，而是客户端不再把列表写下来。保留的行为不变（D-139 的低量告警、D-141 的 `--browser-history` 补读打开过的文章）。本条目现在的用途是拦住"再去破 `applet.db`"这条回头路：它是小程序注册表，`-wal` 在滚动时增长只是因为它被顺带写入了启动/包元数据。想要真正的推送列表，只能走网络接口（需要登录态、属非官方协议），按 `AGENTS.md` 不做。

## D-143: 4.2.0.11 之后用**外部 RSS 订阅源**补推送缺口（标准协议，不碰非官方接口）

**Status:** Active (2026-10-30)

**Context.** D-142 定的是"本地没有完整推送列表"。但把话说死之前，2026-10-30 又做了一次定量测量，得到的不是"没有"，而是**掉了约 98%**：

| 日期 | 落库的公众号消息 | 落库的公众号数 | 备注 |
| --- | --- | --- | --- |
| 2026-10-26 | 486 | 248 | 升级前 |
| 2026-10-27 | 481 | 239 | 升级前 |
| 2026-10-28 | 248 | 151 | **当天 13:16 升级到 4.2.0.11** |
| 2026-10-29 | 21 | 14 | 升级后 |
| 2026-10-30 | 11 | 12 | 其中真正的文章推送只有 7 篇 |

"账号数" = 当天该 `gh_` 消息表至少有 1 行的公众号数（落库口径，不是"客户端显示"）。前两天的 248 / 239 正好对上用户记忆里的"每天两百多个公众号"——所以"两百多篇"在升级前是准确的本地事实，丢的是**记录**，不是用户的记忆。

同日还做了两组对照，把"是不是我们读错了"排掉：① 五个消息分片（`message_0..3`、`biz_message_0`）全扫，`gh_` 消息**只**在 `biz_message_0.db`（550 张表里 539 张是 `gh_`），别的分片一张都没有——所以不是"搬走了"；② 把当天落库的 7 篇标题逐字回查，用用户确认当天在订阅号里看到、但不在那 7 篇里的一篇（`全面从严治党贵在持之以恒`）在**五个库里全搜，0 命中**——所以不是"漏读了某张表"。

**Decision.** 增加 `--rss-feed`（可重复）：从**标准 RSS/Atom** 拉当天文章，过滤条件只有两条——`url` 含 `mp.weixin.qq.com`、发布时间落在目标日期内。理由：

- 它**不碰登录态、不碰非官方协议**，`AGENTS.md` 的红线没有被踩；
- 微信没有"停止支持公众号"，只是不再把列表镜像到本地，因此上游的公众号转 RSS 服务仍然可用；
- 失败必须是**软失败**：单个源解析不了就跳过并记一行 stderr，其余源照常，日报绝不因订阅源挂掉。

**Boundaries (写明，避免下一个误解).** ① 这是**补充源**，不是替代品：没有配置 `--rss-feed` 时行为与 D-139/D-141 完全一致；② RSS 的覆盖取决于用户订阅了哪些源，WeFlow 不去猜、不去抓公众号主页；③ 只取当天，去重走既有的 `_article_key`（url 优先），所以和落库文章重复时以先到者为准，不会双份。

**Measured caveat (2026-10-30, on a real public bridge).** 拿 `wechat2rss.xlab.app` 的「量子位」源实测，发现两件影响准确性的事，写下来免得后来人以为是 bug：

1. **`pubDate` 不是真实发布时间**，而是桥**批量抓取的时间**：同一个批次里 5 篇文章的时间全是 `11:40:00`。所以按日期过滤的粒度是"批次"，真实发布时间最多会差到相邻一天（晚上发的文章，凌晨批次抓到就算到第二天）。要精确到分钟只能用文章页里的 `ori_create_time`（已验证可读），但那是每篇一次抓取，属于"按需启用"而不是默认。
2. **源是全文的，体积不小**：单源约 **1.9 MB**，200 个源一天两次约 **0.4 GB** 下载量。所以现实做法是**只订真正会读的 30–60 个号**，而不是把 240 个全部镜像过来——"回到从前的数量"和"回到从前的体验"在这里不是同一件事。
**覆盖率实测（2026-10-30，这是最该先看的一条）.** 写了 `scripts/rss_match.py` 把本机 690 个订阅号和公开服务的收录列表（实测 **395** 个，文档里说的"600+"已过时）做匹配，结果：**确认可迁移 6 个（1%）、疑似 3 个、迁移不了 681 个（99%）**。原因是收录列表集中在安全/开发两类，而本机订阅以 AI、科研、行业为主。**结论：现成的免费公开列表对任意个人订阅基本无用**——要覆盖"你自己的号"，必须用能按需添加任意公众号的桥（自建或付费私有部署），而那一步通常就走非官方通道了。这条数字应该写在任何"RSS 能救回来"的说法前面。
**Verification.** `test/daily_articles_merge_test.py::RssFeedTests` 钉了 5 件事：只取当天且只取公众号、Atom 也能解析、坏源不拖垮其它源、空源列表返回空、没有日期的条目被跳过。端到端实测（2026-10-30）：`--rss-feed` 补入 2 篇并与库里的 5 篇合并成 7 篇，日期过滤把「昨天 23:59」那条正确排除。

## D-144: 4.2 把订阅号从"会话系统"迁入"信息流"，本地持久化随之终结

**Status:** Active — 重大架构变动，不可逆（2026-10-30 记录）

**这是本仓库面对的最大一次外部变动，单独记录。** 之前 D-140 / D-142 / D-143 各自记的是"某条路走不通"，本条记的是**为什么所有本地路同时失效**。

**机制（实测支撑）.**
- 升级前：每个公众号 = 一个**会话**，每条推送 = 一条 message，落在 `biz_message_0.db` 的 `Msg_<md5(username)>` 表里。这是"读本地库"能拿到全部推送的**唯一原因**。
- 升级后：订阅号整体变成 **WMPF 小程序里的信息流视图**——不是会话。**没有会话就没有 message，没有 message 就没有表可写**。界面里的列表改为联网现拉，客户端只保留"你打开过的"（Chromium `History`，D-141）。

**留下的那批为什么还能落库.** 实测 10-29/30 仍落库的 21 个号里，**19 个是商家/机构/通知类**（支付、快递、医院、学校、招聘），只有 2 个内容号。它们能留下，是因为**仍以会话形式存在**（服务号通知走老通道）。

**动机（推断，明确标注为推断）.**
1. **存储与写放大**——`biz_message_0.db` 单库 297 MB、553 张表；上亿用户每天 200+ 条推送（含正文与图片索引）的写放大不可持续。改为"按需拉取、渲染即弃"可降一个量级。
2. **可运营性**——信息流可以排序、插推荐、做灰度；**逐条落库的会话是不可运营的**，服务端改不了已经写进用户硬盘的数据。
3. **减少第三方抓取面**——列表不落盘确实挡住了本地解析类工具，但**牺牲了客户端自身的离线可读性**，所以更像架构改动的副产品而非主因。
4. **多端一致**——手机端订阅号早已是信息流，PC 端随 4.x 重写（WMPF/radium）对齐。

**为什么"没有风声".** 对普通用户零感知：文章照常能看，只是不再存进硬盘。**没有任何普通用户会因此抱怨，所以不会有新闻。** 这也意味着：**不要指望微信改回来**——信息流化是产品方向，不可逆。

**对本仓库的约束（重要）.**
- "读本地库拿到当天全部推送"这条能力**永久性结束**，不是待修 bug，也不该继续投入（已在 D-142 / D-143 分别验证过 `applet.db` 与公开 RSS 两条替代路）。
- 任何"当天全覆盖"的方案都必须**自己向外获取**，因而受平台额度约束：同类最成熟工具（wechat-article-exporter）自述"每次 1–2 个公众号、间隔 5–10 分钟"，实测本机触发 `200013 freq control` 后 20 分钟仍未恢复。
- 因此产品形态只能是两种之一：**① 少量号高频 + 其余轮转（延迟换覆盖，不漏内容）**；**② 用户自备订阅源（`--rss-feed` / `--rss-opml` / `--articles-json`，已实现）**。**"零配置 + 当天全量 + 免费"三者不可兼得**，这条要写进任何面向用户的说明里。
