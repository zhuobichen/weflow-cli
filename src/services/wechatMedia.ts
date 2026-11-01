/**
 * 官方通道（ilink）**入站媒体**的落地：把 CDN 上的密文拉下来、解密、写到本地磁盘。
 *
 * 为什么值得做：入站图片/语音/文件/视频此前只解析出"有这一项"，`filePath` 是空串，于是助手拿到消息
 * 也不知道图里是什么（那四项的落地当时是显式延后的）。下载与解密的本事本来就有
 * （`WechatClient.downloadMedia`：`<cdn>/download?encrypted_query_param=…` + AES-128-ECB + 去 PKCS#7），
 * 缺的只是把它接上入站这条路。
 *
 * 字段名与优先级照**厂商自己的实现**（`third-party/WeKnora/internal/im/wechat/longpoll.go`）：
 * 图片优先用 `image_item.aeskey`（十六进制字符串，**不走 base64**），退回 `media.aes_key`（base64）；
 * 语音/文件/视频只有 `media.aes_key`。两种编码都由 `WechatClient.parseMediaAesKey` 认。
 *
 * 三条边界写在这里，不指望调用方记得：
 * 1. **它不是默认行为** —— 打开 `wechatMediaDownload` 才发生网络访问；
 * 2. **文件名永远由本地决定** —— 服务端给的名字只取 basename 并过滤字符，`../` 之类必须在这里被吃掉；
 * 3. **超过上限就不写**（`WECHAT_MEDIA_MAX_BYTES`），失败不留半个文件 —— 半个文件比没有更难查。
 */
import { resolveOutputRoot } from '../utils/outputRoot.js'
import { basename, join } from 'path'
import { mkdir, writeFile } from 'fs/promises'

/** 单件上限。够大（手机拍的视频也能过），又不至于一次 CDN 事故把磁盘写满 */
export const WECHAT_MEDIA_MAX_BYTES = 25 * 1024 * 1024

/** 落地目录：默认仓库相对 `output/wechat-media/`（`output/` 已在 .gitignore 里），可用环境变量改（测试用临时目录） */
export function wechatMediaDir(env: NodeJS.ProcessEnv = process.env): string {
  const fromEnv = env.WEFLOW_WECHAT_MEDIA_DIR
  // 默认落在可写输出根（cwd 可能是只读目录，包目录在全局安装时也不可写）
  return fromEnv && fromEnv.trim() ? fromEnv : join(resolveOutputRoot(), 'wechat-media')
}

export type InboundMediaType = 'image' | 'record' | 'file' | 'video'

/** item.type 的数字编码（与服务端 item_list 一致） */
export function mediaTypeOf(itemType: number): InboundMediaType | null {
  if (itemType === 2) return 'image'
  if (itemType === 3) return 'record'
  if (itemType === 4) return 'file'
  if (itemType === 5) return 'video'
  return null
}

/** 服务端给的名字只当作"一个字符串"用：取 basename、去掉控制字符与路径字符、去掉前导点、限长 */
export function sanitizeFileName(name: string): string {
  const base = basename(String(name || '').replace(/\\/g, '/')).trim()
  return base
    .replace(/[\u0000-\u001f<>:"|?*]/g, '_')
    .replace(/^\.+/, '')
    .trim()
    .slice(0, 80)
}

/** 按魔数认扩展名。认不出就返回空串（调用方退回按类型给的后缀），不猜 */
export function extFromMagic(buf: Buffer): string {
  const b = buf
  if (b.length >= 3 && b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return '.jpg'
  if (b.length >= 8 && b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4e && b[3] === 0x47) return '.png'
  if (b.length >= 6 && b.subarray(0, 6).toString('latin1').startsWith('GIF8')) return '.gif'
  if (b.length >= 12 && b.subarray(0, 4).toString('latin1') === 'RIFF'
    && b.subarray(8, 12).toString('latin1') === 'WEBP') return '.webp'
  if (b.length >= 2 && b[0] === 0x42 && b[1] === 0x4d) return '.bmp'
  if (b.length >= 12 && b.subarray(4, 8).toString('latin1') === 'ftyp') return '.mp4'
  if (b.length >= 4 && b[0] === 0x1a && b[1] === 0x45 && b[2] === 0xdf && b[3] === 0xa3) return '.webm'
  if (b.length >= 5 && b.subarray(0, 5).toString('latin1') === '#!AMR') return '.amr'
  if (b.length >= 4 && b.subarray(0, 4).toString('latin1') === 'OggS') return '.ogg'
  if (b.length >= 2 && b[0] === 0x49 && b[1] === 0x44 && b[2] === 0x33) return '.mp3'
  if (b.length >= 12 && b.subarray(0, 4).toString('latin1') === 'RIFF'
    && b.subarray(8, 12).toString('latin1') === 'WAVE') return '.wav'
  return ''
}

/** 该媒体类型在服务端字段树里的节点名 */
function mediaNode(item: any, mediaType: InboundMediaType): any {
  if (mediaType === 'image') return item?.image_item
  if (mediaType === 'record') return item?.voice_item
  if (mediaType === 'file') return item?.file_item
  return item?.video_item
}

/** 下载参数（`encrypt_query_param`）。取不到就没法下载——**这时候不猜、不留空文件** */
export function pickEncryptParam(item: any, mediaType: InboundMediaType): string {
  const node = mediaNode(item, mediaType)
  return String(node?.media?.encrypt_query_param || '')
}

/**
 * AES key 的取值优先级。**图片这一支必须照厂商实现**：`image_item.aeskey` 是十六进制字符串，
 * 比 `media.aes_key`（base64）更靠前；漏了这个优先级，图片会解出一堆乱码却不报错。
 */
export function pickAesKeyValue(item: any, mediaType: InboundMediaType): string {
  const node = mediaNode(item, mediaType)
  if (mediaType === 'image' && node?.aeskey) return String(node.aeskey)
  return String(node?.media?.aes_key || '')
}

export interface SaveMediaOptions {
  item: any
  mediaType: InboundMediaType
  /** 同一条消息里的第几项（一条消息可以带多张图） */
  index: number
  /** 名字前缀：优先用服务端给的 client_id */
  messageId: string
  dir?: string
  /** 注入的下载器（默认由调用方传 `client.downloadMedia`），测试里换成假的 */
  download: (encryptedQueryParam: string, aesKeyValue: string) => Promise<Buffer>
  /** 文件类消息里服务端给的文件名 */
  serverFileName?: string
  maxBytes?: number
}

export interface SaveMediaResult {
  /** 成功是磁盘路径；失败是空串（**绝不返回"看起来成功"的半成品**） */
  path: string
  /** 失败原因，进日志用 */
  reason: string
}

/**
 * 下载一件入站媒体并落盘。失败一律返回 `{ path: '', reason }` 且**不留下文件**：
 * 助手与导出拿到空 path 时知道"没有这玩意"，比拿到一个 0 字节文件强。
 */
export async function saveInboundMedia(opts: SaveMediaOptions): Promise<SaveMediaResult> {
  const { item, mediaType, index, messageId, download } = opts
  const cap = opts.maxBytes ?? WECHAT_MEDIA_MAX_BYTES

  const encryptParam = pickEncryptParam(item, mediaType)
  if (!encryptParam) return { path: '', reason: '服务端没给 encrypt_query_param' }

  const aesKeyValue = pickAesKeyValue(item, mediaType)
  if (!aesKeyValue) return { path: '', reason: '服务端没给 aes key' }

  let bytes: Buffer
  try {
    bytes = await download(encryptParam, aesKeyValue)
  } catch (error: any) {
    return { path: '', reason: `下载或解密失败：${error?.message || error}` }
  }
  if (!bytes || bytes.length === 0) return { path: '', reason: '下载结果为空' }
  if (bytes.length > cap) {
    return { path: '', reason: `超过上限（${bytes.length} > ${cap} 字节）` }
  }

  const byMagic = extFromMagic(bytes)
  const fallbackExt = mediaType === 'image' ? '.jpg' : ''
  const prefix = sanitizeFileName(messageId) || 'msg'
  let name: string
  if (mediaType === 'file' && opts.serverFileName) {
    const safe = sanitizeFileName(opts.serverFileName)
    if (safe) {
      const hasExt = /\.[A-Za-z0-9]{1,8}$/.test(safe)
      name = `${prefix}-${index}-${hasExt || !byMagic ? safe : safe + byMagic}`
    } else {
      name = `${prefix}-${index}${byMagic}`
    }
  } else {
    name = `${prefix}-${index}${byMagic || fallbackExt}`
  }

  const dir = opts.dir || wechatMediaDir()
  const full = join(dir, name)
  try {
    await mkdir(dir, { recursive: true })
    await writeFile(full, bytes)
  } catch (error: any) {
    return { path: '', reason: `写盘失败：${error?.message || error}` }
  }
  return { path: full, reason: '' }
}
