# -*- coding: utf-8 -*-
"""读 `weflow-cli import qchat` 落下的存储（output/imports/qq/sessions/）。

**单一读者**：chat_notes 与 semantic_search 都从这里取导入会话。两边各解析一遍格式，
漂了就是"知识卡看得见、搜索看不见"这类静默分叉——`nt_common` 存在的同款理由。

存储形状（写入方是 `src/services/qchatImport.ts`，字段语义对齐 weflow-message/v1）：
每个会话一个 JSON：schema/session{id,name,type}/importedAt/sourceFiles/messages[]，
消息字段 serverId(=qchat messageId，全局唯一)/createTime(unix 秒)/isSend(可缺省=
未知)/senderUsername(QQ 号，敏感)/senderName/messageType(13 稳定名)/parsedContent/
rawContent。

路径：默认 `output/imports/qq/sessions`（cwd 相对，与 chat_notes 的 OUTPUT_ROOT 同一
约定）；`WEFLOW_IMPORT_ROOT` 指到别处时用 `<root>/sessions`（测试用，TS 写入方读同
一个变量）。
"""
import json
import os
from pathlib import Path

SCHEMA = 'weflow-import/qq-v1'


def sessions_dir(root=None):
    if root is None:
        root = os.environ.get('WEFLOW_IMPORT_ROOT')
    if root:
        return Path(root) / 'sessions'
    # 默认必须与**写入方**一致：TS 侧写的是 `resolveOutputRoot()/imports/qq`，
    # 而可写根在全局安装时会回退到 `~/.weflow-cli/output`。
    # 之前这里写死 cwd 相对的 `output/imports/qq/sessions`——装到 Linux 后
    # 写入方在用户目录、读取方在 cwd，导入的会话会"看不见"（2026-11-01 实测）。
    try:
        from _utils import output_root as _output_root
        return Path(_output_root()) / 'imports' / 'qq' / 'sessions'
    except Exception:
        return Path('output/imports/qq/sessions')


def sessions(root=None):
    """全部已导入会话（按导入时间新→旧）。坏文件/非本 schema 的文件跳过不挡路。

    返回的是存储原文（dict），消息已按 createTime 升序。
    """
    d = sessions_dir(root)
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob('*.json')):
        try:
            data = json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if data.get('schema') != SCHEMA or not isinstance(data.get('messages'), list):
            continue
        out.append(data)
    out.sort(key=lambda s: s.get('importedAt') or '', reverse=True)
    return out


def session_messages(sess, days=None, now=None):
    """一个会话的消息，可选按"最近 days 天"过滤。

    时间语义与 chat_notes/semantic_search 的窗口一致：unix 秒、含当天。
    """
    from datetime import datetime, timedelta, timezone

    msgs = [m for m in sess.get('messages', []) if isinstance(m, dict)]
    if days is None:
        return msgs
    tz = timezone(timedelta(hours=8))
    now = now or datetime.now(tz)
    cutoff = int((now - timedelta(days=days)).timestamp())
    return [m for m in msgs if (m.get('createTime') or 0) >= cutoff]
