#!/usr/bin/env python3
"""
全局语义搜索 — 基于阿里云百炼 Embedding API (text-embedding-v4) + NumPy。

用法:
  # 构建索引（首次或增量）
  python scripts/semantic_search.py build

  # 搜索（有索引用向量，否则关键词 fallback）
  python scripts/semantic_search.py search "有人推荐过遥感的工具吗" --top-k 10

  # 增量更新（只处理新数据）
  python scripts/semantic_search.py update

输出: JSON 格式
"""

import sys, os, json, hashlib, argparse
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
from _utils import load_config, decrypt_lock
from jev_client import create_client

OUTPUT_ROOT = 'output'
INDEX_DIR = Path(OUTPUT_ROOT) / '.semantic_index'
VECTORS_FILE = INDEX_DIR / 'vectors.npy'
META_FILE = INDEX_DIR / 'meta.json'
EMBEDDING_DIM = 1024  # 阿里云 text-embedding-v4
BATCH_SIZE = 10  # 阿里云 text-embedding-v4 单次最多 10 条
TZ = timezone(timedelta(hours=8))


def json_output(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


# ====== Embedding API ======

def get_embeddings(texts: list[str], api_key: str) -> list[list[float]]:
    """Call 阿里云百炼 embedding API (OpenAI-compatible)."""
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
        except Exception as e:
            err_body = ''
            if hasattr(e, 'read'):
                try: err_body = e.read().decode()[:300]
                except: pass
            print(f"[WARN] Embedding API batch {i//BATCH_SIZE + 1} 失败: {e} {err_body}", file=sys.stderr)
            all_embeddings.extend([[0.0] * EMBEDDING_DIM for _ in batch])

    return all_embeddings


# ====== Data Collection ======

def open_db(db_path, key_hex, salt_hex):
    sqlcipher = require_sqlcipher()
    raw_key = f"x'{key_hex}{salt_hex}'"
    conn = sqlcipher.connect(db_path)
    c = conn.cursor()
    c.execute(f'PRAGMA key = "{raw_key}";')
    c.execute("SELECT count(*) FROM sqlite_master")
    return conn


def get_name_map(contact_db, contact_key, contact_salt):
    name_map = {}
    if not contact_db or not contact_key or not os.path.exists(contact_db):
        return name_map
    try:
        conn = open_db(contact_db, contact_key, contact_salt)
        c = conn.cursor()
        c.execute("SELECT username, COALESCE(NULLIF(remark,''), NULLIF(nick_name,''), username) FROM contact")
        for r in c.fetchall():
            name_map[r[0]] = r[1]
        conn.close()
    except:
        pass
    return name_map


def collect_chat_messages(conn, name_map, days=90):
    """Collect chat messages for indexing."""
    c = conn.cursor()
    now = datetime.now(TZ)
    start_ts = int((now - timedelta(days=days)).timestamp())

    try:
        c.execute("SELECT user_name FROM Name2Id WHERE is_session = 1")
        sessions = [r[0] for r in c.fetchall()]
    except:
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")
        sessions = [r[0] for r in c.fetchall()]

    items = []
    for talker in sessions[:100]:  # Limit to avoid too many
        tbl = f"Msg_{hashlib.md5(talker.encode()).hexdigest()}"
        try:
            c.execute(f'SELECT COUNT(*) FROM sqlite_master WHERE name="{tbl}"')
            if c.fetchone()[0] == 0:
                continue

            c.execute(f'''
                SELECT create_time, real_sender_id, message_content
                FROM "{tbl}"
                WHERE create_time >= ?
                ORDER BY create_time DESC
                LIMIT 500
            ''', (start_ts,))

            for ts, sender, content in c.fetchall():
                if not content or not isinstance(content, str) or len(content) < 10:
                    continue
                sender_name = name_map.get(sender, sender) if sender else name_map.get(talker, talker)
                dt = datetime.fromtimestamp(ts, tz=TZ)
                items.append({
                    "id": f"chat:{talker}:{ts}",
                    "type": "chat",
                    "talker": name_map.get(talker, talker),
                    "sender": sender_name,
                    "time": dt.strftime('%Y-%m-%d %H:%M'),
                    "text": content[:500],
                })
        except:
            pass

    return items


def collect_articles():
    """Collect articles from biz-daily."""
    items = []
    daily_dir = Path(OUTPUT_ROOT) / 'biz-daily'
    if not daily_dir.exists():
        return items

    for date_dir in sorted(daily_dir.iterdir(), reverse=True)[:30]:  # Last 30 days
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
                    body = '\n'.join(lines[body_start:body_start + 50]) if body_start else content[:1000]
                    if title:
                        items.append({
                            "id": f"article:{f.relative_to(daily_dir)}",
                            "type": "article",
                            "title": title,
                            "date": date_dir.name,
                            "topic": topic_dir.name,
                            "text": f"{title}\n{body[:500]}",
                        })
                except:
                    pass

    return items


# ====== Index Operations ======

def build_index(api_key: str, full: bool = False):
    np = require_numpy()
    """Build or update the semantic index."""
    INDEX_DIR.mkdir(parents=True, exist_ok=True)

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
    if not nt_db:
        return {"error": "未初始化，请先运行 weflow-cli init"}

    nt_key = decrypt_lock(config.get('ntKey', ''))
    nt_salt = config.get('ntSalt', '')

    # Get name map
    msg_dir = os.path.dirname(nt_db.replace('\\', '/'))
    wxid_dir = os.path.dirname(os.path.dirname(msg_dir))
    contact_db = os.path.join(wxid_dir, 'db_storage', 'contact', 'contact.db')
    contact_key_enc = config.get('contactKey', '')
    contact_salt = config.get('contactSalt', '')
    contact_key = decrypt_lock(contact_key_enc) if contact_key_enc else ''
    name_map = get_name_map(contact_db, contact_key, contact_salt)

    # Collect items
    items = []
    try:
        conn = open_db(nt_db, nt_key, nt_salt)
        chat_items = collect_chat_messages(conn, name_map, days=90)
        items.extend(chat_items)
        conn.close()
    except Exception as e:
        print(f"[WARN] 聊天消息收集失败: {e}", file=sys.stderr)

    article_items = collect_articles()
    items.extend(article_items)

    # Filter new items
    new_items = [item for item in items if item['id'] not in existing_ids]
    print(f"总数据: {len(items)} 条, 新数据: {len(new_items)} 条", file=sys.stderr)

    if not new_items:
        return {"status": "up_to_date", "total": len(items)}

    # Generate embeddings
    print("生成 embeddings...", file=sys.stderr)
    texts = [item['text'] for item in new_items]
    embeddings = get_embeddings(texts, api_key)

    if not embeddings or all(e == [0.0] * EMBEDDING_DIM for e in embeddings):
        return {"error": "Embedding 生成失败，请检查 API key"}

    # Load existing vectors
    if VECTORS_FILE.exists() and not full:
        vectors = np.load(VECTORS_FILE)
        meta = json.loads(META_FILE.read_text(encoding='utf-8'))
    else:
        vectors = np.empty((0, EMBEDDING_DIM), dtype=np.float32)
        meta = []

    # Append new vectors
    new_vectors = np.array(embeddings, dtype=np.float32)
    vectors = np.vstack([vectors, new_vectors]) if vectors.size else new_vectors
    meta.extend(new_items)

    # Normalize vectors for cosine similarity
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1  # Avoid division by zero
    vectors = vectors / norms

    # Save
    np.save(VECTORS_FILE, vectors)
    META_FILE.write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')

    return {
        "status": "success",
        "total": len(meta),
        "new": len(new_items),
    }


def keyword_search(query: str, top_k: int = 10):
    """Simple keyword fallback: scan articles + messages for keyword matches."""
    results = []
    keywords = query.lower().split()

    # Scan biz-daily articles
    biz_dir = Path('output/biz-daily')
    if biz_dir.exists():
        for md_file in sorted(biz_dir.rglob('*.md'), reverse=True):
            if md_file.name == 'README.md' or md_file.name.startswith('.'):
                continue
            try:
                content = md_file.read_text(encoding='utf-8')[:5000]
            except:
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
RERANK_CANDIDATES = 20
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
        print('  [WARN] 重排失败（%s），保持原检索顺序：%s' % (type(error).__name__, error))
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
        print('  [WARN] 重排没有返回任何可用分数，保持原检索顺序')
        return results

    # 没拿到分数的按原序垫在最后，而不是当成 0 分——"未知"和"不相关"不是一回事。
    scored.sort(key=lambda triple: (triple[0] is None, -(triple[0] or 0.0), triple[1]))
    best = (answers.get('best') or {}).get('choice')
    top = scored[0]
    if best is not None and best != '候选%d' % top[1]:
        print('  [WARN] 重排自检不一致：逐条打分最高的是候选%d，单选却答 %s'
              % (top[1], best))

    ordered = []
    for value, i, item in scored:
        entry = dict(item)
        if value is not None:
            entry['rerankScore'] = round(value, 3)
        ordered.append(entry)
    ordered.extend(results[pool_size:])
    return ordered


def search(query: str, api_key: str, top_k: int = 10, rerank_results: bool = True):
    """Semantic search with keyword fallback."""
    # Try embedding-based search first
    if VECTORS_FILE.exists() and META_FILE.exists():
        try:
            np = require_numpy()
            vectors = np.load(VECTORS_FILE)
            meta = json.loads(META_FILE.read_text(encoding='utf-8'))
            query_embeddings = get_embeddings([query], api_key)
            if query_embeddings and not all(v == 0 for v in query_embeddings[0]):
                query_vec = np.array(query_embeddings[0], dtype=np.float32)
                query_vec = query_vec / np.linalg.norm(query_vec)
                similarities = np.dot(vectors, query_vec)
                # 先取一个比重排池更大的候选集，重排才有可以换的空间。
                pool_size = max(top_k, RERANK_CANDIDATES) if rerank_results else top_k
                top_indices = np.argsort(similarities)[::-1][:pool_size]
                results = []
                for idx in top_indices:
                    item = meta[idx]
                    results.append({**item, 'score': float(similarities[idx])})
                if rerank_results:
                    results = rerank(query, results, create_client())
                return results[:top_k]
        except:
            pass

    # Fallback: keyword search
    results = keyword_search(query, max(top_k, RERANK_CANDIDATES) if rerank_results else top_k)
    if rerank_results:
        # 关键词打分比余弦更粗，重排在这里的收益反而更大。
        results = rerank(query, results, create_client())
    return results[:top_k]


# ====== Main ======

def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='command')

    # build
    p = subparsers.add_parser('build')
    p.add_argument('--api-key', help='DeepSeek API key（优先从 config 读取）')
    p.add_argument('--full', action='store_true', help='全量重建')

    # update
    p = subparsers.add_parser('update')
    p.add_argument('--api-key', help='DeepSeek API key（优先从 config 读取）')

    # search
    p = subparsers.add_parser('search')
    p.add_argument('query', nargs='?')
    p.add_argument('--api-key', help='DeepSeek API key（向量搜索时需要，关键词 fallback 不需要）')
    p.add_argument('--top-k', type=int, default=10)
    p.add_argument('--no-rerank', action='store_true',
                   help='不调用决策模型重排，只按向量/关键词相似度返回（回退到引入重排之前）')

    args = parser.parse_args()
    config = load_config()
    api_key = args.api_key or os.environ.get('DASHSCOPE_API_KEY', '') or config.get('dashscopeApiKey', '')

    if args.command == 'build':
        result = build_index(api_key, full=args.full)
    elif args.command == 'update':
        result = build_index(api_key, full=False)
    elif args.command == 'search':
        query = args.query or os.environ.get('WEFLOW_SEARCH_QUERY', '')
        if not query:
            result = {"error": "missing search query"}
        else:
            result = search(query, api_key, top_k=args.top_k,
                            rerank_results=not args.no_rerank)
    else:
        result = {"error": f"未知命令: {args.command}"}

    json_output(result)


if __name__ == '__main__':
    main()
