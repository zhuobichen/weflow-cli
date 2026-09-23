/**
 * Type=49 AppMsg 的**显示形态**：链接、文件、引用、转账…
 *
 * 为什么单独一个文件：这段解析此前是 `sqlcipherCore` 的私有方法，**一行测试都没有**——
 * 而它们其实都是纯函数（字符串进、字符串出），提到模块级就能直接测。
 *
 * 它是 Python 侧 `nt_decrypt.py` 的 `non_text_display()` 的**第二份实现**：微信 4.x 走
 * Python 那份（`ntCore` 是个 Python 桥），3.x 走这份。两边的格式必须一致，否则同一条消息
 * 在不同微信版本里读起来不一样——截断长度与分隔符尤其容易分叉，所以下面三个常量在两个
 * 文件里是同一组值。
 */

import { clipWithMarker } from '../utils/text.js'

export interface AppMsgInfo {
  title?: string
  description?: string
  url?: string
  type?: string
  /** 引用消息里被引用的原文（`refermsg/content`），与 Python 侧同名 */
  quotedText?: string
}

/** 回复正文与被引原文之间的分隔。用词而不是符号：下游会把它读成一句话。 */
export const QUOTE_SEP = ' ｜ 引：'
/** 被引原文的截断长度。实测 37 条真实引用消息：中位 36 字、最长的 12733 字。 */
export const QUOTE_CLIP = 120
/** 标题类文本的截断长度 */
export const TITLE_CLIP = 60

/** 占位值不是内容——`<content>null</content>` 按"没有原文"处理 */
const PLACEHOLDERS = ['null', 'undefined', '0']

export function decodeXmlEntities(str: string): string {
  return str
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
}

/**
 * 取一个标签的文本，取不到回 undefined（自闭合 `<title />` 也算取不到）。
 *
 * 起始标签里不许出现 `/`：否则 `<title />` 会被当成开标签，一路吃到后面某个 `</title>`，
 * 把中间整段 XML 当成文本返回。这条纪律与 Python 侧的 `_xml_text` 相同。
 */
function tagText(xml: string, tag: string): string | undefined {
  const pattern = new RegExp(`<${tag}(?:\\s[^>/]*)?>(?:<!\\[CDATA\\[)?([\\s\\S]*?)(?:\\]\\]>)?</${tag}>`)
  const match = xml.match(pattern)
  const text = match ? decodeXmlEntities(match[1].trim()) : ''
  return text || undefined
}

/**
 * 解析 Type=49 AppMsg XML。
 *
 * 引用消息（`<type>57</type>`）的形状是**实测**出来的，不是猜的：`<title>` 是回复正文，
 * 被引用的原文在 `<refermsg><content>`。此前只取 title，于是模型看到的是一句
 * "是呀，够得意个"却不知道在回什么——引用不带原文等于没引用。
 */
export function parseAppMsgXml(xml: string): AppMsgInfo {
  try {
    // 一律在 `<appmsg>` 段里找：`<refermsg>` 里的 `<type>` 是**被引消息**的类型，
    // 全文档取第一个会把它当成这条消息的类型（实测真的会）。没有 `<appmsg>` 外层
    // 包装的载荷退回整份文档——与原行为一致，不因为这次改动少读到东西。
    const appmsg = xml.match(/<appmsg(?:\s[^>]*)?>([\s\S]*?)<\/appmsg>/)
    const inner = appmsg ? appmsg[1] : xml

    const referBlock = inner.match(/<refermsg>([\s\S]*?)<\/refermsg>/)
    const rawQuoted = referBlock ? tagText(referBlock[1], 'content') : undefined
    // 摘掉 `<refermsg>` 再取本条消息自己的字段：被引消息的 `<type>`/`<title>` 不是这条的。
    const scope = referBlock ? inner.replace(referBlock[0], '') : inner

    const rawType = tagText(scope, 'type')

    return {
      title: tagText(scope, 'title'),
      description: tagText(scope, 'des'),
      url: tagText(scope, 'url'),
      type: rawType && /^\d+$/.test(rawType) ? rawType : undefined,
      quotedText: rawQuoted && !PLACEHOLDERS.includes(rawQuoted.toLowerCase()) ? rawQuoted : undefined,
    }
  } catch {
    return {}
  }
}

/** AppMsgInfo → 给人看的文本。取不到任何东西时回 `[链接/文件]`（原有兜底）。 */
export function formatAppMsg(info: AppMsgInfo): string {
  const parts: string[] = []
  const title = clipWithMarker(info.title ?? '', TITLE_CLIP)

  switch (info.type) {
    case '5': // 链接分享
      if (title) parts.push(`[分享] ${title}`)
      if (info.description) parts.push(info.description)
      if (info.url) parts.push(info.url)
      break
    case '6': // 文件分享
      if (title) parts.push(`[文件] ${title}`)
      break
    case '57': { // 引用回复：回复正文 + 被引原文
      const quoted = clipWithMarker(info.quotedText ?? '', QUOTE_CLIP)
      if (quoted) parts.push(title ? `[引用] ${title}${QUOTE_SEP}${quoted}` : `[引用] ${quoted}`)
      else if (title) parts.push(`[引用] ${title}`)
      break
    }
    default:
      if (title) parts.push(`[AppMsg] ${title}`)
      if (info.description) parts.push(info.description)
      break
  }

  return parts.join('\n') || '[链接/文件]'
}
