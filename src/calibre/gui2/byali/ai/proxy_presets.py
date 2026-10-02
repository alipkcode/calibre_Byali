#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint:
#   1) ProxyPreset dataclass (URL + optional approved name + last probe)
#   2) CRUD in byali_ai JSONConfig (additive key: proxy_presets)
#   3) list_approved() for LLM connection picker
#   4) apply_test_result() / approve_preset(name)

"""Named/approved proxy presets for Byali AI connections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from calibre.gui2.byali.ai.agents import prefs
from calibre.gui2.byali.ai.transport import TransportError, normalize_proxy_url
from calibre.utils.localization import _


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class ProxyPreset:
    id: str
    proxy_url: str
    name: str = ''  # user-facing shortcut / preset name (set when approved)
    approved: bool = False
    last_test_at: str = ''
    last_local_ok: Optional[bool] = None
    last_api_ok: Optional[bool] = None
    last_test_ok: Optional[bool] = None
    last_test_latency_ms: Optional[int] = None
    last_test_message: str = ''
    last_local_latency_ms: Optional[int] = None
    last_api_latency_ms: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'proxy_url': self.proxy_url,
            'name': self.name or '',
            'approved': bool(self.approved),
            'last_test_at': self.last_test_at or '',
            'last_local_ok': self.last_local_ok,
            'last_api_ok': self.last_api_ok,
            'last_test_ok': self.last_test_ok,
            'last_test_latency_ms': self.last_test_latency_ms,
            'last_test_message': self.last_test_message or '',
            'last_local_latency_ms': self.last_local_latency_ms,
            'last_api_latency_ms': self.last_api_latency_ms,
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

        return cls(
            id=str(data.get('id') or uuid4().hex),
            proxy_url=str(data.get('proxy_url') or ''),
            name=str(data.get('name') or ''),
            approved=bool(data.get('approved', False)),
            last_test_at=str(data.get('last_test_at') or ''),
            last_local_ok=_opt_bool('last_local_ok'),
            last_api_ok=_opt_bool('last_api_ok'),
            last_test_ok=_opt_bool('last_test_ok'),
            last_test_latency_ms=_opt_int('last_test_latency_ms'),
            last_test_message=str(data.get('last_test_message') or ''),
            last_local_latency_ms=_opt_int('last_local_latency_ms'),
            last_api_latency_ms=_opt_int('last_api_latency_ms'),
        )

    def display_label(self) -> str:
        if self.name.strip():
            return self.name.strip()
        return self.proxy_url


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


def list_approved_proxy_presets() -> list[ProxyPreset]:
    """Presets with an approved shortcut name — shown in LLM connection picker."""
    return [p for p in load_proxy_presets() if p.approved and p.name.strip() and p.proxy_url.strip()]


def add_proxy_candidate(proxy_url: str) -> ProxyPreset:
    """Add a proxy URL to test (not approved yet). Dedupes by normalised URL."""
    normalised = normalize_proxy_url(proxy_url)
    if not normalised:
        raise TransportError(_('Proxy URL is empty.'))
    items = load_proxy_presets()
    for existing in items:
        if existing.proxy_url == normalised:
            return existing
    preset = ProxyPreset(id=uuid4().hex, proxy_url=normalised, approved=False)
    items.append(preset)
    save_proxy_presets(items)
    return preset


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


def apply_test_result(preset_id: str, result: dict[str, Any]) -> ProxyPreset | None:
    """Persist PBE5-style probe fields onto the preset. Returns updated preset."""
    items = load_proxy_presets()
    for i, p in enumerate(items):
        if p.id != preset_id:
            continue
        p.last_test_at = _now_iso()
        p.last_local_ok = bool(result.get('local_ok'))
        p.last_api_ok = bool(result.get('api_ok'))
        p.last_test_ok = bool(result.get('ok'))
        p.last_test_latency_ms = result.get('latency_ms')
        p.last_local_latency_ms = result.get('local_latency_ms')
        p.last_api_latency_ms = result.get('api_latency_ms')
        p.last_test_message = str(result.get('message') or '')[:500]
        # Failing the probe un-approves so connections never silently use a dead proxy
        if not p.last_test_ok:
            p.approved = False
        items[i] = p
        save_proxy_presets(items)
        return p
    return None


def approve_proxy_preset(preset_id: str, name: str) -> ProxyPreset:
    """
    Mark a preset as approved with a shortcut name.

    Requires last probe to have both local + API green (PBE5 both-ok rule).
    """
    name = (name or '').strip()
    if not name:
        raise ValueError(_('Enter a short name for this approved proxy.'))
    items = load_proxy_presets()
    for i, p in enumerate(items):
        if p.id != preset_id:
            continue
        if not p.last_test_ok:
            raise ValueError(
                _(
                    'Only proxies that pass both the local and API tests can be approved. '
                    'Run Test first and wait for both steps to go green.'
                )
            )
        # Unique name among approved
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
    """
    If a connection still stores a raw proxy_url, create/reuse an approved preset.

    Additive migration — does not delete the legacy field.
    """
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
                p.last_test_ok = True if p.last_test_ok is None else p.last_test_ok
                upsert_proxy_preset(p)
            return p
    name = suggested_name.strip() or _('Migrated proxy')
    preset = ProxyPreset(
        id=uuid4().hex,
        proxy_url=normalised,
        name=name,
        approved=True,
        last_test_ok=True,
        last_local_ok=True,
        last_api_ok=True,
        last_test_message=_('Migrated from a connection’s previous Proxy URL field.'),
        last_test_at=_now_iso(),
    )
    items.append(preset)
    save_proxy_presets(items)
    return preset
