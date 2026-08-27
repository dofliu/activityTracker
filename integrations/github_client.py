import os
import sys
import json
import logging
import subprocess
import requests
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path

from core.config import get_config
from core.database import get_db
from core.models import GitHubRepoState, GitHubPREvent, GitHubIssueEvent, ProjectState
from core.time_utils import get_local_now

logger = logging.getLogger("OmniContext.GitHubClient")

GITHUB_API_BASE = "https://api.github.com"


class GitHubClient:
    def __init__(self):
        self.cfg = get_config()
        self._token: Optional[str] = None
        self._user_cache: Optional[Dict[str, Any]] = None

    def get_token(self) -> Optional[str]:
        """取得有效的 GitHub Token (優先順序: config.yaml -> 環境變數 -> 本機 gh CLI)"""
        # 1. 檢查 config.yaml
        configured_token = self.cfg.get("integrations.github.token")
        if configured_token and configured_token.strip():
            return configured_token.strip()

        token_env = self.cfg.get("integrations.github.token_env", "GITHUB_TOKEN")
        env_token = os.environ.get(token_env) or os.environ.get("GH_TOKEN")
        if env_token and env_token.strip():
            return env_token.strip()

        # 2. 自動探測本機 GitHub CLI (gh auth token)
        try:
            res = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False
            )
            if res.returncode == 0 and res.stdout.strip():
                detected = res.stdout.strip()
                return detected
        except Exception:
            pass

        return None

    def _headers(self, token: Optional[str] = None) -> Dict[str, str]:
        tok = token or self.get_token()
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "OmniContext-ActivityTracker/1.0",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        return headers

    def test_connection(self, token_override: Optional[str] = None) -> Dict[str, Any]:
        """驗證 Token 有效性並取得使用者基本資料與 Rate Limit"""
        token = token_override or self.get_token()
        if not token:
            return {
                "connected": False,
                "message": "未檢測到 GitHub 認證 Token。請使用 GitHub CLI 登入或於設定中輸入 Token。"
            }

        url = f"{GITHUB_API_BASE}/user"
        try:
            res = requests.get(url, headers=self._headers(token), timeout=10)
            if res.status_code == 200:
                user_data = res.json()
                rate_limit = {
                    "limit": res.headers.get("x-ratelimit-limit"),
                    "remaining": res.headers.get("x-ratelimit-remaining"),
                    "reset": res.headers.get("x-ratelimit-reset")
                }
                scopes = res.headers.get("x-oauth-scopes", "")
                return {
                    "connected": True,
                    "username": user_data.get("login"),
                    "name": user_data.get("name"),
                    "avatar_url": user_data.get("avatar_url"),
                    "html_url": user_data.get("html_url"),
                    "public_repos": user_data.get("public_repos", 0),
                    "total_private_repos": user_data.get("total_private_repos", 0),
                    "scopes": [s.strip() for s in scopes.split(",") if s.strip()],
                    "rate_limit": rate_limit
                }
            elif res.status_code == 401:
                return {"connected": False, "message": "GitHub Token 無效或已過期 (401 Unauthorized)"}
            else:
                return {"connected": False, "message": f"GitHub API 錯誤: HTTP {res.status_code} - {res.text}"}
        except Exception as e:
            return {"connected": False, "message": f"連線至 GitHub 失敗: {str(e)}"}

    def fetch_all_repositories(self, limit: int = 100) -> List[Dict[str, Any]]:
        """讀取所有 Public 與 Private 倉庫 (包含個人擁有與組織協作)"""
        token = self.get_token()
        if not token:
            logger.warning("No GitHub token available for fetching repositories.")
            return []

        url = f"{GITHUB_API_BASE}/user/repos"
        params = {
            "visibility": "all",
            "affiliation": "owner,collaborator,organization_member",
            "sort": "pushed",
            "direction": "desc",
            "per_page": min(limit, 100)
        }

        all_repos = []
        try:
            while url and len(all_repos) < limit:
                res = requests.get(url, headers=self._headers(token), params=params, timeout=15)
                if not res.ok:
                    logger.error(f"Failed to fetch repos: {res.status_code} {res.text}")
                    break
                repos = res.json()
                if not isinstance(repos, list):
                    break
                all_repos.extend(repos)

                # 分頁連結解析
                link_header = res.headers.get("Link")
                url = None
                params = None  # 後續分頁 URL 已包含 query string
                if link_header:
                    for part in link_header.split(","):
                        if 'rel="next"' in part:
                            url = part.split(";")[0].strip("<> ")
                            break
        except Exception as e:
            logger.error(f"Exception while fetching GitHub repositories: {e}")

        return all_repos[:limit]

    def fetch_repo_prs(self, owner: str, repo: str, limit: int = 20) -> List[Dict[str, Any]]:
        """讀取指定倉庫的所有 Open PR 與近期 Merged/Closed PR"""
        token = self.get_token()
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls"
        params = {
            "state": "all",
            "sort": "updated",
            "direction": "desc",
            "per_page": limit
        }

        try:
            res = requests.get(url, headers=self._headers(token), params=params, timeout=12)
            if not res.ok:
                return []
            return res.json()
        except Exception as e:
            logger.warning(f"Failed to fetch PRs for {owner}/{repo}: {e}")
            return []

    def fetch_repo_issues(self, owner: str, repo: str, limit: int = 30) -> List[Dict[str, Any]]:
        """讀取指定倉庫的 Open Issues

        注意：GitHub 的 /issues 端點會把 Pull Request 一併回傳（PR 在 API 眼中也是 issue），
        必須用 pull_request 欄位過濾掉，否則 PR 會被重複計成 issue。
        """
        token = self.get_token()
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues"
        params = {
            "state": "open",
            "sort": "updated",
            "direction": "desc",
            "per_page": limit,
        }

        try:
            res = requests.get(url, headers=self._headers(token), params=params, timeout=12)
            if not res.ok:
                return []
            return [item for item in res.json() if "pull_request" not in item]
        except Exception as e:
            logger.warning(f"Failed to fetch issues for {owner}/{repo}: {e}")
            return []

    def fetch_pr_ci_status(self, owner: str, repo: str, head_sha: str) -> str:
        """取得 PR 最新 Commit 的 CI Check Runs 狀態"""
        token = self.get_token()
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits/{head_sha}/check-runs"
        try:
            res = requests.get(url, headers=self._headers(token), timeout=8)
            if res.ok:
                data = res.json()
                check_runs = data.get("check_runs", [])
                if not check_runs:
                    return "neutral"
                conclusions = [c.get("conclusion") for c in check_runs]
                if any(c == "failure" for c in conclusions):
                    return "failure"
                if any(c is None for c in conclusions):
                    return "pending"
                if all(c in ["success", "skipped", "neutral"] for c in conclusions):
                    return "success"
        except Exception:
            pass
        return "neutral"

    def fetch_pr_reviews(self, owner: str, repo: str, pr_number: int) -> str:
        """取得 PR 的最新審核狀態"""
        token = self.get_token()
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
        try:
            res = requests.get(url, headers=self._headers(token), timeout=8)
            if res.ok:
                reviews = res.json()
                if not reviews:
                    return "PENDING"
                # 取最後一筆明確狀態
                for r in reversed(reviews):
                    state = r.get("state")
                    if state in ["APPROVED", "CHANGES_REQUESTED", "COMMENTED"]:
                        return state
        except Exception:
            pass
        return "PENDING"

    def parse_iso_time(self, iso_str: Optional[str]) -> Optional[datetime]:
        """解析 GitHub ISO 8601 時間字串為本地 datetime"""
        if not iso_str:
            return None
        try:
            # 支援 2026-08-22T15:17:28Z
            clean = iso_str.replace("Z", "+00:00")
            dt_utc = datetime.fromisoformat(clean)
            # 轉換為本地時間
            local_tz = get_local_now().tzinfo
            return dt_utc.astimezone(local_tz).replace(tzinfo=None)
        except Exception:
            return None

    def sync_all(self, max_repos: int = 50) -> Dict[str, Any]:
        """全量同步 GitHub 所有倉庫狀態與活躍 PR 數據至本地 SQLite 資料庫"""
        auth_status = self.test_connection()
        if not auth_status.get("connected"):
            return {
                "status": "error",
                "message": auth_status.get("message", "GitHub 尚未連線")
            }

        db = get_db()
        repos = self.fetch_all_repositories(limit=max_repos)
        synced_repos_count = 0
        synced_prs_count = 0
        synced_issues_count = 0
        active_prs_list = []

        now = get_local_now()

        with db.session_scope() as session:
            for r in repos:
                repo_name = r.get("name")
                full_name = r.get("full_name")
                is_private = r.get("private", False)
                html_url = r.get("html_url")
                description = r.get("description")
                default_branch = r.get("default_branch", "main")
                open_issues = r.get("open_issues_count", 0)
                stars = r.get("stargazers_count", 0)
                forks = r.get("forks_count", 0)
                pushed_at = self.parse_iso_time(r.get("pushed_at"))

                # 取得或新增 repo state
                repo_record = session.query(GitHubRepoState).filter_by(full_name=full_name).first()
                if not repo_record:
                    repo_record = GitHubRepoState(
                        repo_name=repo_name,
                        full_name=full_name,
                        is_private=is_private,
                        html_url=html_url,
                        description=description,
                        default_branch=default_branch,
                        stars_count=stars,
                        forks_count=forks,
                        pushed_at=pushed_at,
                        updated_at=now
                    )
                    session.add(repo_record)
                else:
                    repo_record.repo_name = repo_name
                    repo_record.is_private = is_private
                    repo_record.html_url = html_url
                    repo_record.description = description
                    repo_record.default_branch = default_branch
                    repo_record.stars_count = stars
                    repo_record.forks_count = forks
                    repo_record.pushed_at = pushed_at
                    repo_record.updated_at = now

                # 若倉庫在近期有活動（近 90 天內有 push 或有 open issues/PRs），撈取 PR 細節
                owner = full_name.split("/")[0]
                prs_data = self.fetch_repo_prs(owner, repo_name, limit=10)
                
                open_prs_count = 0
                repo_pr_summaries = []

                for pr in prs_data:
                    pr_num = pr.get("number")
                    pr_title = pr.get("title")
                    pr_state = "merged" if pr.get("merged_at") else pr.get("state")
                    is_draft = pr.get("draft", False)
                    author = pr.get("user", {}).get("login")
                    pr_url = pr.get("html_url")
                    b_head = pr.get("head", {}).get("ref")
                    b_base = pr.get("base", {}).get("ref")
                    head_sha = pr.get("head", {}).get("sha", "")
                    created_at = self.parse_iso_time(pr.get("created_at"))
                    updated_at = self.parse_iso_time(pr.get("updated_at"))
                    merged_at = self.parse_iso_time(pr.get("merged_at"))

                    if pr_state == "open":
                        open_prs_count += 1

                    # 取得 CI 狀態與審核狀態
                    ci_status = self.fetch_pr_ci_status(owner, repo_name, head_sha) if (pr_state == "open" and head_sha) else "neutral"
                    review_state = self.fetch_pr_reviews(owner, repo_name, pr_num) if pr_state == "open" else "PENDING"

                    pr_record = (
                        session.query(GitHubPREvent)
                        .filter_by(repo_name=repo_name, pr_number=pr_num)
                        .first()
                    )
                    if not pr_record:
                        pr_record = GitHubPREvent(
                            repo_name=repo_name,
                            pr_number=pr_num,
                            title=pr_title,
                            state=pr_state,
                            is_draft=is_draft,
                            author=author,
                            html_url=pr_url,
                            branch_head=b_head,
                            branch_base=b_base,
                            ci_status=ci_status,
                            review_state=review_state,
                            created_at=created_at,
                            updated_at=updated_at,
                            merged_at=merged_at
                        )
                        session.add(pr_record)
                    else:
                        pr_record.title = pr_title
                        pr_record.state = pr_state
                        pr_record.is_draft = is_draft
                        pr_record.author = author
                        pr_record.html_url = pr_url
                        pr_record.branch_head = b_head
                        pr_record.branch_base = b_base
                        pr_record.ci_status = ci_status
                        pr_record.review_state = review_state
                        pr_record.updated_at = updated_at
                        pr_record.merged_at = merged_at

                    synced_prs_count += 1
                    pr_summary_dict = {
                        "number": pr_num,
                        "title": pr_title,
                        "state": pr_state,
                        "is_draft": is_draft,
                        "author": author,
                        "url": pr_url,
                        "branch": f"{b_head} -> {b_base}",
                        "ci_status": ci_status,
                        "review_state": review_state,
                        "merged_at": merged_at.strftime("%Y-%m-%d %H:%M") if merged_at else None,
                        "updated_at": updated_at.strftime("%Y-%m-%d %H:%M") if updated_at else None
                    }
                    repo_pr_summaries.append(pr_summary_dict)
                    if pr_state == "open" or (merged_at and merged_at >= now - timedelta(days=7)):
                        active_prs_list.append({**pr_summary_dict, "repo": repo_name, "full_name": full_name})

                # Issues：只在該 repo 確實有 issue 時才打 API，避免 57 個 repo 全部多一次請求。
                # open_issues 是 GitHub 的合計值（含 PR），扣掉 open PR 才是真正的 issue 數。
                estimated_issue_count = max(0, int(open_issues or 0) - open_prs_count)
                if estimated_issue_count > 0:
                    issues_data = self.fetch_repo_issues(owner, repo_name, limit=30)
                    for issue in issues_data:
                        issue_num = issue.get("number")
                        if issue_num is None:
                            continue
                        assignee_login = (issue.get("assignee") or {}).get("login")
                        labels = [
                            label.get("name")
                            for label in (issue.get("labels") or [])
                            if isinstance(label, dict) and label.get("name")
                        ]
                        issue_record = (
                            session.query(GitHubIssueEvent)
                            .filter_by(repo_name=repo_name, issue_number=issue_num)
                            .first()
                        )
                        payload = dict(
                            title=issue.get("title") or "",
                            state=issue.get("state") or "open",
                            author=(issue.get("user") or {}).get("login"),
                            assignee=assignee_login,
                            html_url=issue.get("html_url") or "",
                            labels_json=json.dumps(labels, ensure_ascii=False) if labels else None,
                            comments_count=int(issue.get("comments") or 0),
                            updated_at=self.parse_iso_time(issue.get("updated_at")),
                            closed_at=self.parse_iso_time(issue.get("closed_at")),
                        )
                        if issue_record is None:
                            session.add(GitHubIssueEvent(
                                repo_name=repo_name,
                                issue_number=issue_num,
                                created_at=self.parse_iso_time(issue.get("created_at")),
                                **payload,
                            ))
                        else:
                            for field, value in payload.items():
                                setattr(issue_record, field, value)
                        synced_issues_count += 1

                    # 本輪回傳的 open issue 之外，其餘先前記為 open 的視為已關閉
                    seen_numbers = {i.get("number") for i in issues_data if i.get("number") is not None}
                    stale_open = (
                        session.query(GitHubIssueEvent)
                        .filter(
                            GitHubIssueEvent.repo_name == repo_name,
                            GitHubIssueEvent.state == "open",
                        )
                        .all()
                    )
                    for record in stale_open:
                        if record.issue_number not in seen_numbers:
                            record.state = "closed"
                            record.closed_at = record.closed_at or now

                repo_record.open_prs_count = open_prs_count
                repo_record.open_issues_count = open_issues
                repo_record.metadata_json = json.dumps(repo_pr_summaries, ensure_ascii=False)
                synced_repos_count += 1

        logger.info(
            f"GitHub sync completed: {synced_repos_count} repos, "
            f"{synced_prs_count} PRs, {synced_issues_count} issues synced."
        )
        return {
            "status": "success",
            "username": auth_status.get("username"),
            "synced_repos_count": synced_repos_count,
            "synced_prs_count": synced_prs_count,
            "synced_issues_count": synced_issues_count,
            "active_prs": active_prs_list
        }


# 全域單例
_github_client = None


def get_github_client() -> GitHubClient:
    global _github_client
    if _github_client is None:
        _github_client = GitHubClient()
    return _github_client
