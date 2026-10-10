# Changelog

The npm package is published separately from GitHub. It may lag behind the `master` branch until a release is published.

**The GitHub Releases page is cut by hand and can therefore lag npm by several versions.** That decoupling is
deliberate (`docs/RELEASING.md`: "GitHub Release 与本清单解耦：源码、编译包、npm 三者版本差异要对用户可见"), and it comes
with a requirement: the difference has to stay **visible**. So if a version you expect is missing from the Releases
page, check the two sources rather than assuming it was withdrawn:

```
npm view weflow-cli version     # 权威的最新已发布版本
gh release list                 # GitHub 上手工建过哪些
```

Everything published to npm has a section in this file. **No version numbers are written into this paragraph on
purpose** - a hand-written "latest is X" is exactly the claim that goes stale here; those two commands never do.

All notable user-facing changes are recorded here. This project follows [Semantic Versioning](https://semver.org/).

## Unreleased

- **Search can now be scoped to one of the two knowledge bases, and says which one it searched.** Following the
  namespace split above, `search`, `chat`, `vault search`, `vault rag` and the assistant's `search_semantic` /
  `search_knowledge` all take `--line wiki|chat|all` (or `line: "wiki"` for the assistant tools). **The default is
  the article line** because the two bases were split on purpose; `all` merges them explicitly. Every result says
  which line it came from, and an empty result distinguishes "nothing matched" from "this line has no records in the
  index at all". The functions underneath default to `all` instead, so a caller that forgets the argument searches
  too much rather than silently half. Two notes: with the semantic index the line is a mask applied **before** the
  top-k sort over one shared index (one index, filtered at query time - `search-index` deliberately has no `--line`,
  since a per-line index would make `all` a lie), and `semantic_search.py search`'s JSON changed from a bare array
  to an object (`line` / `results` / `indexCounts` / `note`); the assistant now reports an unrecognised shape
  instead of degrading it to "no results". Scoped to the keyword path on this machine, which is the one that runs
  here: `vault search "微信" --line chat` returns chat cards and chat concept pages only, `--line wiki` article
  titles only.
- **The article knowledge base and the chat knowledge base are now two concept namespaces, as requested.** A concept
  name may hold a page on both lines - `wiki compile` no longer skips a name because the other line already has one -
  and every place that turns a name into a key now qualifies it with the line (`wiki:DeepSeek`), so the merged graph
  keeps both instead of one silently shadowing the other. `wiki lint` reports the overlap in its own
  `crossLineSameName` section (by design, not an error) and `duplicateTitles` now only covers collisions **inside**
  one line. Two honest notes: the two lines still share one vault root, so a bare `[[DeepSeek]]` in Obsidian is
  genuinely ambiguous until the roots are split - that ambiguity is now *reported* rather than avoided by not
  creating the page (the official rule is "a bare link with duplicates resolves deterministically but not
  necessarily to the one you meant"; `[[Chat/Concepts/DeepSeek]]` is unambiguous) - and 36 such pairs already
  existed in this vault, so the invariant this replaced was never true of the data. Verified on the real vault:
  36 shared names, and `wechat.get_concept` now names both lines for each of them while 1,491 chat-only names get no
  extra note; the 2D viewer is exercised for real in jsdom, the 3D one only syntax-checked (it needs WebGL).
- **`messages` now says when an ID is one nobody knows, instead of looking like "no messages".** An input shaped like
  an id (`wxid_…`, `…@chatroom`, `…@openim`) is passed straight through, so a typo produced exactly the same output as
  a real conversation that happens to be empty: `未找到消息` and exit code 0. It still exits 0 - "no messages" is not
  an error and scripts depend on that - but when the id appears in neither the session list nor the contacts, the
  output adds a line naming the id and pointing at `sessions` / `contacts`, and `--json` carries an optional `note`
  field beside the unchanged `success: true`. Verified with the real command:
  `messages wxid_zzz_not_a_real_id --json` → `{"success":true,…,"note":"这个 ID 既不在会话里、也不在联系人里：…"}`.
- **The OC Bot channel can save inbound media to disk, and it stays off until you say otherwise.** Inbound images,
  voice notes, files and videos used to arrive as an empty `filePath` and nothing else, so the assistant was told
  "[image]" and no more. `weflow-cli config set wechatMediaDownload true` makes the channel download each item from
  the CDN and decrypt it locally with the scheme the local image files already use, writing it under
  `output/wechat-media/` (the directory can be moved with `WEFLOW_WECHAT_MEDIA_DIR`). It is **off by default** because
  turning it on is a network access the user should choose. Three things are deliberately decided locally: the file
  name (a name sent by the server is reduced to a sanitised basename, so `../` cannot escape the directory), a 25 MB
  per-item cap, and the failure behaviour - a download that fails, comes back empty or exceeds the cap writes
  **nothing**, leaving `filePath` empty plus one log line naming the reason, rather than a 0-byte file that the rest of
  the pipeline would treat as a picture. **Observed on a real inbound message** (2026-10-10): after a fresh `login-wechat`, a
  text message was answered, then a real image came through as 27,637 bytes with PNG magic - and PIL opened it at
  798x513 RGB, so the bytes are a real picture and not just ciphertext that happened to land on disk. **Nothing reads the saved
  path yet either**: the file is on disk and `components[].filePath` points at it, but the assistant still receives
  only the text and the kind, so it cannot act on the picture. Handing images to a model is a separate egress
  decision, not a side effect of this switch. **And the switch downloads from any sender**: it runs while the polled
  message is parsed, before the assistant decides whether to answer, so the allowlist gates replies rather than
  downloads - turn it on only if that is what you want.

- **Two scripts crashed when their output was redirected or piped, and the crash was invisible from inside this
  repository.** `scripts/quality_eval.py` prints `⚠️` and `scripts/wechat_emoticon.py` prints `✓`; with stdout at the
  locale encoding - which is what a by-hand `python scripts/x.py > out.txt` gets on a Chinese Windows console - the
  first character GBK cannot encode raises `UnicodeEncodeError` and the run dies with exit code 1. Nothing here saw it
  because this environment and the bridge both export `PYTHONIOENCODING=utf-8`; the reproducer only appears under
  `python -E` (measured: `sys.stdout.encoding` becomes `gbk`, locale cp936). Both scripts now call
  `sys.stdout.reconfigure(encoding='utf-8', errors='replace')`, like the 39 others already did, so their output no
  longer depends on the caller's environment. `test/script_stdout_encoding_test.py` guards the rule statically (AST,
  not a subprocess run - a spawn-based test is a false negative on this machine) and carries its own controls
  (`DECISIONS.md` D-090).



- **`contact-schema` can now read contacts as well as rooms - `--contacts` - and the flags it declares are now guarded.**
  The command has always decoded only *verified* fields; three more were verified in the schema work, so they now have an
  outlet instead of living only in the notes: `region` (country/region code, ISO 3166-1 alpha-2), `bizType` (equal to the
  row's `biz_info.type`) and `updatedAt` (the row's last-updated epoch seconds, with `0` treated as "unset" rather than
  1970). Two things are deliberately **not** printed: `#4`/`#9` are a digest of the account's own profile text - content,
  not a field name - so they appear only as a length inside `unrecognized` (a test asserts the text itself never reaches
  the output), and the 44-row OpenIM shape is reported as `kind: 'openim'` without decoding its fields (the
  discriminator is "every field number is <= 9", not "it has `#1`" - one row of that family has no `#1`, and a
  full-population check caught it being classified as a contact). Separately, an
  audit found a real hole next to it: the TS layer declares flags and forwards them **by hand**, and nothing tested that
  step - a dropped `--contacts` would be accepted by the CLI and silently read *rooms* instead. `test/contact-schema-cli.test.ts`
  now asserts that every declared option is forwarded inside the action (verified by mutation: replacing the forwarding
  with `[]` turns it red) and that `--contacts` is a flag the CLI actually accepts. `npm test`: 858 -> 860
  (`DECISIONS.md` D-088).



- **A pairwise sweep inside the blob: three real results and three trivial ways to fake one.** Asking "does field A's value
  determine field B's" across the 940-row main proto gave 398 "determinations" before filtering and 16 after - the
  difference being three degenerate shapes that all make the relation vacuous: A constant, **B constant** (the first
  pass was full of `has15`/`v16`/`v23`, which are always the same), or **A nearly unique** (`#41` has 565 distinct
  values, so it "determines" everything). The surviving results: on biz rows (`#13` present, 687 of them) `#2`, `#12`,
  `#17`, `#22`, `#24` are always 0 and `#25` is always absent - these small flags only ever appear on non-biz rows;
  `#10 = 0 iff #11 = 0` is confirmed a second time by an independent method; and `#2` correlates strongly with that
  URL (123 of the 144 rows with `#2` in {1,2} carry it, versus 18 of 109 rows with `#2 = 0`) without being equivalent.
  The non-biz 253 rows also divide cleanly: **78 are `@chatroom` group rows** (`#2 = 0`), the other 175 are individuals
  (`DECISIONS.md` D-087, `docs/CONTACT_DB_SCHEMA.md` §7).



- **`#27`'s second field is a URL, and the notes stop there on purpose.** It is present in 141 of 940 rows, always
  `http://`, 107-110 bytes long, and drawn from only **3 hosts** - none of which appears in any column of
  `contact`, `biz_info` or `chat_room_info_detail` (against 3,914 non-empty `big_head_url` and 691 `brand_icon_url`
  rows, zero overlap), so it is not any CDN this machine knows. The rows are all outside `biz_info`. The meaning stays
  UNKNOWN and digging further is deliberately declined: it would mean reading URL path content to unlock nothing else.
  A reading error of mine is corrected alongside - "14 variants" was the number of *lengths*, not of values; the
  min/max/count column was always a length column (`DECISIONS.md` D-086, `docs/CONTACT_DB_SCHEMA.md` §7).



- **"It parses" is not "it is a message" - and two earlier notes are corrected by that.** Walking every
  length-delimited field one level deeper shows that *text* fields parse too (`#4`, `#5`, `#9` "walk" in 294, 252 and
  393 of 940 rows because random bytes form legal tags), so the test is not parseability but **whether the shape
  repeats across the whole set**: `#14` is identical in **830/830** rows (`{#1: 0/1, #2: 13 bytes}`) and `#27` in
  **937/937** (`{#1, #2: an 84-141 byte text, #3, #4, #5: a mask}`), while `#6` (555/940) and `#7` (473/940) fall
  short - so the standing line "`#6`/`#7` walk as nested messages" does not hold. Also mapped: `#33`/`#36`/`#15`/`#20`/
  `#21`/`#28` (and most `#26`) are **empty shells** whose presence is the information, which is consistent with the
  group of fields that always co-occur (`DECISIONS.md` D-085, `docs/CONTACT_DB_SCHEMA.md` §7).



- **A third field is pinned down: `#41` is the row's last-updated time; `#43` turns out not to be an id.** `#41` holds
  epoch seconds in 691 rows (127 of them an explicit 0), correlates with `contact.id` at **Spearman 0.945** with no
  duplicate values among the 564 non-zero rows, and - decisively - **40 rows carry ids below 1500 together with 2025/2026
  timestamps**, which no "creation time" could produce. So it reads as "this row was updated", with most rows never
  updated (which is why the correlation is high but not 1). That is a local inference, not a vendor name: none of the
  22 columns can hold a time. `#43`'s old description ("a 22-byte id-like value") is wrong - decoding its interior
  gives a small structure with only **10 variants**, the largest covering 448 of 628 rows, so it looks like a set of
  capability/scope codes rather than an opaque id. Two negatives ship with them: none of the twelve small varint
  fields is determined by the row-level attributes tried (account class, `local_type`, `flag`, `verify_flag`,
  `is_in_chat_room`, `chat_room_type`, `chat_room_notify`, presence of `encrypt_username`), and my own first pass at
  that scan was flawed because it included a *constant* attribute as a candidate discriminator - which "determines"
  every field and says nothing (`DECISIONS.md` D-084, `docs/CONTACT_DB_SCHEMA.md` §7).



- **Two more entries in the value-domain table, one positive and one negative.** Verified: `#10 = 0` and `#11 = 0` are
  *the same 129 rows* (811 / 129 with no off-diagonal), so they share one "unset" state and must not be read as two
  independent flags. Refuted: `#19`'s 0-10 count-like values are not "the number of repeats of some sub-field" - the
  best of the ten candidate sub-fields matches only 104 of 940 rows, which rules out a whole class of explanations
  without yet naming the field (`DECISIONS.md` D-083, `docs/CONTACT_DB_SCHEMA.md` §7).



- **A second field is named, and every varint field's value domain is now measured.** `#13` equals that row's
  `biz_info.type` - **687 of 687 rows, no counterexample**, across all five values - which also explains the standing
  fact that `#13` appears exactly when `verify_flag != 0` (i.e. when the username is in `biz_info`): the blob simply
  carries a copy of the account's service type, and non-biz rows have no value to write. The same pass filled a real
  gap: the notes had "value range unmeasured" next to most varint fields, and measuring them turned up `#10`/`#38` as
  `0` / `-1` sentinels (`4294967295` is -1 unsigned), `#8` with 23 values shaped like a bitmask, `#12`/`#17`/`#18`/
  `#22`/`#24` as flags and tiny enums, `#16`/`#23` always 0, `#37` using only bits 8 and 11, and **`#19` as an 0-10
  value that looks like a count** (menu-button count already excluded) - a new, clearly-shaped open item. The lesson is
  recorded with it: "does the field have a value" and "what values does it take" are different questions, and only the
  second one names fields (`DECISIONS.md` D-082, `docs/CONTACT_DB_SCHEMA.md` §7).



- **The first field of the 37 gets a name: `#5` is a country/region code.** It holds exactly two uppercase ASCII
  letters in 717 rows (223 more carry an explicit empty string - a pattern this proto uses a lot) with 25 distinct
  values, and all **25 fall inside the 249 ISO 3166-1 alpha-2 codes** - if the letters were arbitrary the chance of
  all 25 landing there is about **1.4e-11**. Two independent confirmations came with it: the **mode is `CN`, covering
  94.0%** of rows, which follows from "most accounts on this machine are Chinese" without using the code table at all;
  and only **15 of 25** match ISO 639-1 language codes (which are lowercase) - so it is a *region*, not a *language*.
  That second check is the point of the method: the code table is hardcoded by me, so a "25/25 hits" result would
  otherwise confuse an error in my own oracle with a fact (`DECISIONS.md` D-081). The field number is also a trap worth
  naming explicitly: `#5` in `chat_room`'s blob is the member-status-bit-11 projection (D-070), while `#5` in
  `contact`'s blob is this region code - say which table you mean. Nothing in the code needs to change: no code reads
  `contact.extra_buffer`'s fields today.



- **Two of the unnamed fields finally have content: they are the account's own profile text, aggregated.** Flattening
  `biz_info`'s `external_info` and `brand_info` into 16,294 distinct leaves and asking whether a blob field *contains
  the leaf from its own row* gives **336 same-row hits against 7 next-row hits** - so the containment is real, not
  coincidence. The split is clean: `#9` carries the subject name and certification description
  (`RegisterSource.RegisterBody` 147, `VerifySource.Description` 67) while `#4` carries **menu button names**
  (`MMBizMenu.button_list[].name` 31 plus 21 sub-button names), and both also carry trademark and verifier names. So
  `#4`/`#9` read as "a digest of this account's own profile strings" - not a name, and containment is not equality.
  The control is the point: the same query run against the *next row's* leaves is what separates signal from noise, and
  it is what killed `#5`'s 22 apparent matches (a two-byte value is "contained" in almost anything; only leaves of 8+
  bytes survive). Hit rates here now come with the counter-rate that could have produced them
  (`DECISIONS.md` D-080, `docs/CONTACT_DB_SCHEMA.md` §7).



- **The per-room counter has no second copy anywhere locally, which closes its file from three directions.** A sweep of
  all 24 databases (207,156 rows) for the counter's value or its offset found **0 meaningful hits** - but only after the
  criterion was tightened to "same object": a column counts only if the row it sits in *names that room* (in a
  `username`/`room_id`/`chat_name` key column). The loose version of the same search returned **279 hits, every one a
  small-integer collision** (0, 1, 2 and 10 are everywhere, and the rooms sitting at their base keep contributing 0 as a
  candidate) - so the mirror of D-076's rule applies: before reporting a *hit*, ask how likely a collision is. The
  negative ships with the three things this project now requires of any "0 hits" claim: a positive control (rooms at
  base did match zero columns, and `session.last_msg_type = 10000` matched an integer column, so the matcher works),
  what was skipped (735 `Msg_*` tables, plus tables keyed by a hash rather than a username), and the scope (24
  databases, 207k rows). With the earlier two results - it is not a server sequence, and it is not a member-change
  event count - the counter is now refuted from three independent directions, leaving only an off-machine reference
  (another machine, another version, or an older copy of the database) as a route to what one step means
  (`DECISIONS.md` D-079, `docs/CONTACT_DB_SCHEMA.md` §6.1 ⑩).



- **The per-room counter's "one step" is not a member-change event - and the note saying it "needs a second
  timepoint" was framed too narrowly.** The room message table carries its own timestamped event log (member-change
  notifications, base `local_type == 10000`, 1,764 of them locally), so the question could be tested without a second
  snapshot. It fails on all three candidate readings (n = 71): the counter's offset above its base correlates with
  member-change event count 0.168, joins 0.143 and net change 0.144, while the existing member-count correlation is
  0.911. The refutation is not an artifact of the retained window: five rooms created after 2026-08 have their whole
  history in-window and still exceed every possible join count - for `chat_room.id = 3906` (36 members, whole life
  2026-08-31 to 2026-09-28) the offset is **104** against **48** messages of that type in total (9 classified as
  member changes). Two smaller facts came with it: proactive-leave notifications are **0 of 1,622** (WeChat does not
  send them to ordinary members), so the "net change" reading was never measurable from the data side - check that
  both halves of a two-sided test exist before weighting them equally - and the rooms sitting exactly at their base
  are the same **7** under two independent measurements, one of which contains a disband event yet still reads 0
  (`DECISIONS.md` D-078, `docs/CONTACT_DB_SCHEMA.md` §6.1 ⑨).



- **The "read the client binary for field names" route is closed, and the reason it looked closed once before was a
  false negative.** The `ContactExtData` field names are not recoverable from this machine's binaries, and now the
  negative is structural rather than "not found": the four `micromsg.*` type-name strings have **zero** 4-byte
  references anywhere in the file (only `ChatroomDetailInfoExtData` has two), the pointers next to a type name lead to
  either a 96-byte cipher block or to code, and a whole-file sweep of 43,411 name slots found **no four-member group
  and no ~37-name group**. One hard detail: names that sit next to each other are not one message's field table -
  `AnnouncementEditor` and `ChatRoomStatus` are adjacent to three referenced names yet are referenced **zero** times.
  The addressing lesson is the reusable part: this `Weixin.dll` is **64-bit** (imagebase `0x180000000`) with
  `VA != PRAW` for every section, and the descriptor tables store **file offsets** - searching for a true RVA or VA
  returns **0 for every name**, which is exactly how the earlier "these names are never referenced" note was born.
  So: before writing down any "0 hits / never referenced" result, check which address form you searched, and run a
  positive control (here, `InfoVersion`'s 4 hits). Two smaller results: the client's snake_case name arrays *do*
  preserve SQL column order (`chat_room_info_detail` 8/8, `contact` 18/18) but `biz_info`'s does not, so array
  position must not be used to infer proto field numbers. Three speculative field names ship as **speculation only**,
  each with its own falsification test (`DECISIONS.md` D-077, `docs/CONTACT_DB_SCHEMA.md` §3.3/§7).



- **`contact.verify_flag` is decoded - and it never was about friend requests.** It is a bitmask with 9 observed
  values (three single-row values were missing from the earlier list). Bit by bit: **`bit3` (value 8) is true iff the
  row's `username` is in `biz_info`** - 687/687, both directions, no off-diagonal - and every non-zero value carries
  it, so "8" *is* "non-zero". `bit4` (16) holds iff `VerifySource` is present (265/265, 2 exceptions the other way),
  `bit8` (256) iff `PersonVerifyInfo.VerifyDesc` is present (12/12), `bit9` (512) implies `PersonVerifyInfo` (65/65),
  and `bit4 ⊥ bit9` - institutional and personal verification are mutually exclusive. Three conditions turn out to be
  three faces of the same 687 rows: `extra_buffer` carries `#13` iff `verify_flag != 0` iff the username is in
  `biz_info` (and none of those rows is outside the large proto). That is also *why* the older note "`verify_flag != 0`
  implies not in any group" held - they are all biz/system accounts. Read as friendship state, the flag is refuted by
  counting: `!= 0` with no `biz_info` row = 0, with a remark = 0, as a group member = 0, and only 5 of 2946 `wxid_`
  contacts are non-zero.

- **Two notes corrected: `chat_room_info_detail`'s `#1` is a real nested message, and the three `openim_*` blobs are
  not one shape.** The `#1` body (empty in 72 of 77 rows, non-empty in 5) walks cleanly as `DetailList -> Entry`
  (`Entry` has field numbers 1-7 in all 8 entries, with `#2` a member `username` - 8/8 present in `contact`, 7 of them
  exactly the room's `owner` - and `#5` valid UTF-8 free text), so "a chunk of binary" was wrong. Of the three
  `openim_*` tables, the first two carry "`#1` plus {key, value} pairs", `openim_acct_type`'s blob `#1` is
  byte-identical to that row's `acc_type_id`, and **`openim_wording.ext_buffer` is zero-length in all 21 rows** - the
  old line had merged all three into "3-4 fields with nesting". A related field got a *partial* name and is recorded
  as partial: `#9` is byte-identical to the row's registered entity name (`RegisterSource.RegisterBody`) in 213 of 567
  rows and merely *contains* it in another 21 (41% together) - but 333 rows have nothing to do with it, so it is not
  "the subject-name column" and must not be used as one (`DECISIONS.md` D-076, `docs/CONTACT_DB_SCHEMA.md` §3/§6/§7).
  The rule that came out of that: when a hit rate is reported, ask what the *misses* are - only an asymmetry against
  a field that never matches can support a name.



- **`contact.extra_buffer` turns out to hold two message types in one column, and the OpenIM half is pinned by
  cross-table foreign keys.** The 44 rows that were described as "a different proto" are **OpenIM-only**: field
  `#1` byte-exactly equals `openim_appid.app_id` and `#2` equals `openim_wording.wording_id` (43/44 each), and the
  nested `#4.#2` is JSON whose key is always `custom_info` (43/43). The carriers are 42/44 `@openim` usernames with
  `local_type` in {5,6} - and every `local_type` 5/6 row in the database is in that family (both directions), so the
  column's type follows `local_type`. Two counts are corrected with it: the "46 small rows" is **44** by field number
  (the other two carry field numbers `#10`/`#40` and belong to the large proto), and `stranger.extra_buffer` was
  labelled `OpenIMContactExtData` in the type table - that is almost certainly wrong (its skeleton matches
  `contact`'s main body, i.e. `ContactExtData`); the real OpenIM blob is the 44 rows. Two more exact relations came
  out of the same pass: **`#13` is present iff `verify_flag != 0`** (687 / 253, no off-diagonal), and `#33` non-empty,
  `#36` present and `#27`'s subfield 3 are the same 824 rows. Also worth keeping methodologically: those `#1`/`#2`
  names came from **cross-table byte-exact hits**, not from reading the client binary - a route the "field names are
  unobtainable" note had not counted (`docs/CONTACT_DB_SCHEMA.md` §3/§3.1/§7, `DECISIONS.md` D-075).

- **Member `status` bits got an external signal (does this member ever speak in this room?) and almost nothing
  survived.** The one solid member-level footprint is **bit13**: carriers are systematically quieter *within the same
  room* (P(sent) 0.087 vs 0.208, within-room MH OR 0.40, 19/20 rooms same direction) - so the earlier "no clean
  member-level discriminator" line is withdrawn for that bit. The rest went the other way: **bit4 ↔ has a group
  nickname** shrinks from raw OR 6.19 to **MH 2.05** once the room is controlled (~2/3 of it was "rooms with a
  nickname culture"), and **bit3 is just as strong within-room**, so it is not bit4-specific; "bit4 carriers are
  older members" does not hold within-room (0.353 vs a 0.333 baseline, p=0.42); "bit3 carriers talk more" is a room
  effect (within-room signs 15/13/11, mean difference -0.002); and G's apparent activity comes from **one room**
  (8 of the 10 talking G carriers are in the same room, while the other six G rooms have zero). Two traps are
  recorded too: the two "join-order" proxies (`chatroom_member.rowid` percentile and blob position percentile) are
  the **same ordering in 76/77 rooms** (not two independent signals), and small samples lie about value ranges -
  `#41`, seen as "2021-2024" from a partial sample, is really **2019-2026 with 70% in 2026** (plus 127 rows
  explicitly written as 0), i.e. more like a "last updated" stamp (`DECISIONS.md` D-075,
  `docs/CONTACT_DB_SCHEMA.md` §6/§7).



- **The member `status` field is a set of small fields rather than a pile of flags, and `contact.extra_buffer`'s main
  body is a 37-field proto - two earlier notes corrected.** (a) The 27 observed member `status` values decompose into
  non-overlapping bit ranges (`bit0 | A<<3 | bit11 | bit13 | G<<20`). `A` (bits 3-4) is a **2-bit field**, not two
  flags (bit3&bit4 co-occur 46 times against an expected 19.7), and its strongest - not definitional - correlate is
  having a group nickname (monotone 0.26→0.78 across A=0→3, but 31% of carriers have none). `G` (bits 20-22) is a
  **3-bit group with a forced bit**: only the values 2/3/6/7 occur, so `G != 0` always sets bit 21 (bits 20&22
  co-occur 18 times against an expected 0.12). Two hard exclusions were re-verified bit by bit (0/4151 each):
  `bit4 ⊥ bit13` (these two point in *opposite* directions, so they are not one enum) and
  `bit11 ⊥ {13,20,21,22}`. The notes also carried a wrong value set for bits 20-22 (`1/3/5/7` - those two values
  cannot occur here). (b) The notes described `contact.extra_buffer` as a "`#3` varint, about 2 bytes"; that is the
  shape of **46 rows only**. The **main body - 940 rows - is a 37-field proto (`#2`...`#38`), median ~115 bytes**.
  The two older counts now reconcile exactly: **984 rows carry a top-level `#3`**, of which 942 are varints (940 large
  + 2 small), 42 are length-delimited and 2 are absent - so the old "942 / 44" *was* the "is `#3` a varint"
  criterion, two rows off the shape split (940 / 46). (c) The real gain: that 940-row body matches
  `stranger.extra_buffer` (1 row locally) **field number by field number and wire type by wire type** - the same
  message type - so the shape catalogue in §7 can now draw value ranges from 940 rows instead of one. `#41` turns out
  to be an **epoch-seconds** value (691 rows, 2021-2024) with no column among the 22 that could hold it. (d) A
  value-alignment pass over all 986 rows (raw / utf8 / md5 / sha1 / b64 / utf-16le against every column) returned
  **2 sparse hits** (`nick_name` == `#4` 8/716, == `#9` 12/953) and **no** hash / URL / wxid-level hit, so the blob's
  field names still ship as raw numbers (`DECISIONS.md` D-074, `docs/CONTACT_DB_SCHEMA.md` §3/§6/§7). One earlier
  figure also had its origin pinned: the "33 accounts with message tables but no `biz_info` row" is a real 33, but
  it comes from the **official-account database's own `Name2Id`** and **none of the 33 has a message table at all** -
  "message table" was the wrong condition, not the number.



- **`biz_info` - the official-account table in `contact.db` - has been mapped, and it holds fields the daily
  pipeline never had.** The repo has never read this table (a grep finds only the schema notes mentioning it), yet
  its `external_info` JSON carries `RegisterSource.RegisterBody` (**the account's legal/subject name, 93.1% of
  rows**), `VerifySource.Description` / `VerifyBizType` (**verification type**), `ServiceType`, `brand_icon_url`
  (an **avatar direct link**, 691 rows) and `PersonVerifyInfo.VerifyDescribe`. Today's daily report takes account
  names from `contact.remark` / `nick_name`, never uses avatars, and `contact.description` is empty for all 673
  `gh_` rows - so there is currently **no** source for a subject name or a description. Two boundaries are
  recorded with it: the `sync_version` column is declared TEXT but is really a binary blob (`SELECT *` dies on
  UTF-8 decoding - use `text_factory=bytes`), and `PersonVerifyInfo` carries a **verified person's real name**
  plus `ServicePhone` and lat/long, so read the fields you need rather than the whole JSON
  (`docs/CONTACT_DB_SCHEMA.md` §1.1). The same pass decoded two more flag meanings and corrected one earlier
  conclusion: `chat_room_status_` bit 19 means "initialised and not bridged to WeCom/openim" (77/77), bits 2 and
  31 always appear together, and `contact.extra_buffer` does yield two bit-level rules once you test **bit by
  bit** instead of by whole value (`bit1` implies `local_type = 1` and not a group member, 423/423) - which is
  what retired the earlier "the value cannot be judged" line, itself a by-whole-value artefact
  (`DECISIONS.md` D-073).

- **The `#3`/`#4` pair inside `chat_room.ext_buffer` is pinned down to "a local per-room counter", and two name
  candidates are withdrawn.** Three agents worked it from different sides; the results overlap, which is what makes
  them usable. (a) It is **not** a server sequence: 42/42 rooms of the `10000+x` family hold a value below their own
  room's smallest non-zero `server_seq` (median gap 872 million), the entire 84,279-row message set has **zero** rows
  with `server_seq` in `[1e4, 8e8)`, and rooms created in 2024-2026 still sit at `10000+x` while this account's
  server sequence had already reached 841 million back in 2021-11. (b) It is **not** a unique id: seven rooms share
  the value `10000` exactly, which a server-allocated identifier would not do. What survives is "a counter the
  client seeds itself - `10000`, or a ~7e8 block for the older batch, or ~1-2k for the WeCom-bridged rooms - and
  bumps on local events", with member-list changes dominating it (the detail blob's own pair correlates 0.889 with
  member churn and collapses to ~0 against announcements once member count is controlled). Two candidate *names*
  are dropped: `chatroom_seq` (an account-level cursor in the InitContact log, with no room id anywhere near it) and
  `InfoVersion` (the plaintext field-name group that contains it carries **no field numbers**, so its order cannot
  be mapped onto field numbers - reading it as 1..5 would put `AnnouncementPublishTime` at `#4` while the database
  stores it at `#6`). The blobs' `#3`/`#4` therefore still ship as raw field numbers
  (`docs/CONTACT_DB_SCHEMA.md` §3.3/§6.1, `DECISIONS.md` D-071). Two more bites followed the same day, both
  narrowing what the number can be: it is **not** "how many distinct people were ever in this room" (the local
  `name2id` mapping is gap-free - rowids 1..4025 with a count of 4025 - and `chatroom_member.member_id` is that
  same rowid, so only 4025 distinct people exist on this machine, while one room's offset is 15304), and rooms
  whose counter still sits exactly at its base turn out to be **rooms whose member table was written once and
  never touched** (a single contiguous `chatroom_member` rowid run whose length equals the member count, no
  member-change system message, no announcement). So the pair reads as "a per-room counter of member-table
  history" - and the remaining question (does one step mean one member added/removed, or one member-table
  update?) needs a second snapshot of the same room, which a machine-wide sweep showed does not exist locally
  (`DECISIONS.md` D-072 records the full searched range, so nobody re-searches it).

- **`contact-schema`'s `#5` output carried a wrong label; the label is now the measured fact.** The blobs'
  top-level `#5` used to be described as "a run of extra participant ids that no table carries". Probing all 77
  rooms shows the id set equals the members whose `status` has bit 11 (2048) set - exactly, room by room
  (77/77), and the 28 entries spread over 15 rooms match 28 members carrying that bit, no more and no fewer. So
  `#5` holds **no information the member list does not already have**, and `extraIds` was simply the wrong name
  for it: the JSON key is now `statusBit11Ids` and the human line reads `#5 (status bit 11 members)`. **That
  key rename is user-visible** - if you were keying off `extraIds`, read `#5` raw instead. Two more bit meanings
  are written down while we were there (both 77/77): `chat_room_info_detail.chat_room_status_` bit 17 marks a
  room bridged to WeCom/openim, and `524288` (bit 19) is only the **mode**, not a constant - the notes used to
  say all 77 rows carried it. The paired-field story narrowed as well: `#3 != #4` is a property of the *detail*
  blob (5 rooms - exactly the rooms whose member list runs one entry ahead of `chatroom_member`), while
  `chat_room`'s own pair stays equal even in those rooms, so "the difference is a pending-change flag" is not
  something the data supports for the room blob itself. `#3`/`#4` still get no name; the one candidate
  (`chatroom_seq` in the client's obfuscated string pool) was chased down and is an account-level cursor in the
  InitContact log with no room id anywhere near it (`docs/CONTACT_DB_SCHEMA.md` §3.2/§6, `DECISIONS.md` D-070).

- **Running the pointer across the floating ball makes it squirm as if tickled.** Four generated poses (eyes
  squeezed shut, paws up, open laugh) cycle every 110 ms while the pointer moves over it, and settle back after
  260 ms without movement. It **no longer tilts** - see the next entry; the 4-degree rotation computed from the
  pointer's speed and direction was removed on 2026-10-02. Why it was computed rather than drawn is still worth
  recording: a fixed frame set cannot express "the harder you tickle, the more it wriggles". That rotation was
  about the ball's
  **centre**, which is what keeps it safe under the circular clip: rotating about the centre preserves every
  pixel's distance from it, so the ears cannot be turned out of the circle - translating them would. The four
  poses are normalized at **scale 1.0** (a tickle must not change the ball's size, unlike the lift sequence),
  which is also why they live in their own table (`TICKLE` in `scripts/panel_frames.py`) instead of joining the
  ordered lift sequence. Skipped under `prefers-reduced-motion`, skipped while the ball is peek-hidden (that
  artwork's straight cut edge would open into a seam when rotated), and a press settles it so the lift owns the
  ball.

- **The tickle no longer tilts the ball - rotation belongs to the lift alone.** It used to add up to 4 degrees of
  rotation on top of the four poses, and both features wanted the same channel (a `rotate` on `#ball`), so running
  the pointer across the ball looked like a small version of the take-off. The user asked exactly that - "why does
  it sway when I move the mouse across it, instead of only swaying in the lift state" - and it is also why the code
  had to keep the two mutually exclusive in JS (`canTickle()` excludes `ball-lift`, and a press settles the tickle
  first). `--tickle-deg` is no longer written and its CSS rule is gone, so the tickle is now only the pose frames.
  Two guards keep it from creeping back: `test/panel-tickle.test.ts` asserts the tickle writes **no** angle at all,
  and `test/panel-lift-frames.test.ts` asserts that no `body.ball-tickle #ball` rule carries a `transform`.

- **`weflow-cli wiki graph` exports the concept graph as a self-contained 3D page.** `output/knowledge-graph-3d.html`
  (9.05 MB for this machine's 49,956 concepts / 145,794 links) opens by double-click with no server: the libraries,
  the graph and the layout coordinates are all inlined, because a `file://` page cannot `fetch` its data and a CDN
  reference would make a local-first feature depend on the network. The generator used to live in a gitignored
  `output/_3d/`, so on any other machine it simply did not exist - and its Node layout helper carried **this
  machine's absolute path** in the source. Both now live in the repository (`scripts/graph_3d.py`, and the helper
  beside the libraries it loads under `resources/js/graph3d/`, with versions and licences in `NOTICE.txt`). The
  layout is the slow part, so it is computed once and cached under `output/.graph3d-cache/`; `--dry-run` reports the
  counts and writes nothing. `test/graph-3d-cli.test.ts` pins the parts that break silently: the page must carry
  **no** external reference, the two library lists must agree, `--dry-run` must not write, and a hand-run without the
  CLI's `PYTHONIOENCODING` must still print readable UTF-8 (this machine's console is GBK, and that trap has bitten
  the repository before).

- **`chat-notes --transcribe-voice` was documented but unreachable.** The Python script has supported it since the
  local-whisper path landed, and `docs/PROJECT_STATE.md` told readers to add the flag to fill missing transcripts -
  but the CLI command forwarded only `--days`, `--limit`, `--yes`, `--json` and `--dry-run`, so the flag came back as
  an unknown option. It is now declared and forwarded; combined with `--dry-run` it fills the local cache and prints
  the counts **without calling any model** (zero cost, zero egress). Run on this machine it took the 30-day window to
  **130 voice messages with transcripts, 0 without**. `test/chat-notes-cli.test.ts` pins both halves (declared *and*
  forwarded) - either one alone leaves the switch dead, which is exactly the state it was in.

- **The 3D graph page can be filtered down to a size you can actually read.** On this machine the concept graph is
  49,956 nodes / 145,794 links / 9.05 MB, and at that size a force layout in a browser is a smear - the measured
  answer is that only **3,825** of those concepts have ten or more links, so `--min-degree 10` produces a 1.09 MB
  page that shows the structure. Degree is taken from the **whole** graph rather than recomputed inside the subset,
  otherwise the same threshold would mean something different on every page, and links pointing at removed nodes are
  dropped with them: keeping them makes the layout fail with "node not found" at build time, which is where the test
  catches it. `--line wiki|chat` draws one corpus at a time (48,430 article concepts against 1,527 chat ones - the
  vocabularies differ enough that merging them hides the structure of both). A filtered graph is a *different* graph,
  so the layout cache is now keyed by a hash of the data (`graph-<hash>.json`) instead of one fixed name: before this,
  building the small page evicted the 50k-node coordinates and switching back cost a 55-second relayout. Three
  mutations confirm the guards bite - keeping dangling links, skipping the node filter, and ignoring `--line` each
  turn the suite red.

- **`wiki graph --flat` draws the same graph as a 2D canvas page.** The 2D layout is a *separate* build-time solve,
  not a flattening of the 3D coordinates: the z axis carries structure, so dropping it stacks clusters that have
  nothing to do with each other on top of one another. 2D is also far cheaper - 3,825 nodes took **2.4 s** for 250
  ticks where the 3D solve at 25k took 55 s - so the page is **0.71 MB** against the 3D page's 1.34 MB, and it needs
  **no libraries at all**: the layout is precomputed, the renderer is plain canvas, and unlike the 3D page there is
  not even three.js to inline (a test asserts the page contains no `Three.js Authors` and no external reference).
  Interaction is the Obsidian-like set - drag to pan, wheel to zoom, hover for a name, click a concept to dim
  everything except it and its neighbours, and a search box to jump - with labels only for the highest-degree nodes,
  because 5,000 names on screen is a smear rather than a graph. `--min-degree` / `--line` apply unchanged, and 2D and
  3D coordinates are cached separately (the digest includes the dimension), so switching between the four
  combinations never recomputes anything. `test/graph-2d-cli.test.ts` includes a **render smoke test**: it feeds the
  built data to the viewer over a fake canvas context and counts the `arc` / `lineTo` / `fillText` calls, because a
  JS error in a viewer yields a blank page with **no failing assertion anywhere** - and the page cannot be eyeballed
  from here. **The page was then reported as too slow, and the numbers say where it went** - measured in a real
  headless Edge over CDP (Node's built-in WebSocket speaks the protocol, so no puppeteer was needed): one `draw()`
  cost **12.78 ms** at 49,956 nodes, and a micro-benchmark split it up - 50,000 `arc` calls 9.0 ms, 50,000 `rect`
  calls 3.4 ms, 145,000 line segments 4.1 ms, 80 labels 1.3 ms. Two fixes followed. **Sub-2px dots are now drawn as
  squares** (identical at that size, 2.6x cheaper), and **every interaction redraw is coalesced into one per frame**
  - the handlers used to call `draw()` on every `pointermove`, so a drag issued hundreds of full redraws and the
  canvas never caught up; that was the actual "lag". After: **7.6 ms** per frame (132 fps) and a 200-event drag
  storm went from ~2.6 s (computed from the measured 12.78 ms per draw) to **6.9 ms**. The same measurement pass also
  showed why the labels were unreadable - the top-degree concepts are all hubs sitting in the middle, so all eighty
  names landed on top of each other; labels are now capped and greedily de-overlapped. **The 2D layout needed its own
  force parameters**: the same numbers in one dimension fewer pack 50,000 nodes into a featureless disc (the first
  screenshot was exactly that), so 2D now uses stronger repulsion and longer links. That change exposed a second bug:
  the layout cache key hashed only the *data*, so changing the forces silently kept serving the old coordinates -
  the key now covers data, dimensions, tick count **and** the force parameters (a Python test pins each of those four,
  since the symptom is a silently stale result). And that smoke test did not catch the first version's real defect: the graph drew into the top-left
  300x150 corner. `canvas` is a *replaced element*, so `position:fixed; inset:0` does not stretch it - right/bottom
  are ignored and it keeps its intrinsic size, `clientWidth` came back as 300, and the whole view was therefore
  computed for a 300x150 viewport. The page CSS now sets width/height explicitly and `resize()` keeps the CSS box and
  the backing store in step; both halves are asserted, and reverting either one turns the suite red (verified). The
  3D page never had this problem because it draws into a `div` (which does stretch) and lets three.js size its own
  canvas - but nobody has watched *that* one either.

- **`weflow-cli contact-schema` decodes the verified half of `chat_room.ext_buffer`.** Those blobs had no
  documentation anywhere - not in this repository, not in the native DLL that reads them (`wcdb_api.dll` just
  hands the string across and never parses it). A read-only probe established the structure and wrote it up in
  `docs/CONTACT_DB_SCHEMA.md`: the top-level `#1` is a repeated member entry (`1=userName`, `2=displayName`,
  `3=status`, `4=inviter`), whose count matches `chatroom_member` row-for-row on 29 of 30 groups, and `#5` is a
  run of extra participant ids (`wxid_...`, `...@openim`) that no table carries. The command outputs exactly
  those two things and **nothing it cannot name**: the top-level `#3`/`#4` pair - which the published
  `roomdata.proto` does not describe at all - goes into `unrecognized` keyed by field number, because three
  separate falsifications (not a member id, not the room id, not any id in the message shards) left its meaning
  open. Two of that proto's claims are wrong for V4 and the notes say so: `roomCap = 5` is really a
  length-delimited string list, and `status` is not the documented 0-9 but a bitfield (0/1/9/17/25/2057/2073/
  8193/2097153/3145729/6291457/7340049 observed). A malformed blob raises and lands in `failures` rather than
  returning an empty member list, since "cannot parse" and "nobody is in this group" are opposite facts -
  and the test for that only became real after mutation testing showed the first version never reached the
  bounds check it was supposed to guard.

### Added

- **The ball now reacts twice more on its own: a hop when a reply lands, and a blink every 7-13 seconds.** The hop
  answers the half `busy` never did - a collapsed ball told you it was *thinking* but never that it was *done*. It
  has **no generated frames, deliberately**: asked for a crouch, the model reliably squashes the body (measured
  +9.2% / +8.0% on the outline metric, and milder wording did not help), and over three frames that meant the ball
  visibly fattening and thinning four times in 270 ms. A hop is a *motion* - the character does not change shape
  when it jumps - so it is a transform, with the rise **budgeted** rather than guessed: the art already uses 125.25
  of the circle's 128 radius, so the airborne step shrinks to 0.96 (content to 120.2) and only then rises 7 px
  (127.2 < 128). It plays only when the turn succeeded (a failure must not make the ball jump) and after the busy
  face is cleared. The blink adds two frames - and **those are composited, not used as generated**: the model
  repaints the whole face, and even a one-pixel difference is the entire ball flickering at 96 px, so the pipeline
  pastes back only the eye band (the iris layer's bounds plus a margin, which stops just above the mouth).
  Measured after compositing: **0 pixels differ outside that band**, asserted in `test/panel-lift-frames.test.ts`
  together with a reverse check that the band itself did change. Both are skipped under `prefers-reduced-motion`;
  the blink also waits until no other face is showing (the state faces have their eyes painted on), the ball is not
  being carried or tickled, and the page is visible.

- **The ball now swings while you carry it - and it swings from the moment you pick it up.** Holding it already
  played the "picked up" sequence, but the carry itself was stiff. The first version derived a static tilt from the
  drag speed and leaned **against** the direction of travel, the way something carried lags behind; the user asked
  for something more specific - it should start swinging **when picked up**, and the side it leans towards should be
  the side you are moving towards. So it is now a single sine (a 900 ms period) whose **amplitude and lean both
  come from one "energy" value** read off the drag speed: the swing runs from 6 degrees when it is hanging still
  (picking it up is enough to start it) up to 16, and the lean towards the direction of travel runs from 0 to 6 on
  top of that - a full-speed fling reaches about 22 degrees on the side you are moving towards and dips only to
  about -10 on the other. A
  click sways too, since a click *is* a pick-up - the 4-pixel threshold only decides whether the gesture counts as a
  drag, which is why the old "a click must not tilt" assertion is **removed rather than inverted**. Rotation about
  the ball's centre preserves every pixel's distance from it, so the amplitude is **a matter of taste, not of
  budget** (a translation would have had to fit inside the circle's remaining 2%). The energy decays after 1.2 s of
  stillness, **deliberately longer than the 900 ms period**: the 240 ms tried first expired before the second peak
  arrived, so a leftward drag merely damped the rightward swing (measured -12.6 degrees, indistinguishable from not
  dragging at all) instead of amplifying the leftward one - half of the request. The CSS transition was shortened
  from 90 ms to 45 ms for the same reason: at a 16 ms frame interval a 90 ms first-order lag eats about 15% of the
  amplitude and delays what you see by ~90 ms, and **no test can see that**, because the tests read the
  `--dangle-deg` property rather than the rendered transform. `test/panel-dangle.test.ts` now samples a **whole
  swing period** and asserts both extremes; an earlier version sampled only the first 300 ms, which mutation
  testing showed could not fail on the decay bug above, the negative peak it depends on never falling in that
  window.

- **...and the whole swing grows with the speed - scaling only the lean was not enough.** The speed term was inert
  from the start there: it saturated at **450 px/s, slower than any real drag**. Raising that to 1800 px/s fixed
  the lean, and the ball still looked the same at any speed, because the swing itself was a constant +/-12 degrees
  - the part you actually see was fixed, and the speed only moved a 10-degree lean on top of it. The user said so
  twice ("move it left quickly and it should sway further left... currently fixed"), and the second time named the
  gesture explicitly, which is what separated it from the tickle: the swing is the one that renders only while the
  ball is held (`body.ball-lift`), the tickle is the four poses you get by running the pointer across it (its 4-degree tilt was removed on
  2026-10-02, precisely because that distinction was too easy to miss), and
  the two cannot co-occur (`canTickle()` excludes `ball-lift`). Both quantities now come from the single energy
  value described above, and the curve is pinned on a **virtual clock** (see below): hanging still is 6.0 degrees,
  100 px/s 6.7, 400 px/s 8.9, 900 px/s 12.6, and 1800 px/s 19.3 for a single move - a continuous drag reaches
  about 22 - so a slow nudge and a fast flick differ by about 10 px of travel at the rim of the 96 px ball, and
  everything in between is monotone. `DANGLE_FULL_SPEED` is a taste value, not a measurement: lower it for a more
  sensitive ball, raise it for a stiffer one.
- **`test/panel-dangle.test.ts` runs on a virtual clock, because a real one could not control the thing it was
  testing.** The speed cases set the speed as *pixels moved / milliseconds waited*, and that only means something
  if the wait is the wait you asked for: measured here, `await wait(20)` actually took 25-31 ms (Windows timer
  granularity), so "slow" was never the speed the test believed. Mutation testing found the consequence - restoring
  the 450 px/s saturation above, **the very bug the user reported**, left the suite green. The harness now replaces
  `setTimeout` and `performance.now` and advances time itself, which makes the speed exact, removes the jitter, and
  cuts the file from 5.6 s to under a second. `setInterval` was already stubbed, so the status poll still never
  runs.
- **The panel can show you what the assistant remembers about you.** Those long-term facts were previously visible
  only by opening `~/.weflow-cli/assistant_memory.json` by hand, so the assistant could act on a belief the user had
  no window into. A `记忆` button in the bubble header fetches `GET /api/memory` - token-gated like every other
  route - and lists each fact with the time it was written and the quote it came from, which is what makes a wrong
  memory traceable to the sentence that produced it. It is **read-only**, the bucket is chosen **server-side** and
  cannot be steered by a query parameter, and the payload **never goes to a model**: it is deliberately separate from
  the fact injection in `buildSystemPrompt`, which has its own budget and redaction. Facts are capped at 30
  (`FACTS_MAX`) so the view is bounded by construction, and the page renders with `textContent` only - a test feeds
  in a memory shaped like an `<img onerror=…>` tag and asserts it displays as text.

- **Pressing the floating ball now plays a twelve-frame "picked up" animation.** The hard half already existed - a press
  swapped in a squint-eyed face and a release reverted it instantly - but the only way the ball could say "you have my
  attention" was one still frame. A press now plays six frames up (hard cuts about 60 ms apart, the first
  synchronous so the press never shows an empty ball) and a release plays six frames back down to the resting frame. The
  frames are held assets, so nothing is fetched at press time - a face fetched on demand costs about 15 ms, one 60 Hz
  frame, which is what used to make the ball flash empty. The twelve frames are **generated by the project's own image
  model** and then normalized to a written-down geometry contract, which is the part worth keeping: 256x256 RGBA,
  **everything inside the ball's inscribed circle** (0 solid pixels past r=128, farthest solid distance under
  0.98 x 128 measured from the canvas centre with solid = alpha >= 128), the **apex anchored to the resting frame**
  (the scruff is what is held, so the body hangs from a fixed point) and the **scale taken from the cat's own bounding
  box** (170 x s) rather than from the canvas, which also carries the speech bubble and the shock marks. An earlier
  generation drew the hood as a pointed cone with no ears and **every geometric assertion passed** - it was inside the
  circle, the right size, and whitelisted - so the suite now pins the largest connected component's width and aspect
  ratio and makes "it is no longer the same cat" fail a test instead of shipping. `prefers-reduced-motion` plays no
  animation and keeps the squint face, which is now what that face is for. The pipeline is `scripts/panel_frames.py`
  (`--gen` / `--normalize` / `--check` / `--check-raw`); `--check` uses only the standard library, because the test
  suite runs it.

### Changed

- **The digest pipeline no longer writes your Obsidian Vault unless you ask.** `pipeline` used to copy the day's
  content into `Sources/WeChat/<date>/` and compile concept pages as part of every run. **Obsidian's own graph view
  is live** - it watches the vault's files and re-renders the moment one changes - so "the content updates and the
  graph rebuilds itself" was the vault being rewritten, not the graph being rebuilt. Both steps are now opt-in
  (`--with-vault` / `--with-wiki`), the run prints which ones it skipped and how to ask for them, and
  `test/pipeline-vault-default.test.ts` pins the default: writing the vault succeeds silently, so a flip back to
  "do it every time" would otherwise have gone unnoticed.

### Fixed

- **A transient failure used to leave the floating ball stuck on the aggrieved "offline" face forever.** That face
  was set when a status poll failed and the **success path never cleared it** - only a turn's `finally` cleared
  `busy`. So the few seconds an `assistant stop` / `assistant start` takes were enough to latch it: the daemon came
  back, the status line read "微信 + 本机", and the ball stayed aggrieved until the window was reopened. The user
  reported it as "the default face changed", and the artwork was innocent - `mascot-base.png` and `mascot-iris.png`
  are byte-identical to the repo. The success path now clears it, without touching `busy` (a turn owns that) and
  while keeping the quota colour. `test/panel-ball-state.test.ts` pins the **recovery**, because the failure half was
  always correct; a mutation check confirms it goes red when the clear is removed.

## 1.9.0

### Added

- **`draft --pick N --send`: a candidate can now be sent, by a human who names which one.** The hard half already
  existed - judge the intent, draft three deliberately different candidates, rank them with the decision model - and it
  ended by saying the candidates are only text and that sending is your call. That call can now be carried out from the
  same command. Three rules keep it honest: `--send` **requires** `--pick` (no "draft three, let it choose, let it
  send" path exists); without `--yes` it only returns `CONFIRMATION_REQUIRED` (with the text in the payload) or asks
  interactively; and the blacklist -> whitelist -> rate limit -> audit chain is now **one shared implementation**
  (`src/services/outboundSend.ts`) used by both `send` and `draft --send`, because a second copy of that chain is how a
  send path ends up quietly skipping the whitelist. Sending stays **absent from the assistant and MCP tool tables** (a
  test asserts it), so nothing model-driven can send; `draft_reply` stays present because drafting is not sending.
  `--dry-run` means zero egress, and since candidates can only come from a model it cannot preview one - it reports the
  character count and says so. Usage errors (`--send` without `--pick`, `--pick 9` when there are 3) are reported
  **before any model call**, which is also what the tests assert (D-063).
- **`weflow-cli bonds` - relationship temperature: who is going quiet, who is heating up, who is reachable only here.** Not a
  leaderboard of who you message most (that tells you nothing you did not know); the useful signal is the *shape*. It
  reports relationships with substantial history that have gone silent (ranked by messages x silent days, so it
  surfaces "294 messages last year, nothing for 282 days"), relationships whose last 7 days run at least twice the
  long-run rate, and contacts whose entire history never mentions another channel - phone, email, meeting up - which
  is the data-derived version of "if this account disappeared tomorrow". Read-only, local, no model, no network.
  Three measurements shaped it, and all three overturned a simpler design: ranking by volume produced no information;
  the first irreplaceability rule was **inverted** (it is trivially true of bots, so the top of the list was a coffee
  shop's bot); and `flag` - the field that looked like a clean human/bot marker - would have silently deleted two
  real friends, since two of the 38 people on this machine carry extra bits from however they were added. What
  remains is the **shape of the id** (`@` anything, `filehelper`, `gh_` are out), plus accounts that look structurally
  like a person but whose name reads as a shop are **flagged for you to decide** rather than dropped, with a skip file
  and `--skip`. Every exclusion is printed with its reason. Deliberately **not** in the assistant or MCP tool tables:
  its output is an inference about other people (D-062).
- **Voice messages reach the conversation cards as text, not as `[语音]`.** The chat line fed the model a bare
  `[语音]` placeholder for every voice message - no length, no words - while the transcripts were already on disk:
  the local faster-whisper pass writes a content-addressed cache, and measured over the last 30 days **1,780 of
  1,869 clips** in the busiest conversations already had a transcript that nothing was reading. `chat-notes` now
  reads that cache and substitutes the text, labelled `[语音·本机转写·未校对]` - the label is not decoration: it is
  someone's speech as a machine heard it and it will contain errors, so the model has to know not to treat it as
  quoted fact (the same discipline as `verified: false` on concept pages). `--transcribe-voice` fills what is
  missing (local model, never leaves the machine, resumable - the cache is the progress); without it the run only
  reads, and the count of what is still missing is printed rather than quietly omitted. First real run: **54 of the
  125 voice messages in the 30-day window** carry text into the cards that previously said `[语音]`.
  Finding this also surfaced a quieter bug in the same feature: the cache directory was being derived from
  `chat-notes`'s *card* output directory, so it read a cache it had just created itself (`output/chat-notes/.voice-cache`,
  16 entries) instead of the real one (`output/.voice-cache`, 1,788) - and the run reported "16 hits" that were
  internally consistent and wrong. The cache's location is now defined once, in the module that owns the cache, and
  a test asserts the three readers agree.
- **The MCP server can speak Streamable HTTP, so clients that cannot spawn a process can use it too.** stdio stays
  the default and is unchanged; `--http` (or `WEFLOW_MCP_HTTP=1`) serves the **same tools** over HTTP, because the
  table is derived from one place (`TOOL_DEFS` -> `MCP_TOOL_DEFS`). Serving private messages on a listening socket is
  a different risk from writing to stdout, so four rules are enforced in code and each is mutation-checked: it is
  **off unless asked for**; a **token is mandatory with no default** (missing or under 24 characters, the process
  refuses to start and prints how to generate one); the bind is **loopback only** (`0.0.0.0` is rejected with the
  tunnel command in the message, because widening the bind is what would put chat data on the network); and
  **DNS-rebinding protection is on**, which the SDK leaves off by default - the realistic attack on a loopback server
  is a web page you happen to visit reaching `127.0.0.1`, so the `Host` header must match and any `Origin` must be
  allowlisted. Requests are stateless and the token is never logged. Default port 8790 (5030 is chatlog's, and two
  servers fighting over a port is not a useful failure). The decisions live in `mcp-server/httpConfig.ts` as pure
  functions with 13 offline tests, five of which were shown to go red by mutation.
- **Tool coverage is now a test-watched table instead of a paragraph.** The list of "which tools have no eval
  case" used to be prose inside the case file, and prose does not fail when it goes stale - two of its claims had.
  It is now `EVAL_UNCOVERED`, a map from tool name to reason, with `test/assistant-eval.test.ts` asserting both
  directions: every tool is either claimed by some case's assertions or registered here, and no registered name has
  stopped existing. Adding a tool now means writing a case or writing a reason; skipping both fails. That walk also
  caught two cases whose comments promised a distinction they never asserted - `daily-review` says it is testing
  "get_review, not get_daily_report" and `skills-health` says the same about `check_skills` vs `list_skills`, and
  neither had the `mustNotCall` half. Both have it now, verified against the real model.
- **Five more behaviour-eval cases, and a limitation list that had gone stale.** Walking the 31 tools one by one
  against `assistantEval.ts` turned up five with no assertions at all - and one stub that made its own case
  untestable: `getSnsTimeline` returned a hard-coded empty timeline, so `get_sns` could only ever be asserted as "no
  cached data", the same defect `listContacts` had. The `sns` fixture is per-case now, and the new cases cover
  `get_sns`, `read_skill` (the body, not just `list_skills`' catalogue), `get_stats`' knowledge-base half,
  `export_chat` (into a temp root, via `WEFLOW_ASSISTANT_EXPORT_ROOT`), and `draft_reply` under strict privacy - that
  last one is a guard for a real regression: with `aiEngine=ollama` the old code still handed the conversation to the
  script, whose two remote calls have nothing to do with that setting. The block of comments claiming which tools
  were untestable was rewritten too, because two of its claims were no longer true (`get_concepts`/`get_review` had
  been covered since 2026-09-30, and the harness does set environment variables). `get_weread` was filed as untestable too, for the same reason - which mistook a missing mechanism for an
  untestable tool, since the harness had been overriding `assistantPrivacy` per case all along without
  generalising it. A general per-case `config` override fixed that, and `weread-notebooks` now asserts the book
  title actually reaches the answer (reading `b.title` off the notebooks response returns `undefined` while the
  output still looks fine - the silent version of that bug is what the case is for). What is genuinely uncovered
  is **one** tool: `search_semantic`, which needs a `dashscopeApiKey` and a built index. Run against the real
  model the same day: **37 passed, 0 failed** once `weread-notebooks` was added. The strict-draft case is worth one note for what it did *not* do - it
  called `get_messages` and then refused, never reaching for `draft_reply`, so an expectation of
  `mustCall: ['draft_reply']` (that case's first version) would have failed on its first run and sent someone chasing
  a defect that does not exist. Asserting the outcome rather than the call is what the memory cases already argued
  for, and here it is what kept the case honest.
- **`backfill_articles.py --topic` fetches one topic instead of a whole day - and a day fetched that way knows it is
  partial.** The filter is the classifier the script already had: `_guess_topic` reads only the article's title and its
  account name, never the body, so `--topic AI` selects exactly the articles a full run would have filed under `AI`.
  The saving is the point - over 2025-09-05 ~ 2026-03-02 the untouched backlog is 9,207 articles across 156 days
  (official accounts only: `gh_%`), 1,758 of them AI, and because 5,278 of the 9,207 already hold enough text in the
  database a full run needs **3,929** network fetches against the AI run's **1,208**. What makes it safe rather than merely convenient is the bookkeeping: the day
  records `topicFilter` in `.articles.json` and names the missing topics in its README, and `day_done` refuses to call
  it finished, so a later full run still covers those days. Without that marker the other five topics would have been
  dropped with nothing to report it - the same shape as the `backfilled`-marker mistake of 2026-09-28 (D-060). The
  filter is applied inside `write_day` too, so a caller that forgets cannot stamp "only AI" on a directory holding all
  six topics. An unknown `--topic` is rejected before the database is opened: a typo would otherwise select zero
  articles and report "nothing to do", which is indistinguishable from a day that genuinely had none.
- **Two more behaviour-eval cases cover the tools that had no testable data at all.** `daily-review`
  (`get_review`) and `knowledge-overview` (`get_concepts`) both read directories rather than services, and
  with the eval running in a temporary home those directories are always empty - which is why neither tool
  could be asserted beyond "nothing here yet". `WEFLOW_ASSISTANT_VAULT_DIR` was added as a third per-call
  injection point (the same mechanism `WEFLOW_ASSISTANT_BIZ_DAILY_DIR` and `WEFLOW_ASSISTANT_REVIEWS_DIR`
  already use), and the eval can now lay down fixture files and set those variables for a case. Same day, a
  real gap surfaced: `listContacts` had never been stubbed, so `list_contacts` was calling the real service
  against a temp home and always coming back empty - two cases were running on that accident while the
  harness claims synthetic data. It is stubbed now, with contacts derived from the case's own sessions.
- **The assistant's behaviour eval now covers the tools the widened surface added.** Ten tools went in on
  2026-09-30 and nine of them had no assertions at all - which matters because that is exactly how "did routing get
  worse once there were ten more tools to choose from" goes unnoticed. Six cases were added (`format-for-wechat`,
  `themes-listed`, `skills-health`, `wiki-health`, `todo-done`, `todo-ambiguous-refused`); the two that leave the
  machine cannot be evaluated there by design, and two more have no injection point yet - all four are written down
  in the case file rather than skipped quietly. The write cases came with a new floor, `neverSucceeds`: if the named
  tool was called, that call must have produced nothing, so "asked the user instead of writing" and "called the tool
  and was refused" both pass while an actual write fails. Full run the same day: **29 passed, 0 failed**.

- **Skills: the assistant can read the skill packages you already have.** A skill is a directory with a `SKILL.md`
  (`name`/`description`), scanned from `skillDirs` - by default `~/.claude/skills` and `~/.weflow-cli/skills` - so the
  skills already on this machine are usable immediately. The system prompt carries the catalogue and two read-only
  tools (`list_skills`, `read_skill`) return it and one body; a scene can reference a skill inline with
  `{{skill:<id>}}`. **A skill grants no new powers**: nothing is installed, nothing is executed, no tool is added to
  the model's table, and `read_skill` can only return a file named `SKILL.md` that the scan already found. Referencing
  a skill that is missing, disabled or unreadable renders exactly that state and asks the model to continue and say
  what it could not do rather than dropping the reference. Disable one with
  `config set skillDisabled <id, ...>` or with `enabled: false` inside the skill itself.
  `weflow-cli skill list|show|check|--json`; `skill check` reports unreadable frontmatter, cross-root collisions,
  non-conforming ids and disabled skills. On this machine `skill check` finds **28 skills, 0 unreadable, 0 collisions**,
  and warns about 5 ids that are usable here but would not conform to the Anthropic Agent Skills naming rule
  (`clz_docx_to_mp` and friends). Rationale and the two rules taken from real data (read only the leading `---` block;
  support block scalars) are in D-056.

- **Scenes: per-conversation prompt presets.** A scene is keywords + an extra instruction + an output spec + required
  skills, stored in `~/.weflow-cli/assistant_scenes.json` (`weflow-scenes/v1`, unknown version quarantined rather than
  migrated). Each turn picks at most one through three tiers - **explicit binding → keyword hit → last used in that
  conversation** - and no match means no scene section at all, leaving the default behaviour byte-identical. Two
  equally long keyword matches produce **no** scene plus a trace note saying why, instead of picking one. Manage them
  with `weflow-cli scene list|show|add|remove|enable|disable|bind|unbind` (every mutation is `--dry-run` then `--yes`),
  or from a conversation with the built-in `场景 <id>` / `场景 无` / `场景`, which accepts only an argument that names an
  existing scene so an ordinary sentence starting with 场景 is left to the model. **Scenes cannot be read or written by
  the model** - there is no such tool - and a scene's content enters the prompt through `frameLocalData`. Which scene
  applied, and which tier matched, is recorded in the turn trace you already see under 「思考过程」 in the panel.
  Scene ids are validated against quotes and angle brackets before they reach the prompt label. Rationale, the MCP
  collapse cost and the deliberately skipped fourth tier are in D-057.

- **The assistant can reach everything it already could over MCP, and only what the project allows.** Three
  surfaces had drifted: the CLI, the 21-tool assistant table, and 11 tools hand-written in the MCP server that the
  chat path could not call at all. Ten tools are now shared - `list_contacts` (names only: never `wxid`, never the
  avatar URL), `get_review`, `get_concepts`, `fetch_article`, `search_public`, `format_article`, `list_themes`,
  `lint_wiki`, `check_skills`, and `set_todo_status` - seven MCP-only tools were **moved** into the shared table
  (same tool names and arguments, single implementation; the hand-written table went from 11 entries to 4), and four
  tools were **widened instead of duplicated**: `get_daily_report` now returns the human-readable daily with
  `full: true` and searches across dates when given a keyword without a date (that was the MCP-only
  `search_articles`), `get_todos` shows each item's id and groups by urgency (that was `todos remind`), `get_weread`
  gained `stats` / `book` / `review` / `discover` / `profile` modes, and `get_stats` now reports the knowledge-base
  half it had been missing while the MCP copy had it. Almost all of this is **alignment rather than new access**:
  every capability added was already reachable by an MCP client.
  Two things are genuinely new and both are **recorded with their residual risk** (D-058) rather than left to be
  found: `search_public` sends a **model-authored query** to a third-party search page with no allowlist and no
  preview (chosen deliberately - `fetch_article`, by contrast, accepts only `https://mp.weixin.qq.com` and
  re-validates that allowlist on **every redirect hop**), and `set_todo_status` can flip one existing todo between
  done/pending - it **cannot create, delete or edit anything else**, it refuses an ambiguous task-text match instead
  of guessing, and it is deliberately **excluded from the MCP surface** ("mutating todos stays out").
  What stays out of reach from a model-driven path is unchanged and named in D-058: sending, publishing,
  configuration, access-list writes, `evidence-review`, `vault promote`, scenes, interactive login, and `decide`.
  Side effect worth noting: `WEFLOW_ASSISTANT_BIZ_DAILY_DIR` and `WEFLOW_ASSISTANT_REVIEWS_DIR` were added as
  per-call injection points, which closed the **last uncovered tool branch** - `get_daily_report` had never been
  executed because its directory was a frozen module constant, so the only possible assertion was a shape check that
  asserted nothing. All 31 tool branches are now executed against fixtures.
- **Push the ball to a screen edge and it hides behind it, peeking out; click it to come back.** Drag the ball
  until it touches the left or right edge of a monitor and let go: the ball ducks behind the edge and reappears as
  the cat **peeking out from behind it** - head and front paws out, body hidden, with a dedicated drawing whose
  left side is a straight cut that lands exactly on the screen edge (so it reads as "behind the bezel" rather than
  "a ball that got clipped"). The first click (or drag) brings the normal ball back; that click deliberately does
  *not* open the conversation, because on a hidden ball a click means "come back", not "let's talk" - a second
  click expands as usual. Only the left and right edges: top and bottom would fight the taskbar and Windows' own
  edge snapping. The window itself stays **fully on screen**, flush against the edge - the illusion comes from the
  drawing, so nothing is pushed off-screen, the whole thing stays clickable, and a restart just puts the ball back
  at the edge (not lost). Two rules make or break the effect and both have tests, because everything else stays
  green without them: the ball's circular clip has to be off in this state (a circle would slice that straight cut
  into an arc), and hover-zoom has to be off too (scaling by 1.02 lifts the cut 1px off the edge - a visible seam).
  The detection threshold is deliberately below the normal 24px edge margin, so a ball that is merely parked is
  never mistaken for one being pushed off (also tested).

- **The ball's eyes follow your mouse.** Move the pointer and the mascot looks that way; stop moving and it settles
  and stops animating entirely (no 60 fps loop running for decoration on a window that is always open). The cursor
  position has to come from the main process - the ball is 96x96, so the pointer is outside the window almost all of
  the time and the page never sees a mousemove - and it is sampled at 120 ms, pushed **only when the coordinates
  change**, kept strictly in-process, and never logged or sent anywhere (D-059). How far the eyes can travel is
  **measured from the artwork**, not picked: the iris has about 7 px of sclera to its left and none above it, which
  at the ball's size is roughly 2.6 px sideways and 0.8 px up - past that the eye reads as a sticker sliding, so the
  movement is clamped to an ellipse. The eyes drift only on the idle and "pressed" faces; the thinking / offline /
  tired faces have their eyes drawn in, so the moving layer is hidden there rather than showing two pupils. The ball
  is otherwise pixel-for-pixel what it was: the new base image plus the iris layer, composited at rest, are asserted
  to equal the old ball image exactly. If your system asks for reduced motion, the eyes stay still.


- **The panel shows what the assistant did, collapsed under each reply.** The user asked for the
  thinking process to be visible but not in the way - so each answer now carries a `<details>` block
  ("思考过程") that is **closed by default**: routing decision, any guard that pushed the turn back,
  each tool call with its argument summary and byte count, the round count and the stop reason. The
  lines come from the **same** `describeForChat` that the WeChat side's 「轨迹」 command prints, so the
  two surfaces cannot drift into disagreeing about what happened - and that also means the panel
  inherits the guarantee that `userId` never appears. When the model actually returns
  `reasoning_content` it is appended (clipped, marked when cut); the current default model returns
  none, and the trace says so rather than pretending.

  One thing had to be defended: **the trace is an accessory, the reply is the result.** The first
  version called the lookup unguarded, and the panel test went red immediately - its stub service
  had no such method, so every ask became a 500. A throw there would have cost the user their answer
  to gain nothing, so it is caught, and the test pins both halves (the trace comes through, and a
  failing trace still returns the reply).

### Fixed

- **A concept name containing a double quote lost a character on every read.** `parse_frontmatter` split
  list values with `item.strip().strip('"\'')`, and `strip` removes *every* leading and trailing quote character - not a matched
  pair - so `AI 长出"手脚"` came back as `AI 长出"手脚`; `wiki_lint` then repeated the mistake, stripping
  quotes a second time off values the parser had already unwrapped. One character, no error, and the visible
  effect was a page nobody could explain: the card's `[[AI 长出"手脚"]]` matched no page name, so the page was
  reported as an orphan. Both now unwrap a **matched pair** only. Related and fixed with it: the file name of a
  concept page is the name sanitised (`:` `/ ? * " < > |` → `_`) and cut to 60 characters, so pages could not be
  reached by the link that pointed at them - the builder now records the original name in `aliases:`, and
  `wiki_lint` counts an alias as an inbound edge (it already accepted one as "exists", which is why no broken link
  was ever reported). Measured on the live knowledge base: **28 orphan pages → 1** (`DESIGN`, which no card mentions
  at all), with 0 broken links across 26 pages that had been silently unlinked. The case-only half of the same
  transform is fixed with it: two names differing only in case are one file, and the builder wrote both, so the second
  overwrote the first - **53 pages were lost this way** in the two builds of 2026-10-01 alone (44 AI, 9 non-AI).
  Names are now grouped by file name: one page, the other spellings as `aliases` (which is what
  `wiki compile --merge-duplicates` already did for one concept written two ways), and the **233 pages** that were
  missing a spelling got it back - `[[AI agent]]` and `[[AI Agent]]` now reach the same page.
- **Backfilling a day in two passes dropped the first pass from the index.** `--topic` was written for the
  one-topic-at-a-time case, and `write_day` rewrote `.articles.json` from that run's results - so fetching `AI` on
  Monday and `学术` on Tuesday left Tuesday's file listing only the 学术 articles while Monday's `AI` files sat on
  disk, an index shorter than the directory and nothing reported. The index now describes **the day**, not the run:
  entries for other topics are carried over and merged (de-duplicated on the name the file actually has), the
  `topicFilter` mark becomes the union, and it is **removed** once the union covers all six topics - otherwise such a
  day could never converge and every later full run would refetch the whole window. `truncated` is now sticky, and an
  unreadable index is preserved as `.articles.json.bad` instead of being silently replaced. `--topic` also takes
  repeated flags now, not only commas: the same flag in `article_notes.py` is repeatable, so `--topic AI --topic 学术`
  was silently acting on only the last one. Verified on a real day (8 `AI` entries became 42, none lost, directory and
  index equal) and across the window (8,754 entries over 157 days, 0 days with a mismatch, 0 entries lost).
- **A day backfilled with `--limit-per-day` reported itself as complete.** The day was sliced, written, and then
  `day_done` saw a non-empty article list and skipped it from then on, so everything past the limit was silently out
  of scope - and had been since the flag was added. Such a day now carries a `truncated` marker and is redone on the
  next run. Note the behaviour change: a limited day is no longer incremental, so re-running it re-fetches the same
  first N articles. Written down as D-060 alongside `--topic`, because it is the same defect - an incomplete
  criterion read as "done".
- **The subscription-account database key was derived in three places, and two of them could never
  have worked.** `chat_stats.py` and `mcp_bridge.py` read `bizKey` / `bizSalt` from the configuration -
  two keys that do not exist on this machine, because WeChat 4.x derives each database's key from the
  account-wide passphrase (PBKDF2 over the file's own 16-byte header salt). Both paths therefore reported
  "missing key", and the hint they printed (`config set bizKey`) leads somewhere worse: that key is not in
  the CLI's writable allowlist, so following the advice hits `INVALID_CONFIG_KEY`. `biz_daily.py` held the
  working derivation - and computed it **twice in a row**, the first result immediately overwritten by the
  second, which is what a fork in the logic leaves behind. All three now call one shared
  `_utils.biz_message_db()`, and a source-level test asserts that no other script reads `bizKey` at all -
  so a script added later is covered too, rather than only today's three. Verified by running the path
  that used to fail: `daily-stats` now returns per-account counts, and a daily dry run opens the database.
- **`wiki lint` was O(links x pages) and took 9.5 minutes on the article line.** The link-existence
  predicate passed `resolvable_names(pages)` *inside a lambda*, so the set of all page names plus aliases was
  rebuilt once per link instead of once per run - measured on this machine: 3,623 pages took **9m29s**, while the
  1,527-page chat line took 3.2s. The set is now computed once, outside the lambda: the same page count now takes
  **3.4s**. Behaviour is unchanged, and that was checked rather than assumed - the old and new versions produce
  **byte-identical JSON** (sha256 match, 366,638 bytes) on the chat line, and hoisting a pure function out of a
  per-call position is a no-op by construction. This also unblocked the new `lint_wiki` assistant tool, whose
  first real-machine run is what surfaced the timeout. Side note for whoever touches that code: two other places in
  `assistantTools.ts` split a path with `/[\/]/` for the same reason (telling the two knowledge-base lines
  apart) and one of them had lost a backslash to a shell heredoc, so on Windows both lines were labelled
  文章线 - silently, because the output still looked plausible. The helper `wikiIndexPath` and the line label are
  now covered by a mutation-checked test.


- **The ball is never backed by anything now - the glow layer is gone entirely.** The user reported
  it a second time: "the background is not transparent while it is thinking". That is the *same*
  observation that led to the 2026-09-26 change, where an always-on glow was narrowed to
  "only while there is a state" - so the remaining case was exactly the busy one. **The same
  complaint arriving twice means the ask is not "draw less of it" but "not at all"**, so the whole
  layer is removed: the element, the three state rules, the `radial-gradient`, `@property --hue`,
  and the `drift` / `breathe` animations, including the two selectors still named inside
  `prefers-reduced-motion` (the tests caught that leftover - a half-removal is what this change was
  most likely to leave behind). The state light is not lost: all four states already have their own
  face (thinking / sorry / tired / idle), and that is the primary signal.

- **The pressed face is preloaded, because fetching it cost a frame.** The user reported that
  clicking the ball flickers. Measured: a face served from the daemon takes **~15 ms** against a
  16.7 ms frame at 60 Hz, and the faces are CSS backgrounds - fetched **when first used**, which is
  the instant the ball is pressed. So the first press can render an empty face for a frame. The
  repo's own comment had recorded the symptom before ("球在按下的一瞬间会闪成一张空图") and fixed
  the whitelist at the time; the whitelist is correct, but the first use still waits for the
  network. All five faces are now fetched when the page loads, inside the `hasShell` branch (the
  browser fallback never shows the ball).

- **Expanding the ball no longer makes it jump first.** The user described it precisely: click the
  ball and it flashes off to one side, snaps back, and only then does the bubble open. The cause is
  an ordering rule the collapse path already followed and the expand path did not: `setMode` resized
  the window **first** and told the page **afterwards**. The ball is pinned to a *corner* of the
  window, and which corner depends on the layout (`anchor-top` when the bubble opens upward,
  `bubble-right` when it opens to the right) - so during those frames the ball was still drawn with
  the default anchor, i.e. at a **different corner of the already-larger window**. Measured against
  the real layout the vertical error is (bubble height - ball size), which is why it reads as a jump
  rather than a flicker. Collapsing had the same trap and dodged it deliberately ("tell the page
  first, then shrink, and wait at least one frame - otherwise a 76x76 window shows a sliver of the
  bubble"); expanding now does the same: reposition the anchor, **wait for the page to actually
  paint** (two `requestAnimationFrame`s via `executeJavaScript`, with a ceiling so a busy renderer
  cannot stall the expansion), resize, and only then switch modes. It is two messages rather than
  one because switching modes early shows the bubble inside the still-96px window - the same trap
  from the other side.

- **Clicking anywhere in the panel threw a `ReferenceError`.** `renderer.js` called
  `closeQuickMenu()` on every document click and on Escape, and that function **does not exist** -
  the menu is native now, opened by the main process outside the window, so there is no in-page menu
  to close. The two calls were leftovers from the in-page implementation. Harmless in that a throw
  inside one listener does not stop the others, but it fired on every click, and it was the
  unhandled error jsdom kept reporting while the panel tests passed. Deleted, with the reason left
  in place.


- **Every subprocess now runs without a console window.** The user reported a black command-line
  window popping up while the assistant was thinking and using tools. The cause was not one bad
  call: the daemon itself is started with `detached` + `stdio: 'ignore'` + `windowsHide`, so it has
  **no console** - and Windows therefore creates a fresh one for every child it spawns unless that
  call site says `windowsHide: true`. Fourteen of the fifteen sites did not, including the assistant
  tool path itself (`pythonBridge`) and the Python probes (`--version`, `import sqlcipher3`).

  Nothing was watching that before, which is why it was missed everywhere at once, so a test now
  requires it: it walks `src/`, `bin/` and `resources/panel/`, strips comments (a comment that
  mentions `spawn(` is not a call - that tripped the first draft), and fails listing any call site
  without `windowsHide`. Writing the scan found a fifteenth site that a hand pass had missed
  (`bin/weflow-cli-electron.cjs`), and the mutation check confirms removing one is caught.

## 1.8.2

### Changed

- **The ball no longer casts a drop shadow.** The user reported that the mascot's background was
  "not fully transparent" and asked for another round of matting. **It was not the image**: the PNGs
  are clean - all four corners are `(0,0,0,0)`, the solid areas sit at alpha 253/255, and the only
  low-alpha pixels are 674 anti-aliasing edge pixels within 4-8 px of solid ones. What they were
  seeing was `filter: drop-shadow(...)` on the ball, which was there deliberately ("a transparent
  subject needs it to separate from a light wallpaper") and pinned by a test.

  The trade-off is recorded rather than glossed: that earlier check covered whether the **transparent
  background** stayed legible against light, mid and dark wallpapers - not whether it does so
  **without a shadow**, so this is a new, unverified visual change. If the cat ends up looking
  unmoored, the line to bring back is a much fainter one, and it is written in `panel.css` next to
  the `filter: none`. Hover feedback survives on `scale(1.02)` and `brightness(1.08)`.

## 1.8.1

### Changed

- **The floating ball's "busy" face is now a thinking face, not a contemplative one.** `busy` is
  exactly "a request went out and we are waiting" (`renderer.js`'s `ask()` sets it on entry), so the
  face is drawn as thought: eyes glancing up-left, level brows, a flat mouth line, a paw under the
  chin. The file was renamed `mascot-focus.png` -> `mascot-thinking.png` rather than overwritten -
  a file named `focus` holding a thinking face is the next misreading - and all four places that
  name it were updated together (CSS, the static whitelist, the packaging list, the state-to-file
  assertion). The two reconciliation tests in `panel-packaging.test.ts` went red when the wiring
  landed before the image did, which is how we know they actually watch that gap.

  It is generated with `gpt-image-2-ca` through `/images/edits`, using the existing mascot as the
  base so only the expression changes - a text-only redraw takes the style and framing with it. The
  first prompt asked for "thinking" and produced **worry** (downturned mouth, lowered brows), which
  collided with the offline face; the second spells out "level brows, flat mouth line, not sad, not
  worried". Geometry is checked rather than eyeballed, because the ball clips to a circle: content
  box 171x212 (the existing faces are 170x214), farthest solid pixel 125.2 against a 125.4 limit,
  and **0 solid pixels outside the inscribed circle**.

### Fixed

- **The panel ball went blank after the assistant restarted, and it now repairs itself.** The
  daemon generates a fresh token on every start, and the window's cookie is planted when it opens -
  so a window that outlives a restart holds dead credentials. Every `/panel/*` request then answers
  401, and because those responses carry `Cache-Control: no-store` the ball's face (`panel.css`
  loads it as a background image) fails to load: **a blank ball, with the window otherwise looking
  normal**. Measured: the window had been open 25 hours against a daemon 25 hours younger, which is
  how this was found - the user reported "my agent icon is gone".

  The window can now recover by itself. Its status poll already distinguishes the case, and on
  `UNAUTHORIZED` it asks the main process to re-read the endpoint file, refresh the cookie and
  reload. The credentials still never reach the renderer - the main process does the reading, which
  is where they belong; the preload's exposed surface grows by exactly one method and
  `test/panel-packaging.test.ts` pins that list, so widening it stays a deliberate act. Two guards
  keep the cure from being worse than the disease: **the reload only happens when the file's token
  actually differs** from the cookie (otherwise an unrelated 401 would reload forever), and there is
  a five-second floor plus a three-attempt cap in the page, because **a reload storm is harder to
  diagnose than a blank ball**. Verified end to end by restarting the daemon and watching the
  window reload on its own: the log's load counter went 73 → 74 with nobody touching the window.

- **The assistant denied that unconfigured capabilities existed.** A tool whose prerequisite key is
  missing is deliberately left out of the tool table - a tool that cannot run makes the model try it
  and apologise with errors in the answer. But the model then has no way to know it exists, so
  asking "what am I reading" produced **"I don't have a get_weread tool"**. That is true about the
  tool table and false about the product: the feature was built, it just was not configured, and the
  user reasonably concluded it had never been built. The system prompt now carries a
  `[本机没启用的能力]` line naming each missing prerequisite and telling the model to answer "this
  part is not configured" with the setting to change, **not** "I don't have that tool". The tools
  stay unavailable - this only stops the model from denying the feature exists. Verified by asking:
  the answer is now "can't do it, the only reason is dashscopeApiKey isn't configured".

- **`get_reading_stats`'s description claimed a question it cannot answer.** It said to use it for
  "what have I been reading lately" - which is exactly the WeRead question, so with `get_weread`
  hidden the model reached for this tool and returned public-account push counts ("the most active
  account is 红星新闻"). The tool was right; the sentence had taken over another capability's
  question space. It now says what it covers (public-account pushes and daily-report processing) and
  that it carries no reading data.

- **`weflow-cli weread ...` read the key from a different place than the assistant did, so
  configuring it satisfied one and not the other.** The CLI read `WEREAD_API_KEY` from the
  environment (which is what WeRead's own setup page tells you to export); the assistant's
  `get_weread` availability check reads the **config key** `wereadApiKey`. Configure the config key -
  as the CLI's own error message invites you to - and `weread shelf` still answers "not configured".
  Both are accepted now, environment variable first because the official guidance is not to hand the
  key to an AI. The resolution is a pure exported function with a test, rather than a line inside the
  CLI's closure where the last change to it could not be tested.

  The same session turned up two smaller things. **A tool hidden for a missing key is invisible to
  the model, so the user gets "I don't have that tool"** - which is what happened here: the answer
  said the capability did not exist when it did, unconfigured. That is now written into
  `OPERATIONS.md` next to the filtering rule it belongs to, because "no key" and "no such feature"
  read the same from the outside. And **`wereadService`'s header pointed at
  `~/.claude/skills/weread-skills/`, which does not exist on this machine** (checked, 2026-09-28);
  it now points at `https://weread.qq.com/r/weread-skills`, the page that actually issues the key -
  the key's origin had been recorded nowhere in the repo, which is why finding it took reading a
  stored article from May. `OPERATIONS.md` gained the setup steps, including that **`configService`
  reads the file once at construction, so a running assistant needs a restart** to see a new key.

- **Fixing the CLI was not enough, and only an end-to-end run showed that.** With the key configured
  and the assistant restarted, asking "what am I reading" still produced "WEREAD_API_KEY is not set".
  The assistant tool was reading the secret a **third** way: its availability check loads the config
  key - which is why the tool appeared in the table at all - and then calls the module singleton
  `new WereadService()`, whose constructor read only the environment variable. A tool that is visible
  but cannot run is the worst of both designs. The no-argument constructor now resolves both sources
  through the same `resolveWereadApiKey`, which fixes every user of the singleton at once.

- **`get_weread`'s notebooks mode printed `undefined` for every book title.** `/user/notebooks` nests
  the title and author inside `item.book`; the item itself carries only `bookId`, `noteCount`,
  `bookmarkCount` and friends. The code read `item.title`, so the output was still well-formed - it
  just had no names in it, which is why nothing failed. Verified against the gateway's real response
  and pinned with a test that stubs **that** shape: the previous test for this branch stubbed an
  empty list, so it could not have caught it.

  All three of these were found in one session by configuring the key and then actually asking the
  assistant a question, not by reading the code. Two of the three were invisible from any single
  file: the secret had three readers and they disagreed.

- **`wiki lint`'s duplicate-title report was rebuilt rather than re-tuned, and the two tiers now
  differ by an order of magnitude in how much you should trust them.** The old rule - a common
  substring of ≥5 characters covering ≥40% of the shorter title - was reasonable at 5,062 pages of
  long Chinese headlines and had become useless at 22,374: it reported **424,697 groups**, starting
  with coincidences like `AI Agent开发框架` against `生物信息学LLM Agent综述` (both contain "Agent").
  A 20-pair sample showed the rule firing correctly on every pair, which is the point: the threshold
  had stopped discriminating, so the implementation was never the problem.

  Two directions were measured and rejected before settling. **Keeping the long-common-substring
  rule** fails because `Claude Code 上下文窗口` and `Claude Code 联网能力` share six characters and are
  nonetheless different concepts - sharing a product name is not the same as being the same thing,
  and that class dominated the false positives, so no ratio value separates them. **Containment
  without a guard** fails because `DeepSeek` is swallowed by `DeepSeek 融资`, `DeepSeek-V4 涨价` and so
  on; that is a *hub*, not a duplicate (measured: `agent` is contained in 731 names, `模型` in 643).

  What it reports now: **314 groups that differ only in spacing, hyphens, case or an English plural**
  (`GLM 5.1` / `GLM-5.1`, `AI 编程` / `AI编程`), scored by hand at 24 of 24 being the same node - and
  these use `_utils.normalize_concept_name`, the **same function** `wiki compile --merge-duplicates`
  uses, so the list is directly executable. Measured on the real vault, every group the lint reports
  is one that tool would merge (the extra 14 groups it would act on are same-filename collisions,
  which the lint reports separately). Plus **379 groups where one name is the other plus a qualifier**,
  explicitly labelled a lead rather than a conclusion because hand-sampling puts its precision near
  half - the other half being "hub vs its own subtopic" (`国家自然科学基金` /
  `国家自然科学基金申请书`), which should not be merged.

  The normaliser moved to `_utils` so the two readers cannot drift apart, and a test now pins the
  **function object** rather than "two copies that look alike". The old caveat that the rule could not
  catch the case it was written for still stands and is still written down: `打虎！陈勇被查` and
  `中建集团副总经理陈勇被查` are the same event with different headlines and share only four characters,
  so no substring rule reaches them - the tier-1 rule replaces "wrong and noisy" with "narrow and
  right", not with "complete".

  Two silent-failure traps were caught by mutation testing rather than by reading. **The bucketing
  width must stay at or below the shortest shared substring the rule can accept**, or the report
  quietly loses pairs; the first version of the equivalence test read the module constant for both, so
  changing the bucketing width moved the reference implementation with it and the test stayed green -
  it now uses literal contract values. And **the hub filter's count is computed from the same 8-gram
  index** rather than an O(n²) scan, which keeps the whole pass at 0.1s.

- **The two tiers compare within a directory, not across the whole vault.** The two concept
  directories are two knowledge bases and `--merge-duplicates` runs per directory, so a group spanning
  them can never be acted on by the tool the report points at. The first version compared across both
  and, after the merge, was still listing `AI 工具` and `GLORIA` as "groups that tool will merge" -
  which was simply false. Cross-line same names are a separate report (`duplicateTitles`).

- **`--merge-duplicates` was silently dropping card names from the surviving page's `sources:`.** It
  carried that key over from the main page only, so every merged group lost the absorbed page's
  entries - measured on the real vault, **all 315 groups** lost at least one. The body's source lines
  were always correct (those were unioned); only the frontmatter side was short, and that key is what
  `--relabel` feeds to `source_kinds_for`, so a page could lose a `来源/文章|聊天|收藏` tag. It now
  unions. The 315 pages were repaired from a pre-merge backup, and the `output/` vault is not under
  version control, which is why that backup existed. **The loss turned out to have changed no tag**:
  0 of 22,033 pages carry sources from more than one line, so this was a latent defect rather than a
  live one - recorded that way rather than as a rescue.

- **Merging was run on the 314 same-node groups**: 341 pages absorbed (338 article-line, 3 chat-line)
  out of 22,374, leaving 0 groups in that tier. `wiki lint` still reports **0 broken links** - the
  `aliases` written by the merge are what make that true, and it is why no other file had to change.

## 1.8.0

### Added

- **The assistant can walk the knowledge graph more than one hop at a time.** `search_knowledge` used to return
  the page plus its **direct** neighbours, and the tool description told the model it could "walk them one at a
  time" - which meant deciding, on its own, which neighbour to look up next. It now takes a `depth` (default 1,
  max 3) and returns the layers: from `RAG`, one hop is `向量化模型` / `LangChain` / `大语言模型` /
  `上下文窗口`, and two hops reaches `向量数据库` / `Milvus` / `语义搜索`. Measured on the real vault: `RAG` has 4
  one-hop neighbours and the second layer is reachable only through them.

  Three rules, each because the obvious version misbehaves: **deduplicate** (a concept reachable by several paths
  keeps only the first arrival, since BFS guarantees that is the shortest - otherwise the same name shows up at
  several depths), **cap the nodes** (4 neighbours × 4 × 4 is 64; an uncapped third hop drowns the output, and a
  graph the model cannot finish reading is worthless - and when it truncates it **says so**), and keep counting
  neighbours that have no page yet rather than swallowing them.

  Pinned by a scenario that asserts the **arguments**, not the tool name: `argsMatch: /depth=2/` - because
  "called `search_knowledge`" and "called it with two hops" are different things. That is what the field is for,
  and its format is `key=value` (the existing `time-window` uses `/since=/`), not JSON - my first attempt wrote
  `/"depth"\s*:\s*2/` and the scenario duly failed while the assistant was doing exactly the right thing. The
  soft assertion deliberately does not name a specific concept: asking about `RAG` actually matches `2-Step RAG`
  (the name match takes the first hit), so what the second hop returns is not predictable - it checks that the
  layers were laid out at all. Full evaluation **23/23**.

- **The chat cards are now in the Vault, under `Sources/Chat/`, and the assistant can search them.** They were only
  in `output/chat-notes/`, which meant a person browsing Obsidian could see the chat *concepts* but never the
  conversation that produced them (the timeline, the people, what is owed) - and, measurably, **1,176 of the chat
  concept pages' "source" links pointed at nothing** plus **30 pointed at the page itself**. Both numbers are now
  zero (1,822 resolve).

  **The cards had to be renamed on the way in**: a chat card is named after its conversation (`白马非马`,
  `老表亲戚群`), and a concept page can carry the same name. Two files with the same stem make `[[白马非马]]`
  ambiguous in Obsidian, which would have broken links that currently work - so the Vault copies carry a
  `会话-` prefix, and both the copy step and the link fixer read that prefix **from one shared constant**
  (`_utils.CHAT_CARD_PREFIX`), because two copies of it is exactly the "same fact written twice" failure this repo
  keeps hitting. `compile_wiki --fix-source-links` rewrote 1,825 links across 1,520 pages; `vault_search
  --type note` now reads `Sources/Chat` too, so "what did we talk about in that group" reaches the cards.

- **The chat line is now part of the knowledge graph, and it arrives as a *people* graph.** `chat-notes` over a
  400-day window produced **132 conversation cards** (one call per conversation) and `wiki compile` turned them
  into **1,519 concept pages** - `Wiki/Concepts` went from 2,115 to **3,634 pages**, and the graph from 4,162 to
  **5,538 concept-to-concept edges**. The chat corpus holds **572 distinct people** and **992 topics**, and only 4
  concepts overlap with the article line - a conversation is a genuinely different knowledge space from a feed of
  articles, and its natural nodes are people (龙老师 ×31, 耿哥 ×21, 闫学昊师兄 ×17, 董老师 ×15 …). The pages carry
  `来源/聊天`, which the graph view already colours separately from `来源/文章`, so the two sources are one graph
  and still tellable apart.

  **The first attempt at this was wrong in a way worth recording: it read 19% of the text and said nothing.**
  `NOTE_MESSAGES = 120` was a per-conversation cap, so across 138 conversations holding 80,246 messages only
  **9,010** were ever sent - and `--days 400` did almost nothing, because fetching the newest 120 messages and then
  filtering by date can only ever pass messages inside the window. That version produced 254 people; the corrected
  one produces 572. Fixing it meant **paging through the whole conversation** (`get_messages` takes an `offset`),
  against the measured ceiling: the user's requirement was that **one group chat stays in one context** (splitting
  would hide cause and effect), so the question was whether it fits - and the API's own error answered it:
  `maximum context length is 1048576 tokens. However, you requested 1052622`. Two fixes then: the per-card output
  cap had to go from 2,000 to **16,000** tokens (34 conversations were failing with `finish_reason=length`, JSON
  truncated mid-object, reported only as "the model did not return usable JSON") and the prompt is trimmed from the
  **oldest** end against a character budget when it would not fit. The second run: **132/132, 0 failures.**

- **`[[name]]` now carries which section it came from, because dropping it made the model overwrite the material
  with its own prior.** A chat card separates `### 话题` from `### 人`, and aggregation used to treat every link
  identically. Measured consequence: a contact whose nickname is **白马非马** got a page about 公孙龙's classical
  paradox - and the material handed to the model said, verbatim, "对话对方，准备面试和申请博士，学生证在我这里".
  The refs now carry `人物` / `话题` into the reference line (`- [白马非马]（人物 · 白马非马 · 聊天）：…`) and the
  prompt says explicitly that a `人物` row means a **person** from the user's chats and must not be written as a
  same-named thing, work or allusion. Rebuilt, that page now reads "白马非马是用户聊天记录中的一位联系人，正处
  最后一学年，准备面试和申请博士", tagged 联系人 / 求职升学 / 私人对话. "Not annotated" and "annotated as a topic"
  stay distinct - an article note has neither section, so it gets neither label.

  This is a **contract change**: `scan_articles`' `wikilinks` went from `(name, desc)` to `(name, desc, kind)`.
  The four downstream tests that read it as pairs were updated to the new shape rather than loosened.

- **`search_knowledge` now returns a concept's neighbours, so the assistant can walk the graph.** The Obsidian
  graph view is a human artefact - nothing on the assistant's side ever read "the graph", and the tool returned a
  single page in isolation. But a concept page already lists its neighbours under `## 相关概念`, and **measured,
  every page is shorter than the tool's 2,000-character slice** (median 730), so that list was already inside the
  returned text - it was simply never pointed at, and the neighbour definitions were never resolved. The tool now
  appends each neighbour that has a page **with its one-line definition**, plus a count of the ones that do not
  (so the model does not chase a name it cannot fetch), and says in its own description that it can be called again
  for any neighbour. One lookup becomes "this concept plus a small subgraph of what it connects to": verified
  against the real vault - `提示工程` returns 上下文学习 / 思维链 / 工具调用 / 智能体, `Claude Code` returns
  Codex / Vibe Coding / 生物信息分析, each with a definition.

  Two details are pinned by test. **Neighbour names are sanitised before being joined into a path** - they come out
  of file *content*, so `../../etc/passwd` has to stay a filename (same rule as `compile_wiki`; a mutation check
  removing the sanitiser fails the traversal test). And the "no page" case reports a count rather than a name,
  because a name the model cannot fetch is worse than no name.

- **`backfill_articles --vault-copy / --vault-sync`: the raw articles reach the Vault's `Sources/` without their
  images.** The daily pipeline copies a whole day into `Sources/WeChat/<date>/` with `copytree`, images included -
  ~200 MB per day, which is why the historical import deliberately skipped it (165 days would have been ~33 GB).
  But skipping it left the Vault **inconsistent in a way only the user could see**: `002_Literature/` held the
  reading notes for all 175 days while `Sources/` held raw material for eleven, so the raw-material layer looked
  stale and nothing in the vault said why. `copy_to_vault()` copies the `.md` files only (~14 KB per article,
  ~24,500 files, ~400 MB for the 165 days) and copies them **file by file rather than replacing the directory** -
  `copytree`'s semantics would have removed whatever else was in that day's folder, and this is the user's Vault.
  `--vault-sync` runs the copy alone, without fetching and without calling a model, because the days were already
  on disk from the earlier import; that is how the gap was closed.

  The honest note on this one: I had flagged the omission **in conversation only**, and a caveat that lives only in
  chat is not a caveat. Nothing in the vault or the docs recorded that `Sources/` was deliberately incomplete.

- **The right-click menu gained a second section: eight one-click functions.** The first section is still the
  `quickReplyContacts` list (draft a reply for that person); below it now sit 谁在等我回话 / 我的待办 / 今日日报 /
  最近统计 / 阅读统计 / 最近会话 / 朋友圈 / 微信读书. The admission rule for that section is one thing only:
  **the action has to be completable by one fixed sentence**, because a native menu cannot collect input. Anything
  needing a query, a target, or a path (search, export, read-one-message) is therefore excluded rather than shipped
  as an item that does nothing when clicked. All eight map to tools the assistant already has, and each prompt names
  its tool (`用 get_todos …`) - that is a **request to the model, not a hard binding**: the assistant still picks,
  from 19 tools, and a wrong pick is possible; adding a parameter to force it is a separate decision for when that
  actually happens.

  Three design points, each with a failure it prevents. **The prompt travels with the menu item** rather than the
  page keeping its own id→prompt table: two tables drift, and the drift shows up as "the menu says A, clicking does
  B", silently. A test asserts `renderer.js` contains no action id at all. **The cost line is derived from the
  actions** (`costNote()`), not written down: the hand-written version goes stale the moment a function is added,
  and a stale cost warning is worse than none - it makes people think they were told. It now names exactly which
  items call a cloud model (起草回复 and 谁在等我回话) and says the rest are local reads, so the free ones are
  visibly free. **An empty contact list no longer produces a nearly-empty menu**: the functions and the close item
  are always there, so a user who never configured `quickReplyContacts` still gets a usable menu.

  The pick payload changed shape (`{kind:'contact'|'action', …}` instead of a bare name), and the page ignores
  anything that is not that shape - during an upgrade the menu and the page are two pieces of code in two processes,
  and a bare name from the old contract would otherwise be dispatched as a *draft nobody asked for*.

- **`article-notes --topic` - and the number that makes it worth having.** Concept extraction is the only step in
  this pipeline that costs money per article, so the useful question is not "does it support filtering" but "how
  much does filtering save". Measured rather than estimated, on the 3,498 backfilled articles: news is **57%**, AI
  28%, academia 13%. Carding only AI therefore removes more than half the spend. The filter is applied **before**
  `--limit`, so `--limit 500 --topic AI` means "500 AI articles", not "the newest 500, of which keep the AI ones" -
  the latter silently under-delivers as you page backwards and looks like the corpus is that small. Both spellings
  of the topic field are accepted because the Vault's existing 1,633 notes use `hasTopic: [[AI]]` while newer
  writers use `topic: AI`; the read path is `compile_wiki.article_topic`, unchanged. The confirmation prompt now
  shows the resolved topic list, because the number a person is agreeing to is the filtered one.

  **The per-call cost was measured against the account balance, not a price table.** DeepSeek's published pricing
  has at least three mutually inconsistent versions in circulation (¥3/6, $0.14/0.28 and $0.22/0.66 per million for
  the same model), so 120 probe calls were run and the balance delta read: **¥0.22 for 120 calls, ¥0.0018 each,
  ≈¥30 for 16,600 articles**. The balance endpoint resolves in ¥0.01, so that figure carries a few percent of
  rounding error, and it is the account's real spend rather than a rate card's. It also surfaced something no price
  table could: the balance was ¥27.12, i.e. the unfiltered run would have run out of money partway through. Two
  cheaper-looking knobs were measured and rejected: `MAX_ARTICLE_CHARS` (3000) never binds, because a real call
  sends ~1,394 characters, so lowering it saves nothing; and the input is the larger half (803 of the ~945 tokens
  per call), so trimming what is sent is the only lever that could matter - which is what `--topic` does, by sending
  nothing at all for the articles that are not wanted.

### Fixed

- **`search_knowledge`'s fallback reported source titles as body text.** When no page *name* matches, the tool
  falls back to "the file contains this word" - and since pages can now list 300+ article titles in their `## 来源`
  section (the 2026-09-27 change), a word appearing in some *source title* was reported as "concept pages whose
  **body** mentions it". Measured with `Grok-4`: 4 pages hit, **3 of them do not contain it anywhere in the body**
  (only `AdsMind` does). The fallback now searches the body only (`pageBody` cuts at `## 来源`), and returns the
  matching **sentence** rather than just the page name - the assistant had been filling the gap itself, answering
  "Grok-4 was one of the models under test" from its own knowledge rather than from the page.

  **This was caught by a scenario that then had to be deleted, and the deletion is the interesting part.** The
  natural assertion - forbid the false-positive page names - does not hold: those three names appear **inside the
  real page's own body** (its 「相关概念」 section), so a model mentioning them is *right*, and any page-name
  blacklist misfires. The accuracy question therefore moved down a layer: `pageBody` is a pure function, and it is
  pinned in `test/knowledge-neighbors.test.ts` (mutation-checked: returning the full text turns it red). That is
  "put the test where it can hold", not "skipped".

  Verified end to end: before the fix the answer listed four pages and invented detail; after it, it says there is
  **one** place and quotes the sentence. The eval re-run after the change is **22/22**.

- **`--pages-from-cards` checked the wrong name and produced 5 broken links.** The guard that skips names the
  linter cannot resolve (square brackets, or a title ending in `.md`) was applied to the **concept** name but not
  to the **source** name - so `IPCC图件`, an ordinary-looking concept whose single card carries square brackets,
  got a source line pointing at nothing. Caught by running the health check right after, not by reasoning: the
  broken-link count went 0 -> 5. Fixed, the five pages removed, and a test pins it (with the mutation checked:
  dropping the guard turns it red).

- **Every embedded Dataview query in the Vault was broken, for two independent reasons.** The reading-note and
  daily-note templates embed `dataview` code blocks - **25,676 reading notes and 176 daily notes, 100% of both** -
  and a user checking their vault found them rendering as raw code. Two separate faults, either of which alone
  would have broken the feature:

  - **The plugin was not installed.** `.obsidian/plugins/` did not exist and `community-plugins.json` was absent,
    while five places in the code write `dataview` blocks and one CLI message tells the user to use "the Obsidian
    Dataview plugin". Dataview 0.5.68 is now installed and enabled in this vault (it is a community plugin, so it
    is per-machine and **not** covered by the repo - `output/` is gitignored, and the 2.4 MB `main.js` is
    deliberately not committed).
  - **The queries themselves were wrong**, which is the part that would have survived installing the plugin.
    `SORT date DESC` sorts by a field **that does not exist** - the reading notes carry `published`, not `date`.
    The daily note used `WHERE created = date(...)`, and `created` is the **generation** date: one backfill wrote
    `created: 2026-09-26` into all 25,676 notes (measured), so 175 of the 176 daily notes listed nothing and the
    one for the generation date would have listed twenty-five thousand. The related-articles table also listed the
    note itself, and led with `rating` - a field that is empty across the whole corpus because it is the user's to
    fill, so ten rows of blanks.

  Both templates now use `published`, exclude `this.file`, and lead with `published`/`source`. **Fixing the
  templates alone would have fixed nothing**: `create_reading_note()` skips a note that already exists, so the
  25,852 existing files needed a rewrite pass - `create_reading_notes --refresh-queries` rewrites only the code
  block, is idempotent, leaves notes without one alone (counted, not silently skipped), and reports the notes whose
  topic is empty separately rather than inventing one. Applied: 25,845 + 7 rewritten, 0 stale queries left.

- **`vault_search --type note` searched a directory that has never existed in this repo.** It read `Vault/Notes/`,
  while the notes live in `002_Literature` (25,676 reading notes), `001_Daily`, `003_Ideas` and `008_MOC`. The
  failure mode is the quiet one: the directory simply does not exist, so the type returned nothing and the command
  still reported "found N results" - **the largest layer of the vault was invisible to search**, which is the layer
  an agent would most want. Now it reads the four real directories (4.6s for a query over 26k notes). Pinned by
  `test/vault_search_test.py`, including a regression test for the empty result, verified by a mutation check that
  puts `Notes/` back.

  The article half of the same function keeps its `--days 90` default, which **now hides most of the corpus**: the
  vault spans 175 days (March-September), so anything older than ~90 days is unreachable unless `--days` is raised.
  Reported, not changed - it is a default, and changing it changes how long every search takes.

- **The graph view drew 10,466 ghost nodes.** `.obsidian/graph.json` had `hideUnresolved: false`, so the concept
  graph (`search: path:"Wiki/Concepts"`) rendered not only the 1,963 concept pages but every link target that has no
  page - 10,466 of them, against 2,794 real concept-to-concept edges. Verified by screenshot after the change: the
  graph now shows only real concepts (`Github Copilot`, `GPT-6`, `Claude Code Skills`, `MCP 权限边界`, …). It is
  also **sparse** - 1.4 real edges per page, 498 pages with no concept-to-concept link at all - because most of each
  page's "related concepts" are concepts that have no page yet. Building the remaining 3,725 candidate concepts
  would densify it; that is a spend decision, not a defect.

- **The generated MOCs and the concept pages' source links had defects that only show up in Obsidian.** The user's
  bar was "the knowledge base should present properly in Obsidian", so this pass looked at what renders rather than
  at what runs:

  - **`008_MOC` had a file called `MOC-.md`.** The 9 notes with an empty topic produced a malformed filename (and
    the same guard was missing in the ideas generator, which would have written `想法-.md`). Empty-topic notes now
    produce nothing on either path; "no topic" is not a topic.
  - **MOC links were written as `[[<date>/<filename>.md|<title>]]`** while the notes' own links are
    `[[<date>-<title>|<title>]]`. Neither full path nor bare filename - Obsidian resolves by shortest unique path,
    so these were at best accidental. Links are now the file stem, which is what the rest of the vault uses; verified
    against the filesystem: 4,497 links, 0 ambiguous stems, 0 pointing at a file that does not exist.
  - **The MOC's own dataview query carried the same two faults fixed in the reading notes** (`SORT date` on a corpus
    that has `published`; `contains(hasTopic, …)` on a nested list).
  - **Concept pages linked their sources by the *card's* filename, not the reading note's.** Cards are named
    `{date}-{full title}` and reading notes `{date}-{title truncated to 50}`, so for short titles (98%) they coincide
    and the link resolved **by luck**; measured, **125 of 5,470 source links pointed at nothing**. The card already
    records the right name in its `from` field - unused until now. `compile_wiki --fix-source-links` repairs the
    existing pages locally (no model calls, ~20 lines, idempotent, only touches lines with the ` — ` form that the
    source section uses, so the "related concepts" list is unaffected). Applied: **125 -> 17**, and the 17 remaining
    point at the user's own notes, which are deliberately not copied into the Vault.
  - Those same source links also carried a **`.md` extension** - the only place in the whole vault that does. Every
    other link (note-to-note, daily notes, MOCs) omits it. Whether Obsidian tolerates the extension is not something
    I verified, so the code now emits the form that is known to work and is used everywhere else.

- **Three more Vault directories were declared but never written to, and the existing Vault had never been
  initialized at all.** Auditing the layout after the `007_Wiki` bug found the same shape twice more: `Wiki/Entities/`
  and `Wiki/Topics/` are created by `vault init` **and described in the generated `README.md` as if they existed**
  ("实体页（公众号、作者等）", "主题总览页"), while nothing in the codebase writes either; and `.obsidian/app.json`
  set `attachmentFolderPath: 'Assets'` while the layout creates `_attachments` - so attachments would have gone to
  a folder that is never created. That `Assets` literal appeared exactly **once** in the whole codebase, which is
  the same tell as before: a name that exists in one place and contradicts the two places that matter. Both are
  fixed, and the generated README's directory table now describes the real layout - including an explicit note that
  the `000`-`008` series is for the user's own notes and stays empty until `vault promote`, because "empty by design"
  and "empty because nobody built it" are indistinguishable to whoever is reading the vault.

  The two layout lists live in different languages (the TS CLI and a Python script) so they cannot share a
  constant; the agreement is pinned by test instead - the pattern this repo already uses for its four copies of
  `TOPIC_ORDER`. `test/create_reading_notes_test.py` parses the CLI's `dirs` array and asserts that it creates
  everything `VAULT_DIRS` expects, declares no `Wiki/*` subdirectory besides `Concepts`, and that
  `attachmentFolderPath` names a directory the layout really creates. Three mutation checks.

  **The Vault itself had never been initialized.** It had been built by `create_reading_notes`'s `mkdir` loop, so
  `README.md`, `Templates/article.md`, `.gitignore` were missing and `.obsidian/app.json` was `{}`. One
  `vault init --yes` fixed it - **with Obsidian closed first**, because Obsidian holds `app.json` in memory and
  writes it back on exit, which would have silently reverted the corrected attachment path.

- **The Vault declared its concept directory twice, and one of the two was never written to.** `VAULT_DIRS`
  (created by `vault init` / `create_reading_notes`) listed `007_Wiki/Concepts`, while the pages are actually
  written to the top-level `Wiki/Concepts` - which is also what `vault_rag`, `vault_search`, `wiki_lint`, the
  assistant's knowledge search and two CLI options read: **six places versus two**. So every init created a
  `007_Wiki/` folder that no code ever filled, and a user looking at their vault sees an empty directory in the
  numbered series and concludes the knowledge base was not updated. That is exactly how it was reported. The
  template now declares `Wiki/Concepts` (the real one), the CLI's init preview lists it once instead of twice, and
  the empty directory is gone from the vault. `test/compile_wiki_test.py` pins that whatever the template declares
  for concepts **must equal** where `compile_wiki` actually writes - verified by a mutation check (putting
  `007_Wiki/Concepts` back fails the test).

### Changed

- **The assistant eval now actually exercises the knowledge base.** Of its 19 scenarios, the only
  knowledge-related one was `which-store-knowledge` - "does it know *which* store to search" - and none made it
  read a page. That was fine when the vault held 5,062 pages; it now holds **22,374**, and the knowledge line is
  the part of the assistant that grew most. Three scenarios added: a concept from the article line, one from the
  chat line (whose answer - "project lead / coordinator / ABaCAS" - is **only** in the page, so the assertion
  cannot be satisfied from general knowledge), and one concept the vault does not contain at all.

  **The third is the valuable one**: it asks about something with zero matches, and the point is not that it
  answers, but that it **does not invent**. It calls `search_knowledge` twice, finds nothing, and says so -
  asserted with `toolEmpty` rather than "the answer must contain one of these words", because the wording of an
  honest "I don't have that" is unbounded. That reasoning was already written next to `EvalCase`; this is the
  first scenario to use it for its intended purpose.

  **The eval immediately caught something, and it was my scenario, not the assistant.** The first version asked
  "who is 龙老师?" - and the model reasonably read that as a question about a person *in the chats* and went to
  `search_chats` (it made no claims about the failure, which is the `tool-failure-honesty` behaviour working).
  The question now names the store ("in the concept pages I organised, who is 龙老师?") because that is the path
  the scenario exists to measure. All three pass three runs in a row (9/9) and the full suite is **22/22** - the
  first baseline this line has had.

  Note these three **depend on this machine's vault** (the pages are real files, not synthesised like the
  sessions and favourites), so they will fail elsewhere or after a rebuild - and that would not be an assistant
  problem.

- **The WeChat reader residue in note summaries is cleaned up - 1,484 of them, and the scope mistake is recorded
  too.** The cleaner runs *at read time* (`create_reading_notes` calls `strip_wx_ads` while writing a summary), so
  notes written before that logic existed were never cleaned: 1,484 of 25,676 carried page-button text inside their
  summary (`继续滑动看下一个`, `去阅读`, `轻触阅读原文`). New notes are already clean, so this is a one-off
  backfill: `create_reading_notes --clean-summaries`, local and idempotent. Re-measured afterwards: **0 notes left
  with residue in the summary**.

  **The first version swept the whole vault** (`Path(vault).rglob('*.md')` instead of the notes layer) and cleaned
  **10,949 raw articles under `Sources/WeChat`** as well - material, which this command has no business touching.
  The damage was checked with a diff rather than assumed: what was removed is the page-button phrases and
  underscore rules, plus 167 lines of whitespace normalisation; **no article content was lost**. `Sources/` has no
  backup, but it can be re-copied from `output/biz-daily`, so it is reversible - and it was left as is, because
  reverting would put the interface noise back while leaving the 4,809 articles that have no summary section
  uncleaned, i.e. a larger inconsistency. The scope is now pinned by a test (the "sweep the whole vault" mutation
  fails).

  One more criterion worth recording: the check is "this summary section **contains** one of the eight known
  phrases", **not** "the text changed" - `strip_wx_ads` also normalises whitespace and strips, so any paragraph
  with a trailing newline "changes", and using that as the trigger rewrites everything (measured while
  investigating: the criterion was always true and reported all 25,676, against a true answer of 1,484).

- **Two more duplicate pairs merged by hand, out of the 741 Jev had proposed.** The loose "similar name" set
was narrowed by rule to 27, and reading each one's actual definition left exactly **two** that are the same
thing (`TRAE IDE Linux 版` / `TRAE IDE Linux 版本`; `100 万 token 上下文窗口` / `100万token上下文`). The
other twenty-five were rejected on meaning rather than on spelling - "日均Token调用量" is a rate where
"Token调用量" is a total, "2026世界人工智能大会" is one year's event against the series, "Token 节省" is a
concept where "Token 节省技巧" is a set of methods. Merged with aliases; nothing linked to the dropped
names, and the health check reports **0 broken links** across 22,374 pages.

That is the useful form of this result: a decision model proposed 741 merges, a string rule then cut them to
27, and a human reading the definitions kept 2. The graph's name-level duplication is now settled.
- **`wiki lint` is fast again at the new scale - and the "similar names" count it prints is now mostly noise.**
  The near-duplicate check compared every page with every other (5,062 pages took 1-2 minutes; 22,445 is twenty
  times the pairs). It now builds a 5-gram inverted index and compares only within a bucket, which is **the same
  result by construction**: the criterion is "longest common substring >= 5 characters", and that is equivalent
  to "shares a 5-character substring", so a pair that shares none cannot pass. Verified two ways - brute-force
  comparison on random data (2,258 groups either way, identical as sets) and the real vault: **22,381 pages in
  4.2 seconds**.

  The fix also made something visible that the slowness had been hiding: at this size the check reports
  **425,187 pairs**, up from 13,367. Pages grew 4.4x, pairs grow quadratically, and the new pages are
  single-article fragments whose names overlap heavily (`Agent…`, `Token…`). That number is no longer a signal -
  the one time it was investigated properly, roughly three pairs out of 741 candidates were genuinely the same.
  The report still prints only the first ten, so it stays readable. Loosening or tightening the criterion would be
  a change of meaning, and there is no evidence yet for what it should become, so it is recorded rather than
  adjusted.

- **17,383 pages built without calling a model** - `wiki compile --pages-from-cards`. The candidate backlog was
  priced at about **¥31** and the user's verdict was "that's a bit expensive", which is right: every candidate is
  mentioned by exactly **one** article, so there is nothing to synthesise - a model would only be rewording a
  single sentence. The definition is taken from the `desc` the card already carries, related concepts come from
  **co-occurrence** (the other concepts in the same article, median 3), and the source is that article.

  **The honest description is "reusing output already paid for", not "no model involved".** Those `desc` lines
  were written by `article_notes` when it extracted concepts, so the quality is that line's quality: most read
  like definitions (`招聘陷阱` - "job seekers should be wary of high-salary bait"), a few are meta-descriptions
  (`免疫生态位` - "the other thing the article title splits out alongside cell types"). Pages carry
  `summary_by: card` to say where the text came from, following the `summary_by: model` convention that
  `user_notes` established.

  Verified: **0 broken links** among the 17,319 new pages' source lines, only 40 ended up with an empty
  "related concepts" section, and a re-run builds 0. Two of the four mutations that had to be caught were about
  coverage, not correctness - and one guard was added after the first dry-run showed it building **18,885** pages
  across every card directory at once, which would have put chat-line concepts into `Wiki/Concepts`. The flag now
  requires explicit `--cards` and prints the exact commands when it is missing.

- **A scale problem the new pages exposed: `wiki lint` no longer finishes in reasonable time.** Its
  near-duplicate check compares every page with every other (5,062 pages took 1-2 minutes; 22,445 is **20x** the
  pairs). The check is the one that produced the 13,367 "similar names" and, through that, the 74 duplicate
  groups worth merging - so it cannot simply be dropped. It needs bucketing (compare only within a name-length
  band or a normalised prefix) rather than all-pairs. Not fixed here; recorded so the next person does not
  discover it by waiting.

- **One concept, one node: 74 groups of duplicate pages merged** - `wiki compile --merge-duplicates`, local.
  The user's read was that the graph was piling up rather than getting better: "some of them are the same thing
  but drawn as different ones". Measured: 74 groups, 80 redundant pages, where the same concept had two or three
  nodes because of a hyphen, a space, or a plural (`GPT 5.6` / `GPT-5.6` / `GPT5.6`, `AI skill` / `AI skills`,
  `AI 工具` / `AI工具`).

  **The trick that makes it cheap is `aliases`.** The group keeps the page with the most sources (`claude code`
  has 411, `ClaudeCode` has 7), the other names go into that page's `aliases`, their source lines are merged in,
  and the extras are deleted. **98 links pointed at the names that were deleted and every one of them still
  resolves** - because Obsidian honours aliases, nothing else in the vault had to change. Verified by listing
  all 80 dropped names and checking each is an alias on some page: 80/80, and 98/98 links covered.

  The criterion is a normal form (strip spaces/hyphens/punctuation, lowercase, drop an English plural tail), not
  the lint's "5-character common substring" rule - that one calls `Claude Code` and `Claude 4.8` a pair, and the
  merge deliberately does not. The lint now reads `aliases` too, so those names stop being reported as "not built
  yet, run compile with a higher limit" (that count fell 9,421 -> 9,384).

- **500 pages built from the 17,774-candidate backlog, as a trial.** The candidates are all "mentioned by exactly
  one article", which is why the earlier `--min-refs`/`--limit` filters had left them alone. Sampling them: the
  definitions are specific and readable (`TRAE SOLO 模式` - "AI-driven development mode that plans and runs the
  whole flow from requirements to preview"), each links 3-4 related concepts, and each has exactly one source.
  The other 17,274 were left alone pending a look.

  With them came a fix that was written down but not implemented: `generate_concept` truncated the page's source
  list to five (`for r in refs[:5]`), which is the *prompt* budget's job (`build_ref_lines`), not the page's. A
  page that lists 5 sources while the index says 301 is exactly the gap `--refresh-sources` was written to close,
  so new pages would have been born needing it.

- **Old concept pages can grow new edges now** - `wiki compile --refresh-sources`, local, no model calls.
  The gap it closes was measured: `wiki compile` only creates pages for concepts that do not have one yet, so
  an existing page is never touched - a new article that mentions `DeepSeek` does not add a line to that page.
  Meanwhile `OPERATIONS.md` claimed the concept pages were "a ledger". One run of the new flag rewrote the
  source section of **324 article-line pages (+3,164 edges)** and **34 chat-line pages (+148)**.

  Three things it deliberately does not do, each because the obvious version is silently wrong:

  - **It does not touch the frontmatter.** The body and `sources:` carry *deliberately different* names: the
    body needs what Obsidian can resolve (chat cards are `会话-<name>`, pointing at `Sources/Chat/`), while
    `sources:` needs what `source_kinds_for` can find under `output/<line>/<card>.md` (chat cards there have
    **no** prefix). Syncing either side breaks `来源/聊天` tag inference - and since tags only ever grow, the
    page still *looks* right afterwards.
  - **It only adds.** Source lines that no current card accounts for stay exactly as they are (322 of them on
    the article line); they are counted, not deleted. So a page can legitimately hold more sources than the
    index reports - the index says what the cards say *now*, the page holds every reference it ever had.
  - **It skips names that cannot be written.** Six with square brackets (Obsidian itself cannot resolve them)
    and one whose *title* ends in `.md` - that last one would have turned the lint's "0 broken links" into 1,
    because `wiki_lint.resolve` treats a `.md`-suffixed name as a complete filename while the real card is
    `….md.md`.

  Ordering is normalised with `sorted()` (a spec, not a history - measured: that costs 29 extra pages). Two
  mistakes were made and caught here rather than reasoned away: a lenient reader that was off by one character
  (`line[3:]` instead of `line[4:]`) manufactured **6,721 phantom missing edges** and would have driven the
  whole change, and the `.md`-suffix case above put a real broken link into `Codex.md`. The first was found by
  printing one concrete page when the numbers looked wrong; the second by running the lint afterwards.

- **The three real states each have a face now.** The panel has always tracked `busy` / `offline` / `quota`
  (that is what the coloured halo reads), so this adds three faces to classes that already existed - **no new
  state machine**. Busy is a calm half-closed eye with a small mouth (focused), offline is a closed sad eye
  with a squiggly mouth (apologetic), quota-out is a heavy-lidded eye with an oval sigh (tired). The halo stays:
  it is the ambient light you catch peripherally, the face is the detail you only see by looking, and they say
  the same thing at two distances rather than repeating each other.

  **The order of two CSS rules is a contract here.** `ball-happy` must come *after* the three state rules,
  because with equal specificity the later rule wins - otherwise poking the ball while the assistant happens to
  be busy shows the focused face and the poke does nothing visible. That is asserted, not commented.

  All three are generated through the same `/images/edits` + mask pipeline as the grin, with the mask widened to
  a third hole over the mouth (a resigned face cannot keep grinning). Each was checked for the thing that
  actually breaks a circular ball: **0 solid pixels outside the inscribed circle**. The busy face took two tries
  - the first came back with angled slits that read as *suspicious* rather than *concentrated*.

- **Poking the ball makes it grin.** Press it and it switches to a second face - the same cat with its eyes
  closed in a happy arc - and it goes back to the normal face the instant you let go. The first version held
  the grin for 900ms after release, on the theory that a click lasts a few tens of milliseconds and an
  expression that short is one nobody sees; **the user tried it and said the delay felt stuck**, so there is
  no hold at all now, and a test asserts the constant is gone rather than weakened. `pointercancel` also
  reverts, so a pointer stolen by the system cannot leave the ball grinning forever.

  The second face is a **generated** image, and both halves of that needed measuring. It was produced with the
  same endpoint's `/images/edits` (image-in, not text-in - `/images/generations` would have drawn a *similar
  cat* rather than *this* cat) with a mask whose transparent area was the two eyes and the eyelids. **The model
  still repainted the whole canvas**: mean colour distance inside the hole 197.8, outside 10.9 - so the shipped
  file takes the generated pixels **only inside the hole**, feathered at the edge, and keeps the original
  everywhere else, which is also why switching faces cannot shift the colours of the face. It came back **RGB,
  with the transparent background filled black** (the second time this endpoint has done that), so the alpha
  channel is taken from the source art.

  Both faces are cut and scaled from the same source with the same parameters, so they are pixel-aligned: the
  two 256px assets differ by 3.5% of their pixels, all of them in the eye strip. Switching is a hard cut, not a
  fade - an expression change is one frame long, and a fade reads as an image loading.

  One gap this closed rather than stepped around: `resources/panel`'s file list was hand-written and nothing
  checked it, so **a new asset could be added without a single test going red** (measured: deleting
  `mascot-happy.png` from the list kept the whole file green). The list is now a constant, and a test
  reconciles it against the actual directory in both directions.

- **The ball is a quarter bigger and answers the mouse.** `BALL_SIZE` 76 -> 96 (the window grows with it;
  the expanded window is now 528 wide instead of 508), and the artwork is re-derived with a 2% margin instead
  of 4%, so the cat itself is ~28% larger than before.

  The interactions existed but were too quiet to notice: hover only changed the shadow and brightness, and
  pressing gave `scale(.95)` with an `ease` transition - a 5% squeeze that snaps back rather than bounces.
  Now hover scales to 1.02, press squashes to 0.92, and the transition is `cubic-bezier(.34, 1.56, .64, 1)` so
  releasing overshoots back. **The enlarged-on-hover factor is 1.02 and cannot be raised on its own**: it is
  paired with the margin the asset is generated with (measured: at 1.02x, one solid pixel falls outside the
  circle; at 1.03x, ten - the window clips the ball to a circle and would shave its edge). A test pins both
  halves. Idle stays perfectly still, so "not moving" still means idle and the halo keeps being the only
  status light - and `prefers-reduced-motion` now stops the **deformation** too, not just the keyframes:
  disabling the transition alone made hover/press jump instantly to another shape, which reads as a glitch.

- **The ball wears the mascot's illustrated version; the pixel version is gone.** Same character, drawn
  rather than pixel-art. The new source is 1254x1254 with its content filling 98% of the canvas, which is the
  part that needed care: the ball clips its image to a **circle** (`border-radius: 50%` on both `#ball` and the
  face layer), so pasting it in as-is put the **green speech bubble entirely outside the circle** (10,368 solid
  pixels) along with the paws. The shipped asset is therefore cropped to the alpha bounding box and scaled so
  that the furthest solid pixel sits within 96% of the radius - 1226x1247 becomes 210x213 centred on a 256
  canvas - which measures **0 solid pixels outside the circle**, at 41KB (the source is 813KB). For scale: the
  previous asset filled 80% x 91% of its canvas, the new one 82% x 83%, so the character does not shrink.
  `serveStatic` reads the file per request with `Cache-Control: no-store`, so a running panel only needs a
  **page reload** - no restart. The tray icon (`tray.png`) is a separate image and still shows the old
  pixel-art mascot on its disc.

- **The two knowledge bases now live in one Vault, in two directories.** The chat line had grown to 1,519
  concept pages inside `Wiki/Concepts` alongside the article line's 2,115 - one graph, correctly, but the user
  asked for the two to be kept apart. `Chat/` now holds the chat side (`Chat/Concepts/`, `Chat/00-Overview.md`),
  `Wiki/` holds the article side, and no page mixes sources. `Sources/Chat/` (the conversation cards) sits next to
  `Sources/WeChat/` by the same rule.

  **The half that matters is not the move, it is that every reader had to learn about the second directory.**
  Five places read concept pages - `vault_search`, `vault_rag`, the assistant's `search_knowledge` and
  `conceptNeighbors`, and the MCP `wechat.get_concept` - and each of them reading one directory would have been
  **silently wrong rather than broken**: half the hits missing, no error, the tool still looking like it works.
  They now all read both, the CLI's `vault init` preview creates both, and the generated `README.md` describes
  both. The list itself cannot be shared across the Python/TypeScript boundary (and the MCP package is separate
  again), so `test/concept-dirs-agreement.test.ts` pins all five declarations to one; the two scripts that
  `import` it instead are checked for still iterating it.

  **A name still belongs to one page.** Splitting the directories immediately created 11 concepts that had a page
  on *both* sides (`DeepSeek`, `智谱`, `Agent Skills` …) - and two files with the same stem make `[[DeepSeek]]`
  ambiguous in Obsidian, which is the same failure the `会话-` prefix prevents for conversation cards. The
  second line now skips a name the first already has, and says so ("跳过 1530 个已有概念（其中 11 个在另一条线
  已有同名页）") instead of folding it into the re-run count. `wiki lint` reports **0** duplicate titles; those
  11 concepts live on the article side and the chat pages link to them, so the overlap still shows up as edges.

  Three things broke while doing it and were caught by running the suites rather than by reasoning:
  `conceptNeighbors` took a directory and began taking a list, and a caller still passing a string made
  `for (const dir of wikiDirs)` walk the path **one character at a time** and return "no neighbours" - a wrong
  answer, not a crash, so it now throws on a string; and `wiki lint` went from 7,872 broken links to **0** once it
  was told that a bare `[[name]]` may resolve to a card or a raw source as well as to a page.

- **`compile_wiki` generates concept pages concurrently too, and filters existing pages before spending.** Same
  pattern as `article-notes` and for the same reason: 1,893 pages took minutes at 8 workers instead of the ~53
  minutes the serial loop would have needed (it deliberately slept 0.5s between calls - a per-article throttle that
  only makes sense when one request is in flight, so the concurrent path does not sleep). Writing stays serial and
  **streams**: a page is written as soon as its answer arrives, so a killed run keeps what it already produced.

  One trap belongs to this step specifically, and it is a money trap rather than a correctness one: the existing
  pages were previously skipped inside the serial loop (`if out_file.exists(): continue`), which is safe by
  construction. Handing that check to the thread pool - submit everything, discard the results you did not want -
  would **pay for a page that already exists** and show up only on the bill. `build_jobs()` therefore filters
  before submission, and the test asserts the skipped concept's name never reaches `generate_concept`.

- **`article-notes` asks concurrently, which is 6.5x faster for the same money.** The loop was one blocking call
  per article; measured, that is 49 cards/minute against **319 cards/minute at 8 workers** - and the token count is
  unchanged, so the bill is identical (~¥11.5 for 6,409 articles). Only the *asking* is parallel: the writing stays
  serial, because a call is ~1.2s (pure network wait) while writing a card is sub-millisecond, so concurrency there
  buys nothing and only makes "who wrote which card" harder to trace. The default is 6, matching `biz_daily`'s
  `JEV_WORKERS` / `SUMMARY_WORKERS` / `IMAGE_WORKERS`, which measure the same upstream.

  **Why not batch several articles into one call** - it is the other obvious saving, and it was measured too: the
  fixed instructions are 459 characters (32% of a 1,394-character prompt), so 5-per-call cuts prompt tokens to 74%
  and would save roughly ¥2.3. It was **rejected on two grounds**. First, batching does not save *time*: latency is
  dominated by output length, so five times fewer calls each four times slower is a wash - concurrency is the lever,
  and the two are orthogonal anyway. Second, and the reason that matters: `compile_wiki.build_ref_lines` feeds the
  concept-page model up to five references collected **from different articles**, each being that article's `desc`
  line - so cross-article synthesis already happens, downstream and deterministically, and the `desc` line is its raw
  material. A `desc` means "what **this** article said about it"; five articles in one prompt makes "this article"
  ambiguous and blurs exactly the input the fusion depends on. **Not tested**: whether batched extraction yields
  *better* concepts. That needs human labels, and this project does not record calibration conclusions without a
  gold standard - what could be measured is how much the concept sets differ, which is consistency, not quality.

- **The floating ball is just the cat while idle.** The coloured halo behind it (`#ball .glow`) used to be painted
  at all times, which was the point when it was added - the user asked for a background that moves. Two costs only
  became visible after living with it: the ball always had a coloured disc behind it, so **"this image has a
  transparent background" was impossible to see**; and that same layer doubles as the status light (`offline` turns
  it red, `quota` amber, `busy` speeds it up and makes it breathe), so having it always on meant having no status
  light - offline, busy and quota-out all looked identical. Now the glow is painted **only** under
  `body.busy` / `body.offline` / `body.quota`: still = the cat, lit = something to say. This is the earlier request
  **narrowed, not cancelled** - the hue drift is still there, it just happens while busy rather than always.
  `test/panel-packaging.test.ts` was rewritten to pin the new contract rather than loosened: idle must be
  `background: none`, and each of the three states must appear **in the selector of the rule that carries the
  radial-gradient** - the first version searched the whole stylesheet for `body.quota #ball .glow`, which also
  matches that state's unrelated `animation: none` rule, so deleting quota from the gradient selector passed. A
  mutation check confirmed both halves of the new assertion fail when broken.

- **`scripts/backfill_articles.py` - pulling months of articles into a knowledge base, without the 250-hour path.**
  The existing `pipeline run --date` cannot do this, for three measured reasons rather than guessed ones: the CLI
  wraps the whole pipeline in a hardcoded 10-minute timeout (`bin/weflow-cli.ts:3906`) that one historical day
  already exceeds, so the run dies with zero output; `biz_daily` fetches serially *and* downloads 13-21 images per
  article (~30s each - a 25-minute pilot processed 47 articles); and none of that is needed downstream, because
  `create_reading_notes` reads only the md frontmatter (`title`/`source`/`url`/`topic`/`date`) plus the first ten
  lines of the body, and `compile_wiki` reads only the `## AI 摘要` section. So this script reuses `biz_daily`'s
  collection, topic-normalisation, md-writing and `.articles.json` functions - one implementation of each, not a
  second copy - and changes exactly three things: fetch concurrently, do not download images, do not emit the HTML
  reader. Measured over 2026-03-01..2026-08-31: 164 days, 25,603 articles, of which **12,386 actually need a
  fetch** (the other half already carry usable text in the database), ~2.5-3 articles/second at 10 workers.

  Two limits are stated rather than discovered later. **Image-only articles yield nothing**: measured on 3/4 and
  7/4, 27/40 and 44/60 carried text, and the rest are 图文 messages whose content is in the pictures - the same
  `内容过短` gate `biz_daily` applies, not a fetch failure. And the topic comes from `_guess_topic` (the keyword
  fallback `biz_daily` uses when AI classification fails) rather than a model, because this step makes no AI calls
  at all; the concept extraction that does is a separate, later command.

- **`article-notes --since / --until` - a date window, because "do all of September" is how a person says it.**
  `--limit` can only express "the newest N", so using it to cover a month either misses articles (a busy month
  exceeds the limit) or drags in the neighbouring month (a quiet one does not reach it). Measured on the real vault:
  September holds 710 article notes, 120 already carded, so the exact job is 590 calls - a number `--limit` could only
  have approximated. With the default incremental behaviour the already-carded 120 are skipped rather than re-asked.

- **Your own notes are now a source: the AI reads what you write and generates notes back.** The three existing
  lines all read *material* - articles, conversations, favourites. This one reads **notes the user wrote**, which
  changes what has to be guaranteed:

  - **your notes are read-only.** Nothing in this line writes to, moves or renames them; the output is a separate
    card under `output/user-notes/`. The failure mode here is asymmetric - corrupting someone's own writing is worse
    than any of the other three lines going wrong - so the promise is stated in the command's own description.
  - **the card's summary is marked `summary_by: model`** and the body says it in words: *this is the model's reading
    of your note, not your words*. A model's paraphrase mistaken for the user's own writing is the worst confusion
    this line could produce.
  - **links the user wrote win.** If the note contains `[[权限边界]]`, that concept name is kept, ranked first, and
    the model only fills in what the note did not mention - the user's own vocabulary is what they will search with
    later. Verified on a demo note: 5 concepts came out, one of them the user's own link.
  - **the prompt forbids completing the user's thoughts.** "Do not fill in what they left open, and do not pretend
    to be certain where they were unsure" is the one instruction that differs most from the other three lines - a
    note is where someone thinks out loud, and a model that tidies that up destroys the signal.

  It reads only the human layers (`000_Inbox`, `003_Ideas`, `004_Permanent`, `005_Reference`, `006_Projects`,
  `008_MOC`, `999_Archive`) plus loose `.md` files in the vault root, and never `Wiki/` (generated),
  `001_Daily`/`002_Literature` (pipeline) or `Sources/` (raw material) - otherwise the AI would read its own output
  back and drift. Pages derived from these notes are tagged `来源/我的笔记`, so `tag:#来源/我的笔记` separates
  "what I thought" from "what I read" in the graph.

  The loop closes: write a note → the AI makes concepts → edit a generated page and it is overwritten on the next
  run, so anything worth keeping belongs in `004_Permanent` (a human layer), where the next run will read it back as
  a source rather than discard it.


- **The generated vault now says four things it should have said all along**, borrowed from a local Obsidian vault
  built for exam prep (studied at the user's request; its own usage doc is a good example of a generated knowledge
  base that explains itself):

  - **"This page is generated"** - re-running `wiki compile` overwrites the overview and every concept page, so a
    note edited by hand disappears on the next run. Their vault states this in the first line of its usage doc; ours
    said nothing, which is how a user loses an afternoon of editing.
  - **"Model-generated, not human-verified"** - every concept page now carries `verified: false`. The borrowed
    vault's README states the same rule ("unverified information must be marked, never presented as settled"), which
    is also this project's own rule for drafts (`judged: false`); concept pages had no such marker.
  - **A nested type tag** (`知识/概念`) so `tag:#知识/概念` finds every knowledge page - their vault uses nested tags
    (`资料分析/增长`) exactly this way, and it costs nothing to make our pages filterable by kind.
  - **How to open the graph without hanging**: the vault holds thousands of source notes, so a global graph is
    unusable; the index now names the filter string (`path:"Wiki/Concepts"`) and the better habit (local graph).
    Their doc does this with a table of filter strings, including one row that says "the panorama is slow, use with
    care" - an honest note this project is happy to copy. The Obsidian config file itself is **not** written: our
    vault has no `.obsidian` directory, and guessing a config schema would be worse than giving the user a string to
    paste.

- **`wiki compile --relabel`** rewrites only the frontmatter of existing pages (type tag, verification marker) with no
  model calls - the same trick as `article-notes --refresh-summaries`: metadata is computable locally, so a
  convention change should not cost 51 regenerations. Idempotent, and tested as such.

### Fixed

- **The index page reported "0 references" for pages that had them.** Concept titles are written as `title: "甲"`
  (YAML-safe) while the aggregation keys them bare, so the lookup missed and the page looked merely un-cited rather
  than mis-counted.
- **The index could only ever show one source's view.** It is rewritten by every compile, and the three lines
  (articles, conversations, favourites) are compiled separately - so the table was rebuilt each time against
  whichever source ran last, and a favourite-derived concept showed 0 while its card was right there. Counts are now
  taken across **all** `output/*-notes` directories, which also fixes the first bug's symptom.


- **WeChat favourites are the third source feeding the knowledge base.** The daily line covers what the user *read*;
  favourites are what they deliberately **kept**, which includes articles outside the daily window and is a stronger
  signal of interest. `fav-notes` reads the favourites (locally), takes each one's text where the record carries it and
  otherwise fetches the article, then writes the same card shape the other two producers write - so the aggregator,
  the health check and the assistant's search needed no changes. The fetch **reuses the daily line's cached
  fetcher** rather than growing a second downloader: the WeChat-UA/WAF/gzip retry logic was hard-won once, and the
  cache means favourites the daily line already fetched cost no network at all - measured on the first real run, all
  four fetched bodies came from the cache.

  One thing this line does differently and says so on the card: the article and chat lines **copy** an existing
  summary, while a favourite record has none, so this summary is **model-written** and marked `summary_by: model`.
  `saved` (when the user kept it) is deliberately not called `published`.

- **`wiki lint` finds card directories with a glob now** (`output/*-notes`), because hard-coding the two directories
  meant a third source's inbound links would be silently missed - and the visible symptom of that is "every new page
  is an orphan", which is worse than no check at all.

### Fixed

- **`fav-notes --dry-run` was fetching articles.** The preview's own description says "local only", but it classified
  each favourite by calling the function that fetches - so a preview quietly made four HTTP requests. Classification
  is now a separate, side-effect-free question (`local` / `needs-fetch` / `none`), the preview reports what *would*
  be fetched without fetching it, and a test asserts that classifying an unreachable URL does not resolve it. Found by
  running the preview, not by reading it.


- **The chat knowledge line was failing on long conversations, and the failure said nothing useful.** Two of three
  conversations came back as "the model did not return usable JSON" - a reason that fits both "the output was
  truncated" and "the model answered in prose", so it points at neither. The cause was truncation: the JSON a long
  conversation has to produce (summary, timeline, topics, people) is token-hungry in Chinese and the call was capped
  at 1200 tokens, so the JSON ended mid-object. The cap is now 2000 with a longer timeout, and a failed call records
  the **tail of what the model actually returned**, so the next occurrence is diagnosable instead of guessable.
  Measured after the change: 3 of 3 conversations succeed.

- **A partly-successful build was reported as a total failure.** Both `chat-notes` and `article-notes` exited
  non-zero if *anything* failed, so "2 of 3 cards written" arrived at the CLI as a failure - and the CLI then
  replaced the script's own JSON (which listed which one failed and why) with a bare `(exit 1)`. Partial success is
  now exit 0 with the failures listed as data, only "nothing was written" is a failure, and the CLI's error path
  carries the child's output so a real failure keeps its reason. (This is the same principle as the earlier fix to
  the assistant prompt: an error that does not carry its cause invites a guess that is wrong.)

### Added

- **The knowledge base can now be checked, and the check found three things worth fixing.** `wiki lint`
  (`scripts/wiki_lint.py`, `weflow-cli wiki lint`) reports broken links, orphan pages, empty pages and duplicate
  titles - locally, with no model calls. The gap was recorded in D-049 as deliberately not done; running it against
  the real 39 pages is what justified closing it, because two judgements in it are not obvious:

  - **It separates "a concept the pages link to that has no page yet" from a break.** `## 相关概念` sections name
    concepts the model proposed; only the most-referenced ones have pages. Reporting both kinds as "dead links"
    gave "40 dead links" on a healthy knowledge base - the fastest way to teach a reader to ignore a tool. It now
    reports expansions as candidates (131 of them) and breaks as breaks (0).
  - **Card-side links count as inbound.** Without that, every single page was reported as an orphan, because pages
    mostly link *forward* to concepts that do not exist yet - so page-to-page inlinks are nearly empty by
    construction. Card links are the real inbound edges, and counting them turned "10 orphans" into the truth.

- **`wiki compile --min-refs` - a gate on what deserves a page.** Measured on the real corpus: 120 article notes
  produce **387 concepts, of which only 37 are mentioned by more than one** - the rest are the people, companies and
  single events of one news story. Writing a page each is not a knowledge base, it is a clippings file. `--min-refs 2`
  keeps only the durable ones, and it is what the default recommendation is based on, not a guess.

- **`article-notes` is incremental by default.** It used to re-ask the model about every article on every run:
  thirty articles meant thirty wasted calls, and the full 1633 would have meant a whole run's money. A card newer
  than its source note now means "skip"; a changed source note means "redo"; `--refresh` forces everything. The
  chat line keeps rewriting its cards on purpose - those are rolling snapshots.

- **`wiki lint` reports near-duplicate titles too**, because the same event arrives from several accounts. The real
  run produced a pair of *pages for one event* (`尼泊尔热索瓦泥石流` and `热索瓦泥石流灾害`) - which the orphan check
  cannot catch, since both have cards linking to them - and a four-source cluster around one news story, which is what
  inflates a concept's rank (visible directly in a page's `sources`). Report only, no automatic merging: two accounts
  writing about one event may genuinely carry different information, and deciding which page to drop is a person's
  call.

- **`wiki lint` also reports degenerate fields** - a metadata column that is nearly one value. Measured: all 39
  concept pages carry `topics: [学术]`, i.e. the field looks like metadata and says nothing, and it is the kind of
  thing that gets trusted. (An independent measurement on the same corpus: of 224 notes whose titles read as news,
  40 are labelled `学术`.) This reports the degeneracy rather than fixing the classifier - that classifier belongs to
  the daily pipeline, and changing its criteria without labels would be a guess.


- **The article line can now produce knowledge pages, and this was the missing half of the pipeline.** Running the
  aggregator over the whole Vault had shown that the 1633 article notes contain no concept links at all - only their own
  topic and `## 关联网络` path links - so `wiki compile` had nothing to aggregate, no matter how it was invoked.
  `article-notes` is the step that was missing: one model call per article, **asking only for concepts** (the summary is
  copied from the existing note rather than regenerated, which removes one chance to invent something), writing a card
  into `output/article-notes/` in exactly the shape the aggregator reads. Measured on this machine: 30 articles -> 110
  concepts -> 10 concept pages plus an index (`Wiki/00-Overview.md`), and those pages are now searchable locally
  (`vault search`, and the assistant's `search_knowledge`, which until today could only answer "the knowledge base has
  not been generated yet"). The concepts that come out are specific ("标签配料表一致性", "椰子水全覆盖风险排查") rather than
  taxonomy words, which is what the prompt asks for in so many words.

  Two noise sources in the *upstream* material surfaced in that trial and are deliberately not papered over here: some
  article notes carry the WeChat reader's own UI text inside their summary (`在小说阅读器读本章 去阅读`), which the card
  copies verbatim because copying is the point; and the topic classifier puts unrelated stories in one bucket (a murder
  case and a food-safety notice both came out as `学术`). Both belong to the daily pipeline rather than this one, and both
  are recorded in `docs/PROJECT_STATE.md`.

- **The knowledge-card helpers are shared rather than copied.** `chat_notes` and `article_notes` produce the same kind of
  card, so `note_path`, `plain` (the `[[...]]` stripper) and `SEPARATOR` now live in `_utils.py` - one implementation,
  two callers - and `_utils` is deliberately dependency-light so the CI job that installs only `zstandard pycryptodome`
  can still import it (importing `chat_notes` would have dragged in `nt_decrypt`).

- **Chat conversations can now feed the knowledge base, as notes the existing wiki pipeline aggregates.**
  Until now the knowledge side had one source - `output/biz-daily`, the article line, over ten thousand files -
  while conversations could only be searched, never condensed. `chat-notes` closes that: one knowledge card
  per conversation (summary, timeline, topics and people, what is owed), written to `output/chat-notes/`, and
  `wiki compile --source ./output/chat-notes` turns the `[[topic]]` and `[[person]]` links into concept pages,
  because `compile_wiki` already takes a `--source` and already aggregates wikilinks. The three shapes asked
  for therefore come out of **one** producer plus the aggregator that was already there: the card is the
  per-conversation shape, and topic pages and people/timeline pages are what the aggregator makes of it. No
  second aggregator, no new concept format, and backlinks and search keep working.

  Four things in it are deliberate. **The `[[...]]` links are rendered by our code, never by the model**: the
  model returns structured lists plus free text, and any `[[...]]` inside that free text is stripped, because
  `scan_articles` collects *every* wikilink in a body and cannot tell who wrote it - a stray one would invent
  a concept. **Thin conversations get no card** (under 3 messages or 60 characters) and are reported as
  skipped rather than silently dropped: a three-line chat yields a card saying "they said two things", at the
  cost of a model call and one contentless source in a concept page. **Cards roll, concepts accumulate** - one
  file per conversation, rewritten on each run with its window recorded, so repeated runs cannot multiply; the
  cards are a snapshot and the concept pages are the ledger. And the prompt tells the model it is a compiler
  rather than a writer, so contradictions stay side by side instead of being smoothed into one sentence, which
  is what a knowledge base of real conversations needs. That phrasing is borrowed from Tencent/WeKnora's wiki
  prompts, read for this work - as is the "too little content, do not call the model" gate.

  Verified by running it: `chat-notes --dry-run` reports 37 conversations, 2913 messages and 105,607
  characters over 30 days on this machine, and names the 3 that are too thin to card.

- **`wiki compile` now uses the per-topic description it was already collecting and throwing away.** The
  aggregator collects, for every wikilink, the sentence the source wrote *about that concept* - and page
  generation only ever used the note's overall summary. A concept mentioned in twenty notes was therefore
  described by five of their generic summaries, never by what any of them actually said about it. Reference
  lines now prefer that description and fall back to the summary, which is also what makes a person page read
  as a timeline: each contributing line is "what happened then".


- **The panel's ball wears the project mascot, with a transparent background.** `resources/panel/mascot.png`
  is served through the panel's static whitelist and drawn with **no disc behind it** - a mascot floating on
  the desktop rather than sitting on a plate - separated from light wallpapers by a `drop-shadow` that
  follows its outline. Three things about it were measured rather than eyeballed: the source
  (`weflow-cli图标.png` at the repo root) is 1024x1024 but its **content occupies only 600x689**, so the
  shipped asset is cropped to the alpha bounding box and resized to 256 (41KB rather than 1.37MB), which is
  what makes the mascot fill the space instead of floating small inside it; the source's background is
  **transparent**, so nothing needs keying; and the outline the ball used to have (a 1px light ring, drawn
  for the disc) had to go with it - with no disc it draws a circle in empty space. Dropping the ring is
  easy to forget, so a test asserts the two changes travel together.

  An earlier revision of this entry claimed the transparent version "loses the cat's body into a dark
  background". **That was my misreading of a small comparison image** in which the mascot was rendered too
  small; at the real size (76 logical pixels, inspected at 3x against light, mid and dark backdrops) it
  reads fine on all three. The user picked transparent after seeing both.

  The **tray icon is a separate asset** (`tray.png`) with the disc baked in: at 16-24 pixels on a dark
  taskbar there is no drop-shadow to lean on, so the disc is what keeps the outline legible. `nativeImage`
  cannot composite, so that one is generated with a canvas and committed.

- **The ball's background moves, and it says what the assistant is doing.** Behind the mascot there is now
  a soft-edged glow (deliberately not a hard disc - a hard edge is "sitting on a plate" again) whose hue
  drifts on a 26-second loop, slow enough that it is only noticeable if you look at it. Its **state**
  changes are the useful half: while a turn is in flight it brightens and breathes, and when the daemon
  cannot be reached it goes warm and **stops moving** (a still thing is what gets noticed). A collapsed ball
  previously gave no sign at all that it was working. `prefers-reduced-motion` turns all of it off, and the
  three state classes are set from one function so a branch cannot forget to clear `busy` and leave the ball
  glowing "working" forever - a test asserts that, and that no other code touches those class names.

  Two details that came out of looking at the renders rather than the code: the breathing animation's
  `scale(1.08)` overflowed the 76x76 window and produced a **scrollbar** inside the ball (visible as
  up/down arrows in a screenshot), so ball mode is `overflow: hidden`; and the glow is a separate layer from
  the mascot so the background can animate while the cat stays put.

- **The panel window no longer opens to an empty void.** It used to show nothing but black until you
  typed, which says neither what the assistant can do nor that it is alive. It now opens with a
  one-line explanation (including that the database never leaves the machine) and four **clickable**
  example questions that submit through the same path as typing. Verified end to end by clicking one
  through the DevTools protocol: the example block gives way, the question becomes a real turn, and
  the answer comes back through the shared quota counter (`[panel] 9字 → 已回复 (427字, 今日 1/100)`).

- **A local panel: talk to the assistant without logging into the WeChat channel.** Until now the only
  way in was the WeChat Bot channel, which requires scanning a QR code - so asking one question cost a
  login. `weflow-cli panel` opens a small always-on-top window that talks to the same brain: the same
  memory, the same daily quota, the same serial queue. It works with the channel logged out. Memory is
  shared rather than duplicated because facts are stored per conversation id and the panel resolves to
  the **single** allowlisted id when there is exactly one; with none or several it does not guess - it
  uses its own bucket and **says so on screen**, because "separate memory that you think is shared" is
  the exact failure this arrangement invites. `assistantPanelUser` pins the choice.

  The window is a client, never a second assistant (D-045). `weflow-cli panel --status --json` reports
  it, `panel --ask "…"` asks a question from the terminal, and both go through the same loopback
  endpoint the window uses. The endpoint binds `127.0.0.1` and cannot be configured otherwise
  (continuing D-004), but it is deliberately stricter than the daily reader: **every** request needs a
  per-run token, Origin is a second gate, and POSTs must be JSON. The token never reaches a command
  line - the Electron shell reads the endpoint file itself and installs the token as an `HttpOnly`
  cookie, while the browser fallback uses a one-time 60-second code.

  Also in this change, because the panel made them visible: `assistant start` **no longer refuses to
  start when the WeChat channel is not logged in** (it used to throw before doing anything, so
  "not logged in" meant "no assistant at all" - a field observation recorded in PROJECT_STATE), and
  `assistant status` gained `channelActive` / `mode` / `panelPort` / `memoryBucket`, because
  `messageChannelLoggedIn` only ever answered "is a token configured".

  **The ball needs Electron, and Electron is not a dependency**: with none installed, `panel` falls
  back to Edge/Chrome `--app` - a small window without an address bar, but **not** a floating ball (no
  frameless, no always-on-top, no tray, no global hotkey), and the command says so. Install Electron
  (`npm i -g electron`, or `npm install` in a checkout - the binary is downloaded by its own install
  script) and the same command opens the ball. Verified on Windows 11 with Electron 42: 76x76, no
  caption, always-on-top, the renderer authenticates through the cookie, and the ball is visible on
  screen, and the interactive paths were driven without a click (DevTools protocol for the ball-to-chat
  resize, a differential registration test for the hotkey). Doing that found two bugs a screenshot
  could not have shown: collapsing left the window at chat size because a non-resizable window
  ignores `setSize` on Windows, and the window sometimes never appeared because `ready-to-show` was
  listened for only after the load. A third came out of running what the tray items actually do: the
  "quit and stop the assistant" action spawned the CLI in a form commander rejects inside Electron's
  Node mode. Only a click on the tray menu itself remains untested; each item's effect has been run
  directly.

  The ball also now **sits in the bottom-right corner and stays where you drag it**. It used to open
  wherever Windows felt like it (measured 815,418, mid-left) and forget the position on every
  restart. The remembered spot is checked before use: if it is no longer reachable - a monitor was
  unplugged, or the ball was dragged off-screen - it falls back to the corner instead of leaving the
  ball somewhere invisible. Expanding to the chat window and collapsing back re-fit into the work
  area, so a 420x560 window no longer hangs off the screen when opened from a bottom-right corner.

- **The four "search" tools now name each other.** They search four different stores - chat logs,
  the knowledge base, assistant memory, and a semantic index over chats - and each description used to
  explain only what it searched, not how it differed from its siblings. `search_knowledge` did not say
  it is *not* chat history; `search_memory` did not say what "memory" means here. Each now names the
  store it covers and points at the others. Three eval cases pin the choice ("where did I mention X" must
  use the chat search and must **not** reach for the knowledge base), which is the part that was never
  measured.

  Found while writing those cases: **an empty fixture invites retries.** With a stubbed search that
  returns nothing, the model re-queries with different keywords - seven calls in one run, each with
  different arguments, so the identical-call guard cannot help. That is the fixture's doing, not a
  defect, so the fixture now returns a hit (and where it cannot, the count sits in the soft budget).

- **Tools this machine cannot run are no longer offered to the model.** `search_semantic` needs a
  `dashscopeApiKey` and `get_weread` needs `wereadApiKey`; with neither configured the tools were still
  in the tool list, so the model tried them and got errors back - the eval's ambiguous-contact case
  ended with a reply that reported "both search tools failed", which is not the assistant's fault but
  ours for offering a tool that cannot run. The list is now built by `availableToolDefs()`, which drops
  a tool when its prerequisite is missing, and the fast path - which dispatches **directly**, bypassing
  the list - goes through the same predicate before dispatching. The filter is deliberately narrow: it
  only drops tools that *cannot* run, never tools that merely have no data yet, because "run
  `weflow-cli wiki compile` first" is a useful answer while an authentication error is not.

- **The eval can now run a conversation, and it separates floors from expectations.** Multi-turn
  cases let it check the half of memory that was never verified: storing a fact was covered, **using** it
  was not. `memory-recall` says "remember I'm allergic to peanuts", then asks what to order for dinner;
  the hard floor is that the fact is in long-term memory, and whether the reply brings it up is reported
  as a soft expectation - it failed in one run out of three, and an intermittent red trains people to
  ignore the report. Same principle applied across the board: every `maxTools: 3` became a runaway guard
  (6) plus an efficiency budget (3), because the same question produced anything from 2 to 7 calls. The
  criterion is one line: **if the number or expectation moves with the model's route or wording, it is a
  budget, not a floor.** The only hard cap left is `no-tool`'s 0 - calling a tool when none is needed is
  a real defect. And `unknown-contact` stopped asserting on wording: the model's way of saying "not
  found" is unbounded (没找到 / 查不到 / 不存在 / …), so that case now asserts the **observable fact**
  that the tool call returned nothing.

- **A reading-stats tool, and the eval learned to tell a floor from a budget.** `get_reading_stats`
  answers "what have I been reading / which accounts post the most" from the local archive (it reuses
  `daily_stats.py` rather than recomputing the same numbers). Its second purpose is honesty about
  coverage: when the daily has not run, every account's processed count is zero, and saying "nothing was
  processed in these 7 days; the last report with content was 2026-09-05 (19 days ago)" is a different
  statement from letting the user believe those accounts had no content. That is how the staleness was
  found - a 7-day window showed all zeros while a 30-day window did not, and the difference was not the
  accounts.

  The eval now separates two things it had conflated: **floors are hard, budgets are soft.** The
  ambiguous-contact case was failing intermittently on a tool-call cap of 5 while every single run
  correctly asked which person was meant - the same question produced anywhere from 2 to 7 calls, so
  the cap was measuring the model's route rather than a defect. `maxTools` is now only a runaway guard
  (raised to 8) and `toolBudget` reports exceeding the budget as a warning that never fails the run.
  A flaky case is worse than no case: it teaches people to ignore the report.

- **`contacts -k` only searched the first N rows, and the assistant's name lookup had a blind spot.**
  Two defects found by trying to resolve a person by name on real data. First, the keyword filter ran
  **after** `LIMIT`: `get_contacts` fetched the first `limit` rows of `Name2Id` and only then filtered
  them, so on a 500-contact address book anyone past row 200 was invisible to a search - measured, a
  lookup by remark hit 3 of 10. The filter now runs first and truncates afterwards (extracted as
  `filter_contacts`, with tests). Second, `resolveTalker` searched only the most recent **300 sessions**;
  a name that is not in them fell through to "treat the query as a talker id", so the assistant reported
  "no messages" for someone who is plainly in the address book. Resolution now falls back to the contact
  book, matching remark / display name / nickname / alias / username, and still refusing when the match
  is not unique. Measured on 378 real contacts that are outside the recent sessions: **362 resolved, 10
  correctly refused for duplicate names, 0 ever resolved to the wrong person**, 5 single-character names
  still miss - and a miss is safe, because it returns null and the assistant says it could not find
  them rather than reading someone else's chat.

- **The assistant can see time now, and can reach a specific day.** Two halves of one gap: the
  system prompt carried no current date, so "上周三" had nothing to resolve against, and `get_messages`
  took only a message count - meaning a day far enough back was simply unreachable (the model either
  said it could not find it or, worse, answered from the most recent messages as if they were that
  day's). The prompt now opens with `[当前时间] 2026-09-23（星期三）09:05`, and `get_messages` accepts
  `since`/`until` (`2026-09-16`, or relative `3d` / `2w` / `12h`, resolved by day boundary rather than
  an exact 24 hours because that is what people mean). With a window it reads through
  `getMessagesInRange` and says which window it used; without one, behaviour is byte-identical to
  before. An unparseable time is a readable parameter error - guessing a window would be worse, since
  the model would then believe it had queried the period the user named.

- **The same call twice in one turn is now skipped.** The eval caught this rather than a person: its
  `ambiguous-contact` case produced **seven** tool calls, three of them the identical `list_sessions`.
  Repeating an identical call cannot return anything new, so the loop now returns a note
  ("this step is identical to an earlier one; use the result already in the conversation") instead of
  executing, and records the skip in the trace. The case dropped to four calls and passes.

- **The assistant now records what it did on the way to an answer, and you can read it.**
  `weflow-cli assistant trace` prints the last few turns, and sending `轨迹` in WeChat returns the previous
  one. Each record carries the fast-route's decision, every tool call with a **redacted argument summary**
  and the size of what came back, how many model round trips it took, and why it stopped
  (`answered` / `rounds-exhausted` / `llm-error` / `builtin`). Until now the only output was the reply:
  `TURN_DONE tools=5` said five tools were called and nothing about which five or with what arguments -
  which is exactly the question that could not be answered the one time it mattered (a turn that called
  `search_favorites` twice and `read_favorite` twice).

  Two things are deliberately separate. The **audit** is unchanged: events and byte counts, never
  content, because it is the egress record. The **trace** is a local debugging artifact and does contain
  argument summaries, redacted and truncated at 40 characters per value; the in-chat rendering leaves the
  machine (as the reply already does) and omits `userId`.

  On chain of thought: the trace carries whatever reasoning the provider returns in
  `reasoning_content`, clipped at 800 characters. The default `deepseek-chat` does not return any, so the
  field is empty and the trace says "无" rather than implying it thought something. Switch to a model that
  returns it (`config set aiModel deepseek-reasoner`) and the text shows up in the CLI view; it is never
  fed back into the conversation, since it is the model's monologue rather than an answer. See D-043 for
  the boundaries - including why "did this step produce anything" is a documented convention with a
  test-guarded whitelist rather than a real outcome field.

- **The assistant has a behaviour eval now** (`npm run eval:assistant`). Until this, there was no way
  to know whether the assistant was any good: the unit tests all inject a fake model (they prove the
  code paths still work, not what a model does with 16 tools), and the only other feedback was talking
  to it in WeChat and noticing problems by luck. The eval runs twelve synthetic cases against the real
  model and asserts **floor conditions**, not quality: did it call the tool that could answer the
  question, and did it not reach for tools when none was needed; did tool usage stay bounded (one
  question in the archive used five calls); does a **failing** tool get reported as failing - the case
  that earns its keep, since a script returning exit code 2 produces a reply that quotes the error,
  names the likely cause and offers an alternative, instead of "no one is waiting on you"; is an
  unknown contact ever given invented content; and is memory judged by **the outcome** (the fact
  landing in long-term memory) rather than by whether `save_memory` was called.

  Data is synthetic and the whole run happens in a temporary home directory, so the real audit log and
  memory file are untouched - writing the memory file from a second process would otherwise clobber
  whatever the daemon had just written. Only the model call leaves the process; any other request
  throws. The key is read from the real config (a `lock:` ciphertext that only decrypts on this
  machine) and the temporary config holds that one field and nothing else.

  Three limits are documented with it, because they are the difference between a useful instrument and
  a reassuring one: the expectations are the author's, not human labels, so this measures floors rather
  than quality; a first run that comes back green proves little, since cases and behaviour share an
  author - the value is in re-running it after a change; and **false failures are a real hazard**: this
  suite's own first version asserted the model would say "查不到" and failed twice on "查不了", so
  assertions now key off the tool's own diagnostics (error text, exit code) rather than the model's
  wording. The run history, kept as it happened rather than tidied: **6/8** (one fixture bug - the
  `getMessages` stub ignored the talker, so a non-existent contact was served another conversation's
  messages and the assistant reported them as theirs - and one false failure) → **8/8** → **8/8** →
  **7/8** (the same false failure again) → **8/8, 8/8, 8/8** after the assertion was rewritten. Both
  failures were the assertion's fault, not the assistant's.

  Four more cases followed (favourites search, a refused fetch of an internal address, two asks in one
  message, an ambiguous contact name - the last two guard "ask which one instead of guessing" and "serve
  both halves"), and their first runs produced **11/12 twice**, again entirely the assertions' fault:
  one case demanded a refused fetch be described with words the model did not use ("抓不了" versus its
  "抓不下来"), and another was capped at three tool calls when four was reasonable exploration. The
  rewrite settled on rules now written into OPERATIONS: **match stems rather than whole words**, **never
  assert on a forbidden word** (the model said "this is a local problem, **not** that nobody is waiting
  on you" and the regex read it as the opposite), and ask before tightening a cap. Three consecutive
  12/12 runs after that.


- **The calibration harness now covers what the report actually decides, and gained a half that needs
  no labels at all.** `quality_eval.py` sampled articles and asked you to label `topic` + `include`;
  `relevance` was not in that list even though **it** is what the admission rule reads. The label file
  now has three fields, `score` reports relevance agreement **and the mean error in levels** (two
  articles both labelled 中, one 0.4 too high and one 0.4 too low, score 100% accuracy while the score
  is visibly misaligned - accuracy cannot see that), and there is now a second threshold sweep: the
  relevance cuts `0.5`/`1.5` were written with the comment "暂定" because there was nothing to calibrate
  them against, and this is the thing that calibrates them. The printed sheet is **blind** - it no
  longer shows Jev's answer next to each title, because seeing it first is an unmeasurable inflation of
  the agreement being measured.

- `quality_eval.py consistency <file>` is the **label-free** half: it reports how often the two
  questions answer the same article two ways (high relevance with nothing usable, or low relevance with
  something usable). This came from `jev-chat-jarvis` (the vendored reference implementation of this
  exact pattern), whose task spec makes "questions must not contradict each other" a hard requirement
  and then needs a labelled set to enforce it - but a contradiction is self-evident, so this number is
  available today, without a single human label. Items missing either score are counted as undecidable
  rather than as agreement, which is the same discipline the rest of this repo applies to missing
  values.

- `jev_probe.py --criteria-ab` asks the same articles twice, current criteria (Chinese, score bins that
  name an abstraction level) against an English variant whose score bins describe concrete scenes, both
  rules taken from `jev-chat-jarvis`'s hard constraints. Measured here on 24 and 12 real articles: topic
  agreement 79% and 67%, relevance raw-score mean absolute difference 0.17 and 0.24, disagreements
  concentrated on the AI↔学术 boundary (a paper about a method) and 文学↔新闻. **These numbers say what
  a wording change moves, not whether it improves anything** - that still needs the labelled set, which
  is why the variant stays a candidate and the production criteria are unchanged.

- **The assistant can look at a picture.** 15.5% of the messages in one measured 30-day archive are
  images (242 of 1564), and the model used to see `[图片]` and nothing else - the largest remaining gap,
  and one no amount of prompt work closes. `get_messages` now renders an image as `[图片 #1234]`, and a
  new `look_at_image` tool takes that number, decrypts that one image **locally**, and attaches it to the
  next request as a real image. Looking is on demand: one image per call, at most two per turn.

  **An image is data leaving the machine, and it is treated as such.** `strict` mode (`assistantPrivacy`)
  holds images back at two layers - the tool refuses to fetch, and the request builder drops anything that
  got through anyway - and a drop is stated in the message body rather than happening silently. The
  `#N` handle is not even shown in `strict` mode: advertising something the tool will certainly refuse is
  worse than not mentioning it. The audit gained `IMAGE_SENT` and `IMAGE_HELD` lines (byte counts and
  message ids, never content).

  Reading is 0.9 s for a direct chat and up to ~19 s for a group with 30k messages, because the media
  index is rebuilt per read; a resolved image is cached under `output/.cache/read-image/`, so a second
  look at the same picture is cheap. Images never enter memory. See D-042 for the boundaries, and note
  that this makes a third-party vision model part of the loop - the same content already went to
  DeepSeek as text, but images are a new class of it.

- **Drafted replies** (`weflow-cli draft <联系人>`, and `draft_reply` in the assistant). Given one
  conversation it asks a decision model seven typed questions - what the other side is after, what they
  need, which action type fits, whether substance is owed, how risky it is (0-9), whether money is
  involved, whether a commitment is outstanding - then writes and ranks candidate replies. The pattern is
  borrowed from `jev-chat-jarvis`; the two things it does *not* do are the two worth doing, and both were
  read from its source: its risk level is only a badge colour (it never refuses a draft), and its judgement
  never reaches the drafting prompt. Here the judgement **is** injected, and the user's own earlier
  messages are offered as a tone sample. **It produces text and stops there** - no input-box filling, no
  key simulation, no focus stealing; `capabilities --json` reports `sendsNothing: true`.

  Money, or risk >= 7, means **no draft at all**: you get what the risk is and what to confirm first. That
  is the answer, not a failure. The 0-9 threshold is **picked, not calibrated**, so the band explains the
  refusal and does not decide alone; an outstanding commitment deliberately does *not* refuse (that is
  exactly when a draft helps) and constrains the prompt instead (`--gate-commitment` makes it hard).
  Three model calls per run, all reported by `--dry-run` before anything leaves the machine. In the
  assistant the transcript is masked first and handed over on stdin, and `strict` mode refuses outright -
  drafting has to send the body to remote models, and drafting from a body masked into "content N
  characters" would produce a fluent answer built on nothing. Text in the panel now carries a copy button,
  which says so when the clipboard write fails instead of claiming success. See D-047.

  Two things about the judgement are recorded rather than settled: the criteria are **English** (following
  the reference implementation, whose `TASK.md` states that is the model's main training language) while
  the money/commitment questions are reused **verbatim in Chinese** from `reply_debt` - two wording
  conventions against one model with **no A/B run**; and nothing here is calibrated against a gold
  standard, so "is this draft any good" has only your own reading as a check.

- `strict` mode now keeps the **type** of a non-text message. Its own rule says "time, direction and type
  only", and masking `[图片]` / `[文件] Base.csv` into `[内容4字已按严格模式屏蔽]` was throwing the type
  away too - the model could not tell an image from a text message, and learned nothing that was not
  already allowed. The payload is still withheld. A message the user *typed* that happens to start with
  `[图片]` is still masked as text: only the reader's own non-text labels count as labels.


- **Right-click in the panel for a quick reply.** A new `quickReplyContacts` config key
  (`weflow-cli config set quickReplyContacts "咸鱼梦想家,老王"`) holds the contacts the menu offers; picking one asks the
  assistant to draft candidate replies for that person through the existing `/api/ask`, so quota, the serial queue, memory and
  the copy button all come along unchanged. The list rides on `/api/status`, which the page already polls every 30 seconds -
  no new endpoint, no new IPC, and the preload surface is untouched. Three details are deliberate: the menu **states its own
  cost** ("drafting sends this conversation to two cloud models") instead of letting you find out afterwards, an empty list
  shows the command to run rather than an empty box, and the menu is **native** (built in the main process), so it draws
  outside the window - the ball is 76x76 and never expands for it. (The first version did expand it, and the user said what
  that felt like: right-click makes the 第二大脑 window pop up.) The browser fallback does not take over right-click at all -
  the native menu's Copy belongs to the user.

  Two follow-ups from using it: the window now **raises and focuses itself when it expands** (the chat window is not
  always-on-top by design, and closing a native popup can hand focus back elsewhere - the report was "I do not know where
  the answer went"), and the conversation shows a readable turn (`快速回复：<name>`) while what is actually sent is the
  explicit tool-naming request, so the chat log does not fill up with machine-shaped instructions.

- **The same right-click menu can now put the ball away.** Its last item is the only one in that menu that reaches no
  model at all: it hides the window instead of quitting the panel, so it is reversible by design - and that is exactly
  why the label names the way back (`关闭悬浮球（托盘图标能再打开）`). The ball runs with `setSkipTaskbar(true)`, so
  once it is hidden there is nothing on the taskbar to click; a label that just said "close" would read as "it is gone
  for good". Keeping "hide" and "exit" apart is the same line the tray menu already draws (its two exit items sit after
  a separator, away from show/expand), so no exit action was added here. The item is also the **only one present when
  the contact list is empty** - a user who never configured `quickReplyContacts` would otherwise get a menu of two grey
  lines that does nothing when clicked, including no way to dismiss the ball.

- **The semantic index's window is adjustable, and the preview says what it is.** How far back the index reaches was
  hardcoded at the two call sites, and neither the command nor its preview mentioned it at all: 90 days of chat and 30
  date-directories of articles. So "a personal knowledge base" quietly meant "the last three months", with nothing on
  screen saying so. `search-index` now takes `--days` and `--article-days`, and both the read-only preview and the
  confirmation prompt state the window they will use - the window is part of the cost, because more days means more
  text to the embedding service. **The defaults are unchanged (90 / 30)**: this makes a fixed number adjustable rather
  than changing what a build costs. Out-of-range values are refused before anything is read, with `0` called out
  specifically - a zero-day window builds an *empty* index that looks like a successful build, which is the same shape
  of failure as the `collect_articles` glob bug that once made the article half of the index silently empty. The
  script also reports the effective window in its JSON, which the CLI passes through, so a real run states what it
  used rather than leaving the user to infer it.

- **The interactive menu is the last extension point that had nothing watching it - now it has a guard.**
  `test/cli-menu.test.ts` requires the menu's entries and `showInteractiveMenu`'s `switch` cases to match **in both
  directions**, and every `runCmd` / `runSubCmd` the menu calls to name a command that actually exists. Both failures are
  silent by construction: an entry with no `case` does nothing when picked, and `runCmd` is written as
  `if (cmd) await ...`, so a renamed command makes an entry do nothing too. The command list is taken from the CLI's own
  `--help` rather than from the source, because the source cannot tell the two apart - 64 `.command('...')` call sites
  against 44 real commands. Four mutations (rename a menu value, rename a `case`, point `runCmd` at a missing command,
  point `runSubCmd` at a missing subcommand) each turn a specific assertion red, and `docs/EXTENDING.md`'s recipe C no
  longer carries a "nothing will tell you" caveat.

- **`docs/EXTENDING.md` - what to touch when adding to this project, and what will catch you.** The repository had no
  document for its most common kind of change, so "add an assistant tool" existed only as tribal knowledge spread over
  several files while "add a config key" was a comment at the top of a test. The guide gives one recipe per extension
  kind (assistant tool, config key, CLI command, hand-written MCP tool, Python workflow), each with the places to touch
  and **the test that fails if you miss one** - and, just as usefully, the two places where *nothing* will fail: the CLI
  command's interactive-menu entry has no guard at all. It also states plainly that there is no plugin loader and no
  public import surface, why that is deliberate (an in-process plugin would hold the same reach as the code it joins -
  database path, every API key, the message channel - against a standing rule that those boundaries stay explicit), and
  what a third party can use today instead (MCP, which is enumerable, scoped per tool, and switchable off).

### Changed

- **Drafting no longer stops when the judgement model is unreachable.** Jev's free period ended on 2026-09-25, and
  the first failure it produced here was already visible to a user as a bare "the tool errored (script exit code 1)"
  - with the assistant then *inventing* a reason for it ("the model was inconsistent about whether to promise
  something"), because the tool only reported the exit code. The judgement step is now degradable: when that call
  fails, drafting continues on DeepSeek alone and returns the candidates **with `judged: false`** plus the reason, so
  the CLI prints "这次没有判断…闸门没生效" before the list and the assistant tool prints the same. That warning is the
  point: the gate (money, or risk >= 7) exists only because a judgement exists, so a degraded run must not look like
  a normal one - and it must not look like one *to the model relaying it* either. Availability follows the same
  logic: `deepseekApiKey` is now the only required key, and a missing `typesafeApiKey` no longer hides the tool.
  Verified end to end with a deliberately bad Jev key: the candidates come back, the warning comes back, and the
  tools never send anything.


- **Dragging the ball in the chat window grew the window by the drag distance, and the extra width showed up as
  a gap between the ball and the bubble.** `panel:dragMove` re-read the window's *current* size with
  `getContentBounds()` and wrote it straight back with the new position - a read-modify-write, and the read is
  the one that lags. Measured with a probe driving the same IPC sequence (30 steps, a 30x24 move) on a window
  with the panel's own options: with `resizable: false` the size does not move at all, and with
  `resizable: true` (which is what the chat window is) it drifts by **exactly the drag delta, 30x24**. The size
  is now captured once in `dragStart` and reused for every move, which measures 0x0 in both cases. The symptom
  matched: the bubble is pinned to the window's left and the ball to its right, so any width beyond
  "bubble + gap + ball" appears between them - and expanding or collapsing re-asserts the exact size, which is
  why clicking the ball "reset" it. A test pins that `dragMove` never calls `getContentBounds()`.


- **The MCP surface now says what it actually is, and `draft_reply` on that surface needs an explicit
  confirmation.** Two claims were wrong and are corrected here rather than dropped: `capabilities --json`
  reported `safety.mcpDefaultReadOnly: true`, and the MCP guide said the default surface is read-only. The
  surface is **derived from the assistant tool table minus `save_memory`**, so it has contained a file writer
  (`export_chat`) and a model-calling tool (`look_at_image`) since those shipped. `capabilities` now reports
  `mcpDefaultReadOnly: false` plus a `safety.mcpSurface` breakdown (`writesFiles`, `callsCloudModels`,
  `requiresConfirm`), and the guide's tool table lists the whole surface instead of the eleven hand-written
  entries. Auditing that surface turned up one more thing: it lists **four** tools that send user data to cloud
  models (`who_owes_reply`, `search_chats`, `search_semantic`, `draft_reply`), not the two the first version of
  that declaration guessed, and `look_at_image` is now **excluded from the surface entirely** - it hands its
  image to the assistant's own model through a side channel MCP never reads, so over MCP it answered "you can
  see it now" while shipping nothing. The two tests whose names said "read-only tool surface" but only asserted that publishing, sending
  and memory writes are absent are renamed to say what they check.

  Because drafting sends a conversation to two cloud models, an **MCP call without `confirm: true` returns a
  preview only** - how many messages, how many characters, which two models - and nothing leaves the machine.
  The panel and the WeChat bot keep working without that flag: their boundary is the sender allowlist plus a
  user asking in a conversation only they can see, whereas an MCP client is a third-party process whose
  session nobody here can observe. `capabilities.safety.mcpSurface.requiresConfirm` declares it, the tool
  description states it (a calling model that does not know it cannot ask its user), and a new test drives the
  real MCP protocol to prove that a call without the flag never produces a draft. The same gate now covers
  the other three tools that send user data to cloud models (`who_owes_reply`, `search_chats`,
  `search_semantic`) through one shared message shape - each tool fills in what it actually sends, and the
  two whose scripts support `--dry-run` show real counts.


- **The ball no longer disappears when you open it: the conversation unfolds beside it, like an icon
  that is talking.** The window is now "ball + gap + bubble" as one piece (508x560), with the ball
  pinned to its own corner and the bubble growing out of the other side. The ball is the speaker, so it
  stays put - it is a **toggle** (click to open, click again to close), and the geometry is computed by
  `bubbleLayout` in `ball-position.cjs`, a pure function CI drives with synthetic displays. It prefers
  the bubble on the ball's left, bottom-aligned (the ball defaults to the bottom-right, so the bubble
  grows up-left without covering anything), flips to the right when there is no room on the left, and
  switches to top-alignment when the bubble would run off the top of the work area - a popover, in
  short. A 12px CSS tail points at the ball's centre. The mode still has **one source of truth** (the
  main process announces `{mode, side, anchorY}`; the page only requests changes), so the ball, the
  collapse button, the tray and the hotkey all take the same path, and `prefers-reduced-motion` still
  stops both halves. The page's layout moved into a new `#bubble` wrapper element, which is what carries
  the rounded corners and the background - the window's background is transparent now in both modes, so
  the gap beside the ball really shows the desktop.

  Measured on a real Electron window with the real page (throwaway probe, 150% display scaling), because
  two of the three things below were **not** visible by reading the code:

  - **The ball does not move.** Its offset inside the window is exactly 0 at every sample, the window's
    anchored corner is held constant, and sampling the ball's screen position through a whole expand and
    a whole collapse gives a spread of **at most 1px** (page-side, 23 and 30 samples). At rest before and
    after, the ball's screen position is identical (1608.33 both times; the 0.33 is the 150% scale).
  - **Two bugs found by measuring.** (1) The tween rounded each of the four edges independently, so the
    anchored edge wobbled by 1-2px (measured right-edge values 1683/1684/1685) - and since the ball is
    CSS-fixed to that edge, that wobble *is* the ball moving. `boundsAt` now takes the corner to pin and
    derives the coordinates from that edge, so it is exact. (2) Collapsing placed the ball at the
    **window's top-left** instead of the corner it actually sits on, which would have made it jump to the
    bubble's opposite corner; `ballRectInWindow` (also pure, now tested as a round-trip: for all four
    corners, expand-then-collapse returns the ball to where it started) fixes it.
  - **A platform wrinkle worth knowing**: asking this window for 76x76 at 150% scaling yields a ~79px
    client width - Windows enforces a minimum window width. Everything stays consistent because the ball
    is anchored to the edge rather than to a computed size, but the window is a few px wider than the
    ball, and the remembered position (`~/.weflow-cli/panel_position.json`) is the window's, not the
    ball's visual left edge.

  Frames land every 28-45ms (3-8 frames for a 190ms tween across runs), because the cost is the actual
  window resize, not the 16ms timer; the motion is time-based, so it drops frames rather than stretching.
  `BALL_SIZE` and the new `BUBBLE_SIZE`/`BUBBLE_GAP` live in `ball-position.cjs` only, with tests pinning
  the CSS copies (`--ball-size`, `--bubble-width`, `--bubble-gap`) to them. How it *looks* is still a
  human judgement - a screenshot cannot settle it, because GDI capture misses DWM-composited transparent
  windows.

### Fixed

- **91% of the article notes had WeChat's own reader UI text inside their summary.** The article body comes from
  WeChat, so it carries the reader's widget text and the footer's 原创 marker with the account name pasted three
  times - and the summary is built from the article's first lines, so it went straight in. Measured over the corpus:
  **1484 of 1633** notes are affected (an earlier count of 330 in this work was wrong - it counted one exact phrase
  over whole files, and the residue has several spellings). The cleaner now runs where notes are written and where
  knowledge cards are built, and re-running it over the corpus leaves **0** affected.

  Two things about it are worth recording. The pattern list that already existed in `classify_daily.py` was used only
  on the daily-report path - the note writer never called it - and comparing it against the real string showed it
  **missed one of the three reader phrases**: the actual text is 在**公众号**小说中沉浸阅读 while the list had
  在小说阅读器**中**沉浸阅读. The list now covers the observed forms, lives in `_utils.strip_wx_ads` (one
  implementation; `classify_daily.clean_ads` delegates to it and keeps only its daily-specific rules), and strips the
  underscore runs and the repeated account name as well. It is deliberately narrow: a test asserts that a sentence
  containing 沉浸, an underscore in a variable name and the word 原创性 comes back untouched.

  The **existing** vault notes still carry the residue - the cleaner applies to what gets written, not to what is
  already there - but the knowledge path cleans at the card step, so it does not reach concept pages. Rewriting the
  1484 existing summaries is a separate, approval-shaped action and has not been done.

- **The wiki aggregator was reading the wrong fields and treating non-concepts as concepts, and scanning the whole
  corpus showed why the article line produces nothing.** This was found by running the aggregator over the real Vault
  rather than over fixtures - 1633 notes, read-only - which turned up four things at once:

  - the notes' theme lives in `hasTopic: [[AI]]` (because the vault's own dataview queries use that field), while the
    aggregator read `topic`, so **every note's theme was silently empty**. It now reads both and strips the brackets;
    the writer was deliberately *not* changed, since renaming the field would break the user's Obsidian queries;
  - the summary section of those notes is headed `## 📋 摘要`, which the aggregator did not recognise - it was landing
    on the same paragraph only because the "first paragraph" fallback happened to reach it. That heading is now
    explicit, with the fallback kept for producers that write no heading;
  - **the body's `[[...]]` links are not concepts**: 1631 of 1633 notes have exactly one body link and it is their own
    theme (`> - **主题**: [[AI]]`), and the `## 🔗 关联网络` sections link **note paths** (`[[AI/某篇.md]]`). Taken at
    face value those produced a concept ranking led by "新闻 (752), 政治 (388), 学术 (341)" plus two `.md` file paths -
    a `--limit 20` run would have spent twenty model calls writing that into the Vault. Relation sections are now
    excluded by structure and path-shaped links by form;
  - and the conclusion: **after filtering, the article corpus yields zero concepts**, because these notes have no
    concept section at all. `wiki compile` cannot produce concept pages from articles until something extracts
    concepts from them - the same gap the conversation line closed on 2026-09-26 with `chat-notes`, which is the only
    source that produces them today. `--source`'s help text and `OPERATIONS.md` now state the requirement instead of
    pointing at a directory that cannot satisfy it.

  The theme is also *used* now, not just read: it appears in the reference lines handed to page generation and as
  `topics` in a concept page's frontmatter (`topic` had been collected and dropped since the aggregator was written).
  Eleven tests cover it, six of them new here, including one that pins the measured corpus shape and one that asserts
  a relation section yields no concepts *whatever* is written in it. Mutation checks caught two of my own tests being
  unable to distinguish the two filters - each filter alone could be deleted without a failure, because the fixture
  only exercised them together - so both now have a case of their own.

- **The WeChat channel could die silently, and one of the three reasons was a wrong error code.** Checked
  against the vendor's own implementation (`third-party/WeKnora/internal/im/wechat/longpoll.go:151`), which
  encodes what this side had been guessing at: **`errcode: -14` is "this token is no longer valid"**, while this
  repository only ever looked at `-1 || 401`. A wrong code does not raise anything - it quietly reclassifies
  "expired" as an ordinary error, so the loop retried every 5 seconds, forever, while the user saw nothing.
  Three things changed, and the third is the one that matters most:

  - `-14` (and HTTP 401) are now recognised, and treated differently from a network blip, because retrying an
    expired token cannot succeed;
  - retries use 1s→30s exponential backoff instead of a fixed 5 seconds, so a long outage no longer floods the
    log - noise is the same as saying nothing;
  - **the failure is now stated where a user will see it**: one unmissable log line naming the remedy, and
    `isChannelActive()` - which the panel shows - reports `false`, because it used to mean "a token was
    configured at startup" and stayed true forever after the token died. The panel now reads "仅本机入口",
    which is the truth, and the local entrance keeps working either way.

  Also: the message callback was wrapped in `try { cb(msg) } catch {}`, so **any exception raised while the
  assistant handled a message vanished** - the user sent something, nothing happened, and the log had no trace
  of either. It is logged now. A fourth item from the same 2026-09-17 comparison turns out **not** to have been
  a defect and is retracted: this side uses a 40 s HTTP timeout for the long poll, and so does the vendor
  (`longPollHTTPTimeout = 40 * time.Second`, in the same file) - the "we might abort on the boundary" worry in
  that note does not hold.

  Five tests were added for the parts that can be tested without a live channel: the classification table, the
  backoff curve, and a driven loop (a stubbed client reporting `-14` once and then a message whose callback
  throws) asserting that the expiry is reported **once** rather than every round, that the callback's exception
  is logged, and that the state recovers after a healthy poll. Four mutations - reverting the error code, the
  backoff, the channel-status semantics, and the swallowing - each turn one red. The live path (a real expired
  token) has not been exercised: it needs the server to say `-14`, which cannot be forced from here.

- **`draft`'s interactive confirmation was broken: answering "yes" ran nothing.** The command built the
  script's arguments *before* asking, so `--yes` was only ever present when it was passed on the command line;
  the interactive answer never became that flag, and the script - which has its own `--yes` gate - refused the
  run. What the user saw was "I confirmed, and it told me I had not". The two ways of saying yes are now one
  variable and the arguments are built after the decision, so the flag cannot be missing on one path while
  present on the other. The interactive path itself has no test (it needs a terminal), which is exactly why the
  fix is structural: the shape that could forget the flag no longer exists.

- **The transcript line was implemented twice, and the two copies had drifted in four ways.** Nothing failed - the two
  paths just fed different text into the same judgement and drafting prompts. The tool path
  (`assistantTools.transcriptLine`, panel and WeChat: mask, then send over `--stdin`) and the CLI path
  (`reply_debt.format_line`, used by `draft_reply.py --talker`, which reads the database itself) rendered the same
  message as `[9/23 12:20] 对方：…` and `[09-23 12:20] 老王：…`, truncated at 160 characters against 120, and marked the
  cut with `…` on one side only. The most dangerous of the four is the `我` marker: the scripts pick the user's tone
  sample by matching `'] 我：'` and decide who sent the last message from it, so one character of drift would disable
  both silently. The shape is now a single contract, enforced by `test/transcript-format-contract.test.ts`, which
  renders the same fixtures - self, other, non-text, over-long, a third person in a group - through both
  implementations and requires them to be equal character for character. Seven mutations, each reverting one
  difference or breaking one side's clip marker or clip length, turn it red.

  Two things surfaced while measuring, and both changed the fix. The clip-length comparison had to take its value from
  the Python constant rather than the TypeScript one - feeding Python the TypeScript number made "both sides changed to
  120" compare equal, so the first version of that test could not see its own subject. And `senderUsername` turned out
  to be **the raw wxid column** (`wcdbCore`'s `sender_username`, `sqlcipherCore`'s `StrTalker`), not a name, so using
  it as a speaker label sent an account identifier to two cloud models while the transcript only needed to tell
  speakers apart. Both implementations now use a resolved display name when there is one and `对方` otherwise, and
  neither falls back to the raw column.

  The same contract also covers **how much conversation each path sends**: 30 messages (`TRANSCRIPT_MESSAGES` in
  TypeScript, `TRANSCRIPT_SIZE` in Python) alongside the 160-character limit per message. Those two numbers agree
  today, but they agreed by coincidence - neither was watched, and either one changing alone would have made the same
  conversation a different input depending on which entrance it came through. Two mutations, one per side, each turn
  the assertion red.

- **`get_messages` labelled speakers with a raw wxid, and that text goes to a cloud model.** The tool's output is a
  transcript the assistant reads, and every line from the other side was tagged with the value of `senderUsername` -
  which is a database column holding the sender's **wxid**, not a name. So asking "what did 老王 send me" produced
  lines tagged with an opaque account identifier: an identifier leaving the machine across a boundary the project
  treats as sensitive, in exchange for information the model cannot use. It is fixed by the same rule the drafting
  transcript now follows - a resolved display name when there is one, `对方` otherwise - and by one shared helper
  (`speakerLabel`), since "who is speaking" is exactly the kind of thing that grows a second implementation. This was
  measured before it was changed, and the measurement is the reason it *could* be changed: across 8 sessions and 179
  messages from other people, `senderDisplay` held a name 174 times, `senderUsername` was guid-shaped 176 times, and
  the two were never the same value - so using the name keeps a group's speakers distinguishable rather than
  collapsing them. The user's own lines still read `用户` there and `我` in the drafting transcript; those are
  different framings on purpose and each is pinned by a test.

- **The assistant was allowed to invent a reason for a tool failure, and did.** When drafting failed on 2026-09-25 the
  tool reported only "the judgement step failed (script exit code 1)" and the assistant answered with "the reason is
  probably that the model was inconsistent about whether to promise something" - a cause it had no evidence for. The
  real cause was a dropped connection while calling the judgement model, which the tool *did* know about; what the
  prompt lacked was a rule. It now has one: when a tool fails (its result starts with a bracket), relay only the reason
  the tool gave, and if it gave none - or only an exit code - say that, plus the next step. Guessing is named
  explicitly, because the user reads the guess as a fact. The assertion reads the system prompt **as actually sent to
  the model**, not the source string.

- **Drafting asked "what is their last message about?" even when the last message was mine.** The judgement questions
  are written around *their* last message, and the drafting prompt is told to reply to what they said - but nothing
  checked who sent the last line. If the user had sent it, the judgement interpreted **the user's own words** as the
  other person's position (intent, need and "what should I do" all answered against the wrong person), and those
  answers then went into the drafting prompt as context. Both prompts now receive the premise when it holds
  ("the last message is mine, they have not replied - do not read my words as their position"), and the premise is
  printed to the user ahead of the candidates, because otherwise the candidates look like answers to a message that
  does not exist. The fact is carried as a structured `isSend` field, never parsed back out of the transcript text -
  in a group chat the other side's label is a person's name, not `对方`. When the caller cannot say who sent it, no
  premise is produced at all: a guessed premise would be worse than none.

- **Nothing kept the tool table and its four consumers in agreement - now something does.** `TOOL_DEFS` (19 tools),
  the `executeTool` switch, the availability rules, the MCP exclusion table and the MCP documentation table were five
  hand-maintained lists with no assertion between them. The way that fails is silent in both directions: declare a tool
  without a `case` and the model sees it, calls it, and gets `(未知工具: x)`; add a `case` without a declaration and it
  is dead code no model can reach. `test/tool-registry.test.ts` now derives the set from the source and compares all
  five - both directions, names unique, every declaration usable, every name in the two lookup tables real, and the
  `docs/MCP.md` table equal to the actually-served surface. It found two live defects on its first run: the availability
  rules and the MCP exclusion table became **declarative one-line entries** (`TOOL_REQUIREMENTS`, `MCP_EXCLUDED`, the
  value being the reason) instead of name checks buried in if-chains, and **`wechat.export_messages` was missing from
  the `docs/MCP.md` table** - a tool that is served but was not documented, i.e. the same direction of drift as the
  duplicate row below. Five mutations (rename in the table, rename a case, a bogus exclusion name, a duplicated doc
  row, a renamed served tool) each turn a specific assertion red.

- **`capabilities --json` was under-reporting which MCP tools need `confirm: true`.** A machine-readable safety
  field that under-reports is the same class of problem as one that lies. `safety.mcpSurface.requiresConfirm`
  listed one tool (`draft_reply`) while **four** handlers carry the identical gate
  (`if (ctx.requiresConfirm && args.confirm !== true)` - `search_chats`, `who_owes_reply`, `draft_reply`,
  `search_semantic`). `docs/MCP.md` had been saying "all four" correctly all along; the two things that disagreed
  were the field and its test, because the assertion had been written by copying the field's value rather than by
  reading the code - so the pair stayed consistent with each other and wrong together. The assertion now **derives**
  the set from the source (every `case` block that contains the gate) and compares it against both `requiresConfirm`
  and `callsCloudModels`, so changing one without the other turns a test red; a mutation that restores the old
  single-name list fails it. The same audit turned up a **duplicate row** in the `docs/MCP.md` tool table:
  `wechat.who_owes_reply` appeared twice, and the stale copy was the one that did not say chat text leaves the
  machine. Deleted.

- **Clicking the ball did nothing.** The ball carried `-webkit-app-region: drag` so it could be dragged -
  and on Windows a drag region **swallows mouse events**, so the page never received the click. Dragging is
  now implemented in the page itself (pointer events, with a 4-pixel threshold that separates a click from
  a drag) and the native drag region is gone. The verification is the part worth recording: this had been
  "verified" earlier by calling `element.click()` through the DevTools protocol, which **bypasses real
  input** and happily reported a ball that could not be clicked. Both the new click and the drag are now
  confirmed with synthetic-but-real mouse input (`SetCursorPos` + `mouse_event`): 78x76 -> 421x560 -> 77x76,
  and a drag that moves the window without changing its size.

- **Dragging made the ball grow.** `win.setPosition` / `win.setBounds` on this window (frameless,
  transparent, non-resizable) operate on the **outer** rect and drift a little on every call - measured at
  roughly +0.8px per call, with no bound: 20 moves took 76x76 to 97x92, and the same happens without any
  mouse involved (calling the drag IPC directly reproduces it). `setContentBounds` (the client area) is
  stable - 20 moves, size unchanged. **That measurement was narrower than the sentence:** it was taken on the
  non-resizable ball window, and the claim does not survive on a *resizable* one if the size is re-read from
  `getContentBounds()` on every move - see the drag fix below. The same drift was quietly affecting `setMode` too: expanding measured
  421x561 rather than the requested 420x560.

 Every "atomic write" in this project (the memory file,
  the configuration, the panel's endpoint file) wrote a `.tmp` and renamed it over the target - and on
  Windows `rename` fails with `EPERM` when another handle has the target open, which antivirus and search
  indexers do briefly and routinely. The callers all record a reason and carry on, so the visible effect
  was nothing at all: one full-suite run here saved the memory file and the file came back without its
  `version` field, with the test green on the other three runs. The write now retries a busy target and
  then falls back to writing in place (D-046) - the bytes are the same either way, so only the atomicity of
  that single write is given up, and the test that reproduces it is in the suite.

- **"No pending todos" and "todos were never extracted" were the same sentence.** Todo extraction reads
  chat logs, so it runs only when the user invokes `weflow-cli todos extract --days N --yes` - it is a
  confirmed action, and nothing schedules it. On a machine where that has never been run, the todo file
  does not exist, `list` returns an empty array, and both the assistant tool and the terminal printed
  "nothing to do". Those are different claims: one is about the user's workload, the other about whether
  the question was ever asked. This was not hypothetical - on this machine `~/.weflow-cli/todos.json`
  does not exist, so `get_todos` was answering `(没有待办任务)` to every question about pending work.
  The script now reports whether the file exists (`list --json --meta` gives `{items, extracted,
  count}`), the assistant names the missing step and the command that fixes it, and `todos list` /
  `todos remind` say it too instead of congratulating an empty list.

  The bare-array shape of `list --json` is deliberately unchanged - it is a published capability - so
  the new signal rides on a flag rather than a changed contract; the tool also falls back to the old
  wording if it gets an array. `mcp_bridge.py` had always made this distinction; the assistant tool was
  the one reader that dropped it.

- **The chat export tool reported a directory it may not have written to.** `export_chat` builds the
  destination as `output/exports/<name>-<timestamp>`, and on a name collision appends `-2`. The reply
  to the user was assembled from the *pre-collision* name, so the second export in the same second told
  them to look in `<name>-<timestamp>` while the files were in `...-2`. A write tool that names the wrong
  location is worse than one that names none: the user goes looking and finds nothing. The message is now
  built from the directory that was actually written; with the default root it stays a repository-relative
  path (`output/exports/...`, so it is followable), and with an overridden root it is the bare directory
  name - an absolute local path has no reason to enter the conversation.

  Found by pointing the tool at a real database with the export root redirected to a temporary directory.
  The test that should have caught it had pinned the bug: it redirected the root *and* asserted the
  message contained `output/exports/`, so it was written to match the code rather than the intent. It is
  now two cases - overridden root reports the bare name, default root reports the relative path - and
  both assert no drive letter appears.

- **A failed memory save was silent.** `AssistantMemory.save()` ended in `catch { /* persistence failure
  must not break the conversation */ }` - the right *behaviour* (a disk hiccup should not drop the
  reply) with the wrong *silence*: the user says "remember this", the write fails, the memory is gone,
  and nothing anywhere records it. Found by accident: one flaky full-suite run failed two "memory
  survives a restart" tests and no line anywhere pointed at why. A failed save now records the error
  code in the audit (`MEMORY_SAVE_FAILED code=…`, never the message, which can hold a path), exposes
  the reason as `memory.lastSaveError`, and the in-chat `记忆` command prints it. That last one matters
  most: a user asking what the assistant remembers must not be shown an account of a memory that never
  reached the disk. The in-memory state was never at risk - a failed save leaves the dirty set intact,
  so the next save retries.

- The test for it needed its own file, and its first two versions were wrong in ways worth recording.
  The failure has to be manufactured **before** `assistantMemory` is imported (module-level paths are
  computed at import), so it cannot live beside the other memory tests. And the first fixture put a
  directory where the memory file goes, which does not work: `load()` reads a directory, throws
  `EISDIR` and takes its **quarantine** path, renaming the directory away - so by the time `save()` runs
  the name is free and the save succeeds. The failure being tested for did not exist, and the test
  reported "the reason was not recorded". The working fixture occupies the `.tmp` name instead, which
  fails the write while leaving the audit file writable.


- **The labelling sheet could not actually be labelled.** `sample` printed the first 400 characters of
  the article body, and that region is boilerplate: `# title`, `> source / > time / > 阅读原文`, then the
  scraped copy of the article, which repeats the title and carries the cleanup leftovers. On the sample
  that produced it, most rows showed a title and a source name and nothing else - found by actually
  labelling a batch, where the only thing left to judge from was the title. The excerpt now takes the
  **`## AI 摘要` section**, the two or three sentences the judgement is really about; without a summary it
  falls back to the body after `## 正文`, then to the first non-boilerplate paragraph. Cleanup leftovers
  are matched by their invariant fragments (`在小说阅读器`, `沉浸阅读`) rather than by whole sentences,
  because the wording varies between articles.

- **`--seed` did not actually reproduce a sample**, despite the help text promising "两次抽样结果一致".
  Two causes, found one after the other: the pool was drawn in **concurrent completion order**
  (`as_completed`), and `rng.shuffle` consumes that order, so the same seed produced a different 50 (7 of
  50 rows differed between two consecutive runs); and even with the order fixed, **the scores come from a
  live model**, so an article scoring 0.49 in one run and 0.52 in the next changes band and therefore
  changes the draw. The pool is now sorted before grouping and scores are **cached by article path**
  (`~/.weflow-cli/labels/.jev-scores.json`), so two runs of the same command produce the same 50 in the
  same order - verified, and pinned by a test that feeds the same items in reverse order. The cache also
  means a re-run costs no quota; `--refresh` bypasses it when the model or the criteria change.


- **Quoted messages no longer lose what they were quoting.** In the appmsg payload the *reply* sits in
  `title` and the *quoted original* in `refermsg/content`, and only the first was read. Measured on 37
  real quote messages in one archive: the quoted original is a median of 36 characters (longest 12733 -
  a whole article was pasted in), so the model was routinely shown a line like "是呀，够得意个" with no
  way to know what it was replying to. Both parts are carried now, separated by ` ｜ 引：`. Each is
  clipped with a trailing `…` (reply 60, quoted text 120) because an unmarked cut reads as a complete
  sentence - the longest quoted text would otherwise have looked like it simply ended. Entities are
  decoded for display (`a&amp;b` reads as `a&b`), which the WeChat 3.x reader already did and the 4.x
  one did not.

- **The same fix reached the WeChat 3.x reader, which had no test at all.** `sqlcipherCore`'s AppMsg
  formatting was the second implementation of this display form; it is now `core/appMsgFormat.ts` as
  pure functions with 13 tests. Extracting it immediately paid for itself: reading `<type>` from the
  whole document could pick up a quoted message's `<type>` instead of the outer message's, and the
  entity-decoding the original did was very nearly dropped in the move (the tests caught both). The two
  implementations now share one set of clip lengths and one separator, so the same message reads the
  same on either WeChat version - they are still two implementations, and the tests pin the values they
  must agree on.

- **One assistant message body was cut at 80 characters.** A quote (`[引用] reply ｜ 引：quoted`) had
  its quoted text reduced to seven or eight characters - carried, but useless - and any long message
  was cut mid-sentence with nothing to show it had been cut. The limit is now 160, with a trailing `…`.
  Worst case stays bounded: 50 messages × ~180 characters ≈ 9k.

- **The Vault's copy of an article now has the WeChat interface phrases stripped; the file it was copied
  from does not.** `Sources/WeChat/` is the layer a person reads and searches, and "继续滑动看下一个",
  "轻触阅读原文" and the `在小说阅读器中沉浸阅读` family (eight phrases) are page furniture rather than
  article text - searching the vault used to surface them. `output/biz-daily/` keeps the fetched text
  byte-for-byte, because it is the only copy of the bodies and a cleaner with a bug there would destroy
  material that cannot be re-derived. Measured after the pass: 15,731 of 35,463 files carried a phrase
  before, **0 after**.

  **The cleaning lives inside `copy_to_vault`, and that placement is the fix.** It is not a one-off: this
  step re-runs, so a cleaner sitting anywhere else gets undone by the next sync - which is exactly what
  happened during development, when a plain `--vault-sync` restored the residue on 15,731 files because an
  earlier pass had cleaned the Vault copies while the copy function was still a dumb `write_bytes`.

  Two things are deliberately untouched. **The frontmatter**, because these are short words: `去阅读`
  occurs inside `如何去阅读一本书`, and the title is the only alignment key between a reading note and its
  source - measured, 0 of 35,060 files currently need it, so the guard is for the future and is written
  down as such rather than dressed up as a fix. **Fenced code blocks**, because `strip_wx_ads` also
  normalises whitespace, which is harmless for the summaries it was written for and eats indentation in
  code: 20 files carry fences and **10 of them would have been altered**.

  **What it actually deletes, measured instead of assumed.** It is `strip_wx_ads`, the same function the
  notes already use, so it does more than the eight phrases: `______` runs, `javascript:void(0)` links,
  the `原创 <账号名>` byline that WeChat pastes several times over, and repetitions of the same token.
  The byline rule alone fires on **9,334 of 35,463 files**. It never rewords prose - sampled 300 articles
  and classified every diff - but this *is* text removal, and an earlier draft of this entry claimed the
  pass only touched phrases and whitespace, which measurement did not support. The byline is page
  furniture and the account name survives in `source:` in the frontmatter, so the trade is defensible;
  the original is in `output/biz-daily/` either way.

- **`--vault-sync` had been deciding which days to copy by a marker only one writer sets.** The gate was
  `backfilled` in `.articles.json`, but that key is written by the backfill path only - so the 12 days
  produced by the `daily` path were never copied **and were never reported as skipped**, including two days
  (08-24, 08-25) with no directory in the Vault at all. The gate is now "the day has at least one `.md`
  besides `README.md`", which is what the flag always meant: 187 → 199 days, 33,232 → 35,463 files. The
  1,755 files still carrying residue after the first cleaning pass were exactly this population. The
  reading notes were never affected - every one of them points at `output/biz-daily/...` through
  `local_source`, not at the Vault copy (verified across five months, 1,144 of 1,144), so `wiki lint`
  reports the same result before and after.

- **`wiki lint`'s "similar titles" report now says out loud that its own threshold has stopped
  discriminating.** The rule (a common substring of ≥5 characters covering ≥40% of the shorter title) was
  set when there were 5,062 pages with long Chinese news headlines. At 22,374 pages full of short names it
  fires on coincidences - `AI Agent开发框架` against `生物信息学LLM Agent综述` - and reports **424,697**
  groups. A 20-pair sample confirms the rule itself holds, so this is the threshold and not the
  implementation; the printed line now carries that caveat rather than letting the number read as a
  finding. The same investigation turned up a second, older problem: the rule **cannot catch the case it
  was written for** - `打虎！陈勇被查` and `中建集团副总经理陈勇被查` are the same event with different
  headlines, and their longest common substring is 4 characters, so only 2 of the 4 motivating titles ever
  matched. The misleading claim in the docstring is gone; tightening the threshold is left as a decision
  because every variant trades one kind of miss for another.


## 1.7.0

### Added

- Article bodies are now cached as they are fetched, so a daily run can resume. The run used to be **all-or-nothing**: it fetches every article before writing anything, and the bodies only ever lived in memory, so any interruption discarded the whole day. On a 400-article day that is over an hour of fetching, and on 2026-09-22 it stopped at 105/403 having written nothing. Bodies are now stored under `output/.cache/fetch/<md5(url)>.md`: a re-run fetches only what is missing (a resumed article costs ~0s instead of ~20s, and it does **not** take the 8-12 s throttle sleep, because a cache hit sends no request), and switching between `--no-summary` and normal, or changing the classifier, no longer re-downloads the day. Only successes are cached - a failure would otherwise be remembered forever instead of retried. `--no-fetch-cache` forces a refetch.

- Topic exclusion: `dailyExcludeTopics` (comma-separated, e.g. `新闻,投资,学术`), settable with
  `weflow-cli config set dailyExcludeTopics "..."`, plus `--exclude-topics` on
  `generate_ai_report.py` / `generate_html.py` for a one-off override. This is a **display**
  switch: bodies are fetched and archived as usual, and the topic simply does not appear in the
  two views. Pre-fetch filtering was measured first and rejected - judging the type from a source
  name plus a title and digest agreed with the source-level configuration only 60% of the time on
  217 articles, with 48 false positives and no gold standard - and a skipped fetch is
  **irreversible**, since nothing gets archived. Excluding at the display layer also means
  changing your mind costs a regeneration, not a refetch. The exclusion is orthogonal to
  `--include-all` and to the inclusion score, it is applied identically by the report and the
  reader page, and all three lists in the report's **我拿不准的** section are filtered, so a
  report that excludes 新闻 cannot turn around and list 新闻 in its own uncertainty section.
  Excluding the focus topic is refused with a warning rather than obeyed: an empty subject makes
  the report either bodyless or exit with "未找到文章", which blames the wrong thing. A misspelled
  topic name is ignored **with a warning** - a silent no-op would look like the filter working.

- Source-level prior: `~/.weflow-cli/source_topics.json` accumulates how many articles each
  公众号 has been judged to publish per topic, from what the daily actually archived. A source
  with enough samples (`SOURCE_PRIOR_MIN_SAMPLES`) dominated by one topic (`SOURCE_PRIOR_SHARE`)
  is reported at the end of the run, and when that topic is one you exclude the run names it:
  `来源先验: 甲号 → 新闻（20 篇里 19 篇 = 95%）`. It **only reports - nothing is skipped**,
  because a pre-fetch skip is irreversible and this table is still growing. The table holds
  account names, so it lives outside the repository; counts are append-only and only articles
  that landed on disk are recorded, so the table and `output/` describe the same corpus.
  `stable_source_topic` returns `None` for "not known yet", and callers must not read that as a
  default topic. See D-037 and D-038.

- `test/config-keys.test.ts`: every key in `bin`'s `configurableKeys` must also be declared in
  the `CliConfig` interface, given a default, reset by `clear()` and read back by `load()`.
  These are the four ways a key can be accepted by `config set` and then quietly not survive a
  restart.

- `daily --no-summary`: judge without generating. Topic, relevance and the inclusion decision still come from the decision model, but **no LLM call is made at all** - no summaries, no tags, no concepts, no README briefing - so this run needs no DeepSeek key. Articles are still fetched and archived (the body is kept); the md simply carries no `## AI 摘要` section, and `.articles.json` records `summary: ""` rather than quietly substituting the platform's digest, because an empty heading reads as a failed generation and a digest reads as our summary - both claim something that is not there. Verified by running a 4-article day with a **deliberately invalid** DeepSeek key: it completes normally with no 401, i.e. nothing reached the LLM. Wired through `weflow-cli daily --no-summary` and `pipeline.py`; the downstream pipeline steps (action suggestions, wiki compile, AI report) still use an LLM, so a fully LLM-free run adds their `--skip-*` flags, and the pipeline says so when it notices.

- `sync run|status|verify`: a local message-sync checkpoint. `sync run` reads a time window, deduplicates against the previous checkpoint and records what it covered; `sync status` reports coverage without touching the database, so it still works while the database is locked. It does **not** advertise a stable cursor - overlapping windows plus local deduplication is what it offers, per D-027.
- Per-shard read reporting. Shard open and read failures used to be swallowed by a bare `except: continue`, so "read 31 messages" and "read one shard and silently skipped three" were indistinguishable from the outside. `--report-shards` (opt-in; without it the JSON is byte-identical) surfaces `scanned/opened/failed` and one entry per shard. On the machine this was developed against the report reads `message_0.db` 31 rows and `message_3.db` 1258 - exactly the shape of the shard-read bug fixed in 1.6.4, which was completely invisible at the time.
- A media coverage report on HTML export: `<prefix>_media.json` beside the parts, with a status and a reason for every media item (`embedded | cached | remote-fetched | missing | unsupported`). The exporter had been computing `COVER_STATE` counters and discarding them, and a media miss showed up only as a bare `[图片]` with no reason recorded. A real 120-message export reports 54 items: 35 embedded, 15 missing, 2 unsupported, 2 remote-fetched, with reasons `not-in-local-cache` and `voice-not-in-media-index`.
- `docs/SYNC_CONTRACT.md` freezing the `weflow-sync/v1` and `weflow-job/v1` state schemas, and `D-029` recording the additive-only boundary, why the identity omits `shard`, and why `sync retry` is deliberately not implemented.
- `python scripts/nt_decrypt.py verify-native`: check a passphrase against an encrypted database using only the standard library (PBKDF2-HMAC-SHA512 over the page-1 salt, then the SQLCipher page MAC). "Is this key right?" can now be answered when `sqlcipher3` is missing or built against a different SQLCipher - previously that question and a broken driver looked the same from the outside. It answers yes / no / cannot-tell, and does not raise on unusable input. The synthetic test fixture was also made faithful while adding this: it had been encrypting every shard with one fixed salt and the passphrase as a raw key, so it could not exercise per-shard key derivation at all.
- Article topic and relevance in the daily report are now decided by TypeSafe's Jev decision model (`scripts/jev_client.py`) instead of being parsed out of generated text. One request asks a 6-way `choice` for the topic and a 3-level `score` for relevance, and returns probabilities rather than a string with `【主题】` in it. The LLM still writes the summary, tags and concepts - Jev cannot generate text. `weflow-cli config set typesafeApiKey "..."` enables it; without a key the previous path runs unchanged, and `--classifier llm` forces it. See D-031.
- Two additive frontmatter keys on each article: `relevanceScore` (the raw score) and `topicConfidence`. The three-level cut points are provisional and uncalibrated, so the raw value is kept rather than discarded at the write step.
- The daily report ends with **我拿不准的**: the entries whose probabilities sit near the threshold, split into those admitted and those excluded, plus any article whose topic confidence is low enough that it may be in the wrong section. It is computed **locally, from frontmatter** - no extra model call - because asking a model to describe its own uncertainty means asking it to generate more prose. When the articles predate the probability fields it says so outright, rather than printing nothing and letting the absence read as confidence. See D-033.
- The daily report now asks whether an article belongs in the report, instead of inferring it. `worth_including` is a yes/no question riding along in the same decision request (measured: 12 questions cost 0.91s against 0.84s for 2, since `state` dominates the token count), and it is stored as `includeScore`. Observed to diverge usefully from `relevance`: an award announcement scored 0.79 for relevance but 0.03 for inclusion.

- `scripts/route_cards.py`: search your own conversations by describing what you are looking for. The decision model picks the real query words out of the n-gram fragments the question produces (measured: keeps 会议 at 0.77, rejects seven fragments at 0.11-0.40), and **ranking is local** - by how many messages in each session literally contain those words - because ranking sessions with the model was measured and does not work (all candidates scored 0.50-0.51 as yes/no and 1.74-1.76 on a 0/1/2 scale, regardless of 247 versus 8 actual hits). Retrieval reads WeChat's own `message_fts.db`, whose text is plaintext and whose integer `session_id` is the rowid of the same database's `name2id` table, so it needs no index of its own. Only the candidate words and your question leave the machine; `--keyword` keeps the whole path local, and message text never leaves. See D-036.

- Decision-model calls now record **which model actually served them**, and validate the `choice` contract on the way in. The API returns a `model` field naming the served version (`jev-1.13.0`) as distinct from the requested alias (`jev-latest`), and the daily stores it as `decisionModel` in `.articles.json` - so "production is no longer running what was measured" becomes visible instead of producing results that merely look fine. Every `choice` answer is checked before use: probabilities present, key set equal to the criteria, values in `[0,1]`, sum ≈ 1, and `choice` equal to the argmax; a non-argmax choice is not a wrong-looking answer but a normal-looking wrong one. `instructions` is also confirmed to accept a structured object (`{"goal": …, "rules": […]}`), though the daily's prompts deliberately stay as strings - changing them would move behaviour the cut points were calibrated against.

- When the decision model has judged an article, the LLM prompt no longer asks it to classify too. It used to ask for `【主题】`/`【相关度】` whoever was judging, so on the Jev path those answers were written and thrown away - the fields plus the six-category criteria and the three-level definitions came to 446 characters of prompt per article (57% of the prompt skeleton; about 9% of the total input, since the article body dominates), plus 10-15 output tokens. The **summary, tag and concept requirements are byte-identical between the two prompts** (a test asserts it), so what gets generated does not change; `--classifier llm` and any article whose Jev call failed still get the full prompt, keeping the fallback path byte-identical as D-031 requires. The choice is per article, not per batch - a failed judgement needs the LLM's own topic and relevance. Token savings are estimated from prompt size and output fields, not read from a bill: neither this client nor the decision-model client exposes usage amounts.

- The assistant's two untested core files now have coverage, via a synthetic harness rather than a
  live channel. `assistantMemory` gets 17 tests (working window, compression past the cap, the
  degraded path when the LLM refuses to compress, fact extraction cadence and noisy-output parsing,
  the fact cap, persistence across a restart) and `assistantService.handleMessage` gets 14 (built-in
  commands without any model call, the ReAct loop feeding a real tool result back, tool-result
  redaction, the unknown-tool and malformed-arguments paths, the 6-round cap, an LLM failure that
  must say so, audit lines, and memory wiring), plus 8 for the daemon start path. No network, no
  WeChat channel, no real home directory: `callLLM` is injected, the tools used are the two that
  only touch memory (`search_memory` / `save_memory`), and `HOME` is pointed at a temporary
  directory before the modules are imported. Two invariants the harness exists to hold: a tool
  result is redacted **before** it can leave the machine, and the audit log never contains message
  text. One sharp edge was found and **documented instead of changed**: fact de-duplication uses
  mutual containment, so an existing `事实 1` silently blocks a new `事实 10`.

- 11 of the assistant's 12 tool branches are now executed by tests, through stubs on the exported
  `chatService` / `wereadService` singletons and a replaced `fetch` - so no database, no network
  and no real home directory. Before this, no test had ever run a tool branch: only three pure
  helpers were covered, and the rest of the surface was unverified. The branches now pinned
  include: the strict-mode body mask applied **inside** `get_messages` (so third-party chat text
  cannot appear in a tool result), `read_favorite` refusing an unsafe link **without issuing any
  request at all**, its single retry for WeChat's WAF challenge page and the `content_noencode`
  fallback, the "deleted by the publisher" case, out-of-range tool arguments becoming a readable
  parameter error, ambiguous contact names asking for a more precise one (and an exact name
  winning over a partial match), and a tool that throws internally returning a readable failure
  instead of rethrowing into the ReAct loop. **Not covered, deliberately**: `get_todos` spawns a
  Python subprocess against the real database and has no cheap stub point.

- The assistant's resident loop is now driven by tests too - who gets answered and who is denied,
  without a WeChat channel: `WechatMessageService.prototype` is replaced, the token comes from a
  stubbed `configService.get`, and the queue is drained by awaiting it. Pinned: an empty allowlist
  denies everyone with an audit line and **no model call**; a non-text message is ignored without
  spending quota; an exhausted quota replies "额度已用完" and does not call the model; the quota
  resets across a date change; two messages are processed strictly in arrival order (the serial
  queue is what keeps the memory window from interleaving); a group needs all three controls
  (group allowlist, sender allowlist, @ mention) and is denied with a distinct reason for each
  missing one; and one message that throws does not take the loop down with it.

- The privacy wiring - which config value decides "this data does not leave the machine" - is now
  tested. Existing privacy tests called `redactText(text, mode, localInference)` with explicit
  arguments, so the function was covered while the thing that *derives* that boolean in
  production was not: `PrivacyGate.isLocalInference()` reads `aiEngine`, and `engineConfig()`
  decides where the request is actually sent. Pinned: only `ollama`/`lmstudio`/`local` count as
  local inference and skip redaction; a custom `aiBaseUrl` is still cloud, so swapping in a relay
  does not quietly stop PII masking; local engines point at `localhost` with `key: null` and a
  trailing slash on a custom base URL is stripped; and `reviewEvidence` refuses to put chat text
  on the wire without an explicit `--allow-cloud` (**without issuing any request**), redacts the
  transcript when it does, and keeps it intact under local inference.

- The assistant's single-round fast path (D-035) is implemented and **off by default**. It asks the local
  decision layer one question - "must this be answered from local data, and if so which of these nine
  capabilities" - then pre-dispatches that one tool, so the ReAct loop's first request already sees the
  result: two model round trips become one. Only tools with closed or empty arguments are routable
  (`list_sessions`, `get_stats`, `get_daily_report`, `get_sns`/`get_weread`/`get_todos` modes); a question
  like "summarise my chat with X" needs a free-text argument a closed-set question cannot produce, so it
  falls back - passing the raw message as the argument would manufacture exactly the failure this
  decision was written to avoid (a wrong tool, and a confident answer built on an irrelevant result).
  `config set assistantFastRoute log` records "would have routed to X" and changes nothing; `on` enables
  it. Every uncertain exit - judge unavailable, non-numeric probability (`Number(true)` is 1, so a boolean
  `noul` would have routed *certainly*), low confidence, unknown capability - falls back to the previous
  behaviour rather than to a weaker new one, and a test compares the fallback transcript field by field
  against the baseline. The dispatch-and-audit step is now one shared implementation, since "no tool
  dispatched without the audit line" is the property that must not regress. Measured on 17 questions
  against the real decision layer (`scripts/assistant_route_probe.ts`): 0 wrong routes, 10 routed
  concretely, ~1.33 s per decision, 2 fell back - with the probe's own expectations, not human labels.

- First-run setup for the assistant now says what to do and, more importantly, **which ID to allowlist is
  not guessed**. The assistant denies every sender until `assistantWhitelist` is set, so a fresh
  `login-wechat` used to end in silence: talk to your own bot, get nothing back, and go digging through
  logs for the reason. Two sources of that ID exist and only one is verified - the login response carries
  an `ilink_user_id` while the allowlist matches the inbound `from_user_id` (documented as an
  `@im.wechat` ID), and nothing in this repo connects the two, so the login path does not write the
  allowlist. Instead, while the allowlist is empty the daemon prints the **first denied direct message's**
  full sender ID together with the exact `config set` line to run, once, and never for a group (a group's
  sender is a member, not the person to allowlist). Auto-configuring an allowlist from an unverified
  identifier would have failed in the worst way available here - non-empty, so it looks configured, and
  still denying you, with the one-time hint disabled because the list is no longer empty.
  `assistant log --json` still returns metadata only, never log content.

- The assistant now names **all three** ways out when the strict privacy mode hides chat bodies. It had
  said only "switch strict mode off", which omits the option that actually matches a privacy worry: a
  local engine (`aiEngine=ollama`/`lmstudio`) leaves the machine out of it entirely, and strict-mode
  masking is skipped there by design because nothing is sent anywhere. The system prompt now requires
  it to list balanced (bodies leave, PII masked), a local engine (nothing leaves), or staying as is -
  rather than presenting one of them as the only choice.

- A `隐私` built-in command in the assistant: send it and the reply lists the current privacy mode, what the
  tools actually receive (masked, original, or not sent at all), and the exact `config set` lines to
  change it - including the local-engine option when inference is in the cloud. It is **read-only on
  purpose**: a chat message must not be able to weaken a privacy setting, so the mode is changed on the
  machine, not from inside the conversation.

- The assistant claimed it had called a tool and been blocked by strict mode, **without calling any tool at
  all** - the audit line read `TURN_DONE 264B tools=0`. Two things changed: the privacy state is now written
  into the system prompt as a **fact about the current mode** instead of a conditional ("if strict mode hides
  bodies, then ..."), and a rule says not to conclude anything about tool output before actually calling the
  tool. **The first explanation for it was wrong, and measurement said so**: this release's notes originally
  attributed the missing call to that conditional. A probe of the same question against the real model - four
  prompt variants (current, hypothesis-sentence only, with the model's own previous "blocked" answer seeded
  into its window, and the complete pre-change prompt) x 8 runs = **32 calls - called the tool 32 times out of
  32**. The prompt was therefore not the cause, and the cause of that single occurrence is unknown (sampling
  variance is the likeliest). The changes stay, because stating the current mode as a fact is right on its own
  terms, but they are **not** presented as a proven fix. What actually caught this was the audit line: a
  per-turn `tools=N` count turned "the assistant says it looked" into "the assistant did not look".
- A **tool-use guard** in the assistant: if the router judged that a message needs local data and the turn
  then produced **no tool call at all**, the model is pushed back once - "you hold no tool result; call a
  tool or say which one you called and what it returned" - and the loop runs again. It exists because two
  live answers said "I did look it up, the content was masked" while the audit showed `tools=0`; 40 probe
  calls against the real model could not reproduce it, so this is a code-level **contradiction check**
  rather than a theory about the cause. The trigger is the routing decision, so: it never fires in `off`
  (no signal), and it **does** fire in `log` - deliberately, because `log` promises that *routing*
  changes nothing, while this is a safety behaviour, and the observation period is exactly when a
  fabricated "I looked" is most likely to be noticed. It fires at most once per turn, only on the
  contradiction, and a failed push-back keeps the reply it already had. `TOOL_GUARD_PUSHBACK` in the
  audit is the line to grep; the loop is now one shared implementation for both passes.

- **Non-text messages are no longer dropped when reading a conversation.** `parsedContent` was filled only
  for `local_type == 1`, and a non-text body is a zstd-compressed BLOB that "is not a str, so it becomes
  an empty string" - so images, stickers, files, quotes, transfers, red packets and **revoke notices**
  all arrived downstream as empty. In a real 44-message conversation the three "empty" messages turned out
  to be a revoke notice and two stickers, and the assistant answered, truthfully, that it could not read
  the content. They now get a display form: the type code is derived (`local_type = apptype * 2**32 + 49`,
  49 being the appmsg family) rather than tabulated, the XML is decompressed when it is zstd, and the
  useful part is carried through - `[文件] Base.csv`, `[引用] <quoted text>`, `"某人" 撤回了一条消息`,
  `[转账] 微信转账`, `[表情]`. An unknown app type says `[应用消息]` rather than guessing "link", and an
  unknown type says `[未识别的消息类型 N]`: **a non-text message never becomes an empty string again**, and
  a test asserts that over a list of types. The assistant tool now prefers `parsedContent` for non-text
  messages, so raw XML (md5, cdn urls) no longer goes into the model's context. Also fixed a latent wrong
  value: `getMediaStream` labelled **everything** that was not a video as `mediaType: 'image'`, text
  included (it has no callers today, but a wrong value in a public method gets believed eventually).

- The assistant's memory file now carries a **format version** (`version: 1`, conversations under a
  `users` key). A file without a version is the previous shape and is migrated; any other version is
  **refused** - renamed to `assistant_memory.json.unreadable-<timestamp>` and announced in the log and the
  audit - rather than parsed by guesswork. A file that cannot be parsed at all takes the same path, because
  it may be the only copy. The criterion for what counts as a structural change (and therefore a bump) is
  written down next to the constant: renaming/removing a field, changing its meaning or units, or changing
  the key space; **adding an optional field does not**, and a test pins that unknown fields in a
  same-version file are ignored.
- Long-term facts gained **provenance and usage**: each fact records the user turn it was extracted from
  and the sentence that triggered it (`sourceTurn`, `sourceQuote`), and the time it was last retrieved
  (`usedAt`). A fact can now be checked against what the user actually said, and stale facts are
  distinguishable from live ones.
- Fact de-duplication no longer loses the more specific version. It used to treat mutual containment as a
  duplicate, so `项目叫 weflow-cli` blocked `项目叫 weflow-cli 并开源` - the more precise statement could never
  be stored. Now the longer, more specific fact replaces the general one (normalised comparison, with a
  length-ratio guard so that two facts merely sharing a short fragment stay separate). The remaining
  ambiguity is documented in a test: two facts that are prefixes of each other resolve in favour of the
  longer.
- The working window compresses on **two gates with different retention rules**, because a fixed turn count
  is window-independent and goes wrong as soon as the model changes. The turn gate (many short turns) keeps
  about half; the budget gate (a few long turns) keeps what fits 16% of the input budget, and the character
  bound wins over the turn floor - six 6,000-character turns are 36,000 characters, and no turn floor
  justifies exceeding the budget. Both rules are clamped to the window length, which fixed a real bug: a
  computed retain of 6 in a 5-turn window turned into `slice(-1)`, i.e. keeping exactly one turn.
- The rolling summary is now a **fixed eight-section skeleton** (用户诉求 / 技术要点 / 涉及的文件与命令 /
  错误与修复 / 待办 / 当前进展 / 下一步 / 关键上下文) with an explicit merge law: keep what is still true, drop
  what is stale, produce one summary, never copy the previous one verbatim. An empty section writes
  `(none)`; a section is never dropped. Free-form summaries were where compression quietly lost information.
  The extraction prompt also now asks only for what **the user stated**, not for the assistant's guesses.
- A memory file that could not be loaded is announced at startup (`⚠ 记忆: …` plus a `MEMORY_LOAD_ISSUE`
  audit line) instead of silently presenting an empty memory, which reads as "it forgot me".

- Facts are now **selected for injection by relevance** instead of all being sent every turn. Thirty facts at
  ~72 characters each is about 2.1 KB per request, and the ones unrelated to the question are noise - which
  costs more than money. Selection ranks by direct containment first, then character-bigram overlap, then
  recent use (`usedAt`) and recency; the selected facts keep their chronological order so the block still
  reads as a list. The prompt now **says how many were left out** ("另有 N 条与这次问题关系较远，未列出"):
  claiming "here is everything I remember" while sending a subset is worse than sending less, because the
  model then believes it has seen it all.
- Local data entering the system prompt (the rolling summary and the memory facts) is now wrapped in a
  `<weflow-local-data source="…">` frame, with **any occurrence of the frame tags inside the content
  neutralised**. The threat is frame spoofing: chat text, article text and a stored fact can all contain
  `</weflow-local-data>` followed by something that reads like a system instruction, and without escaping
  the data could close the frame and speak as the system. This does not make the model immune to
  instructions hidden in data - it only removes the ability to **close our frame**, which is the part we
  can guarantee. The idea came from the same `deepseek-harness` audit: it treats referenced session content
  as untrusted and keeps its injected instructions tag-safe for exactly this reason.

- Fact selection now has a **relevance floor**, applied **before** the budget cut. Measuring the earlier
  version showed the flaw: the budget is counted in characters, thirty short facts came to about 1,080
  characters, everything fitted, and "select by relevance" had degraded into sorting - i.e. full injection
  again (2,483 bytes of facts per request). With the floor, an unrelated question injects eight recent
  facts as a fallback (791 bytes) and a question matching one fact injects 390 bytes; the "另有 N 条" note
  appears in both cases. A relevance-only fallback is intentional: memory itself is context, so injecting
  nothing at all is worse than injecting the most recent few.

- Two more assistant tools, and the Python-calling code is now one implementation instead of two.
- `search_chats`: "where did we talk about X" across every conversation. It runs `scripts/route_cards.py`
  (the decision model picks the query words, ranking is local against the message full-text index) and
  reuses that script's own egress gate; only the question and the candidate words leave the machine, never
  chat bodies - and the excerpts it does return go through `privacyGate.maskMessageBody`, the same rule
  `get_messages` follows, so strict mode masks them here too.
- `who_owes_reply`: who is waiting on you, from `scripts/reply_debt.py --json`. It reports who/ how long /
  how likely and deliberately **not** what they wrote; ask for a specific person with `get_messages`, which
  does the masking properly.
- `route_cards.py` gained a `--json` output for this (the JSON is the last line, since the human-readable
  progress lines still go first) with a test asserting that shape and that `--keyword` stays offline.
- New `src/services/pythonBridge.ts`: the single way to call an in-repo Python script and get JSON back.
  There were two implementations before (`get_todos` via `execFile` with argv, the router via `spawn` with
  stdin), each with its own timeout and error handling - exactly the duplication this repo keeps finding.
  The bridge classifies failures (timeout / non-zero exit / no JSON / the script reporting `success:false`)
  instead of collapsing them into "execution failed", does not throw at callers, and is injectable so the
  tool branches can be tested without spawning a process - which is what finally makes `get_todos`
  testable, the one tool that had been left uncovered for that reason.

- Two more assistant tools, taking the set to 16.
- `search_semantic`: meaning-based search for when literal words miss (`search_chats` matches literal strings
  only). The query travels in `WEFLOW_SEARCH_QUERY` rather than argv, following the rule this repo already
  tests for other readers - user text must not show up in a process list. Being explicit about the egress:
  the query is embedded by Aliyun (百炼 text-embedding-v4) and the candidate snippets are reranked by the
  decision model, both existing cloud paths in this repo, and it needs an index built first
  (`weflow-cli search-index`).
- `export_chat`: write a conversation out as HTML with images. It is the first **write** tool the assistant
  has, so its boundary is fixed in code rather than left to the prompt: it only ever creates a **new**
  directory under `output/exports/` (the model cannot supply a path), the name carries a timestamp, and if
  that name is taken a numeric suffix is appended - "never overwrite" is a property, not an intention.
  The root is overridable with `WEFLOW_ASSISTANT_EXPORT_ROOT` (tests use a temp directory; a test run must
  not write into the repository).
- Deliberately **not** tool-ified, with reasons recorded rather than left implicit: `evidence-review` and
  `vault promote` have their own preview-and-confirm gates for a human at a terminal, and routing a chat
  message around those gates would defeat the reason they exist; sending messages is outward-facing and
  irreversible, and the repository does not implement remote silent control; changing privacy settings, the
  allowlist or the sources stays on the machine.

- Three gaps in existing tools closed rather than three new tools added - each was a case where the CLI could
  already do it and the tool was just narrower than the question:
- `get_sns` gained mode `users` ("who posts the most"), aggregated locally from the timeline it already
  fetches, so it adds no new egress; the CLI had this as a subcommand and the tool only had timeline/stats.
- `export_chat` gained a `format` argument (`html` default, plus `txt`/`json`/`excel`). An unrecognised format
  is a parameter error, not a guess, and the reply names the format it wrote.
- `search_favorites` no longer requires a keyword: with none it lists the most recent favourites, which is a
  question people actually ask. "The collection is empty" and "nothing matched that word" are now different
  sentences, as they were for the search case.

### Changed
- Image downloads during the daily run are concurrent (6-way). They were sequential at 0.37 s and 135 KB each - about 18 minutes per 190-article day - even though they come from `.qpic.cn`, WeChat's CDN, which a browser fetches in parallel anyway. Same three articles: 17.2 s → 2.1 s. The same change fixed the map: a failed download used to be recorded in `.image_map.json` **before** it was attempted, and the reader injects that map as `window._IMG_MAP`, so the page was told to look for a local file that did not exist. Only files that are actually on disk are mapped now, and duplicates in a page are fetched once (31 image links in one article were 17 distinct images).
- LLM summaries are generated concurrently, so a 190-article day spends about 2 minutes there instead of 9 (measured 2.27/2.92/2.45 s per article). The calls are **prefetched, not the loop rewritten**: responses are filled back by their original index and the existing loop still does the parsing and the field writes in the same order, so every fallback branch behaves exactly as before - a failed call comes back as an error and the loop re-raises it into its own `except`. The per-article 0.3 s pacing moved into the worker, so the request rate to the provider is unchanged. The stage now prints `摘要完成 N/M 篇，耗时 Xs（6 并发；串行约需 Ys）`, the shape the classification stage already used.
- Daily article fetching now asks for compression. `urllib` does not send `Accept-Encoding` by default, so this machine was downloading every 公众号 article page - 3-4 MB of inline JS and CSS - uncompressed. Measured on the same four articles: **3.3-3.5 MB and 33-40 s before, 0.7-0.8 MB and 6-10 s after**, with the decompressed page identical in size and `js_content` / `rich_media_content` still present, and the parsed markdown byte-for-byte the same length. On a 190-article day that is roughly **130 minutes of fetching down to 33** - and fewer bytes cross the wire than before, which is the same direction as the deliberate per-article throttle rather than against it. Where the rest of a many-source day goes is now measured too: image downloads ~18 min (0.37 s and 135 KB per image, sequential), the 8-12 s inter-article throttle ~32 min, the serial summary loop ~11 min - so the throttle is now the largest single line item.
- Daily classification now runs concurrently **before** the serial summary loop instead of one article at a time inside it. Classification has no dependency on the summary, so serialising the two only ever meant queueing N network waits behind N model calls. Measured on 12 real articles: 3.3s at 6 workers against ~12s serial, so a ~190-article day goes from ~184s to about 30s. The concurrent stage prints one progress line (`分类完成 N/M 篇`, with the elapsed time and the serial estimate) because per-article logs stop being meaningful once it is parallel; the `M` is the number actually asked, so an article that failed to classify is visible rather than inferred.
- The three-level cut points for relevance had to be chosen before there was anything to calibrate against. Observed scores span `[0,2]`, so the levels are split at 0.5 and 1.5. On a 6-article trial with the shipped criteria nothing crossed into 高, so the report's `relevance != '高'` gate stays about as closed as before - it is now **able** to open, which it never was.
- The HTML exporter now tries the conversation cache before the network when resolving an article thumbnail; it had been downloading covers it already had on disk.
- `collectMessagesInRangeDetailed` distinguishes why paging stopped. The original loop ended on a short page exactly as it ended on an exhausted conversation, so a short page caused by offset shifting on a live database looked identical to having reached the end. The original function is unchanged; the variant is used by the new sync path and available to the export path next.
- Message reads adapt to the shard's actual columns instead of assuming a fixed schema. The read used to select fourteen columns while consuming six, so a WeChat version renaming any of the other eight made every shard raise - and the conversation came back **empty with no error** unless `--report-shards` was used. The read now selects only the columns it consumes, and a shard that lost one is read anyway with the loss named in `missingColumns`. A shard where none of `create_time`/`local_id`/`server_id` survives is reported as `SCHEMA_MISMATCH` and makes `coverage` partial. `export_chat_html` reads rows by position, so there a missing column keeps its slot as `NULL` rather than shifting the fields after it - a shifted export would have been silently wrong data rather than an obvious failure.
- `messages --from <unix>` / `--to <unix>` bound the read in SQL, and `sync` passes its window lower bound through. **This measured no faster** on the conversation used for development (519 ms unbounded vs 514 ms from the newest message, 925 messages over 4 shards) because the cost is process start plus 256000-round PBKDF2 per shard, not row transfer. It is there for very large conversations and as the prerequisite for a stable cursor; `sync` still applies the window itself, so backends without range support stay correct.
- Two closed vocabularies that the code compares by literal are now declared once instead of twice. The message **anchor columns** (`create_time`, `local_id`, `server_id`) had three copies - a named one, a second literal in the same file's `ORDER BY`, and a third in the exporter - and moved into `nt_common` alongside the other shared NT plumbing. Their **order is behaviour**: one use is a set membership test (order irrelevant) and the other is the sort key, so listing `local_id` first would silently reorder every conversation read. A test pins `create_time` first, the tuple type (a set would let the order follow the interpreter's hash seed), and the literal's absence from both readers. The relevance levels moved to `_utils` beside `TOPICS`, with `DEFAULT_RELEVANCE` naming what an unclassified article is recorded as - the value every failed classification path lands on, and the reason the corpus once read 中 for 2199 of 2201 articles even though 中 reads as a positive judgement rather than "not judged". `extract_todos.py`'s identical `['高','中','低']` is deliberately left uncoupled: that is the `--urgency` vocabulary, which merely shares three characters.

### Fixed
- The fetch guard in the assistant (`isSafeUrl`, used by `read_favorite` before it fetches
  a link found in a favourite) had two holes, found by testing it for the first time. **IPv6 was
  not handled at all**: `[::ffff:127.0.0.1]` (an IPv4-mapped loopback), `[fd00::1]` and
  `[fe80::1]` were all allowed, so a favourite could have pointed the assistant - a process
  holding the local database handle - at loopback or link-local space. In the other direction,
  the private-range regexes were matched against any hostname, so the legitimate public domain
  `10.example.com` was refused as unsafe. The dotted-quad checks now apply only when the
  hostname really is four dotted octets, and IPv6 is refused by prefix (`::`, `::1`, `::ffff:`,
  `fc00::/7`, `fe80::/10`). Measuring also retired an assumption: Node normalises integer IPv4
  forms (`2130706433`, `0x7f000001`) to `127.0.0.1` before this function runs, so those were
  never a bypass - now pinned by a test instead of believed. The guard remains a denylist of
  known forms, not a proof that an address is publicly routable (NAT64 is uncovered), and the
  code says so. See D-040.

- `assistant start` now actually starts the daemon, and reports success only when it does. The child
  was spawned without `--yes` while `assistant run` confirms through `inquirer` on a stdin the
  daemon had set to `ignore` - so the child died on the prompt while the parent printed
  `✓ 守护进程已启动 (pid …)` and wrote a pid file. `~/.weflow-cli/` contained **no `assistant.log`
  and no `assistant.pid` at all**, which is how the documented flow turned out never to have
  completed. The env marker that appeared to guard the path (`WEFLOW_ASSISTANT_DAEMON=1`) was read
  by nothing and was constructed as an env **key** containing `=`; both are gone. The parent
  confirms and passes `--yes` explicitly, and success is now observed: the child is watched for
  700 ms and a dead one is reported with its exit code and the log tail, **without** writing a pid
  file. A spawn `'error'` event is handled as well - without a listener Node turns it into an
  uncaught exception in the caller. Verified live: it now reports
  `子进程启动后立即退出 (code 1)；日志尾部: Error: 未登录消息通道, 先运行 weflow-cli login-wechat`,
  which is the actual blocker on that machine (no `wechatOcToken`, empty allowlist). See D-039.


- A daily run wrote **two different topics for the same article**. `.articles.json` defaulted a missing topic to `''` while the step that names the folder and writes the md frontmatter defaulted it to `学术` - seven lines apart, neither reporting anything. The 2026-09-04 output shows the split directly: 178 articles, every md under `学术/` carrying `topic: 学术` (including "OpenAI 深夜发布 GPT-6 Astra" and an AI-tool launch), and every entry in that day's `.articles.json` carrying `topic: ""`. Downstream reads the JSON, so the admission gate `topic != FOCUS_TOPIC and relevance != '高'` dropped the whole batch without a word. It is reachable without anything unusual: `daily --no-ai`, or a daily run with no API key, skips classification entirely, so no article has a `topic` key at all while the write phase still runs. The fallback is now one constant (`_utils.DEFAULT_TOPIC`) applied by one function that both normalises and groups (`biz_daily._group_by_topic`), so "the topic used for the folder" and "the topic written to the JSON" cannot be different values by construction. It validates **membership** in `TOPICS` rather than mere emptiness, and the run prints how many articles fell back, so a whole-batch fallback cannot pass as a normal classification. `test/default_topic_test.py` pins the grouping key, the md frontmatter and the JSON entry to the same value, and the fallback literal to one place; the checks were mutation-tested. The **same shape seven lines away** sat in `tags`: the md writer defaulted a missing key to `[topic]` and the JSON writer to `[]`, so those same 09-04 articles read `tags: ['学术']` in the md and `tags: []` in the JSON. Both now call `_tags_for_write`, which follows the convention the codebase already had (three classification paths write `[topic]` themselves) while still keeping a *present but empty* list empty - that is a different case, and collapsing it would just be a new default written in two places.
- A leaked database handle when a shard opened but its key was rejected: the connection was left open, which on Windows keeps the file locked.
- `coverage: 'partial'` now also covers a shard that opened but could not be read as asked (`SCHEMA_MISMATCH`, `WINDOW_UNAVAILABLE`) rather than only `READ_FAILED`. All three describe rows that were not read. A shard that never opened still reports `unverified`, unchanged. A shard read with columns missing is **not** partial - its rows did come back.
- A shard whose `Name2Id` table is absent no longer loses its messages: sender names are unavailable, the rows are still returned.
- A windowed read can no longer pass as covered when a shard cannot express the window. On a shard without `create_time` the read has nothing to filter on: it now says `WINDOW_UNAVAILABLE` instead of returning rows that the window filter then silently drops while `coverage` still said `complete`. No real WeChat schema observed so far triggers this - it is a hypothetical-path fix, tested against synthetic shards.
- `awaiting --html <path>` writes a one-page, **self-contained**, shareable summary: headline counts, a table of verdicts with probabilities, debt age, conversation kind and evidence strength, and the uncertain bucket. It deliberately carries no chat text - only probabilities, counts and day counts - which is what makes it safe to show someone else. Contact display names are escaped, and the renderer passes through a fixed set of fields, so a message body attached to a row cannot leak into the page. The footer states what the numbers are not: no gold standard, run-to-run variance near the threshold, and that thin evidence is not a conclusion. See `--html`.
- `scripts/quality_eval.py`: the way to finally check whether the probabilities mean anything. `sample` draws a stratified sample of real articles and scores each one through the **production** path, then writes a file with two blank fields per article (`topic`, `include`); `score` reads it back and prints topic agreement for both Jev and the stored labels, a **calibration table** (per probability bucket, what fraction the human actually said should be included), and a threshold sweep naming the cut point that best matches the human. Labels live in `~/.weflow-cli/labels/`, never in the repo. Every number it has produced so far has been a comparison against an imperfect baseline; this is the first thing that can replace that with a person's answer. 49 articles sampled and scored for ~$0.005.
- `decide --over <glob> --ask <text>` batch mode: one glob times one set of yes/no questions expands into N×M questions in a single request, and the response carries a `batch` mapping so the caller never parses question names to find out which file an answer belongs to. `--max-chars` (default 1500) is reported back in the response, because the evidence you feed is what sets the ceiling - measured: the same 132 questions scored 77.3% agreement against a baseline at 14 lines per file and 88.6% at 1500 characters. `capabilities --json` now declares the two input paths separately (`readsLocalData: {request: false, over: true}`), since only `--over` reads local files.
- `weflow-cli decide --request <file>`: a local judgement primitive. Takes a caller-supplied `{state, questions}` and returns typed answers with probabilities, plus the token count and the cost of the call, from one request. It reads no local data - the state is whatever the caller provides. Requests are validated locally so a malformed one fails with a reason instead of a service-side 422. **Not exposed over MCP**: an MCP client driving local outbound calls is a new egress surface and needs its own decision (recorded as `mcpExposed: false` in `capabilities --json`). See D-034.
- `weflow-cli awaiting`: who is waiting on a reply. Asks one decision-model request per conversation and prints a ranked list; `--dry-run` previews what would be sent without any egress, `--yes` runs, and it is registered in `capabilities --json` (D-018). The underlying script is `scripts/reply_debt.py`: it asks one decision-model request per conversation whether the thread is sitting on a reply the user owes, and prints a ranked list with probabilities, debt age, and conversation kind. It is **not** the same question as `extract_todos.py`, which scans up to 80 conversations into a *single* LLM call asking whether any task was mentioned - that call has nowhere to record *who* is waiting. Judgements here are per-conversation, so each one knows whose it is. 25 conversations judged in ~5s at 6 workers. Each row prints the strength of its evidence, because a score derived from a two-character message is not the same claim as one derived from a paragraph. See D-033.
- Search results are reranked by a decision model. `semantic_search.search()` scored by embedding similarity and took `argsort[:top_k]` - there was no second pass at all, and the keyword fallback (used whenever embeddings are unavailable) was cruder still. It now asks one question per candidate in a **single** request (~1s for 20 candidates; measured 0.84s at 2 questions against 0.91s at 12) and reorders by the probabilities, keeping `score` as-is and adding `rerankScore`. `--no-rerank` restores the previous behaviour. See D-032. Verified on real data: an article about PM2.5 and plant water-use efficiency, planted at position 4 of 8, came out first at 0.94 against 0.01-0.04 for the rest.
- Transient decision-model failures are retried (429/5xx/529, short backoff). A live `HTTP 529 system_overloaded` was hit while building the reranker - the service launched on 2026-09-15 and being saturated is plausible. Auth failures are not retried.
- A preview no longer requires the API key it is meant to help you decide about spending. `awaiting --dry-run` used to check for a key first and refuse without one, which put the cost of entry *before* the menu.
- Non-text messages (images, voice, stickers) no longer reach a decision model as blank lines. WeChat stores them with empty `message_content`, so `reply_debt.py` sent `对方：` with nothing after it and got a confident answer back from the blank. They now carry a type label. See D-033.
- `build_index` now indexes articles. `collect_articles()` filtered directory entries with `glob(' marriage*.md')` - a leading space and a `marriage` prefix, matching **0 of 188** files in a real topic directory. So the semantic index had never contained a single article, only chat messages, and nothing reported it. **The next `search-index` run will index articles for the first time and therefore consume embedding calls it never spent before** - that is a deliberate rebuild, not something a routine command should do silently.
- `semantic_search` imports `numpy` and `sqlcipher3` lazily now (`require_numpy()` / `require_sqlcipher()`, the pattern `nt_decrypt.require_sqlcipher` already used). Previously a missing dependency called `sys.exit()` at import time, so the keyword-only and reranking paths - which need neither - were unusable on such a machine, and no test could import the module at all.
- Batch mode in `decide` shipped without tests and crashed on first use: a deletion patch had left dead code in `main()` that the batch branch fell into, referencing an undefined variable. Found by running it, not by reading it, which is the point - `test/decide_test.py` now covers the batch path, including that it reaches the model at all.
- `relevanceScore`, `topicConfidence` and `includeScore` never reached `.articles.json`, only the markdown frontmatter - and the report reads the JSON first. So the probability fields were invisible on the report's **primary** path. Introduced with the fields themselves, found by running a real dry-run and seeing the new section claim there were no probabilities, while the frontmatter plainly had them. Their serialization is now a named function with a test.
- `dashscopeApiKey` is a first-class config key now: declared in `CliConfig`, **encrypted at rest** like the other secrets, and settable with `config set`. It had been read by `rag_chat` and `semantic_search` while not existing in the schema at all, so `config set dashscopeApiKey` was refused and the scripts' own error text could only tell the user to hand-edit `config.json`. Their reads now go through `_utils.get_dashscope_key`.
- `config set favPassphrase` works. It was in `ENCRYPTED_KEYS` and read by `biz_daily` and `reply_debt`, but was missing from the `configurableKeys` allowlist - so a first-class secret could be neither set nor rotated through any documented path, and hand-editing a file whose field is ciphertext does not produce something usable.
- Two static checks now enforce what that audit was looking for: every key a script reads is declared in `CliConfig` (`test/config_keys_declared_test.py`, with a justification-required exception list), and no script reads an encrypted key without decrypting it.
- `nt_decrypt` and `export_chat_html` shared four same-named helpers that were **not the same code**, and they are now one implementation in `scripts/nt_common.py` (`discover_message_shards`, `derive_database_key`, `table_columns`, plus the exclusion set). The two differed in contract, not just in wording: `discover_message_shards` in `nt_decrypt` returned `[the configured database]` when it globbed no shards, while the exporter's returned an empty list and left the compensation to its call site. A second caller that forgot to compensate would silently read zero shards. `derive_database_key` and `table_columns` were byte-identical apart from docstrings. The never-empty contract is now the only one, the exporter's `if not shards: shards = [db_path]` is gone, and `test/nt_decrypt_shards_test.py` pins the single implementation by **identity** rather than by "the two agree" - two copies that happen to agree is exactly how this drifted in the first place.
- `nt_decrypt.load_contact_names` no longer leaks its connection when the read fails: `conn.close()` sat at the end of the `try`, so any exception before it left the handle open, which on Windows keeps the file locked (the same shape as the shard-read leak fixed earlier). It also keeps the names it did read instead of discarding all of them.
- The keyword-search fallback no longer returns generated report artifacts as articles. Searching for 环境 空气质量 returned **行动建议 — 2026-08-30**, a file the daily pipeline writes, not an article. The artifact exists at **two depths** (`<day>/行动建议.md` and `<day>/<topic>/行动建议.md`, four of each on the real data), so filtering by path is not enough - the fix uses the same predicate `quality_eval` already applies: a real article carries `url:` in its frontmatter. The index-building path never had this problem because it only walks topic directories, which is why it went unnoticed: **the two paths disagreed, and the unfiltered one is the one that actually runs** (the index has never been built on this machine).
- Four scripts read `deepseekApiKey` straight out of the config file while every other reader decrypts it first: `annual_report`, `classify_daily`, `extract_todos` and `rag_chat`. Since that key is in `ENCRYPTED_KEYS`, `config set` stores it as `lock:<ciphertext>` - so those four would hand `lock:...` to the provider as an API key and report an auth failure that says nothing about why. **It has not surfaced only because on this machine that one key happens to be plaintext**, written by something that bypassed `config set`; every other encrypted key on disk is ciphertext. Rotating the key with `config set` would have broken all four at once. They now go through `_utils.get_api_key`, and `test/encrypted_config_test.py` statically fails the build if any script reads an encrypted key without decrypting it - while accepting the repo's existing `*_enc` convention (hold the ciphertext, decrypt later), which seven correct call sites use.
- Four declared-but-unused things are gone, each of which described behaviour the code did not have: `biz_daily.MAX_ARTICLES = 50` with a comment claiming it capped fetching (never read; `--limit` is what works, and a normal day is 150-270 articles); a `CONFIG_PATH` in the same file (config goes through `_utils.load_config`); `_utils.parallel_map` (a concurrency helper with no callers); and fifteen lines of **unreachable** code after `search()`'s return, which returned a `dict` while the live code returns a `list` - a dead block documenting a contract the function no longer has. Their imports went with them.
- The article prompt no longer contradicts itself about the topic list. `TOPIC_PROMPT` generated its "must be one of" line from `TOPICS` (six categories) but hardcoded **five** in the two reminders further down, and the judging rules had no entry for 政治 at all - a category that accounts for 26% of the corpus. Both reminders and the rules are now generated from the shared table, so the LLM path is told to pick from six categories *and* given a definition for each. **This can change topic output on the LLM path** (which is the fallback when no decision-model key is configured): a category that previously had no criteria now has one.
- The topic taxonomy has a single definition. `TOPICS` had been copied into six files and `TOPIC_CRITERIA` existed separately in `jev_client`; both now live in `_utils` and every consumer imports them, so the prompt path and the decision-model path judge by the same table. `test/topic_taxonomy_test.py` pins it by identity, not equality - copying a list and getting it right is exactly how it drifted the first time.
- The admission filter's log line claimed the threshold was applied even when it was not: on articles without `includeScore` it printed `（阈值 includeScore >= 0.5）` while the old `relevance == '高'` rule was doing the filtering. It now names the rule it actually used, including the mixed case. A log that misdescribes what it did is worse than no log.
- The daily report's admission rule is now actually applied. `if topic != FOCUS_TOPIC and relevance != '高': continue` existed only in the markdown-scan *fallback* loader; the primary loader (`.articles.json`, present on every normal run) returned every article unfiltered, so the rule had never taken effect. Both loaders now call one predicate, and `--include-all` restores the previous collect-everything behaviour. **This changes what the report contains**: non-focus articles will be fewer, because previously there were no filters at all.
- `relevance` is now assigned on **every** path through the daily classification loop. It used to be set on two of five: `category_hint` hard-coded it to `中`, and the AI-exception and short-content paths left it unset for the writer's `fm.get('relevance', '中')` to catch. Measured over the 2201 stored articles, 2199 of them carried that default. That alone would have left `generate_ai_report.py`'s gate filtering on topic only; the gate turning out to be unreachable on the primary loader made even that generous.

## 1.6.4

### Fixed

- Restored four pieces of matching logic in the HTML exporter that the `2d368c6` clone-consolidation merge had silently dropped, all of which lowered media coverage without failing loudly:
  - the `unique:<local_id>` fallback, which matches an image when that id resolves to exactly one distinct picture in the conversation (content-deduped, so the shard-collision risk that rules out a bare `local_id` does not apply);
  - `is_encoded_media_type`, needed because WeChat stores some forwards as high-bit variants of type 49 — the mask turns those into a plain 49, which is a registered type, so the branch guarding them had become unreachable;
  - the guard that keeps a type-49 row carrying a title or url on the link-card path instead of hiding it behind a cached thumbnail;
  - the cache-first lookup in the article branch, which had been downloading covers it already had on disk.
- Seeded `COVER_STATE`'s budgets with their limits instead of 0. They were only ever set by `main()`, so any caller reaching the fetchers another way silently skipped every remote fetch and got `None` back with no error.
- `pipeline_security_test` no longer reads the developer's real `~/.weflow-cli/config.json`; it points `CONFIG_PATH` at an empty temp file. The test only passed on a machine that happened to have a config, and reading a real config from a unit test is exactly what the repo rules forbid.
- CI actually runs, for the first time. `npm test` passed locally and failed on every push: the script used `test/**/*.test.ts`, which Git Bash expands locally but CI's bash does not (globstar is off by default), so Node received the literal pattern and reported `Could not find ...`. The glob is now quoted so Node expands it. The single combined job was also split into `node` / `python` / `release-consistency`, because a Node failure previously stopped the Python tests from running at all — they had been failing unnoticed.
- Raised the Node floor to 22.13.0. `src/core/sqlcipherCore.ts` imports `node:sqlite` at module scope; that builtin arrives in 22.5.0 and stops needing `--experimental-sqlite` at 22.13.0, so on Node 18/20 every command died at load while `engines` still claimed `>=18`. Both are past end-of-life, so the floor moved rather than adding a lazy-load path for dead runtimes.
- Rebuilt `package-lock.json`, which still said 1.6.1 and still listed `lz4` as an ordinary dependency after the 1.6.3 change moved it to optional. `npm audit fix` then took production vulnerabilities from 17 to 6; the remainder (`@xmldom/xmldom`, `exceljs`, `@wenyan-md/core`, `mermaid`, `speech-rule-engine`, `uuid`) have no non-breaking fix — `exceljs@4.4.0` and `@wenyan-md/core@3.0.11` are already the newest releases and the advisories' suggested "fix" is to downgrade them.

### Added

- `docs/PROJECT_STATE.md` no longer carries a hardcoded version — it points at `package.json`, which is what drifted to `1.5.1`. Example version numbers in `docs/NPM-PUBLISH.md` and `docs/HEALTH-CHECK.md` became placeholders for the same reason.
- Git tags and GitHub Releases for `1.6.0`, `1.6.1` and `1.6.3`, so npm versions map to commits. `1.6.2` has neither: it was published, but its version bump was never committed.

## 1.6.3

### Fixed

- `npm install -g weflow-cli` no longer fails on machines without Visual Studio build tools. `lz4@0.6.5` declares an unconditional `"install": "node-gyp rebuild"` with no prebuilt binaries and no fallback, so having it in `dependencies` made the whole install abort with `gyp ERR! find VS could not find a version of Visual Studio 2017 or newer` - for every Windows user without a C++ toolchain, which is most of them. The code only ever used it for 3.x WCDB `CompressContent` decompression, and already loaded it lazily behind a `try/catch` that degrades to `null`, so it is now an `optionalDependency`: npm installs it best-effort and continues when the build fails. Measured on a machine with no Visual Studio: before, `npm install` exited 1; after, it exits 0 and the installed CLI runs.

## 1.6.2

### Fixed

- Read every message shard, not just the configured one. WeChat rolls a conversation into a new `message_N.db` over time and encrypts each shard with its own PBKDF2-derived key. `sessions`, `messages`, `contacts`, `export json/txt/excel`, `evidence` and the MCP server opened only `message_0.db` with the single configured key, so anything written after the last shard roll was **invisible** - and the commands still reported success. On the machine this was found on, one conversation showed 31 messages ending 2026-08-30 instead of 1145 ending 2026-09-17. Only `export html` merged shards (its Python exporter always had), which is why the two read paths disagreed.
- `export <talker> html --limit N` now honours the limit. It reached only the fallback renderer; the primary path passed the flag nowhere, so a capped export of a busy chat silently produced the entire history.
- `export <talker> excel` works at all. `exceljs` is CJS-only, so `await import('exceljs')` yields a namespace whose only member is `default`; `new ExcelJS.Workbook()` on the namespace is a `TypeError`, which a bare `catch {}` turned into the uninformative "Excel 导出失败". The reason is now reported too.
- Group messages no longer show the sender's own `wxid_...:` prefix in the text (`波: [强]`, not `波: wxid_ogfiei1l1ye722: [强]`). Only `parsedContent` is normalised; `content`/`rawContent` keep the stored value.
- `contacts` no longer emits a blank row for `Name2Id`'s placeholder entry.
- `fav list` no longer blames the data channel when the actual blocker is a missing favorites key. Added `check --json` → `favoritesReady`, which distinguishes "database found" from "usable".
- Subprocess failures now include the last stderr line, redacted of key-shaped strings. A missing config key used to surface as `统计失败 (exit 1)` with no cause; it now names the cause and the command that fixes it.
- `daily-stats` / `daily` no longer tell users to run `config set bizKey`, which the CLI rejects as an unwritable key.
- `init --refresh` no longer fails with "密钥提取失败" when a perfectly good key is already configured. The hook only fires at the login moment, so a refresh run against an already-logged-in WeChat always times out; the fallback then consulted **only** `favPassphrase`, which `init` itself never writes (it writes `decryptKey`, and `favPassphrase` is set only when a usable `favorite.db` exists). Reads use `favPassphrase || decryptKey`, so the fallback rejected keys the rest of the tool was happily using. Reported as "最新版本的微信密钥解不开了" (Issue #9) after a WeChat upgrade, where `init --refresh` is exactly what the CLI tells users to run. The fallback now matches the read path, and the summary distinguishes a fresh capture ("密钥获取成功") from reusing what was configured ("已沿用配置中的密钥") instead of claiming success either way. Reaching that point also lets `enableFavorites` run, so favorites start working for users who previously died here.

### Added

- `scripts/health_check.py` and `docs/HEALTH-CHECK.md`: a periodic, zero-token health check whose exit code is the verdict, covering version drift, shard read consistency, session freshness, export formats and the watcher tasks.

## 1.6.1

### Fixed

- Report the real version from `--version` and `capabilities`. The version was a literal in the CLI source and `npm version` only rewrites `package.json`, so the 1.6.0 package announced itself as `1.5.1` - which made a user's bug report impossible to tell apart from a stale install, and took a clean install to disprove.
- Keep the discovered database list when the NT scan cannot read WeChat's memory. `nt_decrypt.py scan` returned early on "Weixin.exe 未运行" *before* walking the filesystem, and the caller discarded the whole result on any error. The passphrase-derivation path - the correct one for current WeChat, and one that needs only the file list - therefore never ran, so a run that had already captured the passphrase still ended with no usable keys and `sessions` reported "WCDB 初始化失败: -1006".

### Documentation

- Added `docs/RELEASING.md`: versioning rules, the pre-publish check for local media and keys, npm credentials with 2FA, and the China-mirror sync step that otherwise leaves users unable to install a fresh release.

## 1.6.0

### Documentation

- Synchronized setup, operations, architecture, security, MCP, and maintenance guidance with the current `1.6.1` source baseline.
- Clarified source-versus-npm version drift, no-AI daily runs, staged data-directory discovery, media-export limitations, and local-data privacy boundaries.
- Replaced the outdated architecture image with a GPT-image-2 diagram covering current CLI, MCP, service, workflow, data, and privacy boundaries.

### Fixed

- Decode locally cached WeChat 4.x V2 image containers during HTML chat export by deriving and validating the account-specific media key from local `kvcomm` data.
- Match exported chat media by stable server-message identity so reused local IDs cannot attach an unrelated image or emoji.
- Match NT cache media by the exact local-message-ID and timestamp pair when server-resource metadata is unavailable, including reused local IDs.
- Preserve forwarded app cards with cached covers, including CDATA-wrapped Bilibili links, and decrypt remote WeChat 4.x emoticons with their message-provided AES key.
- Decode entity-escaped emoji XML and try `encrypturl`, `thumburl`, `cdnurl`, and `externurl` fallbacks; resolve Bilibili BV covers when a share page omits `og:image`.
- Render signature-only WeChat default `[打脸]` messages with the bundled official `Facepalm` asset when no message-specific resource is available.
- Cache remote export media (covers, article thumbnails, emoticon CDN) on disk, misses included, so a re-export no longer repeats hundreds of requests against dead WeChat CDN links. First export of a link-heavy conversation dropped from 228s to 28s and re-export to 1.8s, with identical output.
- Fetch that remote media concurrently instead of one URL per message. A throwaway local-only pass records what the conversation needs, the URLs are fetched in parallel, then the real pass runs against a warm cache.
- Try WeChat's local sticker cache before the CDN for custom emoticons. The local path is offline and instant; the CDN cost 1.32s per sticker and was tried first.
- Discover the account's sticker seed automatically. `find_seed`/`any_sticker_file` existed but nothing called them, so `emoticonSeed` stayed empty, local decryption never ran, and custom stickers silently degraded. The export now derives it from a real cached sticker, memoises it, and prints the command to persist it.
- Render `local_type=10000` system rows as text. Escaping the raw row put WeChat's own display markup (`<img src="SystemMessages_HongbaoIcon.png"/>`, `<_wc_custom_link_ ...>`) in the bubble as a wall of `&lt;sysmsg ...&gt;`, so a revoke notice read as XML instead of naming who revoked what. `$wxid_...$` placeholders are expanded too.
- Flatten quoted replies (appmsg type 57) whose `<des>` carries a whole escaped nested message, instead of dumping the nested markup.
- Surface the Python exporter's progress and diagnostics in `export html` output; only the trailing JSON summary was read, so a multi-minute export showed nothing in between.
- Name the actual speaker in group chats. `display_name` is the group, so every bubble was labelled with the group name and no message could be attributed; group rows now use the sender id carried by the content prefix, falling back to the sender map.
- Render `local_type=48` location rows as their place label (`[位置] ...`) instead of dumping the location XML.
- Strip the redundant `wxid_...: ` content prefix from group rows once it has been used to identify the speaker.
- Resolve group senders to names from the contact database (remark, then nickname, then alias). A group transcript previously showed raw wxids for every speaker; unresolved ids still fall back to the id rather than a blank.
- Merge the conversation cache and the account media index instead of choosing between them. A `--cache-dir` short-circuited the account scan, and a conversation cache only keeps recent months, so a group photo from last year resolved 0 of 1444 images and every one rendered as a bare `[图片]`. The same fix also recovered 82 additional images in a 1:1 conversation.
- Show a cached poster frame for `local_type=43` videos when one exists, and always show the clip's duration (`[视频 10″]`) instead of a bare `[视频]`. Where WeChat never downloaded the video, no poster exists locally to show.
- Fix HTML part navigation, which linked to `<talker>_partN.html` while the files were written as `<remark>_partN.html`, so every "第 N 部分" link opened a missing file (`ERR_FILE_NOT_FOUND`). Affected both group and 1:1 exports; all 16165 links across the two test conversations now resolve.
- Correct the exported page footer, which still claimed images cover only the most recent two months.
- Show the drawn frame of a `wxgf` sticker instead of a blank white square. Sticker H.265 streams routinely open with a blank transition frame, and taking frame 1 embedded that; decoding a few frames and keeping the one carrying the most artwork fixes it (one sticker measured 99.1% white before, 2.1% after). Verified across three conversations: 2215 embedded images, 0 blank.
- Treat an all-blank sticker payload as a failed decode rather than an image, so a truncated local download or a CDN placeholder falls back to the `[表情]` label instead of rendering an empty square.
- Test the plain-text branch against the message body rather than the row's combined metadata. `metadata_content` always carries `<msgsource>`, so the check always saw a `<` and every text message that had sender metadata skipped the text branch and fell through to the emoji one - rendering a Tencent Meeting invite as `[表情]` beside a broken image.
- Label `local_type=10000` system rows as `系统` and never fall back to the conversation name for a group speaker. A revoke notice or join template resolves to no member, and the fallback labelled it with the group name, reading as if the group itself had spoken.
- Render `sysmsgtemplate` join notices from their template and member list (`"彪弟"邀请你和"777"加入了群聊`) instead of stripping the tags and leaving only the chatroom id.
- Never emit a remote URL as an `<img>` source. The candidate comes from a catch-all that accepts any URL in the row, and a sample of 57 such sources found 56 were web page links (`meeting.tencent.com`, `github.com`, `support.weixin.qq.com`) rather than images, each rendering as a broken-image icon. Images we could not fetch are now simply not shown.
- Improved WeChat data-directory discovery for custom locations, nested folders, and database subdirectories.
- Added staged guidance and optional `init --full-scan` fallback when automatic discovery cannot find the data.
- Completed incomplete yesterday output before an unqualified daily report run.

### Added

- Transcribe voice messages into HTML exports. WeChat voice is SILK v3, which browsers cannot play and ffmpeg cannot decode at all, so a voice message previously carried no information whatsoever. `scripts/wechat_voice.py` decodes the payloads from `media_*.db`'s `VoiceInfo` table and recognises them on-device, and the export renders `[语音 6″]` with the transcript beneath it.
- Transcription is a separate, resumable pass rather than part of the export: exports only read a content-addressed transcript cache, so a long conversation never blocks an export and an interrupted run continues where it stopped.
- Prefer a Cantonese fine-tune over stock Whisper. Stock Whisper answers Cantonese speech with fluent, confident Mandarin that was never said — worse than no transcript, because it reads as a real sentence. The Cantonese model transcribes the same clips into actual Cantonese, and stock `large-v3` was measurably worse still, hallucinating Vietnamese and English.
- Use the GPU when one is available, falling back to CPU. The same clip goes from 2.0s to 0.07s, which is the difference between a ~30 minute pass and an overnight one. Includes the Windows DLL-path setup the recognition library needs for its CUDA runtime.
- `requirements-voice.txt` declares the optional voice dependencies.
- Label machine transcripts and state the limitation in the page footer. Sampling a Cantonese family group found the recogniser producing Cantonese-shaped text whose meaning often does not hold - right sounds, wrong words. A confidently wrong transcript is worse than an obvious placeholder in a record that may be cited, so transcripts are marked `机器转写·粤语欠准` and the footer says they must not be quoted as the original words. See OPERATIONS.md for the measurements that rule out decoding, audio quality, and model confidence as causes.

### Security and reliability

- Run the regression suite in CI and make NT path-discovery checks independent of the optional SQLCipher runtime.
- Make data-directory search staged: common locations by default, explicit cross-drive search, then explicit deep structural search.
- Avoid printing database salts, account identifiers, and message paths in `dbkey` diagnostics.
- Added `daily favorites` commands to synchronize and manage reader favorites as local files.
- Added `vault promote ideas` and `vault promote all`; both default to no-AI local generation and require explicit opt-in for AI outputs.
- Keep the default MCP surface read-only and require unique conversation-name resolution with bounded query limits.
- Require preview and explicit confirmation for machine-driven messaging, configuration, access-control, todo, assistant lifecycle, MCP configuration, key reset, and Vault synchronization changes.
- Require preview and explicit confirmation for Vault initialization, semantic indexing, knowledge pipelines, and report generation; expose no-AI/source filtering and strict bounded parameters for Agent use.
- Validate local-reader ports before process startup and open browser URLs without shell interpolation.
- Restrict MCP article fetching to bounded HTTPS requests on the exact WeChat article host, including redirect revalidation.
- Validate outbound media files and remove full local paths from message previews and audit records.
- Resolve bundled Python scripts consistently from both source and compiled package layouts, and propagate worker failures through nonzero exit codes.
- Remove the ineffective `mcp-config --port` option; the MCP server uses stdio and does not bind a network port.
- Reject absolute, non-Markdown, symlink-escaping, and parent-traversal entries in daily favorite state before linking or copying files.
- Add preview, confirmation, and content-free JSON results to Vault content mutations, WeRead synchronization, daily favorites, and personal consumption reports.
- Add preview and confirmation to Wiki compilation and AI todo extraction; keep process-memory key capture explicitly human-gated.
- Add a machine-safe preview for database-key capture and require an interactive terminal for execution.
- Add a content-free initialization preview while keeping actual database discovery and key capture human-gated.
- Require preview and confirmation before Vault RAG reads local knowledge or sends selected context to AI.
- Require preview and confirmation for semantic search and RAG chat, and keep their private inputs out of child-process arguments.
- Keep report conversation selections and NT scan roots out of child-process arguments, and clear unrelated internal values from long-lived worker environments.
- Require preview and confirmation for evidence review, with content-free and path-free machine results.
- Reject unsupported WCDB query parameters instead of silently executing SQL without bindings.

### Agent interfaces

- Added `capabilities --json`, redacted configuration status, structured export results, reader status, diagnostics, access-list JSON, and no-AI daily JSON output.
- Added the versioned `weflow-message/v1` contract to CLI exports and the read-only `wechat.export_messages` MCP tool for downstream projects.
- Added conservative `coverage` metadata to versioned message exports and capability discovery, while preserving the legacy raw JSON array.
- Added bounded local evidence-package and explicitly authorized evidence-review commands.
- Applied message date ranges before pagination and preserved unknown message types in downstream contracts.
- Added content-free JSON summaries for account scanning and assistant logs, plus structured todo reminders.
- Added preview and confirmed background startup for Agent-controlled local daily readers.
- Apply the same startup confirmation to the legacy `fav-server` compatibility command.
- Require explicit confirmation for machine-driven daily generation, including no-AI runs, while preserving human and scheduled non-JSON commands.
- Add content-free previews for configuration, key, access-list, and audit-log clearing before confirmed deletion.

### Documentation and packaging

- Added a unified Python dependency manifest for the standard Windows 4.x workflow and an optional legacy 3.x manifest.
- Added MCP integration, contribution and security guidance.
- Included README architecture assets and installation manifests in npm and portable releases.

## 1.5.0

### Added

- Local reader dark mode, keyboard navigation and read/favorite tracking.
- WeChat Moments local-cache commands and AI learning daily reports.
- Improved knowledge-pipeline and reader workflows.

For earlier history, see the [commit log](https://github.com/zhuobichen/weflow-cli/commits/master).
