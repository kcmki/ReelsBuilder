import httpx
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from dashboard.auth import require_auth
from dashboard.api_client import INTERNAL_API_URL

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
router = APIRouter()


@router.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request, user: str = Depends(require_auth)):
    return templates.TemplateResponse("logs.html", {
        "request": request, "active": "logs", "user": user,
    })


@router.get("/logs/stream")
def stream_logs(request: Request, user: str = Depends(require_auth)):
    def generate():
        try:
            with httpx.Client(timeout=None) as client:
                with client.stream("GET", f"{INTERNAL_API_URL}/logs/stream") as resp:
                    for line in resp.iter_lines():
                        if line:
                            yield f"{line}\n\n"
        except Exception as e:
            yield f"data: [stream error: {e}]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
