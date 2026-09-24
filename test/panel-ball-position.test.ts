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
const { BALL_SIZE, EDGE_MARGIN, BUBBLE_SIZE, BUBBLE_GAP,
        defaultBallPosition, clampInto, isReachable, resolveStartPosition, bubbleLayout,
        ballRectInWindow } = mod.default ?? mod

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

// ------------------------------------------------- 展开：球 + 空隙 + 气泡 的合成布局

const ballAt = (x: number, y: number) => ({ x, y })

/** 球钉在窗口的哪个角。**"球不动"这条性质就是它**：窗口的那个角与球的对应角重合。 */
function anchored(layout: any, ball: { x: number; y: number }) {
  const win = layout.window
  const ballRight = ball.x + BALL_SIZE
  const ballBottom = ball.y + BALL_SIZE
  const horizontal = layout.side === 'left' ? win.x + win.width === ballRight : win.x === ball.x
  const vertical = layout.anchorY === 'bottom' ? win.y + win.height === ballBottom : win.y === ball.y
  return { horizontal, vertical }
}

test('气泡默认开在球的左边、底对齐，窗口 = 气泡 + 空隙 + 球', () => {
  const ball = ballAt(1607, 812)
  const layout = bubbleLayout(ball, BUBBLE_SIZE, PRIMARY)
  assert.equal(layout.side, 'left')
  assert.equal(layout.anchorY, 'bottom')
  assert.deepEqual(layout.window, {
    x: 1607 - BUBBLE_GAP - BUBBLE_SIZE.width,
    y: 812 + BALL_SIZE - BUBBLE_SIZE.height,
    width: BUBBLE_SIZE.width + BUBBLE_GAP + BALL_SIZE,
    height: BUBBLE_SIZE.height,
  })
  assert.equal(layout.window.width, 508)
  assert.equal(layout.window.height, 560)
  assert.equal(layout.window.x, 1175, '真机上的落点')
  assert.equal(layout.window.y, 328)
})

test('**球全程不动**：窗口的锚角与球的对应角重合（四种组合都要成立）', () => {
  // 这条是"图标不要撤起来"的数学形式：球画在窗口的这个角上，而窗口改大小时这个角的
  // **屏幕坐标没变**（另一条边才是被推开的），所以球一个像素都不会动——哪怕窗口是在
  // 页面收到通知之前就变大了。前提是球真的坐在角上。
  const corners = [
    { ball: ballAt(1607, 812), label: '右下角（默认）' },
    { ball: ballAt(100, 812), label: '左下角' },
    { ball: ballAt(1607, 100), label: '右上角' },
    { ball: ballAt(100, 100), label: '左上角' },
  ]
  for (const { ball, label } of corners) {
    const layout = bubbleLayout(ball, BUBBLE_SIZE, PRIMARY)
    const { horizontal, vertical } = anchored(layout, ball)
    assert.ok(horizontal, `${label}：水平方向球要钉在窗口边上（side=${layout.side}）`)
    assert.ok(vertical, `${label}：垂直方向球要钉在窗口边上（anchorY=${layout.anchorY}）`)
  }
})

test('球贴屏幕左缘：气泡翻到右边', () => {
  const ball = ballAt(0, 812)
  const layout = bubbleLayout(ball, BUBBLE_SIZE, PRIMARY)
  assert.equal(layout.side, 'right')
  assert.equal(layout.window.x, 0, '球在窗口左边')
  assert.equal(layout.window.width, 508)
  assert.ok(anchored(layout, ball).horizontal, '翻边之后球仍然钉在窗口边上')
})

test('球贴屏幕顶边：气泡改成顶对齐（否则会顶到屏幕外）', () => {
  const ball = ballAt(1607, 0)
  const layout = bubbleLayout(ball, BUBBLE_SIZE, PRIMARY)
  assert.equal(layout.anchorY, 'top')
  assert.equal(layout.window.y, 0)
  assert.ok(anchored(layout, ball).vertical)
})

test('窄屏（两边都放不下）：挑空间大的一侧，并夹进工作区', () => {
  // 工作区只有 600 宽，508 的窗口放不下——退化情况，球会被挪动，这里只钉住"不越界"
  const NARROW = { x: 0, y: 0, width: 600, height: 912 }
  for (const x of [0, 200, 520]) {
    const layout = bubbleLayout(ballAt(x, 400), BUBBLE_SIZE, NARROW)
    assert.ok(layout.window.x >= NARROW.x, `x=${x}：窗口跑到工作区左边去了`)
    assert.ok(layout.window.x + layout.window.width <= NARROW.x + NARROW.width,
      `x=${x}：窗口右边越界（${layout.window.x + layout.window.width} > ${NARROW.width}）`)
  }
})

test('工作区原点不是 0,0（副屏）时也夹得住', () => {
  // 左副屏的 x 是负数：夹取用的是工作区自己的原点和宽度，不是 0 与屏幕尺寸
  const ball = ballAt(-1920, 900)
  const layout = bubbleLayout(ball, BUBBLE_SIZE, LEFT)
  assert.equal(LEFT.x <= layout.window.x, true)
  assert.equal(layout.window.x + layout.window.width <= LEFT.x + LEFT.width, true)
  assert.equal(anchored(layout, ball).horizontal, true, '副屏上球也钉在窗口边上')
})

test('收起时从窗口反推得回球的原地（四个角都要对得上）', () => {
  // **这条是真 bug 的回归测试**：收起时图省事拿窗口的左上角当球的落点，球会"跳"到
  // 气泡的另一个角去（默认布局下就是从右下角跳到左上角）。探针量出来的。
  const corners = [
    { ball: ballAt(1607, 812), label: '右下角（默认）' },
    { ball: ballAt(0, 812), label: '左下角' },
    { ball: ballAt(1607, 0), label: '右上角' },
    { ball: ballAt(0, 0), label: '左上角' },
  ]
  for (const { ball, label } of corners) {
    const layout = bubbleLayout(ball, BUBBLE_SIZE, PRIMARY)
    const back = ballRectInWindow(layout.window, layout, BALL_SIZE)
    assert.deepEqual(back, { x: ball.x, y: ball.y }, `${label}：收起后球应当回到原地`)
  }
})

test('球在屏幕竖向中间：气泡改矮/改对齐，**球还是不动**（用户真机那个位置）', () => {
  // 用户把球拖到了 967,302（工作区 1707x912）。从前那版气泡固定 560 高，这里上下都放不下，
  // 于是窗口被夹进工作区、球跟着挪了 57-75px——屏幕上就是球跳了一下。
  // 现在：两种对齐各自算能有多高，取高的那个（底对齐 378 vs 顶对齐 560 → 顶对齐），
  // 球仍然坐在窗口的右上角 = 它原来那个位置。
  const ball = ballAt(967, 302)
  const layout = bubbleLayout(ball, BUBBLE_SIZE, PRIMARY)
  assert.equal(layout.side, 'left')
  assert.equal(layout.anchorY, 'top')
  assert.equal(layout.bubbleHeight, 560, '顶对齐能放下整块 560')
  assert.deepEqual(layout.window, { x: 535, y: 302, width: 508, height: 560 })
  assert.ok(anchored(layout, ball).horizontal && anchored(layout, ball).vertical, '球仍然在窗口的角上')
})

test('球贴屏幕底边：底对齐给满高度（默认位置就是这一支）', () => {
  const ball = ballAt(1607, 812)
  const layout = bubbleLayout(ball, BUBBLE_SIZE, PRIMARY)
  assert.equal(layout.anchorY, 'bottom')
  assert.equal(layout.bubbleHeight, 560)
  assert.equal(layout.window.y + layout.window.height, 812 + BALL_SIZE, '球的底 = 窗口的底')
})

test('屏幕再矮也不越界：气泡跟着变矮，球不动', () => {
  // 工作区只有 400 高、球在中间 → 两边各能放 ~200，取高的那个；气泡就矮一点，但不越界
  const SHORT = { x: 0, y: 0, width: 1707, height: 400 }
  const ball = ballAt(1607, 150)
  const layout = bubbleLayout(ball, BUBBLE_SIZE, SHORT)
  assert.ok(layout.bubbleHeight < BUBBLE_SIZE.height, `应当变矮，实际 ${layout.bubbleHeight}`)
  assert.ok(layout.window.y >= SHORT.y, '上边不越界')
  assert.ok(layout.window.y + layout.window.height <= SHORT.y + SHORT.height, '下边不越界')
  assert.equal(anchored(layout, ball).vertical, true, '球仍然钉在窗口边上')
})

test('气泡高度永远不超过设计高度，也永远为正', () => {
  for (const y of [0, 50, 150, 300, 500, 700, 836]) {
    const layout = bubbleLayout(ballAt(1607, y), BUBBLE_SIZE, PRIMARY)
    assert.ok(layout.bubbleHeight > 0 && layout.bubbleHeight <= BUBBLE_SIZE.height,
      `y=${y} 时算出 ${layout.bubbleHeight}`)
    assert.ok(anchored(layout, ballAt(1607, y)).vertical, `y=${y} 时球脱离了窗口边`)
  }
})
