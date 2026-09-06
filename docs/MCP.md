# MCP Integration

WeFlow CLI exposes article, knowledge-base, and selected local-data functions through an MCP server over stdio. The server reads the project's local `output/` directory and only contacts external services when a requested tool requires it. The available inventory follows `mcp-server/index.ts`; do not hard-code a tool count in client documentation.

## Configure a client

From the project root, generate the baseline configuration:

```powershell
weflow-cli mcp-config --output .mcp.json
```

The generated server starts with Node.js and `tsx`:

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

Copy this entry into your MCP client's configuration and ensure `cwd` points to the cloned WeFlow CLI directory. Restart the client after saving the configuration.

## Available tools

| Tool | Purpose | Local data required |
| --- | --- | --- |
| `wechat.search_articles` | Search captured official-account articles. | `output/biz-daily/` |
| `wechat.get_daily` | Read a daily article collection. | `output/biz-daily/` |
| `wechat.get_review` | Read an AI learning review. | `output/reviews/Daily/` |
| `wechat.get_stats` | Show article, knowledge-base, and local WeChat data statistics. | Generated output |
| `wechat.get_concepts` | List compiled concepts. | Vault Wiki output |
| `wechat.get_concept` | Read one compiled concept. | Vault Wiki output |
| `wechat.format_article` | Convert Markdown to WeChat-ready HTML. | None |
| `wechat.list_themes` | List available article themes. | None |
| `wechat.fetch_article` | Fetch and convert a public WeChat article. | Network access |
| `wechat.search_public` | Search public WeChat articles. | Network access |
| `wechat.publish_article` | Create a WeChat official-account draft. | `WECHAT_APPID` and `WECHAT_APPSECRET` |
| `wechat.list_sessions` | List recent chat sessions with last-message snippets. | Decrypted WeChat database |
| `wechat.get_messages` | Read recent messages with one contact. | Decrypted WeChat database |
| `wechat.search_favorites` | Search WeChat favorites (official-account articles, notes, links). | Decrypted WeChat database |
| `wechat.read_favorite` | Fetch and read the body of a favorited article. | Decrypted WeChat database, network access |
| `wechat.get_daily_report` | Filter daily reports by topic/keyword with AI summaries. | `output/biz-daily/` |
| `wechat.get_sns` | Read the Moments timeline or statistics. | Decrypted WeChat database |
| `wechat.get_weread` | Read WeRead shelf, notebooks, or search books. | `wereadApiKey` config |
| `wechat.get_todos` | List todos extracted from chat history. | `scripts/extract_todos.py`, Python |
| `wechat.search_knowledge` | Fuzzy-search concept pages in the knowledge base. | Vault Wiki output |
| `wechat.search_memory` | Search long-term assistant memory. | `~/.weflow-cli/assistant_memory.json` |
| `wechat.save_memory` | Save a durable fact into long-term memory. | `~/.weflow-cli/assistant_memory.json` |

The chat-data tools share service code with the optional `weflow-cli assistant` WeChat bot, but access is still controlled by the MCP client's permissions and local configuration. The bot and MCP transport are not interchangeable message channels.

## Safety boundary

- The server inherits the permissions of the MCP client. Only add it to a client you trust.
- Article and knowledge-base tools read files under the current project directory. Keep `cwd` scoped to your intended WeFlow CLI checkout.
- Chat-data tools (`list_sessions`, `get_messages`, `search_favorites`, `get_sns`, `get_todos`) read your locally decrypted WeChat database. Only run this MCP server on machines where that is acceptable, and never expose the stdio server over a network.
- `read_favorite`, `fetch_article`, `search_public`, and `publish_article` make network requests; `read_favorite` rejects private/loopback URLs. Publishing creates a draft in the connected official account; review client prompts before approving that action.
- Do not place API keys in `.mcp.json`. Use environment variables where a client supports them.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Server fails to start | Run `npm install` in the configured `cwd`, then run `npx tsx mcp-server/index.ts`. |
| No articles found | Generate a daily collection first, then confirm `output/biz-daily/` exists under `cwd`. |
| Publish fails | Confirm `WECHAT_APPID` and `WECHAT_APPSECRET` are set in the MCP client's environment. |
| Client cannot find `npx` | Configure an absolute Node.js command path or install Node.js 18+. |
