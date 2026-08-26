import os
from typing import List, Optional
from rag.retrieval.base import BaseRetriever, CitationSource
from rag.vector_store import vector_store


class VectorRetriever(BaseRetriever):
    name: str = "vector_only"
    display_name: str = "Dense 向量檢索 (FastEmbed Vector Only)"
    description: str = "使用 FastEmbed 本地稠密語意向量進行相似度比對，適合概念與意圖檢索"

    def retrieve(self, query: str, top_k: int = 6, **kwargs) -> List[CitationSource]:
        vec_results = vector_store.query(query, top_k=top_k)
        citations: List[CitationSource] = []
        for idx, item in enumerate(vec_results):
            meta = item.get("metadata", {})
            citations.append(CitationSource(
                index=idx + 1,
                chunk_id=item["chunk_id"],
                file_path=meta.get("file_path", ""),
                filename=meta.get("filename", os.path.basename(meta.get("file_path", ""))),
                file_type=meta.get("file_type", ""),
                page=meta.get("page"),
                slide=meta.get("slide"),
                sheet=meta.get("sheet"),
                title=meta.get("title"),
                content=item["content"],
                score=round(item["score"], 4),
                retrieval_type="vector"
            ))
        return citations
