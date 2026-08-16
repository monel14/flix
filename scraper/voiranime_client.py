from __future__ import annotations

import asyncio
import logging
import os

import httpx

SOURCE_URL = os.getenv("VOIRANIME_SOURCE_URL", "https://voir-anime.to").rstrip("/")
TIMEOUT_SECONDS = 15

logger = logging.getLogger(__name__)

_http_client: httpx.AsyncClient | None = None
_client_loop: asyncio.AbstractEventLoop | None = None


class VoiranimeFetchError(RuntimeError):
    pass


class VoiranimeNotFoundError(VoiranimeFetchError):
    pass


def get_voiranime_client() -> httpx.AsyncClient:
    """Retourne le client HTTP async singleton pour voir-anime.to lié à la boucle courante."""
    global _http_client, _client_loop
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if _http_client is None or _http_client.is_closed or _client_loop != current_loop:
        _http_client = httpx.AsyncClient(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
                "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8",
                "Referer": SOURCE_URL + "/",
            },
            timeout=TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        _client_loop = current_loop
    return _http_client


async def close_voiranime_client() -> None:
    """Ferme la session HTTP persistante."""
    global _http_client, _client_loop
    if _http_client is not None and not _http_client.is_closed:
        await _http_client.aclose()
    _http_client = None
    _client_loop = None


async def voiranime_get_html(path: str, params: dict | None = None) -> str:
    """GET HTML depuis voir-anime.to avec retry + backoff."""
    url = path if path.startswith("http") else SOURCE_URL + path
    client = get_voiranime_client()
    backoff = 2.0

    for attempt in range(3):
        logger.info("Voiranime fetch %s (attempt %d/3)", url, attempt + 1)
        try:
            r = await client.get(url, params=params)
            if r.status_code == 429:
                logger.warning("Voiranime 429 pour %s, attente %.1fs", url, backoff)
                await asyncio.sleep(backoff)
                backoff *= 2.0
                continue
            if r.status_code == 404:
                raise VoiranimeNotFoundError(f"Introuvable : {url}")
            r.raise_for_status()
            return r.text
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise VoiranimeNotFoundError(f"Introuvable : {url}") from exc
            if exc.response.status_code == 429:
                await asyncio.sleep(backoff)
                backoff *= 2.0
                continue
            raise VoiranimeFetchError(f"HTTP {exc.response.status_code} pour {url}") from exc
        except httpx.HTTPError as exc:
            if attempt < 2:
                await asyncio.sleep(1.5)
                continue
            raise VoiranimeFetchError(f"Erreur réseau pour {url} : {exc}") from exc

    raise VoiranimeFetchError(f"Échec après 3 tentatives pour {url}")
