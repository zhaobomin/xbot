"""Authentication helpers for the WebUI adapter."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import bcrypt
import jwt
from fastapi import HTTPException, status

DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "nanobot"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


@dataclass
class UserStore:
    """Simple single-admin auth store backed by a JSON file."""

    path: Path

    def ensure_default_admin(self) -> None:
        if self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "username": DEFAULT_USERNAME,
                    "password_hash": hash_password(DEFAULT_PASSWORD),
                    "created_at": datetime.now(UTC).isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def load(self) -> dict[str, Any]:
        self.ensure_default_admin()
        return json.loads(self.path.read_text(encoding="utf-8"))

    def authenticate(self, username: str, password: str) -> dict[str, Any]:
        user = self.load()
        if username != user["username"] or not verify_password(password, user["password_hash"]):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
        return {"id": "admin", "username": user["username"], "role": "admin"}

    def change_password(self, current_password: str, new_password: str) -> None:
        user = self.load()
        if not verify_password(current_password, user["password_hash"]):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
        user["password_hash"] = hash_password(new_password)
        self.path.write_text(json.dumps(user, ensure_ascii=False, indent=2), encoding="utf-8")


class AuthManager:
    """JWT issue/verify helper."""

    def __init__(self, secret: str) -> None:
        self.secret = secret

    def issue_token(self, user: dict[str, Any], ttl_minutes: int = 60 * 24) -> str:
        payload = {
            "sub": user["id"],
            "username": user["username"],
            "role": user["role"],
            "exp": datetime.now(UTC) + timedelta(minutes=ttl_minutes),
        }
        return jwt.encode(payload, self.secret, algorithm="HS256")

    def decode_token(self, token: str) -> dict[str, Any]:
        try:
            return jwt.decode(token, self.secret, algorithms=["HS256"])
        except jwt.PyJWTError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc
