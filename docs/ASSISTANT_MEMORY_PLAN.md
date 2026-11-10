# 助手记忆改造方案（weflow-cli assistant）

> 触发：Anthropic《The components of agent memory》（四层金字塔 + 离线 Dreaming）。
> 本文只针对 **weflow-cli 内置助手**（`src/services/assistant*.ts`），不涉及 DSH / 其它 Agent。
> 状态：**P0 / P1 / P2（确定性版）已实现**；P3 暂缓。实现记录见各节标注。

---

## 1. 现状：已经有的，比想象中好

`src/services/assistantMemory.ts` 目前是三层：

| 层 | 实现 | 参数 |
| --- | --- | --- |
| L1 工作记忆 | 最近若干轮原文 | `CONTEXT_BUDGET_CHARS=24000`，占用到 `0.8` 触发压缩，压后保留 `0.16`，最少 6 轮 / 最多 16 轮 |
| L2 滚动摘要 | 滑出轮次压成摘要 | `SUMMARY_MAX=800` |
| L3 长期事实 | 每 6 轮抽取一次 | `FACTS_MAX=30`、每条 ≤40 字、带 `sourceTurn` / `sourceQuote` / `usedAt` |

注入侧做得尤其克制（**这点比文章的"200 行"还严格**）：

- 按 `bigrams` 相关度选，`FACT_RELEVANCE_FLOOR=0.2`，无关的不塞
- 总预算 `FACTS_INJECT_BUDGET_CHARS=1200`
- 没选中会**如实报数**（`另有 N 条未列出`），不谎报"这是全部记忆"

另有：`MEMORY_FORMAT_VERSION` + 未知版本改名留档、`privacyGate` 脱敏、`assistant_trace.jsonl` 记决策轨迹。

**所以这不是"重做"，是补两个缺口。**

---

## 2. 缺口：对照四层，缺 L1 底账与离线对账

| 文章层 | weflow 现状 | 判断 |
| --- | --- | --- |
| L1 原始流水 | `assistant_trace.jsonl` 记的是**怎么走**（工具名/参数摘要/stopReason），`assistant_audit.log` **不记内容**；原文只活在 `working` 里，压缩后只剩摘要 | **缺口 ①**：事实抽出来之后，原始那句话再也找不回来 |
| L2 记忆笔记 | `facts` 30 条 | **缺口 ②**：不分类、无 `verified`，"规则/教训"和"偏好"混在一起 |
| L3 知识地图 | `vault` / `wiki` 是**文章**的知识库，助手不读 | **缺口 ③**：助手与自己的知识库割裂 |
| L4 开局注入 | 1200 字符相关度注入 | **已达标**，保留 |
| Dreaming | 无 | **缺口 ④**：事实从不复核，错了就一错到底 |

### 为什么 ① 最致命

`Fact.sourceQuote` 只存了**触发那一句的截断**。它证明"这句话被说过"，不能证明"这件事成立"。

后果在文章里叫**错误记忆的级联崩溃**：一条抽错的事实（比如把"我猜是 X"抽成"是 X"），会被 `selectFactsForInjection` 按相关度反复注入，模型每次都当真。

而我们**目前没有任何机制**能发现它。

---

## 3. 改造方案（四步，按性价比）

### P0 · 给 Fact 分层并标记可信度（半天）

```ts
export type FactKind = 'profile' | 'rule' | 'lesson'

export interface Fact {
  content: string
  ts: number
  kind: FactKind            // 新增
  verified?: boolean        // 新增：是否被原始流水复核过
  evidence?: string         // 新增：复核依据（turn 号 / 命令输出片段）
  sourceTurn?: number
  sourceQuote?: string
  usedAt?: number
}
```

`buildFactPrompt` 加一类并收紧判据：

```
分类：profile（用户背景/偏好）、rule（用户明确的长期规则，如"以后回复先读 REPLY-RULES"）、
      lesson（踩过的坑，如"immutable=1 读不到热 WAL"）。
只提取用户说过的；**助手自己的推测一律不算**。
rule/lesson 必须能指向一句原话；找不到原话就不要提。
```

存入时 `kind` 必填、`verified` 默认 `false`。

**为什么值得**：`rule` / `lesson` 就是文章说的「隐性规则」，现在它们被混在"偏好"里，注入时按字数掐掉，最该留的反而最先被丢。

### P1 · 落 L1：会话原文 append-only（1 天）

新增 `~/.weflow-cli/assistant_transcript/YYYY-MM-DD.jsonl`：

```
{"ts":…, "role":"user"|"assistant", "content":"…", "session":"…"}
```

- **只追加**，永不改写（文章第一条法则：保留原始底账）
- **不做出境脱敏**（按 `assistantPrivacy` 既有判据：用户自己的话按原意处理），靠目录 `0700` / 文件 `0600` 保护；`content` 超长截断到 4000 字
- 按天轮转，默认保留 **7 天**（可配），过期删除
- 与 `assistant_trace.jsonl` 分工不变：trace 记"怎么走"，transcript 记"说了什么"

**成本**：一天正常使用约几百 KB。这是整套改造里唯一新增的存储。

### P2 · Dreaming：离线对账（2-3 天）　**已实现（确定性版）**

新增脚本 `scripts/assistant_dream.py`（或 TS 侧 `src/services/assistantDream.ts`），跑在**空闲时**（助手 daemon 的空闲钩子 / 手动触发）：

```
输入：facts（verified=false 的全部） + assistant_transcript 最近 N 天
步骤：
  1. 用 sourceQuote 在 transcript 里定位原话（找不到 → 标记 stale）
  2. 判三态：
       supported   —— 原话明确支持 → verified=true, evidence=turn
       contradicted—— 原话相反 / 后续有更正 → 删除，写报告
       unknown     —— 找不到依据 → 保留但降权（不注入，除非相关度极高）
  3. 同类事实合并（复用现有 `FACT_SAME_RATIO=0.5` 的包含判据）
  4. 输出 `dreaming-report.md`：改了哪条、依据哪句、为什么
```

**这条是整套改造的核心**：没有它，P0/P1 只是让错误记得更整齐。

### P3 · 接 Wiki（可选，1 周）

当一条 `lesson` 命中 `vault` 里的概念名时，把 wiki 页的**摘要**（不是全文）附在事实注入里。复用现有 wiki 搜索，不新建索引。

**判断**：先不做。等 P0-P2 跑一周，看 `lesson` 类事实有多少再说——否则就是"为了用上 wiki 而接 wiki"。

---

## 4. 与现有代码的接口

| 文件 | 改动 |
| --- | --- |
| `src/services/assistantMemory.ts` | `Fact` 加字段；`buildFactPrompt` 加分类；`selectFactsForInjection` 优先 `kind='rule'\|'lesson'` 且 `verified` 的 |
| `src/services/assistantService.ts` | 每轮追加 transcript；空闲时触发 dreaming |
| `src/services/assistantTrace.ts` | **不动**（职责不同，别混） |
| 新增 `assistantDream.ts` | 对账逻辑 |
| `test/assistant-memory.test.ts`（已有则扩） | ① 分类提取；② 矛盾事实被删；③ 无法核验的降权；④ transcript 轮转 |

---

## 5. 验收（可测，不靠感觉）

| 指标 | 怎么测 | 目标 |
| --- | --- | --- |
| **注入不膨胀** | 统计注入字符数 | 仍 ≤ 1200 |
| **规则类不被挤掉** | 造 30 条事实，其中 3 条 `rule`，问相关问题 | 3 条必进 |
| **对账有效** | 塞一条"用户说过 X"的假事实（原文里没有） | dreaming 后 `verified=false` 且不注入 |
| **底账可回溯** | 任意一条 `verified=true` 的事实 | 能在 transcript 里定位到原话 |

---

## 6. 边界（不做的）

- **不存微信聊天内容**到 fact / transcript——助手对话可以，用户数据不行（`privacyGate` 已有拦截，改造不能绕过）
- **不做全量重写事实**：`FACTS_MAX=30` 保持，超出走"合并 + 剪枝"，不是扩容
- **不把 dreaming 做成实时**：在线对账会挤占当前轮次，这正是文章说的取舍

---

## 7. 一句话总结

> 这套改造成本最大的一步不是"加一层"，而是 **P1 + P2**：先有原始底账，才有可能把抽错的事实抓回来。
> 没有底账，Dreaming 无从对账；没有 Dreaming，底账只是占地方。
