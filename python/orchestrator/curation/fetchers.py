import os
import hashlib
import stat
import unicodedata
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from pydantic import BaseModel
from orchestrator.curation.registry import SourceModel

class FetchedSource(BaseModel):
    source_id: str
    content: str
    source_hash: str
    fetched_at: str

def find_git_root(start_dir: Optional[str] = None) -> str:
    if not start_dir:
        start_dir = os.getcwd()
    current = os.path.abspath(start_dir)
    while True:
        if os.path.isdir(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            raise FileNotFoundError("Git repository root not found (traversed up to root)")
        current = parent

def resolve_source_path(path: str, source_type: str, base_dir: Optional[str] = None) -> str:
    if "\x00" in path:
        raise ValueError("Path traversal detected: null byte in path")
    if os.path.isabs(path):
        resolved_path = os.path.abspath(path)
    elif source_type == "repo_file":
        git_root = find_git_root()
        resolved_path = os.path.abspath(os.path.join(git_root, path))
    elif source_type in ("local_file", "markdown_file"):
        base = base_dir or os.getcwd()
        resolved_path = os.path.abspath(os.path.join(base, path))
    else:
        raise ValueError(f"Unsupported source type: {source_type}")
        
    real_path = os.path.realpath(resolved_path)
    
    if source_type == "repo_file":
        git_root = find_git_root()
        real_git_root = os.path.realpath(git_root)
        rel = os.path.relpath(real_path, real_git_root)
        if rel.startswith("..") or os.path.isabs(rel):
            raise ValueError(f"Path traversal detected: {path} is outside of repository root")
    elif source_type in ("local_file", "markdown_file"):
        # Only enforce base directory check if base_dir was explicitly passed OR if the path is relative!
        is_relative = not os.path.isabs(path)
        if base_dir is not None or is_relative:
            base = base_dir or os.getcwd()
            real_base = os.path.realpath(base)
            rel = os.path.relpath(real_path, real_base)
            if rel.startswith("..") or os.path.isabs(rel):
                raise ValueError(f"Path traversal detected: {path} is outside of base directory")
            
    return real_path

def fetch_source(source: SourceModel, base_dir: Optional[str] = None) -> FetchedSource:
    if source.type not in ("local_file", "markdown_file", "repo_file"):
        raise ValueError(f"Unsupported source type: {source.type}")
        
    resolved_path = resolve_source_path(source.path, source.type, base_dir)
    
    if not os.path.exists(resolved_path):
        raise FileNotFoundError(f"Source file not found at: {resolved_path}")

    mode = os.stat(resolved_path).st_mode
    if not mode & (stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH):
        raise PermissionError(f"Source file is not readable: {resolved_path}")
        
    with open(resolved_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    # Apply NFC normalization to file contents after reading and before computing hashes and chunking
    content = unicodedata.normalize('NFC', content)
        
    # Normalize line endings to ensure platform independence
    normalized_content = content.replace("\r\n", "\n")
    
    # Generate SHA-256 hash formatted as a hex string with sha256: prefix
    sha256_hash = hashlib.sha256(normalized_content.encode("utf-8")).hexdigest()
    source_hash = f"sha256:{sha256_hash}"
    
    fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    
    return FetchedSource(
        source_id=source.source_id,
        content=normalized_content,
        source_hash=source_hash,
        fetched_at=fetched_at
    )
