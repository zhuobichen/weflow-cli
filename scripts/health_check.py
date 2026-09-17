#!/usr/bin/env python3
"""Periodic health check for weflow-cli.

A pure script - no model, no tokens. Every check here exists because that exact
thing has silently broken at least once, and the failure was invisible:

  版本一致        1.6.0 自报 1.5.1, so "which build am I running" was unanswerable
  读取路径一致    只读 message_0.db 时, export json 有 31 条而 export html 有 1145 条
  会话新鲜度      同上, 会话列表停在 18 天前而命令仍然"成功"
  导出格式         excel 全程从未成功过, 报错只有"导出失败"四个字
  Python 依赖     缺 sqlcipher3 时命令报的是"数据库连接失败"
  计划任务        提醒脚本静默停跑, 没人会发现

Run it ad hoc, or schedule it and read the summary line:

    py scripts/health_check.py
    py scripts/health_check.py --json

Exit code is 0 only when every check passes, so a scheduler can act on it.
"""
import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / 'cli.cjs'

# Windows consoles default to a legacy codepage that cannot encode the report.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass

# Each check shells out to the CLI, which takes seconds; keep the sample small.
SAMPLE_LIMIT = 5
TIMEOUT = 300

# A session list that has not moved in this long means reads are stale. WeChat
# is not chatty for everyone, so this is deliberately generous.
STALE_SESSION_DAYS = 7


def run(args, timeout=TIMEOUT):
    """(returncode, stdout, stderr) with Node's experimental warning suppressed."""
    env = {**os.environ, 'NODE_NO_WARNINGS': '1', 'PYTHONIOENCODING': 'utf-8'}
    try:
        proc = subprocess.run(
            ['node', str(CLI), *args], capture_output=True, timeout=timeout,
            encoding='utf-8', errors='replace', env=env, cwd=str(ROOT))
    except (OSError, subprocess.SubprocessError) as error:
        return 1, '', f'{type(error).__name__}: {error}'
    return proc.returncode, proc.stdout or '', proc.stderr or ''


def run_json(args):
    """Parsed JSON payload, or None.

    The CLI pretty-prints, so the payload spans many lines - parse from the
    first brace rather than looking for a single-line blob.
    """
    code, out, _ = run([*args, '--json'])
    start = out.find('{')
    if start < 0:
        return code, None
    try:
        return code, json.JSONDecoder().raw_decode(out[start:])[0]
    except ValueError:
        return code, None


class Report:
    def __init__(self):
        self.rows = []

    def add(self, name, ok, detail, hint=''):
        self.rows.append({'name': name, 'ok': bool(ok), 'detail': detail, 'hint': hint})

    @property
    def failed(self):
        return [r for r in self.rows if not r['ok']]

    def render(self):
        stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
        lines = [f'WeFlow 健康检查 · {stamp}', '']
        width = max(len(r['name']) for r in self.rows) + 2
        for row in self.rows:
            mark = 'OK  ' if row['ok'] else 'FAIL'
            lines.append(f'[{mark}] {row["name"]:<{width}}{row["detail"]}')
            if not row['ok'] and row['hint']:
                lines.append(f'{"":<7}{"":<{width}}↳ {row["hint"]}')
        lines.append('')
        if self.failed:
            lines.append(f'{len(self.rows) - len(self.failed)} 项通过 / {len(self.failed)} 项失败')
        else:
            lines.append(f'全部通过（{len(self.rows)} 项）')
        return '\n'.join(lines)


def check_version(report):
    """The CLI must report the version it actually is."""
    try:
        declared = json.loads((ROOT / 'package.json').read_text(encoding='utf-8'))['version']
    except (OSError, ValueError, KeyError) as error:
        report.add('版本一致', False, f'读不到 package.json ({type(error).__name__})')
        return
    code, out, _ = run(['--version'])
    reported = out.strip().split('\n')[-1].strip() if out.strip() else ''
    report.add('版本一致', code == 0 and reported == declared,
               f'CLI {reported or "?"} / package {declared}',
               '两者不一致说明 --version 又写死了，或 dist 未重新构建')


def check_environment(report):
    """Environment self-check: config present, Python deps importable."""
    _, data = run_json(['check'])
    if not data:
        report.add('环境自检', False, 'check 未返回 JSON', '先跑 weflow-cli check 看原始输出')
        return
    config = data.get('configuration') or {}
    deps = ((data.get('dependencies') or {}).get('required') or {})
    missing = [name for name, status in deps.items() if not status]
    ok = bool(config.get('initialized')) and bool(config.get('messageDatabase')) and not missing
    detail = f'已初始化={config.get("initialized")} 消息库={config.get("messageDatabase")}'
    if missing:
        detail += f' 缺依赖={",".join(missing)}'
    report.add('环境自检', ok, detail,
               '缺依赖: pip install -r requirements.txt；未初始化: weflow-cli init')


def pick_talkers(report):
    """The most recently active sessions - the likeliest to span shards."""
    _, data = run_json(['sessions', '--limit', str(SAMPLE_LIMIT)])
    sessions = ((data or {}).get('sessions') or [])
    if not sessions:
        report.add('选择样本会话', False, '会话列表为空', '先确认微信已登录且 init 成功')
        return []
    ordered = sorted(sessions, key=lambda s: s.get('sortTimestamp') or 0, reverse=True)
    return [s['username'] for s in ordered[:2] if s.get('username')]


def check_read_paths(report, talker):
    """`export json` and `export html` read the same chat by different code.

    They disagreed by 1114 messages when the JSON path read only the first
    shard, so comparing their newest message is the cheapest way to notice the
    read paths drifting apart again.
    """
    with tempfile.TemporaryDirectory(prefix='wf-health-') as tmp:
        out_json = Path(tmp) / 'json'
        code_j, err_j = run(['export', talker, 'json', '--limit', '5',
                             '--output', str(out_json), '--non-interactive'])[0::2]
        newest_json = None
        for path in out_json.glob('*.json'):
            try:
                rows = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            times = [int(r.get('createTime') or 0) for r in rows]
            if times:
                newest_json = max(times)
                break

        out_html = Path(tmp) / 'html'
        code_h, out_h, _ = run(['export', talker, 'html', '--limit', '5',
                                '--output', str(out_html), '--non-interactive'])
        total_html = None
        for line in out_h.split('\n'):
            line = line.strip()
            if line.startswith('Total:') and 'messages' in line:
                try:
                    total_html = int(line.split()[1])
                except (IndexError, ValueError):
                    pass
        # Read the pages while the temp directory still exists.
        newest_html = html_newest_stamp(out_html)

    label = f'读取路径一致 ({talker[:14]}…)'
    if newest_json is None:
        report.add(label, False, 'json 导出没有产出可比对的消息',
                   'export json 失败时先单独跑一次看报错')
        return
    if total_html is None:
        report.add(label, False, 'html 导出没有报告条数',
                   'export html 失败时先单独跑一次看报错')
        return

    newest_txt = datetime.datetime.fromtimestamp(newest_json).strftime('%Y-%m-%d %H:%M')
    if newest_html:
        match = newest_html[:16] == newest_txt
        report.add(label, match,
                   f'json 最新 {newest_txt} / html 最新 {newest_html}',
                   '两条路径读到不同的最新消息，通常又只剩一个分片被读了')
    else:
        # No timestamp in the page (empty chat); the count comparison still holds.
        report.add(label, total_html > 0, f'json 最新 {newest_txt} / html 共 {total_html} 条')


def html_newest_stamp(out_dir):
    """Newest 'YYYY-MM-DD HH:MM:SS' stamp rendered into the exported pages."""
    import re
    newest = ''
    try:
        for page in sorted(Path(out_dir).glob('*.html')):
            text = page.read_text(encoding='utf-8', errors='replace')
            stamps = re.findall(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', text)
            if stamps and stamps[-1] > newest:
                newest = stamps[-1]
    except OSError:
        pass
    return newest


def check_session_freshness(report):
    """A read path that silently stops at an old shard shows up here first."""
    _, data = run_json(['sessions', '--limit', '5'])
    sessions = ((data or {}).get('sessions') or [])
    stamps = [s.get('sortTimestamp') or 0 for s in sessions]
    if not stamps or not max(stamps):
        report.add('会话新鲜度', False, '会话列表没有时间戳', 'init 可能未完成')
        return
    newest = max(stamps)
    age = (time.time() - newest) / 86400
    when = datetime.datetime.fromtimestamp(newest).strftime('%Y-%m-%d %H:%M')
    report.add('会话新鲜度', age <= STALE_SESSION_DAYS,
               f'最新会话 {when}（{age:.1f} 天前）',
               f'超过 {STALE_SESSION_DAYS} 天未更新，通常意味着读取只覆盖了部分分片')


def check_export_formats(report, talker):
    """json / txt / excel must each produce a file. html is covered above."""
    with tempfile.TemporaryDirectory(prefix='wf-health-') as tmp:
        for fmt, pattern in (('json', '*.json'), ('txt', '*.txt'), ('excel', '*.xlsx')):
            out = Path(tmp) / fmt
            code, stdout, stderr = run(['export', talker, fmt, '--limit', '3',
                                        '--output', str(out), '--non-interactive'])
            produced = list(out.glob(pattern)) if out.exists() else []
            size = produced[0].stat().st_size if produced else 0
            detail = f'{size} 字节' if produced else (stderr.strip().split('\n')[-1][:60] or f'exit {code}')
            report.add(f'导出格式 {fmt}', code == 0 and size > 0, detail)


def check_python_deps(report):
    """The NT reader is a Python subprocess; a missing module looks like a DB error."""
    required = {
        'sqlcipher3': 'sqlcipher3',
        'html2text': 'html2text',
        'zstandard': 'zstandard',
        'cryptography': 'cryptography',
    }
    missing = []
    for module, package in required.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    report.add('Python 依赖', not missing,
               '齐全' if not missing else f'缺 {", ".join(missing)}',
               'pip install -r requirements.txt')


def check_scheduled_tasks(report):
    """The watcher tasks must exist and their last run must have succeeded."""
    if os.name != 'nt':
        report.add('计划任务', True, '非 Windows，跳过')
        return
    if not shutil.which('schtasks'):
        report.add('计划任务', True, '找不到 schtasks，跳过')
        return
    names = ['WeFlow Issue Watch', 'WeFlow Mail Watch']
    broken, absent = [], []
    for name in names:
        try:
            proc = subprocess.run(['schtasks', '/query', '/tn', name, '/fo', 'LIST', '/v'],
                                  capture_output=True, timeout=60, encoding='utf-8', errors='replace')
        except (OSError, subprocess.SubprocessError):
            absent.append(name)
            continue
        if proc.returncode != 0:
            absent.append(name)
            continue
        text = proc.stdout or ''
        for line in text.split('\n'):
            if 'Last Result' in line or '上次运行结果' in line:
                result = line.split(':', 1)[-1].strip()
                if result not in ('0', '0x0'):
                    broken.append(f'{name}={result}')
    if broken:
        report.add('计划任务', False, f'上次运行失败: {", ".join(broken)}',
                   '脚本可能抛异常了，手动跑一次看输出')
    elif absent:
        report.add('计划任务', False, f'不存在: {", ".join(absent)}',
                   '提醒不会触发；见 docs/HEALTH-CHECK.md 的注册命令')
    else:
        report.add('计划任务', True, '均在位且上次成功')


def main():
    parser = argparse.ArgumentParser(description='weflow-cli periodic health check')
    parser.add_argument('--json', action='store_true', help='机器可读输出')
    args = parser.parse_args()

    if not CLI.exists():
        print(f'找不到 {CLI}', file=sys.stderr)
        return 2

    report = Report()
    check_version(report)
    check_environment(report)
    check_python_deps(report)
    check_session_freshness(report)

    talkers = pick_talkers(report)
    if talkers:
        check_read_paths(report, talkers[0])
        check_export_formats(report, talkers[0])

    check_scheduled_tasks(report)

    if args.json:
        print(json.dumps({'success': not report.failed, 'checks': report.rows},
                         ensure_ascii=False, indent=2))
    else:
        print(report.render())
    return 1 if report.failed else 0


if __name__ == '__main__':
    sys.exit(main())
