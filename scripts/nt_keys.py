#!/usr/bin/env python3
"""WeChat 4.x NT database key derivation and message-shard discovery.

WeChat 4.1.12.26+ keeps a single master key and derives each database's key as

    PBKDF2-HMAC-SHA512(master_key, <that file's 16-byte salt>, 256000)

so every database and every message shard is reachable from the master key
alone - no per-database key capture needed. Verified against all databases on a
live install: derived keys match the separately captured ntKey/contactKey/snsKey
byte for byte.

The master key is what `weflow-cli dbkey` / `init` capture (config `decryptKey`).
"""
import hashlib
import os
import re

PBKDF2_ITERATIONS = 256000
SALT_BYTES = 16


def file_salt(db_path):
    """SQLCipher stores this database's salt in the first 16 bytes."""
    with open(db_path, 'rb') as fh:
        return fh.read(SALT_BYTES)


def derive_db_key(master_key_hex, db_path):
    """Derive a database's key from the master key. Returns hex."""
    return hashlib.pbkdf2_hmac(
        'sha512', bytes.fromhex(master_key_hex), file_salt(db_path),
        PBKDF2_ITERATIONS, dklen=32,
    ).hex()


def message_db_dir(message_db_path):
    return os.path.dirname(os.path.abspath(message_db_path))


def discover_message_shards(message_db_path, master_key_hex):
    """Every message_N.db beside message_db_path, as (path, key).

    WeChat rotates the message store across shards, so one conversation's
    history can be split over several files. Reading only the configured one
    silently exports a partial conversation.
    """
    db_dir = message_db_dir(message_db_path)
    shards = []
    if not db_dir or not os.path.isdir(db_dir):
        return shards
    for fname in sorted(os.listdir(db_dir)):
        if not re.match(r'^message_\d+\.db$', fname):
            continue
        fpath = os.path.join(db_dir, fname)
        try:
            shards.append((fpath, derive_db_key(master_key_hex, fpath)))
        except (OSError, ValueError):
            pass
    return shards


def session_db_path(message_db_path):
    """session.db lives beside the message dir under db_storage/.

    <account>/db_storage/message/message_0.db -> <account>/db_storage/session/session.db
    """
    db_dir = message_db_dir(message_db_path)
    db_storage = os.path.dirname(db_dir)
    candidate = os.path.join(db_storage, 'session', 'session.db')
    return candidate if os.path.isfile(candidate) else ''


def derive_key_or_none(master_key_hex, db_path):
    if not master_key_hex or not os.path.isfile(db_path):
        return None
    try:
        return derive_db_key(master_key_hex, db_path)
    except (OSError, ValueError):
        return None
