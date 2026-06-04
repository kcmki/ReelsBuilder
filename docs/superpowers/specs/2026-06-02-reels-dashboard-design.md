# ReelsBuilder Admin Dashboard — Design Spec
**Date:** 2026-06-02  
**Status:** Approved

---

## Overview

A web-based admin dashboard for the ReelsBuilder Instagram automation system. Provides real-time visibility into job execution, editable configuration, execution history, and Instagram account data. Deployed as a separate container on ZimaPaaS at `reels.mekki.tech`.

---

## Architecture

### Two-container model

| Container | Role | Port exposed |
|-----------|------|--------------|
| `reelsbuilder` | Existing automation + new internal API sidecar (port 8080) | None (internal only) |
| `reels-dashboard` | FastAPI + Jinja2 + HTMX frontend | 8000 → Traefik → `reels.mekki.tech` |

Both containers are on `platform-network` (external Docker bridge). The dashboard communicates with the reelsbuilder over `http://reelsbuilder:8080`. The internal API is never exposed outside the Docker network — no auth needed on it.

### Communication flow

```
Browser → Cloudflare → Traefik → reels-dashboard:8000
                                        ↓ (internal API calls)
                              reelsbuilder:8080 (sidecar thread)
                                        ↓
                              ReelsManager instance (shared memory)
                              SQLite DB (reelsbuilder_db volume)
                              Log file (reelsbuilder_logs volume)
```

---

## Internal API (sidecar in `run_manager.py`)

A FastAPI app started as a daemon thread at manager boot. Shares the `ReelsManager` instance directly.

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/status` | Scheduler state, next/last run per job, `interactions_enabled` flag |
| `GET` | `/jobs` | List jobs with id, next_run_time, last_run_time |
| `POST` | `/jobs/{id}/run` | Trigger job immediately in background thread |
| `POST` | `/jobs/{id}/toggle` | Enable/disable interactions job (adds/removes from scheduler) |
| `GET` | `/settings` | Read `settings.ini` as JSON |
| `PUT` | `/settings` | Write updates to `settings.ini`, hot-reload scheduler crons |
| `GET` | `/logs/stream` | SSE endpoint — tails `reels_manager.log`, streams new lines |
| `GET` | `/db/videos` | Paginated video rows |
| `GET` | `/db/clips` | Paginated clip rows with publish status |
| `GET` | `/db/interactions` | Paginated interaction rows |
| `POST` | `/instagram/sync` | Fetch published media + comments from Graph API, upsert to DB |

**Note:** DMs/conversations are excluded from Instagram sync — not available through current Graph API permissions.

---

## Dashboard App (`reels-dashboard`)

### Tech stack
- **FastAPI** — routing, session middleware
- **Jinja2** — server-side HTML templates
- **HTMX** — partial page updates, SSE, polling (no JS framework)
- **itsdangerous** — signed session cookie
- **bcrypt** — password hashing
- **httpx** — async HTTP client for internal API calls
- **SQLite** (`dashboard.db`) — users table only

### File structure

```
dashboard/
├── main.py               # FastAPI app, middleware, startup
├── auth.py               # Login/logout routes, session helpers, user CRUD
├── db.py                 # dashboard.db init, user table queries
├── api_client.py         # All calls to reelsbuilder:8080 (one function per endpoint)
├── routes/
│   ├── overview.py       # GET /
│   ├── jobs.py           # GET/POST /jobs
│   ├── settings.py       # GET/POST /settings
│   ├── logs.py           # GET /logs + SSE proxy
│   ├── executions.py     # GET /executions
│   └── instagram.py      # GET/POST /instagram
├── templates/
│   ├── base.html         # Layout: nav, auth wrapper
│   ├── login.html
│   ├── overview.html
│   ├── jobs.html
│   ├── settings.html
│   ├── logs.html
│   ├── executions.html
│   └── instagram.html
├── static/
│   └── style.css         # Minimal custom CSS (utility-first)
├── requirements.txt
├── Dockerfile
└── platform.yaml
```

Each route file is independent — adding a new page means creating one file and registering its router in `main.py`.

### Auth

- `users` table: `id`, `username`, `password_hash`, `created_at`
- Login sets a signed session cookie (`SESSION_SECRET` env var)
- All routes except `/login` check for valid session via a FastAPI dependency (`require_auth`)
- First-run: if no users exist, redirect to `/setup` to create the first admin account

### Pages

#### `/` — Overview
Cards: scheduler status, next run time per job (search / interactions / cleanup), total videos, total clips published, total interactions. HTMX auto-poll every 10s (`hx-trigger="every 10s"`).

#### `/jobs` — Job Control
Table of 3 jobs with last/next run times. "Run Now" button per job (HTMX POST, shows spinner). Enable/disable toggle for interactions job.

#### `/settings` — Configuration
Form with all `settings.ini` fields grouped by section:
- **Scheduler:** search_cron, interactions_cron, cleanup_cron
- **Search:** queries (textarea, `||`-separated), limit, max_limit, sort_by
- **Clips:** min_duration, clips_count
- **Interactions:** reply_message
- **Instagram:** reels_caption
- **Cleanup:** keep_days

Save is a PUT via HTMX — shows success/error inline without full page reload.

#### `/logs` — Live Log Stream
SSE stream proxied from `reelsbuilder:8080/logs/stream`. Dashboard SSE endpoint re-streams to browser. HTMX `hx-ext="sse"` appends new lines to a pre-element. Filter buttons (ALL / INFO / WARNING / ERROR) hide lines client-side via CSS classes.

#### `/executions` — History
Two tabs: **Videos** and **Clips**. Paginated tables. Videos table: title, downloaded_at, clips_created, instagram_media_id. Clips table: video_id, reel_path, duration, published, published_at, deleted_at. HTMX pagination (no full reload).

#### `/instagram` — Instagram Data
- **Published Reels** section: list from local DB (clips where published=1) with permalink and caption.
- **Interactions** section: paginated `interactions` table (type, platform, replied_at, payload preview).
- **Sync button**: `hx-post="/instagram/sync"` — calls internal API, refreshes both sections. Shows count of new items found.

---

## Docker & ZimaPaaS Setup

### Changes to `reelsbuilder` container
- Add `fastapi`, `uvicorn`, `httpx` to `requirements.txt`
- Start internal API as daemon thread in `main()` before scheduler starts
- Expose container port 8080 (internal only, no host binding)
- Add `platform-network` to compose

### New `reels-dashboard` container
- Separate `dashboard/` directory at repo root
- Its own `Dockerfile` (Python 3.11-slim, installs dashboard deps)
- Joined to `platform-network`
- Traefik labels for `reels.mekki.tech`
- Env vars: `SESSION_SECRET`, `INTERNAL_API_URL=http://reelsbuilder:8080`

### `platform.yaml`
```yaml
name: reels-dashboard
subdomain: reels
port: 8000
strategy: docker
health_check: /health
environment:
  - SESSION_SECRET
  - INTERNAL_API_URL
```

### Volumes
The dashboard container does **not** mount any reelsbuilder volumes. All data access goes through the internal API. The reelsbuilder container keeps its existing volume mounts (`reelsbuilder_db`, `reelsbuilder_logs`, etc.) unchanged.

---

## Key Design Decisions

1. **No auth on internal API** — it's network-isolated; adding auth would complicate the sidecar without security benefit
2. **dashboard.db separate from reelsbuilder.db** — avoids schema coupling; users are a dashboard concern
3. **SSE over WebSockets** — log streaming is read-only; SSE is simpler and works perfectly with HTMX
4. **HTMX over React** — dashboard is mostly server-rendered tables/forms; HTMX partial updates give SPA feel without a build step
5. **One router file per page** — each page is self-contained; adding/removing pages doesn't touch unrelated code
6. **`api_client.py` as the single seam** — all internal API calls go through one module; if the internal API URL or contract changes, only this file changes

---

## UI Design System

**Style:** Dark Mode (OLED) — deep black, high contrast, OLED-friendly. No light mode.

**Colors:**
```css
--color-background:  #020617;   /* near-black page bg */
--color-primary:     #0F172A;   /* card/sidebar bg */
--color-secondary:   #1E293B;   /* hover surfaces */
--color-muted:       #1A1E2F;   /* subtle fills */
--color-border:      #334155;   /* dividers */
--color-foreground:  #F8FAFC;   /* primary text */
--color-accent:      #22C55E;   /* CTAs, success, active states */
--color-destructive: #EF4444;   /* errors, destructive actions */
```

**Typography:**
- Headings / code blocks / cron values: `Fira Code` (monospace — fits the technical nature)
- Body / labels / nav: `Fira Sans`
- Import: `@import url('https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600;700&family=Fira+Sans:wght@300;400;500;600;700&display=swap');`

**Effects:** Minimal glow on key values (`text-shadow: 0 0 10px rgba(34,197,94,0.4)`), smooth hover transitions 150–300ms.

**Icon rule:** SVG only via Lucide or Heroicons. No emojis.

**Accessibility:** 4.5:1 contrast minimum, visible focus rings (2px `--color-accent`), `cursor-pointer` on all clickables, `prefers-reduced-motion` respected.

**Responsive breakpoints:** 375 / 768 / 1024 / 1440px. Desktop-first layout (sidebar nav), collapses to top nav on mobile.

---

## Out of Scope

- Instagram DM/conversation sync (Graph API doesn't support it with current permissions)
- Multi-account support
- Role-based access (single admin role for now)
- Mobile-optimized layout (desktop-first)
