"""
SES Agentic API Gateway — FastAPI application factory.

Usage:
    pip install ses-core[server]
    ses serve
"""

import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ses.config import EMBEDDED_MODE, DEBUG
from ses.payments.receipts import ReceiptLedger
from ses.payments.keys import KeyStore
from ses.payments.solana_pay import SolanaPay
from ses.core.rag import OfflineRAGEngine

from .middleware import PaymentRequiredMiddleware
from .routes import router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("SES Agentic Gateway starting (mode: %s)", "embedded" if EMBEDDED_MODE else "qdrant")
    yield
    solana: SolanaPay = app.state.solana
    await solana.close()
    logger.info("SES Agentic Gateway shut down")


def create_app(
    *,
    engine: Optional[OfflineRAGEngine] = None,
    ledger: Optional[ReceiptLedger] = None,
    keystore: Optional[KeyStore] = None,
    solana: Optional[SolanaPay] = None,
) -> FastAPI:
    app = FastAPI(
        title="SES Agentic Gateway",
        version="2.0.0",
        description="RAG-as-a-Service for autonomous agents. Pay-per-use with crypto.",
        lifespan=lifespan,
    )
    app.state.engine = engine
    app.state.ledger = ledger or ReceiptLedger()
    app.state.keystore = keystore or KeyStore()
    app.state.solana = solana or SolanaPay()

    # CORS: Restrictive by default. Allow "*" only in DEBUG or when explicitly configured.
    # Never use "*" with allow_credentials=True in production (browser security requirement).
    cors_origins = os.getenv("CORS_ALLOW_ORIGINS", "").split(",") if os.getenv("CORS_ALLOW_ORIGINS") else None
    if not cors_origins:
        cors_origins = ["*"] if DEBUG else ["http://localhost:3000", "http://localhost:8000"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=DEBUG,  # Only enable credentials in debug/local
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"] if not DEBUG else ["*"],
        allow_headers=["*"],
    )

    app.add_middleware(
        PaymentRequiredMiddleware,
        ledger=app.state.ledger,
        keystore=app.state.keystore,
    )

    app.include_router(router)

    @app.get("/.well-known/agent-card.json")
    async def well_known_agent_card():
        from ses.discovery.card import get_agent_card
        return get_agent_card()

    return app

