from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from core.exceptions import AppError


@dataclass(frozen=True, slots=True)
class PasswordHasher:
    iterations: int = 260_000
    salt_bytes: int = 16
    min_length: int = 8
    salt: str | None = None

    def __post_init__(self) -> None:
        if self.iterations < 1000:
            raise AppError(
                "VALIDATION_ERROR",
                "Password hash iterations must be at least 1000",
                status_code=400,
            )
        if self.salt_bytes < 8:
            raise AppError(
                "VALIDATION_ERROR",
                "Password salt_bytes must be at least 8",
                status_code=400,
            )
        if self.min_length < 8:
            raise AppError(
                "VALIDATION_ERROR",
                "Password min_length must be at least 8",
                status_code=400,
            )

    def hash_password(self, password: str) -> str:
        self._validate_password(password)
        salt = self.salt or secrets.token_hex(self.salt_bytes)
        digest = self._digest(password, salt, self.iterations)
        return f"pbkdf2_sha256${self.iterations}${salt}${digest}"

    def verify_password(self, password: str, password_hash: str) -> bool:
        try:
            algorithm, iterations_text, salt, expected_digest = password_hash.split("$", 3)
            if algorithm != "pbkdf2_sha256":
                return False
            iterations = int(iterations_text)
            if not password:
                return False
            digest = self._digest(password, salt, iterations)
            return hmac.compare_digest(digest, expected_digest)
        except (TypeError, ValueError):
            return False

    def _validate_password(self, password: str) -> None:
        if len(password) < self.min_length:
            raise AppError(
                "VALIDATION_ERROR",
                f"Password must be at least {self.min_length} characters",
                status_code=400,
            )

    def _digest(self, password: str, salt: str, iterations: int) -> str:
        return hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            iterations,
        ).hex()
