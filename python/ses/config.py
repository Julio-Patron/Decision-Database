import os
import json

# --- Environment & Debug ---
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

# --- Embedded Mode (zero external dependencies) ---
EMBEDDED_MODE = os.getenv("EMBEDDED_MODE", "true").lower() == "true"
SES_DATA_DIR = os.getenv("SES_DATA_DIR", os.path.join(os.getcwd(), "ses_data"))
CURATION_REGISTRIES_DIR = os.getenv("CURATION_REGISTRIES_DIR", os.path.join(SES_DATA_DIR, "registries"))

# --- Embedding Model ---
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
EMBEDDING_DEVICE = os.getenv("EMBEDDING_DEVICE", "cpu")
VECTOR_SIZE = 384

# --- Chunking ---
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1000"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "200"))

# --- Qdrant ---
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", None)

# --- Redis ---
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

# --- Metadata ---
ALLOWED_METADATA_KEYS = {"source", "author", "date", "tags", "category", "title", "url"}
EXCLUDED_METADATA_KEYS = {"text_snippet", "full_text", "original_id"}

# --- Persistence ---
MOUNT_MANIFEST_PATH = os.getenv("MOUNT_MANIFEST_PATH", "data/mount_manifest.json")

# --- SES Personal / Watcher ---
WATCH_DIRECTORIES = [d.strip() for d in os.getenv("WATCH_DIRECTORIES", "").split(",")] if os.getenv("WATCH_DIRECTORIES") else []
PERSONAL_NAMESPACE = os.getenv("PERSONAL_NAMESPACE", "personal_default")
DEBOUNCE_SECONDS = float(os.getenv("DEBOUNCE_SECONDS", "2.0"))

# --- Local LLM ---
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")

# --- Namespace Quotas (Fase 1) ---
MAX_DOCS_PER_NAMESPACE = int(os.getenv("MAX_DOCS_PER_NAMESPACE", "10000"))
MAX_CHUNKS_PER_NAMESPACE = int(os.getenv("MAX_CHUNKS_PER_NAMESPACE", "50000"))


def validate_config() -> None:
    """Call once at engine startup to verify the environment is sane."""
    if EMBEDDED_MODE:
        return
    if DEBUG:
        logger = __import__("logging").getLogger(__name__)
        logger.warning("DEBUG mode active - Qdrant/Redis credentials not checked.")
        return
    qdrant_is_local = any(host in QDRANT_URL for host in ("localhost", "127.0.0.1", "qdrant"))
    redis_is_local = REDIS_HOST in {"localhost", "127.0.0.1", "redis"}
    if not QDRANT_API_KEY and not qdrant_is_local:
        raise ValueError(
            "CRITICAL: QDRANT_API_KEY is mandatory in production (non-embedded mode). "
            "Set EMBEDDED_MODE=true to run without Qdrant, or DEBUG=true for local dev."
        )
    if not REDIS_PASSWORD and not redis_is_local:
        raise ValueError(
            "CRITICAL: REDIS_PASSWORD is mandatory in production (non-embedded mode). "
            "Set EMBEDDED_MODE=true to run without Redis."
        )
