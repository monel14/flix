"""No live requests to private services; upstream sockets and DNS are mocked."""
import builtins
import io
import os
from pathlib import Path
import runpy
import socket
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
import api_app
from services import image_proxy as images


@pytest.mark.parametrize('url', [
    'http://127.0.0.1/a', 'http://localhost/a', 'http://169.254.169.254/a',
    'http://[::1]/a', 'https://example.org/a',
    'https://voiranime.cx.evil.example/a', 'https://voiranime.cx@evil.example/a',
    'https://evil.example/a?voiranime.cx', 'https://voiranime.cx:8443/a',
    'https://user:password@voiranime.cx/a', 'file:///etc/passwd',
    'https://voiranime.cx/a#fragment', 'https://voiranime.cx/\r\nHeader:x',
])
def test_forbidden_url_never_fetches(monkeypatch, url):
    fetch = AsyncMock()
    monkeypatch.setattr(api_app, 'fetch_image', fetch)
    r = TestClient(api_app.app).get('/api/image-proxy', params={'url': url})
    assert r.status_code == 400
    fetch.assert_not_awaited()


@pytest.mark.parametrize('ip', ['127.0.0.1', '10.0.0.1', '169.254.169.254',
                                  '192.168.1.1', '::1', 'fd00::1', '::ffff:127.0.0.1'])
def test_allowed_host_private_dns_rejected(monkeypatch, ip):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(0, 0, 0, '', (ip, 443))])
    with pytest.raises(images.UnsafeImageURL):
        images.public_addresses('voiranime.cx', 443)


def test_mixed_dns_rejected(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [
        (0, 0, 0, '', ('1.1.1.1', 443)), (0, 0, 0, '', ('127.0.0.1', 443))])
    with pytest.raises(images.UnsafeImageURL):
        images.public_addresses('voiranime.cx', 443)


class FakeResponse:
    status = 200
    def __init__(self, mime='image/jpeg', body=b'image', headers=None):
        self.headers = {'Content-Type': mime, **(headers or {})}
        self.body = io.BytesIO(body)
    def getheader(self, name, default=None):
        return self.headers.get(name, default)
    def read1(self, count):
        return self.body.read(count)


def setup_fetch(monkeypatch, responses):
    connections = []
    monkeypatch.setattr(images, 'public_addresses', lambda *a: ['1.1.1.1'])
    class Connection:
        def __init__(self, host, ip, port, secure):
            connections.append((host, ip, port, secure))
        def request(self, *a, **k): pass
        def getresponse(self): return responses.pop(0)
        def close(self): pass
    monkeypatch.setattr(images, 'PinnedHTTPConnection', Connection)
    return connections


def test_pins_public_address(monkeypatch):
    seen = setup_fetch(monkeypatch, [FakeResponse()])
    assert images.fetch_image_sync('https://voiranime.cx/a', 'https://voiranime.cx/')[0] == b'image'
    assert seen == [('voiranime.cx', '1.1.1.1', 443, True)]


def test_redirect_revalidated(monkeypatch):
    r = FakeResponse(headers={'Location': 'http://127.0.0.1/secret'})
    r.status = 302
    seen = setup_fetch(monkeypatch, [r])
    with pytest.raises(images.UnsafeImageURL):
        images.fetch_image_sync('https://voiranime.cx/a', 'https://voiranime.cx/')
    assert len(seen) == 1


@pytest.mark.parametrize('mime', ['text/html', 'image/svg+xml', 'application/javascript'])
def test_active_content_rejected(monkeypatch, mime):
    setup_fetch(monkeypatch, [FakeResponse(mime=mime)])
    with pytest.raises(ValueError):
        images.fetch_image_sync('https://voiranime.cx/a', 'https://voiranime.cx/')


def test_size_limit_without_content_length(monkeypatch):
    setup_fetch(monkeypatch, [FakeResponse(body=b'x' * (images.MAX_BYTES + 1))])
    with pytest.raises(ValueError):
        images.fetch_image_sync('https://voiranime.cx/a', 'https://voiranime.cx/')


def test_passenger_failure_does_not_leak(monkeypatch):
    original = builtins.__import__
    def fail(name, *args, **kwargs):
        if name == 'a2wsgi':
            raise RuntimeError('SECRET_PATH_AND_TOKEN')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', fail)
    cwd = os.getcwd()
    try:
        module = runpy.run_path(str(Path(__file__).parent / 'passenger_wsgi.py'))
    finally:
        os.chdir(cwd)
    status = []
    body = b''.join(module['application']({}, lambda s, h: status.append((s, dict(h)))))
    assert status[0][0] == '503 Service Unavailable'
    assert b'SECRET_PATH' not in body and b'Traceback' not in body
    assert status[0][1]['Cache-Control'] == 'no-store'
