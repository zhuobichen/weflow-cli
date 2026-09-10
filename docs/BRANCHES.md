# 分支与合并记录

本文件记录分支的用途、合并时间与合并内容，以及并行开发时的对齐约定。
每次把工作分支并入 `master` 后，请在「合并记录」中追加一行。

## 分支一览

| 分支 | 用途 | 状态 |
| --- | --- | --- |
| `master` | 主线，随时可发布 | 受保护 |
| `trae/*` | 早期 AI 协作产生的分支 | 部分已合并，部分搁置 |
| `draft/*` | 探索性草稿，允许与主线脱节 | 按需清理 |
| `feat/*`、`fix/*` | 从**当前** `master` 切出的工作分支 | 合并后删除 |

> **约定**：`feat/*` 与 `fix/*` 必须从最新的 `master` 切出。`draft/*` 无此要求，
> 但**合并前必须先把 `master` 合进来**（见下方「并行开发」）。

## 合并记录

| 日期 | 来源分支 | 目标 | 方式 | 说明 |
| --- | --- | --- | --- | --- |
| 2026-06-30 | `trae/solo-agent-Xl3Lhb` | `master` | 合并提交 `bc44e93` | 优化消息与朋友圈功能 |
| 2026-09-01 | `Mandark6/master` | `master` | PR #6，合并提交 `2d3005c` | 外部贡献 |
| 2026-09-10 | `draft/weflow-cli-improvements` | — | **未合并** | 见下方「2026-09-10」条目 |

### 2026-09-10 · `draft/weflow-cli-improvements`

该分支产出了导出链路的若干增强，但**基于过期的 `master`**（共同祖先 `cbec736`），
当时 `master` 已领先 78 个提交。因此**没有直接合并**，改为把其中的独有功能移植到
从最新 `master` 切出的 `feat/export-enhancements`。

分支仍保留在本地与远端，标签 `backup/my-export-work` 指向其顶端 `c181afb`。

移植与放弃的清单：

| 项 | 处理 | 原因 |
| --- | --- | --- |
| 跨分片读取 | **放弃** | `master` 已有 `fetch_messages_from_shards` 等 |
| 主密钥派生 | **放弃** | `master` 已有 `derive_database_key` |
| 图片按 `(local_id, create_time)` 配对 | **放弃** | `master` 已有 |
| `.dat` 图片解密与资源映射 | **放弃** | `master` 已有 `decode_wechat_v2`、`load_resource_media_map` |
| zstd 解压、CDATA 解析、名片渲染 | **放弃** | `master` 已有 |
| 会话列表改读 `session.db` | 待定 | 需与 `master` 现有实现比对 |
| **微信原版表情图（109 张）** | **移植** | `master` 仅 9 张 |
| **表情按 CSS 类去重** | **移植** | `master` 每个出现都重复内嵌 base64 |
| **`--per-page` 按条数分页** | **移植** | `master` 只有 `--parts`（按份数） |
| **表情包本地解密** | **移植** | `master` 走 CDN，需联网 |
| **图片缩放管线** | **移植** | `master` 按原始尺寸内嵌 |

## 并行开发

本次出现的问题：本地克隆停在 `cbec736`，之后主线积累了 78 个提交，导致在旧基线上
从零实现了主线已有的功能，白做一遍。

**动手前先对齐：**

```bash
git fetch origin
git status -sb          # 看 ahead / behind
git log --oneline HEAD..origin/master | wc -l    # 主线领先多少
```

若 `behind` 不为 0，先 `git checkout master && git pull --ff-only`，再切工作分支。

**合并前自检：**

```bash
git fetch origin
git log --oneline HEAD..origin/master            # 应尽量为空
git merge-tree --write-tree origin/master HEAD   # 预看冲突
```

## 强推与清理

- **禁止对 `master` 强推。** 若推送被拒，一律先 `git fetch` 查明主线新增内容。
- 工作分支合并后应删除本地与远端副本；`draft/*` 保留至内容确认无用为止。
