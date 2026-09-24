/**
 * 小球该在哪儿：**纯算术**，不 require electron。
 *
 * 为什么单独一个文件：多显示器这件事**在这台机器上造不出来**（只有一块屏）。
 * 但真正要保证的是一条数学性质——"记住的位置永远落得回某块屏的可见区域里"——
 * 它可以在 CI 里用**合成的显示器布局**验：左副屏的 x 是负的、副屏被拔掉后位置不可达、
 * 球比工作区还大……这些都是这台机器上永远试不到、却一定会发生在别人机器上的情况。
 *
 * `main.cjs` 负责"问 Electron 要显示器列表"（`screen.getAllDisplays()`），
 * 这里只负责算。两边分开之后，算的那半有测试，问的那半是 Electron 的。
 *
 * 坐标是 Electron 的坐标：多屏时左副屏的 `x` 是**负数**，主屏的 `workArea` 也扣掉了任务栏。
 */
'use strict'

const BALL_SIZE = 76
/** 球离屏幕边缘留多少：贴死边缘在 Windows 上会跟任务栏/贴边功能打架 */
const EDGE_MARGIN = 24
/** 对话气泡多大（展开后那块圆角面板）。`panel.css` 里有一份 `--bubble-width`，有测试钉住两者相等。 */
const BUBBLE_SIZE = { width: 420, height: 560 }
/** 气泡与球之间留的空隙。气泡那侧的透明缝隙靠它，`--bubble-gap` 是同一个数。 */
const BUBBLE_GAP = 12

/** 默认位置：主屏工作区的右下角 */
function defaultBallPosition(workArea, ballSize = BALL_SIZE, margin = EDGE_MARGIN) {
  return {
    x: workArea.x + workArea.width - ballSize - margin,
    y: workArea.y + workArea.height - ballSize - margin,
  }
}

/** 把一个 `size x size` 的方块挪进给定工作区（只挪，不改尺寸） */
function clampInto(x, y, size, workArea) {
  const maxX = workArea.x + workArea.width - size
  const maxY = workArea.y + workArea.height - size
  return {
    x: Math.max(workArea.x, Math.min(x, maxX)),
    y: Math.max(workArea.y, Math.min(y, maxY)),
  }
}

/**
 * 记住的位置还能不能用：判据取**球的中心点**落在某块工作区里。
 * 中心可见就一定抓得回来——只露 1 像素也算"可见"，但那不叫抓得回来。
 */
function isReachable(pos, workAreas, ballSize = BALL_SIZE) {
  const cx = pos.x + ballSize / 2
  const cy = pos.y + ballSize / 2
  return workAreas.some((a) =>
    cx >= a.x && cx <= a.x + a.width && cy >= a.y && cy <= a.y + a.height)
}

/**
 * 启动时该把球放哪：记住的位置可达就用它，否则回默认角落。
 * **副屏被拔掉、或用户把球拖到屏幕外，走的就是这一条**。
 */
function resolveStartPosition(savedPos, workAreas, primaryWorkArea, ballSize = BALL_SIZE, margin = EDGE_MARGIN) {
  if (savedPos && Number.isInteger(savedPos.x) && Number.isInteger(savedPos.y)
      && isReachable(savedPos, workAreas, ballSize)) {
    return { x: savedPos.x, y: savedPos.y }
  }
  return defaultBallPosition(primaryWorkArea, ballSize, margin)
}

/**
 * 展开时的整体布局：**球 + 空隙 + 气泡**合成一个矩形，球在它那个角上**一动不动**。
 *
 * 用户要的是"图标别撤起来，气泡在旁边长出来，像这个图标在说话"。这决定了三件事：
 *
 * 1. 展开后的窗口比气泡大一圈——多出来的就是球占的那条（外加空隙），窗口宽
 *    = 420 + 12 + 76 = 508；
 * 2. **球必须正好坐在窗口的某个角上**。这是硬要求，不是审美：窗口是一整块，页面把球
 *    钉在角上（`position: fixed; right/bottom`），于是**窗口怎么改大小，球在屏幕上的位置
 *    都不变**——因为那个角没动。反过来，球要是不在角上（改成按坐标钉），窗口任意时刻改
 *    大小，球都会先跟着窗口跳一下、等页面收到新坐标才回来，那就是一闪。
 * 3. 气泡默认开在球的**左边**（球默认在右下角，往左上长不遮屏），左边放不下就翻到右边。
 *
 * **竖向：气泡的高度可缩，球的位置不可动。** 先算两种对齐各自能有多高——
 * 底对齐（球的底 = 气泡的底）时 `球底 - 工作区顶`，顶对齐时 `工作区底 - 球顶`——
 * **取高的那个**（够 560 就用 560），气泡高度随之为那个值。于是：
 *
 * - 球在下面（默认）→ 底对齐给满 560，球在窗口**右下角**；
 * - 球在中间（本机实测用户把球拖到了 967,302）→ 底对齐只有 378，顶对齐有 560 → 顶对齐，
 *   球在窗口**右上角**，气泡整块挂在它下面；
 * - 两头都不够高（很小的屏幕）→ 取高的那个，气泡就是矮一点，不越界。
 *
 * 从前那版是"气泡固定 560 高、上下夹取"，结果球在中间时窗口被夹进工作区、**球跟着挪了
 * 57-75px**（用户屏幕上就是球跳了一下）。让气泡变矮就再也不用挪球。
 *
 * @returns {{window: {x,y,width,height}, side: 'left'|'right', anchorY: 'bottom'|'top', bubbleHeight: number}}
 */
function bubbleLayout(ball, bubble = BUBBLE_SIZE, workArea, gap = BUBBLE_GAP, ballSize = BALL_SIZE) {
  const leftX = ball.x - gap - bubble.width
  const rightX = ball.x + ballSize + gap
  const fitsLeft = leftX >= workArea.x
  const fitsRight = rightX + bubble.width <= workArea.x + workArea.width

  let side = 'left'
  let bubbleX = leftX
  if (!fitsLeft && fitsRight) {
    side = 'right'
    bubbleX = rightX
  } else if (!fitsLeft && !fitsRight) {
    const roomLeft = ball.x - workArea.x
    const roomRight = (workArea.x + workArea.width) - (ball.x + ballSize)
    side = roomLeft >= roomRight ? 'left' : 'right'
    bubbleX = side === 'left' ? leftX : rightX
  }

  // 竖向：两种对齐各能有多高，取高的那个
  const heightIfBottom = Math.min(bubble.height, (ball.y + ballSize) - workArea.y)
  const heightIfTop = Math.min(bubble.height, (workArea.y + workArea.height) - ball.y)
  const anchorY = heightIfTop > heightIfBottom ? 'top' : 'bottom'
  const bubbleHeight = Math.max(1, anchorY === 'top' ? heightIfTop : heightIfBottom)
  const bubbleY = anchorY === 'top' ? ball.y : ball.y + ballSize - bubbleHeight

  // 窗口 = 球与气泡的并集（球在角上，所以并集就是"气泡 + 空隙 + 球"那一块）
  const x = Math.min(bubbleX, ball.x)
  const y = Math.min(bubbleY, ball.y)
  const window_ = {
    x,
    y,
    width: Math.max(bubbleX + bubble.width, ball.x + ballSize) - x,
    height: Math.max(bubbleY + bubbleHeight, ball.y + ballSize) - y,
  }
  // 横向超出工作区时按 `clampInto` 那套夹（左侧对齐、右侧被裁）——窄屏才走得到
  const maxX = workArea.x + workArea.width - window_.width
  window_.x = Math.max(workArea.x, Math.min(window_.x, maxX))

  return { window: window_, side, anchorY, bubbleHeight }
}

/**
 * 球此刻在屏幕上的矩形：从**当前**窗口矩形与展开时记下的锚（哪一侧、哪个角）反推。
 *
 * 收起时要用——用户拖着标题栏挪过窗口、或者拉大过它，球都跟着那个角走，所以反推始终成立。
 * **不能图省事拿窗口的左上角当球的落点**：球在别的角上，那样收起时球会跳到气泡的另一头去
 * （上一版真的这么写的，探针量出来的）。
 */
function ballRectInWindow(windowRect, anchor, ballSize = BALL_SIZE) {
  const atRight = anchor.side === 'left'      // 气泡在左 → 球在窗口右
  const atBottom = anchor.anchorY !== 'top'
  return {
    x: atRight ? windowRect.x + windowRect.width - ballSize : windowRect.x,
    y: atBottom ? windowRect.y + windowRect.height - ballSize : windowRect.y,
  }
}

module.exports = {
  BALL_SIZE, EDGE_MARGIN, BUBBLE_SIZE, BUBBLE_GAP,
  defaultBallPosition, clampInto, isReachable, resolveStartPosition, bubbleLayout,
  ballRectInWindow,
}
