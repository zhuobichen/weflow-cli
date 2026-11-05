# WeFlow on Linux

> Last checked: 2026-11-05 · Ubuntu 24.04 / Python 3.12 / Node 22 · WeChat Linux **4.1.13.26**
> (real login, real data directory).

This page answers the questions people actually ask. Details that are not listed here are
in the Chinese page [`LINUX.md`](./LINUX.md); the rest of this file is a faithful summary.

## Can I export WeChat chats on Linux?

**Yes.** Five formats: HTML / Markdown / JSON / TXT / Excel.

```bash
weflow-cli export "Contact A" html        # also markdown / json / txt / excel
```

Two preconditions: WeChat Linux 4.x (verified on 4.1.13.26) and **one login authorization**
to obtain the shared passphrase (see below). Output goes to `~/.weflow-cli/output/`;
nothing is written back into the WeChat directory.

## How does WeFlow read the WeChat Linux 4.x database?

Since WeChat Linux 4.1.12.26 the client uses a **shared passphrase + per-database salt
derivation**; per-database key/salt pairs no longer exist in memory. The flow is:

```bash
weflow-cli capture-passphrase    # one login authorization; captures and verifies
weflow-cli init                  # derives per-database keys into the config
weflow-cli sessions              # sanity check: sessions must list real chats
```

**The database is opened read-only.** Without a `-wal` file it is read with `immutable`;
with one, WeFlow reads a temporary copy. Neither path writes `-wal` / `-shm` next to the
WeChat databases, and neither changes client state.

## Does it need root?

Reading, querying, exporting and daily reports **do not**. Only *capturing the passphrase*
attaches to the running client, which Yama restricts for ordinary users — WeFlow then
retries once with `sudo` and says so. To forbid that:

```bash
weflow-cli capture-passphrase --no-sudo   # fail instead of escalating
```

That step runs `gdb -p <wechat pid>` once to read a single register. It does not write the
WeChat directory and does not change system configuration. If you already have the
passphrase, you never need it.

## Where do the outputs go?

The output root must be writable; priority is `WEFLOW_OUTPUT_DIR` → the package's own
`output/` (when writable) → `~/.weflow-cli/output`. For a global install the package
directory belongs to root, so the last one wins.

```
~/.weflow-cli/output/biz-daily/<date>/    daily report (index.html / article.html / topics)
~/.weflow-cli/output/wechat-vault/        Obsidian vault
~/.weflow-cli/output/knowledge-graph-3d.html
~/.weflow-cli/output/chat-notes/          per-conversation cards
~/.weflow-cli/output/article-notes/       per-article cards
~/.weflow-cli/output/.cache/              fetch cache
```

## What works, what does not

| Capability | Linux | Notes |
| --- | --- | --- |
| `check` environment report | ✅ | dependencies, paths, and how many databases are readable |
| Directory discovery / account scan | ✅ | reads `~/xwechat_files` |
| Process detection | ✅ | matches `wechat` / `weixin` (including AUR / Flatpak wrappers) |
| Version detection | ✅ | from `~/.xwechat/uh/uh.dat`; says "unknown" instead of guessing |
| Reading local databases | ✅ | with the passphrase: `sessions` / `messages` / `daily` verified |
| Daily reports | ✅ | from local databases, or external sources without any key |
| Scheduled runs | ✅ | `scripts/daily_task.sh` via cron or a systemd timer |
| Knowledge base / MCP / exports | ✅ | platform independent |
| Concept graph (`wiki graph`) | ✅ | 3D and `--flat` 2D both verified |
| WCDB data service | ⚠️ | needs the native library; optional |

## Verified vs. not verified

```
Verified:   Ubuntu 24.04 + WeChat Linux 4.1.13.26 (20 of 21 databases passed page checks)
Not tested: other distributions (Debian / Fedora / Arch), Windows ARM, WeChat 4.2+
```

These are measurements, not a compatibility promise. Run `weflow-cli check` first; it
reports exactly which step fails.

## Related documents

- [`LINUX.md`](./LINUX.md) — full Chinese reference (dependencies, systemd, CI, known traps)
- [`../OPERATIONS.md`](../OPERATIONS.md) — initialization and troubleshooting
- [`../SECURITY.md`](../SECURITY.md) — data handling and boundaries
