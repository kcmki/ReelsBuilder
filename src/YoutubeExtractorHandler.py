import re
from typing import List, Optional
from pathlib import Path

import scrapetube
from yt_dlp import YoutubeDL


class YoutubeExtractorHandler:

    def search_videos(self, query: str, limit: int = 10, sort_by: str = "relevance", sleep: int = 1) -> List[dict]:
        """Search YouTube; returns [{video_id, title}]."""
        results = []
        for v in scrapetube.get_search(query=query, limit=limit, sleep=sleep, sort_by=sort_by, results_type="video"):
            vid = v.get("videoId")
            if vid:
                runs = (v.get("title") or {}).get("runs") or [{}]
                results.append({"video_id": vid, "title": runs[0].get("text", "")})
        return results

    def download_video(self, url: str, output_path: str, max_height: int = 1080) -> dict:
        """Download to output_path. Returns {path, title, duration, heatmap}.

        heatmap is yt-dlp's "most replayed" data ([{start_time, end_time, value}]) or None.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        ydl_opts = {
            # H.264 + AAC first: decodes several times faster than AV1/VP9 and muxes cleanly into mp4
            "format": (f"bv*[height<={max_height}][vcodec^=avc1]+ba[ext=m4a]/"
                       f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b"),
            "outtmpl": str(output_path),
            "merge_output_format": "mp4",
            "quiet": True,
            "noprogress": True,
        }
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
        return {
            "path": str(output_path),
            "title": info.get("title") or "",
            "duration": info.get("duration"),
            "heatmap": info.get("heatmap") or None,
        }

    @staticmethod
    def video_id(url: str) -> Optional[str]:
        m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([\w-]{11})", url)
        return m.group(1) if m else None
