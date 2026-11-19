#!/usr/bin/env python3
"""macOS 上捕获微信的共享口令（passphrase）。

为什么不扫内存、也不找偏移
--------------------------
macOS 微信 4.x 与 Linux 4.1.12.26+ 是同一种机制：内存里没有明文 `enc_key`，
只有 PBKDF2 派生之后的结果。公开实践（见下）试过 hex 扫内存、salt 匹配、
HMAC 暴力，全部失败——因为它们找的是"密钥本身"，而密钥是算出来的。

我们的做法是断在**派生函数**上：

    int CCKeyDerivationPBKDF(CCPBKDFAlgorithm algorithm,
                             const char *password, size_t passwordLen, ...)

`CCKeyDerivationPBKDF` 是 CommonCrypto 的**公开符号**，lldb 按名字下断点即可，
不需要反汇编找偏移——这比 Linux 那边（要按版本登记 call site）更稳。

取到候选后，用**库首页的 HMAC** 逐个库校验，通过才写配置。判据与 Linux 完全一致。

实现说明（2026-11 重写）
------------------------
旧版把断点回调写成 `import gdb`——lldb 的 Python 脚本环境里没有 gdb 模块，
回调必然抛异常，lldb 随即退出；主循环又把"子进程退出"当成失败提前 break，
于是用户设置的 timeout 形同虚设，最后那句"被 SIP 挡住"的提示还会把真正原因
（脚本自身崩溃）误导成系统权限问题。

本版改为**纯 lldb 原生断点命令**，零 Python API 依赖：

    breakpoint set -n CCKeyDerivationPBKDF
    breakpoint command add -o 'register read x1 x2' \
        -o 'memory read --size 1 --format x --count 64 $x1' -o 'process continue'

另修两个真机踩过的坑：
1. "退出登录 → 重新登录"后微信 PID 会变——本版持续扫描新 PID 并补挂；
2. 口令可能在 WeChatAppEx 辅助进程里派生——两类进程都挂。

真机验证：macOS 27.2 / Apple M4 / 微信 4.1.15，10/10 库通过首页 HMAC。

前提
----
* Xcode Command Line Tools（提供 lldb）：`xcode-select --install`
* 捕获阶段需要能 attach 到微信进程。macOS 微信带 Hardened Runtime，
  **仅开"开发者模式"不够**（task_for_pid 仍被内核拒绝），需要在恢复模式
  临时 `csrutil disable`，捕获后立即 `csrutil enable` 恢复。
  本脚本不替你改系统安全设置，只会在被拒时明确说出原因。

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

#: 每种架构的 (password 指针寄存器, passwordLen 寄存器)。
#: arm64 走 x1/x2，x86_64 走 rsi/rdx（System V 第二、三参数）。
REGISTERS = {
    'arm64': ('x1', 'x2'),
    'aarch64': ('x1', 'x2'),
    'x86_64': ('rsi', 'rdx'),
}

#: 命中口令所需的 32 字节（256 bit）。少于 2 次重复不提前收敛，防噪声。
PASSWORD_BYTES = 32
MIN_REPEATS = 2


def build_lldb_script(ptr_reg: str, len_reg: str) -> str:
    """生成断点脚本：命中时打印 (ptr, len) 与前 64 字节，然后自动放行。

    `process continue` 是回调的一部分——没有它，微信会停在断点上冻死，
    用户看到的"登录转圈掉帧"就是这个。
    """
    return (
        'breakpoint set -n CCKeyDerivationPBKDF\n'
        "breakpoint command add -o 'register read {p} {l}'"
        " -o 'memory read --size 1 --format x --count 64 ${p}'"
        " -o 'process continue'\n"
        'process continue\n'
    ).format(p=ptr_reg, l=len_reg)


def find_wechat_pids() -> list[int]:
    """主进程 WeChat 与辅助进程 WeChatAppEx 都要；口令在哪边派生不保证。"""
    pids: list[int] = []
    for name in ('WeChat', 'WeChatAppEx'):
        try:
            out = subprocess.run(['pgrep', '-x', name], capture_output=True,
                                 text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        pids.extend(int(x) for x in out.split() if x.isdigit())
    return pids


def wechat_version() -> str | None:
    """读 app bundle 的版本号（Info.plist），不依赖运行中的进程。"""
    for base in ('/Applications/WeChat.app', str(pathlib.Path.home() / 'Applications' / 'WeChat.app')):
        plist = pathlib.Path(base) / 'Contents' / 'Info.plist'
        if not plist.is_file():
            continue
        try:
            out = subprocess.run(['plutil', '-extract', 'CFBundleShortVersionString',
                                  'raw', str(plist)], capture_output=True, text=True, timeout=10).stdout.strip()
            return out or None
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def _attach(pid: int, script_path: pathlib.Path, log_path: pathlib.Path) -> subprocess.Popen:
    fh = open(log_path, 'w', encoding='utf-8', errors='replace')
    try:
        proc = subprocess.Popen(['lldb', '-p', str(pid), '-b', '-s', str(script_path)],
                                stdout=fh, stderr=subprocess.STDOUT, text=True)
    finally:
        # 子进程已经 dup 出自己的 fd，父进程这份必须关掉。
        # 不关的话每 attach 一次泄漏一个 fd——SIP 没关时正是 attach 反复重试的场景，
        # 几百次就把 ulimit 用尽，最后报出来的是 OSError traceback 而不是"需要关 SIP"。
        fh.close()
    return proc


def _attach_failed(log_path: pathlib.Path) -> bool:
    """lldb 日志里出现 attach 失败（SIP / Hardened Runtime 挡住）。"""
    try:
        return 'attach failed' in log_path.read_text(encoding='utf-8', errors='replace').lower()
    except OSError:
        return False


def plan_attachments(pids: list[int], procs: dict[int, subprocess.Popen],
                     failed: set[int]) -> list[int]:
    """这一轮该 attach 哪些 pid。

    三条规则，每条都对应一个真机踩过的形态：

    * 没挂过的 pid → 挂（重登录会换 pid，WeChatAppEx 是另一个 pid）；
    * 挂过、但**进程已经退出**且没被判失败 → 再挂一次（lldb 自己崩了不代表不能再来一次）；
    * 已经 attach 失败过的 pid → **永远不再碰**。否则 SIP 没关时每 0.5 秒重开一个 lldb，
      5 分钟就是六百次，用户等满超时才看到"需要 csrutil disable"。
    """
    todo: list[int] = []
    for pid in pids:
        if pid in failed:
            continue
        proc = procs.get(pid)
        if proc is None or proc.poll() is not None:
            todo.append(pid)
    return todo


_HEX_LINE = re.compile(r'^0x[0-9a-f]+:\s+((?:0x[0-9a-f]{2}\s+)+)')


def collect_candidates(logdir: pathlib.Path) -> list[tuple[str, int]]:
    """从各 lldb 会话日志里提取 32 字节口令候选，按出现次数排序。"""
    counts: dict[str, int] = {}
    for f in sorted(logdir.glob('lldb_*.log')):
        lines = f.read_text(encoding='utf-8', errors='replace').splitlines()
        i = 0
        while i < len(lines):
            if 'memory read' in lines[i]:
                body = ''
                j = i + 1
                while j < len(lines):
                    m = _HEX_LINE.match(lines[j])
                    if not m:
                        break
                    body += m.group(1) + ' '
                    j += 1
                bs = re.findall(r'0x([0-9a-f]{2})', body)
                if len(bs) >= PASSWORD_BYTES:
                    h = ''.join(bs[:PASSWORD_BYTES])
                    counts[h] = counts.get(h, 0) + 1
                i = j
            else:
                i += 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


_REG_LINE = re.compile(r'^\s*([A-Za-z0-9]+)\s*=\s*0x([0-9a-fA-F]+)\s*$')


def collect_lengths(logdir: pathlib.Path, len_reg: str) -> set[int]:
    """日志里出现过的 `passwordLen` 取值。

    为什么需要它：候选是按"读 64 字节取前 32"得到的，**没有校验长度**。
    ABI 变了（Rosetta、寄存器换位、微信改实现）时产出的就是垃圾候选，
    最后只会报"没有命中"——而真相是"命中了，但长度不是 32"。
    这条诊断在新版里被弄丢了，这里补回来；取不到就返回空集，不影响主流程。
    """
    seen: set[int] = set()
    for f in sorted(logdir.glob('lldb_*.log')):
        try:
            text = f.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        for line in text.splitlines():
            m = _REG_LINE.match(line)
            if not m or m.group(1) != len_reg:
                continue
            try:
                seen.add(int(m.group(2), 16))
            except ValueError:
                continue
    return seen


def capture(timeout: int, logdir: pathlib.Path) -> str:
    pids = find_wechat_pids()
    if not pids:
        raise SystemExit('没有找到运行中的微信进程（先启动并登录微信再试）')
    version = wechat_version() or '未知'
    print(f'  微信 {version}  pids={pids}', flush=True)
    print('  请在微信里**退出登录 → 重新登录**（口令只在登录时派生一次）', flush=True)
    print(f'  等待最多 {timeout} 秒…', flush=True)

    arch = subprocess.run(['uname', '-m'], capture_output=True, text=True).stdout.strip()
    ptr_reg, len_reg = REGISTERS.get(arch, REGISTERS['arm64'])
    script_path = logdir / 'capture.lldb'
    script_path.write_text(build_lldb_script(ptr_reg, len_reg), encoding='utf-8')

    procs: dict[int, subprocess.Popen] = {}
    failed: set[int] = set()
    deadline = time.time() + timeout
    while time.time() < deadline:
        for pid in plan_attachments(find_wechat_pids(), procs, failed):
            log_path = logdir / f'lldb_{pid}.log'
            procs[pid] = _attach(pid, script_path, log_path)
            # attach 被拒就到此为止：继续等下去只会让用户白白耗满 timeout。
            # 先给 lldb 一拍时间把错误写进日志，再判。
            time.sleep(0.3)
            if _attach_failed(log_path):
                failed.add(pid)
                procs[pid].terminate()
                for p in procs.values():        # 其余会话也收掉，别留着占着进程
                    if p.poll() is None:
                        p.terminate()
                raise SystemExit(
                    '调试器无法 attach：微信带 Hardened Runtime，仅开"开发者模式"不够，'
                    '需要在恢复模式临时 csrutil disable（用完恢复）。本工具不替你改系统安全设置。')
        cands = collect_candidates(logdir)
        if cands and cands[0][1] >= MIN_REPEATS:
            break
        time.sleep(0.5)

    for p in procs.values():                    # 收网：别让微信停在断点上
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()

    cands = collect_candidates(logdir)
    if cands:
        return cands[0][0]

    # 命中了断点、但长度对不上：说清楚是"长度不对"，别让用户去猜是不是没操作对。
    lengths = collect_lengths(logdir, len_reg)
    if lengths and PASSWORD_BYTES not in lengths:
        raise SystemExit(
            f'命中了 PBKDF2，但 {len_reg}（passwordLen）是 {sorted(lengths)}，不是 {PASSWORD_BYTES}——'
            '口令长度不符合预期（架构/ABI 可能变了，或打到的是别的调用）。'
            '这时不要用产出结果写配置。')

    raise SystemExit(
        f'调试器没有记录到任何命中——确认这 {timeout} 秒内在微信里完成了"退出登录 → 重新登录"。')


def db_candidates() -> list[pathlib.Path]:
    """macOS 微信的库目录。**四种布局都看**，不要删。

    沙箱（App Store 版）把数据放在 `~/Library/Containers/<bundle>/Data/...` 下，
    而 `Catalog`/`Application Support` 两种内部布局在不同版本里都出现过；
    bundle id 也有 `com.tencent.xinWeChat` 与 `com.tencent.WeChat` 两种。
    少列一条，那类用户就是"找不到库目录"——而这条本来是支持的，删掉属于倒退。
    """
    home = pathlib.Path.home()
    roots = [
        home / 'Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files',
        home / 'Library/Containers/com.tencent.xinWeChat/Data/Library/Application Support/com.tencent.xinWeChat',
        home / 'Library/Containers/com.tencent.WeChat/Data/Documents/xwechat_files',
        home / 'Library/Application Support/com.tencent.xinWeChat',
    ]
    found: list[pathlib.Path] = []
    for r in roots:
        if r.is_dir():
            found.append(r)
            for sub in r.glob('*/db_storage'):
                found.append(sub)
    return found


def validate(passphrase_hex: str) -> tuple[int, int, str]:
    """复用 Linux 那边同一套校验：逐库首页 HMAC。"""
    from linux_capture import validate as linux_validate
    dirs = db_candidates()
    if not dirs:
        raise SystemExit('找不到微信库目录（~/Library/Containers/com.tencent.xinWeChat/...）')
    total_ok = total = 0
    first = ''
    seen: set[str] = set()
    for d in dirs:
        key = str(d)
        if key in seen:
            continue
        seen.add(key)
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

    logdir = pathlib.Path(tempfile.mkdtemp(prefix='weflow-mac-capture-'))
    passphrase = capture(args.timeout, logdir)
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
