import time
import threading
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

_manager = None


def set_manager(manager):
    global _manager
    _manager = manager


def create_app() -> FastAPI:
    app = FastAPI(title="ReelsBuilder Internal API", docs_url=None, redoc_url=None)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/status")
    def get_status():
        if _manager is None:
            return JSONResponse({"error": "manager not initialized"}, status_code=503)
        scheduler = _manager.scheduler
        jobs = []
        for job in scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger),
            })
        return {
            "scheduler_running": scheduler.running,
            "interactions_enabled": _manager.interactions_enabled,
            "jobs": jobs,
        }

    @app.get("/jobs")
    def list_jobs():
        if _manager is None:
            raise HTTPException(503, "manager not initialized")
        jobs = []
        for job in _manager.scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger),
            })
        return {"jobs": jobs}

    @app.post("/jobs/{job_id}/run")
    def run_job(job_id: str):
        if _manager is None:
            raise HTTPException(503, "manager not initialized")
        job_map = {
            "search_job":   _manager.search_download_publish,
            "interact_job": _manager.respond_interactions,
            "cleanup_job":  _manager.cleanup_storage,
        }
        fn = job_map.get(job_id)
        if fn is None:
            raise HTTPException(404, f"Unknown job: {job_id}")
        threading.Thread(target=fn, daemon=True).start()
        return {"status": "triggered", "job_id": job_id}

    @app.post("/jobs/{job_id}/toggle")
    def toggle_job(job_id: str):
        if _manager is None:
            raise HTTPException(503, "manager not initialized")
        if job_id != "interact_job":
            raise HTTPException(400, "Only interact_job can be toggled")
        scheduler = _manager.scheduler
        existing = scheduler.get_job(job_id)
        if existing:
            scheduler.remove_job(job_id)
            _manager.interactions_enabled = False
            return {"enabled": False}
        from apscheduler.triggers.cron import CronTrigger
        interact_cron = _manager.cfg.get("scheduler", "interactions_cron", fallback="*/30 * * * *")
        scheduler.add_job(
            _manager.respond_interactions,
            CronTrigger.from_crontab(interact_cron),
            id="interact_job",
        )
        _manager.interactions_enabled = True
        return {"enabled": True}

    @app.get("/settings")
    def get_settings():
        if _manager is None:
            raise HTTPException(503)
        result = {}
        for section in _manager.cfg.sections():
            result[section] = dict(_manager.cfg[section])
        return result

    @app.put("/settings")
    def update_settings(body: dict):
        if _manager is None:
            raise HTTPException(503)
        settings_path = Path(__file__).resolve().parent.parent / "settings.ini"
        cfg = _manager.cfg
        for section, values in body.items():
            if not cfg.has_section(section):
                cfg.add_section(section)
            for key, value in values.items():
                cfg.set(section, key, str(value))
        with open(settings_path, "w", encoding="utf-8") as f:
            cfg.write(f)
        try:
            from apscheduler.triggers.cron import CronTrigger
            scheduler = _manager.scheduler
            for job_id, cfg_key, section in [
                ("search_job",   "search_cron",      "scheduler"),
                ("interact_job", "interactions_cron", "scheduler"),
                ("cleanup_job",  "cleanup_cron",      "cleanup"),
            ]:
                new_cron = cfg.get(section, cfg_key, fallback=None)
                if new_cron and scheduler.get_job(job_id):
                    scheduler.reschedule_job(job_id, trigger=CronTrigger.from_crontab(new_cron))
        except Exception:
            pass
        return {"status": "saved"}

    @app.get("/db/videos")
    def get_videos(page: int = 1, per_page: int = 20):
        if _manager is None:
            raise HTTPException(503)
        offset = (page - 1) * per_page
        with _manager.db_lock:
            cur = _manager.conn.cursor()
            cur.execute("SELECT COUNT(*) FROM videos")
            total = cur.fetchone()[0]
            cur.execute(
                "SELECT video_id, title, url, downloaded_at, clips_created, instagram_media_id, last_updated "
                "FROM videos ORDER BY last_updated DESC LIMIT ? OFFSET ?",
                (per_page, offset),
            )
            rows = [dict(r) for r in cur.fetchall()]
        return {"total": total, "page": page, "per_page": per_page, "items": rows}

    @app.get("/db/clips")
    def get_clips(page: int = 1, per_page: int = 20):
        if _manager is None:
            raise HTTPException(503)
        offset = (page - 1) * per_page
        with _manager.db_lock:
            cur = _manager.conn.cursor()
            cur.execute("SELECT COUNT(*) FROM clips")
            total = cur.fetchone()[0]
            cur.execute(
                "SELECT id, video_id, reel_path, duration, published, published_at, deleted_at, created_at "
                "FROM clips ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (per_page, offset),
            )
            rows = [dict(r) for r in cur.fetchall()]
        return {"total": total, "page": page, "per_page": per_page, "items": rows}

    @app.get("/db/interactions")
    def get_interactions(page: int = 1, per_page: int = 20):
        if _manager is None:
            raise HTTPException(503)
        offset = (page - 1) * per_page
        with _manager.db_lock:
            cur = _manager.conn.cursor()
            cur.execute("SELECT COUNT(*) FROM interactions")
            total = cur.fetchone()[0]
            cur.execute(
                "SELECT id, platform, external_id, type, payload, replied_at "
                "FROM interactions ORDER BY replied_at DESC LIMIT ? OFFSET ?",
                (per_page, offset),
            )
            rows = [dict(r) for r in cur.fetchall()]
        return {"total": total, "page": page, "per_page": per_page, "items": rows}

    @app.post("/instagram/sync")
    def instagram_sync():
        if _manager is None:
            raise HTTPException(503)
        added = 0
        try:
            media = _manager.ig.list_published_media(limit=50)
            for m in media:
                media_id = m.get("id")
                if not media_id:
                    continue
                comments = _manager.ig.list_media_comments(media_id, limit=50)
                for c in comments:
                    cid = c.get("id")
                    if cid and not _manager.interaction_exists("instagram_comment", cid):
                        _manager.record_interaction("instagram_comment", cid, "synced", c.get("text", ""))
                        added += 1
        except Exception as e:
            raise HTTPException(500, str(e))
        return {"added": added}

    @app.get("/logs/stream")
    def stream_logs():
        log_path = Path(__file__).resolve().parent.parent / "logs" / "reels_manager.log"

        def generate():
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(0, 2)
                    while True:
                        line = f.readline()
                        if line:
                            yield f"data: {line.rstrip()}\n\n"
                        else:
                            time.sleep(0.5)
            except FileNotFoundError:
                yield "data: [log file not found]\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app
