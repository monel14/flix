from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from cache import cache
from routes import detail, home, player, search
from scraper.coflix_client import close_coflix_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("coflix")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Nettoyage automatique du cache expiré au démarrage
    try:
        purged = cache.purge_expired()
        if purged > 0:
            logger.info("Cache nettoyé au démarrage : %d entrée(s) expirée(s) supprimée(s)", purged)
    except Exception as exc:
        logger.warning("Impossible de purger le cache : %s", exc)
    yield
    await close_coflix_client()


app = FastAPI(title="Coflix", lifespan=lifespan)

BASE_DIR = Path(__file__).resolve().parent

static_dir = BASE_DIR / "static"
static_dir.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=static_dir), name="static")

templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals["str"] = str


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
app.include_router(search.router)
