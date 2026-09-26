/**
 * 微信消息收发服务 — 通过 ilink API 桥接登录、收消息、发消息。
 *
 * 协议参考: AstrBot weixin_oc_adapter.py
 */
import crypto from 'crypto'
import path from 'path'
import { WechatClient } from '../core/wechatClient.js'
import { configService } from './configService.js'
import { resolveInboundRouting } from './assistantRouting.js'
import type { WechatOCConfig, WechatLoginSession, WechatInboundMessage, WechatMessageComponent } from '../types.js'

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}

function uuidHex(): string {
  return crypto.randomUUID().replace(/-/g, '')
}

/**
 * 长轮询失败的分类。
 *
 * **`-14` 才是"token 已失效"**——这是厂商实现里写死的（`third-party/WeKnora/internal/im/wechat/longpoll.go:10,151`
 * `if result.ErrCode == -14 { return ErrTokenExpired }`），而本仓原来只查 `-1 || 401`。
 * 查错码的后果**不是报错**，而是把"过期"当成普通错误：每 5 秒重试一次，永远重试下去，
 * 而用户那边看不到任何异常（`channelActive` 只表示"配了 token"，不表示轮询是活的）。
 *
 * 三分类的用意：
 * - `ok`：正常（长轮询空响应会带 0，或干脆没有这两个字段——见下面的调用处注释）；
 * - `token-expired`：`-14` 或 HTTP 401。**重试不可能成功**，所以它的待遇与别的失败不同；
 * - `retryable`：其余（网络抖动、`-1` 这类语义不明的码）。仍然重试，但**退避**，
 *   免得把日志刷成噪音——噪音等于没说。
 */
export function classifyPollResult(
  data: { ret?: number | null; errcode?: number | null } | null | undefined,
  httpStatus?: number,
): 'ok' | 'token-expired' | 'retryable' {
  if (httpStatus === 401) return 'token-expired'
  const errcode = data?.errcode
  if (errcode === -14) return 'token-expired'
  const ret = data?.ret
  const badRet = ret != null && ret !== 0
  const badCode = errcode != null && errcode !== 0
  if (badRet || badCode) return 'retryable'
  return 'ok'
}

/** 重试间隔：1s 起、翻倍、封顶 30s（厂商那边是 1s→30s 指数退避，同一条路数） */
export const POLL_BACKOFF_MS = [1000, 2000, 4000, 8000, 16000, 30000]

export function nextPollDelay(failures: number): number {
  const index = Math.min(Math.max(failures - 1, 0), POLL_BACKOFF_MS.length - 1)
  return POLL_BACKOFF_MS[index]
}

export class WechatMessageService {
  private client: WechatClient
  private config: WechatOCConfig
  private loginSession: WechatLoginSession | null = null
  private shutdownFlag = false
  private contextTokens: Map<string, string> = new Map()
  private syncBuf: string
  private messageCallbacks: Array<(msg: WechatInboundMessage) => void> = []
  /** 服务端说过 token 失效了。**只由长轮询置位**（见 `classifyPollResult`），恢复时清掉 */
  private tokenExpired = false

  constructor(config: WechatOCConfig = {}) {
    this.config = config
    this.syncBuf = config.syncBuf || ''
    this.client = new WechatClient({
      baseUrl: config.baseUrl,
      cdnBaseUrl: config.cdnBaseUrl,
      apiTimeoutMs: config.apiTimeoutMs,
      token: config.token,
    })
    // 加载持久化的 context_token, 让一次性 send 命令也能复用历史 token
    if (config.contextTokens && typeof config.contextTokens === 'object') {
      for (const [wxid, token] of Object.entries(config.contextTokens)) {
        if (wxid && token) this.contextTokens.set(wxid, token)
      }
    } else {
      // 未显式传入时, 从 configService 读取 (覆盖常见的一次性 send 场景)
      const persisted = configService.getContextTokens()
      for (const [wxid, token] of Object.entries(persisted)) {
        if (wxid && token) this.contextTokens.set(wxid, token)
      }
    }
  }

  // ====== Login ======

  async startLogin(): Promise<{ qrcodeUrl: string; qrcodeContent: string }> {
    const data = await this.client.requestJson('GET', 'ilink/bot/get_bot_qrcode', {
      params: { bot_type: this.config.botType || '3' },
    })

    const qrcode = data.qrcode as string
    const qrcodeImgContent = data.qrcode_img_content as string

    this.loginSession = {
      sessionKey: uuidHex(),
      qrcode,
      qrcodeImgContent,
      startedAt: Date.now(),
      status: 'wait',
    }

    return { qrcodeUrl: qrcode, qrcodeContent: qrcodeImgContent }
  }

  async pollQrStatus(): Promise<WechatLoginSession> {
    if (!this.loginSession) {
      throw new Error('未开始登录流程，请先调用 startLogin()')
    }

    const data = await this.client.requestJson('GET', 'ilink/bot/get_qrcode_status', {
      params: { qrcode: this.loginSession.qrcode },
      extraHeaders: { 'iLink-App-ClientVersion': '1' },
    })

    const status = data.status as string
    this.loginSession.status = status as WechatLoginSession['status']

    if (status === 'confirmed') {
      this.loginSession.botToken = data.bot_token as string
      this.loginSession.accountId = data.ilink_bot_id as string
      this.loginSession.baseUrl = data.baseurl as string
      this.loginSession.userId = data.ilink_user_id as string

      // Persist token
      if (this.loginSession.botToken) {
        this.client.token = this.loginSession.botToken
        configService.set('wechatOcToken', this.loginSession.botToken)
      }
      if (this.loginSession.accountId) {
        configService.set('wechatOcAccountId', this.loginSession.accountId)
      }
    } else if (status === 'expired') {
      // qrcode expired — caller should retry startLogin()
    }

    return this.loginSession
  }

  async waitForLogin(pollIntervalMs = 2000): Promise<WechatLoginSession> {
    let expiredCount = 0

    while (true) {
      try {
        const session = await this.pollQrStatus()

        if (session.status === 'confirmed') {
          return session
        }
        if (session.status === 'expired') {
          expiredCount++
          if (expiredCount >= 3) {
            session.error = '二维码过期次数过多，请重新运行登录命令'
            return session
          }
          await this.startLogin()
          console.log('二维码已过期，已获取新二维码，请重新扫码')
        }
      } catch (e: any) {
        // Timeout or network error — retry after interval
        if (e.message?.includes('timeout')) {
          // Expected: QR status poll times out, keep retrying
        } else {
          console.error(`轮询异常: ${e.message}`)
        }
      }

      await sleep(pollIntervalMs)
    }
  }

  // ====== Message Polling ======

  async startPolling(): Promise<void> {
    let failures = 0
    let expiryReported = false
    while (!this.shutdownFlag) {
      try {
        const data = await this.client.requestJson('POST', 'ilink/bot/getupdates', {
          payload: {
            base_info: { channel_version: 'astrbot' },
            get_updates_buf: this.syncBuf,
          },
          tokenRequired: true,
          timeoutMs: 40_000, // Long-poll: server will hold until message arrives
        })

        // ret/errcode may be absent on empty response (normal for long-poll timeout)
        const kind = classifyPollResult(data)
        if (kind !== 'ok') {
          failures += 1
          const detail = `ret=${data.ret} errcode=${data.errcode} errmsg=${data.errmsg || 'unknown'}`
          if (kind === 'token-expired') {
            this.tokenExpired = true
            // **只在状态翻过去时喊一次**：每 5 秒刷一行会被当成噪音，而噪音等于没说
            if (!expiryReported) {
              expiryReported = true
              console.error(`微信通道的 token 已失效（${detail}）——重试不会成功。`
                + '重新登录：weflow-cli login-wechat，之后重启助手（weflow-cli assistant stop/start）'
                + '；在此之前本机面板那条入口照常可用。')
            }
          } else {
            console.error(`getupdates error: ${detail}`)
          }
          await sleep(nextPollDelay(failures))
          continue
        }

        // 这一轮是通的：清掉失败计数与"说过一次"的标记（恢复了就该重新能喊）
        failures = 0
        expiryReported = false
        this.tokenExpired = false

        if (data.get_updates_buf) {
          this.syncBuf = data.get_updates_buf
          configService.set('wechatOcSyncBuf', this.syncBuf as any)
        }

        const msgs: any[] = data.msgs || []
        for (const msg of msgs) {
          if (this.shutdownFlag) return
          const inbound = this.parseInboundMessage(msg)
          if (inbound) {
            for (const cb of this.messageCallbacks) {
              try { cb(inbound) } catch (error: any) {
                // **不许吞**：这里进的是助手处理消息的入口。吞掉的话，用户发了消息、
                // 助手什么都没发生、日志里一个字都没有——三种证据全无，最难查的一种坏法。
                console.error(`消息回调异常（这条消息没有被处理）：${error?.message || error}`)
              }
            }
          }
        }
      } catch (e: any) {
        if (this.shutdownFlag) return
        // Long-poll timeout is normal — server holds connection until message arrives
        if (e.name === 'AbortError' || e.message?.includes('timeout')) {
          // Restart poll immediately
          continue
        }
        failures += 1
        console.error(`Polling error: ${e.message}`)
        await sleep(nextPollDelay(failures))
      }
    }
  }

  /** token 是否已被服务端判为失效（面板/状态用它来说实话，见 `isChannelActive`） */
  isTokenExpired(): boolean {
    return this.tokenExpired
  }

  async stop(): Promise<void> {
    this.shutdownFlag = true
  }

  // ====== Send ======

  async sendText(userId: string, text: string): Promise<boolean> {
    if (!this.client.token) return false

    const contextToken = this.contextTokens.get(userId)
    if (!contextToken) return false

    const result = await this.client.requestJson('POST', 'ilink/bot/sendmessage', {
      payload: {
        base_info: { channel_version: 'astrbot' },
        msg: {
          from_user_id: '',
          to_user_id: userId,
          client_id: uuidHex(),
          message_type: 2,
          message_state: 2,
          context_token: contextToken,
          item_list: [{ type: 1, text_item: { text } }],
        },
      },
      tokenRequired: true,
    })

    // 服务器成功时返回 message_id (不带 ret/errcode); 失败时返回非零 ret/errcode
    const failed = (result.ret != null && result.ret !== 0) ||
                   (result.errcode != null && result.errcode !== 0)
    return !failed
  }

  async sendMedia(
    userId: string,
    filePath: string,
    mediaType: 'image' | 'video' | 'file',
  ): Promise<boolean> {
    if (!this.client.token) return false

    const contextToken = this.contextTokens.get(userId)
    if (!contextToken) return false

    const fs = await import('fs/promises')
    const fileBuffer = await fs.readFile(filePath)
    const fileName = path.basename(filePath)
    const rawMD5 = crypto.createHash('md5').update(fileBuffer).digest('hex')
    const rawSize = fileBuffer.length

    const fileKey = uuidHex()
    const aesKeyHex = crypto.randomBytes(16).toString('hex')
    const cipherSize = WechatClient.aesPaddedSize(rawSize)

    const typeMap = {
      image: { uploadMediaType: 1, itemType: 2 },
      video: { uploadMediaType: 2, itemType: 5 },
      file: { uploadMediaType: 3, itemType: 4 },
    }
    const { uploadMediaType, itemType } = typeMap[mediaType]

    // Get upload URL
    const uploadResult = await this.client.requestJson('POST', 'ilink/bot/getuploadurl', {
      payload: {
        filekey: fileKey,
        media_type: uploadMediaType,
        to_user_id: userId,
        rawsize: rawSize,
        rawfilemd5: rawMD5,
        filesize: cipherSize,
        aeskey: aesKeyHex,
        no_need_thumb: true,
        base_info: { channel_version: 'astrbot' },
      },
      tokenRequired: true,
    })

    if (uploadResult.ret !== 0 || uploadResult.errcode !== 0) {
      console.error(`getuploadurl error: ${uploadResult.errmsg}`)
      return false
    }

    // Upload to CDN
    const encryptedParam = await this.client.uploadToCdn(
      uploadResult.upload_full_url as string,
      uploadResult.upload_param as string,
      fileKey,
      aesKeyHex,
      fileBuffer,
    )

    // Build media item and send
    const mediaPayload = {
      encrypt_query_param: encryptedParam,
      aes_key: Buffer.from(aesKeyHex, 'hex').toString('base64'),
      encrypt_type: 1,
    }

    let itemList: any[]
    if (mediaType === 'image') {
      itemList = [{ type: 2, image_item: { media: mediaPayload, mid_size: rawSize, aeskey: aesKeyHex } }]
    } else if (mediaType === 'file') {
      itemList = [{ type: 4, file_item: { media: mediaPayload, file_name: fileName, len: rawSize } }]
    } else {
      itemList = [{ type: 5, video_item: { media: mediaPayload, video_size: rawSize } }]
    }

    const result = await this.client.requestJson('POST', 'ilink/bot/sendmessage', {
      payload: {
        base_info: { channel_version: 'astrbot' },
        msg: {
          from_user_id: '',
          to_user_id: userId,
          client_id: uuidHex(),
          message_type: 2,
          message_state: 2,
          context_token: contextToken,
          item_list: itemList,
        },
      },
      tokenRequired: true,
    })

    const failed = (result.ret != null && result.ret !== 0) ||
                   (result.errcode != null && result.errcode !== 0)
    return !failed
  }

  async sendImage(userId: string, imagePath: string): Promise<boolean> {
    return this.sendMedia(userId, imagePath, 'image')
  }

  async sendFile(userId: string, filePath: string, _fileName?: string): Promise<boolean> {
    return this.sendMedia(userId, filePath, 'file')
  }

  // ====== Callbacks ======

  onMessage(callback: (msg: WechatInboundMessage) => void): void {
    this.messageCallbacks.push(callback)
  }

  // ====== Status ======

  isLoggedIn(): boolean {
    return !!(this.client.token || this.config.token)
  }

  getAccountId(): string | null {
    return this.config.accountId || null
  }

  // ====== Private ======

  private parseInboundMessage(msg: any): WechatInboundMessage | null {
    const fromUserId: string = msg.from_user_id || ''
    if (!fromUserId) return null
    const routing = resolveInboundRouting(msg, String(configService.get('wechatOcAccountId') || ''))

    // Store context token for future replies
    if (msg.context_token) {
      this.contextTokens.set(routing.conversationId, msg.context_token)
      // 持久化, 让后续一次性 send 命令也能复用
      try { configService.upsertContextToken(routing.conversationId, msg.context_token) } catch {}
    }

    const components: WechatMessageComponent[] = []
    const itemList: any[] = msg.item_list || []

    for (const item of itemList) {
      const itemType = item.type as number
      if (itemType === 1 && item.text_item) {
        components.push({ type: 'plain', text: item.text_item.text || '' })
      } else if (itemType === 2 && item.image_item) {
        components.push({ type: 'image', filePath: '' }) // image download deferred
      } else if (itemType === 3 && item.voice_item) {
        components.push({ type: 'record', filePath: '' })
      } else if (itemType === 4 && item.file_item) {
        components.push({ type: 'file', name: item.file_item.file_name || '', filePath: '' })
      } else if (itemType === 5 && item.video_item) {
        components.push({ type: 'video', filePath: '' })
      }
    }

    // Determine message kind
    let messageKind: WechatInboundMessage['messageKind'] = 'unknown'
    if (itemList.length === 1) {
      const t = itemList[0].type
      if (t === 1) messageKind = 'text'
      else if (t === 2) messageKind = 'image'
      else if (t === 3) messageKind = 'voice'
      else if (t === 4) messageKind = 'file'
      else if (t === 5) messageKind = 'video'
    }

    const textComponents = components.filter(c => c.type === 'plain')
    const messageStr = textComponents.map(c => (c as { type: 'plain'; text: string }).text).join('')

    return {
      messageId: msg.client_id || uuidHex(),
      fromUserId,
      ...routing,
      senderNickname: msg.from_user_id || '',
      timestamp: Math.floor(Date.now() / 1000),
      timestampMs: Date.now(),
      components,
      messageStr,
      messageKind,
      rawMessage: msg,
      isReply: !!(msg.ref_msg),
      quotedText: msg.ref_msg?.text || undefined,
    }
  }
}
