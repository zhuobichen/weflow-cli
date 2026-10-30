[根目录](../CLAUDE.md) > **src**

# src - 核心业务库

## 模块职责

CLI、MCP 与面板共用的业务实现。分为四个子目录：

| 子目录 | 职责 |
| --- | --- |
| `core/` | 数据目录发现、数据库连接（NT / SQLCipher / WCDB）、密钥适配、微信客户端底层集成 |
| `services/` | 聊天、导出、证据包、日报、助手、白名单、隐私策略、配置、Python 桥接 |
| `panel/` | 本机面板的回环 HTTP 服务（`127.0.0.1:8766`）、Electron/Edge 启动决策、CLI 侧客户端 |
| `utils/` | 路径、日期范围、原子写入、MCP 安全校验、Python 进程环境等纯工具 |

`src/types.ts` 与 `src/lz4.d.ts` 为顶层类型声明。

## 入口与启动

本目录没有独立入口，由 `bin/` 与 `mcp-server/` 导入。

## 对外接口

- `src/services/assistantTools.ts`：助手与 MCP 共用的工具表。`TOOL_DEFS` 声明、`executeTool` 实现、`MCP_TOOL_DEFS` 派生。改动方法见 `docs/EXTENDING.md` Recipe A。
- `src/services/configService.ts`：配置读写，`ENCRYPTED_KEYS` 决定哪些键加密存储。改动五处见 Recipe B。
- `src/services/pythonBridge.ts`：调用 `scripts/`，`runPythonJson` 解析脚本的 JSON 输出（`parseJsonFrom`）。
- `src/panel/server.ts`：`STATIC_FILES` 白名单决定面板可取的静态资源。

## 关键依赖与配置

- 配置文件与助手记忆位于用户本机目录，不在仓库内，文档中一律使用占位符。
- Python 子进程统一带 `PYTHONIOENCODING=utf-8`，由 `utils/pythonProcessEnv.ts` 注入。
- 原生库（`wcdb`、`wx_key`）经 `koffi` 加载，DLL 位于 `resources/`。

## 数据模型

本目录不定义数据库 schema。聊天与联系人结构见 `docs/CONTACT_DB_SCHEMA.md`、`docs/DATA_CONTRACT.md`。

## 测试与质量

- `test/assistant-tools*.test.ts`、`test/tool-registry.test.ts`：工具表一致性与 MCP 派生。
- `test/config-keys.test.ts`：配置键五处一致性。
- `test/panel-*.test.ts`：面板服务、打包、资源白名单。
- `test/concept-dirs-agreement.test.ts`：概念页目录声明一致性。
- `test/privacy-paths.test.ts`、`test/assistant-privacy-wiring.test.ts`：隐私与出网边界。

## 常见问题 (FAQ)

- **新增助手工具后模型看不到？** 只在 `executeTool` 加了 `case` 而没在 `TOOL_DEFS` 声明，或反之。`tool-registry.test.ts` 会红。
- **面板图标闪空？** 新资源没加入 `STATIC_FILES`，见 `docs/EXTENDING.md` Recipe G。

## 相关文件清单

- `src/services/assistantTools.ts`
- `src/services/configService.ts`
- `src/services/pythonBridge.ts`
- `src/services/assistantService.ts`
- `src/services/outboundSend.ts`
- `src/core/wechatClient.ts`
- `src/core/dbPathService.ts`
- `src/panel/server.ts`
- `src/utils/mcpSecurity.ts`
- `src/utils/pythonProcessEnv.ts`

## 覆盖说明

本次为轻量扫描：已列出全部子目录与服务文件名，未逐文件阅读。`services/` 下的 `assistantRouter*`、`syncService`、`wechatMessageService` 等的内部契约尚未读取。

## 变更记录 (Changelog)

- 2026-10-15：由 `/init-project` 首次生成。
- 2026-10-30：随上游 v1.21.4 同步；复核所列服务文件与测试文件名均仍存在。
