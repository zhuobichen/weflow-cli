#!/usr/bin/env python3
"""
全局语义搜索 — 基于 DeepSeek Embedding API + NumPy。

用法:
  # 构建索引（首次或增量）
  python scripts/semantic_search.py build --api-key <key>

  # 搜索
  python scripts/semantic_search.py search "有人推荐过遥感的工具吗" --top-k 10

  # 增量更新（只处理新数据）
  python scripts/semantic_search.py update --api-key <key>

输出: JSON 格式
"""

import sys, os, json, hashlib, argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import numpy as np
except ImportError:
    print(json.dumps({"error": "请安装: pip install numpy"}))
    sys.exit(1)

try:
    from sqlcipher3 import dbapi2 as sqlcipher
except ImportError:
    print(json.dumps({"error": "请安装: pip install sqlcipher3"}))
    sys.exit(1)

try:
    import urllib.request
    import urllib.parse
except:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _utils import load_config, decrypt_lock

OUTPUT_ROOT = 'output'
INDEX_DIR = Path(OUTPUT_ROOT) / '.semantic_index'
VECTORS_FILE = INDEX_DIR / 'vectors.npy'
META_FILE = INDEX_DIR / 'meta.json'
EMBEDDING_DIM = 1536  # DeepSeek embedding dimension
BATCH_SIZE = 100  # Embedding API batch size
TZ = timezone(timedelta(hours=8))


def json_output(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


# ====== Embedding API ======

def get_embeddings(texts: list[str], api_key: str) -> list[list[float]]:
    """Call DeepSeek embedding API."""
    if not texts:
        return []

    url = "https://api.deepseek.com/v1/embeddings"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Batch processing
    all_embeddings = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i:i + BATCH_SIZE]
        data = {
            "model": "deepseek-embedding",  # DeepSeek embedding model
            "input": batch,
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode('utf-8'),
            headers=headers,
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode('utf-8'))
                embeddings = [item['embedding'] for item in result['data']]
                all_embeddings.extend(embeddings)
        except Exception as e:
            print(f"[WARN] Embedding API 调用失败: {e}", file=sys.stderr)
            # Fallback: return zero vectors
            all_embeddings.extend([[0.0] * EMBEDDING_DIM for _ in batch])

    return all_embeddings


# ====== Data Collection ======

def open_db(db_path, key_hex, salt_hex):
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
            for f in topic_dir.glob(' marriage*.md'):
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


def search(query: str, api_key: str, top_k: int = 10):
    """Semantic search with keyword fallback."""
    # Try embedding-based search first
    if VECTORS_FILE.exists() and META_FILE.exists():
        try:
            vectors = np.load(VECTORS_FILE)
            meta = json.loads(META_FILE.read_text(encoding='utf-8'))
            query_embeddings = get_embeddings([query], api_key)
            if query_embeddings and not all(v == 0 for v in query_embeddings[0]):
                query_vec = np.array(query_embeddings[0], dtype=np.float32)
                query_vec = query_vec / np.linalg.norm(query_vec)
                similarities = np.dot(vectors, query_vec)
                top_indices = np.argsort(similarities)[::-1][:top_k]
                results = []
                for idx in top_indices:
                    item = meta[idx]
                    results.append({**item, 'score': float(similarities[idx])})
                return results
        except:
            pass

    # Fallback: keyword search
    return keyword_search(query, top_k)

    results = []
    for idx in top_indices:
        item = meta[idx]
        results.append({
            **item,
            "score": float(similarities[idx]),
        })

    return {
        "query": query,
        "total": len(meta),
        "results": results,
    }


# ====== Main ======

def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='command')

    # build
    p = subparsers.add_parser('build')
    p.add_argument('--api-key', required=True)
    p.add_argument('--full', action='store_true', help='全量重建')

    # update
    p = subparsers.add_parser('update')
    p.add_argument('--api-key', required=True)

    # search
    p = subparsers.add_parser('search')
    p.add_argument('query')
    p.add_argument('--api-key', required=True)
    p.add_argument('--top-k', type=int, default=10)

    args = parser.parse_args()
    api_key = args.api_key or os.environ.get('DEEPSEEK_API_KEY', '')

    if args.command == 'build':
        result = build_index(api_key, full=args.full)
    elif args.command == 'update':
        result = build_index(api_key, full=False)
    elif args.command == 'search':
        result = search(args.query, api_key, top_k=args.top_k)
    else:
        result = {"error": f"未知命令: {args.command}"}

    json_output(result)


if __name__ == '__main__':
    main()
