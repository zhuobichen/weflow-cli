/**
 * 快路径路由的实测探针（D-035）。跑法：`node --import tsx scripts/assistant_route_probe.ts`
 *
 * **它会真的调用本机判断层**（出境内容只有这些问题本身），所以是有成本的、也是要联网的；
 * 因此它是探针，不是测试。测试里那条路走的是注入的假判断层。
 *
 * 期望值写在 `CASES` 的第二列，是**我按"显而易见该查哪一项"写的**，不是人工标注的金标准——
 * 引用它的结论时必须连着这句话一起引用。
 */
/**
 * 量一次快路径的真实路由：拿一组典型用户问题打真实判断层，看它挑什么。
 * 期望值是我自己写的（"显而易见该查哪一项"），**不是人工标注的金标准**——报告里要这么写。
 * 只在免费期内跑，出境内容只有问题本身。
 */
import { decideRoute } from '../src/services/assistantRouter.js'

const CASES = [
  ['今天公众号推了什么', 'get_daily_report'],
  ['最近和谁聊得比较多', 'list_sessions'],
  ['我在读什么书', 'weread_shelf'],
  ['我的读书笔记里都有哪些书', 'weread_notebooks'],
  ['我有什么待办', 'todos_pending'],
  ['我已经做完了哪些事', 'todos_done'],
  ['朋友圈最近有啥动态', 'sns_timeline'],
  ['朋友圈一共发了多少条', 'sns_stats'],
  ['我的收藏有多少条', 'get_stats'],
  ['有哪些聊天会话', 'list_sessions'],
  ['总结一下我和丁ding的聊天', 'none'],
  ['那篇文章讲了什么', 'none'],
  ['你好呀', 'none'],
  ['帮我写一首关于秋天的诗', 'none'],
  ['把这句话翻译成英文：今天天气不错', 'none'],
  ['我明天该干嘛', 'todos_pending'],
  ['最近有没有什么书可以读', 'weread_shelf'],
]

let hit = 0, routed = 0, wrong = [], totalMs = 0
for (const [question, expected] of CASES) {
  const started = Date.now()
  const decision = await decideRoute(question)
  const ms = Date.now() - started
  totalMs += ms
  const picked = decision.capability ? decision.capability.name : 'none'
  if (decision.capability) routed++
  const ok = picked === expected
  if (ok) hit++
  else wrong.push({ question, expected, picked, reason: decision.reason })
  console.log(`${ok ? '✓' : '✗'} ${question.padEnd(22)} → ${picked.padEnd(18)} ` +
    `needs_tool=${decision.needsTool.toFixed(2)} conf=${decision.confidence.toFixed(2)} ${ms}ms`)
  if (!ok) console.log(`    ${decision.reason}`)
}

console.log(`\n共 ${CASES.length} 条：与期望一致 ${hit}，路由到具体能力 ${routed}，平均 ${Math.round(totalMs / CASES.length)}ms`)
if (wrong.length) {
  console.log('不一致的：')
  for (const w of wrong) console.log(`  ${w.question} → 期望 ${w.expected}，实际 ${w.picked}`)
}
