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

**这是出境调用**：state 会被发到 api.typesafe.ai。命令自己**不读任何本地数据**——
state 是什么，完全由调用方决定。
"""
import argparse
import json
import sys
import urllib.error

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
from jev_client import INPUT_USD_PER_TOKEN, JevError, create_client  # noqa: E402

QUESTION_TYPES = ('choice', 'score', 'noul')


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
    parser.add_argument('--model', default='', help='模型别名，默认 jev-latest')
    parser.add_argument('--dry-run', action='store_true', help='只校验并回显请求，不调用')
    args = parser.parse_args()

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

    state, questions, error = validate_request(payload)
    if error:
        print(json.dumps({'success': False, 'code': 'INVALID_REQUEST',
                          'error': error}, ensure_ascii=False))
        return 2

    if args.dry_run:
        size = len(state) if isinstance(state, str) else len(
            json.dumps(state, ensure_ascii=False))
        print(json.dumps({
            'success': True, 'dryRun': True, 'action': 'decide',
            'questions': list(questions), 'questionCount': len(questions),
            'stateChars': size, 'invokesAI': True, 'readsLocalData': False,
        }, ensure_ascii=False))
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
    print(json.dumps({
        'success': True,
        'model': client.model,
        'answers': answers,
        'usage': usage,
        # 把成本一起回给调用方：一个 Agent 要能自己决定"再问一批"划不划算。
        'costUsd': round(tokens * INPUT_USD_PER_TOKEN, 8),
    }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
