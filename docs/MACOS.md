# macOS 上怎么用 weflow-cli

> 最后更新：2026-11-19。
> **状态说明**：已在真机跑通完整流程（macOS 27.2 / Apple M4 / 微信 4.1.15，
> 10/10 库通过首页 HMAC 校验，由贡献者在自己的机器上验证）。此前实现有两处必炸的
> bug（lldb 里 import gdb、轮询把调试器崩溃当超时），现已重写为纯 lldb 原生断点命令，
> 见 `scripts/macos_capture.py` 头注释。

## macOS 版微信能导出聊天记录吗？

**理论上可以，但需要你先提供那串共享口令。** 步骤和 Linux 一样：

```bash
weflow-cli capture-passphrase   # 尝试自动捕获（见下方前提）
weflow-cli init                 # 派生各库密钥
weflow-cli sessions             # 验证：能列出会话才算真的读到
```

如果自动捕获走不通，用口令自助这条路：

```bash
weflow-cli fav set-key --passphrase <64位hex>
weflow-cli init
```

## 为什么 macOS 的自动捕获可能失败

macOS 微信 4.x 与 Linux 是同一套机制：内存里没有明文密钥，只有 PBKDF2 派生后的结果。
所以**扫内存基本扫不到**（公开实践试过 hex 扫描、salt 匹配、HMAC 暴力，全部失败）。

我们的做法是断在派生函数上：

```
CCKeyDerivationPBKDF(password, passwordLen, ...)
```

它是 CommonCrypto 的**公开符号**，lldb 按名字下断点即可，不需要找版本偏移。

但 attach 到别人的进程需要权限，macOS 的 **SIP / Hardened Runtime** 会挡住普通用户。
同类工具（chatlog）的做法是：**捕获前临时关闭 SIP，捕获后恢复**。本工具不替你改系统安全设置，
被挡住时会明确报出原因。

前提：

```bash
xcode-select --install     # 提供 lldb
# 以及：允许 attach（通常意味着临时关闭 SIP）
```

## macOS 需要 root 吗？

不需要 root。需要的是"能 attach 到微信进程"的权限，这在 macOS 上受 SIP 控制，
和 Linux 的 sudo 不是一回事。

## 库在哪、产物在哪

微信的库：

```
~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/
~/Library/Containers/com.tencent.xinWeChat/Data/Library/Application Support/com.tencent.xinWeChat/
```

我们的产物（与平台无关）：

```
~/.weflow-cli/output/biz-daily/<日期>/     日报
~/.weflow-cli/output/wechat-vault/         Vault
~/.weflow-cli/output/chat-notes/           会话卡片
```

## 已验证到哪一步

```
✅ 数据目录发现：认得上面两种 Containers 布局（单测覆盖）
✅ 捕获脚本：arm64 / x86_64 两套寄存器都覆盖（单测覆盖）
✅ 校验逻辑：复用 Linux 同一套逐库首页 HMAC
❌ 真机端到端：未验证（没有 Mac 环境）
❌ 微信 4.x macOS 的实际库结构：未验证
```

如果你愿意帮忙验证，跑一次：

```bash
weflow-cli check --report
```

把输出贴到 issue 里（已脱敏）就能推进这件事。

## 相关文档

- [Linux 版问答](./LINUX.md) / [English](./LINUX.en.md)
- [操作与排障](../OPERATIONS.md)
- [安全边界](../SECURITY.md)
