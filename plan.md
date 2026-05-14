# WeFlow CLI 开发计划

## 当前状态

版本 v1.1.0，核心功能完备。支持 npm 全局安装，一行命令解密/查询/导出微信聊天记录，自动抓取公众号文章并 AI 分类整理。

### 已完成 (v1.1.0)

- [x] 4.x NT 格式数据库解密 + 3.x SQLCipher 解密，连接优先级：预解密 → PBKDF2 → NT → WCDB
- [x] 微信进程内存密钥扫描，密钥 AES-256-GCM 加密绑定单机
- [x] 多格式导出：JSON / TXT / Markdown / HTML / Excel
- [x] HTML 导出：微信风格气泡 + 图片 base64 内嵌 + 发送人昵称 + 全文搜索
- [x] HTML 单文件导出，文件名使用备注名
- [x] contact.db 昵称/备注名映射，sessions/contacts/messages 替换 wxid
- [x] CLI 兼容昵称、备注名、序号
- [x] 公众号日报：biz_daily.py 抓取全文 + DeepSeek V4 摘要与分类
- [x] 后处理管线：classify_daily.py 广告清洗 + 兴趣深度摘要 + AI/学术/新闻/文学分文件夹
- [x] npm 全局安装支持：`npm install -g weflow-cli` → `weflow-cli <command>`
- [x] 聊天月报：chat_report.py 三阶段（采集→AI任务识别→Markdown），支持 --talker 多选
- [x] 公共工具模块 scripts/_utils.py
- [x] export 命令内置 Python HTML 导出
- [x] sns.db 朋友圈数据库扫描
- [x] 微信消息 bridge（实验性，ilink API）
- [x] README 对齐 qchat-cli 风格 + 苏轼诗句 + 架构图
- [x] 隐私修复：git filter-repo 清理敏感文件历史

### 运行命令

```bash
# 安装
npm install -g weflow-cli

# 初始化
weflow-cli init

# 查看会话 / 消息
weflow-cli sessions
weflow-cli messages 联系人A -n 20

# 导出 HTML（单文件，含图片+搜索+昵称）
weflow-cli export 联系人A html

# 公众号日报 + AI 分类
python scripts/biz_daily.py --api-key <key>
python scripts/classify_daily.py --api-key <key> --interest AI
```

---

## 未来考虑

收到用户反馈后再做：

- Electron 入口错误提示友好化
- HTML 时间线导航（按日期折叠）
- init 进度条

---

## 技术备忘

### 关键路径

| 版本 | 数据库路径 | 加密方式 |
|------|-----------|---------|
| 3.x | `Documents\WeChat Files\<wxid>\Msg\Multi\MSG0.db` | SQLCipher 3, PBKDF2-HMAC-SHA1 |
| 4.x | `xwechat_files\<wxid>\db_storage\message\message_0.db` | SQLCipher 4, hex密钥+盐 |

### 密钥安全

- 配置：`~/.weflow-cli/config.json`，敏感字段 `lock:` 前缀 AES-256-GCM 加密
- 密钥由 `机器名+用户名` PBKDF2 派生，绑定单机

### NT 图片限制

- `.dat` 文件（`msg/attach/<md5>/YYYY-MM/Img/`）是微信专有加密格式，无法离线破解
- 从 `cache/YYYY-MM/Message/<md5>/Thumb/` 提取 JPG 缩略图，按 `local_id` 匹配
- 覆盖率 ~15%（仅最近两个月），翻看旧聊天可生成更多缓存

### 发送人识别

- `real_sender_id` → `Name2Id` → `contact.db` 三重映射

### DeepSeek V4 注意

- 推理模型需 `max_tokens ≥ 500`（含 reasoning_tokens），否则输出为空
- 分类 prompt 需简短，避免过多 examples 触发长推理
