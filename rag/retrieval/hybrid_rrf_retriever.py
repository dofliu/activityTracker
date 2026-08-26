import os
from typing import List, Dict, Any, Optional
from rag.retrieval.base import BaseRetriever, CitationSource
from rag.vector_store import vector_store
from rag.retriever import bm25_service
from rag.config import rag_settings


class HybridRRFRetriever(BaseRetriever):
    name: str = "hybrid_rrf"
    display_name: str = "Hybrid RRF (向量 + BM25 倒數排名融合)"
    description: str = "結合稠密語意與稀疏關鍵字排名 (Reciprocal Rank Fusion)，綜合表現最穩健"

    def __init__(self, rrf_k: int = 60):
        self.rrf_k = rrf_k

    def retrieve(self, query: str, top_k: int = 6, **kwargs) -> List[CitationSource]:
        alpha = kwargs.get("alpha")
        if alpha is None:
            alpha = rag_settings.DEFAULT_HYBRID_ALPHA
        w_vec = float(alpha)
        w_bm25 = 1.0 - w_vec

        vec_results = vector_store.query(query, top_k=top_k * 2)
        bm25_results = bm25_service.query(query, top_k=top_k * 2)

        fused: Dict[str, Dict[str, Any]] = {}

        for rank, item in enumerate(vec_results):
            cid = item["chunk_id"]
            score = w_vec * (1.0 / (self.rrf_k + rank + 1))
            if cid not in fused:
                fused[cid] = {"item": item, "score": score, "has_vec": True, "has_bm25": False}
            else:
                fused[cid]["score"] += score
                fused[cid]["has_vec"] = True

        for rank, item in enumerate(bm25_results):
            cid = item["chunk_id"]
            score = w_bm25 * (1.0 / (self.rrf_k + rank + 1))
            if cid not in fused:
                fused[cid] = {"item": item, "score": score, "has_vec": False, "has_bm25": True}
            else:
                fused[cid]["score"] += score
                fused[cid]["has_bm25"] = True

        sorted_items = sorted(fused.values(), key=lambda x: x["score"], reverse=True)[:top_k]

        citations: List[CitationSource] = []
        for idx, entry in enumerate(sorted_items):
            it = entry["item"]
            meta = it.get("metadata", {})
            r_type = "hybrid"
            if entry["has_vec"] and not entry["has_bm25"]:
                r_type = "vector"
            elif entry["has_bm25"] and not entry["has_vec"]:
                r_type = "bm25"

            citations.append(CitationSource(
                index=idx + 1,
                chunk_id=it["chunk_id"],
                file_path=meta.get("file_path", ""),
                filename=meta.get("filename", os.path.basename(meta.get("file_path", ""))),
                file_type=meta.get("file_type", ""),
                page=meta.get("page"),
                slide=meta.get("slide"),
                sheet=meta.get("sheet"),
                title=meta.get("title"),
                content=it["content"],
                score=round(entry["score"] * 1000, 3),
                retrieval_type=r_type
            ))
        return citations
