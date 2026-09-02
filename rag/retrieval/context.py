"""Light-weight helpers shared by the main service and the retrieval worker.

這個模組故意不 import Chroma、BM25 或 embedding：主服務在 worker 模式下只需要
把 worker 回傳的 citation 組成 system prompt，不應因此把大型索引載進自己的記憶體。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from rag.retrieval.base import CitationSource


def citations_from_payload(items: Iterable[Dict[str, Any]]) -> List[CitationSource]:
    """Rebuild typed citations from the JSON the retrieval worker sent back."""
    return [CitationSource(**item) for item in items]


def format_context_prompt(citations: List[CitationSource]) -> str:
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
