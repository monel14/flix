"""Tests du mode TV & télécommande : détection, manifeste, contrôles lecteur."""
from pathlib import Path

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)

ROOT = Path(__file__).resolve().parent
PLAYER_TEMPLATES = ("player.html", "anime_player.html", "drama_player.html")


def test_tv_js_est_servi():
    response = client.get("/static/tv.js")
    assert response.status_code == 200
    assert "text/javascript" in response.headers["content-type"]
    assert "tv-mode" in response.text
    assert "player-fullscreen-btn" in response.text
    assert "media-session-data" in response.text
    assert "navigator.mediaSession" in response.text


def test_manifest_orientation_any():
    response = client.get("/static/manifest.webmanifest")
    assert response.status_code == 200
    manifest = response.json()
    assert manifest["orientation"] == "any"
    assert manifest["display"] == "standalone"


def test_templates_lecteur_ont_controles_tv():
    for name in PLAYER_TEMPLATES:
        html = (ROOT / "templates" / name).read_text(encoding="utf-8")
        assert 'id="player-fullscreen-btn"' in html, name
        assert 'id="media-session-data"' in html, name
        assert 'id="player-frame"' in html, name


def test_base_charge_tv_js_et_manifeste_v3():
    html = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    assert '/static/tv.js' in html
    assert 'manifest.webmanifest?v=3' in html


def test_service_worker_cache_v8():
    js = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
    assert "nokatv-shell-v8" in js
    assert "'/static/tv.js'" in js
    assert "'/static/pwa-install-manager.js?v=2'" in js
    assert "'/static/pwa-install-prompt.js?v=2'" in js
    assert "'/static/pwa-install.css?v=2'" in js
    assert "manifest.webmanifest?v=3" in js


def test_mode_tv_css_present():
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    assert "html.tv-mode" in css
    assert "html.tv-mode .stream-card:focus-visible" in css
    assert "html.tv-tizen" in css
    assert "html.tv-webos" in css
