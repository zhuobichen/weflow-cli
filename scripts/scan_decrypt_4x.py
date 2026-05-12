#!/usr/bin/env python3
"""
WeChat 4.x Database Key Scanner & Decryptor
Scans Weixin.exe process memory for WCDB key+salt pairs and tests decryption.
"""
import ctypes
from ctypes import wintypes, c_void_p, c_size_t, create_string_buffer, byref, sizeof
import re
import hashlib
import hmac as hmac_lib
import sys
import os
from pathlib import Path

try:
    from Crypto.Cipher import AES
except ImportError:
    print("Install pycryptodome: pip install pycryptodome")
    sys.exit(1)

# ========== Constants ==========
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100
PAGE_SIZE = 4096
EXPECTED_MAGIC = b'SQLite format 3\x00'

# ========== Memory Structures ==========
class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('BaseAddress', ctypes.c_void_p),
        ('AllocationBase', ctypes.c_void_p),
        ('AllocationProtect', wintypes.DWORD),
        ('PartitionId', wintypes.WORD),
        ('RegionSize', ctypes.c_size_t),
        ('State', wintypes.DWORD),
        ('Protect', wintypes.DWORD),
        ('Type', wintypes.DWORD),
    ]

kernel32 = ctypes.windll.kernel32
ReadProcessMemory = kernel32.ReadProcessMemory
ReadProcessMemory.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.LPVOID, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
ReadProcessMemory.restype = wintypes.BOOL

VirtualQueryEx = kernel32.VirtualQueryEx
VirtualQueryEx.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, ctypes.c_void_p, ctypes.c_size_t]
VirtualQueryEx.restype = ctypes.c_size_t


def find_weixin_pid():
    """Find Weixin.exe process ID."""
    import pymem
    import pymem.process
    for proc in pymem.process.list_processes():
        try:
            name = proc.szExeFile
            if isinstance(name, bytes):
                name = name.decode('utf-8', errors='ignore')
            if name.lower() == 'weixin.exe':
                return proc.th32ProcessID
        except:
            pass
    return None


def scan_memory_keys(pid):
    """Scan process memory for x'<64hex_key><32hex_salt>' patterns."""
    hProcess = kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not hProcess:
        print(f"Failed to open PID {pid}")
        return []

    pattern = re.compile(rb"x'([0-9a-fA-F]{64})([0-9a-fA-F]{32})'")
    keys_found = []

    address = 0x10000
    region_count = 0

    print(f"Scanning process {pid} memory...")

    while address < 0x7FFFFFFFFFFF:
        mbi = MEMORY_BASIC_INFORMATION()
        result = VirtualQueryEx(hProcess, ctypes.c_void_p(address), ctypes.byref(mbi), sizeof(mbi))
        if result == 0:
            break

        region_count += 1
        if region_count % 1000 == 0:
            print(f"  ... {region_count} regions scanned, {len(keys_found)} keys found")

        region_addr = mbi.BaseAddress or 0
        region_size = mbi.RegionSize or 0

        if (mbi.State == MEM_COMMIT and
                region_size > 256 and region_size < 200 * 1024 * 1024 and
                mbi.Protect not in (0, PAGE_NOACCESS, PAGE_GUARD)):

            pos = region_addr
            end = region_addr + region_size
            while pos < end:
                chunk_size = min(65536, end - pos)
                buf = create_string_buffer(chunk_size)
                bytesRead = c_size_t(0)
                ok = ReadProcessMemory(hProcess, ctypes.c_void_p(pos), buf, chunk_size, byref(bytesRead))
                if ok and bytesRead.value > 0:
                    data = buf.raw[:bytesRead.value]
                    for m in pattern.finditer(data):
                        abs_addr = pos + m.start()
                        key_hex = m.group(1).decode()
                        salt_hex = m.group(2).decode()
                        keys_found.append((key_hex, salt_hex, abs_addr))
                pos += chunk_size

        address = region_addr + region_size

    kernel32.CloseHandle(hProcess)

    # Deduplicate
    seen = set()
    unique_keys = []
    for k, s, a in keys_found:
        pair = (k, s)
        if pair not in seen:
            seen.add(pair)
            unique_keys.append((k, s, a))

    print(f"Found {len(unique_keys)} unique key+salt pairs in {region_count} regions")
    return unique_keys


def try_decrypt_page0(enc_data, key_bytes):
    """Try to decrypt page 0 with given key using various parameters."""

    # Parameter combinations to try
    kdf_configs = [
        # (use_pbkdf2, sha_name, iterations, salt_source)
        (False, None, 0, 'none'),       # Raw key, no KDF
        (True, 'sha512', 256000, 'file'),  # SQLCipher 4 standard
        (True, 'sha1', 64000, 'file'),     # SQLCipher 3 standard
        (True, 'sha256', 256000, 'file'),
        (True, 'sha512', 64000, 'file'),
        (True, 'sha1', 256000, 'file'),
        (True, 'sha512', 4000, 'file'),
    ]

    file_salt = enc_data[:16]  # First 16 bytes of file (zero for MSG0.db)

    for use_pbkdf2, sha, iters, salt_src in kdf_configs:
        if use_pbkdf2:
            salt = file_salt if salt_src == 'file' else bytes(16)
            try:
                dk = hashlib.pbkdf2_hmac(sha, key_bytes, salt, iters, dklen=32)
            except:
                continue
        else:
            dk = key_bytes

        for rsv in [48, 80, 64, 32, 96]:
            if rsv >= PAGE_SIZE:
                continue

            iv_offset = PAGE_SIZE - rsv
            iv = enc_data[iv_offset:iv_offset + 16]
            if len(iv) < 16:
                continue

            enc_len = PAGE_SIZE - 16 - rsv  # For page 0: skip 16-byte salt
            encrypted = enc_data[16:16 + enc_len]

            if len(encrypted) % 16 != 0:
                continue

            try:
                cipher = AES.new(dk, AES.MODE_CBC, iv)
                decrypted = cipher.decrypt(encrypted)
                if decrypted[:16] == EXPECTED_MAGIC:
                    return {
                        'key': key_bytes.hex(),
                        'pbkdf2': use_pbkdf2,
                        'sha': sha,
                        'iterations': iters,
                        'reserved': rsv,
                        'iv_offset': iv_offset,
                    }
            except:
                pass

    return None


def decrypt_full(enc_path, out_path, key_bytes, params):
    """Decrypt entire database using discovered parameters."""
    with open(enc_path, 'rb') as f:
        enc = f.read()

    rsv = params['reserved']
    use_pbkdf2 = params['pbkdf2']
    sha = params['sha']
    iters = params['iterations']

    file_salt = enc[:16]

    if use_pbkdf2:
        dk = hashlib.pbkdf2_hmac(sha, key_bytes, file_salt, iters, dklen=32)
    else:
        dk = key_bytes

    result = bytearray()
    # Write SQLite header
    result.extend(b'SQLite format 3\x00')

    num_pages = len(enc) // PAGE_SIZE

    for pg_num in range(num_pages):
        page_start = pg_num * PAGE_SIZE
        page_data = enc[page_start:page_start + PAGE_SIZE]

        if pg_num == 0:
            # Page 0: salt(16) + encrypted_data + reserved
            iv_offset = PAGE_SIZE - rsv
            iv = page_data[iv_offset:iv_offset + 16]
            enc_data = page_data[16:PAGE_SIZE - rsv]
        else:
            # Other pages: encrypted_data + reserved
            iv_offset = PAGE_SIZE - rsv
            iv = page_data[iv_offset:iv_offset + 16]
            enc_data = page_data[:PAGE_SIZE - rsv]

        cipher = AES.new(dk, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(enc_data)
        result.extend(decrypted)

        # Write reserved area
        reserved = page_data[PAGE_SIZE - rsv:PAGE_SIZE]
        result.extend(reserved)

        if pg_num % 1000 == 0 and pg_num > 0:
            print(f"  ... decrypted {pg_num}/{num_pages} pages")

    with open(out_path, 'wb') as f:
        f.write(result)
    print(f"Decrypted database written to: {out_path}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='WeChat 4.x Database Decryptor')
    parser.add_argument('--db', help='Path to encrypted database (e.g., MSG0.db)')
    parser.add_argument('--out', help='Output path for decrypted database')
    parser.add_argument('--scan-only', action='store_true', help='Only scan for keys, no decryption')
    parser.add_argument('--key', help='Specific key hex to try (64 chars)')
    parser.add_argument('--salt', help='Specific salt hex to try with key (32 chars)')
    args = parser.parse_args()

    pid = find_weixin_pid()
    if not pid:
        print("Weixin.exe not running. Start WeChat 4.x first.")
        sys.exit(1)
    print(f"Found Weixin.exe PID: {pid}")

    # Scan memory for keys
    keys = scan_memory_keys(pid)
    print(f"\nFound {len(keys)} key+salt pairs:")
    for key_hex, salt_hex, addr in keys:
        print(f"  {key_hex[:32]}... | {salt_hex} @ {hex(addr)}")

    if args.scan_only:
        return

    # If specific key+salt provided, use it
    if args.key:
        keys.insert(0, (args.key, args.salt or '00' * 16, 0))

    # Database to decrypt
    db_path = args.db
    if not db_path:
        # Try to find MSG0.db
        wx_docs = os.path.expandvars(r'%USERPROFILE%\Documents\WeChat Files')
        if os.path.isdir(wx_docs):
            for wxid_dir in os.listdir(wx_docs):
                msg_path = os.path.join(wx_docs, wxid_dir, 'Msg', 'Multi', 'MSG0.db')
                if os.path.exists(msg_path):
                    db_path = msg_path
                    print(f"\nAuto-detected database: {db_path}")
                    break

    if not db_path or not os.path.exists(db_path):
        print("No database file found. Use --db to specify path.")
        sys.exit(1)

    out_path = args.out or db_path.replace('.db', '_decrypted.db')

    with open(db_path, 'rb') as f:
        enc_data = f.read()

    print(f"\nTesting {len(keys)} keys against {db_path}...")
    print(f"File size: {len(enc_data)} bytes, {len(enc_data) // PAGE_SIZE} pages")
    print(f"File salt (first 16 bytes): {enc_data[:16].hex()}")

    for idx, (key_hex, salt_hex, addr) in enumerate(keys):
        key_bytes = bytes.fromhex(key_hex)
        result = try_decrypt_page0(enc_data, key_bytes)
        if result:
            print(f"\n*** SUCCESS with key #{idx + 1} ***")
            print(f"  Key:        {key_hex}")
            print(f"  Memory salt: {salt_hex}")
            print(f"  PBKDF2:     {result['pbkdf2']}")
            if result['pbkdf2']:
                print(f"  Hash:       {result['sha']}")
                print(f"  Iterations: {result['iterations']}")
            print(f"  Reserved:   {result['reserved']} bytes")

            decrypt_full(db_path, out_path, key_bytes, result)
            break
        elif idx % 5 == 0:
            print(f"  ... tested {idx + 1}/{len(keys)} keys")
    else:
        print("\nNo key could decrypt the database.")
        print("Possible reasons:")
        print("  - Database uses a different encryption format")
        print("  - Key is not in the x'<hex>' format in memory")
        print("  - Database was created with a key from a different session")


if __name__ == '__main__':
    main()
