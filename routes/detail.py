from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from cache import DETAIL_TTL, cache
from scraper.coflix_client import CoflixFetchError, CoflixNotFoundError, coflix_get_html, coflix_get_json
from scraper.coflix_parser import parse_coflix_detail, parse_coflix_episodes
from scraper.voirdrama_client import VoirdramaNotFoundError, voirdrama_get_html
from scraper.voirdrama_parser import parse_voirdrama_detail
from services.blocklist import is_blocked, is_amazon_risky, is_monitored_high_traffic
from services.dedup import canonical_path_for, should_redirect_to_preferred, version_label
from services.related import get_similar_items
from services.seo import content_seo, title_qualifiers

logger = logging.getLogger(__name__)
router = APIRouter()
from services.templates import templates


def _known_paths() -> set[str]:
    cached = cache.get("sitemap:paths") or []
    return {p for p in cached if isinstance(p, str)}


async def load_detail(slug: str) -> dict:
    html = await coflix_get_html(f"/film/{slug}")
    detail = parse_coflix_detail(html, slug)
    if not detail.get("movie_id"):
        raise CoflixNotFoundError(f"Film ou série introuvable sur Coflix : {slug}")
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


@router.get("/film/{slug}", response_class=HTMLResponse)
async def film_detail(request: Request, slug: str):
    # DMCA hard block - 404 (Butterfly + Balls Up)
    if is_blocked(slug):
        logger.warning("DMCA blocked slug accessed: %s", slug)
        raise HTTPException(status_code=404, detail="Contenu retiré suite à demande DMCA")

    # Anticipation Amazon - soft noindex + hors sitemap (120+ titres)
    risky = is_amazon_risky(slug)
    if risky:
        logger.info("Amazon risky slug (soft noindex) accessed: %s", slug)

    # Option B: high-traffic Amazon kept indexed but monitored (The Last Sunrise 84 clics)
    monitored = is_monitored_high_traffic(slug)
    if monitored:
        logger.info("Amazon MONITORED high-traffic slug (kept indexed, watch Lumen): %s", slug)

    known = _known_paths()
    redirect_path = should_redirect_to_preferred(slug, "/film/", known)
    if redirect_path:
        logger.info("Redirect 301 /film/%s -> %s (préférence VF)", slug, redirect_path)
        return RedirectResponse(url=redirect_path, status_code=301)

    try:
        data = await cache.get_or_set(
            f"detail:{slug}", DETAIL_TTL, lambda: load_detail(slug)
        )
        if data and data.get("title") and data.get("movie_id"):
            canonical_path = canonical_path_for(slug, "/film/", known)
            if data.get("content_type"):
                versions = (data.get("version") or "").split("/")
            else:
                versions = (version_label(slug),)
            qualifiers = title_qualifiers(
                data.get("title", ""),
                versions=versions,
                year=data.get("year", ""),
            )
            try:
                similar = await get_similar_items(slug, data.get("genres", []), limit=6, pool="films")
            except Exception:
                similar = []
            related_all = (data.get("related", []) or []) + similar
            seen = set()
            deduped = []
            for it in related_all:
                s = (it.get("slug") or "").lower()
                if s and s not in seen and s != slug.lower():
                    seen.add(s)
                    deduped.append(it)
            return templates.TemplateResponse(request, "detail.html", {
                "request": request,
                "film": data,
                "slug": slug,
                "related": deduped[:12],
                "similar": similar,
                "seo": content_seo(
                    request,
                    item=data,
                    path=canonical_path,
                    title_suffix="Streaming HD",
                    kind_label="en Streaming VF & VOSTFR HD",
                    content_type=data.get("content_type", ""),
                    qualifiers=qualifiers,
                    breadcrumbs=[
                        ("Séries" if data.get("type") == "series" else "Films",
                         "/series" if data.get("type") == "series" else "/films")
                    ],
                    noindex=risky,
                ),
            })
    except (CoflixNotFoundError, CoflixFetchError):
        pass

    try:
        html_drama = await voirdrama_get_html(f"/drama/{slug}/")
        drama_data = parse_voirdrama_detail(html_drama, slug)
        if drama_data and drama_data.get("title") and drama_data.get("episodes"):
            logger.info("Redirection automatique de /film/%s vers /drama/%s (détecté comme K-Drama)", slug, slug)
            return RedirectResponse(url=f"/drama/{slug}", status_code=302)
    except Exception:
        pass

    raise HTTPException(status_code=404, detail="Film, série ou drama introuvable")
