"""Inventaire d'URLs indexables pour le sitemap dynamique.

Le SEO du site repose sur les fiches de contenu (long tail « regarder X en
streaming ») : sans elles, le sitemap ne référençait que 6 pages statiques.

Ce module collecte les slugs réels depuis les mêmes sources que les pages
publiques (listes coflix / voirdrama / voiranime), en réutilisant le cache
SQLite (TTL long : 12 h) pour ne jamais re-scaper sous la charge d'un crawler.
Chaque source est indépendante : si l'une tombe, les autres alimentent quand
même le sitemap (Stale-on-Error du cache en dernier recours).
"""
from __future__ import annotations

import asyncio
import logging
import os

from cache import cache
from scraper.coflix_client import coflix_get_html
from scraper.coflix_parser import get_last_page, parse_coflix_list
from scraper.voiranime_client import voiranime_get_html
from scraper.voiranime_parser import get_voiranime_last_page, parse_voiranime_list
from scraper.voirdrama_client import voirdrama_get_html
from scraper.voirdrama_parser import get_voirdrama_last_page, parse_voirdrama_list
from scraper.frenchstream_client import (
    FrenchstreamFetchError,
    frenchstream_get_html,
)
from scraper.frenchstream_parser import parse_frenchstream_category
from services.dedup import merge_variants

logger = logging.getLogger(__name__)

SITEMAP_CACHE_KEY = "sitemap:paths"
# Fiches voirdrama seules (exclusions FrenchStream) — voir `_collect_all`.
SITEMAP_VOIDRAMA_DRAMA_KEY = "sitemap:voirdrama_drama"
SITEMAP_TTL = 12 * 3600  # 12 h — un sitemap n'a pas besoin d'être plus frais

STATIC_PATHS = ["/", "/films", "/series", "/dramas", "/animes"]
# Pages de confiance (E-E-A-T) : toujours disponibles, donc toujours listées.
LEGAL_PATHS = ["/mentions-legales", "/contact"]
BASE_PATHS = STATIC_PATHS + LEGAL_PATHS


def _max_pages() -> int:
    """Profondeur de collecte par catégorie (surchivable via SITEMAP_MAX_PAGES)."""
    try:
        return max(1, int(os.getenv("SITEMAP_MAX_PAGES", "5")))
    except ValueError:
        return 5


def _slugs_of(items: list) -> set[str]:
    return {(it.get("slug") or "").strip() for it in items if it.get("slug")}


def _preferred_slugs(items: list) -> set[str]:
    """Slugs des versions préférées (VF d'abord) après fusion des variantes.

    Un même titre existe souvent en plusieurs fiches dont le slug porte un
    suffixe de version (`-vf`, `-vostfr`, `-french`, `-truefrench`, `-vo`).
    Le sitemap ne doit référencer QUE la variante préférée (celle qui porte le
    canonical) : les doublons VF/VOSTFR dupliquaient le contenu et divisaient
    la confiance (cannibalisation constatée dans GSC, ex. `/film/lodyssee-vf`
    vs `/film/lodyssee-vostfr`).
    """
    return _slugs_of(merge_variants(items))


async def _collect_coflix(section: str, prefix: str, list_path: str) -> set[str]:
    """Slugs coflix d'une section (« movies » / « series ») + pages de liste."""
    max_pages = _max_pages()
    paths: set[str] = set()

    html_1 = await coflix_get_html(f"/{section}/")
    items = parse_coflix_list(html_1, section)
    slugs = _preferred_slugs(items)
    paths |= {f"{prefix}{slug}" for slug in slugs}
    paths.add(list_path)

    # Pages de liste ?page=N (self-canonicalisées par les routes, donc indexables)
    last = min(get_last_page(html_1), max_pages)
    for page in range(2, last + 1):
        paths.add(f"{list_path}?page={page}")

    async def fetch_page(page: int) -> set[str]:
        html = await coflix_get_html(f"/{section}/?page={page}")
        return _preferred_slugs(parse_coflix_list(html, section))

    results = await asyncio.gather(
        *(fetch_page(p) for p in range(2, last + 1)), return_exceptions=True
    )
    for page, res in enumerate(results, start=2):
        if isinstance(res, Exception):
            logger.warning("Sitemap : %s page %d ignorée (%s)", list_path, page, res)
            continue
        paths |= {f"{prefix}{slug}" for slug in res}
    return paths


async def _collect_paged_source(
    fetch_html, parse_list, get_last, first_path: str, page_tpl: str, prefix: str, list_path: str
) -> set[str]:
    """Slugs d'une source à pagination « /liste-.../page/N/ » (voirdrama / voiranime)."""
    max_pages = _max_pages()
    paths: set[str] = set()

    html_1 = await fetch_html(first_path)
    paths |= {f"{prefix}{slug}" for slug in _preferred_slugs(parse_list(html_1))}
    paths.add(list_path)

    last = min(get_last(html_1), max_pages)
    for page in range(2, last + 1):
        paths.add(f"{list_path}?page={page}")

    async def fetch_page(page: int) -> set[str]:
        html = await fetch_html(page_tpl.format(page=page))
        return _preferred_slugs(parse_list(html))

    results = await asyncio.gather(
        *(fetch_page(p) for p in range(2, last + 1)), return_exceptions=True
    )
    for page, res in enumerate(results, start=2):
        if isinstance(res, Exception):
            logger.warning("Sitemap : %s page %d ignorée (%s)", list_path, page, res)
            continue
        paths |= {f"{prefix}{slug}" for slug in res}
    return paths


async def _collect_frenchstream_kdrama() -> set[str]:
    """Fiches K-Drama FrenchStream (catégorie k-drama-) — nouveautés absentes
    de voirdrama. Ne garde que celles qui n'ont pas de fiche voirdrama
    existante (pas de doublon dans le sitemap).

    L'exclusion voirdrama est faite dans `_collect_all` (union de sets, les
    chemins identiques se dédoublonnent) : une lecture du cache sitemap ici
    ferait osciller le sitemap (le cache contient aussi les fiches FS du build
    précédent, ce qui exclurait tout FrenchStream un build sur deux)."""
    paths: set[str] = set()
    for page in range(1, int(os.getenv("FRENCHSTREAM_CATALOG_PAGES", "39")) + 1):
        path = (
            f"/index.php?cstart={page}&do=cat&category=k-drama-"
            if page > 1
            else "/k-drama-//"
        )
        try:
            html = await frenchstream_get_html(path)
        except FrenchstreamFetchError as exc:
            logger.warning("Sitemap : FrenchStream page %d indisponible (%s)", page, exc)
            break
        for it in parse_frenchstream_category(html):
            slug = it.get("slug", "")
            if slug:
                paths.add(f"/drama/{slug}")
        await asyncio.sleep(0.1)
    return paths


async def _collect_all() -> list[str]:
    """Collecte en parallèle toutes les sources ; chacune est isolée en erreur."""
    tasks = {
        "films": _collect_coflix("movies", "/film/", "/films"),
        "series": _collect_coflix("series", "/film/", "/series"),
        "dramas": _collect_paged_source(
            voirdrama_get_html, parse_voirdrama_list, get_voirdrama_last_page,
            "/liste-dramas/", "/liste-dramas/page/{page}/", "/drama/", "/dramas",
        ),
        "animes": _collect_paged_source(
            voiranime_get_html, parse_voiranime_list, get_voiranime_last_page,
            "/liste-danimes/", "/liste-danimes/page/{page}/", "/anime/", "/animes",
        ),
        "dramas_fs": _collect_frenchstream_kdrama(),
    }
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)

    paths: set[str] = set(BASE_PATHS)
    for (name, _), res in zip(tasks.items(), results):
        if isinstance(res, Exception):
            logger.warning("Sitemap : source « %s » indisponible (%s)", name, res)
            continue
        paths |= res

    # Cache dédié des fiches voirdrama seules (pas l'union) : les exclusions
    # FrenchStream (catalogue /dramas, fusion) doivent comparer au voirdrama
    # uniquement — le cache `sitemap:paths` contient aussi les fiches FS.
    try:
        dramas = dict(zip(tasks.keys(), results)).get("dramas")
        if isinstance(dramas, set):
            cache.set(SITEMAP_VOIDRAMA_DRAMA_KEY, sorted(dramas), SITEMAP_TTL)
    except Exception as exc:
        logger.warning("Sitemap : cache voirdrama drama indisponible (%s)", exc)

    # Filet de sécurité : si aucune fiche n'a été collectée (sources down ou
    # HTML source qui a changé), on ne met PAS en cache 12 h un sitemap
    # dégradé — on laisse le Stale-on-Error du cache servir la version
    # précédente (plus riche), comme pour le reste du site.
    if paths == set(BASE_PATHS):
        stale = cache.get_stale(SITEMAP_CACHE_KEY)
        stale_paths = stale.get("data") if stale else None
        if isinstance(stale_paths, list) and len(stale_paths) > len(STATIC_PATHS):
            raise RuntimeError(
                "aucune fiche collectée (sources indisponibles ou structure changée) — "
                "conservation du sitemap précédent"
            )

    return sorted(paths)


async def collect_sitemap_paths() -> list[str]:
    """Chemins indexables (statiques + fiches), cachés 12 h via SQLite."""
    return await cache.get_or_set(SITEMAP_CACHE_KEY, SITEMAP_TTL, _collect_all)
