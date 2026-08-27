"""Tests SEO : sitemap dynamique (fiches incluses), robots.txt, noindex."""
from xml.etree import ElementTree

from fastapi.testclient import TestClient

import main
from main import app
from services.seo import page_seo

client = TestClient(app)


def _fake_seo_request():
    """Requête minimale pour page_seo (seuls scheme/netloc/path sont lus)."""
    class R:
        class url:
            scheme = "https"
            netloc = "nokatv.xyz"
            path = "/films"
        headers = {}
    return R()


def test_page_seo_noindex_par_defaut_desactive():
    seo = page_seo(_fake_seo_request(), title="Test", path="/films")
    assert seo.noindex is False
    seo_ni = page_seo(_fake_seo_request(), title="Test", path="/regarder/x/ep-1", noindex=True)
    assert seo_ni.noindex is True


def test_base_html_porte_la_balise_robots():
    from pathlib import Path
    base = (Path(__file__).parent / "templates" / "base.html").read_text(encoding="utf-8")
    assert 'seo.noindex' in base
    assert 'name="robots"' in base
    assert 'noindex, follow' in base


def test_ma_liste_est_noindex():
    response = client.get("/ma-liste")
    assert response.status_code == 200
    assert '<meta name="robots" content="noindex, follow">' in response.text


def test_robots_txt_bloque_api_et_declare_sitemap(monkeypatch):
    monkeypatch.setenv("SITE_URL", "https://nokatv.xyz")
    response = client.get("/robots.txt")
    assert response.status_code == 200
    body = response.text
    assert "Disallow: /api/" in body          # budget de crawl préservé
    assert "Disallow: /regarder" not in body  # noindex meta lisible par Googlebot
    assert "Disallow: /recherche" not in body
    assert "Sitemap: https://nokatv.xyz/sitemap.xml" in body


def test_sitemap_contient_statiques_et_fiches(monkeypatch):
    async def fake_collect():
        return sorted({"/", "/films", "/series", "/dramas", "/animes",
                       "/film/interstellar", "/drama/queen-of-tears", "/animes?page=2"})
    monkeypatch.setattr(main, "collect_sitemap_paths", fake_collect)
    response = client.get("/sitemap.xml")
    assert response.status_code == 200
    assert "application/xml" in response.headers["content-type"]

    root = ElementTree.fromstring(response.text)  # XML valide
    ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
    locs = {u.findtext(f"{ns}loc") for u in root.findall(f"{ns}url")}
    locs = {l for l in locs if l}
    assert "https://testserver/film/interstellar" in locs or any("film/interstellar" in l for l in locs)
    assert any("drama/queen-of-tears" in l for l in locs)
    assert "https://testserver/" in locs or any(l.endswith("/") and "films" not in l for l in locs)
    # /recherche est noindex : elle ne doit plus apparaître dans le sitemap
    assert not any("recherche" in l for l in locs)
    # Cache-friendly pour les crawlers
    assert "max-age" in response.headers.get("cache-control", "")


def test_sitemap_resiste_a_une_source_indisponible(monkeypatch):
    async def fake_collect():
        raise RuntimeError("sources down")
    monkeypatch.setattr(main, "collect_sitemap_paths", fake_collect)
    response = client.get("/sitemap.xml")
    assert response.status_code == 200
    assert "/films" in response.text  # version statique de secours
