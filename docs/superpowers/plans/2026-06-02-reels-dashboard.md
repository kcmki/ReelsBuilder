# ReelsBuilder Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a dark OLED admin dashboard (FastAPI + Jinja2 + HTMX) for the ReelsBuilder automation script, with an internal REST API sidecar, SQLite auth, SSE log streaming, and ZimaPaaS deployment.

**Architecture:** The existing `run_manager.py` gains a FastAPI sidecar thread on port 8080 (internal only, no host binding) exposing job control, settings, DB reads, and SSE log streaming. A separate `dashboard/` service on port 8000 authenticates users via bcrypt+itsdangerous sessions, proxies all data through the internal API, and renders server-side HTML pages with HTMX partial updates.

**Tech Stack:** FastAPI, Uvicorn, Jinja2, HTMX 1.9, itsdangerous, bcrypt, httpx, SQLite, Heroicons SVG (inline), Fira Code + Fira Sans (Google Fonts)

---

## File Map

### Modified
- `requirements.txt` — add fastapi, uvicorn[standard], httpx
- `run_manager.py` — add `rm.scheduler = scheduler` + sidecar thread in `main()`
- `docker-compose.yml` — add platform-network, named logs volume, dashboard service
- `Dockerfile` — add curl for healthcheck

### New (sidecar)
- `src/api.py` — internal FastAPI app, all 11 endpoints + `/health`

### New (dashboard)
- `dashboard/__init__.py` — empty package marker
- `dashboard/requirements.txt`
- `dashboard/db.py` — dashboard.db init, users table CRUD
- `dashboard/auth.py` — bcrypt hashing, itsdangerous session, `require_auth` dependency
- `dashboard/api_client.py` — one function per internal API endpoint
- `dashboard/routes/__init__.py` — empty
- `dashboard/routes/overview.py` — GET `/`, GET `/overview/status` (HTMX partial)
- `dashboard/routes/jobs.py` — GET `/jobs`, POST `/jobs/{id}/run`, POST `/jobs/{id}/toggle`
- `dashboard/routes/settings.py` — GET/POST `/settings`
- `dashboard/routes/logs.py` — GET `/logs`, GET `/logs/stream` (SSE proxy)
- `dashboard/routes/executions.py` — GET `/executions`
- `dashboard/routes/instagram.py` — GET `/instagram`, POST `/instagram/sync`
- `dashboard/templates/base.html`
- `dashboard/templates/login.html`
- `dashboard/templates/setup.html`
- `dashboard/templates/overview.html`
- `dashboard/templates/jobs.html`
- `dashboard/templates/settings.html`
- `dashboard/templates/logs.html`
- `dashboard/templates/executions.html`
- `dashboard/templates/instagram.html`
- `dashboard/static/style.css`
- `dashboard/Dockerfile`
- `dashboard/platform.yaml`

---

### Task 1: Add sidecar dependencies to reelsbuilder

**Files:**
- Modify: `requirements.txt`
- Modify: `Dockerfile`

- [ ] **Step 1: Append FastAPI deps to requirements.txt**

Open `requirements.txt` and add these three lines at the end:
```
fastapi
uvicorn[standard]
httpx
```

- [ ] **Step 2: Add curl to Dockerfile for healthcheck**

In `Dockerfile`, find:
```
        sqlite3 \
    && rm -rf /var/lib/apt/lists/*
```
Replace with:
```
        sqlite3 \
        curl \
    && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 3: Commit**
```bash
git add requirements.txt Dockerfile
git commit -m "feat: add fastapi/uvicorn/httpx deps and curl to reelsbuilder"
```

---

### Task 2: Create internal API — all endpoints

**Files:**
- Create: `src/api.py`

- [ ] **Step 1: Create `src/api.py`**

```python
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
            "search_job": _manager.search_download_publish,
            "interact_job": _manager.respond_interactions,
            "cleanup_job": _manager.cleanup_storage,
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
                ("search_job", "search_cron", "scheduler"),
                ("interact_job", "interactions_cron", "scheduler"),
                ("cleanup_job", "cleanup_cron", "cleanup"),
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
```

- [ ] **Step 2: Verify the file parses**
```bash
cd C:\Users\Admin\Desktop\Dev_\ReelsBuilder
py -c "from src.api import create_app; app = create_app(); print(len(app.routes), 'routes — OK')"
```
Expected output contains `routes — OK`.

- [ ] **Step 3: Commit**
```bash
git add src/api.py
git commit -m "feat: add internal API with all 12 endpoints (status, jobs, settings, db, logs SSE, instagram sync)"
```

---

### Task 3: Wire sidecar into run_manager.py

**Files:**
- Modify: `run_manager.py`

- [ ] **Step 1: Add `import uvicorn` to the top-level imports in run_manager.py**

After the existing import block (after `import threading`), add:
```python
import uvicorn
```

- [ ] **Step 2: Expose scheduler on ReelsManager and start the sidecar thread**

In `run_manager.py`, find the `main()` function. Find this line:
```python
    scheduler = BackgroundScheduler()
```
Immediately after it, insert:
```python
    rm.scheduler = scheduler  # expose to internal API
```

Then, immediately after `rm.scheduler = scheduler`, insert the sidecar block:
```python
    # start internal API sidecar (daemon thread — dies with main process)
    from src.api import create_app, set_manager
    set_manager(rm)
    _api_app = create_app()

    def _run_sidecar():
        uvicorn.run(_api_app, host="0.0.0.0", port=8080, log_level="warning")

    threading.Thread(target=_run_sidecar, daemon=True, name="internal-api").start()
    logger.info("Internal API sidecar started on :8080")
```

The relevant section of `main()` should now read:
```python
    scheduler = BackgroundScheduler()
    rm.scheduler = scheduler  # expose to internal API

    # start internal API sidecar (daemon thread — dies with main process)
    from src.api import create_app, set_manager
    set_manager(rm)
    _api_app = create_app()

    def _run_sidecar():
        uvicorn.run(_api_app, host="0.0.0.0", port=8080, log_level="warning")

    threading.Thread(target=_run_sidecar, daemon=True, name="internal-api").start()
    logger.info("Internal API sidecar started on :8080")

    search_cron = cfg.get("scheduler", "search_cron", fallback="*/15 * * * *")
    # ... rest of main() unchanged
```

- [ ] **Step 3: Verify the import chain is clean**
```bash
py -c "
import sys, os
sys.path.insert(0, '.')
# just verify imports resolve — don't actually call main()
from src.api import create_app, set_manager
print('sidecar imports OK')
"
```
Expected: `sidecar imports OK`

- [ ] **Step 4: Commit**
```bash
git add run_manager.py
git commit -m "feat: start internal API sidecar thread on port 8080 in run_manager"
```

---

### Task 4: Update docker-compose.yml for platform-network

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Replace docker-compose.yml with platform-network version**

Write `docker-compose.yml`:
```yaml
version: '3.8'
services:
  reelsbuilder:
    build: .
    container_name: reelsbuilder
    env_file:
      - .env
    environment:
      - DB_PATH=/app/db/reels_manager.db
      - LOG_PATH=/app/logs/reels_manager.log
    volumes:
      - ./clips:/app/clips
      - ./reels:/app/reels
      - ./temp:/app/temp
      - reelsbuilder_logs:/app/logs
      - reelsbuilder_data:/app/data
      - reelsbuilder_db:/app/db
    restart: unless-stopped
    networks:
      - platform-network
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  reels-dashboard:
    build:
      context: .
      dockerfile: dashboard/Dockerfile
    container_name: reels-dashboard
    env_file:
      - .env
    environment:
      - INTERNAL_API_URL=http://reelsbuilder:8080
      - DASHBOARD_DB_PATH=/app/db/dashboard.db
    volumes:
      - dashboard_db:/app/db
    restart: unless-stopped
    networks:
      - platform-network
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.reels-dashboard.rule=Host(`reels.mekki.tech`)"
      - "traefik.http.routers.reels-dashboard.entrypoints=web"
      - "traefik.http.services.reels-dashboard.loadbalancer.server.port=8000"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
    depends_on:
      - reelsbuilder

volumes:
  reelsbuilder_data:
  reelsbuilder_db:
  reelsbuilder_logs:
  dashboard_db:

networks:
  platform-network:
    external: true
```

- [ ] **Step 2: Commit**
```bash
git add docker-compose.yml
git commit -m "feat: add platform-network, dashboard service, named logs volume to compose"
```

---

### Task 5: Scaffold dashboard package

**Files:**
- Create: `dashboard/__init__.py`
- Create: `dashboard/routes/__init__.py`
- Create: `dashboard/requirements.txt`

- [ ] **Step 1: Create empty package files**

Create `dashboard/__init__.py` — empty file.

Create `dashboard/routes/__init__.py` — empty file.

- [ ] **Step 2: Create `dashboard/requirements.txt`**
```
fastapi
uvicorn[standard]
jinja2
python-multipart
itsdangerous
bcrypt
httpx
```

- [ ] **Step 3: Commit**
```bash
git add dashboard/
git commit -m "feat: scaffold dashboard package structure and requirements"
```

---

### Task 6: dashboard/db.py — user CRUD

**Files:**
- Create: `dashboard/db.py`

- [ ] **Step 1: Create `dashboard/db.py`**

```python
import sqlite3
import os
from datetime import datetime
from pathlib import Path

DB_PATH = Path(os.getenv("DASHBOARD_DB_PATH", "/app/db/dashboard.db"))


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def has_users() -> bool:
    conn = _conn()
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    return count > 0


def get_user(username: str):
    conn = _conn()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return row


def create_user(username: str, password_hash: str):
    conn = _conn()
    conn.execute(
        "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
        (username, password_hash, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()
```

- [ ] **Step 2: Smoke-test locally**
```bash
py -c "
import os; os.environ['DASHBOARD_DB_PATH'] = 'C:/Temp/test_rb_dash.db'
from dashboard.db import init_db, has_users, create_user, get_user
init_db()
print('has_users:', has_users())
create_user('admin', 'fakehash')
print('has_users after insert:', has_users())
print('get_user:', dict(get_user('admin')))
"
```
Expected: `has_users: False`, then `True`, then the user dict.

- [ ] **Step 3: Commit**
```bash
git add dashboard/db.py
git commit -m "feat: add dashboard SQLite user CRUD module"
```

---

### Task 7: dashboard/auth.py — sessions and password hashing

**Files:**
- Create: `dashboard/auth.py`

- [ ] **Step 1: Create `dashboard/auth.py`**

```python
import os
import bcrypt
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import Request, HTTPException

SESSION_SECRET = os.getenv("SESSION_SECRET", "change-me-in-production")
SESSION_COOKIE = "rb_session"
SESSION_MAX_AGE = 60 * 60 * 8  # 8 hours

_serializer = URLSafeTimedSerializer(SESSION_SECRET)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_session_token(username: str) -> str:
    return _serializer.dumps({"u": username})


def _decode_token(token: str):
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE)
        return data.get("u")
    except (BadSignature, SignatureExpired):
        return None


def get_current_user(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return _decode_token(token)


def require_auth(request: Request) -> str:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=307, headers={"Location": "/login"})
    return user
```

- [ ] **Step 2: Smoke-test**
```bash
py -c "
from dashboard.auth import hash_password, verify_password, create_session_token, _decode_token
h = hash_password('hunter2')
print('correct:', verify_password('hunter2', h))
print('wrong:  ', verify_password('nope', h))
t = create_session_token('admin')
print('round-trip:', _decode_token(t))
"
```
Expected: `correct: True`, `wrong: False`, `round-trip: admin`

- [ ] **Step 3: Commit**
```bash
git add dashboard/auth.py
git commit -m "feat: add session auth with bcrypt + itsdangerous"
```

---

### Task 8: dashboard/api_client.py — internal API calls

**Files:**
- Create: `dashboard/api_client.py`

- [ ] **Step 1: Create `dashboard/api_client.py`**

```python
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
```

- [ ] **Step 2: Commit**
```bash
git add dashboard/api_client.py
git commit -m "feat: add api_client module — single seam for all internal API calls"
```

---

### Task 9: dashboard/static/style.css — dark OLED theme

**Files:**
- Create: `dashboard/static/style.css`

- [ ] **Step 1: Create `dashboard/static/style.css`**

```css
@import url('https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600;700&family=Fira+Sans:wght@300;400;500;600;700&display=swap');

:root {
  --bg:          #020617;
  --primary:     #0F172A;
  --secondary:   #1E293B;
  --muted:       #1A1E2F;
  --border:      #334155;
  --fg:          #F8FAFC;
  --fg-muted:    #94A3B8;
  --accent:      #22C55E;
  --accent-dim:  rgba(34,197,94,0.15);
  --destructive: #EF4444;
  --warn:        #F59E0B;
  --radius:      6px;
  --mono:        'Fira Code', monospace;
  --sans:        'Fira Sans', sans-serif;
  --ease:        150ms ease;
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

html, body {
  background: var(--bg);
  color: var(--fg);
  font-family: var(--sans);
  font-size: 15px;
  line-height: 1.6;
  min-height: 100dvh;
}

a { color: var(--accent); text-decoration: none; cursor: pointer; }
a:hover { text-decoration: underline; }
a:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 2px; }

/* ── Layout ─────────────────────────────── */
.layout { display: flex; min-height: 100dvh; }

.sidebar {
  width: 220px;
  background: var(--primary);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  padding: 24px 0;
  position: sticky;
  top: 0;
  height: 100dvh;
  flex-shrink: 0;
  overflow-y: auto;
}

.sidebar-brand {
  padding: 0 20px 20px;
  font-family: var(--mono);
  font-size: 13px;
  font-weight: 700;
  color: var(--accent);
  letter-spacing: 0.08em;
  text-shadow: 0 0 10px rgba(34,197,94,0.4);
  border-bottom: 1px solid var(--border);
  margin-bottom: 8px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 20px;
  color: var(--fg-muted);
  font-size: 14px;
  cursor: pointer;
  transition: background var(--ease), color var(--ease);
  border-left: 3px solid transparent;
  text-decoration: none;
}
.nav-item:hover { background: var(--secondary); color: var(--fg); text-decoration: none; }
.nav-item.active { color: var(--accent); border-left-color: var(--accent); background: var(--accent-dim); }
.nav-item:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.nav-item svg { width: 16px; height: 16px; flex-shrink: 0; }

.sidebar-footer { margin-top: auto; padding: 16px 20px; border-top: 1px solid var(--border); }

.main { flex: 1; padding: 32px; overflow-y: auto; min-width: 0; }

.page-header { margin-bottom: 24px; }
.page-title { font-size: 20px; font-weight: 600; }
.page-subtitle { color: var(--fg-muted); font-size: 13px; margin-top: 4px; }

/* ── Cards ───────────────────────────────── */
.card {
  background: var(--primary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px;
}

.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin-bottom: 20px;
}

.stat-label { font-size: 11px; color: var(--fg-muted); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 8px; }
.stat-value { font-family: var(--mono); font-size: 26px; font-weight: 700; color: var(--accent); text-shadow: 0 0 8px rgba(34,197,94,0.3); }
.stat-sub   { font-size: 11px; color: var(--fg-muted); margin-top: 4px; }

/* ── Badges ──────────────────────────────── */
.badge {
  display: inline-flex; align-items: center;
  padding: 2px 8px; border-radius: 99px;
  font-size: 11px; font-weight: 600; font-family: var(--mono);
  text-transform: uppercase; letter-spacing: 0.04em;
}
.badge-green { background: var(--accent-dim); color: var(--accent); }
.badge-red   { background: rgba(239,68,68,0.15); color: var(--destructive); }
.badge-gray  { background: var(--muted); color: var(--fg-muted); }
.badge-warn  { background: rgba(245,158,11,0.15); color: var(--warn); }

/* ── Buttons ─────────────────────────────── */
.btn {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 8px 16px; border-radius: var(--radius);
  font-size: 13px; font-weight: 500; cursor: pointer;
  border: 1px solid transparent; transition: all var(--ease);
  font-family: var(--sans); text-decoration: none;
}
.btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.btn:disabled { opacity: 0.45; cursor: not-allowed; pointer-events: none; }
.btn-primary { background: var(--accent); color: #000; border-color: var(--accent); }
.btn-primary:hover { background: #16a34a; text-decoration: none; }
.btn-ghost { background: transparent; color: var(--fg-muted); border-color: var(--border); }
.btn-ghost:hover { background: var(--secondary); color: var(--fg); text-decoration: none; }
.btn-danger { background: transparent; color: var(--destructive); border-color: var(--destructive); }
.btn-danger:hover { background: rgba(239,68,68,0.1); text-decoration: none; }
.btn-sm { padding: 5px 10px; font-size: 12px; }

/* ── Tables ──────────────────────────────── */
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th {
  text-align: left; padding: 10px 12px;
  color: var(--fg-muted); font-weight: 500;
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em;
  border-bottom: 1px solid var(--border);
}
td {
  padding: 10px 12px; border-bottom: 1px solid rgba(51,65,85,0.5);
  font-family: var(--mono); font-size: 12px; vertical-align: middle;
}
tr:last-child td { border-bottom: none; }
tr:hover td { background: rgba(30,41,59,0.5); }

/* ── Forms ───────────────────────────────── */
.form-group { margin-bottom: 16px; }
.form-label { display: block; font-size: 13px; font-weight: 500; margin-bottom: 6px; }
.form-hint  { font-size: 11px; color: var(--fg-muted); margin-top: 4px; }
.form-section { margin-bottom: 24px; }
.form-section-title {
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.1em;
  color: var(--accent); font-weight: 600; margin-bottom: 16px;
  border-bottom: 1px solid var(--border); padding-bottom: 8px;
}

input[type="text"],
input[type="password"],
input[type="email"],
input[type="number"],
textarea,
select {
  width: 100%; padding: 9px 12px;
  background: var(--secondary); border: 1px solid var(--border);
  border-radius: var(--radius); color: var(--fg);
  font-family: var(--sans); font-size: 13px;
  transition: border-color var(--ease), box-shadow var(--ease);
}
input:focus, textarea:focus, select:focus {
  outline: none; border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(34,197,94,0.2);
}
textarea { resize: vertical; min-height: 80px; font-family: var(--mono); font-size: 12px; }

/* ── Log viewer ──────────────────────────── */
.log-viewer {
  background: #000; border: 1px solid var(--border); border-radius: var(--radius);
  padding: 16px; height: 600px; overflow-y: auto;
  font-family: var(--mono); font-size: 12px; line-height: 1.7;
}
.log-line { white-space: pre-wrap; word-break: break-all; display: block; }
.log-line.INFO     { color: #94A3B8; }
.log-line.WARNING  { color: var(--warn); }
.log-line.ERROR    { color: var(--destructive); }
.log-line.CRITICAL { color: var(--destructive); font-weight: 700; }

/* ── Alerts ──────────────────────────────── */
.alert { padding: 10px 14px; border-radius: var(--radius); font-size: 13px; margin-bottom: 12px; }
.alert-success { background: var(--accent-dim); border: 1px solid var(--accent); color: var(--accent); }
.alert-error   { background: rgba(239,68,68,0.1); border: 1px solid var(--destructive); color: var(--destructive); }

/* ── Pagination ──────────────────────────── */
.pagination { display: flex; gap: 8px; align-items: center; margin-top: 16px; font-size: 13px; }

/* ── Login ───────────────────────────────── */
.login-wrap { min-height: 100dvh; display: flex; align-items: center; justify-content: center; }
.login-card { width: 100%; max-width: 380px; }
.login-title { font-family: var(--mono); font-size: 18px; color: var(--accent); margin-bottom: 24px; text-shadow: 0 0 10px rgba(34,197,94,0.4); }

/* ── HTMX loading ────────────────────────── */
.htmx-indicator { display: none; }
.htmx-request .htmx-indicator { display: inline; }
.htmx-request.htmx-indicator  { display: inline; }

/* ── Responsive ──────────────────────────── */
@media (max-width: 768px) {
  .sidebar { width: 180px; }
  .main { padding: 16px; }
  .card-grid { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 480px) {
  .sidebar { display: none; }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { transition: none !important; animation: none !important; }
}
```

- [ ] **Step 2: Commit**
```bash
git add dashboard/static/style.css
git commit -m "feat: add dark OLED CSS theme (Fira Code/Sans, green accent, accessible)"
```

---

### Task 10: Base layout and auth templates

**Files:**
- Create: `dashboard/templates/base.html`
- Create: `dashboard/templates/login.html`
- Create: `dashboard/templates/setup.html`

- [ ] **Step 1: Create `dashboard/templates/base.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{% block title %}ReelsBuilder{% endblock %} — Dashboard</title>
  <link rel="stylesheet" href="/static/style.css">
  <script src="https://unpkg.com/htmx.org@1.9.10" defer></script>
  <script src="https://unpkg.com/htmx.org@1.9.10/dist/ext/sse.js" defer></script>
</head>
<body>
<div class="layout">
  <nav class="sidebar">
    <div class="sidebar-brand">ReelsBuilder</div>

    <a href="/" class="nav-item {% if active == 'overview' %}active{% endif %}">
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V8.625zM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V4.125z"/></svg>
      Overview
    </a>
    <a href="/jobs" class="nav-item {% if active == 'jobs' %}active{% endif %}">
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M5.25 5.653c0-.856.917-1.398 1.667-.986l11.54 6.347a1.125 1.125 0 010 1.972l-11.54 6.347c-.75.412-1.667-.13-1.667-.986V5.653z"/></svg>
      Jobs
    </a>
    <a href="/settings" class="nav-item {% if active == 'settings' %}active{% endif %}">
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.324.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 011.37.49l1.296 2.247a1.125 1.125 0 01-.26 1.431l-1.003.827c-.293.24-.438.613-.431.992a6.759 6.759 0 010 .255c-.007.378.138.75.43.99l1.005.828c.424.35.534.954.26 1.43l-1.298 2.247a1.125 1.125 0 01-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.57 6.57 0 01-.22.128c-.331.183-.581.495-.644.869l-.213 1.28c-.09.543-.56.941-1.11.941h-2.594c-.55 0-1.02-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 01-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 01-1.369-.49l-1.297-2.247a1.125 1.125 0 01.26-1.431l1.004-.827c.292-.24.437-.613.43-.992a6.932 6.932 0 010-.255c.007-.378-.138-.75-.43-.99l-1.004-.828a1.125 1.125 0 01-.26-1.43l1.297-2.247a1.125 1.125 0 011.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.087.22-.128.332-.183.582-.495.644-.869l.214-1.281z"/><path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
      Settings
    </a>
    <a href="/logs" class="nav-item {% if active == 'logs' %}active{% endif %}">
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M3.75 12h16.5m-16.5 3.75h16.5M3.75 19.5h16.5M5.625 4.5h12.75a1.875 1.875 0 010 3.75H5.625a1.875 1.875 0 010-3.75z"/></svg>
      Logs
    </a>
    <a href="/executions" class="nav-item {% if active == 'executions' %}active{% endif %}">
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M20.25 6.375c0 2.278-3.694 4.125-8.25 4.125S3.75 8.653 3.75 6.375m16.5 0c0-2.278-3.694-4.125-8.25-4.125S3.75 4.097 3.75 6.375m16.5 0v11.25c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125V6.375m16.5 0v3.75m-16.5-3.75v3.75m16.5 0v3.75C20.25 16.153 16.556 18 12 18s-8.25-1.847-8.25-4.125v-3.75m16.5 0c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125"/></svg>
      Executions
    </a>
    <a href="/instagram" class="nav-item {% if active == 'instagram' %}active{% endif %}">
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M7.5 8.25h9m-9 3H12m-9.75 1.51c0 1.6 1.123 2.994 2.707 3.227 1.129.166 2.27.293 3.423.379.35.026.67.21.865.501L12 21l2.755-4.133a1.14 1.14 0 01.865-.501 48.172 48.172 0 003.423-.379c1.584-.233 2.707-1.626 2.707-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0012 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018z"/></svg>
      Instagram
    </a>

    <div class="sidebar-footer">
      <a href="/logout" class="btn btn-ghost btn-sm" style="width:100%;justify-content:center">Sign out</a>
    </div>
  </nav>

  <main class="main" id="main-content">
    {% block content %}{% endblock %}
  </main>
</div>
</body>
</html>
```

- [ ] **Step 2: Create `dashboard/templates/login.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Login — ReelsBuilder</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
<div class="login-wrap">
  <div class="login-card card">
    <div class="login-title">ReelsBuilder</div>
    {% if error %}<div class="alert alert-error">{{ error }}</div>{% endif %}
    <form method="post" action="/login">
      <div class="form-group">
        <label class="form-label" for="username">Username</label>
        <input type="text" id="username" name="username" required autofocus autocomplete="username">
      </div>
      <div class="form-group">
        <label class="form-label" for="password">Password</label>
        <input type="password" id="password" name="password" required autocomplete="current-password">
      </div>
      <button type="submit" class="btn btn-primary" style="width:100%;justify-content:center;margin-top:8px">Sign in</button>
    </form>
  </div>
</div>
</body>
</html>
```

- [ ] **Step 3: Create `dashboard/templates/setup.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>First-run Setup — ReelsBuilder</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
<div class="login-wrap">
  <div class="login-card card">
    <div class="login-title">First-run setup</div>
    <p style="color:var(--fg-muted);font-size:13px;margin-bottom:20px">Create your admin account.</p>
    {% if error %}<div class="alert alert-error">{{ error }}</div>{% endif %}
    <form method="post" action="/setup">
      <div class="form-group">
        <label class="form-label" for="username">Username</label>
        <input type="text" id="username" name="username" required autofocus autocomplete="username">
      </div>
      <div class="form-group">
        <label class="form-label" for="password">Password</label>
        <input type="password" id="password" name="password" required autocomplete="new-password">
        <div class="form-hint">Minimum 8 characters</div>
      </div>
      <button type="submit" class="btn btn-primary" style="width:100%;justify-content:center;margin-top:8px">Create account</button>
    </form>
  </div>
</div>
</body>
</html>
```

- [ ] **Step 4: Commit**
```bash
git add dashboard/templates/
git commit -m "feat: add base layout, login, and first-run setup templates"
```

---

### Task 11: dashboard/main.py — app factory + auth routes

**Files:**
- Create: `dashboard/main.py`

- [ ] **Step 1: Create `dashboard/main.py`**

```python
import os
from pathlib import Path
from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dashboard.db import init_db, has_users, get_user, create_user
from dashboard.auth import (
    verify_password, hash_password, create_session_token,
    get_current_user, require_auth, SESSION_COOKIE, SESSION_MAX_AGE,
)
from dashboard.routes import overview, jobs, settings, logs, executions, instagram

BASE_DIR = Path(__file__).resolve().parent


def create_app() -> FastAPI:
    app = FastAPI(title="ReelsBuilder Dashboard", docs_url=None, redoc_url=None)

    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

    @app.on_event("startup")
    def on_startup():
        init_db()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    # First-run redirect middleware
    @app.middleware("http")
    async def first_run_redirect(request: Request, call_next):
        exempt = {"/setup", "/login", "/health"}
        if request.url.path not in exempt and not request.url.path.startswith("/static"):
            if not has_users():
                return RedirectResponse("/setup", 302)
        return await call_next(request)

    # ── Auth routes ──────────────────────────────────────────────────────
    @app.get("/login", response_class=HTMLResponse)
    def login_get(request: Request):
        if get_current_user(request):
            return RedirectResponse("/", 302)
        return templates.TemplateResponse("login.html", {"request": request, "error": None})

    @app.post("/login")
    def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
        user = get_user(username)
        if not user or not verify_password(password, user["password_hash"]):
            return templates.TemplateResponse(
                "login.html",
                {"request": request, "error": "Invalid username or password"},
                status_code=401,
            )
        token = create_session_token(username)
        resp = RedirectResponse("/", 302)
        resp.set_cookie(SESSION_COOKIE, token, max_age=SESSION_MAX_AGE, httponly=True, samesite="lax")
        return resp

    @app.get("/logout")
    def logout():
        resp = RedirectResponse("/login", 302)
        resp.delete_cookie(SESSION_COOKIE)
        return resp

    @app.get("/setup", response_class=HTMLResponse)
    def setup_get(request: Request):
        if has_users():
            return RedirectResponse("/login", 302)
        return templates.TemplateResponse("setup.html", {"request": request, "error": None})

    @app.post("/setup")
    def setup_post(request: Request, username: str = Form(...), password: str = Form(...)):
        if has_users():
            return RedirectResponse("/login", 302)
        if len(password) < 8:
            return templates.TemplateResponse(
                "setup.html",
                {"request": request, "error": "Password must be at least 8 characters"},
                status_code=400,
            )
        create_user(username, hash_password(password))
        token = create_session_token(username)
        resp = RedirectResponse("/", 302)
        resp.set_cookie(SESSION_COOKIE, token, max_age=SESSION_MAX_AGE, httponly=True, samesite="lax")
        return resp

    # ── Page routers ─────────────────────────────────────────────────────
    app.include_router(overview.router)
    app.include_router(jobs.router)
    app.include_router(settings.router)
    app.include_router(logs.router)
    app.include_router(executions.router)
    app.include_router(instagram.router)

    return app


app = create_app()
```

- [ ] **Step 2: Verify the app builds without import errors**
```bash
py -c "
import sys; sys.path.insert(0, '.')
import os; os.environ['DASHBOARD_DB_PATH'] = 'C:/Temp/test_rb2.db'
from dashboard.main import app
print('routes:', len(app.routes))
print('app created OK')
"
```
Expected: route count printed, `app created OK`.

- [ ] **Step 3: Commit**
```bash
git add dashboard/main.py
git commit -m "feat: add dashboard main app with auth routes and first-run redirect"
```

---

### Task 12: Overview route + template

**Files:**
- Create: `dashboard/routes/overview.py`
- Create: `dashboard/templates/overview.html`

- [ ] **Step 1: Create `dashboard/routes/overview.py`**

```python
import httpx
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
        "status": status, "total_videos": tv, "total_clips": tc, "total_interactions": ti,
        "partial": False,
    })


@router.get("/overview/refresh", response_class=HTMLResponse)
def overview_refresh(request: Request, user: str = Depends(require_auth)):
    status, tv, tc, ti = _fetch_overview()
    return templates.TemplateResponse("overview.html", {
        "request": request, "active": "overview", "user": user,
        "status": status, "total_videos": tv, "total_clips": tc, "total_interactions": ti,
        "partial": True,
    })
```

- [ ] **Step 2: Create `dashboard/templates/overview.html`**

```html
{% if not partial %}{% extends "base.html" %}{% block title %}Overview{% endblock %}{% block content %}{% endif %}

<div class="page-header">
  <div class="page-title">Overview</div>
  <div class="page-subtitle">Scheduler and automation status</div>
</div>

<div id="overview-content"
  hx-get="/overview/refresh"
  hx-trigger="every 10s"
  hx-swap="outerHTML">

  <div class="card-grid">
    <div class="card">
      <div class="stat-label">Scheduler</div>
      <div style="margin-top:8px">
        {% if status.get('scheduler_running') %}
          <span class="badge badge-green">Running</span>
        {% else %}
          <span class="badge badge-red">Stopped</span>
        {% endif %}
      </div>
    </div>
    <div class="card">
      <div class="stat-label">Interactions</div>
      <div style="margin-top:8px">
        {% if status.get('interactions_enabled') %}
          <span class="badge badge-green">Enabled</span>
        {% else %}
          <span class="badge badge-gray">Disabled</span>
        {% endif %}
      </div>
    </div>
    <div class="card">
      <div class="stat-label">Videos Processed</div>
      <div class="stat-value">{{ total_videos }}</div>
    </div>
    <div class="card">
      <div class="stat-label">Clips Published</div>
      <div class="stat-value">{{ total_clips }}</div>
    </div>
    <div class="card">
      <div class="stat-label">Interactions Logged</div>
      <div class="stat-value">{{ total_interactions }}</div>
    </div>
  </div>

  {% if status.get('jobs') %}
  <div class="card">
    <div class="form-section-title">Upcoming Runs</div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Job</th><th>Next Run</th><th>Trigger</th></tr></thead>
        <tbody>
          {% for job in status['jobs'] %}
          <tr>
            <td>{{ job.id }}</td>
            <td>{{ job.next_run_time or '—' }}</td>
            <td style="color:var(--fg-muted)">{{ job.trigger or '—' }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
  {% else %}
  <div class="card" style="color:var(--fg-muted);font-size:13px">
    No job data — is the reelsbuilder container running?
  </div>
  {% endif %}

</div>

{% if not partial %}{% endblock %}{% endif %}
```

- [ ] **Step 3: Commit**
```bash
git add dashboard/routes/overview.py dashboard/templates/overview.html
git commit -m "feat: add overview page with 10s HTMX auto-refresh"
```

---

### Task 13: Jobs route + template

**Files:**
- Create: `dashboard/routes/jobs.py`
- Create: `dashboard/templates/jobs.html`

- [ ] **Step 1: Create `dashboard/routes/jobs.py`**

```python
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
    return templates.TemplateResponse("jobs.html", {
        "request": request, "active": "jobs", "user": user,
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
```

- [ ] **Step 2: Create `dashboard/templates/jobs.html`**

```html
{% extends "base.html" %}
{% block title %}Jobs{% endblock %}
{% block content %}

<div class="page-header">
  <div class="page-title">Jobs</div>
  <div class="page-subtitle">Trigger and control scheduled automation jobs</div>
</div>

<div id="job-feedback" style="min-height:36px"></div>

<div class="card">
  <div class="table-wrap">
    <table>
      <thead>
        <tr><th>Job</th><th>Next Run</th><th>Trigger</th><th style="width:200px">Actions</th></tr>
      </thead>
      <tbody>
        {% for job in jobs %}
        <tr>
          <td>
            {{ job.label }}
            {% if job.id == 'interact_job' %}
              <span id="interact-badge" class="badge {% if interactions_enabled %}badge-green{% else %}badge-gray{% endif %}" style="margin-left:8px">
                {% if interactions_enabled %}Enabled{% else %}Disabled{% endif %}
              </span>
            {% endif %}
          </td>
          <td>{{ job.next_run_time or '—' }}</td>
          <td style="color:var(--fg-muted);max-width:200px;overflow:hidden;text-overflow:ellipsis">{{ job.trigger or '—' }}</td>
          <td>
            <div style="display:flex;gap:8px;align-items:center">
              <button class="btn btn-primary btn-sm"
                hx-post="/jobs/{{ job.id }}/run"
                hx-target="#job-feedback"
                hx-swap="innerHTML"
                hx-indicator="#spin-{{ job.id }}">
                Run Now <span id="spin-{{ job.id }}" class="htmx-indicator">…</span>
              </button>
              {% if job.id == 'interact_job' %}
              <button class="btn btn-ghost btn-sm"
                hx-post="/jobs/interact_job/toggle"
                hx-target="#interact-badge"
                hx-swap="outerHTML">
                Toggle
              </button>
              {% endif %}
            </div>
          </td>
        </tr>
        {% endfor %}
        {% if not jobs %}
        <tr><td colspan="4" style="text-align:center;color:var(--fg-muted);padding:24px">No jobs — is the reelsbuilder running?</td></tr>
        {% endif %}
      </tbody>
    </table>
  </div>
</div>

{% endblock %}
```

- [ ] **Step 3: Commit**
```bash
git add dashboard/routes/jobs.py dashboard/templates/jobs.html
git commit -m "feat: add jobs page with run-now and toggle controls"
```

---

### Task 14: Settings route + template

**Files:**
- Create: `dashboard/routes/settings.py`
- Create: `dashboard/templates/settings.html`

- [ ] **Step 1: Create `dashboard/routes/settings.py`**

```python
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from dashboard.auth import require_auth
import dashboard.api_client as api

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
router = APIRouter()

_TEXTAREA_KEYS = {"reply_message", "reels_caption", "queries"}


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, user: str = Depends(require_auth)):
    try:
        cfg = api.get_settings()
    except Exception:
        cfg = {}
    return templates.TemplateResponse("settings.html", {
        "request": request, "active": "settings", "user": user,
        "cfg": cfg, "saved": False, "error": None, "textarea_keys": _TEXTAREA_KEYS,
    })


@router.post("/settings", response_class=HTMLResponse)
async def settings_save(request: Request, user: str = Depends(require_auth)):
    form = await request.form()
    body: dict = {}
    for key, value in form.items():
        if "__" in key:
            section, field = key.split("__", 1)
            body.setdefault(section, {})[field] = value
    try:
        api.update_settings(body)
        cfg = api.get_settings()
        return templates.TemplateResponse("settings.html", {
            "request": request, "active": "settings", "user": user,
            "cfg": cfg, "saved": True, "error": None, "textarea_keys": _TEXTAREA_KEYS,
        })
    except Exception as e:
        try:
            cfg = api.get_settings()
        except Exception:
            cfg = {}
        return templates.TemplateResponse("settings.html", {
            "request": request, "active": "settings", "user": user,
            "cfg": cfg, "saved": False, "error": str(e), "textarea_keys": _TEXTAREA_KEYS,
        }, status_code=500)
```

- [ ] **Step 2: Create `dashboard/templates/settings.html`**

```html
{% extends "base.html" %}
{% block title %}Settings{% endblock %}
{% block content %}

<div class="page-header">
  <div class="page-title">Settings</div>
  <div class="page-subtitle">Edits are written to settings.ini and hot-reloaded into the scheduler</div>
</div>

{% if saved %}<div class="alert alert-success">Settings saved and reloaded.</div>{% endif %}
{% if error %}<div class="alert alert-error">{{ error }}</div>{% endif %}

<form method="post" action="/settings">
  {% for section, values in cfg.items() %}
  <div class="card form-section" style="margin-bottom:16px">
    <div class="form-section-title">{{ section }}</div>
    {% for key, value in values.items() %}
    <div class="form-group">
      <label class="form-label" for="{{ section }}__{{ key }}">{{ key }}</label>
      {% if key in textarea_keys %}
      <textarea id="{{ section }}__{{ key }}" name="{{ section }}__{{ key }}">{{ value }}</textarea>
      {% else %}
      <input type="text" id="{{ section }}__{{ key }}" name="{{ section }}__{{ key }}" value="{{ value }}">
      {% endif %}
    </div>
    {% endfor %}
  </div>
  {% endfor %}
  {% if not cfg %}
  <div class="card" style="color:var(--fg-muted);font-size:13px">
    Cannot reach the reelsbuilder API. Is the container running?
  </div>
  {% else %}
  <button type="submit" class="btn btn-primary">Save Settings</button>
  {% endif %}
</form>

{% endblock %}
```

- [ ] **Step 3: Commit**
```bash
git add dashboard/routes/settings.py dashboard/templates/settings.html
git commit -m "feat: add settings page with hot-reload on save"
```

---

### Task 15: Logs route + template (SSE proxy)

**Files:**
- Create: `dashboard/routes/logs.py`
- Create: `dashboard/templates/logs.html`

- [ ] **Step 1: Create `dashboard/routes/logs.py`**

```python
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
```

- [ ] **Step 2: Create `dashboard/templates/logs.html`**

```html
{% extends "base.html" %}
{% block title %}Logs{% endblock %}
{% block content %}

<div class="page-header" style="display:flex;justify-content:space-between;align-items:flex-start;gap:16px">
  <div>
    <div class="page-title">Live Logs</div>
    <div class="page-subtitle">Real-time stream from reels_manager.log</div>
  </div>
  <div style="display:flex;gap:8px;flex-wrap:wrap">
    <button class="btn btn-ghost btn-sm" onclick="filterLogs('ALL')">All</button>
    <button class="btn btn-ghost btn-sm" onclick="filterLogs('INFO')">Info</button>
    <button class="btn btn-ghost btn-sm" onclick="filterLogs('WARNING')">Warn</button>
    <button class="btn btn-ghost btn-sm" onclick="filterLogs('ERROR')">Error</button>
    <button class="btn btn-ghost btn-sm" onclick="document.getElementById('log-viewer').innerHTML=''">Clear</button>
  </div>
</div>

<div id="log-viewer" class="log-viewer"
  hx-ext="sse"
  sse-connect="/logs/stream"
  sse-swap="message"
  hx-swap="beforeend"
  hx-on::sse-message="onLogLine(event)">
</div>

<script>
function onLogLine(event) {
  const viewer = document.getElementById('log-viewer');
  const last = viewer.lastElementChild;
  if (!last) return;
  const t = last.textContent;
  let level = 'INFO';
  if (t.includes('CRITICAL')) level = 'CRITICAL';
  else if (t.includes(' ERROR ') || t.includes('ERROR:')) level = 'ERROR';
  else if (t.includes(' WARNING ') || t.includes('WARNING:')) level = 'WARNING';
  last.classList.add('log-line', level);
  last.dataset.level = level;
  if (_activeFilter !== 'ALL' && level !== _activeFilter) last.style.display = 'none';
  viewer.scrollTop = viewer.scrollHeight;
}

let _activeFilter = 'ALL';
function filterLogs(level) {
  _activeFilter = level;
  document.querySelectorAll('.log-line').forEach(el => {
    el.style.display = (level === 'ALL' || el.dataset.level === level) ? '' : 'none';
  });
}
</script>

{% endblock %}
```

- [ ] **Step 3: Commit**
```bash
git add dashboard/routes/logs.py dashboard/templates/logs.html
git commit -m "feat: add live log viewer with SSE proxy and level filter"
```

---

### Task 16: Executions route + template

**Files:**
- Create: `dashboard/routes/executions.py`
- Create: `dashboard/templates/executions.html`

- [ ] **Step 1: Create `dashboard/routes/executions.py`**

```python
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
```

- [ ] **Step 2: Create `dashboard/templates/executions.html`**

```html
{% extends "base.html" %}
{% block title %}Executions{% endblock %}
{% block content %}

<div class="page-header">
  <div class="page-title">Executions</div>
  <div class="page-subtitle">Videos processed and clips published</div>
</div>

<div style="display:flex;gap:8px;margin-bottom:16px">
  <a href="/executions?tab=videos&page=1" class="btn {% if tab == 'videos' %}btn-primary{% else %}btn-ghost{% endif %} btn-sm">Videos</a>
  <a href="/executions?tab=clips&page=1"  class="btn {% if tab == 'clips'  %}btn-primary{% else %}btn-ghost{% endif %} btn-sm">Clips</a>
</div>

{% if tab == 'videos' %}
<div class="card">
  <div class="table-wrap">
    <table>
      <thead><tr><th>Video ID</th><th>Title</th><th>Downloaded</th><th>Clips</th><th>IG Media ID</th></tr></thead>
      <tbody>
        {% for v in videos.items %}
        <tr>
          <td><a href="https://youtube.com/watch?v={{ v.video_id }}" target="_blank" rel="noopener">{{ v.video_id }}</a></td>
          <td style="max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ v.title or '—' }}</td>
          <td>{{ v.downloaded_at[:16] if v.downloaded_at else '—' }}</td>
          <td>{{ v.clips_created or 0 }}</td>
          <td>{{ v.instagram_media_id or '—' }}</td>
        </tr>
        {% endfor %}
        {% if not videos.items %}
        <tr><td colspan="5" style="text-align:center;color:var(--fg-muted);padding:24px">No videos yet</td></tr>
        {% endif %}
      </tbody>
    </table>
  </div>
  <div class="pagination">
    {% if page > 1 %}<a href="/executions?tab=videos&page={{ page-1 }}" class="btn btn-ghost btn-sm">← Prev</a>{% endif %}
    <span style="color:var(--fg-muted);font-size:12px">Page {{ page }} · {{ videos.total }} total</span>
    {% if page * 20 < videos.total %}<a href="/executions?tab=videos&page={{ page+1 }}" class="btn btn-ghost btn-sm">Next →</a>{% endif %}
  </div>
</div>
{% endif %}

{% if tab == 'clips' %}
<div class="card">
  <div class="table-wrap">
    <table>
      <thead><tr><th>ID</th><th>Video ID</th><th>Duration</th><th>Status</th><th>Published</th><th>Created</th></tr></thead>
      <tbody>
        {% for c in clips.items %}
        <tr>
          <td>{{ c.id }}</td>
          <td>{{ c.video_id }}</td>
          <td>{{ "%.1f"|format(c.duration) if c.duration else '—' }}s</td>
          <td>
            {% if c.deleted_at %}
              <span class="badge badge-gray">Deleted</span>
            {% elif c.published %}
              <span class="badge badge-green">Published</span>
            {% else %}
              <span class="badge badge-warn">Pending</span>
            {% endif %}
          </td>
          <td>{{ c.published_at[:16] if c.published_at else '—' }}</td>
          <td>{{ c.created_at[:16] if c.created_at else '—' }}</td>
        </tr>
        {% endfor %}
        {% if not clips.items %}
        <tr><td colspan="6" style="text-align:center;color:var(--fg-muted);padding:24px">No clips yet</td></tr>
        {% endif %}
      </tbody>
    </table>
  </div>
  <div class="pagination">
    {% if page > 1 %}<a href="/executions?tab=clips&page={{ page-1 }}" class="btn btn-ghost btn-sm">← Prev</a>{% endif %}
    <span style="color:var(--fg-muted);font-size:12px">Page {{ page }} · {{ clips.total }} total</span>
    {% if page * 20 < clips.total %}<a href="/executions?tab=clips&page={{ page+1 }}" class="btn btn-ghost btn-sm">Next →</a>{% endif %}
  </div>
</div>
{% endif %}

{% endblock %}
```

- [ ] **Step 3: Commit**
```bash
git add dashboard/routes/executions.py dashboard/templates/executions.html
git commit -m "feat: add executions page with videos/clips tabs and pagination"
```

---

### Task 17: Instagram route + template

**Files:**
- Create: `dashboard/routes/instagram.py`
- Create: `dashboard/templates/instagram.html`

- [ ] **Step 1: Create `dashboard/routes/instagram.py`**

```python
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

    return templates.TemplateResponse("instagram.html", {
        "request": request, "active": "instagram", "user": user,
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
```

- [ ] **Step 2: Create `dashboard/templates/instagram.html`**

```html
{% extends "base.html" %}
{% block title %}Instagram{% endblock %}
{% block content %}

<div class="page-header" style="display:flex;justify-content:space-between;align-items:flex-start;gap:16px">
  <div>
    <div class="page-title">Instagram</div>
    <div class="page-subtitle">Published reels and interaction history</div>
  </div>
  <button class="btn btn-ghost btn-sm"
    hx-post="/instagram/sync"
    hx-target="#sync-result"
    hx-swap="innerHTML"
    hx-indicator="#sync-spin">
    Sync from Instagram <span id="sync-spin" class="htmx-indicator">…</span>
  </button>
</div>

<div id="sync-result" style="min-height:12px"></div>

<div class="card" style="margin-bottom:16px">
  <div class="form-section-title">Published Reels (local DB)</div>
  <div class="table-wrap">
    <table>
      <thead><tr><th>Video ID</th><th>Published At</th><th>Duration</th></tr></thead>
      <tbody>
        {% for c in published %}
        <tr>
          <td>{{ c.video_id }}</td>
          <td>{{ c.published_at[:16] if c.published_at else '—' }}</td>
          <td>{{ "%.1f"|format(c.duration) if c.duration else '—' }}s</td>
        </tr>
        {% endfor %}
        {% if not published %}
        <tr><td colspan="3" style="text-align:center;color:var(--fg-muted);padding:24px">No published reels yet</td></tr>
        {% endif %}
      </tbody>
    </table>
  </div>
</div>

<div class="card">
  <div class="form-section-title">Interaction History</div>
  <div class="table-wrap">
    <table>
      <thead><tr><th>Type</th><th>Platform</th><th>External ID</th><th>Replied At</th><th>Payload</th></tr></thead>
      <tbody>
        {% for i in interactions.items %}
        <tr>
          <td><span class="badge {% if i.type == 'replied' %}badge-green{% else %}badge-gray{% endif %}">{{ i.type }}</span></td>
          <td>{{ i.platform }}</td>
          <td>{{ i.external_id }}</td>
          <td>{{ i.replied_at[:16] if i.replied_at else '—' }}</td>
          <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ i.payload or '—' }}</td>
        </tr>
        {% endfor %}
        {% if not interactions.items %}
        <tr><td colspan="5" style="text-align:center;color:var(--fg-muted);padding:24px">No interactions recorded yet</td></tr>
        {% endif %}
      </tbody>
    </table>
  </div>
  <div class="pagination">
    {% if page > 1 %}<a href="/instagram?page={{ page-1 }}" class="btn btn-ghost btn-sm">← Prev</a>{% endif %}
    <span style="color:var(--fg-muted);font-size:12px">Page {{ page }} · {{ interactions.total }} total</span>
    {% if page * 20 < interactions.total %}<a href="/instagram?page={{ page+1 }}" class="btn btn-ghost btn-sm">Next →</a>{% endif %}
  </div>
</div>

{% endblock %}
```

- [ ] **Step 3: Commit**
```bash
git add dashboard/routes/instagram.py dashboard/templates/instagram.html
git commit -m "feat: add instagram page with sync button and interaction history"
```

---

### Task 18: Dashboard Dockerfile + platform.yaml

**Files:**
- Create: `dashboard/Dockerfile`
- Create: `dashboard/platform.yaml`

- [ ] **Step 1: Create `dashboard/Dockerfile`**

```dockerfile
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY dashboard/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY dashboard/ /app/dashboard/

EXPOSE 8000
CMD ["uvicorn", "dashboard.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Note: build context is the repo root so `dashboard/` is accessible.

- [ ] **Step 2: Create `dashboard/platform.yaml`**

```yaml
name: reels-dashboard
subdomain: reels
port: 8000
strategy: docker
health_check: /health
environment:
  - SESSION_SECRET
  - INTERNAL_API_URL
  - DASHBOARD_DB_PATH
```

- [ ] **Step 3: Commit**
```bash
git add dashboard/Dockerfile dashboard/platform.yaml
git commit -m "feat: add dashboard Dockerfile and platform.yaml for ZimaPaaS"
```

---

### Task 19: Final verification

- [ ] **Step 1: Verify all files exist**

Run:
```bash
py -c "
import os
files = [
    'src/api.py',
    'dashboard/__init__.py',
    'dashboard/requirements.txt',
    'dashboard/db.py',
    'dashboard/auth.py',
    'dashboard/api_client.py',
    'dashboard/main.py',
    'dashboard/routes/__init__.py',
    'dashboard/routes/overview.py',
    'dashboard/routes/jobs.py',
    'dashboard/routes/settings.py',
    'dashboard/routes/logs.py',
    'dashboard/routes/executions.py',
    'dashboard/routes/instagram.py',
    'dashboard/templates/base.html',
    'dashboard/templates/login.html',
    'dashboard/templates/setup.html',
    'dashboard/templates/overview.html',
    'dashboard/templates/jobs.html',
    'dashboard/templates/settings.html',
    'dashboard/templates/logs.html',
    'dashboard/templates/executions.html',
    'dashboard/templates/instagram.html',
    'dashboard/static/style.css',
    'dashboard/Dockerfile',
    'dashboard/platform.yaml',
]
missing = [f for f in files if not os.path.exists(f)]
if missing:
    print('MISSING:', missing)
else:
    print('All files present — OK')
"
```
Expected: `All files present — OK`

- [ ] **Step 2: Verify dashboard app creates without import errors**
```bash
py -c "
import sys, os
sys.path.insert(0, '.')
os.environ['DASHBOARD_DB_PATH'] = 'C:/Temp/verify_rb.db'
from dashboard.main import app
routes = [r.path for r in app.routes if hasattr(r, 'path')]
print('Routes:', routes)
print('OK')
"
```
Expected: all route paths listed, `OK` at the end.

- [ ] **Step 3: Verify internal API creates**
```bash
py -c "
from src.api import create_app
app = create_app()
paths = [r.path for r in app.routes if hasattr(r, 'path')]
print('Internal API paths:', paths)
print('OK')
"
```
Expected: 12 paths listed including `/health`, `/status`, `/jobs`, `/logs/stream`, etc.

- [ ] **Step 4: Final commit**
```bash
git add -A
git commit -m "feat: complete ReelsBuilder admin dashboard

Internal API sidecar on :8080, dashboard on :8000 at reels.mekki.tech.
Dark OLED theme, Fira Code/Sans, 6 pages, SSE logs, SQLite auth."
```

---

## Deployment

After merging:

1. **Register env vars** on ZimaPaaS dashboard for `reels-dashboard`:
   - `SESSION_SECRET` — random 32+ char string
   - `INTERNAL_API_URL` — `http://reelsbuilder:8080`
   - `DASHBOARD_DB_PATH` — `/app/db/dashboard.db`

2. **Rebuild and redeploy** both containers:
   ```bash
   docker compose build && docker compose up -d
   ```

3. **First visit** to `https://reels.mekki.tech` — redirected to `/setup` to create admin account.
