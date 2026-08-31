"""Tests SEO : une seule balise <h1> par page (exigence Bing/Google).

Règle : chaque page rendue ne doit contenir qu'UN SEUL <h1>. Un carrousel
hero qui mettait le titre de chaque slide en <h1> produisait 6 instances sur
l'accueil (signalé par Bing Webmaster Tools). Ce test verrouille la règle.
"""
from __future__ import annotations

import re
from pathlib import Path

from starlette.requests import Request

from main import app
from services.seo import page_seo
from services.templates import templates

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def _fake_request(path: str = "/") -> Request:
    """Requête Starlette minimale capable de rendre un template (url_for inclus)."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
        "scheme": "https",
        "server": ("nokatv.xyz", 443),
        "query_string": b"",
        "router": app.router,
        "app": app,
        "client": ("test", 1),
        "root_path": "",
    }
    return Request(scope)


def test_chaque_template_contient_exactement_un_h1():
    """Balayage statique : aucune page ne déclare plus d'un <h1>.

    `base.html` (layout) et les partiels `_*.html` n'ont volontairement pas
    de <h1> — ce sont des gabarits, pas des pages. C'est le garde-fou le
    plus simple contre la régression (ex. un second <h1> ajouté par mégarde
    dans un autre template).
    """
    templates = sorted(
        template
        for template in TEMPLATES_DIR.glob("*.html")
        if template.name != "base.html" and not template.name.startswith("_")
    )
    assert templates, "aucun template de page trouvé"
    for template in templates:
        content = template.read_text(encoding="utf-8")
        count = content.count("<h1")
        assert count == 1, f"{template.name} contient {count} balise(s) <h1>"


def test_accueil_hero_un_seul_h1_et_h2_pour_les_autres_slides():
    """Rendu réel de l'accueil avec 6 slides hero : exactement 1 <h1>.

    La première slide porte le H1 éditorial ; les 5 autres sont en <h2>,
    avec le même rendu (le CSS cible la classe .hero-slide-title, pas la
    balise).
    """
    request = _fake_request("/")
    slides = [
        {
            "title": f"Film {index}",
            "slug": f"film-{index}",
            "image": "/static/poster.jpg",
            "version": "VF",
            "year": "2026",
            "synopsis": "Synopsis",
            "content_type": "movie",
        }
        for index in range(1, 7)
    ]
    html = templates.TemplateResponse(
        request,
        "home.html",
        {
            "request": request,
            "hero_slides": slides,
            "top": [],
            "recent_movies": [],
            "recent_series": [],
            "recent_dramas": [],
            "recent_animes": [],
            "top_filter": "day",
            "seo": page_seo(request, path="/"),
        },
    ).body.decode()

    h1_tags = re.findall(r"<h1\b[^>]*>", html)
    assert len(h1_tags) == 1, f"{len(h1_tags)} balise(s) <h1> sur l'accueil"
    assert 'class="hero-slide-title"' in h1_tags[0]

    hero_h2 = re.findall(r'<h2\b[^>]*class="hero-slide-title"', html)
    assert len(hero_h2) == 5, f"{len(hero_h2)} slides en <h2> attendues"


def test_fiches_de_contenu_un_seul_h1():
    """Les fiches film/série/animé/drama rendues n'ont qu'un seul <h1>."""
    cases = [
        ("detail.html", {"film": {"title": "Titre", "episodes": [], "image": "/static/poster.jpg"}}),
        ("anime_detail.html", {"anime": {"title": "Titre", "episodes": [], "image": "/static/poster.jpg"}}),
        ("drama_detail.html", {"drama": {"title": "Titre", "episodes": [], "image": "/static/poster.jpg"}}),
    ]
    for template_name, extra in cases:
        request = _fake_request("/film/titre-vf")
        context = {
            "request": request,
            "seo": page_seo(request, path="/film/titre-vf", title="Titre"),
            **extra,
        }
        html = templates.TemplateResponse(request, template_name, context).body.decode()
        h1_tags = re.findall(r"<h1\b[^>]*>", html)
        assert len(h1_tags) == 1, f"{template_name} : {len(h1_tags)} <h1>"
