from html import escape
from pathlib import Path

import httpx
from fastapi import APIRouter, Request, Depends, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

from dashboard.auth import require_auth
import dashboard.api_client as api

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
router = APIRouter()

_TEXT_FIELDS = ("name", "ig_business_id", "page_id", "access_token", "cron", "queries", "caption", "reply_message")


def _alert(msg: str, ok: bool = True, status: int = 200) -> HTMLResponse:
    return HTMLResponse(f'<div class="alert alert-{"success" if ok else "error"}">{escape(str(msg))}</div>',
                        status_code=status)


@router.get("/accounts", response_class=HTMLResponse)
def accounts_page(request: Request, user: str = Depends(require_auth)):
    try:
        accounts, error = api.list_accounts(), None
    except Exception as e:
        accounts, error = [], str(e)
    return templates.TemplateResponse(request, "accounts.html", {
        "active": "accounts", "user": user, "accounts": accounts, "error": error})


def _form_page(request, user, account, error=None, status=200):
    try:
        fields = api.get_edit_fields()
    except Exception as e:
        fields, error = [], error or str(e)
    return templates.TemplateResponse(request, "account_form.html", {
        "active": "accounts", "user": user, "a": account, "fields": fields, "error": error,
    }, status_code=status)


@router.get("/accounts/new", response_class=HTMLResponse)
def account_new(request: Request, user: str = Depends(require_auth)):
    return _form_page(request, user, {"enabled": 1, "cron": "0 * * * *", "edit": {}})


@router.get("/accounts/{account_id}", response_class=HTMLResponse)
def account_edit(account_id: int, request: Request, user: str = Depends(require_auth)):
    try:
        return _form_page(request, user, api.get_account(account_id))
    except api.ApiError:
        return RedirectResponse("/accounts", 302)


@router.post("/accounts/save", response_class=HTMLResponse)
async def account_save(request: Request, user: str = Depends(require_auth)):
    form = await request.form()
    data = {k: form.get(k, "") for k in _TEXT_FIELDS}
    # checkboxes post a hidden "0" first, then "1" when ticked: the last value wins
    data["enabled"] = int(form.getlist("enabled")[-1]) if form.getlist("enabled") else 0
    data["edit"] = {k[6:]: form.getlist(k)[-1] for k in form.keys() if k.startswith("edit__")}
    account_id = int(form.get("id") or 0)
    try:
        saved = api.update_account(account_id, data) if account_id else api.create_account(data)
        return RedirectResponse(f"/accounts/{saved['id']}?saved=1", 303)
    except Exception as e:
        data["id"] = account_id or None
        return _form_page(request, user, data, error=str(e), status=400)


@router.post("/accounts/{account_id}/delete")
def account_delete(account_id: int, user: str = Depends(require_auth)):
    api.delete_account(account_id)
    return RedirectResponse("/accounts", 303)


@router.post("/accounts/{account_id}/logo")
async def account_logo(account_id: int, request: Request, logo: UploadFile = File(None), remove: str = Form(""),
                       user: str = Depends(require_auth)):
    data = b"" if remove or not logo else await logo.read()
    try:
        api.upload_logo(account_id, data)
    except Exception as e:
        return _form_page(request, user, api.get_account(account_id), error=f"Logo: {e}", status=400)
    return RedirectResponse(f"/accounts/{account_id}?saved=1", 303)


@router.post("/accounts/{account_id}/test", response_class=HTMLResponse)
def account_test(account_id: int, user: str = Depends(require_auth)):
    try:
        info = api.test_account(account_id)
        return _alert(f"Connected to @{info.get('username')} · {info.get('followers_count', '?')} followers")
    except Exception as e:
        return _alert(f"Connection failed: {e}", ok=False)


@router.post("/accounts/{account_id}/run", response_class=HTMLResponse)
def account_run(account_id: int, user: str = Depends(require_auth)):
    try:
        api.run_job(f"account_{account_id}")
        return _alert("Run started — follow it in Logs.")
    except Exception as e:
        return _alert(e, ok=False)


@router.post("/accounts/{account_id}/process", response_class=HTMLResponse)
async def account_process(account_id: int, request: Request, user: str = Depends(require_auth)):
    url = (await request.form()).get("url", "")
    try:
        r = api.process_url(account_id, url)
        return _alert(f"Rendering {r['video_id']} — it will appear in the Reels queue when done.")
    except Exception as e:
        return _alert(e, ok=False)


# --- reels queue -------------------------------------------------------------------
@router.get("/reels", response_class=HTMLResponse)
def reels_page(request: Request, user: str = Depends(require_auth), account_id: int = 0,
               status: str = "queued", page: int = 1):
    try:
        clips = api.get_clips(page=page, per_page=24, account_id=account_id, status=status)
        accounts = api.list_accounts()
    except Exception:
        clips, accounts = {"items": [], "total": 0}, []
    return templates.TemplateResponse(request, "reels.html", {
        "active": "reels", "user": user, "clips": clips, "accounts": accounts,
        "account_id": account_id, "status": status, "page": page,
    })


@router.post("/reels/{clip_id}/publish", response_class=HTMLResponse)
def reel_publish(clip_id: int, user: str = Depends(require_auth)):
    try:
        api.publish_clip(clip_id)
        return _alert("Publishing… refresh in a minute.")
    except Exception as e:
        return _alert(e, ok=False)


@router.post("/reels/{clip_id}/delete", response_class=HTMLResponse)
def reel_delete(clip_id: int, user: str = Depends(require_auth)):
    api.delete_clip(clip_id)
    return HTMLResponse("")  # htmx removes the card


@router.get("/reels/{clip_id}/video")
def reel_video(clip_id: int, request: Request, user: str = Depends(require_auth)):
    """Proxy the mp4 (with Range support so the <video> player can seek)."""
    client = httpx.Client(base_url=api.INTERNAL_API_URL, timeout=None)
    headers = {"range": request.headers["range"]} if "range" in request.headers else {}
    r = client.send(client.build_request("GET", f"/clips/{clip_id}/file", headers=headers), stream=True)
    keep = ("content-type", "content-length", "content-range", "accept-ranges")
    return StreamingResponse(r.iter_bytes(), status_code=r.status_code,
                             headers={k: r.headers[k] for k in keep if k in r.headers},
                             background=BackgroundTask(lambda: (r.close(), client.close())))
