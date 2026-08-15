from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from scraper.coflix_client import close_coflix_client
from routes import home, detail, player, search


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await close_coflix_client()


app = FastAPI(title="Coflix", lifespan=lifespan)

BASE_DIR = Path(__file__).resolve().parent

static_dir = BASE_DIR / "static"
static_dir.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=static_dir), name="static")

templates = Jinja2Templates(directory=BASE_DIR / "templates")


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
