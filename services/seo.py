"""Métadonnées SEO & partage — source unique de vérité du <head>.

Toutes les métadonnées de page (title, description, canonical, Open Graph,
Twitter Cards, JSON-LD) sont construites ici et rendues par base.html.
Aucun template ne redéfinit sa propre logique de métadonnées.

Domaine public : variable d'environnement SITE_URL (ex. "https://nokatv.xyz").
Sans elle, on retombe sur l'origine réellement vue par la requête
(en tenant compte des en-têtes de proxy X-Forwarded-*).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from fastapi import Request

SITE_NAME = "NokaTV"

# Valeurs génériques = exactement celles qui étaient codées en dur dans
# base.html avant la centralisation (aucune perte d'information, ni de
# changement d'identité).
DEFAULT_TITLE = "NokaTV — Regarder Films, Séries, K-Dramas & Animés en Streaming HD"
DEFAULT_OG_TITLE = "NokaTV — Films, Séries, K-Dramas & Animés en Streaming HD"
DEFAULT_DESCRIPTION = (
    "Plateforme NokaTV de streaming gratuit pour regarder des films, "
    "séries, K-Dramas et animés en VF et VOSTFR HD."
)
DEFAULT_OG_DESCRIPTION = (
    "Regardez des milliers de films, séries, K-Dramas et animés en streaming "
    "gratuit VF et VOSTFR sans coupure."
)
DEFAULT_TWITTER_TITLE = "NokaTV — Streaming Gratuit HD"
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
    # Pages d'usage non indexables (players, recherche, ma-liste, erreurs).
    # `follow` est conservé : les liens vers les fiches restent suivis.
    noindex: bool = False
    # Données structurées additionnelles (WebSite, BreadcrumbList, ItemList…)
    # au-delà du JSON-LD d'œuvre éventuel.
    extra_json_ld: list[dict] = field(default_factory=list)

    @property
    def all_json_ld(self) -> list[dict]:
        """Tous les blocs JSON-LD de la page (œuvre + additionnels)."""
        blocks = [self.json_ld, *self.extra_json_ld]
        return [b for b in blocks if b]

    @property
    def json_ld(self) -> dict | None:
        """Données structurées — uniquement si le type d'œuvre est fiable.

        `name` est le titre exact de l'œuvre (pas le titre de page suffixé
        « Streaming — NokaTV ») et `image` n'est jamais l'image générique
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
    noindex: bool = False,
    extra_json_ld: list[dict] | None = None,
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
        noindex=noindex,
        extra_json_ld=list(extra_json_ld or []),
    )


def website_json_ld(request: Request) -> dict:
    """JSON-LD WebSite + SearchAction (page d'accueil uniquement).

    Déclare le moteur de recherche interne du site à Google
    (`/recherche?q=…`). Les URLs sont ancrées sur le domaine public.
    """
    origin = site_origin(request)
    return {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": SITE_NAME,
        "url": f"{origin}/",
        "potentialAction": {
            "@type": "SearchAction",
            "target": {
                "@type": "EntryPoint",
                "urlTemplate": f"{origin}/recherche?q={{search_term_string}}",
            },
            "query-input": "required name=search_term_string",
        },
    }


def breadcrumb_json_ld(request: Request, trail: list[tuple[str, str]]) -> dict:
    """JSON-LD BreadcrumbList depuis un fil de chemins (nom, chemin).

    Le chemin courant est le dernier élément du fil ; les URLs sont absolues.
    """
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": pos,
                "name": name,
                "item": make_absolute(request, path),
            }
            for pos, (name, path) in enumerate(trail, start=1)
        ],
    }


def item_list_json_ld(request: Request, entries: list[tuple[str, str]]) -> dict:
    """JSON-LD ItemList pour une page de catalogue (nom, chemin).

    Chaque entrée devient un ListItem pointant vers la fiche — les pages
    de liste décrivent ainsi leur contenu réel, sans le fabriquer.
    """
    return {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": pos,
                "url": make_absolute(request, path),
                "name": name,
            }
            for pos, (name, path) in enumerate(entries, start=1)
        ],
    }


def title_qualifiers(
    title: str,
    *,
    versions: Iterable[str] = (),
    year: str = "",
) -> str:
    """Qualificatifs réels d'une fiche, insérés dans son <title>.

    Stratégie « données réelles de la fiche, jamais d'ajout automatique » :
    - une version n'est affichée que si elle est réellement connue (label de
      la source, ou suffixe porté par le slug) — l'appelant ne transmet que
      ces cas, jamais de valeur par défaut ;
    - une mention déjà présente dans le titre (ex. « Black Torch (VF) ») n'est
      jamais dupliquée ;
    - l'année n'est ajoutée que si elle est présente et pas déjà dans le titre.

    Retourne « (VF) », « (VF/VOSTFR) », « (2024) », « (VF, 2024) » ou « ».
    """
    seen: list[str] = []
    for v in versions or ():
        label = (v or "").strip().upper()
        if not label or label in seen:
            continue
        if re.search(rf"\(\s*{re.escape(label)}\s*\)", title, re.IGNORECASE):
            continue  # déjà mentionné dans le titre : ne pas dupliquer
        seen.append(label)

    year_s = (year or "").strip()
    if year_s and re.search(rf"\b{re.escape(year_s)}\b", title):
        year_s = ""

    if not seen and not year_s:
        return ""
    if seen and year_s:
        return f"({'/'.join(seen)}, {year_s})"
    if seen:
        return f"({'/'.join(seen)})"
    return f"({year_s})"


def content_seo(
    request: Request,
    *,
    item: dict,
    path: str,
    title_suffix: str,
    kind_label: str,
    content_type: str = "",
    qualifiers: str = "",
    breadcrumbs: list[tuple[str, str]] | None = None,
) -> SeoMeta:
    """SeoMeta d'une fiche de contenu (film, série).

    - `content_type` fiable ("Movie" / "Series") -> JSON-LD Movie/TVSeries.
      Toute autre valeur (inconnue, non fiable) -> pas de JSON-LD.
    - Le synopsis, les genres et l'année proviennent de la source réelle ;
      rien n'est fabriqué.
    - `qualifiers` : chaîne déjà formatée (ex. « (VF, 2024) ») produite par
      `title_qualifiers()` ; elle ne contient que des données réelles de la
      fiche (version connue, année présente). Vide = rien à afficher.
    - `breadcrumbs` : sections mères (nom, chemin) ; le fil d'Ariane complet
      Accueil > section > œuvre est dérivé, la fiche en étant le dernier
      élément.
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
    extra: list[dict] = []
    if title and breadcrumbs:
        trail = [("Accueil", "/"), *breadcrumbs, (title, path)]
        extra.append(breadcrumb_json_ld(request, trail))
    base_title = f"{title} {qualifiers}".strip() if qualifiers else title
    return page_seo(
        request,
        title=f"{base_title} — {title_suffix} — {SITE_NAME}" if base_title else "",
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
        extra_json_ld=extra,
    )
