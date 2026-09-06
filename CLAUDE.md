# WeFlow CLI 快速维护入口

先阅读：

1. `AGENTS.md`：仓库级约束。
2. `docs/PROJECT_STATE.md`：当前能力、限制和验证状态。
3. `docs/DECISIONS.md`：不可随意推翻的安全与架构取舍。
4. `OPERATIONS.md`：用户操作和排障流程。
5. `ARCHITECTURE.md`：模块边界和数据流。

## 常用开发命令

```powershell
npm install
npm run build
npm test
npm run dev -- <command>
```

日报无 AI 运行：

```powershell
npm run dev -- daily --no-ai
npm run dev -- daily-server --date YYYY-MM-DD --open
```

## 维护原则

- 仅处理用户本人或明确获授权的数据。
- 不把数据库、密钥、token、账号标识、聊天内容、导出文件或真实路径放进代码、文档、Issue、PR 或测试夹具。
- 不把 npm 发布包当作 GitHub 源码的实时镜像；先确认版本和提交。
- 变更功能、限制或验证状态时同步 `docs/PROJECT_STATE.md`；影响安全、数据流或兼容性时追加 `docs/DECISIONS.md`。
- 发送、助手、MCP 和云端 AI 都要保留显式权限边界；不实现远程静默控制或非官方协议绕过。
