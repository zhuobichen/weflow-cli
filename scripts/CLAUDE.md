[根目录](../CLAUDE.md) > **scripts**

# scripts - Python 工作流

## 模块职责

耗时或依赖 Python 生态的工作流：NT 数据读取与解密、日报与报告、知识卡（文章 / 聊天 / 收藏 / 自写笔记）、概念页编译与体检、图谱页面、RAG 与语义搜索、面板立绘帧校验。

约 79 个 `.py` 文件，另有 3 个 TypeScript 与 2 个 MJS 辅助脚本（`assistant_eval.ts`、`assistant_route_probe.ts`、`capture_favorite_schema.ts`、`graph3d_check.mjs`、`json_surface_probe.mjs`）等探针。

## 入口与启动

由 `src/services/pythonBridge.ts` 调用，不直接作为用户入口。手动运行时注意编码：`sys.stdout.reconfigure(encoding='utf-8', errors='replace')` 要求见 `docs/EXTENDING.md` Recipe E。

## 对外接口

- 每个脚本对应一个或多个 CLI 命令，映射关系在 `bin/` 中。
- 通过 JSON 输出的脚本使用 `runPythonJson`；失败必须非零退出或返回 `success: false`。
- 状态通过 stdin 或环境变量传入，不经 argv（见 Recipe E）。

## 关键依赖与配置

- `requirements.txt`、`requirements-3x.txt`、`requirements-voice.txt`（根目录）。
- 公共工具：`_utils.py`（`KNOWLEDGE_LINES`、`LINE_LABELS`、`LinkNamer`），`nt_common.py`（NT 数据通用逻辑）。
- 图谱页面库位于 `resources/js/graph3d/`，两处库列表需同步（见 Recipe E）。

## 数据模型

- 知识卡格式由生产者写出、`compile_wiki.scan_articles` 读取，契约表见 `docs/EXTENDING.md` Recipe F。
- 概念页目录：`Wiki/Concepts`（文章线）与 `Chat/Concepts`（聊天线）。

## 测试与质量

- Python 测试位于 `test/*_test.py`，运行：`python -m unittest discover -s test -p '*_test.py'`。
- `test/script_stdout_encoding_test.py`：静态检查 stdout 与文本 I/O 编码。
- `test/chat_notes_test.py`、`test/compile_wiki_test.py`：生产者与消费者真实联调。
- `test/config_keys_declared_test.py`、`test/contact_schema_test.py` 等。
- `test/key_cache_test.py`、`test/key_cache_integration_test.py`、`test/key_cache_e2e_test.py`：密钥缓存（v1.19.1 起，第二次查询不再扫内存）。
- `test/database_cache_test.py`：数据库缓存（默认关闭，需显式开启）。
- `test/pdf_notes_test.py`、`test/pdf_index_test.py`：PDF 笔记与 OCR 流水线（v1.20 起）。
- `test/wechat_extras_test.py`：此前无人读取的 NT 库（general / session / emoticon / solitaire / bizchat）。
- `test/wechat_emoticon_test.py`：表情解码，seed 从配置 → 日志 → 内存三级解析（v1.21.3 起不再要求微信在运行）。

## 常见问题 (FAQ)

- **脚本手动运行输出乱码或 `UnicodeEncodeError`？** 本机 `PYTHONIOENCODING=utf-8` 会掩盖用户环境的 GBK 问题，见 Recipe E。
- **新增知识源？** 按 Recipe F 写生产者，并通过 `_utils.KNOWLEDGE_LINES` 与 `test/concept-dirs-agreement.test.ts` 注册。

## 相关文件清单

- `scripts/_utils.py`
- `scripts/nt_common.py`
- `scripts/compile_wiki.py`
- `scripts/chat_notes.py`
- `scripts/graph_3d.py`
- `scripts/panel_frames.py`
- `scripts/key_cache.py`（密钥缓存）
- `scripts/database_cache.py`、`scripts/manage_cache.py`（数据库缓存）
- `scripts/pdf_notes.py`（PDF 笔记 / OCR）
- `scripts/wechat_extras.py`（NT 附加库）
- `scripts/wechat_emoticon.py`（表情解码）
- `scripts/data/`（数据子目录，本次未读取内容）

## 覆盖说明

本次仅列出文件清单与 `docs/EXTENDING.md` 中的约定，未读取任何脚本正文。`scripts/data/` 未检查。

## 变更记录 (Changelog)

- 2026-10-15：由 `/init-project` 首次生成。
- 2026-10-30：随上游 v1.21.4 同步；`.py` 计数由约 60 更新为 79，补充五个新脚本与对应测试。
