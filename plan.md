# WeFlow CLI 开发计划

## 当前状态

版本 v1.0.0，核心功能可用：微信 3.x/4.x 数据库解密、聊天记录查询导出。

### 已完成

- [x] 4.x NT 格式数据库解密（sqlcipher3 Python 桥接）
- [x] 3.x SQLCipher 解密（PBKDF2 + 预解密文件）
- [x] 微信进程内存密钥扫描
- [x] 连接优先级：预解密 → PBKDF2 → NT → WCDB
- [x] 多格式导出：JSON / TXT / Markdown / HTML / Excel
- [x] HTML 导出：微信风格气泡、图片 base64 内嵌（从 NT 缓存提取）
- [x] 导出发送人显示备注名 + "我"（不再是 wxid）
- [x] contact.db 昵称/备注名映射，sessions/contacts 自动替换 wxid
- [x] CLI 兼容昵称、备注名、序号（resolveTalker + inquirer 交互选择）
- [x] sns.db 朋友圈数据库扫描支持（密钥已匹配，可解密读取）
- [x] README 对齐 qchat-cli NapCatQQ 风格
- [x] 联系人搜索（来自 contact.db）
- [x] 隐私修复：删除含敏感信息的测试文件，git filter-repo 清理历史

### 运行命令

```bash
# 初始化（需要微信已登录运行）
npx tsx bin/weflow-cli.ts init

# 查看会话
npx tsx bin/weflow-cli.ts sessions

# 导出 HTML（带图片内嵌）
python scripts/export_chat_html.py \
  --db "xwechat_files/<wxid>/db_storage/message/message_0.db" \
  --key "<64位hex密钥>" --salt "<32位hex盐>" \
  --talker "<目标wxid>" --name "备注名" \
  --out "./output/<wxid>" --parts 5 \
  --cache-dir "xwechat_files/<wxid>/cache"
```

---

## 待做事项

### P0 — 用户体验

**安装方式**
- 打包为 npm 全局包或独立 exe，用户一行命令/双击即用
- 当前需要 `npx tsx bin/weflow-cli.ts`，对普通用户不可接受

**错误提示**
- Electron 入口报错几千行堆栈，需替换为友好提示
- 原生模块（lz4/xxhash）与 Electron 内置 Node.js 版本不兼容时，需降级到纯 Node.js 入口

### P1 — 功能增强

**导出格式**
- 单文件 HTML 选项（当前固定分 part）
- 内嵌全文搜索（纯前端 JS 实现）
- 聊天记录时间线导航（按日期折叠）

**图片覆盖率**
- 缓存预热提示：仅能提取最近两个月缩略图，旧图片需在微信中翻看以生成缓存
- 引导文字写入导出 HTML 的 footer

### P2 — 交互优化

**init 流程**
- 微信未检测到时的清晰指引（不要只报错）
- 多账号选择（当前自动选第一个）
- 进度条替代等待省略号

**命令体验**
- `export` 命令内置 HTML 导出（当前需手动调 Python 脚本）
- 导出同时自动匹配备注名

---

## 技术备忘

### 关键路径

| 版本 | 数据库路径 | 加密方式 |
|------|-----------|---------|
| 3.x | `Documents\WeChat Files\<wxid>\Msg\Multi\MSG0.db` | SQLCipher 3, PBKDF2-HMAC-SHA1, 64000 迭代 |
| 4.x | `xwechat_files\<wxid>\db_storage\message\message_0.db` | SQLCipher 4, 64位hex密钥+32位hex盐 |

### 密钥存储

- 配置文件：`~/.weflow-cli/config.json`
- 敏感字段用 `lock:` 前缀，AES-256-GCM 加密，密钥由 `机器名+用户名` PBKDF2 派生
- 绑定单机，换机无法解密

### NT 图片限制

- `.dat` 文件（`msg/attach/<md5>/YYYY-MM/Img/`）是微信专有加密格式（双密钥链），无法离线破解
- 当前方案：从 `cache/YYYY-MM/Message/<md5>/Thumb/` 提取 JPG 缩略图，按 `local_id` 匹配
- 覆盖率 ~15%（仅最近两个月）
- 扩展覆盖需用户在微信客户端翻看旧聊天以触发缓存生成

### 发送人识别

- `message_0.db` 的 `real_sender_id` 通过 `Name2Id` 表映射到 `user_name`
- `real_sender_id` 在 NT 格式下从不为 0
- 应与目标 talker 比较来区分"我"和"对方"

---

## 下次启动

```bash
# 确保微信已登录运行
npx tsx bin/weflow-cli.ts init

# 验证连接
npx tsx bin/weflow-cli.ts sessions
```

优先从 **安装方式** 或 **导出格式单文件** 开始。
