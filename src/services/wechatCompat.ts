/**
 * 微信版本兼容：**单一事实源** + 纯比较。
 *
 * 背景（2026-10-25 起立此规矩）：微信 4.x 几周一更，而"这版 weflow-cli 在哪个微信版本上
 * 验证过"原来只写在 PROJECT_STATE 的一段散文里——CHANGELOG 不记、机器读不到，用户升级
 * 微信后读不出数据时，第一件事（"你的微信比我们验证过的新吗"）没人答得上来。
 *
 * 三条接线，都只从这读、别处不许再写一份：
 *  - `capabilities --json` 的 `compatibility.wechat`（机器可读）；
 *  - `check`（含 `--json` / `--report`）拿本机**实际加载**的版本与此比对，给出四种判定之一；
 *  - `docs/RELEASING.md` 的发布清单要求每个 CHANGELOG 版本段记录验证版本（来源就是这里）。
 *
 * 更新 `WECHAT_VERIFIED` 的门槛：**必须伴随一次真实数据验证**（不只是"进程起来了"），
 * 验证日期与读了什么写进 `verifiedHow`。没验证就不许改——这正是这个常量存在的意义。
 */
export const WECHAT_VERIFIED = {
  version: '4.1.15.56',
  verifiedAt: '2026-10-26',
  verifiedHow: '升级后不重新提钥，用旧密钥实读真实数据：sessions / messages / contacts / '
    + 'daily-stats / fav / sns stats 全部返回数据（2026-10-19，记录见 PROJECT_STATE「Initialization」）；'
    + '2026-10-26 复核：sessions 实读仍正常、check 判定 verified',
} as const

export type CompatVerdict =
  | 'verified'              // 本机加载的就是验证过的版本
  | 'newer-than-verified'   // 本机更新：可能不兼容，读不出数据先怀疑这里
  | 'older-than-verified'   // 本机更旧：通常是兼容的（向下兼容是常态，但没验证过也说不上保证）
  | 'unknown'               // 微信没在跑 / 版本探测失败 / 版本串形状不对——"不知道"不是"没问题"

/**
 * 四段数字逐段比较（`4.1.15.56` vs `4.1.15.60`）。缺段按 0 处理（`4.1.15` < `4.1.15.56`）；
 * 任一侧不是纯数字点分串就不猜——返回 `unknown`。纯函数。
 */
export function compareWechatVersions(live: string | null | undefined, verified: string): CompatVerdict {
  const parse = (v: string): number[] | null => {
    const parts = v.split('.')
    if (!parts.length || !parts.every(p => /^\d+$/.test(p))) return null
    return parts.map(Number)
  }
  if (!live) return 'unknown'
  const a = parse(live)
  const b = parse(verified)
  if (!a || !b) return 'unknown'
  const len = Math.max(a.length, b.length)
  for (let i = 0; i < len; i++) {
    const x = a[i] ?? 0
    const y = b[i] ?? 0
    if (x > y) return 'newer-than-verified'
    if (x < y) return 'older-than-verified'
  }
  return 'verified'
}

/**
 * 判定的人类可读一句话（check 人类输出与 `--report` 共用，措辞只有这一份）。
 *
 * `reason` 用来把"为什么判不了"说准：沙箱/容器里本进程的进程视图可能被隔离，
 * 那时原因**不是**"微信没在运行"（2026-10-28 实测：微信在跑、库正在被写，
 * 而这句话把它讲成了没开）。不给 `reason` 时保持原措辞。
 */
export function describeCompatVerdict(verdict: CompatVerdict, verifiedVersion: string,
                                     reason = ''): string {
  switch (verdict) {
    case 'verified':
      return `本机加载的微信就是本版本验证过的 ${verifiedVersion}`
    case 'newer-than-verified':
      return `本机微信比本版本验证过的 ${verifiedVersion} 更新——如果读不出数据，先怀疑这里`
        + '（去 GitHub 看有没有更新版本）'
    case 'older-than-verified':
      return `本机微信比本版本验证过的 ${verifiedVersion} 更旧——一般兼容，但没有专门验证过`
    default:
      return `无法判定（${reason || '微信没在运行或版本探测失败'}）；本版本验证过的是 ${verifiedVersion}`
  }
}


/**
 * 版本未知的**原因**要分三种说清（别把它们混成一句"微信没在运行"）：
 *   1. 进程视图被隔离（沙箱/容器里看不到主程序）；
 *   2. **平台不支持版本探测**——版本探测只实现了 Windows，
 *      Linux/macOS 上客户端明明在跑也拿不到版本；
 *   3. 客户端真的没开。
 *
 * 第 2 种是 2026-11-01 在 Ubuntu + 微信 4.1.13.26 上实测踩到的：
 * `check` 报"未知（微信没在运行）"，而同一时刻微信正在写 biz_message_0.db。
 * 这就是 D-145 要避免的假错误——把"探测不支持"讲成"客户端没开"。
 */
export function describeUnknownVersionReason(options: {
  platform?: NodeJS.Platform
  viewFiltered?: boolean
} = {}): { reason: string; compatReason: string } {
  const platform = options.platform ?? process.platform
  if (options.viewFiltered) {
    return {
      reason: '未知（进程视图被隔离，判断不了）',
      compatReason: '本进程的进程视图被隔离，看不到主程序',
    }
  }
  if (platform !== 'win32') {
    return {
      reason: `未知（版本探测仅支持 Windows，当前平台 ${platform}）`,
      compatReason: '版本探测仅支持 Windows，本平台无法判定，不代表客户端没运行',
    }
  }
  return { reason: '未知（微信没在运行）', compatReason: '' }
}
