"""模式感知提案（ADR-017）：秘書開始使用它記得的東西。

每日工作誌（ADR-012 Addendum A）讓大腦裡有了「你每天做了什麼」，但提案引擎
讀記憶區只做兩件事：``mute`` 壓掉提案、附一行專案筆記——**它不看任何模式**。
你連續一週幾乎天天在 uavMonitor 上 commit，秘書全都記下來了，卻不會據此排序
或提議；兩週前很活躍、這週完全沒動的專案，也沒有人提醒。

這個模組補的是「注意到你的習慣，並在既有的安全閘門內提議」那一步。它產生三種
**確定性**的東西：

- ``no_daily_routine``：近一週有 ≥ N 天在工作，但秘書沒有任何每日排程
  （早晨包／工作誌）。修法就是建立既有的預設排程；一旦建立這個提案就消失。
- ``neglected_active_project``：前一週活躍 ≥ N 天、近一週 0 天的專案。對應的
  動作是既有的 L0 ``generate_handoff``——看一眼 Handoff 再決定接續或放下。
- **習慣加權**（不是新提案）：近一週有 ≥ N 天活動的專案，其既有提案（需要 pull、
  PR、未結事項…）分數加一點並附一句理由——你目前的主線該排在一個月沒碰的 repo
  前面。

三條刻意守住的邊界：

- **只用可回溯的計數，不推測意圖。** 模式來自 ``git_activity_events``／
  ``ai_prompt_events``／``file_activity_events`` 依（專案 × 日）分組的計數；
  不讀 prompt 內容、不做任何「他大概想做什麼」的推論。
- **只算已結束的日子。** 今天的活動不進任何模式（與驗收中心 A1 修正同一個教訓：
  今天的分母只到現在）。
- **不新增可執行動作。** 所有提案對應的都是既有 L0/L1 template，仍走 ADR-008
  的批准與收據；本模組本身唯讀、不呼叫 LLM、不寫任何資料。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time as dtime, timedelta
from typing import Any

from sqlalchemy import func

from core.config import get_config
from core.database import get_db
from core.models import (
    AIPromptEvent,
    FileActivityEvent,
    GitActivityEvent,
    SecretaryNote,
    SecretaryScheduledTask,
)

PATTERN_CLAIM_BOUNDARY = (
    "模式只來自（專案 × 日）的可回溯活動計數，且只算已結束的日子；不讀 prompt 內容、"
    "不推測意圖。提案仍為唯讀建議，對應的動作全部是既有 template，執行仍需批准。"
)

ROUTINE_TEMPLATES = ("morning_pack", "daily_digest")

DEFAULT_LOOKBACK_DAYS = 7
DEFAULT_ROUTINE_MIN_ACTIVE_DAYS = 4
DEFAULT_NEGLECT_MIN_PREV_DAYS = 3
DEFAULT_HABIT_MIN_DAYS = 3
DEFAULT_HABIT_BOOST = 0.15
MAX_PROJECT_NAMES_IN_TITLE = 3


# ---- 設定 ----------------------------------------------------------------


def patterns_enabled(cfg: Any | None = None) -> bool:
    cfg = cfg or get_config()
    return bool(cfg.get("proactive_secretary.patterns.enabled", True))


def _int_setting(cfg: Any, key: str, default: int, low: int, high: int) -> int:
    try:
        return min(high, max(low, int(cfg.get(key, default))))
    except (TypeError, ValueError):
        return default


def _float_setting(cfg: Any, key: str, default: float, low: float, high: float) -> float:
    try:
        return min(high, max(low, float(cfg.get(key, default))))
    except (TypeError, ValueError):
        return default


def pattern_settings(cfg: Any | None = None) -> dict[str, Any]:
    cfg = cfg or get_config()
    lookback = _int_setting(cfg, "proactive_secretary.patterns.lookback_days", DEFAULT_LOOKBACK_DAYS, 3, 30)
    return {
        "enabled": patterns_enabled(cfg),
        "lookback_days": lookback,
        "routine_min_active_days": _int_setting(
            cfg, "proactive_secretary.patterns.routine_min_active_days",
            DEFAULT_ROUTINE_MIN_ACTIVE_DAYS, 1, lookback,
        ),
        "neglect_min_prev_days": _int_setting(
            cfg, "proactive_secretary.patterns.neglect_min_prev_days",
            DEFAULT_NEGLECT_MIN_PREV_DAYS, 1, lookback,
        ),
        "habit_min_days": _int_setting(
            cfg, "proactive_secretary.patterns.habit_min_days", DEFAULT_HABIT_MIN_DAYS, 1, lookback
        ),
        "habit_boost": _float_setting(
            cfg, "proactive_secretary.patterns.habit_boost", DEFAULT_HABIT_BOOST, 0.0, 0.5
        ),
    }


# ---- 活動矩陣：（專案 × 日）有沒有活動 -------------------------------------


def _naive(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


def activity_matrix(
    *,
    end_day: date,
    days: int,
    database: Any | None = None,
) -> dict[str, set[date]]:
    """``end_day``（含）往前 ``days`` 天內，每個專案哪幾天有活動。

    只用三張事件表依（專案 × 日）分組計數；沒歸戶（project 為空）的活動只計入
    ``"*"`` 這個代表「任何活動」的鍵，不猜專案。回傳值裡 ``"*"`` 一定存在。
    """
    database = database or get_db()
    start = datetime.combine(end_day - timedelta(days=days - 1), dtime.min)
    until = datetime.combine(end_day + timedelta(days=1), dtime.min)
    matrix: dict[str, set[date]] = defaultdict(set)
    matrix["*"]  # 保證存在

    def absorb(rows: list[tuple[Any, Any]]) -> None:
        for project, day_text in rows:
            if not day_text:
                continue
            try:
                day = date.fromisoformat(str(day_text)[:10])
            except ValueError:
                continue
            matrix["*"].add(day)
            key = (project or "").strip()
            if key:
                matrix[key].add(day)

    with database.session_scope() as session:
        absorb(
            session.query(GitActivityEvent.repo_name, func.date(GitActivityEvent.timestamp))
            .filter(GitActivityEvent.timestamp >= start, GitActivityEvent.timestamp < until)
            .group_by(GitActivityEvent.repo_name, func.date(GitActivityEvent.timestamp))
            .all()
        )
        absorb(
            session.query(AIPromptEvent.project_tag, func.date(AIPromptEvent.timestamp))
            .filter(AIPromptEvent.timestamp >= start, AIPromptEvent.timestamp < until)
            .group_by(AIPromptEvent.project_tag, func.date(AIPromptEvent.timestamp))
            .all()
        )
        absorb(
            session.query(FileActivityEvent.project_name, func.date(FileActivityEvent.timestamp))
            .filter(FileActivityEvent.timestamp >= start, FileActivityEvent.timestamp < until)
            .group_by(FileActivityEvent.project_name, func.date(FileActivityEvent.timestamp))
            .all()
        )
    return dict(matrix)


def _split_windows(
    matrix: dict[str, set[date]], *, end_day: date, lookback: int
) -> tuple[dict[str, int], dict[str, int]]:
    """把矩陣切成「近一週」與「前一週」兩個視窗，各自數活動天數。"""
    recent_start = end_day - timedelta(days=lookback - 1)
    prev_start = recent_start - timedelta(days=lookback)
    recent: dict[str, int] = {}
    previous: dict[str, int] = {}
    for project, days in matrix.items():
        recent[project] = sum(1 for d in days if recent_start <= d <= end_day)
        previous[project] = sum(1 for d in days if prev_start <= d < recent_start)
    return recent, previous


# ---- 旁證：記憶區裡的工作誌 ------------------------------------------------


def _digest_refs(
    project: str | None, *, since: date, database: Any
) -> list[dict[str, Any]]:
    """把已寫進記憶區的每日工作誌當成證據附在提案上（有才附，沒有不編）。"""
    prefix = "daily_digest:"
    with database.session_scope() as session:
        rows = (
            session.query(SecretaryNote.source_ref, SecretaryNote.created_at)
            .filter(
                SecretaryNote.kind == "observation",
                SecretaryNote.source == "daily_digest",
                SecretaryNote.source_ref.like(f"{prefix}%"),
            )
            .order_by(SecretaryNote.created_at.desc())
            .limit(60)
            .all()
        )
    refs: list[dict[str, Any]] = []
    for source_ref, created_at in rows:
        parts = str(source_ref).split(":")
        if len(parts) < 2:
            continue
        try:
            day = date.fromisoformat(parts[1])
        except ValueError:
            continue
        if day < since:
            continue
        ref_project = parts[2] if len(parts) >= 3 else None
        if project is None and ref_project is not None:
            continue
        if project is not None and ref_project != project:
            continue
        refs.append({"source_ref": str(source_ref), "kind": "daily_digest", "observed_at": created_at})
        if len(refs) >= 7:
            break
    return refs


# ---- 排程：秘書有沒有每日例行 ----------------------------------------------


def routine_schedules_present(database: Any | None = None) -> set[str]:
    database = database or get_db()
    with database.session_scope() as session:
        rows = (
            session.query(SecretaryScheduledTask.template_id)
            .filter(
                SecretaryScheduledTask.enabled.is_(True),
                SecretaryScheduledTask.template_id.in_(ROUTINE_TEMPLATES),
            )
            .all()
        )
    return {str(row[0]) for row in rows}


# ---- 對外：signals 與習慣加權 ----------------------------------------------


def collect_pattern_signals(
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
    exclude_projects: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """回傳 ``(signals, meta)``；signals 的形狀與其他 triage signal 一致。

    ``exclude_projects`` 是已經有未結事項提案的專案——它們已經在清單上，
    不必再用「被冷落」重複提醒。
    """
    from core.time_utils import get_local_now

    database = database or get_db()
    cfg = cfg or get_config()
    now = _naive(now or get_local_now())
    settings = pattern_settings(cfg)
    meta: dict[str, Any] = {
        "used": False,
        "settings": {k: v for k, v in settings.items() if k != "enabled"},
        "claim_boundary": PATTERN_CLAIM_BOUNDARY,
    }
    if not settings["enabled"]:
        meta["reason"] = "disabled"
        return [], meta

    lookback = settings["lookback_days"]
    end_day = now.date() - timedelta(days=1)  # 今天不算
    matrix = activity_matrix(end_day=end_day, days=lookback * 2, database=database)
    recent, previous = _split_windows(matrix, end_day=end_day, lookback=lookback)
    exclude = {p for p in (exclude_projects or set())}
    signals: list[dict[str, Any]] = []
    observed_at = datetime.combine(end_day, dtime.max).replace(microsecond=0)
    recent_start = end_day - timedelta(days=lookback - 1)

    # S1：近一週有在工作，但秘書沒有每日例行。
    active_days = recent.get("*", 0)
    schedules = routine_schedules_present(database)
    meta["recent_active_days"] = active_days
    meta["routine_schedules"] = sorted(schedules)
    if active_days >= settings["routine_min_active_days"] and not schedules:
        top_projects = sorted(
            (p for p in recent if p != "*" and recent[p] > 0),
            key=lambda p: (-recent[p], p.casefold()),
        )[:MAX_PROJECT_NAMES_IN_TITLE]
        names = f"（{'、'.join(top_projects)}）" if top_projects else ""
        signals.append({
            "signal_type": "no_daily_routine",
            "project_key": "OmniContext",
            "subject_ref": "schedule:daily_routine",
            "evidence_ref": f"activity_pattern:*:{recent_start.isoformat()}..{end_day.isoformat()}",
            "evidence_extra": _digest_refs(None, since=recent_start, database=database),
            "observed_at": observed_at,
            "url": None,
            "age_days": 0.0,
            "open_loop_refs": [],
            "title": f"你近一週有 {active_days} 天在工作{names}，但秘書還沒有每日排程",
            "detail": (
                f"近 {lookback} 個完整日中有 {active_days} 天觀測到活動；"
                "排程任務裡沒有啟用中的早晨包或每日工作誌。"
            ),
            "reasons": [
                f"近 {lookback} 天有 {active_days} 天在工作，秘書卻沒有任何每日例行",
                "沒有排程就沒有每日工作誌，記憶區不會累積「你做了什麼」",
            ],
            "score": 0.6,
        })

    # S2：前一週活躍、近一週歸零的專案。
    for project, prev_days in previous.items():
        if project == "*" or project in exclude:
            continue
        if prev_days < settings["neglect_min_prev_days"] or recent.get(project, 0) > 0:
            continue
        signals.append({
            "signal_type": "neglected_active_project",
            "project_key": project,
            "subject_ref": f"project:{project}",
            "evidence_ref": f"activity_pattern:{project}:{recent_start.isoformat()}..{end_day.isoformat()}",
            "evidence_extra": _digest_refs(project, since=recent_start - timedelta(days=lookback), database=database),
            "observed_at": observed_at,
            "url": None,
            "age_days": float(lookback),
            "open_loop_refs": [],
            "title": f"{project} 前一週活躍 {prev_days} 天，近一週完全沒動",
            "detail": (
                f"{(recent_start - timedelta(days=lookback)).isoformat()}～{(recent_start - timedelta(days=1)).isoformat()}"
                f" 有 {prev_days} 天活動；{recent_start.isoformat()}～{end_day.isoformat()} 為 0 天。"
            ),
            "reasons": [
                f"前一週 {prev_days} 天有活動、近一週 0 天——不是剛做完就是被擱下了",
                "沒有未結事項在提醒你，所以這件事只有模式看得到",
            ],
            "score": round(min(0.75, 0.5 + prev_days * 0.05), 3),
        })

    meta["used"] = True
    meta["signals"] = len(signals)
    meta["recent_active_by_project"] = {
        p: n for p, n in sorted(recent.items(), key=lambda kv: -kv[1]) if p != "*" and n > 0
    }
    return signals, meta


def apply_habit_boost(
    signals: list[dict[str, Any]],
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
    recent_active: dict[str, int] | None = None,
) -> int:
    """近一週活躍 ≥ N 天的專案，其既有訊號分數加一點並附理由；回傳加權筆數。

    這是排序，不是新提案：讓你目前的主線排在一個月沒碰的 repo 前面。
    """
    from core.time_utils import get_local_now

    cfg = cfg or get_config()
    settings = pattern_settings(cfg)
    if not settings["enabled"] or settings["habit_boost"] <= 0:
        return 0
    if recent_active is None:
        database = database or get_db()
        now = _naive(now or get_local_now())
        end_day = now.date() - timedelta(days=1)
        matrix = activity_matrix(end_day=end_day, days=settings["lookback_days"], database=database)
        recent_active, _ = _split_windows(matrix, end_day=end_day, lookback=settings["lookback_days"])

    boosted = 0
    for signal in signals:
        if signal.get("signal_type") in ("no_daily_routine", "neglected_active_project"):
            continue
        days = recent_active.get(str(signal.get("project_key") or ""), 0)
        if days < settings["habit_min_days"]:
            continue
        signal["score"] = round(min(1.0, float(signal.get("score", 0.0)) + settings["habit_boost"]), 3)
        signal.setdefault("reasons", []).append(
            f"這個專案近 {settings['lookback_days']} 天有 {days} 天在動，是你目前的主線"
        )
        signal["habit_boosted"] = True
        boosted += 1
    return boosted
