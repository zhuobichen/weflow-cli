# WeFlow CLI 操作手册

> 本文档帮助用户和 AI 助手快速上手、排查问题，覆盖初始化到日常使用全流程。

---

## 一、环境要求

| 依赖 | 版本/说明 | 检查命令 |
|------|-----------|----------|
| Node.js | ≥18 | `node -v` |
| Python | ≥3.9 | `python --version` |
| sqlcipher3 | NT 数据库解密 | `python -c "import sqlcipher3"` |
| pymem | NT 密钥扫描 | `python -c "import pymem"` |
| 微信 | 4.x（Weixin.exe） | `tasklist \| grep -i weixin` |

```bash
pip install sqlcipher3 pymem
```

---

## 二、初始化完整流程

**核心前提**：密钥提取通过 Hook 微信进程完成，**Hook 必须在微信登录之前安装**。

### 方式 A：标准流程（推荐）

```bash
# 1. 完全退出微信（右下角托盘 → 右键退出）
# 2. 运行 init，程序会等待微信进程
weflow-cli init

# 3. 看到 "请现在启动微信 4.x 并登录" 后，启动微信
# 4. 扫码登录 — Hook 在登录时自动捕获密钥
# 5. 看到 "密钥获取成功!" 即完成
```

### 方式 B：微信已在运行

如果微信已登录且不便重新登录，用 `dbkey` 从内存提取：

```bash
weflow-cli dbkey --timeout 120000

# 保存提取到的密钥
weflow-cli config set decryptKey <64位密钥>
```

> **注意**：`dbkey` 只能提取主密钥。NT 数据库（`message_0.db`）还需要独立的 key + salt（见第四节）。

---

## 三、连接数据库的 4 层优先级

`weflow-cli` 连接 4.x 数据库时，按以下顺序尝试：

| 优先级 | 方案 | 条件 | 状态 |
|--------|------|------|------|
| 1 | 预解密 `MSG0_decrypted.db`（纯 SQLite） | 文件已存在 | 通常不存在 |
| 2 | SQLCipher 解密 `MSG0.db` | 主密钥正确 | 4.x 新版已无此文件 |
| **3** | **NT 格式** `message_0.db`（Python sqlcipher3） | NT key + salt + Python 依赖 | **✅ 当前主路径** |
| 4 | WCDB API（降级） | 原生 WCDB 库 | 通常报 `-1006` |

**关键理解**：如果看到 `WCDB 初始化失败: -1006`，说明前 3 个方案全部失败，需检查 NT 密钥配置。

---

## 四、NT 密钥扫描

NT 数据库（`message_0.db`、`contact.db`）有独立的 **key + salt**，和主解密密钥不同：

```bash
# 前提：微信已登录、Python 已装 sqlcipher3 + pymem
python scripts/nt_decrypt.py scan --json
```

输出示例（JSON），找到对应数据库的 key/salt 后保存：

```bash
# message_0.db（主消息库）
weflow-cli config set ntKey <message_0的key>
weflow-cli config set ntSalt <message_0的salt>

# contact.db（联系人库，用于昵称/备注名映射）
weflow-cli config set contactKey <contact.db的key>
weflow-cli config set contactSalt <contact.db的salt>
```

---

## 五、常见错误速查

| 错误信息 | 根因 | 解决方案 |
|----------|------|----------|
| `WCDB 初始化失败: -1006` | 方案 1-3 都失败，WCDB 不可用 | 检查/重新扫描 NT 密钥（第四节） |
| `未找到会话` / `未找到联系人` | 数据库连接失败 | `config show` 检查密钥状态 |
| `获取密钥超时` | init 时微信已登录，Hook 装不上 | 用 `dbkey` 代替 init（方式 B） |
| `需要 sqlcipher3` | Python 缺依赖 | `pip install sqlcipher3 pymem` |
| `Weixin.exe 未运行` | pymem 未装或微信没启动 | `pip install pymem`，确认微信在运行 |
| `Python not found` | PATH 中没有 python | 安装 Python 并添加到 PATH |

---

## 六、Python 环境避坑

NT 方案通过 Node.js 调用 Python 脚本（`execFile('python', ...)`），使用系统默认 `python` 命令。

**常见问题**：系统有多个 Python/venv，默认 `python` 指向的 venv 没有安装依赖。

```bash
# 诊断：找到 python 的实际位置
which python

# 诊断：检查依赖
python -c "import sqlcipher3, pymem; print('OK')"

# 如果 import 失败，在当前 python 安装
pip install sqlcipher3 pymem

# 更可靠的做法：确认 python 和 pip 是同一个环境
python -m pip install sqlcipher3 pymem
```

---

## 七、密钥过期处理

微信版本更新后（或切换账号），密钥会失效，表现为所有查询返回空。

```bash
# 完整重新初始化
weflow-cli config show                 # 1. 查看当前状态

# 2. 完全退出微信 → weflow-cli init → 重新登录（捕获主密钥）

python scripts/nt_decrypt.py scan --json  # 3. 扫描 NT 密钥
weflow-cli config set ntKey <key>         # 4. 保存 NT key/salt
weflow-cli config set ntSalt <salt>
weflow-cli config set contactKey <key>    # 5. 保存 contact key/salt
weflow-cli config set contactSalt <salt>

weflow-cli contacts -k <已知联系人>        # 6. 验证
weflow-cli messages <联系人> -n 5
```

---

## 八、AI 助手排查清单

按顺序执行以下 6 步即可定位绝大多数问题：

```bash
# Step 1: 项目构建
npm run build

# Step 2: 查看当前配置（密钥是否已填）
weflow-cli config show

# Step 3: 微信是否在运行（dbkey / NT 扫描需要）
tasklist | grep -i weixin    # Windows

# Step 4: Python 依赖
python -c "import sqlcipher3, pymem; print('OK')"

# Step 5: NT 数据库能否解密（直接用 Python 测试）
python scripts/nt_decrypt.py sessions \
  --db "<NT数据库路径>" \
  --key "<ntKey>" --salt "<ntSalt>"
# 成功 → 返回 JSON 会话列表

# Step 6: CLI 最终验证
weflow-cli sessions
weflow-cli contacts -k <部分昵称>
```

---

## 九、每日使用

```bash
# 查看所有会话
weflow-cli sessions

# 搜索联系人
weflow-cli contacts -k <关键词>

# 查看最近消息
weflow-cli messages <联系人或群名> -n 50

# 导出聊天记录
weflow-cli export <联系人> html
weflow-cli export <联系人> json

# 生成月报
weflow-cli report --month 2026-05 --talker <联系人>

# 初始化 Obsidian Vault
weflow-cli vault init

# 公众号日报
python scripts/biz_daily.py --api-key <DeepSeek-key>
python scripts/classify_daily.py --api-key <DeepSeek-key> --interest AI
```

---

## 十、用户自定义脚本

`scripts/user/` 目录可供用户存放自定义脚本（如查询特定联系人的对话），该目录已加入 `.gitignore`，不会被提交。

```bash
# 示例：创建自己的查询脚本
cat > scripts/user/my_query.py << 'EOF'
# 你的自定义查询逻辑
import subprocess, json, sys
# ...
EOF

python scripts/user/my_query.py
```

---

## 十一、Obsidian Vault 集成

### 初始化 Vault

```bash
weflow-cli vault init                    # 默认 output/wechat-vault/
weflow-cli vault init --path ~/my-vault  # 自定义路径
```

生成的结构：
```
wechat-vault/
├── .obsidian/app.json       # Obsidian 基础配置
├── .gitignore
├── README.md                # Vault 总索引（含 Dataview 查询示例）
├── Templates/article.md     # 文章模板
├── Sources/WeChat/          # 公众号文章（按日期+主题）
├── Wiki/Concepts/           # AI 生成的概念页
├── Wiki/Entities/           # 实体页（公众号、作者）
└── Wiki/Topics/             # 主题总览页
```

### 在 Obsidian 中打开

1. 安装 [Obsidian](https://obsidian.md/)
2. `File → Open Vault` → 选择 `output/wechat-vault/` 目录
3. 安装推荐插件：**Dataview**（表格查询）、**Graph View**（知识图谱）

### 日常工作流

```bash
# 1. 生成日报（文章自动带 Frontmatter + Wiki Links）
python scripts/biz_daily.py --api-key <key>

# 2. 后处理（广告清洗 + AI 深度摘要 + 更新 Frontmatter）
python scripts/classify_daily.py --api-key <key> --interest AI

# 3. 复制到 Vault
cp -r output/biz-daily/YYYY-MM-DD/* output/wechat-vault/Sources/WeChat/YYYY-MM-DD/
```

### Dataview 查询示例

```dataview
TABLE date, topic, tags
FROM "Sources/WeChat"
WHERE topic = "AI"
SORT date DESC
```

### Frontmatter 格式

每篇文章自动带 YAML 元数据：

```yaml
---
title: "文章标题"
source: "公众号名称"
date: 2026-05-15
topic: AI
tags: [AI, Agent, 开源]
url: "https://mp.weixin.qq.com/s/xxx"
created: 2026-05-15
---
```

文末自动生成 `[[Wiki Links]]` 概念链接，可在 Graph View 中作为节点展示。

---

## 十二、概念图谱编译

将 Phase 1 生成的 `[[Wiki Links]]` 编译为 Wiki 概念页，在 Obsidian Graph View 中形成真正的知识节点。

```bash
# 扫描所有文章 wikilinks → 聚合 → AI 生成概念页
weflow-cli wiki compile --limit 20 --api-key <key>

# 或直接调用 Python
python scripts/compile_wiki.py --api-key <key> --limit 20 \
  --source output/biz-daily \
  --output output/wechat-vault/Wiki/Concepts
```

生成的每个概念页包含：
- **定义** — 1-2 句话精确定义
- **关键要点** — 3 条简洁摘要
- **相关概念** — `[[wikilinks]]` 到其他概念
- **来源** — 引用该概念的所有文章

```bash
# 查看概念索引
cat output/wechat-vault/Wiki/00-Overview.md
```

---

## 十三、端到端流水线

```bash
# 一键跑完 biz_daily → classify → wiki compile
weflow-cli pipeline run --api-key <key> [--date 2026-05-15]

# 或直接 Python
python scripts/pipeline.py --api-key <key> --interest AI --wiki-limit 20
```

## 十四、AI 日报生成

```bash
# 基于今日文章生成学习日报
python scripts/generate_review.py --api-key <key> [--date 2026-05-15]

# 输出: output/reviews/Daily/Daily-YYYY-MM-DD.md
```

## 十五、GitHub 自动同步

```bash
# 设置远端仓库
weflow-cli config set vaultRepo git@github.com:user/wechat-knowledge.git

# 增量同步
weflow-cli vault sync
# 或指定: weflow-cli vault sync --repo <url> --branch main
```

## 十六、多 AI 引擎切换

```bash
# 切换到 Claude
weflow-cli config set aiEngine claude

# 切换到本地 Ollama
weflow-cli config set aiEngine ollama

# Python 脚本也支持：
python scripts/biz_daily.py --api-key <key> --engine claude
```

---

## 附录

### 关键路径

| 数据 | 路径 |
|------|------|
| 配置 | `~/.weflow-cli/config.json` |
| 4.x 数据 | `C:\Users\<用户名>\xwechat_files` |
| NT 消息库 | `<xwechat>\<wxid>\db_storage\message\message_0.db` |
| NT 联系人库 | `<xwechat>\<wxid>\db_storage\contact\contact.db` |
| 3.x 消息库 | `Documents\WeChat Files\<wxid>\Msg\Multi\MSG0.db` |
| 公众号库 | `<xwechat>\<wxid>\db_storage\message\biz_message_0.db` |

### 密钥安全

- 密钥用 `机器名+用户名` PBKDF2 派生，AES-256-GCM 加密存储
- 绑定单机，其他电脑无法解密
- 配置中 `lock:` 前缀表示已加密字段

### 限制 & 注意

- NT 图片：HTML 导出中图片来自缩略图缓存 + 公众号封面图远程下载；公众号文章通过 `data-src` 懒加载提取，图片覆盖率显著提升
- DeepSeek V4：推理模型需 `max_tokens ≥ 500`（含 reasoning_tokens），否则输出为空
- 公众号抓取：8-12s 随机间隔，防止触发 WAF
- 消息收发：ilink API 是实验性功能，需要扫码登录
