"""P5-1 proposal-only 主動秘書；只讀取本機 evidence，不執行任何行動。"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import hashlib
from typing import Any

from .config import get_config
from .database import get_db
from .extension_monitor import build_extension_status
from .models import OpenLoop, ProjectState
from .time_utils import get_local_now


CLAIM_BOUNDARY = (
    "Proposals are deterministic read-only suggestions derived from observed local evidence. "
    "They are not executed, persisted, or proof that the suggested action is necessary or correct."
)


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

    stalled_hours = max(
        1,
        min(int(cfg.get("proactive_secretary.stalled_open_loop_hours", 48)), 24 * 90),
    )
    proposals: list[dict[str, Any]] = []

    # Extension status 是非敏感 derived status；不得將歷史 event 等同近期 heartbeat。
    status = extension_status
    if status is None:
        status = build_extension_status(database=database, cfg=cfg, now=now)
    extension = status.get("extension", {}) if isinstance(status, dict) else {}
    if extension.get("token_configured") and not extension.get("heartbeat_verified"):
        evidence_refs = ["extension_status:live"]
        proposals.append(
            {
                "proposal_id": _proposal_id(
                    "verify_extension_heartbeat", "OmniContext", evidence_refs
                ),
                "proposal_type": "verify_extension_heartbeat",
                "project_key": "OmniContext",
                "title": "驗證 Browser Extension 即時連線",
                "reason": "本機服務已有 ingest token，但目前沒有近期 token-authenticated heartbeat receipt。",
                "suggested_action": "在 Chrome 重新載入 unpacked Extension，開啟 popup 後檢查 heartbeat 與逐站 Content Ready。",
                "priority": "high",
                "risk_level": "L0_READ_ONLY",
                "execution_available": False,
                "evidence_refs": evidence_refs,
                "evidence": [
                    {
                        "source_ref": "extension_status:live",
                        "kind": "derived_extension_status",
                        "observed_at": now.isoformat(timespec="seconds"),
                    }
                ],
                "score": 1.0,
            }
        )

    # 直接讀 ProjectState/OpenLoop，避免呼叫會 refresh/write 的 project list helper。
    with database.session_scope() as session:
        projects = {
            item.project_key.lower(): item
            for item in session.query(ProjectState).all()
        }
        open_loops = (
            session.query(OpenLoop)
            .filter(OpenLoop.status == "open")
            .order_by(OpenLoop.project_key, OpenLoop.id)
            .all()
        )
        grouped: dict[str, list[OpenLoop]] = defaultdict(list)
        for loop in open_loops:
            grouped[loop.project_key.lower()].append(loop)

        for normalized_key, loops in grouped.items():
            project = projects.get(normalized_key)
            if project is None or project.last_activity_at is None:
                continue
            last_activity_at = _local_naive(project.last_activity_at)
            idle_hours = max(0.0, (now - last_activity_at).total_seconds() / 3600)
            if idle_hours < stalled_hours:
                continue

            selected_loops = loops[:3]
            evidence = [
                _evidence(
                    f"project_states:{project.id}",
                    "project_state",
                    project.last_activity_at,
                )
            ] + [
                _evidence(
                    f"open_loops:{loop.id}",
                    "open_loop",
                    loop.last_seen_at or loop.created_at,
                )
                for loop in selected_loops
            ]
            evidence_refs = [item["source_ref"] for item in evidence]
            idle_days = int(idle_hours // 24)
            priority = "high" if idle_hours >= 24 * 7 else "medium"
            proposals.append(
                {
                    "proposal_id": _proposal_id(
                        "review_stalled_open_loops", project.project_key, evidence_refs
                    ),
                    "proposal_type": "review_stalled_open_loops",
                    "project_key": project.project_key,
                    "title": f"複核 {project.display_name} 的未結事項",
                    "reason": (
                        f"此專案已有約 {idle_days} 天未觀察到活動，"
                        f"且仍有 {len(loops)} 項 actionable Open Loop。"
                    ),
                    "suggested_action": "先檢視 Context Handoff 與來源，再決定要繼續、標記 stale 或結案。",
                    "priority": priority,
                    "risk_level": "L0_READ_ONLY",
                    "execution_available": False,
                    "evidence_refs": evidence_refs,
                    "evidence": evidence,
                    "score": round(min(0.99, 0.55 + idle_hours / (24 * 90)), 3),
                }
            )

    priority_rank = {"high": 0, "medium": 1, "low": 2}
    proposals.sort(
        key=lambda item: (
            priority_rank.get(item["priority"], 9),
            -float(item["score"]),
            item["proposal_id"],
        )
    )
    return {
        "status": "proposal_only",
        "mode": "proposal_only",
        "proposals": proposals[:result_limit],
        "generated_at": now.isoformat(timespec="seconds"),
        "inputs": {
            "project_states": len(projects),
            "actionable_open_loops": len(open_loops),
            "stalled_open_loop_hours": stalled_hours,
        },
        "execution_available": False,
        "cloud_llm_used": False,
        "query_persisted": False,
        "claim_boundary": CLAIM_BOUNDARY,
    }
