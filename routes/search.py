from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from cache import SEARCH_TTL, cache
from scraper.coflix_client import CoflixFetchError, coflix_get_html
from scraper.coflix_parser import parse_coflix_search

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


async def _load_search(query: str) -> list:
    html = await coflix_get_html("/filter", params={"keyword": query})
    return parse_coflix_search(html)


@router.get("/recherche", response_class=HTMLResponse)
async def search(request: Request, q: str = Query(default="")) -> HTMLResponse:
    """Page de résultats de recherche."""
    results = []
    error = None

    cleaned_q = q.strip()
    if cleaned_q:
        try:
            results = await cache.get_or_set(
                f"search:{cleaned_q.lower()}",
                SEARCH_TTL,
                lambda: _load_search(cleaned_q),
            )
        except CoflixFetchError as exc:
            logger.warning("Erreur recherche '%s' : %s", cleaned_q, exc)
            error = "La recherche a échoué. Réessaie dans un instant."

    return templates.TemplateResponse(request, "search.html", {
        "request": request,
        "query": q,
        "results": results,
        "error": error,
    })


@router.get("/api/search", response_class=JSONResponse)
async def api_search(q: str = Query(default="")) -> JSONResponse:
    """API JSON pour l'autocomplétion de la barre de recherche."""
    cleaned_q = q.strip()
    if len(cleaned_q) < 2:
        return JSONResponse({"results": []})
    try:
        results = await cache.get_or_set(
            f"search:{cleaned_q.lower()}",
            SEARCH_TTL,
            lambda: _load_search(cleaned_q),
        )
        # Format léger pour l'autocomplétion
        lite = [
            {"title": r["title"], "slug": r["slug"], "image": r["image"], "type": r["type"]}
            for r in results[:10]
        ]
        return JSONResponse({"results": lite})
    except CoflixFetchError:
        return JSONResponse({"results": []})
