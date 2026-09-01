from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)

# Titre type « The First Jasmine - Saison 1 » -> « The First Jasmine »
_SAISON_SUFFIX = re.compile(
    r"[-–—]\s*Saison\s*\d+.*$|\s*Saison\s*\d+\s*$|\(\d{4}\)\s*$", re.IGNORECASE
)
# URL de fiche dans les résultats de recherche : /films/7795-slug-streaming-complet.html
_FICHE_URL = re.compile(r"/(?:films|serie)/(\d+)-([a-z0-9-]+)-streaming-complet\.html")
# Entrée de catégorie : <a ... href="/index.php?newsid=15128446" ... alt="Titre">
_CATEGORY_ENTRY = re.compile(r'newsid=(\d+)"[^>]*alt="([^"]+)"')
# URL de fiche dans le sitemap : /{id}-{slug}.html
_SITEMAP_URL = re.compile(r"/(\d+)-([a-z0-9-]+)\.html")
# Suffixes de saison/année dans un slug de série : "taxi-driver-saison-3-2021" -> "taxi-driver"
_SLUG_SAISON = re.compile(r"-saison-\d+(?:-\d{4})?$", re.IGNORECASE)


def normalize_title(raw: str) -> str:
    """Nettoie un titre FrenchStream : retire le suffixe saison et l'année."""
    return _SAISON_SUFFIX.sub("", raw.strip()).strip()


def slugify(title: str) -> str:
    """Normalise un titre en slug comparable (the-first-jasmine)."""
    t = unicodedata.normalize("NFKD", title.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return re.sub(r"-+", "-", t)


def parse_frenchstream_category(html: str) -> list[dict]:
    """Parse une page de catégorie K-Drama -> liste {newsid, title, slug}."""
    items: list[dict] = []
    for newsid, title in _CATEGORY_ENTRY.findall(html):
        clean = normalize_title(title)
        if not clean:
            continue
        items.append({
            "newsid": int(newsid),
            "title": clean,
            "slug": slugify(clean),
        })
    return items


def parse_frenchstream_search(html: str) -> list[dict]:
    """Parse les résultats de recherche DLE -> liste {newsid, title, slug}.

    La recherche interne renvoie du bruit SEO (contenus bourrés de mots-clés) :
    l'appelant doit filtrer par slug/mots-clés forts.
    """
    items: list[dict] = []
    seen: set[int] = set()
    for newsid_s, slug in _FICHE_URL.findall(html):
        newsid = int(newsid_s)
        if newsid in seen:
            continue
        seen.add(newsid)
        items.append({
            "newsid": newsid,
            "title": slug.replace("-", " ").title(),
            "slug": slug,
        })
    return items


def pick_best_match(candidates: list[dict], target_slug: str) -> dict | None:
    """Choisit le meilleur candidat parmi des résultats de recherche / un index.

    Règles strictes pour éviter les faux positifs (afficher les lecteurs FS d'un
    AUTRE drama serait pire que pas de match du tout) :
      1. slug exact
      2. préfixe (un slug est le début de l'autre, longueur >= 6)
      3. >= 2 tokens significatifs (>= 5 chars) partagés — un seul token partagé
         est TOUJOURS refusé : des mots courants (« world », « first »,
         « special »…) et même rares (« century ») créent des faux positifs.
    """
    if not candidates:
        return None

    # 1. Exact
    for c in candidates:
        if c["slug"] == target_slug:
            return c

    # 2. Préfixe (ex: « 6ixtynin9 » vs « 6ixtynin9-la-srie »)
    for c in candidates:
        a, b = c["slug"], target_slug
        if min(len(a), len(b)) >= 6 and (a.startswith(b) or b.startswith(a)):
            return c

    # 3. >= 2 tokens significatifs partagés
    target_tokens = {w for w in target_slug.split("-") if len(w) >= 5}
    if len(target_tokens) < 2:
        return None
    best: dict | None = None
    best_score = 0
    for c in candidates:
        c_tokens = {w for w in c["slug"].split("-") if len(w) >= 5}
        shared = target_tokens & c_tokens
        score = len(shared)
        if score >= 2 and score > best_score:
            best = c
            best_score = score
    return best


def parse_frenchstream_episodes(data: dict) -> dict:
    """Normalise le JSON d'épisodes FrenchStream.

    Entrée : {"vf": {"1": {...lecteurs...}}, "vostfr": {...}, "vo": {...}, "info": {...}}
    Sortie : {"vf": {1: [DramaServer-like]}, "vostfr": {...}, "vo": {...}}
    """
    versions: dict[str, dict[int, list[dict]]] = {}
    for version in ("vf", "vostfr", "vo"):
        raw = data.get(version) or {}
        episodes: dict[int, list[dict]] = {}
        for num_s, links in raw.items():
            if not isinstance(links, dict):
                continue
            try:
                num = int(num_s)
            except (TypeError, ValueError):
                continue
            servers = []
            for name, url in links.items():
                if not isinstance(url, str) or not url.strip():
                    continue
                servers.append({
                    "server_name": str(name).strip() or f"Serveur #{len(servers) + 1}",
                    "server_link": url.strip(),
                    "server_type": "embed",
                })
            if servers:
                episodes[num] = servers
        versions[version] = episodes
    return versions


# Affiche de série depuis la fiche : data-affiche="https://image.tmdb.org/t/p/w500/..."
_FICHE_AFFICHE = re.compile(r'data-affiche="([^"]+)"')
# Titre depuis la fiche : data-title="Comme un Rat - Saison 1"
_FICHE_TITLE = re.compile(r'data-title="([^"]+)"')


def parse_frenchstream_poster(html: str) -> str | None:
    """Extrait l'affiche (poster TMDB) d'une fiche FrenchStream."""
    m = _FICHE_AFFICHE.search(html)
    return m.group(1) if m else None


def parse_frenchstream_fiche_title(html: str) -> str | None:
    """Extrait le titre (ex: « Comme un Rat - Saison 1 ») d'une fiche FrenchStream."""
    m = _FICHE_TITLE.search(html)
    return m.group(1) if m else None


def parse_frenchstream_sitemap(xml: str) -> dict[str, int]:
    """Construit l'index {slug_série_normalisé: newsid} depuis le sitemap.

    Ne retient que les fiches de séries (slug contenant « saison ») pour éviter
    de matcher des films (ex: « taxi-driver-1976 » est le film de Scorsese,
    pas la série coréenne). Pour un même slug (plusieurs saisons), la première
    saison rencontrée est prioritaire : le JSON d'épisodes servira de garde-fou.
    """
    index: dict[str, int] = {}
    for newsid_s, slug in _SITEMAP_URL.findall(xml):
        if "saison" not in slug:
            continue
        newsid = int(newsid_s)
        base = _SLUG_SAISON.sub("", slug)
        if not base:
            continue
        # Priorité : première saison (saison-1) > autres saisons > rien
        current = index.get(base)
        if current is None:
            index[base] = newsid
        elif slug.endswith("saison-1") or "-saison-1-" in slug:
            index[base] = newsid
    return index


def episode_servers(parsed: dict, number: str, preferred: str = "vostfr") -> list[dict]:
    """Retourne les serveurs d'un épisode donné, VOSTFR d'abord, puis VF, puis VO.

    Note : les clés d'épisodes sont des int en mémoire mais des chaînes après un
    aller-retour JSON (cache) — on gère les deux formes.
    """
    try:
        num = int(number)
    except (TypeError, ValueError):
        return []
    for version in (preferred, "vostfr", "vf", "vo"):
        eps = parsed.get(version) or {}
        if num in eps:
            return [dict(s, version=version) for s in eps[num]]
        key = str(num)
        if key in eps:
            return [dict(s, version=version) for s in eps[key]]
    return []
