"""API deployment has no mounted HTML routes or domain redirects."""
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from api_app import app
from routes import api_v1


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('NOKATV_API_KEY', 'unit-test-only')
    api_v1._requests.clear()
    return TestClient(app, base_url='https://noka-tv.xyz')


def test_health_and_noindex(client):
    r = client.get('/health')
    assert r.status_code == 200
    assert r.json() == {'status': 'ok', 'service': 'nokatv-api'}
    assert r.headers['x-robots-tag'] == 'noindex, nofollow'
    assert r.headers['cache-control'] == 'private, no-store'

    # Alias /api/health
    r_api = client.get('/api/health')
    assert r_api.status_code == 200
    assert r_api.json() == {'status': 'ok', 'service': 'nokatv-api'}


@pytest.mark.parametrize('path', ['/', '/films', '/series', '/animes', '/dramas',
    '/ma-liste', '/recherche', '/alternative', '/film/demo', '/regarder/demo/ep-1',
    '/static/style.css', '/docs', '/openapi.json'])
def test_frontend_not_registered(client, path):
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 404
    assert r.json() == {'detail': 'Not Found'}
    assert 'location' not in r.headers


def test_api_auth_on_new_host(client):
    r = client.get('/api/v1/genres?type=movie', follow_redirects=False)
    assert r.status_code == 401
    assert 'location' not in r.headers


def test_api_authenticated_on_new_host(client):
    r = client.get('/api/v1/genres?type=anime', headers={'X-API-Key': 'unit-test-only'})
    assert r.status_code == 200
    assert r.json()['items']


def test_catalog_route_uses_json(client, monkeypatch):
    monkeypatch.setattr(api_v1.catalog, 'coflix_list', AsyncMock(return_value={
        'items': [], 'pagination': {'page': 1, 'total_pages': 1, 'has_next': False, 'has_previous': False}}))
    r = client.get('/api/v1/movies', headers={'X-API-Key': 'unit-test-only'})
    assert r.status_code == 200
    assert r.json()['items'] == []


def test_robots(client):
    r = client.get('/robots.txt')
    assert r.status_code == 200
    assert 'Disallow: /' in r.text
    assert 'Allow: /api/image-proxy' in r.text


def test_image_proxy_registered(client, monkeypatch):
    async def fake_fetch(*args, **kwargs):
        return b'fake-image-bytes', 'image/jpeg'

    monkeypatch.setattr('api_app.fetch_image', fake_fetch)
    r = client.get('/api/image-proxy?url=https://voiranime.cx/poster.jpg')
    assert r.status_code == 200
    assert r.content == b'fake-image-bytes'
    assert 'public, max-age=604800, immutable' in r.headers['cache-control']
    assert 'x-robots-tag' not in r.headers
