from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Union

import numpy as np

from ses.config import QDRANT_URL, QDRANT_API_KEY, VECTOR_SIZE, SES_DATA_DIR, EMBEDDED_MODE

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lightweight data types (used by both Qdrant and Lite backends)
# ---------------------------------------------------------------------------

@dataclass
class ScoredPoint:
    id: str
    score: float
    payload: Dict[str, Any]
    vector: Optional[List[float]] = None


@dataclass
class PointStruct:
    id: str
    vector: List[float]
    payload: Dict[str, Any]


@dataclass
class CollectionInfo:
    points_count: int
    config: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Qdrant backend (existing, unchanged in interface)
# ---------------------------------------------------------------------------

if not EMBEDDED_MODE:
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.http import models as qmodels


class QdrantVectorStore:
    def __init__(self, url: Optional[str] = None, api_key: Optional[str] = None, max_cache_size: int = 1000) -> None:
        self._url = url or QDRANT_URL
        self._api_key = api_key or QDRANT_API_KEY

        self.client = AsyncQdrantClient(url=self._url, api_key=self._api_key)
        self._collections_cache = OrderedDict()
        self._max_cache_size = max_cache_size

    def _add_to_cache(self, collection_name: str) -> None:
        self._collections_cache[collection_name] = True
        self._collections_cache.move_to_end(collection_name)
        if len(self._collections_cache) > self._max_cache_size:
            self._collections_cache.popitem(last=False)

    async def ensure_collection(self, collection_name: str) -> None:
        if collection_name in self._collections_cache:
            self._collections_cache.move_to_end(collection_name)
            return

        exists = False
        try:
            await self.client.get_collection(collection_name)
            exists = True
            self._add_to_cache(collection_name)
        except Exception:
            exists = False

        if not exists:
            try:
                await self.client.create_collection(
                    collection_name=collection_name,
                    vectors_config=qmodels.VectorParams(
                        size=VECTOR_SIZE,
                        distance=qmodels.Distance.COSINE,
                    ),
                )
                self._add_to_cache(collection_name)
            except Exception as exc:
                error_str = str(exc).lower()
                if "already exists" in error_str or "conflict" in error_str:
                    self._add_to_cache(collection_name)
                    logger.info("Collection %s already exists (race condition handled)", collection_name)
                else:
                    logger.error("Error creating collection: %s", exc, exc_info=True)

    async def upsert_points(self, collection_name: str, points: Union[List[Any], Any]) -> None:
        # If it's already a Batch or a list, pass it directly.
        # If it's a generator/iterable (and not Batch/list), convert to list.
        if not isinstance(points, (list, qmodels.Batch)):
            points = list(points)
            
        converted_points = []
        for p in points:
            if hasattr(p, "vector") and hasattr(p, "id") and hasattr(p, "payload") and not isinstance(p, dict) and not isinstance(p, qmodels.PointStruct):
                converted_points.append(qmodels.PointStruct(
                    id=p.id,
                    vector=p.vector,
                    payload=p.payload
                ))
            else:
                converted_points.append(p)

        await self.client.upsert(collection_name=collection_name, points=converted_points)

    async def search(
        self,
        collection_name: str,
        query_vector: List[float],
        limit: int,
        score_threshold: float,
        with_payload: qmodels.PayloadSelectorExclude,
        with_vectors: bool,
    ):
        res = await self.client.query_points(
            collection_name=collection_name,
            query=query_vector,
            limit=limit,
            score_threshold=score_threshold,
            with_payload=with_payload,
            with_vectors=with_vectors,
        )
        return res.points

    async def search_batch(self, collection_name: str, requests: List[qmodels.SearchRequest]):
        res = await self.client.query_batch_points(collection_name=collection_name, requests=requests)
        return [r.points for r in res]

    async def retrieve(self, collection_name: str, ids: List[str], with_payload: bool):
        return await self.client.retrieve(
            collection_name=collection_name,
            ids=ids,
            with_payload=with_payload,
        )

    async def delete(self, collection_name: str, points: qmodels.PointIdsList) -> None:
        await self.client.delete(collection_name=collection_name, points_selector=points)

    async def delete_by_payload(self, collection_name: str, key: str, value: str) -> None:
        await self.client.delete(
            collection_name=collection_name,
            points_selector=qmodels.FilterSelector(
                filter=qmodels.Filter(
                    must=[
                        qmodels.FieldCondition(
                            key=key,
                            match=qmodels.MatchValue(value=value),
                        )
                    ]
                )
            ),
        )

    async def delete_collection(self, collection_name: str) -> None:
        await self.client.delete_collection(collection_name)
        if collection_name in self._collections_cache:
            del self._collections_cache[collection_name]

    async def get_collection(self, collection_name: str):
        return await self.client.get_collection(collection_name)

    async def clear_cache(self) -> None:
        self._collections_cache.clear()


# ---------------------------------------------------------------------------
# Lite backend — SQLite + numpy, zero external services
# ---------------------------------------------------------------------------

class LiteVectorStore:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or os.path.join(SES_DATA_DIR, "vectors.db")
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        self._lock = threading.Lock()

        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError as exc:
            logger.warning("SQLite WAL mode unavailable for vector store at %s: %s", self._db_path, exc)
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.row_factory = sqlite3.Row

        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS collections (
                name TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                vector_size INTEGER NOT NULL DEFAULT 384
            );
            CREATE TABLE IF NOT EXISTS points (
                id TEXT NOT NULL,
                collection TEXT NOT NULL,
                vector TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                indexed_at REAL NOT NULL,
                PRIMARY KEY (id, collection)
            );
            CREATE INDEX IF NOT EXISTS idx_points_collection ON points(collection);
        """)
        self._conn.commit()

    def _get_db(self) -> sqlite3.Connection:
        return self._conn

    async def ensure_collection(self, collection_name: str) -> None:
        with self._lock:
            self._get_db().execute(
                "INSERT OR IGNORE INTO collections (name, created_at) VALUES (?, ?)",
                (collection_name, time.time()),
            )
            self._get_db().commit()

    async def upsert_points(
        self, collection_name: str, points: Union[List[PointStruct], List[Dict]]
    ) -> None:
        now = time.time()
        with self._lock:
            for p in points:
                if isinstance(p, dict):
                    pid = str(p["id"])
                    pvec = json.dumps(p["vector"])
                    ppayload = json.dumps(p["payload"])
                else:
                    pid = str(p.id)
                    pvec = json.dumps(p.vector)
                    ppayload = json.dumps(p.payload)

                self._get_db().execute(
                    """INSERT OR REPLACE INTO points (id, collection, vector, payload, indexed_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (pid, collection_name, pvec, ppayload, now),
                )
            self._get_db().commit()

    async def search(
        self,
        collection_name: str,
        query_vector: List[float],
        limit: int,
        score_threshold: float,
        with_payload: Optional[Any] = None,
        with_vectors: bool = False,
    ) -> List[ScoredPoint]:
        query_np = np.array(query_vector, dtype=np.float32)
        q_norm = np.linalg.norm(query_np)
        if q_norm == 0:
            return []

        # Determine payload filtering: if with_payload is a PayloadSelectorExclude,
        # we store the excluded keys locally.
        exclude_keys: set = set()
        if with_payload is not None:
            cls_name = type(with_payload).__name__
            if "Exclude" in cls_name:
                exclude_keys = set(getattr(with_payload, "exclude", []))
            elif hasattr(with_payload, "exclude"):
                exclude_keys = set(with_payload.exclude)

        with self._lock:
            cursor = self._get_db().execute(
                "SELECT id, vector, payload FROM points WHERE collection = ?",
                (collection_name,),
            )
            rows = cursor.fetchall()

        results: List[ScoredPoint] = []
        for row in rows:
            vec = np.array(json.loads(row["vector"]), dtype=np.float32)
            v_norm = np.linalg.norm(vec)
            if v_norm == 0:
                continue
            score = float(np.dot(vec, query_np) / (q_norm * v_norm))
            if score < score_threshold:
                continue

            payload = json.loads(row["payload"])
            if exclude_keys:
                payload = {k: v for k, v in payload.items() if k not in exclude_keys}

            results.append(
                ScoredPoint(
                    id=row["id"],
                    score=score,
                    payload=payload,
                    vector=vec.tolist() if with_vectors else None,
                )
            )

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:limit]

    async def search_batch(
        self, collection_name: str, requests: List[Any]
    ) -> List[List[ScoredPoint]]:
        # requests is a list of objects with .vector, .limit, .score_threshold
        output: List[List[ScoredPoint]] = []
        for req in requests:
            vec = req.vector if hasattr(req, "vector") else req
            limit = getattr(req, "limit", 10)
            threshold = getattr(req, "score_threshold", 0.0)
            res = await self.search(
                collection_name,
                query_vector=vec,
                limit=limit,
                score_threshold=threshold,
            )
            output.append(res)
        return output

    async def retrieve(
        self, collection_name: str, ids: List[str], with_payload: bool = True
    ) -> List[ScoredPoint]:
        if not ids:
            return []
        with self._lock:
            rows = []
            for point_id in ids:
                cursor = self._get_db().execute(
                    "SELECT id, vector, payload FROM points WHERE collection = ? AND id = ?",
                    (collection_name, point_id),
                )
                row = cursor.fetchone()
                if row is not None:
                    rows.append(row)

        results = []
        for row in rows:
            payload = json.loads(row["payload"]) if with_payload else {}
            results.append(
                ScoredPoint(
                    id=row["id"],
                    score=0.0,
                    payload=payload,
                )
            )
        return results

    async def delete(
        self, collection_name: str, points: Any
    ) -> None:
        ids: List[str] = []
        if hasattr(points, "points"):
            ids = [str(p) for p in points.points]
        elif isinstance(points, list):
            ids = [str(p) for p in points]
        else:
            ids = [str(points)]

        with self._lock:
            self._get_db().executemany(
                "DELETE FROM points WHERE collection = ? AND id = ?",
                [(collection_name, point_id) for point_id in ids],
            )
            self._get_db().commit()

    async def delete_by_payload(
        self, collection_name: str, key: str, value: str
    ) -> None:
        with self._lock:
            cursor = self._get_db().execute(
                "SELECT id, payload FROM points WHERE collection = ?",
                (collection_name,),
            )
            to_delete = []
            for row in cursor.fetchall():
                payload = json.loads(row["payload"])
                if str(payload.get(key, "")).startswith(value):
                    to_delete.append(row["id"])

            if to_delete:
                self._get_db().executemany(
                    "DELETE FROM points WHERE collection = ? AND id = ?",
                    [(collection_name, point_id) for point_id in to_delete],
                )
            self._get_db().commit()

    async def delete_collection(self, collection_name: str) -> None:
        with self._lock:
            self._get_db().execute("DELETE FROM points WHERE collection = ?", (collection_name,))
            self._get_db().execute("DELETE FROM collections WHERE name = ?", (collection_name,))
            self._get_db().commit()

    async def get_collection(self, collection_name: str) -> CollectionInfo:
        with self._lock:
            cursor = self._get_db().execute(
                "SELECT COUNT(*) as cnt FROM points WHERE collection = ?",
                (collection_name,),
            )
            row = cursor.fetchone()
            count = row["cnt"] if row else 0
        return CollectionInfo(points_count=count)

    async def scroll(
        self, collection_name: str, limit: int = 20, with_payload: bool = True, with_vectors: bool = False
    ) -> tuple:
        with self._lock:
            cursor = self._get_db().execute(
                "SELECT id, vector, payload FROM points WHERE collection = ? ORDER BY indexed_at DESC LIMIT ?",
                (collection_name, limit),
            )
            rows = cursor.fetchall()

        points = []
        for row in rows:
            payload = json.loads(row["payload"]) if with_payload else {}
            points.append(
                ScoredPoint(
                    id=row["id"],
                    score=0.0,
                    payload=payload,
                    vector=json.loads(row["vector"]) if with_vectors else None,
                )
            )
        return (points, None)

    def clear_cache(self) -> None:
        pass
