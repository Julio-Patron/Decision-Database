import os
import json
from typing import Dict, Optional
from pydantic import BaseModel, Field
from datetime import datetime, timezone

class NamespaceManifest(BaseModel):
    namespace: str
    version: str
    publisher: Optional[str] = None
    trust_level: Optional[str] = None
    source_hashes: Dict[str, str] = Field(default_factory=dict)
    chunk_count: int = 0
    embedding_model: str = "all-MiniLM-L6-v2"
    vector_store: str = "qdrant"
    status: str = "staging"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    published_at: Optional[str] = None

def load_manifest_from_file(filepath: str) -> NamespaceManifest:
    """
    Loads a namespace manifest from a JSON file.
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Manifest file not found: {filepath}")
        
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    return NamespaceManifest.model_validate(data)

def save_manifest_to_file(manifest: NamespaceManifest, filepath: str) -> None:
    """
    Saves a namespace manifest to a JSON file.
    """
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(manifest.model_dump(), f, indent=2, ensure_ascii=False)
