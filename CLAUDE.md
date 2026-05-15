# WeFlow CLI

微信聊天记录命令行工具（TypeScript/Node.js 18+），Python 脚本处理公众号日报 + AI 摘要。

## 项目结构

- `bin/weflow-cli.ts` — CLI 入口（commander）
- `src/core/` — 数据库核心（SQLCipher/NT/WCDB）
- `src/services/` — 业务逻辑
- `scripts/` — Python 脚本（biz_daily, classify_daily, chat_report, compile_wiki, pipeline, generate_review）
- `mcp-server/` — MCP Server

## 常用命令

```bash
npm run build && node cli.cjs <command>
npm run dev -- <command>                    # 开发模式（tsx）
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
