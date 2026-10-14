"""Inventaire d'URLs indexables pour le sitemap dynamique (Stratégie 2026-10 « Coque propre »).

Objectif SEO :
1. Protéger le nom de domaine contre les désindexations massives et les plaintes
   DMCA Lumen sur les titres sous droit d'auteur.
2. Concentrer 100% de la puissance SEO sur des pages d'information et d'actualité
   saines et très recherchées :
   - Le Hub observatoire `/alternative`
   - Les 96 fiches ciblées `/alternative/{slug}` (réponse aux requêtes 'X ne marche plus')
   - Les pages institutionnelles et de confiance E-E-A-T (/, mentions légales, DMCA).
3. Toutes les fiches directes de films/séries/animes/dramas sont en `noindex` et
   exclues du sitemap pour ne plus être associées aux signalements Google.
4. L'ancien scraper de fiches (`_collect_all`) est conservé en sommeil dans ce fichier
   pour permettre un éventuel retour arrière si la stratégie évolue.
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
from services.blocklist import BLOCKED_SLUGS, is_excluded_from_sitemap
from services.dedup import merge_variants

logger = logging.getLogger(__name__)

SITEMAP_CACHE_KEY = "sitemap:paths"
# Fiches voirdrama seules (exclusions FrenchStream) — voir `_collect_all`.
SITEMAP_VOIDRAMA_DRAMA_KEY = "sitemap:voirdrama_drama"
SITEMAP_TTL = 12 * 3600  # 12 h — un sitemap n'a pas besoin d'être plus frais

from services.alternatives_data import STREAMING_SITES

# Stratégie 2026-10 « coque propre » : seul l'écosystème indexable sans risque
# est exposé à Google — le Hub /alternative + les 4 dossiers piliers thématiques
# (/alternative/football, /alternative/dramas, /alternative/manga,
# /alternative/f1-ufc) + toutes les fiches d'information /alternative/{slug}
# (veille ARCOM 2026) + home + pages légales.
PILLAR_PATHS = [
    "/alternative/football",
    "/alternative/dramas",
    "/alternative/manga",
    "/alternative/f1-ufc",
]
STATIC_PATHS = (
    ["/", "/alternative"]
    + PILLAR_PATHS
    + [f"/alternative/{slug}" for slug in sorted(STREAMING_SITES.keys())]
)
# Pages de confiance (E-E-A-T) : toujours disponibles, donc toujours listées.
LEGAL_PATHS = ["/mentions-legales", "/contact", "/dmca"]
BASE_PATHS = STATIC_PATHS + LEGAL_PATHS


def _max_pages() -> int:
    """Profondeur de collecte par catégorie (surchivable via SITEMAP_MAX_PAGES).
    
    P0 Fix SEO: réduit de 5 à 2 par défaut pour éviter d'inonder GSC avec 2361 URLs
    d'un coup sur un site jeune (cause 1019 Détectée non indexée). Augmenter
    progressivement quand taux indexation >60%.
    """
    try:
        return max(1, int(os.getenv("SITEMAP_MAX_PAGES", "2")))
    except ValueError:
        return 2


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

    DMCA: exclut les slugs bloqués.
    """
    slugs = _slugs_of(merge_variants(items))
    # Exclut DMCA hard + Amazon risky (anticipation Lumen 95976390 - 100 titres)
    return {s for s in slugs if not is_excluded_from_sitemap(s)}


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
    """Chemins indexables.

    Stratégie 2026-10 « coque propre » : le sitemap ne déclare plus les fiches
    de contenu (elles portent toutes un meta noindex et ne doivent plus être
    associées au domaine dans l'index Google — plaintes DMCA Lumen
    96775472/96775526). Seules la page /alternative, la home et les pages
    légales restent déclarées.

    La collecte scraper (`_collect_all`) est conservée mais dormante : elle
    pourra être réactivée quand la confiance du domaine sera rétablie.
    """
    return sorted(BASE_PATHS)
