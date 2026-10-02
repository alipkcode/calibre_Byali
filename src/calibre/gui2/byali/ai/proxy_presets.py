#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint (PBE5 network library storage semantics for proxies):
#   1) ProxyPreset + rolling 5-slot test history
#   2) Active / archived shelves
#   3) Filter / sort / success-rate helpers
#   4) Batch add, archive, approve for LLM connection picker

"""Named/approved proxy presets with PBE5-style history and shelves."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from calibre.gui2.byali.ai.agents import prefs
from calibre.gui2.byali.ai.proxy_probe import network_attempt_color
from calibre.gui2.byali.ai.transport import TransportError, normalize_proxy_url
from calibre.utils.localization import _

HISTORY_SLOTS = 5
BATCH_PROXY_LIMIT = 200
SHELF_ACTIVE = 'active'
SHELF_ARCHIVED = 'archived'
SHELF_ALL = 'all'


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class ProxyPreset:
    id: str
    proxy_url: str
    name: str = ''  # approved shortcut / preset name
    approved: bool = False
    source: str = 'user'  # user | paste | scan
    is_archived: bool = False
    last_test_at: str = ''
    last_local_ok: Optional[bool] = None
    last_api_ok: Optional[bool] = None
    last_test_ok: Optional[bool] = None
    last_test_latency_ms: Optional[int] = None
    last_test_message: str = ''
    last_local_latency_ms: Optional[int] = None
    last_api_latency_ms: Optional[int] = None
    test_history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'proxy_url': self.proxy_url,
            'name': self.name or '',
            'approved': bool(self.approved),
            'source': self.source or 'user',
            'is_archived': bool(self.is_archived),
            'last_test_at': self.last_test_at or '',
            'last_local_ok': self.last_local_ok,
            'last_api_ok': self.last_api_ok,
            'last_test_ok': self.last_test_ok,
            'last_test_latency_ms': self.last_test_latency_ms,
            'last_test_message': self.last_test_message or '',
            'last_local_latency_ms': self.last_local_latency_ms,
            'last_api_latency_ms': self.last_api_latency_ms,
            'test_history': list(self.test_history or []),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProxyPreset:
        def _opt_bool(key: str) -> Optional[bool]:
            if key not in data or data[key] is None:
                return None
            return bool(data[key])

        def _opt_int(key: str) -> Optional[int]:
            if key not in data or data[key] is None:
                return None
            try:
                return int(data[key])
            except (TypeError, ValueError):
                return None

        source = str(data.get('source') or 'user')
        if source not in ('user', 'paste', 'scan'):
            source = 'user'
        history = []
        for item in data.get('test_history') or []:
            if not isinstance(item, dict):
                continue
            local_ok = bool(item.get('local_ok'))
            api_ok = bool(item.get('api_ok'))
            color = item.get('color')
            if color not in ('green', 'orange', 'red'):
                color = network_attempt_color(local_ok, api_ok)
            history.append({
                'at': str(item.get('at') or ''),
                'local_ok': local_ok,
                'api_ok': api_ok,
                'color': color,
            })
        return cls(
            id=str(data.get('id') or uuid4().hex),
            proxy_url=str(data.get('proxy_url') or ''),
            name=str(data.get('name') or ''),
            approved=bool(data.get('approved', False)),
            source=source,
            is_archived=bool(data.get('is_archived', False)),
            last_test_at=str(data.get('last_test_at') or ''),
            last_local_ok=_opt_bool('last_local_ok'),
            last_api_ok=_opt_bool('last_api_ok'),
            last_test_ok=_opt_bool('last_test_ok'),
            last_test_latency_ms=_opt_int('last_test_latency_ms'),
            last_test_message=str(data.get('last_test_message') or ''),
            last_local_latency_ms=_opt_int('last_local_latency_ms'),
            last_api_latency_ms=_opt_int('last_api_latency_ms'),
            test_history=history[-HISTORY_SLOTS:],
        )

    def display_label(self) -> str:
        if self.name.strip():
            return self.name.strip()
        return self.proxy_url

    def history_items(self) -> list[dict[str, Any]]:
        items = list(self.test_history or [])
        if not items and (self.last_test_at or self.last_test_ok is not None):
            local_ok = self.last_local_ok
            api_ok = self.last_api_ok
            if local_ok is None and api_ok is None:
                if self.last_test_ok is True:
                    local_ok, api_ok = True, True
                elif self.last_test_ok is False:
                    local_ok, api_ok = False, False
                else:
                    return []
            items.append({
                'at': self.last_test_at or '',
                'local_ok': bool(local_ok),
                'api_ok': bool(api_ok),
                'color': network_attempt_color(local_ok, api_ok),
            })
        return items[-HISTORY_SLOTS:]

    def latest_color(self) -> Optional[str]:
        items = self.history_items()
        if items:
            return items[-1].get('color')
        if self.last_test_ok is True:
            return 'green'
        if self.last_local_ok is True and self.last_api_ok is False:
            return 'orange'
        if self.last_test_ok is False:
            return 'red'
        return None

    def success_pct(self) -> Optional[int]:
        items = self.history_items()
        if not items:
            return None
        green = sum(1 for i in items if i.get('color') == 'green')
        return int(round(100.0 * green / len(items)))

    def history_dots_text(self) -> str:
        """Compact history for list rows: e.g. ···●● 40%"""
        items = self.history_items()
        slots = ['.'] * (HISTORY_SLOTS - len(items))
        for item in items:
            color = item.get('color')
            slots.append({'green': 'G', 'orange': 'O', 'red': 'R'}.get(color, '.'))
        pct = self.success_pct()
        pct_s = f' {pct}%' if pct is not None else ''
        return ''.join(slots) + pct_s

    def append_test_history(self, local_ok: bool, api_ok: bool) -> None:
        items = list(self.test_history or [])
        if not items:
            seed = None
            if self.last_test_at or self.last_test_ok is not None:
                lo = self.last_local_ok
                ao = self.last_api_ok
                if lo is None and ao is None:
                    if self.last_test_ok is True:
                        lo, ao = True, True
                    elif self.last_test_ok is False:
                        lo, ao = False, False
                if lo is not None or ao is not None:
                    seed = {
                        'at': self.last_test_at or '',
                        'local_ok': bool(lo),
                        'api_ok': bool(ao),
                        'color': network_attempt_color(lo, ao),
                    }
            if seed:
                items.append(seed)
        items.append({
            'at': _now_iso(),
            'local_ok': bool(local_ok),
            'api_ok': bool(api_ok),
            'color': network_attempt_color(local_ok, api_ok),
        })
        self.test_history = items[-HISTORY_SLOTS:]


def load_proxy_presets() -> list[ProxyPreset]:
    raw = prefs().get('proxy_presets') or []
    return [ProxyPreset.from_dict(x) for x in raw if isinstance(x, dict)]


def save_proxy_presets(items: list[ProxyPreset]) -> None:
    prefs().set('proxy_presets', [p.to_dict() for p in items])


def get_proxy_preset(preset_id: str) -> ProxyPreset | None:
    for p in load_proxy_presets():
        if p.id == preset_id:
            return p
    return None


def shelf_counts() -> dict[str, int]:
    items = load_proxy_presets()
    active = sum(1 for p in items if not p.is_archived)
    archived = sum(1 for p in items if p.is_archived)
    return {'active': active, 'archived': archived, 'total': len(items)}


def list_by_shelf(shelf: str = SHELF_ACTIVE) -> list[ProxyPreset]:
    items = load_proxy_presets()
    if shelf == SHELF_ARCHIVED:
        return [p for p in items if p.is_archived]
    if shelf == SHELF_ALL:
        return items
    return [p for p in items if not p.is_archived]


def list_approved_proxy_presets() -> list[ProxyPreset]:
    """Approved named presets available to LLM connections (active shelf only)."""
    return [
        p for p in load_proxy_presets()
        if p.approved and p.name.strip() and p.proxy_url.strip() and not p.is_archived
    ]


def add_proxy_candidate(proxy_url: str, source: str = 'user') -> ProxyPreset:
    normalised = normalize_proxy_url(proxy_url)
    if not normalised:
        raise TransportError(_('Proxy URL is empty.'))
    if source not in ('user', 'paste', 'scan'):
        source = 'user'
    items = load_proxy_presets()
    for existing in items:
        if existing.proxy_url == normalised:
            if existing.is_archived:
                existing.is_archived = False
                upsert_proxy_preset(existing)
            return existing
    preset = ProxyPreset(id=uuid4().hex, proxy_url=normalised, approved=False, source=source)
    items.append(preset)
    save_proxy_presets(items)
    return preset


def batch_add_proxy_urls(urls: list[str], source: str = 'paste') -> tuple[int, int]:
    """Add many URLs. Returns (added, skipped). Caps at BATCH_PROXY_LIMIT."""
    added = skipped = 0
    existing = {p.proxy_url.lower() for p in load_proxy_presets()}
    for raw in urls[:BATCH_PROXY_LIMIT]:
        try:
            normalised = normalize_proxy_url(raw)
        except TransportError:
            skipped += 1
            continue
        if not normalised:
            skipped += 1
            continue
        if normalised.lower() in existing:
            # restore if archived
            for p in load_proxy_presets():
                if p.proxy_url.lower() == normalised.lower() and p.is_archived:
                    p.is_archived = False
                    upsert_proxy_preset(p)
                    break
            skipped += 1
            continue
        add_proxy_candidate(normalised, source=source)
        existing.add(normalised.lower())
        added += 1
    return added, skipped


def upsert_proxy_preset(preset: ProxyPreset) -> None:
    if preset.proxy_url.strip():
        preset.proxy_url = normalize_proxy_url(preset.proxy_url) or preset.proxy_url
    items = load_proxy_presets()
    for i, existing in enumerate(items):
        if existing.id == preset.id:
            items[i] = preset
            break
    else:
        items.append(preset)
    save_proxy_presets(items)


def delete_proxy_preset(preset_id: str) -> None:
    save_proxy_presets([p for p in load_proxy_presets() if p.id != preset_id])


def set_archived(preset_id: str, archived: bool) -> None:
    items = load_proxy_presets()
    for i, p in enumerate(items):
        if p.id == preset_id:
            p.is_archived = bool(archived)
            if archived:
                p.approved = False
            items[i] = p
            save_proxy_presets(items)
            return


def bulk_set_archived(preset_ids: list[str], archived: bool) -> int:
    ids = set(preset_ids)
    items = load_proxy_presets()
    n = 0
    for i, p in enumerate(items):
        if p.id in ids and bool(p.is_archived) != bool(archived):
            p.is_archived = bool(archived)
            if archived:
                p.approved = False
            items[i] = p
            n += 1
    if n:
        save_proxy_presets(items)
    return n


def apply_test_result(preset_id: str, result: dict[str, Any]) -> ProxyPreset | None:
    items = load_proxy_presets()
    for i, p in enumerate(items):
        if p.id != preset_id:
            continue
        local_ok = bool(result.get('local_ok'))
        api_ok = bool(result.get('api_ok'))
        p.append_test_history(local_ok, api_ok if local_ok else False)
        p.last_test_at = _now_iso()
        p.last_local_ok = local_ok
        p.last_api_ok = api_ok if local_ok else False
        p.last_test_ok = bool(result.get('ok'))
        p.last_test_latency_ms = result.get('latency_ms')
        p.last_local_latency_ms = result.get('local_latency_ms')
        p.last_api_latency_ms = result.get('api_latency_ms')
        p.last_test_message = str(result.get('message') or '')[:500]
        if not p.last_test_ok:
            p.approved = False
        items[i] = p
        save_proxy_presets(items)
        return p
    return None


def approve_proxy_preset(preset_id: str, name: str) -> ProxyPreset:
    name = (name or '').strip()
    if not name:
        raise ValueError(_('Enter a short name for this approved proxy.'))
    items = load_proxy_presets()
    for i, p in enumerate(items):
        if p.id != preset_id:
            continue
        if p.is_archived:
            raise ValueError(_('Restore this proxy from the Archived shelf before approving it.'))
        if not p.last_test_ok:
            raise ValueError(
                _(
                    'Only proxies that pass both the local and API tests can be approved. '
                    'Run Test first and wait for both steps to go green.'
                )
            )
        for other in items:
            if other.id != p.id and other.approved and other.name.strip().lower() == name.lower():
                raise ValueError(_('Another approved proxy already uses the name “{0}”.').format(name))
        p.name = name
        p.approved = True
        items[i] = p
        save_proxy_presets(items)
        return p
    raise ValueError(_('Proxy not found.'))


def ensure_legacy_proxy_migrated(proxy_url: str, suggested_name: str = '') -> ProxyPreset | None:
    raw = (proxy_url or '').strip()
    if not raw:
        return None
    try:
        normalised = normalize_proxy_url(raw)
    except TransportError:
        return None
    if not normalised:
        return None
    items = load_proxy_presets()
    for p in items:
        if p.proxy_url == normalised:
            if not p.approved:
                p.approved = True
                p.name = p.name.strip() or (suggested_name.strip() or _('Migrated proxy'))
                p.is_archived = False
                p.last_test_ok = True if p.last_test_ok is None else p.last_test_ok
                upsert_proxy_preset(p)
            return p
    name = suggested_name.strip() or _('Migrated proxy')
    preset = ProxyPreset(
        id=uuid4().hex,
        proxy_url=normalised,
        name=name,
        approved=True,
        source='user',
        last_test_ok=True,
        last_local_ok=True,
        last_api_ok=True,
        last_test_message=_('Migrated from a connection’s previous Proxy URL field.'),
        last_test_at=_now_iso(),
    )
    items.append(preset)
    save_proxy_presets(items)
    return preset


def parse_pct_input(raw: Any) -> Optional[float]:
    if raw is None or raw == '':
        return None
    cleaned = str(raw).replace('%', '').replace(',', '.').strip()
    if not cleaned:
        return None
    try:
        n = float(cleaned)
    except ValueError:
        return None
    return max(0.0, min(100.0, n))


def matches_pct_rule(pct: Optional[float], threshold: Optional[float], mode: str = 'min') -> bool:
    if pct is None or threshold is None:
        return False
    if mode == 'exact':
        return abs(float(pct) - float(threshold)) < 0.051
    return float(pct) + 1e-9 >= float(threshold)


def is_failed_entry(preset: ProxyPreset) -> bool:
    color = preset.latest_color()
    return (
        color in ('red', 'orange')
        or preset.last_local_ok is False
        or (preset.last_local_ok is True and preset.last_api_ok is False)
    )


def sort_presets(entries: list[ProxyPreset]) -> list[ProxyPreset]:
    def rank(e: ProxyPreset) -> int:
        color = e.latest_color()
        if color == 'green':
            return 0
        if color == 'orange':
            return 1
        if color == 'red':
            return 2
        return 3

    return sorted(
        entries,
        key=lambda e: (
            rank(e),
            -(e.success_pct() if e.success_pct() is not None else -1),
            e.last_test_latency_ms if e.last_test_latency_ms is not None else 10**9,
            e.proxy_url,
        ),
    )


def filter_presets(
    entries: list[ProxyPreset],
    *,
    search: str = '',
    result_filter: str = 'all',
    rate_filter: str = '',
    rate_custom: str = '',
    source_filter: str = 'all',
    named_only: bool = False,
) -> list[ProxyPreset]:
    """PBE5-style list filters for the Proxies tab."""
    q = (search or '').strip().lower()
    out: list[ProxyPreset] = []
    for entry in entries:
        if named_only and not (entry.approved and entry.name.strip()):
            continue
        if q:
            hay = f'{entry.name} {entry.proxy_url}'.lower()
            if q not in hay:
                continue
        if source_filter != 'all' and (entry.source or 'user') != source_filter:
            continue
        color = entry.latest_color()
        if result_filter == 'untested' and color is not None:
            continue
        if result_filter in ('green', 'orange', 'red') and color != result_filter:
            continue
        if result_filter == 'local-fail' and entry.last_local_ok is not False:
            continue
        if result_filter == 'api-fail' and not (entry.last_local_ok is True and entry.last_api_ok is False):
            continue
        # success rate (skipped for untested filter)
        if result_filter != 'untested' and rate_filter:
            pct = entry.success_pct()
            if rate_filter == '0':
                if not matches_pct_rule(pct, 0, 'exact'):
                    continue
            elif rate_filter == 'custom':
                custom = parse_pct_input(rate_custom)
                if custom is None or not matches_pct_rule(pct, custom, 'min'):
                    continue
            else:
                min_pct = parse_pct_input(rate_filter)
                if min_pct is not None and not matches_pct_rule(pct, min_pct, 'min'):
                    continue
        out.append(entry)
    return sort_presets(out)


def entries_for_test(
    pool: list[ProxyPreset],
    scope: str,
    *,
    visible: list[ProxyPreset] | None = None,
    min_pct: str = '',
    min_pct_custom: str = '',
) -> list[ProxyPreset]:
    """PBE5 Test-library scope selection."""
    all_entries = visible if scope == 'visible' else pool
    min_val = None
    if min_pct == 'custom':
        min_val = parse_pct_input(min_pct_custom)
    elif min_pct not in ('', None):
        min_val = parse_pct_input(min_pct)

    matched: list[ProxyPreset] = []
    for entry in all_entries:
        color = entry.latest_color()
        pct = entry.success_pct()
        if scope == 'untested':
            if color is not None:
                continue
        elif scope in ('green', 'orange', 'red'):
            if color != scope:
                continue
        elif scope == 'local-fail':
            if entry.last_local_ok is not False:
                continue
        elif scope == 'api-fail':
            if not (entry.last_local_ok is True and entry.last_api_ok is False):
                continue
        elif scope not in ('all', 'visible'):
            continue
        if scope != 'untested':
            if min_pct == 'custom' and min_val is None:
                continue
            if min_val is not None and not matches_pct_rule(pct, min_val, 'min'):
                continue
        matched.append(entry)
    return matched
