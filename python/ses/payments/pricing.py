"""
Pricing table for SES Agentic Service.

Prices are defined in USDC.
"""

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class OperationCost:
    usdc: float
    description: str


PRICING: Dict[str, OperationCost] = {
    "search":         OperationCost(0.0001, "Vector search (top-5)"),
    "batch_search":   OperationCost(0.0005, "Batch search (10 queries)"),
    "ingest":         OperationCost(0.001,  "Document ingestion per file"),
    "ingest_text":    OperationCost(0.0002, "Inline text ingestion"),
    "delete":         OperationCost(0.0001, "Document deletion"),
    "stats":          OperationCost(0.0,    "Statistics (free)"),
    "embedding":      OperationCost(0.0001, "Standalone embedding"),
    "namespace":      OperationCost(0.01,   "New namespace creation"),
    "query":          OperationCost(0.001,  "Search + LLM answer"),
}


def cost_for(operation: str) -> float:
    return PRICING.get(operation, OperationCost(0.0, "")).usdc
