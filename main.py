import os
import sys
import time
import argparse
import logging
import threading
from datetime import datetime, date, timedelta
from pathlib import Path

# 強制 Windows 控制台輸出支援 UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 將專案根目錄加入 sys.path
sys.path.insert(0, str(Path(__file__).parent))

from core.config import get_config
from core.database import get_db
from core.models import AIPromptEvent, FileActivityEvent, GitActivityEvent, WindowEvent, DailySummary, ProjectState, OpenLoop
from core.manager import get_manager
from core.time_utils import get_local_now
from core.project_engine import get_active_projects_list, get_open_loops_list
from synthesizer.aggregator import generate_daily_summary_pipeline, generate_periodic_checkpoint
from scripts.cleanup_noise import cleanup_noise_and_demo_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("OmniContext.Main")


def run_web_and_services(autostart_monitoring: bool = True):
    """一鍵啟動 FastAPI Web 伺服器與監控服務"""
    try:
        import uvicorn
        from core.server import app
    except ImportError as e:
        logger.error(f"Missing web server dependencies: {e}. Please run `pip install -r requirements.txt`.")
        return

    cfg = get_config()
    db = get_db()
    manager = get_manager()
    logger.info("Initializing OmniContext Engine...")

    if autostart_monitoring:
        manager.start_all()

    host = cfg.get("server.host", "127.0.0.1")
    port = cfg.get("server.port", 8765)

    print("\n" + "="*70)
    print(f"🌐 OmniContext 視覺化 Web 儀表板已就緒: http://{host}:{port}")
    print(f"👉 請在瀏覽器中打開 http://{host}:{port} 查看進行中專案、即時活動流與回顧！")
    print("="*70 + "\n")

    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    except KeyboardInterrupt:
        logger.info("\nStopping services...")
    finally:
        manager.stop_all()
        logger.info("OmniContext stopped.")


def cmd_now():
    """隨時查詢：一秒回答「我現在在做什麼、剛剛做了什麼、有什麼沒收尾」 (P2 核心)"""
    db = get_db()
    now = get_local_now()

    print("\n" + "="*70)
    print(f"🎯 OmniContext 即時工作全景 ({now.strftime('%Y-%m-%d %H:%M:%S')})")
    print("="*70)

    # 1. 取得當前活躍專案 (近 3 天)
    projects = get_active_projects_list()
    active_projs = [p for p in projects if p["status"] == "active"][:5]

    print("\n🔥 當前活躍專案 (Active Workstreams):")
    if active_projs:
        for p in active_projs:
            print(f"  • [{p['category']}] {p['display_name']} (最後活動: {p['last_activity_at']})")
            print(f"    └─ 動態: {p['last_action_summary']}")
    else:
        print("  (目前尚無 48 小時內的高頻活動專案)")

    # 2. 剛剛做了什麼 (最近 5 筆跨源事件)
    print("\n⚡ 最近 5 筆活動 (Recent Activities):")
    with db.session_scope() as session:
        ai_latest = session.query(AIPromptEvent).order_by(AIPromptEvent.timestamp.desc()).limit(3).all()
        git_latest = session.query(GitActivityEvent).order_by(GitActivityEvent.timestamp.desc()).limit(2).all()
        file_latest = session.query(FileActivityEvent).order_by(FileActivityEvent.timestamp.desc()).limit(2).all()

        recent_all = []
        for a in ai_latest:
            recent_all.append((a.timestamp, f"[AI:{a.platform.upper()}] {a.prompt_text[:50]}..."))
        for g in git_latest:
            recent_all.append((g.timestamp, f"[Git:{g.repo_name}] {g.message[:50]}..."))
        for f in file_latest:
            recent_all.append((f.timestamp, f"[File:{f.action.upper()}] {f.file_name}"))

        recent_all.sort(key=lambda x: x[0], reverse=True)
        for t, desc in recent_all[:5]:
            print(f"  [{t.strftime('%H:%M:%S')}] {desc}")

    # 3. 未結事項 (Open Loops)
    open_loops = get_open_loops_list()[:5]
    print("\n📌 待收尾與未結事項 (Open Loops):")
    if open_loops:
        for ol in open_loops:
            print(f"  [ ] ({ol['project_key']}) {ol['title']}")
    else:
        print("  (目前無待辦事項)")
    print("="*70 + "\n")


def cmd_summary(target_date: str | None, provider: str | None, force: bool):
    """手動觸發生成特定日期的 AI 總結報告"""
    date_str = target_date or get_local_now().strftime("%Y-%m-%d")
    logger.info(f"Generating summary for {date_str} using provider '{provider or 'default'}'...")
    res = generate_daily_summary_pipeline(
        target_date_str=date_str,
        provider_override=provider,
        force_refresh=force
    )
    print("\n" + "="*70)
    print(f"[每日回顧生成完成] 日期: {res.get('date_str')}")
    print("="*70)
    print(res.get("markdown"))
    if "report_path" in res:
        print("\n" + "-"*70)
        print(f"[存檔路徑] {res['report_path']}")
        print("-"*70)


def cmd_checkpoint(hours: int = 2):
    """手動產出最近 N 小時的活動快照日誌"""
    logger.info(f"Generating checkpoint log for past {hours} hours...")
    res = generate_periodic_checkpoint(hours=hours)
    print("\n" + "="*70)
    print(f"⏱️ 活動快照日誌已產生: {res.get('file_name')}")
    print("="*70)
    print(res.get("content"))
    print("\n" + "-"*70)
    print(f"[存檔路徑] {res.get('file_path')}")
    print("-"*70)


def cmd_status():
    """查看本地 SQLite 資料庫累積事件統計與專案狀態"""
    manager = get_manager()
    status = manager.get_status()
    db = get_db()

    with db.session_scope() as session:
        proj_count = session.query(ProjectState).count()
        open_loops_count = session.query(OpenLoop).filter(OpenLoop.resolved_at.is_(None)).count()

    print("\n" + "="*50)
    print("📊 OmniContext 系統統計數據")
    print("="*50)
    print(f"• 監控狀態                 : {'🟢 運行中' if status['is_running'] else '🔴 已停止'}")
    print(f"• 識別進行中專案數         : {proj_count} 個")
    print(f"• 未結待辦事項 (Open Loops): {open_loops_count} 項")
    print(f"• AI 互動紀錄 (Prompts)    : {status['metrics']['ai_prompts_count']} 筆")
    print(f"• 檔案與論文異動事件       : {status['metrics']['file_events_count']} 筆")
    print(f"• Git Commit 紀錄          : {status['metrics']['git_commits_count']} 筆")
    print(f"• 視窗焦點時間統計         : {status['metrics']['window_events_count']} 筆")
    print(f"• 已生成每日摘要報告       : {status['metrics']['daily_summaries_count']} 篇")
    print("="*50 + "\n")


def main():
    parser = argparse.ArgumentParser(description="OmniContext - 個人全景上下文與進行中專案智慧中樞")
    subparsers = parser.add_subparsers(dest="command", help="子指令")

    # run / web
    run_parser = subparsers.add_parser("run", help="啟動 Web 儀表板與後台監控服務")
    run_parser.add_argument("--no-autostart", action="store_true")

    web_parser = subparsers.add_parser("web", help="啟動 Web 視覺化儀表板 (同 run)")
    web_parser.add_argument("--no-autostart", action="store_true")

    # now 指令
    subparsers.add_parser("now", help="一秒查詢當前進行中工作與最近動作")

    # summary 指令
    summary_parser = subparsers.add_parser("summary", help="生成每日以專案為主軸的 AI 總結報告")
    summary_parser.add_argument("--date", help="指定日期 (YYYY-MM-DD)，預設為今天")
    summary_parser.add_argument("--provider", help="指定 LLM 供應商 (gemini, anthropic, openai, ollama)")
    summary_parser.add_argument("--force", action="store_true", help="強制重新生成已存在的摘要")

    # checkpoint 指令
    cp_parser = subparsers.add_parser("checkpoint", help="手動生成近時段的活動快照 Log")
    cp_parser.add_argument("--hours", type=int, default=2, help="回溯時數 (預設 2 小時)")

    # clear-demo 指令
    subparsers.add_parser("clear-demo", help="清除示範假資料與歷史噪音")

    # status 指令
    subparsers.add_parser("status", help="查看當前數據庫統計與監控狀態")

    args = parser.parse_args()

    if args.command in ["run", "web", None]:
        autostart = not getattr(args, "no_autostart", False) if args.command else True
        run_web_and_services(autostart_monitoring=autostart)
    elif args.command == "now":
        cmd_now()
    elif args.command == "summary":
        cmd_summary(args.date, args.provider, args.force)
    elif args.command == "checkpoint":
        cmd_checkpoint(args.hours)
    elif args.command == "clear-demo":
        cleanup_noise_and_demo_data()
    elif args.command == "status":
        cmd_status()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
