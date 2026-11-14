"""Tests API v1 isolés : aucun appel réseau, aucun secret de production."""
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import api_v1 as api
from scraper.voiranime_client import VoiranimeFetchError, VoiranimeNotFoundError

LIST = '''<main id="main"><div class="page-item-detail"><h3><a href="https://voiranime.cx/anime/demo/">Demo</a></h3>
<img src="https://voiranime.cx/demo.jpg"></div><div class="pagination"><a href="/page/2/">2</a></div></main>'''
DETAIL = '''<main id="main"><div class="post-title"><h1>Demo</h1></div>
<div class="summary_image"><img src="https://voiranime.cx/demo.jpg"></div>
<li class="wp-manga-chapter"><a href="https://voiranime.cx/anime/demo/demo-01-vostfr/">01 VOSTFR</a></li></main>'''


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('NOKATV_API_KEY', 'test-only-key')
    monkeypatch.setenv('NOKATV_API_PUBLIC_URL', 'https://api.example.org')
    api._requests.clear()
    store = {}
    monkeypatch.setattr(api.cache, 'get', lambda key: store.get(key))
    async def get_or_set(key, ttl, loader, **kwargs):
        result = await loader()
        store[key] = result
        return result
    monkeypatch.setattr(api.cache, 'get_or_set', get_or_set)
    monkeypatch.setattr(api, 'voiranime_get_html', AsyncMock(return_value=LIST))
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app, headers={'X-API-Key': 'test-only-key'})


def test_auth_and_disabled(client, monkeypatch):
    assert client.get('/api/v1/animes', headers={'X-API-Key': 'bad'}).status_code == 401
    monkeypatch.delenv('NOKATV_API_KEY')
    assert client.get('/api/v1/animes').status_code == 503
    api.voiranime_get_html.assert_not_awaited()


def test_listing_absolute_images_and_cache(client):
    response = client.get('/api/v1/animes')
    assert response.status_code == 200
    data = response.json()
    assert data['items'][0]['id'] == 'voiranime:demo'
    assert data['items'][0]['image_url'].startswith('https://api.example.org/api/image-proxy?')
    assert data['pagination'] == {'page': 1, 'has_next': True, 'total_pages': None}
    assert response.headers['cache-control'] == 'private, no-store'
    assert response.headers['x-robots-tag'] == 'noindex, nofollow'
    client.get('/api/v1/animes')
    api.voiranime_get_html.assert_awaited_once_with('/')


@pytest.mark.parametrize('query,path', [
    ('?page=2', '/page/2/'),
    ('?genre=action&page=2', '/anime-genre/action/page/2/'),
    ('?sort=az&page=2', '/liste-danimes/page/2/'),
])
def test_source_paths(client, query, path):
    assert client.get('/api/v1/animes' + query).status_code == 200
    api.voiranime_get_html.assert_awaited_once_with(path)


@pytest.mark.parametrize('query', ['?page=0', '?page=1001', '?genre=invalid', '?sort=bad'])
def test_validation(client, query):
    assert client.get('/api/v1/animes' + query).status_code == 422
    api.voiranime_get_html.assert_not_awaited()


def test_genres(client):
    assert len(client.get('/api/v1/genres?type=anime').json()['items']) == 19
    assert len(client.get('/api/v1/genres?type=movie').json()['items']) == 18
    assert client.get('/api/v1/genres?type=invalid').status_code == 422


def test_search_encoded(client):
    response = client.get('/api/v1/search', params={'q': 'test & name'})
    assert response.status_code == 200
    assert api.voiranime_get_html.await_args.args[0] == '/?s=test+%26+name&post_type=wp-manga'
    assert client.get('/api/v1/search?q=%20%20').status_code == 422
    assert client.get('/api/v1/search?q=test&type=invalid').status_code == 422


def test_detail_and_episodes(client):
    api.voiranime_get_html.return_value = DETAIL
    response = client.get('/api/v1/titles/anime/demo')
    assert response.status_code == 200
    assert response.json()['episodes'][0]['id'] == 'demo-01-vostfr'
    assert response.json()['first_episode_id'] == 'demo-01-vostfr'
    assert 'url' not in response.json()['episodes'][0]


def test_blocked_detail_and_listing(client):
    assert client.get('/api/v1/titles/anime/pillion').status_code == 404
    api.voiranime_get_html.assert_not_awaited()
    api.voiranime_get_html.return_value = LIST.replace('/anime/demo/', '/anime/pillion/')
    assert client.get('/api/v1/animes').json()['items'] == []


@pytest.mark.parametrize('exception,status', [(VoiranimeFetchError('private upstream detail'), 502), (VoiranimeNotFoundError('private'), 404)])
def test_upstream_errors(client, exception, status):
    api.voiranime_get_html.side_effect = exception
    response = client.get('/api/v1/animes')
    assert response.status_code == status
    assert 'private' not in response.text


def test_empty_vs_broken_page(client):
    api.voiranime_get_html.return_value = '<main id="main">Aucun résultat</main>'
    response = client.get('/api/v1/search?q=unknown')
    assert response.status_code == 200
    assert response.json()['items'] == []
    api.voiranime_get_html.return_value = '<html><h1>Challenge</h1></html>'
    assert client.get('/api/v1/animes').status_code == 502


def test_fallback_title_not_a_real_detail(client):
    api.voiranime_get_html.return_value = '<main id="main">Introuvable</main>'
    assert client.get('/api/v1/titles/anime/unknown').status_code == 404


def test_rate_limit(client):
    for _ in range(120):
        assert client.get('/api/v1/genres').status_code == 200
    response = client.get('/api/v1/genres')
    assert response.status_code == 429
    assert response.headers['retry-after'] == '60'


def test_main_registers_api_without_removing_html(monkeypatch):
    from main import app
    paths = {route.path for route in app.routes}
    assert {'/animes', '/films', '/api/v1/animes', '/api/v1/titles/anime/{slug}'}.issubset(paths)

@pytest.mark.parametrize('path,expected', [('/api/v1/animes?page=0', 422), ('/api/v1/titles/anime/pillion', 404)])
def test_main_error_headers(client, path, expected):
    from main import app
    response = TestClient(app, headers={'X-API-Key': 'test-only-key'}).get(path)
    assert response.status_code == expected
    assert response.headers['cache-control'] == 'private, no-store'
    assert response.headers['x-robots-tag'] == 'noindex, nofollow'


def test_missing_key_does_not_fetch(client):
    client.headers.pop('X-API-Key')
    assert client.get('/api/v1/animes').status_code == 401
    api.voiranime_get_html.assert_not_awaited()
