"""
Credit ledger — SQLite-backed balance tracking for the System Agentic Service.

Used server-side to track prepaid credits per API key.
Thread-safe, atomic deductions.
"""

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from orchestrator.config import DEBUG, DATA_DIR

logger = logging.getLogger(__name__)

TX_PURCHASE = "purchase"
TX_DEDUCTION = "deduction"
TX_REFUND = "refund"
TX_BONUS = "bonus"


@dataclass
class Transaction:
    id: int
    api_key: str
    tx_type: str
    amount: int
    balance_after: int
    operation: str
    created_at: float
    metadata: Dict = field(default_factory=dict)


class ReceiptLedger:
    def __init__(
        self,
        db_path: Optional[str] = None,
        require_purchase_transaction: Optional[bool] = None,
    ):
        self._db_path = db_path or os.path.join(DATA_DIR, "receipts.db")
        self._require_purchase_transaction = (
            not DEBUG
            if require_purchase_transaction is None
            else require_purchase_transaction
        )
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
            logger.warning("SQLite WAL mode unavailable for receipts ledger at %s: %s", self._db_path, exc)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                api_key TEXT PRIMARY KEY,
                balance INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                daily_usage TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                api_key TEXT NOT NULL,
                tx_type TEXT NOT NULL,
                amount INTEGER NOT NULL,
                balance_after INTEGER NOT NULL,
                operation TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}',
                tx_signature TEXT,
                idempotency_key TEXT,
                request_hash TEXT,
                response_payload TEXT,
                status TEXT DEFAULT 'success'
            );
            CREATE INDEX IF NOT EXISTS idx_tx_api_key ON transactions(api_key);
            CREATE INDEX IF NOT EXISTS idx_tx_created ON transactions(created_at);
        """)
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(transactions)").fetchall()
        }
        if "tx_signature" not in columns:
            self._conn.execute("ALTER TABLE transactions ADD COLUMN tx_signature TEXT")
        if "idempotency_key" not in columns:
            self._conn.execute("ALTER TABLE transactions ADD COLUMN idempotency_key TEXT")
        if "request_hash" not in columns:
            self._conn.execute("ALTER TABLE transactions ADD COLUMN request_hash TEXT")
        if "response_payload" not in columns:
            self._conn.execute("ALTER TABLE transactions ADD COLUMN response_payload TEXT")
        if "status" not in columns:
            self._conn.execute("ALTER TABLE transactions ADD COLUMN status TEXT DEFAULT 'success'")
        self._conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_tx_signature_unique
               ON transactions(tx_signature) WHERE tx_signature IS NOT NULL"""
        )

    def _get_account(self, api_key: str) -> Optional[sqlite3.Row]:
        cursor = self._conn.execute(
            "SELECT * FROM accounts WHERE api_key = ?", (api_key,)
        )
        return cursor.fetchone()

    def _ensure_account(self, api_key: str) -> None:
        self._conn.execute(
            """INSERT OR IGNORE INTO accounts (api_key, balance, created_at)
               VALUES (?, 0, ?)""",
            (api_key, time.time()),
        )

    def reserve_receipt(self, identity: str, amount: float, operation: str, tx_signature: str, idempotency_key: str, request_hash: str) -> Optional[str]:
        """Reserves a receipt based on idempotency_key.
        Returns 'reserved' if success, or the JSON response payload if already successful.
        Returns 'spent_other' if tx_signature is spent for a different request."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_account(identity)

                previous = self._conn.execute(
                    "SELECT idempotency_key, request_hash, status, response_payload FROM transactions WHERE tx_signature = ?",
                    (tx_signature,)
                ).fetchone()
                if previous is not None:
                    if previous["status"] == "failed":
                        self._conn.execute(
                            "DELETE FROM transactions WHERE tx_signature = ? AND status = 'failed'",
                            (tx_signature,),
                        )
                    elif previous["idempotency_key"] == idempotency_key and previous["request_hash"] == request_hash:
                        self._conn.rollback()
                        if previous["status"] == "success":
                            return previous["response_payload"]
                        if previous["status"] == "reserved":
                            return "spent_other"
                    else:
                        self._conn.rollback()
                        return "spent_other"

                current_balance = self._get_account(identity)["balance"]

                # Delete any previous failed attempt with this tx_signature
                self._conn.execute("DELETE FROM transactions WHERE tx_signature = ? AND status = 'failed'", (tx_signature,))

                self._conn.execute(
                    """INSERT INTO transactions
                       (api_key, tx_type, amount, balance_after, operation, created_at, tx_signature, idempotency_key, request_hash, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'reserved')""",
                    (identity, "purchase", amount, current_balance, operation, time.time(), tx_signature, idempotency_key, request_hash),
                )
                self._conn.commit()
                return "reserved"
            except Exception:
                self._conn.rollback()
                raise

    def commit_receipt(self, tx_signature: str, response_payload: str) -> bool:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "UPDATE transactions SET status = 'success', response_payload = ? WHERE tx_signature = ?",
                    (response_payload, tx_signature)
                )
                self._conn.commit()
                return True
            except Exception:
                self._conn.rollback()
                raise

    def mark_failed(self, tx_signature: str) -> bool:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "UPDATE transactions SET status = 'failed' WHERE tx_signature = ?",
                    (tx_signature,)
                )
                self._conn.commit()
                return True
            except Exception:
                self._conn.rollback()
                raise

    def get_receipt(self, tx_signature: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT operation, amount, tx_signature, status, created_at FROM transactions WHERE tx_signature = ?",
                (tx_signature,)
            ).fetchone()
            if row:
                return dict(row)
            return None

    def close(self) -> None:
        with self._lock:
            self._conn.close()
