# Security Policy

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
