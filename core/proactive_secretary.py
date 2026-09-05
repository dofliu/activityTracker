"""P5-1 proposal-only 主動秘書；只讀取本機 evidence，不執行任何行動。

分流（triage）而非派工（dispatch）：專案太多時，這裡負責回答
「接下來該碰哪一個、為什麼」，決定權仍在使用者手上。

訊號來源見 `core/triage_signals.py`。回饋（snooze / 忽略）由 `proposal_snoozes` 承載——
沒有回饋迴路，清單會一直重推使用者已經判斷過不重要的事，永遠不會變準。
"""

from __future__ import annotations

from datetime import datetime
import hashlib
from typing import Any

from .config import get_config
from .database import get_db
from .extension_monitor import build_extension_status
from .models import ProposalSnooze
from .time_utils import get_local_now
from .triage_signals import (
    collect_issue_signals,
    collect_open_loop_signals,
    collect_pr_signals,
    repo_issue_backlog,
)


CLAIM_BOUNDARY = (
    "Proposals are deterministic read-only suggestions derived from observed local evidence. "
    "They are not executed, persisted, or proof that the suggested action is necessary or correct."
)

# 每種訊號對應的下一步；措辭一律是使用者自己要做的判斷，不是交給系統代勞。
SUGGESTED_ACTIONS = {
    "ci_failing_pr": "先看 CI 失敗的檢查項目，修好再 merge；若已不需要則直接關閉。",
    "review_ready_pr": "檢視差異後 merge，或留下 review 意見。",
    "aging_pr": "確認這個 PR 還要不要——繼續推進、轉回 draft 或關閉。",
    "assigned_issue": "確認目前狀態，回報進度或重新指派。",
    "aging_issue": "判斷是否仍需處理；不需要就關閉，需要就排入本週。",
    "stalled_open_loop": "先看 Context Handoff 與來源，再決定要繼續、標記 stale 或結案。",
    "unfinished_recent": "趁脈絡還在，把未收尾的部分收掉或明確標記下一步。",
    "verify_extension_heartbeat": "在 Chrome 重新載入 unpacked Extension，開啟 popup 後檢查 heartbeat 與逐站 Content Ready。",
    "repo_needs_pull": "確認沒有未保存的工作後批准 fast-forward pull；或先 Fetch 看看遠端是否又有新變更。",
    "repo_needs_push": "到同步中心確認這些 commit 該發佈後再 Push（不會 force）。",
    "repo_diverged": "本機與遠端各有新 commit；在 Git/IDE 手動 merge 或 rebase，系統不會代為處理。",
    # ADR-017 模式感知：對應的都是既有 template，這裡只講該做的判斷。
    "no_daily_routine": "在「01 小秘書 → 今日行動清單」按「📦 建立每日排程」（需 execution token）；之後每天早上會有早晨包與工作誌，記憶區才會累積。",
    "neglected_active_project": "看一眼 Context Handoff 決定要接續還是明確放下；不決定的話脈絡會繼續流失。",
}


def why_now(signal_type: str, age_days: float, extra: dict[str, Any] | None = None) -> str:
    """一句話說明「為什麼是現在」：把排序依據講給使用者聽，而不是只給分數。"""
    extra = extra or {}
    days = max(0.0, float(age_days or 0.0))
    hours = int(round(days * 24))
    if signal_type == "ci_failing_pr":
        return "CI 紅燈擋住合併，越晚修越容易和其他改動衝突" + (f"；已 {int(days)} 天" if days >= 1 else "")
    if signal_type == "review_ready_pr":
        return "只差一個 review／merge 動作，成本最低、收益立即"
    if signal_type == "aging_pr":
        return f"已放 {int(days)} 天，再放下去脈絡會流失、衝突會累積"
    if signal_type == "assigned_issue":
        return "指派給你且仍開著" + (f"，已 {int(days)} 天" if days >= 1 else "")
    if signal_type == "aging_issue":
        return f"沒人認領 {int(days)} 天；決定要不要做比一直掛著便宜"
    if signal_type == "unfinished_recent":
        return f"{hours} 小時前還在動，脈絡還新鮮，現在收尾最省力"
    if signal_type == "stalled_open_loop":
        return f"停滯 {int(days)} 天但仍有未結事項，再放就要重新讀脈絡"
    if signal_type == "verify_extension_heartbeat":
        return "沒有 heartbeat 就沒有瀏覽器對話收集，今天的紀錄會缺一塊"
    if signal_type == "repo_needs_pull":
        return "本機落後遠端，繼續開發前先同步可避免之後合併衝突"
    if signal_type == "repo_needs_push":
        return "本機 commit 尚未備份到遠端，其他機器與 CI 都看不到"
    if signal_type == "repo_diverged":
        return "本機與遠端各自前進，越久越難合"
    if signal_type == "no_daily_routine":
        return "秘書只記得它跑過的日子；越早建立排程，記憶區越早有底"
    if signal_type == "neglected_active_project":
        return f"上週還很活躍、這週歸零；再放 {int(days)} 天就得重讀脈絡"
    return ""


def _local_naive(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


def _proposal_id(proposal_type: str, project_key: str, evidence_refs: list[str]) -> str:
    material = "|".join([proposal_type, project_key, *sorted(evidence_refs)])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def _evidence(source_ref: str, kind: str, observed_at: datetime | None) -> dict[str, Any]:
    return {
        "source_ref": source_ref,
        "kind": kind,
        "observed_at": (
            _local_naive(observed_at).isoformat(timespec="seconds")
            if observed_at is not None
            else None
        ),
    }


def _priority_from_score(score: float) -> str:
    if score >= 0.80:
        return "high"
    if score >= 0.55:
        return "medium"
    return "low"


def _apply_project_diversity(
    proposals: list[dict[str, Any]], max_per_project: int
) -> list[dict[str, Any]]:
    """每個專案最多保留 N 項。

    沒有這道限制，一個累積了 8 個 PR 的 repo 會把整張清單佔滿，
    分流就退化成「看某個 repo 的 PR 列表」，失去跨專案比較的意義。
    被折疊的數量掛在保留項目上，使用者仍看得到那裡還有多少事。
    """
    kept: list[dict[str, Any]] = []
    per_project: dict[str, int] = {}
    suppressed: dict[str, int] = {}

    for item in proposals:
        key = item["project_key"]
        if per_project.get(key, 0) < max_per_project:
            per_project[key] = per_project.get(key, 0) + 1
            kept.append(item)
        else:
            suppressed[key] = suppressed.get(key, 0) + 1

    for item in kept:
        item["same_project_pending"] = suppressed.get(item["project_key"], 0)
    return kept


def _disabled_result(now: datetime) -> dict[str, Any]:
    return {
        "status": "disabled",
        "mode": "proposal_only",
        "proposals": [],
        "generated_at": now.isoformat(timespec="seconds"),
        "execution_available": False,
        "cloud_llm_used": False,
        "query_persisted": False,
        "claim_boundary": CLAIM_BOUNDARY,
    }


def _active_snoozes(session: Any, now: datetime) -> set[tuple[str, str, str]]:
    """回傳仍在生效的 snooze 目標；過期的自動失效，不需要清理排程。"""
    active: set[tuple[str, str, str]] = set()
    for row in session.query(ProposalSnooze).all():
        if row.dismissed:
            active.add((row.proposal_type, row.project_key, row.subject_ref or ""))
            continue
        until = _local_naive(row.snoozed_until)
        if until is not None and until > now:
            active.add((row.proposal_type, row.project_key, row.subject_ref or ""))
    return active


def _signal_to_proposal(signal: dict[str, Any], now: datetime) -> dict[str, Any]:
    evidence = [
        _evidence(
            signal["evidence_ref"],
            signal["signal_type"],
            signal.get("observed_at"),
        )
    ]
    # 未結事項只帶 source_ref，不帶標題：標題可能含使用者的原始提問內容。
    for ref in signal.get("open_loop_refs", []):
        evidence.append(_evidence(ref, "open_loop", signal.get("observed_at")))
    # ADR-017：模式提案把已寫進記憶區的工作誌當旁證附上（有才附，沒有不編）。
    for extra in signal.get("evidence_extra", []) or []:
        evidence.append(_evidence(extra["source_ref"], extra.get("kind", "memory"), extra.get("observed_at")))

    evidence_refs = [item["source_ref"] for item in evidence]
    score = float(signal["score"])

    proposal = {
        "proposal_id": _proposal_id(
            signal["signal_type"], signal["project_key"], evidence_refs
        ),
        "proposal_type": signal["signal_type"],
        "project_key": signal["project_key"],
        "subject_ref": signal["subject_ref"],
        "title": signal["title"],
        "detail": signal.get("detail", ""),
        "reason": "；".join(signal["reasons"]),
        "reasons": list(signal["reasons"]),
        "suggested_action": SUGGESTED_ACTIONS.get(signal["signal_type"], ""),
        "why_now": why_now(signal["signal_type"], signal.get("age_days", 0.0)),
        "priority": _priority_from_score(score),
        "risk_level": "L0_READ_ONLY",
        "execution_available": False,
        "url": signal.get("url"),
        "age_days": signal.get("age_days", 0.0),
        "evidence_refs": evidence_refs,
        "evidence": evidence,
        "score": round(score, 3),
    }
    if signal.get("habit_boosted"):
        proposal["habit_boosted"] = True
    return proposal


def build_action_proposals(
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
    limit: int | None = None,
    extension_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """建立唯讀 proposals；此函式不得寫資料庫或呼叫外部 LLM。"""
    database = database or get_db()
    cfg = cfg or get_config()
    now = _local_naive(now or get_local_now())
    configured_limit = int(cfg.get("proactive_secretary.max_proposals", 6))
    result_limit = max(1, min(int(limit or configured_limit), 12))
    if not bool(cfg.get("proactive_secretary.enabled", True)):
        return _disabled_result(now)

    stalled_hours = max(
        1,
        min(int(cfg.get("proactive_secretary.stalled_open_loop_hours", 48)), 24 * 90),
    )
    # 剛動過的專案不需要提醒——你正在做。要閒置超過這個門檻才值得提「還沒收尾」。
    recent_idle_hours = max(
        1,
        min(int(cfg.get("proactive_secretary.unfinished_recent_min_idle_hours", 12)), stalled_hours),
    )

    proposals: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []

    # Extension status 是非敏感 derived status；不得將歷史 event 等同近期 heartbeat。
    status = extension_status
    if status is None:
        status = build_extension_status(database=database, cfg=cfg, now=now)
    extension = status.get("extension", {}) if isinstance(status, dict) else {}
    if extension.get("token_configured") and not extension.get("heartbeat_verified"):
        evidence_refs = ["extension_status:live"]
        proposals.append({
            "proposal_id": _proposal_id(
                "verify_extension_heartbeat", "OmniContext", evidence_refs
            ),
            "proposal_type": "verify_extension_heartbeat",
            "project_key": "OmniContext",
            "subject_ref": "extension:heartbeat",
            "title": "驗證 Browser Extension 即時連線",
            "detail": "",
            "reason": "本機服務已有 ingest token，但目前沒有近期 token-authenticated heartbeat receipt。",
            "reasons": ["本機服務已有 ingest token，但目前沒有近期 token-authenticated heartbeat receipt。"],
            "suggested_action": SUGGESTED_ACTIONS["verify_extension_heartbeat"],
            "why_now": why_now("verify_extension_heartbeat", 0.0),
            "priority": "high",
            "risk_level": "L0_READ_ONLY",
            "execution_available": False,
            "url": None,
            "age_days": 0.0,
            "evidence_refs": evidence_refs,
            "evidence": [
                _evidence("extension_status:live", "derived_extension_status", now)
            ],
            "score": 1.0,
        })

    counters = {
        "open_prs": 0,
        "open_issues": 0,
        "open_loop_projects": 0,
        "snoozed": 0,
    }

    with database.session_scope() as session:
        snoozed = _active_snoozes(session, now)

        pr_signals = collect_pr_signals(session, now)
        issue_signals = collect_issue_signals(session, now)
        loop_signals = collect_open_loop_signals(session, now, stalled_hours)

        # 剛動過就不提醒；閒置超過門檻才納入
        loop_signals = [
            item
            for item in loop_signals
            if item["signal_type"] != "unfinished_recent"
            or item["age_days"] * 24 >= recent_idle_hours
        ]

        counters["open_prs"] = len(pr_signals)
        counters["open_issues"] = len(issue_signals)
        counters["open_loop_projects"] = len(loop_signals)
        counters["repo_issue_backlog"] = repo_issue_backlog(session)

        signals = pr_signals + issue_signals + loop_signals

    # Repo 同步提案只讀最近一次 L0 排程報告留下的快照（沒有或過期就不提），
    # 因為 proposals 每次請求都會重建，不能在這裡對數十個 repo 跑 git status。
    try:
        from .repo_sync_report import collect_repo_sync_signals

        repo_signals, repo_snapshot_meta = collect_repo_sync_signals(cfg=cfg, now=now)
    except Exception as exc:  # noqa: BLE001 — 快照損毀不得拖垮整個提案清單
        repo_signals, repo_snapshot_meta = [], {"used": False, "reason": f"error:{type(exc).__name__}"}
    counters["repo_sync_snapshot"] = repo_snapshot_meta
    signals = signals + repo_signals

    # ADR-017 模式感知：只用（專案 × 日）活動計數、只算已結束的日子。
    # (a) 新訊號：沒有每日例行、被冷落的專案；(b) 排序：主線專案的既有訊號加權。
    pattern_meta: dict[str, Any] = {"used": False}
    try:
        from .activity_patterns import apply_habit_boost, collect_pattern_signals

        loop_projects = {str(item["project_key"]) for item in loop_signals}
        pattern_signals, pattern_meta = collect_pattern_signals(
            database=database, cfg=cfg, now=now, exclude_projects=loop_projects
        )
        pattern_meta["habit_boosted"] = apply_habit_boost(
            signals, cfg=cfg, recent_active=pattern_meta.get("recent_active_by_project", {})
        )
        signals = signals + pattern_signals
    except Exception as exc:  # noqa: BLE001 — 模式層故障不得拖垮提案清單
        pattern_meta = {"used": False, "reason": f"error:{type(exc).__name__}"}
    counters["patterns"] = pattern_meta

    # 記憶區（ADR-012）：偏好筆記裡的「不要提醒 X」壓掉提案；決定／筆記附在同專案的提案卡上。
    mutes: set[str] = set()
    memory_lines: dict[str, list[str]] = {}
    counters["memory_muted"] = 0
    try:
        from .secretary_memory import memory_enabled, preference_mutes, project_memory_lines

        if memory_enabled(cfg):
            mutes = preference_mutes(database=database)
            memory_lines = project_memory_lines(database=database)
    except Exception as exc:  # noqa: BLE001 — 記憶區故障不得拖垮提案清單
        counters["memory_error"] = type(exc).__name__

    with database.session_scope() as session:
        snoozed = _active_snoozes(session, now)
        for signal in signals:
            key = (signal["signal_type"], signal["project_key"], signal["subject_ref"])
            if key in snoozed:
                counters["snoozed"] += 1
                continue
            if mutes and (
                str(signal["signal_type"]).lower() in mutes
                or str(signal["project_key"]).lower() in mutes
            ):
                counters["memory_muted"] += 1
                continue
            proposal = _signal_to_proposal(signal, now)
            notes = memory_lines.get(str(signal["project_key"]))
            if notes:
                proposal["memory_note"] = notes[0]
            proposals.append(proposal)

    priority_rank = {"high": 0, "medium": 1, "low": 2}
    proposals.sort(
        key=lambda item: (
            priority_rank.get(item["priority"], 9),
            -float(item["score"]),
            item["proposal_id"],
        )
    )
    total_candidates = len(proposals)
    max_per_project = max(1, int(cfg.get("proactive_secretary.max_per_project", 2)))
    proposals = _apply_project_diversity(proposals, max_per_project)

    return {
        "status": "proposal_only",
        "mode": "proposal_only",
        "proposals": proposals[:result_limit],
        "total_candidates": total_candidates,
        "generated_at": now.isoformat(timespec="seconds"),
        "inputs": {
            "open_prs": counters["open_prs"],
            "open_issues": counters["open_issues"],
            "open_loop_projects": counters["open_loop_projects"],
            "snoozed_suppressed": counters["snoozed"],
            "memory_muted": counters.get("memory_muted", 0),
            "repo_issue_backlog": counters.get("repo_issue_backlog", {}),
            "repo_sync_snapshot": counters.get("repo_sync_snapshot", {}),
            "patterns": counters.get("patterns", {}),
            "max_per_project": max_per_project,
            "stalled_open_loop_hours": stalled_hours,
            "unfinished_recent_min_idle_hours": recent_idle_hours,
        },
        "execution_available": False,
        "cloud_llm_used": False,
        "query_persisted": False,
        "claim_boundary": CLAIM_BOUNDARY,
    }


def briefing_proposals(
    limit: int = 3,
    *,
    with_advisor: bool = True,
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """P5-R4：給晨報通知與每日入口檔用的 top 建議摘要。

    仍為唯讀：只是把既有 proposal-only 結果整理成適合通知的欄位；
    advisor 註解依設定沿用（預設關閉；失敗自動回退純規則）。
    """
    result = build_action_proposals(
        database=database, cfg=cfg, now=now, limit=max(1, min(int(limit), 6))
    )
    if with_advisor:
        from core.secretary_advisor import annotate_action_proposals

        result = annotate_action_proposals(result, cfg=cfg)

    top = [
        {
            "title": item.get("title") or "",
            "detail": item.get("detail") or "",
            "project_key": item.get("project_key") or "",
            "priority": item.get("priority") or "medium",
            "suggested_action": item.get("suggested_action") or "",
            "why_now": item.get("why_now") or "",
            "llm_note": item.get("llm_note"),
        }
        for item in result.get("proposals", [])[: max(1, int(limit))]
    ]
    advisor = result.get("advisor") or {}
    return {
        "proposals": top,
        "total": int(result.get("total_candidates") or len(top)),
        "advisor_summary": advisor.get("summary"),
        "claim_boundary": "建議僅供判斷，不會自動執行。",
    }


def snooze_proposal(
    *,
    proposal_type: str,
    project_key: str,
    subject_ref: str = "",
    days: int | None = 7,
    dismissed: bool = False,
    note: str | None = None,
    database: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """記錄「這個先不要再提醒我」。

    這是唯一會寫入資料庫的秘書相關操作，且只寫 proposal_snoozes，
    不觸碰任何事件資料，也不改變 build_action_proposals 的唯讀性質。
    """
    from datetime import timedelta

    database = database or get_db()
    now = _local_naive(now or get_local_now())
    until = None if dismissed or days is None else now + timedelta(days=max(1, int(days)))

    with database.session_scope() as session:
        record = (
            session.query(ProposalSnooze)
            .filter_by(
                proposal_type=proposal_type,
                project_key=project_key,
                subject_ref=subject_ref or "",
            )
            .first()
        )
        if record is None:
            record = ProposalSnooze(
                proposal_type=proposal_type,
                project_key=project_key,
                subject_ref=subject_ref or "",
                created_at=now,
            )
            session.add(record)
        record.snoozed_until = until
        record.dismissed = bool(dismissed)
        record.note = note

    return {
        "status": "snoozed",
        "proposal_type": proposal_type,
        "project_key": project_key,
        "subject_ref": subject_ref or "",
        "dismissed": bool(dismissed),
        "snoozed_until": until.isoformat(timespec="seconds") if until else None,
    }
