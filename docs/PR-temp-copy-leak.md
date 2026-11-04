# PR 草稿：fix(nt_decrypt) 只读副本用完即删，修复轮询填满系统盘

> **目标仓库**：`zhuobichen/weflow-cli`（分支 `fix/temp-copy-leak`）
> **已推送分支**：`git push origin HEAD:refs/heads/fix/temp-copy-leak`（到 fork `fcmyoo/weflow-cli`）
> **提交**：`9a11ef8` fix + `512087b` docs
> **PR 链接**：https://github.com/fcmyoo/weflow-cli/pull/new/fix/temp-copy-leak
>
> GitHub connector 对上游仓库无 PR/issue 写权限（403 Resource not accessible by integration），
> 因此 PR 正文存于此，手动提交时直接复制。

---

## 标题

```
fix(nt_decrypt): 只读副本用完即删，修复轮询填满系统盘导致数据库读取失效
```

## 正文

### 问题

`connect_readonly()` 在目标库存在 `-wal` 时，会把「库 + `-wal` + `-shm`」**完整拷贝**到临时目录再读副本（`tempfile.mkdtemp(prefix='weflow-ro-')`）。

这些副本：

1. 单个 0.3~1 GB（取决于目标分片大小）
2. 只被 append 到模块级 `_CHROME_TMPDIRS` 列表，**从未被清理**
3. `tempfile.mkdtemp()` 未指定 `dir`，走 `tempfile.gettempdir()` —— 该值在进程内会缓存首次取值，设 `TEMP` 环境变量未必可靠

因此**任何轮询式使用**（脚本 / 定时任务 / AI agent 监听）都会以极高速度填满系统盘。

### 真实事故

Windows 11 / Node v22.22.2 / weflow-cli v1.21.5：

| 环节 | 事实 |
|---|---|
| 触发方式 | 45 秒轮询的监听脚本，运行约 15 小时 |
| 累积量 | `weflow-ro-*` 目录 **1220 个，合计 267.44 GB**（C 盘可用空间归零） |

后果链：

1. C 盘写满 → 微信客户端写盘失败
2. 微信在 `db_storage/message/` 下**新建分片 `message_6.db`**（原 `message_0.db` 主文件时间戳停在 3 月）
3. `weflow-cli messages <talker>` 开始返回 `这个 ID 既不在会话里、也不在联系人里`，**所有会话全部读不出来**
4. `weflow-cli check --report` 只报「微信客户端没有在运行」，密钥与配置看起来都正常 —— **排查成本极高**
5. 连 WorkBuddy 自身数据库都写不进去（`database or disk is full`）

清理 `weflow-ro-*` 后磁盘占用与读取功能**立即全部恢复**，无需重新 `init`（密钥未失效）。

### 本 PR 的修改

`scripts/nt_decrypt.py`：

1. **注册 `atexit` 钩子**，进程退出时删除本次创建的所有副本目录 —— 覆盖所有入口（CLI / MCP / 库调用）
2. **新增 `WEFLOW_TMPDIR` 环境变量**指定临时根目录。`tempfile.gettempdir()` 在进程内缓存首次取值，只设 `TEMP` 不可靠；`WEFLOW_TMPDIR` 是显式且确定的
3. **提供 `cleanup_tmpdirs()`** 供调用方主动清理
4. 抽出 `_temp_root()` 统一临时目录解析逻辑

### 验证

```
# 调用后 scratch 零残留
$ node cli.cjs messages <wxid> -n 5 --json
$ ls D:/weflow-tmp/            # 空
```

连续多轮轮询后 `D:\weflow-tmp` 残留文件数 = **0**，C 盘可用空间稳定在 267 GB，读取正常无 WARN。

### 复现（修复前）

```powershell
for ($i=0; $i -lt 20; $i++) { weflow-cli messages <wxid> -n 5 --json | Out-Null }
Get-ChildItem "$env:LOCALAPPDATA\Temp" -Directory -Filter "weflow-ro-*" | Measure-Object
# → 20 个目录，约 10~20 GB
```

### 补充

同时附带 `docs/ISSUE-temp-copy-leak.md`，记录完整事故链与排查过程，便于其他用户遇到「所有会话读不出来」时能第一时间想到查磁盘空间。

---

## 给维护者的补充说明（可选）

- 本 PR 不改变任何读取语义，只增加副本的生命周期管理。
- `_CHROME_TMPDIRS` 这个名字看起来像是历史遗留（与 Chrome 无关），如果要重命名可以另开一个 PR，本 PR 保持最小改动。
- 若上游更倾向在 `connect_readonly()` 返回的连接上挂 `__del__` 而非 `atexit`，也可以调整——但 `atexit` 能覆盖进程被信号中断的路径，而 `__del__` 不行，所以更稳。
