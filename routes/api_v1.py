"""API v1 catalogue et lecture, additive aux routes HTML existantes."""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import time
from collections import deque
from typing import Literal
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel

from cache import DETAIL_TTL, HOME_TTL, cache
from scraper.voiranime_client import SOURCE_URL, VoiranimeFetchError, VoiranimeNotFoundError, voiranime_get_html
from scraper.voiranime_parser import (VOIRANIME_GENRES, get_voiranime_last_page,
    parse_voiranime_detail, parse_voiranime_list, parse_voiranime_search)
from services.blocklist import is_blocked
from services import api_catalog as catalog
import asyncio

logger = logging.getLogger(__name__)
# Limiteur par processus et par clé serveur (pas de confiance dans X-Forwarded-For).
_requests: dict[str, deque] = {}


async def authorize(response: Response, x_api_key: str | None = Header(default=None)):
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Robots-Tag'] = 'noindex, nofollow'
    expected = os.getenv('NOKATV_API_KEY', '').strip()
    if not expected:
        raise HTTPException(503, detail={'code': 'api_not_configured'}, headers={'Cache-Control': 'no-store'})
    if not x_api_key or not secrets.compare_digest(x_api_key.encode(), expected.encode()):
        raise HTTPException(401, detail={'code': 'unauthorized'}, headers={'Cache-Control': 'no-store'})
    now = time.monotonic()
    digest = hashlib.sha256(expected.encode()).hexdigest()
    # Efface l'historique d'une ancienne clé après rotation.
    for key in list(_requests):
        if key != digest:
            del _requests[key]
    hits = _requests.setdefault(digest, deque())
    while hits and hits[0] <= now - 60:
        hits.popleft()
    if len(hits) >= 120:
        raise HTTPException(429, detail={'code': 'rate_limited'}, headers={'Retry-After': '60', 'Cache-Control': 'no-store'})
    hits.append(now)


router = APIRouter(prefix='/api/v1', tags=['API v1'], dependencies=[Depends(authorize)])


class Item(BaseModel):
    id: str
    type: Literal['anime', 'movie', 'series', 'drama'] = 'anime'
    source: str = 'voiranime'
    slug: str
    title: str
    image_url: str
    version: str = ''
    latest_episode: str | None = None


class Pagination(BaseModel):
    page: int
    has_next: bool
    total_pages: int | None = None


class Listing(BaseModel):
    items: list[Item]
    pagination: Pagination
    warnings: list[dict[str, str]] = []


class Episode(BaseModel):
    id: str
    title: str
    number: str
    version: str
    season: str | None = None


class Detail(Item):
    synopsis: str = ''
    genres: list[str] = []
    year: str = ''
    status: str = ''
    episodes: list[Episode]
    first_episode_id: str | None = None


def public_image(image: str, request: Request) -> str:
    if not image:
        return ''
    origin = (os.getenv('NOKATV_API_PUBLIC_URL') or str(request.base_url)).rstrip('/') + '/'
    return urljoin(origin, image)


def item_payload(data: dict, request: Request) -> dict:
    slug = data['slug']
    kind = data.get('type', 'anime')
    source = data.get('_source', 'voiranime')
    return dict(id=f'{source}:{slug}', type=kind, source=source, slug=slug, title=data['title'],
                image_url=public_image(data.get('image', ''), request),
                version=data.get('version', ''), latest_episode=data.get('latest_episode'))


def check_slug(slug: str):
    if is_blocked(slug):
        raise HTTPException(404, detail={'code': 'not_found'})


async def cached_html(key: str, path: str, ttl: int):
    async def load():
        html = await voiranime_get_html(path)
        # Ne pas confondre une page de challenge avec un catalogue vide.
        soup = BeautifulSoup(html, 'html.parser')
        if not soup.select_one('.page-item-detail, .c-tabs-item__content, .site-content, #main, .site-main'):
            raise VoiranimeFetchError('Structure source inattendue')
        return html
    try:
        # Échec explicite : pas de prolongation silencieuse d'une réponse périmée.
        cache_key = f'api:v1:voiranime:{SOURCE_URL}:{key}'
        fresh = cache.get(cache_key)
        if fresh is not None:
            return fresh
        return await cache.get_or_set(cache_key, ttl, load, allow_stale_on_error=False)
    except VoiranimeNotFoundError:
        raise HTTPException(404, detail={'code': 'not_found'}) from None
    except VoiranimeFetchError:
        logger.warning('Source Voiranime indisponible pour API v1 (%s)', key)
        raise HTTPException(502, detail={'code': 'source_unavailable'}) from None


@router.get('/genres')
async def genres(type: Literal['anime', 'movie', 'series', 'drama'] = 'anime'):
    items = VOIRANIME_GENRES if type == 'anime' else catalog.dp.VOIRDRAMA_GENRES if type == 'drama' else catalog.cp.AVAILABLE_GENRES
    return {'type': type, 'items': items}


@router.get('/animes', response_model=Listing)
async def animes(request: Request, page: int = Query(1, ge=1, le=1000),
                 genre: str | None = None, sort: Literal['latest', 'az'] = 'latest',
                 version: Literal['all', 'vf', 'vostfr'] = 'all'):
    if genre is not None and genre not in {g['slug'] for g in VOIRANIME_GENRES}:
        raise HTTPException(422, detail={'code': 'invalid_genre'})
    path = f'/anime-genre/{genre}/' if genre else '/liste-danimes/' if sort == 'az' else '/'
    if page > 1:
        path += f'page/{page}/'
    html = await cached_html(f'list:{genre}:{sort}:{page}', path, HOME_TTL)
    items = [item_payload(i, request) for i in catalog.version_filter(parse_voiranime_list(html), version) if not is_blocked(i['slug'])]
    return {'items': items, 'pagination': {'page': page, 'has_next': get_voiranime_last_page(html) > page,
            'total_pages': None}}


@router.get('/search', response_model=Listing)
async def search(request: Request, q: str = Query(..., min_length=2, max_length=120),
                 type: Literal['anime', 'movie', 'series', 'drama', 'all'] = 'anime', page: int = Query(1, ge=1, le=100)):
    q = q.strip()
    if len(q) < 2:
        raise HTTPException(422, detail={'code': 'invalid_query'})
    if type != 'anime':
        data = await catalog_call(catalog.search_all(q, type, page))
        return listing_payload(data, request)
    from urllib.parse import urlencode
    path = ('/' if page == 1 else f'/page/{page}/') + '?' + urlencode({'s': q, 'post_type': 'wp-manga'})
    html = await cached_html('search:' + hashlib.sha256(path.encode()).hexdigest(), path, HOME_TTL)
    items = [item_payload(i, request) for i in parse_voiranime_search(html) if not is_blocked(i['slug'])]
    return {'items': items, 'pagination': {'page': page, 'has_next': get_voiranime_last_page(html) > page, 'total_pages': None}}


@router.get('/titles/anime/{slug}', response_model=Detail)
async def detail(request: Request, slug: str):
    import re
    if len(slug) > 200 or not re.fullmatch(r'[A-Za-z0-9_-]+', slug):
        raise HTTPException(422, detail={'code': 'invalid_slug'})
    check_slug(slug)
    html = await cached_html(f'detail:{slug}', f'/anime/{slug}/', DETAIL_TTL)
    # Le parser historique invente un titre de secours : vérifier la fiche réelle.
    soup = BeautifulSoup(html, 'html.parser')
    if not soup.select_one('.post-title h1, .post-title h3'):
        raise HTTPException(404, detail={'code': 'not_found'})
    data = parse_voiranime_detail(html, slug)
    return {**item_payload(data, request), 'synopsis': data.get('synopsis', ''),
            'genres': data.get('genres', []), 'year': data.get('year', ''), 'status': data.get('status', ''),
            'first_episode_id': data.get('first_episode_id'),
            'episodes': [dict(id=e['episode_id'], title=e['title'], number=e['number'], version=e['version']) for e in data.get('episodes', [])]}


async def catalog_call(awaitable):
    try:
        return await awaitable
    except catalog.CatalogError as exc:
        raise HTTPException(exc.status, detail={'code': exc.code}) from None


def listing_payload(data, request):
    return {**data, 'items': [item_payload(i, request) for i in data['items']]}


def detail_payload(data, request):
    return {**item_payload(data, request),
            'synopsis': data.get('synopsis', ''), 'genres': data.get('genres', []),
            'year': str(data.get('year') or ''), 'status': data.get('status', ''),
            'first_episode_id': data.get('first_episode_id'),
            'episodes': [dict(id=str(e['episode_id']), title=e.get('title', ''),
                number=str(e.get('number', '')), season=str(e['season']) if e.get('season') is not None else None,
                version=e.get('version', data.get('version', ''))) for e in data.get('episodes', [])]}


@router.get('/movies', response_model=Listing)
async def movies(request: Request, page: int = Query(1, ge=1, le=1000), genre: str | None = None,
                 version: Literal['all', 'vf', 'vostfr'] = 'all'):
    return listing_payload(await catalog_call(catalog.coflix_list('movie', page, genre, version)), request)


@router.get('/series', response_model=Listing)
async def series(request: Request, page: int = Query(1, ge=1, le=1000), genre: str | None = None,
                 version: Literal['all', 'vf', 'vostfr'] = 'all'):
    return listing_payload(await catalog_call(catalog.coflix_list('series', page, genre, version)), request)


@router.get('/dramas', response_model=Listing)
async def dramas(request: Request, page: int = Query(1, ge=1, le=1000), genre: str | None = None,
                 version: Literal['all', 'vf', 'vostfr'] = 'all', sort: Literal['latest', 'az'] = 'latest',
                 source: Literal['all', 'voirdrama', 'frenchstream'] = 'all'):
    if source == 'frenchstream' and version == 'vf':
        raise HTTPException(422, detail={'code': 'version_not_supported_for_source'})
    return listing_payload(await catalog_call(catalog.drama_list(page, genre, sort, version, source)), request)


@router.get('/titles/{kind}/{slug}', response_model=Detail)
async def other_detail(request: Request, kind: Literal['movie', 'series', 'drama'], slug: str,
                       source: Literal['auto', 'voirdrama', 'frenchstream'] = 'auto'):
    if kind != 'drama' and source != 'auto':
        raise HTTPException(422, detail={'code': 'invalid_source'})
    return detail_payload(await catalog_call(catalog.title(kind, slug, source)), request)


@router.get('/titles/{kind}/{slug}/episodes')
async def episodes(request: Request, kind: Literal['anime', 'movie', 'series', 'drama'], slug: str,
                   source: Literal['auto', 'voirdrama', 'frenchstream'] = 'auto'):
    if kind != 'drama' and source != 'auto':
        raise HTTPException(422, detail={'code': 'invalid_source'})
    data = await catalog_call(catalog.title(kind, slug, source))
    payload = detail_payload(data, request)
    return {'id': payload['id'], 'type': kind, 'slug': slug, 'source': payload['source'],
            'items': payload['episodes'], 'first_episode_id': payload['first_episode_id']}


@router.get('/playback/{kind}/{slug}/{episode_id}')
async def playback(kind: Literal['anime', 'movie', 'series', 'drama'], slug: str, episode_id: str,
                   source: Literal['auto', 'voirdrama', 'frenchstream'] = 'auto'):
    if kind != 'drama' and source != 'auto':
        raise HTTPException(422, detail={'code': 'invalid_source'})
    return await catalog_call(catalog.playback(kind, slug, episode_id, source))


@router.get('/home')
async def home(request: Request, top: Literal['day', 'week', 'month'] = 'day'):
    jobs = {'hero': catalog.hero(), 'movies': catalog.coflix_list('movie'),
            'series': catalog.coflix_list('series'), 'animation_movies': catalog.coflix_list('movie', genre='animation'),
            'dramas': catalog.drama_list(), 'animes': animes(request, page=1, genre=None, sort='latest', version='all'),
            'top': catalog.top_items(top)}
    results = await asyncio.gather(*jobs.values(), return_exceptions=True)
    sections, warnings, success = {}, [], 0
    for name, result in zip(jobs, results):
        if isinstance(result, BaseException):
            if not isinstance(result, (catalog.CatalogError, HTTPException)):
                raise result
            code = result.code if isinstance(result, catalog.CatalogError) else 'source_unavailable'
            warnings.append({'section': name, 'code': code})
            sections[name] = {'status': 'unavailable', 'items': []}
            continue
        success += 1
        if name == 'animes':
            items = result['items']
        else:
            raw = result['items'] if isinstance(result, dict) else result
            items = [item_payload(i, request) for i in catalog.visible(raw)]
            if name == 'hero':
                by_slug = {i['slug']: i for i in raw}
                items = [{**i, 'synopsis': by_slug[i['slug']].get('synopsis', ''),
                          'year': by_slug[i['slug']].get('year', '')} for i in items]
        sections[name] = {'status': 'ok', 'items': items}
        if isinstance(result, dict):
            warnings.extend({'section': name, **w} for w in result.get('warnings', []))
    if not success:
        raise HTTPException(502, detail={'code': 'source_unavailable'})
    return {'sections': sections, 'warnings': warnings}
