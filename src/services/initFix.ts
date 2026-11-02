/**
 * `check --fix` 的执行计划：**只推断，不擅自执行**。
 *
 * 起因是 issue #13 这类情况：用户环境明明正常，只是 `init` 没成功过，
 * 而诊断只给一句"运行 weflow-cli init"——路径要他自己猜、参数要他自己估
 * （#8 就是在这一步撞上 `unknown option '--path'`）。
 *
 * 这里把"能确定的那一步"算出来，交给调用方**先打印再确认**。设计边界：
 *
 * - 只处理**配置为空 / 上次 init 失败**这一种情形；
 * - **找不到数据目录就不猜**（返回 null），因为乱写一个路径比不写更糟；
 * - 不生成任何"删文件 / 扫内存 / 改已有配置"的动作——那些必须由用户自己发起。
 */

export interface InitFixFacts {
  /** 当前是否已初始化（有可用配置） */
  configured: boolean
  /** 探测到的微信数据目录；探测不到就是 null */
  detectedPath: string | null
  /** 上一次 init 的失败记录（没有就是 null） */
  lastInitError: { message: string } | null
}

export interface InitFixPlan {
  /** 为什么要跑这一步，用于在确认提示里说清楚 */
  reason: string
  /** 展示给用户、可直接复制的命令 */
  command: string
  /** 真正要执行的参数（不含 node / 脚本本身） */
  args: string[]
}

export function planInitFix(facts: InitFixFacts): InitFixPlan | null {
  if (facts.configured) return null
  const path = (facts.detectedPath || '').trim()
  // 没有探测到目录就不猜：写一个错路径会让后续每一步都失败，且更难排查。
  if (!path) return null

  const args = ['init', '--path', path]
  const reason = facts.lastInitError
    ? `上次 init 没有成功：${facts.lastInitError.message}`
    : '还没有初始化，但已经找到了微信数据目录'

  // 含空格的中文/中文用户名路径很常见，展示时加引号，用户复制就能用。
  const quoted = path.includes(' ') ? `"${path}"` : path
  return { reason, command: `weflow-cli init --path ${quoted}`, args }
}
