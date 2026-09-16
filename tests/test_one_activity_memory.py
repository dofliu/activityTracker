"""一份活動記憶（ADR-023，TODO D7）。

2026-09-16 之前同一批活動被 embedding 兩次：`core/semantic_index`（核心、Ollama ＋
SQLite）與 `rag/activity_indexer`（Chroma ＋ BM25）各存一份，而且兩邊對「活動」的
定義還不一樣——檔案事件只有核心看得到，秘書筆記與微摘要只有 RAG 看得到。同一個問題
問 `omni ask` 與問知識庫對話會拿到不同的證據，沒有任何東西會說出這件事。

這裡把 ADR-023 的決定寫成測試：

1. 活動記憶只有一份定義，而且在**核心**——`rag/` 底下沒有任何模組再讀活動資料表。
2. 同一筆活動只 embedding 一次；內容沒變就不重算。
3. 知識庫對話的活動段來自同一份核心索引，每筆都回溯得到原始 SQLite row。
4. Ollama 沒開或索引是空的，只少活動段，文件段照常（反之亦然：沒裝 `[rag]` 仍有活動記憶）。
5. 報告同步會把舊版寫進 RAG 的活動切片清掉，並把清掉幾筆寫進收據。
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.models import (
    ActivityMicroSummary,
    AIPromptEvent,
    Base,
    GitActivityEvent,
    SecretaryNote,
    SemanticDocument,
)
from core.semantic_index import (
    ACTIVITY_SOURCE_TYPES,
    build_semantic_index,
    collect_source_documents,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 16, 10, 0)

# 活動資料表只能由核心索引讀；rag/ 底下再出現就是第二份活動記憶又長回來了
ACTIVITY_MODELS = (
    "AIPromptEvent", "GitActivityEvent", "FileActivityEvent",
    "OpenLoop", "ProjectState", "SecretaryNote", "ActivityMicroSummary",
)


class DictConfig:
    def __init__(self, data=None):
        self.data = data or {}

    def get(self, key_path, default=None):
        value = self.data
        for key in key_path.split("."):
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]
        return value


class TempDatabase:
    def __init__(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine)

    @contextmanager
    def session_scope(self):
        session = self.factory()
        try:
            yield session
            session.commit()
        finally:
            session.close()


class FakeProvider:
    """固定維度的假 embedding；記錄被要求算過幾次，用來證明沒有重複計算。"""

    model = "fake-embed"

    def __init__(self):
        self.calls: list[str] = []

    def embed(self, texts):
        self.calls.extend(texts)
        return [[float(len(text) % 7), 1.0, 0.5] for text in texts]


@pytest.fixture
def db():
    database = TempDatabase()
    with database.session_scope() as session:
        session.add_all([
            AIPromptEvent(platform="claude_code", project_tag="alpha", prompt_text="修 CI 的那個 job",
                          response_status="final_candidate", response_text="改了 workflow", timestamp=NOW),
            GitActivityEvent(repo_name="alpha", repo_path="/r/alpha", commit_hash="abc1234567",
                             message="fix ci", timestamp=NOW - timedelta(hours=1)),
            SecretaryNote(kind="preference", body="回答用繁體中文", source="web", created_at=NOW),
            ActivityMicroSummary(period_start=NOW - timedelta(hours=2), period_end=NOW - timedelta(hours=1),
                                 provider="ollama", summary_text="在修 CI", event_count=3),
        ])
    return database


# ---- 1. 只有一份定義，而且在核心 ----------------------------------------


def test_activity_sources_are_defined_once_in_the_core_index(db):
    documents = collect_source_documents(database=db)
    kinds = {d.source_type for d in documents}
    assert kinds <= set(ACTIVITY_SOURCE_TYPES)
    # 原本只有 RAG 看得到的兩種，現在核心也看得到（搬家沒有弄丟任何來源）
    assert {"secretary_note", "micro_summary"} <= kinds
    assert {"ai_turn", "git_commit"} <= kinds


def test_rag_no_longer_reads_any_activity_table():
    """`rag/` 不得再 import 活動資料表——那正是第二份活動記憶的入口。"""
    pattern = re.compile("|".join(ACTIVITY_MODELS))
    offenders = [
        f"{path.relative_to(ROOT).as_posix()}:{number}"
        for path in sorted((ROOT / "rag").rglob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert offenders == [], offenders

    # 這條掃描真的抓得到東西：舊版 activity_indexer 的 import 段就會被擋下來
    legacy = "from core.models import (\n    ActivityMicroSummary,\n    AIPromptEvent,\n)"
    assert [line for line in legacy.splitlines() if pattern.search(line)]


def test_the_old_activity_indexer_module_is_gone():
    assert not (ROOT / "rag" / "activity_indexer.py").exists()
    assert (ROOT / "rag" / "report_indexer.py").exists()


# ---- 2. 每筆只算一次 ------------------------------------------------------


def test_each_activity_row_is_embedded_once_and_not_recomputed(db):
    provider = FakeProvider()
    cfg = DictConfig({"semantic_index": {"enabled": True}})
    first = build_semantic_index(database=db, cfg=cfg, provider=provider)
    assert first["status"] == "indexed" and first["indexed"] == first["source_documents"]

    with db.session_scope() as session:
        rows = session.query(SemanticDocument).all()
        keys = [(row.source_type, row.source_id) for row in rows]
    assert len(keys) == len(set(keys)) == first["source_documents"]

    calls_after_first = len(provider.calls)
    second = build_semantic_index(database=db, cfg=cfg, provider=provider)
    assert second["indexed"] == 0 and second["unchanged"] == second["source_documents"]
    assert len(provider.calls) == calls_after_first  # 內容沒變就不再算一次


# ---- 3./4. 對話的活動段 ---------------------------------------------------


def _chat_request():
    from rag.router import ChatRequest, MessageItem

    return ChatRequest(messages=[MessageItem(role="user", content="CI 那件事後來怎麼了？")])


def _fake_search_result():
    return {
        "sources": [{
            "score": 0.83, "source_type": "ai_turn", "source_id": "1",
            "source_ref": "ai_prompt_events:1", "project_key": "alpha",
            "title": "claude_code AI turn", "trust_status": "final_candidate",
            "source_updated_at": "2026-09-16T10:00:00", "excerpt": "Prompt:\n修 CI 的那個 job",
            "citation": "S1",
        }]
    }


def test_chat_activity_context_comes_from_the_core_index(monkeypatch):
    import rag.router as router_module

    monkeypatch.setattr("core.semantic_index.semantic_search", lambda *a, **k: _fake_search_result())
    monkeypatch.setattr(router_module, "_retrieve_documents", lambda query, req: [])

    citations = router_module._retrieve_citations("CI", _chat_request())
    assert len(citations) == 1
    only = citations[0]
    assert only.source_domain == "activity" and only.source_type == "ai_turn"
    assert only.source_ref == "ai_prompt_events:1"      # 回得到原始 SQLite row
    assert only.trust_status == "final_candidate"       # 可信度照實帶出來
    assert only.retrieval_type == "semantic_index"      # 不是 Chroma 的第二份


def test_activity_citations_are_numbered_after_the_document_ones(monkeypatch):
    import rag.router as router_module
    from rag.retrieval.base import CitationSource

    document = CitationSource(index=1, chunk_id="c1", file_path="/d/a.md", filename="a.md",
                              file_type=".md", content="文件內容", score=0.9, retrieval_type="hybrid_rrf")
    monkeypatch.setattr(router_module, "_retrieve_documents", lambda query, req: [document])
    monkeypatch.setattr("core.semantic_index.semantic_search", lambda *a, **k: _fake_search_result())

    citations = router_module._retrieve_citations("CI", _chat_request())
    assert [c.index for c in citations] == [1, 2]
    assert [c.source_domain for c in citations] == ["document", "activity"]


def test_activity_section_degrades_alone_when_ollama_is_down(monkeypatch):
    """Ollama 沒開：只少活動段，文件段照常——而且絕不讓整段對話失敗。"""
    import rag.router as router_module
    from rag.retrieval.base import CitationSource

    def boom(*args, **kwargs):
        raise ConnectionError("ollama offline")

    document = CitationSource(index=1, chunk_id="c1", file_path="/d/a.md", filename="a.md",
                              file_type=".md", content="文件內容", score=0.9, retrieval_type="hybrid_rrf")
    monkeypatch.setattr(router_module, "_retrieve_documents", lambda query, req: [document])
    monkeypatch.setattr("core.semantic_index.semantic_search", boom)

    citations = router_module._retrieve_citations("CI", _chat_request())
    assert [c.source_domain for c in citations] == ["document"]


def test_activity_context_survives_without_the_rag_extra(monkeypatch):
    """沒裝 `[rag]`：文件段是空的，活動記憶照樣在——記憶不綁選用依賴。"""
    import rag.router as router_module

    monkeypatch.setattr(router_module, "_retrieve_documents", lambda query, req: [])
    monkeypatch.setattr("core.semantic_index.semantic_search", lambda *a, **k: _fake_search_result())
    citations = router_module._retrieve_citations("CI", _chat_request())
    assert len(citations) == 1 and citations[0].source_domain == "activity"


def test_context_prompt_keeps_activity_and_documents_apart():
    from rag.retrieval.base import CitationSource
    from rag.retrieval.context import format_context_prompt

    document = CitationSource(index=1, chunk_id="c1", file_path="/d/a.md", filename="a.md",
                              file_type=".md", content="文件內容", score=0.9, retrieval_type="hybrid_rrf")
    activity = CitationSource(index=2, chunk_id="activity:ai_prompt_events:1", file_path="ai_prompt_events:1",
                              filename="[alpha] 🧠 AI turn", file_type=".ai_turn", content="Prompt: 修 CI",
                              score=0.8, retrieval_type="semantic_index", source_domain="activity",
                              source_type="ai_turn", source_ref="ai_prompt_events:1",
                              trust_status="final_candidate")
    text = format_context_prompt([document, activity])
    assert "【參考知識庫文件切片】" in text and "【你自己的工作紀錄" in text
    assert "ai_prompt_events:1" in text and "final_candidate" in text
    assert "（檢索無符合的參考文件）" == format_context_prompt([])


# ---- 5. 舊的重複切片會被清掉 ----------------------------------------------


def test_report_sync_purges_the_legacy_activity_chunks(monkeypatch, tmp_path):
    pytest.importorskip("rank_bm25")
    from rag import report_indexer as ri

    deleted: list[str] = []

    class FakeBm25:
        def count_by_source_domain(self, domain):
            return 12 if domain == "activity" else 0

        def delete_by_source_domain(self, domain):
            deleted.append(f"bm25:{domain}")

        def add_or_update_chunks(self, chunks):
            pass

    class FakeVectorStore:
        def delete_by_source_domain(self, domain):
            deleted.append(f"chroma:{domain}")

        def add_chunks(self, chunks):
            pass

    monkeypatch.setattr(ri, "bm25_service", FakeBm25())
    monkeypatch.setattr(ri, "vector_store", FakeVectorStore())
    monkeypatch.setattr(ri, "get_config", lambda: DictConfig({"exporters": {"reports_dir": str(tmp_path)}}))
    monkeypatch.setattr(ri, "resolve_runtime_path", lambda value: Path(value))
    (tmp_path / "handoffs").mkdir()
    (tmp_path / "handoffs" / "Handoff_alpha_20260916_1000.md").write_text("# Handoff\n下一步", encoding="utf-8")

    receipt = ri.ReportIndexer().sync_all()
    assert receipt["legacy_activity_chunks_removed"] == 12
    assert "bm25:activity" in deleted and "chroma:activity" in deleted
    assert receipt["total_reports_indexed"] == 1
    assert "core/semantic_index" in receipt["claim_boundary"]
