#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint (port of PBE5 provider_settings.js paste parser):
#   1) Token / host / port validation
#   2) extract_proxies_from_text — scheme://, host:port, wrapped tables
#   3) format with fallback scheme; dedupe against library

"""Clipboard / paste-list proxy extraction matching PBE5 AI Connections."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from calibre.gui2.byali.ai.proxy_presets import BATCH_PROXY_LIMIT, load_proxy_presets

PASTE_SCHEMES = {'http', 'https', 'socks4', 'socks5', 'socks5h'}
PASTE_SCHEME_ORDER = ('socks5h', 'socks5', 'socks4', 'https', 'http')
PASTE_TIME_WORD = re.compile(r'^(ms|s|sec|secs|second|seconds|m|min|mins|minute|minutes)$', re.I)


@dataclass
class PasteProxy:
    scheme: str
    host: str
    port: int
    auth: str = ''
    netloc: str = ''
    raw: str = ''


def is_ipv4_host(host: str) -> bool:
    m = re.match(r'^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$', host or '')
    if not m:
        return False
    return all(0 <= int(o) <= 255 for o in m.groups())


def is_ipv6_host(host: str) -> bool:
    return bool(host) and ':' in host and re.match(r'^[0-9a-f:]+$', host, re.I)


def is_proxy_hostname(host: str) -> bool:
    if not host or len(host) > 253:
        return False
    if host.startswith('.') or host.endswith('.'):
        return False
    for label in host.split('.'):
        if not label or len(label) > 63:
            return False
        if label.startswith('-') or label.endswith('-'):
            return False
        if not re.match(r'^[a-z0-9-]+$', label, re.I):
            return False
    return True


def is_valid_proxy_port(port) -> bool:
    try:
        p = int(port)
    except (TypeError, ValueError):
        return False
    return 1 <= p <= 65535


def strip_proxy_token(token: str) -> str:
    s = str(token or '').strip()
    s = re.sub(r'^[\s<("\'\[{]+', '', s)
    s = re.sub(r'[\s>)"\'\]},.;]+$', '', s)
    return s


def paste_tokenize(text: str) -> list[str]:
    return [t for t in (strip_proxy_token(x) for x in re.split(r'[\s,;|]+', str(text or ''))) if t]


def schemes_in_text(text: str) -> list[str]:
    found = []
    for m in re.finditer(r'\b(socks5h|socks5|socks4a|socks4|https|http)\b', str(text or ''), re.I):
        scheme = m.group(1).lower()
        if scheme == 'socks4a':
            scheme = 'socks4'
        if scheme in PASTE_SCHEMES and scheme not in found:
            found.append(scheme)
    return found


def pick_paste_scheme(schemes: list[str]) -> str:
    for s in PASTE_SCHEME_ORDER:
        if s in schemes:
            return s
    return ''


def ipv4s_in_text(text: str) -> list[dict]:
    out = []
    for m in re.finditer(r'\b\d{1,3}(?:\.\d{1,3}){3}\b', str(text or '')):
        if is_ipv4_host(m.group(0)):
            out.append({'host': m.group(0), 'index': m.start()})
    return out


def port_from_tokens_after(tokens: list[str], start_index: int = 0) -> Optional[int]:
    for i in range(start_index, len(tokens)):
        tok = tokens[i]
        if not re.match(r'^\d{1,5}$', tok):
            continue
        port = int(tok)
        if not is_valid_proxy_port(port):
            continue
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ''
        if PASTE_TIME_WORD.match(nxt or ''):
            continue
        return port
    return None


def make_paste_proxy(scheme: str, host: str, port: int, auth: str = '') -> PasteProxy:
    host = str(host or '').lstrip('[').rstrip(']').lower()
    netloc = f'{auth + "@" if auth else ""}{host}:{port}'
    return PasteProxy(scheme=scheme or '', host=host, port=port, auth=auth or '', netloc=netloc, raw=netloc)


def parse_proxy_token(token: str) -> Optional[PasteProxy]:
    token = strip_proxy_token(token)
    if not token or len(token) > 255 or re.search(r'\s', token):
        return None

    scheme = ''
    rest = token
    m = re.match(r'^(https?|socks5h?|socks4a?):\/\/', token, re.I)
    if m:
        scheme = m.group(1).lower()
        if scheme == 'socks4a':
            scheme = 'socks4'
        if scheme not in PASTE_SCHEMES:
            return None
        rest = token[m.end():]

    auth = ''
    at = rest.rfind('@')
    if at != -1:
        auth = rest[:at]
        rest = rest[at + 1:]
        if not auth or not rest:
            return None

    rest = rest.split('/')[0].split('?')[0]
    if not rest:
        return None

    host = ''
    port = None
    if rest.startswith('['):
        close = rest.find(']')
        if close < 2:
            return None
        host = rest[1:close]
        rest = rest[close + 1:]
        if rest.startswith(':'):
            after = rest[1:].split(':')
            if not re.match(r'^\d+$', after[0] or ''):
                return None
            port = int(after[0])
            if not auth and len(after) >= 3:
                auth = ':'.join(after[1:])
    else:
        parts = rest.split(':')
        if scheme:
            host = parts[0] or ''
            if len(parts) >= 2 and re.match(r'^\d+$', parts[1]):
                port = int(parts[1])
        elif len(parts) == 2:
            host = parts[0]
            if not re.match(r'^\d+$', parts[1]):
                return None
            port = int(parts[1])
        elif len(parts) >= 4 and is_ipv4_host(parts[0]) and re.match(r'^\d+$', parts[1]):
            host = parts[0]
            port = int(parts[1])
            if not auth:
                auth = ':'.join(parts[2:])
        else:
            return None

    host = (host or '').lstrip('[').rstrip(']').lower()
    if not host or not is_valid_proxy_port(port):
        return None
    if not (is_ipv4_host(host) or is_ipv6_host(host) or is_proxy_hostname(host)):
        return None
    if not scheme and not is_ipv4_host(host) and not is_ipv6_host(host) and not re.search(r'[a-z]', host, re.I):
        return None

    netloc = f'{auth + "@" if auth else ""}{host}:{port}'
    return PasteProxy(scheme=scheme or '', host=host, port=int(port), auth=auth, netloc=netloc, raw=token)


def extract_proxies_from_text(text: str) -> tuple[list[PasteProxy], bool]:
    """Return (proxies, truncated) — same extraction rules as PBE5."""
    items: list[PasteProxy] = []
    by_netloc: dict[str, PasteProxy] = {}
    truncated = False
    raw = str(text or '')

    def remember(parsed: Optional[PasteProxy]):
        nonlocal truncated
        if not parsed or not parsed.host or not is_valid_proxy_port(parsed.port):
            return
        if not parsed.scheme and parsed.port == 53:
            return  # DNS-only; Proxies tab ignores DNS paste
        loc = parsed.netloc
        prev = by_netloc.get(loc)
        if prev is not None:
            if not prev.scheme and parsed.scheme:
                prev.scheme = parsed.scheme
            return
        if len(items) >= BATCH_PROXY_LIMIT:
            truncated = True
            return
        by_netloc[loc] = parsed
        items.append(parsed)

    for token in paste_tokenize(raw):
        remember(parse_proxy_token(token))

    lines = raw.splitlines()
    i = 0
    while i < len(lines):
        ips = ipv4s_in_text(lines[i])
        if not ips:
            i += 1
            continue
        end = i + 1
        while end < len(lines) and not ipv4s_in_text(lines[end]):
            end += 1
        block = ' '.join(lines[i:end])
        scheme = pick_paste_scheme(schemes_in_text(block))
        for hit in ips:
            after = block[hit['index'] + len(hit['host']):]
            tokens = paste_tokenize(after)
            port = port_from_tokens_after(tokens, 0)
            if not port:
                continue
            remember(make_paste_proxy(scheme, hit['host'], port, ''))
        i = end

    return items, truncated


def format_paste_proxy(item: PasteProxy, fallback_scheme: str) -> str:
    scheme = item.scheme or fallback_scheme
    if not scheme:
        return ''
    return f'{scheme}://{item.netloc}'


def library_dedupe_index() -> dict:
    proxy_exact = {}
    proxy_host_port = {}
    for entry in load_proxy_presets():
        val = (entry.proxy_url or '').strip().lower()
        if val:
            proxy_exact[val] = True
        parsed = parse_proxy_token(entry.proxy_url or '')
        if parsed and parsed.host and parsed.port:
            proxy_host_port[f'{parsed.host}:{parsed.port}'] = True
    return {'proxy_exact': proxy_exact, 'proxy_host_port': proxy_host_port}


def is_paste_duplicate(item: PasteProxy, url: str, index: dict) -> bool:
    if url and index['proxy_exact'].get(str(url).lower()):
        return True
    if item and item.host and item.port and index['proxy_host_port'].get(f'{item.host}:{item.port}'):
        return True
    return False


def formatted_ready_urls(
    items: list[PasteProxy],
    scheme: str,
    *,
    skip_dupes: bool = True,
) -> tuple[list[str], int]:
    """Return (urls ready to add, duplicate_count among ready candidates)."""
    index = library_dedupe_index() if skip_dupes else {'proxy_exact': {}, 'proxy_host_port': {}}
    urls = []
    dupes = 0
    seen = set()
    for item in items:
        url = format_paste_proxy(item, scheme)
        if not url:
            continue
        if skip_dupes and is_paste_duplicate(item, url, index):
            dupes += 1
            continue
        key = url.lower()
        if key in seen:
            dupes += 1
            continue
        seen.add(key)
        urls.append(url)
    return urls, dupes
