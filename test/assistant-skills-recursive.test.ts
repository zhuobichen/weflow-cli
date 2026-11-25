/**
 * 技能递归发现（2026-11-24）。
 *
 * 原来 `scanSkills` 只认 `<root>/<id>/SKILL.md` **一层**。用户按主题分目录
 * （`skills/写作/公众号/SKILL.md`）时**全部扫不到，而且不报错**——他只会觉得
 * "我的技能怎么不见了"。这与本仓反复强调的"静默失败最坑"是同一类。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const HOME = mkdtempSync(join(tmpdir(), 'weflow-skill-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME

const { scanSkills } = await import('../src/services/assistantSkills.js')

function makeSkill(dir: string, id: string) {
  mkdirSync(dir, { recursive: true })
  writeFileSync(join(dir, 'SKILL.md'),
    `---\nname: ${id}\ndescription: 测试技能 ${id}\n---\n\n正文\n`, 'utf8')
}

test('按主题分目录也能被发现（原来只认一层）', () => {
  const root = join(HOME, 'skills-test')
  makeSkill(join(root, 'flat-skill'), 'flat-skill')              // 第一层
  makeSkill(join(root, '写作', 'gongzhonghao'), 'gongzhonghao')  // 第二层
  makeSkill(join(root, '写作', 'xhs'), 'xhs')                    // 第二层
  makeSkill(join(root, 'a', 'b', 'deep'), 'deep')                // 第三层

  const scan = scanSkills([root])
  const ids = scan.skills.map((s) => s.id).sort()
  assert.deepEqual(ids, ['deep', 'flat-skill', 'gongzhonghao', 'xhs'],
    '四层的技能都该被发现，实际：' + ids.join(', '))
})

test('有技能就不再往下钻（技能内部不会套技能）', () => {
  const root = join(HOME, 'skills-nest')
  makeSkill(join(root, 'outer'), 'outer')
  // 在 outer 内部再放一个：不该被当成独立技能
  makeSkill(join(root, 'outer', 'inner'), 'inner')

  const scan = scanSkills([root])
  const ids = scan.skills.map((s) => s.id)
  assert.ok(ids.includes('outer'))
  assert.ok(!ids.includes('inner'), '技能内部的 SKILL.md 不该被当独立技能')
})

test('深度上限：超过 4 层不扫（防止误扫整个家目录）', () => {
  const root = join(HOME, 'skills-deep')
  makeSkill(join(root, 'l1', 'l2', 'l3', 'l4', 'l5', 'l6'), 'l6')
  const scan = scanSkills([root])
  assert.equal(scan.skills.length, 0, '超过深度上限的不该被发现')
})

test('root 字段取"包含它的那个技能根"，不是文件的直接父目录', () => {
  const root = join(HOME, 'skills-root')
  makeSkill(join(root, '主题', 'sub'), 'sub')
  const scan = scanSkills([root])
  const found = scan.skills.find((s) => s.id === 'sub')
  assert.ok(found)
  assert.equal(found!.root, root, 'root 应是技能根，便于展示与排查')
})
