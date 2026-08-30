# Project State

> Last reviewed: 2026-08-29. This is the current maintenance snapshot, not a release note. Keep it factual and update it with meaningful project changes.

## Purpose

WeFlow CLI is a local-first command-line tool and MCP server for user-authorized WeChat data workflows: local chat queries and exports, official-account daily reading, favorites, knowledge workflows, and an optional local assistant.

## Current Baseline

- Package version: `1.5.0`.
- Runtime: Node.js 18+; Python 3.10+ is required for database and daily-reading workflows.
- Main development command: `npm run dev -- <command>`.
- Build check: `npm run build`.
- Regression check: `npm test`.
- Supported focus: Windows WeChat 4.x; Linux WeChat 4.x has an NT database path; macOS requires user-provided local access credentials for initialization.

## Verified Capabilities

| Area | Current state | Main entry points |
| --- | --- | --- |
| Local chat data | Query sessions, contacts, messages, favorites, Moments cache, and exports. | `sessions`, `contacts`, `messages`, `export`, `fav`, `sns` |
| Initialization | Verify and reuse existing local database access by default; refresh only when needed. Missing-key tests can run without changing saved configuration. | `init`, `init --refresh`, `init --test-missing-keys`, `config forget-keys`, `check` |
| Official-account daily | Filter configured sources, preserve source categories, fetch articles, create summaries, and generate a local HTML reader. | `daily`, `daily-stats`, `daily-server` |
| Knowledge workflows | Wiki compilation, semantic search, RAG, WeRead sync, reviews, and reading notes. | `wiki`, `search`, `chat`, `weread`, `review` |
| MCP | Local stdio MCP server exposes daily, knowledge, and local-data tools. | `mcp-config`, `mcp-server/index.ts` |
| Assistant | Optional WeChat Bot-channel assistant with local memory and privacy gates. | `login-wechat`, `assistant` |

The current regression suite covers home-path expansion, outbound PII redaction, local-inference bypass, strict message-body masking, MCP path/date validation, and cover-image signature validation.

The daily workflow supports `dailyAiEnabled=false` for a persistent no-AI mode, or `daily --no-ai` for a single run. Both the CLI and direct Python entry points honor the setting. Fetching, HTML generation, and local indexes remain available in that mode.

## Security Baseline

- Process only data the user owns or is explicitly authorized to access.
- Sensitive configuration fields are encrypted at rest with machine- and user-bound AES-256-GCM.
- The daily reader binds to `127.0.0.1`; its API rejects cross-origin mutations, validates local paths, and restricts image proxy requests.
- The assistant denies all senders until `assistantWhitelist` is explicitly configured. Group routing is experimental and remains denied unless the upstream explicitly supplies group metadata, the group and sender are both allowlisted, and the bot is mentioned. New or incomplete configurations use `strict` privacy mode for cloud inference.
- MCP path inputs are constrained to their expected data roots. MCP clients remain trusted local integrations and must be reviewed before configuration.
- Public reports and commits must not contain databases, keys, tokens, wxid values, real chat content, or unredacted logs.

## Active Constraints

- WeChat platform behavior, database formats, account restrictions, and terms can change without notice. Local operation is not a legal, account-safety, or platform-compatibility guarantee.
- The daily workflow can take substantial time when many configured sources publish on the same day. It fetches article bodies sequentially to reduce upstream pressure.
- The OC Bot channel is separate from a personal WeChat message stream. `send` can only reply through an already established Bot-channel conversation with a valid context token; it cannot initiate messages to existing personal contacts, post as the user's personal account, or send into ordinary personal WeChat groups.
- The current official OC/iLink payload model has not been verified to support group events or group invitations. The project does not implement client automation or protocol bypasses to add a bot to groups.
- Cloud AI workflows may transmit user-selected, privacy-filtered input to the configured provider. Local inference avoids that network transfer.
- Dependency audit findings must be reviewed before dependency upgrades; do not run breaking `npm audit fix --force` without validation.

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
- User-visible releases: [CHANGELOG.md](../CHANGELOG.md)
