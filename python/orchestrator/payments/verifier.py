"""
Payment proof verification logic.
"""

from typing import Dict, Any, Optional

from orchestrator.config import DEBUG
from .solana_pay import SolanaPay

async def verify_payment_proof(proof: str, expected_amount: float, solana_client: SolanaPay) -> bool:
    """
    Verifies a transaction signature as a payment proof.
    """
    if DEBUG and proof == "simulated":
        return True
        
    result = await solana_client.verify_usdc_transfer(
        tx_signature=proof,
        expected_amount=expected_amount,
        expected_memo="ses", # In B2A, memo might just identify System or the resource
    )
    return result.verified
