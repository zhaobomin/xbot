"""Owned upload storage. SDK inputs always use local bytes, never client paths."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from functools import wraps
from pathlib import Path

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_ATTACHMENTS = 8
UNSENT_RETENTION_SECONDS = 24 * 3600


def _locked(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapped


class AttachmentStore:
    def __init__(self, directory: Path, secret: str):
        self._lock = threading.RLock()
        self.directory = directory
        self.secret = secret.encode()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _metadata_path(self, attachment_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", attachment_id):
            raise ValueError("Invalid attachment ID")
        return self.directory / (attachment_id + ".meta")

    @_locked
    def get(self, attachment_id: str) -> dict:
        try:
            return json.loads(self._metadata_path(attachment_id).read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Attachment not found") from exc

    @_locked
    def save(self, owner: str, name: str, content: bytes) -> dict:
        if not content or len(content) > MAX_UPLOAD_BYTES:
            raise ValueError("Attachment must be between 1 byte and 20MB")
        # Retain suffix for runtime classification, never the user-supplied path.
        name = Path(name.replace("\\", "/")).name[:200] or "attachment"
        suffix = Path(name).suffix.lower()
        allowed = {
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
            ".pdf",
            ".txt",
            ".md",
            ".csv",
            ".json",
            ".log",
            ".docx",
            ".xlsx",
            ".pptx",
            ".zip",
            ".mp3",
            ".wav",
            ".mp4",
            ".m4a",
            ".ogg",
        }
        if suffix not in allowed:
            raise ValueError("Unsupported attachment type")
        if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
            from xbot.platform.utils.helpers import detect_image_mime

            if not detect_image_mime(content):
                raise ValueError("Invalid image content")
        self.cleanup()
        attachment_id = secrets.token_hex(16)
        path = self.directory / (attachment_id + suffix)
        metadata = {
            "id": attachment_id,
            "owner": owner,
            "name": name,
            "filename": path.name,
            "created_at": time.time(),
            "referenced": False,
        }
        try:
            path.write_bytes(content)
            path.chmod(0o600)
            self._write(metadata)
        except BaseException:
            path.unlink(missing_ok=True)
            self._metadata_path(attachment_id).unlink(missing_ok=True)
            raise
        return metadata

    @_locked
    def _write(self, metadata: dict) -> None:
        path = self._metadata_path(metadata["id"])
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(metadata), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(path)

    @_locked
    def resolve(
        self, attachment_ids: list[str], owner: str, *, reference: bool = False
    ) -> list[str]:
        if len(attachment_ids) > MAX_ATTACHMENTS:
            raise ValueError("Too many attachments (maximum 8)")
        metadata = [self.get(identifier) for identifier in attachment_ids]
        if any(item["owner"] != owner for item in metadata):
            raise ValueError("Attachment belongs to another user")
        paths = [str(self.directory / item["filename"]) for item in metadata]
        if any(not Path(path).is_file() for path in paths):
            raise ValueError("Attachment not found")
        if reference:
            for item in metadata:
                item["referenced"] = True
                self._write(item)
        return paths

    def preview_signature(self, attachment_id: str, expires: int) -> str:
        return hmac.new(
            self.secret, f"attachment:{attachment_id}:{expires}".encode(), hashlib.sha256
        ).hexdigest()

    def preview_path(self, attachment_id: str, expires: int, signature: str) -> tuple[Path, str]:
        if expires < time.time() or not hmac.compare_digest(
            signature, self.preview_signature(attachment_id, expires)
        ):
            raise ValueError("Attachment preview expired or invalid")
        item = self.get(attachment_id)
        return self.directory / item["filename"], item["name"]

    @_locked
    def cleanup(self) -> None:
        for path in self.directory.glob("*.meta"):
            try:
                item = json.loads(path.read_text())
                if (
                    not item.get("referenced")
                    and time.time() - item["created_at"] > UNSENT_RETENTION_SECONDS
                ):
                    (self.directory / item["filename"]).unlink(missing_ok=True)
                    path.unlink(missing_ok=True)
            except (OSError, ValueError, KeyError):
                continue


def mirror_to_s3(config: dict, metadata: dict, content: bytes) -> None:
    """Optional remote copy; local bytes remain available to the SDK."""
    if not config.get("enabled"):
        return
    if not all(config.get(key) for key in ("bucket", "access_key_id", "secret_access_key")):
        raise ValueError("Incomplete S3 configuration")
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "s3",
        endpoint_url=config.get("endpoint_url") or None,
        aws_access_key_id=config["access_key_id"],
        aws_secret_access_key=config["secret_access_key"],
        region_name=config.get("region") or "us-east-1",
        config=Config(connect_timeout=5, read_timeout=20, retries={"max_attempts": 1}),
    )
    try:
        client.put_object(
            Bucket=config["bucket"], Key="xbot-attachments/" + metadata["filename"], Body=content
        )
    finally:
        client.close()
