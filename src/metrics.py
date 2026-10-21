"""Prometheus metrics (scraped from reelsbuilder:8080/metrics by the platform's app-metrics job).

Counters are incremented by the pipeline; per-account state (queue size, token health, last publish)
is read from the DB at scrape time by the collector registered in src/api.py.
"""
from prometheus_client import Counter, Histogram

PUBLISHED = Counter("reels_published_total", "Reels published to Instagram", ["account"])
PUBLISH_FAILURES = Counter("reels_publish_failures_total", "Failed publish attempts", ["account", "reason"])
RENDERED = Counter("reels_rendered_total", "Reels rendered into the queue", ["account"])
VIDEOS = Counter("reels_videos_processed_total", "YouTube videos processed", ["account", "result"])
RUNS = Counter("reels_runs_total", "Scheduled/manual account runs by outcome", ["account", "outcome"])
PIPELINE_SECONDS = Histogram("reels_pipeline_seconds", "Download + render time per video", ["account"],
                             buckets=(30, 60, 120, 300, 600, 1200, 1800, 3600))
