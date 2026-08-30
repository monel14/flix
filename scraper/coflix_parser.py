from __future__ import annotations

import re
from typing import TypedDict

from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# Genres supportés par Coflix
# ---------------------------------------------------------------------------

AVAILABLE_GENRES = [
    {"slug": "action", "label": "Action"},
    {"slug": "animation", "label": "Animation"},
    {"slug": "aventure", "label": "Aventure"},
    {"slug": "comedie", "label": "Comédie"},
    {"slug": "crime", "label": "Crime"},
    {"slug": "documentaire", "label": "Documentaire"},
    {"slug": "drame", "label": "Drame"},
    {"slug": "famille", "label": "Famille"},
    {"slug": "fantastique", "label": "Fantastique"},
    {"slug": "guerre", "label": "Guerre"},
    {"slug": "histoire", "label": "Histoire"},
    {"slug": "horreur", "label": "Horreur"},
    {"slug": "musique", "label": "Musique"},
    {"slug": "mystere", "label": "Mystère"},
    {"slug": "romance", "label": "Romance"},
    {"slug": "science-fiction", "label": "Science-Fiction"},
    {"slug": "thriller", "label": "Thriller"},
    {"slug": "western", "label": "Western"},
]


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class CoflixItem(TypedDict):
    title: str
    slug: str       # ex: "reacher-saison-4-vf"
    url: str        # URL complète de la fiche détail
    image: str
    version: str    # VF / VOSTFR / TrueFrench
    type: str       # "movie" | "series"


class CoflixHeroItem(TypedDict):
    title: str
    slug: str
    url: str
    image: str
    synopsis: str
    year: str
    version: str


class CoflixDetail(TypedDict):
    title: str
    slug: str
    url: str
    image: str
    version: str
    type: str
    content_type: str   # "Movie" | "Series"
    synopsis: str
    genres: list[str]
    year: str
    status: str
    movie_id: str       # ID interne coflix pour les listes AJAX (data-id)
    episode_id: str     # ID d'épisode / streaming pour le player (data-ep-name)
    related: list[CoflixItem]


class CoflixEpisode(TypedDict):
    episode_id: str
    season: str
    number: str
    title: str
    url: str


class CoflixServer(TypedDict):
    server_name: str
    server_link: str
    server_type: str
    version: str


# ---------------------------------------------------------------------------
# Parseurs
# ---------------------------------------------------------------------------

def _extract_slug(url: str) -> str:
    m = re.search(r"/film/([^/?#]+)", url)
    return m.group(1) if m else url.rsplit("/", 1)[-1]


def _extract_image(card) -> str:
    img = card.select_one("img")
    if img and img.get("src"):
        return img["src"]
    span = card.select_one("span[style*='background-image']")
    if span:
        m = re.search(r"url\('([^']+)'\)", span.get("style", ""))
        if m:
            return m.group(1)
    return ""


def parse_coflix_hero(html: str) -> list[CoflixHeroItem]:
    """Parse les slides à la une du #slider-main sur la page d'accueil."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[CoflixHeroItem] = []

    for slide in soup.select("#slider-main .slide"):
        link = slide.select_one("a[href*='/film/']")
        if not link:
            continue
        href = re.sub(r"/ep-\d+$", "", link.get("href", ""))
        if not href:
            continue

        title_tag = slide.select_one(".title.d-title, .title, h2")
        title = (title_tag.get("data-jp") or title_tag.text.strip()) if title_tag else ""

        thumb = slide.get("data-thumb", "")
        if not thumb:
            img = slide.select_one("img")
            thumb = img.get("src", "") if img else ""
        if thumb:
            # Le backdrop du Hero s'affiche jusqu'à ~1600px de large : on
            # demande la taille supérieure w780 quand la source suit le
            # schéma de tailles /wNNN/ ; home.html dérive l'URL w500 comme
            # repli silencieux si la w780 n'existe pas réellement.
            thumb = thumb.replace("/w300/", "/w780/")

        syn_tag = slide.select_one(".synopsis, .content p, p")
        synopsis = syn_tag.text.strip() if syn_tag else ""

        year_tag = slide.select_one(".year, .meta .year")
        year = year_tag.text.strip() if year_tag else ""

        version_tag = slide.select_one(".version, .badge, .meta .quality")
        version = version_tag.text.strip() if version_tag else ""

        items.append(CoflixHeroItem(
            title=title,
            slug=_extract_slug(href),
            url=href,
            image=thumb,
            synopsis=synopsis,
            year=year,
            version=version,
        ))

    return items


def _extract_coflix_version(card=None, slug: str = "", title: str = "", default: str = "") -> str:
    """Extrait la version linguistique depuis le DOM, le slug ou le titre."""
    if card is not None:
        version_tag = card.select_one(".version, .badge, .status, .quality, .meta .quality, .meta .version")
        if version_tag and version_tag.text.strip():
            raw = version_tag.text.strip().upper()
            if "VOSTFR" in raw or "VOST" in raw:
                return "VOSTFR"
            if "TRUEFRENCH" in raw:
                return "TRUEFRENCH"
            if "VF" in raw or "FRENCH" in raw:
                return "VF" if "FRENCH" not in raw else "FRENCH"
            return raw

    slug_lower = (slug or "").lower()
    title_lower = (title or "").lower()

    if (
        re.search(r"(?:^|[\s\-_(\[])(vostfr|vost|sub)(?:$|[\s\-_)\]])", title_lower)
        or slug_lower.endswith("-vostfr")
        or "-vostfr-" in slug_lower
        or "vostfr" in slug_lower
    ):
        return "VOSTFR"

    if (
        re.search(r"(?:^|[\s\-_(\[])(truefrench|true-french)(?:$|[\s\-_)\]])", title_lower)
        or slug_lower.endswith("-truefrench")
        or "truefrench" in slug_lower
    ):
        return "TRUEFRENCH"

    if (
        re.search(r"(?:^|[\s\-_(\[])(french)(?:$|[\s\-_)\]])", title_lower)
        or slug_lower.endswith("-french")
        or "-french-" in slug_lower
    ):
        return "FRENCH"

    if (
        re.search(r"(?:^|[\s\-_(\[])(vf|vff|vfq)(?:$|[\s\-_)\]])", title_lower)
        or slug_lower.endswith("-vf")
        or "-vf-" in slug_lower
        or " vf" in title_lower
        or "(vf)" in title_lower
        or "[vf]" in title_lower
    ):
        return "VF"

    if (
        re.search(r"(?:^|[\s\-_(\[])(multi)(?:$|[\s\-_)\]])", title_lower)
        or slug_lower.endswith("-multi")
        or "-multi-" in slug_lower
    ):
        return "MULTI"

    return default


def parse_coflix_list(html: str, section: str) -> list[CoflixItem]:
    """Parse une page /movies/ ou /series/."""
    soup = BeautifulSoup(html, "html.parser")
    content_type = "movie" if "movie" in section.lower() or "film" in section.lower() else "series"
    items: list[CoflixItem] = []

    for card in soup.select("div.item"):
        link = card.select_one("a.ani.poster, a.poster, a[href*='/film/']")
        if not link:
            continue
        href = re.sub(r"/ep-\d+$", "", link.get("href", ""))
        if not href:
            continue

        title_tag = card.select_one(".name.d-title, a.name.d-title, .d-title")
        title = ""
        if title_tag:
            title = title_tag.get("data-jp") or title_tag.text.strip()

        slug = _extract_slug(href)
        version = _extract_coflix_version(card=card, slug=slug, title=title)

        items.append(CoflixItem(
            title=title,
            slug=slug,
            url=href,
            image=_extract_image(card),
            version=version,
            type=content_type,
        ))

    return items


def parse_coflix_top(html: str) -> list[CoflixItem]:
    """Parse la réponse HTML de /ajax/movie/top."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[CoflixItem] = []

    for a in soup.select("a.item"):
        href = re.sub(r"/ep-\d+$", "", a.get("href", ""))
        if not href:
            continue
        name_tag = a.select_one(".name.d-title")
        title = (name_tag.get("data-jp") or name_tag.text.strip()) if name_tag else ""
        slug = _extract_slug(href)
        version = _extract_coflix_version(card=a, slug=slug, title=title)
        items.append(CoflixItem(
            title=title,
            slug=slug,
            url=href,
            image=_extract_image(a),
            version=version,
            type="movie",
        ))

    return items


def parse_coflix_detail(html: str, slug: str) -> CoflixDetail:
    """Parse la page de détail d'un film ou d'une série avec ses contenus similaires."""
    soup = BeautifulSoup(html, "html.parser")

    watch = soup.select_one("#watch-page")
    movie_id = watch.get("data-id", "") if watch else ""
    detail_url = watch.get("data-url", f"https://coflix.wiki/film/{slug}") if watch else ""

    # Extraction de l'ID d'épisode / lecteur (data-ep-name sur #watch-page ou URL /ep-XXXX)
    episode_id = ""
    if watch:
        episode_id = (
            watch.get("data-ep-name", "")
            or watch.get("data-episode-id", "")
            or watch.get("data-ep-id", "")
        )
    if not episode_id:
        m_ep = re.search(rf"/film/{re.escape(slug)}/ep-(\d+)", html) or re.search(rf"{re.escape(slug)}/ep-(\d+)", html)
        if m_ep:
            episode_id = m_ep.group(1)

    h1 = soup.select_one("h1.title.d-title, h1.d-title, h1.title")
    title = (h1.get("data-jp") or h1.text.strip()) if h1 else slug

    poster = soup.select_one("#w-info .poster img, .poster img")
    image = ""
    if poster:
        image = poster.get("src", "").replace("/w300/", "/w500/")

    synopsis_tag = soup.select_one(".synopsis .content p, .synopsis p, .synopsis .content")
    synopsis = synopsis_tag.text.strip() if synopsis_tag else ""

    genres = [a.text.strip() for a in soup.select(".list-genres a")]

    content_type = version = year = status = ""
    for div in soup.select(".bl-meta .meta > div"):
        text = div.get_text(" ", strip=True)
        if text.startswith("Type:"):
            content_type = text.replace("Type:", "").strip()
        elif text.startswith("Version:"):
            version = text.replace("Version:", "").strip()
        elif text.startswith("Date aired:"):
            m = re.search(r"\d{4}", text)
            year = m.group() if m else ""
        elif text.startswith("Status:"):
            status = text.replace("Status:", "").strip()

    item_type = "series" if content_type.lower() == "series" else "movie"

    # Extraction des films/séries liés (Recommandations / #related)
    related_items: list[CoflixItem] = []
    for card in soup.select("#related div.item"):
        link = card.select_one("a.ani.poster, a.poster, a[href*='/film/']")
        if not link:
            continue
        href = re.sub(r"/ep-\d+$", "", link.get("href", ""))
        if not href:
            continue
        title_tag = card.select_one(".name.d-title, a.name.d-title, .d-title")
        r_title = (title_tag.get("data-jp") or title_tag.text.strip()) if title_tag else ""
        version_tag = card.select_one(".version")
        r_version = version_tag.text.strip() if version_tag else ""
        related_items.append(CoflixItem(
            title=r_title,
            slug=_extract_slug(href),
            url=href,
            image=_extract_image(card),
            version=r_version,
            type=item_type,
        ))

    return CoflixDetail(
        title=title,
        slug=slug,
        url=detail_url,
        image=image,
        version=version,
        type=item_type,
        content_type=content_type,
        synopsis=synopsis,
        genres=genres,
        year=year,
        status=status,
        movie_id=movie_id,
        episode_id=episode_id,
        related=related_items,
    )


def parse_coflix_episodes(json_data: dict) -> list[CoflixEpisode]:
    """Parse la réponse JSON de /ajax/episode/list-episode."""
    html = json_data.get("html", "")
    soup = BeautifulSoup(html, "html.parser")
    episodes: list[CoflixEpisode] = []

    for ep in soup.select("a.ep-item, li a[data-id]"):
        ep_id = ep.get("data-id", "")
        if not ep_id:
            continue
        ep_num = ep.get("data-num", "")
        ep_href = ep.get("href", "")
        title_tag = ep.select_one(".title")
        ep_title = title_tag.text.strip() if title_tag else f"Épisode {ep_num}"
        ul = ep.find_parent("ul")
        season = ul.get("data-season", "1") if ul else "1"

        episodes.append(CoflixEpisode(
            episode_id=ep_id,
            season=season,
            number=ep_num,
            title=ep_title,
            url=ep_href,
        ))

    return episodes


def parse_coflix_servers(json_data: dict) -> list[CoflixServer]:
    """Parse la réponse JSON de /ajax/episode/player."""
    # Quand l'épisode est introuvable côté source, message est une string d'erreur
    message = json_data.get("message", [])
    if not isinstance(message, list):
        return []

    servers = []
    for idx, item in enumerate(message, 1):
        if not isinstance(item, dict):
            continue
        link = item.get("server_link", "")
        if not link:
            continue
        name = item.get("server_name")
        server_name = name.strip() if (isinstance(name, str) and name.strip()) else f"Serveur #{idx}"
        servers.append(CoflixServer(
            server_name=server_name,
            server_link=link,
            server_type=item.get("server_type", "embed"),
            version=item.get("version", ""),
        ))
    return servers


def parse_coflix_search(html: str) -> list[CoflixItem]:
    """Parse les résultats de /filter?keyword=..."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[CoflixItem] = []

    for card in soup.select("div.item"):
        link = card.select_one("a.ani.poster, a.poster, a[href*='/film/']")
        if not link:
            continue
        href = re.sub(r"/ep-\d+$", "", link.get("href", ""))
        if not href:
            continue

        title_tag = card.select_one(".name.d-title, a.name.d-title, .d-title")
        title = ""
        if title_tag:
            title = title_tag.get("data-jp") or title_tag.text.strip()

        slug = _extract_slug(href)
        version = _extract_coflix_version(card=card, slug=slug, title=title)

        # Détecter séries ou films depuis la section de la page
        is_series = "series" in html[:500].lower()
        content_type = "series" if is_series else "movie"

        items.append(CoflixItem(
            title=title,
            slug=slug,
            url=href,
            image=_extract_image(card),
            version=version,
            type=content_type,
        ))

    return items


def get_last_page(html: str) -> int:
    """Extrait le numéro de la dernière page depuis le HTML de liste."""
    import re as _re
    pages = _re.findall(r"\?page=(\d+)", html)
    return max(int(p) for p in pages) if pages else 1
