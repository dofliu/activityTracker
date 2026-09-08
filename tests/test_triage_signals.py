"""分流引擎契約測試

重點不在「有沒有產生建議」，而在排序與抑制的規則是否可被信任：
一個 repo 不能霸佔清單、snooze 要真的生效、issue 不能把 PR 混進來。
"""

from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.models import (
    Base,
    GitHubIssueEvent,
    GitHubPREvent,
    GitHubRepoState,
    OpenLoop,
    ProjectState,
)
from core.proactive_secretary import build_action_proposals, snooze_proposal
from core.triage_signals import repo_issue_backlog

NOW = datetime(2026, 8, 28, 10, 0)


class DictConfig:
    def __init__(self, data):
        self.data = data

    def get(self, key_path, default=None):
        value = self.data
        for key in key_path.split("."):
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]
        return value


class TempDatabase:
    def __init__(self, path):
        self.engine = create_engine(f"sqlite:///{path.as_posix()}")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine)

    @contextmanager
    def session_scope(self):
        session = self.factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def _config(**overrides):
    data = {
        "enabled": True,
        "max_proposals": 12,
        "stalled_open_loop_hours": 48,
        "max_per_project": 2,
        "unfinished_recent_min_idle_hours": 12,
    }
    data.update(overrides)
    return DictConfig({"proactive_secretary": data})


def _extension_status(heartbeat_verified=True):
    return {
        "extension": {
            "token_configured": True,
            "heartbeat_verified": heartbeat_verified,
        }
    }


def _pr(session, repo, number, *, ci="success", draft=False, days_old=10, review="PENDING"):
    session.add(GitHubPREvent(
        repo_name=repo,
        pr_number=number,
        title=f"{repo} PR {number}",
        state="open",
        is_draft=draft,
        author="dofliu",
        html_url=f"https://github.com/dofliu/{repo}/pull/{number}",
        ci_status=ci,
        review_state=review,
        created_at=NOW - timedelta(days=days_old),
        updated_at=NOW - timedelta(days=days_old),
    ))


def _build(database, **kwargs):
    return build_action_proposals(
        database=database,
        cfg=kwargs.pop("cfg", _config()),
        now=NOW,
        extension_status=_extension_status(),
        **kwargs,
    )


def test_ci_failing_pr_outranks_green_pr_of_same_age(tmp_path):
    database = TempDatabase(tmp_path / "pr_rank.db")
    with database.session_scope() as session:
        _pr(session, "repoA", 1, ci="failure", days_old=10)
        _pr(session, "repoB", 2, ci="success", days_old=10)

    proposals = _build(database)["proposals"]
    types = [item["proposal_type"] for item in proposals]

    assert types[0] == "ci_failing_pr", "CI 紅燈的 PR 應排在綠燈之前"
    assert "review_ready_pr" in types
    # 理由必須說得出來，不能只有一個 priority 標籤
    assert all(item["reasons"] for item in proposals)


def test_single_repo_cannot_dominate_the_list(tmp_path):
    database = TempDatabase(tmp_path / "diversity.db")
    with database.session_scope() as session:
        for number in range(1, 7):
            _pr(session, "busyRepo", number, ci="failure", days_old=30)
        _pr(session, "quietRepo", 99, ci="success", days_old=5)

    result = _build(database)
    proposals = result["proposals"]
    busy = [item for item in proposals if item["project_key"] == "busyRepo"]

    assert len(busy) == 2, "同一 repo 最多只能佔 2 個名額"
    assert any(item["project_key"] == "quietRepo" for item in proposals), (
        "其他專案不應被單一 repo 擠掉"
    )
    assert busy[0]["same_project_pending"] == 4, "被折疊的數量要讓使用者看得到"
    assert result["total_candidates"] == 7


def test_snooze_suppresses_target_until_it_expires(tmp_path):
    database = TempDatabase(tmp_path / "snooze.db")
    with database.session_scope() as session:
        _pr(session, "repoA", 1, ci="failure", days_old=10)

    before = _build(database)["proposals"]
    assert len(before) == 1

    snooze_proposal(
        proposal_type="ci_failing_pr",
        project_key="repoA",
        subject_ref="pr:repoA#1",
        days=7,
        database=database,
        now=NOW,
    )

    after = build_action_proposals(
        database=database,
        cfg=_config(),
        now=NOW + timedelta(days=1),
        extension_status=_extension_status(),
    )
    assert after["proposals"] == []
    assert after["inputs"]["snoozed_suppressed"] == 1

    expired = build_action_proposals(
        database=database,
        cfg=_config(),
        now=NOW + timedelta(days=10),
        extension_status=_extension_status(),
    )
    assert len(expired["proposals"]) == 1, "snooze 到期後應自動恢復提醒"


def test_dismissed_target_never_returns(tmp_path):
    database = TempDatabase(tmp_path / "dismiss.db")
    with database.session_scope() as session:
        _pr(session, "repoA", 1, ci="failure", days_old=10)

    snooze_proposal(
        proposal_type="ci_failing_pr",
        project_key="repoA",
        subject_ref="pr:repoA#1",
        dismissed=True,
        database=database,
        now=NOW,
    )

    far_future = build_action_proposals(
        database=database,
        cfg=_config(),
        now=NOW + timedelta(days=365),
        extension_status=_extension_status(),
    )
    assert far_future["proposals"] == []


def test_recently_touched_project_is_not_nagged(tmp_path):
    database = TempDatabase(tmp_path / "recent.db")
    with database.session_scope() as session:
        session.add(ProjectState(
            project_key="live",
            display_name="Live",
            last_activity_at=NOW - timedelta(hours=1),
            status="active",
        ))
        session.add(OpenLoop(
            project_key="live",
            title="still working on it",
            status="open",
            created_at=NOW - timedelta(hours=2),
        ))

    result = _build(database)
    assert result["proposals"] == [], "1 小時前才動過的專案不需要提醒"


def test_yesterday_unfinished_work_is_surfaced(tmp_path):
    database = TempDatabase(tmp_path / "yesterday.db")
    with database.session_scope() as session:
        session.add(ProjectState(
            project_key="yesterday",
            display_name="Yesterday",
            last_activity_at=NOW - timedelta(hours=20),
            status="active",
        ))
        session.add(OpenLoop(
            project_key="yesterday",
            title="sensitive open loop title",
            status="open",
            created_at=NOW - timedelta(hours=22),
        ))

    result = _build(database)
    types = [item["proposal_type"] for item in result["proposals"]]
    assert "unfinished_recent" in types, "昨天做到一半的事應該被提出來"
    # 未結事項標題可能含原始提問內容，不得外洩
    assert "sensitive open loop title" not in str(result)


def test_repo_issue_backlog_excludes_pull_requests(tmp_path):
    """GitHub 的 open_issues_count 把 PR 也算進去，扣掉才是真的 issue 數。"""
    database = TempDatabase(tmp_path / "backlog.db")
    with database.session_scope() as session:
        session.add(GitHubRepoState(
            repo_name="mixed",
            full_name="dofliu/mixed",
            html_url="https://github.com/dofliu/mixed",
            open_prs_count=7,
            open_issues_count=7,
        ))
        session.add(GitHubRepoState(
            repo_name="real",
            full_name="dofliu/real",
            html_url="https://github.com/dofliu/real",
            open_prs_count=0,
            open_issues_count=19,
        ))

    with database.session_scope() as session:
        backlog = repo_issue_backlog(session)

    assert "mixed" not in backlog, "7 個 issue 全是 PR，實際 issue 數為 0"
    assert backlog["real"] == 19


def test_open_issue_becomes_proposal_with_traceable_evidence(tmp_path):
    # 這支測的是「issue 變成提案時收據可追溯」，不是年齡。原本用 120 天前的 issue；
    # 2026-09-08 起超過 60 天沒更新的 PR／issue 不納入考量（ADR-007 Addendum），
    # 所以改成 20 天——太舊的情境由下面的專屬測試負責。
    database = TempDatabase(tmp_path / "issue.db")
    with database.session_scope() as session:
        session.add(GitHubIssueEvent(
            repo_name="labRepo",
            issue_number=12,
            title="學生專題進度確認",
            state="open",
            author="dofliu",
            assignee="student01",
            html_url="https://github.com/dofliu/labRepo/issues/12",
            labels_json='["todo"]',
            created_at=NOW - timedelta(days=20),
            updated_at=NOW - timedelta(days=20),
        ))

    proposals = _build(database)["proposals"]
    assert len(proposals) == 1
    item = proposals[0]
    assert item["proposal_type"] == "assigned_issue"
    assert item["url"].endswith("/issues/12")
    assert any(ref.startswith("github_issue_events:") for ref in item["evidence_refs"])
    assert any("student01" in reason for reason in item["reasons"])
    assert item["execution_available"] is False


# ---- 太舊的 PR／issue 不納入考量（ADR-007 Addendum 2026-09-08）----


def _issue(session, repo, number, *, days_old=10, assignee=None):
    session.add(GitHubIssueEvent(
        repo_name=repo,
        issue_number=number,
        title=f"{repo} issue {number}",
        state="open",
        assignee=assignee,
        html_url=f"https://github.com/dofliu/{repo}/issues/{number}",
        created_at=NOW - timedelta(days=days_old),
        updated_at=NOW - timedelta(days=days_old),
    ))


def test_github_items_idle_for_months_are_left_out_but_counted(tmp_path):
    """實機：放了 101 天的 PR 被標成「只差一個 review、收益立即」。幾個月沒動不是
    「現在該做的事」——預設 60 天以上不進提案，但 inputs 要說得出少了哪些。"""
    db = TempDatabase(tmp_path / "t.db")
    with db.session_scope() as session:
        _pr(session, "z72-scada-system", 92, ci="success", days_old=101)
        _pr(session, "z72-scada-system", 93, ci="success", days_old=100)
        _pr(session, "fresh-app", 7, ci="success", days_old=59)
        _issue(session, "z72-scada-system", 40, days_old=200)
        _issue(session, "fresh-app", 3, days_old=12, assignee="dofliu")

    result = _build(db)
    subjects = {p["subject_ref"] for p in result["proposals"]}
    assert "pr:fresh-app#7" in subjects and "issue:fresh-app#3" in subjects
    assert not any(ref.startswith("pr:z72-scada-system") for ref in subjects)
    assert "issue:z72-scada-system#40" not in subjects

    stale = result["inputs"]["github_stale_excluded"]
    assert stale == {
        "threshold_days": 60,
        "prs": 2,
        "issues": 1,
        "total": 3,
        "subjects": [
            {"subject_ref": "issue:z72-scada-system#40", "age_days": 200.0},
            {"subject_ref": "pr:z72-scada-system#92", "age_days": 101.0},
            {"subject_ref": "pr:z72-scada-system#93", "age_days": 100.0},
        ],
    }
    # open_prs／open_issues 仍是「全部開著的」，不是過濾後的數——帳要對得上
    assert result["inputs"]["open_prs"] == 3 and result["inputs"]["open_issues"] == 2


def test_stale_cutoff_is_configurable_and_zero_disables_it(tmp_path):
    db = TempDatabase(tmp_path / "t.db")
    with db.session_scope() as session:
        _pr(session, "old-app", 1, ci="success", days_old=101)
        _pr(session, "old-app", 2, ci="success", days_old=45)

    strict = _build(db, cfg=_config(github_stale_after_days=30))
    assert {p["subject_ref"] for p in strict["proposals"]} == set()
    assert strict["inputs"]["github_stale_excluded"]["total"] == 2

    off = _build(db, cfg=_config(github_stale_after_days=0))
    assert {p["subject_ref"] for p in off["proposals"]} == {"pr:old-app#1", "pr:old-app#2"}
    assert off["inputs"]["github_stale_excluded"] == {
        "threshold_days": 0, "prs": 0, "issues": 0, "total": 0, "subjects": [],
    }


def test_split_stale_keeps_the_boundary_day():
    from core.triage_signals import split_stale_github_signals

    signals = [{"subject_ref": "a", "age_days": 60.0}, {"subject_ref": "b", "age_days": 60.1}]
    kept, dropped = split_stale_github_signals(signals, 60)
    assert [x["subject_ref"] for x in kept] == ["a"] and [x["subject_ref"] for x in dropped] == ["b"]
    assert split_stale_github_signals(signals, 0) == (signals, [])
