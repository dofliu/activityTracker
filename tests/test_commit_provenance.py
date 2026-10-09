"""commit 來源歸因的三種證據分開記帳（TODO F1，ADR-034）。

鎖住的是邊界，不是演算法：只有白名單 trailer 能讓 ``ai_declared`` 成立；
時間鄰近與相似度各自帶 ADR-006 既有的 claim boundary，三個區塊互不污染。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core import context_memory
from core.commit_provenance import (
    LEVEL_SIMILARITY,
    LEVEL_TEMPORAL,
    LEVEL_TRAILER,
    TRAILER_CLAIM_BOUNDARY,
    build_commit_provenance,
    classify_trailer,
    parse_trailers,
)
from core.models import AIPromptEvent, GitActivityEvent
from core.semantic_index import build_semantic_index
from tests.test_context_memory import DictConfig, TempDatabase

COMMIT_AT = datetime(2026, 10, 1, 15, 0, 0)
CLAUDE = "Claude <noreply@anthropic.com>"


class AlwaysSimilarProvider:
    """每段文字都映到同一個向量：任何 AI turn 對 commit message 的相似度都是 1.0。"""

    model = "fake-provenance-v1"

    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


class BrokenProvider:
    model = "fake-broken"

    def embed(self, texts):
        raise ConnectionError("ollama is not running")


def _cfg():
    return DictConfig({
        "semantic_index": {"embedding_model": "fake-provenance-v1", "default_top_k": 6},
        "context_memory": {"related_threshold": 0.5},
    })


def _db(tmp_path, message, *, conversation_id="local-uuid-1", ai_minutes_before=10):
    database = TempDatabase(tmp_path / "provenance.db")
    with database.session_scope() as session:
        session.add(AIPromptEvent(
            timestamp=COMMIT_AT - timedelta(minutes=ai_minutes_before),
            platform="claude_code",
            conversation_id=conversation_id,
            prompt_text="please refactor the rollback helper",
            response_text="done",
            project_tag="alpha",
            response_status="final_candidate",
            turn_key="turn-1",
        ))
        session.add(GitActivityEvent(
            timestamp=COMMIT_AT,
            repo_name="alpha",
            repo_path="/work/alpha",
            commit_hash="abcdef12",
            message=message,
        ))
    with database.session_scope() as session:
        commit_id = session.query(GitActivityEvent.id).scalar()
    return database, commit_id


# --- trailer 解析：只認最後一段、只認白名單 key ---


def test_single_paragraph_subject_is_never_a_trailer():
    assert parse_trailers("docs: update readme") == []
    assert parse_trailers("refactor: Co-Authored-By: Claude <noreply@anthropic.com>") == []


def test_last_paragraph_must_be_all_trailer_lines():
    message = "feat: x\n\nbody text\nCo-Authored-By: " + CLAUDE
    assert parse_trailers(message) == []


def test_conventional_prefix_in_last_paragraph_is_not_evidence():
    # git 自己的 trailer 解析會把這種段落認成 trailer（ADR-034 Context 3）；白名單不認。
    message = "feat: x\n\ndocs: also touched the readme"
    assert parse_trailers(message) == [("docs", "also touched the readme")]
    assert classify_trailer("docs", "also touched the readme") is None


def test_non_anthropic_co_author_is_recorded_but_not_ai():
    item = classify_trailer("Co-authored-by", "Alice <alice@example.com>")
    assert item["kind"] == "co_author" and item["ai"] is False and item["tool"] is None


def test_allowlisted_trailers_are_ai_and_case_insensitive():
    assert classify_trailer("co-authored-by", CLAUDE)["ai"] is True
    session = classify_trailer("Claude-Session", "https://claude.ai/code/session_01ABC")
    assert session["ai"] is True and session["session_id"] == "session_01ABC"


# --- ai_declared 只看 trailer ---


def test_commit_without_trailer_is_never_ai_declared_even_with_strong_other_evidence(tmp_path):
    database, commit_id = _db(tmp_path, "fix: rollback helper\n\nRewrote the retry loop.")
    provider = AlwaysSimilarProvider()
    build_semantic_index(database=database, cfg=_cfg(), provider=provider)

    result = build_commit_provenance(
        commit_id, database=database, include_similarity=True, provider=provider, cfg=_cfg(),
    )

    evidence = result["evidence"]
    assert evidence[LEVEL_TEMPORAL]["status"] == "observed"
    assert evidence[LEVEL_SIMILARITY]["status"] == "observed"
    assert evidence[LEVEL_SIMILARITY]["matches"][0]["score"] == pytest.approx(1.0)
    assert evidence[LEVEL_TRAILER]["found"] is False
    assert result["ai_declared"] is False


def test_human_co_author_alone_does_not_make_commit_ai_declared(tmp_path):
    database, commit_id = _db(tmp_path, "fix: x\n\nCo-authored-by: Alice <alice@example.com>")
    result = build_commit_provenance(commit_id, database=database)
    assert result["evidence"][LEVEL_TRAILER]["trailers"][0]["ai"] is False
    assert result["ai_declared"] is False


def test_anthropic_co_author_trailer_makes_commit_ai_declared(tmp_path):
    database, commit_id = _db(tmp_path, f"fix: x\n\nbody\n\nCo-Authored-By: {CLAUDE}")
    result = build_commit_provenance(commit_id, database=database)
    assert result["ai_declared"] is True
    assert result["evidence"][LEVEL_TRAILER]["found"] is True


# --- session：只認精確相等 ---


def test_session_trailer_resolves_only_on_exact_conversation_id(tmp_path):
    message = f"feat: x\n\nCo-Authored-By: {CLAUDE}\nClaude-Session: https://claude.ai/code/local-uuid-1"
    database, commit_id = _db(tmp_path, message)
    link = build_commit_provenance(commit_id, database=database)["evidence"][LEVEL_TRAILER]["sessions"][0]
    assert link["resolution"] == "resolved"
    assert link["source_refs"] == ["ai_prompt_events:1"]


def test_cloud_session_id_is_honestly_unresolved_not_filled_by_time(tmp_path):
    message = "feat: x\n\nClaude-Session: https://claude.ai/code/session_01CLOUD"
    database, commit_id = _db(tmp_path, message, conversation_id="session_01CLOUD-but-not-equal")
    result = build_commit_provenance(commit_id, database=database)
    link = result["evidence"][LEVEL_TRAILER]["sessions"][0]
    assert link["resolution"] == "unresolved" and link["source_refs"] == []
    # 時間上同專案確實有 AI turn——但它只出現在 temporal_only，不被拿去補 trailer。
    assert result["evidence"][LEVEL_TEMPORAL]["candidates"][0]["source_ref"] == "ai_prompt_events:1"
    assert result["ai_declared"] is True  # 宣告本身仍成立：commit 說了它出自某個 session


# --- 三個區塊互不污染，claim boundary 沿用既有字串 ---


def test_each_block_carries_its_own_level_and_existing_boundary(tmp_path):
    database, commit_id = _db(tmp_path, f"fix: x\n\nCo-Authored-By: {CLAUDE}")
    provider = AlwaysSimilarProvider()
    build_semantic_index(database=database, cfg=_cfg(), provider=provider)
    evidence = build_commit_provenance(
        commit_id, database=database, include_similarity=True, provider=provider, cfg=_cfg(),
    )["evidence"]

    assert set(evidence) == {LEVEL_TRAILER, LEVEL_TEMPORAL, LEVEL_SIMILARITY}
    for key, block in evidence.items():
        assert block["level"] == key
    assert evidence[LEVEL_TRAILER]["claim_boundary"] is TRAILER_CLAIM_BOUNDARY
    assert evidence[LEVEL_TEMPORAL]["claim_boundary"] is context_memory.SESSION_CLAIM_BOUNDARY
    assert evidence[LEVEL_SIMILARITY]["claim_boundary"] is context_memory.RELATED_CLAIM_BOUNDARY
    boundaries = [block["claim_boundary"] for block in evidence.values()]
    assert len(set(boundaries)) == 3
    # 相似度只回 AI turn：commit 跟自己相似不是歸因。
    assert all(m["source_ref"].startswith("ai_prompt_events:") for m in evidence[LEVEL_SIMILARITY]["matches"])
    # 時間鄰近與相似度的候選沒有任何 AI 判定欄位。
    for key in (LEVEL_TEMPORAL, LEVEL_SIMILARITY):
        rows = evidence[key].get("candidates", []) + evidence[key].get("matches", [])
        assert rows and all("ai" not in row and "tool" not in row for row in rows)


def test_existing_context_endpoints_still_return_the_same_boundary_strings():
    assert "do not prove task continuity" in context_memory.SESSION_CLAIM_BOUNDARY
    assert "does not validate truth" in context_memory.RELATED_CLAIM_BOUNDARY


def test_temporal_window_excludes_turns_outside_it_and_after_commit(tmp_path):
    database, commit_id = _db(tmp_path, "fix: x\n\nbody", ai_minutes_before=200)
    result = build_commit_provenance(commit_id, database=database, window_minutes=120)
    assert result["evidence"][LEVEL_TEMPORAL]["status"] == "empty"
    later = tmp_path / "later"
    later.mkdir()
    database2, commit_id2 = _db(later, "fix: x\n\nbody", ai_minutes_before=-5)
    assert build_commit_provenance(commit_id2, database=database2)["evidence"][LEVEL_TEMPORAL]["status"] == "empty"


# --- 相似度：預設不跑、壞了降級不丟例外 ---


def test_similarity_is_not_requested_by_default_and_degrades_when_unavailable(tmp_path):
    database, commit_id = _db(tmp_path, "fix: x\n\nbody")
    default = build_commit_provenance(commit_id, database=database)
    assert default["evidence"][LEVEL_SIMILARITY]["status"] == "not_requested"

    broken = build_commit_provenance(
        commit_id, database=database, include_similarity=True, provider=BrokenProvider(), cfg=_cfg(),
    )
    block = broken["evidence"][LEVEL_SIMILARITY]
    assert block["status"] == "unavailable" and "ConnectionError" in block["reason"]


def test_unknown_commit_returns_none(tmp_path):
    database, _ = _db(tmp_path, "fix: x")
    assert build_commit_provenance(999, database=database) is None
