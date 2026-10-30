[根目录](../CLAUDE.md) > **resources**

# resources - 随包只读资源

## 模块职责

随 npm 包发布的只读资源：Electron 面板应用与吉祥物素材、原生 DLL、图谱页面用的前端库、表情图片。

## 子目录

| 路径 | 内容 |
| --- | --- |
| `panel/` | 悬浮球面板：`main.cjs`、`renderer.js`、`preload.cjs`、`panel.css`、`index.html`、`tray-menu.cjs`、`quick-menu.cjs`、`ball-position.cjs`，以及约 30 张吉祥物 PNG |
| `js/graph3d/` | three.js 与 d3 模块、`layout.mjs`、`viewer2d.js`，版本与许可见同目录 `NOTICE.txt` |
| `js/marked.min.js` | Markdown 渲染库 |
| `wcdb/win32/x64/` | 微信数据库访问相关原生 DLL |
| `key/win32/x64/` | 密钥提取相关原生 DLL |
| `wechat-emoji/` | 表情图片 |

## 入口与启动

无入口。面板由 `src/panel/launch.ts` 启动；图谱页面由 `scripts/graph_3d.py` 生成时内联这些库。

## 对外接口

- 面板静态文件只能通过 `src/panel/server.ts` 的 `STATIC_FILES` 白名单提供。新增资源见 `docs/EXTENDING.md` Recipe G（四处接线，其中三处静默失败）。
- 图谱库列表在 `scripts/graph_3d.py` 的 `LIB_D3` 与 `resources/js/graph3d/layout.mjs` 的 `LIBS` 两处，必须同步。

## 关键依赖与配置

- 面板：Electron（devDependency）。
- 图谱：three.js、d3 系列（见 `NOTICE.txt`）。

## 数据模型

无。

## 测试与质量

- `test/panel-packaging.test.ts`：`PANEL_FILES` 与目录双向对账。
- `test/panel-lift-frames.test.ts`、`test/panel-tray-pixels.test.ts`：立绘帧几何与 PNG 格式。
- `test/graph-3d-cli.test.ts`：两处库列表一致，且生成页面无外部引用。

## 常见问题 (FAQ)

- **面板球体首次按下闪空？** 新表情面未加入 `renderer.js` 的预取列表。
- **能否从 CDN 引用图谱库？** 不能。`file://` 页面与"本地优先"约束要求内联，见 `docs/EXTENDING.md` Recipe E。

## 相关文件清单

- `resources/panel/main.cjs`
- `resources/panel/renderer.js`
- `resources/panel/panel.css`
- `resources/js/graph3d/NOTICE.txt`
- `resources/js/graph3d/layout.mjs`

## 覆盖说明

本次只列出目录结构，未读取任何资源正文，也未读取 `key/` 与 `wcdb/` 下的内容（敏感路径，按约定不读）。

## 变更记录 (Changelog)

- 2026-10-15：由 `/init-project` 首次生成。
- 2026-10-30：随上游 v1.21.4 同步；复核所列文件均仍存在。
