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

module.exports = { BALL_SIZE, EDGE_MARGIN, defaultBallPosition, clampInto, isReachable, resolveStartPosition }
