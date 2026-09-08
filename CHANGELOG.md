# Changelog

All notable user-facing changes are recorded here. This project follows [Semantic Versioning](https://semver.org/).

## Unreleased

### Documentation

- Synchronized setup, operations, architecture, security, MCP, and maintenance guidance with the current `1.5.1` source baseline.
- Clarified source-versus-npm version drift, no-AI daily runs, staged data-directory discovery, media-export limitations, and local-data privacy boundaries.
- Replaced the outdated architecture image with a GPT-image-2 diagram covering current CLI, MCP, service, workflow, data, and privacy boundaries.

### Fixed

- Decode locally cached WeChat 4.x V2 image containers during HTML chat export by deriving and validating the account-specific media key from local `kvcomm` data.
- Match exported chat media by stable server-message identity so reused local IDs cannot attach an unrelated image or emoji.
- Preserve forwarded app cards with cached covers, including CDATA-wrapped Bilibili links, and decrypt remote WeChat 4.x emoticons with their message-provided AES key.
- Decode entity-escaped emoji XML and try `encrypturl`, `thumburl`, `cdnurl`, and `externurl` fallbacks; resolve Bilibili BV covers when a share page omits `og:image`.
- Render signature-only WeChat default `[打脸]` messages with the bundled official `Facepalm` asset when no message-specific resource is available.

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

### Agent interfaces

- Added `capabilities --json`, redacted configuration status, structured export results, reader status, diagnostics, access-list JSON, and no-AI daily JSON output.
- Added the versioned `weflow-message/v1` contract to CLI exports and the read-only `wechat.export_messages` MCP tool for downstream projects.
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

## 1.5.1

### Fixed

- Improved WeChat data-directory discovery for custom locations, nested folders, and database subdirectories.
- Added staged guidance and optional `init --full-scan` fallback when automatic discovery cannot find the data.
- Completed incomplete yesterday output before an unqualified daily report run.

The npm package is published separately from GitHub. It may lag behind the `master` branch until a release is published.

## 1.5.0

### Added

- Local reader dark mode, keyboard navigation and read/favorite tracking.
- WeChat Moments local-cache commands and AI learning daily reports.
- Improved knowledge-pipeline and reader workflows.

For earlier history, see the [commit log](https://github.com/zhuobichen/weflow-cli/commits/master).
