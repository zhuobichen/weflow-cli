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
