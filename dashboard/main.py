import os
from pathlib import Path
from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dashboard.db import init_db, has_users, get_user, create_user
from dashboard.auth import (
    verify_password, hash_password, create_session_token,
    get_current_user, require_auth, SESSION_COOKIE, SESSION_MAX_AGE,
)
from dashboard.routes import overview, jobs, settings, logs, executions, instagram

BASE_DIR = Path(__file__).resolve().parent


def create_app() -> FastAPI:
    app = FastAPI(title="ReelsBuilder Dashboard", docs_url=None, redoc_url=None)

    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

    @app.on_event("startup")
    def on_startup():
        init_db()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    # First-run redirect middleware
    @app.middleware("http")
    async def first_run_redirect(request: Request, call_next):
        exempt = {"/setup", "/login", "/health"}
        if request.url.path not in exempt and not request.url.path.startswith("/static"):
            if not has_users():
                return RedirectResponse("/setup", 302)
        return await call_next(request)

    # ── Auth routes ──────────────────────────────────────────────────────
    @app.get("/login", response_class=HTMLResponse)
    def login_get(request: Request):
        if get_current_user(request):
            return RedirectResponse("/", 302)
        return templates.TemplateResponse("login.html", {"request": request, "error": None})

    @app.post("/login")
    def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
        user = get_user(username)
        if not user or not verify_password(password, user["password_hash"]):
            return templates.TemplateResponse(
                "login.html",
                {"request": request, "error": "Invalid username or password"},
                status_code=401,
            )
        token = create_session_token(username)
        resp = RedirectResponse("/", 302)
        resp.set_cookie(SESSION_COOKIE, token, max_age=SESSION_MAX_AGE, httponly=True, samesite="lax")
        return resp

    @app.get("/logout")
    def logout():
        resp = RedirectResponse("/login", 302)
        resp.delete_cookie(SESSION_COOKIE)
        return resp

    @app.get("/setup", response_class=HTMLResponse)
    def setup_get(request: Request):
        if has_users():
            return RedirectResponse("/login", 302)
        return templates.TemplateResponse("setup.html", {"request": request, "error": None})

    @app.post("/setup")
    def setup_post(request: Request, username: str = Form(...), password: str = Form(...)):
        if has_users():
            return RedirectResponse("/login", 302)
        if len(password) < 8:
            return templates.TemplateResponse(
                "setup.html",
                {"request": request, "error": "Password must be at least 8 characters"},
                status_code=400,
            )
        create_user(username, hash_password(password))
        token = create_session_token(username)
        resp = RedirectResponse("/", 302)
        resp.set_cookie(SESSION_COOKIE, token, max_age=SESSION_MAX_AGE, httponly=True, samesite="lax")
        return resp

    # ── Page routers ─────────────────────────────────────────────────────
    app.include_router(overview.router)
    app.include_router(jobs.router)
    app.include_router(settings.router)
    app.include_router(logs.router)
    app.include_router(executions.router)
    app.include_router(instagram.router)

    return app


app = create_app()
