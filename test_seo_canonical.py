"""Tests SEO essentiels : canonical VF/VOSTFR (fin de la cannibalisation) & sitemap dédupliqué.

Les fiches d'un même titre existent en plusieurs versions dont le slug porte un
suffixe (`-vf`, `-vostfr`, `-french`, `-truefrench`, `-vo`). Google les traitait
comme des pages concurrentes (ex. `/film/lodyssee-vf` ET `/film/lodyssee-vostfr`
dans GSC). Règles verrouillées ici :

1. Le canonical d'une fiche pointe vers la version préférée (VF d'abord) SI
   cette URL est réellement connue (sitemap en cache) — jamais vers une URL
   potentiellement inexistante.
2. Le sitemap ne liste que la version préférée de chaque titre.
"""
from __future__ import annotations

import re

from fastapi.testclient import TestClient

from cache import cache
from main import app
from services.sitemap import _preferred_slugs

client = TestClient(app)


def test_fiche_vostfr_canonical_pointe_vers_la_vf_connue(monkeypatch):
    import routes.detail

    monkeypatch.setenv("SITE_URL", "https://nokatv.xyz")

    def _fake_film(slug: str, version: str) -> dict:
        return {
            "title": "L'Odyssée", "slug": slug, "movie_id": "42",
            "type": "movie", "content_type": "Movie", "image": "", "version": version,
            "year": "2024", "genres": ["Aventure"], "status": "Released",
            "synopsis": "Synopsis.", "episodes": [], "first_episode_id": "42",
            "first_episode_url": f"/regarder/{slug}/ep-42", "related": [],
        }

    async def fake_load(slug: str):
        return _fake_film(slug, "VOSTFR" if slug == "lodyssee-vostfr" else "VF")

    monkeypatch.setattr(routes.detail, "load_detail", fake_load)
    cache.set("sitemap:paths", ["/film/lodyssee-vf", "/film/lodyssee-vostfr"], 3600)

    r = client.get("/film/lodyssee-vostfr")
    assert r.status_code == 200
    canonical = re.search(r'rel="canonical" href="([^"]+)"', r.text)
    assert canonical is not None
    # Version préférée (VF) connue -> canonical vers elle, sur l'origine publique configurée.
    assert canonical.group(1) == "https://nokatv.xyz/film/lodyssee-vf"

    # Version préférée inconnue -> canonical = page courante (aucune URL cassée)
    cache.set("sitemap:paths", ["/film/lodyssee-vostfr"], 3600)
    r2 = client.get("/film/lodyssee-vostfr")
    canonical2 = re.search(r'rel="canonical" href="([^"]+)"', r2.text)
    assert canonical2 is not None
    assert canonical2.group(1) == "https://nokatv.xyz/film/lodyssee-vostfr"


def test_sitemap_ne_liste_que_la_version_preferee():
    items = [
        {"slug": "lodyssee-vf", "title": "L'Odyssée", "version": "VF"},
        {"slug": "lodyssee-vostfr", "title": "L'Odyssée", "version": "VOSTFR"},
        {"slug": "dune-vostfr", "title": "Dune", "version": "VOSTFR"},
        {"slug": "reacher-saison-4", "title": "Reacher", "version": ""},
    ]
    slugs = _preferred_slugs(items)
    assert "lodyssee-vf" in slugs
    assert "lodyssee-vostfr" not in slugs  # doublon éliminé
    assert "dune-vostfr" in slugs          # seule version : conservée
    assert "reacher-saison-4" in slugs     # slug nu : conservé
