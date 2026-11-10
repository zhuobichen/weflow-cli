/**
 * 输出根目录：**必须可写**。
 *
 * 为什么单独一个模块：全局安装时包目录属于 root（Linux `/usr/lib/node_modules`、
 * macOS `/usr/local/lib`），往里面写 `output/` 会直接 `PermissionError`。
 * Windows 上全局包在用户目录下，所以这个坑只在类 Unix 系统暴露——
 * 2026-11-01 在 Ubuntu 上用 `weflow-cli daily` 实跑时撞到：
 *
 *     PermissionError: [Errno 13] Permission denied: '/usr/lib/node_modules/weflow-cli/output'
 *
 * 顺序：
 *   1. `WEFLOW_OUTPUT_DIR`（显式指定；测试与定制都用它）
 *   2. 包根下的 `output/`——**可写时保持原位置**，Windows 与源码检出的老用法完全不变
 *   3. 都不可写就退回 `~/.weflow-cli/output`
 */
import { mkdirSync, unlinkSync, writeFileSync } from 'node:fs'
import { randomUUID } from 'node:crypto'
import { homedir } from 'node:os'
import { join } from 'node:path'

import { resolvePackageRoot } from './packageRoot.js'

let cached: string | null = null

/** 真正探测一次可写性，而不是看权限位（root 下权限位会说谎）。 */
function isWritable(dir: string): boolean {
  try {
    mkdirSync(dir, { recursive: true })
    // probe 文件名**必须唯一**（@fcmyoo 在 PR #15 指出并实测）：
    // 并发探测时（`node --test` 每个测试文件一个子进程，都在探测同一个输出根）
    // 固定名会互相 unlink 对方的 probe，后者的 `unlinkSync` 抛 ENOENT，
    // 被 catch 吞成"不可写"——于是输出根在竞态窗口里漂到家目录回退，
    // 测试与真实行为一起变得不可预测。他的实测：8 路并发 1600 次里 1400 次误判。
    const probe = join(dir, `.write_probe.${process.pid}.${randomUUID()}`)
    try {
      writeFileSync(probe, '')
      unlinkSync(probe)
    } catch (error) {
      // 写成了但删失败（不该发生）也别当成"不可写"——那是清理问题，不是权限问题。
      try { unlinkSync(probe) } catch { /* 已被别的进程/自己删掉 */ }
      throw error
    }
    return true
  } catch {
    return false
  }
}

export function resolveOutputRoot(): string {
  // **先看环境变量，且不缓存它**：缓存会把"先按默认跑过一次"的进程钉在旧目录上，
  // 测试与"运行中改配置"都会因此失效（第一版就踩了，测试当场红）。
  const env = process.env.WEFLOW_OUTPUT_DIR
  if (env) {
    mkdirSync(env, { recursive: true })
    return env
  }
  if (cached) return cached
  // `resolvePackageRoot` 需要本模块的 URL（它靠它向上找 package.json）
  const repo = join(resolvePackageRoot(import.meta.url), 'output')
  if (isWritable(repo)) {
    cached = repo
    return repo
  }
  const fallback = join(homedir(), '.weflow-cli', 'output')
  mkdirSync(fallback, { recursive: true })
  cached = fallback
  return fallback
}
