"""Tests SEO : canonical VF/VOSTFR (fin de la cannibalisation) & sitemap dédupliqué.

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
from services.dedup import canonical_path_for, preferred_version_slug
from services.sitemap import _preferred_slugs

client = TestClient(app)


# ── Helpers purs (services/dedup.py) ────────────────────────────────────────

def test_preferred_version_slug_prioritise_la_vf():
    assert preferred_version_slug("lodyssee-vostfr") == "lodyssee-vf"
    assert preferred_version_slug("lodyssee-vf") == "lodyssee-vf"
    assert preferred_version_slug("lodyssee-truefrench") == "lodyssee-vf"
    assert preferred_version_slug("black-box-vf") == "black-box-vf"
    # Slug sans suffixe de version : inchangé
    assert preferred_version_slug("reacher-saison-4") == "reacher-saison-4"


def test_canonical_path_for_ne_point_jamais_vers_une_url_inconnue():
    known = {"/film/lodyssee-vf", "/film/lodyssee-vostfr"}
    # Version préférée connue -> canonical vers elle
    assert canonical_path_for("lodyssee-vostfr", "/film/", known) == "/film/lodyssee-vf"
    # Version préférée inconnue -> on garde la page courante (aucune URL cassée)
    assert canonical_path_for("lodyssee-vostfr", "/film/", {"/film/lodyssee-vostfr"}) == "/film/lodyssee-vostfr"
    # Aucune connaissance -> page courante
    assert canonical_path_for("lodyssee-vostfr", "/film/") == "/film/lodyssee-vostfr"
    # Page déjà préférée -> self
    assert canonical_path_for("lodyssee-vf", "/film/", known) == "/film/lodyssee-vf"
    # Animes : même règle
    anime_known = {"/anime/solo-leveling-vf", "/anime/solo-leveling-vostfr"}
    assert canonical_path_for("solo-leveling-vostfr", "/anime/", anime_known) == "/anime/solo-leveling-vf"


# ── Rendu réel des fiches ───────────────────────────────────────────────────

def _fake_film(slug: str, version: str) -> dict:
    return {
        "title": "L'Odyssée", "slug": slug, "movie_id": "42",
        "type": "movie", "content_type": "Movie", "image": "", "version": version,
        "year": "2024", "genres": ["Aventure"], "status": "Released",
        "synopsis": "Synopsis.", "episodes": [], "first_episode_id": "42",
        "first_episode_url": f"/regarder/{slug}/ep-42", "related": [],
    }


def test_fiche_vostfr_canonical_pointe_vers_la_vf_connue(monkeypatch):
    import routes.detail

    async def fake_load(slug: str):
        return _fake_film(slug, "VOSTFR" if slug == "lodyssee-vostfr" else "VF")

    monkeypatch.setattr(routes.detail, "load_detail", fake_load)
    cache.set("sitemap:paths", ["/film/lodyssee-vf", "/film/lodyssee-vostfr"], 3600)

    r = client.get("/film/lodyssee-vostfr")
    assert r.status_code == 200
    canonical = re.search(r'rel="canonical" href="([^"]+)"', r.text)
    assert canonical is not None
    assert canonical.group(1) == "http://testserver/film/lodyssee-vf"


def test_fiche_canonical_self_quand_la_vf_est_inconnue(monkeypatch):
    import routes.detail

    async def fake_load(slug: str):
        return _fake_film(slug, "VOSTFR")

    monkeypatch.setattr(routes.detail, "load_detail", fake_load)
    cache.set("sitemap:paths", ["/film/lodyssee-vostfr"], 3600)

    r = client.get("/film/lodyssee-vostfr")
    assert r.status_code == 200
    canonical = re.search(r'rel="canonical" href="([^"]+)"', r.text)
    assert canonical is not None
    assert canonical.group(1) == "http://testserver/film/lodyssee-vostfr"


def test_fiche_anime_title_slug_nu_affiche_annee_seule(monkeypatch):
    """Slug sans suffixe de version → la version n'est pas prouvée : rien
    d'ajouté ; l'année réelle de la fiche est affichée."""
    import routes.anime

    fake_anime = {
        "title": "LV999 no Murabito", "slug": "lv999-no-murabito", "image": "",
        "version": "VOSTFR", "year": "2024", "genres": ["Fantaisie"], "status": "Ongoing",
        "synopsis": "Un villageois dans un monde fantastique.",
        "episodes": [{"episode_id": "ep-1", "number": "1", "title": "Épisode 1",
                      "url": "", "version": "VOSTFR"}],
        "type": "anime",
    }

    async def fake_load(slug: str):
        return dict(fake_anime)

    monkeypatch.setattr(routes.anime, "_load_anime_detail", fake_load)
    cache.set("sitemap:paths", ["/anime/lv999-no-murabito"], 3600)

    r = client.get("/anime/lv999-no-murabito")
    assert r.status_code == 200
    title = re.search(r"<title>(.*?)</title>", r.text, re.S)
    assert title is not None
    assert "LV999 no Murabito (2024) — Animé en Streaming HD" in title.group(1)


def test_fiche_anime_title_affiche_version_prouvee_par_le_slug(monkeypatch):
    """Slug avec suffixe « -vf » → la version est prouvée : affichée."""
    import routes.anime

    fake_anime = {
        "title": "Solo Leveling", "slug": "solo-leveling-vf", "image": "",
        "version": "VF", "year": "2024", "genres": [], "status": "Ongoing",
        "synopsis": "Un chasseur faible redevient fort.",
        "episodes": [], "type": "anime",
    }

    async def fake_load(slug: str):
        return dict(fake_anime)

    monkeypatch.setattr(routes.anime, "_load_anime_detail", fake_load)

    r = client.get("/anime/solo-leveling-vf")
    assert r.status_code == 200
    title = re.search(r"<title>(.*?)</title>", r.text, re.S)
    assert title is not None
    assert "Solo Leveling (VF, 2024) — Animé en Streaming HD" in title.group(1)


# ── Titles : données réelles, jamais d'ajout automatique ────────────────────

def test_title_qualifiers_uniquement_donnees_reelles():
    from services.seo import title_qualifiers

    # Version connue seule
    assert title_qualifiers("The Last Sunrise", versions=["VF"]) == "(VF)"
    assert title_qualifiers("L'Odyssée", versions=["VOSTFR"]) == "(VOSTFR)"
    # Plusieurs versions connues
    assert title_qualifiers("X", versions=["VF", "VOSTFR"]) == "(VF/VOSTFR)"
    # Année seule
    assert title_qualifiers("The First Jasmine", year="2024") == "(2024)"
    # Version + année
    assert title_qualifiers("The Last Sunrise", versions=["VF"], year="2026") == "(VF, 2026)"
    # Rien d'utile → rien
    assert title_qualifiers("Reacher") == ""
    assert title_qualifiers("Reacher", versions=[""], year="") == ""
    # Version déjà dans le titre → jamais dupliquée
    assert title_qualifiers("Black Torch (VF)", versions=["VF"]) == ""
    # Année déjà dans le titre → jamais dupliquée
    assert title_qualifiers("Vaiana 2 (2024)", year="2024") == ""
    # Valeurs vides et doublons ignorés
    assert title_qualifiers("X", versions=["VF", "", "VF", "vf"]) == "(VF)"


def test_fiche_film_title_affiche_version_et_annee_reelles(monkeypatch):
    import routes.detail

    fake = {
        "title": "The Last Sunrise", "slug": "the-last-sunrise-vf", "movie_id": "42",
        "type": "movie", "content_type": "Movie", "image": "", "version": "VF",
        "year": "2026", "genres": ["Action"], "status": "Released",
        "synopsis": "Synopsis.", "episodes": [], "first_episode_id": "42",
        "first_episode_url": "/regarder/the-last-sunrise-vf/ep-1", "related": [],
    }

    async def fake_load(slug: str):
        return dict(fake)

    monkeypatch.setattr(routes.detail, "load_detail", fake_load)
    r = client.get("/film/the-last-sunrise-vf")
    assert r.status_code == 200
    title = re.search(r"<title>(.*?)</title>", r.text, re.S)
    assert title is not None
    assert "The Last Sunrise (VF, 2026) — Streaming HD — NokaTV" in title.group(1)


def test_fiche_sans_version_ni_annee_title_inchange(monkeypatch):
    """Aucune donnée réelle → le title reste tel quel (pas d'ajout automatique)."""
    import routes.detail

    fake = {
        "title": "Reacher", "slug": "reacher-saison-4", "movie_id": "42",
        "type": "series", "content_type": "Series", "image": "", "version": "",
        "year": "", "genres": [], "status": "", "synopsis": "Synopsis.",
        "episodes": [], "first_episode_id": "42",
        "first_episode_url": "/regarder/reacher-saison-4/ep-1", "related": [],
    }

    async def fake_load(slug: str):
        return dict(fake)

    monkeypatch.setattr(routes.detail, "load_detail", fake_load)
    r = client.get("/film/reacher-saison-4")
    assert r.status_code == 200
    title = re.search(r"<title>(.*?)</title>", r.text, re.S)
    assert title is not None
    assert "Reacher — Streaming HD — NokaTV" in title.group(1)


def test_fiche_drama_title_affiche_annee_reelle(monkeypatch):
    import routes.drama

    fake_drama = {
        "title": "The First Jasmine", "slug": "the-first-jasmine", "image": "",
        "version": "VOSTFR", "year": "2024", "genres": ["Romance"], "status": "Ongoing",
        "synopsis": "Un drame coréen.", "episodes": [], "type": "drama",
    }

    async def fake_load(slug: str):
        return dict(fake_drama)

    monkeypatch.setattr(routes.drama, "_load_drama_detail", fake_load)
    r = client.get("/drama/the-first-jasmine")
    assert r.status_code == 200
    title = re.search(r"<title>(.*?)</title>", r.text, re.S)
    assert title is not None
    assert "The First Jasmine (2024) — K-Drama en Streaming HD" in title.group(1)


def test_liste_animes_title_contient_genre_et_version(monkeypatch):
    import routes.anime

    async def fake_list(page=1, genre=None, sort="latest"):
        return {"items": [{"title": "Soul Land", "slug": "soul-land", "image": "",
                           "version": "VOSTFR", "type": "anime"}], "last_page": 1}

    monkeypatch.setattr(routes.anime, "_load_animes_list", fake_list)
    r = client.get("/animes?genre=chinese&version=vostfr")
    assert r.status_code == 200
    title = re.search(r"<title>(.*?)</title>", r.text, re.S)
    assert title is not None
    assert "Animation Chinoise (Donghua)" in title.group(1)
    assert "VOSTFR" in title.group(1)


# ── Sitemap dédupliqué ──────────────────────────────────────────────────────

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
