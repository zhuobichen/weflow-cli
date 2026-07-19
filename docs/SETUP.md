# WeFlow CLI — 跨电脑部署指南

从零开始，在一台新 Windows 电脑上搭建公众号日报环境。

## 前置条件

| 项目 | 要求 | 验证方式 |
|------|------|---------|
| 操作系统 | Windows 10/11（DLL 仅支持 win32 x64） | — |
| 微信 | 4.x 已安装并登录 | 任务管理器能看到 `WeChat.exe` |
| Node.js | ≥ 18 | `node -v` |
| Python | ≥ 3.10 | `python -V` |
| Git | 任意版本 | `git --version` |

## 第一步：克隆项目 & 安装依赖

```bash
git clone https://github.com/zhuobichen/weflow-cli.git
cd weflow-cli
npm install
```

## 第二步：安装 Python 依赖

```bash
pip install sqlcipher3 requests beautifulsoup4 markdownify cryptography
```

> ⚠️ `sqlcipher3` 是 C 扩展，如果 `pip install` 编译失败，尝试：
> ```bash
> pip install sqlcipher3 --only-binary=:all:
> ```
> 或者从 [GitHub Releases](https://github.com/sqlcipher/sqlcipher) 获取预编译 wheel。

## 第三步：确认 DLL 文件

`resources/` 下有微信数据库解密所需的 DLL，已随 git 提交，正常情况下不用额外操作：

```
resources/
├── key/win32/x64/wx_key.dll          ← 注入微信进程提取密钥
└── wcdb/win32/x64/
    ├── WCDB.dll                       ← 微信 WCDB 原生库
    ├── wcdb_api.dll                   ← WCDB 桥接 DLL
    ├── SDL2.dll
    ├── msvcp140.dll / msvcp140_1.dll
    └── vcruntime140.dll / vcruntime140_1.dll
```

> ⚠️ 如果微信版本更新后 `wx_key.dll` 抓不到密钥，说明 DLL 版本不匹配。可以从**成功运行的那台电脑**复制 `resources/key/` 和 `resources/wcdb/` 过来覆盖（那台机器的微信版本和 DLL 是对齐的）。

## 第四步：初始化 — 提取数据库密钥

这是最关键的一步。`init` 命令会**注入微信进程**抓取 SQLCipher 解密密钥。

```bash
npm run dev -- init
```

执行时会经历 3 个步骤：

1. **检测微信版本** — 确认你用的是 4.x 还是 3.x
2. **扫描数据目录** — 自动找到 `xwechat_files/<你的wxid>/` 路径
3. **提取密钥** — 加载 `wx_key.dll` 注入微信进程，在内存中捕获数据库密钥

### ⚠️ 时序注意事项

`wx_key.dll` 在微信**登录瞬间** hook `sqlite3_key()` 调用。如果微信已经登录很久了，hook 窗口已经过去。

**正确操作**：
1. **先关闭微信**
2. 运行 `npm run dev -- init`
3. 看到 `请现在启动微信 4.x 并登录！` 提示后，**立刻**打开微信登录
4. 密钥会自动捕获并写入 `~/.weflow-cli/config.json`

### 成功后的 config.json 示例

```json
{
  "dbPath": "C:\\Users\\xxx\\xwechat_files",
  "ntDbPath": "C:/Users/xxx/xwechat_files/wxid_xxx/db_storage/message/message_0.db",
  "ntKey": "lock:...",
  "ntSalt": "xxx",
  "wxid": "wxid_xxx",
  "dataVersion": "4.x",
  "decryptKey": "明文64位hex密钥..."
}
```

## 第五步：验证

```bash
npm run dev -- check
```

如果输出显示数据库连接正常、联系人数量 > 0，说明配置正确。

也可以直接测试：

```bash
npm run dev -- contacts
```

## 第六步：抓取公众号日报

```bash
python scripts/biz_daily.py --date 2026-07-19 --api-key <your-deepseek-key> --engine deepseek
```

- `--date`: 日期 YYYY-MM-DD
- `--api-key`: DeepSeek API key
- `--engine`: deepseek | claude | ollama

输出在 `output/biz-daily/<日期>/`。

## 第七步：启动阅读器

```bash
python scripts/fav_server.py --date 2026-07-19 --port 8765
```

浏览器打开 `http://localhost:8765/`。

---

## 配置文件详解

配置文件位置：`C:\Users\<用户名>\.weflow-cli\config.json`

### 关键字段

| 字段 | 含义 | 示例值 |
|------|------|--------|
| `dbPath` | 微信数据根目录 | `C:\Users\xxx\xwechat_files` |
| `ntDbPath` | NT 消息数据库路径 | `.../db_storage/message/message_0.db` |
| `ntKey` | NT 数据库解密密钥 | `lock:Base64...` |
| `decryptKey` | 明文数据库密钥（旧格式） | 64 位 hex 字符串 |
| `wxid` | 微信账号 ID | `wxid_xxx` |
| `dataVersion` | 微信版本 | `4.x` |

### `lock:` 加密原理

敏感字段（`ntKey`, `bizKey`, `contactKey` 等）使用 `lock:` 前缀的 AES-256-GCM 加密：

```
加密密钥 = PBKDF2(machine_id, salt, iterations=100000)
machine_id = "{hostname}-{username}-weflow-cli"
```

**换电脑后 hostname 变化 → machine_id 不同 → `lock:` 解不开**。

两种解决方案：

**方案 A（推荐）：重新跑 `init`**

在新电脑上重新提取密钥，生成属于新机器的 `lock:` 加密值。

**方案 B：导出明文 key**

在原电脑上运行：

```python
import sys; sys.path.insert(0, 'scripts')
from _utils import load_config, decrypt_lock
c = load_config()
for k in ['ntKey', 'ntSalt', 'contactKey', 'contactSalt', 'bizKey', 'bizSalt']:
    if k in c and c[k]:
        v = decrypt_lock(c[k]) if c[k].startswith('lock:') else c[k]
        print(f'{k} = {v}')
```

把输出的明文 key 写到新电脑的 `config.json` 中（去掉 `lock:` 前缀），就不绑 hostname 了。

---

## 常见问题

### 1. `npm run dev -- init` 提示 "未检测到微信数据目录"

微信没有安装到默认路径，或者微信数据目录在其他盘。手动指定：

```bash
npm run dev -- config set dbPath "你的微信数据目录路径"
```

### 2. `wx_key.dll` 加载失败 / 密钥提取失败

微信版本和 DLL 不匹配。从正常工作的机器复制 `resources/key/` 和 `resources/wcdb/` 整个目录覆盖。

### 3. 文章全部归类为"学术"

`pipeline.py` 的 `--engine` 默认值可能在某个版本是 `local`。确保始终用 `deepseek`：

```bash
python scripts/biz_daily.py --date ... --api-key ... --engine deepseek
```

### 4. `sqlcipher3` 安装报错

Windows 上编译 SQLCipher 经常失败。尝试：
```bash
# 方法 1：用 pip 的预编译 wheel
pip install sqlcipher3 --only-binary=:all:

# 方法 2：如果还不行，检查 Visual Studio Build Tools 是否安装了 C++ 编译器
```

### 5. `biz_message_0.db` 不存在

需要微信客户端本地有公众号订阅数据。确保微信关注了足够的公众号，并且数据同步到了本地。

### 6. 换电脑后阅读器打不开

阅读器用的是已生成好的 `output/biz-daily/` 下的静态 HTML 文件，不需要数据库。但如果在新电脑上直接开 `index.html`（`file://` 协议），浏览器会拦截 `fetch()`。应该始终用 `fav_server.py` 启动 HTTP 服务器。
