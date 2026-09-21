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
  tool routing, long-term memory extraction, todo urgency, monthly-report task detection, ...),
  the `tags` field, and `TOPICS` being duplicated across five files. The first group was never
  measured on Chinese; the others are separate defects with their own blast radius.

## Decision Template


```markdown
## D-XXX: Short title

**Status:** Proposed | Active | Superseded

State the decision in one or two sentences.

**Reason:** Explain the constraint, trade-off, and why alternatives were not selected.

**Consequences:** List compatibility, migration, security, or documentation follow-up when relevant.
```
