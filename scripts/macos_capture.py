#!/usr/bin/env python3
"""macOS 上捕获微信的共享口令（passphrase）。

为什么不扫内存、也不找偏移
--------------------------
macOS 微信 4.x 与 Linux 4.1.12.26+ 是同一种机制：内存里没有明文 `enc_key`，
只有 PBKDF2 派生之后的结果。公开实践（见下）试过 hex 扫内存、salt 匹配、
HMAC 暴力，全部失败——因为它们找的是"密钥本身"，而密钥是算出来的。

这个脚本断在**派生函数**上：

    int CCKeyDerivationPBKDF(CCPBKDFAlgorithm algorithm,
                             const char *password, size_t passwordLen, ...)

`CCKeyDerivationPBKDF` 是 CommonCrypto 的**公开符号**，lldb 可以直接按名字下断点，
不需要反汇编找偏移——这比 Linux 那边（要按版本登记 call site）更稳。

取到候选后，用**库首页的 HMAC** 逐个库校验，通过才写配置。判据与 Linux 完全一致。

前提
----
* Xcode Command Line Tools（提供 lldb）：`xcode-select --install`
* 捕获阶段需要能 attach 到微信进程。macOS 的 SIP / Hardened Runtime 会挡住普通
  attach；同类工具（chatlog）的做法是**临时关闭 SIP** 并在捕获后恢复。
  本脚本不替你改 SIP，只会在被拒时明确说出原因。

参考
----
* chatlog 的 README：macOS 获取密钥前需临时关闭 SIP
* 一次公开的尝试记录：DYLD hook 被 SIP + Hardened Runtime 挡住，
  hook CCKeyDerivationPBKDF 成功
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

#: lldb 脚本：命中 CCKeyDerivationPBKDF 时读 (password, passwordLen)。
#: arm64 走 x1/x2，x86_64 走 rsi/rdx——同一段脚本用 python 分支处理。
LLDB_TEMPLATE = """set pagination off
set confirm off
handle SIGPIPE nostop noprint pass
handle SIGSEGV nostop noprint pass
breakpoint set -n CCKeyDerivationPBKDF
breakpoint command add
script
import gdb
PYTHON_BLOCK
end
continue
"""

PY_BODY = """import gdb, json, os
arch = gdb.selected_inferior().architecture().name()
def reg(name):
    try:
        return int(gdb.parse_and_eval('$' + name))
    except Exception:
        return 0
if 'arm64' in arch or 'aarch64' in arch:
    ptr, ln = reg('x1'), reg('x2')
else:
    ptr, ln = reg('rsi'), reg('rdx')
data = b''
if ptr and 0 < ln <= 128:
    try:
        data = bytes(gdb.selected_inferior().read_memory(ptr, ln))
    except Exception:
        data = b''
with open(LOG, 'a') as fh:
    fh.write(json.dumps({'arch': arch, 'len': ln, 'hex': data.hex()}) + '\\n')
"""


def find_wechat_pid() -> int | None:
    """找微信主进程（macOS 的可执行名叫 WeChat，也可能是 微信）。"""
    try:
        out = subprocess.run(['ps', '-axo', 'pid=,comm='], capture_output=True,
                             text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        pid, _, comm = line.partition(' ')
        if 'WeChat.app/Contents/MacOS/WeChat' in comm or comm.endswith('/微信'):
            try:
                return int(pid)
            except ValueError:
                continue
    return None


def wechat_version() -> str | None:
    """读 app bundle 的版本号（Info.plist），不依赖运行中的进程。"""
    for base in ('/Applications/WeChat.app', str(pathlib.Path.home() / 'Applications' / 'WeChat.app')):
        plist = pathlib.Path(base) / 'Contents' / 'Info.plist'
        if not plist.is_file():
            continue
        try:
            out = subprocess.run(['/usr/libexec/PlistBuddy', '-c', 'Print :CFBundleShortVersionString',
                                  str(plist)], capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            out = ''
        if out:
            return out
    return None


def db_candidates() -> list[pathlib.Path]:
    """macOS 微信的库目录（两种布局都看）。"""
    home = pathlib.Path.home()
    roots = [
        home / 'Library' / 'Containers' / 'com.tencent.xinWeChat' / 'Data' / 'Documents' / 'xwechat_files',
        home / 'Library' / 'Containers' / 'com.tencent.xinWeChat' / 'Data' / 'Library'
        / 'Application Support' / 'com.tencent.xinWeChat',
        home / 'Library' / 'Containers' / 'com.tencent.WeChat' / 'Data' / 'Documents' / 'xwechat_files',
    ]
    found = []
    for root in roots:
        if not root.is_dir():
            continue
        found.append(root)
        for child in sorted(root.glob('*/db_storage')):
            found.append(child)
    return found


def capture(timeout: int, log_path: pathlib.Path) -> str:
    pid = find_wechat_pid()
    if pid is None:
        raise SystemExit('没有找到运行中的微信进程（先启动并登录微信再试）')
    version = wechat_version() or '未知'
    print(f'  微信 {version}  pid={pid}', flush=True)
    print('  请在微信里**退出登录 → 重新登录**（口令只在登录时派生一次）', flush=True)
    print(f'  等待最多 {timeout} 秒…', flush=True)

    script = LLDB_TEMPLATE.replace(
        'script\nimport gdb\nPYTHON_BLOCK',
        'script\n' + PY_BODY.replace('LOG', repr(str(log_path))),
    )
    script_path = pathlib.Path(tempfile.mkdtemp(prefix='weflow-mac-capture-')) / 'capture.lldb'
    script_path.write_text(script, encoding='utf-8')

    proc = subprocess.Popen(['lldb', '-p', str(pid), '-s', str(script_path), '-b'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            text=True, encoding='utf-8', errors='replace')
    deadline = time.time() + timeout
    while time.time() < deadline:
        if log_path.exists() and log_path.stat().st_size > 0:
            break
        if proc.poll() is not None:
            break
        time.sleep(0.5)

    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    if not log_path.exists():
        err = (proc.stderr.read() if proc.stderr else '') or ''
        hint = ''
        if 'not permitted' in err.lower() or 'attach' in err.lower() or 'ptrace' in err.lower():
            hint = ('\n  看起来被 SIP / Hardened Runtime 挡住了。同类工具的做法是'
                    '\n  临时关闭 SIP 再捕获（用完恢复），本工具不替你改系统安全设置。')
        raise SystemExit('调试器没有记录到任何命中。' + hint)

    counts: dict[str, int] = {}
    for line in log_path.read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if int(rec.get('len') or 0) == 32 and len(rec.get('hex') or '') == 64:
            counts[rec['hex']] = counts.get(rec['hex'], 0) + 1
    if not counts:
        raise SystemExit('命中了 PBKDF2，但没有长度 32 的候选（口令长度不符合预期）')
    return max(counts, key=counts.get)


def validate(passphrase_hex: str) -> tuple[int, int, str]:
    """复用 Linux 那边同一套校验：逐库首页 HMAC。"""
    from linux_capture import validate as linux_validate
    dirs = db_candidates()
    if not dirs:
        raise SystemExit('找不到微信库目录（~/Library/Containers/com.tencent.xinWeChat/...）')
    total_ok = total = 0
    first = ''
    for d in dirs:
        ok, n, name = linux_validate(passphrase_hex, d)
        total_ok += ok
        total += n
        if ok and not first:
            first = name
    return total_ok, total, first


def main() -> int:
    ap = argparse.ArgumentParser(description='捕获微信 macOS 的共享口令（passphrase）')
    ap.add_argument('--timeout', type=int, default=300, help='等待登录的秒数（默认 300）')
    ap.add_argument('--json', action='store_true', help='输出机器可读结果')
    ap.add_argument('--write-config', action='store_true', help='验证通过后写进配置')
    args = ap.parse_args()

    if sys.platform != 'darwin':
        raise SystemExit('这个脚本只用于 macOS；Linux 用 linux_capture.py')

    log_path = pathlib.Path(tempfile.mkdtemp(prefix='weflow-mac-capture-')) / 'hits.log'
    passphrase = capture(args.timeout, log_path)
    ok, total, first = validate(passphrase)

    result = {'success': ok > 0, 'passphrase': passphrase, 'matched': ok, 'total': total,
              'firstDb': first}
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f'  候选口令: {passphrase}')
        print(f'  校验结果: {ok}/{total} 个库通过首页 HMAC' + (f'（首个: {first}）' if first else ''))

    if ok == 0:
        raise SystemExit('捕获到候选但一个库都验不过——口令可能不完整')

    if args.write_config:
        subprocess.run(['weflow-cli', 'fav', 'set-key', '--passphrase', passphrase], check=False)
        print('  已写入配置；接着跑 `weflow-cli init` 派生各库密钥。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
