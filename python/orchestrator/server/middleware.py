"""
Payment middleware handling x402 workflow for agents.

Identity is resolved either by API Key or by an Agent ID.
Paid endpoints require an X-Payment-Proof header.
"""

import logging
import hashlib
from typing import Callable, Optional

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from orchestrator.payments.receipts import ReceiptLedger
from orchestrator.server.ratelimit import RateLimiter
from orchestrator.payments.keys import KeyStore
from orchestrator.payments.pricing import cost_for
from orchestrator.payments.x402 import create_402_response
from orchestrator.payments.verifier import verify_payment_proof

logger = logging.getLogger(__name__)

FREE_OPERATIONS = {"health", "balance", "stats", "keys"}


def _namespace_from_key_id(key_id: str) -> str:
    return hashlib.sha256(key_id.encode()).hexdigest()[:16]


class PaymentRequiredMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: FastAPI, ledger: ReceiptLedger, keystore: KeyStore, rate_limiter: Optional[RateLimiter] = None):
        super().__init__(app)
        self.ledger = ledger
        self.keystore = keystore
        self.rate_limiter = rate_limiter or RateLimiter()

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        ip = request.client.host if request.client else "unknown"
        if not self.rate_limiter.is_allowed(f"ip:{ip}"):
            return JSONResponse(
                status_code=429,
                content={"error": "Too Many Requests", "code": "rate_limit_exceeded"},
            )

        # Open endpoints
        if path in {"/v1/health", "/v1/capabilities", "/v1/pricing", "/.well-known/agent-card.json"}:
            return await call_next(request)

        if path.startswith("/v1/keys") and request.method == "POST":
            return await call_next(request)

        # Resolve identity. API Key is preferred, X-Agent-Id is fallback.
        api_key = request.headers.get("X-API-Key", "")
        agent_id = request.headers.get("X-Agent-Id", "")

        identity = "anonymous"
        if api_key:
            key_id = self.keystore.validate(api_key)
            if key_id is not None:
                identity = key_id
                request.state.api_key = api_key
                request.state.key_id = key_id
                request.state.namespace = _namespace_from_key_id(key_id)
            else:
                return JSONResponse(
                    status_code=401,
                    content={"error": "Invalid or revoked API key", "code": "invalid_api_key"},
                )
        elif agent_id:
            identity = agent_id
            request.state.namespace = _namespace_from_key_id(agent_id)
        else:
            # Enforce some identity token for abuse tracking
            return JSONResponse(
                status_code=401,
                content={"error": "Requires X-API-Key or X-Agent-Id header", "code": "missing_identity"},
            )

        if not self.rate_limiter.is_allowed(f"id:{identity}"):
            return JSONResponse(
                status_code=429,
                content={"error": "Too Many Requests", "code": "rate_limit_exceeded"},
            )

        # Rate limit based on payload size for ingest (weight)
        if path == "/v1/ingest" and request.method == "POST":
            content_length = request.headers.get("Content-Length")
            if content_length:
                try:
                    length_int = int(content_length)
                    if length_int > 0:
                        weight = max(1, length_int // 102400)
                        if not self.rate_limiter.is_allowed(f"size:{identity}", weight=weight):
                            return JSONResponse(
                                status_code=429,
                                content={"error": "Payload too large for current rate limit", "code": "rate_limit_exceeded"},
                            )
                except ValueError:
                    pass

        operation = self._resolve_operation(request)
        if operation is None or operation in FREE_OPERATIONS:
            return await call_next(request)

        cost = cost_for(operation)

        if cost > 0:
            payment_proof = request.headers.get("X-Payment-Proof")
            if not payment_proof:
                solana = getattr(request.app.state, "solana", None)
                merchant_info = solana.get_merchant_info() if solana else None
                return JSONResponse(
                    status_code=402,
                    content=create_402_response(cost, operation, request.method, request.url.path, merchant_info),
                )

            is_valid = await verify_payment_proof(payment_proof, cost, request.app.state.solana)

            if not is_valid:
                return JSONResponse(
                    status_code=400,
                    content={"error": "Invalid or unpaid payment proof", "code": "invalid_payment_proof"},
                )

            idempotency_key = request.headers.get("Idempotency-Key", payment_proof)
            # Compute request hash on the normalized request body payload.
            body_bytes = await request.body()
            
            # Restore the request receive channel so that route handlers can consume it.
            async def receive():
                return {"type": "http.request", "body": body_bytes, "more_body": False}
            request._receive = receive

            try:
                import json
                body_json = json.loads(body_bytes)
                normalized = json.dumps(body_json, sort_keys=True, separators=(',', ':'))
                request_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            except Exception:
                request_hash = hashlib.sha256(body_bytes).hexdigest()

            reservation = self.ledger.reserve_receipt(identity, cost, operation, payment_proof, idempotency_key, request_hash)

            if reservation == "spent_other":
                return JSONResponse(
                    status_code=409,
                    content={"error": "Payment proof already spent on a different request", "code": "replay_detected"}
                )
            elif reservation not in ("reserved", "failed"):
                # This is a cached successful response payload
                try:
                    import json
                    return JSONResponse(content=json.loads(reservation))
                except Exception:
                    pass

            import datetime
            request.state.receipt = {
                "operation": operation,
                "amount_paid": str(cost),
                "asset": "USDC",
                "network": "solana",
                "tx_signature": payment_proof,
                "receipt_id": payment_proof,  # For now we just use the tx_signature as the receipt_id
                "request_hash": request_hash,
                "created_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            }

            # Read body to compute actual hash if needed, but we already reserved.
            # Now we call the next middleware/route
            response = await call_next(request)

            if response.status_code >= 400:
                self.ledger.mark_failed(payment_proof)
                return response

            # If success, we want to capture the response body to cache it.
            # Fastapi responses are streaming, so we need to consume it
            import json
            from starlette.responses import StreamingResponse

            body = b""
            async for chunk in response.body_iterator:
                body += chunk

            try:
                # Cache the successful response
                self.ledger.commit_receipt(payment_proof, body.decode('utf-8'))
            except Exception as e:
                logger.error(f"Failed to commit receipt: {e}")

            # Return a new response with the body
            return Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type
            )

        response = await call_next(request)
        return response

    @staticmethod
    def _resolve_operation(request: Request) -> Optional[str]:
        path = request.url.path
        if path == "/v1/search":
            return "search"
        if path == "/v1/ingest":
            return "ingest"
        if path == "/v1/query":
            return "query"
        return None
