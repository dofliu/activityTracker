import os
from typing import List, Dict, Any, Optional
from rag.retrieval.base import BaseRetriever, CitationSource
from rag.vector_store import vector_store
from rag.retriever import bm25_service
from rag.config import rag_settings


class WeightedFusionRetriever(BaseRetriever):
    name: str = "weighted_fusion"
    display_name: str = "Weighted Linear Fusion (線性加權融合)"
    description: str = "依據自訂 Alpha 權重線性組合向量餘弦得分與 BM25 正規化得分"

    def retrieve(self, query: str, top_k: int = 6, **kwargs) -> List[CitationSource]:
        alpha = kwargs.get("alpha")
        if alpha is None:
            alpha = rag_settings.DEFAULT_HYBRID_ALPHA
        w_vec = float(alpha)
        w_bm25 = 1.0 - w_vec

        vec_results = vector_store.query(query, top_k=top_k * 2)
        bm25_results = bm25_service.query(query, top_k=top_k * 2)

        fused: Dict[str, Dict[str, Any]] = {}

        for item in vec_results:
            cid = item["chunk_id"]
            vec_s = float(item["score"])
            if cid not in fused:
                fused[cid] = {"item": item, "vec_s": vec_s, "bm25_s": 0.0}
            else:
                fused[cid]["vec_s"] = vec_s

        for item in bm25_results:
            cid = item["chunk_id"]
            bm25_s = float(item["score"])
            if cid not in fused:
                fused[cid] = {"item": item, "vec_s": 0.0, "bm25_s": bm25_s}
            else:
                fused[cid]["bm25_s"] = bm25_s

        for cid, data in fused.items():
            data["final_score"] = w_vec * data["vec_s"] + w_bm25 * data["bm25_s"]

        sorted_items = sorted(fused.values(), key=lambda x: x["final_score"], reverse=True)[:top_k]

        citations: List[CitationSource] = []
        for idx, entry in enumerate(sorted_items):
            it = entry["item"]
            meta = it.get("metadata", {})
            r_type = "hybrid"
            if entry["vec_s"] > 0 and entry["bm25_s"] == 0:
                r_type = "vector"
            elif entry["bm25_s"] > 0 and entry["vec_s"] == 0:
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
                score=round(entry["final_score"], 4),
                retrieval_type=r_type
            ))
        return citations
