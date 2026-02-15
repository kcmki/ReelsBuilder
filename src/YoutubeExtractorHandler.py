from curl_cffi import  requests
import re
import json
from typing import List, Callable, Optional

class YoutubeExtractorHandler:
    def __init__(self):
        self.session = requests.Session()

    # --- Fetch videos from search query ---
    def search_videos_depreciated(
        self, 
        query: str, 
        max_results: int = 10, 
        filter_fn: Optional[Callable[[dict], bool]] = None
    ) -> List[dict]:
        """
        Search YouTube for videos matching a query.
        Optionally filter results with filter_fn(video_dict) -> bool
        """
        search_url = f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}"
        response = self.session.get(search_url)
        
        # Extract JSON from ytInitialData
        script_match = re.search(r'<script.*?>\s*var ytInitialData = ({.*?});\s*</script>', response.text, re.DOTALL)
        if not script_match:
            print("ytInitialData not found in search page")
            return []

        json_data = json.loads(script_match.group(1))
        try:
            videos = []
            contents = json_data["contents"]["twoColumnSearchResultsRenderer"]["primaryContents"]["sectionListRenderer"]["contents"]
            for section in contents:
                items = section.get("itemSectionRenderer", {}).get("contents", [])
                for item in items:
                    if "videoRenderer" in item:
                        video_info = item["videoRenderer"]
                        if not filter_fn or filter_fn(video_info):
                            videos.append(video_info)
                        if len(videos) >= max_results:
                            break
            return videos
        except KeyError:
            print("Unexpected JSON structure")
            return []

    # ---------------------------------------------------
    # SEARCH
    # ---------------------------------------------------
    def search_videos(
        self,
        query: str,
        limit: int = 10,
        sort_by: str = "relevance",
        results_type: str = "video",
        sleep: int = 1,
        proxies: Optional[dict] = None,
        filters: Optional[List[Callable[[dict], bool]]] = None,
    ) -> List[dict]:
        """
        Search YouTube using scrapetube with optional filters
        """
        videos: Generator[dict, None, None] = scrapetube.get_search(
            query=query,
            limit=limit,
            sleep=sleep,
            sort_by=sort_by,
            results_type=results_type,
            proxies=proxies,
        )

        results = []
        for video in videos:
            if filters:
                if not all(f(video) for f in filters):
                    continue
            results.append(video)

        return results
    
    # --- Extract points without concatenation ---
    def get_points(self, url: str, top_x: int = 5) -> List[dict]:
        response = self.session.get(url)
        script_match = re.search(r'<script.*?>\s*var ytInitialData = ({.*?});\s*</script>', response.text, re.DOTALL)
        if not script_match:
            print("ytInitialData not found")
            return []

        json_data = json.loads(script_match.group(1))
        try:
            scores = json_data["frameworkUpdates"]["entityBatchUpdate"]["mutations"][0]["payload"]["macroMarkersListEntity"]["markersList"]["markers"]
            max_intensity_elements = sorted(scores, key=lambda x: x['intensityScoreNormalized'])
            return max_intensity_elements[-top_x:]
        except KeyError:
            print("Markers not found in video data")
            return []

    # --- Extract points with concatenation ---
    def get_points_concatenated(self, url: str, top_x: int = 5) -> List[dict]:
        response = self.session.get(url)
        script_match = re.search(r'<script.*?>\s*var ytInitialData = ({.*?});\s*</script>', response.text, re.DOTALL)
        if not script_match:
            print("ytInitialData not found")
            return []

        json_data = json.loads(script_match.group(1))
        try:
            scores = json_data["frameworkUpdates"]["entityBatchUpdate"]["mutations"][0]["payload"]["macroMarkersListEntity"]["markersList"]["markers"]
        except KeyError:
            print("Markers not found in video data")
            return []

        # Ensure duration is int
        for score in scores:
            score['durationMillis'] = int(score['durationMillis'])

        # Merge consecutive points with high intensity
        i = 0
        while i < len(scores) - 1:
            current = scores[i]
            next_point = scores[i + 1]
            if current['intensityScoreNormalized'] > 0.5 and next_point['intensityScoreNormalized'] > 0.5:
                current['durationMillis'] += next_point['durationMillis']
                current["intensityScoreNormalized"] = max(current["intensityScoreNormalized"], next_point["intensityScoreNormalized"])
                scores.pop(i + 1)
            else:
                i += 1

        max_intensity_elements = sorted(scores, key=lambda x: x['intensityScoreNormalized'])
        return max_intensity_elements[-top_x:]

    def download_video(
        self,
        url: str,
        output_path: str,
        max_height: int = 1080,
    ) -> str:
        """
        Download a YouTube video to a given path.
        Returns the final video path.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        ydl_opts = {
            "format": f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]",
            "outtmpl": str(output_path),
            "merge_output_format": "mp4",
        }

        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        return str(output_path)


