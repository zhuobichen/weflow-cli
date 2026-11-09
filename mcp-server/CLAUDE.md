[根目录](../CLAUDE.md) > **mcp-server**

# mcp-server - MCP 服务端

## 模块职责

把受控工具以 MCP 协议暴露给外部客户端。提供两种传输，工具表相同：

- stdio（默认）：`index.ts`
- Streamable HTTP（`--http`，回环 `127.0.0.1:8790`，强制 Bearer token，含 DNS-rebinding 防护）：`http.ts`、`httpConfig.ts`

## 入口与启动

- `mcp-server/index.ts`：`buildServer()` 构造 `Server`，`main()` 选择传输。
- `mcp-server/http.ts`：`startHttpServer()`。
- `mcp-server/httpConfig.ts`：`resolveHttpOptions()`，读取端口与 token 配置。

## 对外接口

- 工具分两部分：
  1. 派生工具：来自 `src/services/assistantTools.ts` 的 `MCP_TOOL_DEFS`，改一处两侧同时生效。
  2. 手写 `wechat.*` 工具，`CallTool` 的 `switch` 中有 `wechat.search_articles`、`wechat.get_daily`、`wechat.get_concept`、`wechat.export_messages` 四个 `case`。
- 派生工具被 `MCP_EXCLUDED` 排除的三个：`save_memory`、`look_at_image`、`set_todo_status`。
- 工具总数（32 个）与 `docs/MCP.md` 表格的一致性由测试守护，不在本文件重复维护。

## 关键依赖与配置

- `@modelcontextprotocol/sdk`。
- `src/utils/mcpSecurity.ts` 提供 `safeChildPath`、`safeDate`，用于 `wechat.*` 工具的路径与日期入参校验。
- 写入类与出网类调用需要显式 `confirm`，见 `docs/MCP.md`。

## 数据模型

无。返回体经 `src/services/messageContract.ts` 的 `createWeFlowEnvelope` 包装为 `weflow-message/v1`。

## 测试与质量

- `test/tool-registry.test.ts`：`docs/MCP.md` 表格与实际服务的工具集合双向一致。
- `test/mcp-http.test.ts`：HTTP 传输的鉴权与绑定。
- `test/assistant-tools.test.ts`：MCP 子集不含写入、发送、发布类工具。

## 常见问题 (FAQ)

- **MCP 不是只读吗？** 不是。工具面由助手工具表派生，含写入类（需 `confirm`）与出网类读取，见根 `ARCHITECTURE.md` 的"助手与 MCP"。
- **想加一个只属于 MCP 的工具？** 按 `docs/EXTENDING.md` Recipe D：`ListTools`、`CallTool` 的 `case`、`docs/MCP.md` 三处同时改。

## 相关文件清单

- `mcp-server/index.ts`
- `mcp-server/http.ts`
- `mcp-server/httpConfig.ts`
- `docs/MCP.md`

## 覆盖说明

本次只读取了 `index.ts` 的导入、函数签名与 `case` 列表，未读 `http.ts` 与 `httpConfig.ts` 的正文。

## 变更记录 (Changelog)

- 2026-10-15：由 `/init-project` 首次生成。
- 2026-10-30：随上游 v1.21.4 同步；复核所列文件均仍存在。
