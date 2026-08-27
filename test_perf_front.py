"""Tests performance front : zéro CDN tiers, icônes SVG locales, cache & gzip."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)

ROOT = Path(__file__).resolve().parent
BASE = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
ICONS_CSS = (ROOT / "static" / "icons" / "icons.css").read_text(encoding="utf-8")
STYLE = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")


def test_base_html_sans_cdn_tiers():
    """Le chemin critique de rendu ne dépend d'aucun CDN de police/icônes
    (DNS + TLS bloquants avant le premier paint)."""
    for cdn in ("fonts.googleapis", "fonts.gstatic", "cdnjs.cloudflare"):
        assert f"https://{cdn}" not in BASE, cdn
    assert "/icons/icons.css" in BASE             # icônes locales (url_for)
    assert "style.css" in BASE and "?v=" in BASE  # CSS versionné (cache 1 an)


def test_police_auto_hebergee():
    """Les 5 graisses woff2 existent localement et sont déclarées dans style.css."""
    for w in (400, 500, 600, 700, 800):
        f = ROOT / "static" / "fonts" / f"plus-jakarta-sans-{w}-latin.woff2"
        assert f.exists() and f.stat().st_size > 1000, f.name
        assert f"plus-jakarta-sans-{w}-latin.woff2" in STYLE
    assert "font-display: swap" in STYLE
    # Œ/œ (U+0152-0153) couverts par le sous-ensemble latin (français)
    assert "U+0152-0153" in STYLE


def test_icons_css_couvre_toutes_les_icones_utilisees():
    """Garde anti-régression : toute icône fa-* présente dans les templates
    ou les scripts DOIT avoir sa classe --fa-icon dans icons.css."""
    defined = set(re.findall(r"\.fa-([a-z0-9-]+)\s*\{", ICONS_CSS))
    used = set()
    for path in list((ROOT / "templates").glob("*.html")) + list((ROOT / "static").glob("*.js")):
        text = path.read_text(encoding="utf-8")
        for m in re.findall(r"fa-([a-z0-9]+(?:-[a-z0-9]+)*)", text):
            used.add(m)
    # artefact de concaténation JS (« fa-chevron-' + … ») : les deux vraies
    # icônes chevron-left / chevron-right sont utilisées telles quelles ailleurs
    used.discard("chevron")
    missing = used - defined
    assert not missing, f"icônes sans définition SVG locale : {sorted(missing)}"
    assert defined  # sanity


def test_icons_css_methode_mask_sans_webfont():
    """Les icônes héritent de la couleur (currentColor) et suivent font-size."""
    assert "background-color: currentColor" in ICONS_CSS
    assert "mask: var(--fa-icon)" in ICONS_CSS
    assert "data:image/svg+xml" in ICONS_CSS
    # alias FA5 conservé
    assert ".fa-times {" in ICONS_CSS


def test_assets_statiques_cache_un_an():
    for path in ("/static/style.css?v=5", "/static/icons/icons.css?v=1",
                 "/static/fonts/plus-jakarta-sans-400-latin.woff2"):
        r = client.get(path)
        assert r.status_code == 200, path
        cc = r.headers.get("cache-control", "")
        assert "max-age=31536000" in cc and "immutable" in cc, (path, cc)


def test_reponses_html_compresses_gzip():
    r = client.get("/ma-liste", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers.get("content-encoding") == "gzip"


def test_service_worker_shell_mis_a_jour():
    """Le SW pré-cache le CSS versionné, les icônes et la police locale."""
    assert "nokatv-shell-v5" in SW
    assert "/static/style.css?v=5" in SW
    assert "/static/icons/icons.css?v=1" in SW
    assert "/static/fonts/plus-jakarta-sans-400-latin.woff2" in SW
