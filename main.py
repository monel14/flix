from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
import time
import urllib.parse
from collections import defaultdict, deque
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
from scraper.coflix_client import close_coflix_client, get_coflix_client
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
# Rate limiting simple par IP (protection des endpoints /api/*)
# ---------------------------------------------------------------------------

RATE_LIMIT_WINDOW = 60        # fenêtre glissante (secondes)
RATE_LIMIT_MAX = 60           # requêtes max par fenêtre et par IP pour /api/*
IMAGE_PROXY_RATE_LIMIT_MAX = 300  # quota séparé pour /api/image-proxy
                                  # (une page d'accueil charge ~32 images d'un coup,
                                  #  et le navigateur les met ensuite en cache immutable)
_requests_buckets: dict[str, deque] = defaultdict(deque)
_image_buckets: dict[str, deque] = defaultdict(deque)
_requests_since_prune = 0


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Limite le débit des endpoints /api/* qui déclenchent des scrapes coûteux."""
    global _requests_since_prune
    if request.url.path.startswith("/api/"):
        is_image = request.url.path.startswith("/api/image-proxy")
        buckets = _image_buckets if is_image else _requests_buckets
        limit = IMAGE_PROXY_RATE_LIMIT_MAX if is_image else RATE_LIMIT_MAX

        ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        bucket = buckets[ip]
        while bucket and now - bucket[0] > RATE_LIMIT_WINDOW:
            bucket.popleft()
        if len(bucket) >= limit:
            return JSONResponse(
                {"detail": "Trop de requêtes. Réessaie dans un instant."},
                status_code=429,
                headers={"Retry-After": str(RATE_LIMIT_WINDOW)},
            )
        bucket.append(now)

        # Nettoyage périodique des IP inactives pour éviter la croissance mémoire
        _requests_since_prune += 1
        if _requests_since_prune > 1000:
            _requests_since_prune = 0
            for buckets_map in (_requests_buckets, _image_buckets):
                for key in [k for k, b in buckets_map.items() if not b]:
                    del buckets_map[key]

    return await call_next(request)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """En-têtes de sécurité de base (CSP volontairement omise : les templates
    utilisent massivement des scripts et styles inline)."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


# ---------------------------------------------------------------------------
# Proxy d'images (Contournement de la protection anti-hotlink 403)
# Protection SSRF : whitelist de domaines + blocage des IP privées + taille max
# ---------------------------------------------------------------------------

# Domaines autorisés pour le proxy (extensibles via .env, séparés par des virgules)
_env_extra = os.getenv("IMAGE_PROXY_ALLOWED_DOMAINS", "")
ALLOWED_IMAGE_DOMAINS = {
    d.strip().lower() for d in _env_extra.split(",") if d.strip()
} | {
    "coflix.wiki",
    "www.coflix.wiki",
    "voirdrama.to",
    "www.voirdrama.to",
    "voir-anime.to",
    "www.voir-anime.to",
}

MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10 Mo


def _is_private_or_unresolvable(host: str) -> bool:
    """Résout le DNS et vérifie qu'aucune IP n'est privée/réservée (anti-SSRF).
    Un domaine non résolvable est refusé par sécurité."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return True
    return False


@app.get("/api/image-proxy")
async def image_proxy(url: str = Query(..., max_length=2048)):
    """Relaye les images distantes avec le bon Referer pour contourner les erreurs 403."""
    if not url.startswith("http://") and not url.startswith("https://"):
        raise HTTPException(status_code=400, detail="Invalid URL")

    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()

    # 1. Whitelist de domaines (anti-SSRF)
    if host not in ALLOWED_IMAGE_DOMAINS:
        raise HTTPException(status_code=403, detail="Domaine non autorisé")

    # 2. Blocage des IP privées même si le domaine est dans la whitelist
    if await asyncio.to_thread(_is_private_or_unresolvable, host):
        raise HTTPException(status_code=403, detail="Destination interdite")

    # 3. Client HTTP avec le bon Referer selon la source
    if "voirdrama" in host:
        referer = "https://voirdrama.to/"
        client = get_voirdrama_client()
    elif "voir-anime" in host:
        referer = "https://voir-anime.to/"
        client = get_voiranime_client()
    else:
        referer = "https://coflix.wiki/"
        client = get_coflix_client()

    try:
        async with client.stream(
            "GET",
            url,
            headers={
                "Referer": referer,
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            },
            timeout=10.0,
        ) as r:
            if r.status_code != 200:
                raise HTTPException(status_code=404, detail="Image introuvable")

            content_type = r.headers.get("content-type", "")
            if not content_type.startswith("image/"):
                raise HTTPException(status_code=415, detail="Type non supporté")

            # Lecture en streaming avec plafond de taille
            chunks: list[bytes] = []
            total = 0
            async for chunk in r.aiter_bytes():
                total += len(chunk)
                if total > MAX_IMAGE_SIZE:
                    raise HTTPException(status_code=413, detail="Image trop volumineuse")
                chunks.append(chunk)

            return Response(
                content=b"".join(chunks),
                media_type=content_type,
                headers={"Cache-Control": "public, max-age=604800, immutable"},
            )
    except HTTPException:
        raise
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
# robots.txt
# ---------------------------------------------------------------------------

@app.get("/robots.txt", include_in_schema=False)
async def robots_txt():
    return Response(
        "User-agent: *\n"
        "Disallow: /api/\n"
        "Disallow: /regarder/\n"
        "Disallow: /regarder-drama/\n"
        "Disallow: /regarder-anime/\n",
        media_type="text/plain; charset=utf-8",
    )


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
