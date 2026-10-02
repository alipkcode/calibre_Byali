#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint (ported from PBE5 ai_agents/transport.py):
#   1) normalize_dns_server / normalize_proxy_url
#   2) Optional DNS A/AAAA resolve (stdlib)
#   3) urllib opener: proxy path OR DNS-gate path OR plain
#   4) Context manager that temporarily patches calibre.ai.utils.opener

"""Per-connection DNS gate + proxy transport for Byali AI HTTP calls."""

from __future__ import annotations

import http.client
import ipaddress
import random
import socket
import ssl
import struct
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Optional
from urllib.parse import urlparse, urlunparse
from urllib.request import (
    HTTPHandler,
    HTTPSHandler,
    OpenerDirector,
    ProxyHandler,
    Request,
    build_opener,
)

from calibre.constants import __version__
from calibre.utils.localization import _

DNS_QUERY_TIMEOUT = 5.0
_ALLOWED_PROXY_SCHEMES = ('http', 'https', 'socks4', 'socks5', 'socks5h')
_TRANSPORT_LOCK = threading.RLock()


class TransportError(ValueError):
    """User-safe validation / resolution error (never contains secrets)."""


# ---------------------------------------------------------------------------
# Validation / normalisation  (same rules as PBE5)
# ---------------------------------------------------------------------------
def normalize_dns_server(value: Optional[str]) -> Optional[str]:
    """Return ``host`` or ``host:port`` or ``None`` if blank. Raises TransportError."""
    raw = (value or '').strip()
    if not raw:
        return None
    if '://' in raw:
        raise TransportError(_('DNS server must be a host or IP (optionally with :port), not a URL.'))
    host, port = _split_host_port(raw, default_port=53)
    if not host:
        raise TransportError(_('DNS server is empty.'))
    if port < 1 or port > 65535:
        raise TransportError(_('DNS server port must be between 1 and 65535.'))
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not _looks_like_hostname(host):
            raise TransportError(_('DNS server must be an IP address or hostname.')) from None
    return f'{host}:{port}' if port != 53 else host


def normalize_proxy_url(value: Optional[str]) -> Optional[str]:
    """Return a normalised proxy URL or ``None`` if blank. Raises TransportError."""
    raw = (value or '').strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    scheme = (parsed.scheme or '').lower()
    if scheme not in _ALLOWED_PROXY_SCHEMES:
        raise TransportError(
            _('Proxy URL must start with http://, https://, socks4://, socks5://, or socks5h://.')
        )
    if not parsed.hostname:
        raise TransportError(_('Proxy URL is missing a host.'))
    if parsed.port is not None and (parsed.port < 1 or parsed.port > 65535):
        raise TransportError(_('Proxy port must be between 1 and 65535.'))
    netloc = parsed.netloc
    return urlunparse((scheme, netloc, parsed.path or '', '', parsed.query, ''))


def _looks_like_hostname(value: str) -> bool:
    if not value or len(value) > 253:
        return False
    if value.startswith('.') or value.endswith('.'):
        return False
    for label in value.split('.'):
        if not label or len(label) > 63:
            return False
        if label.startswith('-') or label.endswith('-'):
            return False
        for ch in label:
            if not (ch.isalnum() or ch == '-'):
                return False
    return True


def _split_host_port(value: str, default_port: int) -> tuple[str, int]:
    value = value.strip()
    if value.startswith('['):
        if ']:' in value:
            host, port_s = value.rsplit(']:', 1)
            host = host.lstrip('[')
            try:
                return host, int(port_s)
            except ValueError as exc:
                raise TransportError(_('Invalid DNS server port.')) from exc
        if value.endswith(']'):
            return value[1:-1], default_port
        raise TransportError(_('Invalid IPv6 DNS server address.'))
    if value.count(':') == 1:
        host, port_s = value.rsplit(':', 1)
        if port_s.isdigit():
            return host, int(port_s)
    return value, default_port


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Minimal DNS A / AAAA client (stdlib only) — same as PBE5
# ---------------------------------------------------------------------------
def resolve_via_dns(hostname: str, dns_server: str) -> str:
    if _is_ip(hostname):
        return hostname
    server_host, server_port = _split_host_port(dns_server, default_port=53)
    addrs = _dns_lookup(hostname, server_host, server_port, qtype=1)
    if not addrs:
        addrs = _dns_lookup(hostname, server_host, server_port, qtype=28)
    if not addrs:
        raise TransportError(_("DNS gate could not resolve '{0}' via {1}.").format(hostname, dns_server))
    return addrs[0]


def _dns_lookup(hostname: str, server: str, port: int, qtype: int) -> list[str]:
    query = _build_dns_query(hostname, qtype)
    try:
        family = socket.AF_INET6 if (':' in server and not server.startswith(':')) else socket.AF_INET
        if _is_ip(server):
            family = socket.AF_INET6 if ':' in server else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_DGRAM)
        sock.settimeout(DNS_QUERY_TIMEOUT)
        try:
            sock.sendto(query, (server, port))
            data, _ = sock.recvfrom(4096)
        finally:
            sock.close()
    except OSError as exc:
        raise TransportError(_('DNS gate query to {0}:{1} failed: {2}').format(server, port, exc)) from exc
    return _parse_dns_answers(data, qtype)


def _build_dns_query(hostname: str, qtype: int) -> bytes:
    txid = random.randint(0, 65535)
    header = struct.pack('!HHHHHH', txid, 0x0100, 1, 0, 0, 0)
    qname = b''
    for label in hostname.rstrip('.').split('.'):
        encoded = label.encode('idna')
        if len(encoded) > 63:
            raise TransportError(_('Invalid hostname for DNS gate: {}').format(hostname))
        qname += bytes([len(encoded)]) + encoded
    qname += b'\x00'
    return header + qname + struct.pack('!HH', qtype, 1)


def _parse_dns_answers(data: bytes, qtype: int) -> list[str]:
    if len(data) < 12:
        return []
    _txid, flags, qdcount, ancount, _nscount, _arcount = struct.unpack('!HHHHHH', data[:12])
    if (flags & 0x000F) != 0:
        return []
    offset = 12
    for _ in range(qdcount):
        offset = _skip_name(data, offset)
        offset += 4
        if offset > len(data):
            return []
    answers: list[str] = []
    for _ in range(ancount):
        offset = _skip_name(data, offset)
        if offset + 10 > len(data):
            break
        rtype, _rclass, _ttl, rdlength = struct.unpack('!HHIH', data[offset:offset + 10])
        offset += 10
        rdata = data[offset:offset + rdlength]
        offset += rdlength
        if rtype == qtype == 1 and rdlength == 4:
            answers.append(socket.inet_ntop(socket.AF_INET, rdata))
        elif rtype == qtype == 28 and rdlength == 16:
            answers.append(socket.inet_ntop(socket.AF_INET6, rdata))
    return answers


def _skip_name(data: bytes, offset: int) -> int:
    while offset < len(data):
        length = data[offset]
        if length == 0:
            return offset + 1
        if length & 0xC0 == 0xC0:
            return offset + 2
        offset += 1 + length
    return offset


def _rewrite_url_host(url: str, ip: str) -> tuple[str, str, Optional[int]]:
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        raise TransportError(_('Provider URL is missing a host.'))
    port = parsed.port
    host_part = f'[{ip}]' if (_is_ip(ip) and ':' in ip) else ip
    netloc = f'{host_part}:{port}' if port else host_part
    if parsed.username is not None:
        user = parsed.username
        if parsed.password is not None:
            user = f'{user}:{parsed.password}'
        netloc = f'{user}@{netloc}'
    new_url = urlunparse(
        (parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
    )
    return new_url, hostname, port


# ---------------------------------------------------------------------------
# HTTPS handler: connect to IP, SNI/cert check against original hostname
# ---------------------------------------------------------------------------
class _SniHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, port=None, *, server_hostname: str | None = None, **kwargs):
        self._byali_server_hostname = server_hostname
        super().__init__(host, port, **kwargs)

    def connect(self):
        # Mirror http.client.HTTPSConnection.connect, but force SNI/cert host.
        http.client.HTTPConnection.connect(self)
        if self._tunnel_host:
            self._tunnel()
        context = self._context if getattr(self, '_context', None) is not None else ssl.create_default_context()
        if getattr(self, '_check_hostname', None) is False:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        server_hostname = self._byali_server_hostname or self.host
        self.sock = context.wrap_socket(self.sock, server_hostname=server_hostname)


class _SniHTTPSHandler(HTTPSHandler):
    def __init__(self, server_hostname: str, *args, **kwargs):
        self._byali_server_hostname = server_hostname
        super().__init__(*args, **kwargs)

    def https_open(self, req):
        def _connection_class(host, port=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, *, context=None, check_hostname=None):
            kw = {}
            if context is not None:
                kw['context'] = context
            if check_hostname is not None:
                kw['check_hostname'] = check_hostname
            return _SniHTTPSConnection(
                host,
                port,
                timeout=timeout,
                source_address=source_address,
                server_hostname=self._byali_server_hostname,
                **kw,
            )

        return self.do_open(_connection_class, req)


# ---------------------------------------------------------------------------
# Public opener / context
# ---------------------------------------------------------------------------
def opener_for_transport(
    dns_server: Optional[str] = None,
    proxy_url: Optional[str] = None,
    user_agent: str = f'calibre {__version__}',
) -> OpenerDirector:
    """
    Build an urllib opener for this connection's networking.

    Blank dns + proxy → same as calibre.ai.utils.opener (system/env proxies).
    Proxy set → use that proxy only (ignore env proxies), matching PBE5 trust_env=False.
    DNS set (no proxy) → resolve via that nameserver, preserve Host/SNI.
    """
    from calibre import get_proxies

    dns = normalize_dns_server(dns_server) if dns_server else None
    proxy = normalize_proxy_url(proxy_url) if proxy_url else None

    if not dns and not proxy:
        proxy_handler = ProxyHandler(get_proxies(debug=False))
        ans = build_opener(proxy_handler)
        ans.addheaders = [('User-agent', user_agent)]
        return ans

    if proxy:
        scheme = proxy.lower().split('://', 1)[0]
        if scheme in ('socks4', 'socks5', 'socks5h'):
            raise TransportError(
                _(
                    'SOCKS proxy support requires the PySocks package. '
                    'Use an HTTP or HTTPS proxy URL (e.g. http://host:8080), '
                    'or install PySocks into Calibre’s Python environment.'
                )
            )
        # urllib ProxyHandler: both schemes go through the same proxy URL.
        proxy_handler = ProxyHandler({'http': proxy, 'https': proxy})
        ans = build_opener(proxy_handler, HTTPHandler(), HTTPSHandler())
        ans.addheaders = [('User-agent', user_agent)]
        return ans

    # Direct + custom DNS — return a plain opener; URL rewrite happens in open wrapper.
    # Callers should use transport_open() or the patched opener from use_connection_transport.
    proxy_handler = ProxyHandler({})  # disable env proxies on DNS-gate path (PBE5 trust_env=False)
    ans = build_opener(proxy_handler)
    ans.addheaders = [('User-agent', user_agent)]
    # Attach metadata for the wrapping open()
    ans._byali_dns_server = dns  # type: ignore[attr-defined]
    return ans


def _dns_aware_open(base_opener: OpenerDirector, dns_server: str, fullurl, data=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
    req = fullurl if isinstance(fullurl, Request) else Request(fullurl, data=data)
    url = req.full_url
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        raise TransportError(_('Provider URL is missing a host.'))
    if _is_ip(hostname):
        return base_opener.open(req, data if not isinstance(fullurl, Request) else None, timeout)

    ip = resolve_via_dns(hostname, dns_server)
    new_url, original_host, port = _rewrite_url_host(url, ip)

    headers = dict(req.header_items())
    if not any(k.lower() == 'host' for k in headers):
        if port and not (
            (parsed.scheme == 'https' and port == 443)
            or (parsed.scheme == 'http' and port == 80)
        ):
            headers['Host'] = f'{original_host}:{port}'
        else:
            headers['Host'] = original_host

    new_req = Request(
        new_url,
        data=req.data,
        headers=headers,
        origin_req_host=req.origin_req_host,
        unverifiable=req.unverifiable,
        method=req.get_method(),
    )
    # Rebuild opener with SNI handler for HTTPS
    from calibre import get_proxies  # noqa: F401 — keep import style consistent

    handlers: list = [ProxyHandler({})]
    if (parsed.scheme or '').lower() == 'https':
        handlers.append(_SniHTTPSHandler(original_host))
    else:
        handlers.append(HTTPHandler())
    o = build_opener(*handlers)
    o.addheaders = list(base_opener.addheaders)
    return o.open(new_req, timeout=timeout)


class _TransportOpener:
    """OpenerDirector-compatible wrapper that applies DNS rewrite when needed."""

    def __init__(self, dns_server: Optional[str], proxy_url: Optional[str], user_agent: str):
        self._dns = normalize_dns_server(dns_server) if dns_server else None
        self._proxy = normalize_proxy_url(proxy_url) if proxy_url else None
        self._inner = opener_for_transport(None if self._dns and not self._proxy else dns_server, proxy_url, user_agent)
        self.addheaders = list(self._inner.addheaders)

    def open(self, fullurl, data=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
        if self._dns and not self._proxy:
            return _dns_aware_open(self._inner, self._dns, fullurl, data=data, timeout=timeout)
        return self._inner.open(fullurl, data, timeout)

    def add_handler(self, handler):
        return self._inner.add_handler(handler)


@contextmanager
def use_connection_transport(
    dns_server: Optional[str] = None,
    proxy_url: Optional[str] = None,
) -> Iterator[None]:
    """
    Temporarily route calibre.ai.utils.opener through this connection's DNS/proxy.

    Blank both → no patch (historical env-proxy behaviour unchanged).
    """
    dns_raw = (dns_server or '').strip()
    proxy_raw = (proxy_url or '').strip()
    if not dns_raw and not proxy_raw:
        yield
        return

    # Validate early so UI gets clear errors
    if dns_raw:
        normalize_dns_server(dns_raw)
    if proxy_raw:
        normalize_proxy_url(proxy_raw)

    import calibre.ai.utils as ai_utils

    def _opener(user_agent: str = f'calibre {__version__}') -> _TransportOpener:
        return _TransportOpener(dns_raw or None, proxy_raw or None, user_agent)

    with _TRANSPORT_LOCK:
        previous = ai_utils.opener
        ai_utils.opener = _opener  # type: ignore[assignment]
        try:
            yield
        finally:
            ai_utils.opener = previous
