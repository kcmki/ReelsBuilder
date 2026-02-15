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
    - Local media uploads
    - Reading & replying to DMs
    - Listing conversations
    - Fetching conversation history (limit)
    - Replying to comments
    - DMing users (webhook triggered, e.g. new followers)
    - Exporting conversation as JSON (AI-ready, fully formatted)
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

    def publish_media(self, creation_id: str) -> Dict:
        return self._post(f"{self.ig_business_id}/media_publish", {"creation_id": creation_id})

    # ------------------------------------------------------------------
    # 💬 DIRECT MESSAGES
    # ------------------------------------------------------------------

    def list_conversations(self) -> List[Dict]:
        """
        Returns a list of Instagram conversations.
        """
        try:
            return self._get(f"{self.ig_business_id}/conversations").get("data", [])
        except requests.HTTPError as e:
            print("❌ Failed to list conversations:", e.response.text)
            return []
    def get_conversation_messages(self, conversation_id: str, limit: int = 20) -> List[Dict]:
        """
        Fetch messages of a conversation.
        """
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
    # 📂 LOCAL MEDIA UPLOAD
    # ------------------------------------------------------------------

    def upload_local_media(self, file_path: str) -> str:
        """
        Uploads a local media file using Facebook resumable upload.
        Returns a media ID usable by Instagram media endpoints.
        Works for video and images.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(file_path)

        file_size = os.path.getsize(file_path)
        mime_type, _ = mimetypes.guess_type(file_path)
        upload_id = str(uuid.uuid4())

        # Start upload
        start = requests.post(
            "https://upload.facebook.com/video-upload/v21.0/start",
            data={"access_token": self.access_token, "file_size": file_size},
        )
        start.raise_for_status()
        session = start.json()
        upload_url = session["upload_url"]

        # Upload file
        with open(file_path, "rb") as f:
            upload = requests.post(
                upload_url,
                headers={"Authorization": f"OAuth {self.access_token}", "file_offset": "0"},
                data=f,
            )
            upload.raise_for_status()

        # Finish upload
        finish = requests.post(
            "https://upload.facebook.com/video-upload/v21.0/finish",
            data={"access_token": self.access_token, "upload_session_id": session["upload_session_id"]},
        )
        finish.raise_for_status()

        return finish.json()["video_id"]

    def post_local_media(
        self,
        file_path: str,
        caption: Optional[str] = None,
        *,
        location_id: Optional[str] = None,
        user_tags: Optional[str] = None,
        product_tags: Optional[str] = None,
        thumb_offset: Optional[int] = None,
    ) -> Dict:
        media_id = self.upload_local_media(file_path)
        is_video = file_path.lower().endswith((".mp4", ".mov"))
        container = self._post(
            f"{self.ig_business_id}/media",
            {
                "video_id" if is_video else "image_id": media_id,
                "caption": caption,
                "location_id": location_id,
                "user_tags": user_tags,
                "product_tags": product_tags,
                "thumb_offset": thumb_offset,
            },
        )
        return self.publish_media(container["id"])


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
