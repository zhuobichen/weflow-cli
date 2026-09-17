#!/usr/bin/env python3
"""Watch a GitHub repo's issues and email new activity.

Meant to run from Windows Task Scheduler rather than from an editor session, so
notifications arrive whether or not anything else is running.

Credentials are read from outside the repository:

    ~/.weflow-issue-watch.json
    {
      "smtp_user": "1473517806@qq.com",
      "smtp_auth": "<QQ 邮箱 SMTP 授权码，不是登录密码>",
      "mail_to":   "1473517806@qq.com",
      "repo":      "zhuobichen/weflow-cli"
    }

Only `smtp_auth` is required; the rest default to the values above. The file is
never written by this script and never belongs in the repo.

First run seeds the state file without sending anything, so a fresh install does
not mail the entire issue history.
"""
import json
import os
import smtplib
import subprocess
import sys
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

CONFIG_PATH = os.path.join(os.path.expanduser('~'), '.weflow-issue-watch.json')
STATE_PATH = os.path.join(os.path.expanduser('~'), '.weflow-issue-watch-state.json')

DEFAULTS = {
    'repo': 'zhuobichen/weflow-cli',
    'smtp_host': 'smtp.qq.com',
    'smtp_port': 465,
    'smtp_user': '1473517806@qq.com',
    'mail_to': '1473517806@qq.com',
}


def load_config():
    config = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding='utf-8') as handle:
            config.update(json.load(handle))
    except (OSError, ValueError):
        pass
    if os.environ.get('WEFLOW_SMTP_AUTH'):
        config['smtp_auth'] = os.environ['WEFLOW_SMTP_AUTH']
    return config


# Task Scheduler starts with a thinner PATH than an interactive shell, so `gh`
# is not always resolvable by name there.
GH_CANDIDATES = ['gh', r'C:\Program Files\GitHub CLI\gh.exe']


def _runnable(candidate):
    if os.sep in candidate:
        return os.path.isfile(candidate)
    from shutil import which
    return which(candidate) is not None


def gh(args):
    """Run `gh` and parse its JSON output, or None when it fails."""
    executable = next((c for c in GH_CANDIDATES if _runnable(c)), GH_CANDIDATES[0])
    try:
        result = subprocess.run(
            [executable, *args], capture_output=True, timeout=60, encoding='utf-8')
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except ValueError:
        return None


def fetch_activity(repo, since_issue=0):
    """(issues, comments) newer than what has already been reported.

    Comments are fetched per issue rather than from the repo-wide endpoint so an
    issue that is merely edited does not look like new activity.
    """
    issues = gh(['api', f'repos/{repo}/issues?state=all&sort=created&direction=desc&per_page=30'])
    if issues is None:
        return None, None

    new_issues = [i for i in issues if not i.get('pull_request') and i['number'] > since_issue]
    comments = []
    # Only the busiest recent issues can have new replies; 30 is plenty.
    for issue in issues:
        if issue.get('pull_request'):
            continue
        rows = gh(['api', f'repos/{repo}/issues/{issue["number"]}/comments?per_page=100'])
        if not rows:
            continue
        for row in rows:
            if row['user']['login'] != 'zhuobichen':
                comments.append({
                    'issue': issue['number'],
                    'title': issue['title'],
                    'id': row['id'],
                    'author': row['user']['login'],
                    'body': row['body'],
                })
    return new_issues, comments


def load_state():
    try:
        with open(STATE_PATH, encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def save_state(state):
    try:
        with open(STATE_PATH, 'w', encoding='utf-8') as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
    except OSError:
        pass


def send_mail(config, subject, body):
    message = MIMEText(body, 'plain', 'utf-8')
    message['Subject'] = Header(subject, 'utf-8')
    message['From'] = formataddr(('WeFlow Issue Watch', config['smtp_user']))
    message['To'] = config['mail_to']
    with smtplib.SMTP_SSL(config['smtp_host'], config['smtp_port'], timeout=30) as server:
        server.login(config['smtp_user'], config['smtp_auth'])
        server.sendmail(config['smtp_user'], [config['mail_to']], message.as_string())


def main():
    config = load_config()
    if not config.get('smtp_auth'):
        print(f'未配置邮件授权码，跳过。请创建 {CONFIG_PATH}，填入 smtp_auth。')
        return 0

    repo = config['repo']
    state = load_state()
    issues = gh(['api', f'repos/{repo}/issues?state=all&sort=created&direction=desc&per_page=30'])
    if issues is None:
        print('无法访问 GitHub（gh 未登录或网络不通），本次跳过。')
        return 0

    # Seed on first run: remember what exists, send nothing.
    if state is None:
        highest = max([i['number'] for i in issues if not i.get('pull_request')] or [0])
        seen_comments = []
        for issue in issues:
            if issue.get('pull_request'):
                continue
            rows = gh(['api', f'repos/{repo}/issues/{issue["number"]}/comments?per_page=100']) or []
            seen_comments.extend(r['id'] for r in rows if r['user']['login'] != 'zhuobichen')
        save_state({'last_issue': highest, 'seen_comments': seen_comments})
        print(f'首次运行，已记录基线（最新 issue #{highest}，{len(seen_comments)} 条历史回复），不发送邮件。')
        return 0

    new_issues, comments = fetch_activity(repo, state.get('last_issue', 0))
    if new_issues is None:
        return 0

    seen = set(state.get('seen_comments', []))
    fresh = [c for c in comments if c['id'] not in seen]

    if not new_issues and not fresh:
        print('无新动态。')
        return 0

    lines = []
    for issue in new_issues:
        lines.append(f'【新 issue】#{issue["number"]} {issue["title"]}')
        lines.append(f'  来自: {issue["user"]["login"]}')
        lines.append(f'  {issue["html_url"]}')
        lines.append('')
    for comment in fresh:
        lines.append(f'【新回复】#{comment["issue"]} {comment["title"]}')
        lines.append(f'  来自: {comment["author"]}')
        lines.append('  ' + comment['body'][:600].replace('\n', '\n  '))
        lines.append(f'  https://github.com/{repo}/issues/{comment["issue"]}')
        lines.append('')

    body = '\n'.join(lines)
    summary = []
    if new_issues:
        summary.append(f'{len(new_issues)} 个新 issue')
    if fresh:
        summary.append(f'{len(fresh)} 条新回复')
    try:
        send_mail(config, f'[WeFlow] {" / ".join(summary)}', body)
    except Exception as error:
        # Leave the state untouched so the next run retries instead of
        # silently swallowing the notification.
        print(f'发送失败，本次不记录状态，下次重试: {type(error).__name__}: {error}')
        return 1

    highest = max([i['number'] for i in issues if not i.get('pull_request')] + [state.get('last_issue', 0)])
    save_state({'last_issue': highest, 'seen_comments': list(seen | {c['id'] for c in fresh})})
    print(f'已发送通知：{" / ".join(summary)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
