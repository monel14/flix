"""Contrat essentiel : guide sur noka-tv, pas de lecteur ni duplication catalogue."""
import re
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient
from main import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SITE_URL", "https://nokatv.xyz")
    monkeypatch.setenv("ALTERNATIVES_DOMAIN", "noka-tv.xyz")
    monkeypatch.delenv("ALLOWED_DOMAINS", raising=False)
    monkeypatch.delenv("ALLOWED_MIRRORS", raising=False)
    # Sans contexte lifespan : aucun préchauffage / appel au catalogue distant.
    return TestClient(app, base_url="https://noka-tv.xyz", follow_redirects=False)


def test_guide_indexable_et_destination_claire(client):
    r = client.get("/secret-story-afrique?utm_source=test")
    assert r.status_code == 200
    assert 'rel="canonical" href="https://noka-tv.xyz/secret-story-afrique"' in r.text
    assert '<meta name="robots" content="noindex' not in r.text
    assert "noindex" not in r.headers.get("x-robots-tag", "")
    assert len(re.findall(r"<h1[ >]", r.text)) == 1
    assert "18 h 00" in r.text and "20 h 30" in r.text
    assert "https://secretstoryafrique.com/vote" in r.text
    assert "https://nokatv.xyz/secret-story-afrique?utm_source=noka-tv.xyz" in r.text
    assert "utm_campaign=secret-story-afrique" in r.text
    assert "<iframe" not in r.text
    assert 'http-equiv="refresh"' not in r.text.lower()
    assert "Cette page ne propose pas de lecteur vidéo" in r.text
    assert "https://nokatv.xyz/films" in r.text


def test_head_et_variantes(client):
    assert client.head("/secret-story-afrique").status_code == 200
    assert client.head("/secret-story-afrique").content == b""
    for host, path in [("noka-tv.xyz", "/secret-story-afrique/"), ("www.noka-tv.xyz", "/secret-story-afrique")]:
        r = client.get(f"https://{host}{path}", headers={"cf-ray": "test"})
        assert r.status_code == 301
        assert r.headers["location"] == "https://noka-tv.xyz/secret-story-afrique"


def test_sitemap_guide_uniquement_sur_domaine_editorial(client, monkeypatch):
    import main
    async def fake_paths():
        return ["/", "/alternative", "/contact"]
    monkeypatch.setattr(main, "collect_sitemap_paths", fake_paths)
    for host, expected in [("noka-tv.xyz", True), ("nokatv.xyz", False)]:
        r = client.get(f"https://{host}/sitemap.xml", headers={"cf-ray": "test"})
        assert r.status_code == 200
        locs = [v.text for v in ElementTree.fromstring(r.text).findall("{*}url/{*}loc")]
        assert ("https://noka-tv.xyz/secret-story-afrique" in locs) is expected
        if expected:
            assert "https://noka-tv.xyz/" not in locs
            assert all(x.startswith("https://noka-tv.xyz/") for x in locs)
        assert "https://nokatv.xyz/secret-story-afrique" not in locs


def test_maillage_et_catalogue_toujours_ferme(client):
    r = client.get("/alternative")
    assert r.status_code == 200
    assert 'href="https://noka-tv.xyz/secret-story-afrique"' in r.text
    assert client.get("/films").status_code == 404
    assert client.get("/secret-story-autre").status_code == 404


def test_destination_lecteur_sur_catalogue_sans_redirection(client):
    r = client.get("https://nokatv.xyz/secret-story-afrique?utm_source=noka-tv.xyz", headers={"cf-ray": "test"})
    assert r.status_code == 200
    assert "location" not in r.headers
    assert r.headers["x-robots-tag"] == "noindex, follow"
    assert 'rel="canonical" href="https://nokatv.xyz/secret-story-afrique"' in r.text
    assert '<meta name="robots" content="noindex, follow">' in r.text
    assert 'data-token="' in r.text
    assert 'youtube-nocookie.com' not in r.text
    assert 'vUtbR0rvdOI' not in r.text
    assert "<iframe" not in r.text  # créé uniquement après clic par le JS
    assert 'href="https://noka-tv.xyz/secret-story-afrique"' not in r.text
    assert "Cotonou" not in r.text and "Douala" not in r.text and "Libreville" not in r.text
    assert "18 h 00" in r.text and "18 h 15" not in r.text
    assert "pas présenté comme une diffusion officielle" in r.text
    head = client.head("https://nokatv.xyz/secret-story-afrique", headers={"cf-ray": "test"})
    assert head.status_code == 200 and head.content == b""
    assert head.headers["x-robots-tag"] == "noindex, follow"


def test_slash_catalogue_reste_sur_catalogue_et_preserve_utm(client):
    r = client.get("https://nokatv.xyz/secret-story-afrique/?utm_source=test", headers={"cf-ray": "test"})
    assert r.status_code == 301
    assert r.headers["location"] == "/secret-story-afrique?utm_source=test"


@pytest.mark.parametrize("video_id,enabled", [("bad-id", "true"), ("vUtbR0rvdOI", "off"), ("", "true")])
def test_source_invalide_ou_desactivee_pas_de_lecteur(client, monkeypatch, video_id, enabled):
    monkeypatch.setenv("SECRET_STORY_YOUTUBE_ID", video_id)
    monkeypatch.setenv("SECRET_STORY_ENABLED", enabled)
    r = client.get("https://nokatv.xyz/secret-story-afrique", headers={"cf-ray": "test"})
    assert r.status_code == 200
    assert "Le lecteur n’est pas disponible" in r.text
    assert 'id="ss-load-player"' not in r.text
    assert "youtube-nocookie.com/embed/" not in r.text
    assert "https://secretstoryafrique.com/vote" in r.text
