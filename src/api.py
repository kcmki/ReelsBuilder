"""Internal API for the dashboard (only reachable on the Docker network, never routed by Traefik)."""
import threading
import time
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse

from src.ClipBuilder import EDIT_FIELDS

_manager = None
IMAGE_MAGIC = (b"\x89PNG", b"\xff\xd8\xff", b"RIFF")  # png, jpeg, webp


def set_manager(manager):
    global _manager
    _manager = manager


def _m():
    if _manager is None:
        raise HTTPException(503, "manager not initialized")
    return _manager


def _bg(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


def _public(acc: dict) -> dict:
    """Account without its token (the dashboard only ever needs a hint)."""
    out = {k: v for k, v in acc.items() if k != "access_token"}
    out["token_hint"] = "…" + acc["access_token"][-4:]
    out["has_logo"] = _manager.logo_path(acc["id"]) is not None
    stats = _manager.q(
        "SELECT SUM(published=1) AS published, SUM(published=0 AND deleted_at IS NULL) AS queued, "
        "MAX(published_at) AS last_published FROM clips WHERE account_id=?", (acc["id"],), one=True)
    out.update({k: v or 0 for k, v in stats.items()})
    job = _manager.scheduler.get_job(f"account_{acc['id']}") if _manager.scheduler else None
    out["next_run_time"] = job.next_run_time.isoformat() if job and job.next_run_time else None
    return out


def _account(account_id: int) -> dict:
    acc = _m().get_account(account_id)
    if not acc:
        raise HTTPException(404, "account not found")
    return acc


def _jobs():
    return [{"id": j.id, "name": j.name, "trigger": str(j.trigger),
             "next_run_time": j.next_run_time.isoformat() if j.next_run_time else None}
            for j in _m().scheduler.get_jobs()]


def _page(table_sql: str, cols: str, order: str, page: int, per_page: int, where="", args=()):
    m = _m()
    total = m.q(f"SELECT COUNT(*) AS n FROM {table_sql} {where}", args, one=True)["n"]
    items = m.q(f"SELECT {cols} FROM {table_sql} {where} ORDER BY {order} LIMIT ? OFFSET ?",
                (*args, per_page, (page - 1) * per_page))
    return {"total": total, "page": page, "per_page": per_page, "items": items}


def create_app() -> FastAPI:
    app = FastAPI(title="ReelsBuilder Internal API", docs_url=None, redoc_url=None)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    # --- jobs ------------------------------------------------------------------------
    @app.get("/status")
    def get_status():
        m = _m()
        return {"scheduler_running": m.scheduler.running, "interactions_enabled": m.interactions_enabled,
                "jobs": _jobs(), "accounts": [_public(a) for a in m.list_accounts()]}

    @app.get("/jobs")
    def list_jobs():
        return {"jobs": _jobs()}

    @app.post("/jobs/{job_id}/run")
    def run_job(job_id: str):
        m = _m()
        if job_id.startswith("account_"):
            _bg(m.run_account, _account(int(job_id.split("_", 1)[1]))["id"])
        elif job_id == "interact_job":
            _bg(m.respond_interactions)
        elif job_id == "cleanup_job":
            _bg(m.cleanup_storage)
        else:
            raise HTTPException(404, f"Unknown job: {job_id}")
        return {"status": "triggered", "job_id": job_id}

    @app.post("/jobs/{job_id}/toggle")
    def toggle_job(job_id: str):
        m = _m()
        if job_id != "interact_job":
            raise HTTPException(400, "Only interact_job can be toggled")
        if m.scheduler.get_job(job_id):
            m.scheduler.remove_job(job_id)
            m.interactions_enabled = False
        else:
            from apscheduler.triggers.cron import CronTrigger
            cron = m.cfg.get("scheduler", "interactions_cron", fallback="*/30 * * * *")
            m.scheduler.add_job(m.respond_interactions, CronTrigger.from_crontab(cron), id=job_id,
                                name="Respond Interactions", max_instances=1, coalesce=True)
            m.interactions_enabled = True
        return {"enabled": m.interactions_enabled}

    # --- global settings -------------------------------------------------------------
    @app.get("/settings")
    def get_settings():
        cfg = _m().cfg
        return {s: dict(cfg[s]) for s in cfg.sections()}

    @app.put("/settings")
    def update_settings(body: dict):
        from apscheduler.triggers.cron import CronTrigger
        m = _m()
        for section, values in body.items():
            if not m.cfg.has_section(section):
                m.cfg.add_section(section)
            for key, value in values.items():
                m.cfg.set(section, key, str(value))
        for job_id, section, key in [("interact_job", "scheduler", "interactions_cron"),
                                     ("cleanup_job", "cleanup", "cleanup_cron")]:
            cron = m.cfg.get(section, key, fallback=None)
            try:
                trigger = CronTrigger.from_crontab(cron) if cron else None
            except ValueError as e:
                raise HTTPException(400, f"{section}.{key}: {e}")
            if trigger and m.scheduler.get_job(job_id):
                m.scheduler.reschedule_job(job_id, trigger=trigger)
        with open(m.settings_path, "w", encoding="utf-8") as f:
            m.cfg.write(f)
        return {"status": "saved"}

    # --- accounts --------------------------------------------------------------------
    @app.get("/edit-fields")
    def edit_fields():
        return [{"key": k, "label": l, "type": t, "default": d,
                 "options": o if isinstance(o, list) else None, "hint": o if isinstance(o, str) else ""}
                for k, l, t, d, o in EDIT_FIELDS]

    @app.get("/accounts")
    def list_accounts():
        return [_public(a) for a in _m().list_accounts()]

    @app.post("/accounts")
    def create_account(body: dict):
        try:
            return _public(_account(_m().save_account(None, body)))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get("/accounts/{account_id}")
    def get_account(account_id: int):
        return _public(_account(account_id))

    @app.put("/accounts/{account_id}")
    def update_account(account_id: int, body: dict):
        _account(account_id)
        try:
            return _public(_account(_m().save_account(account_id, body)))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.delete("/accounts/{account_id}")
    def delete_account(account_id: int):
        _account(account_id)
        _m().delete_account(account_id)
        return {"status": "deleted"}

    @app.put("/accounts/{account_id}/logo")
    async def upload_logo(account_id: int, request: Request):
        _account(account_id)
        data = await request.body()
        if not data:  # empty body = remove logo
            (_manager.logo_dir / f"{account_id}.png").unlink(missing_ok=True)
            return {"status": "removed"}
        if len(data) > 5_000_000 or not data.startswith(IMAGE_MAGIC):
            raise HTTPException(400, "Logo must be a PNG, JPEG or WebP under 5 MB")
        _manager.logo_dir.mkdir(parents=True, exist_ok=True)
        (_manager.logo_dir / f"{account_id}.png").write_bytes(data)  # ffmpeg sniffs the real format
        return {"status": "saved"}

    @app.post("/accounts/{account_id}/test")
    def test_account(account_id: int):
        try:
            return _m().ig(_account(account_id)).get_instagram_account()
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.post("/accounts/{account_id}/process")
    def process_url(account_id: int, body: dict):
        """Render reels from a specific YouTube URL into this account's queue (no auto-publish)."""
        m = _m()
        acc = _account(account_id)
        vid = m.extractor.video_id(body.get("url", ""))
        if not vid:
            raise HTTPException(400, "Not a YouTube video URL")
        _bg(lambda: m.process_video(acc, vid, force=True))
        return {"status": "processing", "video_id": vid}

    # --- data ------------------------------------------------------------------------
    @app.get("/db/videos")
    def get_videos(page: int = 1, per_page: int = 20):
        return _page("videos v LEFT JOIN accounts a ON a.id = v.account_id",
                     "v.video_id, v.title, v.url, v.downloaded_at, v.clips_created, v.instagram_media_id, "
                     "v.last_updated, a.name AS account", "v.last_updated DESC", page, per_page)

    @app.get("/db/clips")
    def get_clips(page: int = 1, per_page: int = 20, account_id: int = 0, status: str = ""):
        conds, args = [], []
        if account_id:
            conds.append("c.account_id=?")
            args.append(account_id)
        conds += {"queued": ["c.published=0", "c.deleted_at IS NULL"], "published": ["c.published=1"],
                  "deleted": ["c.published=0", "c.deleted_at IS NOT NULL"]}.get(status, [])
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        return _page("clips c LEFT JOIN accounts a ON a.id = c.account_id",
                     "c.id, c.video_id, c.title, c.reel_path, c.duration, c.published, c.published_at, "
                     "c.deleted_at, c.created_at, c.attempts, c.error, c.ig_media_id, c.account_id, "
                     "a.name AS account", "c.id DESC", page, per_page, where, tuple(args))

    @app.get("/db/interactions")
    def get_interactions(page: int = 1, per_page: int = 20):
        return _page("interactions i LEFT JOIN accounts a ON a.id = i.account_id",
                     "i.id, i.platform, i.external_id, i.type, i.payload, i.replied_at, a.name AS account",
                     "i.replied_at DESC", page, per_page)

    @app.get("/clips/{clip_id}/file")
    def clip_file(clip_id: int):
        clip = _m().q("SELECT reel_path FROM clips WHERE id=? AND deleted_at IS NULL AND published=0",
                      (clip_id,), one=True)
        path = _manager.abspath(clip["reel_path"]) if clip else None
        if not path or not path.exists():
            raise HTTPException(404, "file not available")
        return FileResponse(path, media_type="video/mp4")

    @app.post("/clips/{clip_id}/publish")
    def publish_clip(clip_id: int):
        m = _m()
        clip = m.q("SELECT * FROM clips WHERE id=? AND published=0 AND deleted_at IS NULL", (clip_id,), one=True)
        if not clip:
            raise HTTPException(404, "clip not in queue")
        acc = _account(clip["account_id"])
        m.x("UPDATE clips SET attempts=0 WHERE id=?", (clip_id,))
        _bg(m.publish_clip, acc, clip)
        return {"status": "publishing"}

    @app.delete("/clips/{clip_id}")
    def delete_clip(clip_id: int):
        _m().delete_clip(clip_id)
        return {"status": "deleted"}

    @app.post("/instagram/sync")
    def instagram_sync():
        m = _m()
        added = 0
        for acc in m.list_accounts():
            try:
                ig = m.ig(acc)
                for media in ig.list_published_media(limit=50):
                    for c in ig.list_media_comments(media["id"], limit=50):
                        if c.get("id") and not m.interaction_exists("instagram_comment", c["id"]):
                            m.record_interaction("instagram_comment", c["id"], "synced", c.get("text", ""), acc["id"])
                            added += 1
            except Exception as e:
                raise HTTPException(500, f"{acc['name']}: {e}")
        return {"added": added}

    @app.get("/logs/stream")
    def stream_logs():

        def generate():
            try:
                with open(_m().log_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(0, 2)
                    while True:
                        line = f.readline()
                        if line:
                            yield f"data: {line.rstrip()}\n\n"
                        else:
                            time.sleep(0.5)
            except FileNotFoundError:
                yield "data: [log file not found]\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app
