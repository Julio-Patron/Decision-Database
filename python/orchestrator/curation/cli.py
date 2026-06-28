import sys
import os
import argparse
import json
import asyncio
from typing import List, Optional

from orchestrator.curation.registry import load_all_registries
from orchestrator.curation.manifest import load_manifest_from_file
from orchestrator.curation.publisher import build_namespace, publish_namespace_async, get_namespace_build_dir
from orchestrator.curation.validators import validate_freshness, validate_quality
from orchestrator.curation.chunker import CurationChunk

def parse_curate_args(args: List[str]):
    parser = argparse.ArgumentParser(
        prog="orchestrator curate",
        description="Curate namespace CLI commands for System Tollgate"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # curate build <namespace> [--version <version>] [--base-dir <dir>]
    build_parser = subparsers.add_parser("build", help="Build a namespace from its registry definition")
    build_parser.add_argument("namespace", help="The namespace to build, e.g. public:ses-docs")
    build_parser.add_argument("--version", help="Optional version string, e.g. 2026-06-19.1. Defaults to date-based YYYY-MM-DD.1")
    build_parser.add_argument("--base-dir", help="Optional base directory to resolve relative source paths")

    # curate validate <namespace> [--base-dir <dir>]
    validate_parser = subparsers.add_parser("validate", help="Validate freshness and quality of built namespace")
    validate_parser.add_argument("namespace", help="The namespace to validate, e.g. public:ses-docs")
    validate_parser.add_argument("--base-dir", help="Optional base directory to resolve relative source paths")

    # curate publish <namespace> [--base-dir <dir>]
    publish_parser = subparsers.add_parser("publish", help="Publish built namespace to the vector store database")
    publish_parser.add_argument("namespace", help="The namespace to publish, e.g. public:ses-docs")
    publish_parser.add_argument("--base-dir", help="Optional base directory to resolve relative source paths")

    # curate inspect <namespace>
    inspect_parser = subparsers.add_parser("inspect", help="Inspect built namespace metadata and files")
    inspect_parser.add_argument("namespace", help="The namespace to inspect, e.g. public:ses-docs")

    return parser.parse_args(args)

def handle_build(namespace: str, version: Optional[str] = None, base_dir: Optional[str] = None) -> int:
    registries = load_all_registries()
    if namespace not in registries:
        print(f"Error: Namespace '{namespace}' not found in registry search paths.", file=sys.stderr)
        return 1

    registry = registries[namespace]
    print(f"Building namespace '{namespace}' from registry...")
    
    try:
        manifest, chunks = build_namespace(registry, base_dir=base_dir, version=version)
        print(f"Build succeeded for namespace '{namespace}':")
        print(f"  Version: {manifest.version}")
        print(f"  Chunks count: {manifest.chunk_count}")
        print(f"  Saved artifacts to: {get_namespace_build_dir(namespace)}")
        return 0
    except Exception as e:
        print(f"Error building namespace: {e}", file=sys.stderr)
        return 1

def handle_validate(namespace: str, base_dir: Optional[str] = None) -> int:
    registries = load_all_registries()
    if namespace not in registries:
        print(f"Error: Namespace '{namespace}' not found in registry search paths.", file=sys.stderr)
        return 1
    registry = registries[namespace]

    build_dir = get_namespace_build_dir(namespace)
    manifest_path = os.path.join(build_dir, "manifest.json")
    chunks_path = os.path.join(build_dir, "chunks.json")

    if not os.path.exists(manifest_path) or not os.path.exists(chunks_path):
        print(f"Error: Namespace '{namespace}' build artifacts not found. Please run 'orchestrator curate build {namespace}' first.", file=sys.stderr)
        return 1

    try:
        manifest = load_manifest_from_file(manifest_path)
        with open(chunks_path, "r", encoding="utf-8") as f:
            chunks_data = json.load(f)
        chunks = [CurationChunk.model_validate(c) for c in chunks_data]
    except Exception as e:
        print(f"Error loading build artifacts: {e}", file=sys.stderr)
        return 1

    # 1. Freshness check
    print("Running freshness validation...")
    try:
        validate_freshness(registry, manifest, base_dir=base_dir, raise_on_error=True)
        print("  [OK] Freshness validator passed (mtimes and hashes are up to date).")
    except Exception as e:
        print(f"  [FAIL] Freshness validator failed: {e}", file=sys.stderr)
        return 1

    # 2. Quality gate check
    print("Running quality gate check...")
    try:
        validate_quality(chunks, namespace)
        print("  [OK] Quality gate check passed (all chunks are valid).")
    except Exception as e:
        print(f"  [FAIL] Quality gate check failed: {e}", file=sys.stderr)
        return 1

    print(f"Namespace '{namespace}' is valid and ready to publish.")
    return 0

def handle_publish(namespace: str, base_dir: Optional[str] = None) -> int:
    print(f"Publishing namespace '{namespace}' to vector store...")
    try:
        res = asyncio.run(publish_namespace_async(namespace, base_dir=base_dir))
        manifest = res.get("manifest", {})
        print(f"Successfully published namespace '{namespace}':")
        print(f"  Version: {manifest.get('version')}")
        print(f"  Status: {manifest.get('status')}")
        print(f"  Indexed count: {res.get('indexed_count', 0)}")
        return 0
    except Exception as e:
        print(f"Error publishing namespace: {e}", file=sys.stderr)
        return 1

def handle_inspect(namespace: str) -> int:
    build_dir = get_namespace_build_dir(namespace)
    manifest_path = os.path.join(build_dir, "manifest.json")
    chunks_path = os.path.join(build_dir, "chunks.json")

    if not os.path.exists(manifest_path):
        print(f"Error: Build manifest not found for '{namespace}'. Please run build first.", file=sys.stderr)
        return 1

    try:
        manifest = load_manifest_from_file(manifest_path)
        chunks_count = 0
        if os.path.exists(chunks_path):
            with open(chunks_path, "r", encoding="utf-8") as f:
                chunks_count = len(json.load(f))
        
        print(f"Namespace Inspect: {namespace}")
        print(f"  Version:         {manifest.version}")
        print(f"  Status:          {manifest.status}")
        print(f"  Publisher:       {manifest.publisher}")
        print(f"  Trust Level:     {manifest.trust_level}")
        print(f"  Embedding Model: {manifest.embedding_model}")
        print(f"  Vector Store:    {manifest.vector_store}")
        print(f"  Created At:      {manifest.created_at}")
        print(f"  Published At:    {manifest.published_at}")
        print(f"  Chunk Count:     {chunks_count}")
        print("  Source Hashes:")
        for path, file_hash in manifest.source_hashes.items():
            print(f"    - {path}: {file_hash}")
        return 0
    except Exception as e:
        print(f"Error inspecting namespace: {e}", file=sys.stderr)
        return 1

def curate_cli(args: List[str]) -> int:
    parsed_args = parse_curate_args(args)
    if parsed_args.command == "build":
        return handle_build(
            namespace=parsed_args.namespace,
            version=parsed_args.version,
            base_dir=parsed_args.base_dir
        )
    elif parsed_args.command == "validate":
        return handle_validate(
            namespace=parsed_args.namespace,
            base_dir=parsed_args.base_dir
        )
    elif parsed_args.command == "publish":
        return handle_publish(
            namespace=parsed_args.namespace,
            base_dir=parsed_args.base_dir
        )
    elif parsed_args.command == "inspect":
        return handle_inspect(
            namespace=parsed_args.namespace
        )
    return 0
