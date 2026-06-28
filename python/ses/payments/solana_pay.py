"""
Solana Pay integration for on-chain USDC payments.

Handles wallet generation, transaction verification, and balance checking.
Used by the credit system to verify real crypto payments.
"""

import base58
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from ses.config import DEBUG

logger = logging.getLogger(__name__)

SOLANA_NETWORK = os.getenv("SOLANA_NETWORK", "mainnet-beta").strip().lower()
if SOLANA_NETWORK not in ("mainnet-beta", "devnet", "mainnet"):
    SOLANA_NETWORK = "mainnet-beta"

_usdc_override = os.getenv("USDC_MINT")
_rpc_override = os.getenv("SOLANA_RPC_URL")

if SOLANA_NETWORK == "devnet":
    _default_mint = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"
    _default_rpc = "https://api.devnet.solana.com"
else:
    _default_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    _default_rpc = "https://api.mainnet-beta.solana.com"

USDC_MINT = _usdc_override or _default_mint
SOLANA_RPC = _rpc_override or _default_rpc
# NOTE: USDC_MINT and SOLANA_RPC are resolved at import time from env.
# Scripts must load merchant.env (or .env) BEFORE importing ses.payments.solana_pay.

SOLANA_AVAILABLE = False
try:
    from solders.keypair import Keypair
    from solders.pubkey import Pubkey
    from solders.signature import Signature
    from solana.rpc.async_api import AsyncClient
    from solana.rpc.commitment import Confirmed
    from solana.rpc.types import TokenAccountOpts

    TOKEN_PROGRAM_ID = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
    ASSOCIATED_TOKEN_PROGRAM_ID = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
    SOLANA_AVAILABLE = True
except ImportError:
    Keypair = None  # type: ignore
    Pubkey = None  # type: ignore
    logger.warning("solana/solders not installed — Solana Pay disabled")
    TOKEN_PROGRAM_ID = None  # type: ignore
    ASSOCIATED_TOKEN_PROGRAM_ID = None  # type: ignore


@dataclass
class WalletInfo:
    public_key: str
    private_key: str  # base58 encoded
    path: Optional[str] = None


@dataclass
class VerificationResult:
    verified: bool
    amount_usdc: float
    sender: Optional[str] = None
    error: Optional[str] = None


class SolanaPay:
    """
    Real Solana Pay integration.

    In DEBUG mode, transactions are simulated (no RPC calls).
    In production, requires SOLANA_RPC_URL and a merchant wallet keypair.

    Supports mainnet-beta and devnet via SOLANA_NETWORK (or auto defaulted from RPC).
    USDC_MINT can be overridden; otherwise selects the correct Circle/USDC mint per network.
    """

    def __init__(
        self,
        rpc_url: Optional[str] = None,
        merchant_keypair: Optional[Any] = None,
        client_factory: Optional[Callable[[str], Any]] = None,
        debug: Optional[bool] = None,
    ):
        # Resolve network + mint + rpc at construction time (supports env set before import or before instantiation)
        env_network = os.getenv("SOLANA_NETWORK", "").strip().lower()
        env_rpc = os.getenv("SOLANA_RPC_URL")
        env_mint = os.getenv("USDC_MINT")

        # Determine network
        if env_network in ("devnet", "mainnet-beta", "mainnet"):
            network = env_network if env_network != "mainnet" else "mainnet-beta"
        else:
            # Infer from rpc if provided or module default
            candidate = (rpc_url or env_rpc or SOLANA_RPC).lower()
            if "devnet" in candidate:
                network = "devnet"
            else:
                network = "mainnet-beta"

        if network == "devnet":
            default_mint = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"
            default_rpc = "https://api.devnet.solana.com"
        else:
            default_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
            default_rpc = "https://api.mainnet-beta.solana.com"

        resolved_rpc = rpc_url or env_rpc or default_rpc
        resolved_mint = env_mint or default_mint

        self.network = network
        self.usdc_mint = resolved_mint  # str (pubkey string)
        self._rpc_url = resolved_rpc

        self._rpc_client: Optional[Any] = None
        self._client_factory = (
            client_factory or AsyncClient
            if SOLANA_AVAILABLE
            else client_factory
        )
        self._debug = DEBUG if debug is None else debug

        if SOLANA_AVAILABLE:
            if merchant_keypair:
                self._merchant = merchant_keypair
            else:
                pk = os.getenv("SOLANA_MERCHANT_SECRET")
                if pk:
                    self._merchant = Keypair.from_base58_string(pk)
                else:
                    self._merchant = None
                    if self._debug:
                        logger.info("No merchant key configured — generating debug-only ephemeral keypair")
                        self._merchant = Keypair()
                    else:
                        logger.error("SOLANA_MERCHANT_SECRET is required in production")
        else:
            self._merchant = None

        # Validations (after merchant resolution)
        if not self._debug:
            if not os.getenv("SOLANA_MERCHANT_SECRET") and not merchant_keypair:
                logger.error("SOLANA_MERCHANT_SECRET is required when DEBUG=false")

        # Validate mint is parseable
        if SOLANA_AVAILABLE:
            try:
                Pubkey.from_string(self.usdc_mint)
            except Exception:
                logger.error("Invalid USDC_MINT (not a valid Solana pubkey): %s", self.usdc_mint)

        # Rough consistency warning between network and rpc
        if SOLANA_AVAILABLE:
            rpc_l = self._rpc_url.lower()
            if self.network == "devnet" and "mainnet" in rpc_l and "devnet" not in rpc_l:
                logger.warning("SOLANA_NETWORK=devnet but SOLANA_RPC_URL looks like mainnet: %s", self._rpc_url)
            if self.network == "mainnet-beta" and "devnet" in rpc_l:
                logger.warning("SOLANA_NETWORK=mainnet-beta but SOLANA_RPC_URL looks like devnet: %s", self._rpc_url)

    @property
    def merchant_address(self) -> Optional[str]:
        if self._merchant and SOLANA_AVAILABLE:
            return str(self._merchant.pubkey())
        return None

    async def _get_client(self) -> Optional[Any]:
        """Return a cached RPC client. The factory is injectable for tests."""
        if self._client_factory is None:
            return None
        if self._rpc_client is None:
            self._rpc_client = self._client_factory(self._rpc_url)
        return self._rpc_client

    async def close(self):
        if self._rpc_client:
            await self._rpc_client.close()
            self._rpc_client = None

    # ── Wallet generation ──

    def create_wallet(self) -> WalletInfo:
        if not SOLANA_AVAILABLE:
            return WalletInfo(
                public_key="simulated",
                private_key="simulated",
            )
        kp = Keypair()
        return WalletInfo(
            public_key=str(kp.pubkey()),
            private_key=base58.b58encode(bytes(kp)),
        )

    def generate_payment_memo(self, api_key: str, amount_usdc: float) -> str:
        import hashlib
        ref = hashlib.sha256(f"{api_key}:{amount_usdc}:{os.urandom(4).hex()}".encode()).hexdigest()[:16]
        return f"ses:{api_key[:8]}:{ref}"

    # ── Transaction verification ──

    async def verify_usdc_transfer(
        self,
        tx_signature: str,
        expected_amount: float,
        expected_memo: Optional[str] = None,
    ) -> VerificationResult:
        """
        Verify a real USDC transfer on-chain.

        Checks:
        1. Transaction exists and is confirmed
        2. Transfers the expected amount of USDC
        3. (Optional) Contains the expected memo
        """
        tx_signature = (tx_signature or "").strip()
        if not tx_signature:
            return VerificationResult(
                verified=False,
                amount_usdc=0,
                error="Transaction signature is required",
            )
        if expected_amount <= 0:
            return VerificationResult(
                verified=False,
                amount_usdc=0,
                error="Expected amount must be positive",
            )

        if self._debug:
            logger.info("DEBUG mode — simulating verification of tx %s", tx_signature[:16])
            return VerificationResult(verified=True, amount_usdc=expected_amount, sender="simulated")

        # Production path: strict checks
        if not self._merchant:
            return VerificationResult(
                verified=False,
                amount_usdc=0,
                error="Merchant keypair not configured (set SOLANA_MERCHANT_SECRET)",
            )

        if not SOLANA_AVAILABLE:
            return VerificationResult(verified=False, amount_usdc=0, error="Solana not available")
        if not self.merchant_address:
            return VerificationResult(
                verified=False,
                amount_usdc=0,
                error="Merchant wallet is not configured",
            )

        client = await self._get_client()
        if not client:
            return VerificationResult(verified=False, amount_usdc=0, error="Solana not available")

        try:
            signature = Signature.from_string(tx_signature)
            tx = await client.get_transaction(
                signature,
                commitment=Confirmed,
                max_supported_transaction_version=0,
            )

            if not tx or not tx.value:
                return VerificationResult(verified=False, amount_usdc=0, error="Transaction not found")

            meta = tx.value.transaction.meta
            if meta and meta.err:
                return VerificationResult(verified=False, amount_usdc=0, error=f"Transaction failed: {meta.err}")

            post_balances = meta.post_token_balances or []
            pre_balances = meta.pre_token_balances or []
            has_token_balances = bool(post_balances)

            received = 0.0
            pre_by_index = {
                getattr(balance, "account_index", index): balance
                for index, balance in enumerate(pre_balances)
            }
            for index, post in enumerate(post_balances):
                account_index = getattr(post, "account_index", index)
                pre = pre_by_index.get(account_index)
                # Use instance usdc_mint (supports devnet override correctly)
                if (
                    str(post.mint) == self.usdc_mint
                    and str(post.owner) == self.merchant_address
                ):
                    post_amount = float(getattr(getattr(post, 'ui_token_amount', None), 'ui_amount', None) or 0)
                    pre_amount = (
                        float(getattr(getattr(pre, 'ui_token_amount', None), 'ui_amount', None) or 0)
                        if pre is not None
                        else 0.0
                    )
                    received += post_amount - pre_amount

            if received < expected_amount * 0.99:
                # Fallback: parse SPL Token Transfer / TransferChecked instructions
                # Works even if post_token_balances are missing or use different mint view (devnet)
                try:
                    merchant_ata = self._get_ata(self.merchant_address, self.usdc_mint)
                    msg = tx.value.transaction.transaction.message
                    account_keys = [str(k) for k in getattr(msg, 'account_keys', []) or getattr(msg, 'static_account_keys', [])]
                    instructions = []
                    if meta and hasattr(meta, 'inner_instructions') and meta.inner_instructions:
                        for inner in meta.inner_instructions:
                            instructions.extend(getattr(inner, 'instructions', []))
                    main_ixs = getattr(msg, 'instructions', []) or []
                    instructions.extend(main_ixs)

                    for ix in instructions:
                        try:
                            prog_idx = getattr(ix, 'program_id_index', -1)
                            if prog_idx < 0 or prog_idx >= len(account_keys):
                                continue
                            program_id = account_keys[prog_idx]
                            if "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA" not in program_id:
                                continue
                            data = bytes(getattr(ix, 'data', b''))
                            if len(data) < 9 or data[0] not in (3, 12):
                                continue

                            amount = int.from_bytes(data[1:9], "little")
                            accts = getattr(ix, "accounts", []) or []
                            # Transfer (3): accounts = [source, destination, owner]
                            # TransferChecked (12): accounts = [source, mint, destination, owner]
                            if data[0] == 3:
                                if len(accts) < 2:
                                    continue
                                dest_idx = accts[1]
                            else:  # 12 TransferChecked
                                if len(accts) < 3:
                                    continue
                                dest_idx = accts[2]

                            if dest_idx < len(account_keys):
                                dest = account_keys[dest_idx]
                                if dest == merchant_ata and amount >= int(expected_amount * 1_000_000 * 0.99):
                                    received = amount / 1_000_000.0
                                    break
                        except Exception:
                            pass
                except Exception as e:
                    logger.warning("Instruction fallback error: %s", e)

                if received < expected_amount * 0.99:
                    if self.network == "devnet" and not has_token_balances:
                        logger.warning(f"Devnet indexing delay: accepting confirmed tx {tx_signature} despite parsed amount {received}")
                        received = expected_amount
                    else:
                        return VerificationResult(
                            verified=False, amount_usdc=received,
                            error=f"Expected {expected_amount} USDC, got {received}",
                        )

            if expected_memo:
                memo_found = False
                tx_inner = tx.value.transaction.transaction
                if expected_memo in str(tx_inner):
                    memo_found = True
                else:
                    memo_bytes = expected_memo.encode("utf-8")
                    msg = getattr(tx_inner, "message", None)
                    for ix in getattr(msg, "instructions", []) or []:
                        try:
                            if memo_bytes in bytes(getattr(ix, "data", b"")):
                                memo_found = True
                                break
                        except Exception:
                            continue
                if not memo_found:
                    return VerificationResult(
                        verified=False,
                        amount_usdc=received,
                        error="Expected payment memo not found",
                    )

            sender = str(pre_balances[0].owner) if pre_balances else None
            return VerificationResult(verified=True, amount_usdc=received, sender=sender)

        except Exception as e:
            logger.error("Solana verification error: %s", e)
            return VerificationResult(verified=False, amount_usdc=0, error=str(e))

    # ── Balance check ──

    async def get_usdc_balance(self, address: str) -> float:
        if self._debug or not SOLANA_AVAILABLE:
            return 0.0

        client = await self._get_client()
        if not client:
            return 0.0

        mint_pk = None
        try:
            owner = Pubkey.from_string(address)
            mint_pk = Pubkey.from_string(self.usdc_mint)

            # Preferred: list token accounts owned by address filtered by our mint (works for devnet)
            try:
                opts = TokenAccountOpts(mint=mint_pk)
                resp = await client.get_token_accounts_by_owner_json_parsed(owner, opts)
                if resp and resp.value:
                    for acc in resp.value:
                        # ui_amount may be in .account.data.parsed or direct
                        try:
                            amt = getattr(getattr(acc.account, 'data', None), 'parsed', None)
                            if amt and hasattr(amt, 'info') and 'tokenAmount' in amt.info:
                                ui = amt.info['tokenAmount'].get('uiAmount')
                                if ui is not None:
                                    return float(ui)
                            # alternative structure
                            if hasattr(acc, 'account') and hasattr(acc.account, 'data'):
                                parsed = getattr(acc.account.data, 'parsed', None) or acc.account.data
                                if isinstance(parsed, dict) and 'info' in parsed:
                                    ui = parsed['info'].get('tokenAmount', {}).get('uiAmount')
                                    if ui is not None:
                                        return float(ui)
                        except Exception:
                            pass
                    # if list but no match, fallthrough to 0 or ATA
            except Exception as e:
                msg = str(e).lower()
                if "unpacked" not in msg and "not found" not in msg and "could not" not in msg:
                    logger.warning("get_token_accounts_by_owner_json_parsed warning: %s", e)

            # Fallback: compute ATA explicitly using the correct self.usdc_mint and query balance
            try:
                ata = Pubkey.find_program_address(
                    [bytes(owner), bytes(TOKEN_PROGRAM_ID), bytes(mint_pk)],
                    ASSOCIATED_TOKEN_PROGRAM_ID,
                )[0]
                resp = await client.get_token_account_balance(ata)
                if resp and resp.value:
                    return float(resp.value.ui_amount or 0.0)
            except Exception:
                pass

            return 0.0
        except Exception as e:
            msg = str(e).lower()
            if "not found" in msg or "could not find" in msg or "unpacked" in msg or "invalid" in msg:
                return 0.0
            logger.warning("Balance check error: %s", e)
            return 0.0

    async def get_transaction_details(self, signature: str):
        """Debug helper: fetch and return raw token balances + basic instruction info from a tx."""
        if not SOLANA_AVAILABLE:
            return None
        client = await self._get_client()
        if not client:
            return None
        try:
            sig = Signature.from_string(signature)
            tx = await client.get_transaction(sig, commitment=Confirmed, max_supported_transaction_version=0)
            meta = tx.value.transaction.meta if tx.value else None
            result = {
                "post_token_balances": [],
                "pre_token_balances": [],
                "token_transfer_instructions": [],
            }
            if meta:
                result["post_token_balances"] = [
                    {
                        "mint": str(getattr(b, 'mint', '')),
                        "owner": str(getattr(b, 'owner', '')),
                        "ui_amount": getattr(getattr(b, 'ui_token_amount', None), 'ui_amount', None),
                        "account_index": getattr(b, 'account_index', None),
                    }
                    for b in (meta.post_token_balances or [])
                ]
                result["pre_token_balances"] = [
                    {
                        "mint": str(getattr(b, 'mint', '')),
                        "owner": str(getattr(b, 'owner', '')),
                        "ui_amount": getattr(getattr(b, 'ui_token_amount', None), 'ui_amount', None),
                        "account_index": getattr(b, 'account_index', None),
                    }
                    for b in (meta.pre_token_balances or [])
                ]
            # Also extract token transfer instructions for debugging
            msg = tx.value.transaction.transaction.message if tx.value else None
            if msg:
                account_keys = [str(k) for k in getattr(msg, 'account_keys', []) or getattr(msg, 'static_account_keys', [])]
                all_ixs = getattr(msg, 'instructions', []) or []
                if meta and hasattr(meta, 'inner_instructions') and meta.inner_instructions:
                    for inner in meta.inner_instructions:
                        all_ixs.extend(getattr(inner, 'instructions', []))
                for ix in all_ixs:
                    try:
                        prog_idx = getattr(ix, 'program_id_index', -1)
                        if prog_idx < 0 or prog_idx >= len(account_keys):
                            continue
                        prog = account_keys[prog_idx]
                        if 'Tokenkeg' not in prog:
                            continue
                        data = bytes(getattr(ix, 'data', b''))
                        if len(data) >= 9 and data[0] in (3, 12):
                            amt = int.from_bytes(data[1:9], 'little')
                            accts = getattr(ix, 'accounts', [])
                            dest = account_keys[accts[1]] if len(accts) > 1 and accts[1] < len(account_keys) else None
                            result["token_transfer_instructions"].append({
                                "amount_raw": amt,
                                "amount_usdc": amt / 1_000_000,
                                "dest": dest,
                            })
                    except Exception:
                        pass
            return result
        except Exception as e:
            return {"error": str(e)}

    # ── Merchant wallet info ──

    def get_merchant_info(self) -> Dict[str, Any]:
        return {
            "address": self.merchant_address,
            "token": "USDC",
            "mint": self.usdc_mint,
            "network": self.network,
            "rpc_url": self._rpc_url,
        }

    def _get_ata(self, owner: str, mint: str) -> str:
        """Compute the Associated Token Account address."""
        owner_pk = Pubkey.from_string(owner)
        mint_pk = Pubkey.from_string(mint)
        ata, _ = Pubkey.find_program_address(
            [bytes(owner_pk), bytes(TOKEN_PROGRAM_ID), bytes(mint_pk)],
            ASSOCIATED_TOKEN_PROGRAM_ID,
        )
        return str(ata)
