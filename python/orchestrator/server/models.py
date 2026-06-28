from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str
    namespace: Optional[str] = None
    top_k: int = 5
    threshold: float = 0.0


class Citation(BaseModel):
    chunk_id: str
    doc_id: str
    source_title: str
    source_path: str
    citation_anchor: Optional[str] = None
    source_hash: Optional[str] = None
    score: float
    freshness_score: float = 1.0
    quality_score: float = 1.0


class Receipt(BaseModel):
    receipt_id: str
    operation: str
    amount_paid: str
    asset: str = "USDC"
    network: str = "solana"
    tx_signature: str
    request_hash: str
    response_hash: str
    created_at: str


class ValueProof(BaseModel):
    namespace: str
    namespace_version: str = "1.0.0"
    freshness_score: float = 1.0
    retrieved_chunks: int
    source_trust: str = "verified"
    estimated_steps_saved: int = 8
    infrastructure_avoided: List[str] = Field(default_factory=lambda: ["vector_database", "llm_hosting"])
    reason_to_pay: str = "curated agent memory"
    price_paid: str


class NamespaceRef(BaseModel):
    id: str
    version: str = "1.0.0"
    type: str = "public"


class UsageInfo(BaseModel):
    retrieved_chunks: int
    processing_time_ms: float


class SearchResponse(BaseModel):
    results: List[Citation]
    total_documents: int
    receipt: Optional[Receipt] = None
    value_proof: Optional[ValueProof] = None
    namespace: Optional[NamespaceRef] = None
    usage: Optional[UsageInfo] = None


class IngestRequest(BaseModel):
    namespace: Optional[str] = None
    filename: str = "document.txt"
    content: str
    metadata: Dict[str, Any] = {}


class QueryRequest(BaseModel):
    question: str
    namespace: Optional[str] = None
    top_k: int = 5


class QueryResponse(BaseModel):
    answer: str
    citations: List[Citation]
    receipt: Optional[Receipt] = None
    value_proof: Optional[ValueProof] = None
    namespace: Optional[NamespaceRef] = None
    usage: Optional[UsageInfo] = None


class MerchantInfo(BaseModel):
    address: Optional[str]
    token: str = "USDC"
    mint: str
    network: str = "solana"


class KeyCreateResponse(BaseModel):
    status: str
    key_id: str
    secret: str
    warning: str


class KeyListResponse(BaseModel):
    keys: List[Dict[str, Any]]


class ErrorResponse(BaseModel):
    error: str
    code: str


class HealthResponse(BaseModel):
    status: str
    version: str = "2.0.0"
    mode: str
    total_documents: int = 0
