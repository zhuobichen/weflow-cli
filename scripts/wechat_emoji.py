#!/usr/bin/env python3
"""Render WeChat face codes like [害羞] for HTML export.

WeChat stores faces in message text as bracketed names. Two renderers:

* Faces we ship the original artwork for (resources/emoji/*.png, MIT, from the
  `wechat-emojis` package) become a <span> whose CSS class carries the image.
  The image is base64-embedded once per page, not once per occurrence.
* Everything else falls back to the closest standard emoji. WeChat's faces are
  its own artwork, so these are approximations - only used where we have no
  original.

Unrecognised codes (including the placeholders this project generates itself,
such as [图片]) are left untouched.
"""
import base64
import hashlib
import json
import re
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_DATA_FILE = _HERE / 'data' / 'wechat_emoji.json'
EMOJI_DIR = _HERE.parent / 'resources' / 'emoji'

# Keys starting with "_" are documentation, not faces.
_FACES = {}
try:
    with open(_DATA_FILE, encoding='utf-8') as fh:
        _FACES = {k: v for k, v in json.load(fh).items() if not k.startswith('_')}
except (OSError, ValueError):
    _FACES = {}


def _load_image_faces():
    """{face name: png path} for every face we have original artwork for."""
    if not EMOJI_DIR.is_dir():
        return {}
    return {p.stem: p for p in EMOJI_DIR.glob('*.png')}


IMAGE_FACES = _load_image_faces()
ALL_NAMES = set(_FACES) | set(IMAGE_FACES)

# Longest name first so [左哼哼] can never be shadowed by a shorter prefix.
_ALL_RE = None
if ALL_NAMES:
    _alternation = '|'.join(re.escape(n) for n in sorted(ALL_NAMES, key=len, reverse=True))
    _ALL_RE = re.compile(r'\[(' + _alternation + r')\]')


def face_class(name):
    """Stable CSS class for a face with original artwork."""
    return 'wxf-' + hashlib.md5(name.encode()).hexdigest()[:10]


def convert_faces(text):
    """Replace known [表情] codes with emoji. Unknown codes pass through."""
    if not text or _ALL_RE is None:
        return text
    return _ALL_RE.sub(lambda m: _FACES.get(m.group(1), m.group(0)), text)


def render_faces(text):
    """Like convert_faces, but faces with original artwork become a <span>.

    The returned string is HTML and must NOT be escaped again.
    """
    if not text or _ALL_RE is None:
        return text

    def repl(m):
        name = m.group(1)
        if name in IMAGE_FACES:
            return f'<span class="wxface {face_class(name)}"></span>'
        return _FACES.get(name, m.group(0))

    return _ALL_RE.sub(repl, text)


def faces_used_in(html):
    """CSS classes referenced by an HTML fragment."""
    return set(re.findall(r'wxface (wxf-[0-9a-f]{10})', html))


def face_css(html, indent='  '):
    """CSS defining just the faces this page actually uses.

    Embedding only what a page needs keeps paginated exports small - a full
    sprite would add ~1MB to every page.
    """
    used = faces_used_in(html)
    if not used:
        return ''
    rules = []
    for name, path in sorted(IMAGE_FACES.items()):
        cls = face_class(name)
        if cls not in used:
            continue
        try:
            b64 = base64.b64encode(path.read_bytes()).decode()
        except OSError:
            continue
        rules.append(f'.{cls} {{ background-image: url(data:image/png;base64,{b64}); }}')
    if not rules:
        return ''
    return indent + ('\n' + indent).join(rules)


if __name__ == '__main__':
    import sys
    for line in sys.stdin:
        sys.stdout.write(convert_faces(line))
