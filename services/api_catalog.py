"""Adaptateurs API v1 : données uniquement, indépendants du rendu HTML."""
from __future__ import annotations

import asyncio
import re
from urllib.parse import urlencode, urlsplit

from bs4 import BeautifulSoup

from cache import HOME_TTL, DETAIL_TTL, PLAYER_TTL, cache
from scraper import coflix_client as cc, coflix_parser as cp
from scraper import voirdrama_client as dc, voirdrama_parser as dp
from scraper import voiranime_client as ac, voiranime_parser as ap
from scraper import frenchstream_client as fc, frenchstream_parser as fp
from services.blocklist import is_blocked
from services.dedup import merge_variants


class CatalogError(Exception):
    def __init__(self, status=502, code='source_unavailable'):
        self.status, self.code = status, code
        super().__init__(code)


FETCH_ERRORS = (cc.CoflixFetchError, dc.VoirdramaFetchError, ac.VoiranimeFetchError, fc.FrenchstreamFetchError)
NOT_FOUND = (cc.CoflixNotFoundError, dc.VoirdramaNotFoundError, ac.VoiranimeNotFoundError, fc.FrenchstreamNotFoundError)


def validate_slug(slug):
    if len(slug) > 200 or not re.fullmatch(r'[A-Za-z0-9_-]+', slug):
        raise CatalogError(422, 'invalid_slug')
    if is_blocked(slug):
        raise CatalogError(404, 'not_found')


async def cached(key, ttl, loader):
    fresh = cache.get('api:v1:catalog:' + key)
    if fresh is not None:
        return fresh
    try:
        async def bounded():
            return await asyncio.wait_for(loader(), timeout=20)
        return await cache.get_or_set('api:v1:catalog:' + key, ttl, bounded, allow_stale_on_error=False)
    except NOT_FOUND:
        raise CatalogError(404, 'not_found') from None
    except (asyncio.TimeoutError, *FETCH_ERRORS):
        raise CatalogError() from None


def visible(items):
    seen = set()
    result = []
    for i in items:
        slug = i.get('slug', '')
        identity = (i.get('_source'), i.get('type'), slug)
        if not slug or is_blocked(slug) or identity in seen:
            continue
        seen.add(identity)
        result.append(i)
    return result


def version_filter(items, version):
    if version == 'all':
        return items
    def matches(item):
        v = str(item.get('version', '')).lower()
        return ('vostfr' in v or v == 'vo') if version == 'vostfr' else any(x in re.split(r'[ /,]+', v) for x in ['vf', 'truefrench', 'french'])
    return [i for i in items if matches(i)]


def check_genre(genre, genres):
    if genre is not None and genre not in {g['slug'] for g in genres}:
        raise CatalogError(422, 'invalid_genre')


def wp_valid(html, detail=False):
    soup = BeautifulSoup(html, 'html.parser')
    if detail:
        if not soup.select_one('.post-title h1, .post-title h3'):
            # Distingue une fiche absente d'une page de challenge.
            if soup.select_one('#main, .site-content, .site-main'):
                raise CatalogError(404, 'not_found')
            raise CatalogError()
    elif not soup.select_one('.page-item-detail, .c-tabs-item__content, #main, .site-content, .site-main'):
        raise CatalogError()
    return soup


async def coflix_list(kind, page=1, genre=None, version='all'):
    check_genre(genre, cp.AVAILABLE_GENRES)
    section = 'movies' if kind == 'movie' else 'series'
    path = f'/{section}/' + (f'{genre}/' if genre else '') + (f'?page={page}' if page > 1 else '')
    async def load():
        html = await cc.coflix_get_html(path)
        soup = BeautifulSoup(html, 'html.parser')
        if not soup.select_one('.list-page, .films-grid, .ani.items') or soup.select_one('#slider-main'):
            raise CatalogError()
        items = merge_variants(cp.parse_coflix_list(html, section))
        return {'items': [{**i, '_source': 'coflix', 'type': kind} for i in items], 'last_page': cp.get_last_page(html)}
    data = await cached(f'coflix:{cc.SOURCE_URL}:list:{kind}:{genre}:{page}', HOME_TTL, load)
    last = data['last_page']
    if page > last:
        raise CatalogError(404, 'page_not_found')
    return {'items': version_filter(visible(data['items']), version),
            'pagination': {'page': page, 'has_next': last > page, 'total_pages': last}, 'warnings': []}


def fs_ready():
    # Le catalogue lourd est préchauffé par le worker existant. Jamais 39 pages
    # téléchargées dans une requête frontend bornée à quelques secondes.
    data = cache.get('frenchstream:catalog')
    if data is None:
        raise CatalogError(503, 'catalog_warming')
    return data


async def drama_list(page=1, genre=None, sort='latest', version='all', source='all'):
    check_genre(genre, dp.VOIRDRAMA_GENRES)
    if genre and source == 'frenchstream':
        raise CatalogError(422, 'genre_not_supported_for_source')
    warnings, items, has_next, successes = [], [], False, 0
    if source != 'frenchstream':
        path = f'/drama-genre/{genre}/' if genre else '/liste-dramas/' if sort == 'az' else '/'
        if page > 1:
            path += f'page/{page}/'
        async def load():
            html = await dc.voirdrama_get_html(path)
            wp_valid(html)
            return {'items': dp.parse_voirdrama_list(html), 'next': dp.get_voirdrama_last_page(html) > page}
        try:
            d = await cached(f'voirdrama:{dc.SOURCE_URL}:list:{genre}:{sort}:{page}', HOME_TTL, load)
            items = [{**i, '_source': 'voirdrama'} for i in d['items']]
            has_next = d['next']
            successes += 1
        except CatalogError as e:
            warnings.append({'source': 'voirdrama', 'code': e.code})
    if source != 'voirdrama' and not genre and version != 'vf':
        try:
            catalog = fs_ready()
            if sort == 'az':
                catalog = sorted(catalog, key=lambda i: i['title'].casefold())
            fsitems = [{**i, '_source': 'frenchstream', 'type': 'drama', 'version': 'VOSTFR'} for i in catalog[(page-1)*20:page*20]]
            # Même dédoublonnage par slug que le site historique pour le mélange.
            slugs = {i['slug'] for i in items}
            fsitems = [i for i in fsitems if i['slug'] not in slugs]
            items = fsitems + items
            has_next = has_next or len(catalog) > page*20
            successes += 1
        except CatalogError as e:
            warnings.append({'source': 'frenchstream', 'code': e.code})
    if not successes:
        raise CatalogError(503 if warnings and all(w['code'] == 'catalog_warming' for w in warnings) else 502,
                           'catalog_warming' if warnings and all(w['code'] == 'catalog_warming' for w in warnings) else 'source_unavailable')
    return {'items': version_filter(visible(items), version), 'pagination': {'page': page, 'has_next': has_next, 'total_pages': None}, 'warnings': warnings}


async def title(kind, slug, source='auto'):
    validate_slug(slug)
    if kind in ('movie', 'series'):
        async def load_coflix():
            html = await cc.coflix_get_html(f'/film/{slug}')
            data = cp.parse_coflix_detail(html, slug)
            if not data.get('movie_id'):
                raise CatalogError(404, 'not_found')
            if data['type'] != kind:
                raise CatalogError(404, 'type_mismatch')
            episodes = []
            if kind == 'series':
                raw = await cc.coflix_get_json('/ajax/episode/list-episode', params={'movieId': data['movie_id']})
                episodes = cp.parse_coflix_episodes(raw)
            # Un film est une entrée de lecture unique. Pour les séries sans
            # liste d'épisodes, ne pas présenter un ID movie comme épisode inventé.
            if kind == 'movie':
                eid = str(data.get('episode_id') or data['movie_id'])
                episodes = [dict(episode_id=eid, number='1', title=data['title'], version=data.get('version', ''))]
            return {**data, '_source': 'coflix', 'episodes': episodes,
                    'first_episode_id': str(episodes[0]['episode_id']) if episodes else None}
        return await cached(f'coflix:{cc.SOURCE_URL}:detail:{kind}:{slug}', DETAIL_TTL, load_coflix)
    if kind == 'anime':
        async def load_anime():
            html = await ac.voiranime_get_html(f'/anime/{slug}/')
            wp_valid(html, True)
            return {**ap.parse_voiranime_detail(html, slug), '_source': 'voiranime'}
        return await cached(f'voiranime:{ac.SOURCE_URL}:detail:{slug}', DETAIL_TTL, load_anime)
    async def load_drama():
        if source != 'frenchstream':
            try:
                html = await dc.voirdrama_get_html(f'/drama/{slug}/')
                wp_valid(html, True)
                return {**dp.parse_voirdrama_detail(html, slug), '_source': 'voirdrama'}
            except dc.VoirdramaNotFoundError:
                if source == 'voirdrama':
                    raise CatalogError(404, 'not_found') from None
            except CatalogError as e:
                if e.status != 404 or source == 'voirdrama':
                    raise
        catalog = fs_ready()
        if not any(i['slug'] == slug for i in catalog):
            raise CatalogError(404, 'not_found')
        from routes.drama import _load_drama_detail_fs
        data = await _load_drama_detail_fs(slug)
        episodes = sorted(data.get('episodes', []), key=lambda e: float(e.get('number') or 0))
        return {**data, '_source': 'frenchstream', 'episodes': episodes,
                'first_episode_id': episodes[0]['episode_id'] if episodes else None}
    return await cached(f'drama:{dc.SOURCE_URL}:{fc.SOURCE_URL}:detail:{source}:{slug}', DETAIL_TTL, load_drama)


async def search_source(source, q, page=1):
    path = ('/' if page == 1 else f'/page/{page}/') + '?' + urlencode({'s': q, 'post_type': 'wp-manga'})
    async def load():
        if source == 'coflix':
            html = await cc.coflix_get_html('/filter', params={'keyword': q, 'page': page})
            soup = BeautifulSoup(html, 'html.parser')
            if not soup.select_one('.list-page, .ani.items, .films-grid') or soup.select_one('#slider-main'):
                raise CatalogError()
            items = cp.parse_coflix_search(html)
            # Le parser historique attribue un type à la page entière. Les
            # résultats mélangent pourtant films et saisons : classer par carte.
            for i in items:
                i['type'] = 'series' if re.search(r'(?:saison|season)[ -]?\d+', i['slug'] + ' ' + i['title'], re.I) else 'movie'
            more = cp.get_last_page(html) > page
        elif source == 'voirdrama':
            html = await dc.voirdrama_get_html(path)
            wp_valid(html)
            items, more = dp.parse_voirdrama_search(html), dp.get_voirdrama_last_page(html) > page
        elif source == 'voiranime':
            html = await ac.voiranime_get_html(path)
            wp_valid(html)
            items, more = ap.parse_voiranime_search(html), ap.get_voiranime_last_page(html) > page
        else:
            items = [i for i in fs_ready() if all(w in fp.normalize_title(i['title']).casefold() for w in fp.normalize_title(q).casefold().split())]
            more = len(items) > page*20
            items = [{**i, 'type': 'drama', 'version': 'VOSTFR'} for i in items[(page-1)*20:page*20]]
        return {'items': [{**i, '_source': source} for i in items], 'has_next': more}
    return await cached(f'search:{cc.SOURCE_URL}:{dc.SOURCE_URL}:{ac.SOURCE_URL}:{fc.SOURCE_URL}:{source}:{q.casefold()}:{page}', HOME_TTL, load)


async def search_all(q, kind='all', page=1):
    sources = ['coflix'] if kind in ('movie', 'series') else ['voirdrama', 'frenchstream'] if kind == 'drama' else ['coflix', 'voirdrama', 'voiranime', 'frenchstream']
    results = await asyncio.gather(*(search_source(s, q, page) for s in sources), return_exceptions=True)
    groups, warnings, more = [], [], False
    successes = 0
    for source, result in zip(sources, results):
        if isinstance(result, BaseException):
            if not isinstance(result, CatalogError):
                raise result
            warnings.append({'source': source, 'code': result.code})
        else:
            successes += 1
            groups.append([i for i in result['items'] if kind == 'all' or i['type'] == kind])
            more = more or result['has_next']
    if not successes:
        raise CatalogError()
    # Entrelace les sources au lieu de cacher les résultats d'une source lente.
    items = [group[n] for n in range(max((len(g) for g in groups), default=0)) for group in groups if n < len(group)]
    return {'items': visible(items), 'pagination': {'page': page, 'has_next': more, 'total_pages': None}, 'warnings': warnings}


async def hero(top='day'):
    async def load():
        html = await cc.coflix_get_html('/')
        if not BeautifulSoup(html, 'html.parser').select_one('#slider-main'):
            raise CatalogError()
        items = cp.parse_coflix_hero(html)
        return [{**i, 'type': 'series' if re.search(r'saison[ -]?\d+', i['slug'], re.I) else 'movie', '_source': 'coflix'} for i in items]
    return await cached(f'coflix:{cc.SOURCE_URL}:hero', HOME_TTL, load)


async def top_items(period='day'):
    async def load():
        html = await cc.coflix_get_html('/ajax/movie/top', params={'type': period})
        if not BeautifulSoup(html, 'html.parser').select_one('.topv, a.item'):
            raise CatalogError()
        return [{**i, '_source': 'coflix', 'type': 'series' if re.search(r'saison[ -]?\d+', i['slug'], re.I) else 'movie'} for i in cp.parse_coflix_top(html)]
    return await cached(f'coflix:{cc.SOURCE_URL}:top:{period}', HOME_TTL, load)


async def playback(kind, slug, episode_id, source='auto'):
    validate_slug(slug)
    if len(episode_id) > 220 or not re.fullmatch(r'[A-Za-z0-9_-]+', episode_id):
        raise CatalogError(422, 'invalid_episode')
    data = await title(kind, slug, source)
    episodes = data.get('episodes', [])
    pos = next((n for n, e in enumerate(episodes) if str(e['episode_id']) == episode_id), None)
    if pos is None:
        raise CatalogError(404, 'episode_not_found')
    episode = episodes[pos]
    actual_source = data['_source']
    async def load():
        if actual_source == 'coflix':
            raw = await cc.coflix_get_json('/ajax/episode/player', params={'episode_id': episode_id})
            return cp.parse_coflix_servers(raw)
        if actual_source == 'voiranime':
            html = await ac.voiranime_get_html(f'/anime/{slug}/{episode_id}/')
            wp_valid(html)
            return ap.parse_voiranime_servers(html)
        if actual_source == 'voirdrama':
            html = await dc.voirdrama_get_html(f'/drama/{slug}/{episode_id}/')
            wp_valid(html)
            servers = dp.parse_voirdrama_servers(html)
            # Complément FS uniquement si une correspondance exacte est déjà
            # connue dans le catalogue chaud, jamais de scan sitemap synchrone.
            catalog = cache.get('frenchstream:catalog') or []
            match = next((i for i in catalog if i['slug'] == slug), None)
            if match:
                try:
                    raw = await fc.frenchstream_get_json(match['newsid'])
                    servers += fp.episode_servers(fp.parse_frenchstream_episodes(raw), episode['number'])
                except fc.FrenchstreamFetchError:
                    pass  # Le lecteur primaire reste utilisable.
            return servers
        raw = await fc.frenchstream_get_json(data['frenchstream_newsid'])
        return fp.episode_servers(fp.parse_frenchstream_episodes(raw), episode['number'])
    servers = await cached(f'playback:{actual_source}:{cc.SOURCE_URL}:{dc.SOURCE_URL}:{ac.SOURCE_URL}:{fc.SOURCE_URL}:{kind}:{slug}:{episode_id}', PLAYER_TTL, load)
    result = []
    seen = set()
    for s in servers:
        link = s.get('server_link', '')
        if link.startswith('//'):
            link = 'https:' + link
        if urlsplit(link).scheme not in ('https', 'http') or not urlsplit(link).netloc or link in seen:
            continue
        seen.add(link)
        result.append({'name': s.get('server_name') or f'Serveur {len(result)+1}', 'url': link,
                       'type': s.get('server_type') or 'embed', 'version': s.get('version') or episode.get('version') or data.get('version', '')})
    if not result:
        raise CatalogError(404, 'no_servers')
    return {'type': kind, 'slug': slug, 'source': actual_source, 'episode_id': episode_id,
            'servers': result, 'previous_episode_id': str(episodes[pos-1]['episode_id']) if pos > 0 else None,
            'next_episode_id': str(episodes[pos+1]['episode_id']) if pos+1 < len(episodes) else None}
