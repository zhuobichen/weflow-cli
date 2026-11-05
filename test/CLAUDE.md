[根目录](../CLAUDE.md) > **test**

# test - 回归测试

## 模块职责

Node 与 Python 的回归测试，优先使用合成数据。TypeScript 测试使用 `node:test` 与 `tsx`，Python 测试使用 `unittest`。

## 入口与启动

- 全部 TypeScript：`npm test`（脚本为 `node --import tsx --test "test/**/*.test.ts"`）。
- 全部 Python：`python -m unittest discover -s test -p '*_test.py'`。
- 单个文件：`node --import tsx --test test/<name>.test.ts`。

## 对外接口

本目录不对外提供接口。`test/fixtures/make_synthetic_nt.py` 生成合成 NT 数据，供测试使用。

## 关键依赖与配置

- 测试不得依赖真实微信数据库、密钥或导出文件。
- 夹具中的示例人名、群名使用占位符（`联系人A`、`示例群`）。

## 数据模型

无。

## 测试与质量

按不变量分组，每组对应拦截的具体漏改。完整对照表见 `docs/EXTENDING.md` 的"What will catch you"一节，本文件不重复。主要分组：

- 工具与 MCP 一致性：`tool-registry`、`assistant-tools*`
- CLI 与菜单：`cli-menu`、`json-surface`、`json-error-surface`、各 `*-cli`
- 配置：`config-keys`、`config_keys_declared_test`
- 面板：`panel-*`
- 概念页与知识卡：`concept-dirs-agreement`、`chat_notes_test`、`compile_wiki_test`
- 打包与文档：`pack-contents`、`package-root`、`doc-links`、`decision-refs`

本目录的文件总数（`*.test.ts` + `*_test.py`）与 `ARCHITECTURE.md`「代码边界」表里的数字一致，由 `test/architecture-counts.test.ts` 从磁盘数出来钉住——加一个测试文件后若忘了改文档，那条会红。

## 常见问题 (FAQ)

- **测试在 Linux CI 上失败但本地通过？** 路径写死 Windows 形式的情况已出现过（见最近提交 `e71de25`），用 `path.join` 构造。
- **能否放宽断言让测试通过？** 不能。`docs/EXTENDING.md` 明确要求：静态检查被放宽时通常意味着真实变化。

## 相关文件清单

- `test/tool-registry.test.ts`
- `test/config-keys.test.ts`
- `test/cli-menu.test.ts`
- `test/fixtures/make_synthetic_nt.py`

## 覆盖说明

本次只列出文件清单，未读取测试正文。

## 变更记录 (Changelog)

- 2026-10-15：由 `/init-project` 首次生成。
- 2026-10-30：随上游 v1.21.4 同步；补充密钥缓存 / 数据库缓存 / PDF 笔记 / NT 附加库 / 表情解码五组新测试的说明。
