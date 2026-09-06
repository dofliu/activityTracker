"""每週回顧（ADR-020）：說的 vs 做的。

ADR-018 讓你宣告本期優先，ADR-017 讓秘書算出你實際把時間放在哪個專案。兩件事一直
分開放：秘書知道你「說」論文優先，也知道你這週在 uavMonitor 上動了五天，卻不會把
兩者放在一起講。

這個模組把**上一個完整週（週一～週日）**的（專案 × 日）活動天數，對照你宣告的優先：

- 寫成一則記憶區觀察「2026-W37 回顧」——依活躍天數列出專案、每日工作誌寫了幾天、
  宣告的優先各自幾天、說的和做的一致還是不一致。同一週只寫一次、可刪、會過期。
- 不一致時多一張 ``priority_drift`` 提案：「你說『論文』優先，上週它只有 1 天在動、
  uavMonitor 有 5 天」——動作是既有的 L0 Handoff（看一眼再決定接續）或改宣告。

邊界：只用可回溯計數（ADR-017 的矩陣）、不讀 prompt、不呼叫 LLM、不推測「為什麼」；
「不一致」只是兩個數字放在一起，判斷與調整由你來。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time as dtime, timedelta
from typing import Any

from core.activity_patterns import activity_matrix
from core.config import get_config
from core.database import get_db
from core.models import SecretaryNote
from core.time_utils import get_local_now

logger = logging.getLogger("OmniContext.WeeklyReview")

REVIEW_CLAIM_BOUNDARY = (
    "每週回顧只用 commit／AI 對話／檔案異動依（專案 × 日）的計數，對照你在偏好筆記宣告的本期優先；"
    "不讀 prompt 內容、不呼叫 LLM、不推測原因。「不一致」只是兩個數字放在一起，判斷與調整由你來。"
)
SOURCES = ("git_activity_events", "ai_prompt_events", "file_activity_events", "secretary_notes.kind=preference")

DEFAULT_DRIFT_MAX_DAYS = 1        # 宣告優先的專案，上週活躍 ≤ 幾天算「說了沒做」
DEFAULT_DRIFT_MIN_OTHER_DAYS = 3  # 且同週有別的專案 ≥ 幾天才算「時間去了別處」
MAX_WEEKS_BACK = 4
MAX_RANKED_PROJECTS = 6
MAX_BODY_CHARS = 900
WEEK_DAYS = 7


def review_enabled(cfg: Any | None = None) -> bool:
    cfg = cfg or get_config()
    return bool(cfg.get("proactive_secretary.weekly_review.enabled", True))


def _int_setting(cfg: Any, key: str, default: int, low: int, high: int) -> int:
    try:
        return min(high, max(low, int(cfg.get(key, default))))
    except (TypeError, ValueError):
        return default


def review_settings(cfg: Any | None = None) -> dict[str, Any]:
    cfg = cfg or get_config()
    return {
        "enabled": review_enabled(cfg),
        "drift_max_days": _int_setting(cfg, "proactive_secretary.weekly_review.drift_max_days", DEFAULT_DRIFT_MAX_DAYS, 0, 6),
        "drift_min_other_days": _int_setting(
            cfg, "proactive_secretary.weekly_review.drift_min_other_days", DEFAULT_DRIFT_MIN_OTHER_DAYS, 1, 7
        ),
    }


def _naive(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo else value


def review_period(now: datetime, weeks_back: int = 1) -> tuple[date, date, str]:
    """回傳第 ``weeks_back`` 個**已結束**的 ISO 週（週一～週日）與標籤；進行中的這週永遠不算。"""
    weeks_back = min(MAX_WEEKS_BACK, max(1, int(weeks_back)))
    today = _naive(now).date()
    start = today - timedelta(days=today.weekday() + 7 * weeks_back)
    end = start + timedelta(days=6)
    iso_year, iso_week, _ = start.isocalendar()
    return start, end, f"{iso_year}-W{iso_week:02d}"


def active_days_by_project(start: date, end: date, *, database: Any | None = None) -> tuple[dict[str, int], int]:
    """(各專案活躍天數, 整週有活動的天數)。沒歸戶的活動只算進整週天數，不猜專案。"""
    matrix = activity_matrix(end_day=end, days=(end - start).days + 1, database=database or get_db())
    total = len(matrix.get("*", set()))
    per_project = {key: len(days) for key, days in matrix.items() if key != "*" and days}
    return per_project, total


def rank_projects(active_days: dict[str, int], limit: int = MAX_RANKED_PROJECTS) -> list[dict[str, Any]]:
    ranked = sorted(active_days.items(), key=lambda kv: (-kv[1], kv[0].casefold()))
    return [{"project": key, "days": days} for key, days in ranked[:limit]]


def compare_said_vs_done(
    *, active_days: dict[str, int], priorities: list[str], settings: dict[str, Any]
) -> dict[str, Any]:
    """把宣告的優先放到活動天數旁邊。

    每個宣告的專案有三種結果：``done``（活躍 > drift_max_days）、``drift``（活躍 ≤ drift_max_days
    且同週有**未宣告**的專案 ≥ drift_min_other_days——時間去了別處）、``quiet``（活躍很少但也沒有
    別的專案在忙——整週安靜，不算偏移）。``aligned``：沒宣告＝None、有 drift＝False、全 done＝True、
    其餘＝None（活動太少不好比）。專案名比對不分大小寫；對不到任何活動的名字如實標 ``matched_key: None``。
    """
    lowered = {key.casefold(): key for key in active_days}
    declared: list[dict[str, Any]] = []
    for name in priorities:
        key = lowered.get(str(name).casefold())
        declared.append({"project": name, "matched_key": key, "days": int(active_days.get(key, 0)) if key else 0})
    declared_keys = {item["matched_key"] for item in declared if item["matched_key"]}
    others = sorted(
        ((key, days) for key, days in active_days.items() if key not in declared_keys),
        key=lambda kv: (-kv[1], kv[0].casefold()),
    )
    top_other = {"project": others[0][0], "days": others[0][1]} if others else None
    drift: list[dict[str, Any]] = []
    for item in declared:
        if item["days"] > settings["drift_max_days"]:
            item["status"] = "done"
        elif top_other and top_other["days"] >= settings["drift_min_other_days"]:
            item["status"] = "drift"
            item["instead"] = dict(top_other)
            drift.append(item)
        else:
            item["status"] = "quiet"
    if not priorities:
        aligned: bool | None = None
    elif drift:
        aligned = False
    elif all(item["status"] == "done" for item in declared):
        aligned = True
    else:
        aligned = None
    return {"declared": declared, "drift": drift, "aligned": aligned, "top_other": top_other}


def digest_days_in(start: date, end: date, *, database: Any | None = None) -> int:
    """那一週有幾天已經有日層的每日工作誌（ADR-012 Addendum A）——只數 source_ref，不解析正文。"""
    database = database or get_db()
    with database.session_scope() as session:
        refs = (
            session.query(SecretaryNote.source_ref)
            .filter(
                SecretaryNote.kind == "observation",
                SecretaryNote.source == "daily_digest",
                SecretaryNote.source_ref.like("daily_digest:%"),
            )
            .all()
        )
    days: set[date] = set()
    for (source_ref,) in refs:
        parts = str(source_ref).split(":")
        if len(parts) != 2:      # 有第三段的是專案層，不算「那一天有工作誌」
            continue
        try:
            day = date.fromisoformat(parts[1])
        except ValueError:
            continue
        if start <= day <= end:
            days.add(day)
    return len(days)


def _declared_phrase(item: dict[str, Any]) -> str:
    if item["matched_key"] is None:
        return f"{item['project']}（0 天；沒有任何活動歸到這個名字）"
    return f"{item['project']}（{item['days']} 天）"


def compose_review_body(
    *,
    label: str,
    start: date,
    end: date,
    total_days: int,
    ranked: list[dict[str, Any]],
    comparison: dict[str, Any],
    digest_days: int,
) -> str:
    parts = [f"{label}（{start:%m-%d}～{end:%m-%d}）回顧：{WEEK_DAYS} 天裡 {total_days} 天有活動。"]
    if ranked:
        parts.append("依活躍天數：" + "、".join(f"{row['project']} {row['days']} 天" for row in ranked) + "。")
    declared = comparison.get("declared") or []
    if not declared:
        parts.append("你還沒宣告本期優先（在對話框打「偏好：優先：…」），所以這週沒有「說的 vs 做的」可比。")
    else:
        head = "你宣告的本期優先：" + "、".join(_declared_phrase(item) for item in declared)
        drift = comparison.get("drift") or []
        if drift:
            instead = drift[0]["instead"]
            parts.append(f"{head}——說的和做的不一致：時間主要去了 {instead['project']}（{instead['days']} 天）。")
        elif comparison.get("aligned") is True:
            parts.append(f"{head}——說的和做的一致。")
        else:
            parts.append(f"{head}——這週活動太少，不好比。")
    parts.append(f"每日工作誌 {digest_days}/{WEEK_DAYS} 天。")
    body = " ".join(parts)
    return body if len(body) <= MAX_BODY_CHARS else body[: MAX_BODY_CHARS - 1] + "…"


def _priorities(database: Any) -> list[str]:
    from core.secretary_profile import load_profile

    return list(load_profile(database=database).get("priorities") or [])


def build_weekly_review(
    *,
    weeks_back: int = 1,
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
    write_memory: bool = True,
) -> dict[str, Any]:
    """把上一個完整週 reduce 成一則記憶區觀察；唯讀、不呼叫 LLM、同一週只寫一次。"""
    cfg = cfg or get_config()
    database = database or get_db()
    now = _naive(now or get_local_now())
    settings = review_settings(cfg)
    start, end, label = review_period(now, weeks_back)
    receipt: dict[str, Any] = {
        "period_label": label,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "weeks_back": min(MAX_WEEKS_BACK, max(1, int(weeks_back))),
        "enabled": settings["enabled"],
        "llm_used": False,
        "sources": list(SOURCES),
        "claim_boundary": REVIEW_CLAIM_BOUNDARY,
    }
    if not settings["enabled"]:
        receipt.update({"status": "disabled", "notes_written": 0})
        return receipt

    active_days, total_days = active_days_by_project(start, end, database=database)
    priorities = _priorities(database)
    comparison = compare_said_vs_done(active_days=active_days, priorities=priorities, settings=settings)
    ranked = rank_projects(active_days)
    digest_days = digest_days_in(start, end, database=database)
    text = compose_review_body(
        label=label, start=start, end=end, total_days=total_days,
        ranked=ranked, comparison=comparison, digest_days=digest_days,
    )
    receipt.update({
        "observed_anything": total_days > 0,
        "active_days": total_days,
        "projects": ranked,
        "declared": comparison["declared"],
        "drift": comparison["drift"],
        "aligned": comparison["aligned"],
        "digest_days": digest_days,
        "text": text,
        "notes_written": 0,
    })
    if not (write_memory and total_days > 0):
        return receipt
    from core.secretary_memory import memory_enabled, record_observation

    if not memory_enabled(cfg):
        return receipt
    try:
        note = record_observation(
            title=f"{label} 回顧（{start:%m-%d}～{end:%m-%d}）",
            body=text,
            source_ref=f"weekly_review:{label}",
            source="weekly_review",
            database=database,
            now=now,
        )
    except Exception as exc:  # noqa: BLE001 — 寫不進去不該讓動作失敗
        logger.warning("weekly review note not written (%s): %s", label, exc)
        note = None
    receipt["notes_written"] = 1 if note else 0
    return receipt


def _review_note_refs(label: str, *, database: Any) -> list[dict[str, Any]]:
    with database.session_scope() as session:
        rows = (
            session.query(SecretaryNote.source_ref, SecretaryNote.created_at)
            .filter(SecretaryNote.kind == "observation", SecretaryNote.source_ref == f"weekly_review:{label}")
            .all()
        )
    return [{"source_ref": str(ref), "kind": "weekly_review", "observed_at": created} for ref, created in rows]


def collect_priority_drift_signals(
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """上一個完整週「說了沒做」的宣告優先，各一張 ``priority_drift`` 訊號；即時從表計算，不依賴回顧有沒有跑。"""
    cfg = cfg or get_config()
    database = database or get_db()
    now = _naive(now or get_local_now())
    settings = review_settings(cfg)
    if not settings["enabled"]:
        return [], {"used": False, "reason": "disabled"}
    start, end, label = review_period(now, 1)
    priorities = _priorities(database)
    if not priorities:
        return [], {"used": True, "period": label, "reason": "no_priorities", "drift": 0, "aligned": None}
    active_days, _total = active_days_by_project(start, end, database=database)
    comparison = compare_said_vs_done(active_days=active_days, priorities=priorities, settings=settings)
    refs = _review_note_refs(label, database=database)
    signals: list[dict[str, Any]] = []
    for item in comparison["drift"]:
        instead = item["instead"]
        project_key = item["matched_key"] or str(item["project"])
        gap = max(0, int(instead["days"]) - int(item["days"]))
        signals.append({
            "signal_type": "priority_drift",
            "project_key": project_key,
            "subject_ref": f"priority_drift:{label}:{project_key}",
            "evidence_ref": f"activity_matrix:{start.isoformat()}..{end.isoformat()}",
            "observed_at": datetime.combine(end, dtime.max).replace(microsecond=0),
            "url": None,
            "age_days": float((now.date() - end).days),
            "open_loop_refs": [],
            "title": (
                f"你說「{item['project']}」優先，上週它只有 {item['days']} 天在動、"
                f"{instead['project']} 有 {instead['days']} 天"
            ),
            "detail": (
                f"{label}（{start:%m-%d}～{end:%m-%d}）：{item['project']} {item['days']} 天；"
                f"活躍最多的是 {instead['project']}（{instead['days']} 天）。這只是兩個數字放在一起，為什麼由你判斷。"
            ),
            "reasons": ["你宣告的本期優先與上週實際活動不一致"],
            "score": round(min(0.85, 0.6 + 0.05 * gap), 3),
            "evidence_extra": refs,
        })
    meta = {
        "used": True,
        "period": label,
        "declared": comparison["declared"],
        "drift": len(signals),
        "aligned": comparison["aligned"],
    }
    return signals, meta
