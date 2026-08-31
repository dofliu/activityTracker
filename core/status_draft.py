"""P5-R5 STATUS 自動維護（draft-only）：點名 STATUS.yaml 已落後的專案。

只「讀」使用者 repo 根目錄的 ``STATUS.yaml`` 與本機觀測（ProjectState），
草稿一律寫入 ``exporters.reports_dir/status_drafts``；本模組絕不修改任何
使用者 repo 內的檔案——要不要更新 STATUS.yaml 由使用者自己決定（或走
ADR-008 Addendum 的 L2 寫入流程逐項批准）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Optional

import yaml

from core.config import get_config
from core.database import get_db
from core.models import ProjectState
from core.runtime_paths import resolve_runtime_path
from core.time_utils import get_local_now

logger = logging.getLogger("OmniContext.StatusDraft")

# STATUS.yaml 的 last_updated 落後觀測活動超過此天數即點名
STALE_GAP_DAYS = 7
_MAX_LISTED_REPOS = 80
_MAX_STATUS_BYTES = 512 * 1024  # 超大 STATUS.yaml 視為異常，不整份載入

STATUS_DRAFT_CLAIM_BOUNDARY = (
    "草稿只比較 STATUS.yaml 的 last_updated 與本機觀測到的最後活動；"
    "不判斷內容正確性、不代表專案實際進度，也絕不寫入任何使用者 repo。"
)


def _default_repo_references() -> list[Any]:
    from core.repo_sync import LocalRepositorySync

    return LocalRepositorySync()._discover_references()[0]


def _parse_status_yaml(path) -> dict[str, Any]:
    """讀取單一 STATUS.yaml 的非敏感欄位；解析失敗如實回報，不中斷整體。"""
    try:
        if path.stat().st_size > _MAX_STATUS_BYTES:
            return {"parse_error": "file_too_large"}
        data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, yaml.YAMLError):
        return {"parse_error": "unreadable_or_invalid_yaml"}
    if not isinstance(data, dict):
        return {"parse_error": "not_a_mapping"}
    return {
        "last_updated": str(data.get("last_updated") or "").strip(),
        "status": str(data.get("status") or "").strip(),
        "progress": data.get("progress"),
    }


def _parse_date(value: str) -> Optional[datetime]:
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def build_status_draft(
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    now: Optional[datetime] = None,
    repo_references: Optional[Callable[[], list[Any]]] = None,
) -> dict[str, Any]:
    database = database or get_db()
    cfg = cfg or get_config()
    now = now or get_local_now()
    references = (repo_references or _default_repo_references)()

    with database.session_scope() as session:
        observed = {}
        for row in session.query(ProjectState).all():
            for key in (row.project_key, row.display_name):
                if key and key not in observed:
                    observed[key] = row.last_activity_at

    entries: list[dict[str, Any]] = []
    without_status: list[str] = []
    parse_errors = 0
    for ref in references[:_MAX_LISTED_REPOS]:
        repo_name = ref.path.name
        status_path = ref.path / "STATUS.yaml"
        if not status_path.is_file():
            without_status.append(repo_name)
            continue
        parsed = _parse_status_yaml(status_path)
        entry: dict[str, Any] = {"repo": repo_name}
        if "parse_error" in parsed:
            parse_errors += 1
            entry["parse_error"] = parsed["parse_error"]
            entries.append(entry)
            continue
        entry["status_last_updated"] = parsed["last_updated"] or None
        entry["status_value"] = parsed["status"] or None
        last_activity = observed.get(repo_name)
        entry["observed_last_activity"] = (
            last_activity.strftime("%Y-%m-%d") if last_activity else None
        )
        status_date = _parse_date(parsed["last_updated"])
        if status_date is not None and last_activity is not None:
            gap_days = (last_activity.date() - status_date.date()).days
            entry["gap_days"] = gap_days
            entry["stale"] = gap_days >= STALE_GAP_DAYS
        else:
            entry["gap_days"] = None
            entry["stale"] = False
        entries.append(entry)

    stale_entries = [item for item in entries if item.get("stale")]

    lines = [
        f"# 🗂️ STATUS.yaml 維護草稿（{now.strftime('%Y-%m-%d %H:%M')}）",
        f"> {STATUS_DRAFT_CLAIM_BOUNDARY}",
        "",
        f"掃描 repo：{len(references)}；含 STATUS.yaml：{len(entries)}；"
        f"點名落後（觀測活動晚於 last_updated ≥ {STALE_GAP_DAYS} 天）：{len(stale_entries)}；"
        f"解析失敗：{parse_errors}",
        "",
    ]
    if stale_entries:
        lines.append("## ⚠️ 建議更新的 STATUS.yaml")
        lines.append("")
        lines.append("| Repo | STATUS last_updated | 觀測最後活動 | 落後天數 |")
        lines.append("| :--- | :--- | :--- | ---: |")
        for item in stale_entries:
            lines.append(
                f"| {item['repo']} | {item.get('status_last_updated') or '—'} "
                f"| {item.get('observed_last_activity') or '—'} | {item.get('gap_days')} |"
            )
        lines.append("")
        lines.append(
            "> 建議動作：在各 repo 內自行更新 `last_updated` 與 `current_phase`；"
            "本草稿不會代為修改。"
        )
    else:
        lines.append("## ✅ 沒有需要點名的 STATUS.yaml")
    if entries:
        lines.extend(["", "## 📋 全部含 STATUS.yaml 的 repo", ""])
        for item in entries:
            if "parse_error" in item:
                lines.append(f"- {item['repo']}：STATUS.yaml 解析失敗（{item['parse_error']}）")
            else:
                lines.append(
                    f"- {item['repo']}：last_updated={item.get('status_last_updated') or '—'}，"
                    f"觀測最後活動={item.get('observed_last_activity') or '—'}"
                )
    if without_status:
        lines.extend([
            "",
            f"## 📁 未含 STATUS.yaml 的 repo（{len(without_status)} 個）",
            "",
            "、".join(sorted(without_status)[:30])
            + ("…" if len(without_status) > 30 else ""),
        ])
    markdown = "\n".join(lines) + "\n"

    drafts_dir = (
        resolve_runtime_path(cfg.get("exporters.reports_dir", "reports")) / "status_drafts"
    )
    drafts_dir.mkdir(parents=True, exist_ok=True)
    output_path = drafts_dir / f"Status_Draft_{now.strftime('%Y%m%d')}.md"
    output_path.write_text(markdown, encoding="utf-8")

    return {
        "repos_scanned": len(references),
        "repos_with_status": len(entries),
        "stale_count": len(stale_entries),
        "parse_errors": parse_errors,
        "output_path": str(output_path),
        "claim_boundary": STATUS_DRAFT_CLAIM_BOUNDARY,
    }
