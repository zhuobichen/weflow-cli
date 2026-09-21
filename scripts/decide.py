#!/usr/bin/env python3
"""本机判断层 —— 一个 state、一批类型化问题、一次调用，返回带概率的答案。

**它解决的不是"判断得更好"，是"判断得起"。** 调用方自己的模型当然也能判断，
但那是按 token 收费、按次往返、还要把散文解析回结构。这里一次请求问 20 个问题
实测约 1 秒，且问题数几乎不影响成本（2 个 0.84s / 12 个 0.91s），所以
**"给 N 个条目各打一批标签"这件事第一次变得便宜**。

典型用法是一个 Agent 在批量处理时把判断外包出来：

    echo '{"state": "...", "questions": {"相关": {"type": "noul",
           "instructions": "这段是否与大气污染有关？"}}}' | python scripts/decide.py

    python scripts/decide.py --request req.json
    python scripts/decide.py --request req.json --dry-run   # 只校验不调用

输出（stdout，JSON）：

    {"success": true, "model": "jev-1.13.0", "answers": {...},
     "usage": {"input_tokens": 1200, "output_tokens": 60}, "costUsd": 5.04e-05}

请求格式**本地先校验**：形状不对就在本机报错，不让它变成一个来自服务端的 422——
那种错误只告诉你"哪里不对"里最不重要的那部分。

    state     : 字符串 / JSON 对象 / 数组（共享给所有问题）
    questions : {名字: {type, instructions, criteria}}
                choice → criteria 是对象 {选项名: 何时选它}
                score  → criteria 是有序数组，**位置即分值，从 0 开始**
                noul   → 不需要 criteria（可选 {true, false}）

批量模式把最常用的那层展开做掉了——一个 glob × 一批问题，自动变成 N×M 个问题：

    python scripts/decide.py --over "scripts/*.py" --ask "会写本地文件吗" --ask "会出网吗" --yes

**这是出境调用**：state 会被发到 api.typesafe.ai。读不读本地由输入方式决定：
`--request`/stdin **完全由调用方给**，命令自己不读任何文件；`--over` 会读匹配到的
文件（所以它是一条读本地数据的路径，`capabilities` 里也如实分开写了）。
"""
import argparse
import glob
import json
import os
import sys
import urllib.error

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
from jev_client import INPUT_USD_PER_TOKEN, JevError, create_client  # noqa: E402

QUESTION_TYPES = ('choice', 'score', 'noul')


def expand_batch(patterns, asks, max_chars, limit):
    """把「一批文件 × 一批问题」展开成一次请求的 state 与 questions。

    问题名是 `f<序号>|q<序号>` 而不是把文件名编进去：文件名里有 `|`、空格、中文标点，
    编进去之后调用方要靠解析字符串才能还原。序号 + 响应里的 `batch.files` 是显式的映射。

    **喂多少字符要能看见**：实测同一批问题，只喂每个文件前 14 行和喂前 1500 字符，
    与基准的一致率从 77.3% 变成 88.6%——证据量决定上限。所以它由参数给出，
    并且写进返回里，而不是藏在实现里。
    """
    paths = []
    for pattern in patterns:
        paths.extend(glob.glob(pattern, recursive=True))
    # 排序 + 去重：同一批文件两次跑应该展开成同样的顺序，否则问题名对不上。
    paths = sorted({p for p in paths if os.path.isfile(p)})
    if limit:
        paths = paths[:limit]
    if not paths:
        return None, None, None, '没有匹配到任何文件：%s' % ', '.join(patterns)

    blocks = []
    for index, path in enumerate(paths):
        try:
            with open(path, encoding='utf-8', errors='replace') as handle:
                body = handle.read()
        except OSError as error:
            return None, None, None, '读不到 %s：%s' % (path, error)
        blocks.append('【f%d】%s\n%s' % (index, os.path.basename(path), body[:max_chars]))

    names = [os.path.basename(p) for p in paths]
    state = ('下面是 %d 个文件，每个只给出前 %d 个字符：\n\n%s'
             % (len(paths), max_chars, '\n\n'.join(blocks)))
    questions = {}
    for index, name in enumerate(names):
        for ask_index, instruction in enumerate(asks):
            questions['f%d|q%d' % (index, ask_index)] = {
                'type': 'noul',
                'instructions': '【f%d】（%s）%s' % (index, name, instruction),
            }
    return state, questions, {'files': names, 'asks': list(asks),
                              'maxChars': max_chars}, None


def validate_request(payload):
    """返回 (state, questions, error_message)。error 非空时调用方应当拒绝。

    宁可在这里啰嗦，也不要把一个形状不对的请求发出去换一个 422：
    服务端的校验错误只说得出"哪个字段不合法"，说不出"你本来想干什么"。
    """
    if not isinstance(payload, dict):
        return None, None, '请求必须是一个 JSON 对象'
    state = payload.get('state')
    if state is None or (isinstance(state, str) and not state.strip()):
        return None, None, '缺少 state（字符串 / 对象 / 数组都可以，但不能为空）'
    if not isinstance(state, (str, dict, list)):
        return None, None, 'state 只能是字符串、JSON 对象或数组'

    questions = payload.get('questions')
    if not isinstance(questions, dict) or not questions:
        return None, None, 'questions 必须是一个非空对象：{名字: {type, ...}}'

    for name, question in questions.items():
        if not isinstance(question, dict):
            return None, None, '问题 %r 必须是一个对象' % name
        kind = question.get('type')
        if kind not in QUESTION_TYPES:
            return None, None, ('问题 %r 的 type 必须是 %s 之一，收到 %r'
                                % (name, '/'.join(QUESTION_TYPES), kind))
        criteria = question.get('criteria')
        if kind == 'choice':
            if not isinstance(criteria, dict) or not criteria:
                return None, None, ('choice 问题 %r 需要非空 criteria 对象 '
                                    '{选项名: 何时选它}' % name)
        elif kind == 'score':
            # 位置即分值，只有一个档位的话 score 恒等于 0，毫无信息量。
            if not isinstance(criteria, list) or len(criteria) < 2:
                return None, None, ('score 问题 %r 的 criteria 需要至少两个有序档位'
                                    '（位置即分值）' % name)
        elif criteria is not None and not isinstance(criteria, dict):
            return None, None, 'noul 问题 %r 的 criteria 若要给，必须是对象' % name
    return state, questions, None


def read_request(args):
    if args.request and args.request != '-':
        try:
            with open(args.request, encoding='utf-8') as handle:
                return handle.read()
        except OSError as error:
            raise SystemExit('读不到请求文件：%s' % error)
    return sys.stdin.read()


def main():
    parser = argparse.ArgumentParser(
        description='本机判断层：一个 state + 一批类型化问题，一次调用返回带概率的答案')
    parser.add_argument('--request', help='请求 JSON 的路径，或 - 表示读 stdin（默认读 stdin）')
    parser.add_argument('--over', action='append', default=[], metavar='GLOB',
                        help='批量模式：对匹配到的每个文件问 --ask 里的每个问题（可重复）')
    parser.add_argument('--ask', action='append', default=[], metavar='TEXT',
                        help='批量模式下的一个是非题，逐文件展开（可重复）')
    parser.add_argument('--max-chars', type=int, default=1500, metavar='N',
                        help='批量模式下每个文件喂多少字符（默认 1500；它决定上限）')
    parser.add_argument('--limit', type=int, default=50, metavar='N',
                        help='批量模式最多取多少个文件（默认 50，0 表示不限）')
    parser.add_argument('--model', default='', help='模型别名，默认 jev-latest')
    parser.add_argument('--dry-run', action='store_true', help='只校验并回显请求，不调用')
    args = parser.parse_args()

    batch_meta = None
    if args.over:
        if not args.ask:
            print(json.dumps({'success': False, 'code': 'INVALID_REQUEST',
                              'error': '批量模式需要至少一个 --ask（否则没有问题可问）'},
                             ensure_ascii=False))
            return 2
        state, questions, batch_meta, error = expand_batch(
            args.over, args.ask, args.max_chars, args.limit)
        if error:
            print(json.dumps({'success': False, 'code': 'NO_FILES', 'error': error},
                             ensure_ascii=False))
            return 2
        payload = {'state': state, 'questions': questions}
    elif args.request or not sys.stdin.isatty():
        raw = read_request(args)
        if not raw.strip():
            print(json.dumps({'success': False, 'code': 'EMPTY_REQUEST',
                              'error': 'stdin 或 --request 里没有内容'}, ensure_ascii=False))
            return 2
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            print(json.dumps({'success': False, 'code': 'INVALID_JSON',
                              'error': '请求不是合法 JSON: %s' % error}, ensure_ascii=False))
            return 2
    else:
        print(json.dumps({'success': False, 'code': 'EMPTY_REQUEST',
                          'error': '没有输入：用 --request/--over，或把请求接到 stdin'},
                         ensure_ascii=False))
        return 2

    state, questions, error = validate_request(payload)
    if error:
        print(json.dumps({'success': False, 'code': 'INVALID_REQUEST',
                          'error': error}, ensure_ascii=False))
        return 2

    size = len(state) if isinstance(state, str) else len(
        json.dumps(state, ensure_ascii=False))

    if args.dry_run:
        body = {
            'success': True, 'dryRun': True, 'action': 'decide',
            'questions': list(questions), 'questionCount': len(questions),
            'stateChars': size,
            # 批量模式下这两件事必须报出来：读了本地什么、每份喂了多少（决定上限）。
            'readsLocalData': bool(args.over),
            'invokesAI': True,
        }
        if batch_meta:
            body['batch'] = batch_meta
            body['fileCount'] = len(batch_meta['files'])
            body['maxChars'] = batch_meta['maxChars']
        print(json.dumps(body, ensure_ascii=False))
        return 0

    client = create_client(model=args.model) if args.model else create_client()
    if client is None:
        print(json.dumps({'success': False, 'code': 'NO_KEY',
                          'error': '缺少 TypeSafe key：weflow-cli config set '
                                   'typesafeApiKey "..."'}, ensure_ascii=False))
        return 2

    try:
        answers, usage = client.decide(state, questions)
    except JevError as err:
        print(json.dumps({'success': False, 'code': 'DECIDE_FAILED',
                          'error': str(err)}, ensure_ascii=False))
        return 1
    except urllib.error.URLError as err:  # 兜底：客户端应当已经包好了
        print(json.dumps({'success': False, 'code': 'DECIDE_FAILED',
                          'error': str(err)}, ensure_ascii=False))
        return 1

    tokens = usage.get('input_tokens') or 0
    body = {
        'success': True,
        'model': client.model,
        'answers': answers,
        'usage': usage,
        # 把成本一起回给调用方：一个 Agent 要能自己决定"再问一批"划不划算。
        'costUsd': round(tokens * INPUT_USD_PER_TOKEN, 8),
    }
    if batch_meta:
        # 序号到文件/问题的映射由这里给出，调用方不必去解析问题名。
        body['batch'] = batch_meta
    print(json.dumps(body, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
