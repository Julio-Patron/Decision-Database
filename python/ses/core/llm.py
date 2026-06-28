import os
import json
import logging
import urllib.request
from typing import Any, Dict, List, Optional

from ses.config import OLLAMA_BASE_URL, OLLAMA_MODEL

logger = logging.getLogger(__name__)

class LocalLLMProvider:
    """
    Servicio de inferencia local para RAG (Retrieval-Augmented Generation).
    Soporta únicamente Ollama self-hosted para mantener el sistema offline-first.
    """

    def __init__(self, model_override: Optional[str] = None):
        self.model = model_override or OLLAMA_MODEL
        self.ollama_url = f"{OLLAMA_BASE_URL.rstrip('/')}/api/generate"

    def generate_answer(self, query: str, context_docs: List[Dict[str, Any]]) -> str:
        if not context_docs:
            return "La documentación disponible no contiene información suficiente para responder a esta consulta."

        system_prompt = self._build_system_prompt()
        context_text = self._build_context(context_docs)

        try:
            return self._call_ollama(system_prompt, context_text, query)
        except Exception as exc:
            logger.error("Local LLM provider failed: %s", exc)
            return "No fue posible generar una respuesta con el proveedor LLM local. Verifique que Ollama esté en ejecución."

    def _call_ollama(self, system_prompt: str, context: str, query: str) -> str:
        prompt = f"{system_prompt}\n\nCONTEXT:\n{context}\n\nUSER QUERY: {query}"
        
        payload = json.dumps({
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.2},
        }).encode('utf-8')

        req = urllib.request.Request(
            self.ollama_url,
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )

        with urllib.request.urlopen(req, timeout=60) as response:
            result = json.loads(response.read().decode('utf-8'))
            content = result.get("response")
            if not content:
                raise RuntimeError("Ollama no devolvió contenido utilizable")
            return content

    def _build_system_prompt(self) -> str:
        return (
            "### ROLE\n"
            "Eres el Núcleo Cognitivo de SES-Semantic Engine v2.0. Tu función es actuar como una capa de inteligencia "
            "avanzada que sintetiza respuestas precisas basadas EXCLUSIVAMENTE en el contexto documental proporcionado.\n\n"
            "### OBJECTIVE\n"
            "Proporcionar respuestas técnicas, formales y verificables. Tu prioridad absoluta es la fidelidad a la fuente. "
            "Si la información no está presente en los fragmentos recuperados, debes declararlo explícitamente.\n\n"
            "### FORMATTING RULES\n"
            "- Usa Markdown para dar estructura (negritas, listas, tablas).\n"
            "- Si hay contradicciones entre dos documentos, señala ambas versiones indicando sus respectivas fuentes.\n"
        )

    def _build_context(self, context_docs: List[Dict[str, Any]]) -> str:
        context_parts = []
        for index, doc in enumerate(context_docs, start=1):
            meta = doc.get("metadata", {})
            filename = meta.get("filename") or meta.get("file_name") or "desconocido"
            page = meta.get("page_number") or meta.get("page") or "N/A"
            text = doc.get("text") or doc.get("text_snippet") or ""
            context_parts.append(
                f"--- DOCUMENTO [{index}] ---\n"
                f"METADATOS: file_name: {filename}, page_number: {page}\n"
                f"CONTENIDO: {text}"
            )

        return "\n\n".join(context_parts)
