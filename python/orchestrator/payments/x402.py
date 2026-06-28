"""
x402 Protocol Implementation for System Agentic Tollgate.
"""

from typing import Dict, Any, Optional

def create_402_response(
    cost: float,
    operation: str,
    method: str,
    path: str,
    merchant_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Generates an x402-compliant Payment Required payload.

    When merchant_info is provided (from SolanaPay.get_merchant_info()),
    includes the concrete pay_to address, mint and cluster network.
    """
    base = {
        "error": "Payment required",
        "code": "payment_required",
        "next_action": "pay_and_retry",
        "payment_protocol": "x402",
        "amount": str(cost),
        "asset": "USDC",
        "network": "solana",
        "operation": operation.lower(),
        "retry": {
            "method": method,
            "path": path,
            "required_headers": [
                "X-Payment-Proof",
                "Idempotency-Key"
            ]
        }
    }

    if merchant_info:
        # Enrich with real configured values (supports devnet + correct USDC mint)
        if merchant_info.get("address"):
            base["pay_to"] = merchant_info["address"]
        if merchant_info.get("mint"):
            base["mint"] = merchant_info["mint"]
        if merchant_info.get("network"):
            # cluster network: devnet | mainnet-beta  (keep top level "network":"solana" for protocol compat)
            base["cluster"] = merchant_info["network"]
        if merchant_info.get("rpc_url"):
            base["rpc_url"] = merchant_info["rpc_url"]

    return base
