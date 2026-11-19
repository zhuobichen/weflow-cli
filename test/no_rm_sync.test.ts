/**
 * 守卫：`src/` 里不许直接调 `rmSync` / `rmdirSync`。
 *
 * 这条测试来自一次真实故障（2026-11-19）：本机（Windows + 沙箱）下
 * `rmSync(file, { force: true })` 与 `rmdirSync(dir, { recursive: true })` **都不抛错、
 * 且什么都没删**，只有 `unlinkSync(file)` 和 `rmdirSync(空目录)` 真的生效。
 *
 * 这不是"多留个垃圾文件"那么轻——删除失败会让上层语义全错：
 * - `stopDaemon()` 报"已停止"，pid 文件还在；
 * - 原子写退化路径留下 `.tmp`，下一次读到的还是旧内容；
 * - 测试的 cleanup 删不掉夹具，后面几条用例集体变成"已经在运行"。
 *
 * 三处症状当时表现为 5 条用例一起红，且**红的原因和被测逻辑无关**——最难查的那种。
 * 所以把纪律钉在这里：要删文件走 `src/utils/quietFs.ts`，别把 `rmSync` 写回来。
 *
 * 只扫 `src/`：`test/` 里还有几十处 rmSync 用于清理临时目录，泄漏但不影响正确性，
 * 一次性全改属于另一件事（改动量比收益大）。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

const ROOT = process.cwd()
const SRC = join(ROOT, 'src')
/** 唯一允许出现 rmdirSync 的地方：它就是这条纪律的落点，且用的是非递归形态 */
const EXEMPT = join('utils', 'quietFs.ts')

/** 递归收集 .ts 文件（跳过 node_modules 之类不在 src 下，故只需判目录） */
function collect(dir: string): string[] {
  const out: string[] = []
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) out.push(...collect(full))
    else if (name.endsWith('.ts')) out.push(full)
  }
  return out
}

/**
 * 去掉行注释与块注释，避免把解释「为什么不用 rmSync」的注释当成违规调用。
 *
 * 按 `/\r?\n/` 切分，不是 `'\n'`：仓库里这些文件是 CRLF，行尾的 `\r` 会让
 * `/\/\/.*$/` 里的 `$`（非多行模式下只匹配字符串末尾）匹配不上，行注释于是**原样留下**——
 * 一条"本该清掉注释"的正则静默失效，守卫就会冲着注释报违规。第一次写这条测试时就是这么错的。
 */
function stripComments(code: string): string {
  return code
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split(/\r?\n/)
    .map(line => line.replace(/\/\/.*$/, ''))
    .join('\n')
}

test('src/ 里没有 rmSync / rmdirSync 调用（本机这两个静默不删，见 quietFs.ts）', () => {
  const files = collect(SRC)
  // 反空扫：扫不到文件时"0 处违规"是假绿
  assert.ok(files.length > 20, `只扫到 ${files.length} 个 src/*.ts —— 目录读错了？`)

  const offenders: string[] = []
  for (const file of files) {
    if (relative(SRC, file) === EXEMPT) continue   // 见 EXEMPT 注释
    const code = stripComments(readFileSync(file, 'utf8'))
    // 只看**调用**，不看 import：import 列表里出现名字本身不构成删除
    for (const m of code.matchAll(/\b(rmSync|rmdirSync)\s*\(/g)) {
      const line = code.slice(0, m.index).split('\n').length
      offenders.push(`${relative(ROOT, file)}:${line} → ${m[1]}(`)
    }
  }
  assert.deepEqual(offenders, [],
    '要删文件请用 src/utils/quietFs.ts 的 removeFileQuietly / removeDirQuietly')
})

test('quietFs.ts 自己用的是 unlinkSync 与非递归 rmdirSync', () => {
  const code = stripComments(readFileSync(join(SRC, 'utils', 'quietFs.ts'), 'utf8'))
  assert.match(code, /unlinkSync\(/, '单文件删除必须走 unlinkSync')
  assert.match(code, /rmdirSync\(/, '目录删除必须走 rmdirSync（非递归）')
  assert.doesNotMatch(code, /\brmSync\s*\(/, 'quietFs 自己也不许用 rmSync')
})
