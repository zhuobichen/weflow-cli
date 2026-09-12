# Data Contract

This document defines the stable, local export boundary for downstream tools such as `she-love-me`.

## First Integration

The first downstream consumer is `she-love-me`. It should continue to invoke the existing command:

```powershell
weflow-cli export "<contact>" json --output "<local-output>"
```

The command remains a local, user-authorized export. It does not upload messages or grant another application direct access to the WeChat database.

## JSON Contract

The JSON output is a message array. A downstream consumer must tolerate both a top-level array and an object containing a `messages` array.

For new integrations, request the versioned envelope:

```powershell
weflow-cli export "<contact>" json --contract weflow-v1 --output "<local-output>"
```

The envelope contains `schema: "weflow-message/v1"`, `source`, `generatedAt`, and `messages`. Versioned exports also include optional `coverage` metadata: `requestedFrom`, `requestedTo`, `requestedLimit`, `returned`, `mayHaveMore`, and the oldest/newest returned `createTime`. `mayHaveMore` is deliberately conservative: it is true when the result reaches the requested positive limit, and false only when it does not. The default `raw` contract remains unchanged for existing consumers.

The currently supported message fields are:

| Field | Meaning | Consumer guidance |
| --- | --- | --- |
| `localId` | Local database message identifier | Display or trace only; it is not globally unique. |
| `isSend` | Whether the message was sent by the local account | Prefer this field for `me`/`them` mapping when present. |
| `createTime` | Unix timestamp in seconds | Normalize and validate before analysis. |
| `localType` | WeChat message type code | Map unknown values to `other`; do not discard the message. |
| `messageType` | Stable type name in `weflow-v1` | One of `text`, `image`, `voice`, `card`, `video`, `emoji`, `location`, `link`, `call`, `system`, `quote`, or `other`. |
| `senderUsername` | Sender identifier when available | Treat as sensitive and do not expose in public reports. |
| `parsedContent` | Parsed, human-readable content | Prefer over `rawContent` for analysis. |
| `rawContent` | Original or fallback content | Preserve locally for traceability; do not assume it is display-safe. |
| `transcript` / `voiceTranscript` | Optional voice transcription | Treat as user content and keep local. |

Downstream tools must not assume that local IDs are unique across conversations or database shards. Ordering should use `createTime` and a stable local tie-breaker.

## Incremental Reads Before A Cursor Exists

Until a stable cursor is available, a downstream reader can use an overlapping time window:

1. Export with `--contract weflow-v1` and record `coverage.newestCreateTime` locally.
2. On the next run, use `--from` at or slightly before that timestamp so messages sharing a second are not missed.
3. Deduplicate within the downstream store using the conversation identifier plus `localId` and `serverId` when present.

This is a best-effort synchronization recipe. It must not treat a timestamp as a globally unique message identifier, and it must retain the previous checkpoint if the export fails.

## Compatibility Rules

- Keep the existing `export <contact> json` command working.
- Use `--from` and `--to` for bounded exports when available; dates are interpreted in local time for date-only values.
- `--date YYYY-MM-DD` applies the same local calendar-day bound to JSON, TXT, HTML, and Excel exports. It cannot be combined with `--from` or `--to`.
- Add `--json` for a machine-readable operation result containing the output path and exported message count; this flag does not change the file format selected by the positional `format` argument.
- Use `--limit 0` for a complete export. With date filters, a positive limit applies to matching messages after the time range is evaluated.
- Preserve unknown fields so new media and message metadata can be adopted without breaking consumers.
- Treat `coverage` as query metadata, not proof that the underlying database is complete; database shards and concurrent writes can affect observed bounds.
- Treat missing sender identity as `unknown`; never infer identity from a display name alone.
- Keep conversion to a downstream application's internal schema in that application. `weflow-cli` remains a general data foundation.

## Privacy Boundary

- Data is read only from a user's own device or data they are authorized to access.
- Export files, database paths, account identifiers, keys, logs, and raw message content stay local by default.
- `she-love-me` should write its normalized bundle under its own local data directory and should not send raw exports to a model without explicit user consent and privacy filtering.
- AI analysis is not part of the export contract. An export must work without an API key or model call.

## Future Direction

The existing CLI export is the compatibility layer for the first integration. The same contract is available through the versioned, read-only MCP tool `wechat.export_messages`. It accepts `contact` and optional `limit`, `from`, and `to` values, returns JSON directly, and never returns database paths or keys. It preserves the same authorization, local-only default, field semantics, and privacy rules rather than bypassing the CLI boundary.
