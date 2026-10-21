import sys
import os
import json
import shutil
import sqlite3
import logging
import threading
import time
import configparser
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from src.YoutubeExtractorHandler import YoutubeExtractorHandler
from src.ClipBuilder import ClipBuilder, coerce_edit, probe
from src.InstagramHandler import InstagramHandler
from src import metrics as M

DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "reels_manager.db")))
LOG_PATH = Path(os.getenv("LOG_PATH", str(ROOT / "reels_manager.log")))
# settings edited from the dashboard must live on a volume, or they vanish on redeploy
SETTINGS_PATH = Path(os.getenv("SETTINGS_PATH", str(ROOT / "settings.ini")))
LOGO_DIR = ROOT / "data" / "logos"
MAX_PUBLISH_ATTEMPTS = 3

logger = logging.getLogger("reels_manager")
logger.setLevel(logging.INFO)
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
_fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
for _h in (logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()):
    _h.setFormatter(_fmt)
    logger.addHandler(_h)


def now() -> str:
    return datetime.utcnow().isoformat()


def abspath(p: str) -> Path:
    return Path(p) if os.path.isabs(p) else ROOT / p


def delete_file(p: Optional[str]):
    """Delete a media file and its folder once empty (never the reels/ root itself)."""
    if not p:
        return
    fp = abspath(p)
    try:
        fp.unlink(missing_ok=True)
        parent = fp.parent
        while parent.is_dir() and ROOT / "reels" in parent.parents and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    except Exception:
        logger.exception("Failed deleting %s", fp)


def init_db(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS videos (
            video_id TEXT PRIMARY KEY, title TEXT, url TEXT, downloaded_at TEXT, clips_created INTEGER DEFAULT 0,
            drive_file_id TEXT, drive_public_url TEXT, instagram_media_id TEXT, last_updated TEXT);
        CREATE TABLE IF NOT EXISTS clips (
            id INTEGER PRIMARY KEY AUTOINCREMENT, video_id TEXT, clip_path TEXT, reel_path TEXT, duration REAL,
            drive_file_id TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT, external_id TEXT, type TEXT, payload TEXT,
            replied_at TEXT);
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            enabled INTEGER DEFAULT 1,
            ig_business_id TEXT NOT NULL,
            page_id TEXT,
            access_token TEXT NOT NULL,
            cron TEXT DEFAULT '0 * * * *',
            queries TEXT DEFAULT '',
            caption TEXT DEFAULT '',
            reply_message TEXT DEFAULT '',
            edit TEXT DEFAULT '{}',
            created_at TEXT);
    """)
    for table, col in [
        ("clips", "deleted_at TEXT"), ("clips", "drive_deleted INTEGER DEFAULT 0"),
        ("clips", "published INTEGER DEFAULT 0"), ("clips", "published_at TEXT"),
        ("clips", "account_id INTEGER"), ("clips", "title TEXT"), ("clips", "attempts INTEGER DEFAULT 0"),
        ("clips", "error TEXT"), ("clips", "ig_media_id TEXT"),
        ("videos", "account_id INTEGER"), ("interactions", "account_id INTEGER"),
        # account health, shown in the dashboard and exported as metrics
        ("accounts", "auth_failed INTEGER DEFAULT 0"), ("accounts", "last_error TEXT"),
        ("accounts", "last_error_at TEXT"), ("accounts", "last_run_at TEXT"), ("accounts", "last_outcome TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass  # already exists
    conn.commit()


def load_settings() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    # repo defaults first, then the persisted copy on top (so new default keys still appear)
    cfg.read([ROOT / "settings.ini", SETTINGS_PATH], encoding="utf-8")
    return cfg


def is_auth_error(e: Exception) -> bool:
    """Graph API: HTTP 401, or OAuthException code 190 (expired/invalidated token)."""
    resp = getattr(e, "response", None)
    if resp is not None and resp.status_code == 401:
        return True
    text = str(e).lower()
    return "access token" in text and ("expired" in text or "invalid" in text)


def fill(template: str, **values) -> str:
    """str.format without blowing up on stray braces in user-written captions."""
    for k, v in values.items():
        template = template.replace("{" + k + "}", str(v or ""))
    return template


class ReelsManager:
    settings_path, log_path, logo_dir = SETTINGS_PATH, LOG_PATH, LOGO_DIR
    abspath = staticmethod(abspath)
    is_auth_error = staticmethod(is_auth_error)

    def __init__(self, cfg: configparser.ConfigParser):
        self.cfg = cfg
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.db_lock = threading.RLock()
        # ponytail: one global render lock — download+ffmpeg is CPU/disk bound on one box anyway
        self.render_lock = threading.Lock()
        self.run_locks: dict = {}  # account_id -> Lock, so "Run now" can't overlap a scheduled run
        init_db(self.conn)
        self.extractor = YoutubeExtractorHandler()
        self.scheduler: Optional[BackgroundScheduler] = None
        self.interactions_enabled = True
        self.seed_default_account()

    # --- db helpers ------------------------------------------------------------------
    def q(self, sql: str, args=(), one=False):
        with self.db_lock:
            rows = [dict(r) for r in self.conn.execute(sql, args).fetchall()]
        return (rows[0] if rows else None) if one else rows

    def x(self, sql: str, args=()) -> sqlite3.Cursor:
        with self.db_lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur

    def video_exists(self, video_id: str) -> bool:
        return self.q("SELECT 1 FROM videos WHERE video_id=?", (video_id,), one=True) is not None

    def interaction_exists(self, platform: str, external_id: str) -> bool:
        return self.q("SELECT 1 FROM interactions WHERE platform=? AND external_id=?",
                      (platform, external_id), one=True) is not None

    def record_interaction(self, platform, external_id, itype, payload="", account_id=None):
        self.x("INSERT INTO interactions (platform, external_id, type, payload, replied_at, account_id) "
               "VALUES (?,?,?,?,?,?)", (platform, external_id, itype, payload, now(), account_id))

    # --- accounts --------------------------------------------------------------------
    def seed_default_account(self):
        """First boot after the multi-account upgrade: turn the old env-var account into account #1."""
        if self.q("SELECT 1 FROM accounts LIMIT 1", one=True):
            return
        token, ig_id = os.getenv("LONG_TIME_ACCESS_TOKEN"), os.getenv("INSTAGRAM_BUISINESS_ACCOUNT")
        if not token and os.getenv("USER_TOKEN") and os.getenv("APP_ID") and os.getenv("APP_SECRET"):
            try:
                token = InstagramHandler.exchange_long_lived_token(
                    os.getenv("APP_ID"), os.getenv("APP_SECRET"), os.getenv("USER_TOKEN"))
            except Exception:
                logger.exception("Long-lived token exchange failed")
        if not (token and ig_id):
            logger.warning("No accounts configured — add one from the dashboard")
            return
        c = self.cfg
        acc_id = self.save_account(None, {
            "name": "default",
            "ig_business_id": ig_id,
            "page_id": os.getenv("FB_PAGE_ID", ""),
            "access_token": token,
            "cron": c.get("scheduler", "search_cron", fallback="0 * * * *"),
            "queries": "\n".join(q.strip() for q in c.get(
                "search", "queries", fallback="Supercars on the road || Exotic cars in the city || Luxury cars in action || "
                          "Sports cars on track").split("||")),
            "caption": c.get("instagram", "reels_caption", fallback=(
                "Built for the road, driven by passion. Every rev tells a story. 💨🏁 #cars #carsofinstagram "
                "#carlovers #carreels #auto #automotive #carlife #carswithoutlimits #dreamcars #luxurycars "
                "#sportscars #engine #reels #instareels #explorepage")),
            "reply_message": c.get("interactions", "reply_message",
                                   fallback="Thanks for interacting with us! Make sure to follow for more content!"),
            "edit": {"clips_count": c.getint("clips", "clips_count", fallback=10),
                     "clip_length": c.getint("clips", "min_duration", fallback=20)},
        }, schedule=False)
        for table in ("videos", "clips", "interactions"):
            self.x(f"UPDATE {table} SET account_id=? WHERE account_id IS NULL", (acc_id,))
        logger.info("Seeded account #%s from environment variables", acc_id)

    def get_account(self, account_id: int) -> Optional[dict]:
        acc = self.q("SELECT * FROM accounts WHERE id=?", (account_id,), one=True)
        if acc:
            acc["edit"] = coerce_edit(json.loads(acc["edit"] or "{}"))
            acc["queries_list"] = [q.strip() for q in (acc["queries"] or "").splitlines() if q.strip()]
        return acc

    def list_accounts(self) -> list:
        return [self.get_account(r["id"]) for r in self.q("SELECT id FROM accounts ORDER BY id")]

    def logo_path(self, account_id: int) -> Optional[str]:
        for p in (LOGO_DIR / f"{account_id}.png", ROOT / "logo" / "logo.png"):
            if p.exists():
                return str(p)
        return None

    def save_account(self, account_id: Optional[int], data: dict, schedule=True) -> int:
        """Create (account_id=None) or update. Raises ValueError on bad input."""
        current = self.get_account(account_id) if account_id else None
        if account_id and not current:
            raise ValueError("account not found")
        merged = {**(current or {}), **{k: v for k, v in data.items() if v is not None}}
        if not data.get("access_token") and current:
            merged["access_token"] = current["access_token"]  # blank field in the form = keep token
        for req in ("name", "ig_business_id", "access_token"):
            if not str(merged.get(req) or "").strip():
                raise ValueError(f"{req} is required")
        cron = (merged.get("cron") or "0 * * * *").strip()
        CronTrigger.from_crontab(cron)  # raises ValueError if invalid
        row = (
            str(merged["name"]).strip(), 1 if merged.get("enabled", 1) not in (0, "0", False, "off") else 0,
            str(merged["ig_business_id"]).strip(), str(merged.get("page_id") or "").strip(),
            str(merged["access_token"]).strip(), cron, merged.get("queries") or "",
            merged.get("caption") or "", merged.get("reply_message") or "",
            json.dumps(coerce_edit(merged.get("edit"))),
        )
        cols = "name, enabled, ig_business_id, page_id, access_token, cron, queries, caption, reply_message, edit"
        if account_id:
            self.x(f"UPDATE accounts SET {', '.join(c + '=?' for c in cols.split(', '))} WHERE id=?",
                   (*row, account_id))
            if data.get("access_token"):  # new token: give it a fresh chance
                self.x("UPDATE accounts SET auth_failed=0, last_error=NULL WHERE id=?", (account_id,))
        else:
            account_id = self.x(f"INSERT INTO accounts ({cols}, created_at) VALUES ({'?,' * 10}?)",
                                (*row, now())).lastrowid
        if schedule:
            self.schedule_account(account_id)
        return account_id

    def delete_account(self, account_id: int):
        if self.scheduler and self.scheduler.get_job(f"account_{account_id}"):
            self.scheduler.remove_job(f"account_{account_id}")
        for c in self.q("SELECT reel_path FROM clips WHERE account_id=? AND published=0 AND deleted_at IS NULL",
                        (account_id,)):
            delete_file(c["reel_path"])
        self.x("UPDATE clips SET deleted_at=? WHERE account_id=? AND deleted_at IS NULL", (now(), account_id))
        self.x("DELETE FROM accounts WHERE id=?", (account_id,))
        (LOGO_DIR / f"{account_id}.png").unlink(missing_ok=True)

    def schedule_account(self, account_id: int):
        if not self.scheduler:
            return
        job_id = f"account_{account_id}"
        acc = self.get_account(account_id)
        if not acc or not acc["enabled"]:
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
            return
        self.scheduler.add_job(self.run_account, CronTrigger.from_crontab(acc["cron"]), args=[account_id],
                               id=job_id, name=f"Publish · {acc['name']}", replace_existing=True,
                               max_instances=1, coalesce=True, misfire_grace_time=600)

    def ig(self, acc: dict) -> InstagramHandler:
        return InstagramHandler(acc["access_token"], acc["ig_business_id"], acc.get("page_id"))

    # --- pipeline --------------------------------------------------------------------
    def run_account(self, account_id: int):
        """Scheduled job: publish one queued reel; only when the queue is empty, make new ones."""
        lock = self.run_locks.setdefault(account_id, threading.Lock())
        acc = self.get_account(account_id)
        if not acc or not acc["enabled"]:
            return
        tag = f"[{acc['name']}]"
        if not lock.acquire(blocking=False):
            logger.info("%s run skipped: previous run still in progress", tag)
            M.RUNS.labels(acc["name"], "busy").inc()
            return
        outcome = "failed"
        try:
            logger.info("%s run started", tag)
            if acc["auth_failed"]:
                # don't download/render more videos into a queue that can't be published
                logger.warning("%s run skipped: Instagram access token is invalid, update it on the account page",
                               tag)
                outcome = "skipped_auth"
                return
            if self.queue_size(account_id):
                outcome = "published" if self.publish_next(acc) else "publish_failed"
                return
            for query in acc["queries_list"]:
                video = self.find_new_video(query)
                if video and self.process_video(acc, video["video_id"], title=video["title"]):
                    outcome = "published" if self.publish_next(self.get_account(account_id)) else "publish_failed"
                    return
            logger.info("%s nothing new to publish", tag)
            outcome = "idle"
        except Exception as e:
            logger.exception("%s run failed", tag)
            self.x("UPDATE accounts SET last_error=?, last_error_at=? WHERE id=?", (str(e)[:500], now(), account_id))
        finally:
            self.x("UPDATE accounts SET last_run_at=?, last_outcome=? WHERE id=?", (now(), outcome, account_id))
            M.RUNS.labels(acc["name"], outcome).inc()
            logger.info("%s run completed outcome=%s", tag, outcome)
            lock.release()

    def queue_size(self, account_id: int) -> int:
        return self.q("SELECT COUNT(*) AS n FROM clips WHERE account_id=? AND published=0 AND deleted_at IS NULL "
                      "AND attempts < ?", (account_id, MAX_PUBLISH_ATTEMPTS), one=True)["n"]

    def find_new_video(self, query: str) -> Optional[dict]:
        limit = self.cfg.getint("search", "limit", fallback=5)
        max_limit = self.cfg.getint("search", "max_limit", fallback=100)
        sort_by = self.cfg.get("search", "sort_by", fallback="relevance")
        while True:
            logger.info("Searching '%s' (limit=%s)", query, limit)
            try:
                results = self.extractor.search_videos(query, limit=limit, sort_by=sort_by)
            except Exception:
                logger.exception("Search failed for '%s'", query)
                return None
            for v in results:
                if not self.video_exists(v["video_id"]):
                    return v
            if limit >= max_limit or len(results) < limit:
                return None
            limit = min(limit * 2, max_limit)

    def process_video(self, acc: dict, video_id: str, title: str = "", force: bool = False) -> list:
        """Download + render reels into the account's queue. Returns new clip ids.

        The video row is claimed *before* downloading so two accounts never grab the same video,
        and a video that crashes the pipeline isn't retried forever.
        """
        url = f"https://www.youtube.com/watch?v={video_id}"
        claimed = self.x("INSERT OR IGNORE INTO videos (video_id, title, url, downloaded_at, account_id, "
                         "last_updated) VALUES (?,?,?,?,?,?)",
                         (video_id, title, url, now(), acc["id"], now())).rowcount
        if not claimed and not force:
            return []
        tag = f"[{acc['name']}]"
        source = ROOT / "temp" / f"{video_id}.mp4"
        out_dir = ROOT / "reels" / str(acc["id"]) / f"{video_id}_{int(time.time())}"
        clip_ids = []
        started = time.time()
        try:
            with self.render_lock:
                logger.info("%s downloading %s", tag, video_id)
                info = self.extractor.download_video(url, str(source))
                title = info["title"] or title
                logger.info("%s rendering %s (%s, heatmap=%s)", tag, video_id, acc["edit"]["mode"],
                            bool(info["heatmap"]))
                reels = ClipBuilder(acc["edit"], self.logo_path(acc["id"])).build(
                    str(source), info["heatmap"], str(out_dir), title=title)
            for reel in reels:
                clip_ids.append(self.x(
                    "INSERT INTO clips (video_id, account_id, reel_path, duration, title, created_at, published) "
                    "VALUES (?,?,?,?,?,?,0)",
                    (video_id, acc["id"], os.path.relpath(reel, ROOT), probe(reel)["duration"], title, now()),
                ).lastrowid)
            M.RENDERED.labels(acc["name"]).inc(len(clip_ids))
            M.VIDEOS.labels(acc["name"], "ok").inc()
            M.PIPELINE_SECONDS.labels(acc["name"]).observe(time.time() - started)
            logger.info("%s queued %d reel(s) from %s in %.0fs", tag, len(clip_ids), video_id, time.time() - started)
        except Exception:
            M.VIDEOS.labels(acc["name"], "failed").inc()
            logger.exception("%s failed processing %s", tag, video_id)
            shutil.rmtree(out_dir, ignore_errors=True)
        finally:
            # yt-dlp leaves <id>.fNNN.mp4(.part) fragments behind when a download dies
            for leftover in source.parent.glob(f"{video_id}.*"):
                leftover.unlink(missing_ok=True)
            self.x("UPDATE videos SET title=?, clips_created=?, last_updated=? WHERE video_id=?",
                   (title, len(clip_ids), now(), video_id))
        return clip_ids

    def publish_clip(self, acc: dict, clip: dict) -> bool:
        tag = f"[{acc['name']}]"
        path = abspath(clip["reel_path"])
        if not path.exists():
            self.x("UPDATE clips SET deleted_at=?, error='file missing' WHERE id=?", (now(), clip["id"]))
            return False
        caption = fill(acc["caption"], title=clip.get("title"), video_id=clip["video_id"])
        try:
            thumb_ms = int((clip.get("duration") or 2) * 1000 / 3)
            media_id = self.ig(acc).publish_reel(str(path), caption, thumb_offset_ms=thumb_ms)
        except Exception as e:
            msg = str(e)[:500]
            if is_auth_error(e):
                # the token is dead, not the reel: park the account instead of burning the clip's attempts
                logger.error("%s publish failed: Instagram access token invalid (%s)", tag, msg)
                self.x("UPDATE accounts SET auth_failed=1, last_error=?, last_error_at=? WHERE id=?",
                       (msg, now(), acc["id"]))
                M.PUBLISH_FAILURES.labels(acc["name"], "auth").inc()
                return False
            logger.exception("%s publish failed for clip %s", tag, clip["id"])
            self.x("UPDATE clips SET attempts=attempts+1, error=? WHERE id=?", (msg, clip["id"]))
            self.x("UPDATE accounts SET last_error=?, last_error_at=? WHERE id=?", (msg, now(), acc["id"]))
            M.PUBLISH_FAILURES.labels(acc["name"], "error").inc()
            return False
        self.x("UPDATE clips SET published=1, published_at=?, ig_media_id=?, error=NULL WHERE id=?",
               (now(), media_id, clip["id"]))
        self.x("UPDATE accounts SET auth_failed=0, last_error=NULL WHERE id=?", (acc["id"],))
        M.PUBLISHED.labels(acc["name"]).inc()
        self.x("UPDATE videos SET instagram_media_id=? WHERE video_id=?", (media_id, clip["video_id"]))
        logger.info("%s published clip %s -> media %s", tag, clip["id"], media_id)
        delete_file(clip["reel_path"])
        return True

    def publish_next(self, acc: dict) -> bool:
        """Publish the oldest queued reel for this account (one per run, to avoid flooding)."""
        for clip in self.q("SELECT * FROM clips WHERE account_id=? AND published=0 AND deleted_at IS NULL "
                           "AND attempts < ? ORDER BY id", (acc["id"], MAX_PUBLISH_ATTEMPTS)):
            if self.publish_clip(acc, clip):
                return True
            if abspath(clip["reel_path"]).exists():
                return False  # real API failure: stop, retry next run
        return False

    def delete_clip(self, clip_id: int):
        clip = self.q("SELECT * FROM clips WHERE id=?", (clip_id,), one=True)
        if clip:
            delete_file(clip["reel_path"])
            self.x("UPDATE clips SET deleted_at=? WHERE id=?", (now(), clip_id))

    # --- interactions ----------------------------------------------------------------
    def respond_interactions(self):
        for acc in self.list_accounts():
            if not acc["enabled"] or not acc["reply_message"]:
                continue
            tag = f"[{acc['name']}]"
            try:
                ig = self.ig(acc)
                if acc["page_id"]:
                    for conv in ig.list_conversations():
                        for msg in ig.get_conversation_messages(conv.get("id"), limit=20):
                            mid, sender = msg.get("id"), msg.get("from", {}).get("id")
                            if not mid or self.interaction_exists("instagram_dm", mid):
                                continue
                            text = msg.get("message") or ""
                            if sender in (acc["page_id"], acc["ig_business_id"]):
                                self.record_interaction("instagram_dm", mid, "skipped_self", text, acc["id"])
                                continue
                            try:
                                ig.dm_user(sender, acc["reply_message"])
                                self.record_interaction("instagram_dm", mid, "replied", text, acc["id"])
                            except Exception:
                                logger.exception("%s failed replying to DM %s", tag, mid)
                for m in ig.list_published_media(limit=50):
                    for c in ig.list_media_comments(m.get("id"), limit=50):
                        cid = c.get("id")
                        if not cid or self.interaction_exists("instagram_comment", cid):
                            continue
                        try:
                            ig.reply_to_comment(cid, acc["reply_message"])
                            self.record_interaction("instagram_comment", cid, "replied", c.get("text", ""), acc["id"])
                        except Exception:
                            logger.exception("%s failed replying to comment %s", tag, cid)
            except Exception:
                logger.exception("%s respond_interactions failed", tag)

    # --- cleanup ---------------------------------------------------------------------
    def cleanup_storage(self):
        """Delete local reels older than keep_days (published ones are already gone)."""
        logger.info("Starting cleanup_storage run")
        cutoff = datetime.utcnow() - timedelta(days=self.cfg.getint("cleanup", "keep_days", fallback=7))
        for clip in self.q("SELECT id, reel_path, clip_path FROM clips WHERE deleted_at IS NULL AND created_at < ?",
                           (cutoff.isoformat(),)):
            delete_file(clip["reel_path"])
            delete_file(clip["clip_path"])
            self.x("UPDATE clips SET deleted_at=? WHERE id=?", (now(), clip["id"]))
        for base in (ROOT / "temp", ROOT / "clips", ROOT / "reels"):
            # list first: delete_file prunes empty folders, which would break a live rglob walk
            for fp in list(base.rglob("*")):
                try:
                    if fp.is_file() and datetime.utcfromtimestamp(fp.stat().st_mtime) < cutoff:
                        delete_file(str(fp))
                except OSError:
                    logger.warning("cleanup: could not remove %s", fp)
        logger.info("cleanup_storage run completed")


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true", help="Run every account once and exit")
    p.add_argument("--disable-interaction", action="store_true", help="Disable responding to DMs/comments")
    args = p.parse_args()

    if not SETTINGS_PATH.exists():
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / "settings.ini", SETTINGS_PATH)
    cfg = load_settings()
    rm = ReelsManager(cfg)

    if args.once:
        for acc in rm.list_accounts():
            rm.run_account(acc["id"])
        return

    rm.scheduler = scheduler = BackgroundScheduler()
    for acc in rm.list_accounts():
        rm.schedule_account(acc["id"])
    interact_cron = cfg.get("scheduler", "interactions_cron", fallback="*/30 * * * *")
    if args.disable_interaction:
        rm.interactions_enabled = False
        logger.info("Interactions disabled by CLI flag")
    else:
        scheduler.add_job(rm.respond_interactions, CronTrigger.from_crontab(interact_cron), id="interact_job",
                          name="Respond Interactions", max_instances=1, coalesce=True)
    scheduler.add_job(rm.cleanup_storage, CronTrigger.from_crontab(
        cfg.get("cleanup", "cleanup_cron", fallback="0 0 * * *")), id="cleanup_job", name="Cleanup Storage")
    scheduler.start()

    from src.api import create_app, set_manager
    set_manager(rm)
    logger.info("Scheduler started with %d account(s); internal API on :8080", len(rm.list_accounts()))
    uvicorn.run(create_app(), host="0.0.0.0", port=8080, log_level="warning")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("Fatal error in manager")
        raise
