"""驗收中心（Acceptance Center）：把 docs/TODO.md A 段的「完成判準」機器化。

本專案不把「contract tests 通過」當成「實機可用」，因此 TODO A 段列了一批
只能在使用者自己機器上取得的收據。這個模組做的事只有一件：**去本機找那些
收據到底在不在**，把「做過沒」從記憶與人工翻頁變成可重跑的查詢。

claim boundary（很重要，這是本模組唯一會被誤讀的地方）:

- 只讀。不執行任何驗收動作、不代替使用者操作、不寫任何資料表。
- 只查便宜的本機證據：SQLite 查詢、設定值、檔案是否存在。**不跑 git、
  不連網、不呼叫 LLM、不載入索引**——驗收中心自己不該變成一個負擔。
- ``passed`` 只代表「找到符合該項判準的本機收據」，不代表功能在所有情境
  下正確，也不代表覆蓋率。判準需要人眼比對的項目（例如「卡上每個數字都
  對得上」）永遠停在 ``needs_human``，不會因為查得到旁證就自動變綠。
- 程序內記憶體狀態（檢索 worker 的預熱狀態）只有在**服務執行中的那個程序**
  裡才看得到；CLI 另開程序查不到，一律回 ``runtime_only`` 而不是 ``pending``
  ——查不到不等於沒發生。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import func

from core.config import get_config
from core.coverage_ledger import get_daily_coverage
from core.database import get_db
from core.models import (
    AgentExecutionReceipt,
    CalendarEvent,
    RAGChatMessage,
    SecretaryNote,
)
from core.runtime_paths import resolve_runtime_path, source_checkout_root
from core.time_utils import get_local_now


ACCEPTANCE_CLAIM_BOUNDARY = (
    "只讀本機已存在的收據；不執行驗收動作、不寫資料、不跑 git、不連網。"
    "passed 代表找到符合判準的收據，不代表功能在所有情境下正確；"
    "needs_human 的項目一律由你親眼確認，機器不會自動判定；"
    "attested 是你自己署名的確認，不是機器證據，也永遠不會覆蓋機器判定。"
)

# ---- 狀態字彙 -------------------------------------------------------------

PASSED = "passed"              # 找到符合判準的收據
PARTIAL = "partial"            # 判準有多項，只有一部分找得到收據
PENDING = "pending"            # 前置齊備但還沒有任何收據
NEEDS_HUMAN = "needs_human"    # 只能由人眼確認；機器最多提供旁證
NOT_CONFIGURED = "not_configured"  # 前置未設定或功能預設關閉——不是失敗
RUNTIME_ONLY = "runtime_only"  # 只有服務執行中的程序看得到（CLI 查不到）
ATTESTED = "attested"          # 機器沒有判準可查，由使用者親眼確認並留下署名收據

# 這幾個狀態代表「還沒拿到收據」，用於彙總與 release gate 判斷。
_OUTSTANDING = {PARTIAL, PENDING, NEEDS_HUMAN, RUNTIME_ONLY}
# 完成的兩種形態：機器找到收據，或人親眼確認並署名——兩者永遠分開記帳。
_SETTLED = {PASSED, ATTESTED}

COVERAGE_LOOKBACK_DAYS = 8
RECEIPT_SCAN_LIMIT = 500

# 雲端 provider（A2 要的是「真的問到雲端」，本機 ollama 不算）。
_CLOUD_PROVIDERS = ("openai", "gpt", "claude", "anthropic", "gemini", "google")
# gateway 失敗時仍會把錯誤字串存成 assistant message；這些不算成功回答。
_LLM_ERROR_MARKERS = ("[LLMGateway", "[OpenAI API 錯誤]", "[Claude API 錯誤]",
                      "[Gemini API 錯誤]", "[Ollama", "【尚未偵測到")


@dataclass
class _Ctx:
    database: Any
    cfg: Any
    now: datetime
    runtime: bool
    session: Any

    @property
    def today(self) -> date:
        return self.now.date()


def _reports_dir(cfg: Any) -> Path:
    return resolve_runtime_path(cfg.get("exporters.reports_dir", "reports"))


def _receipts(ctx: _Ctx, **filters: Any) -> list[dict[str, Any]]:
    """符合條件的 audit receipt（各種 status 都回，最新在前，只取非敏感欄位）。"""
    query = ctx.session.query(AgentExecutionReceipt)
    for column, value in filters.items():
        attr = getattr(AgentExecutionReceipt, column)
        query = query.filter(attr.in_(value) if isinstance(value, (list, tuple)) else attr == value)
    rows = (
        query.order_by(AgentExecutionReceipt.requested_at.desc(), AgentExecutionReceipt.id.desc())
        .limit(RECEIPT_SCAN_LIMIT)
        .all()
    )
    return [
        {
            "id": row.id,
            "template_id": row.template_id,
            "status": row.status,
            "approved_via": row.approved_via,
            "requested_at": row.requested_at.isoformat(timespec="seconds") if row.requested_at else None,
            "error_code": row.error_code,
        }
        for row in rows
    ]


def _latest_files(directory: Path, pattern: str, limit: int = 3) -> dict[str, Any]:
    """目錄內符合 pattern 的檔案概況；目錄不存在不是錯誤，只是還沒產生過。"""
    if not directory.is_dir():
        return {"dir": str(directory), "exists": False, "count": 0, "latest": []}
    files = sorted(
        (p for p in directory.glob(pattern) if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return {
        "dir": str(directory),
        "exists": True,
        "count": len(files),
        "latest": [
            {
                "name": p.name,
                "modified_at": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
            }
            for p in files[:limit]
        ],
    }


# ---- A1 全天 coverage ledger ---------------------------------------------


def _check_a1(ctx: _Ctx) -> dict[str, Any]:
    days: list[dict[str, Any]] = []
    threshold = 0.0
    for offset in range(COVERAGE_LOOKBACK_DAYS):
        target = ctx.today - timedelta(days=offset)
        coverage = get_daily_coverage(
            target, database=ctx.database, cfg=ctx.cfg, now=ctx.now
        )
        threshold = coverage["full_coverage_ratio_threshold"]
        days.append(
            {
                "date": coverage["date"],
                "coverage_ratio": coverage["coverage_ratio"],
                "meets_full_coverage": coverage["meets_full_coverage"],
                "observed_seconds": coverage["observed_seconds"],
                "interval_count": coverage["interval_count"],
                "ledger_available": coverage["ledger_available"],
            }
        )

    met = [d for d in days if d["meets_full_coverage"]]
    observed = [d for d in days if d["ledger_available"]]
    best = max(days, key=lambda d: d["coverage_ratio"]) if days else None
    evidence = {
        "days": days,
        "threshold": threshold,
        "best_day": best,
        "days_with_ledger": len(observed),
    }
    if met:
        return {
            "status": PASSED,
            "detail": f"{met[0]['date']} 的 ledger coverage 達門檻（{met[0]['coverage_ratio']:.2%}）。",
            "evidence": evidence,
        }
    if not observed:
        return {
            "status": PENDING,
            "detail": "近 8 天沒有任何 coverage interval——服務尚未在這台機器連續運行過。",
            "evidence": evidence,
        }
    return {
        "status": PENDING,
        "detail": (
            f"最好的一天是 {best['date']}（{best['coverage_ratio']:.2%}），"
            f"還沒達到 {evidence['threshold']:.0%} 門檻。"
        ),
        "evidence": evidence,
    }


# ---- A2 RAG 雲端 provider 複測 -------------------------------------------


def _check_a2(ctx: _Ctx) -> dict[str, Any]:
    rows = (
        ctx.session.query(RAGChatMessage)
        .filter(
            RAGChatMessage.role == "assistant",
            func.lower(RAGChatMessage.provider).in_(_CLOUD_PROVIDERS),
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
            "created_at": row.created_at.isoformat(timespec="seconds") if row.created_at else None,
            "chars": len(content),
        }
        if not content or content.startswith(_LLM_ERROR_MARKERS):
            errored.append(record)
        else:
            answered.append(record)

    evidence = {
        "cloud_replies": len(answered),
        "cloud_error_replies": len(errored),
        "latest_ok": answered[0] if answered else None,
        "latest_error": errored[0] if errored else None,
        "basis": "rag_chat_messages",
    }
    if answered:
        return {
            "status": PASSED,
            "detail": f"最近一次雲端回答：{answered[0]['provider']}（{answered[0]['created_at']}）。",
            "evidence": evidence,
        }
    if errored:
        return {
            "status": PARTIAL,
            "detail": "有雲端對話紀錄，但存下來的都是錯誤訊息——金鑰、網路或逾時三者之一。",
            "evidence": evidence,
        }
    return {
        "status": PENDING,
        "detail": "還沒有任何以雲端 provider 產生的回答紀錄。",
        "evidence": evidence,
    }


# ---- A3 Telegram inline 批准 ---------------------------------------------


def _check_a3(ctx: _Ctx) -> dict[str, Any]:
    from notifiers.telegram_approvals import telegram_approvals_enabled

    receipts = _receipts(ctx, approved_via="telegram_inline")
    ok = [r for r in receipts if r["status"] == "succeeded"]
    evidence = {
        "approvals_enabled": telegram_approvals_enabled(ctx.cfg),
        "telegram_enabled": bool(ctx.cfg.get("notifiers.telegram.enabled", False)),
        "telegram_inline_receipts": len(receipts),
        "succeeded": len(ok),
        "latest": receipts[0] if receipts else None,
    }
    if ok:
        return {
            "status": PASSED,
            "detail": f"已有 {len(ok)} 筆 approved_via=telegram_inline 的成功收據。",
            "evidence": evidence,
        }
    if receipts:
        return {
            "status": PARTIAL,
            "detail": "有 telegram_inline 收據但沒有成功的；看收據的 error_code。",
            "evidence": evidence,
        }
    if not evidence["approvals_enabled"]:
        return {
            "status": NOT_CONFIGURED,
            "detail": "inline 批准預設關閉（需執行器與批准通道兩個開關都開）。",
            "evidence": evidence,
        }
    return {"status": PENDING, "detail": "批准通道已開，還沒批過任何一筆。", "evidence": evidence}


# ---- A4 L2 執行器 ---------------------------------------------------------


def _check_a4(ctx: _Ctx) -> dict[str, Any]:
    draft = _receipts(ctx, template_id="agent_draft_plan")
    apply_plan = _receipts(ctx, template_id="agent_apply_plan")
    draft_ok = [r for r in draft if r["status"] == "succeeded"]
    evidence = {
        "executor_enabled": bool(ctx.cfg.get("proactive_secretary.executor.enabled", False)),
        "l2_enabled": bool(ctx.cfg.get("proactive_secretary.executor.l2.enabled", False)),
        "allow_write": bool(ctx.cfg.get("proactive_secretary.executor.l2.allow_write", False)),
        "draft_receipts": len(draft),
        "draft_succeeded": len(draft_ok),
        "apply_receipts": len(apply_plan),
        "latest_draft": draft[0] if draft else None,
    }
    if draft_ok:
        return {
            "status": PASSED,
            "detail": f"agent_draft_plan 已有 {len(draft_ok)} 筆成功收據。",
            "evidence": evidence,
        }
    if draft:
        return {
            "status": PARTIAL,
            "detail": "有 draft 收據但都不是 succeeded；看 error_code。",
            "evidence": evidence,
        }
    if not (evidence["executor_enabled"] and evidence["l2_enabled"]):
        return {
            "status": NOT_CONFIGURED,
            "detail": "L2 預設關閉（executor 與 l2 兩個開關都要開）。",
            "evidence": evidence,
        }
    return {"status": PENDING, "detail": "L2 已開，還沒跑過 draft。", "evidence": evidence}


# ---- A5 P4.3 對帳實操 -----------------------------------------------------


def _check_a5(ctx: _Ctx) -> dict[str, Any]:
    # onboarding 的 init／attach／clone 目前不寫任何本機收據（見 docs/TODO.md B4），
    # 而且掃描對帳要跑 git 與讀 GitHub 快取——不在本模組的便宜查詢範圍。
    # 因此這項永遠由人眼確認，機器不猜。
    return {
        "status": NEEDS_HUMAN,
        "detail": "onboarding 動作不留本機收據（TODO B4），對帳掃描也不在唯讀便宜查詢範圍內。",
        "evidence": {"receipt_available": False, "reason": "no_durable_receipt_for_onboarding_actions"},
    }


# ---- A6 檢索 worker -------------------------------------------------------


def _check_a6(ctx: _Ctx) -> dict[str, Any]:
    if not ctx.runtime:
        return {
            "status": RUNTIME_ONLY,
            "detail": "worker 狀態是服務程序內的記憶體狀態；請在儀表板的驗收中心看這一項。",
            "evidence": {"basis": "in_memory_process_state"},
        }
    from rag.retrieval_client import retrieval_client

    status = retrieval_client.status()
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
    if status.get("state") == "ready" and chunks > 0:
        return {
            "status": PASSED,
            "detail": f"worker 就緒，已載入 bm25={evidence['bm25_chunks']}／vector={evidence['vector_chunks']}。",
            "evidence": evidence,
        }
    if not status.get("index_present"):
        return {
            "status": NOT_CONFIGURED,
            "detail": "本機還沒有索引，沒有可預熱的東西。",
            "evidence": evidence,
        }
    if status.get("last_error"):
        return {
            "status": PARTIAL,
            "detail": f"預熱留下錯誤：{status['last_error']}",
            "evidence": evidence,
        }
    return {
        "status": PENDING,
        "detail": f"worker 目前是 {status.get('state')}；預熱完成後這裡會顯示載入計數。",
        "evidence": evidence,
    }


# ---- A7 Repo 同步全覽與批次 -----------------------------------------------


def _check_a7(ctx: _Ctx) -> dict[str, Any]:
    from core.repo_sync_report import load_snapshot

    snapshot = load_snapshot(ctx.cfg)
    reports = _latest_files(_reports_dir(ctx.cfg) / "repo_sync", "RepoSync_*.md")
    report_receipts = _receipts(ctx, template_id="repo_sync_report")
    pull_receipts = _receipts(ctx, template_id="repo_pull_ff")
    pull_ok = [r for r in pull_receipts if r["status"] == "succeeded"]
    evidence = {
        "snapshot_available": snapshot is not None,
        "snapshot_generated_at": (snapshot or {}).get("generated_at"),
        "snapshot_repositories": len((snapshot or {}).get("repositories", [])),
        "reports": reports,
        "repo_sync_report_receipts": len(report_receipts),
        "repo_pull_ff_succeeded": len(pull_ok),
    }
    if reports["count"] and pull_ok:
        return {
            "status": PASSED,
            "detail": f"已有 {reports['count']} 份同步報告，且 repo_pull_ff 有 {len(pull_ok)} 筆成功收據。",
            "evidence": evidence,
        }
    if reports["count"] or report_receipts or snapshot:
        return {
            "status": PARTIAL,
            "detail": "同步報告已產生，但還沒有批准後的 repo_pull_ff 成功收據。",
            "evidence": evidence,
        }
    return {
        "status": PENDING,
        "detail": "還沒跑過 repo_sync_report，也沒有同步快照。",
        "evidence": evidence,
    }


# ---- A8 小秘書每日包 ------------------------------------------------------


def _check_a8(ctx: _Ctx) -> dict[str, Any]:
    morning = _receipts(ctx, template_id="morning_pack")
    handoff = _receipts(ctx, template_id="handoff_active_projects")
    morning_ok = [r for r in morning if r["status"] == "succeeded"]
    handoff_ok = [r for r in handoff if r["status"] == "succeeded"]
    handoffs = _latest_files(_reports_dir(ctx.cfg) / "handoffs", "*.md")
    evidence = {
        "morning_pack_succeeded": len(morning_ok),
        "handoff_active_projects_succeeded": len(handoff_ok),
        "latest_morning_pack": morning[0] if morning else None,
        "handoff_files": handoffs,
    }
    if morning_ok and handoff_ok:
        return {
            "status": PASSED,
            "detail": f"morning_pack 與 handoff_active_projects 都有成功收據（最近一次 {morning_ok[0]['requested_at']}）。",
            "evidence": evidence,
        }
    if morning or handoff:
        return {
            "status": PARTIAL,
            "detail": "兩個 L0 動作只跑成功了一個；缺的那個看收據 errors。",
            "evidence": evidence,
        }
    return {"status": PENDING, "detail": "還沒建立或執行過每日排程。", "evidence": evidence}


# ---- A9 小秘書記憶區 ------------------------------------------------------


def _check_a9(ctx: _Ctx) -> dict[str, Any]:
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
    have = [k for k in ("user_note", "preference", "observation") if evidence[k] > 0]
    if len(have) == 3:
        return {
            "status": PASSED,
            "detail": "筆記、偏好與秘書觀察三種都存在——一輪記憶區流程走完了。",
            "evidence": evidence,
        }
    if have:
        missing = [k for k in ("user_note", "preference", "observation") if evidence[k] == 0]
        return {
            "status": PARTIAL,
            "detail": f"還缺：{'、'.join(missing)}（observation 由早晨包這類 L0 收據產生）。",
            "evidence": evidence,
        }
    return {"status": PENDING, "detail": "記憶區還是空的。", "evidence": evidence}


# ---- A10 手機 Telegram 對話 -----------------------------------------------


def _check_a10(ctx: _Ctx) -> dict[str, Any]:
    from notifiers.telegram_chat import telegram_chat_enabled

    telegram_notes = (
        ctx.session.query(func.count(SecretaryNote.id))
        .filter(SecretaryNote.source == "telegram")
        .scalar()
    ) or 0
    evidence = {
        "chat_enabled": telegram_chat_enabled(ctx.cfg),
        "remote_arm_enabled": bool(ctx.cfg.get("notifiers.telegram.chat.allow_remote_arm", False)),
        "notes_from_telegram": int(telegram_notes),
        "basis": "secretary_notes.source=telegram",
    }
    if telegram_notes:
        return {
            "status": PASSED,
            "detail": f"記憶區有 {int(telegram_notes)} 筆來自 Telegram 的筆記——手機那條管線真的通了。",
            "evidence": evidence,
        }
    if not evidence["chat_enabled"]:
        return {
            "status": NOT_CONFIGURED,
            "detail": "Telegram 對話預設關閉（通知與對話兩個開關都要開）。",
            "evidence": evidence,
        }
    return {
        "status": PENDING,
        "detail": "對話已開啟，但還沒有從手機寫進來的筆記。",
        "evidence": evidence,
    }


# ---- A11 LINE 推播 --------------------------------------------------------


def _check_a11(ctx: _Ctx) -> dict[str, Any]:
    from notifiers.channels import channels_status

    status = channels_status(ctx.cfg)
    line = status.get("channels", {}).get("line", {})
    evidence = {
        "push_ready": status.get("push_ready", []),
        "line_enabled": line.get("enabled"),
        "token_configured": line.get("token_configured"),
        "to_configured": line.get("to_configured"),
    }
    if "line" not in evidence["push_ready"]:
        return {
            "status": NOT_CONFIGURED,
            "detail": "LINE 尚未設定或未啟用（push_ready 沒有 line）。",
            "evidence": evidence,
        }
    # 設定齊備只證明「送得出去」；「手機上收到且是純文字」只有你看得到。
    return {
        "status": NEEDS_HUMAN,
        "detail": "LINE 已就緒；推一則晨報後由你確認手機收到、且沒有裸 HTML 標籤。",
        "evidence": evidence,
    }


# ---- A12 小秘書問候卡 -----------------------------------------------------


def _check_a12(ctx: _Ctx) -> dict[str, Any]:
    evidence = {
        "display_name_set": bool(str(ctx.cfg.get("proactive_secretary.greeting.display_name", "") or "").strip()),
        "in_morning_briefing": bool(ctx.cfg.get("proactive_secretary.greeting.in_morning_briefing", True)),
        "llm_polish_enabled": bool(ctx.cfg.get("proactive_secretary.greeting.llm.enabled", False)),
    }
    # 判準是「卡上每個數字都能在別的分頁對得上」——這是人眼比對，機器不代勞。
    return {
        "status": NEEDS_HUMAN,
        "detail": "問候卡的數字要由你對照 03／04 分頁核對；這裡只回報設定狀態。",
        "evidence": evidence,
    }


# ---- A13 本機行事曆 -------------------------------------------------------


def _check_a13(ctx: _Ctx) -> dict[str, Any]:
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
        "last_seen_at": last_seen.isoformat(timespec="seconds") if last_seen else None,
        "basis": "calendar_events",
    }
    if not paths:
        return {
            "status": NOT_CONFIGURED,
            "detail": "沒有設定任何 .ics 路徑——行事曆等於停用。",
            "evidence": evidence,
        }
    if ctx.runtime:
        # 只有真的設了路徑才去問採集器診斷；沒設路徑不值得為此碰 manager。
        from core.manager import get_manager

        diagnostics = (
            get_manager().get_status().get("collector_diagnostics", {}).get("calendar_watcher", {})
        )
        evidence["degraded_sources"] = diagnostics.get("degraded_sources", [])
        evidence["degraded_sources_count"] = diagnostics.get("degraded_sources_count", 0)
    if evidence.get("degraded_sources_count"):
        return {
            "status": PARTIAL,
            "detail": f"有 {evidence['degraded_sources_count']} 個來源檔解析失敗；看 degraded_sources 的 error。",
            "evidence": evidence,
        }
    if total > 0:
        return {
            "status": PASSED,
            "detail": f"{sources} 個來源檔、視野內 {total} 筆行程已入庫。",
            "evidence": evidence,
        }
    return {
        "status": PENDING,
        "detail": "路徑已設定但還沒採到任何行程；等下一次掃描或檢查檔案內容。",
        "evidence": evidence,
    }


# ---- 項目清單 -------------------------------------------------------------

_ITEMS: tuple[dict[str, Any], ...] = (
    {
        "id": "A1",
        "title": "全天 coverage ledger",
        "priority": "P0",
        "blocks_release": True,
        "how": "讓實機跨午夜連續運行一整天",
        "criterion": "任一天的 ledger coverage 達門檻（meets_full_coverage: true）",
        "probe": _check_a1,
    },
    {
        "id": "A2",
        "title": "RAG 雲端 provider 複測",
        "priority": "P0",
        "blocks_release": True,
        "how": "在小秘書／知識庫分頁選 Gemini（或 OpenAI／Claude）問一題",
        "criterion": "rag_chat_messages 有一筆雲端 provider 的非錯誤回答",
        "probe": _check_a2,
    },
    {
        "id": "A3",
        "title": "Telegram 設定 + inline 批准",
        "priority": "P1",
        "blocks_release": False,
        "how": "設定 Telegram → 開 inline 批准 → 解鎖 → 實批一次 L1 動作",
        "criterion": "有 approved_via=telegram_inline 的成功 receipt",
        "probe": _check_a3,
    },
    {
        "id": "A4",
        "title": "L2 執行器實機試用",
        "priority": "P1",
        "blocks_release": False,
        "how": "開三個執行器開關，實跑 draft →（可選）confirm → apply",
        "criterion": "agent_draft_plan 有 succeeded receipt",
        "probe": _check_a4,
    },
    {
        "id": "A5",
        "title": "P4.3 對帳實操",
        "priority": "P1",
        "blocks_release": False,
        "how": "Git 同步中心 → 掃描對帳 → 各實跑一種動作（init／attach／clone）",
        "criterion": "三類分類符合預期、拒絕條件如實擋下（人眼確認）",
        "probe": _check_a5,
    },
    {
        "id": "A6",
        "title": "檢索 worker 大索引實測",
        "priority": "P1",
        "blocks_release": False,
        "how": "啟動服務，等檢索 worker 卡片變「就緒」，再問一題",
        "criterion": "worker state=ready 且載入計數與實際索引一致",
        "probe": _check_a6,
    },
    {
        "id": "A7",
        "title": "Repo 同步全覽與批次實操",
        "priority": "P1",
        "blocks_release": False,
        "how": "載入全覽 → 全部 Fetch → 批次 Pull；另跑一次 repo_sync_report",
        "criterion": "reports/repo_sync 有報告，且 repo_pull_ff 有成功 receipt",
        "probe": _check_a7,
    },
    {
        "id": "A8",
        "title": "小秘書每日包實機收據",
        "priority": "P1",
        "blocks_release": False,
        "how": "建立每日排程，或對 morning_pack 按立即執行",
        "criterion": "morning_pack 與 handoff_active_projects 都有 succeeded receipt",
        "probe": _check_a8,
    },
    {
        "id": "A9",
        "title": "小秘書記憶區實機收據",
        "priority": "P1",
        "blocks_release": False,
        "how": "對話框「記下來：…」「偏好：…」→ 跑一次早晨包 → 刪一則觀察",
        "criterion": "記憶區同時有 user_note、preference 與 observation",
        "probe": _check_a9,
    },
    {
        "id": "A10",
        "title": "手機 Telegram 對話實機收據",
        "priority": "P1",
        "blocks_release": False,
        "how": "啟用小秘書對話 → 在手機送 /today、記下來與一句提問",
        "criterion": "記憶區出現 source=telegram 的筆記",
        "probe": _check_a10,
    },
    {
        "id": "A11",
        "title": "LINE 推播實機收據",
        "priority": "P1",
        "blocks_release": False,
        "how": "設定 LINE channel → 測試並儲存啟用 → 推一則晨報",
        "criterion": "手機收到純文字晨報（人眼確認）；push_ready 含 line",
        "probe": _check_a11,
    },
    {
        "id": "A12",
        "title": "小秘書問候卡實機收據",
        "priority": "P2",
        "blocks_release": False,
        "how": "填問候稱呼 → 回 01 分頁看「小秘書的話」→ 切近 2 小時視窗",
        "criterion": "卡上每個數字都能在其他分頁對得上（人眼確認）",
        "probe": _check_a12,
    },
    {
        "id": "A13",
        "title": "本機行事曆實機收據",
        "priority": "P1",
        "blocks_release": False,
        "how": "加入 .ics 路徑 → 儲存並套用 → 看系統健康與 01 首頁",
        "criterion": "行事曆來源運作中、視野內有行程且沒有來源錯誤",
        "probe": _check_a13,
    },
)

ITEM_IDS: tuple[str, ...] = tuple(item["id"] for item in _ITEMS)


# ---- 人工確認收據 ---------------------------------------------------------

CONFIRMATIONS_FILENAME = "confirmations.json"


def confirmations_path(cfg: Any | None = None) -> Path:
    cfg = cfg or get_config()
    return _reports_dir(cfg) / "acceptance" / CONFIRMATIONS_FILENAME


def load_confirmations(cfg: Any | None = None) -> dict[str, Any]:
    """讀使用者自己署名的確認；檔案不存在或壞掉都只是「還沒有」，不讓驗收中心壞掉。"""
    path = confirmations_path(cfg)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(key).upper(): value
        for key, value in data.items()
        if isinstance(value, dict) and str(key).upper() in ITEM_IDS
    }


def record_human_confirmation(
    item_id: str,
    *,
    confirmed: bool = True,
    note: str = "",
    cfg: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """記下「我親眼確認過這一項」。

    這是**人工署名**，不是機器證據：它只會讓機器查不到判準的項目（needs_human）
    收斂，永遠不會覆蓋機器已經查到的結果。取消確認就把該項移除。
    """
    item_id = str(item_id).upper()
    if item_id not in ITEM_IDS:
        raise ValueError(f"unknown acceptance item: {item_id}")
    cfg = cfg or get_config()
    now = now or get_local_now()
    path = confirmations_path(cfg)
    data = load_confirmations(cfg)
    if confirmed:
        data[item_id] = {
            "confirmed_at": now.isoformat(timespec="seconds"),
            "note": str(note or "")[:500],
            "basis": "human_attested_not_machine_evidence",
        }
    else:
        data.pop(item_id, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "item_id": item_id,
        "confirmed": bool(confirmed),
        "confirmation": data.get(item_id),
        "path": str(path),
        "claim_boundary": ACCEPTANCE_CLAIM_BOUNDARY,
    }


# ---- release gates（ROADMAP §12.3） --------------------------------------


def _quality_gate_summary() -> dict[str, Any]:
    """STATUS.yaml 的 quality_gates 是否都是 passed_*；讀不到就如實說讀不到。"""
    root = source_checkout_root()
    status_path = (root / "STATUS.yaml") if root else None
    if not status_path or not status_path.is_file():
        return {"available": False, "reason": "status_yaml_not_in_runtime_layout"}
    try:
        import yaml

        data = yaml.safe_load(status_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 — 讀不到就降級，不讓驗收中心壞掉
        return {"available": False, "reason": f"unreadable:{type(exc).__name__}"}
    gates = data.get("quality_gates") or {}
    not_passed = sorted(
        name for name, value in gates.items() if not str(value).startswith("passed_")
    )
    return {
        "available": True,
        "total": len(gates),
        "not_passed": not_passed,
        "known_blockers": len(data.get("known_blockers") or []),
    }


def _release_gates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {item["id"]: item for item in items}
    blocking = [item for item in items if item["blocks_release"] and item["status"] not in _SETTLED]
    default_on = ["A2", "A6", "A12", "A13"]  # 預設開啟路徑；危險能力可標 optional
    default_on_outstanding = [
        i for i in default_on if by_id.get(i, {}).get("status") in _OUTSTANDING
    ]
    quality = _quality_gate_summary()
    return [
        {
            "id": "G1",
            "text": "🔴 P0 項目取得實機收據（A1 全天 coverage ledger、A2 雲端 provider 複測）",
            "status": PASSED if not blocking else PENDING,
            "outstanding": [item["id"] for item in blocking],
        },
        {
            "id": "G2",
            "text": "預設開啟路徑的收據齊備（預設關閉的危險能力可標 optional-verified）",
            "status": PASSED if not default_on_outstanding else PENDING,
            "outstanding": default_on_outstanding,
        },
        {
            "id": "G3",
            "text": "docs/RELEASE_CHECKLIST.md 走完一輪，且跨平台 CI 在該 commit 有自己的 run receipt",
            "status": NEEDS_HUMAN,
            "outstanding": [],
        },
        {
            "id": "G4",
            "text": "STATUS.yaml 的 quality gates 全為 passed_*、known_blockers 無 🔴",
            "status": (
                PASSED
                if quality.get("available") and not quality.get("not_passed")
                else (PENDING if quality.get("available") else NEEDS_HUMAN)
            ),
            "outstanding": quality.get("not_passed", []),
            "evidence": quality,
        },
    ]


# ---- 對外入口 -------------------------------------------------------------


def build_acceptance_report(
    *,
    runtime: bool = False,
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
    only: list[str] | None = None,
) -> dict[str, Any]:
    """回傳 TODO A 段每一項的收據狀態。唯讀、便宜、可重跑。

    ``runtime=True`` 代表呼叫端就是服務執行中的那個程序（Web API），
    程序內記憶體狀態才有意義；CLI 用 False，該類項目回 ``runtime_only``。
    """
    database = database or get_db()
    cfg = cfg or get_config()
    now = now or get_local_now()
    wanted = {i.upper() for i in only} if only else None
    confirmations = load_confirmations(cfg)

    items: list[dict[str, Any]] = []
    with database.session_scope() as session:
        ctx = _Ctx(database=database, cfg=cfg, now=now, runtime=runtime, session=session)
        for spec in _ITEMS:
            if wanted and spec["id"] not in wanted:
                continue
            probe: Callable[[_Ctx], dict[str, Any]] = spec["probe"]
            try:
                result = probe(ctx)
            except Exception as exc:  # noqa: BLE001 — 單項查不到不該讓整頁壞掉
                result = {
                    "status": PENDING,
                    "detail": f"這項的查詢失敗（{type(exc).__name__}: {exc}）；其餘項目不受影響。",
                    "evidence": {"probe_error": type(exc).__name__},
                }
            attestation = confirmations.get(spec["id"])
            if attestation and result["status"] == NEEDS_HUMAN:
                # 機器沒有判準可查的項目，才由人工署名收斂；其餘一律機器判定優先。
                result = {**result, "status": ATTESTED}
            items.append(
                {
                    "id": spec["id"],
                    "title": spec["title"],
                    "priority": spec["priority"],
                    "blocks_release": spec["blocks_release"],
                    "how": spec["how"],
                    "criterion": spec["criterion"],
                    "attestation": attestation,
                    **result,
                }
            )

    counts: dict[str, int] = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    outstanding = [i["id"] for i in items if i["status"] in _OUTSTANDING]

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "mode": "server" if runtime else "cli",
        "items": items,
        "summary": {
            "total": len(items),
            "counts": counts,
            "passed": counts.get(PASSED, 0),
            "attested": counts.get(ATTESTED, 0),
            "outstanding": outstanding,
            "blocking_release": [
                i["id"] for i in items if i["blocks_release"] and i["status"] not in _SETTLED
            ],
        },
        # gate 是「整份清單」的收斂條件；只查了部分項目時給不出誠實的答案，就不給。
        "release_gates": [] if wanted else _release_gates(items),
        "release_gates_note": (
            "只查了部分項目，release gate 需要完整清單才有意義" if wanted else ""
        ),
        "source": "docs/TODO.md A 段",
        "claim_boundary": ACCEPTANCE_CLAIM_BOUNDARY,
    }
