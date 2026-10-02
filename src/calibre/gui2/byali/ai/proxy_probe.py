#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint (PBE5 network_library probe — proxy half, urllib edition):
#   1) probe_local_proxy — TCP reachability of the proxy host:port
#   2) probe_api_via_proxy — GET /models (or target) through that proxy
#   3) test_proxy — two-step combine + green/orange/red semantics

"""Two-step proxy probe matching PBE5 AI Connections library tests."""

from __future__ import annotations

import socket
import time
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request

from calibre.gui2.byali.ai.transport import (
    TransportError,
    normalize_proxy_url,
    use_connection_transport,
)
from calibre.utils.localization import _

LOCAL_PROBE_TIMEOUT = 4.0
PROBE_CONNECT_TIMEOUT = 6
PROBE_READ_TIMEOUT = 12


def network_attempt_color(local_ok: Optional[bool], api_ok: Optional[bool]) -> str:
    """Green = both passed; orange = exactly one; red = both failed / untested local fail."""
    local_ok = bool(local_ok)
    api_ok = bool(api_ok)
    if local_ok and api_ok:
        return 'green'
    if local_ok or api_ok:
        return 'orange'
    return 'red'


def probe_local_proxy(proxy_url: str) -> tuple[bool, Optional[int], str]:
    """Step 1 (PBE5): can we TCP-connect to the proxy itself?"""
    try:
        normalised = normalize_proxy_url(proxy_url)
    except TransportError as exc:
        return False, None, str(exc)
    parsed = urlparse(normalised or '')
    host = parsed.hostname
    port = parsed.port
    if not host:
        return False, None, _('Proxy URL is missing a host.')
    if port is None:
        scheme = (parsed.scheme or 'http').lower()
        port = 1080 if scheme.startswith('socks') else (443 if scheme == 'https' else 80)

    started = time.monotonic()
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not infos:
            return False, None, _('Could not resolve proxy host {0}').format(host)
        family, socktype, proto, _canon, sockaddr = infos[0]
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(LOCAL_PROBE_TIMEOUT)
        try:
            sock.connect(sockaddr)
        finally:
            sock.close()
        ms = int((time.monotonic() - started) * 1000)
        return True, ms, _('TCP connected to {0}:{1}').format(host, port)
    except OSError as exc:
        return False, None, _('Cannot reach proxy {0}:{1} — {2}').format(host, port, exc)
    except Exception as exc:  # noqa: BLE001
        return False, None, _('Proxy probe failed: {0}').format(exc)


def probe_api_via_proxy(
    proxy_url: str,
    target_url: str,
    headers: list[tuple[str, str]] | None = None,
) -> tuple[bool, Optional[int], str]:
    """Step 2 (PBE5): authenticated (or bare) HTTP GET of target through the proxy."""
    target = (target_url or '').strip()
    if not target:
        return False, None, _('No API probe URL configured.')

    hdrs = list(headers or [])
    has_auth = any(k.lower() in ('authorization', 'x-api-key', 'api-key') for k, _ in hdrs)
    started = time.monotonic()
    try:
        with use_connection_transport('', proxy_url):
            from calibre.ai.utils import opener

            req = Request(target, headers=dict(hdrs), method='GET')
            o = opener()
            with o.open(req, timeout=PROBE_READ_TIMEOUT) as resp:
                status = getattr(resp, 'status', None) or resp.getcode()
                # Drain a little so the connection completes cleanly
                resp.read(65536)
        ms = int((time.monotonic() - started) * 1000)
        host = urlparse(target).hostname or target
        if 200 <= int(status) < 300:
            return True, ms, _('API auth OK at {0} (HTTP {1})').format(host, status)
        if int(status) in (401, 403):
            if not has_auth:
                return (
                    False,
                    ms,
                    _(
                        'API reached {0} (HTTP {1}) but no API key was provided for the probe. '
                        'Pick an LLM connection that has a key, then test again.'
                    ).format(host, status),
                )
            return (
                False,
                ms,
                _(
                    'Authentication failed through this path (HTTP {0}). '
                    'The proxy reached a host, but the API key was rejected.'
                ).format(status),
            )
        if int(status) == 404:
            return False, ms, _('API endpoint not found at {0} (HTTP 404). Check the base URL.').format(host)
        return False, ms, _('API returned HTTP {0} from {1}').format(status, host)
    except TransportError as exc:
        return False, None, str(exc)
    except HTTPError as exc:
        ms = int((time.monotonic() - started) * 1000)
        host = urlparse(target).hostname or target
        code = exc.code
        if code in (401, 403):
            return (
                False,
                ms,
                _(
                    'Authentication failed through this path (HTTP {0}). '
                    'The proxy reached a host, but the API key was rejected.'
                ).format(code),
            )
        if code == 404:
            return False, ms, _('API endpoint not found at {0} (HTTP 404). Check the base URL.').format(host)
        return False, ms, _('API returned HTTP {0} from {1}').format(code, host)
    except URLError as exc:
        return False, None, _('API connection failed: {0}').format(exc.reason if hasattr(exc, 'reason') else exc)
    except TimeoutError:
        return False, None, _('API request timed out')
    except Exception as exc:  # noqa: BLE001
        return False, None, _('API probe failed: {0}').format(exc)


def models_probe_url(api_url: str) -> str:
    """Build an OpenAI-compatible /models URL from a connection base URL."""
    base = (api_url or '').strip().rstrip('/')
    if not base:
        return 'https://api.openai.com/v1/models'
    if base.endswith('/models'):
        return base
    if base.endswith('/chat/completions'):
        base = base[: -len('/chat/completions')]
    return f'{base}/models'


def test_proxy(
    proxy_url: str,
    *,
    probe_api_url: str = '',
    probe_headers: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """
    Two-step probe identical in spirit to PBE5 ``test_library_entry`` for proxies.

    Returns dict with ok, local_ok, api_ok, latencies, messages, color.
    """
    local_ok, local_ms, local_msg = probe_local_proxy(proxy_url)
    api_ok, api_ms, api_msg = False, None, _('Skipped — local probe failed')
    if local_ok:
        target = models_probe_url(probe_api_url) if probe_api_url else 'https://api.openai.com/v1/models'
        api_ok, api_ms, api_msg = probe_api_via_proxy(proxy_url, target, probe_headers)

    both_ok = bool(local_ok and api_ok)
    if both_ok:
        message = _('OK — local and API both green. {0}').format(api_msg)
    elif local_ok and not api_ok:
        message = _('Local OK, API failed. {0}').format(api_msg)
    else:
        message = _('Local failed. {0}').format(local_msg)

    display_latency = api_ms if both_ok else (local_ms if local_ok else None)
    return {
        'ok': both_ok,
        'local_ok': local_ok,
        'local_latency_ms': local_ms,
        'local_message': local_msg,
        'api_ok': api_ok if local_ok else False,
        'api_latency_ms': api_ms,
        'api_message': api_msg,
        'latency_ms': display_latency,
        'message': message,
        'color': network_attempt_color(local_ok, api_ok if local_ok else False),
    }
