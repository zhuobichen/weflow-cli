/**
 * 「读取失败」与「确实为空」必须分开报告。
 *
 * 背景（issue #16）：`messages` / `sessions` 在读库失败时返回 `success: true` + 空数组，
 * 调用方和用户看到的是「没有消息」。这会把人引向「数据丢了」的方向排查很久，
 * 而真正的原因可能在别处（密钥失效、库被重建、分片读不出来、数据目录不是同一个）。
 *
 * 这里只做一件事：把"为什么空"写清楚。**不改变原有成功路径的输出形状**，
 * 只在确实失败时补一个 `failure` 字段并附带可执行的下一步。
 *
 * 设计取舍：
 * - 不抛异常。现有调用方都是按 `success` 判断的，改成抛会击穿一大片调用点。
 * - 不做自动重试。失败原因未知时重试只会把"慢"变成"很慢"。
 * - `code` 只用稳定的短标识（下面几个），`message`/`next` 面向人。
 */

export type EmptyReason =
  /** 连接/密钥层面失败：最可能是密钥失效或还没初始化 */
  | 'connect-failed'
  /** 连上了，但查询报错（例如 SQLITE_NOTADB 26 这种"不是数据库"） */
  | 'query-failed'
  /** 连上了、查询成功，但库里确实没有匹配的记录 */
  | 'genuinely-empty'

export interface ReadFailure {
  code: EmptyReason
  /** 面向人的一句话原因 */
  message: string
  /** 建议的下一步（可执行的动作，不是"请排查"） */
  next: string
  /** 原始错误文本（可能含路径，调用方决定是否展示） */
  detail?: string
}

/** 把底层错误文本归类，并给出可执行的下一步。 */
export function classifyReadError(detail: string | undefined): ReadFailure {
  const raw = String(detail ?? '')
  const low = raw.toLowerCase()

  // SQLITE_NOTADB(26) / "file is not a database" —— 典型是密钥不对（含库被重建）
  if (low.includes('not a database') || low.includes('notadb') || /\bcode\s*[:=]?\s*26\b/.test(low)) {
    return {
      code: 'query-failed',
      message: '数据库打不开：看起来这不是一个有效的库文件——通常是密钥与库不匹配（例如客户端升级时重建过数据库）。',
      next: '保持微信客户端运行并已登录，然后重新获取密钥：weflow-cli init；完成后用 weflow-cli sessions 验证。',
      detail: raw || undefined,
    }
  }

  if (low.includes('no such table')) {
    return {
      code: 'query-failed',
      message: '连上了，但找不到预期的表；可能是读到了另一个库，或客户端版本结构已变。',
      next: '先跑 weflow-cli check --report，核对它报的数据目录与客户端实际使用的是不是同一个。',
      detail: raw || undefined,
    }
  }

  if (low.includes('database is locked') || low.includes('busy')) {
    return {
      code: 'query-failed',
      message: '数据库正忙（被占用），本次没有读成。',
      next: '等客户端同步告一段落后重试；若持续出现，先关闭微信客户端再跑一次。',
      detail: raw || undefined,
    }
  }

  if (low.includes('unable to open') || low.includes('no such file') || low.includes('enoent')) {
    return {
      code: 'query-failed',
      message: '库文件打不开（路径不存在或没有权限）。',
      next: '跑 weflow-cli check --report 核对数据目录；如果客户端刚换过数据目录，用 weflow-cli init --path 重新指定。',
      detail: raw || undefined,
    }
  }

  return {
    code: 'query-failed',
    message: '读取失败（原因未归类）。',
    next: '跑 weflow-cli check --report 并把它贴进 issue——它会自动脱敏，能直接定位到卡在哪一步。',
    detail: raw || undefined,
  }
}

/** 连接层失败（拿不到连接 / 没初始化 / 客户端没跑）。 */
export function connectFailure(detail: string | undefined): ReadFailure {
  return {
    code: 'connect-failed',
    message: '没能连上微信数据库。',
    next: '按提示先跑 weflow-cli check --report；没初始化的先 weflow-cli init。',
    detail: detail || undefined,
  }
}

/** 真的没有数据时也给一句话——避免和"读失败"混为一谈。 */
export function emptyResult(reason: 'ok' | EmptyReason = 'ok'): ReadFailure | undefined {
  if (reason === 'ok') return undefined
  return {
    code: reason,
    message: '读取过程没有完成，所以这里显示为空——**这不代表没有数据**。',
    next: '先确认是不是密钥/目录的问题：weflow-cli check --report',
  }
}
