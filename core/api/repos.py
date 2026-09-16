"""本機 Git 同步中心與 Repo Onboarding（TODO D4，ROADMAP §13 R1）。

ADR-011 的對外端點：逐項確認的 fetch／pull／commit／push、全覽與批次、onboarding 四種動作。

路徑與 handler 名稱與切分前完全相同，由 `tests/test_api_route_snapshot.py` 鎖住。
"""

import json
import yaml

from core.config import get_config
from core.database import get_db
from core.models import GitHubPREvent
from core.models import GitHubRepoState
from core.project_engine import refresh_project_states
from core.repo_sync import LocalRepositorySync
from core.repo_sync import RepositorySyncRejected
from core.schemas import GitHubConnectRequest, RepoOnboardingActionRequest, RepositorySyncActionRequest, RepositorySyncBatchRequest, RepositorySyncFetchAllRequest
from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Query
from typing import Optional


router = APIRouter()


@router.get("/api/v1/repos/sync-status")
def get_local_repository_sync_status(
    scope: str = Query("recent", pattern="^(recent|all)$"),
):
    """列出設定 root 內的本機 Git 狀態。

    ahead/behind 只比較目前本機保存的 remote-tracking ref；不會在載入頁面時
    自動連線、fetch 或改動任何 worktree。``scope=all`` 回傳全部 repo（全覽表格）。
    """
    return LocalRepositorySync().list_statuses(scope=scope)


@router.get("/api/v1/repos/sync-snapshot")
def get_local_repository_sync_snapshot():
    """最近一次 L0 同步報告留下的快照（只讀檔，不跑 git）；專案卡用它顯示 git 狀態 chip。"""
    from core.repo_sync_report import load_snapshot

    snapshot = load_snapshot()
    if snapshot is None:
        return {"available": False, "repositories": [], "reason": "no_snapshot_yet",
                "hint": "排程或手動執行 repo_sync_report／早晨包後才會有快照。"}
    return {
        "available": True,
        "generated_at": snapshot.get("generated_at"),
        "remote_tracking_basis": snapshot.get("remote_tracking_basis"),
        "repositories": [
            {k: repo.get(k) for k in ("repo_id", "name", "path", "branch", "ahead", "behind", "sync_state", "clean", "last_fetch_at")}
            for repo in snapshot.get("repositories", [])
        ],
        "summary": snapshot.get("summary"),
        "claim_boundary": snapshot.get("claim_boundary"),
    }


@router.post("/api/v1/repos/sync-fetch-all")
def run_local_repository_fetch_all(req: RepositorySyncFetchAllRequest):
    """對全部 repo 執行 fetch --prune：只更新 remote-tracking refs，不改 worktree。"""
    return LocalRepositorySync().fetch_all()


@router.get("/api/v1/repos/sync-batch-plan")
def get_local_repository_batch_plan(
    action: str = Query(..., pattern="^(pull_ff_only|push)$"),
):
    """列出目前符合前置條件的 repo 清單（唯讀）；使用者確認的是這份清單。"""
    try:
        return LocalRepositorySync().batch_plan(action)
    except RepositorySyncRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/v1/repos/sync-batch")
def run_local_repository_batch(req: RepositorySyncBatchRequest):
    """逐一執行已確認清單內的 fast-forward pull 或 push；每個 repo 執行前重檢。"""
    import re as _re

    if any(not _re.fullmatch(r"[a-f0-9]{16}", item) for item in req.repo_ids):
        raise HTTPException(status_code=422, detail="repo_ids 必須是同步狀態清單內的 repo_id")
    try:
        return LocalRepositorySync().batch_execute(req.action, req.repo_ids)
    except RepositorySyncRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/v1/repos/sync-action")
def run_local_repository_sync_action(req: RepositorySyncActionRequest):
    """逐一執行已確認的 fetch / fast-forward pull / staged commit / push。"""
    try:
        return LocalRepositorySync().execute(
            repo_id=req.repo_id,
            action=req.action,
            commit_message=req.commit_message,
        )
    except RepositorySyncRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/v1/repos/onboarding-report")
def get_repo_onboarding_report():
    """P4.3 對帳（唯讀）：未 git init 的資料夾、無 remote 的 repo、
    尚未 clone 的 GitHub repo。已 clone 與否只以 remote URL 比對；
    同名僅提示、不自動配對。"""
    from core.repo_onboarding import RepoOnboarding

    return RepoOnboarding().build_report()


@router.post("/api/v1/repos/onboarding-action")
def run_repo_onboarding_action(req: RepoOnboardingActionRequest):
    """執行單一、已確認的 onboarding 動作（init／attach remote／clone／
    create remote）。不覆寫非空目錄、不批次、永不 force、永不代為 push。"""
    from core.repo_onboarding import RepoOnboarding, RepoOnboardingRejected

    service = RepoOnboarding()
    try:
        if req.action == "init_folder":
            if not req.folder_id:
                raise RepoOnboardingRejected("init_folder 需要 folder_id")
            return service.init_folder(req.folder_id)
        if req.action == "attach_remote":
            if not (req.repo_id and req.github_full_name):
                raise RepoOnboardingRejected("attach_remote 需要 repo_id 與 github_full_name")
            return service.attach_remote(req.repo_id, req.github_full_name)
        if req.action == "clone_repo":
            if not (req.github_full_name and req.root_id):
                raise RepoOnboardingRejected("clone_repo 需要 github_full_name 與 root_id")
            return service.clone_repo(req.github_full_name, req.root_id)
        # create_remote
        if not req.repo_id:
            raise RepoOnboardingRejected("create_remote 需要 repo_id")
        return service.create_remote(req.repo_id, name=req.name, private=req.private)
    except RepoOnboardingRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/v1/github/status")
def get_github_status():
    """取得 GitHub 連線與認證狀態"""
    from integrations.github_client import get_github_client
    client = get_github_client()
    return client.test_connection()


@router.post("/api/v1/github/connect")
def connect_github(req: GitHubConnectRequest):
    """啟用 GitHub 認證連線 (支援本機 gh CLI 自動偵測或自訂 PAT)"""
    from integrations.github_client import get_github_client
    client = get_github_client()

    token_to_use = None
    if req.method == "token" and req.token:
        token_to_use = req.token.strip()
    elif req.method == "gh_cli":
        token_to_use = client.get_token()

    if not token_to_use:
        raise HTTPException(status_code=400, detail="未提供有效之 GitHub Token 且未偵測到 gh CLI 登入憑證")

    # 驗證 Token
    test_res = client.test_connection(token_override=token_to_use)
    if not test_res.get("connected"):
        raise HTTPException(status_code=401, detail=test_res.get("message", "Token 驗證失敗"))

    # 儲存至 config.yaml
    cfg = get_config()
    cfg.data["integrations"] = cfg.data.get("integrations", {})
    cfg.data["integrations"]["github"] = cfg.data["integrations"].get("github", {})
    cfg.data["integrations"]["github"]["enabled"] = True
    if req.method == "token":
        cfg.data["integrations"]["github"]["token"] = token_to_use
    else:
        cfg.data["integrations"]["github"]["token"] = ""  # 使用 gh CLI 動態讀取

    cfg.config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg.config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg.data, f, allow_unicode=True, sort_keys=False)

    # 立即執行一次同步
    sync_res = client.sync_all(max_repos=40)
    return {
        "status": "success",
        "message": f"GitHub 帳號 @{test_res.get('username')} 連線成功！",
        "auth": test_res,
        "sync": sync_res
    }


@router.post("/api/v1/github/disconnect")
def disconnect_github():
    """解除 GitHub 連線"""
    cfg = get_config()
    if "integrations" in cfg.data and "github" in cfg.data["integrations"]:
        cfg.data["integrations"]["github"]["enabled"] = False
        cfg.data["integrations"]["github"]["token"] = ""
        cfg.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cfg.config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg.data, f, allow_unicode=True, sort_keys=False)
    return {"status": "success", "message": "已解除 GitHub 整合連線"}


@router.post("/api/v1/github/sync")
def trigger_github_sync():
    """手動觸發即時同步所有 Public/Private 專案與 PR 狀態"""
    from integrations.github_client import get_github_client
    client = get_github_client()
    res = client.sync_all(max_repos=50)
    # 強制重整專案快取
    refresh_project_states(force=True)
    return res


@router.get("/api/v1/github/repos")
def list_github_repos():
    """取得所有已同步的 GitHub 遠端倉庫清單"""
    db = get_db()
    with db.session_scope() as session:
        repos = session.query(GitHubRepoState).order_by(GitHubRepoState.pushed_at.desc()).all()
        return [
            {
                "id": r.id,
                "name": r.repo_name,
                "full_name": r.full_name,
                "is_private": r.is_private,
                "html_url": r.html_url,
                "description": r.description,
                "default_branch": r.default_branch,
                "open_prs_count": r.open_prs_count,
                "open_issues_count": r.open_issues_count,
                "stars": r.stars_count,
                "pushed_at": r.pushed_at.strftime("%Y-%m-%d %H:%M") if r.pushed_at else None,
                "prs_summary": json.loads(r.metadata_json) if r.metadata_json else []
            }
            for r in repos
        ]


@router.get("/api/v1/github/prs")
def list_github_prs(state: Optional[str] = None):
    """取得所有活躍 PRs (包含 Open, Merged, CI 狀態)"""
    db = get_db()
    with db.session_scope() as session:
        query = session.query(GitHubPREvent)
        if state:
            query = query.filter_by(state=state)
        prs = query.order_by(GitHubPREvent.updated_at.desc()).limit(40).all()
        return [
            {
                "id": pr.id,
                "repo": pr.repo_name,
                "number": pr.pr_number,
                "title": pr.title,
                "state": pr.state,
                "is_draft": pr.is_draft,
                "author": pr.author,
                "html_url": pr.html_url,
                "branch_head": pr.branch_head,
                "branch_base": pr.branch_base,
                "ci_status": pr.ci_status,
                "review_state": pr.review_state,
                "created_at": pr.created_at.strftime("%Y-%m-%d %H:%M") if pr.created_at else None,
                "updated_at": pr.updated_at.strftime("%Y-%m-%d %H:%M") if pr.updated_at else None,
                "merged_at": pr.merged_at.strftime("%Y-%m-%d %H:%M") if pr.merged_at else None
            }
            for pr in prs
        ]
