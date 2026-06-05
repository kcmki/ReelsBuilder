from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from dashboard.auth import require_auth
import dashboard.api_client as api

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
router = APIRouter()

_TEXTAREA_KEYS = {"reply_message", "reels_caption", "queries"}


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, user: str = Depends(require_auth)):
    try:
        cfg = api.get_settings()
    except Exception:
        cfg = {}
    return templates.TemplateResponse(request, "settings.html", {
        "active": "settings", "user": user,
        "cfg": cfg, "saved": False, "error": None, "textarea_keys": _TEXTAREA_KEYS,
    })


@router.post("/settings", response_class=HTMLResponse)
async def settings_save(request: Request, user: str = Depends(require_auth)):
    form = await request.form()
    body: dict = {}
    for key, value in form.items():
        if "__" in key:
            section, field = key.split("__", 1)
            body.setdefault(section, {})[field] = value
    try:
        api.update_settings(body)
        cfg = api.get_settings()
        return templates.TemplateResponse(request, "settings.html", {
            "active": "settings", "user": user,
            "cfg": cfg, "saved": True, "error": None, "textarea_keys": _TEXTAREA_KEYS,
        })
    except Exception as e:
        try:
            cfg = api.get_settings()
        except Exception:
            cfg = {}
        return templates.TemplateResponse(request, "settings.html", {
            "active": "settings", "user": user,
            "cfg": cfg, "saved": False, "error": str(e), "textarea_keys": _TEXTAREA_KEYS,
        }, status_code=500)
