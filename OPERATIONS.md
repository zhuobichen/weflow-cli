# WeFlow CLI 操作与排障手册

本文面向用户和维护 Agent。所有命令都应在项目目录或已安装 CLI 的终端中执行；示例中的路径、联系人和密钥均为占位符。

## 1. 环境

| 依赖 | 用途 | 检查 |
| --- | --- | --- |
| Node.js 22.13+ | CLI、MCP、构建 | `node --version` |
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

### 导出耗时与远程媒体

HTML 导出的时间几乎全部花在远程媒体上（公众号封面、B 站封面、表情 CDN）。这些结果按 URL 缓存在输出目录的 `.cover-cache/`，**成功和失败都会记**——多数是已失效的微信 CDN 链接，不记负结果的话每次重导都要把几百次请求重跑一遍。因此：

- 同一会话的**重复导出接近瞬时**（实测 1614 条约 2 秒）；首次导出才是真正要付网络成本的。
- 首次导出会先做一遍**只记录 URL、不联网**的扫描，再用 24 线程并发取回，最后用热缓存正式生成。逐条串行取回会慢一个数量级。
- `.cover-cache/` 可以随时删除，删了只会退回「首次导出」的耗时，不会影响产出内容。
- 原图（5–24MB/张）默认不嵌入，用微信缓存里的缩略图；确需原图时加 `--full-images`，代价是数分钟。

### 图片显示为 `[图片]`、群聊看不出谁在说话

导出会同时合并两个索引：**会话缓存**（`cache/YYYY-MM/Message/<会话md5>/`，只有最近几个月）和**账号媒体索引**（`msg/attach/<会话md5>/`，历史图片都在这里）。只有前者时，去年的群图片命中率是 0，全部退化成光秃秃的 `[图片]`。

群聊的发言人从联系人库解析：**备注 > 昵称 > 别名**。都取不到时显示 wxid 本身——它仍能区分是谁，比留空或写群名有用。

以下情况是**数据本身不在本机**，改代码也解决不了，导出会如实标注而不是猜：

- 视频显示为 `[视频 10″]` 而没有封面图。微信只在视频被完整下载过之后才在本地留下封面（`msg/video/<月份>/<md5>_thumb.jpg`）；没下载过就没有，而 `cdnthumburl` 是加密标识、不是能直接下载的地址，离线拿不到。
- 图片显示为 `[图片]` 且无图：该图片从未在这台设备上下载过。
- `[位置]` 只有地名、没有地图：位置消息本身不携带地图图片。

每次 HTML 导出还会在输出目录里写一份 **`<会话名>_media.json`**，逐项记录每个媒体的状态与原因，不用再靠肉眼比对：

| status | 含义 |
| --- | --- |
| `embedded` | 已内嵌进页面，`mediaKey` 说明按哪个键命中 |
| `cached` | 用了本地缓存 |
| `remote-fetched` | 从网络取回的（封面/链接图） |
| `missing` | 没有可用媒体，`reason` 说明为什么 |
| `unsupported` | 该类型暂不支持（如语音不在媒体索引里） |

`missing` 的常见 reason：`not-in-local-cache`（本机没有）、`no-reliable-identity`（这条消息没有可靠身份可匹配——按 D-014 宁缺毋滥，不会拿相邻消息或裸 localId 去猜）、`budget-exhausted`（本次远程抓取配额用完）、`download-failed`（链接失效）。

### 同步检查点与覆盖范围

`sync` 用重叠时间窗增量读取，并在本机记下覆盖到哪：

```powershell
weflow-cli sync run <会话> --since 2026-09-01   # 首次需要 --since 或 --full
weflow-cli sync run <会话>                      # 之后从上次检查点继续
weflow-cli sync status                          # 不访问数据库，数据库被占用时也能看
weflow-cli sync verify <会话>                   # 重读记录范围并与当前数据比对
```

每次运行都给出 `coverage`：

| coverage | 含义 |
| --- | --- |
| `complete` | 扫到的分片全部打开、打开的都读成功 |
| `unverified` | 有分片**打不开**——读到的部分可信，但那几个分片里有没有本会话消息**无法判断** |
| `partial` | 已打开的分片没能按请求读完、或还有更多未读。**不算成功**，退出码 1，且不推进"上次成功时间" |

看到 `unverified` 时，`sync status` 的 `warnings` 会点名是哪个分片。这通常意味着该分片的密钥没解开——先排查 `init` 是否覆盖了全部分片。

`partial` 的 `warnings` 有三种，都意味着**那个分片的这段范围没读到**，对应处理方式不同：

| 警告 | 含义 | 怎么办 |
| --- | --- | --- |
| `shard-read-failed:<分片>` | 打开成功但读到一半出错 | 数据库可能损坏或被占用；重试，仍失败则视为该分片不可信 |
| `shard-not-read:<分片>:SCHEMA_MISMATCH` | 表里的列没有一个能认出来 | 微信表结构变了，需要更新读库代码；先别把这次同步当完整 |
| `shard-not-read:<分片>:WINDOW_UNAVAILABLE` | 表里没有 `create_time`，时间窗无法表达 | 同上；此时**不会**返回未筛选的行来充数 |

另有一类**不算** partial 的警告：`shard-columns-missing:<分片>:<列名>`——行都读回来了，只是少了某个字段。`coverage` 仍是 `complete`，因为要问的"有没有读全"答案是"读全了"。

**没有"稳定游标"**：`sync` 提供的是重叠窗口 + 本地去重，不是可直接续传的游标（D-027 要求所有后端都支持后才能宣称）。边界消息被重复读取是**预期行为**，会被去重吸收。

### 谁在等我回话

```powershell
weflow-cli awaiting --dry-run          # 预览：会判哪些会话、要发多少字符（不出境）
weflow-cli awaiting --yes              # 真跑：最近 14 天有动静的会话
weflow-cli awaiting --days 75 --limit 25 --min-prob 0.45 --yes
```

**它会读取聊天正文并发送到决策模型**，所以照 `search` 的规矩来：`--dry-run` 只读本地、
零出境；`--yes` 才真跑；都没给时会让你确认。它不写任何本地数据。

每个会话一次决策调用（一次请求里同时问：是否停在我该回的位置、多急、有没有没兑现的
承诺、涉不涉及钱、属于哪类），所以每条判断都知道它属于哪个人。25 个会话约 5 秒。
`waiting` 概率低于 `--min-prob` 的不算欠账；判定为客服/推销/通知类的不计入（但会报数量，
免得看起来像没扫到）。

- **每行都会打印它的证据有多薄**：`证据：对方末条 N 字 · 对方实质发言 M 条`。
  少于 5 个字会直接标注「证据很薄，这个分数不可当结论」——一个从两个字的末条得出的
  0.69 和一个从整段说明得出的 0.69 不是一回事。
- 判定"对方在等我"但对话最后一条其实是我发的，会被自检抓出来单列。
- `--html <路径>` 另外写一份单页 HTML：**自包含、不含任何聊天内容**（只有概率、天数、
  类别与证据量），所以可以直接发给别人看。页脚写明它不是什么：没有金标准校准过、
  **概率在阈值附近会抖**（同一批数据两次跑会有出入，别细究 0.45 与 0.55 的区别）、
  以及证据薄的那几条不足为凭。
- **没有金标准校准过**，当提示看，不当事实用。图片/语音等非文本消息以类型标签进入判断，
  不会被当成空内容。

### 本机判断层

把"判断"从"生成"里拆出来，做成一条命令行原语：**一个 state、一批类型化问题、一次调用**，
返回带概率的类型化答案，外加这次调用的 token 数与花费。它**不读任何本地数据**——
state 是什么完全由你给。

```powershell
# 从文件（CLI）：
weflow-cli decide --request req.json --dry-run     # 只校验并回显形状，不出境、不需要 key
weflow-cli decide --request req.json --yes

# 从 stdin（直接调脚本，管道里更好用）：
echo '{"state":"...","questions":{"相关":{"type":"noul","instructions":"..."}}}' | python scripts/decide.py
```

请求格式：

```json
{"state": "字符串 / JSON 对象 / 数组都行",
 "questions": {
   "要退款": {"type": "noul", "instructions": "对方明确要求退款吗？"},
   "紧急度": {"type": "score", "instructions": "多急？", "criteria": ["不急", "一般", "紧急"]},
   "部门":   {"type": "choice", "instructions": "转给谁？",
              "criteria": {"billing": "账单", "tech": "报错"}}
 }}
```

**`score` 的 criteria 是零索引的有序数组**——位置即分值，`criteria[0]` 是 0 分。
只有一档时会被本机拒掉，因为那样 score 恒等于 0，问不出东西。

批量模式把最常用的那层展开做掉了——一个 glob × 一批问题，自动变成 N×M 个问题：

```powershell
weflow-cli decide --over "scripts/*.py" --ask "会写本地文件吗" --ask "会出网吗" --dry-run
weflow-cli decide --over "scripts/*.py" --ask "会写本地文件吗" --ask "会出网吗" --yes
```

- `--max-chars`（默认 1500）决定每个文件喂多少，**它决定上限**：同一批问题，只给每个文件
  前 14 行时与基准一致 77%，给前 1500 字符时 89%。返回里会回显这个值。
- 答案的键是 `f<序号>|q<序号>`，序号到文件/问题的映射在返回的 `batch.files` / `batch.asks` 里，
  不用去解析问题名。
- `--request` 那条路**不读任何本地文件**，`--over` 会读——`capabilities` 里是分开声明的。

什么时候值得用它：**一批**判断（比如给 200 个条目各打几个标签），一次请求约 1 秒，
问题数几乎不影响成本。**一次性的单个判断不值得**——调用方自己的模型就够了，
多这一跳只是绕路。

它对**不生成文本**这一点是有意的：答案不会以散文形式回来，所以放在控制流里是安全的。
这一版**没有**把它暴露成 MCP 工具——那会让远端 MCP 客户端能驱动本机往第三方发文，
属于要单独决策的出境面。

### 校准：这些概率到底准不准

前面所有概率的依据都是"与另一个不完美的基准比"。要换成真的答案，只有一条路：人工标注
一批，然后算校准。这个脚本把那条路压到十分钟：

```powershell
python scripts/quality_eval.py sample --n 50      # 抽样并现场打分（约 $0.005）
# 打开 ~/.weflow-cli/labels/labels-<时间戳>.json，把每条的 label 填成：
#   {"topic": "AI|学术|新闻|文学|投资|政治", "include": true/false}
# 没把握的留 null —— 它算"未标注"，不算"标错"
python scripts/quality_eval.py score ~/.weflow-cli/labels/labels-<时间戳>.json
```

`score` 会给出三件事：

1. **主题判断的准确率**，同时给出**历史存档标签**的准确率作对照——存档标签一直被当成
   基准，但它本身就是要被检验的那个东西。
2. **校准表**：每个概率区间里，人工说"该收"的比例。**斜着往上走说明分数有分辨力；
   一条平线说明它只是在乱猜**——这是"概率有没有意义"唯一能回答的方式。
3. **阈值扫描**：哪个切点与你的判断最一致。现在用的是 `0.50`；如果最优点离它很远，
   该改的是那个常数，不是模型。

**样本是现场用生产路径重新打分的**，不是读存档值：磁盘上没有一篇文章带 `includeScore`
（2026-09-05 那 6 篇生成于该字段引入之前），而且存档的 `topic` 正是要被检验的对象。
它只**读** `output/`，不重跑、不写回。

`--pool`（默认 150）是**先打分的候选池**，最终样本再从池里**按概率档位**分层取。这一步
是必须的：只按主题抽样得到的 50 篇里，45 篇的收录分都在 0.2 以下，**你标 50 条买到的
信息量等于标 4 条**。分层后 50 篇里有 22 篇落在真正影响阈值的区间。代价是样本不再是
自然分布——所以档位在池里的**自然占比会单独报出来**（`prevalence`），它回答的是另一个
问题："这个阈值每天会多收或少收多少篇"。实测（160 篇均衡候选）：86.1% 低于 0.2，
**8.9% ≥ 0.5**。

**耗时**：单次判断约 1 秒，且与 state 长短无关（实测 200 字与 4000 字都是 ~1.1s）；
但这个数字是**空载**下的。6 并发跑 158 篇时实测平均 **5.7 秒/篇**（有 10 秒的离群），
所以估算大批量时按"秒/篇"算，别按"一秒"。

标注文件落在 `~/.weflow-cli/labels/`，里面是文章标题与你的判断，不进仓库。

### 语义检索要用的 embedding key

语义检索和 RAG 问答需要阿里云百炼（DashScope）的 key：

```powershell
weflow-cli config set dashscopeApiKey "sk-..."
```

它和别的密钥一样**机器绑定加密保存**。不设的话 `search`/`chat` 会直接说缺 key，
而不会拿一个空 key 去请求。`--api-key` 参数或 `DASHSCOPE_API_KEY` 环境变量仍然优先，
临时用一次时更方便。

顺带一提，`favPassphrase`（解锁收藏与 biz 库的口令）现在也能这样设置了——

```powershell
weflow-cli config set favPassphrase "..."
```

在此之前它是个"一等机密却设不了"的项：在加密名单里、被日报与欠账雷达读，
却不在 `config set` 的允许名单里。

### 检索与重排

```powershell
weflow-cli search "PM2.5 对植物用水效率的影响"
weflow-cli search "..." --no-rerank     # 只按向量/关键词相似度，不调决策模型
```

检索分两段：第一段算相似度取回 20 条候选，第二段用**一次**决策请求给每条候选问一个
"是否真的回答了这个问题"，按概率重排后返回 `--top-k` 条（默认 10）。第二段约 1 秒，
与候选数基本无关。

- 返回里 `score` 仍是相似度（含义没变），重排的分数在 `rerankScore`。
- 没配 `typesafeApiKey`、连接失败或服务过载（529）时，**原序返回并打一行 WARN**，
  检索本身不会因此失败。瞬时错误会自动重试最多 2 次。
- 想确认重排有没有帮上忙：同一个查询跑一次带重排、一次 `--no-rerank` 对比顺序即可。

### 语音消息转文字

HTML 里放不出语音：微信语音是 **SILK v3** 格式，浏览器不支持，ffmpeg 也没有 SILK 解码器（只有 AMR）。所以语音转文字是让语音消息在导出里能携带信息的唯一办法。

**它是独立的、可断点续跑的一步，不在导出过程里。** 导出只读转写缓存；缓存没有的语音显示为 `[语音 6″]`。这样可以随时跑、随时停，跑过一遍就永久命中（按语音内容的 md5 缓存，同一条语音被转发到别处也只识别一次）。

安装（可选，较重）：

```powershell
python -m pip install -r requirements-voice.txt
```

下载模型。**这一步对粤语是必须的**：原版 Whisper 遇到粤语会输出通顺但完全是编造的普通话——看着像真句子，实际什么都没说过，比不转写更危险。粤语微调版才转得出真正的粤语：

```powershell
$env:HF_ENDPOINT = "https://hf-mirror.com"; $env:HF_HUB_DISABLE_XET = "1"
python -c "from huggingface_hub import snapshot_download; snapshot_download('alvanlii/whisper-small-cantonese', allow_patterns=['cts/*'], local_dir='models/whisper-small-cantonese')"
```

> `HF_HUB_DISABLE_XET=1` 是必需的：hf-mirror 不代理 HuggingFace 的 Xet 传输，不加会报 401。

模型放好后会自动被优先使用（`models/whisper-small-cantonese/cts` 存在即可，该目录已在 `.gitignore` 中）。跑转写：

```powershell
python scripts/wechat_voice.py --db "<message_0.db>" --key <key> --salt <salt> `
  --passphrase <passphrase> --talker "<会话id>" --cache-dir "output\.voice-cache"
```

- **有 NVIDIA GPU 时自动走 GPU**，约快 30 倍（实测 0.07 秒/条 vs 2.0 秒/条）；GPU 不可用则退回 CPU，不会报错。需要 `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12`。
- 语言**自动检测**。强制 `--language yue` 在粤语模型上会返回空，别加。
- 中断后重跑会从缓存未命中的地方继续，不会从头再来。
- 实测：1775 条语音（约 2.5 小时音频）全量约 9 分钟。

> **⚠️ 粤语/方言的准确率有限，务必当作草稿。** 实测一个钦州/东兴一带的家族群
> （疑为钦廉片白话）1777 条语音：模型输出的是**粤语形态的文字，但相当一部分句子
> 意思不成立**——音对、词不对。公开的粤语微调模型基本训练于广府片（香港/广州话），
> 与钦廉片差异明显，这很可能是主因。
>
> 排除了这些可能：SILK 解码（三种采样率结果一致、`\x02` 前缀处理无差别）、
> 音频质量（峰值/RMS/过零率均为正常语音特征）、模型置信度（`avg_logprob` 中位
> -0.25，模型对自己输出的内容很有把握——这恰恰说明它错得自信）。
>
> 因此导出的每条转写都标注「机器转写·粤语欠准」，页脚也写明**不可作为原话引用**。
> 需要确证时以录音为准。若某个会话不需要转写，删掉输出目录下的 `.voice-cache`
> 即可，导出会退回只显示 `[语音 N″]`。

实测对比（同一条粤语语音）：

| 设置 | 输出 |
| --- | --- |
| 原版 small，`zh` | 有些人在拍攝,我們都很懶拍七六歲 ← 编造 |
| large-v3，自动检测 | `Các bạn hãy đăng ký kênh...` ← 幻觉成越南语 |
| **粤语 small，自动检测** | 你所以東興人沒處來囉，東興人沒處來開囉 ← 正确识别出地名 |

### 自定义表情包（表情包/贴纸）

自定义表情包是 AES 加密的，密钥由一个**账号级 seed** 参与派生。该 seed 只存在于微信进程内存中：

- 导出时如果 `emoticonSeed` 未配置，会自动扫描微信内存反推（用真实缓存文件验证，误报基本不可能），结果记在输出目录的 `.sticker-cache/seed`，只需扫一次。
- 扫描要求**微信正在运行**，且账号曾在微信里查看过表情包。否则自定义表情包会退回 CDN 或显示为 `[表情]`（若其后紧跟图片，说明已渲染成功，`[表情]` 只是标签文字）。
- 固化：`weflow-cli config set emoticonSeed <值>`，之后不再需要扫描。导出日志里会直接给出这条命令。
- wxgf（H.265）贴纸需要 ffmpeg，由 `imageio-ffmpeg` 提供；缺失时这类贴纸无法解码。

## 5. 公众号日报与阅读器

生成今天的日报：

```powershell
weflow-cli daily
```

报告末尾会附一段「我拿不准的」：收录分卡在阈值上的条目（分收了与没收两组）、以及主题
置信度偏低、可能被分错栏的条目。这一段在本地算出来，**不额外调用模型**。如果这一天的文章
生成于概率字段引入之前，它会直接说明"这份报告无法告诉你它哪里不确定"——那一段缺席
不等于"哪里都很确定"。

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

**配了类别的来源会失去标签，这是设计。** 一旦某个来源有了类别，日报对它只发
"生成摘要"的提示词，并明确不要模型输出主题/标签/相关度/概念——主题用你配的那个，
标签直接取 `[类别]`。所以标签只有**没配类别**的来源才会有真内容。实测：11 天
1584 篇里 1396 篇的标签恰好等于主题、182 篇为空、只有 6 篇是真标签，而那 6 篇正是
唯一没配类别的语料。

代价要自己权衡：想让某个来源有真标签，就得把它的类别留空，代价是它的主题改由
模型判断而不是你说了算。类别值必须是 `AI/学术/新闻/文学/投资/政治` 之一——写错
一个词，那篇文章会被兜底类接住，运行时也会打一行 WARN 告诉你有多少篇落了兜底。

### 文章分类用谁判断

主题与相关度默认由 TypeSafe 的 Jev 决策模型判断（`choice` 选主题、`score` 打相关度，
返回概率而不是一段要解析的文字）。配了 key 就用，没配就沿用原来的 LLM 解析路径：

```powershell
weflow-cli config set typesafeApiKey "..."   # 机器绑定加密保存，和 deepseekApiKey 一样
weflow-cli config set typesafeApiKey ""      # 清空即回到 LLM 解析路径
```

### 不想看某一类：排除主题

```powershell
weflow-cli config set dailyExcludeTopics "新闻,投资,学术"   # 留空即不排除
weflow-cli config set dailyExcludeTopics ""
```

**这是展示层开关，不是抓取层的**：正文照常抓取、照常归档，只是不出现在日报报告和
日报页里。所以改主意不用重抓——改配置重新生成一遍就行。

为什么不做成"拉之前就判类型、不想要的不拉"：实测过，拉之前只有来源+标题+平台摘要，
按它判类型和你配的来源类别一致率只有 60%（217 篇里 48 篇误伤），而且**排除不可逆**——
没抓就没归档，而公众号页隔几周再看已不是同一篇。宁可多看一眼，不可丢掉一篇。

几条边界，写下来免得踩：

- 写错主题名会被忽略，但会**打一行 WARN**（静默忽略会让人以为过滤生效了）；
- **焦点主题（AI）不能排除**：排掉它不会得到空报告，而会得到一份会走错方向的报告——
  没有文章时它报的是"未找到文章，请先运行 biz_daily.py"。同样 WARN 后忽略；
- 排除和 `--include-all`、收录分是**正交**的：任何参数组合都不会把排掉的类带回来；
  报告末尾"我拿不准的"那三份清单也一起过滤，不会一边说不要新闻、一边在下面列新闻。

单个脚本上想临时看一次（不动配置）：

```powershell
python scripts/generate_ai_report.py --date 2026-09-05 --exclude-topics "新闻,投资"
python scripts/generate_html.py --date 2026-09-05 --exclude-topics "新闻,投资"
```

### 来源级先验：哪个号一贯发哪一类

日报跑完会在本地累加一张表（**仓库外**，`~/.weflow-cli/source_topics.json`）：每个公众号
被判断过多少篇、各属于哪一类。跑完会打两行，其中一行点名"已经稳到能判、且正好是你不
想看的那些类"的来源：

```
来源先验: 今天记了 187 篇，表里共 42 个来源（~/.weflow-cli/source_topics.json）
来源先验: 这些来源已经稳到能判，且正落在你配的排除主题里（只报数，不跳过）:
    某某号 → 新闻（24 篇里 23 篇 = 96%）
```

**它只报数，不跳过任何东西**：跳过是不可逆的，而这张表还在长。等某个来源攒够样本
（默认 8 篇、单一类占比 ≥80%）再由你决定要不要拿它做拉取前的筛选。计数只增不减——
它是你事后判断"这号稳不稳"的唯一依据。

- 相关度会写进 frontmatter 的 `relevance`（仍是「高/中/低」三个字），并额外写入
  `relevanceScore`（原始分值）与 `topicConfidence`（主题的置信度）。那三档的切点是
  暂定的，原始分留着，将来重新校准时不用重跑历史日报。
- 同一批问题里还问了一个**「该不该收进今天的日报」**（`includeScore`，0~1）。
  日报的收录门用它，而不是拿相关度顶替——相关度答的是"对读者的实用价值"，
  答不了"今天该不该收它"。切点默认 `0.5`，同样暂定；`generate_ai_report.py`
  里那个 `INCLUDE_THRESHOLD` 改起来不用重跑历史数据。想一次收全部：
  `python scripts/generate_ai_report.py --date <日期> --include-all`。
- `includeScore` 出现之前写下的老文章没有这个字段，会退回旧的
  「相关度 = 高才收」规则，所以**重新生成旧日期的报告不会突然换一批文章**。
- 单篇分类失败（网络、鉴权、超时）只影响那一篇，会打印一行 WARN 并退回 LLM 解析路径，
  不会让整天的日报中断。
- 分类要把文章标题与正文发往 `api.typesafe.ai`——和生成摘要发给 DeepSeek 是同一类动作，
  想完全不出网就用 `weflow-cli daily --no-ai`。
- 排查用 `python scripts/biz_daily.py --date <日期> --classifier llm`（强制老路径）对比。
- 只要判断、不要生成（连 DeepSeek key 都不需要）：`weflow-cli daily --no-summary`。摘要/标签/简报一律不生成，md 里不会有 `## AI 摘要` 段；主题与相关度仍由 Jev 判断。流水线里下游步骤（行动建议/概念编译/AI 报告）仍会用 LLM，要全关再加 `--skip-classify --skip-wiki --skip-ai-report`。

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

### 助手能读到多少：隐私三档，以及本地引擎这个例外

| `assistantPrivacy` | 工具拿到的聊天正文 | 出境 |
| --- | --- | --- |
| `strict`（默认） | 换成 `[内容N字已按严格模式屏蔽]` | 不出 |
| `balanced` | **原文**，但电话/证件/邮箱/密钥/链接被打成 `[电话]` 这类占位 | 出（到当前模型） |
| `open` | 原文，什么都不打 | 出 |

**换本地引擎时严格模式的屏蔽会自动让路**：`aiEngine=ollama` 或 `lmstudio` 时数据不出机器，
`maskMessageBodyText` 直接返回原文，不需要动 `assistantPrivacy`。所以"我想让它读得到聊天内容，
但又不想把内容发出去"的正解是**换引擎**，不是降档——降档等于把原文交给第三方。

```powershell
weflow-cli config set assistantPrivacy balanced   # 降档（正文会出境，PII 打码）
weflow-cli config set aiEngine ollama             # 或换本地引擎（内容不出机器）
```

在微信里发「**隐私**」可以随时问出当前档位、工具实际拿到的是原文还是被屏蔽、以及该执行哪条命令
（它只报不改——**不让一条微信消息能改隐私档位**，那等于把隐私开关搬进对话里）。

**改完必须重启助手**：配置是**启动时**读进进程内存的（`configService.get` 不回读磁盘），
所以对一个正在跑的助手，外部 `config set` 不生效——`assistant stop` 再 `assistant start`。

助手默认拒绝所有发送者，需明确设置 `assistantWhitelist`。群聊还需要群白名单、成员白名单和 @ 门槛；项目不通过客户端自动化或非官方协议拉群。

**第一次启用**：扫码一次，剩下的它会告诉你。

1. `weflow-cli login-wechat` —— 扫码登录消息通道（这一步只能人来做：iLink 只有扫码这一条官方登录路）；
2. `weflow-cli assistant start`；
3. 从你的微信给机器人发一条消息。**助手会拒绝它**（白名单为空 = 拒绝所有人），但日志里会出现一行
   `[首次配置]`，带着**你自己的发送者 ID** 和该执行的那条 `config set assistantWhitelist` ——
   照抄执行即可。之后它就开始回话了。

为什么不让登录自己把白名单写好：登录响应给的是 `ilink_user_id`，而白名单要的是入站消息里的
`from_user_id`（文档里写成 `@im.wechat ID`），**这两者是不是同一个值这个仓库里没有任何东西验证过**。
猜错的后果是"白名单非空、看着配好了、却仍然拒你"，而且提示也不会再出现（白名单已经不空了）——
所以这里宁可多一次复制粘贴。真值在第 3 步那条消息里，它自己会来。

想让助手先"只看不答"式地跑一段（记下它本来会怎么走，行为不改）：`config set assistantFastRoute log`。

**启动前先确认两件事**，否则 `assistant start` 会如实地告诉你它起不来：

1. **消息通道要已登录**（`weflow-cli login-wechat`）。没登录时子进程会退出，启动命令会把
   退出码和日志尾部原样报出来：`子进程启动后立即退出 (code 1)；日志尾部: Error: 未登录消息通道…`。
   这句话出现在终端里就说明机制是对的，**问题在通道，不在守护进程**。
2. **改了源码要先 `npm run build`**。守护进程优先运行 `dist/bin/weflow-cli.js`，只要这个文件
   存在就不会用 `bin/weflow-cli.ts`；不重建，守护进程跑的还是旧的编译产物。

助手还有一个**默认关闭**的单轮快路径（D-035）：先用本机判断层问一次"这条要不要查本机数据、
查哪一项"，把那个工具先跑掉，于是模型第一轮就看得到结果（两次往返变一次）。

```powershell
weflow-cli config set assistantFastRoute log   # 灰度：只记"本来会走哪条"，行为一个字不改
weflow-cli config set assistantFastRoute on    # 打开
weflow-cli config set assistantFastRoute off   # 关（默认）
```

还有一个**工具使用守卫**：如果路由判断"这条消息要查本机数据"，而这一轮**一次工具都没调**，它会把矛盾顶回去一次（提示模型"你手上没有工具结果"），让模型重答。审计里对应 `TOOL_GUARD_PUSHBACK`。
注意它与 `log` 的关系：`log` 的承诺是"**路由**不改变行为"，守卫不跟着一起关——它是安全行为，而观测期正是最该有它的时候（`off` 完全不介入，那时不问路由，也就没有触发的信号）。

灰度期看两个地方：`assistant log` 里的 `[快路径/只记] 路由到 X（...）`，以及
`~/.weflow-cli/assistant_audit.log` 里的 `FASTROUTE_WOULD` / `FASTROUTE_SKIP`（回退时那行会写
明原因：不需要查本机数据、置信度不足、能力名不认识…）。只有参数是固定集合的工具会被路由；
像"总结一下我和某某的聊天"这种要点名某个人的，一律回退到原来的循环——这是刻意的，硬凑参数
会让模型拿着不相关的结果自信作答。

启动失败时**不会**留下 pid 文件 —— 写 pid 就等于对外宣称它在运行。排查用
`weflow-cli assistant status`（不碰数据库）与 `weflow-cli assistant log`。

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
