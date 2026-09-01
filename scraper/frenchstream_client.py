from __future__ import annotations

import asyncio
import logging
import os

import httpx

# Domaine actif du miroir FrenchStream (plateforme .fss.lol).
# french-stream.one et fs16.lol servent le même contenu ; french-stream.cv
# redirige vers la page d'accueil et n'a pas la catégorie K-Drama.
SOURCE_URL = os.getenv("FRENCHSTREAM_SOURCE_URL", "https://french-stream.one").rstrip("/")
TIMEOUT_SECONDS = 15

logger = logging.getLogger(__name__)

_http_client: httpx.AsyncClient | None = None
_client_loop: asyncio.AbstractEventLoop | None = None


class FrenchstreamFetchError(RuntimeError):
    pass


class FrenchstreamNotFoundError(FrenchstreamFetchError):
    pass


def get_frenchstream_client() -> httpx.AsyncClient:
    """Retourne le client HTTP async singleton pour french-stream.one lié à la boucle courante."""
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
                    "Chrome/140.0.0.0 Safari/537.36"
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


async def close_frenchstream_client() -> None:
    """Ferme la session HTTP persistante."""
    global _http_client, _client_loop
    if _http_client is not None and not _http_client.is_closed:
        await _http_client.aclose()
    _http_client = None
    _client_loop = None


async def frenchstream_get_html(path: str, params: dict | None = None) -> str:
    """GET HTML depuis french-stream.one avec retry + backoff."""
    url = path if path.startswith("http") else SOURCE_URL + path
    client = get_frenchstream_client()
    backoff = 2.0

    for attempt in range(3):
        logger.debug("FrenchStream fetch %s (attempt %d/3)", url, attempt + 1)
        try:
            r = await client.get(url, params=params)
            if r.status_code == 404:
                raise FrenchstreamNotFoundError(f"Introuvable : {url}")
            r.raise_for_status()
            return r.text
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise FrenchstreamNotFoundError(f"Introuvable : {url}") from exc
            if exc.response.status_code == 429:
                logger.warning("FrenchStream 429 pour %s, attente %.1fs", url, backoff)
                await asyncio.sleep(backoff)
                backoff *= 2.0
                continue
            raise FrenchstreamFetchError(f"HTTP {exc.response.status_code} pour {url}") from exc
        except httpx.HTTPError as exc:
            if attempt < 2:
                await asyncio.sleep(1.5)
                continue
            raise FrenchstreamFetchError(f"Erreur réseau pour {url} : {exc}") from exc

    raise FrenchstreamFetchError(f"Échec après 3 tentatives pour {url}")


async def frenchstream_get_fiche(newsid: int) -> str:
    """Récupère le HTML de la fiche d'une série FrenchStream (poster, titre)."""
    url = f"{SOURCE_URL}/index.php?newsid={newsid}"
    client = get_frenchstream_client()
    try:
        r = await client.get(url, timeout=12)
        if r.status_code == 404:
            raise FrenchstreamNotFoundError(f"Fiche introuvable : {url}")
        r.raise_for_status()
        return r.text
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise FrenchstreamNotFoundError(f"Fiche introuvable : {url}") from exc
        raise FrenchstreamFetchError(f"HTTP {exc.response.status_code} pour {url}") from exc
    except httpx.HTTPError as exc:
        raise FrenchstreamFetchError(f"Erreur réseau pour {url} : {exc}") from exc


async def frenchstream_get_json(newsid: int) -> dict:
    """Récupère le JSON d'épisodes d'une série FrenchStream (chemins anti-adblock, tous équivalents).

    Le premier chemin (ressemble à un asset statique) est le moins susceptible
    d'être bloqué ; les suivants servent de repli.
    """
    client = get_frenchstream_client()
    paths = (
        f"/static/series/{newsid}.js",
        f"/css/sr_{newsid}.css",
        f"/data/eps_{newsid}.txt",
        f"/ep-data.php?id={newsid}&format=js",
    )
    last_error: Exception | None = None
    for path in paths:
        url = SOURCE_URL + path
        try:
            r = await client.get(url, timeout=10)
            r.raise_for_status()
            try:
                data = r.json()
            except ValueError:
                data = None
            if isinstance(data, dict):
                return data
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
            logger.debug("FrenchStream JSON %s indisponible : %s", url, exc)
    raise FrenchstreamFetchError(
        f"JSON épisodes introuvable pour newsid {newsid}"
        + (f" ({last_error})" if last_error else "")
    )
