# WeFlow CLI

微信聊天记录命令行工具（TypeScript/Node.js 18+），Python 脚本处理公众号日报 + AI 摘要。

## 项目结构

- `bin/weflow-cli.ts` — CLI 入口（commander）
- `src/core/` — 数据库核心（SQLCipher/NT/WCDB）
- `src/services/` — 业务逻辑
- `scripts/` — Python 脚本（biz_daily, classify_daily, chat_report, compile_wiki, pipeline, generate_review, fav_server, generate_html）
- `mcp-server/` — MCP Server

## 常用命令

```bash
npm run build && node cli.cjs <command>
npm run dev -- <command>                    # 开发模式（tsx）
```

## 公众号日报

```bash
python scripts/biz_daily.py --date YYYY-MM-DD             # 抓取+AI摘要
python scripts/generate_html.py --date YYYY-MM-DD         # 生成 HTML 页面
python scripts/fav_server.py --date YYYY-MM-DD --port 8765  # 启动阅读器
```

### ⚠️ 阅读器 URL 规则

`fav_server.py` 在启动时会 `os.chdir(date_dir)`，所以 **正确访问方式是 `/` 而不是 `/index.html`**：

| 正确 ✅ | 错误 ❌ |
|------|------|
| `http://localhost:8765/` | `http://localhost:8765/index.html` |
| `http://localhost:8765/#AI` | `http://localhost:8765/index.html#AI` |

**原因**：`/index.html#AI` 会让浏览器相对于 `/index.html` 解析路径，导致 JS 中的 `fetch()`、链接等使用错误相对路径，页面不渲染。

### 模板管理

- **模板位置** `output/biz-daily/.template/article.html`（已 git 提交保护）
- `generate_html.py` 从模板复制 `article.html`，不会修改模板
- 模板修改需要手动替换后重新生成，**绝对不要**删除 `.template/` 目录

### 🐛 Pipeline 故障排查

**文章多（>50 篇）时 exit code 127**

根因：Claude Code 后台任务系统有超时限制，`biz_daily.py` 抓取 80+ 篇文章耗时 20 分钟，任务管理器会提前杀进程。

解决方案：
```bash
# 用 nohup 绕过超时限制
cd E:/CodeProject/weflow-cli
nohup python -u scripts/biz_daily.py --date YYYY-MM-DD --api-key <key> > /tmp/biz.log 2>&1 &

# 随时查看进度
tail -5 /tmp/biz.log
```

**全部分类为"学术"**

根因：`pipeline.py` 的 `--engine` 参数默认值曾为 `local`，覆盖了 `biz_daily.py`/`classify_daily.py` 自身的 `deepseek` 默认值，导致所有文章走 keyword fallback 全归学术。

解决方案：
```bash
# 必须显式指定 --engine deepseek
python scripts/pipeline.py --api-key <key> --engine deepseek --date YYYY-MM-DD
```

已在 `32f569b` 提交中修复（默认引擎改为 `deepseek`）。

**article.html 是暗色而非暖色**

根因：模板被覆盖为内置 `generate_article_viewer()` 生成的暗色版本（~50KB）。正常模板应该是 ~350KB 暖色版。

解决方案：
```bash
# 从 git 恢复模板
git checkout HEAD -- output/biz-daily/.template/article.html
# 重新生成
rm -f output/biz-daily/YYYY-MM-DD/article.html
python scripts/generate_html.py --date YYYY-MM-DD
```

**index.html 链接不渲染（打开是原始 Markdown）**

根因：卡片链接是 `href="AI/xxx.md"` 而非 `href="article.html?file=AI/xxx.md"`，浏览器直接展示 .md 源码。

解决方案：在 `generate_html.py` 中确保 href 格式为 `article.html?file={quote(art['rel_path'])}`（URL 编码中文文件名）。

### 🐛 聊天记录导出（HTML / `export_chat_html.py`）

**图片对错消息（张冠李戴）**

根因：微信 NT 缓存文件名是 `<id>_<create_time>_thumb.jpg`，脚本曾把下划线前的 `<id>` 当作消息的
`local_id` 去配对。**它不是** —— 该数字在不同会话间重复，且与 DB 完全对不上（实测某会话偏差近一年）。
唯一的可靠键是文件名里嵌入的 unix 时间戳，它与 `message.create_time` **精确到秒相等**
（实测 156/156 全部精确命中）。

解决方案：`image_map` 以 `create_time` 为键，不要用 local_id。

**公众号分享没有封面**

老格式（2025 年前后）的 appmsg 只存 `<cdnthumburl>`，那是二进制 ASN.1 描述符（内含 UUID 形态的
fileid + `cdnthumbaeskey`），**不是 URL**，且缺微信会话凭据无法下载（`wxapp.tc.qq.com` / `dldir1.qq.com`
各种路径形态全部 404）。新格式才直接给 `<thumburl>`。

解决方案：本地缓存 → `<thumburl>` → **抓文章页取 `og:image`**（`resolve_appmsg_cover`）。
抓取注意：只能用 XML 里**完整**的 `<url>`，缺了 `&chksm=...` 校验参数会返回错误页；
`og:image` 在页面约 18KB 处，读前 48KB 即可，不必下整页（正文页可达 3MB）。
仅在 `mp.weixin.qq.com` 域名下抓取，每次导出上限 80 篇，结果缓存在 `<out>/.cover-cache/`，
可用 `--no-cover-fetch` 完全关闭联网。

**导出很慢 / 卡住**

首次导出会为缺封面的文章联网抓取（大会话实测 1327 条消息 42 秒，其中仅 16 篇需联网）。
二次导出命中磁盘缓存，同会话降到 8.7 秒。若嫌慢用 `--no-cover-fetch`。

**普通图片消息（`[图片]`）没有图**

`local_type=3` 的图片同样只存 CDN 描述符，且 4.x 没有 `FileStorage` 目录、`--wx_dir` 从未被传入，
所以只能靠 NT 缓存按时间戳命中；缓存约只覆盖最近 2 个月，更早的图片无从恢复。

**消息库有分片（每条消息都可能被劈成两半）**

微信 4.x 会把消息轮换写入 `message_0.db` … `message_N.db`。轮换后旧分片不再更新，**同一个会话的记录
会横跨多个分片**。实测：`咸鱼梦想家` 标称 1327 条、止于 2026-08-29；加上 `message_3.db` 后是 **1580 条、
覆盖到当天**。只读一个分片不会报错，只会静默地少给你一段 —— 极难察觉。

**各分片密钥不用逐个捕获，可以直接算出来。**

> **微信 4.1.12.26+ 起改为「单一主密钥 + PBKDF2 派生」**

```
各库密钥 = PBKDF2-HMAC-SHA512(主密钥, 该库文件头前16字节的盐, 256000 次, dklen=32)
```

主密钥就是配置里的 `decryptKey`（Hook 在登录时捕获）。实测用它派生出的密钥**打开了全部 13 个库**
（`message_0..3`、`message_fts`、`message_resource`、`session`、`media_0/1`、`biz_message_0`、
`contact`、`sns`、`favorite`），并与已知的 `ntKey`/`contactKey`/`snsKey` **逐字节吻合**。

实现见 `export_chat_html.py` 的 `derive_db_key()` / `discover_message_shards()`，由 `--master-key` 触发
（`exportService.ts` 自动传入 `cfg.decryptKey`）。

**这意味着 Hook 只在一个场景下才必需**：首次拿到主密钥。之后所有分片、所有库都能离线派生，
不需要为了某个分片再跑一次注入 —— 早先"hook 必须赶在微信登录前安装"的限制因此对派生路径不再适用。

派生逻辑集中在 `scripts/nt_keys.py`；`_utils.get_db_config()` 会一并给出 `master_key`、`shards`
（所有分片路径+密钥）、`session_db`/`session_key`。读消息的脚本用
`_utils.open_message_shards(config)` / `collect_across_shards(config, fn)` 遍历全部分片 ——
**新写读取消息的代码时务必用它**，直接 `open_db(ntDbPath)` 会漏掉轮换后的数据。

**会话列表要看 `session.db`，不要看消息库的 `Name2Id`**

`db_storage/session/session.db` 的 `SessionTable`（含 `summary`/`last_timestamp`/`sort_timestamp`）
才是实时会话列表；消息库里的 `Name2Id is_session=1` 只覆盖"在该分片里有表的会话"，且微信轮换分片后
摘要就冻结了（曾出现整个列表停在 8/30、近期活跃会话完全缺席）。

**表情：优先用微信原版图，不是 Unicode 近似**

微信用的是自家美术的 105 个经典表情（**图形**，不是字符），Unicode 只能取"最接近"的标准 emoji，
画风必然不同。所以 `resources/emoji/` 放了 109 张原版 PNG（来源 `wechat-emojis`，MIT）：
有图的表情渲染成 `<span class="wxface wxf-xxxxxxxxxx">`，没图的才退回 Unicode，认不出的**原样保留**。

**同一个表情在一页里只内嵌一份 base64**（按 CSS 类去重）—— 一页出现 42 次也只占一份体积。
`wechat_emoji.face_css(html)` 只输出**本页用到的**那几张，不要改成整包内嵌（那会给每页加 ~1MB）。

**很多消息类型的正文是 XML，别当文本渲染**

除了 appmsg（49），这些类型的 `message_content` 也是 XML 载荷，直接 `escape_html` 会**把整页 XML
吐到聊天记录里**（实测 `咸鱼梦想家` 有 194 处）：

| 类型 | 载荷 | 正确渲染 |
|---|---|---|
| 47 自定义表情 | `<msg><emoji md5=… len=… /></msg>` | `[表情]` |
| 42 名片 | `<msg nickname="…" username="…">` | `[名片] 昵称` |
| 10000 系统消息 | `<sysmsg type="revokemsg"><content>…</content>` | 取 `<content>` 文本 |

`format_message` 末尾的 `else` 有兜底：**任何以 `<` 开头的未处理载荷一律渲染成占位符**，绝不原样吐出。

**表情包（自定义表情）可以解密，算法是现成的**

正文 XML 里的 `md5` 就是本地缓存的文件名（`cache/*/Emoticon/<md5前2位>/<md5>`、
`business/emoticon/{Persist,Thumb}/…`）。缓存文件用 **AES-128-CBC + PKCS7，且 key = IV**：

```
key = md5(f"{seed}{wxid}EMOTICON")[:16]        # wxid = 目录名去掉 _xxxx 后缀
```

`seed` 是**账号级常量，存在微信进程内存里**，用 `weflow-cli emoticon-key` 扫描获得并存入配置
（`emoticonSeed`）。验证方式是解出来必须命中图片魔数，所以不存在猜错。

**注意**：我先试的 AES-ECB/CBC/CFB/OFB/CTR 各种 IV、单字节 XOR 全都失败，浪费了不少时间 ——
原因是漏了 `key = IV` 这条，且 `wxid` 要**去掉 `_4c8e` 之类的后缀**。遇到这类问题先搜现成方案
（参考 `CN-Grace/Wechat-Emoticon-Parser` 的 v4.0-plus 分支），别硬试。

**wxgf 格式**：约 84% 的缓存表情是微信自有容器 —— `"wxgf"` + 小头部 + **裸 H.265 流**。
需要 ffmpeg 取首帧（`imageio-ffmpeg` 自带静态二进制，已加入 `requirements.txt`）。
解出来的图缓存在 `<out>/.sticker-cache/`，因为解码是最慢的一步。

**命中率有天花板**：微信会清理旧缓存，实测某会话 194 个表情里 139 个（72%）能在磁盘上找到，
其余早已被清掉，只能用 `[表情]` 占位。

**消息内容是 zstd 压缩的**

`message_content` 对大消息（appmsg XML、长文本）是 zstd 二进制。任何把它当纯文本处理的代码都会
**静默跳过这些消息** —— `mcp_bridge.py` 的搜索就曾因此搜不到任何分享链接。用
`_utils.decode_message_content()` 解码。

### 🐛 消息收发(send/listen)报 "缺少 context_token"

根因：`weflow-cli send`/`listen` 基于 OC Bot 通道（ilink/bot），与个人微信消息流隔离。
Bot 账号（如 `b6903209e131@im.bot`）和个人微信号（如 `wxid_xxx`）是不同的消息系统，
普通微信好友发来的消息不会经过 OC 通道。

解决方案：
- 目前 OC Bot 通道仅支持 Bot-to-Bot 或 Bot-to-自定义应用通信
- 向个人微信好友发消息需要在微信客户端直接操作
- 如需 CLI 控制，需探索 ilink 直接消息 API（非 Bot 模式）

详见 [[oc-channel-limitation]]

## MCP Server

```json
{
  "mcpServers": {
    "weflow": {
      "command": "npx",
      "args": ["tsx", "mcp-server/index.ts"],
      "cwd": "${workspaceFolder}"
    }
  }
}
```
