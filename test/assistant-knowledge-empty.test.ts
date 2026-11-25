/**
 * `search_knowledge` 在**没有概念页**时，说法必须与"查了没命中"不同（D-186）。
 *
 * 由来：第三轮子 Agent 复测时它引用了一段 `search_knowledge` 的返回；我拿同样的关键词
 * 去调，服务器返回「文章线没收录「X」, 这一趟看了 0 个概念页」——而子 Agent 说它看到的
 * 是「看了 **47944** 个概念页」。
 *
 * 两边的数字都是真的：`total` 是**这台机器上实际扫了多少个 .md**。服务器没有概念页（0），
 * 子 Agent 那次跑的临时家目录里有（47944）。**所以断言不能钉具体数字。**
 *
 * 要钉的是**说法**：`total === 0` 时不是"查了 0 页没命中"，而是**根本没东西可查**。
 * 原措辞两种情况都写"看了 0 个概念页"，模型和用户都分不清——这与 D-183 是同一类混淆。
 *
 * 第一版这条测试就是把数字写死了（断言一定出现"没有概念页"），结果在临时家目录里
 * 拿到 47944 就红了。**测试依赖了环境的偶然值，这是脆弱的测试**，已改。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const HOME = mkdtempSync(join(tmpdir(), 'weflow-know-'))
process.env.HOME = HOME
process.env.USERPROFILE = HOME

const { executeTool } = await import('../src/services/assistantTools.js')

test('search_knowledge 的"没命中"文案：必须能区分"没东西可查"与"查了没命中"', async () => {
  const out = String(await executeTool('search_knowledge', { keyword: '一个肯定不存在的关键词zzz' },
    { userId: 'x', memory: null } as any))

  // 抽出它声称扫了多少
  const scanned = out.match(/(\d+)\s*个概念页/)
  if (!scanned) {
    // 也可能是"知识库尚未生成"那种更早的分支——那本身就是清楚的说法
    assert.match(out, /知识库尚未生成|还没有概念页/, '应当明说知识库没有内容，实际：' + out.slice(0, 160))
    return
  }

  const total = Number(scanned[1])
  if (total === 0) {
    // **关键断言**：0 页时不许只说"没收录…看了 0 页"
    assert.match(out, /还没有概念页|知识库还没编译|没东西可查/,
      '0 个概念页时应明说"没东西可查"，实际：' + out.slice(0, 200))
  } else {
    // 有内容但没命中：说"没收录"是正确的
    assert.match(out, /没收录/, '有概念页但没命中时应说"没收录"，实际：' + out.slice(0, 200))
    assert.doesNotMatch(out, /没东西可查/, '有概念页时不该说"没东西可查"')
  }
})
