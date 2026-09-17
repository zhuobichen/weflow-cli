#!/usr/bin/env python3
"""Queue instructions the user sends to the agent mailbox.

A pure script - no model, no tokens. It runs from Windows Task Scheduler and
its only job is to notice new mail from an allowlisted sender and append it to
a local queue file. Acting on those instructions happens later, from a normal
session, which is when a model is running anyway.

That split matters: waking a model every few minutes just to discover there is
no mail costs tokens for nothing. This script does the waiting for free.

    ~/.weflow-mail-queue.jsonl   one JSON object per pending instruction
    ~/.weflow-mail-state.json    message ids already queued

Only mail whose From address is exactly the allowlisted one is ever read or
queued. Mail from anyone else is left untouched - not read, not marked, not
queued.
"""
import json
import os
import subprocess
import sys

STATE_PATH = os.path.join(os.path.expanduser('~'), '.weflow-mail-state.json')
QUEUE_PATH = os.path.join(os.path.expanduser('~'), '.weflow-mail-queue.jsonl')

# Exact address, not a display name: a sender name can say anything.
ALLOWED_SENDERS = {'1473517806@qq.com'}

AGENTLY_CANDIDATES = [
    # The npm shim is a .cmd on Windows; subprocess needs the extension spelled
    # out, since a bare `agently-cli` is not an executable image.
    r'C:\Users\Administrator\AppData\Roaming\npm\agently-cli.cmd',
    'agently-cli.cmd',
    'agently-cli',
]


def _runnable(candidate):
    if os.path.isabs(candidate):
        return os.path.isfile(candidate)
    from shutil import which
    return which(candidate) is not None


def agently(args):
    """Run agently-cli and return its parsed JSON, or None."""
    executable = next((c for c in AGENTLY_CANDIDATES if _runnable(c)), None)
    if not executable:
        print('未找到 agently-cli，跳过。')
        return None
    try:
        result = subprocess.run(
            [executable, *args], capture_output=True, timeout=90, encoding='utf-8')
    except (OSError, subprocess.SubprocessError) as error:
        print(f'agently-cli 调用失败: {type(error).__name__}')
        return None
    # agently-cli pretty-prints its JSON across many lines, so parse the whole
    # payload from the first brace rather than looking for a single-line blob.
    text = result.stdout.strip()
    start = text.find('{')
    if start < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(text[start:])[0]
    except ValueError:
        return None


def load_json(path, fallback):
    try:
        with open(path, encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return fallback


def main():
    state = load_json(STATE_PATH, {'seen': []})
    seen = set(state.get('seen', []))

    listing = agently(['message', '+list', '--limit', '20'])
    if not listing or not listing.get('ok'):
        print('读取邮箱失败（未授权或网络不通），本次跳过。')
        return 0

    messages = (listing.get('data') or {}).get('data') or []
    queued = 0
    for summary in messages:
        sender = (summary.get('from') or {}).get('email', '')
        if sender not in ALLOWED_SENDERS:
            continue
        message_id = summary.get('message_id', '')
        if not message_id or message_id in seen:
            continue

        detail = agently(['message', '+read', '--id', message_id])
        body = ''
        if detail and detail.get('ok'):
            body = ((detail.get('data') or {}).get('text')
                    or (detail.get('data') or {}).get('body') or '')
        if not body:
            body = summary.get('snippet', '')

        entry = {
            'message_id': message_id,
            'from': sender,
            'subject': summary.get('subject', ''),
            'created_at': summary.get('created_at', ''),
            'body': body,
        }
        with open(QUEUE_PATH, 'a', encoding='utf-8') as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + '\n')
        seen.add(message_id)
        queued += 1

    if queued:
        with open(STATE_PATH, 'w', encoding='utf-8') as handle:
            json.dump({'seen': sorted(seen)}, handle, ensure_ascii=False, indent=2)
        print(f'已排队 {queued} 封来自授权发件人的邮件。')
    else:
        # Still persist first-run state so the backlog is not queued later.
        if not os.path.exists(STATE_PATH):
            with open(STATE_PATH, 'w', encoding='utf-8') as handle:
                json.dump({'seen': sorted(seen)}, handle, ensure_ascii=False, indent=2)
        print('无新指令邮件。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
