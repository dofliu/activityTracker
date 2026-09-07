"""文件落後程式（ADR-021）：使用者最常手打的那句指令，變成秘書會提的一張卡。

- 只比兩個時間：文件檔最後異動 vs 那之後該 repo 的 commit 數；不判斷文件內容。
- 文件檔認前綴（README／USAGE／ROADMAP／STATUS…）與 docs/ 下的 .md；程式碼檔不算。
- **沒有文件異動紀錄的 repo 一律不提**（分不出「沒有文件」與「沒被採集到」）。
- 門檻可設；剛改完文件不提；事實區塊由 server 組出（commit 訊息、工作誌摘要）。
- 動作接既有的兩段式 L2：起草文件更新計畫 → 批准 → 依計畫改檔（不 commit）。
- 模組不讀 prompt、不呼叫 LLM、不寫任何資料；引擎失敗隔離；驗收中心 A20。
"""

from __future__ import annotations

import itertools
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core import docs_freshness as df
from core.acceptance import ITEM_IDS, build_acceptance_report
from core.agent_executor import _DRAFT_PLAN_TYPES, _draft_prompt
from core.docs_freshness import (
    collect_docs_freshness_signals,
    commits_since,
    compose_docs_facts,
    doc_baseline,
    docs_freshness_settings,
    is_doc_file,
)
from core.models import Base, FileActivityEvent, GitActivityEvent, SecretaryNote
from core.proactive_secretary import SUGGESTED_ACTIONS, build_action_proposals, why_now
from core.secretary_memory import record_observation

NOW = datetime(2026, 9, 20, 10, 0)


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
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


@pytest.fixture
def db():
    return TempDatabase()


def _cfg(**over):
    data = {"proactive_secretary": {"enabled": True, "max_proposals": 12, "docs_freshness": {}},
            "secretary_memory": {"enabled": True, "observation_ttl_days": 14},
            "exporters": {"reports_dir": "/nonexistent"}}
    data["proactive_secretary"]["docs_freshness"].update(over)
    return DictConfig(data)


_SEQ = itertools.count(1)


def _commit(db, repo, when, message="feat: 做了一件事"):
    with db.session_scope() as s:
        s.add(GitActivityEvent(timestamp=when, repo_name=repo, repo_path=f"/r/{repo}",
                               commit_hash=f"{repo}-{next(_SEQ):06d}", message=message))


def _file(db, project, when, name, path=None, file_type=None):
    with db.session_scope() as s:
        s.add(FileActivityEvent(timestamp=when, file_path=path or f"/r/{project}/{name}", file_name=name,
                                file_type=file_type or ("." + name.rsplit(".", 1)[1] if "." in name else ""),
                                action="modified", project_name=project))


def _behind(db, repo="uav", *, commits=12, docs_days=6):
    """文件在 docs_days 天前更新，之後有 commits 個 commit。"""
    baseline = NOW - timedelta(days=docs_days)
    _file(db, repo, baseline, "README.md")
    _file(db, repo, baseline - timedelta(minutes=5), "USAGE.md")
    for i in range(commits):
        _commit(db, repo, baseline + timedelta(hours=i + 1), message=f"feat: 第 {i + 1} 項變更")
    return baseline


# ---- 文件檔判定 ----


@pytest.mark.parametrize("name, path, expected", [
    ("README.md", "/r/x/README.md", True),
    ("readme_en.md", "/r/x/readme_en.md", True),
    ("USAGE.md", "/r/x/docs/USAGE.md", True),
    ("STATUS.yaml", "/r/x/STATUS.yaml", True),
    ("ADR-021-x.md", "/r/x/docs/ADR-021-x.md", True),
    ("NEXT_SESSION.md", "/r/x/docs/NEXT_SESSION.md", True),
    ("notes.md", "/r/x/docs/notes.md", True),            # docs/ 下的 .md
    ("notes.md", "/r/x/src/notes.md", False),            # 不在 docs/、檔名也不是文件
    ("main.py", "/r/x/main.py", False),
    ("app.js", "/r/x/web/app.js", False),
    ("config.yaml", "/r/x/config.yaml", False),
])
def test_doc_file_detection(name, path, expected):
    assert is_doc_file(name, path) is expected


def test_doc_baseline_uses_the_newest_doc_event_and_lists_names(db):
    _file(db, "uav", NOW - timedelta(days=9), "ROADMAP.md")
    _file(db, "uav", NOW - timedelta(days=5), "README.md")
    _file(db, "uav", NOW - timedelta(hours=2), "main.py")        # 程式碼不算文件
    base = doc_baseline("uav", database=db, since=NOW - timedelta(days=60))
    assert base is not None
    baseline, names = base
    assert baseline == NOW - timedelta(days=5) and names == ["README.md", "ROADMAP.md"]
    assert doc_baseline("nobody", database=db, since=NOW - timedelta(days=60)) is None


def test_commits_since_counts_and_keeps_subject_lines(db):
    baseline = NOW - timedelta(days=3)
    _commit(db, "uav", baseline - timedelta(hours=1), message="before: 不該算")
    _commit(db, "uav", baseline + timedelta(hours=1), message="fix: 修一個 bug\n\n第二行不該進標題")
    _commit(db, "uav", baseline + timedelta(hours=2), message="feat: 加一個功能")
    _commit(db, "other", baseline + timedelta(hours=3), message="別的 repo")
    total, subjects, newest = commits_since("uav", baseline, database=db)
    assert total == 2 and subjects == ["feat: 加一個功能", "fix: 修一個 bug"]
    assert newest == baseline + timedelta(hours=2)


# ---- 訊號 ----


def test_signal_when_commits_pile_up_after_the_last_doc_change(db):
    baseline = _behind(db, "uav", commits=12, docs_days=6)
    record_observation(title="工作誌", body="uav 這幾天在改 API", source_ref="daily_digest:2026-09-19:uav",
                       project_key="uav", source="daily_digest", database=db, now=NOW - timedelta(days=1))
    signals, meta = collect_docs_freshness_signals(database=db, cfg=_cfg(), now=NOW)
    assert meta["used"] is True and meta["signals"] == 1
    assert meta["repos_considered"]["uav"] == {"commits_since_docs": 12, "docs_idle_days": 6.0}
    sig = signals[0]
    assert sig["signal_type"] == "docs_behind_code" and sig["project_key"] == "uav"
    assert sig["title"] == "uav 的文件落後了：文件最後一次更新後又有 12 個 commit"
    assert f"{baseline:%m-%d}" in sig["detail"] and "README.md" in sig["detail"]
    assert sig["score"] == pytest.approx(0.61) and sig["age_days"] == 6.0
    facts = sig["docs_facts"]
    assert "專案／repo：uav" in facts and "那之後的 commit 數：12" in facts
    assert "feat: 第 12 項變更" in facts and "uav 這幾天在改 API" in facts
    assert SUGGESTED_ACTIONS["docs_behind_code"] and why_now("docs_behind_code", 6.0)


def test_a_repo_without_any_doc_event_is_never_proposed(db):
    for i in range(30):
        _commit(db, "nodocs", NOW - timedelta(days=10) + timedelta(hours=i))
    _file(db, "nodocs", NOW - timedelta(days=9), "main.py")      # 只有程式碼被採集到
    signals, meta = collect_docs_freshness_signals(database=db, cfg=_cfg(), now=NOW)
    assert signals == [] and meta["skipped_no_doc_baseline"] == ["nodocs"]
    assert "nodocs" not in meta["repos_considered"]


def test_below_either_threshold_is_not_proposed(db):
    _behind(db, "few", commits=3, docs_days=6)                   # commit 不夠
    _behind(db, "fresh", commits=20, docs_days=0)                # 文件今天才改
    signals, meta = collect_docs_freshness_signals(database=db, cfg=_cfg(), now=NOW)
    assert signals == []
    assert meta["repos_considered"]["few"]["commits_since_docs"] == 3
    assert meta["repos_considered"]["fresh"]["docs_idle_days"] < 1


def test_thresholds_come_from_config_and_can_be_disabled(db):
    _behind(db, "uav", commits=5, docs_days=3)
    assert collect_docs_freshness_signals(database=db, cfg=_cfg(), now=NOW)[0] == []
    loose, _meta = collect_docs_freshness_signals(database=db, cfg=_cfg(min_commits=4), now=NOW)
    assert [s["project_key"] for s in loose] == ["uav"]
    assert docs_freshness_settings(_cfg(min_commits=4, min_days=1))["min_commits"] == 4
    off = collect_docs_freshness_signals(database=db, cfg=_cfg(enabled=False), now=NOW)
    assert off == ([], {"used": False, "reason": "disabled"})


def test_only_the_worst_few_repos_are_proposed_sorted_by_score(db):
    for index, repo in enumerate(("a", "b", "c", "d")):
        _behind(db, repo, commits=10 + index * 5, docs_days=4)
    signals, _meta = collect_docs_freshness_signals(database=db, cfg=_cfg(), now=NOW)
    assert [s["project_key"] for s in signals] == ["d", "c", "b"]        # 上限 3、分數由高到低
    assert signals[0]["score"] >= signals[-1]["score"]


def test_facts_are_bounded_and_module_never_reads_prompts_or_calls_an_llm():
    facts = compose_docs_facts(
        project="uav", baseline=NOW, doc_names=["README.md"], commit_count=50,
        subjects=["x" * 400] * 12, newest_commit=NOW, digest="y" * 2000,
    )
    assert len(facts) <= df.FACTS_MAX_CHARS
    source = Path(df.__file__).read_text(encoding="utf-8")
    for forbidden in ("prompt_text", "response_text", "llm_gateway", "requests.", "httpx", "subprocess", "session.add"):
        assert forbidden not in source, forbidden


# ---- 提案引擎 ----


def _proposals(db, cfg):
    return build_action_proposals(database=db, cfg=cfg, now=NOW,
                                  extension_status={"extension": {"token_configured": False}})


def test_engine_emits_the_card_with_reason_and_reports_inputs(db):
    _behind(db, "uav", commits=12, docs_days=6)
    result = _proposals(db, _cfg())
    card = next(p for p in result["proposals"] if p["proposal_type"] == "docs_behind_code")
    assert card["project_key"] == "uav" and "12 個 commit" in card["reason"]
    assert card["why_now"] and "起草文件更新計畫" in card["suggested_action"]
    assert card["docs_facts"].startswith("專案／repo：uav")
    assert result["inputs"]["docs_freshness"]["signals"] == 1


def test_mute_suppresses_it_and_failure_is_isolated(db, monkeypatch):
    from core.secretary_memory import add_note

    _behind(db, "uav", commits=12, docs_days=6)
    add_note(kind="preference", body="不要提醒 docs_behind_code", database=db, now=NOW)
    result = _proposals(db, _cfg())
    assert not [p for p in result["proposals"] if p["proposal_type"] == "docs_behind_code"]
    assert result["inputs"]["memory_muted"] >= 1

    def boom(**_kwargs):
        raise RuntimeError("docs exploded")

    monkeypatch.setattr(df, "collect_docs_freshness_signals", boom)
    result = _proposals(db, _cfg())
    assert result["status"] == "proposal_only"
    assert result["inputs"]["docs_freshness"] == {"used": False, "reason": "error:RuntimeError"}


# ---- 接既有的兩段式 L2 ----


def test_it_reuses_the_existing_two_phase_l2_and_gets_a_docs_specific_prompt():
    assert "docs_behind_code" in _DRAFT_PLAN_TYPES        # 不新增第二套寫入路徑
    prompt = _draft_prompt({
        "proposal_type": "docs_behind_code", "project_key": "uav",
        "docs_facts": "專案／repo：uav\n那之後的 commit 數：12",
        "title": "不該出現在這個 prompt 的標題",
    })
    assert "文件更新計畫" in prompt and "README" in prompt and "USAGE" in prompt
    assert "不要編造沒有依據的進度" in prompt and "不要修改任何檔案" in prompt
    assert "那之後的 commit 數：12" in prompt
    other = _draft_prompt({"proposal_type": "stalled_open_loop", "project_key": "uav",
                           "title": "舊卡", "detail": "d", "suggested_action": "s"})
    assert "重啟行動計畫" in other and "文件更新計畫" not in other


def test_docs_facts_in_the_prompt_are_truncated_not_trusted():
    prompt = _draft_prompt({"proposal_type": "docs_behind_code", "project_key": "u", "docs_facts": "z" * 9000})
    assert prompt.count("z") == 2400


# ---- 驗收中心 A20 ----


def test_a20_reports_the_comparison_and_the_l2_gates(db):
    assert "A20" in ITEM_IDS
    cfg = _cfg()
    empty = build_acceptance_report(database=db, cfg=cfg, now=NOW, only=["A20"])["items"][0]
    assert empty["status"] == "pending" and "沒有東西可比" in empty["detail"]

    _behind(db, "fresh", commits=2, docs_days=6)
    under = build_acceptance_report(database=db, cfg=cfg, now=NOW, only=["A20"])["items"][0]
    assert under["status"] == "pending" and "文件目前跟得上" in under["detail"]

    _behind(db, "uav", commits=12, docs_days=6)
    partial = build_acceptance_report(database=db, cfg=cfg, now=NOW, only=["A20"])["items"][0]
    assert partial["status"] == "partial" and "三道門沒全開" in partial["detail"]

    open_cfg = _cfg()
    open_cfg.data["proactive_secretary"]["executor"] = {"enabled": True, "l2": {"enabled": True, "allow_write": True}}
    ready = build_acceptance_report(database=db, cfg=open_cfg, now=NOW, only=["A20"])["items"][0]
    assert ready["status"] == "needs_human" and "由你讀過再 commit" in ready["detail"]

    off = build_acceptance_report(database=db, cfg=_cfg(enabled=False), now=NOW, only=["A20"])["items"][0]
    assert off["status"] == "not_configured"


def test_acceptance_a20_writes_nothing(db):
    _behind(db, "uav", commits=12, docs_days=6)
    with db.session_scope() as s:
        before = (s.query(SecretaryNote).count(), s.query(GitActivityEvent).count(), s.query(FileActivityEvent).count())
    build_acceptance_report(database=db, cfg=_cfg(), now=NOW, only=["A20"])
    with db.session_scope() as s:
        assert (s.query(SecretaryNote).count(), s.query(GitActivityEvent).count(), s.query(FileActivityEvent).count()) == before
