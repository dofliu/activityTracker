"""RAG 選用依賴（`pip install "omnicontext[rag]"`）有沒有裝好——單一定義、到處查。

ADR-009 早就規定主服務程序不得 import chromadb／fastembed／rank_bm25／jieba；
2026-09-16 起這些套件連「宣告」都改成選用 extra（ROADMAP §13 R0／TODO D1）。
所以主服務要能在**沒有**這些套件的環境正常啟動，而每一條會用到它們的路徑
（建索引 job、檢索 worker、預熱）都要在動手前先問這裡，並回一句能照做的話，
而不是讓子程序在 import 時 traceback。

只用 ``importlib.util.find_spec``：不 import、不載入、幾微秒；因此不做快取，
測試可以直接 monkeypatch ``missing_index_packages``。
"""

from __future__ import annotations

from importlib.util import find_spec
from typing import Dict, List

# import 名稱 → pip 套件名。索引／檢索四件缺一不可；解析器四件缺了只是該格式退化成 stub。
INDEX_PACKAGES: Dict[str, str] = {
    "chromadb": "chromadb",
    "fastembed": "fastembed",
    "rank_bm25": "rank-bm25",
    "jieba": "jieba",
}
PARSER_PACKAGES: Dict[str, str] = {
    "fitz": "pymupdf",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "openpyxl": "openpyxl",
}
INSTALL_HINT = 'pip install "omnicontext[rag]"'


class RagExtraNotInstalled(RuntimeError):
    """知識庫功能需要的選用依賴沒裝；訊息就是修法。"""

    def __init__(self, missing: List[str]):
        self.missing = list(missing)
        super().__init__(
            f"知識庫（DeskRAG）需要的選用依賴未安裝：{', '.join(self.missing)}。"
            f"請執行 {INSTALL_HINT} 後重啟服務。"
        )


def _missing(packages: Dict[str, str]) -> List[str]:
    missing: List[str] = []
    for module_name, pip_name in packages.items():
        try:
            present = find_spec(module_name) is not None
        except (ImportError, ValueError):  # 壞掉的 namespace／半安裝：當作沒有
            present = False
        if not present:
            missing.append(pip_name)
    return missing


def missing_index_packages() -> List[str]:
    """索引與檢索必需卻缺席的 pip 套件名；空清單＝可以建索引、可以啟動檢索 worker。"""
    return _missing(INDEX_PACKAGES)


def missing_parser_packages() -> List[str]:
    return _missing(PARSER_PACKAGES)


def rag_extra_installed() -> bool:
    return not missing_index_packages()


def rag_extra_status() -> Dict[str, object]:
    """給狀態端點與驗收中心用的一份說明；欄位名稱是對外契約。"""
    missing = missing_index_packages()
    return {
        "extra_installed": not missing,
        "extra_missing": missing,
        "parser_missing": missing_parser_packages(),
        "install_hint": INSTALL_HINT,
    }


def require_rag_extra() -> None:
    missing = missing_index_packages()
    if missing:
        raise RagExtraNotInstalled(missing)
