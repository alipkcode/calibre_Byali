#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint:
#   1) Note dataclass + JSONConfig('byali_viewer_notes') keyed by book path hash
#   2) CRUD: add / list / delete / update note body
#   3) Never wipe other books' notes (additive)

"""Persistent Byali AI notes attached to book passages (viewer)."""

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
class PassageNote:
    id: str
    anchor_text: str  # selected passage (kept in the book)
    note: str  # AI / user note body
    agent_id: str = ''
    agent_name: str = ''
    created_at: str = ''
    updated_at: str = ''

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PassageNote:
        return cls(
            id=str(data.get('id') or uuid4().hex),
            anchor_text=str(data.get('anchor_text') or ''),
            note=str(data.get('note') or ''),
            agent_id=str(data.get('agent_id') or ''),
            agent_name=str(data.get('agent_name') or ''),
            created_at=str(data.get('created_at') or ''),
            updated_at=str(data.get('updated_at') or ''),
        )


@lru_cache(2)
def prefs() -> JSONConfig:
    ans = JSONConfig('byali_viewer_notes')
    ans.defaults['books'] = {}
    return ans


def list_notes_for_book(book_key: str) -> list[PassageNote]:
    key = (book_key or '').strip()
    if not key:
        return []
    books = prefs().get('books') or {}
    raw = books.get(key) or []
    return [PassageNote.from_dict(x) for x in raw if isinstance(x, dict)]


def save_notes_for_book(book_key: str, notes: list[PassageNote]) -> None:
    key = (book_key or '').strip()
    if not key:
        return
    p = prefs()
    books = dict(p.get('books') or {})
    books[key] = [n.to_dict() for n in notes]
    p.set('books', books)


def add_note(
    book_key: str,
    *,
    anchor_text: str,
    note: str,
    agent_id: str = '',
    agent_name: str = '',
    note_id: str | None = None,
) -> PassageNote:
    now = _now_iso()
    item = PassageNote(
        id=note_id or uuid4().hex,
        anchor_text=(anchor_text or '').strip(),
        note=(note or '').strip(),
        agent_id=agent_id or '',
        agent_name=agent_name or '',
        created_at=now,
        updated_at=now,
    )
    notes = list_notes_for_book(book_key)
    notes.append(item)
    save_notes_for_book(book_key, notes)
    return item


def delete_note(book_key: str, note_id: str) -> bool:
    notes = list_notes_for_book(book_key)
    kept = [n for n in notes if n.id != note_id]
    if len(kept) == len(notes):
        return False
    save_notes_for_book(book_key, kept)
    return True


def delete_notes(book_key: str, note_ids: list[str]) -> int:
    if not note_ids:
        return 0
    drop = set(note_ids)
    notes = list_notes_for_book(book_key)
    kept = [n for n in notes if n.id not in drop]
    removed = len(notes) - len(kept)
    if removed:
        save_notes_for_book(book_key, kept)
    return removed


def notes_as_js_payload(book_key: str) -> list[dict[str, Any]]:
    """Minimal fields for the viewer helper."""
    return [
        {
            'id': n.id,
            'anchor_text': n.anchor_text,
            'note': n.note,
            'agent_name': n.agent_name,
            'created_at': n.created_at,
        }
        for n in list_notes_for_book(book_key)
        if n.anchor_text and n.note
    ]
