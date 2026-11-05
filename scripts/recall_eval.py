#!/usr/bin/env python3
"""语义索引的**召回/排序评价**（可复跑、可对比的评测台）。

为什么要有它：2026-10-15 之前，检索质量的结论都来自"手工试问 + 看前几条"——那种判据查不出
"改完是变好还是变坏"，也复现不了。指标一律用业界标准口径，不自创：

  · Recall@1/5/10   —— 该回来的回来没有（RAG 真正在乎的是 @5/@10）
  · MRR@10          —— 第一条正确结果的倒数名次（单答案查询的主指标，MS MARCO 口径）
  · nDCG@10         —— 排序质量（BEIR / MTEB 的主指标；这里是二值相关性、单条标准答案，
                        所以 nDCG@10 = 1/log2(1+rank)，命中在第 10 名之外记 0）
  · Hit@1           —— 与 Recall@1 同义，单列是因为中文规范（《人工智能 检索增强生成系统测评
                        规范》2024003631）里用的是"命中率"这个词
  · 覆盖（coverage）—— **这一条是我们这类语料特有的**：答案在库里根本不存在时，任何排序都救不了，
                        把它算进召回率是在诬赖排序。所以每题先判"库里有没有"。

两套标准答案（都不依赖人工标注、也不把用户数据写进仓库）：
  · `frag`  —— 从记录**自己正文中段**取 12 字干净片段当问句（唯一性已校验）。测"局部能不能召回整条"。
  · `title` —— 文章用它的标题、聊天用它开头 30 字。这是用户最常干的事（记得那句话）。
  · `question`（可选）—— 你手写的真问题 + 一个"答案里必然出现的字面量"当锚，走 `--queries-file`
                        （TSV：问题<TAB>锚<TAB>线）。**该文件请放在仓库外**：锚串取自你的语料。

`--dedup` 会先把**同文本**的记录合并再排名（多号转载同一篇时，top-k 里塞满同一份内容会虚高
召回、掏空信息量）。它与不合并的两种名次都报，差多少一眼能看出来。

用法（调 embedding 服务，需 --yes）：
  python scripts/recall_eval.py --records 30 --yes
  python scripts/recall_eval.py --records 30 --dedup --json --yes
  python scripts/recall_eval.py --queries-file D:/tmp/q.tsv --records 0 --yes
"""

import argparse
import importlib.util
import json
import math
import os
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

spec = importlib.util.spec_from_file_location('semantic_search', HERE / 'semantic_search.py')
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)

CJK = lambda ch: '\u4e00' <= ch <= '\u9fff'
BANNED = set(' \t\n\r:/\\"\'|<>（）()【】[],;○●·、。！？，')
JUNK = set('=_&%/.?#@~^`$*+{}[]')


def clean_window(text, mid, length=12):
    """取一段干净的中文片段（≥60% 汉字、不含标点/URL 残片/长数字）——否则会拿 URL 碎片当问句。"""
    for start in range(max(0, mid), max(0, min(len(text) - length, mid + 60))):
        w = text[start:start + length]
        if len(w) < length or set(w) & BANNED or set(w) & JUNK:
            continue
        if sum(1 for c in w if CJK(c)) / len(w) < 0.6 or re.search(r'\d{5,}', w):
            continue
        return w
    return None


def rank_of(order, target_group):
    """目标（或其同文本组）在结果里的名次；不在表里返回 None。"""
    for i, idx in enumerate(order, 1):
        if idx in target_group:
            return i
    return None


def metrics(rank, k=10):
    hit = rank is not None and rank <= k
    return {
        'hit@1': 1.0 if rank == 1 else 0.0,
        'recall@1': 1.0 if rank == 1 else 0.0,
        'recall@5': 1.0 if rank is not None and rank <= 5 else 0.0,
        'recall@10': 1.0 if hit else 0.0,
        'mrr@10': (1.0 / rank) if hit else 0.0,
        'ndcg@10': (1.0 / math.log2(1 + rank)) if hit else 0.0,
    }


def build_cases(meta, texts, per_line, seed):
    """自动造题：每线抽 per_line 条记录，各出 frag / title 两种问句。"""
    groups = {}
    for i, t in enumerate(texts):
        groups.setdefault(hash(t), []).append(i)

    rng = random.Random(seed)
    cases = []
    for kind, line, minlen in (('article', 'wiki', 220), ('chat', 'chat', 70)):
        pool = [i for i, m in enumerate(meta) if m.get('type') == kind and len(texts[i]) >= minlen]
        rng.shuffle(pool)
        picked = 0
        for i in pool:
            if picked >= per_line:
                break
            t = texts[i]
            body = t[t.find('\n') + 1:] if kind == 'article' and t.find('\n') >= 0 else t
            frag = clean_window(body, len(body) // 3) or clean_window(body, len(body) // 2)
            title = (meta[i].get('title') or '').strip() or clean_window(t, 0, 30)
            if not frag or not title:
                continue
            group = set(groups[hash(t)])
            cases.append({'form': 'frag', 'line': line, 'idx': i, 'group': group, 'query': frag})
            cases.append({'form': 'title', 'line': line, 'idx': i, 'group': group, 'query': title})
            picked += 1
    return cases, groups


def load_questions(path, texts):
    """TSV：问题<TAB>锚<TAB>线 → 标准答案 = 含锚的记录集合（覆盖为 0 的题单独列出，不计入召回）。"""
    cases = []
    for raw in Path(path).read_text(encoding='utf-8').splitlines():
        raw = raw.strip()
        if not raw or raw.startswith('#') or '\t' not in raw:
            continue
        parts = raw.split('\t')
        q, anchor = parts[0].strip(), parts[1].strip()
        line = parts[2].strip() if len(parts) > 2 else 'all'
        truth = {i for i, t in enumerate(texts) if anchor.lower() in t.lower()}
        cases.append({'form': 'question', 'line': line, 'idx': -1, 'group': truth or set(),
                      'query': q, 'anchor': anchor, 'covered': bool(truth)})
    return cases


def main():
    ap = argparse.ArgumentParser(description='语义索引召回/排序评测台')
    ap.add_argument('--records', type=int, default=30, help='每条线抽多少条记录造题（默认 30）')
    ap.add_argument('--queries-file', help='手写问句 TSV（问题<TAB>锚<TAB>线）；请放仓库外')
    ap.add_argument('--top-k', type=int, default=10)
    ap.add_argument('--seed', type=int, default=7)
    ap.add_argument('--dedup', action='store_true', help='排名前把同文本记录合并')
    ap.add_argument('--hybrid', action='store_true',
                    help='走与 search() 相同的混合检索（向量 + BM25 经门控 RRF 融合）；不加则只测向量')
    ap.add_argument('--rerank', action='store_true',
                    help='调真正的重排（先试 Jev 契约，再退到已配置的对话模型）')
    ap.add_argument('--mmr', action='store_true', help='排名后做 MMR 去冗余（λ=0.7）')
    ap.add_argument('--rerank-pool', type=int, default=ss.RERANK_CANDIDATES,
                    help='重排看多少条候选（默认 %d）' % ss.RERANK_CANDIDATES)
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--yes', action='store_true', help='确认调用云端 embedding（每条约十几 tokens）')
    args = ap.parse_args()

    if not args.yes:
        raise SystemExit('这一步会调用 embedding 服务（花钱、且问句出境）。确认后加 --yes。')

    meta = json.loads(ss.META_FILE.read_text(encoding='utf-8'))
    vectors = None
    import numpy as np
    vectors = np.load(ss.VECTORS_FILE, mmap_mode='r')
    texts = [m.get('text') or '' for m in meta]

    cases, groups = build_cases(meta, texts, args.records, args.seed) if args.records else ([], {})
    if args.queries_file:
        cases += load_questions(args.queries_file, texts)
    if not cases:
        raise SystemExit('没有题（--records 0 且没给 --queries-file）')

    key = ss.get_dashscope_key(ss.load_config())
    embs = ss.get_embeddings([c['query'] for c in cases], key)
    if any(all(v == 0 for v in e) for e in embs):
        raise SystemExit('embedding 服务返回全零 —— 先查 key/网络，别拿这个结果下结论')

    rows = []
    for c, emb in zip(cases, embs):
        v = np.array(emb, dtype=np.float32)
        v = v / np.linalg.norm(v)
        sims = np.asarray(vectors @ v)
        types = ss.line_types(c['line'])
        if types is not None:
            sims = np.where(np.array([str(m.get('type', '')) in types for m in meta]), sims, -np.inf)
        order = [int(i) for i in np.argsort(sims)[::-1]]
        order = [i for i in order if np.isfinite(sims[i])]
        if args.hybrid:
            # 与 search() 同一条路：关键词侧先门控（稀有词比例 + 分数比例），再 RRF 融合。
            # 不加这个开关时评测只走向量 —— 那测的不是用户实际用的东西（D-110/D-111）。
            mask = None
            if types is not None:
                mask = np.array([str(m.get('type', '')) in types for m in meta])
            try:
                kw = ss.keyword_rank(c['query'], meta, mask,
                                     rare_ratio=ss.HYBRID_RARE_RATIO,
                                     score_ratio=ss.HYBRID_SCORE_RATIO)
                kw_order = [int(i) for i, _sc in kw]
                order = [int(i) for i, _sc in ss.rrf_fuse([order, kw_order])]
            except Exception as exc:
                print('  [WARN] 混合检索失败，退回纯向量：%s' % exc, file=sys.stderr)
        if args.rerank:
            # 与 `search()` 同一条路：先按原契约（Jev）排，失败再退到已配置的对话模型
            pool = [{**meta[i], '_idx': i, 'score': float(sims[i])}
                    for i in order[:args.rerank_pool]]
            ranked = ss.rerank_dispatch(c['query'], pool, ss.create_client(),
                                         pool_size=args.rerank_pool)
            order = [it['_idx'] for it in ranked] + list(order[args.rerank_pool:])
        if args.mmr:
            pool2 = [{**meta[i], '_row': i, 'rerankScore': r.get('rerankScore')}
                     for i, r in zip(order[:args.rerank_pool], ranked)]
            order = [it['_row'] for it in ss.mmr_select(pool2, vectors, args.top_k)]
        if args.dedup:
            seen, keep = set(), []
            for i in order:
                h = hash(texts[i])
                if h in seen:
                    continue
                seen.add(h)
                keep.append(i)
            order = keep
        rank = rank_of(order, c['group'])
        # 多样性：最终 top-k 里两两余弦的均值 —— 越低说明结果越不重复。
        # 加它是因为 MMR 这类去冗余与「单答案召回」是相冲的，只看召回会误判。
        top = [i for i in order[:args.top_k] if i < len(texts)]
        div = None
        if len(top) >= 2:
            mat = np.asarray(vectors[top], dtype=np.float32)
            sims = mat @ mat.T
            n = len(top)
            div = float((sims.sum() - np.trace(sims)) / (n * (n - 1)))
        rows.append({**c, 'group': None, 'rank': rank, 'div': div,
                     'metrics': metrics(rank, args.top_k)})

    def agg(sel):
        covered = [r for r in sel if r['rank'] is not None or (r.get('covered') is not False)]
        scored = [r for r in covered if r['rank'] is not None]
        out = {'n': len(sel), 'n_covered': len(covered),
               'n_coverage_gap': sum(1 for r in sel if r.get('covered') is False)}
        for k in ('hit@1', 'recall@1', 'recall@5', 'recall@10', 'mrr@10', 'ndcg@10'):
            out[k] = (sum(r['metrics'][k] for r in covered) / len(covered)) if covered else 0.0
        out['n_recalled'] = len(scored)
        divs = [r['div'] for r in sel if r.get('div') is not None]
        out['pairwiseDiversity'] = (sum(divs) / len(divs)) if divs else None
        return out

    report = {'config': {'records': args.records, 'seed': args.seed, 'dedup': args.dedup,
                         'rerank': args.rerank, 'rerankPool': args.rerank_pool, 'mmr': args.mmr, 'rerankState': dict(ss.RERANK_STATE),
                         'topK': args.top_k, 'indexCounts': ss.line_counts()},
              'overall': agg(rows), 'byGroup': {}, 'cases': [
                  {'form': r['form'], 'line': r['line'], 'query': r['query'], 'rank': r['rank']}
                  for r in rows]}
    for form in sorted({r['form'] for r in rows}):
        for line in sorted({r['line'] for r in rows if r['form'] == form}):
            sel = [r for r in rows if r['form'] == form and r['line'] == line]
            if sel:
                report['byGroup']['%s/%s' % (form, line)] = agg(sel)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print('题数 %d（覆盖缺口 %d）  dedup=%s' % (report['overall']['n'],
                                          report['overall']['n_coverage_gap'], args.dedup))
    print('%-16s %6s %8s %8s %8s %8s %8s %9s'
          % ('组', 'n', 'hit@1', 'recall@5', 'recall@10', 'mrr@10', 'ndcg@10', '多样性'))
    for name, m in [('overall', report['overall'])] + list(report['byGroup'].items()):
        div = m.get('pairwiseDiversity')
        print('%-16s %6d %8.2f %8.2f %8.2f %8.2f %8.2f %9s'
              % (name, m['n'], m['hit@1'], m['recall@5'], m['recall@10'], m['mrr@10'],
                 m['ndcg@10'], ('%.3f' % div) if div is not None else '-'))


if __name__ == '__main__':
    main()
