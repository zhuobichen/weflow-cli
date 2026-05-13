# weflow-cli 技术架构

## 一句话定位

本地命令行工具，解密微信数据库，查询/导出聊天记录，抓取公众号文章并 AI 整理。

## 核心架构（4 层）

```
┌─────────────────────────────────────────────────┐
│                  CLI 交互层                       │
│  init · sessions · messages · export · contacts  │
│  whitelist · biz-daily · classify                │
└────────────────────┬────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────┐
│                 服务层                            │
│  chatService  configService  exportService       │
│  wechatMessageService  whitelistService          │
└────────────────────┬────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────┐
│                 核心引擎层                         │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐ │
│  │ ntCore   │ │sqlcipher │ │ wechatClient      │ │
│  │ (NT 4.x) │ │Core(3.x) │ │ (ilink HTTP/CDN)  │ │
│  └─────┬────┘ └────┬─────┘ └────────┬─────────┘ │
│        │           │               │            │
│  ┌─────▼───────────▼───────────────▼──────────┐ │
│  │          Python Bridge                     │ │
│  │  nt_decrypt.py  export_chat_html.py        │ │
│  │  biz_daily.py   classify_daily.py          │ │
│  │  (sqlcipher3 + zstandard + Scrapling)      │ │
│  └────────────────────────────────────────────┘ │
└────────────────────┬────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────┐
│                 数据层                            │
│  3.x: MSG0.db (SQLCipher 3, PBKDF2)             │
│  4.x: message_0.db + contact.db + sns.db         │
│       biz_message_0.db (公众号)                   │
│  外部: mp.weixin.qq.com (公众号文章)              │
│        DeepSeek API (AI 摘要/分类)                │
└─────────────────────────────────────────────────┘
```

## 数据流（三条主链路）

**链路 A：聊天记录查询导出**
```
微信进程内存 → 提取密钥(AES-256-GCM加密存储)
  → 解密 SQLCipher 数据库(3层优先级: 预解密→PBKDF2→NT)
  → 查询消息/会话/联系人(contact.db交叉引用昵称)
  → 导出 JSON/TXT/MD/HTML/Excel
  → HTML 含图片 base64 内嵌(从 NT 缓存目录提取)
```

**链路 B：公众号日报**
```
biz_message_0.db → 提取今日推送(protobuf解压→XML元数据)
  → 微信UA直连 mp.weixin.qq.com 抓取全文(8-12s防风控)
  → DeepSeek V4 生成摘要+主题分类(一次调用)
  → 按 AI/学术/新闻/文学 分文件夹输出
  → classify_daily.py 后处理: 广告清洗+兴趣深度摘要
```

**链路 C：微信消息收发（实验性）**
```
ilink 扫码登录 → bot_token
  → 长轮询 getupdates(40s timeout)
  → whitelist 过滤(非白名单静默丢弃)
  → send 需 context_token(先收后发限制)
```

## 关键技术细节

- **密钥安全**：内存提取后用 AES-256-GCM 加密(机器名+用户名 PBKDF2 派生)，绑定单机
- **NT 数据库**：SQLCipher 4, cipher_plaintext_header_size=0, hex密钥+盐
- **公众号抓取**：微信浏览器 UA 绕过 WAF, 保守间隔 8-12s 随机抖动
- **DeepSeek 适配**：V4 推理模型需 max_tokens≥500(含 reasoning_tokens)
- **联系人识别**：real_sender_id → Name2Id → contact.db 三重映射
