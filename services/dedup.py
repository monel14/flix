"""Fusion des variantes de version (VF / VOSTFR) d'un même titre.

Sur la source coflix, un même titre existe en plusieurs fiches dont le slug
porte un suffixe de version (ex: `black-box-vf`, `black-box-vostfr`).
Ce module les regroupe en UNE entrée (modèle French Stream) avec la liste
`versions` des variantes disponibles.
"""
from __future__ import annotations

# Suffixes de version reconnus (du plus spécifique au plus court)
VERSION_SUFFIXES = ("-truefrench", "-vostfr", "-french", "-vf", "-vo")

VERSION_LABELS = {
    "-truefrench": "TRUEFRENCH",
    "-french": "FRENCH",
    "-vostfr": "VOSTFR",
    "-vf": "VF",
    "-vo": "VO",
}

# Ordre de préférence pour choisir le représentant affiché (VF d'abord)
_PREFERENCE = {"vf": 0, "truefrench": 1, "french": 2, "vostfr": 3, "vo": 4}


def canonical_slug(slug: str) -> str:
    """Retire le suffixe de version d'un slug. `black-box-vf` -> `black-box`."""
    s = (slug or "").lower()
    for suf in VERSION_SUFFIXES:
        if s.endswith(suf):
            return slug[: -len(suf)]
    return slug or ""


def version_label(slug: str) -> str:
    """Label de version porté par un slug ('' si aucun suffixe)."""
    s = (slug or "").lower()
    for suf, label in VERSION_LABELS.items():
        if s.endswith(suf):
            return label
    return ""


def sibling_slugs(slug: str) -> list[str]:
    """Slugs des variantes sœurs possibles (sans le slug courant)."""
    base = canonical_slug(slug)
    if not base or base == slug:
        return []
    return [base + suf for suf in VERSION_SUFFIXES if base + suf != slug]


def preferred_version_slug(slug: str) -> str:
    """Slug de la variante la plus préférée (VF > TRUEFRENCH > FRENCH > VOSTFR > VO).

    Retourne le slug courant s'il est déjà la version préférée, ou si aucun
    suffixe de version ne le qualifie (ex: slugs nus comme `reacher-saison-4`).
    Exemples :
        preferred_version_slug("lodyssee-vostfr") == "lodyssee-vf"
        preferred_version_slug("lodyssee-vf")     == "lodyssee-vf"
        preferred_version_slug("reacher-saison-4") == "reacher-saison-4"
    """
    base = canonical_slug(slug)
    if not base or base == slug:
        return slug
    current = (version_label(slug) or "").lower()
    current_rank = _PREFERENCE.get(current, 99) if current else 99

    best_slug: str | None = None
    best_rank = current_rank
    for suffix in VERSION_SUFFIXES:
        candidate = base + suffix
        if candidate == slug:
            continue
        rank = _PREFERENCE.get(suffix[1:], 99)
        if rank < best_rank:
            best_slug, best_rank = candidate, rank
    return best_slug if best_slug else slug


def canonical_path_for(slug: str, prefix: str, known_paths: set[str] | None = None) -> str:
    """Chemin canonique d'une fiche : la version préférée si elle est réellement
    connue (indexée), sinon le chemin courant.

    `known_paths` : ensemble des chemins réellement servis (ex. issus du cache
    sitemap). Sans preuve que la variante préférée existe, on garde le chemin
    courant : pointer un canonical vers une URL inexistante serait pire que de
    ne rien faire.
    """
    preferred = preferred_version_slug(slug)
    path = f"{prefix}{slug}"
    if preferred == slug:
        return path
    if known_paths and f"{prefix}{preferred}" in known_paths:
        return f"{prefix}{preferred}"
    return path


def merge_variants(items: list[dict]) -> list[dict]:
    """Regroupe les items par slug canonique : un titre = une entrée.

    L'entrée résultante :
    - reprend les champs de la variante préférée (VF > TRUEFRENCH > FRENCH > VOSTFR > VO)
    - porte `versions` : liste des variantes détectées (ex: ['VF', 'VOSTFR'])
    """
    grouped: dict[str, dict] = {}
    order: list[str] = []
    for it in items:
        slug = it.get("slug") or ""
        key = canonical_slug(slug)
        if not key:
            continue
        v = (it.get("version") or "").strip() or version_label(slug)
        if key not in grouped:
            entry = dict(it)
            entry["versions"] = [v] if v else []
            grouped[key] = entry
            order.append(key)
            continue

        entry = grouped[key]
        cur_v = (entry.get("version") or "").strip() or version_label(entry.get("slug") or "")
        pref_cur = _PREFERENCE.get(cur_v.lower(), 99) if cur_v else 99
        pref_new = _PREFERENCE.get(v.lower(), 99) if v else 99
        if pref_new < pref_cur:
            old_versions = entry.get("versions", [])
            for k, val in it.items():
                entry[k] = val
            entry["versions"] = old_versions
        versions = entry.setdefault("versions", [])
        if v and v not in versions:
            versions.append(v)
            
    for entry in grouped.values():
        entry["versions"] = sorted(
            entry.get("versions", []),
            key=lambda x: _PREFERENCE.get((x or "").lower(), 99),
        )
    return [grouped[k] for k in order]
