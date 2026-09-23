/**
 * 本机面板用哪个记忆桶 —— **这决定了它跟微信里那个是不是"同一个大脑"**。
 *
 * 记忆的事实是**按 userId 分**的（`assistant_memory.json` 的 `users["<会话id>"].facts`），
 * 所以"共用记忆"不是一句口号，它等于"两边用同一个字符串当 userId"。
 *
 * 推理链是硬的：直聊时 `resolveInboundRouting` 给出的 `conversationId` 就是 `senderId`
 * （`assistantRouting.ts:73`、`:78-82`），而白名单比的正是 `senderId`（`:31-35`）。
 * **但这条推理在真实微信消息上从未被观测过**——本机那条通道从来没登录成功过
 * （`docs/PROJECT_STATE.md`）。所以这里的态度是：
 *
 * - **恰好一条**白名单 → 就用它（那正是坐在这台机器前的人），并标记 `shared: true`；
 * - **零条或两条以上 → 不猜**。返回一个 `needsAnswer` 的结果，让界面**问一次**
 *   并把答案存进 `assistantPanelUser`，而不是静默落进一个跟微信隔离的桶里
 *   ——那样用户会以为在共用一个大脑，而实际上没有。
 *
 * 界面必须把解析出来的桶显示出来（"当前记忆桶：xxx"），否则上面这些区分用户看不见。
 */

export interface PanelUserResolution {
  /** 用哪个 userId 当记忆桶 */
  userId: string
  /** 是不是与白名单里那个人共用（true 才是"一个大脑"） */
  shared: boolean
  /** 需要用户在界面上选一次（零条或两条以上且没有显式配置） */
  needsAnswer: boolean
  /** 需要回答时列出候选（可能为空数组 = 白名单是空的） */
  candidates: string[]
  /** 给界面看的一句话，说明现在用的是哪个桶、为什么 */
  note: string
}

/** 与本机面板无关的兜底桶：白名单为空、用户又没指定时用它 */
export const PANEL_FALLBACK_BUCKET = 'panel'

function parseList(value: unknown): string[] {
  return String(value ?? '').split(/[,;\s]+/).map(s => s.trim()).filter(Boolean)
}

export function resolvePanelUserId(input: { whitelist?: unknown; configured?: unknown }): PanelUserResolution {
  const configured = String(input.configured ?? '').trim()
  if (configured) {
    // 显式配置**永远优先**，而且不再问：用户已经写过一次答案了
    const inWhitelist = parseList(input.whitelist).includes(configured)
    return {
      userId: configured, shared: inWhitelist, needsAnswer: false, candidates: [],
      note: inWhitelist
        ? `记忆桶：${configured}（与微信白名单里的同一个 id，共用一个大脑）`
        : `记忆桶：${configured}（不在微信白名单里，与微信那边是分开的记忆）`,
    }
  }

  const ids = parseList(input.whitelist)
  if (ids.length === 1) {
    return {
      userId: ids[0], shared: true, needsAnswer: false, candidates: ids,
      note: `记忆桶：${ids[0]}（取自微信白名单里唯一的那个人，共用一个大脑）`,
    }
  }
  if (ids.length === 0) {
    return {
      userId: PANEL_FALLBACK_BUCKET, shared: false, needsAnswer: true, candidates: [],
      note: `微信白名单是空的，所以面板暂时用独立的「${PANEL_FALLBACK_BUCKET}」桶`
        + '——与微信那边是**分开的**记忆。选定一个身份即可共用。',
    }
  }
  return {
    userId: PANEL_FALLBACK_BUCKET, shared: false, needsAnswer: true, candidates: ids,
    note: `微信白名单里有 ${ids.length} 个人，不确定这台机器前是谁，所以面板暂时用独立的`
      + `「${PANEL_FALLBACK_BUCKET}」桶。选定一个即可与那个人共用记忆。`,
  }
}
