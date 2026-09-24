/**
 * 托盘图标的**像素断言**：那张图必须自带圆盘。
 *
 * 为什么值得为它写一个 PNG 解码器（约 40 行，这是本仓库唯一一处解像素的测试）：
 * 这个仓库刚踩过同一个坑两次——球面和托盘都因为"深色的吉祥物直接放在深色背景上"而糊掉，
 * 球面那次是靠人眼在三张底上并排比才发现的。而托盘图标**没有 CSS 可以补救**：
 * 它是 nativeImage 直接读的一张图，谁把它换成拿 `mascot.png`（透明底）缩一版，
 * 在深色任务栏上就糊了，而 CI、尺寸断言、体积断言**全都不会红**（透明底那张也是 64x64、
 * 也不大）。所以这里解一次像素，钉住"盘在"这个事实本身。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { inflateSync } from 'node:zlib'
import { join } from 'node:path'

const PANEL = join(process.cwd(), 'resources', 'panel')

/** 最小 PNG 解码：只处理 8 位 RGBA、非隔行——够读本仓这两张图 */
function decodePng(file: string) {
  const b = readFileSync(file)
  const width = b.readUInt32BE(16)
  const height = b.readUInt32BE(20)
  if (b[24] !== 8 || b[25] !== 6) throw new Error('只处理 8 位 RGBA')
  let off = 8
  const idat: Buffer[] = []
  while (off < b.length) {
    const len = b.readUInt32BE(off)
    const tag = b.subarray(off + 4, off + 8).toString('latin1')
    if (tag === 'IDAT') idat.push(b.subarray(off + 8, off + 8 + len))
    off += 12 + len
  }
  const raw = inflateSync(Buffer.concat(idat))
  const stride = width * 4
  const out = Buffer.alloc(height * stride)
  let prev = Buffer.alloc(stride)
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)]
    const cur = Buffer.from(raw.subarray(y * (stride + 1) + 1, y * (stride + 1) + 1 + stride))
    for (let x = 0; x < stride; x++) {
      const a = x >= 4 ? cur[x - 4] : 0
      const up = prev[x]
      const c = x >= 4 ? prev[x - 4] : 0
      if (filter === 1) cur[x] = (cur[x] + a) & 255
      else if (filter === 2) cur[x] = (cur[x] + up) & 255
      else if (filter === 3) cur[x] = (cur[x] + ((a + up) >> 1)) & 255
      else if (filter === 4) {
        const p = a + up - c
        const pa = Math.abs(p - a); const pb = Math.abs(p - up); const pc = Math.abs(p - c)
        cur[x] = (cur[x] + (pa <= pb && pa <= pc ? a : pb <= pc ? up : c)) & 255
      }
    }
    cur.copy(out, y * stride)
    prev = cur
  }
  return {
    width, height,
    at: (x: number, y: number) => {
      const i = (y * width + x) * 4
      return { r: out[i], g: out[i + 1], b: out[i + 2], a: out[i + 3] }
    },
  }
}

test('托盘图标自带圆盘：那一点在盘内、在原图里是透明的', () => {
  const tray = decodePng(join(PANEL, 'tray.png'))
  const mascot = decodePng(join(PANEL, 'mascot.png'))
  assert.equal(tray.width, 64)
  assert.equal(tray.height, 64)

  // (8,32) 在盘内、在吉祥物轮廓之外：tray 必须是有底色的，而原图那一点是透明的。
  // 实测值 tray = rgb(27,31,39) 就是盘色 #1b1f27；mascot = 全透明。
  const onTray = tray.at(8, 32)
  assert.equal(onTray.a, 255, '盘应当是实心的')
  assert.deepEqual([onTray.r, onTray.g, onTray.b], [27, 31, 39], '盘的底色是 #1b1f27')
  assert.equal(mascot.at(8, 32).a, 0, '球面那张图在这里是透明的——所以两张图确实不是一回事')

  // 角落必须是透的：不然它是个方块，不是圆盘
  assert.equal(tray.at(1, 1).a, 0, '左上角应当是透明的（圆形而不是方图）')
  assert.equal(tray.at(62, 62).a, 0)
})

test('球面那张图**不带**盘 —— 盘是 CSS 画的，不是烤进图里的', () => {
  // 这条与上一条互为对照：如果哪天有人把盘烤进 mascot.png，球面就会"盘上叠盘"，
  // 而且 CSS 里那条"底色必须不透明"的断言会变成一句空话（它以为盘来自 CSS）。
  const mascot = decodePng(join(PANEL, 'mascot.png'))
  assert.equal(mascot.width, 256)
  for (const [x, y] of [[8, 128], [248, 128], [128, 8]]) {
    assert.equal(mascot.at(x, y).a, 0, `(${x},${y}) 应当是透明的`)
  }
})
