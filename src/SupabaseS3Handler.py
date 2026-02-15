import os
import logging
import mimetypes
from typing import Optional, Dict, List
from urllib.parse import quote

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

logger = logging.getLogger("SupabaseS3Handler")


class SupabaseS3Handler:
    def __init__(
        self,
        bucket: str,
        endpoint: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        region: Optional[str] = None,
        public_base: Optional[str] = None,
    ):
        endpoint = endpoint or os.getenv("endpoint")
        access_key = access_key or os.getenv("key_id")
        secret_key = secret_key or os.getenv("secret_key")
        region = region or os.getenv("SUPABASE_REGION")
        public_base = public_base or os.getenv("SUPABASE_PUBLIC_BASE")

        if not endpoint or not access_key or not secret_key:
            raise ValueError("endpoint, key_id and secret_key must be provided via args or env vars")

        self.bucket = bucket
        self.endpoint = endpoint
        self.public_base = public_base

        self.s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=Config(signature_version="s3v4"),
        )

    def upload_file(self, key: str, local_path: str, public: bool = True) -> Dict:
        content_type, _ = mimetypes.guess_type(local_path)
        extra = {}
        if content_type:
            extra["ContentType"] = content_type
        if public:
            extra["ACL"] = "public-read"
        print(content_type, extra)
        try:
            self.s3.upload_file(str(local_path), self.bucket, key, ExtraArgs=extra)
            return {"Bucket": self.bucket, "Key": key}
        except ClientError:
            logger.exception("upload_file failed for %s", local_path)
            raise

    def upload_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream", public: bool = True) -> Dict:
        extra = {"ContentType": content_type}
        if public:
            extra["ACL"] = "public-read"
        try:
            self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, **extra)
            return {"Bucket": self.bucket, "Key": key}
        except ClientError:
            logger.exception("upload_bytes failed for %s", key)
            raise

    def delete_file(self, key: str) -> bool:
        try:
            self.s3.delete_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            logger.exception("delete_file failed for %s", key)
            return False

    def make_file_public(self, key: str) -> str:
        if self.public_base:
            return f"{self.public_base.rstrip('/')}/{self.bucket}/{quote(key)}"

        if self.endpoint and "/storage/" in self.endpoint:
            host = self.endpoint.split("/storage/")[0]
            return f"{host}/storage/v1/object/public/{self.bucket}/{quote(key)}"

        return f"{self.endpoint.rstrip('/')}/{self.bucket}/{quote(key)}"

    def generate_presigned_url(self, key: str, expires_in: int = 3600) -> Optional[str]:
        try:
            return self.s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires_in,
            )
        except ClientError:
            logger.exception("generate_presigned_url failed for %s", key)
            return None

    def list_files(self, prefix: str = "", max_keys: int = 1000) -> List[Dict]:
        try:
            resp = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=prefix, MaxKeys=max_keys)
            items = resp.get("Contents", []) or []
            return [{"Key": it["Key"], "Size": it["Size"], "LastModified": it["LastModified"]} for it in items]
        except ClientError:
            logger.exception("list_files failed for prefix %s", prefix)
            raise

    def object_exists(self, key: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            code = getattr(e, 'response', {}).get('Error', {}).get('Code')
            if code in ("404", "NotFound"):
                return False
            logger.exception("object_exists check failed for %s", key)
            raise
