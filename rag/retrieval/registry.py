from typing import Dict, List, Optional
from rag.retrieval.base import BaseRetriever, CitationSource
from rag.retrieval.hybrid_rrf_retriever import HybridRRFRetriever
from rag.retrieval.weighted_fusion_retriever import WeightedFusionRetriever
from rag.retrieval.vector_retriever import VectorRetriever
from rag.retrieval.bm25_retriever import BM25Retriever
from rag.config import rag_settings
from rag.retrieval.context import format_context_prompt


class RetrieverRegistry:
    def __init__(self):
        self._retrievers: Dict[str, BaseRetriever] = {}
        self.default_strategy = "hybrid_rrf"

        # Register default built-in strategies
        self.register(HybridRRFRetriever())
        self.register(WeightedFusionRetriever())
        self.register(VectorRetriever())
        self.register(BM25Retriever())

    def register(self, retriever: BaseRetriever):
        self._retrievers[retriever.name] = retriever

    def get(self, name: Optional[str] = None) -> BaseRetriever:
        key = name or self.default_strategy
        if key not in self._retrievers:
            key = self.default_strategy
        return self._retrievers[key]

    def list_strategies(self) -> List[Dict[str, str]]:
        return [
            {
                "name": r.name,
                "display_name": r.display_name,
                "description": r.description
            }
            for r in self._retrievers.values()
        ]

    def retrieve(
        self,
        query: str,
        strategy: Optional[str] = None,
        top_k: Optional[int] = None,
        score_threshold: float = 0.0,
        **kwargs
    ) -> List[CitationSource]:
        retriever = self.get(strategy)
        k = top_k or rag_settings.DEFAULT_TOP_K
        citations = retriever.retrieve(query, top_k=k, **kwargs)

        if score_threshold > 0.0:
            citations = [c for c in citations if c.score >= score_threshold]

        return citations

    def format_context_prompt(self, citations: List[CitationSource]) -> str:
        return format_context_prompt(citations)


retriever_registry = RetrieverRegistry()
