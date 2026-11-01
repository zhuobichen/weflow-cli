# Linux 上的 WeFlow：能用什么、不能用什么

> 最后核对：2026-11-01，Ubuntu 24.04 / Python 3.12 / Node 22，
> 微信 Linux **4.1.13.26**（真实登录态，真实数据目录）。

## 一句话结论

**除"自动获取数据库密钥"外，其余链路在 Linux 上都可用**；拿到凭据后读库也是通的。
拿不到密钥时，用外部订阅源仍能出完整日报。

## 能力矩阵

| 能力 | Linux | 说明 |
| --- | --- | --- |
| `check` 环境检查 | ✅ | 依赖、目录、配置、以及**能读几个库**都会报 |
| `init` 目录发现 / 账号扫描 | ✅ | 微信 Linux 的 `~/xwechat_files` 能自动认出 |
| 进程识别 | ✅ | 只认 `wechat`/`weixin`（含 `com.tencent.wechat` 这类前缀） |
| 版本探测 | ❌ | **只实现了 Windows**；Linux 上会明说"探测不了"，不再假报"微信没在运行" |
| 读本地微信库 | ⚠️ | **有凭据就能读**（已用合成库+真实库验证）；自动提取拿不到 |
| 公众号日报（外部源） | ✅ | `--articles-json` / `--rss-feed` / `--rss-opml`，端到端跑通 |
| 定时任务 | ✅ | `scripts/daily_task.sh`（cron / systemd timer 都能调） |
| 知识库 / MCP / 导出 | ✅ | 不依赖 Windows |
| 日报 HTML（index/article） | ✅ | 2026-11-01 实测产出 30KB + 65KB |
| 双向链接 / 阅读笔记 | ✅ | `enrich_backlinks`、`create_reading_notes` 实跑通过 |
| 概念图谱 `wiki graph` | ✅ | 3D 与 `--flat` 2D 都实跑通过 |

## 产物在哪里

输出根**必须可写**，按优先级：`WEFLOW_OUTPUT_DIR` → 包目录下的 `output/`（可写时）
→ `~/.weflow-cli/output`。全局安装时包目录属于 root，所以实际落在后者：

```
~/.weflow-cli/output/biz-daily/<日期>/    日报（index.html / article.html / 分类目录）
~/.weflow-cli/output/wechat-vault/        Vault（002_Literature/WeChat/<日期>/）
~/.weflow-cli/output/knowledge-graph-3d.html
~/.weflow-cli/output/.cache/              抓取缓存
```

用 `python3 scripts/nt_decrypt.py key-status` 或 `weflow-cli check` 都能看到当前配置能否读库。

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

```bash
# 订阅源：RSS/Atom 列表、OPML、或任意来源整理好的 JSON
python3 scripts/pipeline.py --date 2026-11-01 --rss-feeds-file output/rss-feeds.json
python3 scripts/pipeline.py --date 2026-11-01 --articles-json /path/to/articles.json --no-ai
```

产物默认落在 `~/output/biz-daily/<日期>/`（`index.html` / `article.html` / `.articles.json`）。

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

`linux-write-path` 的存在理由：这类 bug（输出/缓存/vault 写进包目录）在**可写的 checkout 里永远不会红**，
只有装到 Linux 才暴露。它的判据是"造出不可写的安装"，不是"在可写环境里跑一遍"。

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
