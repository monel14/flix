"""Blocklist DMCA - slugs retirés suite à plainte ayants droit.

Première plainte: 2026-09-07 - Butterfly Saison 1 (Amazon) - Marketly LLC
Lumen: https://lumendatabase.org/notices/95976390

Quand un slug est ici, la fiche retourne 404 et sort du sitemap.
Procédure: ajouter le slug (lowercase) + purger cache sitemap:paths
"""

BLOCKED_SLUGS = {
    # Amazon - Butterfly - DMCA 2026-09-07
    "butterfly-saison-1-vostfr",
    "butterfly-saison-1-vf",
    "butterfly-saison-1",
    "butterfly-vostfr",
    "butterfly-vf",
}

def is_blocked(slug: str) -> bool:
    s = (slug or "").lower().strip()
    return s in BLOCKED_SLUGS
