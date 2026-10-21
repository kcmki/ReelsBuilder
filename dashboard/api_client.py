import os
import httpx

INTERNAL_API_URL = os.getenv("INTERNAL_API_URL", "http://reelsbuilder:8080")
_TIMEOUT = 15.0


class ApiError(Exception):
    pass


def _req(method: str, path: str, **kw):
    with httpx.Client(base_url=INTERNAL_API_URL, timeout=_TIMEOUT) as c:
        r = c.request(method, path, **kw)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise ApiError(detail)
    return r.json()


def _get(path: str, **params):
    return _req("GET", path, params=params)


def _post(path: str, json: dict = None):
    return _req("POST", path, json=json)


def _put(path: str, json: dict):
    return _req("PUT", path, json=json)


def get_status() -> dict:
    return _get("/status")


def get_jobs() -> dict:
    return _get("/jobs")


def run_job(job_id: str) -> dict:
    return _post(f"/jobs/{job_id}/run")


def toggle_job(job_id: str) -> dict:
    return _post(f"/jobs/{job_id}/toggle")


def get_settings() -> dict:
    return _get("/settings")


def update_settings(data: dict) -> dict:
    return _put("/settings", json=data)


def get_videos(page: int = 1, per_page: int = 20) -> dict:
    return _get("/db/videos", page=page, per_page=per_page)


def get_clips(page: int = 1, per_page: int = 50, account_id: int = 0, status: str = "") -> dict:
    return _get("/db/clips", page=page, per_page=per_page, account_id=account_id, status=status)


def get_interactions(page: int = 1, per_page: int = 20) -> dict:
    return _get("/db/interactions", page=page, per_page=per_page)


def instagram_sync() -> dict:
    return _post("/instagram/sync")


# --- accounts ----------------------------------------------------------------------
def get_edit_fields() -> list:
    return _get("/edit-fields")


def list_accounts() -> list:
    return _get("/accounts")


def get_account(account_id: int) -> dict:
    return _get(f"/accounts/{account_id}")


def create_account(data: dict) -> dict:
    return _post("/accounts", json=data)


def update_account(account_id: int, data: dict) -> dict:
    return _put(f"/accounts/{account_id}", json=data)


def delete_account(account_id: int) -> dict:
    return _req("DELETE", f"/accounts/{account_id}")


def upload_logo(account_id: int, data: bytes) -> dict:
    return _req("PUT", f"/accounts/{account_id}/logo", content=data)


def test_account(account_id: int) -> dict:
    return _post(f"/accounts/{account_id}/test")


def process_url(account_id: int, url: str) -> dict:
    return _post(f"/accounts/{account_id}/process", json={"url": url})


def publish_clip(clip_id: int) -> dict:
    return _post(f"/clips/{clip_id}/publish")


def delete_clip(clip_id: int) -> dict:
    return _req("DELETE", f"/clips/{clip_id}")
