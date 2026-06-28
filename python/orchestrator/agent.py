"""
Agentic entry point for System.

Two modes:

    1. Embedded (local, free):
        from orchestrator.agent import RAG
        rag = RAG()
        rag.ingest("doc.pdf")
        rag.query("pregunta?")

    2. Hosted (API, pays per call):
        from orchestrator_sdk import OrchestratorClient
        api = OrchestratorClient(api_url="http://localhost:8000", payment_provider=...)
        api.search("query")
"""

import asyncio
import logging
import os
from typing import Any, Callable, Dict, Optional

from orchestrator.core.rag import OfflineRAGEngine
from orchestrator.payments.pricing import cost_for

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mode 1: Embedded — local, free, no server needed
# ---------------------------------------------------------------------------

class RAG:
    """
    Local, free, zero-dependency RAG agent.

    Everything runs on-device with SQLite + numpy.
    No API keys, no credits, no internet.
    """

    def __init__(
        self,
        namespace: str = "default",
        embedded: Optional[bool] = None,
    ):
        self._use_embedded = embedded if embedded is not None else True
        self._namespace = namespace
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._engine: Optional[OfflineRAGEngine] = None

    @property
    def engine(self) -> OfflineRAGEngine:
        if self._engine is None:
            self._engine = OfflineRAGEngine(embedded=self._use_embedded)
        return self._engine

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or self._loop.is_closed():
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
        return self._loop

    def _run(self, coro):
        loop = self.loop
        try:
            return loop.run_until_complete(coro)
        except RuntimeError:
            import concurrent.futures
            import threading
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()

    def ingest(self, source: Any, filename: Optional[str] = None,
               metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        filename = filename or (getattr(source, "name", None) or "document")
        metadata = metadata or {}

        if isinstance(source, str) and os.path.isfile(source):
            with open(source, "rb") as f:
                return self._run(self.engine.ingest_file(
                    namespace=self._namespace, file_obj=f, filename=os.path.basename(source), metadata=metadata,
                ))
        elif isinstance(source, str):
            return self._run(self.engine.index_documents(
                namespace=self._namespace,
                documents=[{"id": filename, "text": source, "metadata": metadata}],
            ))
        elif isinstance(source, bytes):
            import io
            return self._run(self.engine.ingest_file(
                namespace=self._namespace, file_obj=io.BytesIO(source), filename=filename, metadata=metadata,
            ))
        else:
            return self._run(self.engine.ingest_file(
                namespace=self._namespace, file_obj=source, filename=filename, metadata=metadata,
            ))

    def query(self, question: str, top_k: int = 5) -> str:
        results = self.search(question, top_k=top_k)
        docs = results.get("results", [])
        if not docs:
            return "No se encontró información relevante."
        from orchestrator.core.llm import LocalLLMProvider
        return LocalLLMProvider().generate_answer(question, docs)

    def search(self, query: str, top_k: int = 5) -> Dict[str, Any]:
        return self._run(self.engine.search(namespace=self._namespace, query=query, top_k=top_k))

    def stats(self) -> Dict[str, Any]:
        return self._run(self.engine.get_stats(namespace=self._namespace))

    def ingest_text(self, text: str, doc_id: Optional[str] = None) -> Dict[str, Any]:
        return self.ingest(text, filename=doc_id or "inline.txt", metadata={"source": "inline"})
