import os
import logging
import asyncio
import httpx
import numpy as np
from abc import ABC, abstractmethod
from typing import List, Union, Any, Optional

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

logger = logging.getLogger(__name__)

class BaseEmbeddingModel(ABC):
    """Abstract base class for embedding models."""

    @abstractmethod
    async def encode(self, sentences: Union[str, List[str]], **kwargs) -> Any:
        """
        Encode sentences into embeddings asynchronously.

        Args:
            sentences: A single sentence or a list of sentences.

        Returns:
            A list of floats (for single sentence) or a list of lists/numpy array (for multiple sentences).
        """
        pass

class LocalEmbeddingModel(BaseEmbeddingModel):
    """Embedding model that runs locally using SentenceTransformer."""

    def __init__(self, model_name: str, device: str = "cpu"):
        if SentenceTransformer is None:
            raise ImportError("sentence-transformers is not installed")

        logger.info("Loading local embedding model %s on device: %s", model_name, device)
        self.model = SentenceTransformer(model_name, device=device)

    async def encode(self, sentences: Union[str, List[str]], **kwargs) -> Any:
        loop = asyncio.get_running_loop()
        # Offload the CPU-bound encoding to a thread pool
        return await loop.run_in_executor(
            None,
            lambda: self.model.encode(sentences, **kwargs)
        )

class RemoteEmbeddingModel(BaseEmbeddingModel):
    """Embedding model that calls a remote service via HTTP."""

    def __init__(self, endpoint_url: str, api_key: Optional[str] = None, model_name: str = "default"):
        self.endpoint_url = endpoint_url
        self.api_key = api_key
        self.model_name = model_name
        logger.info(f"🌐 Initialized RemoteEmbeddingModel pointing to {endpoint_url}")

    async def encode(self, sentences: Union[str, List[str]], **kwargs) -> Any:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "inputs": sentences,
            "model": self.model_name
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(self.endpoint_url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()

            # Handling different response formats
            if isinstance(data, dict) and "embeddings" in data:
                embeddings = data["embeddings"]
            else:
                embeddings = data

            # Convert to numpy array if it's a list, to match SentenceTransformer behavior
            if isinstance(embeddings, list):
                return np.array(embeddings)
            return embeddings

        except httpx.RequestError as e:
            logger.error(f"Error calling remote embedding service: {e}")
            raise
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error calling remote embedding service: {e}")
            raise

def get_embedding_model() -> BaseEmbeddingModel:
    """
    Factory function to get the appropriate embedding model based on environment variables.
    """
    provider = os.getenv("EMBEDDING_PROVIDER", "local").lower()

    if provider == "remote":
        url = os.getenv("EMBEDDING_SERVICE_URL")
        if not url:
             raise ValueError("EMBEDDING_SERVICE_URL must be set when EMBEDDING_PROVIDER is 'remote'")

        api_key = os.getenv("EMBEDDING_API_KEY")
        model_name = os.getenv("EMBEDDING_MODEL", "default")

        return RemoteEmbeddingModel(endpoint_url=url, api_key=api_key, model_name=model_name)

    # Default to local
    model_name = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    device = os.getenv("EMBEDDING_DEVICE", "cpu")
    return LocalEmbeddingModel(model_name=model_name, device=device)
