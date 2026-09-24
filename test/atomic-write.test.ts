/**
 * 原子写的**忙重试与兜底**。这条是真事故的回归：一次全量测试里，记忆文件保存之后
 * 文件里没有 `version`——不是测试的毛病，是 `renameSync` 在 Windows 上覆盖一个
 * **正被别的句柄打开**的文件会 EPERM（实测：只读打开就够触发，句柄一关就成功）。
 * 杀毒、索引器、另一个读者都会短暂造成这个状态，而调用方（`AssistantMemory.save()`）
 * catch 掉只记一个原因，于是"存上了"与"根本没存"在界面上看不出区别。
 *
 * 断言特意写成**跨平台成立**的：不管走的是改名还是兜底写，结果都必须是"内容更新了、
 * 没留下 .tmp"。在 Linux 上它走改名那条路、在 Windows 上多半走兜底，两条都得绿。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { closeSync, existsSync, mkdtempSync, openSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const { writeFileAtomic } = await import('../src/utils/atomicWrite.js')

function scratch(): string {
  return mkdtempSync(join(tmpdir(), 'weflow-atomic-'))
}

test('正常写：内容到位，不留下 .tmp', () => {
  const dir = scratch()
  try {
    const target = join(dir, 'a.json')
    writeFileAtomic(target, '{"v":1}')
    assert.equal(readFileSync(target, 'utf8'), '{"v":1}')
    assert.equal(existsSync(target + '.tmp'), false)
    writeFileAtomic(target, '{"v":2}')          // 覆盖已有的
    assert.equal(readFileSync(target, 'utf8'), '{"v":2}')
    assert.equal(existsSync(target + '.tmp'), false)
  } finally { rmSync(dir, { recursive: true, force: true }) }
})

test('目标正被**别的句柄**打开时，内容照样写得进去、且不留 .tmp', () => {
  // 这就是那个事故的形态。Windows 上 rename 会 EPERM → 走重试与兜底写；
  // Linux 上 rename 直接成功。两条路都必须把内容写进去。
  const dir = scratch()
  try {
    const target = join(dir, 'b.json')
    writeFileSync(target, '{"old":true}')
    const reader = openSync(target, 'r')        // 另一个句柄，只读
    try {
      writeFileAtomic(target, '{"new":true}')
      assert.equal(readFileSync(target, 'utf8'), '{"new":true}', '内容必须更新')
      assert.equal(existsSync(target + '.tmp'), false, '不许留下临时文件')
    } finally { closeSync(reader) }
  } finally { rmSync(dir, { recursive: true, force: true }) }
})

test('写不进去就抛，不吞（调用方自己决定要不要记成"保存失败"）', () => {
  const dir = scratch()
  try {
    // 目标目录不存在：连临时文件都写不了
    assert.throws(() => writeFileAtomic(join(dir, 'nope', 'c.json'), '{}'))
  } finally { rmSync(dir, { recursive: true, force: true }) }
})

test('内存里的三处原子写都走了这个 helper（不再各写一份 tmp+rename）', () => {
  const files = [
    'src/services/assistantMemory.ts',
    'src/services/configService.ts',
    'src/panel/endpoint.ts',
  ]
  for (const f of files) {
    const text = readFileSync(join(process.cwd(), f), 'utf8')
    assert.match(text, /writeFileAtomic/, `${f} 应当用 writeFileAtomic`)
  }
})
