"""Métadonnées SEO & partage — source unique de vérité du <head>.

Toutes les métadonnées de page (title, description, canonical, Open Graph,
Twitter Cards, JSON-LD) sont construites ici et rendues par base.html.
Aucun template ne redéfinit sa propre logique de métadonnées.

Domaine public : variable d'environnement SITE_URL (ex. "https://nokaflix.tv").
Sans elle, on retombe sur l'origine réellement vue par la requête
(en tenant compte des en-têtes de proxy X-Forwarded-*).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from fastapi import Request

SITE_NAME = "Nokaflix"

# Valeurs génériques = exactement celles qui étaient codées en dur dans
# base.html avant la centralisation (aucune perte d'information, ni de
# changement d'identité).
DEFAULT_TITLE = "Nokaflix — Regarder Films, Séries, K-Dramas & Animés en Streaming HD"
DEFAULT_OG_TITLE = "Nokaflix — Films, Séries, K-Dramas & Animés en Streaming HD"
DEFAULT_DESCRIPTION = (
    "Plateforme Nokaflix de streaming gratuit pour regarder des films, "
    "séries, K-Dramas et animés en VF et VOSTFR HD."
)
DEFAULT_OG_DESCRIPTION = (
    "Regardez des milliers de films, séries, K-Dramas et animés en streaming "
    "gratuit VF et VOSTFR sans coupure."
)
DEFAULT_TWITTER_TITLE = "Nokaflix — Streaming Gratuit HD"
DEFAULT_TWITTER_DESCRIPTION = "Films, Séries, K-Dramas & Animés en streaming VF et VOSTFR."
DEFAULT_IMAGE = "https://images.unsplash.com/photo-1574375927938-d5a98e8ffe85?q=80&w=1200"

_DESCRIPTION_MAX = 160


def site_origin(request: Request) -> str:
    """Origine publique du site, sans slash final.

    Priorité : SITE_URL (configuration) > en-têtes de proxy > base_url.
    """
    configured = (os.getenv("SITE_URL") or "").strip()
    if configured:
        return configured.rstrip("/")
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.url.netloc
    return f"{scheme}://{host}".rstrip("/")


def make_absolute(request: Request, value: str) -> str:
    """Rend une URL absolue sur le domaine public (images proxifiées incluses)."""
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if not value.startswith("/"):
        value = "/" + value
    return site_origin(request) + value


def _clip(text: str, limit: int = _DESCRIPTION_MAX) -> str:
    """Tronque proprement une description sans couper en plein mot."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…"


@dataclass
class SeoMeta:
    """Jeu complet de métadonnées d'une page (une seule source de vérité)."""

    title: str
    description: str
    canonical: str            # URL absolue, unique, indexable
    image: str                # URL absolue (fallback : visuel global du site)
    og_title: str = ""
    og_description: str = ""
    twitter_title: str = ""
    twitter_description: str = ""
    og_type: str = "website"
    schema_type: str = ""     # "Movie" | "TVSeries" | "" (jamais deviné)
    work_name: str = ""       # nom nu de l'œuvre (JSON-LD `name` — jamais suffixé marque)
    content_image: str = ""   # image de l'œuvre (JSON-LD `image` — jamais le fallback global)
    year: str = ""
    genres: list[str] = field(default_factory=list)

    @property
    def json_ld(self) -> dict | None:
        """Données structurées — uniquement si le type d'œuvre est fiable.

        `name` est le titre exact de l'œuvre (pas le titre de page suffixé
        « Streaming — Nokaflix ») et `image` n'est jamais l'image générique
        de repli : rien de fabriqué.
        """
        if self.schema_type not in ("Movie", "TVSeries") or not self.work_name:
            return None
        data: dict = {
            "@context": "https://schema.org",
            "@type": self.schema_type,
            "name": self.work_name,
            "url": self.canonical,
        }
        if self.description:
            data["description"] = self.description
        if self.content_image:
            data["image"] = self.content_image
        if self.year:
            data["dateCreated"] = self.year
        if self.genres:
            data["genre"] = self.genres
        return data


def page_seo(
    request: Request,
    *,
    title: str = "",
    og_title: str = "",
    twitter_title: str = "",
    description: str = "",
    path: str | None = None,
    image: str = "",
    og_type: str = "website",
    schema_type: str = "",
    work_name: str = "",
    content_image: str = "",
    year: str = "",
    genres: list[str] | None = None,
) -> SeoMeta:
    """Construit le SeoMeta d'une page avec fallbacks cohérents et URLs absolues.

    `path` : chemin canonique relatif (sans query-string parasite). Par défaut,
    le chemin de la requête courante.
    """
    canonical_path = path if path is not None else request.url.path
    canonical = site_origin(request) + canonical_path
    return SeoMeta(
        title=title.strip() or DEFAULT_TITLE,
        description=_clip(description) or DEFAULT_DESCRIPTION,
        canonical=canonical,
        image=make_absolute(request, image) or DEFAULT_IMAGE,
        og_title=(og_title or title).strip() or DEFAULT_OG_TITLE,
        og_description=_clip(description) or DEFAULT_OG_DESCRIPTION,
        twitter_title=(twitter_title or og_title or title).strip() or DEFAULT_TWITTER_TITLE,
        twitter_description=_clip(description) or DEFAULT_TWITTER_DESCRIPTION,
        og_type=og_type,
        schema_type=schema_type,
        work_name=work_name.strip(),
        content_image=content_image,
        year=(year or "").strip(),
        genres=[g for g in (genres or []) if g],
    )


def content_seo(
    request: Request,
    *,
    item: dict,
    path: str,
    title_suffix: str,
    kind_label: str,
    content_type: str = "",
) -> SeoMeta:
    """SeoMeta d'une fiche de contenu (film, série).

    - `content_type` fiable ("Movie" / "Series") -> JSON-LD Movie/TVSeries.
      Toute autre valeur (inconnue, non fiable) -> pas de JSON-LD.
    - Le synopsis, les genres et l'année proviennent de la source réelle ;
      rien n'est fabriqué.
    """
    title = (item.get("title") or "").strip()
    synopsis = (item.get("synopsis") or "").strip()
    if synopsis:
        description = synopsis
    elif title:
        description = f"Regarder {title} en streaming gratuit."
    else:
        description = ""
    schema_type = {"Movie": "Movie", "Series": "TVSeries"}.get(content_type, "")
    og_type = {"Movie": "video.movie", "Series": "video.tv_show"}.get(content_type, "website")
    return page_seo(
        request,
        title=f"{title} — {title_suffix} — {SITE_NAME}" if title else "",
        og_title=f"{title} {kind_label} — {SITE_NAME}" if title else "",
        description=description,
        path=path,
        image=item.get("image", ""),
        og_type=og_type,
        schema_type=schema_type,
        work_name=title,
        content_image=make_absolute(request, item.get("image", "")),
        year=item.get("year", ""),
        genres=item.get("genres") or [],
    )
