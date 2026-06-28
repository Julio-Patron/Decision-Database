import io
import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from orchestrator.core.rag import OfflineRAGEngine
from orchestrator.core.llm import LocalLLMProvider
from orchestrator.payments.receipts import ReceiptLedger
from orchestrator.payments.keys import KeyStore
from orchestrator.payments.pricing import PRICING
from orchestrator.payments.solana_pay import SolanaPay
from orchestrator.config import DEBUG
from orchestrator.discovery.card import get_agent_card

from .models import (
    Citation,
    HealthResponse,
    IngestRequest,
    NamespaceRef,
    QueryRequest,
    QueryResponse,
    Receipt,
    SearchRequest,
    SearchResponse,
    UsageInfo,
    ValueProof,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1")


def _engine(request: Request) -> OfflineRAGEngine:
    if getattr(request.app.state, "engine", None) is None:
        request.app.state.engine = OfflineRAGEngine()
    return request.app.state.engine


def _ledger(request: Request) -> ReceiptLedger:
    if not hasattr(request.app.state, "ledger"):
        request.app.state.ledger = ReceiptLedger()
    return request.app.state.ledger


def _keystore(request: Request) -> KeyStore:
    if not hasattr(request.app.state, "keystore"):
        request.app.state.keystore = KeyStore()
    return request.app.state.keystore


def _solana(request: Request) -> SolanaPay:
    if not hasattr(request.app.state, "solana"):
        request.app.state.solana = SolanaPay()
    return request.app.state.solana


def _inject_receipt(request: Request, data: Dict[str, Any]) -> Dict[str, Any]:
    receipt = getattr(request.state, "receipt", None)
    if receipt:
        data["receipt"] = receipt
    return data


def _namespace(request: Request, requested: Optional[str] = None) -> str:
    """Scope every logical namespace to the authenticated API key or allow public namespaces."""
    requested_str = (requested or "default").strip() or "default"
    if requested_str.startswith("public:") or requested_str.startswith("premium:"):
        return requested_str

    owner = getattr(request.state, "namespace", "public")
    suffix = hashlib.sha256(requested_str.encode("utf-8")).hexdigest()[:12]
    return f"{owner}_{suffix}"


def map_to_citation(res: Dict[str, Any]) -> Citation:
    metadata = res.get("metadata", {}) or {}

    indexed_at = res.get("indexed_at") or metadata.get("indexed_at") or time.time()
    if isinstance(indexed_at, str):
        try:
            import datetime
            dt = datetime.datetime.fromisoformat(indexed_at.replace("Z", "+00:00"))
            indexed_at = dt.timestamp()
        except ValueError:
            indexed_at = time.time()

    age_days = (time.time() - indexed_at) / 86400
    freshness_score = 1.0 / (1.0 + max(0.0, age_days))

    score = res.get("score", 0.0)
    quality_score = min(1.0, max(0.0, score)) if score > 0 else 0.95

    chunk_id = res.get("id") or metadata.get("original_id") or "unknown_chunk"
    doc_id = metadata.get("parent_document_id") or metadata.get("original_id") or chunk_id

    source_title = metadata.get("filename") or metadata.get("source_title") or "Untitled Document"
    source_path = metadata.get("source_path") or metadata.get("abs_path") or source_title

    chunk_index = metadata.get("chunk_index", 0)
    citation_anchor = metadata.get("citation_anchor") or f"chunk_{chunk_index}"
    source_hash = metadata.get("content_hash") or metadata.get("source_hash") or ""

    return Citation(
        chunk_id=str(chunk_id),
        doc_id=str(doc_id),
        source_title=str(source_title),
        source_path=str(source_path),
        citation_anchor=str(citation_anchor),
        source_hash=str(source_hash),
        score=float(score),
        freshness_score=float(freshness_score),
        quality_score=float(quality_score)
    )


@router.post(
    "/quote",
    tags=["Pricing & Payments"],
    summary="Obtener cotización de precio",
    description="Devuelve el precio en USDC para una operación soportada. Incluye dirección del merchant, mint de USDC y red (devnet/mainnet-beta) cuando Solana Pay está configurado.",
)
async def get_quote(request: Request):
    body = await request.json()
    operation = body.get("operation")
    if operation not in PRICING:
        return JSONResponse(status_code=400, content={"error": f"Unknown operation: {operation}"})

    price = str(PRICING[operation].usdc)

    # Enrich with dynamic Solana Pay info (address, mint, cluster) after the hardening
    solana = _solana(request)
    info = solana.get_merchant_info() if solana else {}

    quote = {
        "operation": operation,
        "price": price,
        "asset": "USDC",
        "pay_to": info.get("address"),
        "mint": info.get("mint"),
        "network": info.get("network", "solana"),
        "cluster": info.get("network"),
    }
    # Clean None values for cleaner responses
    quote = {k: v for k, v in quote.items() if v is not None}
    return quote

@router.get(
    "/receipt/{receipt_id}",
    tags=["Pricing & Payments"],
    summary="Obtener recibo por ID",
    description="Recupera un recibo previamente emitido (idempotencia y auditoría de pagos).",
)
async def get_receipt(receipt_id: str, request: Request):
    ledger = _ledger(request)
    receipt = ledger.get_receipt(receipt_id)
    if not receipt:
        return JSONResponse(status_code=404, content={"error": "Receipt not found"})
    return {"receipt": receipt}

# ── Health ──

@router.get(
    "/health",
    response_model=HealthResponse,
    tags=["System"],
    summary="Health check del servicio",
    description="Verifica que el motor RAG está operativo y reporta modo (embedded / qdrant).",
)
async def health(request: Request):
    eng = _engine(request)
    stats = await eng.get_stats("health")
    return HealthResponse(
        status="ok",
        mode="embedded" if eng.embedded else "qdrant",
        total_documents=stats.get("total_documents", 0),
    )

# ── Discovery ──

@router.get(
    "/capabilities",
    tags=["Discovery"],
    summary="Capacidades del servicio",
    description="Lista las capacidades principales soportadas por el gateway (búsqueda, RAG, ingest, etc).",
)
async def capabilities(request: Request):
    return {
        "capabilities": [
            "semantic_search",
            "rag_query",
            "document_ingest",
            "citation_retrieval"
        ]
    }

@router.get(
    "/pricing",
    tags=["Pricing & Payments"],
    summary="Lista de precios",
    description="Devuelve todos los precios en USDC por operación soportada.",
)
async def pricing(request: Request):
    return {
        k: {"usdc": v.usdc, "description": v.description}
        for k, v in PRICING.items()
    }




# ── API Key management ──

@router.post(
    "/keys",
    tags=["API Keys"],
    summary="Crear nueva API Key",
    description="Genera una API key nueva. El secret solo se muestra una vez.",
)
async def create_api_key(request: Request):
    keystore = _keystore(request)
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    wallet = body.get("wallet_address") if isinstance(body, dict) else None
    key_id, secret = keystore.create_key(wallet_address=wallet)
    return {
        "status": "ok",
        "key_id": key_id,
        "secret": secret,
        "warning": "Save this secret — it will not be shown again",
    }


@router.get(
    "/keys",
    tags=["API Keys"],
    summary="Listar API Keys asociadas",
    description="Lista las API keys del propietario actual (solo la propia).",
)
async def list_api_keys(request: Request):
    keystore = _keystore(request)
    key = keystore.get_by_id(request.state.key_id)
    keys = [key] if key is not None else []
    return {
        "keys": [
            {
                "id": k.id,
                "prefix": k.key_prefix,
                "status": k.status,
                "wallet": k.wallet_address,
                "created_at": k.created_at,
                "last_used": k.last_used_at,
                "daily_usage": k.daily_usage,
            }
            for k in keys
        ]
    }


@router.delete(
    "/keys/{key_id}",
    tags=["API Keys"],
    summary="Revocar API Key",
    description="Revoca la API key indicada (solo puedes revocar tu propia key).",
)
async def revoke_api_key(key_id: str, request: Request):
    if key_id != request.state.key_id:
        return JSONResponse(
            status_code=403,
            content={"error": "A key may only revoke itself", "code": "forbidden_key"},
        )
    keystore = _keystore(request)
    ok = keystore.revoke(key_id)
    if not ok:
        return JSONResponse(status_code=404, content={"error": "Key not found or already revoked"})
    return {"status": "ok", "key_id": key_id, "action": "revoked"}


@router.put(
    "/keys/{key_id}/rotate",
    tags=["API Keys"],
    summary="Rotar API Key",
    description="Genera un nuevo secret para la key. El anterior queda invalidado inmediatamente.",
)
async def rotate_api_key(key_id: str, request: Request):
    if key_id != request.state.key_id:
        return JSONResponse(
            status_code=403,
            content={"error": "A key may only rotate itself", "code": "forbidden_key"},
        )
    rotated = _keystore(request).rotate(key_id)
    if rotated is None:
        return JSONResponse(
            status_code=404,
            content={"error": "Key not found or revoked", "code": "key_not_rotatable"},
        )
    _, secret = rotated
    return {
        "status": "ok",
        "key_id": key_id,
        "secret": secret,
        "warning": "Save this secret — the previous secret is now invalid",
    }




# ── RAG operations ──

@router.post(
    "/search",
    response_model=SearchResponse,
    tags=["RAG Operations"],
    summary="Búsqueda semántica vectorial",
    description="Busca fragmentos relevantes en el namespace. Devuelve citas + opcionalmente receipt + value_proof (si se pagó).",
)
async def search(body: SearchRequest, request: Request):
    t0 = time.time()
    eng = _engine(request)
    ns = _namespace(request, body.namespace)
    result = await eng.search(
        namespace=ns,
        query=body.query,
        top_k=body.top_k,
        threshold=body.threshold,
    )

    docs = result.get("results", [])
    citations = [map_to_citation(doc) for doc in docs]
    processing_time_ms = (time.time() - t0) * 1000

    namespace_id = body.namespace or "default"
    namespace_ref = NamespaceRef(
        id=namespace_id,
        version="1.0.0",
        type="public" if namespace_id.startswith("public:") else "private"
    )

    usage_info = UsageInfo(
        retrieved_chunks=len(citations),
        processing_time_ms=processing_time_ms
    )

    avg_freshness = sum(c.freshness_score for c in citations) / len(citations) if citations else 1.0
    receipt_state = getattr(request.state, "receipt", None)
    price_paid = receipt_state.get("amount_paid") if receipt_state else "0.0"

    value_proof = ValueProof(
        namespace=namespace_id,
        namespace_version="1.0.0",
        freshness_score=avg_freshness,
        retrieved_chunks=len(citations),
        source_trust="verified" if citations else "none",
        estimated_steps_saved=len(citations) * 2,
        infrastructure_avoided=["vector_database", "llm_hosting"],
        reason_to_pay="curated agent memory" if price_paid != "0.0" else "free access",
        price_paid=price_paid
    )

    # Core data structure for response hashing
    core_data = {
        "results": [c.model_dump() for c in citations],
        "total_documents": result.get("total_documents", 0),
        "value_proof": value_proof.model_dump(),
        "namespace": namespace_ref.model_dump(),
        "usage": usage_info.model_dump()
    }
    response_hash = hashlib.sha256(json.dumps(core_data, sort_keys=True).encode("utf-8")).hexdigest()

    receipt = None
    if receipt_state:
        receipt = Receipt(
            receipt_id=receipt_state.get("receipt_id"),
            operation=receipt_state.get("operation"),
            amount_paid=receipt_state.get("amount_paid"),
            asset=receipt_state.get("asset", "USDC"),
            network=receipt_state.get("network", "solana"),
            tx_signature=receipt_state.get("tx_signature"),
            request_hash=receipt_state.get("request_hash", ""),
            response_hash=response_hash,
            created_at=receipt_state.get("created_at", "")
        )

    return SearchResponse(
        results=citations,
        total_documents=result.get("total_documents", 0),
        receipt=receipt,
        value_proof=value_proof,
        namespace=namespace_ref,
        usage=usage_info
    )


@router.post(
    "/ingest",
    tags=["RAG Operations"],
    summary="Ingesta de documento",
    description="Ingesta texto o archivo en un namespace. Genera chunks, embeddings y los indexa. Retorna document_id determinístico.",
)
async def ingest(body: IngestRequest, request: Request):
    t0 = time.time()
    eng = _engine(request)
    file_obj = io.BytesIO(body.content.encode("utf-8"))
    result = await eng.ingest_file(
        namespace=_namespace(request, body.namespace),
        file_obj=file_obj,
        filename=body.filename,
        metadata=body.metadata,
    )
    result["processing_time_ms"] = (time.time() - t0) * 1000
    return _inject_receipt(request, result)


@router.post(
    "/query",
    response_model=QueryResponse,
    tags=["RAG Operations"],
    summary="Consulta RAG (pregunta + respuesta + citas)",
    description="Realiza búsqueda semántica + genera respuesta con LLM local. Incluye citas verificables, value_proof y receipt cuando aplica.",
)
async def query(body: QueryRequest, request: Request):
    t0 = time.time()
    eng = _engine(request)
    ns = _namespace(request, body.namespace)

    search_result = await eng.search(
        namespace=ns,
        query=body.question,
        top_k=body.top_k,
    )

    docs = search_result.get("results", [])
    if docs:
        llm = LocalLLMProvider()
        answer = llm.generate_answer(body.question, docs)
    else:
        answer = "No se encontró información relevante."

    citations = [map_to_citation(doc) for doc in docs]
    processing_time_ms = (time.time() - t0) * 1000

    namespace_id = body.namespace or "default"
    namespace_ref = NamespaceRef(
        id=namespace_id,
        version="1.0.0",
        type="public" if namespace_id.startswith("public:") else "private"
    )

    usage_info = UsageInfo(
        retrieved_chunks=len(citations),
        processing_time_ms=processing_time_ms
    )

    avg_freshness = sum(c.freshness_score for c in citations) / len(citations) if citations else 1.0
    receipt_state = getattr(request.state, "receipt", None)
    price_paid = receipt_state.get("amount_paid") if receipt_state else "0.0"

    value_proof = ValueProof(
        namespace=namespace_id,
        namespace_version="1.0.0",
        freshness_score=avg_freshness,
        retrieved_chunks=len(citations),
        source_trust="verified" if citations else "none",
        estimated_steps_saved=len(citations) * 2,
        infrastructure_avoided=["vector_database", "llm_hosting"],
        reason_to_pay="curated agent memory" if price_paid != "0.0" else "free access",
        price_paid=price_paid
    )

    # Core data structure for response hashing
    core_data = {
        "answer": answer,
        "citations": [c.model_dump() for c in citations],
        "value_proof": value_proof.model_dump(),
        "namespace": namespace_ref.model_dump(),
        "usage": usage_info.model_dump()
    }
    response_hash = hashlib.sha256(json.dumps(core_data, sort_keys=True).encode("utf-8")).hexdigest()

    receipt = None
    if receipt_state:
        receipt = Receipt(
            receipt_id=receipt_state.get("receipt_id"),
            operation=receipt_state.get("operation"),
            amount_paid=receipt_state.get("amount_paid"),
            asset=receipt_state.get("asset", "USDC"),
            network=receipt_state.get("network", "solana"),
            tx_signature=receipt_state.get("tx_signature"),
            request_hash=receipt_state.get("request_hash", ""),
            response_hash=response_hash,
            created_at=receipt_state.get("created_at", "")
        )

    return QueryResponse(
        answer=answer,
        citations=citations,
        receipt=receipt,
        value_proof=value_proof,
        namespace=namespace_ref,
        usage=usage_info
    )


@router.get(
    "/stats",
    tags=["Namespace Management"],
    summary="Estadísticas del namespace",
    description="Número de documentos, dimensiones de embedding y modo de almacenamiento.",
)
async def stats(request: Request):
    eng = _engine(request)
    result = await eng.get_stats(_namespace(request, request.query_params.get("namespace")))
    return result


@router.delete(
    "/namespace",
    tags=["Namespace Management"],
    summary="Eliminar namespace completo",
    description="Borra todos los documentos y chunks de un namespace privado. Operación destructiva (solo para namespaces privados).",
)
async def clear_namespace(request: Request):
    eng = _engine(request)
    namespace = _namespace(request, request.query_params.get("namespace"))
    await eng.clear_namespace(namespace)
    return {"status": "ok", "namespace": namespace}


@router.delete(
    "/documents/{doc_id}",
    tags=["Namespace Management"],
    summary="Eliminar documento específico",
    description="Borra un documento (y sus chunks) de un namespace privado. Usa el document_id retornado en ingest.",
)
async def delete_document(doc_id: str, request: Request):
    eng = _engine(request)
    namespace = _namespace(request, request.query_params.get("namespace"))
    success = await eng.delete_document(namespace, doc_id)
    if not success:
        return JSONResponse(status_code=404, content={"error": "Document not found or could not be deleted"})
    return {"status": "ok", "document_id": doc_id, "namespace": namespace}
