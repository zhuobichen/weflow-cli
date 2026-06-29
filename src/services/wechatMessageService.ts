/**
 * 微信消息收发服务 — 通过 ilink API 桥接登录、收消息、发消息。
 *
 * 协议参考: AstrBot weixin_oc_adapter.py
 */
import crypto from 'crypto'
import path from 'path'
import { WechatClient } from '../core/wechatClient.js'
import { configService } from './configService.js'
import type { WechatOCConfig, WechatLoginSession, WechatInboundMessage, WechatMessageComponent } from '../types.js'

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}

function uuidHex(): string {
  return crypto.randomUUID().replace(/-/g, '')
}

export class WechatMessageService {
  private client: WechatClient
  private config: WechatOCConfig
  private loginSession: WechatLoginSession | null = null
  private shutdownFlag = false
  private contextTokens: Map<string, string> = new Map()
  private syncBuf: string
  private messageCallbacks: Array<(msg: WechatInboundMessage) => void> = []

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
        if (data.ret != null && data.ret !== 0 || data.errcode != null && data.errcode !== 0) {
          console.error(`getupdates error: ret=${data.ret} errcode=${data.errcode} errmsg=${data.errmsg || 'unknown'}`)
          if (data.errcode === -1 || data.errcode === 401) {
            console.error('Token may have expired, please login again')
          }
          await sleep(5000)
          continue
        }

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
              try { cb(inbound) } catch {}
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
        console.error(`Polling error: ${e.message}`)
        await sleep(5000)
      }
    }
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

    return result.ret === 0 && result.errcode === 0
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

    return result.ret === 0 && result.errcode === 0
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

    // Store context token for future replies
    if (msg.context_token) {
      this.contextTokens.set(fromUserId, msg.context_token)
      // 持久化, 让后续一次性 send 命令也能复用
      try { configService.upsertContextToken(fromUserId, msg.context_token) } catch {}
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
