#!/usr/bin/env python3
"""Linux 上自动捕获微信的共享口令（passphrase）。

为什么需要它
------------
微信 Linux 4.1.12.26+ 把密钥方案换成了「共享口令 + 每分片 salt 派生」：
内存里不再有"每库一对 key/salt"，所以扫内存那套自动提取在结构上失效
（`docs/VERSION_METHODS.md` 的 M-11）。唯一还能拿到口令的位置，是**登录时
派生的那一刻**——口令作为参数出现在一个具体调用上。

这个脚本就是做这件事的一条龙：
    找进程 → 算断点地址 → 挂调试器 → 等登录 → 读参数 → 校验 → 输出

验证过的版本
------------
* 微信 Linux **4.1.13.26**（Ubuntu 24.04）：断点落在 `0x62a2c73`，
  该指令把 std::string（长度 + 数据指针）交给密钥设置函数。实测登录时命中
  22 次，全部 `len=32` 且内容一致，21 个库里 20 个通过首页 HMAC。

用到它需要用户配合一次「退出登录 → 重新登录」——口令只在登录时派生一次。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

#: 各版本里"把口令交给密钥设置函数"的那条 call 的偏移（相对 PIE 基址）。
#: 只在**实测过**的版本里写死；没验证过的版本宁可报错，也不要瞎断。
KNOWN_CALL_SITES = {
    '4.1.13.26': 0x62A2C73,
}

#: gdb 脚本：命中时把 (rsi, rdx) 与 rsi 指向的前 64 字节写进日志，然后继续。
GDB_TEMPLATE = """set pagination off
set confirm off
handle SIGPIPE nostop noprint pass
handle SIGSEGV nostop noprint pass
break *{addr}
commands
silent
python
import gdb
inf = gdb.selected_inferior()
try:
    rsi = int(gdb.parse_and_eval('$rsi'))
    rdx = int(gdb.parse_and_eval('$rdx'))
    data = b''
    if rsi and 0 < rdx <= 64:
        data = bytes(inf.read_memory(rsi, rdx))
    open('{log}', 'a').write('rsi=0x%x len=%d data=%s\\n' % (rsi, rdx, data.hex()))
except Exception as exc:
    open('{log}', 'a').write('ERR %s\\n' % exc)
end
continue
end
continue
"""


def find_wechat_pid() -> int | None:
    """按 comm / cmdline 找微信主进程。"""
    for entry in os.listdir('/proc'):
        if not entry.isdigit():
            continue
        try:
            comm = Path(f'/proc/{entry}/comm').read_text(encoding='utf-8', errors='replace').strip().lower()
        except OSError:
            continue
        if comm in ('wechat', 'weixin'):
            return int(entry)
        # AUR / Flatpak 包装下 comm 可能不是 wechat，再看 cmdline 首个参数
        try:
            argv0 = Path(f'/proc/{entry}/cmdline').read_bytes().split(b'\0')[0].decode(
                'utf-8', 'replace')
        except OSError:
            continue
        base = os.path.basename(argv0).lower()
        if base in ('wechat', 'weixin'):
            return int(entry)
    return None


def wechat_version(home: str | None = None) -> str | None:
    """从客户端自己的记录读版本（Linux 无 WMI/模块表）。"""
    root = Path(home or os.path.expanduser('~')) / '.xwechat' / 'uh' / 'uh.dat'
    try:
        raw = root.read_bytes().decode('latin1')
    except OSError:
        return None
    m = re.search(r'(\d+\.\d+\.\d+\.\d+)', raw)
    return m.group(1) if m else None


def pie_base(pid: int) -> int | None:
    """可执行文件第一个映射（offset=0 的 r--p）即 PIE 基址。"""
    try:
        maps = Path(f'/proc/{pid}/maps').read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None
    for line in maps.splitlines():
        parts = line.split()
        if len(parts) >= 6 and parts[2] == '00000000' and parts[5].endswith('/wechat'):
            return int(parts[0].split('-')[0], 16)
    return None


def validate(passphrase_hex: str, db_dir: Path) -> tuple[int, int, str]:
    """用真实库验证：返回 (通过数, 总数, 首个通过的库名)。"""
    import hashlib
    import hmac
    import struct

    ok = total = 0
    first = ''
    for db in sorted(db_dir.rglob('*.db')):
        try:
            with open(db, 'rb') as fh:
                page = fh.read(4096)
        except OSError:
            continue
        if len(page) < 4096:
            continue
        total += 1
        salt = page[:16]
        key = hashlib.pbkdf2_hmac('sha512', bytes.fromhex(passphrase_hex), salt, 256000, 32)
        mac_key = hashlib.pbkdf2_hmac('sha512', key, bytes(b ^ 0x3a for b in salt), 2, 32)
        body_end = 4096 - 80 + 16
        mac = hmac.new(mac_key, page[16:body_end], hashlib.sha512)
        mac.update(struct.pack('<I', 1))
        if hmac.compare_digest(mac.digest(), page[body_end:body_end + 64]):
            ok += 1
            if not first:
                first = db.name
    return ok, total, first


def capture(timeout: int, log_path: Path) -> str:
    """挂调试器，等一次登录，返回捕获到的口令（hex）。"""
    pid = find_wechat_pid()
    if pid is None:
        raise SystemExit('没有找到运行中的微信进程')
    version = wechat_version()
    if version not in KNOWN_CALL_SITES:
        raise SystemExit(
            f'微信 {version or "版本未知"} 还没有验证过断点偏移。\n'
            f'  已支持的版本: {", ".join(sorted(KNOWN_CALL_SITES))}\n'
            '  可以把版本号和下面这条命令的结果贴到 issue 里，我们补上：\n'
            '    strings -t x /opt/wechat/wechat | grep "com.Tencent.WCDB.Config.Cipher"')
    base = pie_base(pid)
    if base is None:
        raise SystemExit('读不到微信的可执行映射（权限不足？可用 sudo 再试）')

    addr = base + KNOWN_CALL_SITES[version]
    script = GDB_TEMPLATE.format(addr=hex(addr), log=str(log_path))
    script_path = Path(tempfile.mkdtemp(prefix='weflow-capture-')) / 'capture.gdb'
    script_path.write_text(script, encoding='utf-8')

    print(f'  微信 {version}  pid={pid}  断点={hex(addr)}', flush=True)
    print(f'  请在微信里**退出登录 → 重新登录**（口令只在登录时派生一次）', flush=True)
    print(f'  等待最多 {timeout} 秒…', flush=True)

    proc = subprocess.Popen(['gdb', '-p', str(pid), '-x', str(script_path), '-batch'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if log_path.exists() and log_path.stat().st_size > 0:
            break
        if proc.poll() is not None:
            break
        time.sleep(0.5)
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()

    if not log_path.exists():
        raise SystemExit('调试器没有记录到任何命中（登录可能没触发）')

    counts: dict[str, int] = {}
    for line in log_path.read_text(encoding='utf-8', errors='replace').splitlines():
        m = re.match(r'rsi=0x[0-9a-f]+ len=(\d+) data=([0-9a-f]*)', line)
        if not m:
            continue
        length, data = int(m.group(1)), m.group(2)
        if length == 32 and len(data) == 64:
            counts[data] = counts.get(data, 0) + 1
    if not counts:
        raise SystemExit('命中了，但没有长度 32 的候选（可能断点位置变了）')
    # 取出现次数最多的那个：登录会为每个库各取一次，同一个口令会重复出现
    return max(counts, key=counts.get)


def main() -> int:
    ap = argparse.ArgumentParser(description='捕获微信 Linux 的共享口令（passphrase）')
    ap.add_argument('--timeout', type=int, default=300, help='等待登录的秒数（默认 300）')
    ap.add_argument('--db-dir', default='', help='库目录；缺省自动找 ~/xwechat_files/*/db_storage')
    ap.add_argument('--json', action='store_true', help='输出机器可读结果')
    ap.add_argument('--write-config', action='store_true',
                    help='验证通过后写进配置（favPassphrase）并派生出各库密钥')
    args = ap.parse_args()

    db_dir = Path(args.db_dir) if args.db_dir else None
    if db_dir is None:
        candidates = sorted(Path.home().glob('xwechat_files/*/db_storage'))
        if not candidates:
            raise SystemExit('找不到 ~/xwechat_files/*/db_storage，请用 --db-dir 指定')
        db_dir = candidates[0]

    log_path = Path(tempfile.mkdtemp(prefix='weflow-capture-')) / 'hits.log'
    passphrase = capture(args.timeout, log_path)
    ok, total, first = validate(passphrase, db_dir)

    result = {'success': ok > 0, 'passphrase': passphrase, 'matched': ok, 'total': total,
              'firstDb': first}
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f'  候选口令: {passphrase}')
        print(f'  校验结果: {ok}/{total} 个库通过首页 HMAC' + (f'（首个: {first}）' if first else ''))

    if ok == 0:
        raise SystemExit('捕获到候选但一个库都验不过——口令可能不完整，或断点位置已变')

    if args.write_config:
        env = dict(os.environ)
        subprocess.run(['weflow-cli', 'fav', 'set-key', '--passphrase', passphrase],
                       check=False, env=env)
        print('  已写入配置；接着跑 `weflow-cli init` 派生各库密钥。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
