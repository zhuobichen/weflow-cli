/**
 * 概念页的**邻居** —— 图谱在 Agent 侧的落地。
 *
 * Obsidian 的关系图谱是人眼视图，助手那条路一直读不到"图"。概念页里本来就写着它的邻居，
 * 把它们连同各自的一句定义一起给出去，"查一个概念"就变成"拿到一小块子图"。
 *
 * 这里钉两件事：**给出去的东西对不对**（邻居、定义、没页的要报数），以及
 * **名字必须消毒**（邻居名来自文件内容，直接拼进路径就能走出目录）。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { conceptNeighbors, conceptBrief, safeConceptFile } from '../src/services/assistantTools.js'

function vault(files: Record<string, string>): string {
  const dir = mkdtempSync(join(tmpdir(), 'wiki-'))
  mkdirSync(dir, { recursive: true })
  for (const [name, text] of Object.entries(files)) writeFileSync(join(dir, name), text, 'utf8')
  return dir
}

const page = (name: string, neighbors: string[], definition = '这是一句定义。') =>
  `---\ntitle: "${name}"\n---\n\n# ${name}\n\n${definition}\n\n## 相关概念\n\n` +
  neighbors.map(n => `- [[${n}]]\n`).join('')

test('邻居连同各自的一句定义一起给出来', () => {
  const dir = vault({
    '甲.md': page('甲', ['乙', '丙']),
    '乙.md': page('乙', [], '乙是第一个邻居。'),
    '丙.md': page('丙', [], '丙是第二个邻居。'),
  })
  const { linked, missing } = conceptNeighbors('甲', dir)
  assert.deepEqual(linked, [
    { name: '乙', brief: '乙是第一个邻居。' },
    { name: '丙', brief: '丙是第二个邻居。' },
  ])
  assert.equal(missing, 0)
  rmSync(dir, { recursive: true, force: true })
})

test('没有页的邻居只报数_不返回一个取不到的名字', () => {
  const dir = vault({
    '甲.md': page('甲', ['乙', '还没建页的概念', '另一个也没建']),
    '乙.md': page('乙', [], '乙的定义。'),
  })
  const { linked, missing } = conceptNeighbors('甲', dir)
  assert.deepEqual(linked.map(l => l.name), ['乙'])
  assert.equal(missing, 2, '没页的要报出来，否则模型会去追一个取不到的名字')
  rmSync(dir, { recursive: true, force: true })
})

test('页里没有相关概念段时不炸', () => {
  const dir = vault({ '甲.md': '---\ntitle: "甲"\n---\n\n# 甲\n\n只有定义。\n' })
  assert.deepEqual(conceptNeighbors('甲', dir), { linked: [], missing: 0 })
  assert.deepEqual(conceptNeighbors('不存在', dir), { linked: [], missing: 0 })
  rmSync(dir, { recursive: true, force: true })
})

test('定义取的是正文第一句_不是 frontmatter 或标题', () => {
  const text = '---\ntitle: "某概念"\ncreated: 2026-01-01\n---\n\n# 某概念\n\n这才是定义。\n\n## 关键要点\n'
  assert.equal(conceptBrief(text), '这才是定义。')
  // 没有正文定义时如实说，不编
  assert.equal(conceptBrief('---\ntitle: "x"\n---\n\n# x\n'), '(这页没写定义)')
})

test('文件名要消毒_邻居名里带路径分隔符走不出目录', () => {
  // 邻居名是从**文件内容**读出来的，拼进路径前必须消毒
  assert.equal(safeConceptFile('正常名字'), '正常名字.md')
  assert.equal(safeConceptFile('../../etc/passwd'), '.._.._etc_passwd.md')
  assert.equal(safeConceptFile('a/b\\c'), 'a_b_c.md')
  assert.equal(safeConceptFile('x'.repeat(200)).length, 63, '截到 60 字再加 .md')
})

test('顺着图走一跳_能拿到邻居那一页的邻居', () => {
  // 这就是"逐跳把一块知识走开"的最小验证：甲 → 乙 → 丙
  const dir = vault({
    '甲.md': page('甲', ['乙']),
    '乙.md': page('乙', ['丙'], '乙的定义。'),
    '丙.md': page('丙', [], '丙的定义。'),
  })
  const one = conceptNeighbors('甲', dir)
  const two = conceptNeighbors(one.linked[0].name, dir)
  assert.equal(two.linked[0].name, '丙')
  assert.equal(two.linked[0].brief, '丙的定义。')
  rmSync(dir, { recursive: true, force: true })
})
