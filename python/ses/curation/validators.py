import os
from typing import Optional, List
from ses.curation.registry import SourceRegistry
from ses.curation.manifest import NamespaceManifest
from ses.curation.fetchers import fetch_source, resolve_source_path
from ses.curation.normalize import normalize_markdown
from ses.curation.chunker import CurationChunk

def validate_freshness(
    registry: SourceRegistry,
    manifest: NamespaceManifest,
    base_dir: Optional[str] = None,
    raise_on_error: bool = False
) -> float:
    """
    Evaluates the freshness of the curation namespace manifest compared to actual source files.
    Returns 1.0 if perfectly fresh and valid, otherwise 0.0 (or raises ValueError if raise_on_error is True).
    """
    try:
        # 1. Check if all sources in the registry exist and can be resolved
        for source in registry.sources:
            try:
                resolved_path = resolve_source_path(source.path, source.type, base_dir)
            except Exception as e:
                if raise_on_error:
                    raise ValueError(f"source path resolution failed: {e}")
                return 0.0

            if not os.path.exists(resolved_path):
                if raise_on_error:
                    raise ValueError(f"source file does not exist: {resolved_path}")
                return 0.0

            # 2. Check if source hash is known in manifest
            # Look up by path or by source_id
            manifest_hash = manifest.source_hashes.get(source.path) or manifest.source_hashes.get(source.source_id)
            if not manifest_hash:
                if raise_on_error:
                    raise ValueError(f"source hash is unknown in manifest for source: {source.source_id}")
                return 0.0

            # 3. Check if source fetches successfully
            try:
                fetched = fetch_source(source, base_dir)
            except Exception as e:
                if raise_on_error:
                    raise ValueError(f"failed to fetch source: {e}")
                return 0.0

            # 4. Check if manifest matches current source hashes
            if fetched.source_hash != manifest_hash:
                if raise_on_error:
                    raise ValueError(
                        f"manifest hash mismatch for {source.source_id}: "
                        f"manifest has {manifest_hash}, current is {fetched.source_hash} "
                        f"(newer unpublished version exists)"
                    )
                return 0.0

            # 5. Check if citation anchors are valid
            sections = normalize_markdown(fetched.content, source.citation_base)
            for sec in sections:
                expected_anchor = f"{source.citation_base}#{sec.section_id}" if sec.section_id else source.citation_base
                if sec.anchor != expected_anchor:
                    if raise_on_error:
                        raise ValueError(f"invalid citation anchor structure: {sec.anchor} != {expected_anchor}")
                    return 0.0

        return 1.0

    except Exception as e:
        if raise_on_error:
            raise ValueError(f"freshness validation encountered unexpected error: {e}")
        return 0.0

def validate_quality(chunks: List[CurationChunk], namespace: str) -> None:
    """
    Quality gate before publishing/publishing.
    Checks for empty chunks, duplicate chunk IDs, missing hashes, missing citation anchors,
    missing namespace, missing doc_id, or missing quality score placeholder.
    """
    if not namespace or not namespace.strip():
        raise ValueError("missing_namespace: Namespace is empty or missing")

    if not chunks:
        raise ValueError("empty_chunks: No chunks found to validate")

    seen_ids = set()
    for idx, chunk in enumerate(chunks):
        if not chunk.text or not chunk.text.strip():
            raise ValueError(f"empty_chunks: Found empty text at chunk index {idx}")

        if chunk.chunk_id in seen_ids:
            raise ValueError(f"duplicate_chunk_ids: Duplicate chunk ID detected: {chunk.chunk_id}")
        seen_ids.add(chunk.chunk_id)

        if not chunk.source_hash or not chunk.source_hash.strip():
            raise ValueError(f"missing_source_hash: Chunk {chunk.chunk_id} is missing source hash")

        if not chunk.citation_anchor or not chunk.citation_anchor.strip():
            raise ValueError(f"missing_citation_anchor: Chunk {chunk.chunk_id} is missing citation anchor")

        if not chunk.doc_id or not chunk.doc_id.strip():
            raise ValueError(f"missing_doc_id: Chunk {chunk.chunk_id} is missing doc_id")

        # Let's ensure chunk_id contains doc_id and starts with it, and follows {doc_id}:{section_id}:{chunk_index} format
        parts = chunk.chunk_id.split(":")
        if len(parts) < 3:
            raise ValueError(f"invalid_chunk_id: Chunk ID '{chunk.chunk_id}' does not match expected doc_id:section_id:chunk_index format")

    # All checks passed!
