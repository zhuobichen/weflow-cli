# Changelog

All notable user-facing changes are recorded here. This project follows [Semantic Versioning](https://semver.org/).

## Unreleased

### Security and reliability

- Run the regression suite in CI and make NT path-discovery checks independent of the optional SQLCipher runtime.
- Make data-directory search staged: common locations by default, explicit cross-drive search, then explicit deep structural search.
- Avoid printing database salts, account identifiers, and message paths in `dbkey` diagnostics.

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
