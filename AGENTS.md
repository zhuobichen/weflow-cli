# Agent Maintenance Guide

Read this file and [docs/PROJECT_STATE.md](docs/PROJECT_STATE.md) before changing the project. Read [docs/DECISIONS.md](docs/DECISIONS.md) before revisiting an established design choice.

## Working Rules

- Treat local WeChat databases, exports, wxid values, API keys, tokens, paths, logs, screenshots, and assistant memory as sensitive. Never put them in source code, documentation, fixtures, issues, commits, or public command output.
- Keep work scoped and preserve existing user changes. Do not reset, discard, or overwrite unrelated files.
- Use placeholders in examples: `联系人A`, `示例群`, `YOUR_API_KEY`, and `YYYY-MM-DD`.
- Do not present local processing as a legal, account-safety, or platform-compatibility guarantee. Follow [SECURITY.md](SECURITY.md).
- Do not expand process-memory, key-extraction, or platform-automation details in public documentation without a security review.
- For future architecture-diagram visual assets, use GPT-image-2 as requested by the project owner, while keeping a maintainable source representation when practical and checking text, dimensions, and repository references after generation.
- **`npm publish` needs no confirmation step.** A `409 ... previously staged version`
  response is not a failure and does not mean the user must approve anything: the release
  lands on its own a few minutes later. Wait, then check `registry.npmjs.org/weflow-cli/<version>`
  and `dist-tags`. Do not ask the user for an OTP or to click an email. See docs/NPM-PUBLISH.md.
- **Push only after the user says so, and squash first.** Local commits may be numerous while a change is in progress; before pushing, fold them into one or a few meaningful commits. Do not rewrite already-published history unless the user explicitly asks - and then tag a backup (`backup-before-rebase`) and use `--force-with-lease`, never `--force`.

## Project Map

- `bin/weflow-cli.ts`: CLI entry point and command wiring.
- `src/core/`: database, key, native-library, and WeChat-client integrations.
- `src/services/`: configuration, chat access, exports, assistant, privacy, whitelist, and message workflows.
- `scripts/`: Python workflows for NT databases, daily reading, HTML generation, knowledge processing, and reports.
- `mcp-server/`: stdio MCP server.
- `test/`: Node built-in regression tests for pure utilities and privacy boundaries.
- Evidence packages and legal notes stay local; never commit real evidence or personal data.

## Verification

Run the narrowest relevant check first. TypeScript changes require `npm run build`; run `npm test` for the regression suite. For Python changes, compile or run the affected script's focused check. Before handoff, run `git diff --check` and inspect the staged diff for sensitive information.

Two rules for *claims*, learned from mistakes that shipped:

- **A negative claim needs a positive control, and the control belongs in the script.** Before writing down "0 hits", "no overlap", "never happens", run the same query against something known to match - and put that control *inside* the script so an empty control reports the comparison as void instead of reporting zero. The trap that has cost this project time three times: a `bytes` value read from one database compared against a `str` from another, where the intersection is empty by construction and "no overlap" looks like a finding.
- **An "A equals B" claim is only as strong as its off-diagonals.** Report both, not just the agreeing cell: `bit3 == (username in biz_info)` is worth writing down because the two *disagreeing* cells are zero, whereas a one-directional correlation says much less. And when reporting how many *kinds* of something there are, say what was counted (lengths, values, or rows) - they differ. **Put the degenerate-match screens inside the check function, not in the caller**: "A is constant", "B is constant", "A is nearly unique" and "both are zero" all make a relation look true, and the fifth time this bit us the screen existed - but lived in the previous script, so the next one forgot it.
- **Searching a short value in a large corpus needs a word-boundary requirement.** A five-to-nineteen-letter English word is *guaranteed* to appear somewhere inside a 192 MB binary or a chat log; measured here, 42 of 51 blob labels "matched" the client binary only as substrings of longer identifiers, and the two that stood alone were the honest ones. So when the claim is "this value comes from the client", require it to stand alone (no alphanumeric/underscore on either side), and say which corpus you searched - a negative in the obfuscated string pool says nothing about the plaintext string area.

## Documentation Protocol

- Update `docs/PROJECT_STATE.md` when a feature, supported platform, known limitation, active issue, or verification status changes.
- Add an entry to `docs/DECISIONS.md` when a decision affects security, data flow, compatibility, public API, or future implementation direction.
- Update `CHANGELOG.md` only for user-visible release notes; do not use it as an engineering diary.
- Every CHANGELOG version section records the WeChat version it was verified against (single source: `WECHAT_VERIFIED` in `src/services/wechatCompat.ts`; see D-130). Changing that constant requires a real-data verification, not just a running process.
- Keep architecture and operations documentation aligned when a behavior changes their stated contracts.
- When a change **fixes** something a document lists as a known/unfixed limitation (a "已知未修" table, a "limitations" or "does not exist yet" section), update that list in the same change. Nothing fails if you skip it - which is exactly why it happens: the code side grows a test (`test/config_keys_declared_test.py` is one), and the prose next to it drifts. A stale row there costs a reader a workaround they no longer need.
