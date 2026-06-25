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
