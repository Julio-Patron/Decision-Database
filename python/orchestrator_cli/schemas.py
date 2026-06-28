from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List

class RuleDefinition(BaseModel):
    name: str
    logic: Dict[str, Any]
    version: Optional[str] = "1.0.0"
    description: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    required_context_keys: List[str] = Field(default_factory=list)

class DecisionInput(BaseModel):
    rule: RuleDefinition
    context: Dict[str, Any]

class DecisionContext(BaseModel):
    success: bool
    result: Optional[Any] = None
    explain_tree: Optional[Dict[str, Any]] = None
    logic_snapshot: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
