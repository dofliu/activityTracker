"""每一項驗收「去查什麼」（ADR-028，TODO D12）。

一個 reading 只做一件事：把本機找得到的便宜證據組成一份 :class:`Reading`。
**它不判定**——「查到什麼就算什麼」寫在 ``items.py`` 的階梯裡。

兩條規矩：

- ``evidence`` 的鍵、順序與值是**對外契約**（會出現在 API 與畫面上），一個都不准動。
- 功能關掉時**提早回傳**：別為了形式統一去做昂貴的收集。哪些項目會提早回傳，
  由階梯的第一列接住，與 D12 之前的 if/return 順序逐列相同。
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from sqlalchemy import func

from core.activity_patterns import collect_pattern_signals, pattern_settings
from core.agent_executor import executor_enabled, l2_enabled, l2_write_enabled
from core.coverage_ledger import get_daily_coverage
from core.docs_freshness import collect_docs_freshness_signals, docs_freshness_enabled
from core.manager import get_manager
from core.meeting_transcripts import (
    SOURCE_PREFIX, list_transcripts, meetings_enabled, parse_followups,
    provider_is_cloud, summary_provider,
)
from core.models import CalendarEvent, RAGChatMessage, RAGIndexJob, RAGIndexedFile, SecretaryNote
from core.repo_sync_report import load_snapshot
from core.secretary.memory import load_profile, priority_boost_value
from core.secretary.present import build_home
from core.weekly_review import collect_priority_drift_signals, review_enabled, review_period

from .rules import (
    COVERAGE_LOOKBACK_DAYS, RECEIPT_SCAN_LIMIT, Ctx, Reading,
    iso, latest_files, receipts, reports_dir, succeeded,
)

# 雲端 provider（A2 要的是「真的問到雲端」，本機 ollama 不算）。
CLOUD_PROVIDERS = ("openai", "gpt", "claude", "anthropic", "gemini", "google")
# gateway 失敗時仍會把錯誤字串存成 assistant message；這些不算成功回答。
LLM_ERROR_MARKERS = ("[LLMGateway", "[OpenAI API 錯誤]", "[Claude API 錯誤]",
                     "[Gemini API 錯誤]", "[Ollama", "【尚未偵測到")
# rag/jobs.py 的 ACTIVE_STATUSES。驗收只查 SQLite，不 import rag 套件；
# 兩邊一致由契約測試鎖住（tests/test_rag_storage_compaction.py）。
ACTIVE_JOB_STATUSES = ("queued", "running", "scanning", "indexing", "paused", "cancelling")


def a1_coverage(ctx: Ctx) -> Reading:
    """A1 的判準是「跨午夜連續運行**一整天**」，所以只有**已結束的日子**能算數。

    ``get_daily_coverage`` 對當天的分母是「今天到目前為止經過的時間」，不是 24
    小時——早上 03:00 觀測了 2.9 小時就會是 97%。拿那個當「全天 coverage」通過，
    正是本專案最該拒絕的假綠燈（TODO A1 也明寫收據要**隔日**查）。今天只當作
    進度顯示，永遠不能讓 A1 變綠。
    """
    days: list[dict[str, Any]] = []
    threshold = 0.0
    for offset in range(COVERAGE_LOOKBACK_DAYS):
        target = ctx.today - timedelta(days=offset)
        coverage = get_daily_coverage(target, database=ctx.database, cfg=ctx.cfg, now=ctx.now)
        threshold = coverage["full_coverage_ratio_threshold"]
        days.append({
            "date": coverage["date"],
            "coverage_ratio": coverage["coverage_ratio"],
            "meets_full_coverage": coverage["meets_full_coverage"],
            "observed_seconds": coverage["observed_seconds"],
            "elapsed_seconds": coverage["elapsed_seconds"],
            "interval_count": coverage["interval_count"],
            "ledger_available": coverage["ledger_available"],
            # 當天還沒過完：分母只到現在，因此不是「全天」的證據。
            "complete_day": offset > 0,
        })

    completed = [d for d in days if d["complete_day"]]
    today_row = next((d for d in days if not d["complete_day"]), None)
    met = [d for d in completed if d["meets_full_coverage"]]
    observed = [d for d in completed if d["ledger_available"]]
    best = max(completed, key=lambda d: d["coverage_ratio"]) if completed else None
    today_note = ""
    if today_row and today_row["coverage_ratio"] > 0:
        hours = today_row["elapsed_seconds"] / 3600
        today_note = (
            f"（今天目前 {today_row['coverage_ratio']:.1%}，但只涵蓋已過的 {hours:.1f} 小時，"
            "不算全天；等跨過午夜再看這一項）"
        )
    return Reading(
        {
            "days": days,
            "threshold": threshold,
            "best_completed_day": best,
            "completed_days_with_ledger": len(observed),
            "today_partial": today_row,
            "basis": "only_completed_days_count_today_denominator_is_elapsed_so_far",
        },
        {"met": met, "observed": observed, "best": best, "threshold": threshold,
         "today_note": today_note},
    )


def a2_cloud_provider(ctx: Ctx) -> Reading:
    rows = (
        ctx.session.query(RAGChatMessage)
        .filter(
            RAGChatMessage.role == "assistant",
            func.lower(RAGChatMessage.provider).in_(CLOUD_PROVIDERS),
        )
        .order_by(RAGChatMessage.created_at.desc(), RAGChatMessage.id.desc())
        .limit(RECEIPT_SCAN_LIMIT)
        .all()
    )
    answered: list[dict[str, Any]] = []
    errored: list[dict[str, Any]] = []
    for row in rows:
        content = (row.content or "").strip()
        record = {
            "provider": row.provider,
            "model": row.model,
            "created_at": iso(row.created_at),
            "chars": len(content),
        }
        (errored if not content or content.startswith(LLM_ERROR_MARKERS) else answered).append(record)
    return Reading({
        "cloud_replies": len(answered),
        "cloud_error_replies": len(errored),
        "latest_ok": answered[0] if answered else None,
        "latest_error": errored[0] if errored else None,
        "basis": "rag_chat_messages",
    })


def a3_telegram_inline(ctx: Ctx) -> Reading:
    from notifiers.telegram_approvals import telegram_approvals_enabled

    rows = receipts(ctx, approved_via="telegram_inline")
    return Reading({
        "approvals_enabled": telegram_approvals_enabled(ctx.cfg),
        "telegram_enabled": bool(ctx.cfg.get("notifiers.telegram.enabled", False)),
        "telegram_inline_receipts": len(rows),
        "succeeded": len(succeeded(rows)),
        "latest": rows[0] if rows else None,
    })


def a4_l2_executor(ctx: Ctx) -> Reading:
    draft = receipts(ctx, template_id="agent_draft_plan")
    apply_plan = receipts(ctx, template_id="agent_apply_plan")
    return Reading({
        "executor_enabled": bool(ctx.cfg.get("proactive_secretary.executor.enabled", False)),
        "l2_enabled": bool(ctx.cfg.get("proactive_secretary.executor.l2.enabled", False)),
        "allow_write": bool(ctx.cfg.get("proactive_secretary.executor.l2.allow_write", False)),
        "draft_receipts": len(draft),
        "draft_succeeded": len(succeeded(draft)),
        "apply_receipts": len(apply_plan),
        "latest_draft": draft[0] if draft else None,
    })


def a5_reconciliation(ctx: Ctx) -> Reading:
    # onboarding 的 init／attach／clone 目前不寫任何本機收據（見 docs/TODO.md B4），
    # 而且掃描對帳要跑 git 與讀 GitHub 快取——不在本模組的便宜查詢範圍。
    # 因此這項永遠由人眼確認，機器不猜。
    return Reading({"receipt_available": False,
                    "reason": "no_durable_receipt_for_onboarding_actions"})


def a6_retrieval_worker(ctx: Ctx) -> Reading:
    if not ctx.runtime:
        return Reading({"basis": "in_memory_process_state"}, {"runtime_only": True})
    from rag.retrieval_client import retrieval_client

    status = retrieval_client.status()
    if status.get("extra_installed") is False:
        return Reading(
            {"extra_missing": status.get("extra_missing"), "basis": "importlib.find_spec"},
            {
                "extra_absent": True,
                "missing_text": ", ".join(status.get("extra_missing") or []),
                "install_hint": str(status.get("install_hint") or 'pip install "omnicontext[rag]"'),
            },
        )
    warmup = status.get("warmup") or {}
    evidence = {
        "mode": status.get("mode"),
        "state": status.get("state"),
        "index_present": status.get("index_present"),
        "bm25_chunks": warmup.get("bm25_chunks"),
        "vector_chunks": warmup.get("vector_chunks"),
        "warmup_at": status.get("warmup_at"),
        "requests_served": status.get("requests_served"),
        "last_retrieval_ms": status.get("last_retrieval_ms"),
        "last_error": status.get("last_error"),
    }
    chunks = (evidence["bm25_chunks"] or 0) + (evidence["vector_chunks"] or 0)
    # 索引可能在預熱之後才重建；worker 記憶體裡的收據不會自己更新。只讀 SQLite 的
    # 來源計數（不載入任何索引套件）就能看出「載入的是舊索引」——那不該判綠
    # （2026-09-07 實機：索引 4839 chunk，收據仍是 3，A6 卻是 passed）。
    source_chunks = None
    try:
        source_chunks = int(
            ctx.session.query(func.coalesce(func.sum(RAGIndexedFile.chunk_count), 0)).scalar() or 0
        )
        evidence["source_chunks"] = source_chunks
    except Exception as exc:  # noqa: BLE001 — 讀不到來源計數就退回原本的判定
        evidence["source_chunks_error"] = type(exc).__name__
    return Reading(evidence, {"worker": status, "chunks": chunks, "source_chunks": source_chunks})


def a7_repo_sync(ctx: Ctx) -> Reading:
    snapshot = load_snapshot(ctx.cfg)
    reports = latest_files(reports_dir(ctx.cfg) / "repo_sync", "RepoSync_*.md")
    report_receipts = receipts(ctx, template_id="repo_sync_report")
    pull_ok = succeeded(receipts(ctx, template_id="repo_pull_ff"))
    return Reading(
        {
            "snapshot_available": snapshot is not None,
            "snapshot_generated_at": (snapshot or {}).get("generated_at"),
            "snapshot_repositories": len((snapshot or {}).get("repositories", [])),
            "reports": reports,
            "repo_sync_report_receipts": len(report_receipts),
            "repo_pull_ff_succeeded": len(pull_ok),
        },
        # 注意 evidence 記的是 `is not None`，但 partial 那條判的是 dict 的真假值——
        # 兩者在「空字典」時不同。這裡跟著判準走，不跟著 evidence 走。
        {"reports_count": reports["count"], "pull_ok": len(pull_ok),
         "report_receipts": len(report_receipts), "snapshot": bool(snapshot)},
    )


def a8_daily_packs(ctx: Ctx) -> Reading:
    morning = receipts(ctx, template_id="morning_pack")
    handoff = receipts(ctx, template_id="handoff_active_projects")
    morning_ok = succeeded(morning)
    handoff_ok = succeeded(handoff)
    return Reading(
        {
            "morning_pack_succeeded": len(morning_ok),
            "handoff_active_projects_succeeded": len(handoff_ok),
            # 注意：這是最近一筆**收據**，不是最近一筆成功的收據——敘述用的是後者。
            "latest_morning_pack": morning[0] if morning else None,
            "handoff_files": latest_files(reports_dir(ctx.cfg) / "handoffs", "*.md"),
        },
        {"both": bool(morning_ok and handoff_ok), "any": bool(morning or handoff),
         "latest_success_at": morning_ok[0]["requested_at"] if morning_ok else None},
    )


def a9_memory_area(ctx: Ctx) -> Reading:
    counts = {
        kind: int(count)
        for kind, count in ctx.session.query(SecretaryNote.kind, func.count(SecretaryNote.id))
        .group_by(SecretaryNote.kind)
        .all()
    }
    evidence = {
        "counts": counts,
        "user_note": counts.get("user_note", 0),
        "preference": counts.get("preference", 0),
        "observation": counts.get("observation", 0),
        "basis": "secretary_notes",
    }
    kinds = ("user_note", "preference", "observation")
    have = [k for k in kinds if evidence[k] > 0]
    return Reading(evidence, {"have": have, "complete": len(have) == 3,
                              "missing": [k for k in kinds if evidence[k] == 0]})


def a10_telegram_chat(ctx: Ctx) -> Reading:
    from notifiers.telegram_chat import telegram_chat_enabled

    telegram_notes = (
        ctx.session.query(func.count(SecretaryNote.id))
        .filter(SecretaryNote.source == "telegram")
        .scalar()
    ) or 0
    return Reading({
        "chat_enabled": telegram_chat_enabled(ctx.cfg),
        "remote_arm_enabled": bool(ctx.cfg.get("notifiers.telegram.chat.allow_remote_arm", False)),
        "notes_from_telegram": int(telegram_notes),
        "basis": "secretary_notes.source=telegram",
    })


def a11_line_push(ctx: Ctx) -> Reading:
    from notifiers.channels import channels_status

    status = channels_status(ctx.cfg)
    line = status.get("channels", {}).get("line", {})
    evidence = {
        "push_ready": status.get("push_ready", []),
        "line_enabled": line.get("enabled"),
        "token_configured": line.get("token_configured"),
        "to_configured": line.get("to_configured"),
    }
    return Reading(evidence, {"ready": "line" in evidence["push_ready"]})


def a12_greeting_card(ctx: Ctx) -> Reading:
    # 判準是「卡上每個數字都能在別的分頁對得上」——這是人眼比對，機器不代勞。
    return Reading({
        "display_name_set": bool(str(ctx.cfg.get("proactive_secretary.greeting.display_name", "") or "").strip()),
        "in_morning_briefing": bool(ctx.cfg.get("proactive_secretary.greeting.in_morning_briefing", True)),
        "llm_polish_enabled": bool(ctx.cfg.get("proactive_secretary.greeting.llm.enabled", False)),
    })


def a13_calendar(ctx: Ctx) -> Reading:
    paths = list(ctx.cfg.get("watchers.calendar_watcher.paths", []) or [])
    total = int(ctx.session.query(func.count(CalendarEvent.id)).scalar() or 0)
    sources = int(
        ctx.session.query(func.count(func.distinct(CalendarEvent.source_path))).scalar() or 0
    )
    last_seen = ctx.session.query(func.max(CalendarEvent.last_seen_at)).scalar()
    evidence = {
        "enabled": bool(ctx.cfg.get("watchers.calendar_watcher.enabled", True)),
        "configured_paths": len(paths),
        "events": total,
        "source_files": sources,
        "last_seen_at": iso(last_seen),
        "basis": "calendar_events",
    }
    if paths and ctx.runtime:
        # 只有真的設了路徑才去問採集器診斷；沒設路徑不值得為此碰 manager。
        diagnostics = (
            get_manager().get_status().get("collector_diagnostics", {}).get("calendar_watcher", {})
        )
        evidence["degraded_sources"] = diagnostics.get("degraded_sources", [])
        evidence["degraded_sources_count"] = diagnostics.get("degraded_sources_count", 0)
    return Reading(evidence, {"paths": len(paths)})


def a14_sync_center_pull(ctx: Ctx) -> Reading:
    # 判準是「按下去會不會動、理由對不對」，那要跑 git 也要人眼看，兩者都不在
    # 本模組範圍（D1）。這裡只回報同步報告快照裡有多少 repo 落後遠端當作旁證。
    snapshot = load_snapshot(ctx.cfg) or {}
    repositories = snapshot.get("repositories", [])
    return Reading({
        "snapshot_available": bool(snapshot),
        "repositories_in_snapshot": len(repositories),
        "behind_in_snapshot": len([r for r in repositories if r.get("sync_state") == "behind"]),
        "basis": "reports/repo_sync/latest.json",
    })


def a15_daily_digest(ctx: Ctx) -> Reading:
    digests = (
        ctx.session.query(SecretaryNote)
        .filter(SecretaryNote.source == "daily_digest")
        .order_by(SecretaryNote.created_at.desc())
        .limit(RECEIPT_SCAN_LIMIT)
        .all()
    )
    days = sorted({(n.source_ref or "").split(":")[1] for n in digests if ":" in (n.source_ref or "")})
    return Reading(
        {
            "digest_notes": len(digests),
            "days_covered": days[-7:],
            "day_count": len(days),
            "enabled": bool(ctx.cfg.get("proactive_secretary.daily_digest.enabled", True)),
            "basis": "secretary_notes.source=daily_digest",
        },
        # days[0] 在超過 7 天時不在 days_covered 裡，所以敘述用的是這份完整清單。
        {"days": days},
    )


def a16_pattern_proposals(ctx: Ctx) -> Reading:
    """模式提案的判準是「N 對得上你的印象、X 確實是你放下的」——那是人眼；機器只回報
    模式層現在算出什麼，讓你有東西可以對。"""
    settings = pattern_settings(ctx.cfg)
    evidence: dict[str, Any] = {
        "enabled": settings["enabled"],
        "basis": "activity_patterns.collect_pattern_signals",
    }
    if not settings["enabled"]:
        return Reading(evidence)
    signals, meta = collect_pattern_signals(database=ctx.database, cfg=ctx.cfg, now=ctx.now)
    evidence.update({
        "recent_active_days": meta.get("recent_active_days"),
        "recent_active_by_project": meta.get("recent_active_by_project", {}),
        "routine_schedules": meta.get("routine_schedules", []),
        "pattern_signals": [
            {"type": s["signal_type"], "project": s["project_key"], "title": s["title"]} for s in signals
        ],
    })
    return Reading(evidence, {"signals": len(signals), "active_days": meta.get("recent_active_days")})


def a17_declared_profile(ctx: Ctx) -> Reading:
    """宣告式個人檔案（ADR-018）：機器只回報你現在宣告了什麼、加分值多少；「提案排序與問候
    語氣是否如你所想」是人眼。沒宣告就是 pending——這是你還沒寫，不是壞掉。"""
    profile = load_profile(database=ctx.database)
    evidence: dict[str, Any] = {
        "basis": "secretary_profile.load_profile（只讀 secretary_notes.kind=preference）",
        "priorities": profile["priorities"],
        "tone": profile["tone"],
        "tone_declared": profile["tone_declared"],
        "ignored_directives": profile["ignored"],
        "priority_boost": priority_boost_value(ctx.cfg),
    }
    parts = []
    if profile["priorities"]:
        parts.append("本期優先：" + "、".join(profile["priorities"]))
    if profile["tone_declared"]:
        # tone_label 刻意不進 evidence（對外只給 tone），所以留在 facts 給敘述用。
        parts.append(f"語氣：{profile['tone_label']}")
    return Reading(evidence, {"declared": profile["declared"], "parts": parts})


def a18_secretary_home(ctx: Ctx) -> Reading:
    """01 首頁（ADR-019）：機器只能回報桌面現在挑出什麼、哪一節壞了；「焦點與記得是不是你會挑的、
    一天離開首頁幾次有沒有變少」是人眼——次數只在你的瀏覽器裡。"""
    home = build_home(database=ctx.database, cfg=ctx.cfg, now=ctx.now)
    focus = (home.get("focus") or {}).get("proposal") or {}
    pick = home.get("memory_pick") or {}
    sections = home.get("sections") or {}
    errors = {k: v for k, v in sections.items() if str(v).startswith("error")}
    evidence: dict[str, Any] = {
        "basis": "secretary_home.build_home（唯讀重排既有資料）",
        "sections": sections,
        "focus": {"type": focus.get("proposal_type"), "project": focus.get("project_key"), "title": focus.get("title")} if focus else None,
        "memory_pick_rule": pick.get("rule"),
        "details": home.get("details") or {},
    }
    return Reading(evidence, {
        "errors": errors,
        "focus_title": focus.get("title") if focus else None,
        "has_focus": bool(focus),
        "has_note": bool(pick.get("note")),
        "why_this": pick.get("why_this"),
    })


def a19_weekly_review(ctx: Ctx) -> Reading:
    """說的 vs 做的（ADR-020）：機器只回報寫了幾週的回顧、上一個完整週的宣告與偏移現在算出什麼；
    「天數與你的印象相符、偏移提案合理」是人眼。"""
    if not review_enabled(ctx.cfg):
        return Reading({"enabled": False}, {"disabled": True})
    start, end, label = review_period(ctx.now, 1)
    with ctx.database.session_scope() as session:
        rows = (
            session.query(SecretaryNote.source_ref, SecretaryNote.created_at)
            .filter(SecretaryNote.kind == "observation", SecretaryNote.source == "weekly_review")
            .order_by(SecretaryNote.created_at.desc())
            .limit(8)
            .all()
        )
    signals, meta = collect_priority_drift_signals(database=ctx.database, cfg=ctx.cfg, now=ctx.now)
    evidence: dict[str, Any] = {
        "basis": "weekly_review.collect_priority_drift_signals（活動矩陣 × 偏好筆記，即時計算）",
        "last_complete_week": {"label": label, "start": start.isoformat(), "end": end.isoformat()},
        "reviews_written": [str(ref) for ref, _ in rows],
        "declared": meta.get("declared"),
        "aligned": meta.get("aligned"),
        "drift_signals": [item["title"] for item in signals],
        "reason": meta.get("reason"),
    }
    if meta.get("reason") == "no_priorities":
        said = "你還沒宣告本期優先，所以只有活動天數、沒有「說的 vs 做的」"
    elif signals:
        said = f"上一個完整週有 {len(signals)} 項宣告優先「說了沒做」"
    elif meta.get("aligned") is True:
        said = "上一個完整週宣告的優先都有在做"
    else:
        said = "上一個完整週活動太少，不好比"
    return Reading(evidence, {"weeks": len(rows), "latest": rows[0][0] if rows else None,
                              "said": said})


def a20_docs_freshness(ctx: Ctx) -> Reading:
    """ADR-021：機器能回報「哪些 repo 的文件落後幾個 commit」與 L2 三道門的狀態；
    「起草的計畫值不值得批准、改出來的文件對不對」是人眼，且改動要由你 commit。"""
    if not docs_freshness_enabled(ctx.cfg):
        return Reading({"enabled": False}, {"disabled": True})
    signals, meta = collect_docs_freshness_signals(database=ctx.database, cfg=ctx.cfg, now=ctx.now)
    executor = executor_enabled(ctx.cfg)
    l2 = l2_enabled(ctx.cfg)
    l2_write = l2_write_enabled(ctx.cfg)
    evidence: dict[str, Any] = {
        "basis": "docs_freshness.collect_docs_freshness_signals（file_activity_events × git_activity_events）",
        "repos_considered": meta.get("repos_considered", {}),
        "skipped_no_doc_baseline": meta.get("skipped_no_doc_baseline", []),
        "proposals": [item["title"] for item in signals],
        "executor_enabled": executor,
        "l2_enabled": l2,
        "l2_write_enabled": l2_write,
    }
    return Reading(evidence, {
        "signals": len(signals),
        "repos": len(meta.get("repos_considered") or {}),
        "any_repo": bool(meta.get("repos_considered")),
        "gates_open": bool(executor and l2 and l2_write),
        "min_commits": meta.get("min_commits"),
        "min_days": meta.get("min_days"),
        "gates": (executor, l2, l2_write),
    })


def a21_chroma_compaction(ctx: Ctx) -> Reading:
    """ADR-009 Addendum C：Chroma 的 delete_collection 只做邏輯刪除，磁碟不會變小。
    這一項只認 worker 的回收收據——沒跑過就說沒跑過，不去猜目錄現在多大。"""
    running = (
        ctx.session.query(RAGIndexJob.status, RAGIndexJob.requested_at)
        .filter(
            RAGIndexJob.job_type == "compact_chroma",
            RAGIndexJob.status.in_(ACTIVE_JOB_STATUSES),
        )
        .order_by(RAGIndexJob.requested_at.desc())
        .first()
    )
    if running is not None:
        # 還在跑不是「沒有完成」。4 GB 級的目錄光 VACUUM 就要一兩分鐘，
        # 這時候叫使用者「再跑一次」是指錯方向——而且同時只能有一個索引工作，
        # 真的再按也會被拒（2026-09-08 實機遇到）。
        return Reading(
            {"job_status": running[0], "requested_at": iso(running[1]), "receipt_available": False},
            {"running": running[0]},
        )
    row = (
        ctx.session.query(RAGIndexJob.status, RAGIndexJob.result_json, RAGIndexJob.completed_at)
        .filter(
            RAGIndexJob.job_type == "compact_chroma",
            RAGIndexJob.status.notin_(ACTIVE_JOB_STATUSES),
        )
        .order_by(RAGIndexJob.completed_at.desc(), RAGIndexJob.requested_at.desc())
        .first()
    )
    if row is None:
        return Reading({"receipt_available": False}, {"never_ran": True})
    status, result_json, completed_at = row
    try:
        result = json.loads(result_json) if result_json else {}
    except (TypeError, ValueError):
        result = {}
    evidence = {
        "job_status": status,
        "completed_at": iso(completed_at),
        "reclaimed_bytes": result.get("reclaimed_bytes"),
        "before_bytes": result.get("before_bytes"),
        "after_bytes": result.get("after_bytes"),
        "removed_dirs": len(result.get("removed_dirs") or []),
        "failed_dirs": result.get("failed_dirs") or [],
        "vacuum": result.get("vacuum"),
        "still_reclaimable_bytes": (result.get("after") or {}).get("reclaimable_bytes"),
    }
    return Reading(evidence, {
        "incomplete": status != "completed" or not result,
        "job_status": status,
        "reclaimed": int(evidence["reclaimed_bytes"] or 0),
    })


def a22_meeting_secretary(ctx: Ctx) -> Reading:
    """ADR-022 第一層：機器能回報「資料夾設了嗎、整理過幾份、還有幾條候選待辦沒處理」；
    「摘要有沒有編造、配對對不對」是人眼。"""
    if not meetings_enabled(ctx.cfg):
        return Reading({"transcript_dir": None}, {"disabled": True})
    provider = summary_provider(ctx.cfg)
    files, meta = list_transcripts(cfg=ctx.cfg, now=ctx.now, limit=50)
    notes = (
        ctx.session.query(SecretaryNote.id, SecretaryNote.body)
        .filter(SecretaryNote.kind == "observation", SecretaryNote.source_ref.like(f"{SOURCE_PREFIX}%"))
        .all()
    )
    pending = 0
    accepted = 0
    for _note_id, body in notes:
        for item in parse_followups(body or ""):
            if item["status"] == "pending":
                pending += 1
            elif item["status"] == "accepted":
                accepted += 1
    return Reading(
        {
            "transcript_dir": meta.get("path"),
            "transcripts_found": len(files),
            "meeting_notes": len(notes),
            "followups_pending": pending,
            "followups_accepted": accepted,
            "provider": provider,
            "provider_is_cloud": provider_is_cloud(provider),
            "degraded_sources": meta.get("degraded_sources", []),
        },
        {"files": len(files), "notes": len(notes), "path": meta.get("path")},
    )
