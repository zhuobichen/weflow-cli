/**
 * 入站媒体落地（`src/services/wechatMedia.ts` + `parseInboundMessage` 的接线）。
 *
 * 为什么要测这些：这条路上四种坏法**全都是静默的** ——
 * - 开关忘了默认关 → 用户没要，进程却去访问 CDN；
 * - 服务端给的文件名直接当路径用 → `../` 把文件写到 `output/` 之外；
 * - 图片用错 key（`media.aes_key` 而不是 `image_item.aeskey`）→ 解出乱码但**不报错**；
 * - 下载失败留下 0 字节文件 → 下游以为"有这张图"，比没有更难查。
 * 所以每条都按下一条断言，而不是只测"成功路径能跑"。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import crypto from 'crypto'
import { mkdtemp, readFile, readdir } from 'fs/promises'
import { existsSync } from 'fs'
import { tmpdir } from 'os'
import { dirname, join } from 'path'

import {
  WECHAT_MEDIA_MAX_BYTES,
  extFromMagic,
  mediaTypeOf,
  pickAesKeyValue,
  pickEncryptParam,
  sanitizeFileName,
  saveInboundMedia,
  wechatMediaDir,
} from '../src/services/wechatMedia.js'
import { WechatClient } from '../src/core/wechatClient.js'
import { WechatMessageService } from '../src/services/wechatMessageService.js'

const PNG = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(8, 7)])

async function tmpMediaDir(): Promise<string> {
  return mkdtemp(join(tmpdir(), 'weflow-media-'))
}

test('文件名：服务端给的名字只当字符串用，路径逃逸在本地就被吃掉', () => {
  assert.equal(sanitizeFileName('../../evil.svg'), 'evil.svg')
  assert.equal(sanitizeFileName('..\\..\\evil.svg'), 'evil.svg')
  assert.equal(sanitizeFileName('/etc/passwd'), 'passwd')
  assert.equal(sanitizeFileName('a<b>c:d"e|f?g*h.txt'), 'a_b_c_d_e_f_g_h.txt')
  assert.equal(sanitizeFileName('..'), '', '去掉前导点之后是空 ⇒ 调用方必须自己造名字')
  assert.equal(sanitizeFileName(''), '')
})

test('扩展名：按魔数认，认不出就不猜', () => {
  assert.equal(extFromMagic(PNG), '.png')
  assert.equal(extFromMagic(Buffer.from([0xff, 0xd8, 0xff, 0xe0])), '.jpg')
  assert.equal(extFromMagic(Buffer.from('GIF89a', 'latin1')), '.gif')
  assert.equal(extFromMagic(Buffer.from('RIFF....WEBPVP8 ', 'latin1')), '.webp')
  assert.equal(extFromMagic(Buffer.from('....ftypisom', 'latin1')), '.mp4')
  assert.equal(extFromMagic(Buffer.from('这是随便的字节')), '')
})

test('媒体项类型与取值：图片优先 image_item.aeskey（hex），其余用 media.aes_key（base64）', () => {
  assert.equal(mediaTypeOf(2), 'image')
  assert.equal(mediaTypeOf(4), 'file')
  assert.equal(mediaTypeOf(1), null, '文本不是媒体项')
  assert.equal(mediaTypeOf(9), null)

  const imageItem = {
    type: 2,
    image_item: {
      aeskey: 'aabbccddeeff00112233445566778899',
      media: { encrypt_query_param: 'PARAM', aes_key: 'BASE64FALLBACK' },
    },
  }
  assert.equal(pickEncryptParam(imageItem, 'image'), 'PARAM')
  assert.equal(pickAesKeyValue(imageItem, 'image'), 'aabbccddeeff00112233445566778899',
    '图片这一支必须先用 hex 的 aeskey —— 用错 key 只会解出乱码，不报错')

  const voiceItem = { type: 3, voice_item: { media: { encrypt_query_param: 'P2', aes_key: 'QkFTRTY0' } } }
  assert.equal(pickAesKeyValue(voiceItem, 'record'), 'QkFTRTY0')
  assert.equal(pickAesKeyValue({ type: 3, voice_item: { media: {} } }, 'record'), '',
    '没有 key 就是没有，不许编一个')
})

test('落盘：成功时写出的字节与下载结果逐字节相同，且路径就在指定目录下', async () => {
  const dir = await tmpMediaDir()
  let asked: [string, string] | null = null
  const result = await saveInboundMedia({
    item: { image_item: { aeskey: 'AA', media: { encrypt_query_param: 'p', aes_key: 'k' } } },
    mediaType: 'image',
    index: 1,
    messageId: 'msg-123',
    dir,
    download: async (param, key) => { asked = [param, key]; return PNG },
  })
  assert.equal(dirname(result.path), dir, '文件必须落在指定目录里')
  assert.ok(result.path.includes('msg-123'), '名字里带消息 id，事后能对上是哪条消息')
  assert.ok(result.path.endsWith('.png'), '按魔数给后缀')
  assert.deepEqual(await readFile(result.path), PNG, '写出的字节必须原样')
  assert.deepEqual(asked, ['p', 'AA'])
})

test('落盘：文件类消息沿用服务端文件名，但逃逸不出来', async () => {
  const dir = await tmpMediaDir()
  const result = await saveInboundMedia({
    item: { file_item: { file_name: '../../evil.txt', media: { encrypt_query_param: 'p', aes_key: 'k' } } },
    mediaType: 'file',
    index: 1,
    messageId: 'msg-9',
    dir,
    serverFileName: '../../evil.txt',
    download: async () => Buffer.from('hello'),
  })
  assert.equal(dirname(result.path), dir)
  assert.ok(result.path.endsWith('evil.txt'))
  assert.equal((await readdir(dir)).length, 1, '不许在目录外多出东西')
})

test('落盘：缺参数/失败/超限/空结果都返回空路径，且不留文件', async () => {
  const dir = await tmpMediaDir()
  let called = 0
  const download = async () => { called += 1; return PNG }

  const noParam = await saveInboundMedia({
    item: { image_item: { media: { aes_key: 'k' } } }, mediaType: 'image', index: 1,
    messageId: 'm', dir, download,
  })
  assert.equal(noParam.path, '')
  assert.match(noParam.reason, /encrypt_query_param/)
  assert.equal(called, 0, '参数都不全就不该发生网络请求')

  const noKey = await saveInboundMedia({
    item: { image_item: { media: { encrypt_query_param: 'p' } } }, mediaType: 'image', index: 1,
    messageId: 'm', dir, download,
  })
  assert.equal(noKey.path, '')
  assert.equal(called, 0)

  const failed = await saveInboundMedia({
    item: { image_item: { aeskey: 'k', media: { encrypt_query_param: 'p' } } }, mediaType: 'image',
    index: 1, messageId: 'm', dir, download: async () => { throw new Error('HTTP 500') },
  })
  assert.equal(failed.path, '')
  assert.match(failed.reason, /HTTP 500/, '失败原因要带上，不然只有"没落地"三个字')

  const empty = await saveInboundMedia({
    item: { image_item: { aeskey: 'k', media: { encrypt_query_param: 'p' } } }, mediaType: 'image',
    index: 1, messageId: 'm', dir, download: async () => Buffer.alloc(0),
  })
  assert.equal(empty.path, '')

  const big = await saveInboundMedia({
    item: { image_item: { aeskey: 'k', media: { encrypt_query_param: 'p' } } }, mediaType: 'image',
    index: 1, messageId: 'm', dir, download: async () => Buffer.alloc(WECHAT_MEDIA_MAX_BYTES + 1),
  })
  assert.equal(big.path, '')
  assert.match(big.reason, /上限/)

  assert.equal(existsSync(dir) ? (await readdir(dir)).length : 0, 0, '四种失败都不许留下半个文件')
})

test('默认关：不打开开关时，解析入站消息**不发起任何下载**', async () => {
  const svc: any = new WechatMessageService({ mediaDownload: false, mediaDir: await tmpMediaDir() })
  let calls = 0
  svc.client = { downloadMedia: async () => { calls += 1; return PNG } }

  const msg = await svc.parseInboundMessage({
    from_user_id: 'peer@im.wechat',
    client_id: 'mid-1',
    item_list: [{ type: 2, image_item: { aeskey: 'k', media: { encrypt_query_param: 'p' } } }],
  })
  assert.equal(calls, 0, '默认关着还去访问 CDN，就是把"本地优先"说成了空话')
  assert.deepEqual(msg.components, [{ type: 'image', filePath: '' }],
    '关着时 filePath 是空串：下游知道"没有这玩意"')
  assert.equal(msg.messageKind, 'image')
})

test('打开开关：入站图片落到本地，filePath 指向真实文件', async () => {
  const dir = await tmpMediaDir()
  const svc: any = new WechatMessageService({ mediaDownload: true, mediaDir: dir })
  svc.client = {
    downloadMedia: async (param: string, key: string) => {
      if (param === 'p') assert.equal(key, 'hexkey', '图片必须用 image_item.aeskey')
      return PNG
    },
  }

  const msg = await svc.parseInboundMessage({
    from_user_id: 'peer@im.wechat',
    client_id: 'mid-2',
    item_list: [
      { type: 1, text_item: { text: '看这个' } },
      { type: 2, image_item: { aeskey: 'hexkey', media: { encrypt_query_param: 'p', aes_key: 'b64' } } },
      { type: 4, file_item: { file_name: '报告.pdf', media: { encrypt_query_param: 'p2', aes_key: 'b64' } } },
    ],
  })

  assert.equal(msg.components[0].type, 'plain')
  const [img, file] = msg.components.slice(1) as any[]
  assert.ok(img.filePath && existsSync(img.filePath), '图片应当真的躺在磁盘上')
  assert.deepEqual(await readFile(img.filePath), PNG)
  assert.ok(file.filePath.endsWith('报告.pdf'), '文件类保留服务端给的名字（已消毒）')
  assert.equal(file.name, '报告.pdf')
  assert.equal(msg.messageStr, '看这个', '有媒体也不许把文本弄丢')
})

test('真实解密链路：URL 形式 + AES-128-ECB + 去 PKCS#7（假 fetch，不联网）', async () => {
  const key = Buffer.from('00112233445566778899aabbccddeeff', 'hex')
  const plain = Buffer.from('这是一张图的字节（测试用）')
  const cipher = crypto.createCipheriv('aes-128-ecb', key, null)
  const body = Buffer.concat([cipher.update(plain), cipher.final()])

  const realFetch = globalThis.fetch
  const seen: string[] = []
  globalThis.fetch = (async (url: any) => {
    seen.push(String(url))
    return { status: 200, arrayBuffer: async () => body } as any
  }) as any
  try {
    const client = new WechatClient({ cdnBaseUrl: 'https://cdn.example/c2c' })
    const out = await client.downloadMedia('enc=1&x=2', key.toString('hex'))
    assert.deepEqual(out, plain, '解出来的必须是原文，padding 要去干净')
    assert.equal(seen[0], 'https://cdn.example/c2c/download?encrypted_query_param=enc%3D1%26x%3D2',
      'URL 形状要对得上厂商实现（encrypted_query_param 需转义）')
    const viaBase64 = await client.downloadMedia('p', key.toString('base64'))
    assert.deepEqual(viaBase64, plain, 'base64 那一种编码也要能认')
  } finally {
    globalThis.fetch = realFetch
  }
})

test('落地目录：默认在可写输出根下，可用环境变量改（测试与自定义都不碰仓库）', async () => {
  // 默认值从"cwd 相对的 output/"改成了 `resolveOutputRoot()/wechat-media`：
  // cwd 可能是只读目录，包目录在全局安装时也不可写（2026-11-01 Linux 实测）。
  const { resolveOutputRoot } = await import('../src/utils/outputRoot.ts')
  const expected = join(resolveOutputRoot(), 'wechat-media')
  assert.equal(wechatMediaDir({} as any), expected)
  assert.equal(wechatMediaDir({ WEFLOW_WECHAT_MEDIA_DIR: 'D:/media' } as any), 'D:/media')
  assert.equal(wechatMediaDir({ WEFLOW_WECHAT_MEDIA_DIR: '  ' } as any), expected,
    '空字符串不算设置过')
})
