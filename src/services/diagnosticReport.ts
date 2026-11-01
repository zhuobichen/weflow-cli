/**
 * 「可粘贴的诊断块」—— 用户在排障时能一键复制、贴给 AI 或贴进 issue 的那段文本。
 *
 * 为什么要有它：这个项目最贵的一类故障是**静默失败**——`init` 报"完成"、`sessions` 空着，
 * 用户只能猜（"是不是微信版本太新？""是不是库坏了？"），于是同一个根因被报成四种问题
 * （issue #3 / #9 / #10 与一封邮件，2026-10-18 核对过）。让用户把现象口头描述一遍，
 * 双方都要猜；给他一段**已经判定过、且脱敏的结构化事实**，就不用了。
 *
 * 三条约束：
 *  1. **脱敏**：报告里永远不出现密钥、口令、聊天内容；路径保留结构但把用户名与 wxid 换掉。
 *     这些值本来就只以布尔（"已配置/未配置"）形式进入输入。
 *  2. **判定，不只是罗列**：`diagnose()` 把"Python 不可用 / 缺 sqlcipher3 / 还没初始化 / 客户端没在跑"
 *     翻成人话结论——用户拿到的不是一串 true/false。
 *  3. **纯函数**：输入是普通对象，输出是字符串。不读文件、不碰配置，所以能直接测。
 */

export type DiagnosticLevel = 'blocker' | 'warning' | 'info'

export interface DiagnosticFinding {
  level: DiagnosticLevel
  /** 一句话结论（给人看，也用来做断言） */
  title: string
  /** 下一步做什么（可执行的命令或检查） */
  hint: string
}

export interface DiagnosticInput {
  cliVersion: string
  /** 传入而不是内部取，测试才能稳定 */
  generatedAt: string
  platform: string
  arch: string
  nodeVersion: string
  python: { command: string; available: boolean; version: string }
  dependencies: { required: Record<string, boolean>; optional?: Record<string, boolean> }
  configuration: {
    initialized: boolean
    messageDatabase: boolean
    momentsDatabase?: boolean
    favoritesDatabase?: boolean
    favoritesReady?: boolean
  }
  /** 只传布尔：这些键的值（密钥/口令）在这里连读都不读 */
  keys: { messageKeyConfigured: boolean; passphraseConfigured: boolean }
  wechatClient?: {
    running: boolean
    version: string | null
    note?: string
    dataDirMatchesConfig?: boolean | null
    dataDirs?: string[]
    unusableVersionDirs?: Array<{ dir: string; files: number }>
    /** D-130：本机加载版本 vs 本包验证版本（check 那边算好传进来） */
    compatibility?: {
      verifiedVersion: string
      verifiedAt: string
      verdict: 'verified' | 'newer-than-verified' | 'older-than-verified' | 'unknown'
    }
  }
  /** 最近一次失败的原文（可选；`redactForReport` 会再过一遍） */
  lastError?: string | null
}

const LEVEL_ORDER: Record<DiagnosticLevel, number> = { blocker: 0, warning: 1, info: 2 }

const LEVEL_LABEL: Record<DiagnosticLevel, string> = {
  blocker: '卡住了',
  warning: '可能有问题',
  info: '只是提醒',
}

/**
 * 报告里的脱敏。**只处理"可能带身份"的那几类**，不做通用 PII 扫描——
 * 这份文本是给作者/AI 看排障事实的，过度打码会把"路径里有个中文目录"这种线索也删掉。
 */
export function redactForReport(text: string): string {
  return String(text)
    // Windows 用户名目录：保留盘符与后面的结构，只换掉用户名本身
    .replace(/([A-Za-z]:\\Users\\)[^\\]+/gi, '$1<用户>')
    .replace(/(\/home\/)[^/\s]+/g, '$1<用户>')
    .replace(/(\/Users\/)[^/\s]+/g, '$1<用户>')
    // wxid 是账号标识（AGENTS.md 点名的敏感项之一）
    .replace(/wxid_[A-Za-z0-9_\-]+/g, 'wxid_<已隐藏>')
    // 密钥形态：宁可按形态拦，不靠"这一定是密钥"的判断
    .replace(/sk-[A-Za-z0-9\-_]{16,}/g, '<密钥>')
    .replace(/(?<![0-9A-Za-z])[0-9a-fA-F]{32,}(?![0-9A-Za-z])/g, '<hex>')
    // 邮箱与手机号：用户可能把作者邮箱/手机贴进来
    .replace(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g, '<邮箱>')
    .replace(/(?<!\d)1[3-9]\d{9}(?!\d)/g, '<手机号>')
}

/** 把输入翻成结论。返回按严重度排好序的清单（同级别保持发现顺序）。 */
export function diagnose(input: DiagnosticInput): DiagnosticFinding[] {
  const out: DiagnosticFinding[] = []
  const required = input.dependencies.required || {}
  const missing = Object.entries(required).filter(([, ok]) => !ok).map(([name]) => name)

  if (!input.python.available) {
    out.push({
      level: 'blocker',
      title: 'Python 不可用（读微信库的那一步跑不起来）',
      hint: 'Windows 上最常见的是只装了应用商店的 python 占位程序。装一个真正的 Python 3.10+，'
        + '确认 `python --version` 能打出版本号，再重试。',
    })
  } else if (missing.includes('sqlcipher3')) {
    out.push({
      level: 'blocker',
      title: '缺少 sqlcipher3（Python 装了，但解不了加密库）',
      hint: '运行 `pip install sqlcipher3`；Windows 上装的是预编译轮子，不需要自己编 OpenSSL。',
    })
  }

  const otherMissing = missing.filter(name => name !== 'sqlcipher3')
  if (input.python.available && otherMissing.length) {
    out.push({
      level: 'warning',
      title: `缺少可选功能依赖：${otherMissing.join('、')}`,
      hint: '只影响对应功能（抓正文/生成 HTML 等）。按需 `pip install ' + otherMissing.join(' ') + '`。',
    })
  }

  if (!input.configuration.initialized) {
    out.push({
      level: 'blocker',
      title: '还没有初始化（配置里没有可用的数据目录/密钥）',
      hint: '运行 `weflow-cli init`，并保持微信已登录、不要退出。',
    })
  } else if (!input.configuration.messageDatabase) {
    out.push({
      level: 'blocker',
      title: '配置指向的消息库不存在（路径失效或换了账号）',
      hint: '运行 `weflow-cli init --refresh` 重新识别数据目录；换过微信账号时尤其需要。',
    })
  }

  if (!input.keys.messageKeyConfigured && !input.keys.passphraseConfigured) {
    out.push({
      level: 'blocker',
      title: '配置里没有任何数据库密钥/口令（读不出数据是必然的）',
      hint: '运行 `weflow-cli init`；之前失败过就先 `weflow-cli config forget-keys --yes` 再重来。',
    })
  }

  const client = input.wechatClient
  if (client) {
    if (!client.running) {
      out.push({
        level: 'warning',
        title: '微信客户端没有在运行',
        hint: '初始化时的扫描与"检查更新"都需要它。读已配置好的库不需要，但刚才若跑过 init，请启动微信后重试。',
      })
    }
    if (client.dataDirMatchesConfig === false) {
      out.push({
        level: 'warning',
        title: '客户端自报的数据目录与配置里推出来的不是同一份',
        hint: '你可能在读另一个账号/另一份客户端正在写的库。确认后用 `weflow-cli init --refresh` 对齐。',
      })
    }
    for (const dead of client.unusableVersionDirs || []) {
      out.push({
        level: 'info',
        title: `磁盘上有残留版本目录 ${dead.dir}（只剩 ${dead.files} 个文件，没有 Weixin.dll）`,
        hint: '指向它的加载会失败。如果客户端起不来，先看这里；正常情况下可以忽略。',
      })
    }
    if (client.compatibility?.verdict === 'newer-than-verified') {
      out.push({
        level: 'warning',
        title: `本机微信（${client.version}）比本版本验证过的 ${client.compatibility.verifiedVersion} 更新`,
        hint: '如果读不出数据，先怀疑版本变化——去 GitHub 看有没有更新版本的 weflow-cli，'
          + '或把这份报告贴进 issue（版本差就是第一条线索）。',
      })
    }
  }

  if (!out.length) {
    out.push({
      level: 'info',
      title: '没发现明显问题',
      hint: '这不是"保证能用"：具体命令失败时，把那条命令的报错原文一起贴出来。',
    })
  }
  return out.sort((a, b) => LEVEL_ORDER[a.level] - LEVEL_ORDER[b.level])
}

function yesNo(value: boolean | null | undefined): string {
  if (value === null || value === undefined) return '未知'
  return value ? '是' : '否'
}

function depLine(label: string, deps: Record<string, boolean> | undefined): string {
  const entries = Object.entries(deps || {})
  if (!entries.length) return `${label}: （无）`
  return `${label}: ` + entries.map(([name, ok]) => `${name} ${ok ? '✓' : '✗'}`).join(' / ')
}

/**
 * 渲染成一段可以直接粘贴的文本。
 *
 * 刻意用 `【】` 分段、不用表格与颜色：粘到微信/issue/AI 对话框里都不会散架，
 * 而且 `--json` 那份是给机器读的，这份是给人（和 AI）读的，两者不混。
 */
export function renderDiagnosticReport(input: DiagnosticInput): string {
  const lines: string[] = []
  const say = (line = ''): void => { lines.push(redactForReport(line)) }

  say(`weflow-cli 诊断报告  v${input.cliVersion}`)
  say(`生成时间 ${input.generatedAt}`)
  say()
  say('【环境】')
  say(`平台 ${input.platform} ${input.arch} / Node ${input.nodeVersion}`)
  // `python --version` 的输出自带 "Python" 前缀，拼上去会变成 "Python Python 3.11.9"
  const pyVersion = input.python.version.replace(/^python\s*/i, '').trim()
  say(`Python ${input.python.available ? pyVersion || '可用' : '不可用'}`)
  say(`  · 解析到的命令: ${input.python.command || '(未解析)'}`)
  say(depLine('必装依赖', input.dependencies.required))
  if (input.dependencies.optional) say(depLine('可选依赖', input.dependencies.optional))
  say()
  say('【微信客户端】')
  const client = input.wechatClient
  if (!client) {
    say('没读到客户端信息（这一步需要活进程；微信没在跑时是正常的）')
  } else {
    say(`运行中: ${yesNo(client.running)}`)
    say(`加载版本: ${client.version || `未知${client.note ? `（${client.note}）` : ''}`}`)
    if (client.compatibility) {
      const v = client.compatibility.verdict
      const word = v === 'verified' ? '就是验证过的版本'
        : v === 'newer-than-verified' ? '比验证过的更新（读不出数据先怀疑这里）'
        : v === 'older-than-verified' ? '比验证过的更旧（一般兼容）'
        : (process.platform === 'win32'
            ? '无法判定（微信没在运行或探测失败）'
            : `无法判定（版本探测仅支持 Windows，当前平台 ${process.platform}）`)
      say(`与本版本的关系: ${word}（本版本验证过 ${client.compatibility.verifiedVersion}，${client.compatibility.verifiedAt}）`)
    }
    if (client.dataDirs?.length) say(`客户端自报数据目录: ${client.dataDirs.join('、')}`)
    say(`与配置推出来的同一份: ${yesNo(client.dataDirMatchesConfig)}`)
    if (client.unusableVersionDirs?.length) {
      say('残留版本目录: ' + client.unusableVersionDirs.map(d => `${d.dir}(${d.files} 个文件)`).join('、'))
    }
  }
  say()
  say('【配置】')
  say(`已初始化: ${yesNo(input.configuration.initialized)}`)
  say(`消息库存在: ${yesNo(input.configuration.messageDatabase)}`)
  say(`数据库密钥已配置: ${yesNo(input.keys.messageKeyConfigured)}`)
  say(`全库口令已配置: ${yesNo(input.keys.passphraseConfigured)}`)
  say()
  say('【结论】')
  const findings = diagnose(input)
  findings.forEach((f, i) => {
    say(`${i + 1}. [${LEVEL_LABEL[f.level]}] ${f.title}`)
    say(`   → ${f.hint}`)
  })
  if (input.lastError) {
    say()
    say('【最近一次报错原文】')
    say(input.lastError.trim())
  }
  return lines.join('\n')
}
