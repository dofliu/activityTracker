"""Repo 同步報告與小秘書提案快照（ADR-011 Addendum B）。

這個模組把「全部本機 repo 的 cached 同步狀態」變成兩樣東西：

1. 一份人可讀的 markdown 報告（``reports/repo_sync/RepoSync_YYYYMMDD.md``）；
2. 一份精簡 JSON 快照（``reports/repo_sync/latest.json``），供
   ``proactive_secretary.build_action_proposals`` 便宜地讀出「哪些 repo
   需要 pull／push」並產生提案——proposals 端點每次請求都會重建，不能
   在那裡對數十個 repo 跑 git status。

契約：

- 全程唯讀且不連網：只讀 git status 與本機 cached remote-tracking ref，
  因此報告反映的是「上次 fetch 之後」的認知；每筆都帶 ``last_fetch_at``。
- 快照只保存計數、branch 名稱與同步狀態，不含檔案路徑以外的內容；
  路徑本來就在 dashboard 可見。
- 這是 L0 排程 template 的 runner；真正的 fetch／pull 仍是 L1，要人批准。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from core.config import get_config
from core.runtime_paths import resolve_runtime_path
from core.time_utils import get_local_now

SNAPSHOT_FILENAME = "latest.json"
DEFAULT_SNAPSHOT_MAX_AGE_HOURS = 36

REPO_SYNC_REPORT_CLAIM_BOUNDARY = (
    "報告只讀本機 cached remote-tracking ref 與 worktree 狀態，不連網；"
    "「需要 pull／push」是相對於上次 fetch 的認知，不代表遠端當下狀態。"
)

_STATE_LABELS = {
    "synced": "已同步",
    "behind": "需要 pull",
    "ahead": "需要 push",
    "diverged": "分歧（需人工處理）",
    "no_upstream": "沒有 upstream",
    "detached_head": "detached HEAD",
    "upstream_unavailable": "upstream 不可用",
    "unavailable": "無法讀取",
}


def report_dir(cfg: Any | None = None) -> Path:
    cfg = cfg or get_config()
    return resolve_runtime_path(cfg.get("exporters.reports_dir", "reports")) / "repo_sync"


def _compact(repo: dict[str, Any]) -> dict[str, Any]:
    worktree = repo.get("worktree") or {}
    return {
        "repo_id": repo.get("repo_id"),
        "name": repo.get("name"),
        "path": repo.get("path"),
        "branch": repo.get("branch"),
        "upstream": repo.get("upstream"),
        "ahead": repo.get("ahead"),
        "behind": repo.get("behind"),
        "sync_state": repo.get("sync_state"),
        "clean": repo.get("clean"),
        "dirty_files": int(worktree.get("staged_files") or 0)
        + int(worktree.get("unstaged_files") or 0)
        + int(worktree.get("untracked_files") or 0)
        + int(worktree.get("conflicted_files") or 0),
        "last_fetch_at": repo.get("last_fetch_at"),
        "error": repo.get("error"),
    }


def _markdown(snapshot: dict[str, Any]) -> str:
    summary = snapshot["summary"]
    lines = [
        f"# Repo 同步報告 {snapshot['generated_at'][:10]}",
        "",
        f"> {REPO_SYNC_REPORT_CLAIM_BOUNDARY}",
        "",
        f"- 掃描 repo：{snapshot['repository_count']}",
        f"- 已同步 {summary['synced']} · 需要 pull {summary['behind']} · 需要 push {summary['ahead']} · 分歧 {summary['diverged']}",
        f"- 沒有 upstream {summary['no_upstream']} · worktree 有未提交變更 {summary['dirty']} · 無法讀取 {summary['unavailable']}",
        f"- 從未 fetch：{snapshot['never_fetched']}",
        "",
        "| Repo | branch → upstream | 狀態 | worktree | 上次 fetch |",
        "| :-- | :-- | :-- | :-- | :-- |",
    ]
    for repo in snapshot["repositories"]:
        state = repo.get("sync_state") or "unknown"
        label = _STATE_LABELS.get(state, state)
        if state == "behind":
            label += f" ↓{repo.get('behind')}"
        elif state == "ahead":
            label += f" ↑{repo.get('ahead')}"
        elif state == "diverged":
            label += f" ↑{repo.get('ahead')} ↓{repo.get('behind')}"
        worktree = "clean" if repo.get("clean") else f"{repo.get('dirty_files') or '?'} 個變更"
        fetched = (repo.get("last_fetch_at") or "從未")[:16]
        lines.append(
            f"| {repo.get('name')} | `{repo.get('branch') or '—'}` → `{repo.get('upstream') or '—'}` | {label} | {worktree} | {fetched} |"
        )
    lines.append("")
    lines.append("需要 pull 的 repo 會出現在小秘書提案中，批准後才會執行 fast-forward pull；push 請在同步中心逐一或批次確認。")
    return "\n".join(lines) + "\n"


def build_repo_sync_report(
    *,
    cfg: Any | None = None,
    now: datetime | None = None,
    sync: Any | None = None,
) -> dict[str, Any]:
    """掃描全部設定範圍內的 repo（cached、不連網），寫快照與報告，回傳收據。"""
    cfg = cfg or get_config()
    now = now or get_local_now()
    if sync is None:
        from core.repo_sync import LocalRepositorySync

        sync = LocalRepositorySync(cfg)
    payload = sync.list_statuses(scope="all")
    repositories = [_compact(repo) for repo in payload.get("repositories", [])]
    summary = dict(payload.get("summary") or {})
    for key in ("synced", "behind", "ahead", "diverged", "no_upstream", "dirty", "unavailable"):
        summary.setdefault(key, 0)
    never_fetched = sum(1 for repo in repositories if not repo.get("last_fetch_at"))
    snapshot = {
        "generated_at": now.isoformat(timespec="seconds"),
        "remote_tracking_basis": "cached_local_remote_tracking_ref",
        "repository_count": int(payload.get("repository_count") or len(repositories)),
        "truncated": bool(payload.get("truncated")),
        "summary": summary,
        "never_fetched": never_fetched,
        "repositories": repositories,
        "claim_boundary": REPO_SYNC_REPORT_CLAIM_BOUNDARY,
    }

    out_dir = report_dir(cfg)
    out_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = out_dir / SNAPSHOT_FILENAME
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    output_path = out_dir / f"RepoSync_{now.strftime('%Y%m%d')}.md"
    output_path.write_text(_markdown(snapshot), encoding="utf-8")

    needs_pull = sum(1 for r in repositories if r.get("sync_state") == "behind" and r.get("clean"))
    needs_push = sum(1 for r in repositories if r.get("sync_state") == "ahead" and r.get("clean"))
    return {
        "repos_scanned": snapshot["repository_count"],
        "needs_pull": needs_pull,
        "needs_push": needs_push,
        "diverged": summary["diverged"],
        "dirty": summary["dirty"],
        "no_upstream": summary["no_upstream"],
        "unavailable": summary["unavailable"],
        "never_fetched": never_fetched,
        "output_path": str(output_path),
        "snapshot_path": str(snapshot_path),
        "claim_boundary": REPO_SYNC_REPORT_CLAIM_BOUNDARY,
    }


def load_snapshot(cfg: Any | None = None) -> dict[str, Any] | None:
    path = report_dir(cfg) / SNAPSHOT_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("repositories"), list) else None


def _snapshot_max_age(cfg: Any) -> timedelta:
    try:
        hours = int(cfg.get("proactive_secretary.repo_sync_snapshot_max_age_hours", DEFAULT_SNAPSHOT_MAX_AGE_HOURS))
    except (TypeError, ValueError):
        hours = DEFAULT_SNAPSHOT_MAX_AGE_HOURS
    return timedelta(hours=max(1, min(hours, 24 * 30)))


def collect_repo_sync_signals(
    *,
    cfg: Any | None = None,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """把新鮮的快照轉成 triage signals；沒有或過期的快照就一筆都不產生。

    回傳 ``(signals, meta)``；meta 描述快照是否被採用與原因，供 proposals
    的 inputs 誠實呈現。
    """
    cfg = cfg or get_config()
    now = now or get_local_now()
    now_naive = now.replace(tzinfo=None) if getattr(now, "tzinfo", None) else now
    snapshot = load_snapshot(cfg)
    if snapshot is None:
        return [], {"used": False, "reason": "no_snapshot"}
    try:
        generated = datetime.fromisoformat(str(snapshot.get("generated_at")))
    except (TypeError, ValueError):
        return [], {"used": False, "reason": "invalid_generated_at"}
    generated_naive = generated.replace(tzinfo=None) if generated.tzinfo else generated
    age = now_naive - generated_naive
    if age > _snapshot_max_age(cfg):
        return [], {"used": False, "reason": "snapshot_stale", "generated_at": snapshot.get("generated_at")}

    signals: list[dict[str, Any]] = []
    for repo in snapshot.get("repositories", []):
        repo_id = str(repo.get("repo_id") or "")
        name = str(repo.get("name") or "")
        if not repo_id or not name:
            continue
        state = repo.get("sync_state")
        clean = bool(repo.get("clean"))
        base = {
            "project_key": name,
            "subject_ref": f"repo:{repo_id}",
            "evidence_ref": f"repo_sync_snapshot:{repo_id}",
            "observed_at": generated_naive,
            "url": None,
            "age_days": round(max(age.total_seconds(), 0) / 86400, 3),
            "open_loop_refs": [],
        }
        fetched = f"上次 fetch {str(repo.get('last_fetch_at'))[:16]}" if repo.get("last_fetch_at") else "從未 fetch"
        if state == "behind" and clean:
            signals.append({
                **base,
                "signal_type": "repo_needs_pull",
                "title": f"{name} 落後遠端 {repo.get('behind')} 個 commit",
                "detail": f"{repo.get('branch')} → {repo.get('upstream')}；worktree clean；{fetched}",
                "reasons": ["本機 branch 落後 cached remote-tracking ref，且 worktree 乾淨，可 fast-forward pull"],
                "score": 0.55,
            })
        elif state == "ahead" and clean:
            signals.append({
                **base,
                "signal_type": "repo_needs_push",
                "title": f"{name} 有 {repo.get('ahead')} 個本機 commit 尚未 push",
                "detail": f"{repo.get('branch')} → {repo.get('upstream')}；worktree clean；{fetched}",
                "reasons": ["本機 branch 領先 cached remote-tracking ref；push 需在同步中心確認"],
                "score": 0.5,
            })
        elif state == "diverged":
            signals.append({
                **base,
                "signal_type": "repo_diverged",
                "title": f"{name} 與遠端分歧（↑{repo.get('ahead')} ↓{repo.get('behind')}）",
                "detail": f"{repo.get('branch')} → {repo.get('upstream')}；{fetched}",
                "reasons": ["本機與遠端各有對方沒有的 commit；需要在 Git/IDE 手動 merge 或 rebase"],
                "score": 0.6,
            })
    return signals, {
        "used": True,
        "generated_at": snapshot.get("generated_at"),
        "repository_count": snapshot.get("repository_count"),
        "signals": len(signals),
    }
