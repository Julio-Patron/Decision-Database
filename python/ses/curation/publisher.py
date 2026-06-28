import os
import json
import time
import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

from ses.config import SES_DATA_DIR, CURATION_REGISTRIES_DIR
from ses.curation.registry import SourceRegistry, load_all_registries
from ses.curation.fetchers import fetch_source, resolve_source_path
from ses.curation.normalize import normalize_markdown
from ses.curation.chunker import chunk_document, CurationChunk
from ses.curation.manifest import NamespaceManifest, save_manifest_to_file, load_manifest_from_file
from ses.curation.validators import validate_freshness, validate_quality
from ses.core.rag import OfflineRAGEngine

def get_namespace_build_dir(namespace: str) -> str:
    """
    Returns the output build directory for a namespace.
    """
    safe_ns = namespace.replace(":", "_")
    return os.path.join(SES_DATA_DIR, "curated_namespaces", safe_ns)

def build_namespace(
    registry: SourceRegistry,
    base_dir: Optional[str] = None,
    version: Optional[str] = None
) -> tuple[NamespaceManifest, List[CurationChunk]]:
    """
    Fetches, normalizes, and chunks the files in the registry.
    Generates and saves the NamespaceManifest and chunks to disk.
    """
    namespace = registry.namespace
    build_dir = get_namespace_build_dir(namespace)
    os.makedirs(build_dir, exist_ok=True)

    if not version:
        # Format: YYYY-MM-DD.1
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        version = f"{today}.1"

    source_hashes = {}
    all_chunks = []

    for source in registry.sources:
        # Fetch the file contents
        fetched = fetch_source(source, base_dir)
        source_hashes[source.path] = fetched.source_hash

        # Normalize the markdown into sections
        sections = normalize_markdown(fetched.content, source.citation_base)

        # Chunk the sections
        chunks = chunk_document(
            sections,
            doc_id=source.source_id,
            source_hash=fetched.source_hash
        )
        all_chunks.extend(chunks)

    # Create the manifest
    manifest = NamespaceManifest(
        namespace=namespace,
        version=version,
        publisher=registry.publisher or "SES",
        trust_level=registry.trust_level or "official",
        source_hashes=source_hashes,
        chunk_count=len(all_chunks),
        embedding_model="all-MiniLM-L6-v2",
        vector_store="qdrant",
        status="staging",
        created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        published_at=None
    )

    # Save manifest.json
    manifest_path = os.path.join(build_dir, "manifest.json")
    save_manifest_to_file(manifest, manifest_path)

    # Save chunks.json
    chunks_path = os.path.join(build_dir, "chunks.json")
    with open(chunks_path, "w", encoding="utf-8") as f:
        chunks_data = [c.model_dump() for c in all_chunks]
        json.dump(chunks_data, f, indent=2, ensure_ascii=False)

    return manifest, all_chunks

async def publish_namespace_async(
    namespace: str,
    base_dir: Optional[str] = None,
    engine: Optional[OfflineRAGEngine] = None
) -> Dict[str, Any]:
    """
    Loads build artifacts, validates freshness & quality, and indexes chunks into the RAG engine.
    """
    # 1. Load registry and manifest
    registries = load_all_registries()
    if namespace not in registries:
        raise ValueError(f"Namespace registry '{namespace}' not found in registry paths")
    registry = registries[namespace]

    build_dir = get_namespace_build_dir(namespace)
    manifest_path = os.path.join(build_dir, "manifest.json")
    chunks_path = os.path.join(build_dir, "chunks.json")

    if not os.path.exists(manifest_path) or not os.path.exists(chunks_path):
        raise FileNotFoundError(
            f"Build artifacts not found for namespace '{namespace}'. "
            f"Please run build first."
        )

    manifest = load_manifest_from_file(manifest_path)

    # Load chunks
    with open(chunks_path, "r", encoding="utf-8") as f:
        chunks_data = json.load(f)
    chunks = [CurationChunk.model_validate(c) for c in chunks_data]

    # 2. Run freshness validation (must match source files exactly)
    freshness = validate_freshness(registry, manifest, base_dir=base_dir, raise_on_error=True)
    if freshness < 1.0:
        raise ValueError("Freshness validation failed: Namespace is stale or newer unpublished files exist.")

    # 3. Run quality gate check
    validate_quality(chunks, namespace)

    # 4. Update status in manifest and save
    manifest.status = "published"
    manifest.published_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    save_manifest_to_file(manifest, manifest_path)

    # 5. Index into RAG engine
    if not engine:
        engine = OfflineRAGEngine()

    # Clean existing namespace in vector store
    # Wait, does the vector store support clearing or dropping a collection?
    # Let's check: offline_rag_engine has collections. Let's recreate or clear collection.
    # In routes.py, we had: `engine.clear_namespace = AsyncMock()` in mock.
    # Let's check if OfflineRAGEngine has clear_namespace or reset or similar method,
    # or if we can delete the collection and re-create it.
    # Let's check VECTOR_STORE implementation in ses/core/vector_store.py
    collection_name = engine._get_collection_name(namespace)
    if hasattr(engine.vector_store, "delete_collection"):
        try:
            await engine.vector_store.delete_collection(collection_name)
        except Exception:
            pass

    # Standardize documents to format expected by index_documents
    documents_to_index = []
    # Build a lookup table from registry for metadata enrichment
    source_by_id = {s.source_id: s for s in registry.sources}

    for chunk in chunks:
        source_title = "Untitled"
        source_path = ""
        if chunk.doc_id in source_by_id:
            src = source_by_id[chunk.doc_id]
            source_title = src.title
            source_path = src.path

        try:
            chunk_idx = int(chunk.chunk_id.split(":")[-1])
        except Exception:
            chunk_idx = 0

        documents_to_index.append({
            "id": chunk.chunk_id,
            "text": chunk.text,
            "metadata": {
                "parent_document_id": chunk.doc_id,
                "filename": source_title,
                "source_title": source_title,
                "source_path": source_path,
                "citation_anchor": chunk.citation_anchor,
                "source_hash": chunk.source_hash,
                "chunk_index": chunk_idx,
                "indexed_at": time.time()
            }
        })

    # Index using RAG Engine
    res = await engine.index_documents(namespace, documents_to_index)
    res["manifest"] = manifest.model_dump()
    return res

def publish_namespace(
    namespace: str,
    base_dir: Optional[str] = None,
    engine: Optional[OfflineRAGEngine] = None
) -> Dict[str, Any]:
    """
    Synchronous wrapper around publish_namespace_async.
    """
    return asyncio.run(publish_namespace_async(namespace, base_dir, engine))
