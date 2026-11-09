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

test('qchat 导入存储也落在可写输出根之下（写入方与读取方必须同根）', async () => {
  const { resolveOutputRoot } = await load('import-root')
  const { importRoot } = await import('../src/services/qchatImport.ts')
  assert.equal(importRoot(), join(resolveOutputRoot(), 'imports', 'qq'))
})

test('媒体落地目录也落在可写输出根之下', async () => {
  const { resolveOutputRoot } = await load('media-root')
  const { wechatMediaDir } = await import('../src/services/wechatMedia.ts')
  // 环境变量必须为空才能测默认值
  const saved = process.env.WEFLOW_WECHAT_MEDIA_DIR
  delete process.env.WEFLOW_WECHAT_MEDIA_DIR
  try {
    assert.equal(wechatMediaDir(), join(resolveOutputRoot(), 'wechat-media'))
  } finally {
    if (saved !== undefined) process.env.WEFLOW_WECHAT_MEDIA_DIR = saved
  }
})

test('并发探测不会互相删 probe（固定名会误判为不可写）', async () => {
  // 这条是 PR #15 的实测结论：`.write_probe` 固定名时，并发探测会互相 unlink
  // 对方的文件，后者的 `unlinkSync` 抛 ENOENT 被吞成"不可写"，于是输出根漂移。
  // 他的实测：8 路并发 1600 次里 1400 次误判。
  //
  // 用**多进程**而不是 Promise.all：Node 是单线程，同进程内根本撞不上。
  // 子进程跑 `dist/` 的编译产物（spawn 里没法走 tsx 的 loader）。
  const { spawn } = await import('node:child_process')
  const { mkdtempSync, rmSync, readdirSync } = await import('node:fs')
  const { tmpdir } = await import('node:os')
  const { join } = await import('node:path')
  const tmp = mkdtempSync(join(tmpdir(), 'wf-probe-'))
  const { pathToFileURL } = await import('node:url')
  // Windows 上 ESM 的 import() 不认裸盘符路径，必须给 file:// URL。
  const entry = pathToFileURL(join(process.cwd(), 'dist', 'src', 'utils', 'outputRoot.js')).href
  const code = `import(${JSON.stringify(entry)}).then(m => console.log(m.resolveOutputRoot()))`
  try {
    const runs = Array.from({ length: 12 }, () => new Promise<string>((res) => {
      const child = spawn(process.execPath, ['--input-type=module', '-e', code], {
        env: { ...process.env, WEFLOW_OUTPUT_DIR: tmp },
      })
      let out = ''
      child.stdout.on('data', (d) => { out += d })
      child.on('close', () => res(out.trim()))
    }))
    const outs = await Promise.all(runs)
    assert.deepEqual([...new Set(outs)], [tmp], `并发探测结果不一致: ${[...new Set(outs)].join(' | ')}`)
    const leftovers = readdirSync(tmp).filter((n) => n.startsWith('.write_probe'))
    assert.deepEqual(leftovers, [], `probe 文件残留: ${leftovers.join(',')}`)
  } finally {
    rmSync(tmp, { recursive: true, force: true })
  }
})
