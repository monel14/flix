from __future__ import annotations

import logging
import urllib.parse
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from cache import cache
from routes import anime, detail, drama, home, player, search
from scraper.coflix_client import close_coflix_client
from scraper.voirdrama_client import close_voirdrama_client, get_voirdrama_client
from scraper.voiranime_client import close_voiranime_client, get_voiranime_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("coflix")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Nettoyage automatique du cache expiré au démarrage (conserve les archives)
    try:
        purged = cache.purge_expired()
        if purged > 0:
            logger.info("Cache nettoyé au démarrage : %d entrée(s) temporaire(s) expirée(s) supprimée(s)", purged)
    except Exception as exc:
        logger.warning("Impossible de purger le cache : %s", exc)
    yield
    await close_coflix_client()
    await close_voirdrama_client()
    await close_voiranime_client()


app = FastAPI(title="Coflix", lifespan=lifespan)

BASE_DIR = Path(__file__).resolve().parent

static_dir = BASE_DIR / "static"
static_dir.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=static_dir), name="static")

templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals["str"] = str


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

    # Image de secours (placeholder neutre)
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
            {"request": request, "status_code": status, "message": msg},
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
            {"request": request, "status_code": 500, "message": "Erreur serveur interne."},
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
