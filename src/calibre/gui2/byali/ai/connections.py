#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint:
#   1) Connection dataclass (named OpenAI-compatible endpoints)
#   2) CRUD in byali_ai JSONConfig (additive)
#   3) fetch_models() via provider API
#   4) test_connection() with clear error reporting

"""Named LLM connections for Byali (multi-endpoint, API-loaded models)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4

from calibre.ai.prefs import decode_secret, encode_secret
from calibre.gui2.byali.ai.agents import prefs
from calibre.utils.localization import _


@dataclass
class Connection:
    id: str
    name: str
    api_url: str = ''
    api_key: str = ''  # plaintext in memory; encoded at rest
    headers: list[list[str]] = field(default_factory=list)  # [[name, value], ...]
    timeout: int = 120
    cached_models: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'name': self.name,
            'api_url': self.api_url,
            'api_key': encode_secret(self.api_key) if self.api_key else '',
            'headers': [list(h) for h in self.headers if len(h) == 2],
            'timeout': int(self.timeout or 120),
            'cached_models': list(self.cached_models or []),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Connection:
        key = data.get('api_key') or ''
        try:
            plain = decode_secret(key) if key else ''
        except Exception:
            plain = key  # tolerate accidental plaintext
        headers = []
        for item in data.get('headers') or []:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                headers.append([str(item[0]), str(item[1])])
        return cls(
            id=str(data.get('id') or uuid4().hex),
            name=str(data.get('name') or _('Unnamed connection')),
            api_url=str(data.get('api_url') or ''),
            api_key=plain,
            headers=headers,
            timeout=int(data.get('timeout') or 120),
            cached_models=[str(x) for x in (data.get('cached_models') or [])],
        )

    def headers_tuples(self) -> tuple[tuple[str, str], ...]:
        return tuple((a, b) for a, b in self.headers if a and b)

    def provider_prefs(self, model: str = '', temperature: float | None = None) -> dict[str, Any]:
        """Prefs map for override_prefs_for_providers(OpenAI compatible)."""
        ans: dict[str, Any] = {
            'api_url': self.api_url.strip(),
            'api_key': encode_secret(self.api_key) if self.api_key else '',
            'text_model': model or (self.cached_models[0] if self.cached_models else ''),
            'timeout': int(self.timeout or 120),
        }
        if self.headers_tuples():
            ans['headers'] = self.headers_tuples()
        if temperature is not None:
            ans['temperature'] = float(temperature)
        return ans


def default_connection() -> Connection:
    return Connection(
        id='byali-conn-default',
        name=_('Default OpenAI-compatible'),
        api_url='https://api.openai.com/v1',
    )


def load_connections() -> list[Connection]:
    raw = prefs().get('connections')
    if not raw:
        conn = default_connection()
        save_connections([conn])
        return [conn]
    return [Connection.from_dict(x) for x in raw if isinstance(x, dict)]


def save_connections(connections: list[Connection]) -> None:
    prefs().set('connections', [c.to_dict() for c in connections])


def upsert_connection(connection: Connection) -> None:
    items = load_connections()
    for i, existing in enumerate(items):
        if existing.id == connection.id:
            items[i] = connection
            break
    else:
        items.append(connection)
    save_connections(items)


def delete_connection(connection_id: str) -> None:
    items = [c for c in load_connections() if c.id != connection_id]
    if not items:
        items = [default_connection()]
    save_connections(items)


def get_connection(connection_id: str) -> Connection | None:
    for c in load_connections():
        if c.id == connection_id:
            return c
    items = load_connections()
    return items[0] if items else None


def new_connection_template() -> Connection:
    return Connection(id=uuid4().hex, name=_('New connection'), api_url='https://api.openai.com/v1')


def fetch_models(connection: Connection) -> list[str]:
    """Load model IDs from the connection's /models API. Raises on failure."""
    from calibre.ai.openai_compatible import OpenAICompatible
    from calibre.ai.utils import plugin_for_name

    if not connection.api_url.strip():
        raise ValueError(_('API URL is required.'))
    plugin = plugin_for_name(OpenAICompatible.name)
    backend = plugin.builtin_live_module
    backend.get_available_models.cache_clear()
    encoded_key = encode_secret(connection.api_key) if connection.api_key else ''
    models_dict = backend.get_available_models(connection.api_url.strip(), encoded_key, connection.headers_tuples())
    model_ids = sorted(models_dict, key=lambda x: x.lower())
    connection.cached_models = model_ids
    upsert_connection(connection)
    return model_ids


def test_connection(connection: Connection, model: str = '') -> str:
    """
    Verify the connection: list models, then optionally send a tiny chat.
    Returns a human-readable success message. Raises ValueError/Exception on failure.
    """
    from calibre.ai import ChatMessage
    from calibre.ai.openai_compatible import OpenAICompatible
    from calibre.ai.prefs import override_prefs_for_providers
    from calibre.ai.utils import plugin_for_name

    models = fetch_models(connection)
    if not models:
        raise ValueError(_('The server responded, but returned no models.'))

    use_model = (model or '').strip() or models[0]
    plugin = plugin_for_name(OpenAICompatible.name)
    overrides = {OpenAICompatible.name: connection.provider_prefs(model=use_model, temperature=0.0)}
    chunks: list[str] = []
    with override_prefs_for_providers(overrides):
        for res in plugin.text_chat((ChatMessage('Reply with exactly the word OK and nothing else.'),), use_model):
            if res.exception is not None:
                raise res.exception
            if res.content:
                chunks.append(res.content)
    reply = ''.join(chunks).strip()
    sample = ', '.join(models[:3])
    extra = _(' (and more)') if len(models) > 3 else ''
    ok_note = _(' Chat test reply: {0}').format(reply[:80]) if reply else ''
    return _('Connection OK. Found {0} models, e.g. {1}{2}.{3}').format(len(models), sample, extra, ok_note)
