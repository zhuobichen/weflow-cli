import { existsSync, mkdirSync, readFileSync, unlinkSync, writeFileSync } from 'fs'
import { join } from 'path'
import os from 'os'

/**
 * 记录"上一次 `init` 为什么没成功"。
 *
 * 为什么需要它（GitHub issue #13，2026-11-02）：
 * 用户跑过 `weflow-cli init`，但它中途退出、什么也没写进配置。之后 `check` 只说
 * "未初始化 — 运行 weflow-cli init"，用户读到的却是"似乎无法获取密钥"——
 * 因为**工具没有区分"从没跑过"和"跑过但失败"**，更没留下失败原因。
 *
 * 存在独立文件里，而不是写进 config.json：
 * - config.json 是用户配置，混进诊断状态会让迁移/比对逻辑变复杂；
 * - 这个状态随时可以删，删了只是回到"从没跑过"的表达。
 */

const DIR = join(os.homedir(), '.weflow-cli')
const FILE = join(DIR, 'init-state.json')

export interface InitFailure {
  /** 退出码（`process.exitCode`），便于判断是主动退出还是崩溃 */
  code: number
  /** 失败前最后一条用户可见的提示 */
  message: string
  /** ISO 时间 */
  at: string
}

export function initStateFile(): string {
  return FILE
}

export function readInitFailure(): InitFailure | null {
  try {
    if (!existsSync(FILE)) return null
    const parsed = JSON.parse(readFileSync(FILE, 'utf8'))
    if (!parsed || typeof parsed !== 'object') return null
    if (typeof parsed.message !== 'string') return null
    return {
      code: typeof parsed.code === 'number' ? parsed.code : 1,
      message: parsed.message,
      at: typeof parsed.at === 'string' ? parsed.at : '',
    }
  } catch {
    // 文件坏了就当"没有记录"——诊断信息不值得让 check 崩掉
    return null
  }
}

export function recordInitFailure(code: number, message: string): void {
  try {
    mkdirSync(DIR, { recursive: true })
    const payload: InitFailure = {
      code,
      message: message.trim().slice(0, 500),
      at: new Date().toISOString(),
    }
    writeFileSync(FILE, JSON.stringify(payload) + '\n', 'utf8')
  } catch {
    // 写不进去不影响 init 本身的行为
  }
}

export function clearInitFailure(): void {
  try {
    if (existsSync(FILE)) unlinkSync(FILE)
  } catch {
    // 同上
  }
}

/** 给 `check` 用的三行说明；失败原因缺失时返回 null（调用方走旧文案）。 */
export function describeInitFailure(failure: InitFailure | null): string[] | null {
  if (!failure) return null
  const when = failure.at ? new Date(failure.at) : null
  const time = when && !Number.isNaN(when.getTime())
    ? when.toLocaleString('zh-CN', { hour12: false })
    : '（时间未知）'
  return [
    `  ○ 未初始化 — 上次 init 没有成功（${time}，退出码 ${failure.code}）`,
    `    最后的提示: ${failure.message}`,
    '    修掉上面这条，再重跑: weflow-cli init',
  ]
}

/**
 * 挂一个"init 结束时自动记账"的钩子。
 *
 * 用退出码判定成败，不去改七处 `process.exit(1)`：
 * - 退出码 0 → 清掉旧记录（这次成功了）
 * - 退出码非 0 → 记下最后一条输出 + 退出码
 *
 * 只截 `console.log` 的最后一行，因此对正常输出零影响（原样转发）。
 */
export function trackInitRun(): void {
  let last = ''
  const original = console.log.bind(console)
  console.log = (...args: any[]) => {
    const text = args.map((v) => (typeof v === 'string' ? v : '')).join(' ').trim()
    if (text) last = text.replace(/\s+/g, ' ')
    original(...(args as []))
  }
  process.once('exit', (code) => {
    console.log = original
    if (code === 0) {
      clearInitFailure()
      return
    }
    recordInitFailure(code, last || '（没有可读的提示输出）')
  })
}
