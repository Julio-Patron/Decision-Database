from pydantic import BaseModel, Field
from typing import Dict, Any, Optional

class RuleDefinitionSchema(BaseModel):
    rule_name: str
    rule_version: str = "1.0.0"
    rule_tags: list[str] = Field(default_factory=list)
    logic: Dict[str, Any]

class BridgeInputSchema(BaseModel):
    rule: RuleDefinitionSchema
    context: Dict[str, Any]

class BridgeOutputSchema(BaseModel):
    success: bool
    result: Optional[Any] = None
    explain_tree: Optional[Dict[str, Any]] = None
    logic_snapshot: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
