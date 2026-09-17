# Changelog

The npm package is published separately from GitHub. It may lag behind the `master` branch until a release is published.

All notable user-facing changes are recorded here. This project follows [Semantic Versioning](https://semver.org/).

## Unreleased

### Fixed

- Read every message shard, not just the configured one. WeChat rolls a conversation into a new `message_N.db` over time and encrypts each shard with its own PBKDF2-derived key. `sessions`, `messages`, `contacts`, `export json/txt/excel`, `evidence` and the MCP server opened only `message_0.db` with the single configured key, so anything written after the last shard roll was **invisible** - and the commands still reported success. On the machine this was found on, one conversation showed 31 messages ending 2026-08-30 instead of 1145 ending 2026-09-17. Only `export html` merged shards (its Python exporter always had), which is why the two read paths disagreed.
- `export <talker> html --limit N` now honours the limit. It reached only the fallback renderer; the primary path passed the flag nowhere, so a capped export of a busy chat silently produced the entire history.
- `export <talker> excel` works at all. `exceljs` is CJS-only, so `await import('exceljs')` yields a namespace whose only member is `default`; `new ExcelJS.Workbook()` on the namespace is a `TypeError`, which a bare `catch {}` turned into the uninformative "Excel 导出失败". The reason is now reported too.
- Group messages no longer show the sender's own `wxid_...:` prefix in the text (`波: [强]`, not `波: wxid_ogfiei1l1ye722: [强]`). Only `parsedContent` is normalised; `content`/`rawContent` keep the stored value.
- `contacts` no longer emits a blank row for `Name2Id`'s placeholder entry.
- `fav list` no longer blames the data channel when the actual blocker is a missing favorites key. Added `check --json` → `favoritesReady`, which distinguishes "database found" from "usable".
- Subprocess failures now include the last stderr line, redacted of key-shaped strings. A missing config key used to surface as `统计失败 (exit 1)` with no cause; it now names the cause and the command that fixes it.
- `daily-stats` / `daily` no longer tell users to run `config set bizKey`, which the CLI rejects as an unwritable key.

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
