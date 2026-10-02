#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Core duplication logic (DB side).
# Blueprint:
#   1) Read source metadata + formats without holding cross locks
#   2) Stamp Duplicated label + source lineage on Metadata
#   3) add_books(..., add_duplicates=True) into the same library
#   4) Return new book ids for GUI refresh / selection

"""Create independent library copies labeled for later Byali AI editing."""

from __future__ import annotations

from io import BytesIO

from calibre.gui2.byali.constants import DUPLICATE_TAG, DUPLICATE_TITLE_SUFFIX, SOURCE_IDENTIFIER
from calibre.utils.date import now


def _stamp_duplicate_metadata(mi, source_book_id: int) -> None:
    """Mutate Metadata so the new book is labeled and linked to its source."""
    title = mi.title or ''
    if DUPLICATE_TITLE_SUFFIX not in title:
        mi.title = f'{title}{DUPLICATE_TITLE_SUFFIX}'

    tags = list(mi.tags or [])
    if DUPLICATE_TAG not in tags:
        tags.append(DUPLICATE_TAG)
    mi.tags = tags

    identifiers = mi.get_identifiers()
    identifiers[SOURCE_IDENTIFIER] = str(source_book_id)
    mi.set_identifiers(identifiers)

    # Fresh entry: new timestamp, new uuid (preserve_uuid=False on add_books)
    mi.timestamp = now()


def _format_map_for_book(api, book_id: int) -> dict:
    """Build {FMT: BytesIO} for all formats of book_id."""
    format_map = {}
    for fmt in api.formats(book_id, verify_formats=False) or ():
        data = api.format(book_id, fmt)
        if data:
            format_map[fmt] = BytesIO(data)
    return format_map


def duplicate_book(db, book_id: int) -> int | None:
    """
    Duplicate one book inside the same library.

    Returns the new book id, or None if creation failed.
    Does not use copy_to_library.copy_one_book (same-db read+write lock hazard).
    """
    api = db.new_api
    mi = api.get_metadata(book_id, get_cover=True, cover_as_data=True)
    _stamp_duplicate_metadata(mi, book_id)
    format_map = _format_map_for_book(api, book_id)
    new_ids, _duplicates = api.add_books(
        [(mi, format_map)],
        add_duplicates=True,
        preserve_uuid=False,
        apply_import_tags=False,
    )
    if not new_ids:
        return None
    return new_ids[0]


def duplicate_books(db, book_ids) -> list[int]:
    """Duplicate many books; returns list of successfully created new ids."""
    created = []
    for book_id in book_ids:
        new_id = duplicate_book(db, int(book_id))
        if new_id is not None:
            created.append(new_id)
    return created
