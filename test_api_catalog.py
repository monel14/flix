"""Extension v1 : tests hors réseau et respect de l'appartenance des épisodes."""
from unittest.mock import AsyncMock

import pytest

from test_api_v1 import client, DETAIL
from routes import api_v1 as api
from services import api_catalog as c

COFLIX='''<div class="list-page"><div class="item"><a class="poster" href="https://coflix.gold/film/demo-vf/ep-123"></a><a class="d-title">Demo</a><div class="version">VF</div></div><a href="?page=3">3</a></div>'''
WP='''<main id="main"><div class="page-item-detail"><h3><a href="https://voirdrama.to/drama/demo/">Demo</a></h3><img src="https://voirdrama.to/a.jpg"></div><div class="pagination"><a href="/page/2/">2</a></div></main>'''
CFDETAIL='''<div id="watch-page" data-id="77" data-type="movie" data-ep-name="123"></div><h1>Demo</h1>'''

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(c.cc,'coflix_get_html',AsyncMock(return_value=COFLIX))
    monkeypatch.setattr(c.cc,'coflix_get_json',AsyncMock(return_value={'message':[]}))
    monkeypatch.setattr(c.dc,'voirdrama_get_html',AsyncMock(return_value=WP))
    monkeypatch.setattr(c.ac,'voiranime_get_html',AsyncMock(return_value=DETAIL))
    monkeypatch.setattr(c.fc,'frenchstream_get_json',AsyncMock(return_value={}))


@pytest.mark.parametrize('route,section',[('movies','movies'),('series','series')])
def test_lists(client, route, section):
    r=client.get(f'/api/v1/{route}?genre=action&page=2')
    assert r.status_code==200
    d=r.json();assert d['items'][0]['source']=='coflix'
    assert d['items'][0]['type']==('movie' if route=='movies' else 'series')
    assert d['pagination']=={'page':2,'has_next':True,'total_pages':3}
    c.cc.coflix_get_html.assert_awaited_once_with(f'/{section}/action/?page=2')


def test_versions_and_bounds(client):
    assert client.get('/api/v1/movies?version=vostfr').json()['items']==[]
    assert client.get('/api/v1/movies?version=vf').json()['items']
    assert client.get('/api/v1/movies?version=bad').status_code==422
    assert client.get('/api/v1/movies?page=4').status_code==404
    assert client.get('/api/v1/movies?genre=../oops').status_code==422
    assert client.get('/api/v1/series?page=1001').status_code==422


def test_drama_partial_warm_catalog(client):
    r=client.get('/api/v1/dramas');assert r.status_code==200
    d=r.json();assert d['items'][0]['source']=='voirdrama'
    assert d['warnings']==[{'source':'frenchstream','code':'catalog_warming'}]
    assert client.get('/api/v1/dramas?source=frenchstream').status_code==503
    assert client.get('/api/v1/dramas?source=frenchstream&version=vf').status_code==422


def test_fs_merge_and_listing(client,monkeypatch):
    monkeypatch.setattr(c,'fs_ready',lambda:[{'slug':'fs-demo','title':'FS Demo','image':''}])
    d=client.get('/api/v1/dramas').json()
    assert {i['source'] for i in d['items']}=={'frenchstream','voirdrama'}
    assert client.get('/api/v1/dramas?source=frenchstream').json()['items'][0]['id']=='frenchstream:fs-demo'
    assert client.get('/api/v1/dramas?source=frenchstream&genre=action').status_code==422


def test_search_partial(client):
    c.dc.voirdrama_get_html.side_effect=c.dc.VoirdramaFetchError('SECRET-UPSTREAM')
    d=client.get('/api/v1/search?q=demo&type=all')
    assert d.status_code==200
    assert any(i['type']=='movie' for i in d.json()['items'])
    assert any(w['source']=='voirdrama' for w in d.json()['warnings'])
    assert 'SECRET-UPSTREAM' not in d.text


def test_search_classifies_each_coflix_card(client):
    c.cc.coflix_get_html.return_value=COFLIX.replace('demo-vf','demo-saison-2-vf')
    assert client.get('/api/v1/search?q=demo&type=series').json()['items'][0]['type']=='series'
    assert client.get('/api/v1/search?q=demo&type=movie').json()['items']==[]


def test_all_upstream_failures_are_not_empty_success(client):
    c.cc.coflix_get_html.side_effect=c.cc.CoflixFetchError('private')
    assert client.get('/api/v1/movies').status_code==502
    assert client.get('/api/v1/search?q=demo&type=movie').status_code==502


def movie_data():
    return {'slug':'demo','title':'Demo','type':'movie','version':'VF','movie_id':'77','episode_id':'123',
            'genres':[],'image':'/api/image-proxy?url=test','synopsis':'Résumé'}


def test_movie_detail_and_episode(client,monkeypatch):
    monkeypatch.setattr(c.cp,'parse_coflix_detail',lambda html,slug: movie_data())
    d=client.get('/api/v1/titles/movie/demo')
    assert d.status_code==200
    assert d.json()['first_episode_id']=='123'
    assert d.json()['episodes'][0]['id']=='123'
    assert client.get('/api/v1/titles/series/demo').status_code==404
    e=client.get('/api/v1/titles/movie/demo/episodes')
    assert e.status_code==200 and e.json()['items'][0]['id']=='123'


def test_playback_membership_and_sanitization(client,monkeypatch):
    monkeypatch.setattr(c.cp,'parse_coflix_detail',lambda html,slug:movie_data())
    c.cc.coflix_get_json.return_value={'message':[{'server_link':'https://player.example/embed/1','server_name':'Player'}, {'server_link':'javascript:alert(1)'}, {'server_link':'https://player.example/embed/1'}]}
    assert client.get('/api/v1/playback/movie/demo/999').status_code==404
    c.cc.coflix_get_json.assert_not_awaited()
    r=client.get('/api/v1/playback/movie/demo/123');assert r.status_code==200
    assert len(r.json()['servers'])==1
    assert r.json()['servers'][0]['version']=='VF'


@pytest.mark.parametrize('kind',['anime','movie','series','drama'])
def test_blocked_everywhere(client,kind):
    assert client.get(f'/api/v1/titles/{kind}/pillion/episodes').status_code==404
    assert client.get(f'/api/v1/playback/{kind}/pillion/1').status_code==404
    c.cc.coflix_get_html.assert_not_awaited();c.dc.voirdrama_get_html.assert_not_awaited();c.ac.voiranime_get_html.assert_not_awaited()


def test_anime_playback(client):
    async def html(path):
        return DETAIL if path=='/anime/demo/' else '<main id="main"><iframe src="https://player.example/test"></iframe></main>'
    c.ac.voiranime_get_html.side_effect=html
    assert client.get('/api/v1/playback/anime/demo/not-in-title').status_code==404
    response=client.get('/api/v1/playback/anime/demo/demo-01-vostfr')
    assert response.status_code==200
    assert response.json()['servers'][0]['url']=='https://player.example/test'


def test_sources_and_unknown_parameters(client):
    assert client.get('/api/v1/titles/movie/demo?source=frenchstream').status_code==422
    assert client.get('/api/v1/playback/anime/demo/e?source=frenchstream').status_code==422
    assert client.get('/api/v1/genres?type=drama').status_code==200


def test_home_partial_and_blocklist(client,monkeypatch):
    monkeypatch.setattr(c,'hero',AsyncMock(return_value=[{'slug':'pillion','title':'Blocked','type':'movie','_source':'coflix'}]))
    monkeypatch.setattr(c,'top_items',AsyncMock(side_effect=c.CatalogError()))
    r=client.get('/api/v1/home');assert r.status_code==200
    assert r.json()['sections']['hero']['items']==[]
    assert r.json()['sections']['top']['status']=='unavailable'
    assert r.json()['sections']['movies']['items']


@pytest.mark.parametrize('path',['/api/v1/movies','/api/v1/series','/api/v1/dramas','/api/v1/home','/api/v1/playback/movie/demo/123','/api/v1/titles/movie/demo/episodes'])
def test_auth_for_new_routes(client,path):
    assert client.get(path,headers={'X-API-Key':'bad'}).status_code==401
    c.cc.coflix_get_html.assert_not_awaited()


def test_fs_detail_playback_and_chronological_order(client,monkeypatch):
    from routes import drama
    monkeypatch.setattr(c,'fs_ready',lambda:[{'slug':'fs-demo','newsid':12,'title':'FS Demo'}])
    async def fs_detail(slug):
        return {'slug':slug,'type':'drama','title':'FS Demo','frenchstream_newsid':12,
                'episodes':[{'episode_id':'fs-demo-02','number':'2','title':'Épisode 2'},
                            {'episode_id':'fs-demo-01','number':'1','title':'Épisode 1'}]}
    monkeypatch.setattr(drama,'_load_drama_detail_fs',fs_detail)
    monkeypatch.setattr(c.fp,'parse_frenchstream_episodes',lambda data: {})
    monkeypatch.setattr(c.fp,'episode_servers',lambda data,num: [{'server_link':'https://player.example/fs','server_name':'FS','version':'VOSTFR'}])
    r=client.get('/api/v1/titles/drama/fs-demo?source=frenchstream')
    assert r.status_code==200
    assert r.json()['source']=='frenchstream'
    assert r.json()['first_episode_id']=='fs-demo-01'
    r=client.get('/api/v1/playback/drama/fs-demo/fs-demo-01?source=frenchstream')
    assert r.status_code==200
    assert r.json()['next_episode_id']=='fs-demo-02'
    assert r.json()['servers'][0]['version']=='VOSTFR'


def test_blocked_search_not_reintroduced_from_cache(client):
    c.cc.coflix_get_html.return_value=COFLIX.replace('demo-vf','pillion')
    assert client.get('/api/v1/search?q=blocked&type=movie').json()['items']==[]
