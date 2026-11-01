/**
 * 输出根目录必须可写（Linux 全局安装的坑）。
 *
 * 2026-11-01 在 Ubuntu 上实跑 `weflow-cli daily` 撞到：
 *   PermissionError: [Errno 13] Permission denied: '/usr/lib/node_modules/weflow-cli/output'
 * 包目录属于 root，而输出根默认落在包内。Windows 上全局包在用户目录下，
 * 所以这个问题只在类 Unix 系统暴露——这组测试把"可写"变成契约。
 */
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync, unlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'

/** 每个用例用不同 query 重新导入，绕开模块内的缓存。 */
async function load(caseId: string) {
  return import(`../src/utils/outputRoot.ts?case=${caseId}`)
}

function withEnv<T>(value: string | undefined, fn: () => Promise<T>): Promise<T> {
  const saved = process.env.WEFLOW_OUTPUT_DIR
  if (value === undefined) delete process.env.WEFLOW_OUTPUT_DIR
  else process.env.WEFLOW_OUTPUT_DIR = value
  return fn().finally(() => {
    if (saved === undefined) delete process.env.WEFLOW_OUTPUT_DIR
    else process.env.WEFLOW_OUTPUT_DIR = saved
  })
}

test('WEFLOW_OUTPUT_DIR 优先，且目录会被创建', async () => {
  const tmp = mkdtempSync(join(tmpdir(), 'wf-out-'))
  const target = join(tmp, 'nested', 'out')
  try {
    // **必须在 withEnv 的回调里调用**：`finally` 会把环境变量还原，
    // 在外面调用等于没设过（第一版就错在这里，断言报的是仓库默认目录）。
    const got = await withEnv(target, async () => {
      const { resolveOutputRoot } = await load('env')
      return resolveOutputRoot()
    })
    assert.equal(got, target, '显式指定的目录必须原样使用')
    // 目录存在 = 真的建出来了
    writeFileSync(join(got, 'probe'), '')
    unlinkSync(join(got, 'probe'))
  } finally {
    rmSync(tmp, { recursive: true, force: true })
  }
})

test('默认返回的目录一定可写（这是这条规则的全部意义）', async () => {
  const { resolveOutputRoot } = await withEnv(undefined, () => load('default'))
  const got = resolveOutputRoot()
  writeFileSync(join(got, 'probe'), 'x')
  unlinkSync(join(got, 'probe'))
  assert.ok(got.length > 0)
})

test('同一进程内结果稳定（缓存不导致目录漂移）', async () => {
  const { resolveOutputRoot } = await withEnv(undefined, () => load('stable'))
  assert.equal(resolveOutputRoot(), resolveOutputRoot())
})
