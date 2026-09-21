# Changelog

The npm package is published separately from GitHub. It may lag behind the `master` branch until a release is published.

All notable user-facing changes are recorded here. This project follows [Semantic Versioning](https://semver.org/).

## 1.7.0

### Added

- `sync run|status|verify`: a local message-sync checkpoint. `sync run` reads a time window, deduplicates against the previous checkpoint and records what it covered; `sync status` reports coverage without touching the database, so it still works while the database is locked. It does **not** advertise a stable cursor - overlapping windows plus local deduplication is what it offers, per D-027.
- Per-shard read reporting. Shard open and read failures used to be swallowed by a bare `except: continue`, so "read 31 messages" and "read one shard and silently skipped three" were indistinguishable from the outside. `--report-shards` (opt-in; without it the JSON is byte-identical) surfaces `scanned/opened/failed` and one entry per shard. On the machine this was developed against the report reads `message_0.db` 31 rows and `message_3.db` 1258 - exactly the shape of the shard-read bug fixed in 1.6.4, which was completely invisible at the time.
- A media coverage report on HTML export: `<prefix>_media.json` beside the parts, with a status and a reason for every media item (`embedded | cached | remote-fetched | missing | unsupported`). The exporter had been computing `COVER_STATE` counters and discarding them, and a media miss showed up only as a bare `[图片]` with no reason recorded. A real 120-message export reports 54 items: 35 embedded, 15 missing, 2 unsupported, 2 remote-fetched, with reasons `not-in-local-cache` and `voice-not-in-media-index`.
- `docs/SYNC_CONTRACT.md` freezing the `weflow-sync/v1` and `weflow-job/v1` state schemas, and `D-029` recording the additive-only boundary, why the identity omits `shard`, and why `sync retry` is deliberately not implemented.
- `python scripts/nt_decrypt.py verify-native`: check a passphrase against an encrypted database using only the standard library (PBKDF2-HMAC-SHA512 over the page-1 salt, then the SQLCipher page MAC). "Is this key right?" can now be answered when `sqlcipher3` is missing or built against a different SQLCipher - previously that question and a broken driver looked the same from the outside. It answers yes / no / cannot-tell, and does not raise on unusable input. The synthetic test fixture was also made faithful while adding this: it had been encrypting every shard with one fixed salt and the passphrase as a raw key, so it could not exercise per-shard key derivation at all.
- Article topic and relevance in the daily report are now decided by TypeSafe's Jev decision model (`scripts/jev_client.py`) instead of being parsed out of generated text. One request asks a 6-way `choice` for the topic and a 3-level `score` for relevance, and returns probabilities rather than a string with `【主题】` in it. The LLM still writes the summary, tags and concepts - Jev cannot generate text. `weflow-cli config set typesafeApiKey "..."` enables it; without a key the previous path runs unchanged, and `--classifier llm` forces it. See D-031.
- Two additive frontmatter keys on each article: `relevanceScore` (the raw score) and `topicConfidence`. The three-level cut points are provisional and uncalibrated, so the raw value is kept rather than discarded at the write step.
- The daily report ends with **我拿不准的**: the entries whose probabilities sit near the threshold, split into those admitted and those excluded, plus any article whose topic confidence is low enough that it may be in the wrong section. It is computed **locally, from frontmatter** - no extra model call - because asking a model to describe its own uncertainty means asking it to generate more prose. When the articles predate the probability fields it says so outright, rather than printing nothing and letting the absence read as confidence. See D-033.
- The daily report now asks whether an article belongs in the report, instead of inferring it. `worth_including` is a yes/no question riding along in the same decision request (measured: 12 questions cost 0.91s against 0.84s for 2, since `state` dominates the token count), and it is stored as `includeScore`. Observed to diverge usefully from `relevance`: an award announcement scored 0.79 for relevance but 0.03 for inclusion.

### Changed

- Daily classification now runs concurrently **before** the serial summary loop instead of one article at a time inside it. Classification has no dependency on the summary, so serialising the two only ever meant queueing N network waits behind N model calls. Measured on 12 real articles: 3.3s at 6 workers against ~12s serial, so a ~190-article day goes from ~184s to about 30s. The concurrent stage prints one progress line (`分类完成 N/M 篇`, with the elapsed time and the serial estimate) because per-article logs stop being meaningful once it is parallel; the `M` is the number actually asked, so an article that failed to classify is visible rather than inferred.
- The three-level cut points for relevance had to be chosen before there was anything to calibrate against. Observed scores span `[0,2]`, so the levels are split at 0.5 and 1.5. On a 6-article trial with the shipped criteria nothing crossed into 高, so the report's `relevance != '高'` gate stays about as closed as before - it is now **able** to open, which it never was.
- The HTML exporter now tries the conversation cache before the network when resolving an article thumbnail; it had been downloading covers it already had on disk.
- `collectMessagesInRangeDetailed` distinguishes why paging stopped. The original loop ended on a short page exactly as it ended on an exhausted conversation, so a short page caused by offset shifting on a live database looked identical to having reached the end. The original function is unchanged; the variant is used by the new sync path and available to the export path next.
- Message reads adapt to the shard's actual columns instead of assuming a fixed schema. The read used to select fourteen columns while consuming six, so a WeChat version renaming any of the other eight made every shard raise - and the conversation came back **empty with no error** unless `--report-shards` was used. The read now selects only the columns it consumes, and a shard that lost one is read anyway with the loss named in `missingColumns`. A shard where none of `create_time`/`local_id`/`server_id` survives is reported as `SCHEMA_MISMATCH` and makes `coverage` partial. `export_chat_html` reads rows by position, so there a missing column keeps its slot as `NULL` rather than shifting the fields after it - a shifted export would have been silently wrong data rather than an obvious failure.
- `messages --from <unix>` / `--to <unix>` bound the read in SQL, and `sync` passes its window lower bound through. **This measured no faster** on the conversation used for development (519 ms unbounded vs 514 ms from the newest message, 925 messages over 4 shards) because the cost is process start plus 256000-round PBKDF2 per shard, not row transfer. It is there for very large conversations and as the prerequisite for a stable cursor; `sync` still applies the window itself, so backends without range support stay correct.

### Fixed

- A leaked database handle when a shard opened but its key was rejected: the connection was left open, which on Windows keeps the file locked.
- `coverage: 'partial'` now also covers a shard that opened but could not be read as asked (`SCHEMA_MISMATCH`, `WINDOW_UNAVAILABLE`) rather than only `READ_FAILED`. All three describe rows that were not read. A shard that never opened still reports `unverified`, unchanged. A shard read with columns missing is **not** partial - its rows did come back.
- A shard whose `Name2Id` table is absent no longer loses its messages: sender names are unavailable, the rows are still returned.
- A windowed read can no longer pass as covered when a shard cannot express the window. On a shard without `create_time` the read has nothing to filter on: it now says `WINDOW_UNAVAILABLE` instead of returning rows that the window filter then silently drops while `coverage` still said `complete`. No real WeChat schema observed so far triggers this - it is a hypothetical-path fix, tested against synthetic shards.
- `awaiting --html <path>` writes a one-page, **self-contained**, shareable summary: headline counts, a table of verdicts with probabilities, debt age, conversation kind and evidence strength, and the uncertain bucket. It deliberately carries no chat text - only probabilities, counts and day counts - which is what makes it safe to show someone else. Contact display names are escaped, and the renderer passes through a fixed set of fields, so a message body attached to a row cannot leak into the page. The footer states what the numbers are not: no gold standard, run-to-run variance near the threshold, and that thin evidence is not a conclusion. See `--html`.
- `scripts/quality_eval.py`: the way to finally check whether the probabilities mean anything. `sample` draws a stratified sample of real articles and scores each one through the **production** path, then writes a file with two blank fields per article (`topic`, `include`); `score` reads it back and prints topic agreement for both Jev and the stored labels, a **calibration table** (per probability bucket, what fraction the human actually said should be included), and a threshold sweep naming the cut point that best matches the human. Labels live in `~/.weflow-cli/labels/`, never in the repo. Every number it has produced so far has been a comparison against an imperfect baseline; this is the first thing that can replace that with a person's answer. 49 articles sampled and scored for ~$0.005.
- `decide --over <glob> --ask <text>` batch mode: one glob times one set of yes/no questions expands into N×M questions in a single request, and the response carries a `batch` mapping so the caller never parses question names to find out which file an answer belongs to. `--max-chars` (default 1500) is reported back in the response, because the evidence you feed is what sets the ceiling - measured: the same 132 questions scored 77.3% agreement against a baseline at 14 lines per file and 88.6% at 1500 characters. `capabilities --json` now declares the two input paths separately (`readsLocalData: {request: false, over: true}`), since only `--over` reads local files.
- `weflow-cli decide --request <file>`: a local judgement primitive. Takes a caller-supplied `{state, questions}` and returns typed answers with probabilities, plus the token count and the cost of the call, from one request. It reads no local data - the state is whatever the caller provides. Requests are validated locally so a malformed one fails with a reason instead of a service-side 422. **Not exposed over MCP**: an MCP client driving local outbound calls is a new egress surface and needs its own decision (recorded as `mcpExposed: false` in `capabilities --json`). See D-034.
- `weflow-cli awaiting`: who is waiting on a reply. Asks one decision-model request per conversation and prints a ranked list; `--dry-run` previews what would be sent without any egress, `--yes` runs, and it is registered in `capabilities --json` (D-018). The underlying script is `scripts/reply_debt.py`: it asks one decision-model request per conversation whether the thread is sitting on a reply the user owes, and prints a ranked list with probabilities, debt age, and conversation kind. It is **not** the same question as `extract_todos.py`, which scans up to 80 conversations into a *single* LLM call asking whether any task was mentioned - that call has nowhere to record *who* is waiting. Judgements here are per-conversation, so each one knows whose it is. 25 conversations judged in ~5s at 6 workers. Each row prints the strength of its evidence, because a score derived from a two-character message is not the same claim as one derived from a paragraph. See D-033.
- Search results are reranked by a decision model. `semantic_search.search()` scored by embedding similarity and took `argsort[:top_k]` - there was no second pass at all, and the keyword fallback (used whenever embeddings are unavailable) was cruder still. It now asks one question per candidate in a **single** request (~1s for 20 candidates; measured 0.84s at 2 questions against 0.91s at 12) and reorders by the probabilities, keeping `score` as-is and adding `rerankScore`. `--no-rerank` restores the previous behaviour. See D-032. Verified on real data: an article about PM2.5 and plant water-use efficiency, planted at position 4 of 8, came out first at 0.94 against 0.01-0.04 for the rest.
- Transient decision-model failures are retried (429/5xx/529, short backoff). A live `HTTP 529 system_overloaded` was hit while building the reranker - the service launched on 2026-09-15 and being saturated is plausible. Auth failures are not retried.
- A preview no longer requires the API key it is meant to help you decide about spending. `awaiting --dry-run` used to check for a key first and refuse without one, which put the cost of entry *before* the menu.
- Non-text messages (images, voice, stickers) no longer reach a decision model as blank lines. WeChat stores them with empty `message_content`, so `reply_debt.py` sent `对方：` with nothing after it and got a confident answer back from the blank. They now carry a type label. See D-033.
- `build_index` now indexes articles. `collect_articles()` filtered directory entries with `glob(' marriage*.md')` - a leading space and a `marriage` prefix, matching **0 of 188** files in a real topic directory. So the semantic index had never contained a single article, only chat messages, and nothing reported it. **The next `search-index` run will index articles for the first time and therefore consume embedding calls it never spent before** - that is a deliberate rebuild, not something a routine command should do silently.
- `semantic_search` imports `numpy` and `sqlcipher3` lazily now (`require_numpy()` / `require_sqlcipher()`, the pattern `nt_decrypt.require_sqlcipher` already used). Previously a missing dependency called `sys.exit()` at import time, so the keyword-only and reranking paths - which need neither - were unusable on such a machine, and no test could import the module at all.
- Batch mode in `decide` shipped without tests and crashed on first use: a deletion patch had left dead code in `main()` that the batch branch fell into, referencing an undefined variable. Found by running it, not by reading it, which is the point - `test/decide_test.py` now covers the batch path, including that it reaches the model at all.
- `relevanceScore`, `topicConfidence` and `includeScore` never reached `.articles.json`, only the markdown frontmatter - and the report reads the JSON first. So the probability fields were invisible on the report's **primary** path. Introduced with the fields themselves, found by running a real dry-run and seeing the new section claim there were no probabilities, while the frontmatter plainly had them. Their serialization is now a named function with a test.
- `dashscopeApiKey` is a first-class config key now: declared in `CliConfig`, **encrypted at rest** like the other secrets, and settable with `config set`. It had been read by `rag_chat` and `semantic_search` while not existing in the schema at all, so `config set dashscopeApiKey` was refused and the scripts' own error text could only tell the user to hand-edit `config.json`. Their reads now go through `_utils.get_dashscope_key`.
- `config set favPassphrase` works. It was in `ENCRYPTED_KEYS` and read by `biz_daily` and `reply_debt`, but was missing from the `configurableKeys` allowlist - so a first-class secret could be neither set nor rotated through any documented path, and hand-editing a file whose field is ciphertext does not produce something usable.
- Two static checks now enforce what that audit was looking for: every key a script reads is declared in `CliConfig` (`test/config_keys_declared_test.py`, with a justification-required exception list), and no script reads an encrypted key without decrypting it.
- `nt_decrypt` and `export_chat_html` shared four same-named helpers that were **not the same code**, and they are now one implementation in `scripts/nt_common.py` (`discover_message_shards`, `derive_database_key`, `table_columns`, plus the exclusion set). The two differed in contract, not just in wording: `discover_message_shards` in `nt_decrypt` returned `[the configured database]` when it globbed no shards, while the exporter's returned an empty list and left the compensation to its call site. A second caller that forgot to compensate would silently read zero shards. `derive_database_key` and `table_columns` were byte-identical apart from docstrings. The never-empty contract is now the only one, the exporter's `if not shards: shards = [db_path]` is gone, and `test/nt_decrypt_shards_test.py` pins the single implementation by **identity** rather than by "the two agree" - two copies that happen to agree is exactly how this drifted in the first place.
- `nt_decrypt.load_contact_names` no longer leaks its connection when the read fails: `conn.close()` sat at the end of the `try`, so any exception before it left the handle open, which on Windows keeps the file locked (the same shape as the shard-read leak fixed earlier). It also keeps the names it did read instead of discarding all of them.
- The keyword-search fallback no longer returns generated report artifacts as articles. Searching for 环境 空气质量 returned **行动建议 — 2026-08-30**, a file the daily pipeline writes, not an article. The artifact exists at **two depths** (`<day>/行动建议.md` and `<day>/<topic>/行动建议.md`, four of each on the real data), so filtering by path is not enough - the fix uses the same predicate `quality_eval` already applies: a real article carries `url:` in its frontmatter. The index-building path never had this problem because it only walks topic directories, which is why it went unnoticed: **the two paths disagreed, and the unfiltered one is the one that actually runs** (the index has never been built on this machine).
- Four scripts read `deepseekApiKey` straight out of the config file while every other reader decrypts it first: `annual_report`, `classify_daily`, `extract_todos` and `rag_chat`. Since that key is in `ENCRYPTED_KEYS`, `config set` stores it as `lock:<ciphertext>` - so those four would hand `lock:...` to the provider as an API key and report an auth failure that says nothing about why. **It has not surfaced only because on this machine that one key happens to be plaintext**, written by something that bypassed `config set`; every other encrypted key on disk is ciphertext. Rotating the key with `config set` would have broken all four at once. They now go through `_utils.get_api_key`, and `test/encrypted_config_test.py` statically fails the build if any script reads an encrypted key without decrypting it - while accepting the repo's existing `*_enc` convention (hold the ciphertext, decrypt later), which seven correct call sites use.
- Four declared-but-unused things are gone, each of which described behaviour the code did not have: `biz_daily.MAX_ARTICLES = 50` with a comment claiming it capped fetching (never read; `--limit` is what works, and a normal day is 150-270 articles); a `CONFIG_PATH` in the same file (config goes through `_utils.load_config`); `_utils.parallel_map` (a concurrency helper with no callers); and fifteen lines of **unreachable** code after `search()`'s return, which returned a `dict` while the live code returns a `list` - a dead block documenting a contract the function no longer has. Their imports went with them.
- The article prompt no longer contradicts itself about the topic list. `TOPIC_PROMPT` generated its "must be one of" line from `TOPICS` (six categories) but hardcoded **five** in the two reminders further down, and the judging rules had no entry for 政治 at all - a category that accounts for 26% of the corpus. Both reminders and the rules are now generated from the shared table, so the LLM path is told to pick from six categories *and* given a definition for each. **This can change topic output on the LLM path** (which is the fallback when no decision-model key is configured): a category that previously had no criteria now has one.
- The topic taxonomy has a single definition. `TOPICS` had been copied into six files and `TOPIC_CRITERIA` existed separately in `jev_client`; both now live in `_utils` and every consumer imports them, so the prompt path and the decision-model path judge by the same table. `test/topic_taxonomy_test.py` pins it by identity, not equality - copying a list and getting it right is exactly how it drifted the first time.
- The admission filter's log line claimed the threshold was applied even when it was not: on articles without `includeScore` it printed `（阈值 includeScore >= 0.5）` while the old `relevance == '高'` rule was doing the filtering. It now names the rule it actually used, including the mixed case. A log that misdescribes what it did is worse than no log.
- The daily report's admission rule is now actually applied. `if topic != FOCUS_TOPIC and relevance != '高': continue` existed only in the markdown-scan *fallback* loader; the primary loader (`.articles.json`, present on every normal run) returned every article unfiltered, so the rule had never taken effect. Both loaders now call one predicate, and `--include-all` restores the previous collect-everything behaviour. **This changes what the report contains**: non-focus articles will be fewer, because previously there were no filters at all.
- `relevance` is now assigned on **every** path through the daily classification loop. It used to be set on two of five: `category_hint` hard-coded it to `中`, and the AI-exception and short-content paths left it unset for the writer's `fm.get('relevance', '中')` to catch. Measured over the 2201 stored articles, 2199 of them carried that default. That alone would have left `generate_ai_report.py`'s gate filtering on topic only; the gate turning out to be unreachable on the primary loader made even that generous.

## 1.6.4

### Fixed

- Restored four pieces of matching logic in the HTML exporter that the `90b165a` clone-consolidation merge had silently dropped, all of which lowered media coverage without failing loudly:
  - the `unique:<local_id>` fallback, which matches an image when that id resolves to exactly one distinct picture in the conversation (content-deduped, so the shard-collision risk that rules out a bare `local_id` does not apply);
  - `is_encoded_media_type`, needed because WeChat stores some forwards as high-bit variants of type 49 — the mask turns those into a plain 49, which is a registered type, so the branch guarding them had become unreachable;
  - the guard that keeps a type-49 row carrying a title or url on the link-card path instead of hiding it behind a cached thumbnail;
  - the cache-first lookup in the article branch, which had been downloading covers it already had on disk.
- Seeded `COVER_STATE`'s budgets with their limits instead of 0. They were only ever set by `main()`, so any caller reaching the fetchers another way silently skipped every remote fetch and got `None` back with no error.
- `pipeline_security_test` no longer reads the developer's real `~/.weflow-cli/config.json`; it points `CONFIG_PATH` at an empty temp file. The test only passed on a machine that happened to have a config, and reading a real config from a unit test is exactly what the repo rules forbid.
- CI actually runs, for the first time. `npm test` passed locally and failed on every push: the script used `test/**/*.test.ts`, which Git Bash expands locally but CI's bash does not (globstar is off by default), so Node received the literal pattern and reported `Could not find ...`. The glob is now quoted so Node expands it. The single combined job was also split into `node` / `python` / `release-consistency`, because a Node failure previously stopped the Python tests from running at all — they had been failing unnoticed.
- Raised the Node floor to 22.13.0. `src/core/sqlcipherCore.ts` imports `node:sqlite` at module scope; that builtin arrives in 22.5.0 and stops needing `--experimental-sqlite` at 22.13.0, so on Node 18/20 every command died at load while `engines` still claimed `>=18`. Both are past end-of-life, so the floor moved rather than adding a lazy-load path for dead runtimes.
- Rebuilt `package-lock.json`, which still said 1.6.1 and still listed `lz4` as an ordinary dependency after the 1.6.3 change moved it to optional. `npm audit fix` then took production vulnerabilities from 17 to 6; the remainder (`@xmldom/xmldom`, `exceljs`, `@wenyan-md/core`, `mermaid`, `speech-rule-engine`, `uuid`) have no non-breaking fix — `exceljs@4.4.0` and `@wenyan-md/core@3.0.11` are already the newest releases and the advisories' suggested "fix" is to downgrade them.

### Added

- `docs/PROJECT_STATE.md` no longer carries a hardcoded version — it points at `package.json`, which is what drifted to `1.5.1`. Example version numbers in `docs/NPM-PUBLISH.md` and `docs/HEALTH-CHECK.md` became placeholders for the same reason.
- Git tags and GitHub Releases for `1.6.0`, `1.6.1` and `1.6.3`, so npm versions map to commits. `1.6.2` has neither: it was published, but its version bump was never committed.

## 1.6.3

### Fixed

- `npm install -g weflow-cli` no longer fails on machines without Visual Studio build tools. `lz4@0.6.5` declares an unconditional `"install": "node-gyp rebuild"` with no prebuilt binaries and no fallback, so having it in `dependencies` made the whole install abort with `gyp ERR! find VS could not find a version of Visual Studio 2017 or newer` - for every Windows user without a C++ toolchain, which is most of them. The code only ever used it for 3.x WCDB `CompressContent` decompression, and already loaded it lazily behind a `try/catch` that degrades to `null`, so it is now an `optionalDependency`: npm installs it best-effort and continues when the build fails. Measured on a machine with no Visual Studio: before, `npm install` exited 1; after, it exits 0 and the installed CLI runs.

## 1.6.2

### Fixed

- Read every message shard, not just the configured one. WeChat rolls a conversation into a new `message_N.db` over time and encrypts each shard with its own PBKDF2-derived key. `sessions`, `messages`, `contacts`, `export json/txt/excel`, `evidence` and the MCP server opened only `message_0.db` with the single configured key, so anything written after the last shard roll was **invisible** - and the commands still reported success. On the machine this was found on, one conversation showed 31 messages ending 2026-08-30 instead of 1145 ending 2026-09-17. Only `export html` merged shards (its Python exporter always had), which is why the two read paths disagreed.
- `export <talker> html --limit N` now honours the limit. It reached only the fallback renderer; the primary path passed the flag nowhere, so a capped export of a busy chat silently produced the entire history.
- `export <talker> excel` works at all. `exceljs` is CJS-only, so `await import('exceljs')` yields a namespace whose only member is `default`; `new ExcelJS.Workbook()` on the namespace is a `TypeError`, which a bare `catch {}` turned into the uninformative "Excel 导出失败". The reason is now reported too.
- Group messages no longer show the sender's own `wxid_...:` prefix in the text (`波: [强]`, not `波: wxid_ogfiei1l1ye722: [强]`). Only `parsedContent` is normalised; `content`/`rawContent` keep the stored value.
- `contacts` no longer emits a blank row for `Name2Id`'s placeholder entry.
- `fav list` no longer blames the data channel when the actual blocker is a missing favorites key. Added `check --json` → `favoritesReady`, which distinguishes "database found" from "usable".
- Subprocess failures now include the last stderr line, redacted of key-shaped strings. A missing config key used to surface as `统计失败 (exit 1)` with no cause; it now names the cause and the command that fixes it.
- `daily-stats` / `daily` no longer tell users to run `config set bizKey`, which the CLI rejects as an unwritable key.
- `init --refresh` no longer fails with "密钥提取失败" when a perfectly good key is already configured. The hook only fires at the login moment, so a refresh run against an already-logged-in WeChat always times out; the fallback then consulted **only** `favPassphrase`, which `init` itself never writes (it writes `decryptKey`, and `favPassphrase` is set only when a usable `favorite.db` exists). Reads use `favPassphrase || decryptKey`, so the fallback rejected keys the rest of the tool was happily using. Reported as "最新版本的微信密钥解不开了" (Issue #9) after a WeChat upgrade, where `init --refresh` is exactly what the CLI tells users to run. The fallback now matches the read path, and the summary distinguishes a fresh capture ("密钥获取成功") from reusing what was configured ("已沿用配置中的密钥") instead of claiming success either way. Reaching that point also lets `enableFavorites` run, so favorites start working for users who previously died here.

### Added

- `scripts/health_check.py` and `docs/HEALTH-CHECK.md`: a periodic, zero-token health check whose exit code is the verdict, covering version drift, shard read consistency, session freshness, export formats and the watcher tasks.

## 1.6.1

### Fixed

- Report the real version from `--version` and `capabilities`. The version was a literal in the CLI source and `npm version` only rewrites `package.json`, so the 1.6.0 package announced itself as `1.5.1` - which made a user's bug report impossible to tell apart from a stale install, and took a clean install to disprove.
- Keep the discovered database list when the NT scan cannot read WeChat's memory. `nt_decrypt.py scan` returned early on "Weixin.exe 未运行" *before* walking the filesystem, and the caller discarded the whole result on any error. The passphrase-derivation path - the correct one for current WeChat, and one that needs only the file list - therefore never ran, so a run that had already captured the passphrase still ended with no usable keys and `sessions` reported "WCDB 初始化失败: -1006".

### Documentation

- Added `docs/RELEASING.md`: versioning rules, the pre-publish check for local media and keys, npm credentials with 2FA, and the China-mirror sync step that otherwise leaves users unable to install a fresh release.

## 1.6.0

### Documentation

- Synchronized setup, operations, architecture, security, MCP, and maintenance guidance with the current `1.6.1` source baseline.
- Clarified source-versus-npm version drift, no-AI daily runs, staged data-directory discovery, media-export limitations, and local-data privacy boundaries.
- Replaced the outdated architecture image with a GPT-image-2 diagram covering current CLI, MCP, service, workflow, data, and privacy boundaries.

### Fixed

- Decode locally cached WeChat 4.x V2 image containers during HTML chat export by deriving and validating the account-specific media key from local `kvcomm` data.
- Match exported chat media by stable server-message identity so reused local IDs cannot attach an unrelated image or emoji.
- Match NT cache media by the exact local-message-ID and timestamp pair when server-resource metadata is unavailable, including reused local IDs.
- Preserve forwarded app cards with cached covers, including CDATA-wrapped Bilibili links, and decrypt remote WeChat 4.x emoticons with their message-provided AES key.
- Decode entity-escaped emoji XML and try `encrypturl`, `thumburl`, `cdnurl`, and `externurl` fallbacks; resolve Bilibili BV covers when a share page omits `og:image`.
- Render signature-only WeChat default `[打脸]` messages with the bundled official `Facepalm` asset when no message-specific resource is available.
- Cache remote export media (covers, article thumbnails, emoticon CDN) on disk, misses included, so a re-export no longer repeats hundreds of requests against dead WeChat CDN links. First export of a link-heavy conversation dropped from 228s to 28s and re-export to 1.8s, with identical output.
- Fetch that remote media concurrently instead of one URL per message. A throwaway local-only pass records what the conversation needs, the URLs are fetched in parallel, then the real pass runs against a warm cache.
- Try WeChat's local sticker cache before the CDN for custom emoticons. The local path is offline and instant; the CDN cost 1.32s per sticker and was tried first.
- Discover the account's sticker seed automatically. `find_seed`/`any_sticker_file` existed but nothing called them, so `emoticonSeed` stayed empty, local decryption never ran, and custom stickers silently degraded. The export now derives it from a real cached sticker, memoises it, and prints the command to persist it.
- Render `local_type=10000` system rows as text. Escaping the raw row put WeChat's own display markup (`<img src="SystemMessages_HongbaoIcon.png"/>`, `<_wc_custom_link_ ...>`) in the bubble as a wall of `&lt;sysmsg ...&gt;`, so a revoke notice read as XML instead of naming who revoked what. `$wxid_...$` placeholders are expanded too.
- Flatten quoted replies (appmsg type 57) whose `<des>` carries a whole escaped nested message, instead of dumping the nested markup.
- Surface the Python exporter's progress and diagnostics in `export html` output; only the trailing JSON summary was read, so a multi-minute export showed nothing in between.
- Name the actual speaker in group chats. `display_name` is the group, so every bubble was labelled with the group name and no message could be attributed; group rows now use the sender id carried by the content prefix, falling back to the sender map.
- Render `local_type=48` location rows as their place label (`[位置] ...`) instead of dumping the location XML.
- Strip the redundant `wxid_...: ` content prefix from group rows once it has been used to identify the speaker.
- Resolve group senders to names from the contact database (remark, then nickname, then alias). A group transcript previously showed raw wxids for every speaker; unresolved ids still fall back to the id rather than a blank.
- Merge the conversation cache and the account media index instead of choosing between them. A `--cache-dir` short-circuited the account scan, and a conversation cache only keeps recent months, so a group photo from last year resolved 0 of 1444 images and every one rendered as a bare `[图片]`. The same fix also recovered 82 additional images in a 1:1 conversation.
- Show a cached poster frame for `local_type=43` videos when one exists, and always show the clip's duration (`[视频 10″]`) instead of a bare `[视频]`. Where WeChat never downloaded the video, no poster exists locally to show.
- Fix HTML part navigation, which linked to `<talker>_partN.html` while the files were written as `<remark>_partN.html`, so every "第 N 部分" link opened a missing file (`ERR_FILE_NOT_FOUND`). Affected both group and 1:1 exports; all 16165 links across the two test conversations now resolve.
- Correct the exported page footer, which still claimed images cover only the most recent two months.
- Show the drawn frame of a `wxgf` sticker instead of a blank white square. Sticker H.265 streams routinely open with a blank transition frame, and taking frame 1 embedded that; decoding a few frames and keeping the one carrying the most artwork fixes it (one sticker measured 99.1% white before, 2.1% after). Verified across three conversations: 2215 embedded images, 0 blank.
- Treat an all-blank sticker payload as a failed decode rather than an image, so a truncated local download or a CDN placeholder falls back to the `[表情]` label instead of rendering an empty square.
- Test the plain-text branch against the message body rather than the row's combined metadata. `metadata_content` always carries `<msgsource>`, so the check always saw a `<` and every text message that had sender metadata skipped the text branch and fell through to the emoji one - rendering a Tencent Meeting invite as `[表情]` beside a broken image.
- Label `local_type=10000` system rows as `系统` and never fall back to the conversation name for a group speaker. A revoke notice or join template resolves to no member, and the fallback labelled it with the group name, reading as if the group itself had spoken.
- Render `sysmsgtemplate` join notices from their template and member list (`"彪弟"邀请你和"777"加入了群聊`) instead of stripping the tags and leaving only the chatroom id.
- Never emit a remote URL as an `<img>` source. The candidate comes from a catch-all that accepts any URL in the row, and a sample of 57 such sources found 56 were web page links (`meeting.tencent.com`, `github.com`, `support.weixin.qq.com`) rather than images, each rendering as a broken-image icon. Images we could not fetch are now simply not shown.
- Improved WeChat data-directory discovery for custom locations, nested folders, and database subdirectories.
- Added staged guidance and optional `init --full-scan` fallback when automatic discovery cannot find the data.
- Completed incomplete yesterday output before an unqualified daily report run.

### Added

- Transcribe voice messages into HTML exports. WeChat voice is SILK v3, which browsers cannot play and ffmpeg cannot decode at all, so a voice message previously carried no information whatsoever. `scripts/wechat_voice.py` decodes the payloads from `media_*.db`'s `VoiceInfo` table and recognises them on-device, and the export renders `[语音 6″]` with the transcript beneath it.
- Transcription is a separate, resumable pass rather than part of the export: exports only read a content-addressed transcript cache, so a long conversation never blocks an export and an interrupted run continues where it stopped.
- Prefer a Cantonese fine-tune over stock Whisper. Stock Whisper answers Cantonese speech with fluent, confident Mandarin that was never said — worse than no transcript, because it reads as a real sentence. The Cantonese model transcribes the same clips into actual Cantonese, and stock `large-v3` was measurably worse still, hallucinating Vietnamese and English.
- Use the GPU when one is available, falling back to CPU. The same clip goes from 2.0s to 0.07s, which is the difference between a ~30 minute pass and an overnight one. Includes the Windows DLL-path setup the recognition library needs for its CUDA runtime.
- `requirements-voice.txt` declares the optional voice dependencies.
- Label machine transcripts and state the limitation in the page footer. Sampling a Cantonese family group found the recogniser producing Cantonese-shaped text whose meaning often does not hold - right sounds, wrong words. A confidently wrong transcript is worse than an obvious placeholder in a record that may be cited, so transcripts are marked `机器转写·粤语欠准` and the footer says they must not be quoted as the original words. See OPERATIONS.md for the measurements that rule out decoding, audio quality, and model confidence as causes.

### Security and reliability

- Run the regression suite in CI and make NT path-discovery checks independent of the optional SQLCipher runtime.
- Make data-directory search staged: common locations by default, explicit cross-drive search, then explicit deep structural search.
- Avoid printing database salts, account identifiers, and message paths in `dbkey` diagnostics.
- Added `daily favorites` commands to synchronize and manage reader favorites as local files.
- Added `vault promote ideas` and `vault promote all`; both default to no-AI local generation and require explicit opt-in for AI outputs.
- Keep the default MCP surface read-only and require unique conversation-name resolution with bounded query limits.
- Require preview and explicit confirmation for machine-driven messaging, configuration, access-control, todo, assistant lifecycle, MCP configuration, key reset, and Vault synchronization changes.
- Require preview and explicit confirmation for Vault initialization, semantic indexing, knowledge pipelines, and report generation; expose no-AI/source filtering and strict bounded parameters for Agent use.
- Validate local-reader ports before process startup and open browser URLs without shell interpolation.
- Restrict MCP article fetching to bounded HTTPS requests on the exact WeChat article host, including redirect revalidation.
- Validate outbound media files and remove full local paths from message previews and audit records.
- Resolve bundled Python scripts consistently from both source and compiled package layouts, and propagate worker failures through nonzero exit codes.
- Remove the ineffective `mcp-config --port` option; the MCP server uses stdio and does not bind a network port.
- Reject absolute, non-Markdown, symlink-escaping, and parent-traversal entries in daily favorite state before linking or copying files.
- Add preview, confirmation, and content-free JSON results to Vault content mutations, WeRead synchronization, daily favorites, and personal consumption reports.
- Add preview and confirmation to Wiki compilation and AI todo extraction; keep process-memory key capture explicitly human-gated.
- Add a machine-safe preview for database-key capture and require an interactive terminal for execution.
- Add a content-free initialization preview while keeping actual database discovery and key capture human-gated.
- Require preview and confirmation before Vault RAG reads local knowledge or sends selected context to AI.
- Require preview and confirmation for semantic search and RAG chat, and keep their private inputs out of child-process arguments.
- Keep report conversation selections and NT scan roots out of child-process arguments, and clear unrelated internal values from long-lived worker environments.
- Require preview and confirmation for evidence review, with content-free and path-free machine results.
- Reject unsupported WCDB query parameters instead of silently executing SQL without bindings.

### Agent interfaces

- Added `capabilities --json`, redacted configuration status, structured export results, reader status, diagnostics, access-list JSON, and no-AI daily JSON output.
- Added the versioned `weflow-message/v1` contract to CLI exports and the read-only `wechat.export_messages` MCP tool for downstream projects.
- Added conservative `coverage` metadata to versioned message exports and capability discovery, while preserving the legacy raw JSON array.
- Added bounded local evidence-package and explicitly authorized evidence-review commands.
- Applied message date ranges before pagination and preserved unknown message types in downstream contracts.
- Added content-free JSON summaries for account scanning and assistant logs, plus structured todo reminders.
- Added preview and confirmed background startup for Agent-controlled local daily readers.
- Apply the same startup confirmation to the legacy `fav-server` compatibility command.
- Require explicit confirmation for machine-driven daily generation, including no-AI runs, while preserving human and scheduled non-JSON commands.
- Add content-free previews for configuration, key, access-list, and audit-log clearing before confirmed deletion.

### Documentation and packaging

- Added a unified Python dependency manifest for the standard Windows 4.x workflow and an optional legacy 3.x manifest.
- Added MCP integration, contribution and security guidance.
- Included README architecture assets and installation manifests in npm and portable releases.

## 1.5.0

### Added

- Local reader dark mode, keyboard navigation and read/favorite tracking.
- WeChat Moments local-cache commands and AI learning daily reports.
- Improved knowledge-pipeline and reader workflows.

For earlier history, see the [commit log](https://github.com/zhuobichen/weflow-cli/commits/master).
