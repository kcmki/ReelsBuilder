import json
import os
import time
import requests
from typing import Dict, List, Optional

GRAPH_BASE = "https://graph.facebook.com/v24.0"


class InstagramHandler:
    """
    Instagram Business API Handler (Graph API only)
    - Posting content (image / video / carousel)
    - Using media URLs
    - Reading & replying to DMs
    - Listing conversations
    - Fetching conversation history (limit)
    - Replying to comments
    - Exporting conversation as JSON
    """

    def __init__(self, access_token: str, ig_business_id: str, page_id: Optional[str] = None):
        if not access_token or not ig_business_id:
            raise ValueError("access_token and ig_business_id are required")
        self.access_token = access_token
        self.ig_business_id = ig_business_id
        self.page_id = page_id

    @staticmethod
    def exchange_long_lived_token(app_id: str, app_secret: str, user_token: str) -> str:
        r = requests.get(
            f"{GRAPH_BASE}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": app_id,
                "client_secret": app_secret,
                "fb_exchange_token": user_token,
            },
            timeout=30,
        )
        _raise(r)
        return r.json()["access_token"]

    # ------------------------------------------------------------------
    # 🌐 GRAPH REQUEST HELPERS
    # ------------------------------------------------------------------

    def _get(self, endpoint: str, params: Optional[Dict] = None) -> Dict:
        params = params or {}
        params["access_token"] = self.access_token
        r = requests.get(f"{GRAPH_BASE}/{endpoint}", params=params, timeout=60)
        _raise(r)
        return r.json()

    def _post(self, endpoint: str, data: Dict) -> Dict:
        # nested dicts (e.g. messaging recipient) must be JSON-encoded for form posts
        data = {k: json.dumps(v) if isinstance(v, dict) else v for k, v in data.items()}
        data["access_token"] = self.access_token
        r = requests.post(f"{GRAPH_BASE}/{endpoint}", data=data, timeout=60)
        _raise(r)
        return r.json()

    # ------------------------------------------------------------------
    # 📸 ACCOUNT & MEDIA
    # ------------------------------------------------------------------

    def get_user_details(self):
        return self._get("me", {"fields": "id,name"})

    def get_page_details(self):
        return self._get(self.page_id, {"fields": "id,name,category,fan_count"})

    def get_instagram_account(self):
        return self._get(
            self.ig_business_id,
            {"fields": "id,username,name,followers_count"},
        )

    def get_instagram_media(self, limit: int = 10):
        return self._get(
            f"{self.ig_business_id}/media",
            {
                "fields": "id,caption,media_type,media_url,permalink,timestamp",
                "limit": limit,
            },
        )

    def create_media_container(
        self,
        *,
        image_url: Optional[str] = None,
        video_url: Optional[str] = None,
        caption: Optional[str] = None,
        is_carousel_item: bool = False,
        location_id: Optional[str] = None,
        user_tags: Optional[str] = None,
        product_tags: Optional[str] = None,
        thumb_offset: Optional[int] = None,
    ) -> str:
        payload = {
            "caption": caption,
            "is_carousel_item": is_carousel_item,
            "location_id": location_id,
            "user_tags": user_tags,
            "product_tags": product_tags,
            "thumb_offset": thumb_offset,
            "media_type": "CAROUSEL" if is_carousel_item else ("VIDEO" if video_url else "IMAGE"),
        }
        if image_url:
            payload["image_url"] = image_url
        if video_url:
            payload["video_url"] = video_url
        payload = {k: v for k, v in payload.items() if v is not None}
        r = self._post(f"{self.ig_business_id}/media", payload)
        return r["id"]

    # -----------------------
    # Resumable upload helpers (rupload)
    # -----------------------
    def create_resumable_media_container(
        self,
        *,
        media_type: str = "REELS",
        caption: Optional[str] = None,
        thumb_offset: Optional[int] = None,
        is_carousel_item: bool = False,
        additional: Optional[dict] = None,
    ) -> Dict:
        """
        Initialize a resumable upload session for a video. Returns dict with at least `id` and `uri`.
        """
        payload = {
            "upload_type": "resumable",
            "media_type": media_type,
            "is_carousel_item": is_carousel_item,
        }
        if caption:
            payload["caption"] = caption
        if thumb_offset is not None:
            payload["thumb_offset"] = int(thumb_offset)
        if additional:
            payload.update(additional)

        # Use requests directly so we can control JSON body and headers
        headers = {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}
        r = requests.post(f"{GRAPH_BASE}/{self.ig_business_id}/media", headers=headers, json=payload, timeout=60)
        _raise(r)
        return r.json()

    def upload_resumable_video(self, upload_uri: str, *, local_path: Optional[str] = None, file_url: Optional[str] = None, offset: int = 0) -> Dict:
        """
        Upload the video data to the rupload endpoint returned by `create_resumable_media_container`.
        - For local files, pass `local_path` and the file will be POSTed with headers `offset` and `file_size`.
        - For hosted files, pass `file_url` and the `file_url` header will be used.
        Returns the JSON response from the rupload server.
        """
        headers = {"Authorization": f"OAuth {self.access_token}"}
        if local_path:
            file_size = os.path.getsize(local_path)
            headers.update({"offset": str(offset), "file_size": str(file_size)})
            with open(local_path, "rb") as fh:
                r = requests.post(upload_uri, headers=headers, data=fh, timeout=600)
        elif file_url:
            headers.update({"file_url": file_url})
            r = requests.post(upload_uri, headers=headers)
        else:
            raise ValueError("Either local_path or file_url must be provided")

        _raise(r)
        # rupload often returns plain JSON-like text
        try:
            return r.json()
        except Exception:
            return {"raw": r.text}

    def get_container_video_status(self, creation_id: str) -> Dict:
        """Return the fields related to video upload/processing for a container."""
        return self._get(creation_id, {"fields": "id,status,status_code,video_status"})

    def get_container_status(self, creation_id: str) -> str:
        """Check the status of a media container. Returns 'FINISHED', 'ERROR', etc."""
        data = self._get(creation_id, {"fields": "status_code"})
        return data.get("status_code")

    def publish_media(self, creation_id: str) -> Dict:
        """Publishes the media once the container is finished."""
        status = self.get_container_status(creation_id)
        if status != "FINISHED":
            raise RuntimeError(f"Media container not ready: {status}")
        return self._post(f"{self.ig_business_id}/media_publish", {"creation_id": creation_id})

    def publish_reel(self, local_path: str, caption: str, thumb_offset_ms: int = 0) -> str:
        """Full resumable flow: container -> upload -> wait -> publish. Returns the IG media id."""
        resp = self.create_resumable_media_container(
            media_type="REELS", caption=caption, thumb_offset=thumb_offset_ms)
        container_id, upload_uri = resp.get("id"), resp.get("uri")
        if not container_id or not upload_uri:
            raise RuntimeError(f"Invalid resumable container response: {resp}")
        self.upload_resumable_video(upload_uri, local_path=local_path)
        for _ in range(60):
            status = self.get_container_status(container_id)
            if status == "FINISHED":
                return self._post(f"{self.ig_business_id}/media_publish", {"creation_id": container_id})["id"]
            if status in ("ERROR", "EXPIRED"):
                raise RuntimeError(f"Container {container_id} {status}: {self.get_container_video_status(container_id)}")
            time.sleep(5)
        raise TimeoutError(f"Container {container_id} not ready after 5 min")

    # ------------------------------------------------------------------
    # 🗂 MEDIA CONTAINER CRUD
    # ------------------------------------------------------------------

    def get_container_details(self, creation_id: str) -> Dict:
        """Fetch details of an existing media container"""
        return self._get(creation_id, {"fields": "id,status_code,media_type,media_url,thumbnail_url,caption"})

    def update_container_caption(self, creation_id: str, new_caption: str) -> Dict:
        """Update the caption of a media container (before publishing)"""
        return self._post(creation_id, {"caption": new_caption})

    def delete_container(self, creation_id: str) -> Dict:
        """Delete a media container"""
        r = requests.delete(f"{GRAPH_BASE}/{creation_id}", params={"access_token": self.access_token})
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------
    # 🖼 MEDIA CRUD
    # ------------------------------------------------------------------

    def get_media_details(self, media_id: str) -> Dict:
        """Get information about a published media"""
        return self._get(media_id, {"fields": "id,caption,media_type,media_url,permalink,timestamp"})

    def update_media_caption(self, media_id: str, new_caption: str) -> Dict:
        """Update the caption of a published media"""
        return self._post(media_id, {"caption": new_caption})

    def delete_media(self, media_id: str) -> Dict:
        """Delete a published media"""
        r = requests.delete(f"{GRAPH_BASE}/{media_id}", params={"access_token": self.access_token})
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------
    # 🗂 LIST MEDIA CONTAINERS
    # ------------------------------------------------------------------

    def list_media_containers(self, limit: int = 25) -> List[Dict]:
        """
        Lists all media containers (published or pending) for the IG Business account.
        Returns a list of dicts with id, status_code, media_type, caption, and media_url.
        """
        try:
            response = self._get(
                f"{self.ig_business_id}/media",
                {"fields": "id,status_code,media_type,caption,media_url,thumbnail_url", "limit": limit}
            )
            return response.get("data", [])
        except requests.HTTPError as e:
            print("❌ Failed to list media containers:", e.response.text)
            return []

    def list_published_media(self, limit: int = 25) -> List[Dict]:
        """
        Lists only published media.
        Returns a list of dicts with id, caption, media_type, media_url, permalink, timestamp.
        """
        try:
            response = self._get(
                f"{self.ig_business_id}/media",
                {"fields": "id,caption,media_type,media_url,permalink,timestamp", "limit": limit}
            )
            return response.get("data", [])
        except requests.HTTPError as e:
            print("❌ Failed to list published media:", e.response.text)
            return []
    # ------------------------------------------------------------------
    # 💬 DIRECT MESSAGES
    # ------------------------------------------------------------------

    def list_conversations(self) -> List[Dict]:
        try:
            return self._get(f"{self.ig_business_id}/conversations").get("data", [])
        except requests.HTTPError as e:
            print("❌ Failed to list conversations:", e.response.text)
            return []

    def get_conversation_messages(self, conversation_id: str, limit: int = 20) -> List[Dict]:
        try:
            return self._get(
                f"{conversation_id}/messages",
                {"fields": "id,from,message,created_time", "limit": limit}
            ).get("data", [])
        except requests.HTTPError as e:
            print("❌ Failed to get conversation messages:", e.response.text)
            return []

    def reply_to_dm(self, recipient_id: str, text: str) -> Dict:
        return self._post(f"{self.page_id}/messages", {"recipient": {"id": recipient_id}, "message": {"text": text}})

    def dm_user(self, instagram_user_id: str, text: str) -> Dict:
        return self.reply_to_dm(instagram_user_id, text)

    # ------------------------------------------------------------------
    # 💬 COMMENTS
    # ------------------------------------------------------------------

    def list_media_comments(self, media_id: str, limit: int = 20) -> List[Dict]:
        return self._get(f"{media_id}/comments", {"fields": "id,text,username,timestamp", "limit": limit}).get("data", [])

    def reply_to_comment(self, comment_id: str, text: str) -> Dict:
        return self._post(f"{comment_id}/replies", {"message": text})

    # ------------------------------------------------------------------
    # 📜 CONVERSATION EXPORT
    # ------------------------------------------------------------------

    def export_conversation_as_json(self, conversation_id: str, limit: int = 20) -> Dict:
        raw_messages = self.get_conversation_messages(conversation_id=conversation_id, limit=limit)
        messages = []
        for msg in reversed(raw_messages):
            sender_id = msg["from"]["id"]
            messages.append({
                "id": msg["id"],
                "sender_id": sender_id,
                "role": "business" if sender_id == self.page_id else "user",
                "text": msg.get("message", ""),
                "timestamp": msg["created_time"],
            })
        return {"conversation_id": conversation_id, "platform": "instagram", "messages": messages}


def _raise(r: requests.Response):
    """raise_for_status, but keep Graph's error message (it says *why*, e.g. expired token)."""
    if r.ok:
        return
    try:
        msg = r.json().get("error", {}).get("message") or r.text
    except ValueError:
        msg = r.text
    raise requests.HTTPError(f"{r.status_code} {msg}", response=r)
