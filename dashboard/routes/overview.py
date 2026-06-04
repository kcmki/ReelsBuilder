from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from dashboard.auth import require_auth
import dashboard.api_client as api

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
router = APIRouter()


def _fetch_overview():
    try:
        status = api.get_status()
    except Exception:
        status = {}
    try:
        total_videos = api.get_videos(per_page=1).get("total", "—")
    except Exception:
        total_videos = "—"
    try:
        total_clips = api.get_clips(per_page=1).get("total", "—")
    except Exception:
        total_clips = "—"
    try:
        total_interactions = api.get_interactions(per_page=1).get("total", "—")
    except Exception:
        total_interactions = "—"
    return status, total_videos, total_clips, total_interactions


@router.get("/", response_class=HTMLResponse)
def overview_page(request: Request, user: str = Depends(require_auth)):
    status, tv, tc, ti = _fetch_overview()
    return templates.TemplateResponse("overview.html", {
        "request": request, "active": "overview", "user": user,
        "status": status, "total_videos": tv, "total_clips": tc,
        "total_interactions": ti, "partial": False,
    })


@router.get("/overview/refresh", response_class=HTMLResponse)
def overview_refresh(request: Request, user: str = Depends(require_auth)):
    status, tv, tc, ti = _fetch_overview()
    return templates.TemplateResponse("overview.html", {
        "request": request, "active": "overview", "user": user,
        "status": status, "total_videos": tv, "total_clips": tc,
        "total_interactions": ti, "partial": True,
    })
