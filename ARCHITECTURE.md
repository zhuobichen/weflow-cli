# weflow-cli 技术架构

## 一句话定位

本地命令行工具 + MCP Server，解密微信数据库，查询/导出聊天记录，抓取公众号文章并 AI 整理，驱动 Obsidian 知识库与微信读书。

## 核心架构（5 层）

```
┌─────────────────────────────────────────────────────────────────┐
│                        MCP Server 层                             │
│  wechat_*  ·  weread_*  ·  obsidian_*  ·  wiki_*  ·  concept_* │
│  (微信公众号抓取/发布 · 微信读书 · Obsidian 知识库 · 概念图谱)    │
└─────────────────────────────┬───────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                        CLI 交互层                                 │
│  init · sessions · messages · export · contacts                 │
│  whitelist · biz-daily · classify · report                      │
└─────────────────────────────┬───────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                        服务层                                     │
│  chatService  configService  exportService  wereadService        │
│  wechatMessageService  whitelistService                          │
└─────────────────────────────┬───────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                       核心引擎层                                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐                 │
│  │ ntCore   │ │sqlcipher │ │ wechatClient      │                 │
│  │ (NT 4.x) │ │Core(3.x) │ │ (ilink HTTP/CDN)  │                 │
│  └─────┬────┘ └────┬─────┘ └────────┬─────────┘                 │
│        │           │               │                             │
│  ┌─────▼───────────▼───────────────▼──────────────────────────┐ │
│  │                   Python Bridge                             │ │
│  │  nt_decrypt.py   biz_daily.py   classify_daily.py           │ │
│  │  fav_server.py   generate_html.py   pipeline.py             │ │
│  │  compile_wiki.py  semantic_search.py  vault_rag.py          │ │
│  │  (sqlcipher3 + zstandard + Scrapling + DeepSeek API)       │ │
│  └─────────────────────────────────────────────────────────────┘ │
└─────────────────────────────┬───────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────────┐
│                        数据层                                     │
│  3.x: MSG0.db (SQLCipher 3, PBKDF2)                             │
│  4.x: message_0.db + contact.db + sns.db                         │
│       biz_message_0.db (公众号)                                   │
│  外部: mp.weixin.qq.com (公众号文章)  i.weread.qq.com (微信读书) │
│        DeepSeek API (AI 摘要/分类/RAG)                           │
│        Obsidian vault (本地知识库)                                │
└─────────────────────────────────────────────────────────────────┘
```

## 数据流（三条主链路）

**链路 A：聊天记录查询导出**
```
微信进程内存 → 提取密钥(AES-256-GCM加密存储)
  → 解密 SQLCipher 数据库(3层优先级: 预解密→PBKDF2→NT)
  → 查询消息/会话/联系人(contact.db交叉引用昵称)
  → 导出 JSON/TXT/MD/HTML/Excel（默认全部消息，无长度限制）
  → HTML 含图片 base64 内嵌(NT 缓存 + 公众号封面图远程下载)
```

**链路 B：公众号日报**
```
biz_message_0.db → 提取今日推送(protobuf解压→XML元数据)
  → 微信UA直连 mp.weixin.qq.com 抓取全文(8-12s防风控)
  → DeepSeek V4 生成摘要+主题分类(一次调用)
  → 按 AI/学术/新闻/文学 分文件夹输出
  → classify_daily.py 后处理: 广告清洗+兴趣深度摘要
```

**链路 C：聊天月报**
```
message_0.db → 获取所有会话 → 按时间/联系人过滤消息
  → 任务关键词预筛 → DeepSeek V4 识别任务+回复分析
  → 生成 Markdown 月报（统计+AI分析+按联系人详情）
```

**链路 D：本地阅读器**
```
fav_server.py(HTTP :8765)
  → 静态文件服务(公众号日报 Markdown + HTML)
  → /api/fav/toggle 收藏实时同步到 收藏/ 文件夹
  → /api/read/toggle 已读状态持久化(.read_state.json)
  → /api/explain 划词 DeepSeek AI 解释
  → /api/proxy?url= 微信CDN图片代理(绕防盗链Referer检查)
  → generate_html.py 生成分类展示页(localStorage + 服务端双持久化)
```

**链路 E：MCP Server**
```
mcp-server/index.ts(stdio JSON-RPC)
  → wechat_fetch_article: 微信公众号文章 → Markdown
  → wechat_format_article: Markdown → 排版 HTML(4 种主题)
  → wechat_publish_article: 发布草稿到公众号后台
  → wechat_search_articles: 知识库全文搜索
  → wechat_get_daily/review: 日报/学习日报检索
  → weread_*: 微信读书书架/笔记/书评
  → wechat_get_concept: 概念图谱 Wiki 查询
```

**链路 F：Obsidian 知识管道**
```
pipeline.py(完整管道)
  → 公众号日报 → classify_daily.py(分类+摘要)
  → compile_wiki.py(概念编译 → Wiki)
  → generate_review.py(学习日报)
  
vault_rag.py: Obsidian vault → 向量索引 → RAG 问答
semantic_search.py: 语义搜索 → 相关笔记
sync_weread.py: 微信读书 → Obsidian 笔记同步
create_reading_notes.py: 文章 → Obsidian 阅读笔记模板
enrich_backlinks.py: Wiki 反向链接增强
```

## 关键技术细节

- **密钥安全**：内存提取后用 AES-256-GCM 加密(机器名+用户名 PBKDF2 派生)，绑定单机
- **NT 数据库**：SQLCipher 4, cipher_plaintext_header_size=0, hex密钥+盐
- **公众号抓取**：微信浏览器 UA 绕过 WAF, 保守间隔 8-12s 随机抖动
- **DeepSeek 适配**：V4 推理模型需 max_tokens≥500(含 reasoning_tokens)
- **联系人识别**：real_sender_id → Name2Id → contact.db 三重映射
