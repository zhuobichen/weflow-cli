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
