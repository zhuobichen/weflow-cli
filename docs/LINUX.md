# Linux 版微信怎么导出聊天记录

> 最后核对：2026-11-05，Ubuntu 24.04 / Python 3.12 / Node 22，
> 微信 Linux **4.1.13.26**（真实登录态、真实数据目录）。

## Linux 版微信能导出聊天记录吗？

**能。** 支持 HTML / Markdown / JSON / TXT / Excel 五种格式：

```bash
weflow-cli export "联系人A" html        # 也可 markdown / json / txt / excel
```

前提两条：微信 Linux 4.x（本文实测 4.1.13.26），以及**一次登录授权**拿到那串共享口令
（见下文「怎么拿到口令」）。导出产物落在 `~/.weflow-cli/output/`，不写回微信目录。

## 微信 Linux 4.x 的数据库怎么读取？

4.1.12.26 之后微信改用「**共享口令 + 每库 salt 派生**」，内存里不再有每库一对 key/salt。
所以流程是：拿口令 → 派生各库密钥 → 逐库校验 → 之后直接读。

```bash
weflow-cli capture-passphrase    # 一次登录授权，自动拿口令并逐库校验
weflow-cli init                  # 派生各库密钥，写进配置
weflow-cli sessions              # 验证：能列出会话才说明真的读到了
```

**对微信数据库一律只读**：没有 `-wal` 时以 `immutable` 直读，有 `-wal` 时读临时副本；
两条路径都不在微信的库旁写 `-wal` / `-shm`，也不改变客户端状态。

## Linux 上需要 root 吗？

**读库、查询、导出、日报全都不需要。** 只有**捕获口令**那一步要 attach 到正在运行的
微信进程，普通用户在 Yama 限制下会被拒——工具会自动用 `sudo` 重试一次，并在输出里说明。

不想让本工具提权的话：

```bash
weflow-cli capture-passphrase --no-sudo   # 被拒就直接失败，不会尝试提权
```

它只会执行一次 `gdb -p <微信进程>`（读一个寄存器），不写微信目录、不改系统配置。
已经拿到口令的环境完全不必跑这一步。

## 导出后数据存在哪里？

输出根**必须可写**，按优先级：`WEFLOW_OUTPUT_DIR` → 包目录下的 `output/`（可写时）
→ `~/.weflow-cli/output`。全局安装时包目录属于 root，所以实际落在后者：

```
~/.weflow-cli/output/biz-daily/<日期>/    日报（index.html / article.html / 分类目录）
~/.weflow-cli/output/wechat-vault/        Vault（002_Literature/WeChat/<日期>/）
~/.weflow-cli/output/knowledge-graph-3d.html
~/.weflow-cli/output/chat-notes/          会话卡片（compile_wiki 读它）
~/.weflow-cli/output/article-notes/       文章卡片
~/.weflow-cli/output/wechat-media/        入站媒体落地
~/.weflow-cli/output/imports/qq/          import qchat 的存储
~/.weflow-cli/output/.cache/              抓取缓存
```

用 `python3 scripts/nt_decrypt.py key-status` 或 `weflow-cli check` 都能看到当前配置能否读库。

## Linux 上还能做什么？

| 能力 | Linux | 说明 |
| --- | --- | --- |
| `check` 环境检查 | ✅ | 依赖、目录、配置、以及**能读几个库**都会报 |
| `init` 目录发现 / 账号扫描 | ✅ | 微信 Linux 的 `~/xwechat_files` 能自动认出 |
| 进程识别 | ✅ | 只认 `wechat`/`weixin`（含 `com.tencent.wechat` 这类前缀） |
| 版本探测 | ✅ | 读客户端自己的记录 `~/.xwechat/uh/uh.dat`（没有 WMI/模块表可查）；读不到时明说"版本未知"，不会假报"微信没在运行" |
| 读本地微信库 | ✅ | 有口令即可读到真实数据：`sessions` / `messages` / `daily` 实测通过 |
| 公众号日报（本地库或外部源） | ✅ | 本地库直读；没有口令时可用 `--articles-json` / `--rss-feed` / `--rss-opml` |
| 定时任务 | ✅ | `scripts/daily_task.sh`，cron / systemd timer 都能调 |
| 知识库 / MCP / 导出 | ✅ | 不依赖 Windows |
| 日报 HTML（index/article） | ✅ | 2026-11-01 实测产出 30KB + 65KB |
| 双向链接 / 阅读笔记 | ✅ | `enrich_backlinks`、`create_reading_notes` 实跑通过 |
| 概念图谱 `wiki graph` | ✅ | 3D 与 `--flat` 2D 都实跑通过 |

## 怎么拿到口令（一条龙）

```bash
weflow-cli capture-passphrase
```

它会：找进程 → 算断点地址 → 挂调试器 → 等你在微信里**退出登录、再重新登录** →
从派生那一步读出口令 → 用真实库逐个校验 → 写进配置。

口令只在登录时派生一次，所以必须有一次重新登录。校验标准是**库首页的 HMAC**，
不是"看起来像"。已验证：微信 Linux 4.1.13.26（Ubuntu 24.04），21 个库中 20 个通过。

版本没验证过时它会明确报错并给出反馈命令，而不是静默失败。

## 拿不到密钥时的两条正路

### 1. 你自己提供口令（能读本地库）

微信 4.1.12.26+ 改用「共享 passphrase + 每分片 salt 派生」，所以只要拿到口令就能开库。
拿到之后**先验一遍**，再配进去：

```bash
# 这个口令能解开几个库？（不依赖 sqlcipher3，只看首页 HMAC）
python3 scripts/nt_decrypt.py verify-passphrase-all --passphrase <64位hex>

# 配置并让 init 派生各库密钥
weflow-cli fav set-key --passphrase <64位hex>
weflow-cli init
```

任何时候想知道"现在能不能读"：

```bash
python3 scripts/nt_decrypt.py key-status
```

### 2. 不碰本地库，走外部源（不需要任何密钥）

主命令原生支持（2026-11-01 起；此前这三个旗标只在 `scripts/pipeline.py` 里，主命令传了会报
`unknown option`，而外部源正是 Linux 无密钥用户的唯一来源）：

```bash
weflow-cli daily --date 2026-11-01 --articles-json articles.json
weflow-cli daily --date 2026-11-01 --rss-feed https://example.com/feed.xml
weflow-cli daily --date 2026-11-01 --rss-opml subs.opml
```

同一条链路也能用脚本跑：

```bash
# 订阅源：RSS/Atom 列表、OPML、或任意来源整理好的 JSON
python3 scripts/pipeline.py --date 2026-11-01 --rss-feeds-file output/rss-feeds.json
python3 scripts/pipeline.py --date 2026-11-01 --articles-json /path/to/articles.json --no-ai
```

产物默认落在 `~/output/biz-daily/<日期>/`（`index.html` / `article.html` / `.articles.json`）。

## 依赖：先装齐 requirements

```bash
pip install -r requirements.txt        # 或 python -m pip install --user -r requirements.txt
```

`requirements.txt` 里的跨平台硬依赖：`sqlcipher3`、`zstandard`、`cryptography`、
`pycryptodome`、`html2text`、`numpy`、`pillow`、`requests`。
（`pillow` 服务图片/头像/PDF/表情等 6 个脚本，`requests` 服务面板抓取——
这两项之前不在清单里，缺了只会让**对应命令**在运行时才报 ImportError。）

`weflow-cli check` 会把依赖分两组报出来：必需项缺了算红，可选依赖只提示安装命令：

```
可选依赖（按功能需要，缺了对应命令会提示安装）:
  fitz                 ○ (pip install pymupdf)
```

## 开机自启（systemd，可选）

仓库带了用户级 systemd 单元（`deploy/systemd/`）：助手常驻 + 日报定时器。

```bash
./deploy/systemd/install.sh            # 只装
./deploy/systemd/install.sh --enable   # 装好并启用
```

它会把你**仓库的实际路径**替换进单元文件再写到 `~/.config/systemd/user/`——
systemd 的 `ExecStart=` / `WorkingDirectory=` 不支持环境变量展开，路径必须写死。

两个单元的关键设计：

| 单元 | 要点 |
| --- | --- |
| `weflow-assistant.service` | 前台跑 `assistant run`（**不是** `assistant start`：那是自己 fork 的托管方式，会和 systemd 抢进程管理）；`KillSignal=SIGTERM` 触发程序里的有序关闭 |
| `weflow-daily@.service` | 模板单元，`%i` 就是 `daily_task.sh` 的模式（`noon` / `night`），一个文件覆盖两种 |

`weflow-daily.timer` 的 `OnCalendar` 与 crontab 一致（13:00 / 23:30），
`Persistent=true` 让关机错过的那次在开机后补跑。

在 Ubuntu 24.04 上用 `systemd-analyze verify` 校验过三个单元（OK）；
`daily_task.sh` 需存在且可执行（npm 包里已包含 `scripts/**/*.sh`）。

## 定时任务

```bash
scripts/daily_task.sh              # catchup：补昨天
scripts/daily_task.sh noon         # 午报：跑今天
scripts/daily_task.sh night        # 日终：跑今天（20 点前调用自动降级为 catchup）
```

crontab（北京时间 13:00 / 23:30）：

```cron
0 13 * * *  cd /path/to/weflow-cli && ./scripts/daily_task.sh noon
30 23 * * * cd /path/to/weflow-cli && ./scripts/daily_task.sh night
```

标记文件与 Windows 版同名（`.noon_done` / `.night_done` / `.catchup_done`），两者可共用输出目录。

## 权限：读进程内存需要提权

```bash
sudo setcap cap_sys_ptrace=ep $(readlink -f $(which python3))
```

没提权时错误信息会明说 `PERMISSION_DENIED`，而不是"微信没在运行"。

## 为什么自动提取在 4.1.13.26 上不成立

这不是"扫得不够深"，是方法本身不适用：

- 旧实现找的是内存里 `x'<64位key><32位salt>'` 这种**成对字面量**；
- 4.1.12.26+ 用的是**共享口令 + 每分片派生**，内存里没有"key/salt 成对"这种东西。

已经排除的路径（都有阳性对照，记录在 `docs/VERSION_METHODS.md` 的 M-11～M-16）：
Windows 凭据复用、设备标识（machine-id / 设备 id / wxid）派生、AES 解 `key_info` blob、
内存里的 ASCII 十六进制、UTF-16 十六进制。

`xwechat_files/all_users/login/<wxid>/key_info.db` 是**明文 SQLite**
（表 `LoginKeyInfoTable`，每行 180 字节 blob），但 blob 是封装过的，
封装密钥看起来来自登录态，离线不可派生。

## CI 里怎么守

Ubuntu 上有四个 job，其中两个直接覆盖 Linux 兼容：

| job | 守什么 |
| --- | --- |
| `node` | TS 单测 + README 命令面 |
| `python` | 1351 项 Python 测试（含合成加密库的读取链路） |
| `linux-write-path` | **包目录只读**时能否运行：复制一份干净包目录并设只读，断言输出根回退到 `$HOME`；再用正对照确认"包目录可写时留在原地" |
| `release-consistency` | 版本号、锁文件、npm 包内容 |

数据目录发现的边界也有测试（在 Ubuntu 上真跑）：Flatpak / Snap / 未预料 app id、
**符号链接**（软链到另一块盘）、**同机多账号**、**中文+空格的家目录**，以及空目录的反例。

`linux-write-path` 的存在理由：这类 bug（输出/缓存/vault 写进包目录）在**可写的 checkout 里永远不会红**，
只有装到 Linux 才暴露。它的判据是"造出不可写的安装"，不是"在可写环境里跑一遍"。

## 从 Windows 迁移配置时：`check` 会指出"数据目录在本机不存在"

把 Windows 的 `config.json` 直接拷到 Linux（路径全是 `C:\…`）时，以前 `check` 照样报
`✓ 已初始化 — 可以运行 weflow-cli`——判据只看"配置里有没有 key"。现在会区分：

```
配置状态:
  ○ 已配置，但数据目录在本机不存在
    可能是从另一台机器拷来的配置（例如 Windows 的 C: 路径）。
    在本机重新运行: weflow-cli init
  数据可读性: 没找到可读的数据库（检查数据目录路径与权限）
```

同时修了一处同类假错误：**一个库都没找到**时不再说"密钥可能不对"
（那是"找不到库"与"密钥不对"两种不同的原因）。

## 目录被设成只读时：明确失败，不假装成功

多人共用的机器上，`~/.weflow-cli` 可能被设成只读（或由 root 管理）。此时：

```
$ weflow-cli config set aiModel x
✗ 配置没有保存成功: 配置没有写进磁盘（…/config.json）：EACCES: permission denied, open '…/config.json.tmp'
  常见原因：~/.weflow-cli 目录不可写（权限/只读挂载），或磁盘已满。
$ echo $?
1
```

两个细节值得记：

- 以前 `save()` 只打一行日志，调用方照样打印 `✓ 已设置`——**假成功**。现在保存失败会
  抛错并让 CLI 以 1 退出。
- "保存时顺手收紧目录权限"只在**目录仍允许 owner 写**且带组/其他位时才做。
  如果用户刻意 `chmod 555` 去掉写位，工具**不会**把它改回可写，也不动它的权限位
  （实测失败后目录仍是 `dr-xr-xr-x`）——否则那等于无视用户的意图。

## 状态文件的权限：600 / 700

Linux 上按默认 umask，配置与状态文件会落成 644、目录 755——**同机其他用户可读**，
而 775 的目录甚至允许同组用户替换里面的 `config.json`（里面有凭据）。

现在保存配置时会：

- `~/.weflow-cli` → `drwx------`（700）。注意 `mkdir` 的 mode 只在**新建**时生效，
  所以每次保存都会对已存在的目录补一次 `chmod`（仅 POSIX；Windows 上 chmod 只映射只读位，不做）。
- `config.json` → `-rw-------`（600），经 `writeFileAtomic(..., { mode: 0o600 })`。
- 助手记忆 / 场景 / 审计与 trace 文件同样 600，目录 700。

Linux 实测（保存前 775 → 保存后）：

```
drwx------  ~/.weflow-cli
-rw-------  ~/.weflow-cli/config.json
```

## 权限：库打不开时会明确说"打不开"，不会说成"口令错"

Linux 上文件属主/权限比 Windows 更容易踩。这两类错误被刻意分开了：

| 场景 | 输出 |
| --- | --- |
| 一个库都没找到（路径错/目录不可读） | `key-status` → `verdict: no-databases`；人类可读输出 `未找到可读的数据库（检查数据目录路径与文件权限）` |
| 库找到但打不开（属主/chmod/被独占） | `[ERROR] 无法打开订阅号数据库: <路径>` + `OperationalError: unable to open database file` + 权限排查提示，退出码 1 |
| 密钥真的不对 | `verdict: mismatch`，提示"口令/密钥与这些库都不匹配" |

之前的实现把前两种都报成 `mismatch`（"口令不对"），而 `daily` 直接抛 traceback——
排查时很容易往错的方向找。

## 一个踩过的坑：缺 `zstandard` 会伪装成"今天没文章"

订阅号消息体是 zstd 压缩的。缺这个依赖时 `extract_article_info()` 抛 ImportError，
被取数循环的 `except: continue` 吞掉，输出就是**"找到 0 篇文章"**——看起来像"今天没有更新"。

现在 `biz_daily` 会在正式取数前显式检查并 `exit 1`：

```
[ERROR] 缺少 zstandard：订阅号消息体是 zstd 压缩的，没它会把"解析不了"当成"今天没文章"。
        安装: pip install zstandard   （或 python -m pip install --user zstandard）
```

按 `requirements.txt` 装齐依赖即可避免。


## 相关文档

- 方法×版本对照表：[VERSION_METHODS.md](VERSION_METHODS.md)
- 决策与理由：`DECISIONS.md` 的 D-140 / D-144 / D-145
- 操作手册：[../OPERATIONS.md](../OPERATIONS.md)
