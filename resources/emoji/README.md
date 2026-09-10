# WeChat 表情资源

这里的 109 张 PNG 是微信内置表情的原版美术资源，用于把聊天记录里的 `[害羞]` 这类表情码
渲染成**和微信客户端里看到的一模一样**的图，而不是风格相近的标准 emoji。

## 来源与许可

- 包名：`wechat-emojis@1.0.2`
- 作者：xxk8
- 仓库：https://github.com/xxk8/wechat-emojis
- 许可：MIT

## 使用方式

文件名即表情名（如 `害羞.png` 对应 `[害羞]`）。`scripts/wechat_emoji.py` 会：

- 有原版图的 → 渲染成 `<span class="wxface wxf-xxxxxxxxxx">`，图片以 base64 内嵌进 CSS
- 没有原版图的（如 `酷`、`西瓜`）→ 退回标准 emoji
- 认不出的码 → **原样保留**，绝不猜

**同一张图在一个页面里只内嵌一次**（按 CSS 类去重），所以一页里出现 42 次表情也只占一份体积。

## 想换掉这套图？

保持目录结构即可 —— 放任意 PNG，文件名用表情名。`scripts/wechat_emoji.py` 启动时会自动扫描，
不需要改代码。
