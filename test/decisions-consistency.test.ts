/**
 * `docs/DECISIONS.md` 的一致性（2026-11-24）。
 *
 * 由来：这一轮推送前做最后检查，发现提交信息里写着"（D-176 ~ D-180）"，
 * 而 `DECISIONS.md` 只记到 **D-176**——后四条根本没写。**靠人记得检查是不可靠的**：
 * 那次是我恰好看了一眼，不是机制在拦。
 *
 * 这个文件把「账不平」变成会红的测试：
 *
 * 1. **代码里引用的 D-编号必须存在**：注释写着"见 D-【不存在的号】"而文档里没有，下一个人会找半天。
 *    （这段注释自己就被 G-引用检查抓过一次：当初在这里写了个假编号当例子，
 *    于是 `test/decision-refs.test.ts` 把它报成了悬空引用——**例子也不能用真格式**。）
 * 2. **编号不重复**：同一编号写两次是笔误，会让"见 D-176"指向两个不同的决策。
 * 3. **新决策必须写理由**：只写结论不写理由，下一个人没法判断它还能不能改。
 *
 * ## 两条**已知的历史偏差**（不假装它们合规）
 *
 * 第一次跑这个测试时，历史条目立刻暴露出两个既成事实：
 *
 * - **编号有历史跳号**（D-47 之后直接 D-126、D-146→D-150 等）：那些编号是别的分支/废弃
 *   草稿用掉的。**回头补写假条目比留着更难查**，所以不补；只在 `KNOWN_GAPS` 里登记，
 *   并要求**新编号从此刻起连续**。
 * - **早期条目没有 `**Decision.** / **Reason.**` 段**（最初那批用的是别的写法）：改成新
 *   格式等于重写历史。同样登记，只要求 D-168 之后（也就是本仓开始用这套格式的地方）必须写。
 *
 * 这两条都**不会自己变好**，也不该被一次"顺手格式化"掩盖。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const DECISIONS = join(ROOT, 'docs', 'DECISIONS.md')
const text = readFileSync(DECISIONS, 'utf8')

/**
 * 已知的历史跳号（`D-x` 到 `D-y` 之间缺号）。**只许变短，不许变长**——
 * 新增条目要是再跳号，下面那条测试就会红。
 */
const KNOWN_GAPS: Array<[number, number]> = [
  [8, 10], [43, 45], [44, 46], [47, 126], [48, 127], [146, 150], [150, 155],
]

/** 从这一条开始要求 `**Decision.** / **Reason.**` 两段（本仓开始统一用这套格式的地方） */
const REASON_FORMAT_FROM = 168

function decisionNumbers(): number[] {
  const out: number[] = []
  for (const line of text.split(/\r?\n/)) {
    const m = line.match(/^## D-(\d{3}):/)
    if (m) out.push(Number(m[1]))
  }
  return out
}

test('代码里引用的 D-编号，文档里必须存在', () => {
  const known = new Set(decisionNumbers())
  assert.ok(known.size > 100, `只找到 ${known.size} 条决策，解析可能坏了`)
  const missing: string[] = []
  for (const file of sourceFiles(join(ROOT, 'src')).concat(sourceFiles(join(ROOT, 'bin')))) {
    const src = readFileSync(file, 'utf8')
    for (const m of src.matchAll(/D-(\d{3})/g)) {
      if (!known.has(Number(m[1]))) missing.push(`${file.slice(ROOT.length + 1)} 引用了 D-${m[1]}`)
    }
  }
  assert.deepEqual([...new Set(missing)], [],
    '引用了不存在的决策：' + [...new Set(missing)].slice(0, 5).join('；'))
})

test('编号不重复', () => {
  const counts = new Map<number, number>()
  for (const n of decisionNumbers()) counts.set(n, (counts.get(n) ?? 0) + 1)
  const dups = [...counts].filter(([, c]) => c > 1).map(([n, c]) => `D-${n} 出现 ${c} 次`)
  assert.deepEqual(dups, [], '重复编号：' + dups.join('；'))
})

test('新增编号不跳号（历史遗留的跳号已在 KNOWN_GAPS 登记）', () => {
  const nums = decisionNumbers().sort((a, b) => a - b)
  const newGaps: string[] = []
  for (let i = 1; i < nums.length; i += 1) {
    const prev = nums[i - 1]
    const cur = nums[i]
    if (cur <= prev + 1) continue
    // 这段区间要是"已知的历史跳号"就放过（比较**区间**，不是单个数字）
    const knownHere = KNOWN_GAPS.some(([from, to]) => from === prev && to === cur)
    if (!knownHere) newGaps.push(`D-${prev} 之后直接跳到 D-${cur}`)
  }
  assert.deepEqual(newGaps, [],
    '新增了跳号（"打算写但忘了"，或者合并时删掉了一条）：' + newGaps.join('；'))
})

test(`D-${REASON_FORMAT_FROM} 之后的决策都写了理由（Decision. + Reason.）`, () => {
  const blocks = text.split(/^(?=## D-\d{3}:)/m).filter((b) => /^## D-\d{3}:/.test(b))
  const weak: string[] = []
  for (const b of blocks) {
    const id = (b.match(/D-(\d{3})/) ?? ['', '0'])[1]
    if (Number(id) < REASON_FORMAT_FROM) continue   // 早期条目不追责，见文件头
    const hasDecision = /\*\*Decision\.\*\*/.test(b)
    const hasReason = /\*\*Reason\.\*\*/.test(b)
    if (!hasDecision || !hasReason) {
      weak.push(`D-${id}（缺 ${[!hasDecision && 'Decision.', !hasReason && 'Reason.'].filter(Boolean).join('、')}）`)
    }
  }
  assert.deepEqual(weak, [], '这些新决策没写理由：' + weak.slice(0, 8).join('；'))
})

/** 递归收集源码（只看 src/ 与 bin/，不碰 test/ dist/ node_modules） */
function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name === '.git' || name === 'dist') continue
    const full = join(dir, name)
    if (statSync(full).isDirectory()) sourceFiles(full, out)
    else if (/\.(ts|mts|cts)$/.test(name)) out.push(full)
  }
  return out
}
