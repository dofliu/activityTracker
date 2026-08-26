import os
import math
import logging
from typing import List, Optional
import httpx
from rag.config import rag_settings
from core.config import get_config
from core.secret_resolver import resolve_secret_env

logger = logging.getLogger("OmniContext.RAG.Embeddings")


class EmbeddingService:
    def __init__(self):
        self._fastembed_model = None
        self._current_model_name = None

    def _get_fastembed(self, model_name: Optional[str] = None):
        target_model = model_name or rag_settings.DEFAULT_FASTEMBED_MODEL
        if self._fastembed_model is None or self._current_model_name != target_model:
            try:
                from fastembed import TextEmbedding
                self._fastembed_model = TextEmbedding(model_name=target_model)
                self._current_model_name = target_model
                logger.info(f"Initialized FastEmbed model: {target_model}")
            except Exception as e:
                logger.error(f"FastEmbed initialization failed: {e}")
                self._fastembed_model = None
        return self._fastembed_model

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        cfg = get_config()
        provider = str(cfg.get("rag.embedding_provider", rag_settings.DEFAULT_EMBEDDING_PROVIDER)).lower()

        if provider == "fastembed":
            model_name = str(cfg.get("rag.embedding_model", rag_settings.DEFAULT_FASTEMBED_MODEL))
            model = self._get_fastembed(model_name)
            if model:
                try:
                    embeddings = list(model.embed(texts))
                    return [emb.tolist() if hasattr(emb, "tolist") else list(emb) for emb in embeddings]
                except Exception as e:
                    logger.error(f"FastEmbed embedding error: {e}")
                    return [self._fallback_dummy_embed(t) for t in texts]

        elif provider == "ollama":
            base_url = str(cfg.get("synthesizer.ollama.base_url", "http://127.0.0.1:11434")).rstrip("/")
            model_name = str(cfg.get("semantic_index.embedding_model", "bge-m3:latest"))
            results = []
            try:
                with httpx.Client(base_url=base_url, timeout=60.0) as client:
                    for text in texts:
                        resp = client.post("/api/embed", json={"model": model_name, "input": text})
                        if resp.status_code == 200:
                            vectors = resp.json().get("embeddings") or []
                            if vectors:
                                results.append(vectors[0])
                            else:
                                results.append(self._fallback_dummy_embed(text))
                        else:
                            results.append(self._fallback_dummy_embed(text))
                return results
            except Exception as e:
                logger.error(f"Ollama embed error: {e}")
                return [self._fallback_dummy_embed(t) for t in texts]

        elif provider == "openai":
            api_key = resolve_secret_env("OPENAI_API_KEY")
            if api_key:
                try:
                    import openai
                    client = openai.OpenAI(api_key=api_key)
                    resp = client.embeddings.create(input=texts, model="text-embedding-3-small")
                    return [d.embedding for d in resp.data]
                except Exception as e:
                    logger.error(f"OpenAI embed error: {e}")

        # Fallback to fastembed if requested provider failed
        fallback_model = self._get_fastembed()
        if fallback_model:
            try:
                embeddings = list(fallback_model.embed(texts))
                return [emb.tolist() if hasattr(emb, "tolist") else list(emb) for emb in embeddings]
            except Exception:
                pass

        return [self._fallback_dummy_embed(t) for t in texts]

    def embed_query(self, query: str) -> List[float]:
        results = self.embed_documents([query])
        return results[0] if results else self._fallback_dummy_embed(query)

    def _fallback_dummy_embed(self, text: str, dim: int = 384) -> List[float]:
        val = sum(ord(c) for c in text[:100]) % 100 / 100.0
        vec = [val] * dim
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


embedding_service = EmbeddingService()
