/**
 * 助手工具里的纯函数：抓取前的 **SSRF 边界**、以及正文提取。
 *
 * `read_favorite` 是助手唯一会去抓远程 URL 的工具，而"能不能抓"这个判断就是
 * `isSafeUrl`。它此前**没有被任何测试碰过**——而它的失效方式不会抛异常：一条收藏里的链接
 * 会把助手（进而把一个带本地数据库访问权的进程）带向内网或云元数据地址。
 *
 * 正文提取那三个也一样：它们是"送去脱敏、再由云端模型阅读"的那段文字的来源，抽错了不会
 * 报错，只会让模型读到导航栏、或者干脆读不到正文。
 *
 * 纯函数测试，不联网、不读数据库、不碰家目录。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

const { isSafeUrl, stripTags, extractText, extractFromChallengePage,
        availableToolDefs, unavailableToolReason } =
  await import('../src/services/assistantTools.js')

test('公网 http(s) 链接放行', () => {
  assert.equal(isSafeUrl('https://mp.weixin.qq.com/s/AbC123'), true)
  assert.equal(isSafeUrl('http://example.com/a?b=1#c'), true)
  assert.equal(isSafeUrl('https://8.8.8.8/'), true, '公网 IP 不是内网')
})

test('环回、私网与云元数据地址一律拒绝', () => {
  for (const url of [
    'http://localhost/x',
    'http://127.0.0.1/',
    'http://127.0.0.1:8080/',
    'http://127.1/',                       // 短写形式
    'http://10.0.0.5/',
    'http://192.168.1.1/',
    'http://169.254.169.254/latest/meta-data/',  // 云元数据
    'http://0.0.0.0/',
    'http://172.16.9.9/',
    'http://172.31.255.254/',
  ]) {
    assert.equal(isSafeUrl(url), false, url)
  }
})

test('172.16-172.31 是私网，172.32 不是（边界不能多拦）', () => {
  assert.equal(isSafeUrl('http://172.15.0.1/'), true)
  assert.equal(isSafeUrl('http://172.32.0.1/'), true)
})

test('非 http(s) 协议一律拒绝', () => {
  for (const url of ['file:///etc/passwd', 'ftp://example.com/x', 'javascript:alert(1)',
                     'data:text/html,<h1>x</h1>', 'gopher://example.com/']) {
    assert.equal(isSafeUrl(url), false, url)
  }
})

test('不是 URL 的输入不抛异常，直接拒绝', () => {
  for (const raw of ['', '   ', '不是一个链接', 'http://']) {
    assert.equal(isSafeUrl(raw), false, JSON.stringify(raw))
  }
})

test('域名里含 localhost 或私网前缀的**公网域名**不该被误杀', () => {
  // 判断必须是相等/前缀精确匹配，不能用 includes —— 否则会拦掉正常站点。
  assert.equal(isSafeUrl('https://localhost.evil.com/'), true)
  assert.equal(isSafeUrl('https://my-127.example.com/'), true)
  assert.equal(isSafeUrl('https://10.example.com/'), true)
})

test('整数形式的 IPv4 会被 URL 解析器归一，仍须拦住', () => {
  // 2130706433 == 127.0.0.1；0x7f000001 同理。内核认这些写法，所以这里不能漏。
  assert.equal(isSafeUrl('http://2130706433/'), false)
  assert.equal(isSafeUrl('http://0x7f000001/'), false)
  assert.equal(isSafeUrl('http://017700000001/'), false)
})

test('IPv6 的环回与内网形态也要拦住', () => {
  for (const url of [
    'http://[::1]/',
    'http://[0:0:0:0:0:0:0:1]/',
    'http://[::ffff:127.0.0.1]/',   // IPv4 映射
    'http://[fd00::1]/',            // 唯一本地地址
    'http://[fe80::1]/',            // 链路本地
  ]) {
    assert.equal(isSafeUrl(url), false, url)
  }
})

test('stripTags 丢掉脚本与样式，解码实体，保留文字', () => {
  const html = '<div><script>var x = 1 < 2;</script><style>p{color:red}</style>'
    + '<p>第一段 &amp; 第二段</p><p>第三段&nbsp;完</p></div>'
  const text = stripTags(html)
  assert.doesNotMatch(text, /var x/, '脚本内容不许留下')
  assert.doesNotMatch(text, /color:red/, '样式不许留下')
  assert.match(text, /第一段 & 第二段/)
  assert.match(text, /第三段 完/)
})

test('extractText 取 js_content 区块并按 div 配平截断', () => {
  const html = '<html><body><div id="js_content"><p>正文一</p><div><p>正文二</p></div></div>'
    + '<div id="other"><p>不该出现</p></div></body></html>'
  const text = extractText(html)
  assert.match(text, /正文一/)
  assert.match(text, /正文二/)
  assert.doesNotMatch(text, /不该出现/, '配平后不该把后面的区块一起吞进来')
})

test('extractText 在配平失败时仍有兜底，不返回空', () => {
  // js_content 之后没有闭合 div（真实页面被截断的样子）
  const html = '<div id="js_content"><p>只有开头</p>'
  assert.match(extractText(html), /只有开头/)
})

test('extractText 没有 js_content 时退到 body', () => {
  assert.match(extractText('<html><body><p>整页正文</p></body></html>'), /整页正文/)
})

test('WAF 挑战页：解出 content_noencode 里的转义字符串', () => {
  const html = `<script>var cgiDataNew = { content_noencode: '<p>\\u4e00\\x41\\n第二行内容填充内容填充内容填充内容填充内容填充内容填充内容填充内容填充内容填充内容填充内容填充内容填充内容填充内容填充内容填充内容填充内容填充内容填充内容填充内容填充内容填充内容填充内容填充内容填充内容填充</p>' }</script>`
  const decoded = extractFromChallengePage(html)
  assert.ok(decoded, '应该解出来了')
  assert.match(decoded!, /\\u4e00/, '\\u 形式不解析，原样保留（解析器只处理 \\x 与 \\n 等）')
  assert.match(decoded!, /A/, '\\x41 要解码成 A')
  assert.match(decoded!, /第二行/)
})

test('WAF 挑战页：没有该字段或内容太短时返回 null', () => {
  assert.equal(extractFromChallengePage('<html><body>普通页面</body></html>'), null)
  assert.equal(extractFromChallengePage("content_noencode: '太短'"), null, '短于 100 字视为没解出来')
})

// --------------------------------------------- 工具表按配置过滤

test('没配 key 的工具不出现在工具表里（摆了跑不了的，模型会去试、拿一个错）', () => {
  // 实测（评测的 ambiguous-contact 那次）：模型连试两个检索工具、两个都报错，答复里带着
  // "两个检索工具都跑不通"。那不是助手的问题，是我们**摆了一个跑不了的工具**。
  const empty = () => ''
  const names = (config: any) => availableToolDefs(config).map(d => d.function.name)

  const withoutKeys = names(empty)
  assert.equal(withoutKeys.includes('search_semantic'), false, '没 dashscope key 就不该摆语义检索')
  assert.equal(withoutKeys.includes('get_weread'), false, '没 weread key 就不该摆微信读书')
  assert.equal(withoutKeys.includes('get_messages'), true, '本地工具照旧')

  const full = names((key: string) => (key === 'dashscopeApiKey' || key === 'wereadApiKey' ? 'x' : ''))
  assert.equal(full.includes('search_semantic'), true)
  assert.equal(full.includes('get_weread'), true)
  assert.equal(full.length, withoutKeys.length + 2)
})

test('过滤的理由要说得出是哪一样缺了', () => {
  assert.match(unavailableToolReason('search_semantic', () => '')!, /dashscopeApiKey/)
  assert.match(unavailableToolReason('get_weread', () => '')!, /wereadApiKey/)
  assert.equal(unavailableToolReason('get_messages', () => ''), null)
  assert.equal(unavailableToolReason('search_semantic', () => 'k'), null)
})
