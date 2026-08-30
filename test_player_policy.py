from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.testclient import TestClient

from services.player_policy import VIDZY_SANDBOX, player_sandbox
from services.templates import TEMPLATES_DIR


def make_server(name: str, link: str):
    return SimpleNamespace(
        server_name=name,
        server_link=link,
        server_type="embed",
        version="",
    )


def test_player_sandbox_vidzy_and_mytv():
    # Vidzy
    assert player_sandbox(make_server("Serveur #1", "https://vidzy.cc/embed-123.html")) == VIDZY_SANDBOX
    assert player_sandbox(make_server("Vidzy", "https://vidzy.cc/e/abc")) == VIDZY_SANDBOX
    # MyTV / Mail.ru
    assert player_sandbox(make_server("MyTV", "https://mytv.to/e/abc")) == VIDZY_SANDBOX
    assert player_sandbox(make_server("Serveur My TV", "https://example.com/embed")) == VIDZY_SANDBOX
    assert player_sandbox(make_server("Mail.ru", "https://my.mail.ru/video/embed/abc")) == VIDZY_SANDBOX
    # Autres lecteurs sans sandbox
    assert player_sandbox(make_server("VOE", "https://voe.sx/e/abc")) == ""
    assert player_sandbox(make_server("Vidmoly", "https://vidmoly.to/e/abc")) == ""
    assert player_sandbox(make_server("Streamtape", "https://streamtape.com/e/abc")) == ""
    assert player_sandbox(make_server("Lecteur inconnu", "https://example.test/embed")) == ""


def test_player_templates_render_per_server_sandbox():
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=str(TEMPLATES_DIR.parent / "static")), name="static")

    env = Jinja2Templates(directory=str(TEMPLATES_DIR))
    env.env.globals["str"] = str
    env.env.globals["player_sandbox"] = player_sandbox

    servers = [
        make_server("Vidzy", "https://vidzy.cc/embed-1"),
        make_server("VOE", "https://voe.sx/e/2"),
        make_server("MyTV", "https://my.mail.ru/video/embed/3"),
        make_server("Inconnu", "https://example.test/4"),
    ]

    @app.get("/player", response_class=HTMLResponse)
    async def player_page(request: Request):
        return env.TemplateResponse(request, "player.html", {
            "film": {"title": "Test", "image": ""},
            "slug": "test",
            "episode_id": "1",
            "servers": servers,
            "default_server": servers[0],
            "current_ep": None,
            "episodes": [],
            "prev_ep": None,
            "next_ep": None,
            "seo": None,
        })

    @app.get("/anime-player", response_class=HTMLResponse)
    async def anime_player_page(request: Request):
        return env.TemplateResponse(request, "anime_player.html", {
            "anime": {"title": "Test", "image": ""},
            "slug": "test",
            "episode_slug": "episode-1",
            "servers": servers,
            "default_server": servers[0],
            "current_ep": None,
            "episodes": [],
            "prev_ep": None,
            "next_ep": None,
            "seo": None,
        })

    @app.get("/drama-player", response_class=HTMLResponse)
    async def drama_player_page(request: Request):
        return env.TemplateResponse(request, "drama_player.html", {
            "drama": {"title": "Test", "image": ""},
            "slug": "test",
            "episode_slug": "episode-1",
            "servers": servers,
            "default_server": servers[0],
            "current_ep": None,
            "episodes": [],
            "prev_ep": None,
            "next_ep": None,
            "seo": None,
        })

    client = TestClient(app)
    for path in ("/player", "/anime-player", "/drama-player"):
        response = client.get(path)
        assert response.status_code == 200, response.text
        html = response.text
        assert 'data-link="https://vidzy.cc/embed-1"' in html
        assert f'data-sandbox="{VIDZY_SANDBOX}"' in html
        assert f'sandbox="{VIDZY_SANDBOX}"' in html
        assert "frame.src = link;" in html
