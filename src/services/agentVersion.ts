/**
 * Agent 版本号（与 npm 包版本解耦）。
 *
 * npm 的 `1.22.0` 是整个 CLI 的发布版本，跟 GitHub Release 解耦、发版节奏慢；
 * Agent（ReAct 循环、记忆、判断层、红线、知识库检索）迭代频率远高于发版，
 * 所以用这个独立版本追踪 Agent 自身的行为变化。
 *
 * 规则（见 AGENTS.md Documentation Protocol）：
 * - 每次改 Agent 行为（提示词/守卫/工具/记忆/检索/配置默认值）就 bump 这里；
 * - minor：新增能力或行为语义变化；patch：修复/措辞/小调整；
 * - 同时在 docs/AGENT_ITERATIONS.md 顶端加一条带版本号的迭代记录；
 * - 提交信息里带 agent-vX.Y.Z。
 *
 * 单一真相源：`capabilities --json` 从这里读，用户与脚本都能查询当前安装的版本。
 */
export const AGENT_VERSION = {
  version: '1.0.0',
  released: '2026-11-27',
  note: '首次建立 Agent 版本序列：词法路由零依赖兜底、Jev 可选、wiki+RAG 双层检索、D-202~217 可靠性红线。',
} as const
