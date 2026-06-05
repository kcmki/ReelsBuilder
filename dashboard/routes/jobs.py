from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from dashboard.auth import require_auth
import dashboard.api_client as api

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
router = APIRouter()

_JOB_LABELS = {
    "search_job":   "Search & Publish",
    "interact_job": "Respond Interactions",
    "cleanup_job":  "Cleanup Storage",
}


@router.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request, user: str = Depends(require_auth)):
    try:
        data = api.get_jobs()
        status = api.get_status()
        jobs = data.get("jobs", [])
        for j in jobs:
            j["label"] = _JOB_LABELS.get(j["id"], j["id"])
        interactions_enabled = status.get("interactions_enabled", True)
    except Exception:
        jobs, interactions_enabled = [], True
    return templates.TemplateResponse(request, "jobs.html", {
        "active": "jobs", "user": user,
        "jobs": jobs, "interactions_enabled": interactions_enabled,
    })


@router.post("/jobs/{job_id}/run", response_class=HTMLResponse)
def run_job(job_id: str, request: Request, user: str = Depends(require_auth)):
    try:
        api.run_job(job_id)
        label = _JOB_LABELS.get(job_id, job_id)
        return HTMLResponse(f'<div class="alert alert-success">Job <strong>{label}</strong> triggered.</div>')
    except Exception as e:
        return HTMLResponse(f'<div class="alert alert-error">Error: {e}</div>', status_code=500)


@router.post("/jobs/{job_id}/toggle", response_class=HTMLResponse)
def toggle_job(job_id: str, request: Request, user: str = Depends(require_auth)):
    try:
        result = api.toggle_job(job_id)
        enabled = result.get("enabled", False)
        cls = "badge-green" if enabled else "badge-gray"
        label = "Enabled" if enabled else "Disabled"
        return HTMLResponse(f'<span id="interact-badge" class="badge {cls}">{label}</span>')
    except Exception as e:
        return HTMLResponse(f'<span class="badge badge-red">Error: {e}</span>', status_code=500)
