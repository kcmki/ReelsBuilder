import os
import httpx

INTERNAL_API_URL = os.getenv("INTERNAL_API_URL", "http://reelsbuilder:8080")
_TIMEOUT = 10.0


def _get(path: str, **params) -> dict:
    with httpx.Client(base_url=INTERNAL_API_URL, timeout=_TIMEOUT) as c:
        return c.get(path, params=params).json()


def _post(path: str, json: dict = None) -> dict:
    with httpx.Client(base_url=INTERNAL_API_URL, timeout=_TIMEOUT) as c:
        return c.post(path, json=json).json()


def _put(path: str, json: dict) -> dict:
    with httpx.Client(base_url=INTERNAL_API_URL, timeout=_TIMEOUT) as c:
        return c.put(path, json=json).json()


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


def get_clips(page: int = 1, per_page: int = 50) -> dict:
    return _get("/db/clips", page=page, per_page=per_page)


def get_interactions(page: int = 1, per_page: int = 20) -> dict:
    return _get("/db/interactions", page=page, per_page=per_page)


def instagram_sync() -> dict:
    return _post("/instagram/sync")
