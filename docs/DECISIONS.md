# Technical Decisions

> Record decisions that affect long-term maintenance. Each entry explains the chosen direction and the reason, not every implementation detail.

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

## Decision Template

```markdown
## D-XXX: Short title

**Status:** Proposed | Active | Superseded

State the decision in one or two sentences.

**Reason:** Explain the constraint, trade-off, and why alternatives were not selected.

**Consequences:** List compatibility, migration, security, or documentation follow-up when relevant.
```
