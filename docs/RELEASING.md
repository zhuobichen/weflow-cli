# 发布清单

npm 包与 GitHub 分开发布，中间隔多久都不奇怪——`1.5.0` 与主线之间曾拉开 128 个提交、
两个多月，直接导致用户照着 README 敲 `init --path` 却报 `unknown option`。

按下面顺序走，每一步都有对应的**失败长什么样**。

## 1. 定版本号

按语义化版本，不是按「改了多少行」：

| 改动性质 | 升哪一位 | 例 |
| --- | --- | --- |
| 新功能、新命令、新参数 | **minor** | 1.5.0 → 1.6.0 |
| 只修 bug、只改文档 | patch | 1.6.0 → 1.6.1 |
| 破坏兼容 | major | 1.6.0 → 2.0.0 |

已备好但**从未发布**的版本号可以并进下一个：`1.5.1` 准备过却没发，
它的修复就直接并进了 `1.6.0`。

## 2. 整理 CHANGELOG

- 把 `## Unreleased` 改名为 `## <新版本号>`
- 若上一版已写好但没发布，把它的条目并入，删掉那个空段
- 通用说明（如「npm 与 GitHub 分开发布」）移到文件顶部，别留在版本段里

## 3. 构建与测试

```powershell
npm run build
npm test
```

## 4. 打包检查（**不能跳过**）

```powershell
npm pack --dry-run
```

本地常有 `output/`（几 GB 产出）、`models/`（几 GB 模型）、`_env.json`（含密钥）
这类东西。`package.json` 的 `files` 是白名单，理论上挡住——但**白名单也可能写漏**，
所以逐项确认：

```powershell
npm pack --dry-run 2>&1 | grep -E "npm notice [0-9]" > /tmp/list.txt
grep -E "^output/|^models/|node_modules|\.env|\.db$|\.dat$|secret|credential" /tmp/list.txt
```

**这条命令必须没有输出。** 有输出就停下来查。

同时确认新增文件确实**在**包里（白名单是按目录配的，容易漏）：

```powershell
grep -E "新文件名" /tmp/list.txt
```

## 5. 发布

需要 npm 凭据。`ribiaosu` 账号开了 2FA，所以两条路：

**凭据（推荐，一次配置长期可用）** — 用**带 `Bypass 2FA` 的 Granular Access Token**，
在 https://www.npmjs.com/settings/ribiaosu/tokens 生成时**必须勾选那个框**。
漏勾的 token 会在发布时报 `403 ... bypass 2fa enabled is required`。

| 现象 | 原因 |
| --- | --- |
| `403 ... two-factor authentication ... required` | token 没勾 Bypass 2FA，或没用 OTP |
| `ENEEAUTH` | 没登录 / 没配 token |

**验证 token 是否可用**（`bypass_2fa` 必须是 `true`）：

```powershell
curl -s -H "Authorization: Bearer <token>" https://registry.npmjs.org/-/npm/v1/tokens
```

**一次性验证码**：

```powershell
npm publish --otp=<6位码>
```

## 6. 触发国内镜像同步（**新加的必要步骤**）

国内用户大多用 `registry.npmmirror.com`（淘宝源），它对新版本有同步延迟。
不同步的话，发布后几个小时内会有人来提「版本不存在」的 issue——
`weflow-cli@1.6.0` 就这样被报过一次（Issue #8）。

```powershell
curl -X PUT https://registry.npmmirror.com/-/package/weflow-cli/syncs
```

返回 `{"ok":true,...,"state":"waiting"}` 即已受理。**约 5 分钟后**生效。

## 7. 验证两个源都是新版本

```powershell
npm view weflow-cli version --registry=https://registry.npmjs.org
npm view weflow-cli version --registry=https://registry.npmmirror.com
```

两个都要是新版本号才对外宣告。官方源发布后自身也有几分钟 CDN 传播延迟——
期间 `npm view` 可能显示旧版本、tarball 可能 404，**这是在传播，不是失败**。
用这个判断是否真的发出去了：

```powershell
npm publish    # 再发一次
```

报 `409 Conflict - Cannot publish over previously staged version` → **已经发布成功**。

## 8. 兜底：告诉用户怎么绕开镜像

```powershell
npm config get registry                                            # 看用的哪个源
npm view weflow-cli version --registry=https://registry.npmjs.org  # 用官方源查
```

两个源结果不一致 → 是镜像同步问题，不是包的问题：
`npm install -g weflow-cli@<版本> --registry=https://registry.npmjs.org` 立刻可用。

## 发布后

- 回到相关 issue 回复「已发布 + 升级方式」
- GitHub Release 与本清单解耦：源码、编译包、npm 三者版本差异要对用户可见

### 回复用户的规矩

报障的人多半不是开发者，也不看长文。**回复写给用户，不是写给同事：**

- **先给出结论和做法**（已修复 / 升级到 x.y.z），技术细节不要写进回复
- **三五句话说完**。根因分析、代码片段、执行顺序论证属于 commit message 和
  本文档，不属于 issue 回复
- 升级类回复直接给命令，别让人自己找
- **发出前先给维护者过目**，确认后再发

反面例子：一条回复里贴了 Python 代码片段和逐步的执行顺序分析，内容全对，
但给用户的感受是「看不懂、太长」。

### 新动态邮件提醒

`scripts/watch_issues.py` 轮询本仓库的 issue 与回复，有非维护者发言时发邮件。
由 Windows 计划任务 `WeFlow Issue Watch` 每 30 分钟调用一次，不依赖任何编辑器
或 Claude 会话处于打开状态。

邮件凭据放在仓库外，不进版本库：

```json
// ~/.weflow-issue-watch.json   （QQ 邮箱需用「授权码」，不是登录密码）
{
  "smtp_user": "1473517806@qq.com",
  "smtp_auth": "<授权码>",
  "mail_to":   "1473517806@qq.com",
  "repo":      "zhuobichen/weflow-cli"
}
```

- 首次运行只记录基线、不发信，避免把历史 issue 一次性全发出去
- 发送失败不推进记录，下次自动重试
- 手动跑一次：`py scripts/watch_issues.py`
- 查看/改频率：`Get-ScheduledTask -TaskName 'WeFlow Issue Watch'`

### 发布后自检

```powershell
npm view weflow-cli version --registry=https://registry.npmjs.org
npm view weflow-cli version --registry=https://registry.npmmirror.com
```

两个源都是新版本，再对外说「已发布」。

然后跑一遍体检，确认这次改动没有把读取路径或某个导出格式弄坏：

```powershell
py scripts/health_check.py
```

退出码 `0` 才算干净。检查项与各自的由来见 [HEALTH-CHECK.md](HEALTH-CHECK.md)。
