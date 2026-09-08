# Security Policy

## Intended use — access your own data only

This tool is designed for a single purpose: letting a WeChat account owner access and back up **their own** local data, on **their own** machine, with **their own** consent.

- Accessing a database you are not the owner of (another person's account, a device you do not control) is illegal in most jurisdictions, regardless of who owns the hardware. Monitoring a partner, employee, or any third party without their informed consent is a crime, not a gray area.
- Deploying this tool onto someone else's machine, bundling it into other software, or operating it remotely and silently is strictly prohibited.
- Users are solely responsible for compliance with local laws. The authors provide the code for legitimate personal-data management and accept no liability for misuse.
- Open-source software can be modified by anyone. Forks or rebuilds that remove safeguards or repurpose this tool for surveillance, stalking, or black/gray-market use have nothing to do with this project; the original repository is the only official source.

If you suspect this tool was installed on a device without consent, stop using the device for sensitive activity and ask a qualified security professional to inspect it. Do not publish the configuration directory or its contents while reporting the concern.

## Local-first data handling

- **Local-first**: WeFlow CLI reads local data directly and has no built-in tracking or telemetry. Explicit article, WeRead, Bot-channel and cloud-AI workflows can make network requests.
- **Encrypted key storage**: database keys are written to `~/.weflow-cli/config.json` only after machine- and user-bound AES-256-GCM encryption; the ciphertext cannot be decrypted on another machine or under another account.
- **Explicit AI opt-in**: AI features (article summaries, classification, RAG Q&A) activate only after you explicitly configure your own API key, and only the content you select for processing is uploaded.
- **Secret-safe subprocesses**: CLI-provided AI keys, database credentials, account identifiers, and selected conversation identifiers are passed to Python workers through their environment rather than copied into child-process command lines. Persistent configuration or environment variables are preferred over entering a secret directly on the command line.
- **Sanitized worker failures**: subprocess failures report a bounded category or exit code instead of repeating the full command, local path, query, or credential-bearing argument list.
- **Strict assistant defaults**: the WeChat assistant denies all senders until `assistantWhitelist` is explicitly configured, and cloud inference defaults to `strict` privacy mode.
- **Loopback-only web services**: local readers and servers bind to `127.0.0.1` and are never exposed to the network.
- **Bounded MCP network reads**: article URLs require exact HTTPS host validation; redirects are revalidated and external responses have time and size limits.
- **Path-minimized send previews**: image and file sends validate a non-empty regular file, reject conflicting media options, and expose only the base name and size in previews and audit records.
- **Bounded favorite sources**: daily-favorite synchronization accepts only existing Markdown files contained by the selected daily directory and rejects absolute paths, traversal, and escaping symlinks from both CLI input and saved state.
- **Narrow initialization scope**: platform-specific initialization is only an optional local setup path; it must not be repurposed for remote access, surveillance, or collection of another account's data.
- **Human-gated channel login**: Bot-channel login requires an interactive QR scan. Machine-readable login requests only preview the action; logout requires explicit confirmation and does not expose account identifiers.
- **Human-gated foreground listeners**: message listening, assistant foreground debugging, and process-memory key capture cannot be started through JSON execution. Their previews do not connect, scan, read messages, or call AI.

## Reporting a vulnerability

Do not open a public issue for a vulnerability involving database access, key extraction, message sending, credentials, path traversal, command execution or data disclosure.

Email the repository owner through the contact channel listed on the GitHub profile, with a concise description, affected version, reproduction steps and impact. Do not attach real databases, keys, access tokens, personal messages or unredacted screenshots. You should receive an acknowledgement within seven days.

## Supported releases

Security fixes are applied to the current `master` branch and the latest npm release when practical. Older releases may require upgrading.

## Handling local data

- Treat `~/.weflow-cli/config.json`, `.mcp.json`, `output/`, exported chats and every `*.db` file as sensitive.
- Treat `vault sync` as an external disclosure action. Preview it first; machine execution requires `--yes`, and its JSON response omits file names and remote URLs.
- Preview Vault initialization, semantic indexing, knowledge pipelines, and report generation before execution. Their machine interfaces require `--yes`; structured output omits local paths, selected names, and generated content.
- Never share encryption keys, wxid values, API keys, WeChat credentials or full process output in an issue.
- Before running an MCP client, confirm its approval model and the directory configured as its working directory.
- The project can make network requests for explicitly selected article, AI, WeRead and official-account workflows. Review your provider and account permissions before enabling them.
