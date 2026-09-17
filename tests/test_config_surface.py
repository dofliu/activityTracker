"""設定面：六層旗標收成三層、引擎參數回到程式常數（TODO D6，ROADMAP §13 R1）。

2026-09-16 之前，預設關閉的危險能力疊了六層開關（`executor` → `l2` → `l2.allow_write`
→ `scheduled_tasks` → `telegram_approvals` → `allow_remote_arm`），而 449 行的
`config.example.yaml` 裡有二十幾個鍵根本不是設定，是秘書引擎的評分權重。這裡把
「收斂之後安全語意沒有變」寫成測試：

1. 真正的三層還在：executor → l2 → l2.allow_write，每一層都要疊在前一層之上。
2. 排程任務跟著 executor 總開關，但**能排的永遠只有 L0 唯讀 template**；關掉 executor
   就連建立都拒絕。
3. `/arm` 跟著 Telegram 批准通道的開關；通道關著就一定拒絕。
4. 既有設定檔明確寫成 ``false`` 的舊鍵**照樣有效**——升級不會靜默放寬任何人已經做過的選擇。
5. 從設定檔搬走的引擎參數，程式常數必須給出**一模一樣**的數字（搬家不改行為）。
6. 範例設定檔不再帶那些參數，也不再帶作者個人的路徑樣式。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.agent_executor import executor_enabled, l2_enabled, l2_write_enabled
from core.scheduled_tasks import (
    LEGACY_ENABLED_KEY,
    RISK_L0,
    SCHEDULABLE_TEMPLATES,
    ScheduleRejected,
    create_scheduled_task,
    legacy_opt_out,
    scheduled_tasks_enabled,
)
from notifiers.telegram_chat import LEGACY_REMOTE_ARM_KEY, remote_arm_enabled

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "config.example.yaml"

# D6 從範例設定檔搬回程式常數的鍵（值不得改變，見 test_engine_defaults_did_not_move）
RETIRED_TUNING_KEYS = (
    "max_per_project", "unfinished_recent_min_idle_hours", "max_proposals",
    "stalled_open_loop_hours", "github_stale_after_days",
    "daily_digest.max_projects", "daily_digest.max_highlights",
    "patterns.lookback_days", "patterns.routine_min_active_days",
    "patterns.neglect_min_prev_days", "patterns.habit_min_days", "patterns.habit_boost",
    "profile.priority_boost",
    "docs_freshness.min_commits", "docs_freshness.min_days", "docs_freshness.lookback_days",
    "weekly_review.drift_max_days", "weekly_review.drift_min_other_days",
    "greeting.llm.timeout_seconds", "greeting.llm.cache_minutes",
    "llm_advisor.timeout_seconds", "llm_advisor.cache_minutes",
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


def _executor_cfg(enabled=True, extra=None):
    executor = {"enabled": enabled}
    executor.update(extra or {})
    return DictConfig({"proactive_secretary": {"executor": executor}})


# ---- 1. 真正的三層 --------------------------------------------------------


def test_the_three_remaining_switches_still_stack():
    assert executor_enabled(_executor_cfg(False)) is False
    assert executor_enabled(_executor_cfg(True)) is True
    # L2 疊在總開關之上；寫入又疊在 L2 之上——少開一層就全部是 False
    assert l2_enabled(_executor_cfg(False, {"l2": {"enabled": True}})) is False
    assert l2_enabled(_executor_cfg(True, {"l2": {"enabled": True}})) is True
    assert l2_write_enabled(_executor_cfg(True, {"l2": {"enabled": False, "allow_write": True}})) is False
    assert l2_write_enabled(_executor_cfg(True, {"l2": {"enabled": True, "allow_write": True}})) is True


# ---- 2. 排程跟著總開關，但只排得動 L0 ------------------------------------


def test_only_read_only_templates_are_schedulable():
    """L1／L2 永不可排程——這是合併 scheduled_tasks.enabled 的前提。"""
    assert SCHEDULABLE_TEMPLATES
    assert {t.risk_level for t in SCHEDULABLE_TEMPLATES.values()} == {RISK_L0}


def test_scheduled_tasks_follow_the_executor_switch():
    assert scheduled_tasks_enabled(_executor_cfg(False)) is False
    assert scheduled_tasks_enabled(_executor_cfg(True)) is True  # 不再需要第二個開關


def test_creating_a_task_is_refused_while_the_executor_is_off():
    with pytest.raises(ScheduleRejected) as exc:
        create_scheduled_task({"template_id": "morning_pack"}, cfg=_executor_cfg(False))
    assert exc.value.error_code == "scheduled_tasks_disabled"


# ---- 3. /arm 跟著批准通道的開關 -------------------------------------------


def _approvals_cfg(approvals=True, extra=None):
    telegram_approvals = {"enabled": approvals}
    telegram_approvals.update(extra or {})
    return _executor_cfg(True, {"telegram_approvals": telegram_approvals})


def test_remote_arm_follows_the_approval_channel_switch():
    assert remote_arm_enabled(_approvals_cfg(approvals=False)) is False
    assert remote_arm_enabled(_approvals_cfg(approvals=True)) is True
    # 批准通道本身仍疊在 executor 總開關之上
    assert remote_arm_enabled(
        DictConfig({"proactive_secretary": {"executor": {
            "enabled": False, "telegram_approvals": {"enabled": True}}}})
    ) is False


# ---- 4. 舊設定檔的明確 false 仍然有效 -------------------------------------


def test_an_explicit_false_in_an_existing_config_is_still_honoured():
    """升級不得靜默放寬任何人已經做過的選擇（曾經啟用又關掉的人可能有休眠任務）。"""
    off = _executor_cfg(True, {"scheduled_tasks": {"enabled": False}})
    assert scheduled_tasks_enabled(off) is False
    assert legacy_opt_out(off) is True
    assert legacy_opt_out(_executor_cfg(True)) is False
    assert LEGACY_ENABLED_KEY.endswith("scheduled_tasks.enabled")

    arm_off = _approvals_cfg(True, {"allow_remote_arm": False})
    assert remote_arm_enabled(arm_off) is False
    assert LEGACY_REMOTE_ARM_KEY.endswith("telegram_approvals.allow_remote_arm")


# ---- 5. 搬家不改數字 ------------------------------------------------------


def test_engine_defaults_did_not_move():
    """搬回程式常數的值，必須與搬走之前範例設定檔寫的一模一樣。"""
    from core.activity_digest import DEFAULT_MAX_HIGHLIGHTS, DEFAULT_MAX_PROJECTS
    from core.activity_patterns import (
        DEFAULT_HABIT_BOOST, DEFAULT_HABIT_MIN_DAYS, DEFAULT_LOOKBACK_DAYS,
        DEFAULT_NEGLECT_MIN_PREV_DAYS, DEFAULT_ROUTINE_MIN_ACTIVE_DAYS, pattern_settings,
    )
    from core.docs_freshness import DEFAULT_LOOKBACK_DAYS as DOCS_LOOKBACK
    from core.docs_freshness import DEFAULT_MIN_COMMITS, DEFAULT_MIN_DAYS
    from core.secretary.memory import DEFAULT_PRIORITY_BOOST
    from core.secretary.signals import DEFAULT_GITHUB_STALE_AFTER_DAYS
    from core.weekly_review import DEFAULT_DRIFT_MAX_DAYS, DEFAULT_DRIFT_MIN_OTHER_DAYS

    assert (DEFAULT_LOOKBACK_DAYS, DEFAULT_ROUTINE_MIN_ACTIVE_DAYS, DEFAULT_NEGLECT_MIN_PREV_DAYS,
            DEFAULT_HABIT_MIN_DAYS, DEFAULT_HABIT_BOOST) == (7, 4, 3, 3, 0.15)
    assert (DEFAULT_MIN_COMMITS, DEFAULT_MIN_DAYS, DOCS_LOOKBACK) == (8, 2, 60)
    assert (DEFAULT_DRIFT_MAX_DAYS, DEFAULT_DRIFT_MIN_OTHER_DAYS) == (1, 3)
    assert (DEFAULT_MAX_PROJECTS, DEFAULT_MAX_HIGHLIGHTS) == (5, 4)
    assert DEFAULT_PRIORITY_BOOST == 0.2 and DEFAULT_GITHUB_STALE_AFTER_DAYS == 60

    # 空設定（＝新安裝）拿到的就是這些常數
    settings = pattern_settings(DictConfig())
    assert settings["lookback_days"] == DEFAULT_LOOKBACK_DAYS
    assert settings["habit_boost"] == DEFAULT_HABIT_BOOST
    # 但要調仍可覆寫同名鍵（是調參，不是設定，所以不列在範例檔裡）
    override = DictConfig({"proactive_secretary": {"patterns": {"habit_boost": 0.3}}})
    assert pattern_settings(override)["habit_boost"] == 0.3


def test_window_ignore_titles_default_moved_into_code():
    from watchers.window_watcher import DEFAULT_IGNORE_TITLES

    assert "Program Manager" in DEFAULT_IGNORE_TITLES


def test_interface_rules_default_lives_in_code():
    from core.usage_analytics import DEFAULT_INTERFACE_RULES, interface_rules_from_config

    names = [rule["name"] for rule in interface_rules_from_config(DictConfig())]
    assert names == [rule["name"] for rule in DEFAULT_INTERFACE_RULES]
    assert "Claude Code" in names and "Codex" in names


# ---- 6. 範例設定檔的面積 --------------------------------------------------


def _example_config() -> dict:
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


def test_example_config_no_longer_ships_engine_tuning_or_the_merged_switches():
    secretary = _example_config()["proactive_secretary"]

    def present(path: str) -> bool:
        node = secretary
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return False
            node = node[part]
        return True

    assert [key for key in RETIRED_TUNING_KEYS if present(key)] == []
    assert not present("executor.scheduled_tasks")           # 併入 executor.enabled
    assert not present("executor.telegram_approvals.allow_remote_arm")  # 併入通道開關
    # 真正的三層仍然在範例檔裡，而且預設全關
    assert secretary["executor"]["enabled"] is False
    assert secretary["executor"]["l2"]["enabled"] is False
    assert secretary["executor"]["l2"]["allow_write"] is False
    assert secretary["executor"]["telegram_approvals"]["enabled"] is False


def test_example_config_is_settings_only_and_carries_no_personal_paths():
    lines = EXAMPLE.read_text(encoding="utf-8").splitlines()
    settings_lines = [l for l in lines if l.strip() and not l.strip().startswith("#")]
    # 「設定」本身要撐得住一次閱讀；其餘是隱私與安全邊界的說明，刻意留著。
    assert len(settings_lines) < 260, len(settings_lines)
    text = "\n".join(lines)
    for personal in ("BladeDamage", "CASE-", "D:/Project_CodingSimulation"):
        assert personal not in text, personal
