# 朋友圈（SNS）功能使用说明

本文基于项目源码整理，说明 `weflow-cli` 的朋友圈功能怎么用、数据从哪来、有哪些限制。

> 依据文件：`bin/weflow-cli.ts`（命令族）、`src/services/chatService.ts`（分流）、`src/core/ntCore.ts` / `src/core/wcdbCore.ts`（两条读库路径）、`src/core/keyService.ts`（密钥捕获）、`src/services/assistantTools.ts`（AI 工具）、`scripts/nt_decrypt.py`（Python 解密）。

---

## 1. 它到底读什么

朋友圈功能**不联网、不调微信服务器接口**，只读微信客户端在本机留下的 `sns.db` 缓存库。

因此有一个硬前提：

> **必须先在微信客户端里打开过朋友圈**，让动态落盘到本地 `sns.db`，否则查询结果就是空的。

项目实测记录：`docs/PROJECT_STATE.md` 记录 `sns stats` 在 4.1.15 / 4.1.15.56 客户端下返回了 4,164 条缓存动态。

### 1.1 数据是「自动刷新」还是「手动刷新」？—— **手动**

这是本功能最容易误解的一点，2026-11-03 在本机做了对照实验后确认：

**结论：`weflow-cli` 自己不会刷新任何朋友圈数据。** 新数据必须由**微信客户端**写入
`sns.db`，而客户端写入是**你打开/刷新朋友圈这个动作触发的**，不是定时轮询。

| 谁在写 `sns.db` | 说明 |
| --- | --- |
| 微信客户端（`Weixin.exe`） | **唯一的写入方**。打开朋友圈、下拉刷新、收到新动态时落盘 |
| `weflow-cli` | **只读**。所有 SNS 查询都是 `SELECT`，且以 `mode=ro&immutable=1` 打开原库（有 WAL 时改读临时副本），**不改原库** |
| 微信服务器 | **不参与**。项目没有任何朋友圈网络接口（ilink 端点只有 bot 的二维码 / getupdates / sendmessage / CDN 上下传） |

**实测证据（2026-11-03，本机）**：

- 阶段 A —— 只观察文件 mtime、**不执行任何 weflow 命令**：`sns.db` 在 12:58:55 → 13:01:14 之间
  自己变了。**是客户端写的**，不是查询触发的。
- 阶段 B/C —— 执行 `sns stats` 前后对比 mtime：**没有变化**。查询不改原库。
- 被动观察 60 秒（微信进程在运行、无任何操作）：mtime **纹丝不动** —— 证明客户端
  **不是**按固定周期写，而是**由刷新动作触发**。

**所以你要做的是**：在微信里打开朋友圈、下拉刷新，让客户端把动态写进 `sns.db`，
然后再查。工具本身不会替你按刷新按钮。

### 1.2 更隐蔽的一层：两张表的新鲜度不一致

`sns.db` 里朋友圈数据分散在多张表，`weflow-cli` 读的是其中两张，而**它们的更新节奏不一样**：

| 表 | 内容 | 本机实测行数 | 时间跨度 | 谁在用 |
| --- | --- | --- | --- | --- |
| `SnsTimeLine` | **正文**（`content` 是 XML blob，含文字、图片数、点赞） | 7,818 | **1,153 天** | `sns timeline` |
| `SnsTopItem_1` | **仅元数据**（谁发的、何时、读没读），`summary` 列 **100% 为空** | 17,484 | 109 天 | `sns stats` |

实测证据（2026-11-03 13:08，同一个库同一时刻）：

- `SnsTimeLine` 最新动态：**12:06:51**
- `SnsTopItem_1` 最新条目：**13:04:49**（新近 1 小时）
- `SnsTopItem_1` 最新那条的 `tid` 在 `SnsTimeLine` 中**对应 0 行** —— 它只有壳，没有正文。

**这会造成一个看起来像 bug 的现象**：

```
sns stats    → totalPosts 一直涨（读 SnsTopItem_1）
sns timeline → 最新一条卡住不动（读 SnsTimeLine）
```

两者读的是不同的表，所以"条数在涨"并不等于"正文在更新"。**要拿到带正文的新动态，
必须让客户端刷新出 `SnsTimeLine` 的新行** —— 只写 `SnsTopItem_1` 不够。

> 注：本机实测中，客户端刷新后 `SnsTopItem_1` 立即前进，而 `SnsTimeLine` 在 60 秒
> 观察窗内**没有**前进（停在 12:06:51）。这说明客户端是**分表按需落盘**的：
> 列表元数据先到，正文要等你真正进入某条动态、或客户端按自己的策略补写。
> **这属于客户端行为，本工具无法控制，也不该被当成工具的缺陷。**

---

## 2. 三层入口

### 2.1 CLI 命令族

```powershell
weflow-cli sns timeline      # 时间线（可过滤）
weflow-cli sns users         # 列出本地缓存中有动态的 wxid
weflow-cli sns stats         # 缓存统计（总动态数 / 有动态的好友人数）
weflow-cli sns capture-key   # 捕获 sns.db 解密密钥（仅 Windows，交互式）
```

#### `sns timeline` 参数

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| `-u, --user <wxid>` | 过滤指定 wxid，**可重复使用** | 无 |
| `-k, --keyword <kw>` | 内容关键词过滤 | 无 |
| `-n, --limit <number>` | 最大条数（1–1000） | `20` |
| `-o, --offset <number>` | 偏移量 | `0` |
| `--start <timestamp>` | 起始时间戳（秒） | 无 |
| `--end <timestamp>` | 结束时间戳（秒） | 无 |
| `--json` | 输出 JSON | 否 |

示例：

```powershell
weflow-cli sns timeline --limit 50
weflow-cli sns timeline -u wxid_abc123 -u wxid_def456 --limit 100
weflow-cli sns timeline -k "咖啡" --start 1759100000 --end 1761500000
weflow-cli sns timeline --json --limit 20
```

#### `sns stats`

输出 `totalPosts`（总动态数）、`totalFriends`（发布过动态的好友人）、`myPosts`（自己发过多少条，仅当传入自己的 wxid 时才有）。

### 2.2 AI 工具（助手 / MCP / 微信里直接问）

工具名 `get_sns`，定义在 `src/services/assistantTools.ts:835-848`。

| 参数 | 取值 | 默认 |
| --- | --- | --- |
| `mode` | `timeline` / `stats` / `users` | `timeline` |
| `limit` | timeline 模式条数 | `10` |

`users` 模式的行为值得注意：它**不是**读 `sns users` 那张名单，而是**拉最近 200 条时间线，在本地按发帖人昵称聚合计数**后取前 10（`assistantTools.ts:1692-1707`）。所以"谁最爱发朋友圈"反映的是**最近窗口内的相对热度**，不是历史总量。

路由规则在 `src/services/assistantRouter.ts:44-47`：
- 问"朋友圈最近有什么动态" → `get_sns` mode=timeline
- 问"朋友圈发了多少、多少人发" → `get_sns` mode=stats

### 2.3 微信里直接问

项目 README 明确列出可在 ClawBot 对话中直接问：
- 「最近朋友圈都发了啥？」
- 「谁最爱发朋友圈？」

---

## 3. 前置配置：密钥怎么来

### 3.1 两条读库路径

`chatService.isSnsSupported()`（`src/services/chatService.ts:349-362`）决定走哪条路：

| 路径 | 条件 | 实现 |
| --- | --- | --- |
| **WCDB 路径** | 4.x 连接且**未启用** raw sqlite / NT 模式 | `wcdbCore.getSnsTimeline()` 等，调用 dll 导出函数 `wcdb_get_sns_timeline` / `wcdb_get_sns_usernames` / `wcdb_get_sns_export_stats` |
| **NT 路径** | 存在 `ntCore` 且 `snsDbPath` + `snsKey` + `snsSalt` **三项齐全** | `ntCore.getSnsTimeline()` → 调 Python `scripts/nt_decrypt.py sns-timeline` |
| 不支持 | 非 4.x，或 NT 模式但三项配置缺任一 | 返回 `SNS_UNAVAILABLE` / `当前数据通道不支持朋友圈查询` |

### 3.2 NT 路径的配置来源

三项配置来自 `src/services/configService.ts`：

| 配置键 | 含义 | 来源 |
| --- | --- | --- |
| `snsDbPath` | sns.db 路径 | `init` 扫描自动发现（`bin/weflow-cli.ts:1199-1204`），或 `sns capture-key` 自动发现 |
| `snsSalt` | 32 位十六进制盐值 | 同上，自动发现 |
| `snsKey` | 64 位十六进制密钥 | **`sns capture-key` 捕获**，或手工 `config set snsKey <64位hex>` |

> 特别注意：`init` 时即使密钥匹配上了，**`snsKey` 仍然可能是空的**。看 `bin/weflow-cli.ts:1404-1412`：密钥未匹配时只会设 `snsDbPath` 和 `snsSalt`，然后提示 `密钥未匹配，请运行: weflow-cli sns capture-key`。所以 `init` 成功 ≠ 朋友圈可用。

### 3.3 `sns capture-key` 的限制

- **仅 Windows**（`keyService.ts:355`：非 Windows 直接返回"朋友圈密钥捕获仅支持 Windows（依赖进程 Hook）"）
- **需要管理员权限**运行终端
- **需要交互式终端**。JSON 模式下调 `--json` 会直接返回 `INTERACTIVE_REQUIRED` 且不扫描进程（`bin/weflow-cli.ts:3867-3884`）。Agent 只能跑 `sns capture-key --dry-run --json` 看要求。
- **30 秒窗口**：命令启动后必须**立即在微信里打开朋友圈**，让客户端访问 sns.db 从而触发密钥捕获（`bin/weflow-cli.ts:3933-3936`）
- 捕获成功后 `snsKey` 被写入加密配置

### 3.4 手工配置兜底

```powershell
weflow-cli config set snsDbPath <path>
weflow-cli config set snsSalt  <32位hex>
weflow-cli config set snsKey   <64位hex>
```

---

## 4. 已知限制

| 限制 | 说明 |
| --- | --- |
| **不会自动刷新** | 工具只读，新数据必须由微信客户端在**你刷新朋友圈时**落盘。见 §1.1 |
| **两张表新鲜度不一致** | `sns stats` 读 `SnsTopItem_1`（仅元数据，`summary` 100% 为空），`sns timeline` 读 `SnsTimeLine`（正文）。前者涨不代表后者更新。见 §1.2 |
| 只读本地缓存 | 不会去服务器拉取历史动态，查不到的东西就是客户端没缓存过 |
| 图片/视频本体拿不到 | 时间线返回 `media_count` 数字，但没有媒体文件导出；`exportService` 中**没有任何 sns 导出目标**，朋友圈**不支持导出** |
| 图片内容无法识别 | 时间线里的图片只有数量，没有 OCR / 描述 |
| `users` 模式是近似值 | 基于最近 200 条聚合，非历史总量 |
| `get_sns` 的 limit 上限 | `boundedToolInteger(args.limit, 10, 20)`，工具侧上限 20 条 |
| WCDB 路径的额外能力 | `wcdbCore` 另有 `getSnsAnnualStats` / `installSnsBlockDeleteTrigger` / `uninstallSnsBlockDeleteTrigger` / `checkSnsBlockDeleteTrigger` / `deleteSnsPost`（朋友圈删除拦截与删除帖）——但这些**目前没有任何 CLI 命令或 AI 工具调用它们**，是尚未接线的未暴露能力 |

---

## 5. 排查清单

### 5.1 「查不到数据」时按顺序检查

1. **微信客户端打开过朋友圈吗？** 没有就先打开一次
2. **配置齐吗？** `weflow-cli config show --json` 看 `snsDbPath` / `snsKey` / `snsSalt` 三项是否都有值
3. **走的哪条路径？** `weflow-cli check --json` 里 `momentsDatabase` 表示 sns.db 文件存在（注意这只说明文件在，不说明密钥对）
4. **NT 模式但密钥空？** 跑 `weflow-cli sns capture-key`（管理员 + 30 秒内开朋友圈）

### 5.2 「数据太旧 / 不更新」时按顺序检查

这是**另一个问题**，别和 5.1 混在一起 —— 配置可能完全正常，只是数据没更新。

1. **客户端刷新了吗？** 在微信里打开朋友圈并下拉刷新。工具**不会**替你刷新（§1.1）
2. **看 stats 涨了但 timeline 没涨？** 正常现象，两张表新鲜度不同（§1.2）。
   `sns stats` 读 `SnsTopItem_1`（元数据），`sns timeline` 读 `SnsTimeLine`（正文）。
   要新正文，得让客户端把 `SnsTimeLine` 也写上
3. **想确认库里最新的时间戳**（不用猜，直接查）：

   ```powershell
   # 元数据表最新时间
   weflow-cli sns stats --json
   # 时间线最新一条的时间戳
   weflow-cli sns timeline --json --limit 1
   ```

4. **对比两边**：如果 `stats` 的 `totalPosts` 明显大于 `timeline` 能返回的条数，
   说明 `SnsTimeLine` 落后 —— **这是客户端分表落盘的行为，不是工具故障**

---

## 6. 与 Agent / MCP 的边界

`docs/AI_INTERFACE.md` 记录了 Agent 可直接调用的只读命令：

```powershell
weflow-cli sns timeline --json --limit 20
weflow-cli sns users --json
weflow-cli sns stats --json
```

以及明确的限制：**朋友圈密钥捕获同样需要用户在微信客户端中触发。Agent 只能调用 `sns capture-key --dry-run --json` 查看要求；实际捕获使用交互终端中的 `sns capture-key --yes`，JSON 执行会返回 `INTERACTIVE_REQUIRED`，不会扫描进程。**
