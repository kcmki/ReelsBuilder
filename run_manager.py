import sys
import os
import sqlite3
import logging
import traceback
from pathlib import Path
from datetime import datetime
import json
from datetime import timedelta
import configparser
from typing import List
import time
import threading
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from moviepy import VideoFileClip
# ensure src is importable
ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from src.YoutubeExtractorHandler import YoutubeExtractorHandler
from src.ClipBuilder import ClipBuilder
from src.DriveHandler import GoogleDriveManager
from src.InstagramHandler import InstagramHandler
from src.SupabaseS3Handler import SupabaseS3Handler

DB_PATH = ROOT / "reels_manager.db"
LOG_PATH = ROOT / "reels_manager.log"

logger = logging.getLogger("reels_manager")
logger.setLevel(logging.INFO)
handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(handler)
logger.addHandler(logging.StreamHandler())


def init_db(conn: sqlite3.Connection):
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS videos (
            video_id TEXT PRIMARY KEY,
            title TEXT,
            url TEXT,
            downloaded_at TEXT,
            clips_created INTEGER DEFAULT 0,
            drive_file_id TEXT,
            drive_public_url TEXT,
            instagram_media_id TEXT,
            last_updated TEXT
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS clips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id TEXT,
            clip_path TEXT,
            reel_path TEXT,
            duration REAL,
            drive_file_id TEXT,
            created_at TEXT
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT,
            external_id TEXT,
            type TEXT,
            payload TEXT,
            replied_at TEXT
        )
        """
    )

    conn.commit()
    # add soft-delete columns if they don't exist (safe on SQLite)
    try:
        c.execute("ALTER TABLE clips ADD COLUMN deleted_at TEXT")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE clips ADD COLUMN drive_deleted INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE clips ADD COLUMN published INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE clips ADD COLUMN published_at TEXT")
    except Exception:
        pass
    conn.commit()


def load_settings(path: Path) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    # explicit encoding
    with open(path, encoding="utf-8") as f:
        cfg.read_file(f)
    return cfg


class ReelsManager:
    def __init__(self, cfg: configparser.ConfigParser):
        self.cfg = cfg
        # allow using the connection from APScheduler worker threads
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # lock to protect concurrent DB access
        self.db_lock = threading.RLock()
        init_db(self.conn)

        self.extractor = YoutubeExtractorHandler()
        self.clip_builder = ClipBuilder()
        self.ig = InstagramHandler()

        # settings
        self.queries = [q.strip() for q in cfg.get("search", "queries").split("||") if q.strip()]
        self.search_limit = cfg.getint("search", "limit", fallback=10)
        self.max_limit = cfg.getint("search", "max_limit", fallback=100)
        self.min_clip_duration = cfg.getint("clips", "min_duration", fallback=20)
        self.reply_message = cfg.get("interactions", "reply_message", fallback="Thanks for reaching out! We will get back to you.")
        # instagram caption template for reels (can use {video_id} and {title})
        self.reels_caption = cfg.get("instagram", "reels_caption", fallback="Auto-upload {video_id}")
        # allow disabling interactions (DMs/comments) when desired via CLI
        self.interactions_enabled = True

    # --- helper DB methods ---
    def video_exists(self, video_id: str) -> bool:
        with self.db_lock:
            cur = self.conn.cursor()
            cur.execute("SELECT 1 FROM videos WHERE video_id=?", (video_id,))
            return cur.fetchone() is not None

    def mark_video(self, video_id: str, **fields):
        now = datetime.utcnow().isoformat()
        with self.db_lock:
            cur = self.conn.cursor()

        # sanitize fields: JSON-serialize dicts/lists to avoid sqlite binding errors
        safe_fields = {}
        for k, v in fields.items():
            if k == "last_updated":
                # we'll set last_updated ourselves
                continue
            if isinstance(v, (dict, list)):
                try:
                    safe_fields[k] = json.dumps(v, ensure_ascii=False)
                except Exception:
                    safe_fields[k] = str(v)
            else:
                safe_fields[k] = v

            if self.video_exists(video_id):
                if safe_fields:
                    sets = ",".join([f"{k}=?" for k in safe_fields.keys()])
                    values = list(safe_fields.values()) + [now, video_id]
                    cur.execute(f"UPDATE videos SET {sets}, last_updated=? WHERE video_id=?", values)
                else:
                    cur.execute("UPDATE videos SET last_updated=? WHERE video_id=?", (now, video_id))
            else:
                cur.execute(
                    "INSERT INTO videos (video_id, title, url, downloaded_at, clips_created, drive_file_id, drive_public_url, instagram_media_id, last_updated) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        video_id,
                        safe_fields.get("title"),
                        safe_fields.get("url"),
                        safe_fields.get("downloaded_at"),
                        safe_fields.get("clips_created", 0),
                        safe_fields.get("drive_file_id"),
                        safe_fields.get("drive_public_url"),
                        safe_fields.get("instagram_media_id"),
                        now,
                    ),
                )
            self.conn.commit()

    def interaction_exists(self, platform: str, external_id: str) -> bool:
        with self.db_lock:
            cur = self.conn.cursor()
            cur.execute("SELECT 1 FROM interactions WHERE platform=? AND external_id=?", (platform, external_id))
            return cur.fetchone() is not None

    def record_interaction(self, platform: str, external_id: str, itype: str, payload: str = ""):
        with self.db_lock:
            cur = self.conn.cursor()
            cur.execute(
                "INSERT INTO interactions (platform, external_id, type, payload, replied_at) VALUES (?,?,?,?,?)",
                (platform, external_id, itype, payload, datetime.utcnow().isoformat()),
            )
            self.conn.commit()

    # --- main processes ---
    def search_download_publish(self):
        logger.info("Starting search_download_publish run")
        # If there are pending local/unpublished reels, publish one and skip searching
        pending = self.publish_pending_reels()
        if pending:
            logger.info("Processed a pending reel; skipping new search/download this run")
            return
        # track uploads this run to avoid flooding (only one reel upload per run)
        uploads_done = 0
        for query in self.queries:
            # start from configured base limit and increase (doubling) until we find a new video
            base_limit = self.search_limit
            max_limit = self.max_limit
            limit = base_limit
            results = []

            while True:
                logger.info(f"Searching '{query}' (limit={limit})")
                try:
                    results = self.extractor.search_videos(query=query, limit=limit, sort_by=self.cfg.get("search", "sort_by", fallback="popular"))
                except Exception:
                    logger.exception("Search failed")
                    results = []
                    break

                # determine if any returned video is new (not in DB)
                any_new = False
                for v in results:
                    vid = None
                    if isinstance(v, dict):
                        vid = v.get("videoId") or v.get("id") or v.get("video_id")
                    else:
                        s = str(v)
                        if "watch?v=" in s:
                            vid = s.split("watch?v=")[-1].split("&")[0]
                    if vid and not self.video_exists(vid):
                        any_new = True
                        break

                # if we found any new video or we've reached max_limit, stop increasing
                if any_new or limit >= max_limit:
                    break

                # otherwise increase limit and retry (double, capped at max_limit)
                next_limit = min(max_limit, limit * 2)
                if next_limit == limit:
                    break
                logger.info("No new videos for query '%s'. Increasing limit %s->%s", query, limit, next_limit)
                limit = next_limit

            new_found = 0
            for video in results:
                # try to extract a video id/url robustly
                video_id = None
                url = None
                if isinstance(video, dict):
                    video_id = video.get("videoId") or video.get("id") or video.get("video_id")
                    url = video.get("url") or video.get("watchUrl")
                else:
                    # some libraries return strings
                    url = str(video)

                if not video_id and url:
                    # try to parse id from url
                    if "watch?v=" in url:
                        video_id = url.split("watch?v=")[-1].split("&")[0]

                if not video_id:
                    logger.warning("Skipping result with no video id")
                    continue

                if self.video_exists(video_id):
                    continue

                new_found += 1
                try:
                    logger.info(f"Processing new video {video_id}")
                    temp_dir = ROOT / "temp"
                    temp_dir.mkdir(exist_ok=True)
                    temp_video_path = temp_dir / f"{video_id}.mp4"

                    # download
                    dl_path = self.extractor.download_video(url or f"https://www.youtube.com/watch?v={video_id}", str(temp_video_path))
                    logger.info(f"Downloaded to {dl_path}")

                    # extract points
                    try:
                        points = self.extractor.get_points(url or f"https://www.youtube.com/watch?v={video_id}", top_x=self.cfg.getint("clips", "clips_count", fallback=5))
                    except Exception:
                        logger.exception("get_points_concatenated failed")
                        raise Exception("Failed extracting points")
                    # build reels
                    cb = ClipBuilder(temp_video_path=str(dl_path))
                    clips_dir = ROOT / "clips" / video_id
                    reels_dir = ROOT / "reels" / video_id
                    clips_dir.mkdir(parents=True, exist_ok=True)
                    reels_dir.mkdir(parents=True, exist_ok=True)

                    cb.build_reels_from_points(points, clips_dir=str(clips_dir), reels_dir=str(reels_dir))

                    # collect reels and upload each to drive and make public; record each clip in DB
                    reels = sorted(reels_dir.glob("*.mp4"))
                    clips_added = 0
                    stop_all = False
                    for reel in reels:
                        if uploads_done >= 1:
                            # do not upload more than one reel per run; record as not published
                            try:
                                original_name = reel.name
                                if original_name.startswith("reels_"):
                                    orig_clip_name = original_name.replace("reels_", "", 1)
                                else:
                                    orig_clip_name = original_name

                                clip_path = str(clips_dir / orig_clip_name)
                                reel_path = str(reel)

                                duration = None
                                try:
                                    with VideoFileClip(reel_path) as vfc:
                                        duration = float(vfc.duration)
                                except Exception:
                                    duration = None

                                with self.db_lock:
                                    cur = self.conn.cursor()
                                    cur.execute(
                                        "INSERT INTO clips (video_id, clip_path, reel_path, duration, drive_file_id, created_at, published, published_at) VALUES (?,?,?,?,?,?,?,?)",
                                        (video_id, clip_path, reel_path, duration, None, datetime.utcnow().isoformat(), 0, None),
                                    )
                                    self.conn.commit()
                                clips_added += 1
                                logger.info(f"Recorded clip (unpublished) in DB: {reel_path}")
                            except Exception:
                                logger.exception("Failed recording (unpublished) clip metadata for %s", reel)

                            continue

                        try:
                            # Direct resumable upload to Meta (rupload) flow
                            reel_path = str(reel)
                            caption = self.reels_caption.format(video_id=video_id, title=(video.get("title") if isinstance(video, dict) else ""))
                            # Step 1: create resumable media container
                            try:
                                
                                video = VideoFileClip(reel_path)
                                duration = int(video.duration)
                                thumb_offset = max(1, duration // 2)
                                resp = self.ig.create_resumable_media_container(media_type="REELS", caption=caption, thumb_offset=thumb_offset)
                                container_id = resp.get("id")
                                upload_uri = resp.get("uri") or resp.get("uri") or resp.get("upload_url")
                                if not container_id or not upload_uri:
                                    raise RuntimeError(f"Invalid resumable container response: {resp}")
                            except Exception:
                                logger.exception("Failed creating resumable container for %s", reel_path)
                                raise

                            # Step 2: upload to rupload
                            try:
                                # compute thumb_offset
                                try:
                                    with VideoFileClip(reel_path) as vfc:
                                        thumb_offset = int(vfc.duration // 2)
                                except Exception:
                                    thumb_offset = None

                                # upload local file
                                self.ig.upload_resumable_video(upload_uri, local_path=reel_path)
                            except Exception:
                                logger.exception("Failed uploading to rupload for %s", reel_path)
                                raise

                            # Step 3: poll container status then publish
                            try:
                                for i in range(60):
                                    st = self.ig.get_container_video_status(container_id)
                                    video_status = st.get("video_status") or {}
                                    uploading = video_status.get("uploading_phase", {}).get("status")
                                    processing = video_status.get("processing_phase", {}).get("status")
                                    if uploading == "complete" and processing in ("complete", "succeeded", "done"):
                                        break
                                    if uploading == "error" or processing == "error":
                                        raise RuntimeError(f"Upload/processing error: {st}")
                                    logger.info("Container %s status uploading=%s processing=%s", container_id, uploading, processing)
                                    time.sleep(5)

                                # publish
                                self.ig.publish_media(container_id)
                                logger.info(f"Published to Instagram: creation_id={container_id}")
                            except Exception:
                                logger.exception("Failed publishing container %s", container_id)
                                raise

                            # record clip/reel in DB (published)
                            try:
                                original_name = reel.name
                                if original_name.startswith("reels_"):
                                    orig_clip_name = original_name.replace("reels_", "", 1)
                                else:
                                    orig_clip_name = original_name

                                clip_path = str(clips_dir / orig_clip_name)
                                reel_path = str(reel)

                                # try to get duration using moviepy if available
                                duration = None
                                try:
                                    with VideoFileClip(reel_path) as vfc:
                                        duration = float(vfc.duration)
                                except Exception:
                                    duration = None

                                with self.db_lock:
                                    cur = self.conn.cursor()
                                    cur.execute(
                                        "INSERT INTO clips (video_id, clip_path, reel_path, duration, drive_file_id, created_at, published, published_at) VALUES (?,?,?,?,?,?,?,?)",
                                        (video_id, clip_path, reel_path, duration, None, datetime.utcnow().isoformat(), 1, datetime.utcnow().isoformat()),
                                    )
                                    self.conn.commit()
                                clips_added += 1
                                logger.info(f"Recorded clip in DB: {reel_path}")
                            except Exception:
                                logger.exception("Failed recording clip metadata for %s", reel)
                                
                        except Exception:
                            logger.exception(f"Failed upload/publish for {reel}")

                        # we uploaded/published one reel this run; stop further processing to avoid flooding
                        uploads_done += 1
                        stop_all = True
                        break

                    if stop_all:
                        # break out to stop processing more reels/videos this run
                        break

                    # update video record with clips count and last metadata
                    try:
                        self.mark_video(
                            video_id,
                            title=video.get("title") if isinstance(video, dict) else None,
                            url=url,
                            downloaded_at=datetime.utcnow().isoformat(),
                            clips_created=clips_added,
                            last_updated=datetime.utcnow().isoformat(),
                        )
                    except Exception:
                        logger.exception("Failed updating video record for %s", video_id)

                except Exception:
                    logger.exception(f"Failed processing video {video_id}")

            # if no new results, increase limit next run up to max
            # if we already uploaded a reel this run, stop processing further queries/videos
            if uploads_done >= 1:
                logger.info("Uploaded one reel this run; stopping further processing of queries")
                break

            if new_found == 0:
                new_limit = min(self.search_limit * 2, self.max_limit)
                if new_limit > self.search_limit:
                    logger.info(f"No new videos for query '{query}'. Increasing limit {self.search_limit}->{new_limit}")
                    self.search_limit = new_limit

        logger.info("search_download_publish run completed")

    def respond_interactions(self):
        logger.info("Starting respond_interactions run")
        try:
            # DMs / Conversations
            convs = self.ig.list_conversations()
            for conv in convs:
                conv_id = conv.get("id")
                messages = self.ig.get_conversation_messages(conv_id, limit=20)
                for msg in messages:
                    mid = msg.get("id")
                    sender = msg.get("from", {}).get("id")
                    text = msg.get("message") or msg.get("text") or ""
                    if not mid or self.interaction_exists("instagram_dm", mid):
                        continue
                    # avoid replying to ourselves
                    if sender == str(self.ig.page_id):
                        self.record_interaction("instagram_dm", mid, "skipped_self", text)
                        continue

                    # reply
                    try:
                        self.ig.dm_user(sender, self.reply_message)
                        self.record_interaction("instagram_dm", mid, "replied", text)
                        logger.info(f"Replied to DM {mid}")
                    except Exception:
                        logger.exception(f"Failed replying to DM {mid}")

            # Comments on published media
            media = self.ig.list_published_media(limit=50)
            for m in media:
                media_id = m.get("id")
                comments = self.ig.list_media_comments(media_id, limit=50)
                for c in comments:
                    cid = c.get("id")
                    if not cid or self.interaction_exists("instagram_comment", cid):
                        continue
                    try:
                        self.ig.reply_to_comment(cid, self.reply_message)
                        self.record_interaction("instagram_comment", cid, "replied", c.get("text", ""))
                        logger.info(f"Replied to comment {cid}")
                    except Exception:
                        logger.exception(f"Failed replying to comment {cid}")

        except Exception:
            logger.exception("respond_interactions failed")

        logger.info("respond_interactions run completed")

    def publish_pending_reels(self) -> int:
        """Find local reels (under reels/) that are not marked published and publish them.
        Returns the number of reels processed.
        """
        with self.db_lock:
            cur = self.conn.cursor()
            # find clips rows that are not published and not soft-deleted
            cur.execute("SELECT id, video_id, reel_path FROM clips WHERE published=0 AND (deleted_at IS NULL OR deleted_at='')")
            rows = cur.fetchall()

        processed = 0

        # If DB has none, scan reels/ folder for any mp4s not recorded as published
        if not rows:
            reels_root = ROOT / "reels"
            if reels_root.exists():
                for video_dir in reels_root.iterdir():
                    if not video_dir.is_dir():
                        continue
                    for fp in video_dir.glob("*.mp4"):
                        pstr = str(fp)
                        # check if there's a clip row for this path and published=1
                        with self.db_lock:
                            cur = self.conn.cursor()
                            cur.execute("SELECT id, published FROM clips WHERE reel_path=?", (pstr,))
                            r = cur.fetchone()
                        if r is None:
                            rows.append((None, video_dir.name, pstr))
                        else:
                            if r[1] == 0:
                                rows.append((r[0], video_dir.name, pstr))

        if not rows:
            return 0

        # group by video_id
        groups = {}
        for rid, vid, path in rows:
            groups.setdefault(vid, []).append((rid, path))

        for vid, items in groups.items():
            logger.info(f"Publishing pending reels for video {vid} ({len(items)} items)")
            for rid, reel_path in items:
                try:
                    # create container
                    caption = self.reels_caption.format(video_id=vid, title="")
                    video = VideoFileClip(reel_path)
                    thumb_offset = int(video.duration // 2)
                    resp = self.ig.create_resumable_media_container(media_type="REELS", caption=caption,thumb_offset=thumb_offset)
                    container_id = resp.get("id")
                    upload_uri = resp.get("uri") or resp.get("upload_url")
                    if not container_id or not upload_uri:
                        logger.error("Invalid resumable response for %s: %s", reel_path, resp)
                        continue

                    # upload
                    try:
                        self.ig.upload_resumable_video(upload_uri, local_path=reel_path)
                    except Exception:
                        logger.exception("Upload failed for %s", reel_path)
                        continue

                    # poll and publish
                    for i in range(60):
                        st = self.ig.get_container_video_status(container_id)
                        vs = st.get("video_status", {})
                        up = vs.get("uploading_phase", {}).get("status")
                        pr = vs.get("processing_phase", {}).get("status")
                        if up == "complete" and pr in ("complete", "succeeded", "done"):
                            break
                        if up == "error" or pr == "error":
                            logger.error("Upload/processing error for %s: %s", reel_path, st)
                            break
                        time.sleep(5)

                    try:
                        self.ig.publish_media(container_id)
                        logger.info("Published pending reel %s -> container %s", reel_path, container_id)
                    except Exception:
                        logger.exception("Failed publishing container %s", container_id)
                        continue

                    # mark DB row published or insert if missing
                    now = datetime.utcnow().isoformat()
                    if rid:
                        try:
                                    with self.db_lock:
                                        cur.execute("UPDATE clips SET published=1, published_at=? WHERE id=?", (now, rid))
                                        self.conn.commit()
                        except Exception:
                            logger.exception("Failed marking clip id %s as published", rid)
                    else:
                        try:
                            # insert record
                            with self.db_lock:
                                cur.execute(
                                    "INSERT INTO clips (video_id, clip_path, reel_path, duration, drive_file_id, created_at, published, published_at) VALUES (?,?,?,?,?,?,?,?)",
                                    (vid, None, reel_path, None, None, now, 1, now),
                                )
                                self.conn.commit()
                        except Exception:
                            logger.exception("Failed inserting clip row for %s", reel_path)

                    processed += 1
                    # only publish one reel per run to avoid flooding
                    return processed
                except Exception:
                    logger.exception("Failed processing pending reel %s", reel_path)

        return processed

    def cleanup_storage(self):
        """Delete Drive files and local clips older than retention period."""
        logger.info("Starting cleanup_storage run")
        try:
            keep_days = self.cfg.getint("cleanup", "keep_days", fallback=7)
            cutoff = datetime.utcnow() - timedelta(days=keep_days)

            with self.db_lock:
                cur = self.conn.cursor()
                cur.execute("SELECT id, reel_path, clip_path, drive_file_id, created_at FROM clips")
                rows = cur.fetchall()

            for r in rows:
                cid_db, reel_path, clip_path, drive_id, created_at = r
                try:
                    created_dt = datetime.fromisoformat(created_at) if created_at else None
                except Exception:
                    created_dt = None

                # delete if older than cutoff
                if created_dt and created_dt < cutoff:
                    # delete local files
                    for p in (reel_path, clip_path):
                        try:
                            if p and os.path.exists(p):
                                os.remove(p)
                                logger.info(f"Deleted local file {p}")
                        except Exception:
                            logger.exception(f"Failed deleting local file {p}")

                    # delete drive file if present
                    if drive_id:
                        try:
                            self.drive.delete_file(drive_id)
                            logger.info(f"Deleted Drive file {drive_id}")
                        except Exception:
                            logger.exception(f"Failed deleting Drive file {drive_id}")
                    # mark DB row as deleted (soft-delete), don't remove the row
                    try:
                        drive_deleted_flag = 0
                        try:
                            # if drive deletion was attempted above and succeeded, set flag
                            # we don't have the success state from delete_file call, so assume success unless exception thrown
                            drive_deleted_flag = 1 if drive_id else 0
                        except Exception:
                            drive_deleted_flag = 0
                        with self.db_lock:
                            cur.execute(
                                "UPDATE clips SET deleted_at=?, drive_deleted=? WHERE id=?",
                                (datetime.utcnow().isoformat(), drive_deleted_flag, cid_db),
                            )
                            self.conn.commit()
                        logger.info(f"Marked clip row {cid_db} deleted in DB")
                    except Exception:
                        logger.exception("Failed marking clip row %s as deleted", cid_db)

            # also clean any leftover files under clips/ and reels/ older than cutoff
            for base in (ROOT / "clips", ROOT / "reels"):
                for dirpath, dirnames, filenames in os.walk(base):
                    for fn in filenames:
                        fp = Path(dirpath) / fn
                        try:
                            mtime = datetime.utcfromtimestamp(fp.stat().st_mtime)
                            if mtime < cutoff:
                                fp.unlink()
                                logger.info(f"Removed old file {fp}")
                        except Exception:
                            logger.exception(f"Failed handling file {fp}")

        except Exception:
            logger.exception("cleanup_storage failed")

        logger.info("cleanup_storage run completed")


def main():
    cfg = load_settings(ROOT / "settings.ini")
    rm = ReelsManager(cfg)

    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true", help="Run both jobs once and exit")
    p.add_argument("--disable-interaction", action="store_true", help="Disable responding to DMs/comments")
    args = p.parse_args()

    scheduler = BackgroundScheduler()

    search_cron = cfg.get("scheduler", "search_cron", fallback="*/15 * * * *")
    interact_cron = cfg.get("scheduler", "interactions_cron", fallback="*/5 * * * *")
    cleanup_cron = cfg.get("cleanup", "cleanup_cron", fallback="0 0 */3 * *")

    scheduler.add_job(rm.search_download_publish, CronTrigger.from_crontab(search_cron), id="search_job")
    if not args.disable_interaction:
        scheduler.add_job(rm.respond_interactions, CronTrigger.from_crontab(interact_cron), id="interact_job")
    else:
        logger.info("Interactions disabled by CLI flag; not scheduling respond_interactions job")
    scheduler.add_job(rm.cleanup_storage, CronTrigger.from_crontab(cleanup_cron), id="cleanup_job")

    if args.once:
        logger.info("Running jobs once (cli --once)")
        rm.search_download_publish()
        if not args.disable_interaction:
            rm.respond_interactions()
        else:
            logger.info("Interactions disabled by CLI flag; skipping respond_interactions run")
        return

    scheduler.start()
    logger.info("Scheduler started. Jobs: search -> %s, interactions -> %s", search_cron, interact_cron)

    try:
        # keep the main thread alive
        import time
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("Fatal error in manager")
