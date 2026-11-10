# 分支与合并记录

本文件记录分支的用途、合并时间与合并内容，以及并行开发时的对齐约定。
每次把工作分支并入 `master` 后，请在「合并记录」中追加一行。

> **2026-09-28：主线历史被压缩过一次，本文里的提交号已同步改成新号。**
> 规则是「相邻两笔间隔不到 1 分钟、且同一天、一组最多 5 笔」合成一笔 —— 只合这种
> "同一个动作分几次提交"的，正常节奏的提交一律不动。400 笔 → 319 笔。
> **内容一个字没变**（改写后 HEAD 的树哈希与改写前逐字节相同）。
> 三条合并提交（`39e3236` / `42e1859` / `2d368c6`）保留原样：它们是集成点，本文引用它们。
> 改写前的完整历史在分支 `backup/pre-squash-20260928-global` 上，逐条可查。
> 注意：其它分支（`feat/*`、`draft/*`、`salvage/*`、旧的 `backup/*`）**没有**跟着改写，
> 它们与新的主线不再有共同祖先 —— 要基于它们继续工作，得先把它们重放到新主线上。

## 分支一览

| 分支 | 用途 | 状态 |
| --- | --- | --- |
| `master` | 主线，随时可发布 | 受保护 |
| `trae/*` | 早期 AI 协作产生的分支 | 部分已合并，部分搁置 |
| `draft/*` | 探索性草稿，允许与主线脱节 | 按需清理 |
| `feat/*`、`fix/*` | 从**当前** `master` 切出的工作分支 | 合并后删除 |

> **2026-11-09 同步后的实际状态**（`origin` 与 `upstream` 均已 fetch）：
>
> | 分支 | 位置 | 跟踪 | 说明 |
> | --- | --- | --- | --- |
> | `master` | `679edbd` | `origin/master` | = 上游 `cfd2f92`（v1.21.8）+ 1 个本地提交 |
> | `docs/architecture-counts-guard` | `679edbd` | `origin/...` | 已对齐 master（内容全在 master，`git cherry` 无独有） |
> | `fix/temp-copy-leak` | `679edbd` | `origin/...` | 已对齐 master（同上） |
> | `backup/architecture-counts-guard-2026-11-03` | `c7459aa` | 无 | **历史快照，不对齐**（见下节） |
>
> 三个活跃分支现在**内容完全一致**（同一提交 `679edbd`），差别只在分支名与远端存在性。
> 它们各自保留了「分支」这一形态，以便将来某条线要单独演进时可直接开分支，
> 而不必先把主线与旧分支的差异重新梳理一遍。

> **约定**：`feat/*` 与 `fix/*` 必须从最新的 `master` 切出。`draft/*` 无此要求，
> 但**合并前必须先把 `master` 合进来**（见下方「并行开发」）。
> **清理节奏（2026-10-01 起）**：一条特性一条分支，切之前先 `git fetch` 并从最新 `master` 切；
> **合并后立刻删**（本地 + 远端）——回溯的锚是**合并提交**（`--no-ff` 保留了完整分支历史）与本文的表格，
> 不是分支本身。删除前逐条验 `git merge-base --is-ancestor <分支> master`。
> **`backup/pre-squash-*` 一律保留**：它们是那次历史改写唯一可查的现场，而且**互不包含**
> （实测 0924 / 0925 / 0928 都不是 `-global` 的祖先，各自有独有内容）——别想当然当冗余删掉。
> 无法用祖先关系证明「已合并」的分支**先记账、再问**，不凭文档记录删（见下节）。

## 待清理分支（2026-10-01 盘点）

这几条**还不能证明可以安全删除**，所以先记账、不删。判据是 `git cherry`（按 **patch-id** 比对，
不依赖祖先链——而本仓主线被改写过，`backup/*`、`draft/*`、`feat/*`、`trae/*` 与它**已无共同祖先**，
所以 `git branch --merged` 在这里是失效的，不能据此判断）。

| 分支 | master 里没有的补丁 | 抽查到的实质内容 |
| --- | --- | --- |
| `origin/trae/solo-agent-Xl3Lhb` / `agent-9Lq0jD` | 各 4 条 | 暗色模式切换与 `:root`/`index.css` 修复**内容已在 master**（模板里相关 token 数一致、`generate_html.py` 里有 `:root`）；但 `plan.md` 的那条隐私规则**不在**（master 少一处） |
| `origin/trae/agent-l9xgSw` | 19 条 | 未逐条核 |
| `origin/feat/export-enhancements` | 17 条 | 内容对照显示它只是改写前的旧快照（master 比它多 2.1 万行），但**证不了**它独有的那 17 条都在 |
| `origin/draft/weflow-cli-improvements` | 11 条 | 本文档早已记它是「未合并、已作废、独有功能已移植」，但它有 126 个独有提交 |

**要删的话**：把上表那几列补全（逐条看 `git cherry -v master <分支>` 那些 `+` 号的 subject），
确认无独有内容之后再删。删之前本节就是那份记录——**先记账再删，信息不会丢**。

`backup/pre-squash-*`（4 条）与 `salvage/clone-b-work` **不在清理范围内**：前者是历史改写的现场，
后者保全的是另一副本里未提交的改动。

## 合并记录

| 日期 | 来源分支 | 目标 | 方式 | 说明 |
| --- | --- | --- | --- | --- |
| 2026-06-30 | `trae/solo-agent-Xl3Lhb` | `master` | 合并提交 `39e3236` | 优化消息与朋友圈功能 |
| 2026-09-01 | `Mandark6/master` | `master` | PR #6，合并提交 `42e1859` | 外部贡献 |
| 2026-09-10 | `feat/export-enhancements` | `master` | 快进至 `64dcf2f` | 表情原版图、`--per-page` 分页、表情包本地解密、图片缩放；并修复跨分片未生效 |
| 2026-09-10 | `draft/weflow-cli-improvements` | — | **未合并，已作废** | 基于过期 master；独有功能已移植，见下方「2026-09-10」条目 |
| 2026-09-30 | `feat/assistant-skills-scenes-tool-surface` | `master` | 合并提交 `1b14c35` | 技能包与场景；工具面 21→31 并与 MCP 上限对齐；`wiki lint` 9 分 29 秒→3.4 秒 |
| 2026-09-30 | `feat/panel-eye-follow` | `master` | 合并提交 `1e4c305` | 悬浮球眼珠跟随鼠标；半隐（推到屏幕边就躲起来探头） |
| 2026-11-10 | `upstream/master`（上游采纳 PR #15 剩余部分） | `master` | 合并提交 `7cea389` | 上游 `99220b5`（temp_root 兜底 / probe 并发误判两处修复，附回归测试）+ `dddeeb4`（架构计数守卫 + SNS 指南）。冲突 3 处全在本地已改过的文件：`nt_decrypt.py` / `outputRoot.ts` 取上游（等价实现 + 更规范的注释），`ARCHITECTURE.md` 保留本地（实测 41,328 行，"4.0 万"比上游抄的"2.8 万"准）。`.gitignore` 自动合并出重复白名单行，已去重。合并后 emoticon 那项历史失败也顺带转绿（1403→1406 全过） |

两处说明（这次合并**不是快进**，有手工解冲突，记下来免得下次当成"随手拼的"）：

- **两条特性分支互不重叠**（助手那支动 `src/services/assistant*` 与 `mcp-server/`，悬浮球那支动 `resources/panel/`），
  冲突只在三个文档文件里，都是**两边往同一处追加**：`CHANGELOG.md` 保留双方；`docs/DECISIONS.md` 按既有倒序排（D-059 前、D-058 后）。
  只有 `docs/PROJECT_STATE.md` 是**同几行的两个版本、真相是混的**，逐行取：评测行与 Assistant 行取助手那支（23 用例 / 31 工具，那支更强），
  面板行取悬浮球那支（含眼球跟随与半隐；比过长度 9043 vs 12024，助手那支**没有改过**这一行），助手新增的两行保留。
- **测试数重新量过**：合并前两边各自写着 766 与 694，都不是合并后的真值；合并后实测 **775 条全过**，文档按实测值改。

两条分支（本地与远端）在合并后**已按约定删除**（2026-10-01）。回溯的锚是上面那两个合并提交——它们保留了完整的分支历史；两条分支当时的 tip（`9b33821` / `871e863`）也记在前面的表格里。

### 2026-09-10 · `draft/weflow-cli-improvements`

该分支产出了导出链路的若干增强，但**基于过期的 `master`**（共同祖先 `e6713f7`），
当时 `master` 已领先 78 个提交。因此**没有直接合并**，改为把其中的独有功能移植到
从最新 `master` 切出的 `feat/export-enhancements`。

分支仍保留在本地与远端，标签 `backup/my-export-work` 指向其顶端 `c181afb`。

移植与放弃的清单：

| 项 | 处理 | 原因 |
| --- | --- | --- |
| 跨分片读取 | **改修为接线修复** | `master` 有 `fetch_messages_from_shards`，但见下方「附带修复」 |
| 主密钥派生 | **放弃** | `master` 已有 `derive_database_key`，算法与草稿一致 |
| 图片按 `(local_id, create_time)` 配对 | **放弃** | `master` 已有 |
| `.dat` 图片解密与资源映射 | **放弃** | `master` 已有 `decode_wechat_v2`、`load_resource_media_map` |
| zstd 解压、CDATA 解析、名片渲染 | **放弃** | `master` 已有 |
| 会话列表改读 `session.db` | 待定 | 需与 `master` 现有实现比对 |
| **微信原版表情图（109 张）** | **移植** | `master` 仅 9 张 |
| **表情按 CSS 类去重** | **移植** | `master` 每个出现都重复内嵌 base64 |
| **`--per-page` 按条数分页** | **移植** | `master` 只有 `--parts`（按份数） |
| **表情包本地解密** | **移植** | `master` 走 CDN，需联网 |
| **图片缩放管线** | **移植** | `master` 按原始尺寸内嵌 |

### 附带修复：跨分片合并默认未生效

移植过程中发现 `master` 的跨分片能力实际处于休眠状态：`derive_database_key()` 需要
`passphrase` 才会派生分片密钥，而 `exportService.ts` 只从配置的 `favPassphrase` 读取，
`init` 写入的却是 `decryptKey`。两者本是同一个值，但字段名不同，于是口令为空、派生
退回单库密钥，导出静默地停在第一个分片的最后一条消息。

实测：某会话只导出 10 条（止于 2026-04-23），而该会话实际有 20 条（到 2026-09-10）。
已在 `exportService.ts` 增加 `favPassphrase || decryptKey` 回退。

## 并行开发

本次出现的问题：本地克隆停在 `e6713f7`，之后主线积累了 78 个提交，导致在旧基线上
从零实现了主线已有的功能，白做一遍。

**动手前先对齐：**

```bash
git fetch origin
git status -sb          # 看 ahead / behind
git log --oneline HEAD..origin/master | wc -l    # 主线领先多少
```

若 `behind` 不为 0，先 `git checkout master && git pull --ff-only`，再切工作分支。

**合并前自检：**

```bash
git fetch origin
git log --oneline HEAD..origin/master            # 应尽量为空
git merge-tree --write-tree origin/master HEAD   # 预看冲突
```

## 提交与推送约定（2026-11-04）

- **提交可以多，推送要干净**：开发过程中随手提交无所谓；**推送前**把同一件事的多条提交合成一条。
  实测过的问题：一个下午留下 `docs(...)` 三条、`fix(linux)` 四条，事后翻历史看不出哪条对应哪件事。
- **推送要用户点头**：不接受"顺手推一下"。本地提交完就停，等明确指令再推。
- **不改写已发布的历史**：合并已推送的提交需要 `--force`，会让别人已 clone 的分支冲突、
  也会让 issue 里引用过的 commit 链接失效。收益是"历史好看"，代价是协作风险，不划算。
- 若用户明确要求重写：先打 `backup-before-rebase` tag，再用 `--force-with-lease`
  （别人推过就失败，而不是覆盖），并只重写最近 N 条。

## 远程仓库角色与同步流程（2026-11-09）

本仓是**上游的 fork**，两个 remote 各有明确角色，不能混：

| remote | URL | 角色 | fetch | push |
| --- | --- | --- | --- | --- |
| `origin` | `https://github.com/fcmyoo/weflow-cli.git` | **自己的 fork**（推送到这里） | ✅ | ✅ |
| `upstream` | `https://github.com/zhuobichen/weflow-cli.git` | **上游作者原仓**（只读来源） | ✅ | ❌ 已禁用 |

`upstream` 的 push URL 被设为 `DISABLED`（`git remote set-url --push upstream DISABLED`）——
**误 push 到上游会被直接拒绝**，这是比"记在心里"更可靠的保护。

### 各分支跟踪谁

**所有本地分支都跟踪 `origin/*`，不跟踪 `upstream/*`。**

```bash
git branch --set-upstream-to=origin/master master
```

理由：`git pull` 默认拉的是「跟踪分支」。若 `master` 跟踪 `upstream/master`，
一次 `git pull` 就会把**上游作者的历史**混进自己的 fork 主线，而 fork 的主线还带着
本地定制提交——那是个必然出事的组合。拉上游要走显式命令（见下）。

### 同步上游的标准步骤

```bash
# 1) 拉上游最新（fetch 到 upstream/master，不动工作区）
git fetch upstream --prune

# 2) 看上游比自己新了多少
git log --oneline upstream/master..master        # 我领先的
git log --oneline master..upstream/master        # 上游领先的

# 3) 把上游更新并进主线
git merge upstream/master          # 或 git rebase upstream/master

# 4) 推到自己的 fork
git push origin master
```

**推之前必须 squash**（见「提交与推送约定」）：本地开发期间的碎提交要先折叠。

### 判断"某分支是否已并入 master"——用 `git cherry`，不要用 `--merged`

```bash
git cherry master <分支>          # '-' = 内容已在 master；'+' = 独有内容
```

本仓主线被改写过（见文首说明），`git branch --merged` 在这里**失效**——
它依赖祖先链，而改写后的主线与旧分支已无共同祖先。`git cherry` 按 **patch-id** 比对，
不依赖祖先链，才是这仓该用的判据。

### 分支与上游"对齐"的两条路，要选对

某分支落后上游几十个提交、但它的独有内容**已全部在 master 里**时（`git cherry` 全是 `-`）：

- **路 A（rebase）**：把分支重放到 `upstream/master`。分支历史保住，但**在 master 之外
  又造一份同样内容**——两份真源，以后必然有一处漏改。
- **路 B（对齐 master）**：分支直接指向 master 的提交。内容一致、含上游最新、
  **无重复历史**。

**优先路 B**，前提是 `git cherry` 证明独有内容为 0。判断依据是「是否还需要独立演进」：
分支若还要继续做，走 A；若内容已全部并入主线、只当历史留存，走 B。

`backup/*` 分支**不对齐**——它的价值就是「当时的原始状态」，对齐反而抹掉了它的意义。

### 今天踩到的坑：rebase 会把上游历史搬两遍

本地线的祖先还在旧上游基线上时，`git rebase upstream/master` 会把上游那几十个提交
**重新搬运一遍**，在 `CHANGELOG.md`、`package-lock.json`、`package.json` 上连环冲突。

**更稳的做法**：在 `upstream/master` 上直接应用净差异补丁。

```bash
git diff upstream/master master > /tmp/net.patch   # 本地独有的净效果
git checkout -B tmp upstream/master
git apply --index /tmp/net.patch
git commit -m "..."
```

1292 行补丁干净应用、零冲突。**关键验证**：折叠前后树必须逐字节相同——

```bash
git diff <折叠前的HEAD> <折叠后的HEAD> | wc -l      # 必须是 0
```

这一条是折叠历史的安全网：最危险的失败是「提交少了但文件没少」的静默丢失，
而 diff 输出 0 行才能证明内容零丢失。

## 强推与清理

- **禁止对 `master` 强推。** 若推送被拒，一律先 `git fetch` 查明主线新增内容。
- 工作分支合并后应删除本地与远端副本；`draft/*` 保留至内容确认无用为止。
- 确需强推工作分支时，用**显式 lease**锁定远端基线，不要用裸 `--force-with-lease`
  （那会与并发推送打架），更不要用 `--force`：
  ```bash
  git push --force-with-lease=<分支>:<远端当前commit> origin <分支>
  ```
  推之前先 `git tag -f backup-<分支>-<日期> <远端commit>` 留后路。
