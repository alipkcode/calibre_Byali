#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint:
#   1) Thread-safe in-memory ring buffer
#   2) Append-only file under calibre config dir
#   3) Session helpers for one agent-run timeline
#   4) Drain JS log payloads into the same buffer

"""Byali AI debug logger for viewer rewrite / note attach diagnostics."""

from __future__ import annotations

import json
import os
import threading
import traceback
from collections import deque
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

_LOCK = threading.RLock()
_MAX_LINES = 2000
_buffer: deque[str] = deque(maxlen=_MAX_LINES)
_listeners: list = []  # callables taking no args — UI refresh hooks


def _now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] + 'Z'


def log_file_path() -> str:
    from calibre.constants import config_dir

    return os.path.join(config_dir, 'byali_ai_debug.log')


def add_listener(cb) -> None:
    with _LOCK:
        if cb not in _listeners:
            _listeners.append(cb)


def remove_listener(cb) -> None:
    with _LOCK:
        if cb in _listeners:
            _listeners.remove(cb)


def _notify() -> None:
    for cb in list(_listeners):
        try:
            cb()
        except Exception:
            pass


def log(level: str, message: str, **fields: Any) -> None:
    """Append one structured log line (memory + file)."""
    level = (level or 'INFO').upper()
    extra = ''
    if fields:
        try:
            extra = ' | ' + json.dumps(fields, ensure_ascii=False, default=str)
        except Exception:
            extra = ' | ' + repr(fields)
    line = f'{_now()} [{level}] {message}{extra}'
    with _LOCK:
        _buffer.append(line)
        try:
            path = log_file_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except Exception as e:
            _buffer.append(f'{_now()} [ERROR] failed to write log file: {e}')
    _notify()


def info(message: str, **fields: Any) -> None:
    log('INFO', message, **fields)


def warn(message: str, **fields: Any) -> None:
    log('WARN', message, **fields)


def error(message: str, **fields: Any) -> None:
    log('ERROR', message, **fields)


def debug(message: str, **fields: Any) -> None:
    log('DEBUG', message, **fields)


def exception(message: str, exc: BaseException | None = None, **fields: Any) -> None:
    tb = ''
    if exc is not None:
        tb = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        fields = {**fields, 'exc_type': type(exc).__name__, 'exc': str(exc)}
    log('ERROR', message, **fields)
    if tb:
        for line in tb.rstrip().splitlines():
            log('ERROR', '  ' + line)


def new_session(label: str = '') -> str:
    sid = uuid4().hex[:10]
    info('=== session start ===', session=sid, label=label or '')
    return sid


def get_text() -> str:
    with _LOCK:
        return '\n'.join(_buffer)


def clear() -> None:
    with _LOCK:
        _buffer.clear()
    info('log cleared')


def ingest_js_entries(entries: Any, session: str = '') -> int:
    """Merge drained JS log objects into the Python buffer."""
    if not entries:
        return 0
    if isinstance(entries, str):
        try:
            entries = json.loads(entries)
        except Exception:
            warn('JS log drain: non-JSON payload', session=session, raw=entries[:500])
            return 0
    if not isinstance(entries, list):
        warn('JS log drain: unexpected type', session=session, type=type(entries).__name__)
        return 0
    count = 0
    for item in entries:
        if not isinstance(item, dict):
            continue
        level = str(item.get('level') or 'DEBUG')
        msg = str(item.get('msg') or '')
        data = item.get('data')
        fields: dict[str, Any] = {'src': 'js'}
        if session:
            fields['session'] = session
        if data is not None:
            fields['data'] = data
        t = item.get('t')
        if t is not None:
            fields['js_t'] = t
        log(level, f'[js] {msg}', **fields)
        count += 1
    return count
