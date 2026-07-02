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
