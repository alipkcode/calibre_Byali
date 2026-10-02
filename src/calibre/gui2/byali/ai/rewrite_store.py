#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint:
#   1) PassageRewrite dataclass + JSONConfig('byali_viewer_rewrites') keyed by book path hash
#   2) CRUD: add / list / delete
#   3) Never wipe other books' rewrites (additive)
#   4) JS payload for viewer restore (same pattern as notes)

"""Persistent Byali AI rewrites applied to book passages (viewer overlay)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any
from uuid import uuid4

from calibre.utils.config import JSONConfig


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class PassageRewrite:
    id: str
    original_text: str  # text that was replaced (search key on restore)
    rewritten_text: str  # AI output shown in the viewer
    agent_id: str = ''
    agent_name: str = ''
    created_at: str = ''
    updated_at: str = ''
    text_direction: str = 'auto'  # auto | ltr | rtl for the containing paragraph

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PassageRewrite:
        direction = str(data.get('text_direction') or 'auto').lower()
        if direction not in ('auto', 'ltr', 'rtl'):
            direction = 'auto'
        return cls(
            id=str(data.get('id') or uuid4().hex),
            original_text=str(data.get('original_text') or ''),
            rewritten_text=str(data.get('rewritten_text') or ''),
            agent_id=str(data.get('agent_id') or ''),
            agent_name=str(data.get('agent_name') or ''),
            created_at=str(data.get('created_at') or ''),
            updated_at=str(data.get('updated_at') or ''),
            text_direction=direction,
        )


@lru_cache(2)
def prefs() -> JSONConfig:
    ans = JSONConfig('byali_viewer_rewrites')
    ans.defaults['books'] = {}
    return ans


def list_rewrites_for_book(book_key: str) -> list[PassageRewrite]:
    key = (book_key or '').strip()
    if not key:
        return []
    books = prefs().get('books') or {}
    raw = books.get(key) or []
    return [PassageRewrite.from_dict(x) for x in raw if isinstance(x, dict)]


def save_rewrites_for_book(book_key: str, items: list[PassageRewrite]) -> None:
    key = (book_key or '').strip()
    if not key:
        return
    p = prefs()
    books = dict(p.get('books') or {})
    books[key] = [r.to_dict() for r in items]
    p.set('books', books)


def add_rewrite(
    book_key: str,
    *,
    original_text: str,
    rewritten_text: str,
    agent_id: str = '',
    agent_name: str = '',
    rewrite_id: str | None = None,
    text_direction: str = 'auto',
) -> PassageRewrite:
    now = _now_iso()
    direction = (text_direction or 'auto').lower()
    if direction not in ('auto', 'ltr', 'rtl'):
        direction = 'auto'
    item = PassageRewrite(
        id=rewrite_id or uuid4().hex,
        original_text=(original_text or '').strip(),
        rewritten_text=(rewritten_text or '').strip(),
        agent_id=agent_id or '',
        agent_name=agent_name or '',
        created_at=now,
        updated_at=now,
        text_direction=direction,
    )
    items = list_rewrites_for_book(book_key)
    # Replace any prior rewrite of the exact same original (avoid duplicates on re-run).
    items = [r for r in items if r.original_text != item.original_text]
    items.append(item)
    save_rewrites_for_book(book_key, items)
    return item


def delete_rewrite(book_key: str, rewrite_id: str) -> bool:
    items = list_rewrites_for_book(book_key)
    kept = [r for r in items if r.id != rewrite_id]
    if len(kept) == len(items):
        return False
    save_rewrites_for_book(book_key, kept)
    return True


def rewrites_as_js_payload(book_key: str) -> list[dict[str, Any]]:
    """Minimal fields for the viewer helper. Longer originals first (safer matching)."""
    items = [
        r
        for r in list_rewrites_for_book(book_key)
        if r.original_text and r.rewritten_text
    ]
    items.sort(key=lambda r: len(r.original_text), reverse=True)
    return [
        {
            'id': r.id,
            'original_text': r.original_text,
            'rewritten_text': r.rewritten_text,
            'agent_name': r.agent_name,
            'text_direction': r.text_direction or 'auto',
        }
        for r in items
    ]
