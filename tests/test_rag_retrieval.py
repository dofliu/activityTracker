import pytest
from rag.retrieval.registry import retriever_registry
from rag.retriever import BM25Service
from rag.retrieval.base import CitationSource


def test_bm25_service(monkeypatch):
    # 單元測試不得覆寫使用者設定中的正式 BM25 pickle。
    monkeypatch.setattr(BM25Service, "_save_index", lambda self: None)
    bm25 = BM25Service()
    chunks = [
        {"chunk_id": "c1", "content": "OmniContext is a personal activity tracker", "metadata": {"filename": "f1.md"}},
        {"chunk_id": "c2", "content": "DeskRAG is a local document knowledge base", "metadata": {"filename": "f2.md"}},
        {"chunk_id": "c3", "content": "Python and FastAPI are used for backend APIs", "metadata": {"filename": "f3.py"}},
    ]
    bm25.build_index(chunks)
    results = bm25.query("DeskRAG document knowledge", top_k=2)
    assert len(results) >= 1
    assert results[0]["chunk_id"] == "c2"


def test_retriever_registry():
    strategies = retriever_registry.list_strategies()
    strategy_names = [s["name"] for s in strategies]
    assert "hybrid_rrf" in strategy_names
    assert "weighted_fusion" in strategy_names
    assert "vector_only" in strategy_names
    assert "bm25_only" in strategy_names

    citations = [
        CitationSource(
            index=1,
            chunk_id="c1",
            file_path="C:/docs/paper.pdf",
            filename="paper.pdf",
            file_type="pdf",
            page=3,
            content="Mathematical derivation of the theorem.",
            score=0.95,
            retrieval_type="hybrid"
        )
    ]
    prompt = retriever_registry.format_context_prompt(citations)
    assert "paper.pdf" in prompt
    assert "第 3 頁" in prompt
    assert "Mathematical derivation" in prompt
