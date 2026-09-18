# npm 发布 — 速查与故障对照

只讲 npm 这一侧。**流程清单**在 [RELEASING.md](RELEASING.md)，这里只解决
「命令跑完了，现在到底算成功没有」。

单个环节按需查，不必通读。

## 0. 省上下文（先看这条）

发布命令会打印几百行 `Tarball Contents`。**永远加过滤**，否则整个文件清单
会灌进上下文，既没用又挤掉真正要看的信息：

```bash
npm publish --registry=https://registry.npmjs.org 2>&1 \
  | grep -vE "^npm notice [0-9.]+[kMB]+ |^npm notice [0-9.]+B " | tail -25
```

`npm pack --dry-run` 同理。要看文件清单时**输出到文件再 grep**，不要直接打印：

```bash
npm pack --dry-run 2>&1 | grep "npm notice [0-9]" > /tmp/packlist.txt
grep -c " dist/" /tmp/packlist.txt     # 注意: 行首是体积, 不是路径
```

## 1. 唯一可靠的判据

**别信命令的输出，信 registry 的 `versions` 表：**

```bash
curl -s "https://registry.npmjs.org/weflow-cli?nocache=$RANDOM" --max-time 30 \
| python -c "import json,sys; d=json.load(sys.stdin); print('latest:', d['dist-tags']['latest']); print('目标版本存在:', '1.6.2' in d['versions'])"
```

- `目标版本存在: True` → **发布成功**，剩下只是传播
- `False` → 没发出去，看第 2 节

`npm view` 会读到客户端缓存，`--prefer-online` 或上面的 `nocache` 才准。

## 2. 症状 → 含义 → 动作

| 现象 | 含义 | 动作 |
| --- | --- | --- |
| `+ weflow-cli@1.6.2` | 已被接受 | 继续，等传播 |
| `being processed and may take a few minutes` | 正常提示 | 等，别重发 |
| `npm view` 仍是旧版本 | CDN 传播中 | **不是失败**，等 2–5 分钟 |
| 重发报 `409 Conflict - Cannot publish over previously staged version` | **已经发布成功了** | 别再发，去等传播 |
| tarball `404`（`/-/weflow-cli-1.6.2.tgz`） | 元数据已发布，tarball 还在传播 | 等，不是失败 |
| `install` 报 `ETARGET No matching version` | 客户端缓存了旧 packument | 加 `--prefer-online` 重试 |
| `install` 报 `404` | 元数据在、tarball 未到 | 等传播 |
| `403 ... bypass 2fa enabled is required` | token 没勾 Bypass 2FA | 重新生成 token（见第 3 节） |
| `E404` 发布时 | 包名或权限问题 | 查 `npm whoami` |

> **404 / 409 / ETARGET 在发布后的几分钟内都是传播现象，不是错误。**
> 唯一例外：`403` 和发布时的 `E404`。

## 3. 凭据自检

不打印 token 本身：

```bash
npm whoami --registry=https://registry.npmjs.org

TOKEN=$(sed -n 's/.*_authToken=//p' ~/.npmrc | tr -d '\r\n')
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://registry.npmjs.org/-/npm/v1/tokens" --max-time 30 \
| python -c "
import json,sys
for t in (json.load(sys.stdin).get('objects') or []):
    print(t.get('tokenId','')[:12], 'bypass_2fa=', t.get('bypass2fa'), 'created=', t.get('created'))"
```

`bypass_2fa` 必须是 `True`，否则发布会要 OTP。

## 4. 触发国内镜像同步

```bash
curl -s -X PUT "https://registry.npmmirror.com/-/package/weflow-cli/syncs" --max-time 40
```

`{"ok":true,...,"state":"waiting"}` 即受理，**约 5 分钟**生效。

两个源不一致时，先怀疑镜像同步，不要怀疑包：

```bash
npm view weflow-cli version --registry=https://registry.npmjs.org
npm view weflow-cli version --registry=https://registry.npmmirror.com
```

## 5. 装一份下来验（最终确认）

```bash
mkdir -p /tmp/verify && cd /tmp/verify && npm init -y >/dev/null
npm install weflow-cli@1.6.2 --registry=https://registry.npmjs.org --prefer-online --no-audit --no-fund
node -e "console.log(require('./node_modules/weflow-cli/package.json').version)"
```

装不上但第 1 节显示版本存在 → 就是 tarball 传播中，等。

## 6. 打包安全检查（发布前，不能跳）

```bash
npm pack --dry-run 2>&1 | grep "npm notice [0-9]" > /tmp/packlist.txt
grep -E "output/|models/|node_modules|\.env|\.db$|\.dat$|_env\.json|secret|credential|\.key$|\.pem$" /tmp/packlist.txt
```

**这条必须没有输出。** 有输出就停下查。

再确认新增文件确实**在**包里（`files` 白名单按目录配，容易漏）：

```bash
for f in scripts/health_check.py docs/HEALTH-CHECK.md cli.cjs; do
  grep -qF "$f" /tmp/packlist.txt && echo "✓ $f" || echo "✗ 缺失 $f"
done
```

## 已知坑

- **`bin` 会被 npm 改写**：`"bin": {"weflow-cli": "./cli.cjs"}` 发布时报
  `script name cli.cjs was invalid and removed`，同时 `repository.url` 被规范化。
  是 warning，不影响发布结果，但要知道它每次都出现，不是本次引入的问题。
- **`.gitignore` 里 `*.md` 默认忽略、逐个放行**：新增文档必须同时加
  `!docs/<名字>.md`，否则 `git add` 会被挡。
