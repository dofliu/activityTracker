import os
import pickle
import logging
import jieba
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from rank_bm25 import BM25Okapi
from rag.config import rag_settings

logger = logging.getLogger("OmniContext.RAG.Retriever")


class CitationSource(BaseModel):
    index: int
    chunk_id: str
    file_path: str
    filename: str
    file_type: str
    page: Optional[int] = None
    slide: Optional[int] = None
    sheet: Optional[str] = None
    title: Optional[str] = None
    content: str
    score: float
    retrieval_type: str


class BM25Service:
    def __init__(self):
        self.bm25: Optional[BM25Okapi] = None
        self.corpus_chunks: List[Dict[str, Any]] = []
        self._load_index()

    def _tokenize(self, text: str) -> List[str]:
        text = text.lower()
        tokens = list(jieba.cut_for_search(text))
        filtered = [t.strip() for t in tokens if len(t.strip()) > 0 and t.strip() not in ["\n", "\t", " "]]
        return filtered

    def build_index(self, chunks: List[Dict[str, Any]]):
        self.corpus_chunks = chunks
        if not chunks:
            self.bm25 = None
            self._save_index()
            return

        tokenized_corpus = [self._tokenize(c["content"]) for c in chunks]
        self.bm25 = BM25Okapi(tokenized_corpus)
        self._save_index()

    def add_or_update_chunks(self, new_chunks: List[Dict[str, Any]]):
        chunk_map = {c["chunk_id"]: c for c in self.corpus_chunks}
        for nc in new_chunks:
            chunk_map[nc["chunk_id"]] = nc
        self.corpus_chunks = list(chunk_map.values())
        self.build_index(self.corpus_chunks)

    def delete_by_file_path(self, file_path: str):
        self.corpus_chunks = [c for c in self.corpus_chunks if c.get("metadata", {}).get("file_path") != file_path]
        self.build_index(self.corpus_chunks)

    def query(self, query_text: str, top_k: int = 10) -> List[Dict[str, Any]]:
        if not self.bm25 or not self.corpus_chunks:
            return []

        tokenized_query = self._tokenize(query_text)
        if not tokenized_query:
            return []

        scores = self.bm25.get_scores(tokenized_query)
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]

        results = []
        max_score = max(scores) if len(scores) > 0 and max(scores) > 0 else 1.0

        for idx in top_indices:
            score = scores[idx]
            if score <= 0:
                continue
            item = self.corpus_chunks[idx]
            norm_score = float(score / max_score)
            results.append({
                "chunk_id": item["chunk_id"],
                "content": item["content"],
                "metadata": item.get("metadata", {}),
                "score": norm_score,
                "retrieval_type": "bm25"
            })
        return results

    def _save_index(self):
        try:
            bm25_path = rag_settings.BM25_PATH
            with open(bm25_path, "wb") as f:
                pickle.dump({"chunks": self.corpus_chunks}, f)
        except Exception as e:
            logger.warning(f"BM25 save error: {e}")

    def _load_index(self):
        bm25_path = rag_settings.BM25_PATH
        if os.path.exists(bm25_path):
            try:
                with open(bm25_path, "rb") as f:
                    data = pickle.load(f)
                    chunks = data.get("chunks", [])
                    if chunks:
                        self.build_index(chunks)
            except Exception as e:
                logger.warning(f"BM25 load error: {e}")


bm25_service = BM25Service()
