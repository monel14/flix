"""Régressions Coflix : source, redirections, catégories et cache (sans réseau)."""
import asyncio
import os
import subprocess
import sys
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from starlette.requests import Request

from scraper import coflix_client
from scraper.coflix_parser import get_last_page, parse_coflix_list


def test_default_source_and_environment_override():
    env = {k: v for k, v in os.environ.items() if k != "COFLIX_SOURCE_URL"}
    cmd = [sys.executable, "-c", "from scraper.coflix_client import SOURCE_URL; print(SOURCE_URL)"]
    assert subprocess.check_output(cmd, env=env, text=True).strip() == "https://coflix.gold"
    env["COFLIX_SOURCE_URL"] = "https://example.org/"
    assert subprocess.check_output(cmd, env=env, text=True).strip() == "https://example.org"


@pytest.mark.parametrize("path", ["/movies/", "/movies/action/?page=2", "/series/drame/"])
def test_reject_path_lost_in_redirect(path):
    def handler(request):
        if request.url.path != "/":
            return httpx.Response(302, headers={"Location": "https://coflix.gold/"})
        return httpx.Response(200, text="page d'accueil identique pour toutes les catégories")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
            with patch.object(coflix_client, "get_coflix_client", return_value=client):
                with pytest.raises(coflix_client.CoflixFetchError, match="Chemin perdu"):
                    await coflix_client.coflix_get_html(path)
    asyncio.run(run())


@pytest.mark.parametrize("path", ["/", "/movies/action/", "/series/drame/?page=2"])
def test_allow_home_and_redirect_preserving_path(path):
    def handler(request):
        if request.url.host != "destination.example":
            return httpx.Response(302, headers={"Location": f"https://destination.example{request.url.raw_path.decode()}"})
        return httpx.Response(200, text="catalogue attendu")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
            with patch.object(coflix_client, "get_coflix_client", return_value=client):
                assert await coflix_client.coflix_get_html(path) == "catalogue attendu"
    asyncio.run(run())


def catalog(title, slug, last=3):
    return f'''<div class="ani items"><div class="item">
      <a class="ani poster" href="https://coflix.gold/film/{slug}/ep-123"><img src="/poster.jpg"></a>
      <a class="name d-title">{title}</a></div></div>
      <a href="?page={last}">Dernière page</a>'''


@pytest.mark.parametrize("section,genre,page", [
    ("movies", None, 1), ("movies", None, 2),
    ("movies", "action", 1), ("movies", "comedie", 2),
    ("series", None, 1), ("series", "drame", 2),
])
def test_category_path_pagination_and_parser(section, genre, page):
    from routes import home
    html = catalog("Titre de test", "titre-test-vf", 7)
    fetch = AsyncMock(return_value=html)
    async def run():
        with patch.object(home, "coflix_get_html", fetch):
            result = await home._load_home_section(section, page, genre)
        expected = f"/{section}/" + (f"{genre}/" if genre else "")
        if page > 1:
            expected += f"?page={page}"
        fetch.assert_awaited_once_with(expected)
        assert result["last_page"] == 7
        assert result["items"][0]["title"] == "Titre de test"
        assert result["items"][0]["type"] == ("movie" if section == "movies" else "series")
    asyncio.run(run())


def test_parser_keeps_different_catalogs_separate():
    action = catalog("Action test", "action-test-vf", 91)
    comedy = catalog("Comédie test", "comedie-test-vf", 117)
    assert parse_coflix_list(action, "movies")[0]["slug"] == "action-test-vf"
    assert parse_coflix_list(comedy, "movies")[0]["slug"] == "comedie-test-vf"
    assert get_last_page(action) == 91
    assert get_last_page(comedy) == 117


@pytest.mark.parametrize("section,route_name", [("movies", "movies_list"), ("series", "series_list")])
def test_cache_separates_source_genre_page_and_old_entries(section, route_name):
    from routes import home
    request = Request({"type": "http", "method": "GET", "scheme": "https",
                       "server": ("nokatv.xyz", 443), "path": "/films" if section == "movies" else "/series",
                       "query_string": b"", "headers": [(b"host", b"nokatv.xyz")]})
    keys = []
    async def get_or_set(key, ttl, loader):
        keys.append(key)
        return {"items": [], "last_page": 7}
    async def run():
        with patch.object(home.cache, "get_or_set", side_effect=get_or_set), patch.object(home.templates, "TemplateResponse", return_value=None):
            for genre, page in [(None, 1), ("action", 1), ("comedie", 1), ("action", 2)]:
                await getattr(home, route_name)(request, page=page, genre=genre, version=None)
    asyncio.run(run())
    assert len(set(keys)) == 4
    assert all(key.startswith(f"coflix:{home.SOURCE_URL}:list:{section}:") for key in keys)
    assert all(not key.startswith("list:") for key in keys)
