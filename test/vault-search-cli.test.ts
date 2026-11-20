/**
 * `vault search` 的**命令行面**：`--json` 必须被接受，而且**默认不是 JSON**。
 *
 * 为什么要有这条（2026-10-11 实测）：这条命令以前是**唯一输出就是 JSON、而 `--json` 本身不存在** ——
 * action 的参数表里硬编码着 `'--json'`，脚本里写着的人类模式（`vault_search.py` 不带 `--json` 时
 * 打印带相关度的可读列表）**没有任何入口能到达**，用户显式敲 `--json` 还会拿到
 * `error: unknown option '--json'`（exit 1、stdout 0 字节）。这与 `docs/EXTENDING.md` 那条
 * "脚本或 AI 会驱动的命令都有 `--json` 模式"正相反 —— 同一类还有 `--no-rerank`
 * （Python 侧有、CLI 没接线，见 `test/search-cli.test.ts`），但那条**文档里写着**，被
 * `scripts/check_readme_commands.py` 扫了出来；这一条文档里没写，只有真敲一次才知道。
 *
 * 判据选**行为**而不是源码文本：`--json` 有没有真的转过去、默认到底吐什么，只有端到端跑一次才有定论。
 * 静态写法在这个仓库里有先例（`test/chat-notes-cli.test.ts` 的 `opts.transcribeVoice ? [...]`），
 * 但一条端到端断言已经蕴含它 —— 把 `...(opts.json ? ['--json'] : [])` 改回硬编码 `'--json'`，
 * 下面第二条会红；声明了却不转发，第一条会红。
 *
 * 静态那半边仍然要有一条，而且方向是**反过来**的：见最后那条"声明 ↔ 转发"一致性检查。行为判据
 * 证明不了"某个旗标忘了声明" —— 参数表里多一个 `'--days'` 而 `.option('--days')` 不在，命令照样能跑，
 * 只有用户敲它才会拿到 `unknown option`。这正是这次这个 bug 的形态（`'--json'` 转发了、没声明），
 * 而下一条 `--days` 是同一处的第二个缺口（脚本一直有它，CLI 没接）。
 *
 * **不需要本机数据**：`DEFAULT_VAULT` 是按脚本位置算的绝对路径，CI 里 `output/` 不存在 ⇒
 * 0 条命中、退出 0，人类模式照样打印"找到 0 条"。已实测过空目录与不存在的目录两种情形
 * （都在调脚本**之前**或**没有 Vault 时**成立，所以本机与 CI 的结论一致）。
 */
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

function runVaultSearch(args: string[]) {
  // 这些用例真起一个子进程跑 CLI（要加载 tsx + 扫库），单跑约 15 秒。
  // **不给 timeout 的话，全量 `npm test` 并行时会撞上 node:test 的 120 秒上限**，
  // 报出来的却是"stdout 不是 JSON"——看起来像 CLI 坏了，实际只是没跑完
  // （2026-11-20 实测：单跑 5/5 过，全量里这两条各花 121 秒后判失败）。
  // 显式给 timeout 之后，超时会变成 `signal=SIGTERM`，指向真正的原因。
  return spawnSync(process.execPath, [
    '--import', 'tsx', join(process.cwd(), 'bin', 'weflow-cli.ts'), 'vault', 'search', ...args,
  ], { cwd: process.cwd(), encoding: 'utf8', timeout: 180_000, maxBuffer: 64 * 1024 * 1024 })
}

function isSingleJson(text: string): boolean {
  try {
    JSON.parse(text)
    return true
  } catch {
    return false
  }
}

test('vault search --json 被接受，且 stdout 是一整份 JSON', () => {
  const r = runVaultSearch(['x', '--json'])
  // 先分清"没跑完"与"输出变了"：超时（signal=SIGTERM）会让 stdout 为空，
  // 而下面那条断言会把它报成"输出不是 JSON"——诊断又指向错误方向。
  assert.equal(r.signal, null,
    `子进程被信号结束了（signal=${r.signal}）；多半是并发下没跑完，`
    + `stderr=${JSON.stringify((r.stderr || '').slice(0, 200))}`)
  assert.ok(isSingleJson(r.stdout),
    `--json 必须给出可解析的一整份 JSON；实际 stdout=${JSON.stringify(r.stdout.slice(0, 200))}`
    + `（若这里是 "unknown option"，说明这条通道又断了）`)
  const body = JSON.parse(r.stdout)
  assert.equal(body.line, 'wiki', '不带 --line 时默认搜文章线')
  assert.ok(Array.isArray(body.results), 'JSON 里应有 results 数组')
})

test('vault search 默认是人类可读，不是 JSON', () => {
  const r = runVaultSearch(['x'])
  // 先分清"没跑起来"与"跑起来但输出变了"。2026-10-12 实测过前者：机器休眠打断那次跑，子进程什么都没回，
  // 而下一条断言把空 stdout 报成了"脚本的默认输出被改了" —— 诊断指向了错误的方向（单独重跑即通过）。
  assert.equal(r.status, 0,
    `vault search 没跑起来（exit=${r.status}）；stdout=${JSON.stringify(r.stdout.slice(0, 200))}，`
    + `stderr=${JSON.stringify((r.stderr || '').slice(0, 200))}`)
  assert.ok(!isSingleJson(r.stdout),
    `默认输出不该是 JSON —— 把 '--json' 重新硬编码进参数表就会让这条红；`
    + `实际 stdout=${JSON.stringify(r.stdout.slice(0, 200))}`)
  assert.match(r.stdout, /找到/, '脚本人类模式那句"找到 N 条"不见了（脚本的默认输出被改了？）')
})

test('vault search 的校验分支认 --json：JSON 模式下 stdout 仍是一整份 JSON', () => {
  const r = runVaultSearch(['x', '--json', '--type', 'bogus'])
  assert.equal(r.status, 1, '不合法的 --type 应当退出 1')
  assert.ok(isSingleJson(r.stdout),
    `JSON 模式下就算参数不合法，stdout 也得是一份 JSON（否则调用方拿到的是彩色散文）；`
    + `实际 stdout=${JSON.stringify(r.stdout.slice(0, 200))}`)
  const body = JSON.parse(r.stdout)
  assert.equal(body.success, false)
  assert.equal(body.code, 'INVALID_ARGUMENT')
  assert.equal(body.field, 'type', '要指出是哪个字段不合法')
})

test('vault search 真的读了 --days（不是个声明了没人用的开关）', () => {
  const r = runVaultSearch(['x', '--json', '--days', '0'])
  assert.equal(r.status, 1, '--days 超出范围应当退出 1')
  const body = JSON.parse(r.stdout)
  assert.equal(body.code, 'INVALID_ARGUMENT')
  assert.equal(body.field, 'days', '要指出是 days 不合法，而不是别的字段')
})

/** `vault search` 那段源码：按注释锚，到 `vault rag` 之前。
 *
 * 同名子命令在别处也有（顶层 `search`、`weread search`…），所以锚在**注释**上，并让调用方
 * 验证锚对了（`test/search-cli.test.ts` 第一版锚错命令块的教训）。
 */
function vaultSearchBlock(src: string): string {
  const start = src.indexOf('// vault search')
  if (start < 0) return ''
  const end = src.indexOf('// vault rag', start)
  return src.slice(start, end > start ? end : src.length)
}

/** 旗标 → commander 放进 `opts` 的名字（`--top-k` → `topK`）。与 `test/search-cli.test.ts` 同一对。 */
function optName(flag: string): string {
  const bare = flag.replace(/^--/, '').replace(/^no-/, '')
  return bare.replace(/-([a-z])/g, (_, c: string) => c.toUpperCase())
}

/** 在源码里找匹配，但**排除行内注释里提到的**。
 *
 * 为什么非排不可：这条守卫的第一次变异检查里，我把"这里原本是 `.option('--json', …)`"写进了注释，
 * 判据于是在注释里读到了一个"声明"，报**通过** —— 变异没被抓到。而被注释掉的声明同样意味着
 * "用户敲它只会拿到 unknown option"，所以这不是变异做得不对，是判据分不清声明和提及。
 * 判法只按行内 `//` 之前有没有内容，不做字符串扫描：这一块里没有含 `//` 的字符串字面量。
 */
function matchesOutsideComments(text: string, re: RegExp): string[] {
  const out: string[] = []
  for (const m of text.matchAll(re)) {
    const at = m.index!
    const lineStart = text.lastIndexOf('\n', at) + 1
    if (text.slice(lineStart, at).includes('//')) continue
    out.push(m[1])
  }
  return out
}

test('vault search：声明 ↔ 转发，两个方向都要对得上', () => {
  const SRC = readFileSync(join(process.cwd(), 'bin', 'weflow-cli.ts'), 'utf8')
  const block = vaultSearchBlock(SRC)
  assert.ok(block.length > 0, '定位不到 vault search 的命令块（注释被改了？）')
  assert.match(block, /vault_search\.py/, '定位到的不是 vault search（锚点失效了）')
  assert.ok(!block.includes('vault_rag.py'), '块越界了：把 vault rag 也框了进来')

  const declared = matchesOutsideComments(block, /\.option\('([^']+)'/g)
    .map(m => m.split(',').pop()!.trim().split(/\s+/)[0])
  assert.ok(declared.length >= 3, `只解析出 ${declared.length} 个声明，判据像是失效了`)

  const call = block.slice(block.indexOf('runPythonCmd('))
  assert.ok(call.length > 0, '定位不到 runPythonCmd 调用（结构变了？）')
  const args = call.slice(call.indexOf('['), call.indexOf(']'))

  // 方向一：声明了 ⇒ 必须转发（否则是个死开关）、必须被读过
  for (const flag of declared) {
    assert.ok(args.includes(`'${flag}'`),
      `${flag} 声明了但没出现在传给脚本的参数表里 —— 用户敲了不会有任何变化`)
    assert.ok(block.includes(`opts.${optName(flag)}`),
      `${flag} 声明了却没人读 opts.${optName(flag)}`)
  }
  // 方向二：转发给脚本的 ⇒ 必须声明过。缺了这一半，就抓不到"硬编码转发、旗标不存在"
  // （这次这个 bug 的原形），而那个形态**不会**让上面任何一条断言变红。
  const forwarded = matchesOutsideComments(args, /'(--[a-z-]+)'/g)
  const undeclared = forwarded.filter(f => !declared.includes(f))
  assert.deepEqual(undeclared, [],
    `这些旗标转发给了脚本却从没声明过 —— 用户敲它只会拿到 unknown option：${undeclared.join('、')}`)
  assert.ok(forwarded.length >= 3, `参数表只解析出 ${forwarded.length} 个旗标，判据像是失效了`)
})
