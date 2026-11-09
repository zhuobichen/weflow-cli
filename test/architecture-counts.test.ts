/**
 * `ARCHITECTURE.md`「代码边界」表里那几个**数字**，必须等于从源码数出来的值。
 *
 * 为什么值得一条守卫：那些数字**没有任何东西看着**（2026-10-15 实测：`ARCHITECTURE.md` 被 0 条测试
 * 读取），所以它们只会朝一个方向走 —— 停在被写下的那一天。当时实测已烂掉 3 处：
 * `test/` 报 118（80 TS / 38 Python）而实际 138（92 / 46），偏差 17%；`scripts/` 报 59 实际 61；
 * `bin/` 报 113 处 `.command(` 实际 114。
 *
 * 坏处不是"数字不好看"，而是**它会被抄走**：2026-10-15 那次 `/init-project` 里，子代理忠实引用了
 * 118 / 80 / 38，把它写进了根 `CLAUDE.md` 与 `test/CLAUDE.md` —— 一条过时的断言就这样扩散到三个文件，
 * 而**没有任何东西会红**。这正是本仓库对这类跨文件常量的既有做法（见 `config-keys.test.ts`、
 * `concept-dirs-agreement.test.ts`、`tool-registry.test.ts`）：各写各的，由测试钉住必须一致。
 *
 * 做法一律是**从源码里数**，不是把文档里的值再抄一遍 —— 抄一遍就是"字段和测试一起错、谁都不响"
 * （`tool-registry.test.ts` 的文件头记着这个仓库吃过的亏）。解析不到会**直接失败**，而不是静默通过：
 * 那是这类测试唯一危险的失败模式。
 *
 * ⚠️ 只钉**精确的、数得出来的**断言。`scripts/` 那行的"约 2.8 万行"是概数，不钉 —— 钉概数只会制造
 * 无价值的 diff。`src/services/` 那行的"31 个工具"钉的是 `TOOL_DEFS` 的长度；注意它与
 * `docs/EXTENDING.md` 的"32 tools"**不是同一个量**（那一处是 MCP 暴露面 = 31 − 3 排除 + 4 手写），
 * 两者都对，别把它们拉到一起。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { TOOL_DEFS } from '../src/services/assistantTools.js'

const ROOT = process.cwd()
const read = (...parts: string[]) => readFileSync(join(ROOT, ...parts), 'utf8')

const architecture = read('ARCHITECTURE.md')

/**
 * 从 `ARCHITECTURE.md` 里取一处数字断言。取不到 -> 抛。
 * 不静默返回 `null`：范文改写后若正则失配，"0 处命中"会让整条测试变成同义反复而仍然全绿。
 */
function claim(re: RegExp, what: string): number {
  const match = architecture.match(re)
  if (!match) throw new Error(`ARCHITECTURE.md 里找不到「${what}」的计数（改写了？守卫要跟着改，别让它静默失效）`)
  return Number(match[1])
}

/** 直接子文件里，按后缀匹配的数量。只数文件，不数目录。 */
function countBySuffix(dir: string, test: (name: string) => boolean): number {
  return readdirSync(join(ROOT, dir), { withFileTypes: true })
    .filter(entry => entry.isFile() && test(entry.name))
    .length
}

test('ARCHITECTURE.md 的 test/ 文件数 = 磁盘上真实数量（TS 与 Python 都要对）', () => {
  const total = claim(/(\d+) 个文件：\d+ TS \/ \d+ Python/, 'test/ 文件总数')
  const ts = claim(/个文件：(\d+) TS \//, 'test/ 的 TS 数量')
  const py = claim(/个文件：\d+ TS \/ (\d+) Python/, 'test/ 的 Python 数量')

  const realTs = countBySuffix('test', name => name.endsWith('.test.ts'))
  const realPy = countBySuffix('test', name => name.endsWith('_test.py'))

  // 反空扫：这两个数不可能是个位数（`npm test` 的 glob 与 Python 的 discover 都指向这个目录）
  assert.ok(realTs > 20 && realPy > 10,
    `只数到 ${realTs} 个 TS / ${realPy} 个 Python —— 目录读错了？`)
  // 分解必须与总数自洽，否则三个数各自"对"、合起来仍然在骗人
  assert.equal(ts + py, total, 'doc 里的分解（TS + Python）加起来不等于总数')
  assert.equal(ts, realTs, `ARCHITECTURE.md 说 ${ts} 个 TS 测试，磁盘上是 ${realTs} 个`)
  assert.equal(py, realPy, `ARCHITECTURE.md 说 ${py} 个 Python 测试，磁盘上是 ${realPy} 个`)
})

test('ARCHITECTURE.md 的 scripts/ 工作流数 = 磁盘上的 *.py 数量', () => {
  const claimed = claim(/(\d+) 个 Python 工作流/, 'scripts/ 的 Python 工作流')
  const real = countBySuffix('scripts', name => name.endsWith('.py'))
  assert.ok(real > 20, `只数到 ${real} 个 .py —— 目录读错了？`)
  assert.equal(claimed, real, `ARCHITECTURE.md 说 ${claimed} 个 Python 工作流，磁盘上是 ${real} 个`)
})

test('ARCHITECTURE.md 的 bin/ 命令数 = 源码里 .command( 的出现次数', () => {
  const claimed = claim(/(\d+) 处 `\.command\(`/, 'bin/ 的 .command( 次数')
  // 数**出现次数**而不是行数：一行可以挂两个 `.command(`，按行数会少报。
  const real = (read('bin', 'weflow-cli.ts').match(/\.command\(/g) ?? []).length
  assert.ok(real > 20, `只数到 ${real} 处 .command( —— 文件读错了？`)
  assert.equal(claimed, real, `ARCHITECTURE.md 说 ${claimed} 处 .command(，实际是 ${real} 处`)
})

test('ARCHITECTURE.md 的助手工具数 = TOOL_DEFS 的长度', () => {
  const claimed = claim(/(\d+) 个工具/, 'src/services/ 的助手工具数')
  assert.equal(claimed, TOOL_DEFS.length,
    `ARCHITECTURE.md 说 ${claimed} 个工具，声明表里是 ${TOOL_DEFS.length} 个`)
})
