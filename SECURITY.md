# Security Policy

## Intended use — access your own data only

This tool is designed for a single purpose: letting a WeChat account owner access and back up **their own** local data, on **their own** machine, with **their own** consent.

- Accessing a database you are not the owner of (another person's account, a device you do not control) is illegal in most jurisdictions, regardless of who owns the hardware. Monitoring a partner, employee, or any third party without their informed consent is a crime, not a gray area.
- Deploying this tool onto someone else's machine, bundling it into other software, or operating it remotely and silently is strictly prohibited.
- Users are solely responsible for compliance with local laws. The authors provide the code for legitimate personal-data management and accept no liability for misuse.
- Open-source software can be modified by anyone. Forks or rebuilds that remove safeguards or repurpose this tool for surveillance, stalking, or black/gray-market use have nothing to do with this project; the original repository is the only official source.

If you are being monitored or suspect this tool was installed on a device without consent: the configuration lives in `~/.weflow-cli/`, and every command invocation leaves traces in `~/.weflow-cli/` timestamps — inspect that directory and remove it.

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
