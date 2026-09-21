# WeFlow CLI 跨电脑部署指南

本指南用于在新电脑部署当前源码。它不迁移数据库、密钥或聊天导出；每台电脑都应在本机重新配置，并只访问用户本人有权访问的数据。

## 前置条件

- Windows 10/11 x64 是当前重点验证平台；Node.js 22.13+、Python 3.10+。
- 已安装并登录兼容的微信版本。微信数据库格式和平台行为可能随版本变化。
- 足够的磁盘空间保存本地日报或导出副本。

## 源码安装

```powershell
git clone https://github.com/zhuobichen/weflow-cli.git
cd weflow-cli
npm install
npm run build
python -m pip install -r requirements.txt
weflow-cli check
```

开发时使用源码入口：

```powershell
npm run dev -- check
npm run dev -- sessions
```

`npm install -g weflow-cli` 使用 npm 发布包；发布包和 GitHub `master` 的发布时间可能不同。需要确认功能版本时，查看 `package.json`、Git 提交和 `npm view weflow-cli version`，不要假定两者相同。

## 初始化

```powershell
weflow-cli init
```

首次初始化时按 CLI 提示完成微信登录。已有有效配置会先验证并复用。数据在自定义目录时：

```powershell
weflow-cli init --path "D:\WeChatData"
weflow-cli init --search-drives
weflow-cli init --full-scan
```

推荐顺序是显式路径、跨磁盘标准目录搜索、深度结构搜索。不要为了排障把真实配置、数据库或密钥复制到仓库。

换账号、换版本或访问失败时：

```powershell
weflow-cli init --refresh
```

完成后用 `sessions`、`contacts` 和一条最小范围的 `messages` 验证，不要直接批量导出。

## Python 环境

Node CLI 会调用当前 PATH 中的 Python。若安装了多个 Python，必须在同一个解释器中安装和检查依赖：

```powershell
python --version
python -c "import sqlcipher3, cryptography, html2text, zstandard; print('OK')"
python -m pip install -r requirements.txt
```

旧版 3.x 数据另装：

```powershell
python -m pip install -r requirements-3x.txt
```

## 日报与阅读器

```powershell
weflow-cli daily --no-ai
weflow-cli daily-server --date YYYY-MM-DD --open
```

持久化关闭日报 AI：

```powershell
weflow-cli config set dailyAiEnabled false
```

日报输出和阅读器均为本地文件/回环服务。阅读器默认地址是 `http://127.0.0.1:8765/`，不要把端口暴露到局域网或公网。

## 配置与隐私

配置默认位于用户目录下的 `.weflow-cli/config.json`。路径由程序决定，不要在文档或 Issue 中写入真实用户目录。数据库访问密钥由本机绑定方式加密保存；换电脑不能依赖复制密文，应重新初始化。

以下内容禁止提交：数据库、导出聊天、日报正文、`.env`、MCP 配置中的密钥、账号标识、完整日志和真实截图。MCP 客户端配置前确认其工作目录和权限；云端 AI 只处理明确选择的内容，敏感聊天优先用本地模型。

日报出网的对象有两个：生成摘要的 LLM（DeepSeek 或你配置的 OpenAI 兼容端点），以及配置了 `typesafeApiKey` 之后判断文章主题与相关度的 TypeSafe Jev（`api.typesafe.ai`）。两者都会看到文章标题与正文。不需要任何出网时用 `weflow-cli daily --no-ai`。

## 最小验收

```powershell
weflow-cli check
weflow-cli sessions -n 3
weflow-cli contacts -k "关键词"
weflow-cli messages "联系人A" -n 3
weflow-cli daily --date YYYY-MM-DD --no-ai
weflow-cli daily-server --date YYYY-MM-DD --open
```

问题反馈只提供操作系统、Node/Python 版本、微信版本、命令和脱敏错误。不要提供密钥、数据库、wxid、聊天正文或完整本地路径。
