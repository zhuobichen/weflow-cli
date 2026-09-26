# Extending WeFlow CLI

**Status: this is a checklist, not a plugin system.** There is no plugin loader, no third-party import surface,
and no `exports` field in `package.json` (`bin` is the only entry point). So "extending" this project today means
one of two things, and they are not equivalent:

1. **In-repo** - add the capability to this source tree (fork + PR). Every recipe below is about this path, and each
   one lists the places you must touch **and the test that will catch you if you miss one**.
2. **Out-of-process** - call the project from a program you keep elsewhere, through **MCP** (`mcp-server/`, 27 tools)
   or the CLI's `--json` surface. This is the only boundary that exists for code that does not live in this repository.

## Why there is no plugin loader

A plugin that runs inside this process would get, by construction, the same reach as the code it joins: the decrypted
database path, the config (including every API key), the filesystem, and the message channel. The project's standing
constraint is that **sending, assistant, MCP and cloud AI all keep an explicit permission boundary** - so the boundary
that must be crossed to extend this thing is a process boundary, not a function call. MCP is that boundary: it is
enumerable (`capabilities --json` lists what is exposed), scoped (per-tool confirmation for anything that leaves the
machine), and can be turned off without touching this repository.

A plugin/adapter mechanism is on the roadmap on purpose, with its constraint written down: third-party extensions must
not read configuration, databases or arbitrary files directly, and must be disableable individually
(`docs/ROADMAP.md`, "插件/适配器机制"). It is not implemented.

## Recipe A - a new assistant tool

The most common extension. All of it is in `src/services/assistantTools.ts` unless noted.

| Step | Where | If you skip it |
| --- | --- | --- |
| 1. Declare it | `TOOL_DEFS` | The model never sees it - dead code. A test fails. |
| 2. Implement it | `executeTool`, one `case '<name>':` | The model sees it, calls it, and gets `(未知工具: <name>)`. A test fails. |
| 3. Config prerequisite (only if it cannot run without a key) | `TOOL_REQUIREMENTS` - one entry, `{ key, why }` | The tool is offered on machines where it cannot work. That is the failure this project already measured once: a model tried both search tools, both errored, and the answer said "both search tools are broken". |
| 4. Cloud egress (only if user data leaves the machine) | In the handler: `if (ctx.requiresConfirm && args.confirm !== true) return confirmPreview(...)`; then claim it in `bin/weflow-cli.ts` under `safety.mcpSurface.callsCloudModels` **and** `requiresConfirm` | An external MCP caller sends the user's data with no preview. Tests fail - the assertion counts `ctx.requiresConfirm` occurrences in the source, so it cannot drift from the field. |
| 5. Keep it off MCP (only if it cannot do its job over that transport) | `MCP_EXCLUDED` - one entry whose **value is the reason** | It is advertised on a transport where it silently cannot work (`look_at_image` is the worked example). |
| 6. Coverage note | The header of `test/assistant-tools-branches.test.ts` states how many tools are executed; update it | The coverage claim becomes false while still reading as true. |

Notes that are easy to get wrong:

- **The description is the interface.** The model decides whether to call the tool from the description alone - keep it
  a sentence about *when* to use it, not a restatement of the name.
- **`parameters` must be a JSON-schema `object`.** An empty `properties` is fine for a no-argument tool.
- **Failures are `(...)`**, successes are not. `producedContent()` in the same file reads that convention, and the
  trace uses it. A success message that starts with a bracket has to be added to `PAREN_SUCCESS_PREFIXES` or it will be
  reported as "no content".
- **MCP needs no change.** The MCP surface is *derived* from `TOOL_DEFS` (`MCP_TOOL_DEFS`) - adding a tool adds it to
  both paths at once. That is deliberate, and it is also why the surface is not read-only: see `docs/MCP.md`.

## Recipe B - a new config key

A key must appear in **five** places, and each one fails differently:

| Where | Failure if missing |
| --- | --- |
| `src/services/configService.ts` - the `CliConfig` interface | Type errors elsewhere; the compiler catches this one. |
| same file - the default literal | - |
| same file - the `load()` mapping | The value silently disappears on the next start. The user sees "I set that already". |
| same file - the `clear()` reset | `config reset` **loses** the old value and `getAll()` then reports it as never set. |
| `bin/weflow-cli.ts` - `configurableKeys` | `config set` refuses the key outright. This one happened: `dashscopeApiKey`. |

`test/config-keys.test.ts` reads all five and fails if a key is missing from any of them. It is a static text
assertion, so changing the *format* of those literals turns it red - which is the intent, not a nuisance.

Keys that hold secrets belong in `ENCRYPTED_KEYS` in the same file (they are stored encrypted at rest; reading the raw
file shows ciphertext, not the value).

## Recipe C - a new CLI command

Two places in `bin/weflow-cli.ts`: the `program.command('...')` registration, and - if it should appear in the
interactive menu - `showInteractiveMenu()` plus the `switch (action)` that maps a menu choice back to the command.

`test/cli-menu.test.ts` keeps those in sync: menu entries and `switch` cases must match **in both directions**, and
every `runCmd('x')` / `runSubCmd('p','c')` the menu calls must name a command that actually exists (that test asks the
CLI's own `--help`, because the source cannot tell top-level commands from subcommands - 64 `.command('...')` call sites
against 44 real commands). This matters because both failure modes are silent: a menu entry with no `case` does nothing
when picked, and `runCmd` is written as `if (cmd) await ...`, so a renamed command makes the entry do nothing too.

Two conventions that hold across the command surface: every command that a script or an AI might drive has a `--json`
mode, and anything destructive is two-phase (`--dry-run` preview, then `--yes`).

## Recipe D - a new hand-written MCP tool

Three places: the `ListTools` table in `mcp-server/index.ts`, a `case` in its `CallTool` switch, and the table in
`docs/MCP.md`. `test/tool-registry.test.ts` compares the doc table against the actually-served set (hand-written +
derived) and fails on a missing row, an extra row, or a duplicate - all three have happened or nearly happened.

Prefer a **derived** tool (Recipe A) unless it needs something the assistant layer cannot provide. The hand-written
list exists for tools with no assistant counterpart (article fetching, the versioned `weflow-message/v1` export).

## Recipe E - a new Python workflow

Scripts live in `scripts/` and are invoked through `src/services/pythonBridge.ts` (`runPythonJson` when the script
prints JSON). Three rules that this project has been bitten by:

- **Encoding: the bridge handles it, your hand-run does not.** Every script spawned through the bridge gets
  `PYTHONIOENCODING=utf-8` (`src/utils/pythonProcessEnv.ts`), so stdout is UTF-8 there and `json.dumps(...,
  ensure_ascii=False)` - what the scripts use - round-trips correctly. Running the same script by hand on this machine
  gives a **GBK** stdout: Chinese comes out as GBK bytes (mojibake if you were expecting UTF-8) and a character
  outside GBK (`✓`, an emoji) raises `UnicodeEncodeError` and kills the run. 28 of the 49 scripts call
  `sys.stdout.reconfigure(encoding='utf-8', errors='replace')` in `main()` so both paths behave the same; if you add a
  script you intend to run by hand, do the same. A probe that forgets to set that env var will show you a decoding bug
  that does not exist in the real path.
- **Fail loudly.** Exit non-zero, or return a JSON object whose `success` is false with an `error` string. "No JSON on
  stdout" must never be read as "no results" - that conflation is why `get_todos` distinguishes "no pending todos" from
  "extraction has never been run".
- **State goes in over stdin, not argv.** Command lines are visible in the process list; the drafting tool passes the
  conversation over stdin for exactly that reason, and `semantic_search.py` takes the query through the environment.

Tests for Python live in `test/*_test.py` and run on the same filesystem as everything else (`npm test` covers
TypeScript; `python -m unittest discover -s test -p '*_test.py'` covers these).

## Recipe F - a new knowledge source

The knowledge base is fed by "material that carries `[[wikilinks]]`": `compile_wiki` scans a source directory
(`--source`, already a flag - no code change needed to add a source), aggregates every wikilink into concepts, and
writes concept pages into the Vault. Two sources exist: `output/biz-daily` (articles) and `output/chat-notes`
(conversation cards, from the `chat-notes` command).

To add a third, the producer must satisfy the consumer's contract exactly:

| The consumer (`scan_articles`) needs | Notes |
| --- | --- |
| `*.md` under the source dir, with frontmatter | `title`, `source`, `topic`, `tags` are read; `README.md` is skipped |
| A `## AI 摘要` (or `## 深度解析`) section | the paragraph after it becomes the note's summary |
| `[[name]] — description` lines | anywhere in the body; the description is what gets fed to page generation, so keep it clean (no trailing labels) |

**Two rules that are not obvious from that table.** Links should be rendered by your code, not written by a model -
the consumer collects *every* wikilink in a body and cannot tell who wrote it, so a stray one invents a concept
(this is why `chat_notes.plain()` strips `[[...]]` from model-authored free text). And a wikilink whose description
is polluted - a trailing `（话题）`, a stray marker - silently degrades every concept page it feeds, because that
description is the material for the page.

The guard to copy: `test/chat_notes_test.py` writes a card and then runs the **real consumer**
(`compile_wiki.scan_articles`) over it, asserting the links and descriptions come back. That is the shape of every
producer/consumer pair in this project - do not assert on the markdown text, assert that the other side reads it.

## What will catch you

| Invariant | Test |
| --- | --- |
| Declaration table and `executeTool` cases match, **both directions**, names unique, every declaration usable | `test/tool-registry.test.ts` |
| Availability rules and the MCP exclusion table name real tools | `test/tool-registry.test.ts` |
| `docs/MCP.md`'s table equals the served MCP surface, no duplicates | `test/tool-registry.test.ts` |
| Every inbound-egress tool carries the `confirm` gate, and `capabilities --json` agrees | `test/assistant-tools.test.ts` |
| A config key exists in all five places | `test/config-keys.test.ts` |
| The MCP subset contains no write/send/publish tool | `test/assistant-tools.test.ts` |
| Panel IPC surface is exactly the declared method list | `test/panel-packaging.test.ts` |
| Interactive-menu entries match `switch` cases, and the commands they call exist | `test/cli-menu.test.ts` |
| A knowledge source's output is readable by the wiki aggregator, links and descriptions intact | `test/chat_notes_test.py`, `test/compile_wiki_test.py` |

## Red lines

These hold for any extension, and the full statements live in `AGENTS.md` and `CLAUDE.md`:

- Local data is the user's. Never put database paths, keys, tokens, account identifiers, chat content, exports or real
  local paths into source, docs, fixtures, issues, commits or command output.
- **Sending is structurally unreachable from a model-driven path.** No extension may add a path that sends a message,
  fills an input box, or drives another application's UI on the model's behalf.
- Anything that leaves the machine goes through `privacyGate`. Redaction, not discretion.
- Do not widen an assertion or a test to make a number look better. A static check that has to be relaxed is usually
  reporting a real change.
- Do not record a guess as a verified fact. When something has not been measured, say so where the claim is made.

## What does not exist yet

Named here so that the gaps are not mistaken for omissions:

- **A plugin loader / adapter interface.** Roadmap, not implemented (see above).
- **A public import surface.** No `exports`, `main` or `types`; the published package is a CLI, and `src/` is shipped
  without a stability promise.
- **A versioned extension contract.** The versioned contracts that exist are data contracts (`weflow-message/v1`,
  `weflow-sync/v1`), not code contracts.
