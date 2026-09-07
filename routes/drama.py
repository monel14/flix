from __future__ import annotations

import asyncio
import logging
import os

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from cache import DETAIL_TTL, HOME_TTL, PLAYER_TTL, cache
from services.dedup import canonical_path_for, should_redirect_to_preferred, version_label
from services.genre_seo import get_genre_seo
from services.related import get_similar_items
from services.seo import content_seo, item_list_json_ld, page_seo, title_qualifiers
from scraper.voirdrama_client import VoirdramaFetchError, VoirdramaNotFoundError, voirdrama_get_html
from scraper.voirdrama_parser import VOIRDRAMA_GENRES, get_voirdrama_last_page, parse_voirdrama_detail, parse_voirdrama_list, parse_voirdrama_servers
from scraper.frenchstream_client import FrenchstreamFetchError, FrenchstreamNotFoundError, SOURCE_URL as FRENCHSTREAM_SOURCE_URL, get_frenchstream_client, frenchstream_get_fiche, frenchstream_get_html, frenchstream_get_json
from scraper.frenchstream_parser import normalize_title as fs_normalize_title, parse_frenchstream_sitemap, parse_frenchstream_category, parse_frenchstream_episodes, parse_frenchstream_poster, pick_best_match, episode_servers, slugify as fs_slugify

logger = logging.getLogger(__name__)
router = APIRouter()
from services.templates import templates


def _known_paths() -> set[str]:
    cached = cache.get("sitemap:paths") or []
    return {p for p in cached if isinstance(p, str)}

def _known_voirdrama_paths() -> set[str]:
    cached = cache.get("sitemap:voirdrama_drama") or []
    return {p for p in cached if isinstance(p, str)}

async def _load_dramas_list(page: int = 1, genre: str | None = None, sort: str = "latest") -> dict:
    if genre:
        path = f"/drama-genre/{genre}/" if page == 1 else f"/drama-genre/{genre}/page/{page}/"
    elif sort in ("all", "az"):
        path = "/liste-dramas/" if page == 1 else f"/liste-dramas/page/{page}/"
    else:
        path = "/" if page == 1 else f"/page/{page}/"
    html = await voirdrama_get_html(path)
    items = parse_voirdrama_list(html)
    last_page = get_voirdrama_last_page(html)
    return {"items": items, "last_page": last_page}

async def _load_drama_detail(slug: str) -> dict:
    html = await voirdrama_get_html(f"/drama/{slug}/")
    detail = parse_voirdrama_detail(html, slug)
    if not detail.get("title") or not detail.get("episodes"):
        if not detail.get("title"):
            raise VoirdramaNotFoundError(f"Drama introuvable : {slug}")
    return dict(detail)

async def _load_drama_servers(slug: str, episode_slug: str) -> list:
    html = await voirdrama_get_html(f"/drama/{slug}/{episode_slug}/")
    return parse_voirdrama_servers(html)

FS_MATCH_TTL = 24 * 3600
FS_INDEX_TIMEOUT = 40

async def _frenchstream_index() -> dict[str, int] | None:
    async def _load() -> dict[str, int]:
        client = get_frenchstream_client()
        r = await client.get(FRENCHSTREAM_SOURCE_URL + "/sitemap.xml", timeout=FS_INDEX_TIMEOUT)
        r.raise_for_status()
        index = parse_frenchstream_sitemap(r.text)
        if not index:
            raise FrenchstreamFetchError("Index FrenchStream vide")
        return index
    try:
        return await cache.get_or_set("frenchstream:index", FS_MATCH_TTL, _load)
    except FrenchstreamFetchError as exc:
        logger.warning("FrenchStream sitemap indisponible : %s", exc)
        return None

async def _frenchstream_match(slug: str, title: str) -> int | None:
    index = await _frenchstream_index()
    if not index:
        return None
    candidates = [{"newsid": newsid, "slug": fs_slug} for fs_slug, newsid in index.items()]
    target = fs_slugify(fs_normalize_title(title))
    best = pick_best_match(candidates, target)
    if best:
        logger.info("FrenchStream match %s -> newsid %s", slug, best["newsid"])
        return best["newsid"]
    return None

async def _frenchstream_episode_servers(newsid: int, number: str) -> list:
    async def _load_json() -> dict:
        data = await frenchstream_get_json(newsid)
        return parse_frenchstream_episodes(data)
    try:
        parsed = await cache.get_or_set(f"frenchstream:eps:{newsid}", PLAYER_TTL, _load_json)
        return [{"server_name": f"FS · {s['server_name']}", "server_link": s["server_link"], "server_type": "embed", "version": s.get("version", "VOSTFR")} for s in episode_servers(parsed, number)]
    except FrenchstreamFetchError as exc:
        logger.warning("FrenchStream indisponible (eps %s/%s) : %s", newsid, number, exc)
        return []

FS_CATALOG_TTL = 12 * 3600
FS_CATALOG_PAGES = int(os.getenv("FRENCHSTREAM_CATALOG_PAGES", "39"))

async def _frenchstream_catalog() -> list[dict]:
    async def _load() -> list[dict]:
        items: dict[str, dict] = {}
        for page in range(1, FS_CATALOG_PAGES + 1):
            path = f"/index.php?cstart={page}&do=cat&category=k-drama-" if page > 1 else "/k-drama-//"
            html = await frenchstream_get_html(path)
            for it in parse_frenchstream_category(html):
                items[it["slug"]] = it
            await asyncio.sleep(0.15)
        existing = _known_voirdrama_paths()
        if existing:
            items = {s: it for s, it in items.items() if f"/drama/{s}" not in existing}
        sem = asyncio.Semaphore(6)
        async def _enrich(it: dict) -> dict | None:
            async with sem:
                try:
                    html = await frenchstream_get_fiche(it["newsid"])
                    poster = parse_frenchstream_poster(html)
                    return {**it, "image": poster or ""}
                except (FrenchstreamFetchError, FrenchstreamNotFoundError):
                    return {**it, "image": ""}
        enriched = await asyncio.gather(*(_enrich(it) for it in items.values()))
        return [it for it in enriched if it is not None]
    try:
        return await cache.get_or_set("frenchstream:catalog", FS_CATALOG_TTL, _load)
    except FrenchstreamFetchError as exc:
        logger.warning("FrenchStream catalogue indisponible : %s", exc)
        return []

async def _load_drama_detail_fs(slug: str) -> dict:
    catalog = await _frenchstream_catalog()
    entry = next((it for it in catalog if it["slug"] == slug), None)
    if not entry:
        raise VoirdramaNotFoundError(f"Drama FS introuvable : {slug}")
    data = await frenchstream_get_json(entry["newsid"])
    parsed = parse_frenchstream_episodes(data)
    episodes: list[dict] = []
    seen: set[int] = set()
    for version in ("vostfr", "vf", "vo"):
        for num in sorted(parsed.get(version) or {}):
            if num in seen:
                continue
            seen.add(num)
            episodes.append({"episode_id": f"{slug}-{num:02d}-vostfr" if version == "vostfr" else f"{slug}-{num:02d}-vf", "number": str(num), "title": f"Épisode {num}", "url": f"https://french-stream.one/index.php?newsid={entry['newsid']}", "version": "VOSTFR" if version == "vostfr" else "VF"})
    episodes.sort(key=lambda e: int(e["number"]))
    display = list(reversed(episodes)) if episodes else []
    info = data.get("info") or {}
    info_nums = sorted(int(n) for n in info)
    first_info = info.get(str(info_nums[0])) if info_nums else {}
    return {"title": entry["title"], "slug": slug, "url": f"/drama/{slug}", "image": entry.get("image", ""), "version": "VOSTFR", "type": "drama", "synopsis": (first_info or {}).get("synopsis", ""), "genres": ["K-Drama"], "year": "", "country": "", "status": "", "episodes": display, "first_episode_id": display[0]["episode_id"] if display else None, "frenchstream_newsid": entry["newsid"]}

@router.get("/dramas", response_class=HTMLResponse)
async def dramas_list(request: Request, page: int = Query(default=1, ge=1), genre: str | None = Query(default=None), version: str | None = Query(default=None, pattern="^(all|vf|vostfr)$"), sort: str = Query(default="latest", pattern="^(latest|az|all)$")) -> HTMLResponse:
    cache_key = f"list:dramas:{genre}:{sort}:{page}" if genre else f"list:dramas:{sort}:{page}"
    try:
        data = await cache.get_or_set(cache_key, HOME_TTL, lambda: _load_dramas_list(page, genre, sort))
    except VoirdramaFetchError as exc:
        logger.warning("Erreur liste dramas p%d genre=%s sort=%s : %s", page, genre, sort, exc)
        data = {"items": [], "last_page": 1}
    items = data.get("items", [])
    last_page = data.get("last_page", 1)
    if not genre and version != "vf":
        try:
            fs_catalog = await _frenchstream_catalog()
            if fs_catalog:
                per_page = 20
                if sort in ("all", "az"):
                    fs_sorted = sorted(fs_catalog, key=lambda it: it["title"].lower())
                else:
                    fs_sorted = fs_catalog
                fs_page = fs_sorted[(page - 1) * per_page: page * per_page]
                fs_items = [{"title": it["title"], "slug": it["slug"], "url": f"/drama/{it['slug']}", "image": it.get("image", ""), "version": "VOSTFR", "type": "drama", "latest_episode": None} for it in fs_page]
                known = _known_voirdrama_paths()
                if known:
                    fs_items = [it for it in fs_items if f"/drama/{it['slug']}" not in known]
                if fs_items:
                    if sort in ("all", "az"):
                        items = items + fs_items
                    else:
                        items = fs_items + items
                fs_last = max(1, -(-len(fs_sorted) // per_page))
                last_page = max(last_page, fs_last)
        except Exception as exc:
            logger.debug("Fusion catalogue FS dans /dramas ignorée : %s", exc)
    if version == "vf":
        items = [it for it in items if it.get("version") == "VF"]
    elif version == "vostfr":
        items = [it for it in items if it.get("version") == "VOSTFR"]
    seen: set[str] = set()
    unique_items: list = []
    for it in items:
        slug = it.get("slug") or ""
        if slug in seen:
            continue
        seen.add(slug)
        unique_items.append(it)
    items = unique_items
    if page > last_page and last_page >= 1:
        raise HTTPException(status_code=404, detail="Page inexistante")
    params = []
    if genre:
        params.append(f"genre={genre}")
    if sort != "latest":
        params.append(f"sort={sort}")
    if version and version != "all":
        params.append(f"version={version}")
    query_str = f"&{'&'.join(params)}" if params else ""
    prev_url = f"/dramas?page={page - 1}{query_str}" if page > 1 else None
    next_url = f"/dramas?page={page + 1}{query_str}" if page < last_page else None

    # V2 SEO genre enrichi
    if genre:
        gseo = get_genre_seo(genre, section="dramas", version=version or "all")
        section_label = gseo["h1"]
        seo_title = gseo["title"]
        seo_desc = gseo["description"]
    elif sort in ("all", "az"):
        section_label = "K-Dramas — Tous les titres (A-Z)"
        seo_title = f"{section_label} en Streaming HD — NokaTV"
        seo_desc = ""
    else:
        section_label = "K-Dramas — Nouveaux Épisodes & Sorties Récentes"
        seo_title = f"{section_label} en Streaming HD — NokaTV"
        seo_desc = ""
    if version == "vf":
        section_label += " (VF)"
    elif version == "vostfr":
        section_label += " (VOSTFR)"
    if not genre:
        seo_title = f"{section_label} en Streaming HD — NokaTV"
        seo_desc = seo_desc or f"{section_label} en streaming VF/VOSTFR HD."

    canon_params = []
    if genre:
        canon_params.append(f"genre={genre}")
    if page > 1:
        canon_params.append(f"page={page}")
    canon_path = request.url.path + (f"?{'&'.join(canon_params)}" if canon_params else "")

    return templates.TemplateResponse(request, "list.html", {
        "request": request,
        "items": items,
        "section": "dramas",
        "section_label": section_label,
        "current_page": page,
        "last_page": last_page,
        "prev_url": prev_url,
        "next_url": next_url,
        "genres": VOIRDRAMA_GENRES,
        "current_genre": genre,
        "current_sort": sort,
        "current_version": version or "all",
        "base_path": "/dramas",
        "seo": page_seo(request, title=seo_title, description=seo_desc, path=canon_path, extra_json_ld=[item_list_json_ld(request, [(it.get("title", ""), f"/drama/{it.get('slug', '')}") for it in items if it.get("slug")])]),
    })

@router.get("/drama/{slug}", response_class=HTMLResponse)
async def drama_detail(request: Request, slug: str) -> HTMLResponse:
    known = _known_paths()
    redirect_path = should_redirect_to_preferred(slug, "/drama/", known)
    if redirect_path:
        logger.info("Redirect 301 /drama/%s -> %s", slug, redirect_path)
        return RedirectResponse(url=redirect_path, status_code=301)
    try:
        data = await cache.get_or_set(f"detail:drama:{slug}", DETAIL_TTL, lambda: _load_drama_detail(slug))
    except VoirdramaNotFoundError:
        try:
            data = await cache.get_or_set(f"detail:drama-fs:{slug}", DETAIL_TTL, lambda: _load_drama_detail_fs(slug))
        except (VoirdramaNotFoundError, FrenchstreamFetchError, FrenchstreamNotFoundError):
            raise HTTPException(status_code=404, detail="Drama introuvable")
    except VoirdramaFetchError as exc:
        logger.warning("Erreur détail drama %s : %s", slug, exc)
        raise HTTPException(status_code=502, detail="Source K-Drama indisponible")
    if not data or not data.get("title"):
        raise HTTPException(status_code=404, detail="Drama introuvable")
    canonical_path = canonical_path_for(slug, "/drama/", known)
    qualifiers = title_qualifiers(data.get("title", ""), versions=(version_label(slug),), year=data.get("year", ""))
    try:
        similar = await get_similar_items(slug, data.get("genres", []), limit=6, pool="dramas")
    except Exception:
        similar = []
    return templates.TemplateResponse(request, "drama_detail.html", {
        "request": request,
        "drama": data,
        "slug": slug,
        "similar": similar,
        "seo": content_seo(request, item=data, path=canonical_path, title_suffix="K-Drama en Streaming HD", kind_label="Drama en Streaming VOSTFR & VF", qualifiers=qualifiers, breadcrumbs=[("K-Dramas", "/dramas")]),
    })

@router.get("/regarder-drama/{slug}/{episode_slug}", response_class=HTMLResponse)
async def drama_player(request: Request, slug: str, episode_slug: str) -> HTMLResponse:
    try:
        drama = await cache.get_or_set(f"detail:drama:{slug}", DETAIL_TTL, lambda: _load_drama_detail(slug))
    except VoirdramaNotFoundError:
        try:
            drama = await cache.get_or_set(f"detail:drama-fs:{slug}", DETAIL_TTL, lambda: _load_drama_detail_fs(slug))
        except (VoirdramaNotFoundError, FrenchstreamFetchError, FrenchstreamNotFoundError):
            raise HTTPException(status_code=404, detail="Drama introuvable")
    except VoirdramaFetchError as exc:
        logger.warning("Erreur player drama %s : %s", slug, exc)
        raise HTTPException(status_code=502, detail="Source K-Drama indisponible")
    episodes = drama.get("episodes", [])
    current_ep = next((e for e in episodes if e["episode_id"] == episode_slug), None)
    fs_newsid = drama.get("frenchstream_newsid")
    if fs_newsid:
        try:
            servers = await _frenchstream_episode_servers(fs_newsid, current_ep["number"] if current_ep else "1")
        except FrenchstreamFetchError as exc:
            logger.warning("Erreur serveurs FS drama %s/%s : %s", slug, episode_slug, exc)
            servers = []
    else:
        try:
            servers = await cache.get_or_set(f"servers:drama:{slug}:{episode_slug}", PLAYER_TTL, lambda: _load_drama_servers(slug, episode_slug))
        except VoirdramaFetchError as exc:
            logger.warning("Erreur chargement serveurs drama %s/%s : %s", slug, episode_slug, exc)
            servers = []
        if current_ep:
            try:
                fs_match_newsid = await _frenchstream_match(slug, drama.get("title", ""))
                if fs_match_newsid:
                    fs_servers = await _frenchstream_episode_servers(fs_match_newsid, current_ep["number"])
                    servers = servers + [s for s in fs_servers if s not in servers]
            except Exception as exc:
                logger.debug("FrenchStream complément %s/%s : %s", slug, episode_slug, exc)
    if not servers:
        raise HTTPException(status_code=404, detail="Aucun lecteur disponible pour cet épisode de drama.")
    ep_index = next((i for i, e in enumerate(episodes) if e["episode_id"] == episode_slug), -1)
    prev_ep = episodes[ep_index - 1] if ep_index > 0 else None
    next_ep = episodes[ep_index + 1] if ep_index >= 0 and ep_index + 1 < len(episodes) else None
    base_title = drama.get("title", "")
    seo_title = f"{base_title} — {current_ep['title']} — K-Drama Streaming — NokaTV" if current_ep else f"{base_title} — K-Drama Streaming — NokaTV"
    return templates.TemplateResponse(request, "drama_player.html", {
        "request": request,
        "drama": drama,
        "slug": slug,
        "episode_slug": episode_slug,
        "servers": servers,
        "current_ep": current_ep,
        "episodes": episodes,
        "prev_ep": prev_ep,
        "next_ep": next_ep,
        "default_server": servers[0] if servers else None,
        "seo": page_seo(request, title=seo_title, path=f"/drama/{slug}", image=drama.get("image", ""), noindex=True),
    })

@router.get("/api/drama/servers/{slug}/{episode_slug}", response_class=JSONResponse)
async def api_drama_servers(slug: str, episode_slug: str) -> JSONResponse:
    try:
        servers = await cache.get_or_set(f"servers:drama:{slug}:{episode_slug}", PLAYER_TTL, lambda: _load_drama_servers(slug, episode_slug))
        return JSONResponse({"servers": servers})
    except VoirdramaFetchError as exc:
        return JSONResponse({"servers": [], "error": str(exc)}, status_code=502)
