# weflow-cli 微信消息收发 + 白名单 实施计划

> 本文档为可执行计划，供 AI 逐阶段实施。每个阶段有明确文件路径、接口签名和验证方式。

---

## 目标

让 weflow-cli（TypeScript/Node.js CLI工具）具备以下能力：
1. **微信扫码登录** — 通过外部桥接服务获取个人微信的收发权限
2. **发送消息** — 发文本、图片、文件给指定联系人
3. **接收消息** — 实时监听收到的微信消息
4. **白名单控制** — 只在白名单中的人才能收发消息

---

## 项目当前状态

- **语言**：TypeScript (ES Module)，Node.js 18+
- **入口**：`bin/weflow-cli.ts`（commander + inquirer）
- **源码目录**：
  - `src/core/` — 核心模块（dbPathService, keyService, ntCore, sqlcipherCore, wcdbCore）
  - `src/services/` — 业务服务（configService, chatService, exportService）
  - `src/types.ts` — 类型定义
- **配置存储**：`~/.weflow-cli/config.json`，敏感字段使用 `lock:` 前缀 AES-256-GCM 加密
- **运行命令**：`npx tsx bin/weflow-cli.ts <command>`
- **无测试框架**，验证靠手动运行命令

---

## 阶段 1 — 新建 `src/core/wechatClient.ts`

### 目的
封装与微信桥接服务（ilink API）的 HTTP 通信，包括 REST 调用和媒体 CDN 上传/下载。

### 协议说明
微信桥接服务（默认 `https://ilinkai.weixin.qq.com`）提供以下端点：

| 方法 | 端点 | 说明 | 需 Token |
|------|------|------|----------|
| GET | `ilink/bot/get_bot_qrcode` | 获取登录二维码 | 否 |
| GET | `ilink/bot/get_qrcode_status` | 轮询扫码状态 | 否 |
| POST | `ilink/bot/getupdates` | 长轮询收消息 | 是 |
| POST | `ilink/bot/sendmessage` | 发送消息 | 是 |
| POST | `ilink/bot/getuploadurl` | 获取媒体上传凭证 | 是 |
| POST | `ilink/bot/getconfig` | 获取 typing 配置 | 是 |
| POST | `ilink/bot/sendtyping` | 发送"正在输入"状态 | 是 |

**请求头固定要求**：
```
Content-Type: application/json
AuthorizationType: ilink_bot_token
X-WECHAT-UIN: <32位随机数的 base64>
Authorization: Bearer <token>   # token_required 为 true 时
```

### 接口定义

```typescript
// src/core/wechatClient.ts

class WechatClient {
  constructor(config: {
    baseUrl: string          // 默认 'https://ilinkai.weixin.qq.com'
    cdnBaseUrl: string       // 默认 'https://novac2c.cdn.weixin.qq.com/c2c'
    apiTimeoutMs: number     // 默认 15000
    token?: string           // 登录后获得的 bot_token
  })

  // === 核心 HTTP 方法 ===
  async requestJson(
    method: 'GET' | 'POST',
    endpoint: string,
    options?: {
      payload?: Record<string, any>
      params?: Record<string, any>
      tokenRequired?: boolean
      timeoutMs?: number
      extraHeaders?: Record<string, string>
    }
  ): Promise<Record<string, any>>

  // === CDN 媒体操作 ===
  async uploadToCdn(
    uploadFullUrl: string,
    uploadParam: string,
    fileKey: string,
    aesKeyHex: string,       // 32位 hex 字符串
    fileBuffer: Buffer
  ): Promise<string>         // 返回 x-encrypted-param（下载凭证）

  async downloadMedia(
    encryptedQueryParam: string,
    aesKeyValue: string      // base64 或 hex 格式
  ): Promise<Buffer>

  // === Utils ===
  static pkcs7Pad(data: Buffer, blockSize: number = 16): Buffer
  static pkcs7Unpad(data: Buffer, blockSize: number = 16): Buffer
  static parseMediaAesKey(aesKeyStr: string): Buffer  // 返回 16 字节 key
  static aesPaddedSize(size: number): number

  async close(): Promise<void>
}
```

### 实现要点

**1. requestJson 方法**
- 使用 Node.js 内置 `fetch`（Node 18+）
- 构建请求头：`Content-Type`, `AuthorizationType`, `X-WECHAT-UIN`
- `X-WECHAT-UIN` 生成：`Buffer.from(String(crypto.randomInt(2 ** 32))).toString('base64')`
- Token 认证时添加 `Authorization: Bearer <token>`
- URL 拼接：`${baseUrl}/${endpoint}`
- 处理 GET（params 拼接到 URL）和 POST（body 为 JSON）
- 超时使用 `AbortController`
- 响应状态码 >= 400 时抛异常
- 空响应体返回 `{}`

**2. uploadToCdn 方法**
- 读取文件为 Buffer，计算 MD5
- 生成 `fileKey = crypto.randomUUID().replace(/-/g, '')`
- 生成 `aesKeyHex = crypto.randomBytes(16).toString('hex')`（如果未提供）
- 对文件内容做 PKCS7 padding
- 使用 `crypto.createCipheriv('aes-128-ecb', keyBuffer, null)` 加密
  - 关键：`cipher.setAutoPadding(false)` 因为已手动 PKCS7 pad
- POST 加密内容到 CDN URL（Content-Type: application/octet-stream）
- 从响应头 `x-encrypted-param` 获取下载凭证
- 状态码非 200 时抛异常

**3. downloadMedia 方法**
- GET CDN download URL（`${cdnBaseUrl}/download?encrypted_query_param=<param>`）
- 解析 AES key（`parseMediaAesKey`）
- AES-ECB 解密后用 `pkcs7Unpad` 去填充
- 返回 Buffer

**4. parseMediaAesKey 方法**
- 输入可能是 base64 或 hex
- 先尝试 base64 decode（补 = padding）
- 如果得到 16 字节直接返回
- 如果是 32 字节且全为 hex 字符，`Buffer.from(str, 'hex')` 返回
- 否则抛异常

### 验证
在 `wechatClient.ts` 底部添加测试代码（`if (require.main === module)`），构造 client 实例调用 requestJson 测试连通性。

---

## 阶段 2 — 在 `src/types.ts` 中新增类型

在现有类型定义后追加：

```typescript
// === 微信消息相关 ===

interface WechatOCConfig {
  baseUrl?: string
  cdnBaseUrl?: string
  botType?: string
  token?: string
  accountId?: string
  syncBuf?: string
  contextTokens?: Record<string, string>
  longPollTimeoutMs?: number
  apiTimeoutMs?: number
  typingKeepaliveIntervalS?: number
  typingTicketTtlS?: number
}

interface WechatLoginSession {
  sessionKey: string
  qrcode: string
  qrcodeImgContent: string
  startedAt: number
  status: 'wait' | 'confirmed' | 'expired' | 'scanned'
  botToken?: string
  accountId?: string
  baseUrl?: string
  userId?: string
  error?: string
}

interface WechatInboundMessage {
  messageId: string
  fromUserId: string
  senderNickname: string
  timestamp: number
  timestampMs: number
  components: WechatMessageComponent[]
  messageStr: string
  messageKind: 'text' | 'image' | 'voice' | 'file' | 'video' | 'unknown'
  rawMessage: Record<string, any>
  isReply: boolean
  quotedText?: string
}

type WechatMessageComponent =
  | { type: 'plain'; text: string }
  | { type: 'image'; filePath: string }
  | { type: 'record'; filePath: string }
  | { type: 'file'; name: string; filePath: string }
  | { type: 'video'; filePath: string }
```

同步修改 `src/types.ts` 的 export，确保这些类型可在其他模块中引用。

---

## 阶段 3 — 新建 `src/services/wechatMessageService.ts`

### 目的
消息收发核心服务，管理登录状态、消息轮询、消息发送。

### 接口定义

```typescript
// src/services/wechatMessageService.ts

import { WechatClient } from '../core/wechatClient.js'
import type { WechatOCConfig, WechatLoginSession, WechatInboundMessage } from '../types.js'

class WechatMessageService {
  private client: WechatClient
  private config: WechatOCConfig
  private loginSession: WechatLoginSession | null = null
  private shutdownFlag = false
  private contextTokens: Map<string, string> = new Map()
  private syncBuf = ''
  private qrExpiredCount = 0
  private messageCallbacks: Array<(msg: WechatInboundMessage) => void> = []

  constructor(config: WechatOCConfig)

  // === 登录 ===
  async startLogin(): Promise<{ qrcodeUrl: string; qrcodeContent: string }>
  async pollQrStatus(): Promise<WechatLoginSession>
  async waitForLogin(): Promise<WechatLoginSession>  // 循环 pollQrStatus 直到成功

  // === 消息轮询 ===
  async startPolling(): Promise<void>    // 阻塞式循环 long-poll
  async stop(): Promise<void>

  // === 发送消息 ===
  async sendText(userId: string, text: string): Promise<boolean>
  async sendImage(userId: string, imagePath: string): Promise<boolean>
  async sendFile(userId: string, filePath: string, fileName?: string): Promise<boolean>

  // === 事件回调 ===
  onMessage(callback: (msg: WechatInboundMessage) => void): void

  // === 状态查询 ===
  isLoggedIn(): boolean
  getAccountId(): string | null
}
```

### 实现要点

**1. startLogin()**
调用 `client.requestJson('GET', 'ilink/bot/get_bot_qrcode', { params: { bot_type: config.botType || '3' } })`，返回 `qrcode` 和 `qrcode_img_content`。创建 `WechatLoginSession` 对象保存状态。

> 注意：不要在此方法中生成终端二维码图片，那是在 CLI 层做的事。

**2. pollQrStatus() / waitForLogin()**
- pollQrStatus：`GET ilink/bot/get_qrcode_status` 带 `params: { qrcode }` 和 header `iLink-App-ClientVersion: 1`
- 根据返回的 status：
  - `wait` → 继续等待
  - `scanned` → 已扫码，继续等待
  - `confirmed` → 登录成功，提取 `bot_token`, `ilink_bot_id`(accountId), `baseurl`, `ilink_user_id`，更新 client.token 和自身状态，**将 token 保存到 configService**
  - `expired` → 如果过期次数 < 3，重新调用 startLogin；否则设 error
- waitForLogin 循环调用 pollQrStatus，每次间隔 1-2 秒，直到 confirmed 或 error

**3. startPolling()**
```typescript
async startPolling(): Promise<void> {
  while (!this.shutdownFlag) {
    const data = await this.client.requestJson('POST', 'ilink/bot/getupdates', {
      payload: {
        base_info: { channel_version: 'astrbot' },
        get_updates_buf: this.syncBuf,
      },
      tokenRequired: true,
      timeoutMs: 35000, // 长轮询超时
    })

    // 检查 ret/errcode
    if (data.ret !== 0 || data.errcode !== 0) {
      console.error('getupdates error:', data.errmsg)
      await sleep(5000)
      continue
    }

    // 更新 sync_buf
    if (data.get_updates_buf) {
      this.syncBuf = data.get_updates_buf
      // 持久化 syncBuf 到 config
    }

    // 处理消息
    const msgs = data.msgs || []
    for (const msg of msgs) {
      if (this.shutdownFlag) return
      const inboundMsg = this.parseInboundMessage(msg)
      if (inboundMsg) {
        for (const cb of this.messageCallbacks) {
          cb(inboundMsg)
        }
      }
    }
  }
}
```

**4. parseInboundMessage(msg)**
参考 AstrBot `_handle_inbound_message` 方法：
- 提取 `from_user_id`（空则跳过）
- 提取 `context_token`（更新内部 Map，用于后续 send）
- 遍历 `item_list`：
  - type=1 → 文本（`Plain(text)`）
  - type=2 → 图片（下载解密保存到临时文件 → `Image(filePath)`）
  - type=3 → 语音
  - type=4 → 文件
  - type=5 → 视频
  - 处理 `ref_msg` 引用消息
- 构建 `WechatInboundMessage` 对象

**5. sendText(userId, text)**
```typescript
async sendText(userId: string, text: string): Promise<boolean> {
  if (!this.client['token']) return false
  const contextToken = this.contextTokens.get(userId)
  if (!contextToken) return false  // 需要先收到该用户一条消息

  const result = await this.client.requestJson('POST', 'ilink/bot/sendmessage', {
    payload: {
      base_info: { channel_version: 'astrbot' },
      msg: {
        from_user_id: '',
        to_user_id: userId,
        client_id: crypto.randomUUID().replace(/-/g, ''),
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
```

**6. sendImage / sendFile**
参考 AstrBot `_send_media_segment` 方法：
1. 读取文件内容，计算 MD5 和大小
2. 生成 `fileKey`（uuid hex）、`aesKeyHex`（uuid bytes hex）
3. 调用 `ilink/bot/getuploadurl` 获取上传凭证和 URL
   - payload: `{ filekey, media_type, to_user_id, rawsize, rawfilemd5, filesize(cipherTextSize), aeskey, no_need_thumb: true, base_info }`
4. 调用 `client.uploadToCdn()` 上传加密文件，获得 `encrypt_query_param`
5. 构建 media item 并调用 `sendmessage`

media_type 映射：
- 图片：upload_media_type=1, item_type=2
- 视频：upload_media_type=2, item_type=5
- 文件：upload_media_type=3, item_type=4

### 依赖
- `crypto` (Node.js 内置)
- `path` (Node.js 内置)
- `fs/promises` (Node.js 内置)
- `WechatClient` (阶段 1)

---

## 阶段 4 — 新建 `src/services/whitelistService.ts`

### 目的
管理消息收发的白名单，仅在白名单中的人才能收发消息。

### 接口定义

```typescript
// src/services/whitelistService.ts

class WhitelistService {
  constructor()

  /** 获取完整白名单列表 */
  getList(): string[]

  /** 检查 wxid 是否在白名单中 */
  isAllowed(wxid: string): boolean

  /**
   * 添加 target 到白名单
   * target 可以是 wxid / 昵称 / 备注名 / 序号 [N]
   * 非 wxid 格式的 target 会通过 chatService 解析为 wxid
   */
  async add(target: string): Promise<{ success: boolean; error?: string; wxid?: string }>

  /** 移除 wxid */
  remove(wxid: string): boolean

  /** 清空白名单 */
  clear(): void

  /** 重新加载（配置变更后调用） */
  reload(): void
}
```

### 实现要点

**1. 存储**

白名单存储在 `~/.weflow-cli/config.json` 的 `whitelist` 字段，类型 `string[]`（wxid 数组），**明文存储**。

在 `configService` 中新增方法：
```typescript
getWhitelist(): string[]
setWhitelist(list: string[]): void
```

**2. add 方法**
```typescript
async add(target: string): Promise<{...}> {
  // 如果已经是 wxid 格式，直接存入
  if (target.startsWith('wxid_') || target.includes('@chatroom')) {
    const list = configService.getWhitelist()
    if (list.includes(target)) {
      return { success: false, error: '已在白名单中' }
    }
    list.push(target)
    configService.setWhitelist(list)
    return { success: true, wxid: target }
  }

  // 否则通过 chatService 解析（支持昵称/备注名/序号）
  try {
    const wxid = await this.resolveToWxid(target)
    const list = configService.getWhitelist()
    if (list.includes(wxid)) {
      return { success: false, error: '已在白名单中' }
    }
    list.push(wxid)
    configService.setWhitelist(list)
    return { success: true, wxid }
  } catch (e) {
    return { success: false, error: `无法解析: ${target}` }
  }
}
```

`resolveToWxid` 复用 `bin/weflow-cli.ts` 中已有的 `resolveTalker` 函数逻辑。建议将该函数提取到 `src/utils/talkerUtils.ts` 中以便复用。

**3. isAllowed 方法**
简单的数组 includes 检查。
```typescript
isAllowed(wxid: string): boolean {
  return configService.getWhitelist().includes(wxid)
}
```

### 工具函数提取（可选）

将 `bin/weflow-cli.ts` 中的 `resolveTalker` 函数提取到 `src/utils/talkerUtils.ts`：
```typescript
// src/utils/talkerUtils.ts
export async function resolveTalker(input: string): Promise<string>
```
这样 `whitelistService` 和 CLI 命令都能复用。

---

## 阶段 5 — 修改 `src/services/configService.ts`

新增配置字段支持。在 `CliConfig` 接口中添加：

```typescript
interface CliConfig {
  // ... 现有字段保持不变 ...

  // 微信消息通道
  wechatOcToken: string
  wechatOcAccountId: string
  wechatOcBaseUrl: string
  wechatOcSyncBuf: string

  // 白名单
  whitelist: string[]
}
```

**关键实现细节**：
- `wechatOcToken` 敏感字段 — 使用现有 `lockEncrypt/decrypt` 加密存储（`lock:` 前缀）
- `wechatOcAccountId`, `wechatOcBaseUrl`, `wechatOcSyncBuf` — 普通明文存储
- `whitelist` — 明文存储（`string[]`）
- 在 `load()` 方法中初始化新字段默认值（空字符串 / 空数组）
- 在 `save()` 方法中序列化新字段
- 新增便捷方法：`getWhitelist(): string[]`, `setWhitelist(list: string[]): void`

---

## 阶段 6 — 修改 `bin/weflow-cli.ts` 添加命令

### 新增命令

所有新命令在现有 `program` 实例上注册。

#### `login-wechat`
```bash
weflow-cli login-wechat [--base-url <url>]
```
实现逻辑：
1. 从 config 读取 wechat OC 配置
2. 创建 `WechatMessageService` 实例
3. 调用 `startLogin()` 获取二维码
4. 使用 `qrcode-terminal` 在终端打印二维码
5. 调用 `waitForLogin()` 等待扫码确认
6. 登录成功后打印 account_id 并提示 "登录成功"
7. 保存 token 到 config（`configService.set('wechatOcToken', token)`）

#### `whitelist`
```bash
weflow-cli whitelist                     # 显示完整白名单
weflow-cli whitelist add <target>        # 添加（支持 wxid/昵称/备注名/序号）
weflow-cli whitelist rm <wxid>           # 移除
weflow-cli whitelist clear               # 清空
```
实现逻辑：
- `whitelist`：打印 `whitelistService.getList()` 格式化为表格
- `whitelist add`：调用 `whitelistService.add(target)`，打印结果
- `whitelist rm`：调用 `whitelistService.remove(wxid)`，打印结果
- `whitelist clear`：确认后调用 `whitelistService.clear()`

#### `send`
```bash
weflow-cli send <target> <message>          # 发文本
weflow-cli send <target> --image <path>     # 发图片
weflow-cli send <target> --file <path>      # 发文件
```
实现逻辑：
1. **白名单检查**：`whitelistService.isAllowed(resolvedWxid)` — 不在白名单则打印 `❌ 目标不在白名单中，拒绝发送` 并 return
2. 解析 target 为 wxid（复用 resolveTalker 逻辑）
3. 检查登录状态（`wechatMessageService.isLoggedIn()`）— 未登录则提示先运行 `login-wechat`
4. 调用对应的 send 方法
5. 打印成功/失败

#### `listen`
```bash
weflow-cli listen [--target <wxid>]
```
实现逻辑：
1. 检查登录状态
2. 注册消息回调：
   - **白名单检查**：`whitelistService.isAllowed(msg.fromUserId)` — 不在白名单则静默跳过
   - 格式化打印消息（时间、发送者、内容）
   - 如果设置了 `--target`，只打印该用户的消息
3. 调用 `startPolling()`（阻塞）
4. Ctrl+C 时调用 `stop()` 优雅退出

### 依赖
```bash
npm install qrcode-terminal
npm install --save-dev @types/qrcode-terminal  # 如果有的话
```

---

## 阶段 7 — 最终验证

按顺序执行以下命令，确认所有功能正常：

```bash
# 1. 白名单管理
npx tsx bin/weflow-cli.ts whitelist add <你的另一个微信号>
npx tsx bin/weflow-cli.ts whitelist
# 预期：显示白名单列表，包含刚添加的 wxid

# 2. 白名单拦截（未添加的目标）
npx tsx bin/weflow-cli.ts send wxid_notexist "hello"
# 预期：❌ 目标不在白名单中，拒绝发送

# 3. 扫码登录
npx tsx bin/weflow-cli.ts login-wechat
# 预期：终端显示二维码，微信扫码后打印 "登录成功"

# 4. 发送消息
npx tsx bin/weflow-cli.ts send <白名单wxid> "来自 weflow-cli 的测试消息"
# 预期：目标微信收到消息

# 5. 接收消息
npx tsx bin/weflow-cli.ts listen
# 在另一设备用白名单微信向登录账号发消息
# 预期：终端实时打印收到的消息
# 非白名单微信发消息 → 预期：无任何输出（被过滤）
```

---

## 附录 A：关键协议参考（来自 AstrBot 源码）

**源文件位置**（供参考，不需要修改）：
- `E:/CodeProject/AstrBot/astrbot/core/platform/sources/weixin_oc/weixin_oc_client.py` — HTTP 客户端
- `E:/CodeProject/AstrBot/astrbot/core/platform/sources/weixin_oc/weixin_oc_adapter.py` — 消息收发逻辑

**消息 item 类型映射**：
| item_type | 含义 | item key |
|-----------|------|----------|
| 1 | 文本 | `text_item: { text }` |
| 2 | 图片 | `image_item: { media, mid_size, aeskey }` |
| 3 | 语音 | `voice_item: { media, voice_size, aeskey }` |
| 4 | 文件 | `file_item: { media, file_name, len }` |
| 5 | 视频 | `video_item: { media, video_size }` |

**media payload 结构**：
```json
{
  "encrypt_query_param": "<CDN下载凭证>",
  "aes_key": "<base64编码的AES密钥>",
  "encrypt_type": 1
}
```

**API 响应通用格式**：
```json
{ "ret": 0, "errcode": 0, "errmsg": "" }
```
成功条件：`ret === 0 && errcode === 0`

---

## 附录 B：不需要做的事

- ❌ 不要引入 Web 框架（weflow-cli 只需 HTTP 客户端）
- ❌ 不要修改 AstrBot 的任何代码
- ❌ 不要实现群聊功能（ilink API 仅支持单聊）
- ❌ 不要实现"正在输入…"功能（v1 可跳过 typing indicator）
- ❌ 不需要 Electron 环境，纯 Node.js 即可
- ❌ 不需要数据库，配置存 JSON 文件即可
