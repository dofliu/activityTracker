"""P5-1 proposal-only 主動秘書；只讀取本機 evidence，不執行任何行動。

分流（triage）而非派工（dispatch）：專案太多時，這裡負責回答
「接下來該碰哪一個、為什麼」，決定權仍在使用者手上。

訊號來源見 `core/triage_signals.py`。回饋（snooze / 忽略）由 `proposal_snoozes` 承載——
沒有回饋迴路，清單會一直重推使用者已經判斷過不重要的事，永遠不會變準。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Callable

from dataclasses import replace

from core.config import get_config
from core.runtime_state import TtlCache, runtime_state
from core.database import get_db
from core.secretary.types import Proposal, Signal
from core.extension_monitor import build_extension_status
from core.models import ProposalSnooze
from core.time_utils import get_local_now
from core.secretary.signals import DEFAULT_GITHUB_STALE_AFTER_DAYS, collect_issue_signals, collect_open_loop_signals, collect_pr_signals, repo_issue_backlog, split_stale_github_signals
from core.llm_client import LLMClient, default_model
from core.repo_sync_report import collect_repo_sync_signals
from core.activity_patterns import apply_habit_boost, collect_pattern_signals
from core.weekly_review import collect_priority_drift_signals
from core.docs_freshness import collect_docs_freshness_signals
from core.meeting_transcripts import collect_meeting_signals
from core.secretary.memory import apply_priority_boost, load_profile, priority_boost_value
from core.secretary.memory import memory_enabled, preference_mutes, project_memory_lines


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
    # ADR-020 每週回顧：說的 vs 做的——二選一，別讓宣告只是字。
    "priority_drift": "決定一件事：下週把時間排給它（看一眼 Handoff 接續），或改宣告——在對話框打「偏好：優先：…」換成你真正在做的。",
    # ADR-021：這正是你最常手打的那句指令；秘書用既有的兩段式 L2 幫你走完。
    # ADR-022 會議秘書：候選待辦是「你好像答應了」，只有你能讓它成為承諾。
    "meeting_followups": "讀一遍候選待辦：要做的按「加入未結事項」，不做的按「忽略」；沒點的不會進任何計數。",
    "meeting_transcript_missing": "若那場會有開轉錄，從 Teams 下載逐字稿放進 meetings.transcript_dir；沒開轉錄就忽略這張卡。",
    "docs_behind_code": "開啟 L2 後按「起草文件更新計畫」，讀過那份計畫再批准「實際改檔」（不會 commit，改動留給你檢視）；也可以自己更新文件，卡就會消失。",
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
    if signal_type == "priority_drift":
        return "上一個完整週剛結束，現在調整下週最划算；再放一週，宣告就只是字"
    if signal_type == "meeting_followups":
        return "會議剛結束、脈絡還在，現在決定哪些真的要做最準" + (f"；已 {int(days)} 天" if days >= 1 else "")
    if signal_type == "meeting_transcript_missing":
        return f"{hours} 小時內剛結束；逐字稿在 Teams 上放久了你會忘記下載"
    if signal_type == "docs_behind_code":
        return f"commit 訊息還在、脈絡還記得；再放 {int(days)} 天就得回頭讀 diff 才寫得出文件"
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


def _proposal_from_signal(signal: Signal, now: datetime) -> Proposal:
    """Signal → Proposal（ADR-024）。欄位對應寫在這裡一次，JSON 形狀由 `Proposal.to_dict` 保證。"""
    evidence = [_evidence(signal.evidence_ref, signal.signal_type, signal.observed_at)]
    # 未結事項只帶 source_ref，不帶標題：標題可能含使用者的原始提問內容。
    for ref in signal.open_loop_refs:
        evidence.append(_evidence(ref, "open_loop", signal.observed_at))
    # ADR-017：模式提案把已寫進記憶區的工作誌當旁證附上（有才附，沒有不編）。
    for extra in signal.evidence_extra:
        evidence.append(_evidence(extra["source_ref"], extra.get("kind", "memory"), extra.get("observed_at")))

    evidence_refs = [item["source_ref"] for item in evidence]
    return Proposal(
        proposal_id=_proposal_id(signal.signal_type, signal.project_key, evidence_refs),
        proposal_type=signal.signal_type,
        project_key=signal.project_key,
        subject_ref=signal.subject_ref,
        title=signal.title,
        detail=signal.detail,
        reason="；".join(signal.reasons),
        reasons=signal.reasons,
        suggested_action=SUGGESTED_ACTIONS.get(signal.signal_type, ""),
        why_now=why_now(signal.signal_type, signal.age_days),
        priority=_priority_from_score(signal.score),
        score=signal.score,
        age_days=signal.age_days,
        evidence_refs=tuple(evidence_refs),
        evidence=tuple(evidence),
        url=signal.url,
        habit_boosted=signal.habit_boosted,
        priority_declared=signal.priority_declared,
        # ADR-021：文件更新的事實區塊由 server 組好帶進提案，L2 prompt 直接引用（呼叫端無法注入）
        docs_facts=signal.docs_facts,
        # ADR-022：候選待辦帶進卡片，讓「加入未結事項／忽略」按鈕有東西可點；
        # 這些字串來自 LLM 摘要，是候選而非承諾——沒被點過的不進任何計數。
        meeting_followups=signal.meeting_followups,
        meeting_note_id=signal.meeting_note_id,
    )


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
    # 超過這個天數沒更新的 PR／issue 不納入考量（0 = 不過濾）。
    github_stale_after_days = max(
        0,
        min(int(cfg.get("proactive_secretary.github_stale_after_days", DEFAULT_GITHUB_STALE_AFTER_DAYS)), 3650),
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

        # 幾個月沒動的 PR／issue 不是「現在該做的事」：不進提案，但如實計數（ADR-007 Addendum 2026-09-08）
        counters["open_prs"] = len(pr_signals)
        counters["open_issues"] = len(issue_signals)
        pr_signals, stale_prs = split_stale_github_signals(pr_signals, github_stale_after_days)
        issue_signals, stale_issues = split_stale_github_signals(issue_signals, github_stale_after_days)
        stale = stale_prs + stale_issues
        counters["github_stale_excluded"] = {
            "threshold_days": github_stale_after_days,
            "prs": len(stale_prs),
            "issues": len(stale_issues),
            "total": len(stale),
            "subjects": [
                {"subject_ref": item["subject_ref"], "age_days": item["age_days"]}
                for item in sorted(stale, key=lambda item: -float(item.get("age_days") or 0.0))[:10]
            ],
        }

        # 剛動過就不提醒；閒置超過門檻才納入
        loop_signals = [
            item
            for item in loop_signals
            if item["signal_type"] != "unfinished_recent"
            or item["age_days"] * 24 >= recent_idle_hours
        ]

        counters["open_loop_projects"] = len(loop_signals)
        counters["repo_issue_backlog"] = repo_issue_backlog(session)

        signals = pr_signals + issue_signals + loop_signals

    # Repo 同步提案只讀最近一次 L0 排程報告留下的快照（沒有或過期就不提），
    # 因為 proposals 每次請求都會重建，不能在這裡對數十個 repo 跑 git status。
    try:

        repo_signals, repo_snapshot_meta = collect_repo_sync_signals(cfg=cfg, now=now)
    except Exception as exc:  # noqa: BLE001 — 快照損毀不得拖垮整個提案清單
        repo_signals, repo_snapshot_meta = [], {"used": False, "reason": f"error:{type(exc).__name__}"}
    counters["repo_sync_snapshot"] = repo_snapshot_meta
    signals = signals + repo_signals

    # ADR-017 模式感知：只用（專案 × 日）活動計數、只算已結束的日子。
    # (a) 新訊號：沒有每日例行、被冷落的專案；(b) 排序：主線專案的既有訊號加權。
    pattern_meta: dict[str, Any] = {"used": False}
    try:

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

    # ADR-020 每週回顧：你宣告的優先 vs 上一個完整週的實際活動，不一致就一張 priority_drift。
    # 同一個專案若同時被判「被冷落」，只留 priority_drift——它多講了「你說過這是優先」。
    review_meta: dict[str, Any] = {"used": False}
    try:

        drift_signals, review_meta = collect_priority_drift_signals(database=database, cfg=cfg, now=now)
        if drift_signals:
            drift_projects = {str(item["project_key"]).casefold() for item in drift_signals}
            signals = [
                item for item in signals
                if not (
                    item.get("signal_type") == "neglected_active_project"
                    and str(item.get("project_key") or "").casefold() in drift_projects
                )
            ]
            signals = signals + drift_signals
    except Exception as exc:  # noqa: BLE001 — 回顧層故障不得拖垮提案清單
        review_meta = {"used": False, "reason": f"error:{type(exc).__name__}"}
    counters["weekly_review"] = review_meta

    # ADR-021 文件落後程式：文件最後異動後又累積了幾個 commit（只比時間與數量）。
    docs_meta: dict[str, Any] = {"used": False}
    try:

        docs_signals, docs_meta = collect_docs_freshness_signals(database=database, cfg=cfg, now=now)
        signals = signals + docs_signals
    except Exception as exc:  # noqa: BLE001 — 文件層故障不得拖垮提案清單
        docs_meta = {"used": False, "reason": f"error:{type(exc).__name__}"}
    counters["docs_freshness"] = docs_meta

    # ADR-022 會議秘書：待處理的候選待辦、剛結束卻沒有逐字稿的會議。
    meetings_meta: dict[str, Any] = {"used": False}
    try:

        meeting_signals, meetings_meta = collect_meeting_signals(database=database, cfg=cfg, now=now)
        signals = signals + meeting_signals
    except Exception as exc:  # noqa: BLE001 — 會議層故障不得拖垮提案清單
        meetings_meta = {"used": False, "reason": f"error:{type(exc).__name__}"}
    counters["meetings"] = meetings_meta

    # ADR-018 宣告式個人檔案：你標為「本期優先」的專案，所有訊號（含被冷落）加分。
    # 加分刻意大於習慣加權——你說的優先勝過我從活動推出來的主線。
    profile_meta: dict[str, Any] = {"declared": False}
    try:

        profile = load_profile(database=database)
        profile_meta = {
            "declared": profile["declared"],
            "priorities": profile["priorities"],
            "tone": profile["tone"],
            "priority_boosted": apply_priority_boost(
                signals, profile["priorities"], boost=priority_boost_value(cfg)
            ),
        }
    except Exception as exc:  # noqa: BLE001 — 個人檔案故障不得拖垮提案清單
        profile_meta = {"declared": False, "reason": f"error:{type(exc).__name__}"}
    counters["profile"] = profile_meta

    # 記憶區（ADR-012）：偏好筆記裡的「不要提醒 X」壓掉提案；決定／筆記附在同專案的提案卡上。
    mutes: set[str] = set()
    memory_lines: dict[str, list[str]] = {}
    counters["memory_muted"] = 0
    try:

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
            # 定型的交接點（ADR-024）：收集器的 dict 到這裡一次驗完，之後只有 Proposal
            typed = Signal.from_dict(signal)
            proposal = _proposal_from_signal(typed, now)
            notes = memory_lines.get(typed.project_key)
            if notes:
                proposal = replace(proposal, memory_note=notes[0])
            proposals.append(proposal.to_dict())

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
            "github_stale_excluded": counters.get(
                "github_stale_excluded",
                {"threshold_days": github_stale_after_days, "prs": 0, "issues": 0, "total": 0, "subjects": []},
            ),
            "snoozed_suppressed": counters["snoozed"],
            "memory_muted": counters.get("memory_muted", 0),
            "repo_issue_backlog": counters.get("repo_issue_backlog", {}),
            "repo_sync_snapshot": counters.get("repo_sync_snapshot", {}),
            "patterns": counters.get("patterns", {}),
            "profile": counters.get("profile", {}),
            "weekly_review": counters.get("weekly_review", {}),
            "docs_freshness": counters.get("docs_freshness", {}),
            "meetings": counters.get("meetings", {}),
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


# ---------------------------------------------------------------- P5-R1 LLM 註解層（原 core/secretary_advisor.py）
#
# P5-R1 LLM advisory 層：對 deterministic proposals 做唯讀註解（ADR-008 階段 1）。
# 嚴格 annotate-only 契約：
# - **不得**新增、刪除、重排 proposals，也不得修改任何 deterministic 欄位；
#   LLM 只能為既有 `proposal_id` 附加 `llm_note` 與 `llm_priority_hint`，
#   以及 envelope 層級的 `advisor.summary`。
# - 預設關閉（`proactive_secretary.llm_advisor.enabled: false`）；關閉時
#   輸出與 ADR-007 proposal-only 完全一致（僅多出 status=disabled 的
#   `advisor` 標示欄位）。
# - 預設 provider 為本機 Ollama。選擇 cloud provider 代表使用者同意將
#   proposal 的白名單欄位（title / reason / suggested_action 等，不含
#   prompt 全文、token 或本機路徑）送往該供應商——與 synthesizer 摘要的
#   既有資料邊界相同；此時 envelope 的 `cloud_llm_used` 會如實轉為 true。
# - 任何失敗（連線、逾時、非 JSON、schema 不符）→ 原樣回傳 deterministic
#   結果並標記 `fallback_deterministic`；秘書功能永不因 LLM 不可用而中斷。
# - 註解不落地：僅有程序內 TTL cache 避免重複呼叫，不寫入 SQLite。



logger = logging.getLogger("OmniContext.SecretaryAdvisor")

ADVISOR_CLAIM_BOUNDARY = (
    "LLM annotations are read-only advisory text over deterministic proposals; "
    "they cannot add, remove or execute anything, and are not persisted."
)

# proposal 只有這些欄位允許進入 prompt——白名單而非黑名單。
PROMPT_FIELDS = (
    "proposal_id",
    "proposal_type",
    "project_key",
    "title",
    "detail",
    "reason",
    "suggested_action",
    "priority",
    "age_days",
    "score",
    "same_project_pending",
)

MAX_NOTE_CHARS = 300
MAX_SUMMARY_CHARS = 600
_ALLOWED_PRIORITY_HINTS = {"high", "medium", "low"}

_SYSTEM_PROMPT = (
    "你是一位唯讀的個人工作分流顧問。輸入是一份由規則引擎產生的工作建議清單"
    "（JSON）。你的任務：\n"
    "1. 為每一項建議寫一句更聰明、更具體的繁體中文判斷提示（note，80 字內），"
    "幫助使用者決定先後與取捨；可指出項目之間的關聯。\n"
    "2. 對排序給出 priority_hint（high/medium/low）。\n"
    "3. 用 2-3 句寫一段今日整體 summary（150 字內）。\n"
    "限制：你沒有執行能力，不得建議執行任何系統指令、刪除資料或自動化操作；"
    "不得虛構清單以外的事項；只能引用輸入中出現的 proposal_id。\n"
    "輸出格式：只回傳一個 JSON 物件，不要其他文字：\n"
    '{"summary": "...", "annotations": [{"proposal_id": "...", "note": "...", '
    '"priority_hint": "high|medium|low"}]}'
)


def advisor_settings(cfg: Any | None = None) -> dict[str, Any]:
    cfg = cfg or get_config()
    provider = str(
        cfg.get("proactive_secretary.llm_advisor.provider", "ollama") or "ollama"
    ).lower()

    def _clamped(key: str, default: int, low: int, high: int) -> int:
        try:
            return min(high, max(low, int(cfg.get(key, default))))
        except (TypeError, ValueError):
            return default

    return {
        "enabled": bool(cfg.get("proactive_secretary.llm_advisor.enabled", False)),
        "provider": provider,
        "cloud": provider != "ollama",
        "model": default_model(provider, cfg),  # 預設模型只在 core/llm_client 定義一份（D2）
        "timeout_seconds": _clamped(
            "proactive_secretary.llm_advisor.timeout_seconds", 20, 5, 120
        ),
        "cache_minutes": _clamped(
            "proactive_secretary.llm_advisor.cache_minutes", 10, 0, 240
        ),
    }


def _prompt_payload(proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: item.get(key) for key in PROMPT_FIELDS if item.get(key) not in (None, "")}
        for item in proposals
    ]


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """從模型輸出擷取第一個 JSON 物件；容忍 code fence 與前後雜訊。"""
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", str(text))
    start = cleaned.find("{")
    if start < 0:
        return None
    depth = 0
    for index in range(start, len(cleaned)):
        char = cleaned[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = cleaned[start : index + 1]
                try:
                    parsed = json.loads(candidate)
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


def _clean_text(value: Any, max_chars: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value).strip()
    if not text:
        return None
    return text[:max_chars]


def _sanitize(
    parsed: dict[str, Any], valid_ids: set[str]
) -> tuple[str | None, dict[str, dict[str, Any]]]:
    """只保留合法 proposal_id 的註解；未知 id、超長與非法值一律丟棄。"""
    summary = _clean_text(parsed.get("summary"), MAX_SUMMARY_CHARS)
    annotations: dict[str, dict[str, Any]] = {}
    raw_items = parsed.get("annotations")
    if isinstance(raw_items, list):
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            proposal_id = str(raw.get("proposal_id") or "")
            if proposal_id not in valid_ids or proposal_id in annotations:
                continue
            note = _clean_text(raw.get("note"), MAX_NOTE_CHARS)
            hint = str(raw.get("priority_hint") or "").lower()
            entry: dict[str, Any] = {}
            if note:
                entry["llm_note"] = note
            if hint in _ALLOWED_PRIORITY_HINTS:
                entry["llm_priority_hint"] = hint
            if entry:
                annotations[proposal_id] = entry
    return summary, annotations


# advisor 摘要快取住在 core/runtime_state 的 TtlCache（ADR-027）：程序內、不寫 SQLite、
# 重啟即失效——與「註解不落地」一致。


def _default_generate(provider: str, timeout_seconds: int) -> Callable[[str, str], str]:
    def _run(system_prompt: str, user_prompt: str) -> str:

        client = LLMClient(provider)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(client.generate, system_prompt, user_prompt)
            return future.result(timeout=timeout_seconds)

    return _run


def annotate_action_proposals(
    result: dict[str, Any],
    *,
    cfg: Any | None = None,
    now: datetime | None = None,
    llm_generate: Callable[[str, str], str] | None = None,
    cache: TtlCache | None = None,
) -> dict[str, Any]:
    """包裝 ``build_action_proposals`` 的輸出；永不改變 deterministic 內容。"""
    cfg = cfg or get_config()
    cache = cache if cache is not None else runtime_state().advisor_cache
    settings = advisor_settings(cfg)
    advisor: dict[str, Any] = {
        "enabled": settings["enabled"],
        "provider": settings["provider"] if settings["enabled"] else None,
        "model": settings["model"] if settings["enabled"] else None,
        "status": "disabled",
        "annotated": 0,
        "summary": None,
        "claim_boundary": ADVISOR_CLAIM_BOUNDARY,
    }
    result["advisor"] = advisor
    if not settings["enabled"]:
        return result
    proposals = result.get("proposals") or []
    if result.get("status") != "proposal_only" or not proposals:
        advisor["status"] = "skipped_no_proposals"
        return result

    now = now or get_local_now()
    payload = _prompt_payload(proposals)
    payload_text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    cache_key = hashlib.sha256(
        f"{settings['provider']}|{settings['model']}|{payload_text}".encode("utf-8")
    ).hexdigest()
    valid_ids = {str(item.get("proposal_id")) for item in proposals}

    cached = cache.get(cache_key, now)
    if cached is not None:
        summary, annotations = cached
        advisor["status"] = "cached"
    else:
        generate = llm_generate or _default_generate(
            settings["provider"], settings["timeout_seconds"]
        )
        try:
            raw = generate(_SYSTEM_PROMPT, payload_text)
        except Exception as exc:  # noqa: BLE001 — 含 timeout；失敗一律回退
            logger.warning("Secretary advisor unavailable: %s", type(exc).__name__)
            advisor["status"] = "fallback_deterministic"
            advisor["fallback_reason"] = type(exc).__name__
            return result
        parsed = _extract_json_object(raw)
        if parsed is None:
            advisor["status"] = "fallback_deterministic"
            advisor["fallback_reason"] = "invalid_json"
            return result
        summary, annotations = _sanitize(parsed, valid_ids)
        if not summary and not annotations:
            # 例如 LLMClient 的備援 markdown 夾帶了 payload 裡的 JSON 片段：
            # 解析得出物件但沒有任何可用註解，一律視為失敗且不得寫入 cache。
            advisor["status"] = "fallback_deterministic"
            advisor["fallback_reason"] = "no_usable_annotations"
            return result
        cache.put(cache_key, (summary, annotations), now, timedelta(minutes=settings["cache_minutes"]))
        advisor["status"] = "annotated"

    for item in proposals:
        entry = annotations.get(str(item.get("proposal_id")))
        if entry:
            item.update(entry)
    advisor["summary"] = summary
    advisor["annotated"] = len(annotations)
    advisor["generated_at"] = now.isoformat(timespec="seconds")
    if settings["cloud"]:
        # 誠實旗標：cloud advisor 實際被使用時，envelope 不得再宣稱未用 cloud LLM。
        result["cloud_llm_used"] = True
    return result
