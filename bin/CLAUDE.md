[根目录](../CLAUDE.md) > **bin**

# bin - CLI 入口

## 模块职责

Commander 命令注册、参数校验、交互式菜单。所有用户可见命令从这里进入，再调用 `src/services/`。

## 入口与启动

- `bin/weflow-cli.ts`：开发入口（`npm run dev -- <command>`，经 tsx）。
- `bin/weflow-cli-electron.cjs`：Electron 面板的启动包装。
- 发布入口是根目录的 `cli.cjs`（`package.json` 的 `bin` 字段），不在本目录。

## 对外接口

- 命令共 138 处 `.command(` 调用（含子命令），以 `weflow-cli --help` 为准。
- 交互式菜单 `showInteractiveMenu()` 与其 `switch (action)` 一一对应。
- 约定：可被脚本或 AI 驱动的命令提供 `--json`；破坏性操作为 `--dry-run` 预览后 `--yes` 确认。

## 关键依赖与配置

- `commander`（命令解析）、`chalk`、`inquirer`。
- `configurableKeys` 列表决定 `config set` 能接受哪些键，见 `docs/EXTENDING.md` Recipe B。

## 数据模型

无自有数据模型。

## 测试与质量

- `test/cli-menu.test.ts`：菜单与 `switch` 双向一致，菜单调用的命令必须存在。
- `test/json-surface.test.ts`、`test/json-error-surface.test.ts`：`--json` 覆盖与 JSON 错误输出。
- `test/config-cli.test.ts`、`test/vault-search-cli.test.ts` 等按命令分组的 CLI 测试。
- 新增命令的完整步骤与拦截测试见 `docs/EXTENDING.md` Recipe C 与"What will catch you"。

## 常见问题 (FAQ)

- **菜单项点了没反应？** 检查菜单条目是否有对应 `case`，以及 `runCmd('x')` 的命令名是否真实存在（`cli-menu.test.ts` 会红）。
- **`config set` 拒绝某个键？** 该键未加入 `configurableKeys`。

## 相关文件清单

- `bin/weflow-cli.ts`
- `bin/weflow-cli-electron.cjs`

## 变更记录 (Changelog)

- 2026-10-15：由 `/init-project` 首次生成。
- 2026-10-30：随上游 v1.21.4 同步；`.command(` 计数由 114 更新为 138（由 `test/architecture-counts.test.ts` 钉住）。
