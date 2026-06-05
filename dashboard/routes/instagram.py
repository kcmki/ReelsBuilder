from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from dashboard.auth import require_auth
import dashboard.api_client as api

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
router = APIRouter()


@router.get("/instagram", response_class=HTMLResponse)
def instagram_page(request: Request, user: str = Depends(require_auth), page: int = 1):
    try:
        clips_data = api.get_clips(page=1, per_page=50)
        published = [c for c in clips_data.get("items", []) if c.get("published") and not c.get("deleted_at")]
    except Exception:
        published = []
    try:
        interactions = api.get_interactions(page=page)
    except Exception:
        interactions = {"items": [], "total": 0, "page": 1}

    return templates.TemplateResponse(request, "instagram.html", {
        "active": "instagram", "user": user,
        "published": published, "interactions": interactions, "page": page,
    })


@router.post("/instagram/sync", response_class=HTMLResponse)
def instagram_sync(request: Request, user: str = Depends(require_auth)):
    try:
        result = api.instagram_sync()
        added = result.get("added", 0)
        return HTMLResponse(f'<div class="alert alert-success">Sync complete — {added} new item(s) added.</div>')
    except Exception as e:
        return HTMLResponse(f'<div class="alert alert-error">Sync failed: {e}</div>', status_code=500)
