from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from dashboard.auth import require_auth
import dashboard.api_client as api

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
router = APIRouter()


@router.get("/executions", response_class=HTMLResponse)
def executions_page(
    request: Request,
    user: str = Depends(require_auth),
    tab: str = "videos",
    page: int = 1,
):
    try:
        videos = api.get_videos(page=page if tab == "videos" else 1)
    except Exception:
        videos = {"items": [], "total": 0, "page": 1}
    try:
        clips = api.get_clips(page=page if tab == "clips" else 1, per_page=20)
    except Exception:
        clips = {"items": [], "total": 0, "page": 1}

    return templates.TemplateResponse("executions.html", {
        "request": request, "active": "executions", "user": user,
        "tab": tab, "videos": videos, "clips": clips, "page": page,
    })
