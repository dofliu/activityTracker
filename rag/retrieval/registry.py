from typing import Dict, List, Optional
from rag.retrieval.base import BaseRetriever, CitationSource
from rag.retrieval.hybrid_rrf_retriever import HybridRRFRetriever
from rag.retrieval.weighted_fusion_retriever import WeightedFusionRetriever
from rag.retrieval.vector_retriever import VectorRetriever
from rag.retrieval.bm25_retriever import BM25Retriever
from rag.config import rag_settings


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
        if not citations:
            return "（檢索無符合的參考文件）"

        lines = ["【參考知識庫文件切片】：\n"]
        for cit in citations:
            src_info = f"來源 [{cit.index}]: 《{cit.filename}》"
            loc = []
            if cit.page:
                loc.append(f"第 {cit.page} 頁")
            if cit.slide:
                loc.append(f"第 {cit.slide} 張投影片")
            if cit.sheet:
                loc.append(f"工作表: {cit.sheet}")
            if cit.title:
                loc.append(f"章節: {cit.title}")
            if loc:
                src_info += f" ({', '.join(loc)})"

            src_info += f" [檔案路徑: {cit.file_path}]"
            lines.append(f"--- {src_info} ---")
            lines.append(cit.content.strip())
            lines.append("")

        return "\n".join(lines)


retriever_registry = RetrieverRegistry()
