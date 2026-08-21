# Security Policy

## Local-first data handling

- **100% local**: WeFlow CLI reads the local WeChat data directory directly. There is no built-in cloud service, tracking, or telemetry — nothing is collected or reported.
- **Encrypted key storage**: database keys are written to `~/.weflow-cli/config.json` only after machine- and user-bound AES-256-GCM encryption; the ciphertext cannot be decrypted on another machine or under another account.
- **Explicit AI opt-in**: AI features (article summaries, classification, RAG Q&A) activate only after you explicitly configure your own API key, and only the content you select for processing is uploaded.
- **Loopback-only web services**: local readers and servers bind to `127.0.0.1` and are never exposed to the network.
- **Minimal process injection**: on Windows, key extraction injects into the running WeChat process only during initialization, performs read-only key retrieval, and unloads immediately afterwards.

## Reporting a vulnerability

Do not open a public issue for a vulnerability involving database access, key extraction, message sending, credentials, path traversal, command execution or data disclosure.

Email the repository owner through the contact channel listed on the GitHub profile, with a concise description, affected version, reproduction steps and impact. Do not attach real databases, keys, access tokens, personal messages or unredacted screenshots. You should receive an acknowledgement within seven days.

## Supported releases

Security fixes are applied to the current `master` branch and the latest npm release when practical. Older releases may require upgrading.

## Handling local data

- Treat `~/.weflow-cli/config.json`, `.mcp.json`, `output/`, exported chats and every `*.db` file as sensitive.
- Never share encryption keys, wxid values, API keys, WeChat credentials or full process output in an issue.
- Before running an MCP client, confirm its approval model and the directory configured as its working directory.
- The project can make network requests for explicitly selected article, AI, WeRead and official-account workflows. Review your provider and account permissions before enabling them.
