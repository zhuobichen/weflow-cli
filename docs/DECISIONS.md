# Technical Decisions

> Record decisions that affect long-term maintenance. Each entry explains the chosen direction and the reason, not every implementation detail.

> 编号说明：`D-009` 从未使用（D-008 直接跳到 D-010），保留为空号；
> 历史上曾有两条决策共用 `D-016`，其中「architecture visuals」一条改号为 `D-028`。

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
`学术` in **zero** articles on a normal day and in **100%** of them on 2026-09-04/05, which is the
signature of the `except` branch's `topic = source_category or '学术'` fallback.

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
  classification. The same shape sat in `tags` and was fixed the same way. What remains open is a
  different question: whether the `【标签】` extraction lands at all. The earlier claim that it never
  does is now doubtful - a 2026-09-05 JSON carries real multi-word tags (`向量检索`, `工程踩坑`) - so
  that should be re-measured rather than repeated.

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

## D-035: The assistant's single-round fast path is designed but deliberately not shipped

**Status:** Deferred (design recorded, no code written)

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

## Decision Template


```markdown
## D-XXX: Short title

**Status:** Proposed | Active | Superseded

State the decision in one or two sentences.

**Reason:** Explain the constraint, trade-off, and why alternatives were not selected.

**Consequences:** List compatibility, migration, security, or documentation follow-up when relevant.
```
