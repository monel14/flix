from __future__ import annotations

import json
import re
import urllib.parse
from typing import TypedDict

from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Genres d'Animés disponibles sur Voiranime
# ---------------------------------------------------------------------------

VOIRANIME_GENRES = [
    {"slug": "action", "label": "Action"},
    {"slug": "adventure", "label": "Aventure"},
    {"slug": "chinese", "label": "Animation Chinoise (Donghua)"},
    {"slug": "comedy", "label": "Comédie"},
    {"slug": "drama", "label": "Drame"},
    {"slug": "ecchi", "label": "Ecchi"},
    {"slug": "fantasy", "label": "Fantastique / Isekai"},
    {"slug": "horror", "label": "Horreur"},
    {"slug": "mahou-shoujo", "label": "Mahou Shoujo"},
    {"slug": "mecha", "label": "Mecha"},
    {"slug": "music", "label": "Musique"},
    {"slug": "mystery", "label": "Mystère"},
    {"slug": "psychological", "label": "Psychologique"},
    {"slug": "romance", "label": "Romance"},
    {"slug": "sci-fi", "label": "Science-Fiction"},
    {"slug": "slice-of-life", "label": "Tranche de vie"},
    {"slug": "sports", "label": "Sport"},
    {"slug": "supernatural", "label": "Surnaturel"},
    {"slug": "thriller", "label": "Thriller"},
]


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class AnimeItem(TypedDict):
    title: str
    slug: str
    url: str
    image: str
    version: str
    type: str       # "anime"
    latest_episode: str | None


class AnimeDetail(TypedDict):
    title: str
    slug: str
    url: str
    image: str
    version: str
    type: str       # "anime"
    synopsis: str
    genres: list[str]
    year: str
    status: str
    episodes: list[AnimeEpisode]
    first_episode_id: str | None


class AnimeEpisode(TypedDict):
    episode_id: str  # ex: "the-ogres-bride-01-vostfr"
    number: str      # "1"
    title: str       # "Épisode 1"
    url: str
    version: str     # "VOSTFR" ou "VF"


class AnimeServer(TypedDict):
    server_name: str
    server_link: str
    server_type: str
    version: str


# ---------------------------------------------------------------------------
# Fonctions de parsing
# ---------------------------------------------------------------------------

# WordPress suffixe les variantes responsives en « -LARGEURxHAUTEUR » ; le
# fichier original (sans suffixe) existe toujours à côté. Ce motif permet de
# remonter à la pleine résolution même quand le srcset ne contient que des
# vignettes.
_WORDPRESS_SIZE_SUFFIX = re.compile(r"-\d+x\d+(?=\.(?:jpg|jpeg|png|webp))")


def _best_voiranime_src(img_tag) -> str:
    """Sélectionne la plus grande image disponible (srcset trié décroissant
    chez voir-anime.to) puis remonte à l'originale pleine résolution."""
    srcset = img_tag.get("srcset", "")
    if srcset:
        candidates: list[tuple[int, str]] = []
        for part in srcset.split(","):
            part = part.strip()
            if not part:
                continue
            pieces = part.split()
            if not pieces:
                continue
            width = 0
            if len(pieces) > 1 and pieces[1].endswith("w") and pieces[1][:-1].isdigit():
                width = int(pieces[1][:-1])
            candidates.append((width, pieces[0]))
        if candidates:
            # Tri par largeur décroissante : plus grand affichage d'abord,
            # indépendamment de l'ordre (croissant ou décroissant) du site.
            candidates.sort(key=lambda item: item[0], reverse=True)
            return _WORDPRESS_SIZE_SUFFIX.sub("", candidates[0][1])
    return img_tag.get("src") or img_tag.get("data-src", "")


def _extract_voiranime_image(img_tag) -> str:
    """
    Extrait l'image en pleine résolution et l'encapsule dans le proxy
    pour contourner l'anti-hotlink 403 de voir-anime.to.
    """
    if not img_tag:
        return ""

    src = _best_voiranime_src(img_tag).strip()
    if not src:
        return ""

    if src.startswith("//"):
        src = "https:" + src
    elif src.startswith("/"):
        src = "https://voir-anime.to" + src

    return f"/api/image-proxy?url={urllib.parse.quote_plus(src)}"


def _extract_anime_slug(url: str) -> str:
    return url.rstrip("/").split("/")[-1]


def parse_voiranime_list(html: str) -> list[AnimeItem]:
    """Parse une liste d'animés (nouveautés, catalogue ou genre)."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[AnimeItem] = []

    cards = soup.select(".page-item-detail, .c-tabs-item__content")
    for card in cards:
        title_a = card.select_one(".post-title a, h3 a, h4 a, .item-title a")
        if not title_a:
            continue
        href = title_a.get("href", "").rstrip("/")
        slug = _extract_anime_slug(href)
        title = title_a.text.strip()

        img_tag = card.select_one("img")
        image = _extract_voiranime_image(img_tag)

        latest_ep_tag = card.select_one(".list-chapter .chapter-item a, .chapter a, .btn-link")
        latest_ep = latest_ep_tag.text.strip() if latest_ep_tag else None

        version = "VF" if "vf" in title.lower() or "vf" in slug.lower() else "VOSTFR"

        items.append(AnimeItem(
            title=title,
            slug=slug,
            url=href,
            image=image,
            version=version,
            type="anime",
            latest_episode=latest_ep,
        ))

    return items


def parse_voiranime_detail(html: str, slug: str) -> AnimeDetail:
    """Parse la fiche complète d'un animé avec ses épisodes."""
    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.select_one(".post-title h1, .post-title h3, h1")
    title = title_tag.text.strip() if title_tag else slug.replace("-", " ").title()

    poster_tag = soup.select_one(".summary_image img, .tab-summary img, .post-thumb img")
    image = _extract_voiranime_image(poster_tag)

    synopsis_tag = soup.select_one(".description-summary .summary__content, .manga-excerpt, .summary__content")
    synopsis = synopsis_tag.text.strip() if synopsis_tag else ""

    genres = [a.text.strip() for a in soup.select(".genres-content a")]

    status = ""
    year = ""
    for item in soup.select(".post-content_item"):
        heading = item.select_one(".summary-heading")
        content = item.select_one(".summary-content")
        if not heading or not content:
            continue
        h_text = heading.text.strip().lower()
        c_text = content.text.strip()
        if "statut" in h_text or "status" in h_text:
            status = c_text
        elif "date" in h_text or "an" in h_text or "release" in h_text:
            m_year = re.search(r"\d{4}", c_text)
            year = m_year.group(0) if m_year else c_text

    # Extraction des épisodes
    raw_episodes: list[AnimeEpisode] = []
    for li in soup.select("li.wp-manga-chapter"):
        a = li.select_one("a")
        if not a:
            continue
        href = a.get("href", "").rstrip("/")
        ep_slug = href.split("/")[-1]
        ep_title = a.text.strip()

        # Numérotation
        m_num = re.search(r"-\s*(\d+)\s*(?:VOSTFR|VF)?\s*(?:-\s*\d+)?$", ep_title, re.IGNORECASE) or re.search(r"(\d+)", ep_title)
        ep_num = m_num.group(1).lstrip("0") if m_num else "1"
        if not ep_num:
            ep_num = "1"

        version = "VF" if "vf" in ep_title.lower() or "vf" in ep_slug.lower() else "VOSTFR"
        clean_title = f"Épisode {ep_num}" if not ep_title.lower().startswith("film") else ep_title

        raw_episodes.append(AnimeEpisode(
            episode_id=ep_slug,
            number=ep_num,
            title=clean_title,
            url=href,
            version=version,
        ))

    episodes = list(reversed(raw_episodes)) if raw_episodes else []
    first_episode_id = episodes[0]["episode_id"] if episodes else None
    version_global = "VF" if "vf" in title.lower() or "vf" in slug.lower() else "VOSTFR"

    return AnimeDetail(
        title=title,
        slug=slug,
        url=f"https://voir-anime.to/anime/{slug}/",
        image=image,
        version=version_global,
        type="anime",
        synopsis=synopsis,
        genres=genres,
        year=year,
        status=status,
        episodes=episodes,
        first_episode_id=first_episode_id,
    )


def parse_voiranime_servers(html: str) -> list[AnimeServer]:
    """Parse les serveurs vidéo d'un épisode d'animé."""
    servers: list[AnimeServer] = []

    m = re.search(r"var\s+thisChapterSources\s*=\s*(\{.*?\});", html, re.DOTALL)
    if m:
        try:
            sources_dict = json.loads(m.group(1))
            for idx, (name, iframe_html) in enumerate(sources_dict.items(), 1):
                clean_name = name.replace("☰", "").strip() or f"Serveur #{idx}"
                src_m = re.search(r'src=[\"\']([^\"\']+)[\"\']', iframe_html)
                link = src_m.group(1) if src_m else ""
                if link:
                    servers.append(AnimeServer(
                        server_name=clean_name,
                        server_link=link,
                        server_type="embed",
                        version="VOSTFR",
                    ))
        except Exception:
            pass

    if not servers:
        soup = BeautifulSoup(html, "html.parser")
        for idx, ifr in enumerate(soup.select(".chapter-video-frame iframe, iframe"), 1):
            src = ifr.get("src") or ifr.get("data-src")
            if src and not src.startswith("about:") and "google" not in src and "disqus" not in src:
                servers.append(AnimeServer(
                    server_name=f"Serveur #{idx}",
                    server_link=src,
                    server_type="embed",
                    version="VOSTFR",
                ))

    return servers


def parse_voiranime_search(html: str) -> list[AnimeItem]:
    """Parse les résultats de recherche sur voir-anime.to."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[AnimeItem] = []

    for row in soup.select(".c-tabs-item__content"):
        title_a = row.select_one(".post-title a, h3 a, h4 a")
        if not title_a:
            continue
        href = title_a.get("href", "").rstrip("/")
        slug = _extract_anime_slug(href)
        title = title_a.text.strip()

        img_tag = row.select_one(".tab-thumb img")
        image = _extract_voiranime_image(img_tag)

        version = "VF" if "vf" in title.lower() or "vf" in slug.lower() else "VOSTFR"

        items.append(AnimeItem(
            title=title,
            slug=slug,
            url=href,
            image=image,
            version=version,
            type="anime",
            latest_episode=None,
        ))

    return items


def get_voiranime_last_page(html: str) -> int:
    """Extrait le numéro de la dernière page."""
    soup = BeautifulSoup(html, "html.parser")
    pages = []
    for a in soup.select(".wp-pagenavi a, .pagination a, .nav-links a"):
        href = a.get("href", "")
        m = re.search(r"/page/(\d+)/?", href)
        if m:
            pages.append(int(m.group(1)))
        elif a.text.strip().isdigit():
            pages.append(int(a.text.strip()))

    return max(pages) if pages else 1
