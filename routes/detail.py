from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from cache import DETAIL_TTL, cache
from scraper.coflix_client import CoflixFetchError, CoflixNotFoundError, coflix_get_html, coflix_get_json
from scraper.coflix_parser import parse_coflix_detail, parse_coflix_episodes
from scraper.voirdrama_client import VoirdramaNotFoundError, voirdrama_get_html
from scraper.voirdrama_parser import parse_voirdrama_detail
from services.dedup import canonical_slug, sibling_slugs, version_label

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


async def load_detail(slug: str) -> dict:
    """Charge les détails d'un film ou d'une série avec ses épisodes et contenus liés."""
    html = await coflix_get_html(f"/film/{slug}")
    detail = parse_coflix_detail(html, slug)

    # Si aucun movie_id n'a été trouvé (ex: redirection vers la page d'accueil par le site source)
    if not detail.get("movie_id"):
        raise CoflixNotFoundError(f"Film ou série introuvable sur Coflix : {slug}")

    # Si c'est une série, on charge les épisodes
    if detail["type"] == "series" and detail["movie_id"]:
        try:
            ep_json = await coflix_get_json(
                "/ajax/episode/list-episode",
                params={"movieId": detail["movie_id"]},
            )
            detail["episodes"] = parse_coflix_episodes(ep_json)  # type: ignore[assignment]
        except CoflixFetchError as exc:
            logger.warning("Impossible de charger les épisodes pour %s : %s", slug, exc)
            detail["episodes"] = []  # type: ignore[assignment]
    else:
        detail["episodes"] = []  # type: ignore[assignment]

    # Déterminer le premier épisode ou l'ID de streaming du film
    first_ep = detail["episodes"][0] if detail.get("episodes") else None  # type: ignore[index]
    if first_ep:
        detail["first_episode_id"] = first_ep["episode_id"]  # type: ignore[assignment]
        detail["first_episode_url"] = f"/regarder/{slug}/ep-{first_ep['episode_id']}"  # type: ignore[assignment]
    elif detail.get("episode_id"):
        detail["first_episode_id"] = detail["episode_id"]  # type: ignore[assignment]
        detail["first_episode_url"] = f"/regarder/{slug}/ep-{detail['episode_id']}"  # type: ignore[assignment]
    elif detail.get("movie_id"):
        detail["first_episode_id"] = detail["movie_id"]  # type: ignore[assignment]
        detail["first_episode_url"] = f"/regarder/{slug}/ep-{detail['movie_id']}"  # type: ignore[assignment]
    else:
        detail["first_episode_id"] = None  # type: ignore[assignment]
        detail["first_episode_url"] = None  # type: ignore[assignment]

    return dict(detail)


async def _variant_exists(slug: str) -> bool:
    """Vérifie (avec cache) qu'une variante de slug existe réellement côté source."""
    try:
        html = await coflix_get_html(f"/film/{slug}")
        detail = parse_coflix_detail(html, slug)
        return bool(detail.get("movie_id"))
    except Exception:
        return False


@router.get("/film/{slug}", response_class=HTMLResponse)
async def film_detail(request: Request, slug: str):
    # 1. Tentative de chargement depuis Coflix (Films / Séries)
    version_links = []
    try:
        data = await cache.get_or_set(
            f"detail:{slug}", DETAIL_TTL, lambda: load_detail(slug)
        )
        if data and data.get("title") and data.get("movie_id"):
            # Onglets de versions (VF / VOSTFR) — modèle French Stream :
            # si ce slug porte un suffixe de version, on sonde ses variantes sœurs.
            base = canonical_slug(slug)
            if base and base != slug:
                version_links.append({
                    "label": version_label(slug) or "Actuel",
                    "slug": slug,
                    "active": True,
                })
                for sib in sibling_slugs(slug):
                    try:
                        exists = await cache.get_or_set(
                            f"variant:{sib}",
                            DETAIL_TTL,
                            lambda s=sib: _variant_exists(s),
                        )
                    except Exception:
                        exists = False
                    if exists:
                        version_links.append({
                            "label": version_label(sib) or sib,
                            "slug": sib,
                            "active": False,
                        })
            if len(version_links) < 2:
                version_links = []

            return templates.TemplateResponse(request, "detail.html", {
                "request": request,
                "film": data,
                "slug": slug,
                "related": data.get("related", []),
                "version_links": version_links,
            })
    except (CoflixNotFoundError, CoflixFetchError):
        pass

    # 2. Fallback intelligent : vérification si le titre est un K-Drama sur Voirdrama
    try:
        html_drama = await voirdrama_get_html(f"/drama/{slug}/")
        drama_data = parse_voirdrama_detail(html_drama, slug)
        if drama_data and drama_data.get("title") and drama_data.get("episodes"):
            logger.info("Redirection automatique de /film/%s vers /drama/%s (détecté comme K-Drama)", slug, slug)
            return RedirectResponse(url=f"/drama/{slug}", status_code=302)
    except Exception:
        pass

    raise HTTPException(status_code=404, detail="Film, série ou drama introuvable")
