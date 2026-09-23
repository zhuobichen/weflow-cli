/**
 * AppMsg（Type=49）的显示形态 —— 3.x 那条路（`sqlcipherCore`）。
 *
 * 这段逻辑此前是 `sqlcipherCore` 的私有方法，**一行测试都没有**：一行 `[引用] ${title}`
 * 写错了也没有任何东西会红。改了它的起因是实测出来的一个数据丢失——37 条真实引用消息里，
 * `appmsg/title` 是**回复正文**、被引用的原文在 `refermsg/content`（中位 36 字、最长
 * 12733 字），而这里只取了前一段，于是模型看到的是一句"是呀，够得意个"却不知道在回什么。
 *
 * 微信 4.x 走的是 Python 侧 `nt_decrypt.py` 的 `non_text_display()`——同一条显示形态的
 * 第二份实现。所以下面有一组用例专门盯**两边格式一致**的那几个常量（分隔符、截断长度）：
 * 分叉了的话，同一条消息在不同微信版本里读起来会不一样。
 *
 * 纯函数测试：字符串进、字符串出，不读数据库、不碰模型。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

const { parseAppMsgXml, formatAppMsg, decodeXmlEntities, QUOTE_SEP, QUOTE_CLIP, TITLE_CLIP } =
  await import('../src/core/appMsgFormat.js')
const { clipWithMarker } = await import('../src/utils/text.js')

/** 一条真实形状的引用消息：`title` 是回复，被引原文在 `refermsg/content` */
const QUOTE_XML =
  '<msg><appmsg><title>是呀，够得意个[呲牙]</title><type>57</type>' +
  '<refermsg><type>1</type><svrid>123</svrid><content>难怪见退群了</content></refermsg>' +
  '</appmsg></msg>'

test('引用消息带上被引用的原文', () => {
  const out = formatAppMsg(parseAppMsgXml(QUOTE_XML))
  assert.equal(out, '[引用] 是呀，够得意个[呲牙] ｜ 引：难怪见退群了')
})

test('没有 refermsg 时只给回复，不留一个空落落的「引：」', () => {
  const xml = '<msg><appmsg><title>师兄这个课比较好嘛还是？</title><type>57</type></appmsg></msg>'
  assert.equal(formatAppMsg(parseAppMsgXml(xml)), '[引用] 师兄这个课比较好嘛还是？')
})

test('refermsg 里是占位值或空时，不算有原文', () => {
  for (const placeholder of ['', 'null', 'undefined', '0']) {
    const xml = '<msg><appmsg><title>好的</title><type>57</type>' +
      `<refermsg><type>1</type><content>${placeholder}</content></refermsg></appmsg></msg>`
    assert.equal(formatAppMsg(parseAppMsgXml(xml)), '[引用] 好的', `content=${placeholder}`)
  }
})

test('被引原文超长时截断并留省略号', () => {
  const long = '引'.repeat(300)
  const xml = '<msg><appmsg><title>看这个</title><type>57</type>' +
    `<refermsg><type>1</type><content>${long}</content></refermsg></appmsg></msg>`
  const out = formatAppMsg(parseAppMsgXml(xml))
  assert.equal(out, '[引用] 看这个 ｜ 引：' + '引'.repeat(QUOTE_CLIP) + '…')
})

test('标题也截断并留省略号', () => {
  // 不标记的截断读起来像一句说完的话
  const xml = `<msg><appmsg><title>${'标'.repeat(200)}</title><type>6</type></appmsg></msg>`
  assert.equal(formatAppMsg(parseAppMsgXml(xml)), '[文件] ' + '标'.repeat(TITLE_CLIP) + '…')
})

test('截断长度与分隔符要与 Python 侧一致（4.x 是另一份实现）', () => {
  // 这组值是 `nt_decrypt.py` 里的 QUOTE_CLIP / QUOTE_SEP / title 的 60。分叉了的话，
  // 同一条消息在 3.x 与 4.x 下读起来会不一样。
  assert.equal(QUOTE_CLIP, 120)
  assert.equal(TITLE_CLIP, 60)
  assert.equal(QUOTE_SEP, ' ｜ 引：')
})

test('链接、文件、未知类型各自成形', () => {
  const link = parseAppMsgXml('<msg><appmsg><title>一篇文章</title><type>5</type>' +
    '<des>一段描述</des><url>https://example.com/a</url></appmsg></msg>')
  assert.equal(formatAppMsg(link), '[分享] 一篇文章\n一段描述\nhttps://example.com/a')

  const file = parseAppMsgXml('<msg><appmsg><title>Base.csv</title><type>6</type></appmsg></msg>')
  assert.equal(formatAppMsg(file), '[文件] Base.csv')

  const unknown = parseAppMsgXml('<msg><appmsg><title>某个东西</title><type>88</type></appmsg></msg>')
  assert.equal(formatAppMsg(unknown), '[AppMsg] 某个东西')
})

test('title 与 des 都取不到时仍有兜底（不回空串）', () => {
  assert.equal(formatAppMsg(parseAppMsgXml('<msg><appmsg><type>5</type></appmsg></msg>')), '[链接/文件]')
  assert.equal(formatAppMsg({}), '[链接/文件]')
})

test('自闭合标签不算有内容', () => {
  // `<title />` 曾被当成开标签，正则一路吃到后面某个 `</title>`，把整段 XML 当文本返回
  const info = parseAppMsgXml('<msg><appmsg><title /><des /><type>8</type></appmsg></msg>')
  assert.equal(info.title, undefined)
  assert.equal(info.description, undefined)
})

test('CDATA 里的标题读得到', () => {
  const xml = '<msg><appmsg><title><![CDATA[微信转账]]></title><type>2000</type></appmsg></msg>'
  assert.equal(formatAppMsg(parseAppMsgXml(xml)), '[AppMsg] 微信转账')
})

test('消息类型只认数字，别处的 <type> 不算', () => {
  // `<refermsg><type>` 是**被引消息**的类型，不是这条消息的
  const info = parseAppMsgXml('<msg><appmsg><title>x</title><refermsg><type>1</type></refermsg></appmsg></msg>')
  assert.equal(info.type, undefined)
})

test('实体字符解回来', () => {
  assert.equal(decodeXmlEntities('a&amp;b&lt;c&gt;d&quot;e&apos;f'), 'a&b<c>d"e\'f')
  const info = parseAppMsgXml('<msg><appmsg><title>a&amp;b</title><type>6</type></appmsg></msg>')
  assert.equal(formatAppMsg(info), '[文件] a&b')
})

test('剪贴：刚好到限不标记，超一个字才标记', () => {
  assert.equal(clipWithMarker('abc', 3), 'abc')
  assert.equal(clipWithMarker('abcd', 3), 'abc…')
  assert.equal(clipWithMarker('', 3), '')
})
