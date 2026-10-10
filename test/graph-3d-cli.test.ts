/**
 * `wiki graph`：把概念图谱导成一张**自包含**的 3D 页面（只读本地、不联网、不调用模型）。
 *
 * 盯三件"坏了不会有别的东西报错"的事：
 *   1. **自包含**：页面里不许有任何外部引用。它被 `file://` 双击打开 —— 那种情况下 `fetch` 会被
 *      CORS 挡死，所以库、数据、坐标全部内联。哪天有人图省事把 CDN 引回来，页面就白屏，
 *      而所有别的测试照样绿。
 *   2. **库随包发**：`resources/js/graph3d/` 少一个文件，页面会在 `new Function` 里炸，
 *      报错长得像"库坏了"（缺件时是 `u.timer is not a function`）。这条还顺带盯着
 *      `graph_3d.py` 与 `layout.mjs` 那两份库清单**必须一致** —— 页面与布局用的是同一套 UMD。
 *   3. **不许踩真实缓存**：布局缓存在 `output/.graph3d-cache/`（几万个坐标，重算要几分钟）。
 *      测试一律 `--cache` 指到临时目录 —— 第一版没这个开关，测试直接把真实缓存覆盖成了三节点的
 *      fixture（后台正在算的那次就撞上了）。这类"测试踩用户数据"不会有任何断言报错。
 *
 * 全程用**临时 Vault**（三张概念页互链）、临时输出、临时缓存，不读也不写真数据。
 *
 * （另一件量过但不写成断言的事：脚本里那行 `os.chdir(REPO)` 是防御性的 ——
 * 实测把 `wiki_lint.CARD_DIRS` 变回 cwd 相关，本脚本的输出**一点不变**（`collect` 收了这个参数
 * 却在函数体里没用过），所以"换个目录跑答案一样"是一条**不可能红**的断言，没写。）
 */
import assert from 'node:assert/strict'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'
import test from 'node:test'

const ROOT = process.cwd()
const GRAPH_DIR = join(ROOT, 'resources', 'js', 'graph3d')
// 与 `graph_3d.py` 的 LIB_D3 + LIB_THREE、`layout.mjs` 的 LIBS 应当完全一致
const LIBS = ['three.min.js', 'd3-dispatch.min.js', 'd3-timer.min.js', 'd3-quadtree.min.js',
  'd3-binarytree.min.js', 'd3-octree.min.js', 'd3-force-3d.min.js']
const python = process.platform === 'win32' ? 'python' : 'python3'

function concept(title: string, body: string) {
  return `---\ntitle: "${title}"\ntype: concept\ntags: [知识/概念]\n---\n\n# ${title}\n\n${body}\n`
}

/** 甲 → 乙 → 丙 → 甲：3 个点、3 条边。 */
function fixtureVault(dir: string): string {
  const wiki = join(dir, 'Wiki', 'Concepts')
  mkdirSync(wiki, { recursive: true })
  writeFileSync(join(wiki, '甲.md'), concept('甲', '甲的定义。见 [[乙]]。'), 'utf8')
  writeFileSync(join(wiki, '乙.md'), concept('乙', '乙的定义。见 [[丙]]。'), 'utf8')
  writeFileSync(join(wiki, '丙.md'), concept('丙', '丙的定义。见 [[甲]]。'), 'utf8')
  return dir
}

function runGraph(cwd: string, args: string[]) {
  // `--cache` 一律指到临时目录：真实缓存是派生数据，重算要几分钟，测试不该碰它。
  return spawnSync(python, [join(ROOT, 'scripts', 'graph_3d.py'), '--cache', join(cwd, 'cache'), ...args], {
    cwd, encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' }, timeout: 300_000,
  })
}

function payload(stdout: string): any {
  const start = stdout.indexOf('{')
  assert.ok(start >= 0, `没有 JSON 输出：${stdout.slice(0, 300)}`)
  return JSON.parse(stdout.slice(start))
}

function withTemp(run: (dir: string) => void): void {
  const dir = mkdtempSync(join(tmpdir(), 'weflow-graph3d-'))
  try {
    run(dir)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
}

test('三张互链的概念页 → 3 点 3 边，且页面完全自包含（没有任何外部引用）', () => {
  withTemp((tmp) => {
    const vault = fixtureVault(join(tmp, 'vault'))
    const out = join(tmp, 'graph.html')
    // **故意在临时目录里跑**（cwd 不是仓库）：页面与产出都不该因此变形
    const r = runGraph(tmp, ['--vault', vault, '--out', out, '--json'])
    assert.equal(r.status, 0, `退出码 ${r.status}：${r.stderr.slice(0, 400)}`)
    const body = payload(r.stdout)
    assert.equal(body.success, true)
    assert.equal(body.nodes, 3, `节点数不对：${JSON.stringify(body)}`)
    assert.equal(body.links, 3, `边数不对：${JSON.stringify(body)}`)
    assert.equal(body.sendsNothing, true)

    assert.ok(existsSync(out), '页面该写出来了')
    const html = readFileSync(out, 'utf8')
    assert.ok(/window\.__DATA__/.test(html), '数据要内联进去')
    assert.ok(/Three\.js Authors/.test(html), 'three.js 要内联进去（而不是引外部文件）')
    // 这两条是"自包含"的全部意思：没有 CDN、也没有 `<script src>`
    assert.ok(!/\b(?:src|href)\s*=\s*["']https?:/i.test(html), '不许有外部 http(s) 引用')
    assert.ok(!/<script[^>]+\bsrc=/i.test(html), '不许有 <script src> —— 库必须内联')
  })
})

test('接线：--min-degree / --line 既被声明、也被透传（少了任一半，开关在命令行里就是死的）', () => {
  // 这条是吃过亏学的：`chat-notes --transcribe-voice` 曾经只有 python 侧支持、CLI 没透传，
  // 文档写着能用、命令行报 unknown option。`wiki graph` 的这两个开关这次一起加，也一起钉住。
  const src = readFileSync(join(ROOT, 'bin', 'weflow-cli.ts'), 'utf8')
  const at = src.indexOf("new Command('graph')")
  assert.ok(at > 0, '找不到 wiki graph 命令（改名了？那这条要跟着改）')
  const declared = src.slice(at, src.indexOf('.action(', at))
  for (const opt of ['--min-degree', '--line']) {
    // commander 把 metavar 写在选项串里（`'--min-degree <n>'`），所以只匹配开关名本身
    assert.ok(declared.includes(opt), `命令要声明 ${opt}`)
  }
  const argsAt = src.indexOf('const args = [script,', at)
  const argsBlock = src.slice(argsAt, argsAt + 900)
  assert.ok(argsBlock.includes("['--min-degree', String(minDegree)]"), '参数数组要透传 --min-degree')
  assert.ok(argsBlock.includes("['--line', opts.line]"), '参数数组要透传 --line')
})

test('库齐全，且两份清单一致（页面与布局用的是同一套 UMD）', () => {
  for (const name of LIBS) {
    assert.ok(existsSync(join(GRAPH_DIR, name)), `缺库：${name}（页面会在 new Function 里炸）`)
  }
  assert.ok(existsSync(join(GRAPH_DIR, 'layout.mjs')), '缺布局助手')
  assert.ok(existsSync(join(GRAPH_DIR, 'NOTICE.txt')), '第三方库的许可与版本要记在 NOTICE.txt 里')

  const py = readFileSync(join(ROOT, 'scripts', 'graph_3d.py'), 'utf8')
  const mjs = readFileSync(join(GRAPH_DIR, 'layout.mjs'), 'utf8')
  for (const name of LIBS.filter((n) => n !== 'three.min.js')) {
    assert.ok(py.includes(`'${name}'`), `graph_3d.py 的 LIB_D3 少了 ${name}`)
    assert.ok(mjs.includes(`'${name}'`), `layout.mjs 的 LIBS 少了 ${name}`)
  }
  assert.ok(py.includes("'three.min.js'"), 'graph_3d.py 少了 three')
})

test('--dry-run 真的不写文件（只报数）', () => {
  withTemp((tmp) => {
    const vault = fixtureVault(join(tmp, 'vault'))
    const out = join(tmp, 'nope.html')
    const r = runGraph(tmp, ['--vault', vault, '--out', out, '--dry-run', '--json'])
    assert.equal(r.status, 0, r.stderr.slice(0, 300))
    const body = payload(r.stdout)
    assert.equal(body.dryRun, true)
    assert.equal(body.nodes, 3)
    assert.ok(!existsSync(out), '--dry-run 不许写文件')
  })
})

test('--min-degree 按**原图**里的连接数筛点，并把指向被删点的边一起删掉', () => {
  withTemp((tmp) => {
    // **单向**链接，度数才好数（`[[x]]` 是有向边，互链会让两端各加一度、把差别抹平）：
    //   甲 → 乙 / 丙 / 丁        戊 → 甲 / 乙
    //   度数：甲 = 3+1(戊)=4，乙 = 1(甲)+1(戊)=2，戊 = 2，丙 = 1，丁 = 1
    const vault = join(tmp, 'vault')
    const wiki = join(vault, 'Wiki', 'Concepts')
    mkdirSync(wiki, { recursive: true })
    writeFileSync(join(wiki, '甲.md'), concept('甲', '见 [[乙]] [[丙]] [[丁]]。'), 'utf8')
    writeFileSync(join(wiki, '乙.md'), concept('乙', '不引用别人。'), 'utf8')
    writeFileSync(join(wiki, '丙.md'), concept('丙', '不引用别人。'), 'utf8')
    writeFileSync(join(wiki, '丁.md'), concept('丁', '不引用别人。'), 'utf8')
    writeFileSync(join(wiki, '戊.md'), concept('戊', '见 [[甲]] [[乙]]。'), 'utf8')

    const all = payload(runGraph(tmp, ['--vault', vault, '--dry-run', '--json']).stdout)
    assert.equal(all.nodes, 5)
    assert.equal(all.links, 5)

    const core = payload(runGraph(tmp, ['--vault', vault, '--min-degree', '2', '--dry-run', '--json']).stdout)
    assert.equal(core.nodes, 3, `甲(4)、乙(2)、戊(2) 留下，丙(1)、丁(1) 走：${JSON.stringify(core)}`)
    assert.equal(core.links, 3, '留下的边只有 甲→乙、戊→甲、戊→乙；甲→丙、甲→丁 要跟着点一起删')

    // 真跑一次：布局必须成功 —— 忘了删悬空边的话，这一步会以 "node not found" 失败
    const out = join(tmp, 'core.html')
    const built = runGraph(tmp, ['--vault', vault, '--min-degree', '2', '--out', out, '--json'])
    assert.equal(built.status, 0, `布局该成功：${built.stderr.slice(0, 300)}`)
    assert.equal(payload(built.stdout).nodes, 3)
    const html = readFileSync(out, 'utf8')
    assert.ok(/"deg":4/.test(html), '留下的点该带着原图里的度数')
  })
})

test('--line 只画一条线（文章线 / 聊天线两张图口径不同，混在一起看不出结构）', () => {
  withTemp((tmp) => {
    const vault = join(tmp, 'vault')
    mkdirSync(join(vault, 'Wiki', 'Concepts'), { recursive: true })
    mkdirSync(join(vault, 'Chat', 'Concepts'), { recursive: true })
    writeFileSync(join(vault, 'Wiki', 'Concepts', '甲.md'), concept('甲', '见 [[乙]]。'), 'utf8')
    writeFileSync(join(vault, 'Wiki', 'Concepts', '乙.md'), concept('乙', '见 [[甲]]。'), 'utf8')
    writeFileSync(join(vault, 'Chat', 'Concepts', '话.md'), concept('话', '见 [[题]]。'), 'utf8')
    writeFileSync(join(vault, 'Chat', 'Concepts', '题.md'), concept('题', '见 [[话]]。'), 'utf8')

    const both = payload(runGraph(tmp, ['--vault', vault, '--dry-run', '--json']).stdout)
    assert.equal(both.nodes, 4)
    const wiki = payload(runGraph(tmp, ['--vault', vault, '--line', 'wiki', '--dry-run', '--json']).stdout)
    assert.equal(wiki.nodes, 2, `只该剩文章线那两张：${JSON.stringify(wiki)}`)
    assert.equal(wiki.links, 2)
  })
})

test('跨线同名：两张页拿到两个不同的主键，展示名随数据进来', () => {
  withTemp((tmp) => {
    const vault = join(tmp, 'vault')
    mkdirSync(join(vault, 'Wiki', 'Concepts'), { recursive: true })
    mkdirSync(join(vault, 'Chat', 'Concepts'), { recursive: true })
    writeFileSync(join(vault, 'Wiki', 'Concepts', '共有.md'), concept('共有', '见 [[甲]]。'), 'utf8')
    writeFileSync(join(vault, 'Wiki', 'Concepts', '甲.md'), concept('甲', '不引用别人。'), 'utf8')
    writeFileSync(join(vault, 'Chat', 'Concepts', '共有.md'), concept('共有', '见 [[话]]。'), 'utf8')
    writeFileSync(join(vault, 'Chat', 'Concepts', '话.md'), concept('话', '不引用别人。'), 'utf8')

    const out = join(tmp, 'g.html')
    const r = runGraph(tmp, ['--vault', vault, '--out', out, '--json'])
    assert.equal(r.status, 0, `退出码 ${r.status}：${r.stderr.slice(0, 400)}`)
    const html = readFileSync(out, 'utf8')
    const data = /window\.__DATA__ = (\{.*?\});/s.exec(html)
    assert.ok(data, '页面上该有 __DATA__')
    const parsed = JSON.parse(data![1])
    const ids = parsed.nodes.map((n: any) => n.id)
    // **主键带线**：不带线的话这两张是同一个键，前端 `new Map(…n.id…)` 只留一个 —— 节点
    // 总数看着对，其中 36 组里的一个点不到（2026-10-10 实测本机库）。ids 全是 `线:名字`。
    assert.equal(new Set(ids).size, ids.length, `主键撞了：${ids.join('、')}`)
    assert.ok(ids.includes('wiki:共有') && ids.includes('chat:共有'),
      `同名的两张该是两个不同的键：${ids.join('、')}`)
    assert.deepEqual(ids.map((i: string) => i.split(':')[0]).sort(), ['chat', 'chat', 'wiki', 'wiki'])
    // 名字单独存一份：图上/面板上要显示的是它，不是主键
    const named = parsed.nodes.filter((n: any) => n.name === '共有')
    assert.equal(named.length, 2, '两张同名页都该在，且都带着名字')
    assert.deepEqual(parsed.lineLabels, { wiki: '文章线', chat: '聊天线' },
      '线的展示名要随数据进来（源头是 _utils.LINE_LABELS，页面里不该再写一份）')
  })
})

test('3D 视图显示的是名字，不是主键（这份视图没有夹具，所以钉源码形状）', () => {
  // 2D 那份能在 jsdom + 假 canvas 上真跑（见 `graph-2d-cli.test.ts`），这份要 WebGL，跑不了。
  // 所以这里**只能钉形状**：退回 `n.id` 的症状很轻（面板和提示条里出现 `wiki:共有`），
  // 而没有任何断言会红。搜输入框那条不该钉：按主键搜是**允许**的（能精确指定某条线）。
  const src = readFileSync(join(ROOT, 'scripts', 'graph_3d.py'), 'utf8')
  for (const bad of [/esc\(n\.id\)/, /n\.id \+ ' · '/, /map\(\(m\) => m\.id\)/]) {
    assert.ok(!bad.test(src), `3D 视图把主键当名字显示了：${bad}`)
  }
  assert.ok(src.includes('esc(n.name)'), '信息面板该显示名字')
})

test('手跑（没有 CLI 的 PYTHONIOENCODING）时中文也不会崩，也不该是乱码', () => {
  withTemp((tmp) => {
    const vault = fixtureVault(join(tmp, 'vault'))
    // 故意**不带** PYTHONIOENCODING：这正是评审能手跑时的环境（这台机器是 GBK）。
    // 脚本里那行 `sys.stdout.reconfigure(encoding='utf-8')` 就是为这个场景写的。
    const env = { ...process.env }
    delete (env as any).PYTHONIOENCODING
    const r = spawnSync(python, [join(ROOT, 'scripts', 'graph_3d.py'),
      '--cache', join(tmp, 'cache'), '--vault', vault, '--out', join(tmp, 'g.html'), '--dry-run'],
      { cwd: tmp, env, encoding: 'buffer' as any, timeout: 120_000 })
    assert.equal(r.status, 0, `退出码 ${r.status}：${String(r.stderr).slice(0, 300)}`)
    const out = Buffer.from(r.stdout as any).toString('utf8')
    assert.ok(out.includes('概念'), `stdout 该是可读的 UTF-8 中文，实际 ${JSON.stringify(out.slice(0, 120))}`)
  })
})

test('布局缓存在用：同一个缓存跑第二次沿用，换一个空缓存就重算', () => {
  withTemp((tmp) => {
    const vault = fixtureVault(join(tmp, 'vault'))
    const a = join(tmp, 'a')
    const b = join(tmp, 'b')
    mkdirSync(a, { recursive: true })
    mkdirSync(b, { recursive: true })
    const outOf = (dir: string) => join(dir, 'graph.html')
    const first = runGraph(a, ['--vault', vault, '--out', outOf(a), '--json'])
    assert.ok(!/图没变/.test(first.stdout), '空缓存不该说"图没变"')
    const cached = readdirSync(join(a, 'cache'))
    assert.ok(cached.some((f) => /^graph-.*\.json$/.test(f)), '缓存要落到 --cache 指的目录里（而不是真实那份）')
    const again = runGraph(a, ['--vault', vault, '--out', outOf(a), '--json'])
    assert.ok(/图没变/.test(again.stdout), '同一个缓存跑第二次该沿用已有布局')
    const fresh = runGraph(b, ['--vault', vault, '--out', outOf(b), '--json'])
    assert.ok(!/图没变/.test(fresh.stdout), '换了个空缓存目录就该重算')
  })
})
