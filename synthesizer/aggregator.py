import os
from datetime import datetime, date, time, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List, Set
import json
import logging

from core.database import get_db
from core.models import AIPromptEvent, FileActivityEvent, GitActivityEvent, WindowEvent, DailySummary, ProjectState, OpenLoop
from core.config import get_config
from core.time_utils import get_local_now
from core.project_engine import get_active_projects_list, get_open_loops_list, refresh_project_states
from .prompt_templates import DAILY_PROJECT_SYNTHESIS_SYSTEM, DAILY_PROJECT_SYNTHESIS_USER
from .llm_client import LLMClient

logger = logging.getLogger("OmniContext.Aggregator")


def fetch_events_in_range(start_dt: datetime, end_dt: datetime) -> Dict[str, Any]:
    """撈取指定時間範圍內的所有事件"""
    db = get_db()
    with db.session_scope() as session:
        ai_events = (
            session.query(AIPromptEvent)
            .filter(AIPromptEvent.timestamp >= start_dt, AIPromptEvent.timestamp <= end_dt)
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

        return {
            "ai_events": [
                {
                    "id": e.id,
                    "time": e.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "platform": e.platform,
                    "prompt": e.prompt_text,
                    "response": e.response_text or "",
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


def fetch_day_data(target_date: date) -> Dict[str, Any]:
    """從 SQLite 資料庫嚴格撈取指定日期的所有事件 (00:00:00 ~ 23:59:59)"""
    start_dt = datetime.combine(target_date, time.min)
    end_dt = datetime.combine(target_date, time.max)
    return fetch_events_in_range(start_dt, end_dt)


def format_context_for_prompt(day_data: Dict[str, Any], target_date_str: str) -> str:
    """將資料庫事件轉換為適合 LLM 閱讀的專案中心格式 (嚴格限於當日真實推進專案)"""
    
    # 找出今日真正有活動的專案名稱集合
    today_active_project_names: Set[str] = set()
    for g in day_data["git_events"]:
        if g.get("repo"): today_active_project_names.add(g["repo"])
    for f in day_data["file_events"]:
        if f.get("project"): today_active_project_names.add(f["project"])
    for a in day_data["ai_events"]:
        if a.get("tag"): today_active_project_names.add(a["tag"])

    # 1. Active Projects (今日真正有活動的專案)
    all_projects = get_active_projects_list()
    today_projects = [p for p in all_projects if p["display_name"] in today_active_project_names or p["project_key"] in today_active_project_names]

    proj_lines = []
    for p in today_projects:
        proj_lines.append(f"- **{p['display_name']}** [{p['category']}]: 最新動態 `{p['last_action_summary']}`")
    
    if not proj_lines and today_active_project_names:
        for name in sorted(today_active_project_names):
            proj_lines.append(f"- **{name}**")

    proj_text = "\n".join(proj_lines) if proj_lines else "（今日無專案更新）"

    # 2. Open Loops
    open_loops = get_open_loops_list()[:8]
    loop_lines = []
    for ol in open_loops:
        loop_lines.append(f"- [{ol['project_key']}] {ol['title']} (建立於 {ol['created_at']})")
    loop_text = "\n".join(loop_lines) if loop_lines else "（目前無待辦事項）"

    # 3. AI Events (今日)
    ai_lines = []
    for item in day_data["ai_events"]:
        tag_info = f" [{item['tag']}]" if item['tag'] else ""
        resp_snippet = f"\n  -> AI 回應摘要: {item['response'][:250]}..." if item['response'] and len(item['response']) > 10 else ""
        ai_lines.append(f"- [{item['time'].split(' ')[1]}] [{item['platform'].upper()}]{tag_info} 問: {item['prompt']}{resp_snippet}")
    ai_text = "\n".join(ai_lines) if ai_lines else "（今日無 AI 互動紀錄）"

    # 4. File Events (今日)
    file_lines = []
    for item in day_data["file_events"]:
        diff_str = f" ({item['diff']})" if item['diff'] else ""
        file_lines.append(f"- [{item['time'].split(' ')[1]}] [{item['action'].upper()}] {item['file_name']} [{item['file_type']}]{diff_str}")
    file_text = "\n".join(file_lines) if file_lines else "（今日無檔案異動紀錄）"

    # 5. Git Events (今日)
    git_lines = []
    for item in day_data["git_events"]:
        git_lines.append(f"- [{item['time'].split(' ')[1]}] [{item['repo']}@{item['branch']}] Commit: {item['message']} (+{item['insertions']}/-{item['deletions']})")
    git_text = "\n".join(git_lines) if git_lines else "（今日無代碼提交紀錄）"

    # 6. Window Events (今日)
    app_durations: Dict[str, float] = {}
    for item in day_data["window_events"]:
        app = item["app"]
        app_durations[app] = app_durations.get(app, 0.0) + item["duration_sec"]

    window_lines = [f"- {app}: 約 {int(sec // 60)} 分鐘" for app, sec in sorted(app_durations.items(), key=lambda x: x[1], reverse=True)[:8]]
    window_text = "\n".join(window_lines) if window_lines else "（今日無視窗統計資料）"

    return DAILY_PROJECT_SYNTHESIS_USER.format(
        target_date=target_date_str,
        active_projects_text=proj_text,
        open_loops_text=loop_text,
        ai_interactions_text=ai_text,
        file_activities_text=file_text,
        git_activities_text=git_text,
        window_activities_text=window_text
    )


def save_summary_to_file(date_str: str, markdown_content: str) -> Path:
    cfg = get_config()
    reports_dir_str = cfg.get("exporters.reports_dir", "reports")
    reports_dir = Path(reports_dir_str)
    if not reports_dir.is_absolute():
        root_dir = Path(__file__).parent.parent
        reports_dir = root_dir / reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)

    file_path = reports_dir / f"Daily_Summary_{date_str}.md"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(markdown_content)

    if cfg.get("exporters.obsidian.enabled", False):
        obsidian_dir = Path(cfg.get("exporters.obsidian.vault_daily_notes_dir", ""))
        if obsidian_dir.exists():
            obsidian_file = obsidian_dir / f"{date_str}.md"
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


def generate_daily_summary_pipeline(
    target_date_str: Optional[str] = None,
    provider_override: Optional[str] = None,
    force_refresh: bool = False
) -> Dict[str, Any]:
    """嚴格以指定日期 (00:00:00 ~ 23:59:59) 真實活動為範疇的每日總結生成管道"""
    if not target_date_str:
        target_date = get_local_now().date()
        target_date_str = target_date.isoformat()
    else:
        target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()

    db = get_db()

    with db.session_scope() as session:
        existing = session.query(DailySummary).filter_by(date_str=target_date_str).first()
        if existing and not force_refresh:
            logger.info(f"Summary for {target_date_str} already exists. Returning cached.")
            return {
                "status": "cached",
                "date_str": target_date_str,
                "markdown": existing.raw_markdown
            }

    # 1. 撈取指定日期當天的數據
    refresh_project_states()
    day_data = fetch_day_data(target_date)

    # 2. 構建 Prompt (僅包含當日有活動的專案)
    user_prompt = format_context_for_prompt(day_data, target_date_str)

    # 3. 調用 LLM
    client = LLMClient(provider=provider_override)
    markdown_result = client.generate(
        system_prompt=DAILY_PROJECT_SYNTHESIS_SYSTEM,
        user_prompt=user_prompt
    )

    # 4. 存檔至本機檔案
    report_file = save_summary_to_file(target_date_str, markdown_result)

    # 5. 存檔至資料庫
    cfg = get_config()
    provider_name = provider_override or cfg.get("synthesizer.provider", "gemini")
    model_name = cfg.get(f"synthesizer.{provider_name}.model", "default")

    with db.session_scope() as session:
        summary_record = session.query(DailySummary).filter_by(date_str=target_date_str).first()
        if not summary_record:
            summary_record = DailySummary(
                date_str=target_date_str,
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

    return {
        "status": "generated",
        "date_str": target_date_str,
        "report_path": str(report_file),
        "markdown": markdown_result
    }
