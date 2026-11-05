#!/usr/bin/env python3
"""
全局语义搜索 — 基于阿里云百炼 Embedding API (text-embedding-v4) + NumPy。

用法:
  # 构建索引（首次或增量）
  python scripts/semantic_search.py build

  # 窗口可调（默认聊天 90 天 / 日报 30 天；要"整段历史"就给大值）
  python scripts/semantic_search.py build --days 3650 --article-days 365

  # 搜索（有索引用向量，否则关键词 fallback）
  python scripts/semantic_search.py search "有人推荐过遥感的工具吗" --top-k 10

  # 只看一条知识库（默认 wiki=文章线；`all` 才两条都看）
  python scripts/semantic_search.py search "项目排期" --line chat

  # 增量更新（只处理新数据）
  python scripts/semantic_search.py update

输出: JSON 格式（`search` 这一支是一个对象：`line` / `results` / `indexCounts` / 有时 `note`）
"""

import sys, os, json, math, hashlib, re, argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 重依赖一律懒加载（照 nt_decrypt.require_sqlcipher 的做法）：
# 关键词检索与重排不需要它们，缺了也不该让整份模块 import 就 sys.exit——
# 那样连不碰向量库的代码路径都用不了，CI 里也没法测任何东西。
np = None
sqlcipher = None


def require_numpy():
    global np
    if np is not None:
        return np
    try:
        import numpy as _np
    except ImportError:
        raise RuntimeError('需要 numpy: pip install numpy')
    np = _np
    return np


def require_sqlcipher():
    global sqlcipher
    if sqlcipher is not None:
        return sqlcipher
    try:
        from sqlcipher3 import dbapi2 as _sqlcipher
    except ImportError:
        raise RuntimeError('需要 sqlcipher3: pip install sqlcipher3')
    sqlcipher = _sqlcipher
    return sqlcipher

try:
    import urllib.request
    import urllib.parse
except:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _utils import (load_config, decrypt_lock, get_dashscope_key, get_api_key,  # noqa: E402
                    parse_frontmatter, LINE_LABELS)
from nt_common import decode_content  # noqa: E402
from jev_client import create_client

# `WEFLOW_OUTPUT_DIR` 要生效：只写死 'output' 会让所有"换个根目录做对照/实验"的用法
# 悄悄写进生产索引（2026-11-05 做分块对照时就这么踩了一次，把 60 篇样本灌进了真索引）。
# 默认值不变，只是不再忽略环境变量。
OUTPUT_ROOT = os.environ.get('WEFLOW_OUTPUT_DIR') or 'output'
INDEX_DIR = Path(OUTPUT_ROOT) / '.semantic_index'
VECTORS_FILE = INDEX_DIR / 'vectors.npy'
META_FILE = INDEX_DIR / 'meta.json'
EMBEDDING_DIM = 1024  # 阿里云 text-embedding-v4
BATCH_SIZE = 10  # 阿里云 text-embedding-v4 单次最多 10 条
# 每多少批往盘上落一次。实测全量重建要跑二十分钟以上，而 CLI 给子进程的超时只有五分钟
# （`search-index` 那条命令），"攒到最后才写"等于"超时/被杀就全丢"。
CHECKPOINT_BATCHES = 100
TZ = timezone(timedelta(hours=8))

# 两条知识库线在**索引里**的 `type` 值。索引是一份，这是**故意的**：两条线在同一次嵌入里
# 一起算，线靠记录上的 `type` 分（`article` / `chat`）——所以"按线检索"就是给相似度加一个
# 掩码，不需要第二份索引、也不用重新嵌入。
#
# 对外（`--line`、图谱、体检）一律说 `wiki` / `chat`，索引内部叫 `article` / `chat`，
# **翻译只在这里做一次**。散着写的话改词只会改到一半，而两条线的名字漂了不会报错。
# `pdf` 归**文章线**：它和公众号文章是同一类东西（"读过/要读的材料"，用户按内容找它），
# 而聊天线是"谁说过什么"。不加进来的后果是实测过的：PDF 明明进了索引，`search` 却一条都不返回——
# 白名单把它过滤掉了，而且不报错。
LINE_TYPES = {'wiki': ('article', 'pdf'), 'chat': ('chat',)}
LINE_CHOICES = ('all', 'wiki', 'chat')


def line_types(line: str):
    """线 → 索引里允许的 `type` 集合；`all` 返回 None（不过滤）。

    认不出的线**报错**，不悄悄当 all：`--line wik` 静默搜两条线的话，读的人会以为
    自己限定住了，而那正是"分开"没生效的样子。
    """
    if line == 'all':
        return None
    if line not in LINE_TYPES:
        raise ValueError('未知的线：%s（可选 %s）' % (line, '、'.join(LINE_CHOICES)))
    return LINE_TYPES[line]


def line_label(line: str) -> str:
    return LINE_LABELS.get(line, '两条线') if line != 'all' else '两条线'


def add_line_arg(parser):
    """三条线共用的 `--line`（默认**文章线**：两条线分开之后，不传就是只看文章那条，
    要合起来看显式给 `all`）。"""
    parser.add_argument('--line', choices=list(LINE_CHOICES), default='wiki',
                        help='只看一条知识库：wiki=文章线（默认）、chat=聊天线、all=两条都看')


def json_output(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


# ====== Embedding API ======

def get_embeddings(texts: list[str], api_key: str, on_batch=None) -> list[list[float]]:
    """Call 阿里云百炼 embedding API (OpenAI-compatible).

    `on_batch(done, total, batch_embeddings)` 每批调一次（可选）。建索引靠它**边嵌边落盘**：
    真实窗口上这个调用要跑二十分钟以上，而 CLI 给子进程的超时只有五分钟 —— "攒到最后才写"
    意味着超时或被杀就是"这一次花的钱和时间全丢，重跑还得再付一次"。检索那条路不传它。
    """
    if not texts:
        return []

    url = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    all_embeddings = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i:i + BATCH_SIZE]
        data = {
            "model": "text-embedding-v4",
            "input": batch,
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode('utf-8'),
            headers=headers,
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode('utf-8'))
                embeddings = [item['embedding'] for item in result['data']]
                all_embeddings.extend(embeddings)
                if on_batch:
                    on_batch(len(all_embeddings), len(texts), embeddings)
        except Exception as e:
            err_body = ''
            if hasattr(e, 'read'):
                try: err_body = e.read().decode()[:300]
                except: pass
            print(f"[WARN] Embedding API batch {i//BATCH_SIZE + 1} 失败: {e} {err_body}", file=sys.stderr)
            all_embeddings.extend([[0.0] * EMBEDDING_DIM for _ in batch])
            if on_batch:      # 失败也要报进度，否则看起来就是"卡住了"
                on_batch(len(all_embeddings), len(texts), [[0.0] * EMBEDDING_DIM] * len(batch))

    return all_embeddings


# ====== Data Collection ======

def open_db(db_path, key_hex, salt_hex, use_cache=False):
    """打开加密数据库，可选启用明文缓存
    
    Args:
        use_cache: 是否使用明文缓存（需要 WEFLOW_CACHE_ENABLED=1）
    """
    raw_key = f"x'{key_hex}{salt_hex}'"
    
    if use_cache:
        from database_cache import open_database_with_cache
        conn, _ = open_database_with_cache(db_path, raw_key, use_cache=True)
    else:
        sqlcipher = require_sqlcipher()
        conn = sqlcipher.connect(db_path)
        c = conn.cursor()
        c.execute(f'PRAGMA key = "{raw_key}";')
        c.execute("SELECT count(*) FROM sqlite_master")
    
    return conn


def get_name_map(contact_db, contact_key, contact_salt, use_cache=False):
    name_map = {}
    if not contact_db or not contact_key or not os.path.exists(contact_db):
        return name_map
    try:
        conn = open_db(contact_db, contact_key, contact_salt, use_cache=use_cache)
        c = conn.cursor()
        c.execute("SELECT username, COALESCE(NULLIF(remark,''), NULLIF(nick_name,''), username) FROM contact")
        for r in c.fetchall():
            name_map[r[0]] = r[1]
        conn.close()
    except:
        pass
    return name_map


def collect_chat_messages(conns, name_map, days=90, per_talker=500):
    """Collect chat messages for indexing. `conns` 是**分片连接列表**，不是单个连接。

    微信 4.x 的消息按分片存，而且**每片要自己派生的钥匙**才打得开（`nt_decrypt.
    derive_database_key`）。本机实测四片：`message_3.db` 里是近两天的消息，而配置指向的
    `message_0.db` 最新一条是 44 天前的。这里原来只开 `ntDbPath` 一个文件、还用裸的全局钥匙 ——
    于是索引的聊天半边会**静默地收进旧数据**（症状与 issue #10 的"只读到半个月前"同形）。
    索引是要花钱嵌的，收错数据比报错更贵。

    三处随之改动，各有理由：
      · 会话清单从所有分片取并集 —— 新会话的表可能只存在于新分片里；
      · 不再 `[:100]` 截断：本机 122 个会话，而且那 100 个是**无排序取前 100**（等于随机）；
      · id 里带 `rowid`：实测一张表里 12,106 条消息只落在 12,058 个不同秒上，只按 `talker:ts`
        去重会静默顶掉同秒的多条。
    """
    now = datetime.now(TZ)
    start_ts = int((now - timedelta(days=days)).timestamp())

    sessions = []
    for conn in conns:
        c = conn.cursor()
        try:
            c.execute("SELECT user_name FROM Name2Id WHERE is_session = 1")
            rows = [r[0] for r in c.fetchall()]
        except Exception:
            try:
                c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")
                rows = [r[0] for r in c.fetchall()]
            except Exception:
                rows = []
        for name in rows:
            if name not in sessions:
                sessions.append(name)

    items, seen = [], set()
    for talker in sessions:
        tbl = f"Msg_{hashlib.md5(talker.encode()).hexdigest()}"
        for conn in conns:
            c = conn.cursor()
            try:
                c.execute('SELECT COUNT(*) FROM sqlite_master WHERE name=?', (tbl,))
                if c.fetchone()[0] == 0:
                    continue
                c.execute(f'''
                    SELECT rowid, create_time, real_sender_id, message_content
                    FROM "{tbl}"
                    WHERE create_time >= ?
                    ORDER BY create_time DESC
                    LIMIT ?
                ''', (start_ts, per_talker))
                rows = c.fetchall()
            except Exception:
                continue
            for rid, ts, sender, content in rows:
                # 正文可能是 zstd 压缩的 BLOB（issue #12）：先解码再判断长度，
                # 否则这些消息会被整条跳过、索引里永远缺一段。
                content = decode_content(content)
                if len(content) < 10:
                    continue
                item_id = f"chat:{talker}:{ts}:{rid}"
                if item_id in seen:
                    continue
                seen.add(item_id)
                sender_name = name_map.get(sender, sender) if sender else name_map.get(talker, talker)
                dt = datetime.fromtimestamp(ts, tz=TZ)
                items.append({
                    "id": item_id,
                    "type": "chat",
                    "talker": name_map.get(talker, talker),
                    "sender": sender_name,
                    "time": dt.strftime('%Y-%m-%d %H:%M'),
                    "text": content[:500],
                })

    return items


def collect_imported_chat_items(days=90):
    """导入的 QQ 会话 → 与微信聊天同形状的索引记录（--include-imports）。

    形状必须与 collect_chat_messages 逐字段一致（id/type/talker/sender/time/text），
    否则同一条记录在两半索引里语义不同。id 带 qq: 段：与微信记录的
    chat:<talker>:<ts>:<rid> 天然不撞，也永远可分辨来源。文本阈值（>=10 字）与
    截断（500 字）同微信侧——索引是要花钱嵌的，两侧口径不同就是两种相关性。
    """
    import imported_chats

    items = []
    for sess in imported_chats.sessions():
        meta = sess.get('session') or {}
        talker = 'QQ-%s' % (meta.get('name') or meta.get('id') or '未命名')
        for m in imported_chats.session_messages(sess, days=days):
            text = (m.get('parsedContent') or '').strip()
            if len(text) < 10:
                continue
            dt = datetime.fromtimestamp(m.get('createTime') or 0, tz=TZ)
            items.append({
                'id': 'chat:qq:%s:%s' % (meta.get('id') or '', m.get('serverId') or ''),
                'type': 'chat',
                'talker': talker,
                'sender': m.get('senderName') or '',
                'time': dt.strftime('%Y-%m-%d %H:%M'),
                'text': text[:500],
            })
    return items


_ARTICLE_BOILERPLATE = frozenset({
    '在小说阅读器读本章', '去阅读', '在公众号小说中沉浸阅读',
})


def extract_article_body(content, limit=500):
    """从日报 md 里取"像正文"的一段 —— **这就是 2026-10-15 修的那个洞**。

    这里原来只有一句 `if line.startswith('## 正文')`，而实测最近 30 个日期目录的 10,759 篇里
    **没有一篇**含这个标记（真实结构是 `frontmatter → # 标题 → > 来源 → --- → ## AI 摘要 →
    --- → 重复标题 → 正文`），于是每一篇都退回到 `content[:1000]`：送去 embedding 的是 YAML 头
    （抽样 300 篇：100% 含 `title:`，汉字占比中位 0.16），正文一个字都没进索引。
    症状不是报错，而是**文章线的排序只能靠标题** —— 所以它值一个函数加一条测试，而不是再塞一句 if。

    取法：切掉 frontmatter，逐行丢掉标题/引用/图片/分隔线/纯符号行，攒到 limit 为止。
    `## AI 摘要` 的正文（一句话概括）会被收进来，它对检索有用。
    """
    lines = content.split('\n')
    if lines and lines[0].strip() == '---':
        for i in range(1, len(lines)):
            if lines[i].strip() == '---':
                lines = lines[i + 1:]
                break
    kept = []
    total = 0
    for raw in lines:
        s = raw.strip()
        if len(s) < 6 or s.startswith(('#', '>', '![', '|', 'http', '```')):
            continue
        if s in _ARTICLE_BOILERPLATE:
            continue
        if not any(c.isalnum() or '一' <= c <= '鿿' for c in s):
            continue                      # 纯符号行（分隔线、下划线横线）
        kept.append(s)
        total += len(s)
        if total >= limit:
            break
    return '\n'.join(kept)[:limit]

#: 文章分块的默认大小与重叠（字符）。400 字 ≈ 一段，60 字重叠足以跨句衔接。
DEFAULT_CHUNK_CHARS = 400
DEFAULT_CHUNK_OVERLAP = 60


def split_for_index(text, size=DEFAULT_CHUNK_CHARS, overlap=DEFAULT_CHUNK_OVERLAP):
    """把长文切成块：优先按段落，其次按中文句末标点，最后才硬切。

    为什么不用固定窗口硬切：中文文章的句末标点（。！？；…）是天然的语义边界，
    切在句子中间会让两块都"半句话"，两边都召不回来。
    """
    text = (text or '').strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    paras = [p for p in re.split(r'\n\s*\n', text) if p.strip()]
    chunks, buf = [], ''
    for para in paras:
        para = para.strip()
        if len(buf) + len(para) <= size:
            buf = (buf + '\n' + para).strip()
            continue
        if buf:
            chunks.append(buf)
        while len(para) > size:
            cut = -1
            for m in re.finditer(r'[。！？；…]', para[:size]):
                cut = m.end()
            if cut <= 0:
                cut = size
            chunks.append(para[:cut].strip())
            para = para[cut:].strip()
            if not para:
                break
        buf = para
    if buf:
        chunks.append(buf)
    if overlap and len(chunks) > 1:
        merged = []
        for i, c in enumerate(chunks):
            if i:
                c = (chunks[i - 1][-overlap:] + c).strip()
            merged.append(c)
        chunks = merged
    return [c for c in chunks if c]


def collect_articles(article_days=30, chunk_chars=0,
                    chunk_overlap=DEFAULT_CHUNK_OVERLAP):
    """Collect articles from biz-daily.

    `article_days` = 取最近多少个**日期目录**（原来写死 30）。可调是必要的：
    这个窗口决定了"知识库里有多少文章"，而它原先只写在常量里，用户看不到也改不了。
    """
    items = []
    daily_dir = Path(OUTPUT_ROOT) / 'biz-daily'
    if not daily_dir.exists():
        return items

    for date_dir in sorted(daily_dir.iterdir(), reverse=True)[:article_days]:
        if not date_dir.is_dir():
            continue
        for topic_dir in date_dir.iterdir():
            if not topic_dir.is_dir():
                continue
            # 这里原先是 topic_dir.glob(' marriage*.md')——前导空格加 marriage 前缀，
            # 在真实的「公众号-标题.md」命名下一个都匹配不到。后果是 collect_articles()
            # 永远返回空表，**语义索引里从来没有任何文章**，只有聊天记录，而且不报错。
            for f in topic_dir.glob('*.md'):
                if f.name == 'README.md':
                    continue
                try:
                    content = f.read_text(encoding='utf-8')
                    # Extract title and body
                    lines = content.split('\n')
                    title = ''
                    body_start = 0
                    for i, line in enumerate(lines):
                        if line.startswith('title:'):
                            title = line.split(':', 1)[1].strip().strip('"')
                        if line.startswith('## 正文'):
                            body_start = i + 1
                            break
                    # 有 `## 正文` 就用它（最精确的标记）；没有就按真实结构取正文，
                    # 不再退回到 YAML 头 —— 那正是"文章线只能靠标题排"的原因。
                    # 索引这一路**只用日报 md 那份**（`## 正文` 标记优先，否则按真实结构提取）。
                    # 2026-10-16 试过改成「与正文缓存取更长的那份」，实测**没有增益**（DECISIONS D-118）：
                    # 100 条自造题里唯一中立的一组（标题）持平；两组碎片题各自跟着「题是从谁的文本里
                    # 抠的」走，方向相反 —— 自造题裁判不了这件事。正文缓存留给 `--with-text`，索引不吃它。
                    body = ('\n'.join(lines[body_start:]) if body_start
                            else extract_article_body(content, limit=10 ** 9))
                    if not title:
                        continue
                    rel = f.relative_to(daily_dir)
                    if chunk_chars and chunk_chars > 0:
                        # 分块模式：读全篇、按语义边界切块，每块一条记录。
                        # parent 指回父文档，检索时按它聚合（同一篇只出一条）。
                        pieces = split_for_index(body, chunk_chars, chunk_overlap)
                        for idx, piece in enumerate(pieces):
                            items.append({
                                "id": f"article:{rel}#c{idx}",
                                "parent": f"article:{rel}",
                                "type": "article",
                                "title": title,
                                "date": date_dir.name,
                                "topic": topic_dir.name,
                                "chunkIndex": idx,
                                "chunkCount": len(pieces),
                                "text": f"{title}\n{piece}",
                            })
                    else:
                        items.append({
                            "id": f"article:{rel}",
                            "type": "article",
                            "title": title,
                            "date": date_dir.name,
                            "topic": topic_dir.name,
                            "text": f"{title}\n{body[:500]}",
                        })
                except:
                    pass

    return items


def collect_pdf_text(pdf_chunks=0, chunk_chars=1200):
    """收 `output/pdf-text/` 里的 PDF 文字（`pdf-notes --ocr-only` 的产物）。

    为什么要单独一路：这条线之前只覆盖公众号文章与聊天，**用户手上的 PDF 完全搜不到**——
    而 PDF 恰恰是最"存了就再也找不着"的那类材料（合同、财报、白皮书、扫描件）。

    与文章那边三点不同：
    · **按页切块**。文章几百字一篇，直接截 500 字进向量；一本书十几万字，截了就只剩开头。
      这里按 `### 第 N 页` 切、再按 `chunk_chars` 合并，每块单独成一条记录（各自一个 id），
      于是"第 137 页那段话"能被单独命中，而不是被整本平均掉。
    · **不读日报那层**（PDF 没有 biz-daily 副本），`text` 就是用来匹配的那段，
      返回时直接用（见 `load_record_text` 里的 `pdf:` 分支）。
    · `pdf_chunks>0` 时每个文件最多取这么多块（默认 0=不限），给"书太大、只想先试"留个闸门。

    切不出块的（没有页码标记）就整篇一块，不丢。
    """
    items = []
    root = Path(OUTPUT_ROOT) / 'pdf-text'
    if not root.exists():
        return items
    for f in sorted(root.glob('*.md')):
        try:
            content = f.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        frontmatter, body = parse_frontmatter(content)
        title = str(frontmatter.get('title') or f.stem).strip()
        saved = str(frontmatter.get('saved') or '')[:10]
        via = str(frontmatter.get('text_via') or '')
        pages = split_pdf_pages(body)
        chunks = merge_page_chunks(pages, chunk_chars)
        if pdf_chunks and pdf_chunks > 0:
            chunks = chunks[:pdf_chunks]
        for idx, (page_label, text) in enumerate(chunks):
            text = text.strip()
            if len(text) < 20:          # 空页/只有标题的页，不值得一条向量
                continue
            items.append({
                'id': f'pdf:{f.stem}#{idx}',
                'type': 'pdf',
                'title': f'{title}（{page_label}）' if page_label else title,
                'date': saved,
                'topic': 'PDF',
                'text': f'{title}\n{text}',
                'textVia': via,
            })
    return items


def split_pdf_pages(body):
    """把 `pdf-text` 的正文按 `### 第 N 页` 切成 [(页码标签, 该页文字)]。

    没有页码标记时整篇当一页返回（`pdf-notes` 之外的工具产出的 md 也就能吃）。
    """
    parts = re.split(r'^###\s*第\s*(\d+)\s*页\s*$', body, flags=re.M)
    if len(parts) < 3:
        return [('', body)]
    pages = []
    # parts = [前言, '1', 第1页, '2', 第2页, …]
    for i in range(1, len(parts) - 1, 2):
        pages.append((f'第 {parts[i]} 页', parts[i + 1]))
    return pages


def merge_page_chunks(pages, chunk_chars=1200):
    """把逐页文字合并成约 `chunk_chars` 大小的块，**页标记留在正文里**。

    为什么要合并：一页通常只有几百字，一页一条向量会让一本 300 页的书产生 300 次
    embedding 调用（每次只 10 条一批，就是 30 批）。按 ~1200 字合并后约 1/3 的量。

    为什么**不把页标记删掉**：命中之后要能说清"在第几页"。所以块正文里保留
    `### 第 N 页` 这类小标题，块标题取首页。一页本身超过上限时不拆（宁可超一点，
    拆了页码就对不上了）。
    """
    chunks = []
    buf, buf_label, size = [], '', 0
    for label, text in pages:
        text = text.strip()
        if not text:
            continue
        piece = f'### {label}\n{text}' if label else text
        if size and size + len(piece) > chunk_chars:
            chunks.append((buf_label, '\n'.join(buf)))
            buf, buf_label, size = [], '', 0
        if not buf_label:
            buf_label = label
        buf.append(piece)
        size += len(piece)
    if buf:
        chunks.append((buf_label, '\n'.join(buf)))
    return chunks


# ====== Index Operations ======

def _save_index(base_vectors, base_meta, pairs):
    """把「旧索引 + 这一批已经嵌好的」写出去，供中途落盘与收尾共用。

    两处讲究都是实测出来的：
    · **先写向量、再写 meta，各自"临时文件 + replace"**。两步之间被杀时，宁可 meta 少一条，
      也不能让它多一条 —— 多出来的那条指向不存在的向量行，检索时 argsort 的下标会越界，
      而 `search()` 里那个 `except: pass` 会把它变成"静默退回关键词"，那是最难看出来的一种。
    · 向量**逐行**归一化后再存（原来是整个矩阵一次性归一化）。归一化本来就是逐行的，
      重复做结果不变，所以中途落盘不会改变最终结果。
    """
    np = require_numpy()
    if pairs:
        new_vectors = np.array([v for _item, v in pairs], dtype=np.float32)
    else:
        new_vectors = np.empty((0, EMBEDDING_DIM), dtype=np.float32)
    vectors = np.vstack([base_vectors, new_vectors]) if base_vectors.size else new_vectors
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1                      # Avoid division by zero
    vectors = vectors / norms
    meta = list(base_meta) + [item for item, _v in pairs]

    tmp_v = VECTORS_FILE.with_name(VECTORS_FILE.name + '.tmp.npy')
    np.save(tmp_v, vectors)
    os.replace(tmp_v, VECTORS_FILE)
    tmp_m = META_FILE.with_name(META_FILE.name + '.tmp')
    tmp_m.write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp_m, META_FILE)
    return len(meta)

def build_index(api_key: str, full: bool = False, days: int = 90, article_days: int = 30,
                include_imports: bool = False, use_cache: bool = False, pdf_chunks: int = 0,
                article_chunk_chars: int = 0, article_chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
                include_chat: bool = True):
    """Build or update the semantic index.

    `days` / `article_days` 决定这个索引**记多久**：它们原先写死在两处调用里
    （聊天 90 天、日报 30 天），用户既看不见也改不了——而"个人知识库只有三个月记忆"
    正是那个写死的数字造成的。现在由 CLI 显式传进来（见 `search-index --days / --article-days`），
    默认值保持不变，**不悄悄改变代价**。
    """
    np = require_numpy()
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    print(f"窗口: 聊天最近 {days} 天 / 日报最近 {article_days} 天", file=sys.stderr)

    # Load existing index
    existing_ids = set()
    if not full and META_FILE.exists():
        meta = json.loads(META_FILE.read_text(encoding='utf-8'))
        existing_ids = {item['id'] for item in meta}
        print(f"现有索引: {len(existing_ids)} 条", file=sys.stderr)

    # Collect data
    print("收集数据...", file=sys.stderr)

    # Load DB
    config = load_config()
    nt_db = config.get('ntDbPath', '')
    # --include-imports 时没有微信库也能建索引（纯 QQ 用户是真实场景）；
    # 但要说出"聊天半边只有导入的"，不然窗口行读起来像微信也收了。
    if not nt_db and not include_imports:
        return {"error": "未初始化，请先运行 weflow-cli init"}
    if not nt_db and include_imports:
        print('[WARN] 未配置微信库：聊天半边只用导入的会话', file=sys.stderr)

    nt_key = decrypt_lock(config.get('ntKey', ''))
    nt_salt = config.get('ntSalt', '')

    # Get name map
    msg_dir = os.path.dirname(nt_db.replace('\\', '/'))
    wxid_dir = os.path.dirname(os.path.dirname(msg_dir))
    contact_db = os.path.join(wxid_dir, 'db_storage', 'contact', 'contact.db')
    contact_key_enc = config.get('contactKey', '')
    contact_salt = config.get('contactSalt', '')
    contact_key = decrypt_lock(contact_key_enc) if contact_key_enc else ''
    name_map = get_name_map(contact_db, contact_key, contact_salt, use_cache=use_cache)

    # Collect items
    items = []
    shards_failed = []
    if not nt_db:
        # 没有微信库：跳过 NT 那半（name_map/分片都不存在），导入与文章照收。
        # 收完直接走同一个收尾（嵌入与写盘），不在这里复制粘贴后半段。
        if include_imports:
            items.extend(collect_imported_chat_items(days=days))
        items.extend(collect_articles(article_days=article_days,
                                      chunk_chars=article_chunk_chars,
                                      chunk_overlap=article_chunk_overlap))
        items.extend(collect_pdf_text(pdf_chunks=pdf_chunks))
        new_items = [item for item in items if item['id'] not in existing_ids]
        print(f"总数据: {len(items)} 条, 新数据: {len(new_items)} 条", file=sys.stderr)
        return _build_embed_and_write(api_key, items, new_items, existing_ids,
                                   full, days, article_days, shards_failed=[])
    try:
        if not include_chat:
            raise RuntimeError('skip-chat')
        import nt_decrypt  # 懒加载：纯逻辑路径（关键词/重排/测试）不该被它拖住
        passphrase = decrypt_lock(config.get('favPassphrase') or config.get('decryptKey') or '')
        pairs, failures = nt_decrypt.connect_message_shards_detailed(
            nt_db, nt_key, nt_salt, passphrase)
        shards_failed = [f['name'] for f in failures]
        if shards_failed:
            print(f"[WARN] {len(shards_failed)} 个分片读不了: {', '.join(shards_failed)}",
                  file=sys.stderr)
        try:
            chat_items = collect_chat_messages([c for _p, c in pairs], name_map, days=days)
            items.extend(chat_items)
        finally:
            for _p, c in pairs:
                try:
                    c.close()
                except Exception:
                    pass
    except Exception as e:
        print(f"[WARN] 聊天消息收集失败: {e}", file=sys.stderr)

    if include_imports:
        imported = collect_imported_chat_items(days=days)
        items.extend(imported)
        print(f"导入会话: +{len(imported)} 条", file=sys.stderr)

    article_items = collect_articles(article_days=article_days,
                                      chunk_chars=article_chunk_chars,
                                      chunk_overlap=article_chunk_overlap)
    items.extend(article_items)
    pdf_items = collect_pdf_text(pdf_chunks=pdf_chunks)
    items.extend(pdf_items)
    if pdf_items:
        print(f"PDF 文字: {len(pdf_items)} 块（来自 output/pdf-text）", file=sys.stderr)

    new_items = [item for item in items if item['id'] not in existing_ids]
    print(f"总数据: {len(items)} 条, 新数据: {len(new_items)} 条", file=sys.stderr)

    return _build_embed_and_write(api_key, items, new_items, existing_ids,
                                   full, days, article_days, shards_failed)



def _build_embed_and_write(api_key, items, new_items, existing_ids, full,
                           days, article_days, shards_failed):
    """build_index 的收尾：去重过滤在调用方做完了，这里管嵌入、断点落盘与返回。

    抽出来是因为聊天半边现在有两条收集路（微信库 / 导入会话），尾巴必须只有一份——
    复制一份的代价是断点续跑、全零批次不落盘这些坑只在一半路径上修过。
    """
    if not new_items:
        return {"status": "up_to_date", "total": len(items),
                "chatDays": days, "articleDays": article_days,
                "shardsFailed": shards_failed}

    texts = [item['text'] for item in new_items]
    total_new = len(texts)
    print(f"生成 embeddings...（{total_new} 条，共 {-(-total_new // BATCH_SIZE)} 批）", file=sys.stderr)

    # 旧索引先读进来：增量时它是底，中途落盘与收尾都要以它开头。
    if VECTORS_FILE.exists() and not full:
        base_vectors = np.load(VECTORS_FILE)
        base_meta = json.loads(META_FILE.read_text(encoding='utf-8'))
    else:
        base_vectors = np.empty((0, EMBEDDING_DIM), dtype=np.float32)
        base_meta = []

    ok_pairs = []          # [(item, vector)] —— 真嵌出来的才配进索引
    state = {'failed': 0, 'flushed': 0}

    def on_batch(done, total, batch_embs):
        """边嵌边落盘，并且把"全零"那批（请求失败）挑出来**不写**。

        两件事都是实测踩出来的：
        · 这个索引要跑二十分钟以上，而 CLI 给子进程的超时是五分钟 —— 攒到最后才写就是
          "超时/被杀等于全丢"；每 CHECKPOINT_BATCHES 批落一次盘，被杀也只丢最后那几千条，
          而且重跑 `update` 会从断点接着来（已落盘的 id 就在 meta 里）。
        · 失败批次拿到的是全零向量。照旧写进去的话，那些 id 会永久躺在索引里**再也不会被重嵌**
          （增量只嵌新 id）—— 索引被安静地投毒。所以它们不写，留到下次重跑。
        """
        start = done - len(batch_embs)
        for k, emb in enumerate(batch_embs):
            if any(emb):
                ok_pairs.append((new_items[start + k], emb))
            else:
                state['failed'] += 1
        if done >= total or done - state['flushed'] >= CHECKPOINT_BATCHES * BATCH_SIZE:
            _save_index(base_vectors, base_meta, ok_pairs)
            state['flushed'] = done
            print(f"[索引] 已嵌入 {done}/{total}（落盘 {len(ok_pairs)} 条，"
                  f"失败 {state['failed']} 条）", file=sys.stderr)

    get_embeddings(texts, api_key, on_batch=on_batch)

    if not ok_pairs:
        return {"error": "Embedding 生成失败，请检查 API key",
                "embedFailed": state['failed'],
                "chatDays": days, "articleDays": article_days}

    return {
        "status": "success",
        "total": len(base_meta) + len(ok_pairs),
        "new": len(ok_pairs),
        # 嵌不出来的那几个**要说出来**：它们既不在索引里，也是下次重跑的输入
        "embedFailed": state['failed'],
        "chatDays": days,
        "articleDays": article_days,
        # 分片读不全时**要报出来**：`shardsFailed` 是"全读了"与"读了能读到的"之差，
        # 正是覆盖度报告存在的理由（nt_decrypt.connect_message_shards_detailed 的原话）。
        "shardsFailed": shards_failed,
    }


def keyword_search(query: str, top_k: int = 10, root=None, line: str = 'all'):
    """Simple keyword fallback: scan articles + messages for keyword matches.

    `root` 只为可测而参数化（默认就是日报目录）。它原先硬编码成相对路径，
    于是这条路径只能靠 chdir 才能测，也就一直没被测过——而它正是检索不可用时
    真正跑着的那条（本机索引从未构建过）。

    `line` 在这里**必须真的把两条路分开**：这一段的两个来源正好一人一条线
    （日报文章 = 文章线、`mcp_bridge` 的聊天消息 = 聊天线）。
    """
    results = []
    keywords = query.lower().split()
    types = line_types(line)
    want_article = types is None or 'article' in types
    want_chat = types is None or 'chat' in types

    # Scan biz-daily articles
    biz_dir = Path(root or 'output/biz-daily')
    if want_article and biz_dir.exists():
        for md_file in sorted(biz_dir.rglob('*.md'), reverse=True):
            # **只收真文章。** 日报目录里还躺着 <天>/README.md、<天>/行动建议.md
            # 这类**生成产物**，它们会被当成检索结果返回（实测搜到过
            # 「行动建议 — 2026-08-30」）。而它们**两种深度都有**：既在日期目录下、
            # 也在主题目录下（实测各 4 个），所以按路径判断是不够的——我第一版就是这么
            # 写的，然后拿一个"看不见产物"的 top_k 去验证，误以为修好了。
            #
            # 判据用内容：真文章的 frontmatter 里一定有 url。
            # `quality_eval.read_article` 用的是同一条。
            try:
                content = md_file.read_text(encoding='utf-8')[:5000]
            except OSError:
                continue
            if not re.search(r'^url:\s*\S', content[:800], re.M):
                continue
            score = sum(content.lower().count(kw) for kw in keywords)
            if score > 0:
                title = content.split('\n')[0].lstrip('# ').strip() if content.startswith('#') else md_file.stem
                results.append({
                    'title': title,
                    'source': str(md_file.relative_to(biz_dir)),
                    'score': score,
                    'text': content[:300].strip(),
                })

    # Scan chat messages from mcp_bridge
    if want_chat:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from mcp_bridge import search_messages as _search_msg
            msg_results = _search_msg(query, 50)
            for r in msg_results.get('results', [])[:top_k]:
                results.append({
                    'title': f"[{r.get('time','')}] {r.get('talker','')} > {r.get('sender','')}",
                    'source': '微信聊天',
                    'score': len(query),
                    'text': r.get('content', '')[:200],
                })
        except:
            pass

    results.sort(key=lambda x: -x['score'])
    return results[:top_k]


# 送进重排的候选数。检索是粗筛（"语义相近"），重排是细判（"真的回答了问题吗"），
# 所以池子要比 top_k 大，才有可换的空间。
# 送进重排的候选数。**2026-10-16 实测把默认从 20 提到 100**：同一批 40 题、只改池子
# （POOL_CURVE = 20 / 50 / 100 / 200）：
#     recall@10  0.82 / 0.88 / 0.95 / 0.95   hit@1  0.65 / 0.68 / 0.72 / 0.75
#     MRR@10     0.72 / 0.75 / 0.80 / 0.82   nDCG@10 0.75 / 0.78 / 0.83 / 0.85
# 最弱的"碎片/文章"那格 recall@10：0.70 → 0.80 → **1.00** → 1.00。到 100 就饱和，
# 200 的增益是噪声级。代价是每次重排的提示从 ~2.5k tokens 涨到 ~20k（约 ¥0.01/次），
# 所以池子做成可调（`--rerank-pool`），嫌贵就调小。
RERANK_CANDIDATES = 100
RERANK_EXCERPT_CHARS = 300


def build_rerank_questions(count):
    """**一次请求问 N 个候选**——这是 Jev 最适合"重排"的地方。

    实测 2 个问题 0.84s、12 个问题 0.91s（`state` 才是开销大头），所以给 20 条候选
    各问一个是 0.9 秒量级，不是 20 次往返。问题编号与候选编号必须严格对应：
    错位了不会报错，只会把最相关的排到最后——所以每条候选在 state 里都带【候选k】标签。
    """
    return {
        'c%d' % i: {'type': 'noul',
                    'instructions': '【候选%d】是否直接回答了上面的问题，'
                                    '或含有回答它所需的关键信息？仅话题相近不算。' % i}
        for i in range(count)
    }


# 这一趟重排是谁排的、有没有降级 —— 必须能带进输出。
# 原来的写法里"重排失败"只往 stderr 打一行 WARN，而 CLI 不转发子进程 stderr，
# 于是"没报错"会被读成"重排过了"。状态放这里，主流程把它塞进 JSON。
RERANK_STATE = {'provider': None, 'degraded': False, 'error': None, 'scored': 0}
# 向量那条腿这次到底跑了没有、没跑是为什么、以及顺手算出的每线条数（省一次 13.5 MB 的解析）。
# 与 `RERANK_STATE` 同一个形状：`search()` 开头重置、`main()` 把它带进返回的 envelope。
#
# 为什么要这个：`search()` 原来有一个**裸 `except: pass`**，于是"向量路径任何异常"都会变成
# "静默只搜关键词"，而返回里照样写着 `hybrid: true` —— 这是最难看出来的一种失效，
# 也让所有"快慢/好坏"的对比都可能落在不同路径上（2026-10-18 审阅时确认）。
VECTOR_STATE = {'used': False, 'fallback': None, 'counts': None}

_RERANK_LLM_RUBRIC = (
    '给每条候选打 0-100 分，判据是**它是否直接回答了问题**（"话题相近"不算）：'
    '100 = 直接给出所需信息；60-80 = 含大部分相关信息；30-50 = 只沾边；0-20 = 无关。'
    '每条候选都必须有分数。只输出 JSON：{"scores":[{"i":1,"score":85},{"i":2,"score":20}]}。')


def cleanse_passage(text, limit=RERANK_EXCERPT_CHARS):
    """送进重排前的段落清洗（照 WeKnora 的 passage cleansing）。

    不清洗的话，模型有相当一部分注意力花在 `**加粗**`、公众号套话、作者署名、"点击蓝字"这些噪声上，
    而候选摘录本来就只有 300 字。清洗是纯文本操作，不改判据。
    """
    s = text or ''
    s = re.sub(r'!\[[^\]]*\]\([^)]*\)', ' ', s)          # 图片
    s = re.sub(r'\[([^\]]{0,40})\]\([^)]*\)', r'\1', s)  # 链接留文字
    s = re.sub(r'\*{1,3}', '', s)                        # 加粗/斜体标记
    s = re.sub(r'(点击蓝字|关注我们|阅读原文|继续滑动看下一个|轻触阅读原文|设为星标)', ' ', s)
    s = re.sub(r'^\s*原创\s+\S+.*$', ' ', s, flags=re.M)  # 署名行
    s = re.sub(r'\s+', ' ', s)
    return s.strip()[:limit]


def llm_rerank(query, results, pool_size=RERANK_CANDIDATES, key=None, model=None):
    """用**已配置的对话模型**（OpenAI 兼容）重排。

    为什么要有它：这条管线本来是"召回 + 重排"两段，而重排在本机一直是**空转**的
    （Typesafe 返 `HTTP 451 not available in your region`），所以排序只有一段。
    契约与 Jev 那版同构：一次请求给全部候选打分；校验不过就**保持原序并把降级说出来**。
    """
    if len(results) < 2:
        return results
    try:
        cfg = load_config()
        key = key or get_api_key(cfg) or cfg.get('deepseekApiKey') or ''
        model = model or cfg.get('aiModel') or 'deepseek-chat'
    except Exception as error:      # noqa: BLE001
        # 读不到配置（这台机器可能还没有 ~/.weflow-cli/config.json）也要**保持原序**：
        # 重排不该让检索失败，更不能一路抛到 `search()` 的 `except: pass` 里
        # —— 那会**静默退回关键词检索**，最难看出来的一种失效。
        print('  [WARN] 重排读不到配置（%s），保持原顺序' % type(error).__name__,
              file=sys.stderr)
        RERANK_STATE.update(provider='llm', degraded=True,
                            error='config-unreadable', scored=0)
        return results
    if not key:
        RERANK_STATE.update(provider='llm', degraded=True, error='no-chat-key', scored=0)
        return results

    pool = results[:pool_size]
    lines = []
    for i, item in enumerate(pool):
        lines.append('【候选%d】%s｜%s' % (i + 1, item.get('title') or item.get('source') or '',
                                          cleanse_passage(item.get('text') or '')))
    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': '问题：%s\n\n候选：\n%s\n\n%s'
                      % (query, chr(10).join(lines), _RERANK_LLM_RUBRIC)}],
        'response_format': {'type': 'json_object'}, 'temperature': 0,
    }
    request = urllib.request.Request('https://api.deepseek.com/v1/chat/completions',
                                     data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                                     headers={'Authorization': 'Bearer %s' % key,
                                              'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        scores = {int(row['i']): float(row['score'])
                  for row in json.loads(data['choices'][0]['message']['content'])['scores']}
    except Exception as error:                     # noqa: BLE001 —— 重排不该让检索失败
        print('  [WARN] 重排（%s）失败：%s: %s —— 保持原顺序'
              % (model, type(error).__name__, error), file=sys.stderr)
        RERANK_STATE.update(provider='llm', degraded=True,
                            error=type(error).__name__, scored=0)
        return results

    if set(scores) != set(range(1, len(pool) + 1)):
        # 编号不齐 = 模型把候选搞混了 —— 这正是重排最危险、最不容易察觉的失效方式，宁可原序
        print('  [WARN] 重排返回的候选编号不齐（%d/%d），保持原顺序'
              % (len(scores), len(pool)), file=sys.stderr)
        RERANK_STATE.update(provider='llm', degraded=True, error='incomplete-scores', scored=0)
        return results

    order = sorted(range(len(pool)), key=lambda i: (-scores[i + 1], i))
    ranked = []
    for i in order:
        item = dict(pool[i])
        item['rerankScore'] = scores[i + 1]      # `score` 保持原义，新的放这里
        ranked.append(item)
    RERANK_STATE.update(provider='llm', degraded=False, error=None, scored=len(ranked))
    return ranked + list(results[pool_size:])


def rerank_dispatch(query, results, client=None, pool_size=RERANK_CANDIDATES):
    """先按原契约（Jev/Typesafe）重排，不可用或失败则退到已配置的对话模型。

    两个都失败时返回原序，并把 `RERANK_STATE` 留在"降级"上 —— 别让"没重排"看起来像"重排过了"。
    """
    RERANK_STATE.update(provider=None, degraded=False, error=None, scored=0, transport=None)
    if client is not None:
        out = rerank(query, results, client, pool_size=pool_size)
        # **`provider` 记的是"哪套契约"**（`typesafe`），不是"谁在服务"：`judgeProvider=chat` 时
        # 契约是由 DeepSeek 类客户端满足的，只看 `provider` 会读成"原生通了"（2026-10-18 实测踩到：
        # 同一批查询里有时 provider=typesafe 其实走的是 deepseek-flash）。所以再记一个 `transport`。
        RERANK_STATE['transport'] = getattr(client, 'provider', 'typesafe')
        if not RERANK_STATE['degraded'] and RERANK_STATE['scored']:
            return out
    RERANK_STATE['transport'] = 'llm'
    return llm_rerank(query, results, pool_size=pool_size)
def rerank(query, results, client=None, pool_size=RERANK_CANDIDATES):
    """用**一次**决策请求给检索结果重排。

    没有客户端、候选太少、或调用失败 → 原样返回（也就是这个函数还不存在时的行为）。
    这是刻意的：重排是加分项，不该让检索本身失败。

    `score` 保持原义（余弦相似度或关键词命中数），新的分数放在 `rerankScore` 里，
    与 `relevanceScore`/`includeScore` 同一套只增字段的做法——下游可能在展示 score。
    """
    if client is None or len(results) < 2:
        return results

    pool = results[:pool_size]
    lines = []
    for i, item in enumerate(pool):
        excerpt = (item.get('text') or '').strip().replace(chr(10), ' ')
        lines.append('【候选%d】%s｜%s' % (i, item.get('title') or item.get('source') or '',
                                          excerpt[:RERANK_EXCERPT_CHARS]))
    state = '问题：%s\n\n候选：\n%s' % (query, chr(10).join(lines))
    questions = build_rerank_questions(len(pool))
    # 同问两次的一致性检查：哪一条最直接地回答了问题。逐条打分与这个单选对不上，
    # 通常意味着编号被模型搞混了——那正是重排最危险、也最不容易察觉的失效方式。
    questions['best'] = {
        'type': 'choice',
        'instructions': '哪一条候选最直接地回答了上面的问题？',
        'criteria': {'候选%d' % i: (item.get('title') or None)
                     for i, item in enumerate(pool)},
    }

    try:
        answers, _usage = client.decide(state, questions)
    except Exception as error:
        # `file=sys.stderr`：`--json` 时 stdout 是机器在解析的那一份（同文件其它 WARN 早就这样）
        print('  [WARN] 重排失败（%s），保持原检索顺序：%s' % (type(error).__name__, error),
              file=sys.stderr)
        RERANK_STATE.update(provider='typesafe', degraded=True,
                            error=type(error).__name__, scored=0)
        return results

    scored = []
    for i, item in enumerate(pool):
        value = (answers.get('c%d' % i) or {}).get('noul')
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = None
        scored.append((value, i, item))

    if all(value is None for value, _i, _item in scored):
        print('  [WARN] 重排没有返回任何可用分数，保持原检索顺序', file=sys.stderr)
        RERANK_STATE.update(provider='typesafe', degraded=True,
                            error='no-usable-scores', scored=0)
        return results

    # 没拿到分数的按原序垫在最后，而不是当成 0 分——"未知"和"不相关"不是一回事。
    scored.sort(key=lambda triple: (triple[0] is None, -(triple[0] or 0.0), triple[1]))
    best = (answers.get('best') or {}).get('choice')
    top = scored[0]
    if best is not None and best != '候选%d' % top[1]:
        print('  [WARN] 重排自检不一致：逐条打分最高的是候选%d，单选却答 %s'
              % (top[1], best), file=sys.stderr)

    ordered = []
    for value, i, item in scored:
        entry = dict(item)
        if value is not None:
            entry['rerankScore'] = round(value, 3)
        ordered.append(entry)
    ordered.extend(results[pool_size:])
    RERANK_STATE.update(provider='typesafe', degraded=False, error=None,
                        scored=len(ordered))
    return ordered


# 混合检索：向量名次 + 本地关键词名次（BM25），用 RRF 合成。纯本地、不联网、不加依赖。
#
# **默认开**，`--no-hybrid` 关 —— 这个默认值是分两步量出来的（都记在 D-110 / D-111）：
#   · 第一步（2026-10-15 上午）：**无门控**的 RRF 单独用是**有害**的。真实问句 5 条里只在 1 条上更好、
#     2 条更差；逐字碎片 16 条上 BM25 单独 16/16、RRF 只有 12/16。原因是 RRF 给两侧同等票权，而中文
#     问句里的虚词（怎么用/有没有/的）会把 BM25 侧变成纯噪声。当时结论：默认关。
#   · 第二步（同日下午，加了门控）：只让"命中稀有词、且分数接近最高分"的文档投票；问句里**没有稀有词
#     就整个弃权**。同一批题（frag 40 / title 40 / 手写问句 12）重测：
#         碎片 recall@10 0.91 → **1.00**，@5 0.89 → 0.97，hit@1 0.60 → **0.80**
#         标题 recall@10 0.97（不变），@5 0.91 → 0.94，hit@1 0.83 → 0.86
#         手写问句 三档全不变（0.75/0.75/0.75）
#     **三个台子都不掉**，于是翻成默认。无门控版同一批题零增益（0.91/0.60，与纯向量相同）——
#     也就是说：让关键词侧起作用的是**门控**，不是融合本身。
#   · 阈值敏感性（同一批题）：宽松 (0.10/0.3) 弱一点（碎片 @5 0.94），紧的 (0.02/0.7) 略好
#     （@5 1.00）—— 平台是平的，说明这个数字不承重；取中间值，且**不许**再拿这一批题去微调它。
RRF_K = 60          # RRF 的平滑常数：名次差在几十名以内才有区分度（教科书默认值）
RRF_POOL = 200      # 两侧各取前多少名进融合；再往后的名次贡献 1/(60+200) 已经可以忽略
BM25_K1 = 1.2
BM25_B = 0.75

# 门控用：稀有词阈值（词出现在 ≤5% 的在范围文档里才算"稀有"）与分数相对阈
HYBRID_RARE_RATIO = 0.05
HYBRID_SCORE_RATIO = 0.5

_CJK_RUN = re.compile(r'[一-鿿]+')
_ASCII_WORD = re.compile(r'[a-z0-9]{2,}')


def query_terms(text):
    """把查询切成检索词：中文按**字符二元组**（没有分词器，二元组对中文足够稳），ASCII 按词。

    二元组是刻意选的：它不需要词表、不会因为新词失手，代价是"的/了/在"这类高频字也会成词——
    那由 BM25 的 idf 自己压下去（df 越大分越低），不用手写停用词表。
    """
    terms = []
    for run in _CJK_RUN.findall(text):
        if len(run) == 1:
            terms.append(run)
        else:
            terms.extend(run[i:i + 2] for i in range(len(run) - 1))
    terms.extend(_ASCII_WORD.findall(text.lower()))
    seen, out = set(), []
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def keyword_rank(query, meta, in_scope=None, limit=RRF_POOL,
                  rare_ratio=None, score_ratio=None):
    """本地 BM25 名次。返回 [(下标, 分数)]，按分数降序、**并列按下标升序**。

    一遍扫过所有记录，同时算 df 与每条的 tf —— 不建额外索引文件，也就没有"索引与正文不同步"
    这种失效。`in_scope` 是与向量侧**同一个掩码**：关键词这一侧也不能把另一条线的记录带进来。

    `limit` 只截名次表（RRF 只看名次，第 200 名之后的贡献可以忽略），不影响打分。
    """
    terms = query_terms(query)
    if not terms:
        return []
    docs = []          # [(下标, 小写文本)]，只含在范围内的
    for i, m in enumerate(meta):
        if in_scope is not None and not in_scope[i]:
            continue
        t = (m.get('text') or '').lower()
        if t:
            docs.append((i, t))
    if not docs:
        return []

    tf, df, total_len = [], {}, 0
    for _i, t in docs:
        total_len += len(t)
        row = {}
        for term in terms:
            c = t.count(term)
            if c:
                row[term] = c
        if row:
            for term in row:
                df[term] = df.get(term, 0) + 1
        tf.append(row or None)

    n_docs = len(docs)
    avgdl = (total_len / n_docs) or 1.0

    # 门控（1）：**没有稀有词就弃权**。中文问句里的"怎么用/有没有/的"会成词，但它们在语料里
    # 到处都是 —— 只命中这类词的文档不该来投票（无门控的 RRF 正是栽在这里，见 D-110）。
    rare = None
    if rare_ratio is not None:
        rare = {t for t, d in df.items() if d and d / n_docs <= rare_ratio}
        if not rare:
            return []

    scored = []
    for (idx, t), row in zip(docs, tf):
        if not row:
            continue
        if rare is not None and not (set(row) & rare):
            continue
        score = 0.0
        for term, c in row.items():
            d = df[term]
            idf = math.log(1 + (n_docs - d + 0.5) / (d + 0.5))
            denom = c + BM25_K1 * (1 - BM25_B + BM25_B * len(t) / avgdl)
            score += idf * c * (BM25_K1 + 1) / denom
        scored.append((idx, score))

    # 门控（2）：长词的垃圾二元组（"器学""习或"）同样稀有 ⇒ 再用"分数接近最高分"筛一道。
    if score_ratio is not None and scored:
        top = max(s for _i, s in scored)
        scored = [(idx, s) for idx, s in scored if s >= score_ratio * top]

    scored.sort(key=lambda kv: (-kv[1], kv[0]))
    return scored[:limit]


def rrf_fuse(rank_lists, k=RRF_K):
    """Reciprocal Rank Fusion：只吃**名次**、不吃分数，所以两侧分数量纲不同也没关系。

    并列时的次序按下标升序定死 —— 这个仓库对"同一批数据两次跑给出不同顺序"很敏感
    （缓存会把偶然结果固化），所以不让它取决于 dict 的插入顺序。
    """
    score = {}
    for lst in rank_lists:
        for rank, idx in enumerate(lst):
            score[idx] = score.get(idx, 0.0) + 1.0 / (k + rank + 1)
    return sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))

MMR_LAMBDA = 0.7                 # 照 WeKnora 的取值：相关性为主、多样性为辅


def mmr_select(items, vectors, k, lam=MMR_LAMBDA):
    """在**已经排好序**的候选里挑 k 条：又相关、又彼此不像（Maximal Marginal Relevance）。

    为什么要有它：这条语料里"同领域邻居"很多（实测 top-5 常有三四条在讲同一件事），它们吃掉名额
    却不增加信息。MMR 给每条的边际价值 = λ×相关性 − (1−λ)×与**已选条目**的最大相似度，逐条贪心挑。

    相关性取重排分（0-100 归一化到 0-1），没有重排分就退回余弦；相似度用向量点积（存的就是归一化的）。
    条目必须带 `_row`（在 meta/向量里的行号）—— `search()` 在重排前挂上、返回前摘掉。
    与"同文本折叠"（`dedup_results`）不是一回事：那个折叠一模一样的内容，这个压的是**近似**的内容。
    """
    np = require_numpy()
    if k <= 0 or not items:
        return items
    rows, rel = [], []
    for item in items:
        row = item.get('_row')
        if row is None:
            return items                      # 拿不到行号就不动它，别猜
        rows.append(int(row))
        score = item.get('rerankScore')
        rel.append((float(score) / 100.0) if isinstance(score, (int, float)) else
                   float(item.get('score') or 0.0))
    matrix = np.asarray(vectors[rows], dtype=np.float32)
    rel = np.asarray(rel, dtype=np.float32)

    chosen, remaining = [], list(range(len(items)))
    max_sim = np.zeros(len(items), dtype=np.float32)
    while remaining and len(chosen) < k:
        gain = lam * rel[remaining] - (1 - lam) * max_sim[remaining]
        best = remaining[int(np.argmax(gain))]
        chosen.append(best)
        remaining.remove(best)
        sims = matrix @ matrix[best]
        max_sim = np.maximum(max_sim, sims)
    rest = [i for i in range(len(items)) if i not in set(chosen)]
    return [items[i] for i in chosen + rest]

def dedup_results(results):
    """同一段文本只留排名最高的那条，被折叠的账号数记在 `duplicateCount` 里。

    为什么在**取出结果时**做：多号转载是这语料的常态（实测 2026-10-16：20 条问句的 top-5 里 7% 是重复文本，
    6/20 条问句的 top-5 出现过重复），重复占的是名额而不是信息。不做成"建索引时去重"是因为那会破坏
    `--from <公众号>` 这类按号查（被折叠的账号仍在 id 里）；所以留着索引，折叠留给展示。
    `duplicateCount` 让用户知道"同一篇被几个号发过"，而不是以为丢了什么。
    """
    seen, out = {}, []
    for item in results:
        text = item.get('text') or ''
        if not text:            # 没有正文的记录各算各的：空串之间不是"同一段"
            out.append({**item, 'duplicateCount': 1})
            continue
        h = hash(text)
        if h in seen:
            seen[h]['duplicateCount'] = seen[h].get('duplicateCount', 1) + 1
            continue
        entry = dict(item)
        entry['duplicateCount'] = 1
        seen[h] = entry
        out.append(entry)
    return out
# 正文里按"行"删掉的公众号套话（只删整行，不动正文句子）
_BOILERPLATE_LINE = re.compile(r'^\s*(在小说阅读器读本章|去阅读|在公众号小说中沉浸阅读|点击蓝字|关注我们|一键关注|设为星标|预览时标签不可点|阅读原文|微信扫一扫|关注该公众号|写留言|点个在看|点赞|在看|不喜欢|______+|-{3,})\s*$')


def readable_body(text):
    """把正文整理成"能读"的样子：去加粗/斜体标记与公众号套话行，**保留段落**。

    与重排那份摘录（`cleanse_passage`）不同：那个会把空白压成一行、还截断到 300 字，
    因为它只是喂给模型的；这一份是给人看的，所以保留换行。原始文件路径在 `sourceFile` 里，
    想核对"有没有被我改过"就打开那个文件对比。
    """
    out = []
    for raw_line in (text or '').split('\n'):
        line = re.sub(r'!\[[^\]]*\]\([^)]*\)', ' ', raw_line)          # 图片语法
        line = re.sub(r'\[([^\]]{0,80})\]\([^)]*\)', r'\1', line)     # 链接留文字
        line = re.sub(r'\*{1,3}', '', line).strip()
        if not line or _BOILERPLATE_LINE.match(line):
            continue
        out.append(line)
    return '\n'.join(out)

FULL_TEXT_MIN_CHARS = 300        # `--fetch-missing` 的判据：正文短于这个数就算"没抓到正文"


def _fetch_body(url):
    """按 url 取正文 —— **复用日报那套带缓存的抓取**（`biz_daily.fetch_article_cached`）。

    不另写一份的理由与仓库里其它"同一份实现"一样：缓存格式、失败语义（只缓存成功）、
    以及"入库前先看缓存"这件事必须只有一处。懒导入：不抓正文的路径不该被它拖着。
    """
    try:
        from biz_daily import fetch_article_cached
    except Exception as error:                      # noqa: BLE001
        print('  [WARN] 抓正文不可用（%s），改用本地那份' % type(error).__name__, file=sys.stderr)
        return None
    try:
        body, _cached = fetch_article_cached(url)
    except Exception as error:                      # noqa: BLE001
        print('  [WARN] 抓正文失败（%s）：%s' % (type(error).__name__, error), file=sys.stderr)
        return None
    return body

FULL_TEXT_LIMIT = 4000          # `--with-text` 默认给多少字（够一篇公众号全文）

_FETCH_CACHE_DIR_NAME = ('.cache', 'fetch')


def _fetch_cache_path(url):
    return Path(OUTPUT_ROOT) / '.cache' / 'fetch' / (hashlib.md5(url.encode('utf-8')).hexdigest() + '.md')


def best_article_body(content, limit=None, md_body=None):
    """**展示路径**用：文章正文取**更长的那份** —— 日报 md，或它旁边那份正文缓存。

    日报 md 里正文中位只有 259 汉字，而日报**自己抓到的**完整正文缓存里是 2,063 汉字 ——
    内容早就抓回来了，只写进 md 一小段。`--with-text` 要给人看的那份，所以取全的。
    ⚠️ **建索引不用这个函数**:实测吃完整正文对检索没有增益（DECISIONS D-118），
    索引只吃 md 那份摘录（`collect_articles` 里 500 字）。
    返回 `(body, url)`；url 从 frontmatter 取，取不到就是 None（那时只能用 md 那份）。
    `md_body` 由调用方给时就用它（例如调用方已按自己的规则切出正文 —— 调用方的切法优先）。
    """
    body = (md_body if md_body is not None
            else extract_article_body(content, limit or FULL_TEXT_LIMIT))
    url = None
    match = re.search(r'^url:\s*"?([^"\n]+)"?', content, re.M)
    if match:
        url = match.group(1).strip()
        cached = _fetch_cache_path(url)
        if cached.exists():
            try:
                full = cached.read_text(encoding='utf-8', errors='replace').strip()
                if len(full) > len(body):
                    body = full
            except OSError:
                pass                      # 读不了缓存就当没有，别让这一篇失败
    return (body[:limit] if limit else body), url


def load_record_text(item, limit=FULL_TEXT_LIMIT):
    """把命中的记录还原成本地那份**正文** —— 索引里存的只是"用来匹配的摘录"。

    这是 2026-10-16 用户提出来的：返回标题+分数没有意义，要能给出**某篇公众号的具体内容**。
    两条来源，取更长的那份：
      · 日报 md：`output/biz-daily/<id 里那段相对路径>`，正文用同一套 `extract_article_body` 取；
      · 正文缓存：`output/.cache/fetch/<md5(url)>.md` —— 日报/回填抓来的**完整正文**（比 md 更全）。
    聊天记录没有这两层：索引里那段**本来就是消息全文**。

    返回 `(text, source_file, url)`；读不到时 text 为 None（**不许**静默当成"这篇没有正文"）。
    """
    rid = str(item.get('id') or '')
    # PDF：索引里那条**就是**一块正文（整本书十几万字，不能每次命中都读一遍全文）。
    # 直接返回它，`sourceFile` 指回 pdf-text 那份 md，便于用户去看上下文。
    if rid.startswith('pdf:'):
        stem = rid[len('pdf:'):].split('#', 1)[0]
        path = Path(OUTPUT_ROOT) / 'pdf-text' / f'{stem}.md'
        return (item.get('text') or ''), str(path), None
    if not rid.startswith('article:'):
        return (item.get('text') or ''), None, None

    rel = rid[len('article:'):].replace('\\', '/')
    path = Path(OUTPUT_ROOT) / 'biz-daily' / rel
    if not path.exists():
        return None, str(path), None
    try:
        content = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None, str(path), None

    body, url = best_article_body(content, limit)
    return readable_body(body), str(path), url


def attach_full_text(results, limit=FULL_TEXT_LIMIT, fetch_missing=0):
    """给每条结果挂上 `fullText` / `sourceFile` / `url`（读不到的挂 None，并数出来）。

    `fetch_missing>0` 时，**本地正文短于 `FULL_TEXT_MIN_CHARS` 的那些**会按 `url` 去抓（最多抓这么多条）：
    抓得到的写回 `fullText` 并标 `fetchedFromUrl`，同一份缓存下次直接命中（不重复打扰上游）。
    默认 0 = 不抓 —— 检索不该默认产生对上游的请求。返回 `(results, missing, fetched)`。
    """
    missing = 0
    fetched = 0
    for item in results:
        text, path, url = load_record_text(item, limit)
        if text is None:
            missing += 1
        if fetch_missing and fetched < fetch_missing and url and len(text or '') < FULL_TEXT_MIN_CHARS:
            body = _fetch_body(url)
            if body and body.strip() and len(body) > len(text or ''):
                text = readable_body(body)[:limit]
                fetched += 1
                item['fetchedFromUrl'] = True
        item['fullText'] = text
        if path:
            item['sourceFile'] = path.replace('\\', '/')
        if url:
            item['url'] = url
    return results, missing, fetched


# ===== 精确短语定位（D-121）=====
#
# **为什么单独有一条腿**：现有两条腿都是"相似"——bigram+BM25 按词项计分、向量按语义近似。
# 于是"哪个号在哪天**原样**写过这句话"这类问句（专有名词、代码名、政策原文、引文核对）
# 它们答不了，也不该硬答：把"查不到原样"混进相似度排序里，用户看不出区别。
#
# 实现用 SQLite **FTS5 + trigram**，标准库自带的，不需要新依赖。四个实测结论决定了这个形状
# （2026-10-17 本机 sqlite 3.45.1，8 次探针）：
#   1. 默认分词 `unicode61` 对中文**完全不可用**（整句一个 token：`沙箱逃逸` 命中 0）⇒ 必须 trigram；
#   2. trigram 是**连续子串**匹配，不是词项 AND（`光伏补贴政策` 只命中原样连续的，
#      而 `补贴政策` 命中"的补贴政策"/"补贴政策解读"）⇒ 正好是"原样定位"要的语义，
#      也正是它**不能**替我们那条关键词腿的原因（那条要的是"分开出现也能排上分"）；
#   3. trigram 有 **3 字下限**：`光伏`（2 字）恒空 ⇒ 短查询必须**明说**，绝不能回报"0 命中"；
#   4. 查询里的标点会被当**语法**（`PM2.5` → `syntax error near "."`、`GPT-4` → `no such column: 4`）
#      ⇒ 整串加引号当字面量（内部 `"` 翻倍）；加引号后 `PM2.5`/`C++`/`GPT-4` 全部正常，
#      且 ASCII 大小写不敏感（`"pm2.5"` 能命中 `PM2.5`）。全角/半角不互认。
#
# **语料只从 `meta.json` 来**（语义索引的记录清单）：再写一套记录枚举，两个索引的语料迟早分叉，
# 而"两个索引覆盖的不是同一批"这种错不会报，只会让结果看起来少。
PHRASE_INDEX_DIR = Path(OUTPUT_ROOT) / '.phrase_index'
PHRASE_DB = PHRASE_INDEX_DIR / 'phrase.sqlite'
PHRASE_MIN_CHARS = 3
PHRASE_TEXT_LIMIT = FULL_TEXT_LIMIT

# 语音转写清单（`voice_transcribe.py` 产出）。它不是语义索引的一部分——语音走的是另一个
# 引擎、也不该为它再花一次 embedding 钱——但它同样是"原话"，所以并进**短语索引**：
# `search --phrase` 于是能搜到语音说过什么，而语音是微信自己搜不到的东西。
VOICE_JSONL = INDEX_DIR.parent / '.voice_index' / 'voices.jsonl'


def escape_phrase(query):
    """把整串当**字面量**（而不是 FTS 语法）：外层加引号，内部 `"` 翻倍。"""
    return '"' + str(query or '').replace('"', '""') + '"'


def phrase_chars(query):
    """"有效字符数"：去掉所有空白再数。trigram 的下限按字符算，空格不参与。"""
    return len(''.join(str(query or '').split()))


def _phrase_label(item):
    """「哪个号」：文章取文件名里第一个 `-` 前那段（`<账号>-<标题>.md`），聊天取会话名。

    与 `--from` 的口径保持一致：那条路也是靠 `id`（文件名）里含账号名来找号的
    （2026-10-15 修正文提取时 `source:` 随 YAML 头离开了嵌入文本，见 `search()` 里的注释）。
    """
    if str(item.get('type')) == 'chat':
        return str(item.get('talker') or '')
    rid = str(item.get('id') or '')
    stem = rid.split(':', 1)[-1].replace('\\', '/').rsplit('/', 1)[-1]
    if stem.endswith('.md'):
        stem = stem[:-3]
    return stem.split('-', 1)[0] if '-' in stem else stem


def build_phrase_index(line: str = 'all', db_path=None, meta_path=None):
    """从 `meta.json` **全量重建**短语索引（不调用任何模型，所以重建便宜到可以每次都全量）。

    返回 `{'records', 'byLine', 'textMissing', 'db', 'bytes'}`；没有语义索引时返回 error ——
    它是**派生**产物，不自己去找语料。
    """
    import sqlite3
    meta_file = Path(meta_path or META_FILE)
    if not meta_file.exists():
        return {'error': '还没有语义索引，短语索引由它派生',
                'hint': 'weflow-cli search-index --yes'}
    meta = json.loads(meta_file.read_text(encoding='utf-8'))
    types = line_types(line)
    rows = []
    text_missing = 0
    by_line = {}
    for item in meta:
        if types is not None and str(item.get('type') or '') not in types:
            continue
        text, _source_file, _url = load_record_text(item, PHRASE_TEXT_LIMIT)
        if not text:
            # 读不到正文（md 被删/移动）：退回索引里那段摘录，并**数出来**告诉用户，
            # 而不是让这篇悄悄从短语索引里消失。
            text = item.get('text') or ''
            text_missing += 1
        if not text:
            continue
        key = str(item.get('type') or '')
        by_line[key] = by_line.get(key, 0) + 1
        rows.append((text, str(item.get('id') or ''), key, _phrase_label(item),
                     str(item.get('date') or item.get('time') or ''),
                     str(item.get('title') or '')))

    # 语音转写并进同一条短语线（`type='voice'`）。`--line chat|wiki` 时**不**带它——
    # 那两条线是用户明确限定的范围，语音属于第三类证据。
    #
    # 路径**在这里现算**（挂在 `OUTPUT_ROOT` 下），不写成模块级常量：常量在 import 时就固定，
    # 测试把 `OUTPUT_ROOT` 指到临时目录后仍会读到真实 `output/` 里的语音，于是
    # "3 条记录"变成 8 条（2026-10-22 就是这么红的）。
    if types is None:
        voice_file = Path(OUTPUT_ROOT) / '.voice_index' / 'voices.jsonl'
        voice_rows = 0
        if voice_file.exists():
            try:
                for line_text in voice_file.read_text(encoding='utf-8').splitlines():
                    if not line_text.strip():
                        continue
                    rec = json.loads(line_text)
                    body = str(rec.get('text') or '').strip()
                    if len(body) < PHRASE_MIN_CHARS:
                        continue
                    rows.append((body, str(rec.get('id') or ''), 'voice',
                                 str(rec.get('talker') or ''), str(rec.get('time') or ''), ''))
                    voice_rows += 1
            except Exception as exc:
                print(f'[WARN] 读语音转写失败（{type(exc).__name__}: {exc}），这次不并入',
                      file=sys.stderr)
        if voice_rows:
            by_line['voice'] = by_line.get('voice', 0) + voice_rows
    path = Path(db_path or PHRASE_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute('DROP TABLE IF EXISTS hits')
        conn.execute("CREATE VIRTUAL TABLE hits USING fts5("
                     "body, rid UNINDEXED, line UNINDEXED, label UNINDEXED,"
                     " date UNINDEXED, title UNINDEXED, tokenize='trigram')")
        conn.executemany('INSERT INTO hits(body, rid, line, label, date, title)'
                         ' VALUES (?, ?, ?, ?, ?, ?)', rows)
        conn.commit()
        # `VACUUM` 要写临时文件；在受限环境（沙箱 / %TEMP% 不可写）它会报
        # `unable to open database file`，而**数据其实已经写进去了** —— 那一步只是把库压实。
        # 所以它失败不能被当成"索引没建好"：2026-10-22 实测 155MB 的库内容完整，
        # 却因为这一行让整个 phrase-build 的退出码变成非 0。
        try:
            conn.execute('VACUUM')
        except Exception as exc:
            print(f'[WARN] 短语索引已写入，但 VACUUM 失败（{type(exc).__name__}: {exc}）'
                  '——只是没压实，查询不受影响', file=sys.stderr)
    finally:
        conn.close()
    return {'records': len(rows), 'byLine': by_line, 'textMissing': text_missing,
            'line': line, 'db': str(path), 'bytes': path.stat().st_size}


def _phrase_where(line, from_source):
    """拼过滤条件。**与向量那条路同一口径**：`--from` 按 `id` 子串或标签精确匹配。"""
    clauses, params = [], []
    if line_types(line) is not None:
        clauses.append('line IN (%s)' % ','.join('?' * len(sorted(line_types(line)))))
        params.extend(sorted(line_types(line)))
    if from_source:
        want = from_source.strip().lower()
        clauses.append('(lower(rid) LIKE ? OR lower(label) = ?)')
        params.extend(['%' + want + '%', want])
    return (' AND ' + ' AND '.join(clauses) if clauses else ''), params


def phrase_search(query, limit: int = 20, line: str = 'all', from_source: str = None,
                  db_path=None, group_limit: int = 20):
    """原样定位：返回命中行 + 按（账号/会话 × 日期）的分组。

    **两条不许静默**：查不到索引 → 报错并给建索引的命令；查询短于下限 → 明说"查不了"
    （而不是 0 命中）。把这两种说成"没有"是这条腿最坏的失效方式。
    """
    import sqlite3
    path = Path(db_path or PHRASE_DB)
    result = {'query': query, 'escaped': escape_phrase(query), 'line': line,
              'from': from_source, 'total': 0, 'hits': [], 'groups': []}
    if not path.exists():
        result.update({'error': '短语索引还没建',
                       'hint': 'weflow-cli search-index --phrase（本地、不花钱）'})
        return result
    chars = phrase_chars(query)
    if chars < PHRASE_MIN_CHARS:
        result.update({'tooShort': True, 'chars': chars, 'minChars': PHRASE_MIN_CHARS,
                       'note': 'trigram 分词有 %d 字下限，更短的串它切不出任何词项 —— '
                               '这里**不报"0 命中"**，因为那是把"查不了"说成"没有"'
                               % PHRASE_MIN_CHARS})
        return result
    where, params = _phrase_where(line, from_source)
    conn = sqlite3.connect(str(path))
    try:
        total = conn.execute('SELECT count(*) FROM hits WHERE hits MATCH ?' + where,
                             [result['escaped']] + params).fetchone()[0]
        result['total'] = total
        for label, day, n in conn.execute(
                'SELECT label, substr(date, 1, 10) AS day, count(*) AS n FROM hits '
                'WHERE hits MATCH ?' + where +
                ' GROUP BY label, day ORDER BY n DESC, day DESC LIMIT ?',
                [result['escaped']] + params + [group_limit]):
            result['groups'].append({'label': label, 'date': day, 'count': n})
        for rid, kind, label, date, title, ctx in conn.execute(
                'SELECT rid, line, label, date, title, '
                "snippet(hits, 0, '【', '】', '…', 14) FROM hits "
                'WHERE hits MATCH ?' + where + ' ORDER BY date DESC LIMIT ?',
                [result['escaped']] + params + [limit]):
            result['hits'].append({'id': rid, 'type': kind, 'label': label,
                                   'date': date, 'title': title, 'context': ctx})
    finally:
        conn.close()
    return result


def collapse_chunks(results, top_k=10):
    """把同一父文档的多个块合并成一条：留最高分那块，并记下块数。

    为什么要合并：分块之后一篇长文会贡献 N 条记录，直接返回 top-k 会被同一篇灌满
    （用户看到的是「同一篇文章的 10 个片段」而不是 10 篇文章）。这里按 parent 聚合，
    保留分数最高的那块作为代表，同时把 `matchedChunk` 带出去——RAG 只喂命中的那一段。
    """
    best = {}
    order = []
    for item in results:
        key = item.get('parent') or item.get('id')
        prev = best.get(key)
        if prev is None:
            best[key] = item
            order.append(key)
        else:
            if float(item.get('score') or 0) > float(prev.get('score') or 0):
                best[key] = item
    merged = []
    for key in order:
        item = dict(best[key])
        if item.get('parent'):
            item['chunkCount'] = item.get('chunkCount') or 1
        merged.append(item)
    return merged[:top_k]


def search(query: str, api_key: str, top_k: int = 10, rerank_results: bool = True,
           line: str = 'all', hybrid: bool = True, from_source: str = None,
           rerank_pool: int = RERANK_CANDIDATES,
           dedup: bool = True, with_text: int = 0,
           fetch_missing: int = 0, mmr: bool = False):
    """Semantic search with keyword fallback.

    `line` 在**取候选之前**就把不属于这条线的记录挡掉（把它们的相似度压成 -inf 再
    argsort）。反过来的做法——先取 top-k 再过滤——看着也行，但那会把条数算错：
    `--top-k 10 --line chat` 可能只回 3 条，而"为什么只有 3 条"没有任何地方会说。

    **函数这一层的默认是 `all`（不缩小范围）**，用户看到的默认（文章线）在 CLI 那一层
    （`add_line_arg`）。这样某个调用方忘了传 `line` 时结果是"多搜了一条线"，而不是
    "静默少搜一半"——后者在这个仓库里是最贵的那类 bug。
    """
    types = line_types(line)   # 认不出的线在这里就报错，不静默当 all
    VECTOR_STATE.update(used=False, fallback=None, counts=None)
    # `--mmr` 在 fallback 里也要用 `vectors`；只在 try 里赋值会让它**未绑定**（NameError）。
    vectors = None
    # Try embedding-based search first
    if VECTORS_FILE.exists() and META_FILE.exists():
        try:
            np = require_numpy()
            vectors = np.load(VECTORS_FILE)
            meta = json.loads(META_FILE.read_text(encoding='utf-8'))
            # meta 与 vectors 的行数必须一致 —— 中途被杀、或两份文件新旧不一，都可能不一样。
            # 不一致时**按少的那个截断**：宁可少几条结果，也不能让下标越界，因为越界会被
            # 下面那个 `except: pass` 变成"静默退回关键词"，那是最难看出来的一种失效。
            rows = min(len(meta), int(vectors.shape[0]))
            if rows < len(meta):
                meta = meta[:rows]
            elif rows < int(vectors.shape[0]):
                vectors = vectors[:rows]
            # 顺手把每线条数算掉：`main()` 原来会再解析一遍同一份 meta（69 + 56 ms/次）
            VECTOR_STATE['counts'] = _counts_from_meta(meta)
            query_embeddings = get_embeddings([query], api_key)
            if query_embeddings and not all(v == 0 for v in query_embeddings[0]):
                query_vec = np.array(query_embeddings[0], dtype=np.float32)
                query_vec = query_vec / np.linalg.norm(query_vec)
                similarities = np.dot(vectors, query_vec)
                # 关键词那一侧要用**同一个**掩码（`--line wiki` 时聊天不许从这条路上混进来）
                in_scope_mask = None
                if types is not None or from_source:
                    in_scope = np.array([True if types is None else str(m.get('type', '')) in types
                                         for m in meta], dtype=bool)
                    if from_source:
                        # **按公众号查是属性查询，不该指望向量。** 号名只住在 `id`（文件名
                        # `<公众号>-<标题>.md`）里：2026-10-15 修正文提取时，`source:` 随 YAML 头
                        # 一起离开了嵌入文本。实测（窗口 2026-09-12..10-13）：红星新闻 908 篇，
                        # 语义前 10 只回 3 条；这里按 id 精确筛，能筛出全部。
                        want = from_source.strip().lower()
                        by_id = np.array([want in str(m.get('id', '')).lower()
                                          or str(m.get('source', '')).lower() == want
                                          for m in meta], dtype=bool)
                        in_scope = in_scope & by_id
                    if not in_scope.any():
                        # 一条都没有：**说出来**（是这条线/这个号在索引里没有记录，不是"没命中"）
                        return []
                    similarities = np.where(in_scope, similarities, -np.inf)
                    in_scope_mask = in_scope
                # 先取一个比重排池更大的候选集，重排才有可以换的空间。
                pool_size = max(top_k, rerank_pool) if rerank_results else top_k
                if types is not None:
                    pool_size = min(pool_size, int(in_scope.sum()))
                vec_order = [int(i) for i in np.argsort(similarities)[::-1][:RRF_POOL]]
                vec_order = [i for i in vec_order if np.isfinite(similarities[i])]
                if hybrid:
                    # 关键词那一侧走**同一个掩码**：`--line wiki` 时聊天记录不许从这条路上混进来。
                    kw_order = [i for i, _s in keyword_rank(query, meta, in_scope_mask,
                                                       rare_ratio=HYBRID_RARE_RATIO,
                                                       score_ratio=HYBRID_SCORE_RATIO)]
                    top_indices = [i for i, _s in rrf_fuse([vec_order, kw_order])[:pool_size]]
                else:
                    top_indices = vec_order[:pool_size]
                results = []
                for idx in top_indices:
                    item = meta[idx]
                    results.append({**item, '_row': int(idx), 'score': float(similarities[idx])})
                if rerank_results:
                    results = rerank_dispatch(query, results, create_client(),
                                              pool_size=rerank_pool)
                if dedup:
                    results = dedup_results(results)
                if with_text:
                    results, _missing, _fetched = attach_full_text(results, with_text,
                                                                  fetch_missing=fetch_missing)
                if mmr:
                    results = mmr_select(results, vectors, top_k)
                for _item in results:                 # `_row` 只是内部用的，别漏进输出
                    _item.pop('_row', None)
                VECTOR_STATE['used'] = True
                # 分块索引：同一篇只留最高分那块（见 collapse_chunks）
                if any(r.get('parent') for r in results):
                    results = collapse_chunks(results, top_k)
                return results[:top_k]
        except Exception as error:
            # **显式降级**，原因留下来。原来是裸 `except: pass`：向量路径任何异常都会变成
            # "静默只搜关键词"，而返回里照样写着 `hybrid: true` —— 那是最难看出来的一种失效，
            # 也让"快慢/好坏"的对比可能落在不同路径上。另外裸 except 会吞掉 KeyboardInterrupt。
            VECTOR_STATE['fallback'] = '%s: %s' % (type(error).__name__, error)

    if not VECTOR_STATE['used'] and VECTOR_STATE['fallback'] is None:
        # 没异常也没返回 —— 最常见的原因是 embedding 拿到空/全零（上游失败）。
        VECTOR_STATE['fallback'] = '未做向量检索（embedding 为空或全零）'

    # Fallback: keyword search
    results = keyword_search(query, max(top_k, RERANK_CANDIDATES) if rerank_results else top_k,
                             line=line)
    if rerank_results:
        # 关键词打分比余弦更粗，重排在这里的收益反而更大。
                    results = rerank_dispatch(query, results, create_client(),
                                              pool_size=rerank_pool)
    if dedup:
        results = dedup_results(results)
    if with_text:
        results, _missing, _fetched = attach_full_text(results, with_text,
                                                      fetch_missing=fetch_missing)
    if mmr and vectors is not None:
        # 向量没拿到时**不能**做 MMR（它需要矩阵）；以前这里会 NameError。
        results = mmr_select(results, vectors, top_k)
    for _item in results:                 # `_row` 只是内部用的，别漏进输出
        _item.pop('_row', None)
    return results[:top_k]


def line_counts() -> dict:
    """索引里每条线各有多少条记录（`all` 也算一份总数）。

    **只读 meta**（不解锁向量文件、不调任何服务、不花一分钱），所以"这一趟为什么是空的"
    可以在返回之前就答上来：`--line chat` 空成一片时，先得能分清是"没命中"还是
    "这条线在索引里根本没有记录"——后者要重新建索引，前者不用。
    """
    counts = {line: 0 for line in LINE_CHOICES}
    if not META_FILE.exists():
        return counts
    try:
        meta = json.loads(META_FILE.read_text(encoding='utf-8'))
    except Exception:
        return counts
    return _counts_from_meta(meta)


def _counts_from_meta(meta) -> dict:
    """从**已经解析好**的 meta 算每线条数（`line_counts` 与 `search()` 共用这一份）。

    抽出来是让 `search()` 复用自己刚解析的那份：它原来在返回之后又被 `main()` 调
    `line_counts()` **再解析一遍**同一个 13.5 MB 文件（实测 69 + 56 ms）。两份实现分开
    写更糟 —— 两处解析同一份文件迟早会漂，而漂的表现是"条数对不上但谁都不报错"。
    """
    counts = {line: 0 for line in LINE_CHOICES}
    for item in meta:
        t = str(item.get('type', ''))
        for line, types in LINE_TYPES.items():
            if t in types:
                counts[line] += 1
    # `all` 是**索引里的总条数**，不是两条线相加：以后要是多出第三种 type，
    # 它会被 `all` 带上、而不属于任何一条线，两条线相加就少算了。
    counts['all'] = len(meta)
    return counts


# ====== Main ======

def add_window_args(parser):
    """索引窗口（两个子命令共用）。

    默认值与原先把死的数字**完全一致**（聊天 90 / 日报 30）——加参数不是改代价，
    只是把原来改不了的数字变成改得了的。要建"整段历史"的知识库就显式给一个大值。
    """
    parser.add_argument('--days', type=int, default=90,
                        help='聊天记录收最近多少天（默认 90）')
    parser.add_argument('--article-days', type=int, default=30,
                        help='日报文章收最近多少个日期目录（默认 30）')
    parser.add_argument('--pdf-chunks', type=int, default=0,
                        help='每个 PDF 最多收多少块（默认 0=不限）；PDF 文字来自 output/pdf-text')
    parser.add_argument('--article-chunk-chars', type=int, default=0,
                        help='文章按多少字符切块后分别嵌入（默认 0=不切，只取前 500 字）')
    parser.add_argument('--no-chat', action='store_true',
                        help='不收聊天记录（只建文章/PDF 半边；做对照实验或只想搜文章时用）')
    parser.add_argument('--article-chunk-overlap', type=int, default=60,
                        help='切块重叠字符数（默认 60）')
    parser.add_argument('--no-pdf', action='store_true',
                        help='不收 PDF 文字（默认收：每份 PDF 按页切块进索引）')


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='command')

    # build
    p = subparsers.add_parser('build')
    p.add_argument('--api-key', help='DeepSeek API key（优先从 config 读取）')
    p.add_argument('--full', action='store_true', help='全量重建')
    p.add_argument('--include-imports', action='store_true',
                   help='把 weflow-cli import qchat 导入的 QQ 会话也收进索引（聊天半边；id 带 qq: 段）')
    add_window_args(p)

    # update
    p = subparsers.add_parser('update')
    p.add_argument('--api-key', help='DeepSeek API key（优先从 config 读取）')
    p.add_argument('--include-imports', action='store_true',
                   help='同 build：把导入的 QQ 会话收进增量')
    add_window_args(p)

    # search
    p = subparsers.add_parser('search')
    p.add_argument('query', nargs='?')
    p.add_argument('--api-key', help='DeepSeek API key（向量搜索时需要，关键词 fallback 不需要）')
    p.add_argument('--top-k', type=int, default=10)
    p.add_argument('--no-rerank', action='store_true',
                   help='不调用决策模型重排，只按向量/关键词相似度返回（回退到引入重排之前）')
    p.add_argument('--mmr', action='store_true',
                   help='对最终结果做 MMR 去冗余（λ=0.7）：压掉近似的同领域邻居，可能牺牲单点召回')
    p.add_argument('--fetch-missing', nargs='?', type=int, const=3, default=0,
                   metavar='N',
                   help='正文短于 300 字的那些按 url 抓一次（最多 N 条，默认 3）；复用日报的缓存')
    p.add_argument('--with-text', nargs='?', type=int, const=FULL_TEXT_LIMIT,
                   default=0, metavar='N',
                   help='给每条命中挂上本地正文字段 fullText/sourceFile/url（默认不给；'
                        'N 控制每条最多多少字，默认 %d）' % FULL_TEXT_LIMIT)
    p.add_argument('--no-dedup', action='store_true',
                   help='不折叠同文本的多号转载（默认折叠，并在结果里给 duplicateCount）')
    p.add_argument('--rerank-pool', type=int, default=RERANK_CANDIDATES,
                   help='重排看多少条候选（默认 %d；池子越大越准、越贵）' % RERANK_CANDIDATES)
    p.add_argument('--no-hybrid', action='store_true',
                   help='不融合本地 BM25 名次（默认融合：加门控后实测三个台子都只增不减）')
    p.add_argument('--from', dest='from_source',
                   help='只查某个公众号（按 id/文件名的子串匹配，不走语义）')
    p.add_argument('--phrase', action='store_true',
                   help='把这串当**原样短语**在本地定位（FTS5/trigram；不走向量、不出境、不花钱）：'
                        '回答"哪个号在哪天原样写过它"。查不到原样不等于查不到语义，收窄了问题范围')

    pp = subparsers.add_parser('phrase-build',
                               help='构建/重建精确短语索引（本地，不调用任何模型）')
    add_line_arg(pp)

    add_line_arg(p)

    args = parser.parse_args()
    # 手动跑时的第二道闸门（CLI 那条路已经按 1-36500 校验过）：0 天等于建一个空库，
    # 那结果看起来"建好了"，实际什么都没有——这种"看起来成功"的失败要挡住
    if getattr(args, 'days', 90) < 1 or getattr(args, 'article_days', 30) < 1:
        json_output({'error': '--days 与 --article-days 都要 ≥ 1（0 天等于索引一个空库）'})
        return
    # 读不到配置不该让**不需要配置**的子命令崩：`phrase-build` 与 `search --phrase` 只读本地索引，
    # 它们没有任何理由要求 `~/.weflow-cli/config.json` 存在（CI 上没有那个文件、本地有，于是这条
    # 会"本地绿、CI 红"）。需要 key 的那条路会在下面自己报"缺 key"。
    try:
        config = load_config()
    except Exception:
        config = {}
    # `getattr`：不是每个子命令都有 `--api-key`（`phrase-build` 不需要 key —— 它不调模型）。
    # 无条件读会让"建本地短语索引"直接崩在一个**与它无关**的参数上。
    api_key = (getattr(args, 'api_key', '') or os.environ.get('DASHSCOPE_API_KEY', '')
               or get_dashscope_key(config))
    
    # 从环境变量读取缓存开关
    use_cache = os.environ.get('WEFLOW_CACHE_ENABLED') == '1'

    if args.command == 'build':
        result = build_index(api_key, full=args.full, days=args.days,
                             article_days=args.article_days,
                             include_imports=getattr(args, 'include_imports', False),
                             use_cache=use_cache,
                             pdf_chunks=0 if getattr(args, 'no_pdf', False) else getattr(args, 'pdf_chunks', 0),
                             article_chunk_chars=getattr(args, 'article_chunk_chars', 0),
                             article_chunk_overlap=getattr(args, 'article_chunk_overlap', 60),
                             include_chat=not getattr(args, 'no_chat', False))
    elif args.command == 'update':
        result = build_index(api_key, full=False, days=args.days,
                             article_days=args.article_days,
                             include_imports=getattr(args, 'include_imports', False),
                             use_cache=use_cache,
                             pdf_chunks=0 if getattr(args, 'no_pdf', False) else getattr(args, 'pdf_chunks', 0),
                             article_chunk_chars=getattr(args, 'article_chunk_chars', 0),
                             article_chunk_overlap=getattr(args, 'article_chunk_overlap', 60),
                             include_chat=not getattr(args, 'no_chat', False))
    elif args.command == 'phrase-build':
        result = build_phrase_index(line=args.line)
        result['success'] = 'error' not in result
    elif args.command == 'search':
        query = args.query or os.environ.get('WEFLOW_SEARCH_QUERY', '')
        if not query:
            result = {"error": "missing search query"}
        elif args.phrase:
            # 原样定位：**本地、无 key、不出境**。所以它不校验 `api_key`，
            # 也不该被当成"向量搜索的降级版"——两者问的是不同的问题。
            # 向量检索的那些旗标在这条路上不生效。CLI 会直接拒绝这种组合；这里兜住
            # **直接调脚本**的人：静默忽略会让人把默认行为当成"调过的结果"。
            ignored_flags = [name for name, on in (
                ('--mmr', getattr(args, 'mmr', False)),
                ('--no-rerank', getattr(args, 'no_rerank', False)),
                ('--no-hybrid', getattr(args, 'no_hybrid', False)),
                ('--no-dedup', getattr(args, 'no_dedup', False)),
                ('--with-text', getattr(args, 'with_text', 0)),
                ('--fetch-missing', getattr(args, 'fetch_missing', 0)),
                ('--rerank-pool', getattr(args, 'rerank_pool', RERANK_CANDIDATES) != RERANK_CANDIDATES),
            ) if on]
            if ignored_flags:
                print('[WARN] --phrase only locates verbatim text; ignored: %s'
                      % ', '.join(ignored_flags), file=sys.stderr)
            result = phrase_search(query, limit=args.top_k, line=args.line,
                                   from_source=args.from_source)
            result['success'] = 'error' not in result
        else:
            # **输出带上是哪条线**（`--line` 默认文章线）。少了这一层，`--line chat`
            # 空成一片时读的人分不清"没命中"和"这条线根本没进索引"——而两者的下一步
            # （换个词 / 重建索引）是两回事。
            rows = search(query, api_key, top_k=args.top_k,
                          rerank_results=not args.no_rerank, line=args.line,
                          hybrid=not args.no_hybrid, from_source=args.from_source,
                          rerank_pool=args.rerank_pool,
                          dedup=not args.no_dedup,
                          with_text=args.with_text,
                          fetch_missing=args.fetch_missing,
                          mmr=args.mmr)
            # `search()` 本来就解析过 meta，顺手把每线条数算在 `VECTOR_STATE['counts']` 里；
            # 只有它没算（走了关键词 fallback）时才重新解析。省掉一次 13.5 MB 的解析。
            counts = VECTOR_STATE.get('counts') or line_counts()
            result = {
                'success': True,
                'line': args.line,
                'lineLabel': line_label(args.line),
                'hybrid': not args.no_hybrid,
                # **这次到底走没走向量**：没走时把原因写出来。缺了它，"只搜到关键词结果"
                # 与"关键词结果就是这样"在返回里长得一模一样。
                'vector': {'used': VECTOR_STATE['used'], 'fallback': VECTOR_STATE['fallback']},
                'rerank': dict(RERANK_STATE),
                'from': args.from_source,
                'dedup': not args.no_dedup,
                'withText': args.with_text,
                'fetchMissing': args.fetch_missing,
                'mmr': args.mmr,
                'results': rows,
                'indexCounts': counts,
            }
            if not rows and counts['all'] == 0:
                result['note'] = ('语义索引还没建（output/.semantic_index 不存在）——'
                                  '先跑 weflow-cli search-index')
            elif not rows and args.from_source:
                result['note'] = ('索引里没有「%s」的记录 —— 按号筛的是文件名里的号名，'
                                  '也可能是建索引那天的窗口没覆盖到它' % args.from_source)
            elif not rows and args.line != 'all' and counts[args.line] == 0:
                result['note'] = ('索引里一条「%s」的记录都没有（建索引那天的窗口可能没覆盖到），'
                                  '先看 --line all' % line_label(args.line))
    else:
        result = {"error": f"未知命令: {args.command}"}

    json_output(result)


if __name__ == '__main__':
    main()
