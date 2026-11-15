"""Bounded image fetching: exact host allowlist, public DNS and pinned connections."""
from __future__ import annotations

import asyncio
import http.client
import ipaddress
import os
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit

MAX_BYTES = 5 * 1024 * 1024
IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/avif', 'image/bmp', 'image/x-icon'}


class UnsafeImageURL(ValueError):
    pass


def allowed_hosts():
    hosts = {'image.tmdb.org', 'cdn.myanimelist.net', 'i.imgur.com',
             'voiranime.cx', 'voir-anime.to', 'voirdrama.to', 'voirdrama.org'}
    from scraper.coflix_client import SOURCE_URL as coflix
    from scraper.voirdrama_client import SOURCE_URL as drama
    from scraper.voiranime_client import SOURCE_URL as anime
    from scraper.frenchstream_client import SOURCE_URL as frenchstream
    for value in [coflix, drama, anime, frenchstream]:
        host = urlsplit(value).hostname
        if host:
            hosts.add(host.lower().rstrip('.'))
    # Server-admin-controlled extension, never derived from request parameters.
    hosts.update(x.strip().lower() for x in os.getenv('IMAGE_PROXY_ALLOWED_HOSTS', '').split(',') if x.strip())
    return hosts


def validate_url(url):
    try:
        p = urlsplit(url)
        if len(url) > 4096 or any(ord(c) < 33 or ord(c) == 127 for c in url) or '\\' in url:
            raise ValueError()
        if p.scheme not in ('http', 'https') or not p.hostname or p.username is not None or p.password is not None:
            raise ValueError()
        if p.hostname.lower() not in allowed_hosts() or p.fragment:
            raise ValueError()
        if p.port not in (None, 443 if p.scheme == 'https' else 80):
            raise ValueError()
        return p
    except (ValueError, UnicodeError):
        raise UnsafeImageURL('Image destination not allowed') from None


def public_addresses(host, port):
    addresses = list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
    if not addresses:
        raise UnsafeImageURL('Image destination unavailable')
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global or (getattr(ip, 'ipv4_mapped', None) and not ip.ipv4_mapped.is_global):
            raise UnsafeImageURL('Image destination not public')
    return addresses


class PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, ip, port, secure):
        super().__init__(host, port, timeout=5)
        self.ip, self.secure = ip, secure

    def connect(self):
        # Connect to the validated numeric IP, not to a second DNS lookup.
        self.sock = socket.create_connection((self.ip, self.port), self.timeout)
        if self.secure:
            self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=self.host)


def fetch_image_sync(url, referer):
    deadline = time.monotonic() + 12
    for redirect in range(4):
        if time.monotonic() >= deadline:
            raise TimeoutError("Image deadline")
        p = validate_url(url)
        port = p.port or (443 if p.scheme == 'https' else 80)
        addresses = public_addresses(p.hostname, port)
        conn = PinnedHTTPConnection(p.hostname, addresses[0], port, p.scheme == 'https')
        try:
            path = (p.path or '/') + ('?' + p.query if p.query else '')
            conn.request('GET', path, headers={'Referer': referer, 'User-Agent': 'Mozilla/5.0',
                         'Accept': 'image/avif,image/webp,image/png,image/jpeg,image/gif', 'Accept-Encoding': 'identity'})
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                target = response.getheader('Location')
                if not target or redirect == 3:
                    raise ValueError('Image redirect limit')
                url = urljoin(url, target)
                continue
            mime = response.getheader('Content-Type', '').split(';')[0].strip().lower()
            if response.status != 200 or mime not in IMAGE_TYPES:
                raise ValueError('Invalid image response')
            if int(response.getheader('Content-Length', '0')) > MAX_BYTES:
                raise ValueError('Image too large')
            chunks, size = [], 0
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Image deadline')
                chunk = response.read1(min(65536, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError('Image too large')
            body = b''.join(chunks)
            if not body or len(body) > MAX_BYTES:
                raise ValueError('Invalid image size')
            return body, mime
        finally:
            conn.close()
    raise ValueError('Image redirect limit')


async def fetch_image(url, referer):
    # Constrain total response latency as well as individual socket operations.
    return await asyncio.wait_for(asyncio.to_thread(fetch_image_sync, url, referer), timeout=12)
