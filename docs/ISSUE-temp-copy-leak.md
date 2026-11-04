# 问题报告：只读命令在系统临时目录留下未清理的解密副本（~1GB/次）

> 状态：**待提交上游**（`zhuobichen/weflow-cli`）。GitHub connector 对本机 token 无上游/fork 的 issue 写权限（403 Resource not accessible by integration），故先在仓库内留档。
> 记录时间：2026-11-04
> 报告人：一次真实的 AI agent 监听任务的轮询脚本设计者

## 一、现象

`weflow-cli messages` / `export` / `sessions` / `sns timeline` 等**只读**命令，每次调用都会在 `%TEMP%` 下创建一个 `weflow-ro-<random>/` 目录，内含解密后的 `message_N.db` 副本（单个 0.3~1 GB，取决于目标分片大小）。

该目录 **在进程退出后不会被删除**。

因此任何**轮询式使用**（脚本 / 定时任务 / AI agent 监听）都会以极高的速率填满系统盘。

## 二、真实事故记录

环境：Windows 11 / Node v22.22.2 / weflow-cli v1.21.5 / Python 3.13.14 + sqlcipher3 / 微信 4.x（数据目录 `D:\bak\wx\xwechat_files`）

| 环节 | 事实 |
|---|---|
| 触发方式 | 45 秒轮询的监听脚本，运行约 15 小时 |
| 累积量 | `weflow-ro-*` 目录 **1220 个，合计 267.44 GB** |
| 终局 | C 盘可用空间 **0 GB** |

### 后果链

1. C 盘写满 → 微信客户端写盘失败
2. 微信在 `db_storage/message/` 下**新建了分片 `message_6.db`**（原 `message_0.db` 主文件时间戳停在 3 月 19 日）
3. `weflow-cli messages <talker>` 开始返回 `这个 ID 既不在会话里、也不在联系人里`，**所有会话全部读不出来**
4. `weflow-cli check --report` 只能报「微信客户端没有在运行」——密钥与配置都显示正常，**排查成本极高**
5. 连 WorkBuddy 自身数据库都写不进去（`database or disk is full`，导致 automation 更新失败）

**关键结论：清理 `weflow-ro-*` 后，磁盘占用与读取功能立即全部恢复正常，无需重新 `init`。**
密钥并未失效——纯粹是磁盘满导致微信分片重建 + 临时目录污染共同造成的读取失效。这一点对后续排查很关键：看到「所有会话读不出来」时，**先查磁盘空间**。

## 三、期望行为

1. **进程退出时自动清理**本次创建的 `weflow-ro-*` 临时目录（在 `finally` 中删除即可）。
2. 或者提供 `weflow-cli cache clean` / `weflow-cli cleanup` 命令，一键清除遗留的 `weflow-ro-*`。
3. 至少应在文档中明确提示：**不要用短间隔轮询**，并说明临时副本的占用量级。

## 四、复现

```powershell
# Windows
for ($i=0; $i -lt 20; $i++) { weflow-cli messages <wxid> -n 5 --json | Out-Null }
Get-ChildItem "$env:LOCALAPPDATA\Temp" -Directory -Filter "weflow-ro-*" | Measure-Object
# → 20 个目录，约 10~20 GB
```

## 五、临时规避（本机已验证有效）

调用前把 `TEMP`/`TMP` 指向**非系统盘**，调用后删除该目录下的 `weflow-ro-*`：

```python
os.environ["TEMP"] = r"D:\weflow-tmp"
os.environ["TMP"]  = r"D:\weflow-tmp"
os.environ["TMPDIR"] = r"D:\weflow-tmp"
# ... 调用 weflow-cli ...
# finally: shutil.rmtree(D:\weflow-tmp\weflow-ro-*, ignore_errors=True)
```

若上游能内置自动清理，这条规避就不必要了。

## 六、参考实现

本机 `E:/data/WorkBuddy/wechat-exports/watch_1103.py`（v2）已实现上述规避：调用前设 D 盘临时目录、`finally` 清理、启动时清理 C 盘遗留。
