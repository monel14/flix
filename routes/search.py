from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from cache import SEARCH_TTL, cache
from services.seo import page_seo
from scraper.coflix_client import CoflixFetchError, coflix_get_html
from scraper.coflix_parser import parse_coflix_search
from scraper.voirdrama_client import VoirdramaFetchError, voirdrama_get_html
from scraper.voirdrama_parser import parse_voirdrama_search
from scraper.voiranime_client import VoiranimeFetchError, voiranime_get_html
from scraper.voiranime_parser import parse_voiranime_search

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


async def _load_search_all(query: str) -> list:
    """Recherche concurrente sur Coflix (films/séries), Voirdrama (dramas) et Voiranime (animés)."""
    async def search_coflix():
        try:
            html = await coflix_get_html("/filter", params={"keyword": query})
            return parse_coflix_search(html)
        except Exception as exc:
            logger.warning("Erreur recherche Coflix '%s' : %s", query, exc)
            return []

    async def search_voirdrama():
        try:
            html = await voirdrama_get_html(f"/?s={query}&post_type=wp-manga")
            return parse_voirdrama_search(html)
        except Exception as exc:
            logger.warning("Erreur recherche Voirdrama '%s' : %s", query, exc)
            return []

    async def search_voiranime():
        try:
            html = await voiranime_get_html(f"/?s={query}&post_type=wp-manga")
            return parse_voiranime_search(html)
        except Exception as exc:
            logger.warning("Erreur recherche Voiranime '%s' : %s", query, exc)
            return []

    coflix_res, drama_res, anime_res = await asyncio.gather(
        search_coflix(),
        search_voirdrama(),
        search_voiranime(),
    )

    combined = []
    max_len = max(len(coflix_res), len(drama_res), len(anime_res))
    for i in range(max_len):
        if i < len(coflix_res):
            combined.append(coflix_res[i])
        if i < len(drama_res):
            combined.append(drama_res[i])
        if i < len(anime_res):
            combined.append(anime_res[i])

    return combined


@router.get("/recherche", response_class=HTMLResponse)
async def search(request: Request, q: str = Query(default="")) -> HTMLResponse:
    """Page de résultats unifiée (Films, Séries, K-Dramas, Animés)."""
    results = []
    error = None

    cleaned_q = q.strip()
    if cleaned_q:
        try:
            results = await cache.get_or_set(
                f"search:all:{cleaned_q.lower()}",
                SEARCH_TTL,
                lambda: _load_search_all(cleaned_q),
            )
        except Exception as exc:
            logger.warning("Erreur recherche globale '%s' : %s", cleaned_q, exc)
            error = "La recherche a rencontré une erreur. Réessaie dans un instant."

    seo_title = (f'Résultats pour "{cleaned_q}"' if cleaned_q else "Explorer & Rechercher") + " — Nokaflix"
    return templates.TemplateResponse(request, "search.html", {
        "request": request,
        "query": q,
        "results": results,
        "error": error,
        # Canonical sans la requête : les pages de résultats ne se
        # concurrencent pas entre elles dans l'index.
        "seo": page_seo(request, title=seo_title, path="/recherche"),
    })


@router.get("/api/search", response_class=JSONResponse)
async def api_search(q: str = Query(default="")) -> JSONResponse:
    """API JSON unifiée pour l'autocomplétion."""
    cleaned_q = q.strip()
    if len(cleaned_q) < 2:
        return JSONResponse({"results": []})
    try:
        results = await cache.get_or_set(
            f"search:all:{cleaned_q.lower()}",
            SEARCH_TTL,
            lambda: _load_search_all(cleaned_q),
        )
        lite = []
        for r in results[:15]:
            t = r.get("type", "movie")
            if t == "drama":
                link = f"/drama/{r['slug']}"
            elif t == "anime":
                link = f"/anime/{r['slug']}"
            else:
                link = f"/film/{r['slug']}"

            lite.append({
                "title": r["title"],
                "slug": r["slug"],
                "image": r["image"],
                "type": t,
                "url": link,
            })
        return JSONResponse({"results": lite})
    except Exception:
        return JSONResponse({"results": []})
