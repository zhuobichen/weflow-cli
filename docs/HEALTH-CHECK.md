# 定期体检

## 为什么是脚本，不是清单

人工清单有两个必然结局：要么没人跑，要么跑了也没人愿意逐条读输出再判断。
所以体检做成 `scripts/health_check.py`——**纯脚本，零 token**，退出码即结论。

每一项检查都对应一个**真实发生过、且当时看不出来**的故障。没有对应故障的检查
不往里加，否则报告会变长到没人看。

## 跑一次

```powershell
py scripts/health_check.py          # 人读的报告
py scripts/health_check.py --json   # 机器读
```

退出码 `0` = 全部通过，`1` = 有失败项。报告长这样（版本号、日期、字节数都是
当次运行的真实值，仅作示例）：

```
WeFlow 健康检查 · 2026-09-19 10:04

[OK  ] 版本一致                      CLI X.Y.Z / package X.Y.Z
[OK  ] 环境自检                      已初始化=True 消息库=True
[OK  ] Python 依赖                  齐全
[OK  ] 会话新鲜度                     最新会话 2026-09-19 10:03（0.0 天前）
[OK  ] 读取路径一致 (wxid_6xx1ehe4h…)  json 最新 2026-09-19 10:03 / html 最新 2026-09-19 10:03:12
[OK  ] 导出格式 json                 938 字节
[OK  ] 导出格式 txt                  145 字节
[OK  ] 导出格式 excel                6788 字节
[OK  ] 计划任务                      均在位且上次成功

全部通过（9 项）
```

全程约 1 分钟，其中大部分是 `export html` 扫描媒体索引的固定开销。

## 检查项与它们防的故障

| 检查 | 防的是什么 |
| --- | --- |
| **版本一致** | `1.6.0` 的 `--version` 写死成 `1.5.1`，导致「我到底跑的哪个版本」无法回答，Issue #8 的诊断因此卡住 |
| **环境自检** | 未初始化 / 缺 Python 依赖时，命令报的是「数据库连接失败」，指向错误的方向 |
| **Python 依赖** | `nt_decrypt.py` 是子进程，缺 `sqlcipher3` 的表现和密码错一模一样 |
| **会话新鲜度** | 读取只覆盖部分分片时，会话列表停在十几天前，而每条命令都返回「成功」 |
| **读取路径一致** | 同一会话，`export json` 走 NT 路径、`export html` 走 Python 路径。二者曾差 1114 条消息——json 只读到第一个分片 |
| **导出格式 json/txt/excel** | `excel` 曾**从未成功过**，报错只有「导出失败」四个字 |
| **计划任务** | 提醒脚本静默停跑，没有任何人会知道 |

### 「读取路径一致」为什么用时间戳而不是条数

条数会受分页、去重、日期过滤影响，两个路径本来就可能差一点。**最新一条消息的时间**
是确定的：两条路径读同一个会话，如果连最新消息都不是同一条，那就是有一边没读到。

这一项正是分片故障的探测器。把分片合并关掉后它立刻报：

```
[FAIL] 读取路径一致 (47966561144@ch…)  json 最新 2026-08-30 15:30 / html 最新 2026-09-17 23:02:23
```

### 「会话新鲜度」的阈值

默认 7 天（脚本顶部 `STALE_SESSION_DAYS`）。不是每个人都天天有微信消息，所以故意
放宽；但如果连最近 5 个会话都超过 7 天没动，基本可以断定读取出了问题，而不是没人聊天。

## 怎么让它定期跑

和 `WeFlow Issue Watch`、`WeFlow Mail Watch` 一样交给计划任务，不依赖任何编辑器或
Claude 会话开着：

```powershell
schtasks /create /tn "WeFlow Health Check" ^
  /tr "py \"<仓库路径>\scripts\health_check.py\"" ^
  /sc daily /st 09:07
```

- 查看：`Get-ScheduledTask -TaskName 'WeFlow Health Check'`
- 手动跑：`Start-ScheduledTask -TaskName 'WeFlow Health Check'`
- 上次结果：`schtasks /query /tn "WeFlow Health Check" /fo LIST /v`

> 体检脚本自身也在检查计划任务，所以它挂了会在下一次运行时报出来（前提是它还能被
> 触发——所以别把它的触发条件设成「上次成功才跑」）。

## 目前已知、但**未修**的问题

体检脚本覆盖不到这些；它们需要人判断，所以留在这里而不是塞进自动检查。

| 问题 | 现象 | 为什么没自动修 |
| --- | --- | --- |
| 未知 talker 的退出码不一致 | `messages <拼错的wxid>` 退出 0 且 `success:true`；同样输入 `export` 退出 1 | 改退出码会动到 MCP 与脚本的既有契约，应由维护者定 |
| `whitelist list` / `blacklist list` 空列表 | 只输出一个换行，没有「为空」提示（`--json` 正常） | 纯观感问题 |
| `dashscopeApiKey` 无法通过 `config set` 写入 | 脚本会读它，但它不在 CLI 可写白名单里；只能给 `--api-key` 或环境变量 | 需要决定是否扩大密钥配置面 |
| 朋友圈/收藏密钥缺失 | `fav list` 曾把「没配密钥」报成「需 4.x NT 连接」——已修，现在会明确说缺密钥 | — |

## 相关文档

- 发布流程与发布后自检：[RELEASING.md](RELEASING.md)
- 数据契约：[DATA_CONTRACT.md](DATA_CONTRACT.md)
