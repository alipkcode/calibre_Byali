#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint (ported from PBE5 ai_agents/network_library.py — storage + seeds):
#   1) Shared DNS / proxy library entries in byali_ai JSONConfig
#   2) Seed well-known public DNS resolvers (same list as PBE5)
#   3) Upsert / list / delete / remember from connection form

"""Reusable DNS gate / proxy URL library for Byali AI connections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
from uuid import uuid4

from calibre.gui2.byali.ai.agents import prefs
from calibre.gui2.byali.ai.transport import TransportError, normalize_dns_server, normalize_proxy_url
from calibre.utils.localization import _

KIND_DNS = 'dns'
KIND_PROXY = 'proxy'
KINDS = (KIND_DNS, KIND_PROXY)

# Same seed list as PBE5 manufacturing_app/ai_agents/network_library.py
SEED_DNS: list[tuple[str, str]] = [
    ('Electro', '78.157.42.100'),
    ('Electro secondary', '78.157.42.101'),
    ('Begzar', '185.55.226.26'),
    ('Shecan', '178.22.122.100'),
    ('Shecan secondary', '185.51.200.2'),
    ('Google', '8.8.8.8'),
    ('Google secondary', '8.8.4.4'),
    ('Cloudflare', '1.1.1.1'),
    ('Cloudflare secondary', '1.0.0.1'),
    ('Quad9', '9.9.9.9'),
    ('OpenDNS', '208.67.222.222'),
    ('OpenDNS secondary', '208.67.220.220'),
    ('NextDNS', '45.90.28.0'),
    ('Yandex', '77.88.8.8'),
    ('CleanBrowsing', '185.228.168.9'),
    ('ControlD', '76.76.2.0'),
    ('Verisign', '64.6.64.6'),
    ('AdGuard', '94.140.14.14'),
    ('AdGuard secondary', '94.140.15.15'),
    ('403.online', '10.202.10.202'),
    ('403.online secondary', '10.202.10.102'),
    ('Radar Game', '10.202.10.10'),
    ('Radar Game secondary', '10.202.10.11'),
]

BATCH_PROXY_LIMIT = 200


@dataclass
class NetworkEntry:
    id: str
    kind: str  # dns | proxy
    value: str
    label: str = ''
    source: str = 'user'  # seed | user | scan
    is_archived: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'kind': self.kind,
            'value': self.value,
            'label': self.label,
            'source': self.source,
            'is_archived': bool(self.is_archived),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NetworkEntry:
        kind = str(data.get('kind') or '')
        if kind not in KINDS:
            kind = KIND_PROXY
        source = str(data.get('source') or 'user')
        if source not in ('seed', 'user', 'scan'):
            source = 'user'
        return cls(
            id=str(data.get('id') or uuid4().hex),
            kind=kind,
            value=str(data.get('value') or ''),
            label=str(data.get('label') or ''),
            source=source,
            is_archived=bool(data.get('is_archived', False)),
        )


def _load_raw() -> list[NetworkEntry]:
    raw = prefs().get('network_library') or []
    return [NetworkEntry.from_dict(x) for x in raw if isinstance(x, dict)]


def _save(entries: list[NetworkEntry]) -> None:
    prefs().set('network_library', [e.to_dict() for e in entries])


def ensure_network_library_seeded() -> int:
    """Insert built-in DNS seeds if missing. Returns number of rows inserted."""
    entries = _load_raw()
    existing = {(e.kind, e.value) for e in entries}
    inserted = 0
    for label, value in SEED_DNS:
        try:
            normalised = normalize_dns_server(value)
        except TransportError:
            continue
        if not normalised:
            continue
        key = (KIND_DNS, normalised)
        if key in existing:
            continue
        entries.append(
            NetworkEntry(
                id=uuid4().hex,
                kind=KIND_DNS,
                value=normalised,
                label=label,
                source='seed',
            )
        )
        existing.add(key)
        inserted += 1
    if inserted:
        _save(entries)
    return inserted


def list_library(kind: str, include_archived: bool = False) -> list[NetworkEntry]:
    if kind not in KINDS:
        raise TransportError(_("Library kind must be 'dns' or 'proxy'."))
    ensure_network_library_seeded()
    items = [e for e in _load_raw() if e.kind == kind]
    if not include_archived:
        items = [e for e in items if not e.is_archived]
    return items


def upsert_library_value(
    kind: str,
    value: str,
    label: str = '',
    source: str = 'user',
    restore_if_archived: bool = False,
) -> NetworkEntry:
    if kind not in KINDS:
        raise TransportError(_("Library kind must be 'dns' or 'proxy'."))
    if kind == KIND_DNS:
        normalised = normalize_dns_server(value)
    else:
        normalised = normalize_proxy_url(value)
    if not normalised:
        raise TransportError(_('Value is empty.'))
    source = source if source in ('seed', 'user', 'scan') else 'user'
    entries = _load_raw()
    for e in entries:
        if e.kind == kind and e.value == normalised:
            if restore_if_archived and e.is_archived:
                e.is_archived = False
            if label:
                e.label = label
            if source != 'seed':
                e.source = source
            _save(entries)
            return e
    entry = NetworkEntry(
        id=uuid4().hex,
        kind=kind,
        value=normalised,
        label=label or '',
        source=source,
    )
    entries.append(entry)
    _save(entries)
    return entry


def batch_add_library_values(kind: str, values: list[str], source: str = 'user') -> tuple[int, int]:
    """Add many values. Returns (added, skipped). Caps at BATCH_PROXY_LIMIT."""
    if kind not in KINDS:
        raise TransportError(_("Library kind must be 'dns' or 'proxy'."))
    added = skipped = 0
    for raw in values[:BATCH_PROXY_LIMIT]:
        try:
            before = {(e.kind, e.value) for e in _load_raw()}
            entry = upsert_library_value(kind, raw, source=source, restore_if_archived=True)
            if (entry.kind, entry.value) in before and entry.source == 'seed':
                skipped += 1
            elif (entry.kind, entry.value) in before:
                skipped += 1
            else:
                added += 1
        except TransportError:
            skipped += 1
    # Recount accurately
    return added, skipped


def delete_library_entry(entry_id: str) -> None:
    entries = [e for e in _load_raw() if e.id != entry_id]
    _save(entries)


def archive_library_entry(entry_id: str) -> None:
    entries = _load_raw()
    for e in entries:
        if e.id == entry_id:
            e.is_archived = True
            break
    _save(entries)


def remember_networking_in_library(dns_server: Optional[str], proxy_url: Optional[str]) -> None:
    """Best-effort: remember non-empty DNS/proxy values in the shared library."""
    try:
        if dns_server and str(dns_server).strip():
            upsert_library_value(KIND_DNS, str(dns_server).strip(), source='user', restore_if_archived=True)
        if proxy_url and str(proxy_url).strip():
            upsert_library_value(KIND_PROXY, str(proxy_url).strip(), source='user', restore_if_archived=True)
    except TransportError:
        pass
