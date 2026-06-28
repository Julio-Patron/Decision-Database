"""
API Key management — create, rotate, revoke.

SQLite-backed. Each key has:
- A key ID (public identifier)
- The secret key (SHA-256 hashed in DB)
- A status: active / revoked
- Optional wallet address (for Solana Pay)
- Daily usage counter
"""

import hashlib
import logging
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ses.config import SES_DATA_DIR

logger = logging.getLogger(__name__)

KEY_PREFIX = "ses_"


@dataclass
class APIKey:
    id: str
    key_prefix: str
    status: str
    wallet_address: Optional[str]
    created_at: float
    last_used_at: Optional[float]
    daily_usage: int
    daily_usage_date: str = ""


class KeyStore:
    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or os.path.join(SES_DATA_DIR, "keys.db")
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        self._lock = threading.Lock()

        self._conn = sqlite3.connect(
            self._db_path,
            check_same_thread=False,
            isolation_level=None,
        )
        self._conn.execute("PRAGMA busy_timeout=5000")
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError as exc:
            logger.warning("SQLite WAL mode unavailable for key store at %s: %s", self._db_path, exc)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id TEXT PRIMARY KEY,
                key_hash TEXT NOT NULL UNIQUE,
                key_prefix TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                wallet_address TEXT,
                created_at REAL NOT NULL,
                last_used_at REAL,
                daily_usage INTEGER NOT NULL DEFAULT 0,
                daily_usage_date TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_key_hash ON api_keys(key_hash);
        """)

    @staticmethod
    def _hash_key(secret: str) -> str:
        return hashlib.sha256(secret.encode()).hexdigest()

    @staticmethod
    def generate_secret() -> str:
        return KEY_PREFIX + secrets.token_hex(24)

    def create_key(self, wallet_address: Optional[str] = None) -> tuple:
        """
        Create a new API key.
        Returns (key_id, secret) — the secret is shown once.
        """
        secret = self.generate_secret()
        key_hash = self._hash_key(secret)
        key_id = f"key_{secrets.token_hex(8)}"
        prefix = secret[:12]

        with self._lock:
            with self._conn:
                self._conn.execute(
                    """INSERT INTO api_keys (id, key_hash, key_prefix, status, wallet_address, created_at)
                       VALUES (?, ?, ?, 'active', ?, ?)""",
                    (key_id, key_hash, prefix, wallet_address, time.time()),
                )

        logger.info("Created API key %s (prefix: %s)", key_id, prefix)
        return key_id, secret

    def lookup(self, secret: str) -> Optional[APIKey]:
        """Find a key by its secret."""
        key_hash = self._hash_key(secret)
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM api_keys WHERE key_hash = ?", (key_hash,)
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return APIKey(
                id=row["id"],
                key_prefix=row["key_prefix"],
                status=row["status"],
                wallet_address=row["wallet_address"],
                created_at=row["created_at"],
                last_used_at=row["last_used_at"],
                daily_usage=row["daily_usage"],
                daily_usage_date=row["daily_usage_date"],
            )

    def validate(self, secret: str) -> Optional[str]:
        """
        Validate a key and return its key_id, or None if invalid/revoked.
        Also updates last_used_at and daily usage.
        """
        today = time.strftime("%Y-%m-%d")
        key_hash = self._hash_key(secret)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    """SELECT id, daily_usage_date FROM api_keys
                       WHERE key_hash = ? AND status = 'active'""",
                    (key_hash,),
                ).fetchone()
                if row is None:
                    self._conn.rollback()
                    return None
                if row["daily_usage_date"] != today:
                    self._conn.execute(
                        """UPDATE api_keys
                           SET daily_usage = 0, daily_usage_date = ?
                           WHERE id = ?""",
                        (today, row["id"]),
                    )
                self._conn.execute(
                    """UPDATE api_keys
                       SET last_used_at = ?, daily_usage = daily_usage + 1
                       WHERE id = ? AND status = 'active'""",
                    (time.time(), row["id"]),
                )
                self._conn.commit()
                return row["id"]
            except Exception:
                self._conn.rollback()
                raise

    def rotate(self, key_id: str) -> Optional[Tuple[str, str]]:
        """
        Replace an active key's secret while preserving its stable key ID.

        Returns ``(key_id, new_secret)``. The old secret becomes invalid as
        part of the same database transaction.
        """
        secret = self.generate_secret()
        key_hash = self._hash_key(secret)
        prefix = secret[:12]

        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._conn.execute(
                    """UPDATE api_keys SET key_hash = ?, key_prefix = ?
                       WHERE id = ? AND status = 'active'""",
                    (key_hash, prefix, key_id),
                )
                if cursor.rowcount != 1:
                    self._conn.rollback()
                    return None
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

        logger.info("Rotated API key %s (prefix: %s)", key_id, prefix)
        return key_id, secret

    def revoke(self, key_id: str) -> bool:
        with self._lock:
            with self._conn:
                cursor = self._conn.execute(
                    "UPDATE api_keys SET status = 'revoked' WHERE id = ? AND status = 'active'",
                    (key_id,),
                )
                return cursor.rowcount > 0

    def list_keys(self, status: Optional[str] = "active") -> List[APIKey]:
        with self._lock:
            if status:
                cursor = self._conn.execute(
                    "SELECT * FROM api_keys WHERE status = ? ORDER BY created_at DESC", (status,)
                )
            else:
                cursor = self._conn.execute(
                    "SELECT * FROM api_keys ORDER BY created_at DESC"
                )
            return [
                APIKey(
                    id=row["id"],
                    key_prefix=row["key_prefix"],
                    status=row["status"],
                    wallet_address=row["wallet_address"],
                    created_at=row["created_at"],
                    last_used_at=row["last_used_at"],
                    daily_usage=row["daily_usage"],
                    daily_usage_date=row["daily_usage_date"],
                )
                for row in cursor.fetchall()
            ]

    def get_by_id(self, key_id: str) -> Optional[APIKey]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM api_keys WHERE id = ?", (key_id,)
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return APIKey(
                id=row["id"],
                key_prefix=row["key_prefix"],
                status=row["status"],
                wallet_address=row["wallet_address"],
                created_at=row["created_at"],
                last_used_at=row["last_used_at"],
                daily_usage=row["daily_usage"],
                daily_usage_date=row["daily_usage_date"],
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
