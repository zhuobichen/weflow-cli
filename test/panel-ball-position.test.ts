/**
 * 小球的位置算术 —— **这台机器上试不到的情况在这里试**。
 *
 * 本机只有一块屏，所以"左副屏 x 是负数""副屏被拔掉之后位置不可达""球比工作区还大"
 * 这些永远现场造不出来，但它们一定会发生在别人的机器上。逻辑抽成纯函数之后，
 * 用**合成的显示器布局**就能把那条性质钉住：**记住的位置永远落得回某块屏的可见区域里**。
 *
 * 真机验过的部分（默认右下角、拖动后重启回到原位、不可达位置回退、展开/收起夹取）
 * 记在 D-045 与提交信息里；这里补的是本机造不出来的那些坐标。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

const mod = await import(pathToFileURL(join(process.cwd(), 'resources', 'panel', 'ball-position.cjs')).href)
const { BALL_SIZE, EDGE_MARGIN, defaultBallPosition, clampInto, isReachable, resolveStartPosition } = mod.default ?? mod

/** 主屏 1707x960、任务栏占 48（与真机一致）；工作区原点 0,0 */
const PRIMARY = { x: 0, y: 0, width: 1707, height: 912 }
/** 左副屏：Electron 里它的 x 是**负数** */
const LEFT = { x: -1920, y: 0, width: 1920, height: 1040 }

test('默认位置是主屏工作区的右下角，且留了边距', () => {
  const p = defaultBallPosition(PRIMARY, BALL_SIZE, EDGE_MARGIN)
  assert.deepEqual(p, { x: 1707 - 76 - 24, y: 912 - 76 - 24 })
  // 真机实测就是 1607,812
  assert.deepEqual(p, { x: 1607, y: 812 })
})

test('工作区原点不是 0,0 时（副屏当主屏、或 Windows 把任务栏放左边）也对', () => {
  const p = defaultBallPosition({ x: -1920, y: 0, width: 1920, height: 1040 }, 76, 24)
  assert.deepEqual(p, { x: -1920 + 1920 - 76 - 24, y: 1040 - 76 - 24 })
})

test('放大成对话窗：从右下角铺开要能挪回屏内', () => {
  // 球在 1607,812，对话窗 420x560 —— 不挪的话右下角会到 (2027,1372)，大半在屏幕外
  const p = clampInto(1607, 812, 560, PRIMARY)
  assert.deepEqual(p, { x: 1707 - 560, y: 912 - 560 })
  assert.ok(p.x + 560 <= PRIMARY.x + PRIMARY.width, '右边要在屏内')
  assert.ok(p.y + 560 <= PRIMARY.y + PRIMARY.height, '下边要在屏内')
})

test('左上溢出也要夹回来（负坐标那一侧）', () => {
  assert.deepEqual(clampInto(-500, -300, 76, PRIMARY), { x: 0, y: 0 })
})

test('要放的比工作区还大时，夹到左上角而不是算出负数', () => {
  const p = clampInto(100, 100, 9999, PRIMARY)
  assert.deepEqual(p, { x: 0, y: 0 })
})

test('可达性：中心在某块屏里就算可达，出界就不算', () => {
  assert.equal(isReachable({ x: 1607, y: 812 }, [PRIMARY], 76), true, '默认角落')
  assert.equal(isReachable({ x: 1631, y: 836 }, [PRIMARY], 76), true, '真机夹取后的位置（贴到右下边界）')
  // 注意 1700,900 与 1631,836 只差几十像素，但它的**中心**已经在工作区外了
  // （1700+38=1738 > 1707）——所以不算可达。（第一版我把这条写成了 true，是断言错，不是代码错。）
  assert.equal(isReachable({ x: 1700, y: 900 }, [PRIMARY], 76), false, '中心已经出界')
  assert.equal(isReachable({ x: 9000, y: 9000 }, [PRIMARY], 76), false)
})

test('多显示器：球在左副屏上算可达', () => {
  // 不认多屏的话，每次插着副屏启动都会把球从副屏硬拽回主屏——那才是最烦人的那种 bug
  assert.equal(isReachable({ x: -1000, y: 300 }, [PRIMARY, LEFT], 76), true)
  assert.equal(isReachable({ x: -1000, y: 300 }, [PRIMARY], 76), false, '只有主屏时它不可达')
})

test('副屏被拔掉：记住的负坐标位置回退到主屏右下角', () => {
  const saved = { x: -1000, y: 300 }        // 昨天还在左副屏上
  const onlyPrimary = resolveStartPosition(saved, [PRIMARY], PRIMARY, BALL_SIZE, EDGE_MARGIN)
  assert.deepEqual(onlyPrimary, { x: 1607, y: 812 }, '回退，而不是把球丢在看不见的坐标上')
  const withLeft = resolveStartPosition(saved, [PRIMARY, LEFT], PRIMARY, BALL_SIZE, EDGE_MARGIN)
  assert.deepEqual(withLeft, saved, '副屏还在就照用')
})

test('记住的位置是垃圾（缺字段/不是整数/没有）时一律回默认角落', () => {
  for (const bad of [null, undefined, {}, { x: 1 }, { x: 1.5, y: 2 }, { x: '3', y: '4' }]) {
    assert.deepEqual(resolveStartPosition(bad as any, [PRIMARY], PRIMARY, BALL_SIZE, EDGE_MARGIN),
      { x: 1607, y: 812 }, `输入 ${JSON.stringify(bad)}`)
  }
})

test('真机验过的那几条，用真机坐标钉住', () => {
  // 默认右下角
  assert.deepEqual(resolveStartPosition(null, [PRIMARY], PRIMARY, BALL_SIZE, EDGE_MARGIN), { x: 1607, y: 812 })
  // 拖到 400,300 之后重启还在 400,300
  assert.deepEqual(resolveStartPosition({ x: 400, y: 300 }, [PRIMARY], PRIMARY, BALL_SIZE, EDGE_MARGIN), { x: 400, y: 300 })
  // 写一个不可达的 9000,9000 → 回退
  assert.deepEqual(resolveStartPosition({ x: 9000, y: 9000 }, [PRIMARY], PRIMARY, BALL_SIZE, EDGE_MARGIN), { x: 1607, y: 812 })
  // 对话窗拖到 1650,870 再收起 → 球夹到 1631,836
  assert.deepEqual(clampInto(1650, 870, BALL_SIZE, PRIMARY), { x: 1631, y: 836 })
})
