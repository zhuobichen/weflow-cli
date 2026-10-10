/**
 * **概念页住在哪两个目录** —— 一份清单，六个声明处。
 *
 * 用户 2026-09-27 要求把聊天知识库和公众号文章知识库**分开**，于是概念页从
 * 一个目录变成两个：`Wiki/Concepts`（文章线）与 `Chat/Concepts`（聊天线）。
 * 但"分开"这件事要成立，得让**每一个读概念页的地方都读两个**：少读一个不会有任何报错，
 * 只会安静地少一半结果——`vault_search` 少一半命中、助手的 `search_knowledge` 少一半概念、
 * MCP 的 `wechat.get_concept` 少一半概念页，而它们看上去都"正常工作"。
 *
 * 这条清单没法在一处定义再共享（一侧 Python、一侧 TypeScript，外加一个独立的 MCP 包），
 * 所以按本仓库对这类常量的既有做法：**各写各的，由测试钉住必须一致**。
 *
 * 注意这条测试**只解析字面量、不执行代码**（Python 那份跑不了，`bin/` 那份带副作用）。
 * 解析不到声明会直接失败，而不是静默通过——那是这类测试唯一危险的失败模式。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const ROOT = process.cwd()
const read = (...parts: string[]) => readFileSync(join(ROOT, ...parts), 'utf8')

/** 取一段以 `marker` 开头、到下一个 `close` 为止的文字。找不到 -> 抛（不静默）。 */
function block(source: string, marker: string, close: string, file: string): string {
  const at = source.indexOf(marker)
  if (at === -1) throw new Error(`${file}: 找不到声明 ${marker}（测试要跟着改，别让它静默失效）`)
  const end = source.indexOf(close, at + marker.length)
  if (end === -1) throw new Error(`${file}: 声明 ${marker} 没有收尾的 ${close}`)
  return source.slice(at, end + close.length)
}

/**
 * 从一段文字里取出所有引号字符串（单引号或双引号）。
 *
 * TypeScript 那边路径是 `join(PKG_ROOT, 'output', 'wechat-vault', 'Wiki', 'Concepts')` 写出来的，
 * 直接取引号会把它拆成 `Wiki` 和 `Concepts` 两个独立的片段——那样每个都不是一条路径，
 * 过滤后剩空集，而**空集与任何清单比较都是假的**（好在是断言失败，不是静默通过）。
 * 所以先把 `join(...)` 折成它拼出来的那一条路径，再取引号。
 */
function quoted(text: string): string[] {
  const folded = text.replace(/join\(([^()]*)\)/g, (_all, args: string) =>
    "'" + [...args.matchAll(/['"]([^'"]*)['"]/g)].map(m => m[1]).join('/') + "'")
  return [...folded.matchAll(/['"]([^'"]+)['"]/g)].map(m => m[1])
}

/** 路径末尾两段，即 `Wiki/Concepts` 这种相对形式。 */
const tail2 = (p: string) => p.replace(/\\/g, '/').split('/').slice(-2).join('/')

/** `Wiki/Concepts` / `Chat/Concepts`，按出现顺序去重。 */
function conceptsOnly(paths: string[]): string[] {
  return [...new Set(paths.map(tail2).filter(p => /^(Wiki|Chat)\/Concepts$/.test(p)))]
}

/** 源头：`scripts/_utils.py` 的 `KNOWLEDGE_LINES`（每条线 = 线 id + 根 + 线内概念目录）。
 *  2026-10-10 起概念目录由它派生（`CONCEPT_DIRS = concept_dirs()`）—— 条目里的括号会把
 *  `block(..., ')')` 提前截断，所以结束标记用下一段的开头。 */
function fromUtils(): string[] {
  const text = block(read('scripts', '_utils.py'), 'KNOWLEDGE_LINES = (', '\nCONCEPT_DIRS', 'scripts/_utils.py')
  return conceptsOnly(quoted(text))
}

test('六个声明处对「概念页目录」的清单完全一致', () => {
  const expected = fromUtils()
  // 清单本身先自检：解析空了或只剩一个，后面的比较就成了同义反复
  assert.deepEqual(expected, ['Wiki/Concepts', 'Chat/Concepts'],
    'scripts/_utils.py 的 CONCEPT_DIRS 是这份清单的源头')

  // 只列**各自重新声明了一份清单**的地方 —— 它们才是能漂移的。
  // 那两处 `import CONCEPT_DIRS` 的（`vault_search` / `vault_rag`）漂不了，但可能**不再用它**，
  // 那种失效由下面那条测试管。
  const decls: Array<[string, () => string[]]> = [
    ['scripts/_utils.py（源头）', fromUtils],
    ['scripts/create_reading_notes.py（init 建的目录）', () => conceptsOnly(quoted(
      block(read('scripts', 'create_reading_notes.py'), 'VAULT_DIRS = [', ']', 'scripts/create_reading_notes.py')))],
    ['src/services/assistantTools.ts（助手检索与概念邻居）', () => conceptsOnly(quoted(
      block(read('src', 'services', 'assistantTools.ts'), 'const vaultDirs = (): string[] => {', '}', 'src/services/assistantTools.ts')))],
    ['mcp-server/index.ts（MCP 的概念工具）', () => conceptsOnly(quoted(
      block(read('mcp-server', 'index.ts'), 'const VAULT_WIKI_DIRS = [', ']', 'mcp-server/index.ts')))],
    ['bin/weflow-cli.ts（init 预览里建的目录）', () => conceptsOnly(quoted(
      block(read('bin', 'weflow-cli.ts'), 'const dirs = [', ']', 'bin/weflow-cli.ts')))],
  ]

  for (const [who, get] of decls) {
    assert.deepEqual(get(), expected, `${who} 声明了别的目录清单——两个知识库分开之后，少一个目录不会报错，只会少一半结果`)
  }
})

test('图谱那两个脚本读同一份清单，而不是自己硬编码两条路径', () => {
  // 2026-10-10 之前它们把 `vault / 'Wiki' / 'Concepts'` 与 `vault / 'Chat' / 'Concepts'` 写死，
  // 而它们**不在**上面那五处声明里 ⇒ 清单改了图不会变，症状是"图里少一半节点"，且不报错。
  for (const file of ['graph_3d.py', 'graph_2d.py']) {
    const text = read('scripts', file)
    assert.ok(text.includes('KNOWLEDGE_LINES'),
      `${file} 没有读 KNOWLEDGE_LINES：硬编码的那份不会跟着清单走`)
    assert.ok(!/'Wiki'\s*\/\s*'Concepts'/.test(text) && !/'Chat'\s*\/\s*'Concepts'/.test(text),
      `${file} 里还留着硬编码的概念路径`)
  }
})

test('导入 CONCEPT_DIRS 的那两个脚本，确实在遍历它', () => {
  // 它们共用源头，所以清单不会漂；会坏的是"导入了却没拿它去遍历"——那时搜的仍然是空气，
  // 而且因为 `CONCEPT_DIRS` 还在 import 列表里，读代码的人看不出问题。
  for (const script of ['vault_search.py', 'vault_rag.py']) {
    const source = read('scripts', script)
    assert.match(source, /for relative in CONCEPT_DIRS:/,
      `${script} 导入了 CONCEPT_DIRS 却没有遍历它——分出去的那一半会静默搜不到`)
  }
})

test('compile_wiki 一次只写一条线：默认文章线，另一条只用来查重', () => {
  // `compile_wiki` 的默认输出是**文章线**；聊天线靠 `--output` 显式指过去
  // （`Chat/00-Overview.md` 就是这么生成的）。默认值要是漂到 `Chat/`，文章线会被写进聊天目录。
  const source = read('scripts', 'compile_wiki.py')
  assert.match(source, /OUTPUT_ROOT = 'output\/wechat-vault\/Wiki\/Concepts'/,
    'compile_wiki 的默认输出目录是文章知识库')
  // 它确实会碰 CONCEPT_DIRS —— 但**只用来查重**（另一个目录里已有同名页就不重复建，
  // 否则 `[[DeepSeek]]` 在 Obsidian 里会同时命中两张页）。所以这里钉的不是"不许提"，
  // 而是"提它的那个函数不许写文件"。
  assert.match(source, /sibling_concept_dirs/, 'compile_wiki 要拿另一条线的目录来查重')
  // 取到下一个函数定义为止（用函数名当收尾标记，避免在字符串里写换行转义）
  const helper = block(source, 'def sibling_concept_dirs(', 'def build_jobs(', 'scripts/compile_wiki.py')
  for (const forbidden of ['write_text', 'open(', 'mkdir', 'write_with_frontmatter']) {
    assert.ok(!helper.includes(forbidden),
      `sibling_concept_dirs 只该回答"另一条线在哪"，不该动它（出现了 ${forbidden}）`)
  }
})
