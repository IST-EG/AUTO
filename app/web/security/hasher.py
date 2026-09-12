"""
Password hashing abstraction and Bcrypt implementation.

Provides timing-safe password verification, configurable work factor,
and future work-factor upgrade detection without coupling the application
directly to a concrete algorithm.
"""

import re
from typing import Protocol, Tuple
import bcrypt


class PasswordHasher(Protocol):
    """Protocol defining the required password hashing interface."""

    def hash(self, plain_password: str) -> str:
        """Hashes a plaintext password string."""
        ...

    def verify(self, plain_password: str, hashed_password: str) -> bool:
        """Verifies a plaintext password against a stored hash using constant-time comparison."""
        ...

    def needs_rehash(self, hashed_password: str) -> bool:
        """Returns True if the hash parameters or work factor should be upgraded."""
        ...


class BcryptPasswordHasher:
    """
    Production-ready password hasher using the bcrypt algorithm.

    Security Invariants:
        - Default cost factor: 12 rounds.
        - Verification is timing-safe via bcrypt.checkpw.
        - Plaintext passwords are never logged or stored.
    """

    def __init__(self, rounds: int = 12):
        if rounds < 4 or rounds > 31:
            raise ValueError("Bcrypt rounds must be between 4 and 31.")
        self.rounds = rounds

    def hash(self, plain_password: str) -> str:
        """Hashes plain_password using bcrypt with salt rounds."""
        if not plain_password:
            raise ValueError("Password cannot be empty.")
        salt = bcrypt.gensalt(rounds=self.rounds)
        hashed = bcrypt.hashpw(plain_password.encode("utf-8"), salt)
        return hashed.decode("utf-8")

    def verify(self, plain_password: str, hashed_password: str) -> bool:
        """Verifies plain_password against hashed_password."""
        if not plain_password or not hashed_password:
            return False
        try:
            return bcrypt.checkpw(
                plain_password.encode("utf-8"),
                hashed_password.encode("utf-8")
            )
        except Exception:
            return False

    def needs_rehash(self, hashed_password: str) -> bool:
        """
        Checks if the current hash work factor is lower than the configured rounds.
        Bcrypt hash format: $2b$<cost>$<22 character salt><31 character hash>
        """
        if not hashed_password or not hashed_password.startswith(("$2a$", "$2b$", "$2y$")):
            return True
        try:
            parts = hashed_password.split("$")
            if len(parts) >= 3:
                cost = int(parts[2])
                return cost < self.rounds
        except Exception:
            return True
        return False


def validate_password_policy(password: str) -> Tuple[bool, str]:
    """
    Validates that a password satisfies complexity requirements:
        - At least 12 characters
        - At least one uppercase letter
        - At least one lowercase letter
        - At least one numeric digit
        - At least one special symbol
    """
    if not password or len(password) < 12:
        return False, "Password must be at least 12 characters long."
    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter."
    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least one lowercase letter."
    if not re.search(r"\d", password):
        return False, "Password must contain at least one number."
    if not re.search(r"[^A-Za-z0-9]", password):
        return False, "Password must contain at least one special character."
    return True, "Password meets complexity requirements."


# Global default hasher instance
default_password_hasher = BcryptPasswordHasher(rounds=12)
