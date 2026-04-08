"""Authentication helpers for the WebUI adapter."""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import bcrypt
import jwt
from fastapi import HTTPException, status

DEFAULT_USERNAME = "admin"
# REMOVED: DEFAULT_PASSWORD = "nanobot" - security vulnerability

# Password file location in user's home directory (not in workspace)
PASSWORD_FILE = Path("~/.xbot/webui/password").expanduser()

# JWT secret file location
JWT_SECRET_FILE = Path("~/.xbot/webui/jwt_secret").expanduser()


def generate_secure_password() -> str:
    """Generate a secure random password (~32 chars, URL-safe)."""
    return secrets.token_urlsafe(24)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def get_password_file_path() -> Path:
    """Get the path to the password file."""
    return PASSWORD_FILE


def ensure_password_file() -> str | None:
    """Ensure password file exists. Returns generated password if created, None if exists.

    This should be called during WebUI startup to generate a password on first run.
    """
    if PASSWORD_FILE.exists():
        return None

    # Generate new password
    password = generate_secure_password()

    # Create directory with secure permissions
    PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    # Write password hash with secure file permissions
    PASSWORD_FILE.write_text(hash_password(password), encoding="utf-8")
    PASSWORD_FILE.chmod(0o600)

    return password


def reset_password() -> str:
    """Generate a new password and save it. Returns the new password."""
    password = generate_secure_password()

    # Create directory with secure permissions
    PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    # Write password hash with secure file permissions
    PASSWORD_FILE.write_text(hash_password(password), encoding="utf-8")
    PASSWORD_FILE.chmod(0o600)

    return password


def set_password(new_password: str) -> None:
    """Set a new password."""
    # Create directory with secure permissions
    PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    # Write password hash with secure file permissions
    PASSWORD_FILE.write_text(hash_password(new_password), encoding="utf-8")
    PASSWORD_FILE.chmod(0o600)


def print_password_banner(password: str) -> None:
    """Print a prominent banner showing the generated password."""
    print(f"\n{'='*60}")
    print("WebUI generated a secure password for first-time setup:")
    print(f"  Username: {DEFAULT_USERNAME}")
    print(f"  Password: {password}")
    print(f"\nPassword hash saved to: {PASSWORD_FILE}")
    print("Please save this password securely. It will not be shown again.")
    print(f"{'='*60}\n")


def print_reset_password_banner(password: str) -> None:
    """Print a banner showing the reset password."""
    print(f"\n{'='*60}")
    print("New WebUI password generated:")
    print(f"  Username: {DEFAULT_USERNAME}")
    print(f"  Password: {password}")
    print(f"\nPassword hash saved to: {PASSWORD_FILE}")
    print("Please save this password securely. It will not be shown again.")
    print(f"{'='*60}\n")


def get_or_create_jwt_secret() -> str:
    """Get JWT secret from env, file, or generate new one.

    Priority:
    1. XBOT_JWT_SECRET environment variable (for production)
    2. JWT secret file (persists across restarts)
    3. Generate new secret and persist to file
    """
    # Check environment variable (production)
    env_secret = os.environ.get("XBOT_JWT_SECRET")
    if env_secret:
        return env_secret

    # Check file (persists across restarts)
    if JWT_SECRET_FILE.exists():
        return JWT_SECRET_FILE.read_text(encoding="utf-8").strip()

    # Generate and persist
    secret = secrets.token_hex(32)
    JWT_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    JWT_SECRET_FILE.write_text(secret, encoding="utf-8")
    JWT_SECRET_FILE.chmod(0o600)
    return secret


@dataclass
class UserStore:
    """Simple single-admin auth store backed by a JSON file."""

    path: Path

    def _get_password_hash(self) -> str:
        """Get password hash from the dedicated password file."""
        if not PASSWORD_FILE.exists():
            raise RuntimeError(
                "WebUI password file missing or corrupted.\n"
                "Run `xbot webui --reset-password` to generate a new password."
            )
        return PASSWORD_FILE.read_text(encoding="utf-8").strip()

    def ensure_default_admin(self) -> None:
        """Ensure the user store file exists with admin user."""
        if self.path.exists():
            return

        # Get password hash from dedicated file
        try:
            password_hash = self._get_password_hash()
        except RuntimeError:
            # Password file doesn't exist - this should have been handled during startup
            raise RuntimeError(
                "WebUI password not initialized. "
                "This should have been done during startup. "
                "Run `xbot webui --reset-password` to generate a password."
            )

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "username": DEFAULT_USERNAME,
                    "password_hash": password_hash,
                    "created_at": datetime.now(UTC).isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def load(self) -> dict[str, Any]:
        self.ensure_default_admin()
        user_data = json.loads(self.path.read_text(encoding="utf-8"))
        # Always resolve the password hash from the dedicated password file so
        # that the value stored in users.json is never used for authentication
        # (it may be stale after a password reset).
        user_data["password_hash"] = self._get_password_hash()
        return user_data

    def authenticate(self, username: str, password: str) -> dict[str, Any]:
        user = self.load()
        if username != user["username"] or not verify_password(password, user["password_hash"]):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
        return {"id": "admin", "username": user["username"], "role": "admin"}

    def change_password(self, current_password: str, new_password: str) -> None:
        user = self.load()
        if not verify_password(current_password, user["password_hash"]):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
        # Write new hash to the dedicated password file (single source of truth).
        # We intentionally do NOT update users.json because load() always reads
        # the hash from PASSWORD_FILE, making the field in users.json unused.
        set_password(new_password)


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
