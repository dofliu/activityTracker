from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.models import ActivityMicroSummary, Base
from exporters.daily_brief import render_html_fragment, render_markdown
from notifiers.desktop_notifier import DesktopNotifier
from synthesizer.aggregator import format_context_for_prompt
from synthesizer.micro_summarizer import (
    generate_micro_summary,
    micro_summaries_for_range,
)


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


def _micro_cfg(enabled=True):
    return DictConfig(
        {
            "synthesizer": {
                "micro_summary": {"enabled": enabled, "provider": "ollama", "timeout_seconds": 30},
                "ollama": {"model": "llama3.1:8b"},
            }
        }
    )


def _range_data(ai=1):
    return {
        "ai_events": [
            {
                "time": f"2026-08-31 09:{i:02d}:00",
                "platform": "claude_code",
                "tag": "activityTracker",
                "prompt": f"提問內容 {i}",
                "response": "完成了某件事" * 3,
            }
            for i in range(ai)
        ],
        "file_events": [],
        "git_events": [
            {"time": "2026-08-31 09:30:00", "repo": "activityTracker", "message": "fix bug", "branch": "main", "insertions": 1, "deletions": 1}
        ],
        "pr_events": [],
        "window_events": [],
    }


def test_micro_summary_stores_clamped_text_and_upserts():
    database = TempDatabase()
    start, end = datetime(2026, 8, 31, 8, 0), datetime(2026, 8, 31, 10, 0)

    first = generate_micro_summary(
        start, end, database=database, cfg=_micro_cfg(),
        range_data=_range_data(), llm_generate=lambda s, u: "  完成 P5 秘書\n晨報整合  " + "長" * 900,
    )
    assert first["status"] == "stored"

    second = generate_micro_summary(
        start, end, database=database, cfg=_micro_cfg(),
        range_data=_range_data(), llm_generate=lambda s, u: "更新後的微摘要",
    )
    assert second["status"] == "stored"

    with database.session_scope() as session:
        rows = session.query(ActivityMicroSummary).all()
        assert len(rows) == 1  # upsert 不重複
        assert rows[0].summary_text == "更新後的微摘要"
    # 首次寫入的超長輸出被截斷至上限
    assert first["chars"] <= 600


def test_micro_summary_skips_empty_period_disabled_and_fallback_output():
    database = TempDatabase()
    start, end = datetime(2026, 8, 31, 8, 0), datetime(2026, 8, 31, 10, 0)
    empty = {"ai_events": [], "file_events": [], "git_events": [], "pr_events": [], "window_events": []}

    assert generate_micro_summary(
        start, end, database=database, cfg=_micro_cfg(enabled=False), range_data=_range_data()
    )["status"] == "disabled"
    assert generate_micro_summary(
        start, end, database=database, cfg=_micro_cfg(), range_data=empty
    )["status"] == "skipped_empty_period"

    def boom(system, user):
        raise ConnectionError("ollama down")

    assert generate_micro_summary(
        start, end, database=database, cfg=_micro_cfg(), range_data=_range_data(), llm_generate=boom
    )["status"] == "skipped_llm_unavailable"

    fallback = generate_micro_summary(
        start, end, database=database, cfg=_micro_cfg(), range_data=_range_data(),
        llm_generate=lambda s, u: "# ⚠️ [本機備援模式] 每日活動與工作日誌",
    )
    assert fallback["status"] == "skipped_llm_unavailable"
    with database.session_scope() as session:
        assert session.query(ActivityMicroSummary).count() == 0  # 失敗一律不落庫


def test_reduce_context_uses_micro_and_falls_back_for_uncovered_periods(monkeypatch):
    monkeypatch.setattr("synthesizer.aggregator.get_active_projects_list", lambda: [])
    monkeypatch.setattr("synthesizer.aggregator.get_open_loops_list", lambda: [])
    monkeypatch.setattr(
        "synthesizer.aggregator.get_config",
        lambda: DictConfig({"synthesizer": {"max_prompt_chars": 180000}}),
    )
    day_data = {
        "git_events": [],
        "pr_events": [],
        "window_events": [],
        "file_events": [
            {"time": "2026-08-31 09:10:00", "file_name": "alpha_covered.py", "file_type": ".py", "action": "modified", "diff": "", "project": "A"},
            {"time": "2026-08-31 13:10:00", "file_name": "beta_open.py", "file_type": ".py", "action": "modified", "diff": "", "project": "A"},
        ],
        "ai_events": [
            {"time": "2026-08-31 09:00:00", "platform": "codex", "tag": "A", "prompt": "已被微摘要涵蓋的提問", "response": ""},
            {"time": "2026-08-31 13:00:00", "platform": "codex", "tag": "A", "prompt": "未涵蓋時段的提問", "response": ""},
        ],
    }
    micro = [
        {
            "period_start": datetime(2026, 8, 31, 8, 0),
            "period_end": datetime(2026, 8, 31, 10, 0),
            "text": "08–10 點：完成秘書晨報整合並修正日報 token 上限。",
        }
    ]

    rendered = format_context_for_prompt(day_data, "2026-08-31", micro_summaries=micro)

    assert "時段微摘要" in rendered
    assert "完成秘書晨報整合" in rendered
    assert "已被微摘要涵蓋的提問" not in rendered  # 覆蓋時段的原始行不重複進 prompt
    assert "未涵蓋時段的提問" in rendered  # 缺漏時段回退原始節錄
    assert "alpha_covered.py" not in rendered
    assert "beta_open.py" in rendered

    # 沒有微摘要時維持原始路徑
    raw = format_context_for_prompt(day_data, "2026-08-31")
    assert "已被微摘要涵蓋的提問" in raw
    assert "時段微摘要" not in raw


def test_micro_summaries_for_range_returns_overlapping_ordered():
    database = TempDatabase()
    with database.session_scope() as session:
        for hour in (12, 8, 10):
            session.add(
                ActivityMicroSummary(
                    period_start=datetime(2026, 8, 31, hour, 0),
                    period_end=datetime(2026, 8, 31, hour + 2, 0),
                    provider="ollama",
                    summary_text=f"{hour} 點段摘要",
                    created_at=datetime(2026, 8, 31, hour, 5),
                )
            )
        session.add(  # 範圍外
            ActivityMicroSummary(
                period_start=datetime(2026, 8, 30, 8, 0),
                period_end=datetime(2026, 8, 30, 10, 0),
                provider="ollama",
                summary_text="前一天",
                created_at=datetime(2026, 8, 30, 10, 5),
            )
        )

    rows = micro_summaries_for_range(
        datetime(2026, 8, 31, 0, 0), datetime(2026, 8, 31, 23, 59), database=database
    )
    assert [r["text"] for r in rows] == ["8 點段摘要", "10 點段摘要", "12 點段摘要"]


def _fake_briefing(limit=2, **_kwargs):
    return {
        "proposals": [
            {
                "title": "驗證 Browser Extension 即時連線",
                "project_key": "OmniContext",
                "priority": "high",
                "suggested_action": "在 Chrome 重新載入 Extension。",
                "llm_note": None,
                "detail": "",
            }
        ],
        "total": 3,
        "advisor_summary": "先收掉 Extension 驗證，再回到論文。",
        "claim_boundary": "建議僅供判斷，不會自動執行。",
    }


def test_morning_briefing_includes_secretary_top_proposal(monkeypatch, capsys):
    monkeypatch.setattr(
        "notifiers.desktop_notifier.get_active_projects_list",
        lambda: [{"display_name": "activityTracker", "status": "active", "project_key": "activityTracker", "last_activity_at": "2026-08-31 09:00"}],
    )
    monkeypatch.setattr("notifiers.desktop_notifier.get_open_loops_list", lambda: [])
    monkeypatch.setattr("core.proactive_secretary.briefing_proposals", _fake_briefing)

    assert DesktopNotifier().send_morning_briefing(dry_run=True) is True
    output = capsys.readouterr().out
    assert "秘書建議：驗證 Browser Extension 即時連線" in output
    assert "共 3 項" in output
    assert "先收掉 Extension 驗證" in output


def test_morning_briefing_survives_secretary_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        "notifiers.desktop_notifier.get_active_projects_list", lambda: []
    )
    monkeypatch.setattr("notifiers.desktop_notifier.get_open_loops_list", lambda: [])

    def boom(**_kwargs):
        raise RuntimeError("secretary offline")

    monkeypatch.setattr("core.proactive_secretary.briefing_proposals", boom)
    assert DesktopNotifier().send_morning_briefing(dry_run=True) is True
    output = capsys.readouterr().out
    assert "晨間簡報" in output
    assert "秘書建議" not in output


def test_daily_brief_renders_secretary_section():
    data = {
        "generated_at": datetime(2026, 8, 31, 8, 30),
        "active": [],
        "stagnant": [],
        "open_loops": [],
        "secretary": _fake_briefing(),
    }
    markdown = render_markdown(data)
    assert "## 🤖 小秘書建議（3）" in markdown
    assert "驗證 Browser Extension 即時連線" in markdown
    assert "建議僅供判斷，不會自動執行" in markdown

    html = render_html_fragment(data)
    assert "小秘書建議" in html
    assert "驗證 Browser Extension 即時連線" in html

    # 無秘書資料時不輸出區塊
    data["secretary"] = None
    assert "小秘書建議" not in render_markdown(data)
