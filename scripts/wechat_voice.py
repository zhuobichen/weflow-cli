#!/usr/bin/env python3
"""Decode WeChat voice messages and transcribe them locally.

WeChat 4.x keeps voice payloads in `media_*.db` under `VoiceInfo.voice_data`,
encoded as SILK v3 (a one-byte length prefix, then `#!SILK_V3`). Browsers
cannot play SILK and ffmpeg has no SILK decoder, so a chat export has nothing
to show for a voice message; the audio is decoded with `pilk` and transcribed
with a local `faster-whisper` model instead.

Recognition is expensive next to everything else an export does, so it is
deliberately kept out of the export path: this module fills a
content-addressed cache, and the exporter only reads it. That makes the pass
resumable, re-runnable, and free to repeat once it has been paid for once.

The default model is a Cantonese fine-tune, chosen because stock Whisper
answers Cantonese with fluent-looking Mandarin nonsense - worse than nothing,
since it reads as a real sentence while saying something that was never said.
See OPERATIONS.md for how to fetch it.

Nothing here touches the network: the model is loaded from the local Hugging
Face cache and no audio or text leaves the machine.
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Appended to the transcript cache for a clip the recogniser returned nothing
# for, so a re-run does not pay for it twice.
EMPTY_MARKER = '\x00empty'

DEFAULT_MODEL = 'small'

# A Cantonese-tuned model, fetched separately (see the README note in
# OPERATIONS.md). Worth preferring automatically when present: stock Whisper
# answers Cantonese speech with fluent-looking Mandarin nonsense - text that
# reads as a real sentence but says nothing that was said. A Cantonese
# fine-tune transcribes the same audio into actual Cantonese.
CANTONESE_MODEL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'models', 'whisper-small-cantonese', 'cts')

# Detected rather than forced. Forcing `yue` on the Cantonese model returned
# empty output for every clip tried; letting it detect produced Cantonese.
DEFAULT_LANGUAGE = None


def voice_shards(db_path):
    """`media_*.db` files that sit beside the configured message database."""
    db = os.path.abspath(db_path)
    folder = os.path.dirname(db)
    if not os.path.isdir(folder):
        return []
    return sorted(
        os.path.join(folder, name)
        for name in os.listdir(folder)
        if name.lower().startswith('media_') and name.lower().endswith('.db')
    )


def _open(path, key_hex, salt_hex, passphrase=''):
    """Connect to a shard, deriving its key the same way message shards do."""
    from export_chat_html import derive_database_key, connect
    shard_key, shard_salt = derive_database_key(path, key_hex, salt_hex, passphrase)
    conn, cursor = connect(path, shard_key, shard_salt)
    return conn, cursor


def load_voice_map(db_path, key_hex, salt_hex, passphrase='', talker=''):
    """{(talker, local_id): voice bytes} for one conversation.

    Scoped to a talker because a whole account is only ever needed one
    conversation at a time, and the payloads are held in memory.
    """
    voices = {}
    if not talker:
        return voices
    for shard in voice_shards(db_path):
        conn = None
        try:
            conn, cursor = _open(shard, key_hex, salt_hex, passphrase)
            cursor.execute('SELECT rowid, user_name FROM Name2Id')
            names = {row[0]: row[1] for row in cursor.fetchall()}
            cursor.execute(
                'SELECT chat_name_id, local_id, voice_data FROM VoiceInfo '
                'WHERE voice_data IS NOT NULL'
            )
            for chat_id, local_id, blob in cursor.fetchall():
                if names.get(chat_id) != talker or not blob:
                    continue
                voices[local_id] = bytes(blob)
        except Exception:
            continue
        finally:
            if conn is not None:
                conn.close()
    return voices


def voice_key(blob):
    """Cache key for a voice payload: its content digest."""
    return hashlib.md5(blob).hexdigest()


def decode_silk(blob, wav_path):
    """Write `blob` to `wav_path` as 16kHz mono WAV. True on success.

    pilk takes paths rather than bytes, and the 16kHz rate is what Whisper
    wants, so no resampling step is needed afterwards.
    """
    try:
        import pilk
    except ImportError:
        return False
    silk_path = wav_path + '.silk'
    try:
        with open(silk_path, 'wb') as handle:
            handle.write(blob)
        pilk.silk_to_wav(silk_path, wav_path, rate=16000)
        return os.path.isfile(wav_path) and os.path.getsize(wav_path) > 44
    except Exception:
        return False
    finally:
        try:
            os.remove(silk_path)
        except OSError:
            pass


def resolve_model(model_name=''):
    """Model to load: the caller's choice, else a local Cantonese fine-tune,
    else the stock model name."""
    if model_name:
        return model_name
    if os.path.isdir(CANTONESE_MODEL_DIR):
        return CANTONESE_MODEL_DIR
    return DEFAULT_MODEL


def _prepare_cuda_dlls():
    """Put the pip-provided CUDA libraries on PATH.

    ctranslate2 resolves cuBLAS/cuDNN through PATH on Windows and ignores
    `os.add_dll_directory`, so the `nvidia-*` wheel directories have to be
    prepended before it is imported. Without this, a `cuda` request fails with
    "Library cublas64_12.dll is not found" even when the wheels are installed.
    """
    try:
        import glob
        import sysconfig
    except ImportError:
        return
    if os.name != 'nt':
        return
    site = sysconfig.get_paths().get('purelib', '')
    if not site:
        return
    dirs = glob.glob(os.path.join(site, 'nvidia', '*', 'bin'))
    if dirs:
        os.environ['PATH'] = os.pathsep.join(dirs) + os.pathsep + os.environ.get('PATH', '')


def load_model(model_name='', device='auto'):
    """A Whisper model, or None when faster-whisper is unavailable.

    `auto` prefers the GPU: on the tested hardware a clip goes from 2.0s to
    0.07s, which is the difference between an overnight pass and a coffee
    break. Falls back to CPU whenever CUDA is not usable, so a machine without
    the GPU libraries still works, just slower.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    order = ('cuda', 'cpu') if device in ('auto', 'cuda') else ('cpu',)
    for candidate in order:
        if candidate == 'cuda':
            _prepare_cuda_dlls()
        try:
            compute = 'float16' if candidate == 'cuda' else 'int8'
            return WhisperModel(resolve_model(model_name), device=candidate, compute_type=compute)
        except Exception:
            continue
    return None


class TranscriptCache:
    """Content-addressed transcripts on disk.

    Keyed by the voice payload's digest rather than by message id: the same
    clip forwarded between chats is recognised once, and a re-export after the
    message ids shift still hits.
    """

    def __init__(self, cache_dir):
        self.dir = cache_dir or ''

    def path(self, key):
        return os.path.join(self.dir, key) if self.dir else ''

    def get(self, key):
        """Transcript, or None when this clip has not been processed yet."""
        path = self.path(key)
        if not path or not os.path.isfile(path):
            return None
        try:
            with open(path, 'r', encoding='utf-8') as handle:
                text = handle.read()
        except OSError:
            return None
        return '' if text == EMPTY_MARKER else text

    def put(self, key, text):
        path = self.path(key)
        if not path:
            return
        try:
            os.makedirs(self.dir, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write(text if text else EMPTY_MARKER)
        except OSError:
            pass


def transcribe_wav(wav_path, model, beam_size=1, language=DEFAULT_LANGUAGE):
    """Transcript for a WAV file, or '' when nothing was recognised."""
    try:
        segments, _ = model.transcribe(wav_path, language=language, beam_size=beam_size)
        return ''.join(segment.text for segment in segments).strip()
    except Exception:
        return ''


def ensure_transcripts(voices, cache_dir, model_name='',
                       beam_size=1, limit=0, language=DEFAULT_LANGUAGE,
                       device='auto', log=print):
    """Fill the transcript cache for any clip that is missing one.

    Returns (processed, cached, failed). Pending work is measured by cache
    misses, so an interrupted run resumes where it stopped instead of starting
    over - which matters, because a long conversation is hours of audio.
    """
    cache = TranscriptCache(cache_dir)
    pending = []
    for local_id, blob in voices.items():
        if not blob:
            continue
        key = voice_key(blob)
        if cache.get(key) is None:
            pending.append((key, blob))

    # Count the cache hits before --limit trims the queue, otherwise a limited
    # run reports everything it did not process as already done.
    already = len(voices) - len(pending)
    if limit and limit > 0:
        pending = pending[:limit]
    if not pending:
        log(f'所有 {len(voices)} 条语音都已有转写（缓存命中）')
        return 0, already, 0

    resolved = resolve_model(model_name)
    log(f'待转写 {len(pending)} 条（已有 {already} 条）')
    log(f'模型: {resolved}')
    model = load_model(model_name, device=device)
    if model is None:
        log('无法加载 faster-whisper 模型，转写跳过')
        return 0, already, len(pending)

    import tempfile
    processed = failed = 0
    scratch = tempfile.mkdtemp(prefix='weflow-voice-')
    try:
        for index, (key, blob) in enumerate(pending, 1):
            wav_path = os.path.join(scratch, f'{index}.wav')
            if not decode_silk(blob, wav_path):
                cache.put(key, '')
                failed += 1
            else:
                text = transcribe_wav(wav_path, model, beam_size=beam_size, language=language)
                cache.put(key, text)
                processed += 1
                try:
                    os.remove(wav_path)
                except OSError:
                    pass
            if index % 10 == 0 or index == len(pending):
                log(f'  转写 {index}/{len(pending)}（新增 {processed}，失败 {failed}）')
    finally:
        try:
            import shutil
            shutil.rmtree(scratch, ignore_errors=True)
        except Exception:
            pass
    return processed, already, failed


def _main():
    import argparse
    parser = argparse.ArgumentParser(description='微信语音转写（本地 Whisper）')
    parser.add_argument('--db', required=True, help='message_0.db 路径，用于定位同目录的 media_*.db')
    parser.add_argument('--key', required=True, help='NT key hex')
    parser.add_argument('--salt', required=True, help='NT salt hex')
    parser.add_argument('--passphrase', default='', help='分片密钥口令')
    parser.add_argument('--talker', required=True, help='会话 id')
    parser.add_argument('--cache-dir', required=True, help='转写缓存目录')
    parser.add_argument('--model', default='', help='模型名或本地目录（默认优先用本地粤语模型）')
    parser.add_argument('--language', default='', help='语言代码；留空为自动检测（粤语模型必须留空）')
    parser.add_argument('--device', default='auto', choices=['auto', 'cuda', 'cpu'], help='推理设备')
    parser.add_argument('--beam-size', type=int, default=1)
    parser.add_argument('--limit', type=int, default=0, help='本次最多转写多少条（0 为不限）')
    args = parser.parse_args()

    voices = load_voice_map(args.db, args.key, args.salt, args.passphrase, args.talker)
    print(f'会话 {args.talker}: 找到 {len(voices)} 条语音', flush=True)
    processed, already, failed = ensure_transcripts(
        voices, args.cache_dir, model_name=args.model,
        beam_size=args.beam_size, limit=args.limit,
        language=args.language or None, device=args.device)
    print(json.dumps({'success': True, 'voices': len(voices),
                      'transcribed': processed, 'cached': already, 'failed': failed},
                     ensure_ascii=False))


if __name__ == '__main__':
    _main()
