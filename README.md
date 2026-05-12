# WeFlow CLI

微信聊天记录命令行查询与导出工具。无需启动 GUI，直接通过命令行操作微信聊天数据。

> **依赖**：需要 Electron 运行时来满足 `wcdb_api.dll` 的安全检查。已通过 wrapper 脚本自动处理。

## 功能

- ✅ 自动检测微信数据目录
- ✅ 从运行中的微信进程自动提取数据库解密密钥
- ✅ 查看会话列表（支持关键词搜索）
- ✅ 查询指定用户的聊天记录
- ✅ 查看联系人列表
- ✅ 导出聊天记录（JSON/TXT/HTML/Excel）

## 安装

```bash
npm install
npm run build
```

## 使用

### 1. 初始化

首次使用需要初始化，自动检测微信数据目录并提取解密密钥：

```bash
npm run dev -- init
```

确保微信 4.x 已登录且正在运行。

### 2. 查看会话列表

```bash
npm run dev -- sessions
npm run dev -- sessions -k "关键词"
```

### 3. 查询消息

```bash
npm run dev -- messages <talker> -n 50
```

### 4. 查看联系人

```bash
npm run dev -- contacts
npm run dev -- contacts -k "关键词"
```

### 5. 导出聊天记录

```bash
# 导出为 JSON
npm run dev -- export <talker> json

# 导出为 TXT
npm run dev -- export <talker> txt

# 导出为 HTML
npm run dev -- export <talker> html

# 导出为 Excel
npm run dev -- export <talker> excel

# 指定输出目录
npm run dev -- export <talker> json -o ./my-output
```

### 6. 其他命令

```bash
# 查看当前配置
npm run dev -- config show

# 手动设置配置
npm run dev -- config set dbPath <path>
npm run dev -- config set decryptKey <key>

# 提取数据库密钥
npm run dev -- dbkey

# 扫描微信账号
npm run dev -- scan
```

## 配置

配置文件存储在 `~/.weflow-cli/config.json`，包含：

- `dbPath`: 微信数据目录路径
- `wxid`: 微信账号 ID
- `decryptKey`: 数据库解密密钥（自动加密存储）

## 技术栈

- TypeScript
- Commander.js (CLI 框架)
- koffi (FFI 调用原生库)
- Electron (运行时环境)
- ExcelJS (Excel 导出)

## 依赖的原生库

- `wcdb_api.dll`: WCDB 数据库访问库
- `wx_key.dll`: 微信密钥提取库

这些原生库从 [WeFlow](https://github.com/hicccc77/WeFlow) 项目复制。

## 注意事项

- 仅支持 **微信 4.x** 数据格式（`xwechat_files` 目录）
- 需要微信 4.x 已登录才能访问聊天数据
- 密钥提取功能需要微信进程正在运行

## 许可证

MIT
