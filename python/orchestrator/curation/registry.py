import os
import yaml
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field, ValidationError
from orchestrator.config import CURATION_REGISTRIES_DIR

BUILTIN_REGISTRIES_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "registries"
)

class SourceModel(BaseModel):
    source_id: str
    type: str
    path: str
    title: str
    citation_base: str

class SourceRegistry(BaseModel):
    namespace: str
    publisher: Optional[str] = None
    trust_level: Optional[str] = None
    update_policy: Optional[str] = None
    sources: List[SourceModel] = Field(default_factory=list)

    @property
    def id(self) -> str:
        return self.namespace

def get_registry_paths() -> List[str]:
    """
    Returns the search paths for registries.
    First is the built-in directory, second is the custom directory configured by CURATION_REGISTRIES_DIR.
    """
    paths = [BUILTIN_REGISTRIES_DIR]
    if CURATION_REGISTRIES_DIR:
        paths.append(CURATION_REGISTRIES_DIR)
        
    # Deduplicate while preserving order
    seen = set()
    deduped = []
    for p in paths:
        abs_p = os.path.abspath(p)
        if abs_p not in seen:
            seen.add(abs_p)
            deduped.append(p)
    return deduped

def load_registry_from_file(filepath: str) -> SourceRegistry:
    """
    Loads a single registry from a YAML file.
    Raises FileNotFoundError, ValueError, yaml.YAMLError, or ValidationError.
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Registry file not found: {filepath}")
        
    with open(filepath, "r", encoding="utf-8") as f:
        # yaml.safe_load raises yaml.YAMLError on syntax errors
        data = yaml.safe_load(f)
        
    if data is None:
        raise ValueError(f"Registry file {filepath} is empty")
        
    if not isinstance(data, dict):
        raise ValueError(f"Registry file {filepath} must contain a YAML mapping (dict)")
        
    return SourceRegistry.model_validate(data)

def load_registries_from_dir(directory: str) -> Dict[str, SourceRegistry]:
    """
    Loads all registries from a directory.
    Returns a dictionary mapping namespace to SourceRegistry.
    """
    registries: Dict[str, SourceRegistry] = {}
    if not os.path.isdir(directory):
        return registries
        
    for filename in os.listdir(directory):
        if filename.endswith((".yaml", ".yml")):
            filepath = os.path.join(directory, filename)
            registry = load_registry_from_file(filepath)
            registries[registry.namespace] = registry
            
    return registries

def load_all_registries() -> Dict[str, SourceRegistry]:
    """
    Loads all registries from the directories returned by get_registry_paths().
    Registries loaded from later paths override registries with the same namespace loaded from earlier paths.
    """
    all_registries: Dict[str, SourceRegistry] = {}
    for path in get_registry_paths():
        if os.path.isdir(path):
            regs = load_registries_from_dir(path)
            for ns, reg in regs.items():
                all_registries[ns] = reg
    return all_registries
