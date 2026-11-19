# 发布清单

> npm 侧的**症状对照与省上下文技巧**在 [NPM-PUBLISH.md](NPM-PUBLISH.md)：
> 404 / 409 / ETARGET 到底是失败还是在传播，查那一份，不用通读本文。

npm 包与 GitHub 分开发布，中间隔多久都不奇怪——`1.5.0` 与主线之间曾拉开 128 个提交、
两个多月，直接导致用户照着 README 敲 `init --path` 却报 `unknown option`。

按下面顺序走，每一步都有对应的**失败长什么样**。

## 1. 定版本号

按语义化版本，不是按「改了多少行」：

| 改动性质 | 升哪一位 | 例 |
| --- | --- | --- |
| 新功能、新命令、新参数 | **minor** | 1.5.0 → 1.6.0 |
| 只修 bug、只改文档 | patch | 1.6.0 → 1.6.1 |
| 破坏兼容 | major | 1.6.0 → 2.0.0 |

已备好但**从未发布**的版本号可以并进下一个：`1.5.1` 准备过却没发，
它的修复就直接并进了 `1.6.0`。

**改 `package.json` 的版本号时必须同时改 `package-lock.json`**（lockfileVersion 3 里有两个地方：
顶层的 `version` 与 `packages[""].version`）。**2026-10-13 发 1.11.0 时就是这么红了一次**：包已经发出去、
tag 也打了，`release-consistency` 才报「版本号与锁文件必须一致」—— 我本地只跑了 build/test/文档命令检查，
而这三样都不看锁文件。发布物本身没问题（`files` 白名单里没有锁文件），但 master 的 CI 红了。

```powershell
node -p "require('./package.json').version"     # 两个必须相等
node -p "require('./package-lock.json').version"
```

顺手验一下锁文件没跑偏（只写锁、不动 `node_modules`）：
`npm install --package-lock-only --dry-run` —— 报 `up to date` 即可，那也正是 `npm ci` 会不会炸的判据。

## 2. 整理 CHANGELOG

- 把 `## Unreleased` 改名为 `## <新版本号>`
- 若上一版已写好但没发布，把它的条目并入，删掉那个空段
- 通用说明（如「npm 与 GitHub 分开发布」）移到文件顶部，别留在版本段里
- **每个版本段记录验证过的微信版本**（D-130）：一行 `> 验证过的微信：4.1.15.56（2026-10-26）`，
  来源是 `src/services/wechatCompat.ts` 的 `WECHAT_VERIFIED`——两边必须一致（测试钉着 capabilities 那半）。
  本版完全不涉及微信数据读取的，写 `> 验证过的微信：沿用上一版（本版不涉及微信数据读取）`；
  验证过**新**微信版本的，先实读真实数据再改常量，再写进这里。

## 3. 构建与测试

```powershell
npm run build
npm test
py scripts/check_readme_commands.py          # 文档/脚本调用/capabilities 里写的命令必须真的存在
```

最后那条盯的是**用户先发现的那一类漂移**：`1.5.0` 与主线拉开 128 个提交时，用户照着 README 敲
`init --path` 得到 `unknown option`。它逐级问 CLI 的 `--help`（不复刻命令表），扫**三个手写的命令面**：
**用户文档**的围栏代码块（两份 README + 仓库根那几份 + `docs/*.md`，排除按性质记录过去的
`CHANGELOG.md` 与 `docs/DECISIONS.md` —— 它们会成段引用当时的命令；同一批代码块里
`node …/cli.cjs <命令>`、`npx tsx bin/weflow-cli.ts <命令>` 也算这一面 —— 那是同一套命令换个写法）、
同一批代码块里的
`python|py scripts/x.py --flag` 调用（**不跑脚本**，按 AST 读它们的 `add_argument` 声明表 —— 仓库对
Python 脚本的既定纪律就是不 import 也不运行），以及 `capabilities --json` 里手写的 `cli:` **与 `preview:`**
串（`preview:` 是两阶段约定的第一步 —— 用户与 agent 都按它来，所以它和 `cli:` 一样会被手写漂移；
那个输出的 MCP 那半是从真实注册表派生的，不用管。串里有这个输出自己的记法：`<a|b>` 是"或"、
括号外的 `|` 是"两种写法"、`…` 之后截断 —— 判据会按这个归一化后再验）。`--self-test` 是十三条正对照；
文档命令那一面还有反空扫（缺省语料一定有命令，扫到 0 条就不报通过）。CI 的 `node` job 每次推送都会跑这两步。

> 2026-10-11 它就是这么抓到东西的：`OPERATIONS.md` 教用户敲 `search --no-rerank`，而 CLI 从来没有
> 这个旗标（Python 侧一直有）。只扫两份 README 时它一条也扫不到 —— 所以缺省集合就是"用户文档"。

## 4. 打包检查（**不能跳过**）

```powershell
npm pack --dry-run
```

本地常有 `output/`（几 GB 产出）、`models/`（几 GB 模型）、`_env.json`（含密钥）
这类东西。`package.json` 的 `files` 是白名单，理论上挡住——但**白名单也可能写漏**，
所以逐项确认：

```powershell
npm pack --dry-run 2>&1 | grep -E "npm notice [0-9]" > /tmp/list.txt
grep -E "^output/|^models/|node_modules|\.env|\.db$|\.dat$|secret|credential" /tmp/list.txt
```

**这条命令必须没有输出。** 有输出就停下来查。

同时确认新增文件确实**在**包里（白名单是按目录配的，容易漏）：

```powershell
grep -E "新文件名" /tmp/list.txt
```

这后半句（"**该有的在不在**"）现在有自动判据：`test/pack-contents.test.ts` 跑一次
`npm pack --dry-run --json`，断言 `scripts/` 下每个跟踪文件（`.ts` 开发入口除外）与两份 README 引用的
本地图片都在包里。它防的故障 2026-10-11 在**真实发布物**上量到过：registry 上 `weflow-cli@1.9.0`
的 501 个文件里同时缺 `scripts/data/wechat_emoji.json` 与 `logo.png` —— 前者让 npm 用户导出的
聊天记录里一批表情码原样显示成 `[名字]`，后者让包页面挂着一张裂图。这条测试跟着 `npm test` 跑，
CI 的 `node` job 也在跑；上面那条人工命令留着，用来核对本次**新加**的文件名。

想验到"**装出来真的能跑**"（最强的一道，代价是多装一次依赖）：把包打进临时目录、再装进一个空
prefix，然后从**装好的那一份**里问版本。它盯的是白名单之外的另一半 —— 清单对、运行时不成立的情况。

```powershell
npm pack --pack-destination $env:TEMP
mkdir $env:TEMP\wf-smoke ; cd $env:TEMP\wf-smoke     # 空目录
npm install --no-save --no-audit --no-fund "$env:TEMP\weflow-cli-<版本>.tgz"
node node_modules\weflow-cli\cli.cjs --version
node node_modules\weflow-cli\cli.cjs --help
```

2026-10-11 拿 1.10.0 跑过一遍：`ALL_NAMES = 222`（不是只有原图的 118 —— 那正是上一版缺
`scripts/data/wechat_emoji.json` 时的值）、`[乒乓]`/`[冷汗]`/`[开红包]` 都渲染成对应表情、
`--version` 答 `1.10.0`。

**两个数字口径，免得下一次又花半小时**（2026-10-11 实测）：`npm publish` 的 notice 报
`package size: 12.5 MB` 用的是**十进制 MB**，而 `npm pack --json` 的 `size` 是**字节**（你按习惯除以
`1MB` 得到 11.88 是 MiB）—— **同一份 tarball**：12,460,202 字节 = 12.46 MB = 11.88 MiB。
`unpacked size` 同理（publish 27.9 MB = pack 26.62 MiB，解压实测 27,915,402 字节）。不是
"publish 比 pack 大"，是两套单位。

**发布时那两条 `npm warn publish … errors corrected` 是修饰性的，不是故障。**
`bin[weflow-cli]` 那句「cli.cjs was invalid and removed」措辞很吓人，实际只是把 `./cli.cjs` 规范成
`cli.cjs`；`repository.url` 被改写成 `git+https://…`。**实测证据**：registry 上 `weflow-cli@1.9.0`
的元数据里 `bin = { 'weflow-cli': 'cli.cjs' }` 完好无损（`npm view weflow-cli@1.9.0 bin repository.url`）
⇒ `npm i -g` 装出来的命令入口一直都在，**别据此以为发布物少了 bin**。
（那两条规范化 2026-10-11 已经直接落到仓库里了：`package.json` 的 `bin` 与 `repository.url` 现在就是
规范形式，发布时不再报这两条 warning，仓库也与 registry 上的一致。）

> ⚠️ **`npm pkg fix` 没有 `--dry-run`**：`npm pkg fix --dry-run` 会把 `--dry-run` 当成不认识的参数**忽略掉**，
> 然后**直接改你的 `package.json`**（2026-10-11 就是这么被改的 —— 我原以为只是"看看会改什么"）。
> 想先看结果就用 `git diff package.json` 兜底，别指望它自己停手。

## 5. 发布

需要 npm 凭据。`ribiaosu` 账号开了 2FA，所以两条路：

**凭据（推荐，一次配置长期可用）** — 用**带 `Bypass 2FA` 的 Granular Access Token**，
在 https://www.npmjs.com/settings/ribiaosu/tokens 生成时**必须勾选那个框**。
漏勾的 token 会在发布时报 `403 ... bypass 2fa enabled is required`。

| 现象 | 原因 |
| --- | --- |
| `403 ... two-factor authentication ... required` | token 没勾 Bypass 2FA，或没用 OTP |
| `ENEEDAUTH` | 没登录 / 没配 token |

**验证 token 是否可用**（`bypass_2fa` 必须是 `true`）：

```powershell
curl -s -H "Authorization: Bearer <token>" https://registry.npmjs.org/-/npm/v1/tokens
```

**一次性验证码**：

```powershell
npm publish --otp=<6位码>
```

## 6. 触发国内镜像同步（**新加的必要步骤**）

国内用户大多用 `registry.npmmirror.com`（淘宝源），它对新版本有同步延迟。
不同步的话，发布后几个小时内会有人来提「版本不存在」的 issue——
`weflow-cli@1.6.0` 就这样被报过一次（Issue #8）。

```powershell
curl -X PUT https://registry.npmmirror.com/-/package/weflow-cli/syncs
```

返回 `{"ok":true,...,"state":"waiting"}` 即已受理。**约 5 分钟后**生效。

## 7. 验证两个源都是新版本

```powershell
npm view weflow-cli version --registry=https://registry.npmjs.org
npm view weflow-cli version --registry=https://registry.npmmirror.com
```

两个都要是新版本号才对外宣告。官方源发布后自身也有几分钟 CDN 传播延迟——
期间 `npm view` 可能显示旧版本、tarball 可能 404，**这是在传播，不是失败**。
用这个判断是否真的发出去了：

```powershell
npm publish    # 再发一次
```

报 `409 Conflict - Cannot publish over previously staged version` → **已经发布成功**。

但 409 只说明**版本槽位已被占**，不等于 registry 已经能查到它。发布后 npm 要先扫描再让版本可读，
**官方不承诺上限**：常见 5 分钟，实测出现过 14、16、25、55 分钟。这个窗口里的表现是：

- `npm view weflow-cli version` 还是旧版本号
- tarball 直链仍然 404
- 再发一次仍然是 409 —— 所以 409 **区分不了**「还在传播」和「已经好了」

窗口期内只做一件事：等 + 轮询，**不要改版本号重发**。想确认不是卡在人工审批：

```powershell
npx --yes npm@11.17.0 stage list weflow-cli --json   # 返回 [] 即无待审批版本，属传播中
```

（`npm stage` 需要 npm ≥ 11.15；本机装的是 11.6.2，直接跑会报 `Unknown command: "stage"`，
所以用 npx 临时拉一个高版本。`npm@11.17.0` 是在本机 Node 24.11.1 上验证过能用的。）

## 8. 兜底：告诉用户怎么绕开镜像

```powershell
npm config get registry                                            # 看用的哪个源
npm view weflow-cli version --registry=https://registry.npmjs.org  # 用官方源查
```

两个源结果不一致 → 是镜像同步问题，不是包的问题：
`npm install -g weflow-cli@<版本> --registry=https://registry.npmjs.org` 立刻可用。

## 建 GitHub Release 时的两个坑（2026-10-02 补 1.8.0/1.8.1/1.8.2 时实测）

- **正文上限 125,000 字符。** 1.8.0 那段 CHANGELOG 是 140,154 字符，直接贴会被 API 拒：
  `HTTP 422 ... body is too long (maximum is 125000 characters)`。处理方式是**按 `### ` 小节边界
  机械截断**（放到装不下为止），末尾补一句"完整条目在 `CHANGELOG.md`"。**不要凭"哪几段更重要"去挑**：
  那既不可复现，也不是发布这件事该做的判断。
- **补历史的 Release 要显式 `--latest=false`。** `gh release create` 默认会把新页面标成 Latest ——
  给 1.8.x 补账时会把 1.9.0 从 Latest 上挤下去。补完用 `gh release list` 确认 Latest 还是最新的那个。
- **发布前先确认工作区干净**（`git status` 为空，HEAD 就是你打算打 tag 的那次提交）。
  理由不是形式主义，是实测出来的：2026-10-02 给 1.8.0/1.8.1/1.8.2 补 tag 时做了逐文件溯源
  （拿 registry 上的 tarball 与 `git archive <tag>` 比，行尾差异不算），结果是 1.8.1 与 1.8.2 的
  发布源码与 tag 树**一致**，而 **1.8.0 的包里有两个文档文件在 git 里找不到对应状态**：
  `docs/DECISIONS.md` 与更晚的一次提交一致，`README.en.md` 则**与任何提交都不一致**（比 tag 那份
  长约 7,000 字符）。代码部分没问题（逐字节相同），但**那次发布出去的文档再也回不来** ——
  这正是"不把 npm 发布包当作 GitHub 源码的实时镜像；先确认版本和提交"这条规矩要防的事。
- **1.9.0 的出处核过了（2026-10-11，用上面同一套办法）**：把 registry 上的 tarball 与
  `git archive v1.9.0` 逐文件比（行尾归一后比内容）：两边都有的 **289 个文件全部逐字节一致、0 处不同**；
  包里多出的 212 个是 `dist/`（构建产物、不在 git 里）；tag 里有而包里没有的 124 个是白名单外的
  东西（`.github/`、`AGENTS.md`、`package-lock.json`、`scripts/*.ts` 等）。
  ⚠️ 其中 **`logo.png` 与 `scripts/data/wechat_emoji.json` 两个文件当时确实没进包** —— 那正是
  1.10.0 修掉的缺口（同一件事，两条独立的路都指到它）。
  **结论：1.8.0 那个"发出去的文档再也回不来"没有在 1.9.0 重演** ✅
- **1.10.0 的出处核过了（2026-10-12，发布当天，三种证据）**：
  1. registry 上 `weflow-cli@1.10.0` 的 `dist.shasum` = `bb7604fe063d330360d00f89c19069367c509d7a`，与本机
     `npm pack --dry-run` 报的是**同一个值** ⇒ 发出去的就是我构建的那一份；
  2. 从 registry 装进一个**空目录**（`npm install --no-save weflow-cli@1.10.0`），用**装好的那份**跑
     `cli.cjs --version` 得到 `1.10.0`、`--help` 正常（4128 字节、含 `Commands:`）；同时确认 1.9.0 缺的
     那两个文件这次都在（`logo.png`、`scripts/data/wechat_emoji.json`），而开发用探针
     `scripts/json_surface_probe.mjs` **不在**包里（`.mjs` 不进包是有意的，见 `test/pack-contents.test.ts`）；
  3. 两个源都是 1.10.0：官方源与 `registry.npmmirror.com`（`curl -X PUT …/syncs` 受理后约 **4 分钟**生效）。
  另外**发布日志 0 条告警** —— 下面说的那两条装饰性 normalize 告警确实随 `package.json` 规范化一起消失了。
  **tag 打在被发布的那个提交上**：`v1.10.0` → `9baa752`（发布时 HEAD 就是它；随后只有这一个文档提交
  3cf5605，它不在包里那一份 tarball 的对应关系里 —— 写下来是因为 1.8.0 那次正是"事后不知道 tag 对应哪个
  提交"）。GitHub Release `v1.10.0` 同样指向它，正文由 `## 1.10.0` 段机械切出（92,213 字符，未触及
  125,000 上限 ⇒ 未截断），List 上已是 **Latest**。
- **1.11.0 的出处核过了（2026-10-13，发布当天，四种证据）**：
  1. 本地 `npm pack` 的 shasum = `21db27cbb151987331c7993c625eb4626c23f58a`，与**两个源**上
     `weflow-cli@1.11.0` 的 `dist.shasum` **三者相同** ⇒ 发出去的就是我构建的那一份，且镜像同步的是同一份；
  2. 从 registry（不是本地 tarball）装进一个**空目录**，用**装好的那份**跑 `cli.cjs --version` 得到 `1.11.0`、
     `--help` 72 行含 `Commands:`；1.9.0 缺过的 `logo.png` 与 `scripts/data/wechat_emoji.json` 都在，
     开发用探针 `.mjs` 不在包里（有意，见 `test/pack-contents.test.ts`）；
  3. **本次真正改动的那个脚本逐字节核过**：装好的那份 `scripts/export_chat_html.py` 的 sha256
     = `73117B7A35347D68FE239D660C9A0531E231BA8CA7A0A31F8DA0C07D5D54C041`，与仓库里那份**相同**
     ⇒ 用户拿到的是这一版脚本，不是旧的。这是比"文件在不在"更强的一层：**在，而且是我这次写的那份**；
  4. 两个源都是 1.11.0：官方源与 `registry.npmmirror.com`（`curl -X PUT …/syncs` 受理后，发布后约 1 分钟
     查 shasum 仍取不到、20 分钟内再查时两源都已生效 —— 这次的窗口比 1.10.0 那次（约 4 分钟）长，
     但**没有逐点采样**，所以只记"两次查询的区间"，不写一个假的分钟数）。
  **发布日志 0 条告警**；**tag 打在被发布的那个提交上**：`v1.11.0` → `bd82316`（`821479b` 是功能提交，
  `bd82316` 只改了 `package.json` 版本号与 CHANGELOG 段名）。GitHub Release `v1.11.0` 指向 `bd82316`，
  正文由 `## 1.11.0` 段机械切出（9,689 字符、7 条 bullet、未触及 125,000 上限 ⇒ 未截断），
  建完在 List 上已是 **Latest**（把 1.10.0 挤下去是预期行为）。
  ⚠️ **这次发布本身有一次 CI 失败，记在这里免得事后只看到"一切正常"**：`chore: 1.11.0` 那次提交的
  `release-consistency` 报「版本号与锁文件必须一致」—— 我改了 `package.json` 忘了 `package-lock.json`，
  连红三次（含紧随其后的文档提交）。**发布物不受影响**（白名单里没有锁文件、shasum 与装出来的那份都验过），
  但 master 的 CI 是红的；修在 `f9e55c8`，之后两次推送都是 success。清单第 1 步已补上这条检查。
- **1.11.1 的出处核过了（2026-10-13，发布当天，四种证据）**：这是 1.11.0 发布后**当天**的补丁版
  （文档导出五处修复，详见 CHANGELOG）。
  1. 本地 `npm pack` 的 shasum = `5527b538e3ed5ec49520e14e563a8bff31ee4ea4`，与**两个源**上
     `weflow-cli@1.11.1` 的 `dist.shasum` **三者相同**；
  2. 从 registry 装进空目录，用**装好的那份**跑 `cli.cjs --version` 得到 `1.11.1`，且它的
     `scripts/export_chat_html.py` 与仓库里那份 **sha256 逐字节相同**（并确认含本次新增的
     `_load_existing_manifest` / `_clean_asset_component`）——"在，而且是我这次写的那份"；
  3. 两个源都是 1.11.1：官方源**约 2 分钟**就有了，`registry.npmmirror.com` 约 **5 分钟**（`curl -X PUT …/syncs`
     受理后）；两个源上该版本的 shasum 也一致；
  4. **版本号三处一起改**（`package.json` + `package-lock.json` 的顶层 `version` 与 `packages[""].version`），
     所以这一次 `release-consistency` 没有再红 —— 1.11.0 那次就是漏了锁文件。
  **tag 打在被发布的那个提交上**：`v1.11.1` → `888d8fc`（只改了 `package.json`/`package-lock.json`/CHANGELOG
  段名，功能提交是 `2b03b4f`）。GitHub Release `v1.11.1` 指向它，正文由 `## 1.11.1` 段机械切出
  （3,154 字符、1 条 bullet、未触及上限），建完是 **Latest**。


## 发布后

- 回到相关 issue 回复「已发布 + 升级方式」
- GitHub Release 与本清单解耦：源码、编译包、npm 三者版本差异要对用户可见

### 回复用户的规矩

报障的人多半不是开发者，也不看长文。**回复写给用户，不是写给同事：**

- **先给出结论和做法**（已修复 / 升级到 x.y.z），技术细节不要写进回复
- **三五句话说完**。根因分析、代码片段、执行顺序论证属于 commit message 和
  本文档，不属于 issue 回复
- 升级类回复直接给命令，别让人自己找
- **发出前先给维护者过目**，确认后再发

反面例子：一条回复里贴了 Python 代码片段和逐步的执行顺序分析，内容全对，
但给用户的感受是「看不懂、太长」。

#### 回 PR / 贡献者时，多两条

读者是开发者，diff 已经给了代码，所以**不解释代码**；要写的是 diff 里看不出来的东西
（这条会不会伤到别人、伤到谁），加上对方看不到的事实（例如"已发布包里其实有这个文件"
配一行核对命令）。在此之上：

- **不写元叙述**：不解释"我为什么这样写""下面只说什么"。
  ✗ 这里只说 diff 里看不到的 / 细节见提交，我长话短说 / 我尽量精简
- **显而易见的不写**：能意会的不点明，写成明文反而像把对方当看不懂。
  ✗ "你的机器是 A 布局，所以没触发"（路径列表已经说明一切）
- **不复述对方的话**，包括他的自我评价（"你说代码是 AI 写的…"）——直接跳到结论
- 一句"谢谢"或一句"诊断很准"就够，不铺铺垫；"如果需要我可以…"除非真打算做

一条 PR 评论 300 字上下为上限。超了先删元叙述，再删铺垫，最后才删事实。

### 新动态邮件提醒

`scripts/watch_issues.py` 轮询本仓库的 issue 与回复，有非维护者发言时发邮件。
由 Windows 计划任务 `WeFlow Issue Watch` 每 30 分钟调用一次，不依赖任何编辑器
或 Claude 会话处于打开状态。

邮件凭据放在仓库外，不进版本库：

```json
// ~/.weflow-issue-watch.json   （QQ 邮箱需用「授权码」，不是登录密码）
{
  "smtp_user": "1473517806@qq.com",
  "smtp_auth": "<授权码>",
  "mail_to":   "1473517806@qq.com",
  "repo":      "zhuobichen/weflow-cli"
}
```

- 首次运行只记录基线、不发信，避免把历史 issue 一次性全发出去
- 发送失败不推进记录，下次自动重试
- 手动跑一次：`py scripts/watch_issues.py`
- 查看/改频率：`Get-ScheduledTask -TaskName 'WeFlow Issue Watch'`

**两个任务怎么建**（这一节此前只写"交给计划任务"，**命令本身没记下来过** —— 而 `scripts/health_check.py`
正是按这两个**名字**判红的，所以缺的从来不是判断，是这三行）：

```powershell
schtasks /create /tn "WeFlow Issue Watch" /sc minute /mo 30 ^
  /tr "py \"<仓库路径>\scripts\watch_issues.py\""
schtasks /create /tn "WeFlow Mail Watch" /sc minute /mo 30 ^
  /tr "py \"<仓库路径>\scripts\watch_mail.py\""
```

- 查看：`Get-ScheduledTask -TaskName 'WeFlow *'` ／ 撤销：`schtasks /delete /tn "<任务名>" /f`
- **PATH 不用操心**（计划任务的 PATH 与交互式会话不同，这是这类任务常见的翻车点）：`watch_issues.py`
  自己带兜底 —— `GH_CANDIDATES = ['gh', r'C:\Program Files\GitHub CLI\gh.exe']`；`py` 是系统启动器
  （`C:\Windows\py.exe`）。两者都不依赖任务继承的 PATH。
- `watch_mail.py` 做的是**另一件事**：把白名单来信追加进本地队列 `~/.weflow-mail-queue.jsonl`
  （不读别人的信、不发信；真正按指令做事要等一个会话在跑）。

### 发布后自检

```powershell
npm view weflow-cli version --registry=https://registry.npmjs.org
npm view weflow-cli version --registry=https://registry.npmmirror.com
```

两个源都是新版本，再对外说「已发布」。

然后跑一遍体检，确认这次改动没有把读取路径或某个导出格式弄坏：

```powershell
py scripts/health_check.py
```

退出码 `0` 才算干净。检查项与各自的由来见 [HEALTH-CHECK.md](HEALTH-CHECK.md)。
