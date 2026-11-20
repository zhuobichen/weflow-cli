/**
 * 守卫：**评测用例不许漏进运行时代码**。
 *
 * ## 为什么这条比"再多写几个用例"重要
 *
 * 这套评测的全部意义是"量出这个 harness 在真实问法上有多好"。一旦有谁把某条用例的问法、
 * 用例 id、或者它期望的答复片段写进 `src/` 或 `bin/`，那条用例就**永远通过**，而分数仍然
 * 好看——评测从此测的是"代码里有没有抄那句话"，不是能力。这种作弊不需要恶意：
 * 只要有人为了让一条红变绿，把 `'甲最近跟我说了什么'` 塞进某个分支里就发生了。
 *
 * 所以这里钉的是一条**方向性禁令**：运行时代码里不得出现评测集里的问句原文或用例 id。
 * 判据是逐字匹配（问句都是一整句中文，正常代码里不该出现同一串字）。
 *
 * ## 三条不让它变成同义反复的措施
 *
 * 1. **阳性对照**：同一个扫描函数必须能在一份"故意写了问句"的文本里找到命中。
 *    没有这一步，扫描函数的"0 命中"可能只是因为它什么都没读。
 * 2. **反空扫**：必须真的扫到若干个运行时代码文件、且用例数 > 20。
 * 3. **例外只留一条**：`assistantEval*.ts` 是**定义**用例的地方，必须排除；除它之外没有例外。
 *    留一个 `allow` 名单就等于给将来的人开了一个可以随手加名字的口子。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { join, relative } from 'node:path'

const ROOT = process.cwd()
const { EVAL_CASES } = await import('../src/services/assistantEval.js')

/** 运行时目录：会被打进包里、会被助手真正执行的那些 */
const RUNTIME_DIRS = ['src', 'bin']
/** 定义用例的地方本身当然含问句，排除；**除它之外没有例外** */
const DEFINING_FILES = /^(assistantEval|assistantEvalReport|assistantEvalRunner)\.ts$/

function collectRuntimeFiles(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(join(ROOT, dir), { withFileTypes: true })) {
    const rel = join(dir, entry.name)
    if (entry.isDirectory()) out.push(...collectRuntimeFiles(rel))
    else if (entry.isFile() && entry.name.endsWith('.ts') && !DEFINING_FILES.test(entry.name)) out.push(rel)
  }
  return out
}

/**
 * 扫描：给一组"不许出现"的字符串，返回真的出现在运行时代码里的那些。
 * **纯函数式**（文件列表由调用方给），所以阳性对照可以拿假文件来测。
 */
function findLeaks(files: Array<{ path: string; text: string }>, forbidden: string[]): string[] {
  const leaks: string[] = []
  for (const file of files) {
    for (const needle of forbidden) {
      if (!needle) continue
      if (file.text.includes(needle)) leaks.push(`${file.path} 含 ${JSON.stringify(needle)}`)
    }
  }
  return leaks
}

test('扫描函数本身有效：阳性对照必须命中', () => {
  const planted = [{ path: 'fake.ts', text: "if (q === '甲最近跟我说了什么') return x" }]
  const found = findLeaks(planted, ['甲最近跟我说了什么', '不存在的句子'])
  assert.equal(found.length, 1, '植入的那一句必须被找到，否则后面所有"0 命中"都不作数')
  assert.match(found[0], /fake\.ts/)
})

test('运行时代码里没有评测集的问句原文', () => {
  const files = collectRuntimeFiles('src').concat(collectRuntimeFiles('bin'))
    .map(path => ({ path, text: readFileSync(join(ROOT, path), 'utf8') }))
  // 反空扫：读到的文件数与用例数都不该是个位数
  assert.ok(files.length > 20, `只读到 ${files.length} 个运行时代码文件 —— 目录读错了？`)
  assert.ok(EVAL_CASES.length > 20, `只看到 ${EVAL_CASES.length} 条用例 —— 用例表读错了？`)

  const forbidden: string[] = []
  for (const spec of EVAL_CASES) {
    if (spec.question) forbidden.push(spec.question)
    for (const turn of spec.turns ?? []) forbidden.push(turn)
  }
  const leaks = findLeaks(files, forbidden)
  assert.deepEqual(leaks, [],
    '评测问句出现在运行时代码里：那条用例从此永远通过，而分数不再说明任何事\n'
    + leaks.slice(0, 10).join('\n'))
})

/**
 * 用例 id 的检查要**加词边界**，否则一半命中是假的：这个仓库里的 id 很短，`sessions` 会命中
 * `list_sessions`、`time-window` 会命中 `overlapping-time-window`、`no-tool` 会命中
 * `no-tool-call` 这类正常标识符。AGENTS.md 记着同一个教训（42/51 的"匹配"只是长标识符里的
 * 一段子串）。所以：要求两侧都不是 `[A-Za-z0-9_-]`，且长度 ≥ 10（更短的 id 在代码里天然
 * 撞词，检查它的信噪比是负的）。
 */
const ID_MIN_LENGTH = 10
const identifierChar = /[A-Za-z0-9_-]/

export function standaloneHits(text: string, needle: string): boolean {
  let from = 0
  for (;;) {
    const at = text.indexOf(needle, from)
    if (at < 0) return false
    const before = at === 0 ? '' : text[at - 1]
    const after = text[at + needle.length] ?? ''
    if (!identifierChar.test(before) && !identifierChar.test(after)) return true
    from = at + 1
  }
}

function findIdLeaks(files: Array<{ path: string; text: string }>, ids: string[]): string[] {
  const leaks: string[] = []
  for (const file of files) {
    for (const id of ids) {
      if (id.length < ID_MIN_LENGTH) continue
      if (standaloneHits(file.text, id)) leaks.push(`${file.path} 含 ${JSON.stringify(id)}`)
    }
  }
  return leaks
}

test('词边界筛选本身有效：阳性对照命中，长标识符里的子串不误报', () => {
  assert.equal(standaloneHits("case 'ambiguous-contact'", 'ambiguous-contact'), true)
  assert.equal(standaloneHits('function list_sessions() {}', 'sessions'), false, '长标识符里的一段不算')
  assert.equal(standaloneHits("mode: 'overlapping-time-window'", 'time-window'), false, '同上')
  assert.equal(standaloneHits('const x = 1 // no-tool-call', 'no-tool'), false)
})

test('运行时代码里没有用例 id（带词边界，长度够长的那些）', () => {
  const files = collectRuntimeFiles('src').concat(collectRuntimeFiles('bin'))
    .map(path => ({ path, text: readFileSync(join(ROOT, path), 'utf8') }))
  const ids = EVAL_CASES.map((spec: any) => String(spec.id))
  const checked = ids.filter((id: string) => id.length >= ID_MIN_LENGTH)
  assert.ok(checked.length > 10, `只检查了 ${checked.length} 个 id —— 长度阈值写歪了？`)
  const leaks = findIdLeaks(files, ids)
  assert.deepEqual(leaks, [], '用例 id 出现在运行时代码里 = 有分支在按"这是哪条用例"做决定')
})

test('排除名单只覆盖"定义用例"的那三个文件（别留口子）', () => {
  // 这条测的是**排除规则本身**：范围放大到整个 src/services 时，必须只放过定义文件
  const all = readdirSync(join(ROOT, 'src', 'services'))
    .filter((name: string) => name.endsWith('.ts'))
  const excluded = all.filter((name: string) => DEFINING_FILES.test(name))
  assert.ok(excluded.length >= 1 && excluded.length <= 3,
    `排除名单命中了 ${excluded.length} 个文件（${excluded.join('、')}）—— 它只该覆盖定义用例的那几个`)
  assert.ok(excluded.every((name: string) => name.startsWith('assistantEval')),
    '排除名单里出现了非 assistantEval* 的文件：等价于给将来的人开了一个口子')
  // 反向对照：这几个文件**真的**含问句（不然排除它们就没必要，说明名单写歪了）
  const sample = readFileSync(join(ROOT, 'src', 'services', 'assistantEval.ts'), 'utf8')
  assert.ok(EVAL_CASES.some((spec: any) => spec.question && sample.includes(spec.question)),
    '定义文件里居然找不到任何问句？那排除名单写错了对象')
})
