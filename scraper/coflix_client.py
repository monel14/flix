from __future__ import annotations

import asyncio
import logging

import httpx

BASE_URL = "https://coflix.wiki"
TIMEOUT_SECONDS = 15

logger = logging.getLogger(__name__)

_http_client: httpx.AsyncClient | None = None


class CoflixFetchError(RuntimeError):
    pass


class CoflixNotFoundError(CoflixFetchError):
    pass


def get_coflix_client() -> httpx.AsyncClient:
    """Retourne le client HTTP async singleton pour coflix.wiki."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
                "Accept-Language": "fr-FR,fr;q=0.9",
                "Referer": BASE_URL + "/",
            },
            timeout=TIMEOUT_SECONDS,
            follow_redirects=True,
        )
    return _http_client


async def close_coflix_client() -> None:
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


async def coflix_get_html(path: str) -> str:
    """GET HTML depuis coflix.wiki avec retry + backoff."""
    url = path if path.startswith("http") else BASE_URL + path
    client = get_coflix_client()
    backoff = 3.0

    for attempt in range(3):
        logger.info("Coflix fetch %s (attempt %d/3)", url, attempt + 1)
        try:
            r = await client.get(url)
            if r.status_code == 429:
                logger.warning("Coflix 429 pour %s, attente %.1fs", url, backoff)
                await asyncio.sleep(backoff)
                backoff *= 2.0
                continue
            if r.status_code == 404:
                raise CoflixNotFoundError(f"Introuvable : {url}")
            r.raise_for_status()
            return r.text
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise CoflixNotFoundError(f"Introuvable : {url}") from exc
            if exc.response.status_code == 429:
                await asyncio.sleep(backoff)
                backoff *= 2.0
                continue
            raise CoflixFetchError(f"HTTP {exc.response.status_code} pour {url}") from exc
        except httpx.HTTPError as exc:
            if attempt < 2:
                await asyncio.sleep(2.0)
                continue
            raise CoflixFetchError(f"Erreur réseau pour {url} : {exc}") from exc

    raise CoflixFetchError(f"Échec après 3 tentatives pour {url}")


async def coflix_get_json(path: str) -> dict:
    """GET JSON depuis l'API AJAX de coflix.wiki."""
    url = path if path.startswith("http") else BASE_URL + path
    client = get_coflix_client()

    for attempt in range(3):
        try:
            r = await client.get(
                url,
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as exc:
            if attempt < 2:
                await asyncio.sleep(2.0)
                continue
            raise CoflixFetchError(f"Erreur JSON pour {url} : {exc}") from exc

    raise CoflixFetchError(f"Échec JSON après 3 tentatives pour {url}")
