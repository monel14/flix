from __future__ import annotations

import logging
import os
import urllib.parse
from contextlib import asynccontextmanager
from pathlib import Path
from xml.sax.saxutils import escape

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from cache import cache
from services.indexnow import ensure_indexnow_key, indexnow_key
from services.seo import page_seo, site_origin
from services.sitemap import LEGAL_PATHS as LEGAL_SITEMAP_PATHS
from services.sitemap import STATIC_PATHS as STATIC_SITEMAP_PATHS
from services.sitemap import collect_sitemap_paths
from services.templates import templates
SITE_NAME = "NokaTV"
from routes import anime, detail, drama, home, player, search
from scraper.coflix_client import close_coflix_client
from scraper.voirdrama_client import close_voirdrama_client, get_voirdrama_client
from scraper.voiranime_client import close_voiranime_client, get_voiranime_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("nokatv")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Nettoyage automatique du cache expiré au démarrage (conserve les archives)
    try:
        purged = cache.purge_expired()
        if purged > 0:
            logger.info("Cache nettoyé au démarrage : %d entrée(s) temporaire(s) expirée(s) supprimée(s)", purged)
    except Exception as exc:
        logger.warning("Impossible de purger le cache : %s", exc)
    # Clé IndexNow stable entre redémarrages (servie à /{clé}.txt)
    try:
        ensure_indexnow_key()
    except Exception as exc:
        logger.warning("IndexNow indisponible au démarrage : %s", exc)
    yield
    await close_coflix_client()
    await close_voirdrama_client()
    await close_voiranime_client()


app = FastAPI(title="NokaTV", lifespan=lifespan)

# Compression transparente des réponses textuelles (HTML, CSS, JS, JSON) —
# actif dès lors que le reverse-proxy frontal ne compresse pas déjà.
app.add_middleware(GZipMiddleware, minimum_size=1024)

BASE_DIR = Path(__file__).resolve().parent

static_dir = BASE_DIR / "static"
static_dir.mkdir(parents=True, exist_ok=True)


class VersionedStaticFiles(StaticFiles):
    """Assets locaux versionnés dans le HTML (?v=N) : cache navigateur 1 an.

    Le hash/version figure dans l'URL référencée par les templates et le
    service worker (bump à chaque déploiement) : `immutable` est donc légitime
    et les ré-visites de Googlebot ne re-téléchargent rien.
    """

    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


app.mount("/static", VersionedStaticFiles(directory=static_dir), name="static")

# ---------------------------------------------------------------------------
# Route Ma Liste (Favoris / Watchlist)
# ---------------------------------------------------------------------------

@app.get("/ma-liste", response_class=HTMLResponse)
async def watchlist_page(request: Request):
    """Page Ma Liste de favoris."""
    # Contenu personnalisé côté client (localStorage) : thin content en SSR
    # et propre à chaque visiteur -> explicitement non indexable.
    return templates.TemplateResponse(request, "watchlist.html", {
        "request": request,
        "seo": page_seo(request, title="Ma Liste — NokaTV", path="/ma-liste", noindex=True),
    })


# ---------------------------------------------------------------------------
# Pages de confiance (E-E-A-T) : mentions légales & contact
# ---------------------------------------------------------------------------

CONTACT_EMAIL_FALLBACK = "contact@nokatv.xyz"


def _contact_email() -> str:
    return (os.getenv("CONTACT_EMAIL") or "").strip() or CONTACT_EMAIL_FALLBACK


@app.get("/mentions-legales", response_class=HTMLResponse)
async def legal_notice_page(request: Request):
    """Mentions légales — indexables : transparence sur la nature du service
    (agrégateur sans hébergement), procédure de retrait et données locales."""
    return templates.TemplateResponse(request, "mentions_legales.html", {
        "request": request,
        "contact_email": _contact_email(),
        "seo": page_seo(
            request,
            title="Mentions légales — NokaTV",
            description="Mentions légales de NokaTV : nature du service d'agrégation, propriété intellectuelle, procédure de retrait et respect des données personnelles.",
            path="/mentions-legales",
        ),
    })


@app.get("/contact", response_class=HTMLResponse)
async def contact_page(request: Request):
    """Page contact — indexable : signalement de liens morts et demandes de
    retrait pour les ayants droit."""
    return templates.TemplateResponse(request, "contact.html", {
        "request": request,
        "contact_email": _contact_email(),
        "seo": page_seo(
            request,
            title="Contact — NokaTV",
            description="Contacter NokaTV : signalement d'un lien mort, demande de retrait d'une référence (ayants droit) ou suggestion d'amélioration.",
            path="/contact",
        ),
    })


# ---------------------------------------------------------------------------
# SEO : Sitemap.xml & Robots.txt
# ---------------------------------------------------------------------------

@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt(request: Request):
    """Robots.txt : tout le HTML public est crawlable, sauf l'API (JSON sans
    valeur d'indexation) qui gaspillerait le budget de crawl.

    Les pages players / recherche / ma-liste ne sont PAS bloquées ici :
    elles portent un meta noindex que Googlebot doit pouvoir lire (une page
    bloquée dans robots.txt mais indexée ne peut pas être dé-indexée).
    """
    base_url = site_origin(request)
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        "\n"
        f"Sitemap: {base_url}/sitemap.xml\n"
    )


# ---------------------------------------------------------------------------
# PWA légère : service worker servi à la racine (scope "/" obligatoire)
# ---------------------------------------------------------------------------


@app.get("/sw.js")
async def service_worker():
    """Sert static/sw.js à la racine : le scope d'un SW dépend de son chemin,
    /static/sw.js ne pourrait contrôler que /static/*."""
    return FileResponse(
        BASE_DIR / "static" / "sw.js",
        media_type="text/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/sitemap.xml")
async def sitemap_xml(request: Request):
    """Sitemap dynamique : pages statiques + fiches de contenu réelles.

    Le trafic organique d'un site de streaming vient du long tail des fiches
    (« regarder X en streaming ») : le sitemap les expose directement à
    Google au lieu d'attendre une découverte par pagination. Les slugs sont
    collectés depuis les sources et cachés 12 h (services/sitemap.py).
    """
    base_url = site_origin(request)

    try:
        paths = await collect_sitemap_paths()
    except Exception as exc:
        logger.warning("Sitemap : collecte impossible, version statique seule (%s)", exc)
        paths = list(STATIC_SITEMAP_PATHS) + list(LEGAL_SITEMAP_PATHS)

    xml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]

    for path in paths:
        # Priorité hiérarchique : hubs (accueil / sections) > pagination >
        # fiches > pages de confiance. Pas de changefreq/lastmod fabriqués :
        # Google les ignore ou les pénalyse s'ils sont faux.
        is_hub = path in STATIC_SITEMAP_PATHS
        is_legal = path in LEGAL_SITEMAP_PATHS
        priority = "0.9" if is_hub else ("0.3" if is_legal else ("0.8" if "?" in path else "0.7"))
        loc = escape(base_url + path)
        xml_lines.append(f"  <url><loc>{loc}</loc><priority>{priority}</priority></url>")

    xml_lines.append("</urlset>")
    return Response(
        content="\n".join(xml_lines),
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )


# ---------------------------------------------------------------------------
# IndexNow : fichier de vérification de la clé (https://{hôte}/{clé}.txt)
# ---------------------------------------------------------------------------


@app.get("/{key}.txt", response_class=PlainTextResponse)
async def indexnow_key_file(key: str):
    """Sert la clé IndexNow à l'emplacement exact exigé par le protocole.

    IndexNow vérifie la propriété du domaine en demandant ce fichier
    (https://nokatv.xyz/{clé}.txt). Sans clé configurée (ou avec une clé qui
    ne correspond pas), on répond 404 : la route n'expose jamais autre chose.
    """
    expected = indexnow_key()
    if expected and key == expected:
        return expected
    raise HTTPException(status_code=404, detail="Clé inconnue")


# ---------------------------------------------------------------------------
# Proxy d'images (Contournement de la protection anti-hotlink 403)
# ---------------------------------------------------------------------------

@app.get("/api/image-proxy")
async def image_proxy(url: str = Query(...)):
    """Relaye les images distantes avec le bon Referer pour contourner les erreurs 403."""
    if not url.startswith("http://") and not url.startswith("https://"):
        raise HTTPException(status_code=400, detail="Invalid URL")

    # Déterminer le bon Referer selon la source
    if "voirdrama" in url:
        referer = "https://voirdrama.to/"
        client = get_voirdrama_client()
    elif "voir-anime" in url:
        referer = "https://voir-anime.to/"
        client = get_voiranime_client()
    else:
        referer = "https://coflix.wiki/"
        client = get_voirdrama_client()

    try:
        r = await client.get(
            url,
            headers={
                "Referer": referer,
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            },
            timeout=10.0,
        )
        if r.status_code == 200:
            content_type = r.headers.get("content-type", "image/jpeg")
            return Response(
                content=r.content,
                media_type=content_type,
                headers={
                    "Cache-Control": "public, max-age=604800, immutable",
                },
            )
    except Exception as exc:
        logger.warning("Erreur image proxy pour %s : %s", url, exc)

    placeholder = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="450" viewBox="0 0 300 450">'
        '<rect width="300" height="450" fill="#131622"/>'
        '<text x="50%" y="50%" font-family="sans-serif" font-size="16" fill="#64748b" text-anchor="middle" dominant-baseline="middle">'
        'Affiche indisponible'
        '</text></svg>'
    )
    return Response(content=placeholder, media_type="image/svg+xml")


# ---------------------------------------------------------------------------
# Gestion des requêtes CDN / Scripts tiers embarqués
# ---------------------------------------------------------------------------

@app.get("/cdn-cgi/{path:path}")
async def cdn_cgi_fallback(path: str):
    """Réponse silencieuse pour les scripts Cloudflare relatifs exécutés par des iframes tierces."""
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Gestion des erreurs
# ---------------------------------------------------------------------------

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    wants_html = "text/html" in (request.headers.get("accept") or "")
    is_api = request.url.path.startswith("/api")
    if wants_html and not is_api:
        status = exc.status_code
        msg = {
            404: "Cette page est introuvable.",
            502: "Le site source ne répond pas, réessaie dans un instant.",
        }.get(status, "Une erreur inattendue s'est produite.")
        return templates.TemplateResponse(
            request, "error.html",
            {"request": request, "status_code": status, "message": msg,
             "seo": page_seo(request, title=f"Erreur {status} — NokaTV", noindex=True)},
            status_code=status,
        )
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def server_error_handler(request: Request, exc: Exception):
    logger.exception("Erreur serveur non gérée sur %s : %s", request.url.path, exc)
    wants_html = "text/html" in (request.headers.get("accept") or "")
    if wants_html:
        return templates.TemplateResponse(
            request, "error.html",
            {"request": request, "status_code": 500, "message": "Erreur serveur interne.",
             "seo": page_seo(request, title="Erreur 500 — NokaTV", noindex=True)},
            status_code=500,
        )
    return JSONResponse({"detail": "Internal Server Error"}, status_code=500)


# ---------------------------------------------------------------------------
# Routeurs
# ---------------------------------------------------------------------------

app.include_router(home.router)
app.include_router(detail.router)
app.include_router(player.router)
app.include_router(drama.router)
app.include_router(anime.router)
app.include_router(search.router)
