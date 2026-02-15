import os
import requests
from dotenv import load_dotenv, set_key
from typing import Dict, List, Optional
import mimetypes
import uuid

ENV_PATH = ".env"
GRAPH_BASE = "https://graph.facebook.com/v21.0"


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

    def __init__(self, env_path: str = ".env"):
        self.env_path = env_path
        load_dotenv(env_path)

        self.app_id = os.getenv("APP_ID")
        self.app_secret = os.getenv("APP_SECRET")
        self.user_token = os.getenv("USER_TOKEN")
        self.page_id = os.getenv("FB_PAGE_ID")
        self.ig_business_id = os.getenv("INSTAGRAM_BUISINESS_ACCOUNT")
        self.access_token = os.getenv("LONG_TIME_ACCESS_TOKEN")

        self._validate_env()
        self._ensure_long_lived_token()

    # ------------------------------------------------------------------
    # 🔐 TOKEN MANAGEMENT
    # ------------------------------------------------------------------

    def _validate_env(self):
        missing = [
            name for name, value in {
                "APP_ID": self.app_id,
                "APP_SECRET": self.app_secret,
                "USER_TOKEN": self.user_token,
                "FB_PAGE_ID": self.page_id,
                "INSTAGRAM_BUISINESS_ACCOUNT": self.ig_business_id,
            }.items() if not value
        ]
        if missing:
            raise EnvironmentError(f"Missing env vars: {missing}")

    def _ensure_long_lived_token(self):
        if self.access_token:
            return

        token = self._exchange_long_lived_token()
        set_key(self.env_path, "LONG_TIME_ACCESS_TOKEN", token)
        self.access_token = token

    def _exchange_long_lived_token(self) -> str:
        r = requests.get(
            "https://graph.facebook.com/v17.0/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": self.app_id,
                "client_secret": self.app_secret,
                "fb_exchange_token": self.user_token,
            },
        )
        r.raise_for_status()
        return r.json()["access_token"]

    # ------------------------------------------------------------------
    # 🌐 GRAPH REQUEST HELPERS
    # ------------------------------------------------------------------

    def _get(self, endpoint: str, params: Optional[Dict] = None) -> Dict:
        params = params or {}
        params["access_token"] = self.access_token
        r = requests.get(f"{GRAPH_BASE}/{endpoint}", params=params)
        r.raise_for_status()
        return r.json()

    def _post(self, endpoint: str, data: Dict) -> Dict:
        data["access_token"] = self.access_token
        r = requests.post(f"{GRAPH_BASE}/{endpoint}", data=data)
        r.raise_for_status()
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
        }
        if image_url:
            payload["image_url"] = image_url
        if video_url:
            payload["video_url"] = video_url
        payload = {k: v for k, v in payload.items() if v is not None}
        r = self._post(f"{self.ig_business_id}/media", payload)
        return r["id"]

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



# ------------------------------------------------------------------
# 🧪 EXAMPLE USAGE
# ------------------------------------------------------------------

if __name__ == "__main__":
    ig = InstagramHandler()

    print("User Details:", ig.get_user_details())
    print("Instagram Account:", ig.get_instagram_account())
    print("Recent Media:", ig.get_instagram_media(limit=5))

    # Export first conversation as JSON
    conversations = ig.list_conversations()
    if conversations:
        conv_json = ig.export_conversation_as_json(conversations[0]["id"], limit=10)
        print("Conversation JSON:", conv_json)

    # Example posting local media
    # ig.post_local_media("./assets/post.jpg", caption="Hello from local file!")
