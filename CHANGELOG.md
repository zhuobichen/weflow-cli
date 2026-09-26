# Changelog

The npm package is published separately from GitHub. It may lag behind the `master` branch until a release is published.

All notable user-facing changes are recorded here. This project follows [Semantic Versioning](https://semver.org/).

## Unreleased

### Added

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

- Restored four pieces of matching logic in the HTML exporter that the `90b165a` clone-consolidation merge had silently dropped, all of which lowered media coverage without failing loudly:
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
