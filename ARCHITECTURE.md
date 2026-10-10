# WeFlow CLI 技术架构

> 本文描述当前代码的边界和数据流。维护时先以 `bin/weflow-cli.ts`、`src/` 和 `scripts/` 为准，再更新本文。

## 定位

WeFlow CLI 是一个本地优先的命令行工具、MCP Server（**stdio 与回环 HTTP 两种传输**）和一个常驻的**本机助手面板**，面向用户本人有权访问的微信本地数据，提供查询、导出、公众号阅读、知识整理、概念图谱和可选 AI 助手能力。

## 分层结构

```text
MCP 客户端 / 终端用户 / 本机面板（悬浮球，只读客户端）
        |            |                     |
        v            v                     v  两条入口连的是同一个大脑
CLI 入口 bin/weflow-cli.ts     mcp-server/index.ts     助手守护进程
        |            |         127.0.0.1:8766（token + 一次性配对码）
        +------------+                     |
                     v                     |
业务服务层  src/services/  <---------------+
  chat / contact / export / evidence / favorites / sns
  daily / vault / weread / assistant / config / privacy
  assistantTools.ts ==== **助手与 MCP 共用同一张工具表**
                     |
          +----------+---------------------------+
          |                                      |
          v                                      v
Node 原生层 src/core/                    Python 工作流 scripts/
  路径发现、配置、数据库适配、消息通道        NT 数据、日报、知识卡、概念页、图谱页
                     |
                     v
本地数据与明确选择的外部服务
  微信本地数据库、缓存、日报输出、Vault、概念图谱页面
  公众号网页、微信读书、用户配置的 AI 端点、官方 Bot 通道
```
```text
回环端口（都只绑 127.0.0.1）：
  8765  日报阅读器      无 token
  8766  面板 / 助手     每个请求都要 token（含 GET /api/status）
  8790  MCP HTTP        强制 Bearer，含 DNS-rebinding 防护
```

## 代码边界

| 目录 | 职责 |
| --- | --- |
| `bin/` | Commander 命令定义、参数校验和交互式菜单（113 处 `.command(`，含子命令）。 |
| `src/core/` | 数据目录发现、数据库连接、密钥配置适配和客户端底层集成。 |
| `src/services/` | 聊天、导出、证据包、日报、助手、白名单和隐私策略。`assistantTools.ts` 是助手与 MCP **共用**的工具表（31 个工具）。 |
| `src/panel/` | 本机面板的入口：回环 HTTP 服务（`127.0.0.1:8766`，每进程一个随机 token，写 `~/.weflow-cli/assistant_endpoint.json`）、Electron/Edge 启动决策、CLI 侧客户端。 |
| `scripts/` | 59 个 Python 工作流（约 2.8 万行）：NT 数据读取与解密、知识卡（文章 / 聊天 / 收藏 / 自写笔记）、概念页编译与体检、日报与报告、搜索与 RAG、图谱页面、判断层与评测。 |
| `mcp-server/` | 用**两种传输**暴露受控工具：stdio（默认）与 Streamable HTTP（`--http`，回环 `127.0.0.1:8790`，强制 Bearer token，含 DNS-rebinding 防护）。两种传输的工具表相同。 |
| `test/` | Node 和 Python 回归测试（118 个文件：80 TS / 38 Python），优先使用合成数据。 |
| `resources/` | 随包发布的只读资源：Electron 面板应用与吉祥物素材、`wcdb` / `wx_key` 原生 DLL、`js/graph3d`（2D/3D 图谱页面用的库，版本与许可记在同目录 `NOTICE.txt`）。 |

## 主要数据流

### 聊天查询与导出

```text
用户指定的数据根目录
  -> 分片数据库发现与配置验证
  -> 聊天/联系人查询
  -> 导出 JSON、TXT、Markdown、HTML 或 Excel
  -> HTML 按稳定消息身份匹配媒体，无法确认时保留占位符
```

4.x 数据可能包含多个 `message_*.db` 分片，以及联系人、朋友圈、收藏和媒体资源库。跨分片查询时不能把本地消息 ID 当作全局唯一值；媒体关联优先使用稳定的服务端消息身份，并结合内容指纹和资源记录。这个策略用于避免把别的消息图片串到当前聊天中。

### 公众号日报与阅读器

```text
本地公众号推送数据
  -> 按来源筛选
  -> 抓取用户选择的文章正文（需要联网）
  -> AI 摘要/分类（可关闭）
  -> output/biz-daily/YYYY-MM-DD/
  -> 127.0.0.1:8765 本地阅读器
```

来源类别优先于文章主题分类：已配置类别的公众号保留其类别，并跳过该文章的自动主题分类；未配置类别时才使用文章分类逻辑。`daily --no-ai` 或持久化配置 `dailyAiEnabled=false` 时不调用 AI，但仍可抓取、生成 HTML 和更新本地索引。

未指定日期运行 `daily` 时，会先检查昨天是否缺少 `README.md`、`.articles.json` 或 `index.html`；昨天不完整则先补齐，成功后再生成今天。显式 `--date` 和 `--dry-run` 是单日期操作。

### 知识库与报告

```text
日报 / 收藏 / 微信读书笔记 / 聊天卡 / 你自己的笔记
  -> output/*-notes（卡片，四条来源各自产出，形状相同）
  -> Vault：**只有显式加 --with-vault / --with-wiki 才写**
       Sources/ 原始素材 + 001_Daily + 002_Literature 阅读笔记
  -> 两个概念页目录，**两条线各自的命名空间**（同名可以各有一张页；图谱主键是 `线:名字`）：
       Wiki/Concepts（文章线）、Chat/Concepts（聊天线）
  -> 本地搜索或可选 AI RAG
  -> 概念图谱页面：wiki graph（自包含 2D/3D HTML，坐标缓存 output/.graph3d-cache/）
  -> review、report、annual-report、todos 等报告
```

**默认不写 Obsidian 库**：Obsidian 自己的关系图谱是活的，库文件一变它就重建，所以"内容一更新图谱就重建"其实是库被重写了。于是 `daily` 只有拿到 `--with-vault`（拷贝当天内容）或 `--with-wiki`（再编概念页）时才动 Vault，由用户自己说什么时候重建。

Vault promotion 默认不调用 AI；只有显式使用 `--with-ai` 并提供凭据时才生成 AI 内容。

### 助手与 MCP

```text
MCP（stdio 或回环 HTTP） / 本机面板 / 官方 Bot 通道
  -> 白名单与隐私策略
  -> 受控工具调用（助手与 MCP 共用 assistantTools.ts 那张表）
  -> 本地数据查询 / 可选云端模型
  -> 脱敏后的回复或工具结果
```

MCP 客户端本身是可信调用方，配置前必须审查其权限和工作目录。**MCP 的工具面由助手的工具表派生**（少掉 `save_memory`、`look_at_image`、`set_todo_status` 三个），所以它**不是纯只读**：写入类调用要显式 `confirm`，读取类里有几个会出网（抓正文、公开文章搜索、语义检索、起草）。**发送消息、发布文章、改配置这三件事在两侧都没有工具**——`weflow-cli capabilities --json` 的 `safety.mcpDefaultReadOnly` 是这条边界的机器可读版本。助手默认拒绝所有来信；`send` 仅用于已建立的官方 Bot 通道会话，不等于操控个人微信账号，也不能向普通个人联系人或群聊发消息。

### 本机面板与助手守护进程

```text
weflow-cli assistant start
  -> 分离进程 weflow-cli assistant run --yes
       -> AssistantService.start()
            -> 可选：微信 Bot 通道长轮询（没有 token 就退化成纯本地）
            -> startPanelServer()  绑 127.0.0.1:8766，随机 token
                 写 ~/.weflow-cli/assistant_endpoint.json
weflow-cli panel
  -> 读 endpoint 文件（没起守护进程就先起）
  -> 起 Electron（resources/panel/，**不带任何参数，凭证不进 argv**）
     或 Edge/Chrome --app + 一次性配对码换 HttpOnly cookie
  -> 窗口里的 renderer.js 走同一个端口；窗口本身**只读不写**
```

面板与微信内问的是**同一个大脑**：同一份记忆、同一条每日配额。所以它是客户端而不是第二个后端——两个入口不会各写一份记忆。配对码只用来换 cookie，token 由服务端生成、写在只有本机用户可读的 endpoint 文件里。

## 支持与限制

- 当前重点验证 Windows 微信 4.x；Linux 可使用相应 NT 数据路径；macOS 不提供自动初始化，需用户自行提供合法的本地访问凭据。
- 数据库格式、微信版本、官方通道和账号策略可能变化；本地运行不保证兼容性、账号安全或法律结果。
- 图片和表情导出会尽力使用本地缓存、媒体资源记录和消息提供的 URL。没有可靠身份或原始资源时不会猜测其他图片，而是显示占位信息。
- 三个本地 HTTP 入口都只绑回环地址，默认不是局域网服务：阅读器 8765（无 token）、面板 8766（每请求 token）、MCP HTTP 8790（Bearer）。要从别的机器连，走 SSH 隧道或自己的反向代理。
- 云端 AI、文章抓取、微信读书和发布草稿都可能产生网络请求；本地推理可避免对应的正文出网，但仍应审查客户端和模型配置。

## 维护规则

功能、平台、限制或验证状态变化时更新 `docs/PROJECT_STATE.md`；影响安全、数据流或兼容性的取舍写入 `docs/DECISIONS.md`；用户可见变化才写入 `CHANGELOG.md`。
