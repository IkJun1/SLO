from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import ensure_runtime_paths, get_settings
from app.db import SessionLocal, init_db
from app.errors import register_exception_handlers
from app.models import UserAccount
from app.routes import core_router, public_router
from app.security import require_mcp_api_key, require_user_auth
from app.services import AUTH_COOKIE_NAME, bootstrap_documents_from_vault, has_registered_user, read_username_from_token


STATIC_DIR = Path(__file__).resolve().parent / "static"


def _has_registered_users() -> bool:
    db = SessionLocal()
    try:
        return has_registered_user(db)
    finally:
        db.close()


def _is_authenticated_request(request: Request) -> bool:
    token = str(request.cookies.get(AUTH_COOKIE_NAME, "")).strip()
    username = read_username_from_token(token)
    if username is None:
        return False

    db = SessionLocal()
    try:
        return db.get(UserAccount, username) is not None
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    ensure_runtime_paths(settings)
    init_db()

    bootstrap_db = SessionLocal()
    try:
        bootstrap_documents_from_vault(bootstrap_db, settings)
        bootstrap_db.commit()
    except Exception:
        bootstrap_db.rollback()
        raise
    finally:
        bootstrap_db.close()

    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="SLO API", version="0.1.0", lifespan=lifespan)
    register_exception_handlers(app)

    user_router = APIRouter(prefix="/user", dependencies=[Depends(require_user_auth)])
    user_router.include_router(core_router)

    legacy_user_router = APIRouter(dependencies=[Depends(require_user_auth)])
    legacy_user_router.include_router(core_router)

    mcp_router = APIRouter(prefix="/mcp", dependencies=[Depends(require_mcp_api_key)])
    mcp_router.include_router(core_router)

    app.include_router(public_router, prefix=settings.api_prefix)
    app.include_router(user_router, prefix=settings.api_prefix)
    app.include_router(legacy_user_router, prefix=settings.api_prefix)
    app.include_router(mcp_router, prefix=settings.api_prefix)

    app.mount("/static/login", StaticFiles(directory=STATIC_DIR / "login"), name="static_login")
    app.mount("/static/signup", StaticFiles(directory=STATIC_DIR / "signup"), name="static_signup")
    app.mount("/static", StaticFiles(directory=STATIC_DIR / "main"), name="static")

    @app.get("/", include_in_schema=False)
    def serve_frontend(request: Request) -> RedirectResponse:
        if not _has_registered_users():
            return RedirectResponse(url="/signup", status_code=303)
        if _is_authenticated_request(request):
            return RedirectResponse(url="/app", status_code=303)
        return RedirectResponse(url="/login", status_code=303)

    @app.get("/app", include_in_schema=False)
    def serve_main_app(request: Request) -> Response:
        if not _has_registered_users():
            return RedirectResponse(url="/signup", status_code=303)
        if not _is_authenticated_request(request):
            return RedirectResponse(url="/login", status_code=303)
        return FileResponse(STATIC_DIR / "main/index.html")

    @app.get("/login", include_in_schema=False)
    def serve_login(request: Request) -> Response:
        if not _has_registered_users():
            return RedirectResponse(url="/signup", status_code=303)
        if _is_authenticated_request(request):
            return RedirectResponse(url="/app", status_code=303)
        return FileResponse(STATIC_DIR / "login/index.html")

    @app.get("/signup", include_in_schema=False)
    def serve_signup(request: Request) -> Response:
        if _has_registered_users():
            if _is_authenticated_request(request):
                return RedirectResponse(url="/app", status_code=303)
            return RedirectResponse(url="/login", status_code=303)
        return FileResponse(STATIC_DIR / "signup/index.html")

    return app


app = create_app()
