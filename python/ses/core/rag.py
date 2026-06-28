import asyncio
import copy
import functools
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from typing import Any, BinaryIO, Dict, List, Optional

import numpy as np
from sentence_transformers import SentenceTransformer

from . import parsers
from .chunking import chunk_text
from .vector_store import VECTOR_SIZE, QdrantVectorStore, LiteVectorStore, PointStruct, ScoredPoint

from ses.config import (
    REDIS_HOST,
    REDIS_PORT,
    REDIS_PASSWORD,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
    EMBEDDED_MODE,
    MAX_DOCS_PER_NAMESPACE,
    MAX_CHUNKS_PER_NAMESPACE,
    validate_config,
)

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DEVICE = os.getenv("EMBEDDING_DEVICE", "cpu")

UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
HEX32_PATTERN = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)

EXCLUDED_METADATA_KEYS = {"text_snippet", "full_text", "original_id"}

RUST_AVAILABLE = False
try:
    import jas_vector_core

    RUST_AVAILABLE = True
    logger.info("Rust Core active")
except ImportError:
    logger.warning("Rust Core not found, using Python fallback")

# Only import Qdrant types when not in embedded mode
if not EMBEDDED_MODE:
    from qdrant_client.http import models as qmodels

# Conditional Redis import (embedded mode uses in-memory fallback)
_redis_available = False
redis = None
if not EMBEDDED_MODE:
    try:
        import redis.asyncio as redis
        _redis_available = True
    except ImportError:
        logger.warning("redis-py no disponible, usando fallback en memoria")


def ttl_cache(seconds: int = 60, maxsize: int = 128, copy_func=copy.deepcopy):
    def decorator(func):
        cache = OrderedDict()
        lock = threading.Lock()

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = (
                (args[1:], tuple(sorted(kwargs.items())))
                if args
                else (tuple(), tuple(sorted(kwargs.items())))
            )

            with lock:
                if key in cache:
                    timestamp, val = cache[key]
                    if time.monotonic() - timestamp < seconds:
                        cache.move_to_end(key)
                        return copy_func(val)
                    del cache[key]

            val = func(*args, **kwargs)
            now = time.monotonic()

            with lock:
                cache[key] = (now, val)
                if len(cache) > maxsize:
                    cache.popitem(last=False)

            return copy_func(val)

        return wrapper

    return decorator


def async_ttl_cache(seconds: int = 60, maxsize: int = 128, copy_func=copy.deepcopy):
    def decorator(func):
        cache = OrderedDict()
        lock = asyncio.Lock()

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            key = (
                (args[1:], tuple(sorted(kwargs.items())))
                if args
                else (tuple(), tuple(sorted(kwargs.items())))
            )

            async with lock:
                if key in cache:
                    timestamp, val = cache[key]
                    if time.monotonic() - timestamp < seconds:
                        cache.move_to_end(key)
                        return copy_func(val)
                    del cache[key]

            val = await func(*args, **kwargs)
            now = time.monotonic()

            async with lock:
                cache[key] = (now, val)
                if len(cache) > maxsize:
                    cache.popitem(last=False)

            return copy_func(val)

        return wrapper

    return decorator


class _HashEmbeddingModel:
    """Small deterministic embedding fallback used when local model files are unavailable."""

    def __init__(self, vector_size: int):
        self.vector_size = vector_size

    def encode(self, sentences):
        single_input = isinstance(sentences, str)
        inputs = [sentences] if single_input else list(sentences)
        vectors = [self._encode_one(text) for text in inputs]
        encoded = np.array(vectors, dtype=np.float32)
        return encoded[0] if single_input else encoded

    def _encode_one(self, text: str) -> np.ndarray:
        vector = np.zeros(self.vector_size, dtype=np.float32)
        tokens = re.findall(r"\w+", str(text).lower())
        if not tokens:
            tokens = [str(text)]
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.vector_size
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = np.linalg.norm(vector)
        if norm:
            vector /= norm
        return vector


class OfflineRAGEngine:
    def __init__(self, embedded: Optional[bool] = None):
        self.embedded = EMBEDDED_MODE if embedded is None else embedded
        if not self.embedded:
            validate_config()
        logger.info(
            "Initializing OfflineRAGEngine (mode: %s)",
            "EMBEDDED" if self.embedded else "QDRANT",
        )

        logger.info(
            "Loading embedding model %s on device: %s",
            EMBEDDING_MODEL,
            EMBEDDING_DEVICE,
        )
        try:
            self.model = SentenceTransformer(EMBEDDING_MODEL, device=EMBEDDING_DEVICE)
        except Exception as exc:
            if os.getenv("SES_ALLOW_HASH_EMBEDDINGS", "true").lower() != "true":
                raise
            logger.warning(
                "Embedding model %s could not be loaded (%s). "
                "Using deterministic hash embeddings for local validation.",
                EMBEDDING_MODEL,
                exc,
            )
            self.model = _HashEmbeddingModel(VECTOR_SIZE)

        self.model_name = EMBEDDING_MODEL
        self.embedding_dim = VECTOR_SIZE
        if self.embedded:
            self.vector_store = LiteVectorStore()
            self.qdrant = None
            self.redis = None
            self._usage: Dict[str, Dict[str, float]] = {}
            self._history: Dict[str, list] = {}
        else:
            self.vector_store = QdrantVectorStore()
            self.qdrant = self.vector_store.client
            if redis is not None:
                self.redis = redis.Redis(
                    host=REDIS_HOST,
                    port=REDIS_PORT,
                    password=REDIS_PASSWORD,
                    decode_responses=True,
                )
            else:
                self.redis = None
                self._usage: Dict[str, Dict[str, float]] = {}
                self._history: Dict[str, list] = {}

    def _get_collection_name(self, namespace: str) -> str:
        """Get the sanitized Qdrant collection name for a namespace."""
        safe_namespace = namespace.replace(":", "_")
        return f"client_{safe_namespace}"

    async def _get_points_count(self, collection_name: str) -> int:
        """Return the number of points in *collection_name*, 0 on error."""
        try:
            info = await self.vector_store.get_collection(collection_name)
            return info.points_count or 0
        except Exception:
            return 0

    def _to_uuid(self, id_str: str) -> str:
        if isinstance(id_str, uuid.UUID):
            return str(id_str)

        s_id = str(id_str)
        length = len(s_id)

        if length == 36 and UUID_PATTERN.match(s_id):
            return s_id

        if length == 32 and HEX32_PATTERN.match(s_id):
            return s_id

        if length in (36, 38, 45):
            try:
                uuid.UUID(s_id)
                return s_id
            except ValueError:
                pass

        return str(uuid.uuid5(uuid.NAMESPACE_DNS, s_id))

    async def ingest_file(
        self, namespace: str, file_obj: BinaryIO, filename: str, metadata: Dict
    ) -> Dict[str, Any]:
        collection_name = self._get_collection_name(namespace)
        await self.vector_store.ensure_collection(collection_name)

        # Basic quota enforcement (Fase 1)
        current = await self._get_points_count(collection_name)
        if current >= MAX_CHUNKS_PER_NAMESPACE:
            return {
                "status": "error",
                "message": f"Namespace quota exceeded (max chunks: {MAX_CHUNKS_PER_NAMESPACE})",
                "current_chunks": current,
            }

        t_extract_start = time.time()
        if hasattr(file_obj, "seek"):
            file_obj.seek(0)

        text_content = await asyncio.to_thread(
            parsers.extract_text_content, file_obj, filename
        )
        await asyncio.to_thread(
            logger.info,
            "Text extraction for %s took %.4fs",
            filename,
            time.time() - t_extract_start,
        )

        # --- Chunking ---
        chunks = chunk_text(text_content, CHUNK_SIZE, CHUNK_OVERLAP)
        if not chunks:
            return {
                "status": "error",
                "message": f"No text content could be extracted from {filename}",
            }

        logger.info(
            "Document %s split into %d chunks (size=%d, overlap=%d)",
            filename, len(chunks), CHUNK_SIZE, CHUNK_OVERLAP,
        )

        start_time = time.time()

        # Batch encode all chunks at once for efficiency
        vectors = await asyncio.to_thread(
            lambda: self.model.encode(chunks).tolist()
        )

        # Metadata filtering
        safe_metadata = {
            k: v for k, v in metadata.items() if k not in EXCLUDED_METADATA_KEYS
        }
        parent_doc_id = self._file_parent_id(filename, text_content, safe_metadata)

        points = []
        for idx, (chunk, vector) in enumerate(zip(chunks, vectors)):
            chunk_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{parent_doc_id}:{idx}"))
            payload = {
                **safe_metadata,
                "filename": filename,
                "parent_document_id": parent_doc_id,
                "chunk_index": idx,
                "text_snippet": chunk[:500],
                "full_text": chunk,
                "indexed_at": time.time(),
            }
            points.append(
                PointStruct(
                    id=chunk_id,
                    vector=vector,
                    payload=payload,
                )
            )

        await self.vector_store.upsert_points(
            collection_name=collection_name,
            points=points,
        )

        return {
            "status": "success",
            "document_id": parent_doc_id,
            "file_type": filename.split(".")[-1],
            "content_length": len(text_content),
            "chunks_count": len(chunks),
            "processing_time": time.time() - start_time,
        }

    def _file_parent_id(self, filename: str, text_content: str, metadata: Dict[str, Any]) -> str:
        source_path = metadata.get("source_path") or metadata.get("abs_path")
        content_hash = metadata.get("content_hash")

        if source_path and content_hash:
            identity = f"{source_path}:{content_hash}"
        else:
            text_digest = hashlib.sha256(text_content.encode("utf-8", errors="ignore")).hexdigest()
            identity = f"{filename}:{text_digest}"

        return str(uuid.uuid5(uuid.NAMESPACE_URL, identity))

    async def index_documents(
        self, namespace: str, documents: List[Dict]
    ) -> Dict[str, Any]:
        collection_name = self._get_collection_name(namespace)
        await self.vector_store.ensure_collection(collection_name)

        texts = [doc["text"] for doc in documents]
        vectors = await asyncio.to_thread(lambda: self.model.encode(texts).tolist())

        points = []
        for i, doc in enumerate(documents):
            original_id = doc["id"]
            final_id = self._to_uuid(original_id)

            # Metadata filtering
            metadata = doc.get("metadata", {})
            safe_metadata = {
                k: v for k, v in metadata.items() if k not in EXCLUDED_METADATA_KEYS
            }

            payload = {
                **safe_metadata,
                "original_id": original_id,
                "text_snippet": doc["text"][:500],
                "full_text": doc["text"],
                "indexed_at": time.time(),
            }

            points.append(
                PointStruct(id=final_id, vector=vectors[i], payload=payload)
            )

        if points:
            await self.vector_store.upsert_points(
                collection_name=collection_name, points=points
            )

        return {
            "indexed_count": len(points),
            "total_documents": await self._get_points_count(collection_name),
            "namespace": namespace,
        }

    @functools.lru_cache(maxsize=1024)
    def _encode_query_cached(self, query: str):
        return self.model.encode(query)

    def _get_intelligent_snippet(
        self, text: str, query: str, context: int = 150
    ) -> str:
        """Extrae un fragmento de texto que contiene la consulta para mejorar la relevancia visual."""
        if not query:
            return text[: context * 2]

        # Búsqueda simple de palabras clave
        query_words = query.lower().split()
        first_word = query_words[0] if query_words else ""

        idx = text.lower().find(first_word)
        if idx == -1:
            return text[: context * 2]

        start = max(0, idx - context)
        end = min(len(text), idx + context)

        snippet = text[start:end]
        if start > 0:
            snippet = "..." + snippet
        if end < len(text):
            snippet = snippet + "..."

        return snippet

    def _format_search_result(self, point, query: str = "") -> Dict[str, Any]:
        payload = point.payload
        doc_id = payload.get("original_id", str(point.id))
        full_text = payload.get("full_text", "")

        text_snippet = (
            self._get_intelligent_snippet(full_text, query)
            if full_text
            else payload.get("text_snippet", "")
        )

        # Use a dictionary comprehension for filtering metadata to avoid mutation
        metadata = {k: v for k, v in payload.items() if k not in EXCLUDED_METADATA_KEYS}

        return {
            "id": doc_id,
            "score": point.score,
            "text": text_snippet,
            "text_snippet": text_snippet,
            "metadata": metadata,
            "indexed_at": payload.get("indexed_at", 0),
        }

    async def _record_query(self, namespace: str, query: str, doc_ids: List[str]):
        """Registra la consulta y actualiza contadores de uso para el ranking."""
        try:
            if self.redis is not None:
                await self.redis.lpush(
                    f"history:{namespace}", json.dumps({"q": query, "ts": time.time()})
                )
                await self.redis.ltrim(f"history:{namespace}", 0, 99)
                for d_id in doc_ids:
                    await self.redis.zincrby(f"usage:{namespace}", 1, d_id)
            else:
                # In-memory fallback
                if namespace not in self._history:
                    self._history[namespace] = []
                self._history[namespace].insert(0, {"q": query, "ts": time.time()})
                self._history[namespace] = self._history[namespace][:100]

                if namespace not in self._usage:
                    self._usage[namespace] = {}
                for d_id in doc_ids:
                    self._usage[namespace][d_id] = self._usage[namespace].get(d_id, 0) + 1
        except Exception as e:
            logger.warning(f"Error registrando consulta: {e}")

    async def _get_usage_scores(
        self, namespace: str, doc_ids: List[str]
    ) -> Dict[str, float]:
        """Obtiene las frecuencias de uso para normalizarlas en el ranking."""
        try:
            if self.redis is not None:
                scores = {}
                for d_id in doc_ids:
                    s = await self.redis.zscore(f"usage:{namespace}", d_id)
                    scores[d_id] = float(s) if s else 0.0
                return scores
            else:
                usage = self._usage.get(namespace, {})
                return {d_id: float(usage.get(d_id, 0)) for d_id in doc_ids}
        except Exception:
            return {d_id: 0.0 for d_id in doc_ids}

    def re_rank(
        self, results: List[Dict], usage_scores: Dict[str, float]
    ) -> List[Dict]:
        """
        Ranking mejorado: combina similitud vectorial, frecuencia de uso y recencia.
        Score final = (vector_sim * 0.7) + (normalized_usage * 0.2) + (recency_boost * 0.1)
        """
        if not results:
            return []

        max_usage = max(usage_scores.values()) if usage_scores.values() else 1.0
        if max_usage == 0:
            max_usage = 1.0

        now = time.time()

        for res in results:
            v_score = res["score"]
            u_score = usage_scores.get(res["id"], 0.0) / max_usage

            # Recency boost (documentos de las últimas 24h tienen un pequeño empujón)
            age_days = (now - res.get("indexed_at", 0)) / 86400
            recency_boost = 1.0 / (1.0 + age_days)  # Decay function

            res["score"] = (v_score * 0.7) + (u_score * 0.2) + (recency_boost * 0.1)

        return sorted(results, key=lambda x: x["score"], reverse=True)

    async def search(
        self, namespace: str, query: str, top_k: int = 5, threshold: float = 0.0
    ) -> Dict[str, Any]:
        collection_name = self._get_collection_name(namespace)
        t0 = time.time()

        query_embedding = await asyncio.to_thread(self._encode_query_cached, query)

        if isinstance(query_embedding, np.ndarray):
            query_vector_np = query_embedding.astype(np.float32, copy=False)
            query_vector = query_vector_np.tolist()
        else:
            query_vector = (
                query_embedding.tolist()
                if hasattr(query_embedding, "tolist")
                else query_embedding
            )
            query_vector_np = np.array(query_vector, dtype=np.float32)

        try:
            if self.embedded:
                payload_filter = None  # LiteStore handles exclusion natively
            else:
                payload_filter = qmodels.PayloadSelectorExclude(exclude=["full_text"])

            search_result = await self.vector_store.search(
                collection_name=collection_name,
                query_vector=query_vector,
                limit=top_k,
                score_threshold=threshold,
                with_payload=payload_filter,
                with_vectors=True if RUST_AVAILABLE else False,
            )
        except Exception as e:
            logger.exception("Error during Qdrant search:")
            return {"results": [], "total_documents": 0, "processing_time_ms": 0}

        final_results_order = []

        if RUST_AVAILABLE and len(search_result) > 50:
            try:
                q_vec_np = query_vector_np
                # Offload CPU-bound numpy array creation to a thread to avoid blocking the event loop
                c_vecs_np = await asyncio.to_thread(
                    lambda: np.array(
                        [point.vector for point in search_result], dtype=np.float32
                    )
                )

                reranked_indices = await asyncio.to_thread(
                    jas_vector_core.cosine_similarity_search,
                    q_vec_np,
                    c_vecs_np,
                    len(search_result),
                )

                for idx, new_score in reranked_indices:
                    point = search_result[idx]
                    point.score = float(new_score)
                    final_results_order.append(point)

            except Exception as exc:
                logger.error(
                    "Error en Rust Acceleration: %s. Usando orden original.",
                    exc,
                    exc_info=True,
                )
                final_results_order = search_result
        else:
            final_results_order = search_result

        results = [
            self._format_search_result(point, query) for point in final_results_order
        ]

        # 4. Re-Ranking Híbrido (Cognitivo)
        usage_scores = await self._get_usage_scores(
            namespace, [r["id"] for r in results]
        )
        reranked_results = self.re_rank(results, usage_scores)

        # 5. Registrar consulta (Background)
        asyncio.create_task(
            self._record_query(
                namespace, query, [r["id"] for r in reranked_results[:3]]
            )
        )

        return {
            "results": reranked_results[:top_k],
            "total_documents": await self._get_points_count(collection_name),
            "processing_time_ms": (time.time() - t0) * 1000,
        }

    async def batch_search(
        self, namespace: str, queries: List[str], top_k: int, threshold: float
    ) -> Dict[str, Any]:
        collection_name = self._get_collection_name(namespace)
        t0 = time.time()

        query_vectors = await asyncio.to_thread(
            lambda: self.model.encode(queries).tolist()
        )

        if self.embedded:
            search_queries = [
                type("SearchReq", (), {"vector": vec, "limit": top_k, "score_threshold": threshold})()
                for vec in query_vectors
            ]
        else:
            search_queries = [
                qmodels.SearchRequest(
                    vector=vec,
                    limit=top_k,
                    score_threshold=threshold,
                    with_payload=qmodels.PayloadSelectorExclude(exclude=["full_text"]),
                )
                for vec in query_vectors
            ]

        try:
            batch_results = await self.vector_store.search_batch(
                collection_name=collection_name, requests=search_queries
            )
        except Exception:
            return {
                "results": [[] for _ in queries],
                "total_documents": 0,
                "processing_time_ms": 0,
            }

        formatter = self._format_search_result
        formatted_results = [
            [formatter(point) for point in result_group]
            for result_group in batch_results
        ]

        return {
            "results": formatted_results,
            "total_documents": await self._get_points_count(collection_name),
            "processing_time_ms": (time.time() - t0) * 1000,
        }

    async def delete_document(self, namespace: str, doc_id: str) -> bool:
        collection_name = self._get_collection_name(namespace)
        final_id = self._to_uuid(doc_id)

        try:
            await self.vector_store.delete_by_payload(
                collection_name=collection_name,
                key="parent_document_id",
                value=str(doc_id),
            )
            if self.embedded:
                await self.vector_store.delete(
                    collection_name=collection_name,
                    points=[final_id],
                )
            else:
                await self.vector_store.delete(
                    collection_name=collection_name,
                    points=qmodels.PointIdsList(points=[final_id]),
                )
            return True
        except Exception:
            return False

    async def clear_namespace(self, namespace: str) -> None:
        collection_name = self._get_collection_name(namespace)
        try:
            await self.vector_store.delete_collection(collection_name)
        except Exception:
            pass

    @async_ttl_cache(seconds=60, copy_func=copy.copy)
    async def get_stats(self, namespace: str) -> Dict[str, Any]:
        collection_name = self._get_collection_name(namespace)
        try:
            info = await self.vector_store.get_collection(collection_name)
            chunks = info.points_count or 0
            return {
                "total_documents": chunks,   # proxy (chunks)
                "chunks": chunks,
                "embedding_dimension": VECTOR_SIZE,
                "rust_acceleration": RUST_AVAILABLE,
                "storage": "Embedded (SQLite)" if self.embedded else "Qdrant Persistent",
                "quota": {
                    "max_chunks": MAX_CHUNKS_PER_NAMESPACE,
                    "usage_pct": round(min(100, (chunks / MAX_CHUNKS_PER_NAMESPACE) * 100), 1) if MAX_CHUNKS_PER_NAMESPACE else 0,
                },
            }
        except Exception:
            return {
                "total_documents": 0,
                "embedding_dimension": VECTOR_SIZE,
                "rust_acceleration": RUST_AVAILABLE,
                "quota": {"max_chunks": MAX_CHUNKS_PER_NAMESPACE, "usage_pct": 0},
            }

    async def get_document(
        self, namespace: str, doc_id: str
    ) -> Optional[Dict[str, Any]]:
        collection_name = self._get_collection_name(namespace)

        ids_to_try = []
        try:
            uuid.UUID(doc_id)
            ids_to_try.append(doc_id)
        except ValueError:
            pass
        hashed_id = self._to_uuid(doc_id)
        if hashed_id not in ids_to_try:
            ids_to_try.append(hashed_id)

        try:
            points = await self.vector_store.retrieve(
                collection_name=collection_name,
                ids=ids_to_try,
                with_payload=True,
            )
            if points:
                p = points[0]
                original_id = p.payload.get("original_id", str(p.id))
                return {
                    "id": original_id,
                    "text": p.payload.get("full_text", ""),
                    "metadata": p.payload,
                    "indexed_at": p.payload.get("indexed_at"),
                }
        except Exception:
            pass
        return None

    async def list_documents(
        self, namespace: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Lista los documentos más recientes indexados"""
        collection_name = self._get_collection_name(namespace)
        try:
            if self.embedded:
                result = await self.vector_store.scroll(
                    collection_name=collection_name,
                    limit=limit,
                    with_payload=True,
                    with_vectors=False,
                )
            else:
                result = await self.qdrant.scroll(
                    collection_name=collection_name,
                    limit=limit,
                    with_payload=True,
                    with_vectors=False,
                )
            points = result[0]

            docs = []
            for p in points:
                docs.append(
                    {
                        "id": p.payload.get("original_id", str(p.id)),
                        "text_snippet": p.payload.get("text_snippet", ""),
                        "metadata": {
                            k: v
                            for k, v in p.payload.items()
                            if k not in EXCLUDED_METADATA_KEYS
                        },
                        "indexed_at": p.payload.get("indexed_at"),
                    }
                )
            return docs
        except Exception:
            return []


_service_instance = None
_service_lock = threading.Lock()


def get_vector_service() -> OfflineRAGEngine:
    global _service_instance
    if _service_instance is None:
        with _service_lock:
            if _service_instance is None:
                _service_instance = OfflineRAGEngine()
    return _service_instance
