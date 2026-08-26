"""
core/handoff_engine.py — OmniContext Project Context Handoff Engine (P3-1)

負責從資料庫中聚合專案狀態、未結事項 (Open Loops)、Git Commits、檔案變更
與真實 AI 問答脈絡，生成可直接注入任何 AI Agent (Claude Code, Codex, Antigravity, ChatGPT) 的接續 Prompt。
"""

import os
import sys
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List
from pathlib import Path
from sqlalchemy import desc

from core.database import get_db
from core.models import ProjectState, OpenLoop, AIPromptEvent, GitActivityEvent, FileActivityEvent, GitHubRepoState, GitHubPREvent
from core.project_paths import configured_self_project_path, find_configured_project_path
from core.time_utils import get_local_now
from core.project_engine import resolve_project_from_path

logger = logging.getLogger("OmniContext.HandoffEngine")


def build_project_handoff(
    project_key: str,
    turns_limit: int = 5,
    files_limit: int = 8,
    commits_limit: int = 5
) -> Dict[str, Any]:
    """
    從資料庫中聚合指定專案的完整接續上下文數據。
    """
    db = get_db()
    now = get_local_now()

    with db.session_scope() as session:
        # 1. 查詢專案狀態
        proj = session.query(ProjectState).filter(
            (ProjectState.project_key == project_key) |
            (ProjectState.display_name == project_key)
        ).first()

        if not proj:
            # 若無記錄，建立基本結構
            display_name = project_key
            category = "General"
            status = "unknown"
            idle_days = 0
            last_activity = now.strftime("%Y-%m-%d %H:%M")
            last_summary = "無歷史紀錄"
        else:
            display_name = proj.display_name
            category = proj.category or "General"
            status = proj.status or "active"
            idle_days = (now - proj.last_activity_at).days if proj.last_activity_at else 0
            last_activity = proj.last_activity_at.strftime("%Y-%m-%d %H:%M") if proj.last_activity_at else ""
            last_summary = proj.last_action_summary or ""

        # 2. 查詢未結事項 (Open Loops)
        loops_db = session.query(OpenLoop).filter(
            (OpenLoop.project_key == project_key) |
            (OpenLoop.project_key == display_name),
            OpenLoop.status == "open"
        ).order_by(desc(OpenLoop.last_seen_at), desc(OpenLoop.created_at)).all()

        open_loops = [
            {
                "id": l.id,
                "title": l.title,
                "status": l.status,
                "created_at": l.created_at.strftime("%Y-%m-%d %H:%M") if l.created_at else "",
                "last_seen_at": l.last_seen_at.strftime("%Y-%m-%d %H:%M") if l.last_seen_at else "",
            }
            for l in loops_db
        ]

        # 3. 查詢 GitHub 倉庫與 PR 狀態
        gh_repo = session.query(GitHubRepoState).filter(
            (GitHubRepoState.repo_name == project_key) |
            (GitHubRepoState.repo_name == display_name)
        ).first()

        github_info = None
        if gh_repo:
            recent_prs = session.query(GitHubPREvent).filter_by(
                repo_name=gh_repo.repo_name
            ).order_by(desc(GitHubPREvent.updated_at)).limit(3).all()

            github_info = {
                "html_url": gh_repo.html_url,
                "is_private": gh_repo.is_private,
                "open_prs_count": gh_repo.open_prs_count,
                "prs": [
                    {
                        "number": pr.pr_number,
                        "title": pr.title,
                        "state": pr.state,
                        "html_url": pr.html_url,
                        "branch": f"{pr.branch_head} -> {pr.branch_base}"
                    }
                    for pr in recent_prs
                ]
            }

        # 4. 解析本機目錄 (Local Path)
        local_path = None
        latest_f = session.query(FileActivityEvent).filter(
            (FileActivityEvent.project_name == project_key) |
            (FileActivityEvent.project_name == display_name)
        ).order_by(desc(FileActivityEvent.timestamp)).first()

        if latest_f and latest_f.file_path:
            fp = Path(latest_f.file_path).resolve()
            curr = fp.parent if fp.is_file() else fp
            while curr != curr.parent:
                if curr.name.lower() == project_key.lower():
                    local_path = str(curr)
                    break
                curr = curr.parent
            if not local_path and fp.parent.exists():
                local_path = str(fp.parent)

        latest_ai = session.query(AIPromptEvent).filter(
            AIPromptEvent.turn_key.isnot(None),
            (AIPromptEvent.project_tag == project_key) |
            (AIPromptEvent.project_tag == display_name) |
            (AIPromptEvent.cwd.like(f"%{project_key}%"))
        ).order_by(desc(AIPromptEvent.timestamp)).first()

        if not local_path and latest_ai and latest_ai.cwd and os.path.exists(latest_ai.cwd):
            local_path = str(Path(latest_ai.cwd).resolve())

        if not local_path:
            configured_path = find_configured_project_path(project_key)
            if configured_path:
                local_path = str(configured_path)

        if not local_path and project_key == "Agent Development":
            local_path = str(
                configured_self_project_path()
                or Path(__file__).resolve().parents[1]
            )

        # 5. 查詢最近 Git Commits
        commits_db = session.query(GitActivityEvent).filter(
            (GitActivityEvent.repo_name == project_key) |
            (GitActivityEvent.repo_name == display_name)
        ).order_by(desc(GitActivityEvent.timestamp)).limit(commits_limit).all()

        recent_commits = [
            {
                "hash": c.commit_hash[:8],
                "branch": c.branch,
                "message": c.message.strip().split("\n")[0] if c.message else "",
                "time": c.timestamp.strftime("%Y-%m-%d %H:%M") if c.timestamp else "",
                "changes": f"+{c.insertions}/-{c.deletions}" if (c.insertions or c.deletions) else ""
            }
            for c in commits_db
        ]

        # 6. 查詢最近異動檔案
        files_db = session.query(FileActivityEvent).filter(
            (FileActivityEvent.project_name == project_key) |
            (FileActivityEvent.project_name == display_name)
        ).order_by(desc(FileActivityEvent.timestamp)).all()

        recent_files = []
        seen_files = set()
        for f in files_db:
            if f.file_path and f.file_path not in seen_files:
                seen_files.add(f.file_path)
                p_obj = Path(f.file_path)
                recent_files.append({
                    "name": p_obj.name,
                    "path": f.file_path,
                    "action": f.action,
                    "time": f.timestamp.strftime("%Y-%m-%d %H:%M") if f.timestamp else "",
                    "diff": f.diff_summary or ""
                })
                if len(recent_files) >= files_limit:
                    break

        # 7. 查詢最近真實 AI 對話問答
        ai_events_db = session.query(AIPromptEvent).filter(
            AIPromptEvent.turn_key.isnot(None),
            (AIPromptEvent.project_tag == project_key) |
            (AIPromptEvent.project_tag == display_name) |
            (AIPromptEvent.cwd.like(f"%{project_key}%"))
        ).order_by(desc(AIPromptEvent.timestamp)).all()

        recent_ai_turns = []
        for ev in ai_events_db:
            prompt_clean = (ev.prompt_text or "").strip()
            if not prompt_clean or len(prompt_clean) < 3:
                continue

            resp_clean = (ev.response_text or "").strip()
            if resp_clean.startswith("[") and resp_clean.endswith("]"):
                resp_clean = ""

            response_status = ev.response_status or "legacy_unverified"
            if response_status != "final_candidate":
                resp_clean = ""

            recent_ai_turns.append({
                "platform": ev.platform,
                "time": ev.timestamp.strftime("%Y-%m-%d %H:%M") if ev.timestamp else "",
                "prompt": prompt_clean,
                "response": resp_clean[:500] if resp_clean else "（無可信結論候選）",
                "response_status": response_status,
                "source_path": ev.source_path,
                "source_position": ev.source_position,
            })
            if len(recent_ai_turns) >= turns_limit:
                break

        # 8. 回傳聚合數據
        return {
            "project_key": project_key,
            "display_name": display_name,
            "category": category,
            "status": status,
            "idle_days": idle_days,
            "last_activity_at": last_activity,
            "last_action_summary": last_summary,
            "local_path": local_path,
            "github": github_info,
            "open_loops": open_loops,
            "recent_commits": recent_commits,
            "recent_files": recent_files,
            "recent_ai_turns": recent_ai_turns
        }


def format_handoff_markdown(data: Dict[str, Any]) -> str:
    """
    將聚合數據排版為乾淨俐落、可直接貼入任何 AI 視窗的結構化 Markdown 接續 Prompt。
    """
    p_name = data.get("display_name") or data.get("project_key")
    cat = data.get("category", "General")
    status = data.get("status", "active")
    idle_days = data.get("idle_days", 0)
    local_path = data.get("local_path") or "尚未定位"
    last_act = data.get("last_activity_at") or "無"
    last_summary = data.get("last_action_summary") or "無紀錄"

    # GitHub 區塊
    gh = data.get("github")
    gh_line = ""
    if gh:
        prs = gh.get("prs", [])
        pr_str = ""
        if prs:
            pr_items = [f"PR #{p['number']} ({p['state'].upper()}): {p['title']}" for p in prs]
            pr_str = f" · 近期 PR: [{'; '.join(pr_items)}]"
        gh_line = f"\n- **GitHub 倉庫**：{gh.get('html_url')} ({'Private' if gh.get('is_private') else 'Public'}){pr_str}"

    # 未結事項 (Open Loops)
    loops = data.get("open_loops", [])
    if loops:
        loops_text = "\n".join([f"- [ ] **[優先跟進]** {l['title']}" for l in loops])
    else:
        loops_text = "- (目前無未結事項)"

    # 最近 Commits
    commits = data.get("recent_commits", [])
    if commits:
        commits_text = "\n".join([
            f"- `{c['hash']}` ({c['time']}) [{c['branch']}]: {c['message']} {c['changes']}"
            for c in commits
        ])
    else:
        commits_text = "- (近期無 Git Commit 紀錄)"

    # 異動檔案清單
    files = data.get("recent_files", [])
    if files:
        files_text = "\n".join([
            f"- **{f['name']}** (`{f['action']}` · {f['time']}) {f['diff']} — `{f['path']}`"
            for f in files
        ])
    else:
        files_text = "- (近期無檔案異動紀錄)"

    # AI 對話與決策脈絡
    ai_turns = data.get("recent_ai_turns", [])
    if ai_turns:
        turns_lines = []
        for i, turn in enumerate(reversed(ai_turns), start=1):
            plat = (turn.get("platform") or "AI").upper()
            t_time = turn.get("time", "")
            prompt_snip = turn.get("prompt", "").replace("\n", " ")
            if len(prompt_snip) > 160:
                prompt_snip = prompt_snip[:160] + "..."
            resp_snip = turn.get("response", "").replace("\n", " ")
            if len(resp_snip) > 220:
                resp_snip = resp_snip[:220] + "..."
            trust = turn.get("response_status", "legacy_unverified")
            source_path = turn.get("source_path")
            source_position = turn.get("source_position")
            source = ""
            if source_path:
                source = f" · 來源 `{source_path}`"
                if source_position is not None:
                    source += f":{source_position}"
            
            turns_lines.append(
                f"**回合 {i} [{plat} · {t_time}]**\n"
                f"• 提問：{prompt_snip}\n"
                f"• 回應可信狀態：`{trust}`{source}\n"
                f"• 結論候選：{resp_snip}"
            )
        ai_text = "\n\n".join(turns_lines)
    else:
        ai_text = "(近期無歷史 AI 對話脈絡)"

    md = f"""# 專案接續上下文 (Project Context Handoff) — {p_name}

> 這是由 **OmniContext** 自動提煉的專案全景接續記憶。包含了專案當前狀態、未結事項、近期代碼提交與歷史決策脈絡，供您直接無縫接續開工。

---

## 📌 1. 專案基本定位
- **專案名稱**：`{p_name}`
- **專案類別**：`{cat}`
- **運行狀態**：`{status}` (距今閒置 {idle_days} 天)
- **本機目錄**：`{local_path}`{gh_line}

---

## 🎯 2. 上次做到哪 (Last Activity)
- **最後活躍時間**：{last_act}
- **最後動作摘要**：{last_summary}

---

## ⚠️ 3. 待跟進未結事項 (Open Loops)
{loops_text}

---

## 💻 4. 近期程式碼與檔案異動 (Recent Code & File Changes)
### 最近 Commits
{commits_text}

### 關鍵檔案異動
{files_text}

---

## 🤖 5. 近期關鍵決策與 AI 探討脈絡 (Recent Decisions & Thoughts)
{ai_text}

---

## 🚀 6. 本輪 AI 接續開工指引
請仔細閱讀以上專案背景、上次做到哪與未結事項。
確認理解後，請直接回覆簡要的下一步行動方案，並準備開始協助進行開發與寫作！
"""
    return md.strip() + "\n"
