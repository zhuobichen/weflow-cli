/**
 * 深度解析**默认不做**（用户 2026-11-19 的要求："深度解析一般情况下不启用，
 * 默认不启用，除非要求"）。
 *
 * 为什么要静态钉住：这一步「跑通」和「没跑」在产物上只差 md 里有没有 `## 深度解析`
 * 一节，而日报主页、邮件、退出码全都正常——也就是说**它被改回默认开启，没有任何东西会红**，
 * 只有那个晚上的日报会从 20 分钟变成 60 分钟。这类"只体现在时长上"的退步，
 * 靠自觉是拦不住的。
 *
 * 量到的事实（2026-11-19 实测，577 篇的那一天）：深度解析覆盖 AI 91 + 学术 77 = 168 篇，
 * 串行每篇约 15 秒，合计约 40 分钟；单篇成本很低（1.8 秒、几百 token），
 * 所以"贵"的不是钱，是**串行的时间**。
 *
 * 顺带记一条：本机 4B 决策模型（APUS Reflex）不能拿来做这件事——它是思考型，
 * 给 2000 token 会全部花在 reasoning_content 上、最终 content 为空（实测 26.7 秒、空输出）。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const CLASSIFY = readFileSync(join(process.cwd(), 'scripts', 'classify_daily.py'), 'utf8')
const PIPELINE = readFileSync(join(process.cwd(), 'scripts', 'pipeline.py'), 'utf8')
const CLI = readFileSync(join(process.cwd(), 'bin', 'weflow-cli.ts'), 'utf8')

test('classify_daily：深度解析由 --deep 把关，默认不做', () => {
  assert.match(CLASSIFY, /'--deep',\s*action='store_true'/, '要有 --deep 开关')
  // 不许再出现"默认做、可跳过"的形态：那正是会被改回去的方向
  assert.doesNotMatch(CLASSIFY, /'--skip-deep'/, '不该有 --skip-deep —— 默认就该是不做')
  // 两个产出（深度解析、行动建议）都必须挂在这个开关上
  assert.match(CLASSIFY, /if interest_files and not args\.deep:/, '深度解析要由 --deep 把关')
  assert.match(CLASSIFY, /if args\.deep and not args\.skip_action:/, '行动建议也要由 --deep 把关')
  // 跳过时要说出来：否则用户只看到"没有深度解析"，分不清是设计还是坏了
  assert.match(CLASSIFY, /跳过深度摘要/, '跳过时要说明原因与怎么打开')
})

test('pipeline：--deep 只在显式给出时才转发给 classify_daily', () => {
  assert.match(PIPELINE, /add_argument\('--deep'/, 'pipeline 要有 --deep')
  assert.match(PIPELINE, /if args\.deep:\s*\n\s*step2_args \+= \['--deep'\]/,
    '只在 args.deep 为真时转发，不能无条件加')
})

test('CLI：daily 有 --deep，并只透传一次', () => {
  assert.match(CLI, /\.option\('--deep',/, 'daily 要有 --deep')
  assert.equal((CLI.match(/args\.push\('--deep'\)/g) || []).length, 1,
    '--deep 应当只有一处透传（重复 push 会让参数出现两次）')
})
