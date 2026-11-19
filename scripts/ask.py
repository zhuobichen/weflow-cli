"""`ask` v1 原型：一条问句走完整条管线，**可中断续跑**。

    plan → route → retrieve → judge → compose → verify → answer

三条硬约束：
1. **每条结论必须挂原话**；挂不上就不许出现在结论里（verify 阶段拦）。
2. **判不了就说判不了**（`insufficient`），不挑一个最像的。
3. **没实现的路由说自己没实现**，不硬凑答案。

状态：每次运行落两个文件——`record.jsonl`（审计，只追加）与
`state.json`（可续跑的阶段产物，原子替换）。前者回答"它做了什么"，
后者回答"它做到哪了"。
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import sys
import time
from datetime import datetime, timedelta, timezone

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ask_routes as policy          # 分权表与路由同在"怎么选"这一层
from ask_judge import classify_v2
from ask_routes import ROUTES, ROUTE_IDS, rule_route

TZ = timezone(timedelta(hours=8))
MAX_EVIDENCE = 8
IMPLEMENTED = ('promises', 'search_chats', 'stats')

# 没实现的路由不是终点：每条都给一个**已经存在**的命令，用户照抄就能继续。
ROUTE_COMMANDS = {
    'export': 'weflow-cli export "联系人A" html',
    'daily': 'weflow-cli daily --date YYYY-MM-DD',
    'relations': 'weflow-cli network --with "联系人A"',
    'contacts': 'weflow-cli contacts --keyword "联系人A"',
    'search_knowledge': 'weflow-cli search "关键词"',
    'unknown': None,
}

# 检索用的停用词：问句里去掉这些才剩下"要找的词"
STOPWORDS = ('找一下', '找找', '帮我', '那段', '这段', '对话', '聊天记录', '什么',
             '哪些', '有没有', '是不是', '怎么', '为什么', '可以', '一下', '关于',
             '说过', '提到', '里面', '里有没有', '我的', '我们', '他们', '然后',
             '那个', '这个', '还有', '以及')


class Run:
    """一次运行的状态容器。record 只追加，state 原子替换。"""

    def __init__(self, question, profile, root=None):
        key = '%s|%s' % (question, profile)
        self.id = 'r-' + hashlib.sha1(key.encode('utf-8')).hexdigest()[:10]
        self.question = question
        self.profile = profile
        base = pathlib.Path(root) if root else (
            pathlib.Path(os.path.expanduser('~')) / '.weflow-cli' / 'runs')
        self.dir = base / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.record = self.dir / 'record.jsonl'
        self.state_path = self.dir / 'state.json'
        self.t0 = time.perf_counter()
        self.degraded: list[str] = []
        self.resumed: list[str] = []
        self._state = self._load()
        self._log('plan', {'question': question, 'profile': profile,
                           'resumed_from': sorted(self._state)})

    # ── 状态 ────────────────────────────────────────────────
    def _load(self) -> dict:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding='utf-8'))
        except Exception:
            return {}

    def _log(self, stage, payload):
        rec = {'stage': stage, 't': round(time.perf_counter() - self.t0, 3)}
        rec.update(payload)
        with self.record.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + '\n')

    def get(self, key):
        return self._state.get(key)

    def put(self, key, value):
        self._state[key] = value
        tmp = self.state_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=1),
                       encoding='utf-8')
        os.replace(str(tmp), str(self.state_path))   # 原子替换：半截文件不会被当成完成

    def has(self, key):
        return key in self._state


# ── 各阶段 ──────────────────────────────────────────────────
def _inherit_route_if_anaphoric(run, route, thread):
    """句子里有指代词、而路由没认出来时，借用上一轮的路由。

    **必须带指代词才借**：不带的话（"帮我写个周报"）新话题会被上一轮拖走，
    而那正是"判不了就别猜"最容易退化的地方。
    """
    if route != 'unknown' or thread is None or not thread.enabled:
        return route
    if not any(w in run.question for w in ANAPHORA):
        return route
    prev = (thread.last() or {}).get('route')
    if not prev or prev == 'unknown':
        return route
    run.resumed.append('thread-route')
    run._log('route', {'inherited_from_thread': prev, 'question': run.question})
    return prev


def stage_route(run, providers, thread=None):
    if run.has('route'):
        run.resumed.append('route')
        return run.get('route')

    question = run.question
    if providers['route'] == 'cloud':
        try:
            from jev_client import create_client
            # 必须走配置路径：显式传 key 会落到官方端点（api.typesafe.ai），
            # 本机 451 地区封锁；配置里那对「302 中转 + lock: key」才是通的。
            client = create_client()
            if client is not None:
                answers, usage = client.decide(
                    state='用户问题：%s' % question,
                    questions={'route': {
                        'type': 'choice',
                        'instructions': '这个问题该由哪一类工具处理？'
                                        '不是关于用户自己的微信历史的，选 unknown。',
                        'criteria': {rid: ROUTES[rid]['criteria'] for rid in ROUTE_IDS},
                    }},
                )
                rid = _inherit_route_if_anaphoric(run, answers['route']['choice'], thread)
                run.put('route', rid)
                run._log('route', {'by': 'cloud', 'route': rid,
                                   'model': getattr(client, 'last_model', None),
                                   'tokens': usage.get('input_tokens')})
                return rid
            run.degraded.append('route:no-key->rule')
        except Exception as exc:
            run.degraded.append('route:cloud-error->rule')
            run._log('route', {'by': 'rule(fallback)', 'error': str(exc)[:160]})
    else:
        run.degraded.append('route:%s->rule' % providers['route'])

    rid, hit = rule_route(question)
    rid = _inherit_route_if_anaphoric(run, rid, thread)
    run.put('route', rid)
    run._log('route', {'by': 'rule', 'route': rid, 'hit': hit})
    return rid


def _open_demo_or_real():
    import nt_decrypt as nd
    import promises as P
    cfg = P.load_config()
    db = cfg.get('ntDbPath') or ''
    if not db:
        raise RuntimeError('配置里没有 ntDbPath')
    key = P.decrypt_lock(cfg.get('ntKey', '') or '')
    salt = P.decrypt_lock(cfg.get('ntSalt', '') or '')
    pw = P.decrypt_lock(cfg.get('favPassphrase', '') or cfg.get('decryptKey', '') or '')
    return P, cfg, nd.connect_message_shards(db, key, salt, pw)


def _keywords(question):
    """中文没有空格，按"词"切会把「提到会议纪要的」整块当成一个词，永远匹配不上。

    改用 n-gram：去掉停用词与虚词后，取 2~3 字的连续片段（「会议」「纪要」
    「会议纪」都会出来），再用它们去命中消息文本。取 3 字是为了压掉一部分噪声，
    但**这一步是原型级的**——真实语料上应该换成分词或嵌入，见 README「已知限制」。
    """
    text = question
    for w in STOPWORDS:
        text = text.replace(w, ' ')
    # 只留汉字/字母/数字，按非字词符号断开
    chunks = [c for c in re.split(r'[^\u4e00-\u9fa5A-Za-z0-9]+', text) if c]
    virtual = tuple('的了吗呢吧啊在是我你他她它和与及就都要会能')
    grams = []
    for chunk in chunks:
        for n in (3, 2):
            for i in range(len(chunk) - n + 1):
                g = chunk[i:i + n]
                if all(ch in virtual for ch in g):
                    continue
                if g not in grams:
                    grams.append(g)
    return grams[:12]


# 指代词：出现这些词时，用户的意图多半依赖上一轮（"那后来呢""他回了吗"）
ANAPHORA = ('他', '她', '它', '那', '那个', '这条', '那条', '上面', '刚才',
            '后来', '继续', '然后呢', '同上', '还有呢')


class Thread:
    """线程态：只存**指代所需的最小信息**，不存原文。

    为什么不存原文：存了就会想把它喂给模型，然后 prompt 无界增长；
    而且原话已经在本地库里，随时可回查。这里只留"上一轮在问谁、找什么词"。
    """

    def __init__(self, thread_id=None, root=None):
        self.id = thread_id
        base = pathlib.Path(root) if root else (
            pathlib.Path(os.path.expanduser('~')) / '.weflow-cli' / 'threads')
        self.path = (base / ('%s.json' % thread_id)) if thread_id else None
        self.data = {}
        if self.path and self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding='utf-8'))
            except Exception:
                self.data = {}

    @property
    def enabled(self):
        return self.path is not None

    def last(self):
        return self.data.get('last') or {}

    def save(self, payload):
        if not self.path:
            return
        self.data['last'] = payload
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding='utf-8')
        os.replace(str(tmp), str(self.path))


def _needs_context(question, keywords):
    """要不要借上一轮的上下文：没有可用关键词，或者句子里出现了指代词。"""
    if not keywords:
        return True
    return any(w in question for w in ANAPHORA)


def _db_fingerprint(cfg):
    """库的指纹：路径 + 主库与 -wal 的 (size, mtime_ns)。

    **为什么续跑必须校验它**：证据是从库里取出来的。库变了而 run 复用旧证据，
    用户看到的是"过期的原话"，而界面上没有任何提示——这比慢更糟。
    """
    db = (cfg or {}).get('ntDbPath') or ''
    if not db:
        return None
    parts = [db]
    for suffix in ('', '-wal', '-shm'):
        try:
            st = os.stat(db + suffix)
            parts.append('%s:%d:%d' % (suffix, st.st_size, st.st_mtime_ns))
        except OSError:
            parts.append('%s:missing' % suffix)
    return hashlib.sha1('|'.join(parts).encode('utf-8')).hexdigest()[:12]


def _grams_of(text):
    """从一段文本取 2~3 字片段（不删停用词）。

    与 `_keywords` 的区别：那个是**从问句里**提检索词，会去掉"的了吗"这类词；
    这里是**从一句原话里**取可比对的片段，去掉了反而对不上。
    """
    grams = []
    for chunk in re.split(r'[^\u4e00-\u9fa5A-Za-z0-9]+', text or ''):
        if not chunk:
            continue
        for n in (3, 2):
            for i in range(len(chunk) - n + 1):
                g = chunk[i:i + n]
                if g not in grams:
                    grams.append(g)
    return grams[:16]


def _gram_hits(text, grams):
    """返回命中的 gram 列表（保持 grams 的顺序，便于复现）。"""
    return [g for g in grams if g in text]


def _is_relevant(hits):
    """相关性判据：一个 3-gram，或者至少两个不同的 2-gram。

    只有一个 2-gram 命中不判相关——中文里两字词太容易撞上，
    "会议"能同时命中"会议纪要""开会""会议室"，那不是同一件事。
    """
    if not hits:
        return False
    if any(len(g) >= 3 for g in hits):
        return True
    return len(set(hits)) >= 2


def stage_retrieve(run, route, thread=None):
    # 只读配置算指纹——不打开数据库，所以校验本身几乎没有成本。
    import promises as _P
    fp = _db_fingerprint(_P.load_config())

    if run.has('candidates'):
        if run.get('db_fp') == fp:
            run.resumed.append('retrieve')
            return run.get('candidates')
        # 库变了：旧证据作废，连带下游一起重算。**这是续跑最容易出错的点**——
        # 复用旧证据会给出"过期的原话"，而界面上看不出任何异常。
        for k in ('candidates', 'items', 'composed', 'verified'):
            run._state.pop(k, None)
        run._state['db_fp'] = fp
        run.put('_stale', True)
        run._log('retrieve', {'stale': True, 'reason': 'db fingerprint changed'})

    P, cfg, conns = _open_demo_or_real()
    try:
        by_talker = P.load_messages(conns, 60, cfg.get('wxid') or '')
    finally:
        for c in conns:
            try:
                c.close()
            except Exception:
                pass

    out = []
    if route == 'promises':
        for talker, msgs in by_talker.items():
            for i, (ts, is_me, _who, text) in enumerate(msgs):
                if not is_me or not P.PROMISE_RE.search(text) or P._NOT_PROMISE.search(text):
                    continue
                out.append({'kind': 'promise', 'ts': ts, 'talker': talker,
                            'said': text[:100],
                            'follow': [[bool(m[1]), m[3]] for m in msgs[i + 1:i + 20]]})
    elif route == 'stats':
        # 聚合类：不找"哪一句"，而是给口径。数据全部来自同一次 load_messages，
        # 所以"共 N 条"与下面各会话的数字天然一致，不会两处算两遍。
        per = {}
        stamps = []
        for talker, msgs in by_talker.items():
            if not msgs:
                continue
            per[talker] = len(msgs)
            stamps.extend(m[0] for m in msgs)
        if stamps:
            lo = datetime.fromtimestamp(min(stamps), tz=TZ).strftime('%Y-%m-%d')
            hi = datetime.fromtimestamp(max(stamps), tz=TZ).strftime('%Y-%m-%d')
            top = sorted(per.items(), key=lambda kv: kv[1], reverse=True)[:5]
            summary = ('共 %d 条消息，覆盖 %d 个会话，时间范围 %s ~ %s'
                       % (sum(per.values()), len(per), lo, hi))
            out.append({'kind': 'stat', 'ts': max(stamps), 'talker': '（全部会话）',
                        'said': summary, 'top': top})
    elif route == 'search_chats':
        kws = _keywords(run.question)
        if thread is not None and thread.enabled and _needs_context(run.question, kws):
            inherited = (thread.last() or {}).get('keywords') or []
            if inherited:
                # 指代解析：**用上一轮的检索词，不改用户的问题**。
                kws = inherited
                run.resumed.append('thread-context')
                run._log('retrieve', {'resolved_from_thread': True, 'keywords': kws})
        for talker, msgs in by_talker.items():
            for ts, _is_me, _who, text in msgs:
                hits = _gram_hits(text, kws)
                if _is_relevant(hits):
                    out.append({'kind': 'hit', 'ts': ts, 'talker': talker,
                                'said': text[:100], 'keywords': kws,
                                'hits': hits})
    out.sort(key=lambda c: c['ts'], reverse=True)
    out = out[:MAX_EVIDENCE]
    run.put('candidates', out)
    run.put('db_fp', fp)
    run._log('retrieve', {'by': route, 'candidates': len(out), 'db_fp': fp,
                          'keywords': _keywords(run.question) if route == 'search_chats' else None})
    return out


def stage_judge(run, candidates, providers):
    if run.has('items'):
        run.resumed.append('judge')
        return run.get('items')

    judge = None
    if providers['judge'] == 'local':
        try:
            import jev_client as jc
            client = jc.LocalJevClient()
            client.decide(state='探测', questions={'q': {
                'type': 'noul', 'instructions': '探测', 'criteria': jc.BINARY}})

            def judge(instruction, text):
                a, _u = client.decide(state=text, questions={'q': {
                    'type': 'noul', 'instructions': instruction,
                    'criteria': {'true': '是', 'false': '不是'}}})
                return float(a['q']['noul'])
        except Exception:
            run.degraded.append('judge:local-unavailable->rule')

    items = []
    for c in candidates:
        if c['kind'] == 'stat':
            items.append({'time': _day(c['ts']), 'talker': c['talker'],
                          'said': c['said'], 'state': 'found',
                          'evidence': c['said'], 'verdict': 'answered',
                          'path': 'aggregate', 'top': c.get('top') or []})
            continue
        if c['kind'] == 'hit':
            items.append({'time': _day(c['ts']), 'talker': c['talker'],
                          'said': c['said'], 'state': 'found',
                          'evidence': c['said'], 'verdict': 'answered',
                          'path': 'direct-hit'})
            continue
        state, evidence, verdict, path = classify_v2(
            [(is_me, text) for is_me, text in c['follow']], judge=judge)
        items.append({'time': _day(c['ts']), 'talker': c['talker'],
                      'said': c['said'], 'state': state, 'evidence': evidence,
                      'verdict': verdict, 'path': path})

    run.put('items', items)
    run._log('judge', {'by': 'local' if judge else 'rule', 'items': len(items),
                       'states': {s: sum(1 for i in items if i['state'] == s)
                                  for s in ('delivered', 'acked', 'chased', 'unknown', 'found')}})
    return items


LBL = {'delivered': '已兑现', 'acked': '已确认收货', 'chased': '可能还欠着',
       'unknown': '看不清', 'found': '命中'}


def _model_compose(route, items, timeout=60):
    """用配置里的对话模型把已定好的事实串成人话。

    **它不参与判断**：输入是已经判好的条目，输出只允许重写措辞。
    所以这段代码不关心"这条算不算已交付"——那是 L4 的事。
    """
    import json as _json
    import urllib.request
    import _utils

    base, model = _utils.get_chat_endpoint() or (None, None)
    key = _utils.get_api_key() or ''
    if not (base and model and key):
        # 与 router 同一条兜底：aiEngine=deepseek 时用官方端点
        if (_utils.get_ai_engine() or '').strip().lower() == 'deepseek':
            from jev_client import DEEPSEEK_BASE
            base, model = DEEPSEEK_BASE, 'deepseek-chat'
            key = _utils.decrypt_lock(_utils.load_config().get('deepseekApiKey', '') or '')
    if not (base and model and key):
        return None, 'no-chat-endpoint'

    facts = []
    for n, it in enumerate(items, 1):
        tail = '后续：%s' % it['evidence'] if it['evidence'] else '后续：没有可引用的证据'
        facts.append('[E%d] %s · %s｜我说：%s｜%s' % (n, it['time'], it['talker'], it['said'], tail))
    prompt = (
        '把下面这些**已经确认的事实**写成一段简短的中文说明，给用户自己核对。\n'
        '硬性要求：\n'
        '1. 每个结论后面必须带对应的编号，例如 [E1]；\n'
        '2. 只能使用给出的内容，不许补充没有的信息，也不许替用户下判断；\n'
        '3. 标着「后续」的条目，必须把后续那件事写出来；只有标着「后续：无」的，\n'
        '   才可以写「还没有看到后续证据」；\n'
        '4. 不许把「对方催过」写成「没有后续证据」。\n\n'
        + '\n'.join(facts)
    )
    payload = _json.dumps({
        'model': model,
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': 0,
        'max_tokens': 400,
    }).encode('utf-8')
    req = urllib.request.Request(
        base.rstrip('/') + '/chat/completions', data=payload, method='POST',
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer %s' % key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = _json.loads(resp.read())
        text = (data['choices'][0]['message']['content'] or '').strip()
        return (text or None), None
    except Exception as exc:
        return None, '%s: %s' % (type(exc).__name__, str(exc)[:80])


NEGATED_EVIDENCE = ('没有可引用的证据', '还没有看到后续证据', '没有看到后续证据',
                    '没有后续证据', '无后续证据')


def _compose_conflicts(text, items):
    """找出"这一条其实有后续，但被写成没有"的引用编号。

    句子级判断：按句号/分号/换行切开，只看含 [En] 的那一句。
    宁可误判（回退模板），不可放过——模板至少不会改事实。
    """
    import re as _re
    bad = []
    # 先把换行统一成句号，再用单个分隔符切——避免在正则里写转义（这一行前后有
    # 多层引号，转义很容易被吃掉，实测踩过一次）。
    flat = (text or '').replace(chr(10), '。')
    for chunk in _re.split(r'[。；;]', flat):
        for n, it in enumerate(items, 1):
            ref = '[E%d]' % n
            if ref not in chunk:
                continue
            if it.get('evidence') and any(w in chunk for w in NEGATED_EVIDENCE):
                # 返回**裸 ref**（E1），与 citations 里的 ref 同形；
                # 带方括号是正文里的写法，不该泄漏到数据里。
                bad.append('E%d' % n)
    return bad


def stage_compose(run, items, route, composer=None):
    if run.has('composed'):
        run.resumed.append('compose')
        return run.get('composed')

    if not items:
        composed = {'text': '没有找到相关记录。', 'citations': [], 'verdict': 'insufficient'}
        run.put('composed', composed)
        run._log('compose', {'by': 'template', 'empty': True})
        return composed

    cites_preview = [{'ref': 'E%d' % n, 'time': it['time'], 'said': it['said']}
                     for n, it in enumerate(items, 1)]

    if composer is not None:
        text, why = composer(route, items)
        if text:
            missing = [c['ref'] for c in cites_preview if c['ref'] not in text]
            conflicts = _compose_conflicts(text, items) if not missing else []
            if not missing and not conflicts:
                verdict = ('insufficient'
                           if all(i['state'] == 'unknown' for i in items) else 'answered')
                composed = {'text': text, 'citations': cites_preview, 'verdict': verdict,
                            'by': 'model'}
                run.put('composed', composed)
                run._log('compose', {'by': 'model', 'citations': len(cites_preview)})
                return composed
            reason = 'missing_refs' if missing else 'fact_conflict'
            run._log('compose', {'by': 'model', 'fallback': reason,
                                 'missing': missing, 'conflicts': conflicts})
        else:
            run._log('compose', {'by': 'model', 'fallback': why or 'empty'})

    lines, cites = [], []
    if route == 'stats':
        head = '统计结果（只读本地库，口径写在括号里）：'
    elif route == 'promises':
        head = '最近 60 天里，"我"承诺过 %d 件事：' % len(items)
    else:
        head = '找到 %d 条相关记录：' % len(items)
    lines += [head, '']
    for n, it in enumerate(items, 1):
        tag = 'E%d' % n
        cites.append({'ref': tag, 'time': it['time'], 'said': it['said']})
        lines.append('[%s] %s · %s' % (tag, it['time'], it['talker']))
        if route == 'stats':
            lines.append('     口径：%s' % it['said'])
            for talker, n in (it.get('top') or []):
                lines.append('       - %s：%d 条' % (talker, n))
        elif route == 'promises':
            lines.append('     我说：%s' % it['said'])
            if it['evidence']:
                lines.append('     后续（%s）：%s' % (LBL[it['state']], it['evidence']))
            else:
                lines.append('     后续：没有可引用的证据（%s）' % LBL[it['state']])
        else:
            lines.append('     %s' % it['said'])
        lines.append('')

    verdict = 'insufficient' if all(i['state'] == 'unknown' for i in items) else 'answered'
    composed = {'text': '\n'.join(lines), 'citations': cites, 'verdict': verdict,
                'by': 'template'}
    run.put('composed', composed)
    run._log('compose', {'by': 'template', 'citations': len(cites), 'verdict': verdict})
    return composed


def stage_verify(run, items):
    if run.has('verified'):
        run.resumed.append('verify')
        return run.get('verified')
    # 两条检查：
    #  1. 有结论的条目必须有原话（`unknown` 豁免——它就是在说没证据）
    #  2. 引用编号必须真的出现在答案正文里（否则"引用了但没写上"，读者核对不了）
    bad = [i for i in items if i['state'] != 'unknown' and not i.get('evidence')]
    composed = run.get('composed') or {}
    text = composed.get('text') or ''
    cites = composed.get('citations') or []
    missing_refs = [c['ref'] for c in cites if c.get('ref') and c['ref'] not in text]
    # 相关性：引用里那句原话要在正文里留下可识别的片段。
    # 判据与检索一致（一个 3-gram 或两个 2-gram）——允许改写措辞，不允许换一件事。
    unrelated = []
    for c in cites:
        said = (c.get('said') or '').strip()
        if not said:
            continue
        # 太短的原话（≤3 字）用逐字包含判断：它凑不出两个 n-gram，
        # 用 n-gram 判据会把正确的引用误杀。
        if len(said) <= 3:
            if said not in text:
                unrelated.append(c['ref'])
            continue
        grams = _grams_of(said)
        if not _is_relevant(_gram_hits(text, grams)):
            unrelated.append(c['ref'])
    ok = not bad and not missing_refs and not unrelated
    run.put('verified', {'ok': ok, 'failed': len(bad), 'missing_refs': missing_refs,
                         'unrelated': unrelated})
    run._log('verify', {'ok': ok,
                        'checks': ['citation_for_claim', 'ref_present_in_text',
                                   'citation_matches_text'],
                        'failed': len(bad), 'missing_refs': missing_refs,
                        'unrelated': unrelated})
    return {'ok': ok, 'failed': len(bad), 'missing_refs': missing_refs,
            'unrelated': unrelated}


def _day(ts):
    return datetime.fromtimestamp(ts, tz=TZ).strftime('%Y-%m-%d')


# ── 入口 ────────────────────────────────────────────────────
def ask(question, profile='balanced', root=None, resume=True, thread_id=None,
        thread_root=None):
    providers = policy.profile(profile)
    thread = Thread(thread_id, root=thread_root)
    run = Run(question, profile, root=root)
    if not resume:
        run._state = {}
        run.put('_fresh', True)

    route = stage_route(run, providers, thread)
    if route not in IMPLEMENTED:
        run._log('answer', {'route': route, 'implemented': False})
        hint = ROUTE_COMMANDS.get(route)
        note = '已实现 %s；这条被判为 %s，尚未实现。' % (' / '.join(IMPLEMENTED), route)
        if hint:
            note += '可以直接用现成命令：%s' % hint
        return {'success': True, 'route': route, 'answer': None,
                'verdict': 'unimplemented', 'run_id': run.id, 'note': note,
                'suggest': hint,
                'degraded': run.degraded, 'resumed': run.resumed}

    candidates = stage_retrieve(run, route, thread)
    items = stage_judge(run, candidates, providers)
    composer = _model_compose if providers['compose'] == 'cloud' else None
    composed = stage_compose(run, items, route, composer=composer)
    check = stage_verify(run, items)

    # 只在线程模式下记录实体：默认不落盘，避免"忘了关"变成长期留存。
    if thread.enabled:
        thread.save({
            'question': question,
            'route': route,
            'keywords': _keywords(question) or (thread.last() or {}).get('keywords') or [],
            'talkers': sorted({i['talker'] for i in items})[:5],
        })

    run._log('answer', {'route': route, 'verdict': composed['verdict'],
                        'ok': check['ok'], 'citations': len(composed['citations'])})
    return {'success': True, 'route': route, 'answer': composed['text'],
            'citations': composed['citations'], 'verdict': composed['verdict'],
            'verified': check['ok'], 'run_id': run.id,
            'run_dir': str(run.dir), 'degraded': run.degraded,
            'resumed': run.resumed}


def main():
    args = [a for a in sys.argv[1:]]
    if not args or args[0] in ('-h', '--help'):
        print('用法: python ask.py "问题" [--profile balanced|offline|cloud] '
              '[--thread <id>] [--json] [--fresh]')
        raise SystemExit(2)
    question = args[0]
    profile = args[args.index('--profile') + 1] if '--profile' in args else 'balanced'
    thread_id = args[args.index('--thread') + 1] if '--thread' in args else None
    result = ask(question, profile, resume='--fresh' not in args, thread_id=thread_id)

    if '--json' in args:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print('问题：%s' % question)
    print('路由：%s ｜ 档位：%s' % (result['route'], profile))
    if result['degraded']:
        print('降级：%s' % ', '.join(result['degraded']))
    if result.get('resumed'):

        print('续跑：跳过 %s' % ', '.join(result['resumed']))
    print()
    print(result.get('answer') or result.get('note'))
    print('判定：%s ｜ 引用校验：%s ｜ run: %s'
          % (result['verdict'],
             '通过' if result.get('verified') else '失败/未执行',
             result['run_id']))


if __name__ == '__main__':
    main()
