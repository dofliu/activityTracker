"""把清單跑成一份報告：人工署名、release gate、以及對外入口（ADR-028，TODO D12）。

這裡不認識任何一項驗收——它只知道「表格裡有幾列、每列有一個階梯」。
要增減驗收項目就改 ``items.py``，這個檔案不用動。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from core.config import get_config
from core.database import get_db
from core.runtime_paths import is_demo_home, source_checkout_root
from core.time_utils import get_local_now

from .items import ITEM_IDS, ITEMS
from .rules import (
    ATTESTED, NEEDS_HUMAN, OUTSTANDING, PASSED, PENDING, SETTLED,
    Ctx, reports_dir,
)

DEMO_MODE_DISCLAIMER = (
    "示範模式：這是 omni demo 灌入的示範資料，不是實機收據——機器判定已強制降級為 needs_human"
    "（ADR-031：示範資料不得讓驗收中心的判定變綠）。"
)

ACCEPTANCE_CLAIM_BOUNDARY = (
    "只讀本機已存在的收據；不執行驗收動作、不寫資料、不跑 git、不連網。"
    "passed 代表找到符合判準的收據，不代表功能在所有情境下正確；"
    "needs_human 的項目一律由你親眼確認，機器不會自動判定；"
    "attested 是你自己署名的確認，不是機器證據，也永遠不會覆蓋機器判定。"
)

# ---- 人工確認收據 ---------------------------------------------------------

CONFIRMATIONS_FILENAME = "confirmations.json"


def confirmations_path(cfg: Any | None = None) -> Path:
    cfg = cfg or get_config()
    return reports_dir(cfg) / "acceptance" / CONFIRMATIONS_FILENAME


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


# ---- release gate ---------------------------------------------------------


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
    blocking = [item for item in items if item["blocks_release"] and item["status"] not in SETTLED]
    default_on = ["A2", "A6", "A12", "A13"]  # 預設開啟路徑；危險能力可標 optional
    default_on_outstanding = [
        i for i in default_on if by_id.get(i, {}).get("status") in OUTSTANDING
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
        ctx = Ctx(database=database, cfg=cfg, now=now, runtime=runtime, session=session)
        for spec in ITEMS:
            if wanted and spec["id"] not in wanted:
                continue
            try:
                result = spec["probe"](ctx)
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

    demo_mode = is_demo_home()
    if demo_mode:
        # ADR-031：示範家目錄裡的一切都是示範資料，任何原本會變綠的判定一律
        # 降級為 needs_human——這是本 ADR 唯一不可退讓的判準，寧可整份報告
        # 看起來「還沒做」，也不能讓示範資料教會驗收中心說謊。
        for item in items:
            if item["status"] in SETTLED:
                item["status"] = NEEDS_HUMAN
                item["detail"] = f"{item['detail']} {DEMO_MODE_DISCLAIMER}"

    counts: dict[str, int] = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    outstanding = [i["id"] for i in items if i["status"] in OUTSTANDING]

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
                i["id"] for i in items if i["blocks_release"] and i["status"] not in SETTLED
            ],
        },
        # gate 是「整份清單」的收斂條件；只查了部分項目時給不出誠實的答案，就不給。
        "release_gates": [] if wanted else _release_gates(items),
        "release_gates_note": (
            "只查了部分項目，release gate 需要完整清單才有意義" if wanted else ""
        ),
        "source": "docs/TODO.md A 段",
        "claim_boundary": ACCEPTANCE_CLAIM_BOUNDARY,
        "demo_mode": demo_mode,
    }
