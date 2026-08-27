"""分流訊號：把「哪些事在等你」轉成可排序的候選清單。

秘書原本只看一種訊號（停滯專案 + 未結事項），所以 70 個專案裡只挑得出 1 件事。
這個模組把訊號來源攤開，每個訊號各自算分並附上「為什麼是這個分數」的理由，
讓排序的依據可以被檢查，而不是一個說不出所以然的 priority 標籤。

所有函式皆為唯讀，不寫資料庫、不呼叫外部服務。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable

from .models import (
    GitHubIssueEvent,
    GitHubPREvent,
    GitHubRepoState,
    OpenLoop,
    ProjectState,
)

# 分數區間刻意重疊：同一類訊號內部靠年齡/嚴重度拉開差距，
# 跨類別則由這裡的基準值決定誰通常比較急。
BASE_SCORES = {
    "ci_failing_pr": 0.88,      # CI 紅燈的 PR：合不進去，且會擋住後續工作
    "review_ready_pr": 0.70,    # CI 綠燈但沒人 review：只差你點一下
    "unfinished_recent": 0.66,  # 最近還在動、但有未收尾的事（趁記憶還在）
    "assigned_issue": 0.58,     # 已指派的 issue
    "aging_pr": 0.55,           # 開很久沒動的 PR
    "stalled_open_loop": 0.52,  # 專案停滯但仍有未結事項
    "aging_issue": 0.40,        # 沒人認領又放很久的 issue
}

# 年齡加權刻意壓低：放很久確實該處理，但不該讓一批舊 issue 蓋過今天真正卡住的事。
AGE_BONUS_MAX = 0.12


def _local_naive(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


def _age_days(value: datetime | None, now: datetime) -> float:
    dt = _local_naive(value)
    if dt is None:
        return 0.0
    return max(0.0, (now - dt).total_seconds() / 86400)


def _age_bonus(days: float, full_at: float) -> float:
    """年齡加權：越久沒動越該處理，但有上限避免壓過嚴重度。"""
    if full_at <= 0:
        return 0.0
    return min(1.0, days / full_at) * AGE_BONUS_MAX


def _clamp(score: float) -> float:
    return round(max(0.0, min(0.99, score)), 3)


def collect_pr_signals(session: Any, now: datetime) -> list[dict[str, Any]]:
    """開著的 PR：CI 紅燈 > 等 review > 單純放很久。"""
    signals: list[dict[str, Any]] = []
    prs: Iterable[GitHubPREvent] = (
        session.query(GitHubPREvent).filter(GitHubPREvent.state == "open").all()
    )

    for pr in prs:
        days = _age_days(pr.updated_at or pr.created_at, now)
        ci = (pr.ci_status or "").lower()
        review = (pr.review_state or "").upper()
        subject_ref = f"pr:{pr.repo_name}#{pr.pr_number}"
        reasons: list[str] = []

        if ci == "failure":
            kind = "ci_failing_pr"
            reasons.append("CI 檢查失敗，這個 PR 目前合不進去")
        elif pr.is_draft:
            kind = "aging_pr"
            reasons.append("仍是 draft，尚未進入 review")
        elif review == "APPROVED":
            kind = "review_ready_pr"
            reasons.append("已通過 review，可以直接 merge")
        elif ci == "success":
            kind = "review_ready_pr"
            reasons.append("CI 綠燈但還沒有人 review")
        else:
            kind = "aging_pr"
            reasons.append(f"CI 狀態為 {ci or 'unknown'}")

        if days >= 30:
            reasons.append(f"已開啟 {int(days)} 天沒有更新")
        elif days >= 7:
            reasons.append(f"{int(days)} 天沒有更新")

        signals.append({
            "signal_type": kind,
            "project_key": pr.repo_name,
            "subject_ref": subject_ref,
            "title": f"{pr.repo_name} #{pr.pr_number}",
            "detail": pr.title or "",
            "url": pr.html_url,
            "reasons": reasons,
            "age_days": round(days, 1),
            "score": _clamp(BASE_SCORES[kind] + _age_bonus(days, 60)),
            "evidence_ref": f"github_pr_events:{pr.id}",
            "observed_at": pr.updated_at,
        })

    return signals


def collect_issue_signals(session: Any, now: datetime) -> list[dict[str, Any]]:
    """開著的 issue：有指派給人的優先，其餘依年齡。"""
    signals: list[dict[str, Any]] = []
    issues: Iterable[GitHubIssueEvent] = (
        session.query(GitHubIssueEvent).filter(GitHubIssueEvent.state == "open").all()
    )

    for issue in issues:
        days = _age_days(issue.updated_at or issue.created_at, now)
        kind = "assigned_issue" if issue.assignee else "aging_issue"
        reasons: list[str] = []

        if issue.assignee:
            reasons.append(f"已指派給 {issue.assignee}")
        else:
            reasons.append("尚未指派負責人")

        labels: list[str] = []
        if issue.labels_json:
            try:
                parsed = json.loads(issue.labels_json)
                if isinstance(parsed, list):
                    labels = [str(item) for item in parsed]
            except (ValueError, TypeError):
                labels = []
        if labels:
            reasons.append("標籤：" + "、".join(labels[:3]))

        if days >= 30:
            reasons.append(f"已 {int(days)} 天沒有更新")

        signals.append({
            "signal_type": kind,
            "project_key": issue.repo_name,
            "subject_ref": f"issue:{issue.repo_name}#{issue.issue_number}",
            "title": f"{issue.repo_name} issue #{issue.issue_number}",
            "detail": issue.title or "",
            "url": issue.html_url,
            "reasons": reasons,
            "age_days": round(days, 1),
            "score": _clamp(BASE_SCORES[kind] + _age_bonus(days, 90)),
            "evidence_ref": f"github_issue_events:{issue.id}",
            "observed_at": issue.updated_at,
        })

    return signals


def collect_open_loop_signals(
    session: Any, now: datetime, stalled_hours: int
) -> list[dict[str, Any]]:
    """未結事項：分成「停滯」與「剛動過但沒收尾」兩種，後者更值得趁熱處理。"""
    signals: list[dict[str, Any]] = []
    projects = {
        item.project_key.lower(): item for item in session.query(ProjectState).all()
    }
    loops = (
        session.query(OpenLoop)
        .filter(OpenLoop.status == "open")
        .order_by(OpenLoop.project_key, OpenLoop.id)
        .all()
    )

    grouped: dict[str, list[OpenLoop]] = {}
    for loop in loops:
        grouped.setdefault(loop.project_key.lower(), []).append(loop)

    for normalized_key, project_loops in grouped.items():
        project = projects.get(normalized_key)
        if project is None or project.last_activity_at is None:
            continue

        idle_days = _age_days(project.last_activity_at, now)
        idle_hours = idle_days * 24
        loop_count = len(project_loops)
        reasons: list[str] = []

        if idle_hours >= stalled_hours:
            kind = "stalled_open_loop"
            reasons.append(f"專案已 {int(idle_days)} 天沒有活動")
            reasons.append(f"仍有 {loop_count} 項未結事項")
            score = BASE_SCORES[kind] + _age_bonus(idle_days, 45)
        else:
            # 還在動但有未收尾的事：脈絡還在腦中，收尾成本最低
            kind = "unfinished_recent"
            reasons.append("最近仍在進行中，脈絡還在")
            reasons.append(f"但有 {loop_count} 項未收尾")
            score = BASE_SCORES[kind] + min(0.2, loop_count * 0.04)

        signals.append({
            "signal_type": kind,
            "project_key": project.project_key,
            "subject_ref": f"project:{project.project_key}",
            "title": project.display_name or project.project_key,
            "detail": project.last_action_summary or "",
            "url": None,
            "reasons": reasons,
            "age_days": round(idle_days, 1),
            "score": _clamp(score),
            "evidence_ref": f"project_states:{project.id}",
            "observed_at": project.last_activity_at,
            "open_loop_refs": [f"open_loops:{loop.id}" for loop in project_loops[:3]],
            "open_loop_titles": [loop.title for loop in project_loops[:3]],
        })

    return signals


def repo_issue_backlog(session: Any) -> dict[str, int]:
    """各 repo 的真實 issue 數（GitHub 的 open_issues_count 含 PR，須扣除）。"""
    backlog: dict[str, int] = {}
    for repo in session.query(GitHubRepoState).all():
        real_issues = max(0, int(repo.open_issues_count or 0) - int(repo.open_prs_count or 0))
        if real_issues > 0:
            backlog[repo.repo_name] = real_issues
    return backlog
