/**
 * 示例知识源插件：把一目录纯文本笔记转成知识库卡片。
 *
 * 它示范知识源插件的三个要点：
 *
 *   1. **作业从 stdin 进来**，不是从 argv。作业里带着 `out_dir`（唯一可写目录）和可选的
 *      `input_dir`。命令行在进程列表里对任何本地进程可见，所以不放那里。
 *   2. **只往 `out_dir` 写**。插件不需要知道知识库长什么样，也不需要知道 WeFlow 的配置在哪。
 *   3. **卡片是给消费端读的**：`compile_wiki.scan_articles` 只认带 `[[wikilink]]` 的卡片。
 *      一张没有链接的卡片会**静默地**不产生任何概念页——所以这里一定会写出概念行，
 *      宿主那边也会用 `plugin run` 把它们交给真消费端读一遍。
 *
 * 不用任何依赖、不联网、不调模型：换成 RSS、Readwise、Kindle 划线时，只是"怎么拿到内容"不同，
 * 产出卡片的形状完全一样。
 */
import { readFileSync, writeFileSync, readdirSync, statSync, mkdirSync, existsSync } from 'node:fs'
import { join, dirname, basename, extname } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))

/** 读完整的 stdin。作业是一次性的 JSON，所以等到 EOF 就行。 */
function readStdin() {
  return new Promise((resolve, reject) => {
    let buffer = ''
    process.stdin.setEncoding('utf8')
    process.stdin.on('data', chunk => { buffer += chunk })
    process.stdin.on('end', () => resolve(buffer))
    process.stdin.on('error', reject)
  })
}

/** `#标签` → 概念名。没有标签就退回文件名，保证每张卡片至少有一条链接。 */
function conceptsOf(body, title) {
  const tags = [...body.matchAll(/#([\p{L}\p{N}_-]{1,20})/gu)].map(m => m[1])
  const unique = [...new Set(tags)]
  if (unique.length) return unique.map(name => ({ name, desc: `来自《${title}》` }))
  return [{ name: title, desc: '这篇笔记的主题' }]
}

/** 消费端认的卡片：frontmatter + `## AI 摘要` + `[[name]] — 描述` 行。 */
function renderCard({ title, body, concepts, rel }) {
  const summary = body.trim().split(/\n\s*\n/)[0].replace(/\s+/g, ' ').slice(0, 300)
  const frontmatter = [
    '---',
    `title: ${JSON.stringify(title)}`,
    'type: plugin-card',
    'source: 纯文本笔记',
    'topic: 我的笔记',
    'tags: [插件, 纯文本]',
    `from: ${JSON.stringify(rel)}`,
    `noteChars: ${body.trim().length}`,
    '---',
  ].join('\n')
  const linkLines = concepts.map(c => `- [[${c.name}]] — ${c.desc}`).join('\n')
  return `${frontmatter}

# ${title}

## AI 摘要

（以下是插件从原文取的第一段，不是模型的理解）

${summary}

## 概念

${linkLines}
`
}

async function main() {
  const job = JSON.parse(await readStdin() || '{}')
  if (!job.out_dir) {
    console.error('作业里没有 out_dir')
    process.exit(2)
  }
  const inputDir = job.input_dir || join(HERE, 'sample')
  if (!existsSync(inputDir) || !statSync(inputDir).isDirectory()) {
    console.error(`输入目录不存在：${inputDir}`)
    process.exit(2)
  }

  mkdirSync(job.out_dir, { recursive: true })

  const files = readdirSync(inputDir)
    .filter(name => ['.txt', '.md'].includes(extname(name).toLowerCase()))
    .sort()

  let written = 0
  for (const name of files) {
    const raw = readFileSync(join(inputDir, name), 'utf8')
    const lines = raw.split(/\r?\n/)
    const title = (lines[0] || basename(name, extname(name))).replace(/^#+\s*/, '').trim()
    const body = lines.slice(1).join('\n')
    const card = renderCard({
      title,
      body,
      rel: name,
      concepts: conceptsOf(body, title),
    })
    // 文件名只保留安全字符：插件写的是"卡片"，不是"路径"
    const safe = basename(name, extname(name)).replace(/[^\p{L}\p{N}_-]/gu, '-')
    writeFileSync(join(job.out_dir, `${safe}.md`), card, 'utf8')
    written += 1
  }

  // stdout 只放结构化结果；调试信息一律走 stderr（宿主会分开收）
  process.stdout.write(JSON.stringify({ success: true, cards: written, input: inputDir }) + '\n')
}

main().catch(error => {
  console.error(error?.stack || String(error))
  process.exit(1)
})
