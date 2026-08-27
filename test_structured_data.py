"""Tests données structurées (WebSite, BreadcrumbList, ItemList) & pages E-E-A-T."""
import json
import re

from fastapi.testclient import TestClient

import main
import routes.anime
import routes.detail
import routes.drama
import routes.home
from main import app
from services.seo import (
    breadcrumb_json_ld,
    item_list_json_ld,
    website_json_ld,
)
from services.sitemap import BASE_PATHS

client = TestClient(app)


def _req():
    class R:
        class url:
            scheme = "https"
            netloc = "nokatv.xyz"
            path = "/"
        headers = {}
    return R()


# ── Fabriques JSON-LD (unitaires) ──────────────────────────────────────────

def test_website_json_ld_avec_searchaction():
    ld = website_json_ld(_req())
    assert ld["@type"] == "WebSite"
    assert ld["url"] == "https://nokatv.xyz/"
    action = ld["potentialAction"]
    assert action["@type"] == "SearchAction"
    assert action["target"]["urlTemplate"] == "https://nokatv.xyz/recherche?q={search_term_string}"
    assert action["query-input"] == "required name=search_term_string"


def test_breadcrumb_json_ld_chemins_absolus():
    ld = breadcrumb_json_ld(_req(), [("Accueil", "/"), ("Films", "/films"), ("Dune", "/film/dune")])
    assert ld["@type"] == "BreadcrumbList"
    items = ld["itemListElement"]
    assert [i["position"] for i in items] == [1, 2, 3]
    assert items[0]["item"] == "https://nokatv.xyz/"
    assert items[2]["item"] == "https://nokatv.xyz/film/dune"
    assert items[2]["name"] == "Dune"


def test_item_list_json_ld_positions_et_urls():
    ld = item_list_json_ld(_req(), [("One Piece", "/anime/one-piece"), ("Naruto", "/anime/naruto")])
    assert ld["@type"] == "ItemList"
    items = ld["itemListElement"]
    assert items[0] == {"@type": "ListItem", "position": 1,
                        "url": "https://nokatv.xyz/anime/one-piece", "name": "One Piece"}
    assert items[1]["position"] == 2


# ── Rendu des pages (sources mockées — aucun réseau) ──────────────────────

def _json_ld_blocks(html: str) -> list[dict]:
    return [json.loads(m) for m in re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]


def test_accueil_contient_website_json_ld(monkeypatch):
    async def no_hero(): return []
    async def no_section(section, page=1, genre=None): return {"items": [], "last_page": 1}
    async def no_top(t="day"): return []
    async def no_list(): return []
    monkeypatch.setattr(routes.home, "_load_hero", no_hero)
    monkeypatch.setattr(routes.home, "_load_home_section", no_section)
    monkeypatch.setattr(routes.home, "_load_top", no_top)
    monkeypatch.setattr(routes.home, "_load_popular_dramas", no_list)
    monkeypatch.setattr(routes.home, "_load_popular_animes", no_list)

    r = client.get("/")
    assert r.status_code == 200
    blocks = _json_ld_blocks(r.text)
    types = [b.get("@type") for b in blocks]
    assert "WebSite" in types
    site = next(b for b in blocks if b.get("@type") == "WebSite")
    assert site["potentialAction"]["@type"] == "SearchAction"


def test_liste_films_contient_itemlist(monkeypatch):
    async def fake_section(section, page=1, genre=None):
        return {"items": [
            {"title": "Dune", "slug": "dune", "image": "", "version": "VOSTFR", "type": "movie"},
            {"title": "Oppenheimer", "slug": "oppenheimer", "image": "", "version": "VF", "type": "movie"},
        ], "last_page": 1}
    monkeypatch.setattr(routes.home, "_load_home_section", fake_section)

    r = client.get("/films")
    assert r.status_code == 200
    blocks = _json_ld_blocks(r.text)
    il = next(b for b in blocks if b.get("@type") == "ItemList")
    urls = [it["url"] for it in il["itemListElement"]]
    assert any(u.endswith("/film/dune") for u in urls)
    assert any(u.endswith("/film/oppenheimer") for u in urls)
    # l'ordre et le nombre suivent la page réelle
    assert len(il["itemListElement"]) == 2


def test_fiche_film_contient_movie_et_breadcrumb(monkeypatch):
    fake = {
        "title": "Interstellar", "slug": "interstellar", "movie_id": "42",
        "type": "movie", "content_type": "Movie", "image": "", "version": "VF",
        "year": "2014", "genres": ["Science-fiction"], "status": "Released",
        "synopsis": "Un voyage au-delà de notre galaxie.",
        "episodes": [], "first_episode_id": "42", "first_episode_url": "/regarder/interstellar/ep-42",
        "related": [],
    }
    async def fake_load(slug): return dict(fake)
    monkeypatch.setattr(routes.detail, "load_detail", fake_load)

    r = client.get("/film/interstellar")
    assert r.status_code == 200
    blocks = _json_ld_blocks(r.text)
    types = [b.get("@type") for b in blocks]
    assert "Movie" in types and "BreadcrumbList" in types
    bc = next(b for b in blocks if b.get("@type") == "BreadcrumbList")
    assert [i["name"] for i in bc["itemListElement"]] == ["Accueil", "Films", "Interstellar"]

    # Une série pointe vers la section Séries
    fake["type"] = "series"; fake["content_type"] = "Series"
    r2 = client.get("/film/reacher")
    assert r2.status_code == 200
    bc2 = next(b for b in _json_ld_blocks(r2.text) if b.get("@type") == "BreadcrumbList")
    assert bc2["itemListElement"][1]["name"] == "Séries"


def test_fiche_anime_et_drama_avec_breadcrumb(monkeypatch):
    fake_anime = {"title": "One Piece", "slug": "one-piece", "image": "",
                  "episodes": [], "type": "anime"}
    async def fa(slug): return dict(fake_anime)
    monkeypatch.setattr(routes.anime, "_load_anime_detail", fa)
    r = client.get("/anime/one-piece")
    assert r.status_code == 200
    bc = next(b for b in _json_ld_blocks(r.text) if b.get("@type") == "BreadcrumbList")
    assert [i["name"] for i in bc["itemListElement"]] == ["Accueil", "Animés", "One Piece"]

    fake_drama = {"title": "Queen of Tears", "slug": "queen-of-tears", "image": "",
                  "episodes": [{"slug": "ep-1"}], "type": "drama"}
    async def fd(slug): return dict(fake_drama)
    monkeypatch.setattr(routes.drama, "_load_drama_detail", fd)
    r2 = client.get("/drama/queen-of-tears")
    assert r2.status_code == 200
    bc2 = next(b for b in _json_ld_blocks(r2.text) if b.get("@type") == "BreadcrumbList")
    assert bc2["itemListElement"][1]["name"] == "K-Dramas"


# ── Pages E-E-A-T ───────────────────────────────────────────────────────────

def test_mentions_legales_et_contact_indexables():
    for path, title in (("/mentions-legales", "Mentions légales"),
                        ("/contact", "Contact")):
        r = client.get(path)
        assert r.status_code == 200, path
        assert "noindex" not in r.text, path
        assert f'<link rel="canonical" href="http://testserver{path}">' in r.text, path
        assert "<h1>" in r.text and title in r.text
        # contenu de confiance réel (procédure de retrait, données locales)
        assert "retrait" in r.text.lower(), path


def test_contact_email_via_env(monkeypatch):
    monkeypatch.setenv("CONTACT_EMAIL", "support@exemple.tld")
    r = client.get("/contact")
    assert "support@exemple.tld" in r.text


def test_pages_eeuat_dans_sitemap():
    assert "/mentions-legales" in BASE_PATHS and "/contact" in BASE_PATHS


def test_footer_lien_vers_pages_confiance():
    r = client.get("/ma-liste")
    assert 'href="/mentions-legales"' in r.text
    assert 'href="/contact"' in r.text


def test_sitemap_statique_de_repli_inclut_pages_legales(monkeypatch):
    async def boom(): raise RuntimeError("down")
    monkeypatch.setattr(main, "collect_sitemap_paths", boom)
    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    assert "/mentions-legales" in r.text and "/contact" in r.text
    # priorité basse pour les pages de confiance
    assert re.search(r"<loc>[^<]*/mentions-legales</loc><priority>0\.3</priority>", r.text)
