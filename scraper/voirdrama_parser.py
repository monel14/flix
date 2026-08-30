from __future__ import annotations

import json
import re
import urllib.parse
from typing import TypedDict

from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Genres de Dramas disponibles sur Voirdrama
# ---------------------------------------------------------------------------

VOIRDRAMA_GENRES = [
    {"slug": "action", "label": "Action"},
    {"slug": "affaires", "label": "Affaires"},
    {"slug": "amitie", "label": "Amitié"},
    {"slug": "arts-martiaux", "label": "Arts martiaux"},
    {"slug": "aventure", "label": "Aventure"},
    {"slug": "comedie", "label": "Comédie"},
    {"slug": "contexte-scolaire", "label": "Contexte scolaire"},
    {"slug": "crime", "label": "Crime"},
    {"slug": "culinaire", "label": "Culinaire"},
    {"slug": "documentaire", "label": "Documentaire"},
    {"slug": "drame", "label": "Drame"},
    {"slug": "famille", "label": "Famille"},
    {"slug": "fantastique", "label": "Fantastique"},
    {"slug": "guerre", "label": "Guerre"},
    {"slug": "historique", "label": "Historique"},
    {"slug": "horreur", "label": "Horreur"},
    {"slug": "jeunesse", "label": "Jeunesse"},
    {"slug": "judiciaire", "label": "Judiciaire"},
    {"slug": "mature", "label": "Mature"},
    {"slug": "medical", "label": "Médical"},
    {"slug": "melodrame", "label": "Mélodrame"},
    {"slug": "militaire", "label": "Militaire"},
    {"slug": "musique", "label": "Musique"},
    {"slug": "mystere", "label": "Mystère"},
    {"slug": "politique", "label": "Politique"},
    {"slug": "psychologique", "label": "Psychologique"},
    {"slug": "romance", "label": "Romance"},
    {"slug": "sf", "label": "Science-Fiction"},
    {"slug": "sitcom", "label": "Sitcom"},
    {"slug": "sport", "label": "Sport"},
    {"slug": "surnaturel", "label": "Surnaturel"},
    {"slug": "thriller", "label": "Thriller"},
    {"slug": "tokusatsu", "label": "Tokusatsu"},
    {"slug": "vie-quotidienne", "label": "Vie quotidienne"},
    {"slug": "wuxia", "label": "Wuxia"},
]


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class DramaItem(TypedDict):
    title: str
    slug: str
    url: str
    image: str
    version: str
    type: str       # "drama"
    latest_episode: str | None


class DramaDetail(TypedDict):
    title: str
    slug: str
    url: str
    image: str
    version: str
    type: str       # "drama"
    synopsis: str
    genres: list[str]
    year: str
    country: str
    status: str
    episodes: list[DramaEpisode]
    first_episode_id: str | None


class DramaEpisode(TypedDict):
    episode_id: str  # ex: "100-days-my-prince-01-vostfr"
    number: str      # "1"
    title: str       # "Épisode 1"
    url: str
    version: str     # "VOSTFR" ou "VF"


class DramaServer(TypedDict):
    server_name: str
    server_link: str
    server_type: str
    version: str


# ---------------------------------------------------------------------------
# Fonctions de parsing
# ---------------------------------------------------------------------------

def _extract_voirdrama_image(img_tag) -> str:
    """
    Extrait l'image à la plus haute résolution possible et l'encapsule
    dans le proxy local (/api/image-proxy) pour contourner l'erreur 403 (anti-hotlink de voirdrama).
    """
    if not img_tag:
        return ""

    # 1. Vérifier si un srcset avec plus haute résolution (ex: 350x476) est présent
    srcset = img_tag.get("srcset", "")
    if srcset:
        parts = [p.strip().split()[0] for p in srcset.split(",") if p.strip()]
        src = parts[-1] if parts else (img_tag.get("src") or img_tag.get("data-src", ""))
    else:
        src = img_tag.get("src") or img_tag.get("data-src", "")

    if not src:
        return ""

    # Normalisation de l'URL absolue
    if src.startswith("//"):
        src = "https:" + src
    elif src.startswith("/"):
        src = "https://voirdrama.to" + src

    # Encapsulation via le proxy d'image local pour injecter le Referer voirdrama.to
    return f"/api/image-proxy?url={urllib.parse.quote_plus(src)}"


def _extract_drama_slug(url: str) -> str:
    return url.rstrip("/").split("/")[-1]


def _extract_drama_version(card=None, slug: str = "", title: str = "") -> str:
    """Détecte la version pour un drama (VF ou VOSTFR)."""
    if card is not None:
        v_tag = card.select_one(".version, .badge, .status, .quality, .meta .quality, .meta .version")
        if v_tag and v_tag.text.strip():
            raw = v_tag.text.strip().upper()
            if "VF" in raw:
                return "VF"
            if "VOSTFR" in raw or "VOST" in raw:
                return "VOSTFR"

    slug_lower = (slug or "").lower()
    title_lower = (title or "").lower()

    if (
        re.search(r"(?:^|[\s\-_(\[])(vf|vff|vfq)(?:$|[\s\-_)\]])", title_lower)
        or slug_lower.endswith("-vf")
        or "-vf-" in slug_lower
        or " vf" in title_lower
        or "(vf)" in title_lower
        or "[vf]" in title_lower
    ):
        return "VF"

    return "VOSTFR"


def parse_voirdrama_list(html: str) -> list[DramaItem]:
    """Parse une page de liste ou de catégorie de dramas."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[DramaItem] = []

    cards = soup.select(".page-item-detail, .c-tabs-item__content")
    for card in cards:
        title_a = card.select_one(".post-title a, h3 a, h4 a, .item-title a")
        if not title_a:
            continue
        href = title_a.get("href", "").rstrip("/")
        slug = _extract_drama_slug(href)
        title = title_a.text.strip()

        img_tag = card.select_one("img")
        image = _extract_voirdrama_image(img_tag)

        latest_ep_tag = card.select_one(".list-chapter .chapter-item a, .chapter a, .btn-link")
        latest_ep = latest_ep_tag.text.strip() if latest_ep_tag else None

        version = _extract_drama_version(card=card, slug=slug, title=title)

        items.append(DramaItem(
            title=title,
            slug=slug,
            url=href,
            image=image,
            version=version,
            type="drama",
            latest_episode=latest_ep,
        ))

    return items


def parse_voirdrama_detail(html: str, slug: str) -> DramaDetail:
    """Parse la fiche complète d'un drama avec l'ensemble de ses épisodes."""
    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.select_one(".post-title h1, .post-title h3, h1")
    title = title_tag.text.strip() if title_tag else slug.replace("-", " ").title()

    poster_tag = soup.select_one(".summary_image img, .tab-summary img, .post-thumb img")
    image = _extract_voirdrama_image(poster_tag)

    synopsis_tag = soup.select_one(".description-summary .summary__content, .manga-excerpt, .summary__content")
    synopsis = synopsis_tag.text.strip() if synopsis_tag else ""

    genres = [a.text.strip() for a in soup.select(".genres-content a")]

    country = ""
    status = ""
    year = ""
    for item in soup.select(".post-content_item"):
        heading = item.select_one(".summary-heading")
        content = item.select_one(".summary-content")
        if not heading or not content:
            continue
        h_text = heading.text.strip().lower()
        c_text = content.text.strip()
        if "pays" in h_text or "country" in h_text:
            country = c_text
        elif "statut" in h_text or "status" in h_text:
            status = c_text
        elif "date" in h_text or "an" in h_text or "release" in h_text:
            m_year = re.search(r"\d{4}", c_text)
            year = m_year.group(0) if m_year else c_text

    # Extraction des épisodes
    raw_episodes: list[DramaEpisode] = []
    for li in soup.select("li.wp-manga-chapter"):
        a = li.select_one("a")
        if not a:
            continue
        href = a.get("href", "").rstrip("/")
        ep_slug = href.split("/")[-1]
        ep_title = a.text.strip()

        # Numérotation de l'épisode
        m_num = re.search(r"-\s*(\d+)\s*(?:VOSTFR|VF)?\s*(?:-\s*\d+)?$", ep_title, re.IGNORECASE) or re.search(r"(\d+)", ep_title)
        ep_num = m_num.group(1).lstrip("0") if m_num else "1"
        if not ep_num:
            ep_num = "1"

        version = "VF" if "vf" in ep_title.lower() or "vf" in ep_slug.lower() else "VOSTFR"
        clean_title = f"Épisode {ep_num}" if not ep_title.lower().startswith("film") else ep_title

        raw_episodes.append(DramaEpisode(
            episode_id=ep_slug,
            number=ep_num,
            title=clean_title,
            url=href,
            version=version,
        ))

    # Ordre chronologique naturel (Épisode 1, Épisode 2, ...)
    episodes = list(reversed(raw_episodes)) if raw_episodes else []
    first_episode_id = episodes[0]["episode_id"] if episodes else None
    version_global = "VF" if "vf" in title.lower() or "vf" in slug.lower() else "VOSTFR"

    return DramaDetail(
        title=title,
        slug=slug,
        url=f"https://voirdrama.to/drama/{slug}/",
        image=image,
        version=version_global,
        type="drama",
        synopsis=synopsis,
        genres=genres,
        year=year,
        country=country,
        status=status,
        episodes=episodes,
        first_episode_id=first_episode_id,
    )


def parse_voirdrama_servers(html: str) -> list[DramaServer]:
    """Parse l'ensemble des serveurs vidéo disponibles pour un épisode de drama."""
    servers: list[DramaServer] = []

    # Méthode 1 : Extraction via l'objet JavaScript `var thisChapterSources = {...}`
    m = re.search(r"var\s+thisChapterSources\s*=\s*(\{.*?\});", html, re.DOTALL)
    if m:
        try:
            sources_dict = json.loads(m.group(1))
            for idx, (name, iframe_html) in enumerate(sources_dict.items(), 1):
                clean_name = name.replace("☰", "").strip() or f"Serveur #{idx}"
                src_m = re.search(r'src=[\"\']([^\"\']+)[\"\']', iframe_html)
                link = src_m.group(1) if src_m else ""
                if link:
                    servers.append(DramaServer(
                        server_name=clean_name,
                        server_link=link,
                        server_type="embed",
                        version="VOSTFR",
                    ))
        except Exception:
            pass

    # Méthode 2 : Fallback sur les iframes directes de la page
    if not servers:
        soup = BeautifulSoup(html, "html.parser")
        for idx, ifr in enumerate(soup.select(".chapter-video-frame iframe, iframe"), 1):
            src = ifr.get("src") or ifr.get("data-src")
            if src and not src.startswith("about:") and "google" not in src and "disqus" not in src:
                servers.append(DramaServer(
                    server_name=f"Serveur #{idx}",
                    server_link=src,
                    server_type="embed",
                    version="VOSTFR",
                ))

    return servers


def parse_voirdrama_search(html: str) -> list[DramaItem]:
    """Parse les résultats de recherche de voirdrama.to."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[DramaItem] = []

    for row in soup.select(".c-tabs-item__content"):
        title_a = row.select_one(".post-title a, h3 a, h4 a")
        if not title_a:
            continue
        href = title_a.get("href", "").rstrip("/")
        slug = _extract_drama_slug(href)
        title = title_a.text.strip()

        img_tag = row.select_one(".tab-thumb img")
        image = _extract_voirdrama_image(img_tag)

        version = _extract_drama_version(card=row, slug=slug, title=title)

        items.append(DramaItem(
            title=title,
            slug=slug,
            url=href,
            image=image,
            version=version,
            type="drama",
            latest_episode=None,
        ))

    return items


def get_voirdrama_last_page(html: str) -> int:
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
