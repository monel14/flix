"""API-only deployment entrypoint. Legacy HTML routes remain in main.py for rollback."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / '.env')

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import PlainTextResponse
from routes.api_v1 import router
from cache import cache
from services.image_proxy import UnsafeImageURL, validate_url, fetch_image
from scraper.coflix_client import SOURCE_URL as COFLIX_SOURCE_URL, close_coflix_client
from scraper.frenchstream_client import close_frenchstream_client
from scraper.voirdrama_client import close_voirdrama_client
from scraper.voiranime_client import close_voiranime_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('nokatv.api')


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        cache.purge_expired()
    except Exception:
        logger.warning('Cache cleanup unavailable')
    # Preserve the catalogue warm-up used by the legacy application.
    async def warm():
        try:
            from routes.drama import _frenchstream_catalog
            await _frenchstream_catalog()
        except Exception:
            logger.warning('Catalogue warm-up unavailable')
    task = asyncio.create_task(warm())
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await close_coflix_client()
        await close_frenchstream_client()
        await close_voirdrama_client()
        await close_voiranime_client()


app = FastAPI(title='NokaTV API', docs_url=None, redoc_url=None,
              openapi_url=None, lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.middleware('http')
async def private_responses(request, call_next):
    response = await call_next(request)
    if not (request.url.path == '/api/image-proxy' and response.status_code == 200):
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['X-Robots-Tag'] = 'noindex, nofollow'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@app.get('/health')
@app.get('/api/health')
async def health():
    """Process health only; does not assert upstream availability."""
    return {'status': 'ok', 'service': 'nokatv-api'}


@app.get('/robots.txt', response_class=PlainTextResponse)
async def robots():
    return 'User-agent: *\nDisallow: /\nAllow: /api/image-proxy\n'


@app.get('/api/image-proxy')
async def image_proxy(url: str = Query(...)):
    """Public images only; no credentials, arbitrary ports or internal destinations."""
    try:
        parsed = validate_url(url)
    except UnsafeImageURL:
        raise HTTPException(status_code=400, detail='Image destination not allowed') from None
    host = parsed.hostname.lower()
    referer = ('https://voiranime.cx/' if host in {'voiranime.cx', 'voir-anime.to'}
               else 'https://voirdrama.to/' if host in {'voirdrama.to', 'voirdrama.org'}
               else COFLIX_SOURCE_URL.rstrip('/') + '/')
    try:
        body, mime = await fetch_image(url, referer)
        return Response(content=body, media_type=mime, headers={
            'Cache-Control': 'public, max-age=604800, immutable',
            'Content-Security-Policy': "default-src 'none'; sandbox",
        })
    except UnsafeImageURL:
        raise HTTPException(status_code=400, detail='Image destination not allowed') from None
    except Exception:
        # Do not log URLs, query parameters or secrets from upstream responses.
        logger.warning('Image unavailable from allowed source')

    placeholder = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="450" viewBox="0 0 300 450">'
        '<rect width="300" height="450" fill="#131622"/>'
        '<text x="50%" y="50%" font-family="sans-serif" font-size="16" fill="#64748b" text-anchor="middle" dominant-baseline="middle">'
        'Affiche indisponible'
        '</text></svg>'
    )
    return Response(content=placeholder, media_type='image/svg+xml')


app.include_router(router)
