"""小秘書問候卡的契約。

- 統計只數視窗內、可回溯到資料表的活動；今天／近兩小時兩個視窗分得清楚。
- 文案由規則產生：有名字就帶、開工不久就說「才開工」、什麼都沒看到就誠實說。
- 鼓勵語依狀況選池（深夜／長時間／週末／衝很快／穩），同一天同一視窗同一句。
- LLM 潤飾預設關閉；開了也只能改語氣：多出統計裡沒有的數字就退回規則版，失敗也退回。
- API 端點與 Telegram /today 都用同一個入口。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core import secretary_greeting as sg
from core.models import (
    ActivityMicroSummary,
    AIPromptEvent,
    Base,
    FileActivityEvent,
    GitActivityEvent,
    GitHubPREvent,
    OpenLoop,
    ProjectState,
)
from core.secretary_greeting import (
    GreetingRejected,
    achievement_lines,
    build_greeting,
    choose_encouragement,
    collect_activity_stats,
    compose_greeting,
    llm_text_is_safe,
    plain_text,
    polish_with_llm,
)
from core.server import app

_LOCAL_ORIGIN = "http://127.0.0.1:8765"
NOW = datetime(2026, 9, 4, 10, 30)  # 週五上午


class DictConfig:
    def __init__(self, data):
        self.data = data

    def get(self, key_path, default=None):
        value = self.data
        for key in key_path.split("."):
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]
        return value


def _cfg(name="Dof", llm=False, provider="ollama"):
    return DictConfig({
        "proactive_secretary": {
            "greeting": {"display_name": name, "llm": {"enabled": llm, "cache_minutes": 30}},
            "llm_advisor": {"provider": provider},
        }
    })


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


@pytest.fixture(autouse=True)
def _clear_cache():
    sg._reset_llm_cache_for_tests()
    yield
    sg._reset_llm_cache_for_tests()


def _seed(db, *, now=NOW):
    """今天 08:00 開工：3 commit、1 PR 開＋1 合併、5 輪 AI、2 個 tex、2 專案、1 收掉；昨天有一堆不該算。"""
    with db.session_scope() as s:
        for i in range(3):
            s.add(GitActivityEvent(timestamp=now - timedelta(hours=2, minutes=10 * i), repo_name="alpha" if i < 2 else "beta",
                                   repo_path="/x", commit_hash=f"c{i}", message="m", insertions=10, deletions=1))
        s.add(GitActivityEvent(timestamp=now - timedelta(days=1), repo_name="old", repo_path="/x", commit_hash="old1", message="m"))
        s.add(GitHubPREvent(repo_name="alpha", pr_number=1, title="p", state="open", html_url="u", created_at=now - timedelta(hours=1), updated_at=now - timedelta(hours=1)))
        s.add(GitHubPREvent(repo_name="alpha", pr_number=2, title="p", state="merged", html_url="u", created_at=now - timedelta(days=3), updated_at=now - timedelta(minutes=30), merged_at=now - timedelta(minutes=30)))
        s.add(GitHubPREvent(repo_name="beta", pr_number=3, title="p", state="open", html_url="u", created_at=now - timedelta(days=2), updated_at=now - timedelta(days=2)))
        for i in range(5):
            s.add(AIPromptEvent(timestamp=now - timedelta(hours=2, minutes=5 * i), platform="claude_code" if i % 2 else "chatgpt", prompt_text="q"))
        s.add(AIPromptEvent(timestamp=now - timedelta(days=1), platform="gemini", prompt_text="q"))
        for i in range(2):
            s.add(FileActivityEvent(timestamp=now - timedelta(hours=2, minutes=15 * i), file_path=f"/p/t{i}.tex", file_name=f"t{i}.tex", file_type=".tex", action="modified"))
        s.add(FileActivityEvent(timestamp=now - timedelta(minutes=5), file_path="/p/a.py", file_name="a.py", file_type=".py", action="modified"))
        s.add(ProjectState(project_key="alpha", display_name="Alpha", status="active", last_activity_at=now - timedelta(minutes=20), last_action_summary="x", category="dev"))
        s.add(ProjectState(project_key="thesis", display_name="論文", status="active", last_activity_at=now - timedelta(hours=1), last_action_summary="x", category="research"))
        s.add(ProjectState(project_key="stale", display_name="Stale", status="idle", last_activity_at=now - timedelta(days=5), last_action_summary="x", category="dev"))
        s.add(OpenLoop(project_key="alpha", title="done", status="resolved", resolved_at=now - timedelta(hours=1), updated_at=now - timedelta(hours=1)))
        s.add(OpenLoop(project_key="alpha", title="open", status="open", updated_at=now - timedelta(hours=1)))
        s.add(ActivityMicroSummary(period_start=now - timedelta(minutes=50), period_end=now - timedelta(minutes=20), provider="ollama", summary_text="修 CI 並開了 PR", event_count=4))
    return db


# ---- 統計 ----


def test_stats_count_only_the_window_and_trace_to_tables():
    db = _seed(TempDatabase())
    stats = collect_activity_stats(window="today", now=NOW, database=db, cfg=_cfg(), include_usage=False)
    assert stats["commits"] == 3 and stats["commit_repos"] == ["alpha", "beta"] and stats["insertions"] == 30
    assert stats["prs_opened"] == 1 and stats["prs_merged"] == 1 and stats["prs_touched"] == 2
    assert stats["ai_turns"] == 5 and stats["ai_platforms"] == ["chatgpt", "claude_code"]
    assert stats["files_changed"] == 3 and stats["files_writing"] == 2 and stats["files_code"] == 1
    assert stats["projects_touched"] == 2 and stats["project_names"] == ["Alpha", "論文"]
    assert stats["loops_resolved"] == 1
    assert stats["recent_summaries"] == ["修 CI 並開了 PR"]
    assert stats["observed_anything"] is True
    assert stats["sources"]["commits"] == "git_activity_events" and stats["sources"]["prs"] == "github_pr_events"
    # 第一筆活動 08:20 → 開工約 2.2 小時
    assert 2.0 <= stats["hours_since_first_activity"] <= 2.5


def test_two_hour_window_is_narrower_than_today():
    db = _seed(TempDatabase())
    stats = collect_activity_stats(window="2h", now=NOW, database=db, cfg=_cfg(), include_usage=False)
    # 08:30 之後：commit 只剩 08:30 之後的（3 筆分別在 08:30/08:20/08:10 → 1 筆），AI 同理
    assert stats["commits"] == 1 and stats["ai_turns"] == 1
    assert stats["files_changed"] == 2  # 08:30 的 tex 與 10:25 的 py
    assert stats["window_label"] == "過去兩小時" and stats["foreground_minutes"] is None


def test_empty_database_is_reported_honestly():
    stats = collect_activity_stats(window="today", now=NOW, database=TempDatabase(), cfg=_cfg(), include_usage=False)
    assert stats["observed_anything"] is False and stats["hours_since_first_activity"] is None
    greeting = compose_greeting(stats, now=NOW, name="Dof")
    assert greeting["achievements"] == [] and "還沒偵測到活動" in greeting["lead"]
    assert greeting["encouragement_pool"] == "nothing" and "慢慢開始" in greeting["encouragement"] or "不急" in greeting["encouragement"]
    assert "郵件" in greeting["claim_boundary"]


def test_invalid_window_is_rejected():
    with pytest.raises(GreetingRejected) as exc:
        collect_activity_stats(window="week", now=NOW, database=TempDatabase(), cfg=_cfg())
    assert exc.value.error_code == "invalid_window"


# ---- 文案 ----


def test_compose_uses_name_fast_start_and_lists_only_observed_things():
    db = _seed(TempDatabase())
    stats = collect_activity_stats(window="today", now=NOW, database=db, cfg=_cfg(), include_usage=False)
    greeting = compose_greeting(stats, now=NOW, name="Dof")
    assert greeting["headline"] == "Dof，早安。"
    assert greeting["lead"].startswith("今天才開工約 2 小時")
    lines = greeting["achievements"]
    assert lines[0] == "推進了 2 個專案（Alpha、論文）"
    assert "開了 1 個 PR、合併了 1 個 PR" in lines
    assert "3 個 commit 落在 2 個 repo，＋30 行" in lines
    assert "改了 2 個文件檔（論文／文檔類）" in lines
    assert "和 AI 對話 5 輪（chatgpt、claude_code）" in lines
    assert "收掉 1 個未結事項" in lines
    # 程式檔有 commit 時不重複報；沒有任何一行提到郵件
    assert not any("程式檔" in l or "郵件" in l for l in lines)
    assert greeting["encouragement_pool"] == "fast_start" and greeting["source"] == "rules"
    text = plain_text(greeting)
    assert text.startswith("Dof，早安。 今天才開工約 2 小時") and greeting["encouragement"] in text


def test_no_name_and_later_in_the_day_changes_the_lead():
    db = _seed(TempDatabase())
    later = NOW + timedelta(hours=6)  # 16:30，開工 8 小時
    stats = collect_activity_stats(window="today", now=later, database=db, cfg=_cfg(), include_usage=False)
    greeting = compose_greeting(stats, now=later, name="")
    assert greeting["headline"] == "下午好。" and greeting["lead"] == "今天到目前為止，你已經："


def test_two_hour_window_mentions_recent_summary():
    db = _seed(TempDatabase())
    stats = collect_activity_stats(window="2h", now=NOW, database=db, cfg=_cfg(), include_usage=False)
    greeting = compose_greeting(stats, now=NOW, name="Dof")
    assert greeting["lead"] == "過去兩小時，你：" and greeting["recent_summary"] == "修 CI 並開了 PR"
    assert "剛剛在做：修 CI 並開了 PR" in plain_text(greeting)


@pytest.mark.parametrize(
    "now, stats, expected",
    [
        (datetime(2026, 9, 4, 23, 30), {"observed_anything": True, "commits": 1}, "late_night"),
        (datetime(2026, 9, 4, 15, 0), {"observed_anything": True, "commits": 1, "foreground_minutes": 6 * 60 + 5}, "long_hours"),
        (datetime(2026, 9, 5, 15, 0), {"observed_anything": True, "commits": 1}, "weekend"),
        (datetime(2026, 9, 4, 10, 0), {"observed_anything": True, "commits": 2, "prs_opened": 1, "hours_since_first_activity": 2.0}, "fast_start"),
        (datetime(2026, 9, 4, 15, 0), {"observed_anything": True, "commits": 9, "hours_since_first_activity": 7.0}, "strong"),
        (datetime(2026, 9, 4, 15, 0), {"observed_anything": True, "commits": 1, "hours_since_first_activity": 7.0}, "steady"),
        (datetime(2026, 9, 4, 15, 0), {"observed_anything": False}, "nothing"),
    ],
)
def test_encouragement_pool_follows_the_situation(now, stats, expected):
    text, pool = choose_encouragement(stats, now=now, seed="x")
    assert pool == expected and text in sg.ENCOURAGEMENT_POOLS[expected]


def test_encouragement_is_stable_within_a_day_and_varies_across_days():
    stats = {"observed_anything": True, "commits": 1, "hours_since_first_activity": 7.0}
    a = compose_greeting({**stats, "window": "today"}, now=NOW, name="")["encouragement"]
    b = compose_greeting({**stats, "window": "today"}, now=NOW + timedelta(hours=3), name="")["encouragement"]
    assert a == b
    seen = {compose_greeting({**stats, "window": "today"}, now=NOW + timedelta(days=d), name="")["encouragement"] for d in range(6)}
    assert len(seen) >= 2


def test_achievement_lines_skip_zero_counts():
    assert achievement_lines({"commits": 0, "ai_turns": 0}) == []
    assert achievement_lines({"foreground_minutes": 10}) == []  # 太短不值得報
    assert achievement_lines({"foreground_minutes": 125}) == ["前景專注 2 小時 5 分"]


# ---- LLM 潤飾 ----


def test_llm_polish_is_off_by_default_and_falls_back_on_failure():
    db = _seed(TempDatabase())
    stats = collect_activity_stats(window="today", now=NOW, database=db, cfg=_cfg(), include_usage=False)
    greeting = compose_greeting(stats, now=NOW, name="Dof")
    assert polish_with_llm(greeting, cfg=_cfg(llm=False), now=NOW) is greeting

    def broken(system, user):
        raise TimeoutError("slow")

    out = polish_with_llm(greeting, cfg=_cfg(llm=True), now=NOW, generate=broken)
    assert out["source"] == "rules" and out["llm_error"] == "TimeoutError"


def test_llm_polish_cannot_add_facts():
    db = _seed(TempDatabase())
    stats = collect_activity_stats(window="today", now=NOW, database=db, cfg=_cfg(), include_usage=False)
    greeting = compose_greeting(stats, now=NOW, name="Dof")
    # 數字都在統計裡 → 接受
    ok = polish_with_llm(greeting, cfg=_cfg(llm=True), now=NOW,
                         generate=lambda s, u: "Dof 早安！才開工 2 小時就推進了 2 個專案、3 個 commit，很棒，記得休息。")
    assert ok["source"] == "llm" and ok["llm_provider"] == "ollama" and ok["llm_cached"] is False
    # 編出「10 個 PR」→ 整段丟掉
    sg._reset_llm_cache_for_tests()
    bad = polish_with_llm(greeting, cfg=_cfg(llm=True), now=NOW, generate=lambda s, u: "Dof 你今天已經有 10 個 PR 了！")
    assert bad["source"] == "rules" and bad["llm_rejected"] == "fact_guard"
    assert llm_text_is_safe("字" * 400, greeting["stats"]) is False


def test_llm_polish_uses_cache_within_window():
    db = _seed(TempDatabase())
    stats = collect_activity_stats(window="today", now=NOW, database=db, cfg=_cfg(), include_usage=False)
    greeting = compose_greeting(stats, now=NOW, name="Dof")
    calls = []

    def gen(s, u):
        calls.append(1)
        return "Dof 早安，2 個專案在動。"

    first = polish_with_llm(greeting, cfg=_cfg(llm=True), now=NOW, generate=gen)
    second = polish_with_llm(greeting, cfg=_cfg(llm=True), now=NOW + timedelta(minutes=5), generate=gen)
    assert first["llm_cached"] is False and second["llm_cached"] is True and len(calls) == 1


# ---- 入口與 API ----


def test_build_greeting_reads_display_name_from_config():
    db = _seed(TempDatabase())
    greeting = build_greeting(window="today", now=NOW, database=db, cfg=_cfg(name="Dof"), use_llm=False)
    assert greeting["headline"].startswith("Dof，") and greeting["text"].startswith("Dof，早安。")
    anonymous = build_greeting(window="today", now=NOW, database=db, cfg=_cfg(name=""), use_llm=False)
    assert anonymous["headline"] == "早安。"


def test_greeting_endpoint(monkeypatch):
    monkeypatch.setattr(
        "core.secretary_greeting.build_greeting",
        lambda window="today": {"window": window, "headline": "Dof，早安。", "achievements": [], "source": "rules", "claim_boundary": "x"},
    )
    client = TestClient(app)
    res = client.get("/api/v1/secretary/greeting?window=2h", headers={"Origin": _LOCAL_ORIGIN})
    assert res.status_code == 200 and res.json()["window"] == "2h"

    def reject(window="today"):
        raise GreetingRejected("invalid_window", "bad")

    monkeypatch.setattr("core.secretary_greeting.build_greeting", reject)
    assert client.get("/api/v1/secretary/greeting?window=week", headers={"Origin": _LOCAL_ORIGIN}).status_code == 422


def test_telegram_today_starts_with_the_greeting(monkeypatch):
    from notifiers import telegram_chat

    monkeypatch.setattr("core.secretary_packs.build_today_view", lambda **kw: {"resume": {}, "pack_line": None, "active_project_count": 0})
    monkeypatch.setattr("core.proactive_secretary.build_action_proposals", lambda **kw: {"proposals": []})
    monkeypatch.setattr(
        "core.secretary_greeting.build_greeting",
        lambda **kw: {"headline": "Dof，早安。", "lead": "今天到目前為止，你已經：", "achievements": ["3 個 commit"],
                      "recent_summary": None, "encouragement": "很好。"},
    )
    text = telegram_chat._today_text(_cfg(), NOW)
    assert text.splitlines()[1] == "Dof，早安。 今天到目前為止，你已經： 3 個 commit。 很好。"
