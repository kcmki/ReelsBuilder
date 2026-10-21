# ReelsBuilder

Finds YouTube videos, cuts their most-replayed moments into vertical reels, and publishes them to one or more Instagram accounts on a schedule. Everything is managed from the web dashboard at https://reels.mekki.tech.

## How it works

Each **account** (Accounts page) has its own Instagram token, cron schedule, YouTube search queries, caption and edit style. On every scheduled run an account:

1. publishes the oldest reel in its queue, or, if the queue is empty,
2. searches its queries for a video no account has used yet, downloads it, renders reels with its edit style, queues them, and publishes the first one.

One reel per run, so the cron sets the posting rate (`0 9,13,19 * * *` = 3 per day).

### Edit styles (rendered by ffmpeg)

- **single**: every highlight becomes its own reel; **montage**: highlights are joined with transitions (`fade`, `slideleft`, `zoomin`, …) into one reel
- **blur** (video over a blurred fill), **crop** (fill the frame) or **fit** (black bars), output 1080×1920 @ 30fps
- zoom, speed, saturation/contrast, edge trim (hides watermarks), fades, title text overlay (`{title}`), per-account logo

Highlights come from YouTube's "most replayed" heatmap (via yt-dlp), and evenly spaced picks fill in when a video has no or few peaks.

### Dashboard

- **Accounts**: add/edit/pause accounts, upload a logo, test the connection, run now, **render from a specific YouTube URL**
- **Reels**: preview queued reels in the browser, publish one immediately, or drop it
- **Jobs / Settings / Logs / Executions / Instagram**: scheduler control, global settings, live logs, history

## Layout

- `run_manager.py`: scheduler, accounts, pipeline, DB (SQLite)
- `src/ClipBuilder.py`: ffmpeg editor plus the `EDIT_FIELDS` list that drives the dashboard form
- `src/InstagramHandler.py`: Graph API (resumable reel upload)
- `src/YoutubeExtractorHandler.py`: search (scrapetube) and download (yt-dlp)
- `src/api.py`: internal API on :8080 used by the dashboard (Docker network only)
- `dashboard/`: FastAPI + Jinja + htmx web UI on :8000
- `settings.ini`: global defaults; the edited copy is persisted at `/app/data/settings.ini`

## Deploy

Pushing to `main` deploys via ZimaPaaS (`platform.yaml`, compose strategy). On first boot after the multi-account upgrade, the old `LONG_TIME_ACCESS_TOKEN` / `INSTAGRAM_BUISINESS_ACCOUNT` / `FB_PAGE_ID` env vars are turned into an account named `default`. After that, accounts are managed in the dashboard and the env vars are no longer read.

## Checks

```bash
python src/ClipBuilder.py   # window picking + real single/montage renders (needs ffmpeg)
```
