"""Retrieval strategies.

這個套件的 `__init__` 故意採 lazy export：`registry`、各 retriever 一經 import
就會建立 Chroma client 並準備 BM25／embedding；主服務只需要 `base`、`catalog`
與 `context` 這幾個輕量模組，不應因為 `import rag.retrieval.context` 就把整個
索引堆疊載進主程序（`tests/test_rag_retrieval_worker.py` 以乾淨直譯器守門）。
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from rag.retrieval.base import BaseRetriever, CitationSource

_LAZY_EXPORTS = {
    "RetrieverRegistry": "rag.retrieval.registry",
    "retriever_registry": "rag.retrieval.registry",
    "HybridRRFRetriever": "rag.retrieval.hybrid_rrf_retriever",
    "WeightedFusionRetriever": "rag.retrieval.weighted_fusion_retriever",
    "VectorRetriever": "rag.retrieval.vector_retriever",
    "BM25Retriever": "rag.retrieval.bm25_retriever",
}

__all__ = ["BaseRetriever", "CitationSource", *_LAZY_EXPORTS.keys()]


def __getattr__(name: str) -> Any:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module 'rag.retrieval' has no attribute {name!r}")
    return getattr(import_module(module_name), name)
