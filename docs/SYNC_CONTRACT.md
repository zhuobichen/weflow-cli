# 同步与任务状态契约

本文冻结两个内部状态 schema：`weflow-sync/v1`（跨运行的同步检查点）与 `weflow-job/v1`
（单次运行的记录）。二者都在本机 `~/.weflow-cli/` 下，**不对外暴露**。

它们是 [FUNCTIONAL-IMPROVEMENT-SPEC.md](FUNCTIONAL-IMPROVEMENT-SPEC.md) §4 与 §5 的落地，
边界受 [DECISIONS.md](DECISIONS.md) 约束（尤其 D-014、D-019、D-025、D-027）。

## 为什么是两个文件

生命周期不同，不是猜测性抽象：

| 文件 | 生命周期 | 记录的是 |
| --- | --- | --- |
| `sync/<source>-<name>-<hash>.json` | **跨运行**，每次同步覆盖 | 已覆盖到哪、分片读没读全 |
| `jobs/<jobId>.json` | **单次运行** | 这次运行的起止、状态、结果 |

一次运行写一个 job 记录；同步检查点记录"到目前为止覆盖范围"这一长期事实。

## `weflow-sync/v1`

路径：`~/.weflow-cli/sync/<source>-<sanitize(talker)>-<sha256(talker)[0:8]>.json`

**按会话分文件**，不是一个全局文件。理由：全局文件要求跨会话读改写并合并，而分文件
让每次写入都是整文件原子替换；同时文件名里的 hash 后缀让 `wxid_a@chatroom` 与
`wxid_a_chatroom` 落到不同文件，而显示名不会出现在目录列表里。

```json
{
  "schema": "weflow-sync/v1",
  "schemaVersion": 1,
  "source": "wechat-nt",
  "scope": "<显示名，未知时等于 talker>",
  "talker": "<会话标识，不是路径>",
  "lastSuccessfulRun": "2026-09-19T09:00:00+08:00",
  "lastAttempt": "2026-09-19T09:02:10+08:00",
  "coveredFrom": "2026-09-01T00:00:00+08:00",
  "coveredTo": "2026-09-19T08:59:59+08:00",
  "checkpoint": { "newestCreateTime": 1789000000, "overlapSeconds": 300 },
  "recordsRead": 1200,
  "recordsDeduplicated": 14,
  "recordsReturned": 1186,
  "shardsRead": 4,
  "shardsFailed": 0,
  "shards": {
    "scanned": 4, "opened": 4, "failed": 0,
    "items": [
      { "name": "message_0.db", "opened": true, "hasTalkerTable": true,
        "rowsForTalker": 8123, "reason": null }
    ]
  },
  "coverage": "complete",
  "mayHaveMore": false,
  "warnings": [],
  "updatedAt": "2026-09-19T09:02:10+08:00"
}
```

### 两个时间戳必须分开

| 字段 | 何时推进 |
| --- | --- |
| `lastAttempt` | **每次尝试都推进**，无论结果 |
| `lastSuccessfulRun` | **仅在 `coverage !== 'partial'` 时推进** |

只有一个的话，"跑了但部分完成"会和"跑成功了"混为一谈——而这正是方案 §5.2 明令禁止的
（部分完成不能返回成功状态）。

### `coverage` 三档

| 值 | 含义 |
| --- | --- |
| `complete` | 扫到的分片全部打开、打开的全读成功，且 `mayHaveMore` 为 false |
| `unverified` | 有分片**打不开**，但打开的都读成功。已读部分是可信的，未打开的分片是否含本会话消息**无法判断** |
| `partial` | 某个**已打开**的分片读失败，或 `mayHaveMore` 为 true，或分页在到达窗口起点前就短页结束 |

`unverified` 不是失败：`connect_message_shards` 一直把打不开的分片当作可跳过的（这是有意设计），
CLI 无法知道那个分片里有没有本会话的消息。如实标成 `unverified` + `warnings` 比直接失败更有用，
也让自动化不至于卡死。

`partial` 则**必须失败**（`code: 'SYNC_PARTIAL'`，退出码 1）：状态文件照写（方案 §5.2 要求
部分完成不得删除已生成的可用结果），但 `lastSuccessfulRun` 不推进。

### 身份

按方案 §4.2，`serverId` 非零时优先；**`localId` 绝不单独作为全局身份**（D-014 同此约束）。

```
talker|s:<serverId>                   serverId 非零
talker|l:<localId>|t:<createTime>     否则
```

**与方案 §4.2 的一处有意偏离**：键里**不含 `shard`**。消息载荷里没有 shard 字段
（`nt_decrypt._message_dict` 不产出它），加进去要动 `export json` 的消息契约，而 D-016/D-027
把那条线封住了。它对本契约要解决的**重叠去重**也不必要——同一条消息重读两次，得到的
`serverId` 或 `(localId, createTime)` 本来就一样。只有跨分片的 `(localId, createTime)` 撞车
才需要它，而 `talker` + `createTime` 已让这种撞车极其遥远。**真观测到再加，不预先加。**

### 明确不做的事

**`mayHaveMore` 不是稳定游标。** 按 D-027，稳定增量游标必须在所有相关后端都支持后才能
对外宣称；在此之前只提供重叠时间窗 + 本地去重，由下游决定重复容忍度。

**不得写入**：数据库密钥、`x'<key><salt>'`、聊天正文、绝对路径（D-001 / D-002 / D-025）。
状态服务在写盘前统一过一遍脱敏，不指望每个调用方都记得。

## `weflow-job/v1`

路径：`~/.weflow-cli/jobs/<jobId>.json`。`jobId` 形如 `job_20260919_0001`。

```json
{
  "schema": "weflow-job/v1",
  "jobId": "job_20260919_0001",
  "kind": "sync.messages",
  "state": "running",
  "createdAt": "2026-09-19T09:00:00+08:00",
  "updatedAt": "2026-09-19T09:02:10+08:00",
  "window": { "from": 1788000000, "to": 1789000000 },
  "progress": { "completed": 800, "total": 1200 },
  "resumeable": true,
  "result": { "records": 800, "recordsDeduplicated": 14, "shardsFailed": 0 },
  "error": null
}
```

状态机：`planned → waiting_confirmation → running → paused`，终态 `completed | partial | failed | cancelled`。
本轮只实现 `running | completed | partial | failed`，其余是预留（不实现的不写进状态机文档当既有能力）。

`partial` 是本项目新增的一档，**不算成功**。`error` 只放既有的大写错误码
（`NOT_INITIALIZED`、`CONFIRMATION_REQUIRED`、`INVALID_ARGUMENT`、`SYNC_PARTIAL`、
`DATABASE_CONNECTION_FAILED` 等），不写中文长句供机器解析。

**幂等**：同一范围重复提交应返回已有任务或明确新建，不得静默重复写入。本轮用
`kind + talker + 窗口` 生成幂等提示，命中未完成的同键任务时返回该任务而非新建。

> 本轮 `sync` 是 job 记录的唯一写入者。若日后认为不值得单独一个文件，
> **回退路径**：删掉 job 存储、把那个 `lastJob` 对象并入各会话的 `weflow-sync/v1` 文件。
> 因为此刻还没有第二个消费者，这是一次单文件改动，无兼容性影响。

## 版本升级策略

- `schema` 变化 = 不兼容变更，需要迁移说明。
- 只增字段 = 兼容变更；读方必须容忍未知字段（与 `DATA_CONTRACT.md` 对下游的要求一致）。
- 读到未知 `schema` 时**不得覆盖**：报明确错误并保留原文件。

## 相关文档

- [功能增强技术方案](FUNCTIONAL-IMPROVEMENT-SPEC.md)
- [消息数据契约](DATA_CONTRACT.md)
- [技术决策记录](DECISIONS.md)
- [项目当前状态](PROJECT_STATE.md)
