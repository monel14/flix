from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from cache import SEARCH_TTL, cache
from scraper.coflix_client import CoflixFetchError, coflix_get_html
from scraper.coflix_parser import parse_coflix_search
from scraper.voirdrama_client import VoirdramaFetchError, voirdrama_get_html
from scraper.voirdrama_parser import parse_voirdrama_search

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


async def _load_search_all(query: str) -> list:
    """Effectue une recherche concurrente sur Coflix et Voirdrama."""
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

    coflix_results, drama_results = await asyncio.gather(
        search_coflix(),
        search_voirdrama(),
    )

    # Entrelacer / fusionner les résultats
    combined = []
    max_len = max(len(coflix_results), len(drama_results))
    for i in range(max_len):
        if i < len(coflix_results):
            combined.append(coflix_results[i])
        if i < len(drama_results):
            combined.append(drama_results[i])

    return combined


@router.get("/recherche", response_class=HTMLResponse)
async def search(request: Request, q: str = Query(default="")) -> HTMLResponse:
    """Page de résultats de recherche unifiée (Films, Séries, K-Dramas)."""
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

    return templates.TemplateResponse(request, "search.html", {
        "request": request,
        "query": q,
        "results": results,
        "error": error,
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
        lite = [
            {
                "title": r["title"],
                "slug": r["slug"],
                "image": r["image"],
                "type": r["type"],
                "url": f"/drama/{r['slug']}" if r.get("type") == "drama" else f"/film/{r['slug']}",
            }
            for r in results[:12]
        ]
        return JSONResponse({"results": lite})
    except Exception:
        return JSONResponse({"results": []})
