import os
from typing import List, Optional
from rag.retrieval.base import BaseRetriever, CitationSource
from rag.retriever import bm25_service


class BM25Retriever(BaseRetriever):
    name: str = "bm25_only"
    display_name: str = "BM25 關鍵字全文檢索 (Sparse Keyword Only)"
    description: str = "使用 Jieba 分詞與 BM25 統計精準匹配專有名詞、代碼與產品型號"

    def retrieve(self, query: str, top_k: int = 6, **kwargs) -> List[CitationSource]:
        bm25_results = bm25_service.query(query, top_k=top_k)
        citations: List[CitationSource] = []
        for idx, item in enumerate(bm25_results):
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
                retrieval_type="bm25"
            ))
        return citations
