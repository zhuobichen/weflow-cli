# WeFlow CLI 操作与排障手册

本文面向用户和维护 Agent。所有命令都应在项目目录或已安装 CLI 的终端中执行；示例中的路径、联系人和密钥均为占位符。

## 1. 环境

| 依赖 | 用途 | 检查 |
| --- | --- | --- |
| Node.js 18+ | CLI、MCP、构建 | `node --version` |
| Python 3.10+ | NT 数据、日报、阅读器 | `python --version` |
| `requirements.txt` | 标准 4.x 工作流 | `python -m pip install -r requirements.txt` |
| `requirements-3x.txt` | 旧版 3.x 数据，可选 | `python -m pip install -r requirements-3x.txt` |

先运行：

```powershell
weflow-cli check
```

源码开发使用：

```powershell
npm install
npm run build
npm run dev -- check
```

## 2. 初始化与已有配置

首次使用：

```powershell
weflow-cli init
```

初始化会发现常见数据位置、识别账号目录并验证本地数据库访问。默认优先复用已验证配置；配置有效时不会重复初始化。迁移、切换账号或访问失败时显式刷新：

```powershell
weflow-cli init --refresh
```

数据目录不在常见位置时按成本递增尝试：

```powershell
weflow-cli init --path "D:\WeChatData"
weflow-cli init --search-drives
weflow-cli init --full-scan
```

`--path` 可以指向微信数据根目录、账号目录或其 `db_storage` 目录。全盘结构搜索可能耗时较长，且会枚举更多本地目录，只有前两种方式找不到时再使用。

测试“密钥缺失但不破坏当前配置”的首次初始化流程：

```powershell
weflow-cli init --test-missing-keys
```

确认密钥确实需要重新获取时，才使用：

```powershell
weflow-cli config forget-keys --yes
weflow-cli init --refresh
```

## 3. 数据读取验证

```powershell
weflow-cli config show
weflow-cli sessions -n 10
weflow-cli contacts -k "关键词"
weflow-cli messages "联系人A" -n 10
```

看到 `WCDB 初始化失败: -1006` 时，先不要反复登录或删除全部配置。先运行 `check`，确认 Python、数据路径和 NT 数据库状态；然后检查 `config show` 是否只显示“已设置”，不要把密钥复制到 Issue 或日志中。若配置失效，再按第二节执行 `init --refresh`。

## 4. 导出聊天记录

```powershell
weflow-cli export "联系人A" html --output ./output
weflow-cli export "联系人A" json --output ./output
```

支持 `json`、`txt`、`md`、`html` 和 `excel`。HTML 导出会尽力匹配本地图片、表情、公众号卡片和其他媒体；匹配不到时应显示类型或占位信息，不应从其他会话猜图。跨分片数据尤其要保留原始导出和完整上下文，导出后请人工抽查发送者、时间和媒体对应关系。

## 5. 公众号日报与阅读器

生成今天的日报：

```powershell
weflow-cli daily
```

关闭本次运行的全部 AI：

```powershell
weflow-cli daily --no-ai
```

无日期运行会先补齐昨天的不完整产物；指定日期只处理该日期：

```powershell
weflow-cli daily --date YYYY-MM-DD --no-ai
```

只处理指定来源：

```powershell
weflow-cli daily --source "公众号A" --source "公众号B"
weflow-cli daily --source "公众号A,公众号B"
```

仅预览来源文章，不写日报、不调用 AI：

```powershell
weflow-cli daily --dry-run
```

持久化来源和 AI 设置：

```powershell
weflow-cli config set dailySources "公众号A,公众号B"
weflow-cli config set dailyAiEnabled false
weflow-cli config set dailyAiEnabled true
weflow-cli config show
```

来源类别配置使用 JSON；值可以是公众号名称或稳定来源 ID：

```powershell
weflow-cli config set dailySourceCategories '{"公众号A":"新闻","公众号B":"政治"}'
```

启动指定日期阅读器：

```powershell
weflow-cli daily-server --date YYYY-MM-DD --open
```

默认地址为 `http://127.0.0.1:8765/`。阅读器是本地服务，已生成的日报可离线阅读；文章正文、封面或图片的抓取可能需要联网。

查看来源阅读频率：

```powershell
weflow-cli daily-stats --days 30 --limit 30
```

## 6. AI 和助手

日报关闭 AI 不会自动关闭其他命令的 AI。报告、RAG、助手和证据线索分析分别按命令参数和配置决定是否调用模型。云端分析前应确认输入范围、供应商和隐私设置；优先使用本地模型处理聊天正文。

助手基础配置：

```powershell
weflow-cli config set aiEngine ollama
weflow-cli login-wechat
weflow-cli assistant start
weflow-cli assistant status
```

助手默认拒绝所有发送者，需明确设置 `assistantWhitelist`。群聊还需要群白名单、成员白名单和 @ 门槛；项目不通过客户端自动化或非官方协议拉群。

## 7. 常见问题

| 现象 | 处理 |
| --- | --- |
| 找不到数据目录 | 先 `init --path`，再 `--search-drives`，最后 `--full-scan`。 |
| `Python not found` | 安装 Python 并确保当前 PowerShell 能执行 `python --version`。 |
| 缺少 `sqlcipher3` 等依赖 | 用同一个 Python 执行 `python -m pip install -r requirements.txt`，再 `weflow-cli check`。 |
| `WCDB ... -1006` | 检查 NT 配置和数据库路径，不要只看 WCDB 降级信息；必要时刷新初始化。 |
| 日报缺昨天 | 无日期运行 `daily` 会检查并补齐昨天；补齐失败会停止今天，不覆盖失败状态。 |
| `daily --dry-run` 不识别 | 确认使用的是当前源码或最新发布包；当前源码支持该参数，旧 npm 包可能落后。 |
| 阅读器打不开 | 使用 `daily-server --date YYYY-MM-DD --open`，不要直接双击 `file://` 页面。 |
| 图片或表情串错 | 保留原始数据库和导出日志，反馈脱敏后的消息类型、版本和最小复现；不要提交真实媒体。 |

## 8. 安全排查原则

不要在 Issue、PR、截图或共享日志中包含数据库、密钥、token、账号 ID、聊天正文或完整本地路径。公开报告只需操作系统、Node/Python 版本、微信版本、命令和脱敏错误。安全漏洞按 `SECURITY.md` 私下报告。

## 9. 维护验证

```powershell
npm run build
npm test
python -m unittest discover -s test -p '*_test.py' -v
git diff --check
```

测试优先使用合成数据。涉及真实账号时只做最小范围读取，并在完成后关闭本地服务和清理临时导出。
