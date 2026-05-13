/**
 * WeChat ilink API HTTP client.
 *
 * Communicates with WeChat's bridge service (default https://ilinkai.weixin.qq.com)
 * for QR login, message send/receive, and CDN media upload/download.
 *
 * References:
 *   AstrBot - weixin_oc_client.py / weixin_oc_adapter.py
 */
import crypto from 'crypto'
import fs from 'fs/promises'
import type { WechatOCConfig } from '../types.js'

const DEFAULT_BASE_URL = 'https://ilinkai.weixin.qq.com'
const DEFAULT_CDN_BASE_URL = 'https://novac2c.cdn.weixin.qq.com/c2c'
const DEFAULT_TIMEOUT_MS = 15_000

export class WechatClient {
  private baseUrl: string
  private cdnBaseUrl: string
  private apiTimeoutMs: number
  token?: string

  constructor(config: WechatOCConfig = {}) {
    this.baseUrl = config.baseUrl || DEFAULT_BASE_URL
    this.cdnBaseUrl = config.cdnBaseUrl || DEFAULT_CDN_BASE_URL
    this.apiTimeoutMs = config.apiTimeoutMs || DEFAULT_TIMEOUT_MS
    this.token = config.token
  }

  // ====== Core HTTP ======

  async requestJson(
    method: 'GET' | 'POST',
    endpoint: string,
    options: {
      payload?: Record<string, any>
      params?: Record<string, any>
      tokenRequired?: boolean
      timeoutMs?: number
      extraHeaders?: Record<string, string>
    } = {},
  ): Promise<Record<string, any>> {
    const url = new URL(`${this.baseUrl}/${endpoint}`)
    if (options.params) {
      for (const [k, v] of Object.entries(options.params)) {
        url.searchParams.set(k, String(v))
      }
    }

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      AuthorizationType: 'ilink_bot_token',
      'X-WECHAT-UIN': Buffer.from(String(crypto.randomInt(2 ** 32))).toString('base64'),
      ...options.extraHeaders,
    }

    if (options.tokenRequired && this.token) {
      headers['Authorization'] = `Bearer ${this.token}`
    }

    const controller = new AbortController()
    const timeout = options.timeoutMs || this.apiTimeoutMs
    const timer = setTimeout(() => controller.abort(), timeout)

    try {
      let response: Response
      if (method === 'GET') {
        response = await fetch(url.toString(), { headers, signal: controller.signal })
      } else {
        response = await fetch(url.toString(), {
          method: 'POST',
          headers,
          body: JSON.stringify(options.payload || {}),
          signal: controller.signal,
        })
      }

      if (response.status >= 400) {
        const text = await response.text().catch(() => '')
        throw new Error(`HTTP ${response.status}: ${text.slice(0, 200)}`)
      }

      const text = await response.text()
      if (!text) return {}
      return JSON.parse(text)
    } catch (e: any) {
      if (e.name === 'AbortError') {
        throw new Error(`Request timeout: ${method} ${endpoint}`)
      }
      throw e
    } finally {
      clearTimeout(timer)
    }
  }

  // ====== CDN Media ======

  async uploadToCdn(
    uploadFullUrl: string,
    uploadParam: string,
    fileKey: string,
    aesKeyHex: string,
    fileBuffer: Buffer,
  ): Promise<string> {
    const padded = WechatClient.pkcs7Pad(fileBuffer)
    const keyBuffer = Buffer.from(aesKeyHex, 'hex')
    const cipher = crypto.createCipheriv('aes-128-ecb', keyBuffer, null)
    cipher.setAutoPadding(false)
    const encrypted = Buffer.concat([cipher.update(padded), cipher.final()])

    const url = `${uploadFullUrl}?${uploadParam}`
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), this.apiTimeoutMs)

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: encrypted,
        signal: controller.signal,
      })

      if (response.status !== 200) {
        const text = await response.text().catch(() => '')
        throw new Error(`CDN upload failed: HTTP ${response.status} ${text.slice(0, 200)}`)
      }

      const encryptedParam = response.headers.get('x-encrypted-param')
      if (!encryptedParam) {
        throw new Error('CDN upload response missing x-encrypted-param header')
      }
      return encryptedParam
    } finally {
      clearTimeout(timer)
    }
  }

  async downloadMedia(
    encryptedQueryParam: string,
    aesKeyValue: string,
  ): Promise<Buffer> {
    const url = `${this.cdnBaseUrl}/download?encrypted_query_param=${encodeURIComponent(encryptedQueryParam)}`
    const keyBuffer = WechatClient.parseMediaAesKey(aesKeyValue)

    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), this.apiTimeoutMs)

    try {
      const response = await fetch(url.toString(), { signal: controller.signal })
      if (response.status !== 200) {
        throw new Error(`CDN download failed: HTTP ${response.status}`)
      }
      const encrypted = Buffer.from(await response.arrayBuffer())

      const decipher = crypto.createDecipheriv('aes-128-ecb', keyBuffer, null)
      decipher.setAutoPadding(false)
      const decrypted = Buffer.concat([decipher.update(encrypted), decipher.final()])
      return WechatClient.pkcs7Unpad(decrypted)
    } finally {
      clearTimeout(timer)
    }
  }

  // ====== Static Utils ======

  static pkcs7Pad(data: Buffer, blockSize: number = 16): Buffer {
    const padLen = blockSize - (data.length % blockSize)
    const pad = Buffer.alloc(padLen, padLen)
    return Buffer.concat([data, pad])
  }

  static pkcs7Unpad(data: Buffer, blockSize: number = 16): Buffer {
    if (data.length === 0) return data
    const padLen = data[data.length - 1]
    if (padLen < 1 || padLen > blockSize) return data
    return data.subarray(0, data.length - padLen)
  }

  static parseMediaAesKey(aesKeyStr: string): Buffer {
    // Try base64 first
    try {
      let b64 = aesKeyStr.replace(/-/g, '+').replace(/_/g, '/')
      while (b64.length % 4 !== 0) b64 += '='
      const buf = Buffer.from(b64, 'base64')
      if (buf.length === 16) return buf
    } catch {}

    // Try hex (32 chars = 16 bytes)
    if (aesKeyStr.length === 32 && /^[0-9a-fA-F]+$/.test(aesKeyStr)) {
      return Buffer.from(aesKeyStr, 'hex')
    }

    throw new Error(`Cannot parse AES key: ${aesKeyStr.slice(0, 20)}...`)
  }

  static aesPaddedSize(size: number): number {
    return size + (16 - (size % 16)) % 16
  }

  async close(): Promise<void> {
    // no persistent connection to close with fetch API
  }
}
