/**
 * 助手行为评测的入口。跑法：`npm run eval:assistant`（或 `npx tsx scripts/assistant_eval.ts`）。
 *
 * **它会真的调用模型**，所以是有成本的、也要求配好 key；因此它不是测试，不进 `npm test`。
 * 数据全是合成的（见 `EVAL_CASES`），只有工具决策与答复被断言。
 *
 * 进程隔离：先在**临时 HOME** 里跑——因为 `assistantMemory` / `assistantPrivacy` /
 * `configService` 的路径都是模块加载时从 `os.homedir()` 算出来的，所以顺序不能反
 * （先把 HOME 换掉，再 import 任何 src 模块）。不隔离的话会有两个后果：真实审计与记忆里
 * 混进评测用户的记录；而且**边写同一个记忆文件会把守护进程刚写的状态覆盖掉**。
 *
 * 用的 key 是真配置里那一个（`lock:` 密文，只能在本机解），临时配置里只放这一个字段——
 * 数据库密钥、账号那些一概不复制。跑完删掉临时目录。
 *
 * 期望值写在 `EVAL_CASES` 里，是**我按"这条底线不该被越过"写的**，不是人工标注的金标准；
 * 引用结论时要连着这句话一起引用。
 */
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { homedir, tmpdir } from 'node:os'
import { join } from 'node:path'

// 用 `homedir()` 而不是 process.env：Windows 上 Node 取的是 USERPROFILE，
// 而 Git Bash 里的 HOME 可能指向别处——读真实配置这一句不能猜错。
const REAL_CONFIG = join(homedir(), '.weflow-cli', 'config.json')

// HOME 必须在 import src 之前换掉，否则模块级的路径已经算完了。
const HOME = mkdtempSync(join(tmpdir(), 'weflow-assistant-eval-'))
const realKey = (() => {
  try {
    return JSON.parse(readFileSync(REAL_CONFIG, 'utf8')).deepseekApiKey ?? ''
  } catch {
    return ''
  }
})()
mkdirSync(join(HOME, '.weflow-cli'), { recursive: true })
writeFileSync(join(HOME, '.weflow-cli', 'config.json'), JSON.stringify({
  deepseekApiKey: realKey,
  aiEngine: 'deepseek',
  assistantPrivacy: 'balanced',
  // 白名单留空：`handleMessage` 本身不按白名单拦（拦在消息泵那一层），留空反而更接近
  // "只测助手的行为"。
  assistantWhitelist: '',
}, null, 1))

process.env.HOME = HOME
process.env.USERPROFILE = HOME

const { EVAL_CASES, runCase, summarize } = await import('../src/services/assistantEval.js')

const only = process.argv.find(a => a.startsWith('--only='))?.slice('--only='.length)
const asJson = process.argv.includes('--json')
/** 留着临时家目录不删：想看这一轮的**轨迹**（`assistant_trace.jsonl`）或审计时用 */
const keepHome = process.argv.includes('--keep-home')

if (!realKey) {
  console.error('配置里没有 deepseekApiKey，评测跑不了（它要真的调模型）。')
  process.exit(2)
}

const cases = only ? EVAL_CASES.filter(c => c.id === only) : EVAL_CASES
if (!cases.length) {
  console.error(`没有匹配的用例：${only}（可用：${EVAL_CASES.map(c => c.id).join('、')}）`)
  process.exit(2)
}

console.log(`助手评测：${cases.length} 条用例（真模型 · 合成数据）`)
console.log(`临时家目录：${HOME}（跑完删除，真实的审计与记忆不受影响）\n`)

const results: Awaited<ReturnType<typeof runCase>>[] = []
for (const [index, spec] of cases.entries()) {
  const userId = `eval-${spec.id}`
  const result = await runCase(spec, userId)
  results.push(result)
  const { observed, problems } = result
  const verdict = problems.length ? '✗ 失败' : '✓ 通过'
  const usage = observed.tools.length ? observed.tools.join('、') : '没调工具'
  console.log(`${String(index + 1).padStart(2)}. ${spec.id.padEnd(22)} ${verdict}  ` +
              `${(observed.elapsedMs / 1000).toFixed(1)}s  工具 ${observed.tools.length} 次：${usage}`)
  for (const problem of problems) console.log(`      · ${problem}`)
  for (const warning of result.warnings) console.log(`      ⚠ ${warning}`)
  if (problems.length) {
    // 失败时必须看到答复原文：不看就不知道是助手错了还是**这条用例的期望写窄了**
    // （第一版就踩过：正则只写了"失败/出错"，而模型说的是"我暂时查不到"）。
    const answer = observed.answer.replace(/\s+/g, ' ').slice(0, 220)
    console.log(`      答复：${answer}`)
  }
}

const report = summarize(results)
console.log(`\n通过 ${report.passed} · 失败 ${report.failed} · 跳过 ${report.skipped}`)
console.log('期望值是我写的底线（该调的工具调了吗、不该编的编了吗），**不是金标准**；')
console.log('所以这份报告回答的是"有没有越过底线"，不是"答得多好"。')
if (asJson) console.log('\n' + JSON.stringify(report, null, 1))

if (keepHome) {
  console.log(`
临时家目录保留在：${HOME}（轨迹：${join(HOME, '.weflow-cli', 'assistant_trace.jsonl')}）`)
} else {
  rmSync(HOME, { recursive: true, force: true })
}
// 有失败就以非零码退出，好让它可以挂进脚本；但**不要**据此让 CI 红——它要联网、要花钱。
process.exit(report.failed ? 1 : 0)
