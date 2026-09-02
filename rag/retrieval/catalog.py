"""Static catalogue of retrieval strategies.

`/api/v1/rag/strategies` 只需要名稱與說明；以前它 import `retriever_registry`，
而 registry 一 import 就會在主服務建立 Chroma client。這份靜態清單讓主服務
不必為了下拉選單載入任何索引；`tests/test_rag_retrieval_worker.py` 會確認
它與 registry 實際註冊的策略一致。
"""

from __future__ import annotations

from typing import Dict, List

DEFAULT_STRATEGY = "hybrid_rrf"

STRATEGY_CATALOG: List[Dict[str, str]] = [
    {
        "name": "hybrid_rrf",
        "display_name": "Hybrid RRF (向量 + BM25 倒數排名融合)",
        "description": "結合稠密語意與稀疏關鍵字排名 (Reciprocal Rank Fusion)，綜合表現最穩健",
    },
    {
        "name": "weighted_fusion",
        "display_name": "Weighted Linear Fusion (線性加權融合)",
        "description": "依據自訂 Alpha 權重線性組合向量餘弦得分與 BM25 正規化得分",
    },
    {
        "name": "vector_only",
        "display_name": "Dense 向量檢索 (FastEmbed Vector Only)",
        "description": "使用 FastEmbed 本地稠密語意向量進行相似度比對，適合概念與意圖檢索",
    },
    {
        "name": "bm25_only",
        "display_name": "BM25 關鍵字全文檢索 (Sparse Keyword Only)",
        "description": "使用 Jieba 分詞與 BM25 統計精準匹配專有名詞、代碼與產品型號",
    },
]
