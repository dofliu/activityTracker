import os
from datetime import datetime, date, time, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List, Set
import json
import logging

from core.database import get_db
from core.models import AIPromptEvent, FileActivityEvent, GitActivityEvent, WindowEvent, DailySummary, ProjectState, OpenLoop, GitHubPREvent
from core.config import get_config
from core.time_utils import get_local_now
from core.project_engine import get_active_projects_list, get_open_loops_list, refresh_project_states, extract_and_save_open_loops_from_summary
from .prompt_templates import RANGE_PROJECT_SYNTHESIS_SYSTEM, RANGE_PROJECT_SYNTHESIS_USER
from .llm_client import LLMClient

logger = logging.getLogger("OmniContext.Aggregator")


def fetch_events_in_range(start_dt: datetime, end_dt: datetime) -> Dict[str, Any]:
    """撈取指定時間範圍內的所有事件"""
    db = get_db()
    with db.session_scope() as session:
        ai_events = (
            session.query(AIPromptEvent)
            .filter(
                AIPromptEvent.timestamp >= start_dt,
                AIPromptEvent.timestamp <= end_dt,
                AIPromptEvent.turn_key.isnot(None),
            )
            .order_by(AIPromptEvent.timestamp.asc())
            .all()
        )
        file_events = (
            session.query(FileActivityEvent)
            .filter(FileActivityEvent.timestamp >= start_dt, FileActivityEvent.timestamp <= end_dt)
            .order_by(FileActivityEvent.timestamp.asc())
            .all()
        )
        git_events = (
            session.query(GitActivityEvent)
            .filter(GitActivityEvent.timestamp >= start_dt, GitActivityEvent.timestamp <= end_dt)
            .order_by(GitActivityEvent.timestamp.asc())
            .all()
        )
        window_events = (
            session.query(WindowEvent)
            .filter(WindowEvent.start_time >= start_dt, WindowEvent.start_time <= end_dt)
            .order_by(WindowEvent.start_time.asc())
            .all()
        )
        pr_events = (
            session.query(GitHubPREvent)
            .filter(
                ((GitHubPREvent.merged_at >= start_dt) & (GitHubPREvent.merged_at <= end_dt)) |
                ((GitHubPREvent.updated_at >= start_dt) & (GitHubPREvent.updated_at <= end_dt))
            )
            .order_by(GitHubPREvent.updated_at.asc())
            .all()
        )

        return {
            "ai_events": [
                {
                    "id": e.id,
                    "time": e.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "platform": e.platform,
                    "prompt": e.prompt_text,
                    "response": e.response_text
                    if e.response_status == "final_candidate" and e.response_text
                    else "",
                    "response_status": e.response_status or "legacy_unverified",
                    "source_path": e.source_path or "",
                    "source_position": e.source_position,
                    "tag": e.project_tag or ""
                }
                for e in ai_events
            ],
            "file_events": [
                {
                    "id": e.id,
                    "time": e.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "file_name": e.file_name,
                    "file_path": e.file_path,
                    "file_type": e.file_type,
                    "action": e.action,
                    "diff": e.diff_summary or "",
                    "project": e.project_name or ""
                }
                for e in file_events
            ],
            "git_events": [
                {
                    "id": e.id,
                    "time": e.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "repo": e.repo_name,
                    "branch": e.branch,
                    "commit_hash": e.commit_hash,
                    "message": e.message,
                    "insertions": e.insertions,
                    "deletions": e.deletions
                }
                for e in git_events
            ],
            "pr_events": [
                {
                    "id": p.id,
                    "repo": p.repo_name,
                    "number": p.pr_number,
                    "title": p.title,
                    "state": p.state,
                    "branch": f"{p.branch_head} -> {p.branch_base}",
                    "ci_status": p.ci_status,
                    "review_state": p.review_state,
                    "merged_at": p.merged_at.strftime("%Y-%m-%d %H:%M") if p.merged_at else None,
                    "updated_at": p.updated_at.strftime("%Y-%m-%d %H:%M") if p.updated_at else None
                }
                for p in pr_events
            ],
            "window_events": [
                {
                    "id": e.id,
                    "time": e.start_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "app": e.app_name,
                    "title": e.window_title,
                    "duration_sec": e.duration_seconds,
                    "category": e.category
                }
                for e in window_events
            ]
        }


def format_context_for_prompt(day_data: Dict[str, Any], time_range_str: str) -> str:
    """將資料庫事件轉換為適合 LLM 閱讀的專案中心格式 (嚴格限於該區間內真實推進的專案)"""
    
    # 找出區間內真正有活動的專案名稱集合
    active_project_names: Set[str] = set()
    for g in day_data["git_events"]:
        if g.get("repo"): active_project_names.add(g["repo"])
    for p in day_data.get("pr_events", []):
        if p.get("repo"): active_project_names.add(p["repo"])
    for f in day_data["file_events"]:
        if f.get("project"): active_project_names.add(f["project"])
    for a in day_data["ai_events"]:
        if a.get("tag"): active_project_names.add(a["tag"])

    # 1. Active Projects (區間內有活動的專案)
    all_projects = get_active_projects_list()
    matched_projects = [p for p in all_projects if p["display_name"] in active_project_names or p["project_key"] in active_project_names]

    proj_lines = []
    for p in matched_projects:
        proj_lines.append(f"- **{p['display_name']}** [{p['category']}]: 最新動態 `{p['last_action_summary']}`")
    
    if not proj_lines and active_project_names:
        for name in sorted(active_project_names):
            proj_lines.append(f"- **{name}**")

    proj_text = "\n".join(proj_lines) if proj_lines else "（該時段無專案更新）"

    # 2. Open Loops
    open_loops = get_open_loops_list()[:8]
    loop_lines = []
    for ol in open_loops:
        loop_lines.append(f"- [{ol['project_key']}] {ol['title']} (建立於 {ol['created_at']})")
    loop_text = "\n".join(loop_lines) if loop_lines else "（目前無待辦事項）"

    # 3. AI Events (嚴格排除佔位字串，僅注入真實結論)
    ai_lines = []
    for item in day_data["ai_events"]:
        tag_info = f" [{item['tag']}]" if item['tag'] else ""
        resp = (item.get('response') or "").strip()
        is_placeholder = resp.startswith("[Executed") or resp.startswith("[Codex CLI") or resp.startswith("<")
        resp_snippet = f"\n  -> AI 回應結論: {resp[:250]}..." if resp and len(resp) > 10 and not is_placeholder else ""
        ai_lines.append(f"- [{item['time']}] [{item['platform'].upper()}]{tag_info} 問: {item['prompt']}{resp_snippet}")
    ai_text = "\n".join(ai_lines) if ai_lines else "（該時段無 AI 互動紀錄）"

    # 4. File Events
    file_lines = []
    for item in day_data["file_events"]:
        diff_str = f" ({item['diff']})" if item['diff'] else ""
        file_lines.append(f"- [{item['time']}] [{item['action'].upper()}] {item['file_name']} [{item['file_type']}]{diff_str}")
    file_text = "\n".join(file_lines) if file_lines else "（該時段無檔案異動紀錄）"

    # 5. Git Events & PRs
    git_lines = []
    for item in day_data["git_events"]:
        git_lines.append(f"- [{item['time']}] [{item['repo']}@{item['branch']}] Commit: {item['message']} (+{item['insertions']}/-{item['deletions']})")
    for pr in day_data.get("pr_events", []):
        state_str = "已合併 (Merged)" if pr["state"] == "merged" else ("開啟中 (Open)" if pr["state"] == "open" else pr["state"])
        ci_str = f" [CI: {pr['ci_status']}]" if pr.get("ci_status") != "neutral" else ""
        git_lines.append(f"- [GitHub PR] [{pr['repo']}] PR #{pr['number']}: {pr['title']} ({state_str}){ci_str} 分支: {pr['branch']}")
    git_text = "\n".join(git_lines) if git_lines else "（該時段無代碼提交或 PR 紀錄）"


    # 6. Window Events (過濾無效/Idle 紀錄)
    app_durations: Dict[str, float] = {}
    for item in day_data["window_events"]:
        app = item["app"]
        if app and app.lower() not in ("idle", "unknown", "none"):
            app_durations[app] = app_durations.get(app, 0.0) + item["duration_sec"]

    window_lines = [f"- {app}: 約 {int(sec // 60)} 分鐘" for app, sec in sorted(app_durations.items(), key=lambda x: x[1], reverse=True)[:8] if sec >= 60]
    window_text = "\n".join(window_lines) if window_lines else "（該時段無有效視窗焦點紀錄 / 服務於背景執行中）"

    return RANGE_PROJECT_SYNTHESIS_USER.format(
        time_range_str=time_range_str,
        active_projects_text=proj_text,
        open_loops_text=loop_text,
        ai_interactions_text=ai_text,
        file_activities_text=file_text,
        git_activities_text=git_text,
        window_activities_text=window_text
    )


def save_summary_to_file(label_str: str, markdown_content: str) -> Path:
    """將生成的 Markdown 報告存檔於 reports/ 與 Obsidian"""
    cfg = get_config()
    reports_dir_str = cfg.get("exporters.reports_dir", "reports")
    reports_dir = Path(reports_dir_str)
    if not reports_dir.is_absolute():
        root_dir = Path(__file__).parent.parent
        reports_dir = root_dir / reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)

    # 檔名清理
    clean_label = label_str.replace(" ", "").replace("~", "_to_")
    file_path = reports_dir / f"Daily_Summary_{clean_label}.md"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(markdown_content)

    if cfg.get("exporters.obsidian.enabled", False):
        obsidian_dir = Path(cfg.get("exporters.obsidian.vault_daily_notes_dir", ""))
        if obsidian_dir.exists():
            obsidian_file = obsidian_dir / f"{clean_label}.md"
            with open(obsidian_file, "w", encoding="utf-8") as f:
                f.write(markdown_content)
            logger.info(f"Synced report to Obsidian: {obsidian_file}")

    return file_path


def generate_periodic_checkpoint(hours: int = 2) -> Dict[str, Any]:
    cfg = get_config()
    checkpoints_dir_str = cfg.get("exporters.checkpoints_dir", "logs/checkpoints")
    cp_dir = Path(checkpoints_dir_str)
    if not cp_dir.is_absolute():
        root_dir = Path(__file__).parent.parent
        cp_dir = root_dir / cp_dir
    cp_dir.mkdir(parents=True, exist_ok=True)

    now = get_local_now()
    start_dt = now - timedelta(hours=hours)
    data = fetch_events_in_range(start_dt, now)

    time_str = now.strftime("%Y%m%d_%H%M")
    timestamp_human = now.strftime("%Y-%m-%d %H:%M")
    start_human = start_dt.strftime("%H:%M")

    md_lines = [
        f"# ⏱️ OmniContext 活動快照日誌 ({timestamp_human})",
        f"> 統計時段：{start_human} ~ {now.strftime('%H:%M')} (過去 {hours} 小時)",
        "",
        "## 🤖 AI 提問與對話紀錄",
    ]
    if data["ai_events"]:
        for e in data["ai_events"]:
            tag_str = f" [{e['tag']}]" if e['tag'] else ""
            md_lines.append(f"- **[{e['time'].split(' ')[1]}] [{e['platform'].upper()}]{tag_str}** {e['prompt']}")
            if e['response'] and len(e['response']) > 10:
                md_lines.append(f"  > 💡 *摘要*: {e['response'][:180]}...")
    else:
        md_lines.append("- *(此時段無 AI 互動)*")

    md_lines.extend([
        "",
        "## 📁 論文與檔案異動",
    ])
    if data["file_events"]:
        for f in data["file_events"]:
            md_lines.append(f"- **[{f['time'].split(' ')[1]}] [{f['action'].upper()}]** `{f['file_name']}` ({f['diff'] or f['file_type']})")
    else:
        md_lines.append("- *(此時段無檔案異動)*")

    md_lines.extend([
        "",
        "## 💻 程式碼 Commits",
    ])
    if data["git_events"]:
        for g in data["git_events"]:
            md_lines.append(f"- **[{g['time'].split(' ')[1]}] [{g['repo']}@{g['branch']}]** {g['message']} (+{g['insertions']}/-{g['deletions']})")
    else:
        md_lines.append("- *(此時段無程式碼提交)*")

    log_content = "\n".join(md_lines)
    file_path = cp_dir / f"Checkpoint_{time_str}.md"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(log_content)

    logger.info(f"Generated periodic checkpoint log: {file_path}")
    return {
        "status": "success",
        "file_name": file_path.name,
        "file_path": str(file_path),
        "timestamp": timestamp_human,
        "content": log_content
    }


def list_periodic_checkpoints() -> List[Dict[str, Any]]:
    cfg = get_config()
    checkpoints_dir_str = cfg.get("exporters.checkpoints_dir", "logs/checkpoints")
    cp_dir = Path(checkpoints_dir_str)
    if not cp_dir.is_absolute():
        root_dir = Path(__file__).parent.parent
        cp_dir = root_dir / cp_dir

    if not cp_dir.exists():
        return []

    results = []
    for p in sorted(cp_dir.glob("Checkpoint_*.md"), reverse=True):
        try:
            results.append({
                "file_name": p.name,
                "file_path": str(p),
                "created_at": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                "size_bytes": p.stat().st_size
            })
        except Exception:
            pass
    return results


def generate_summary_pipeline(
    start_date_str: Optional[str] = None,
    end_date_str: Optional[str] = None,
    provider_override: Optional[str] = None,
    force_refresh: bool = False
) -> Dict[str, Any]:
    """支援單日或自訂任意日期區間 (start_date ~ end_date) 的全景總結生成管道"""
    now = get_local_now()

    if not start_date_str and not end_date_str:
        start_date = now.date()
        end_date = now.date()
    elif start_date_str and not end_date_str:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = start_date
    elif end_date_str and not start_date_str:
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        start_date = end_date
    else:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()

    if start_date > end_date:
        start_date, end_date = end_date, start_date

    is_single_day = (start_date == end_date)
    range_label = start_date.isoformat() if is_single_day else f"{start_date.isoformat()} ~ {end_date.isoformat()}"

    db = get_db()

    with db.session_scope() as session:
        existing = session.query(DailySummary).filter_by(date_str=range_label).first()
        if existing and not force_refresh:
            logger.info(f"Summary for {range_label} already exists. Returning cached.")
            return {
                "status": "cached",
                "date_str": range_label,
                "markdown": existing.raw_markdown
            }

    # 1. 撈取區間資料
    start_dt = datetime.combine(start_date, time.min)
    end_dt = datetime.combine(end_date, time.max)
    range_data = fetch_events_in_range(start_dt, end_dt)

    # 2. 構建 Prompt
    user_prompt = format_context_for_prompt(range_data, range_label)

    # 3. 調用 LLM
    client = LLMClient(provider=provider_override)
    markdown_result = client.generate(
        system_prompt=RANGE_PROJECT_SYNTHESIS_SYSTEM,
        user_prompt=user_prompt
    )

    # 4. 存檔至檔案
    report_file = save_summary_to_file(range_label, markdown_result)

    # 5. 存檔至資料庫
    cfg = get_config()
    provider_name = provider_override or cfg.get("synthesizer.provider", "gemini")
    model_name = cfg.get(f"synthesizer.{provider_name}.model", "default")

    with db.session_scope() as session:
        summary_record = session.query(DailySummary).filter_by(date_str=range_label).first()
        if not summary_record:
            summary_record = DailySummary(
                date_str=range_label,
                llm_provider=provider_name,
                model_name=str(model_name),
                raw_markdown=markdown_result,
                created_at=get_local_now()
            )
            session.add(summary_record)
        else:
            summary_record.llm_provider = provider_name
            summary_record.model_name = str(model_name)
            summary_record.raw_markdown = markdown_result
            summary_record.created_at = get_local_now()

    # 6. 自動從報告中萃取未結事項 (Open Loops)
    try:
        loop_ids = extract_and_save_open_loops_from_summary(markdown_result, range_label)
        if loop_ids:
            logger.info(f"Extracted and saved {len(loop_ids)} Open Loops from summary ({range_label}).")
    except Exception as e:
        logger.warning(f"Error extracting open loops: {e}")

    return {
        "status": "generated",
        "date_str": range_label,
        "report_path": str(report_file),
        "markdown": markdown_result
    }


def generate_daily_summary_pipeline(
    target_date_str: Optional[str] = None,
    provider_override: Optional[str] = None,
    force_refresh: bool = False
) -> Dict[str, Any]:
    """向後相容單日摘要呼叫"""
    return generate_summary_pipeline(
        start_date_str=target_date_str,
        end_date_str=target_date_str,
        provider_override=provider_override,
        force_refresh=force_refresh
    )
