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
