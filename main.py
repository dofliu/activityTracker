import os
import sys
import time
import argparse
import logging
import threading
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any
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
from core.project_engine import get_active_projects_list, get_open_loops_list, create_open_loop
from synthesizer.aggregator import generate_daily_summary_pipeline, generate_periodic_checkpoint
from scripts.cleanup_noise import cleanup_noise_and_demo_data
from notifiers.telegram_notifier import TelegramNotifier
from notifiers.desktop_notifier import DesktopNotifier
from exporters.daily_brief import export_daily_brief

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("OmniContext.Main")

LOCK_FILE = Path(__file__).parent / ".instance.lock"


def acquire_instance_lock() -> bool:
    """單一實例鎖 (Single Instance Lock)：防止重複啟動造成資料庫衝突"""
    try:
        if LOCK_FILE.exists():
            content = LOCK_FILE.read_text().strip()
            if content:
                try:
                    pid = int(content)
                    import psutil
                    if psutil.pid_exists(pid):
                        # 確認是否為 python process
                        proc = psutil.Process(pid)
                        if "python" in proc.name().lower():
                            logger.error(f"⚠️ 另一個 OmniContext 實例正在運行中 (PID: {pid})。請勿重複啟動！")
                            return False
                except Exception:
                    pass
        # 寫入當前 process PID
        LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
        return True
    except Exception as e:
        logger.warning(f"Failed to acquire instance lock: {e}")
        return True


def release_instance_lock():
    """釋放單一實例鎖"""
    try:
        if LOCK_FILE.exists():
            LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def run_web_and_services(autostart_monitoring: bool = True):
    """一鍵啟動 FastAPI Web 伺服器與監控服務"""
    if not acquire_instance_lock():
        sys.exit(1)

    try:
        import uvicorn
        from core.server import app
    except ImportError as e:
        logger.error(f"Missing web server dependencies: {e}. Please run `pip install -r requirements.txt`.")
        release_instance_lock()
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
        release_instance_lock()
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
    active_projs = [p for p in projects if p["status"] == "active"][:6]

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
            resp_info = f" -> {a.response_text[:40]}..." if a.response_text else ""
            recent_all.append((a.timestamp, f"[AI:{a.platform.upper()}] {a.prompt_text[:45]}...{resp_info}"))
        for g in git_latest:
            recent_all.append((g.timestamp, f"[Git:{g.repo_name}] {g.message[:50]}..."))
        for f in file_latest:
            recent_all.append((f.timestamp, f"[File:{f.action.upper()}] {f.file_name}"))

        recent_all.sort(key=lambda x: x[0], reverse=True)
        for t, desc in recent_all[:5]:
            print(f"  [{t.strftime('%H:%M:%S')}] {desc}")

    # 3. 未結事項 (Open Loops)
    open_loops = get_open_loops_list()
    print(f"\n📌 待收尾與未結事項 (Open Loops - 共 {len(open_loops)} 項):")
    if open_loops:
        for ol in open_loops[:8]:
            print(f"  [ ] ({ol['project_key']}) {ol['title']} (建立於 {ol['created_at']})")
    else:
        print("  (目前無待辦事項)")
    print("="*70 + "\n")


def cmd_summary(target_date: str | None, start_date: str | None, end_date: str | None, provider: str | None, force: bool):
    """手動觸發生成特定日期或區間的 AI 總結報告"""
    from synthesizer.aggregator import generate_summary_pipeline
    start_d = start_date or target_date or get_local_now().strftime("%Y-%m-%d")
    end_d = end_date or target_date or get_local_now().strftime("%Y-%m-%d")
    
    logger.info(f"Generating summary for {start_d} ~ {end_d} using provider '{provider or 'default'}'...")
    res = generate_summary_pipeline(
        start_date_str=start_d,
        end_date_str=end_d,
        provider_override=provider,
        force_refresh=force
    )
    print("\n" + "="*70)
    print(f"[工作回顧生成完成] 區間/日期: {res.get('date_str')}")
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
        ai_resp_count = session.query(AIPromptEvent).filter(
            AIPromptEvent.response_text.isnot(None),
            ~AIPromptEvent.response_text.like("[%Session]"),
            ~AIPromptEvent.response_text.like("[%Agent Session]")
        ).count()

    print("\n" + "="*50)
    print("📊 OmniContext 系統統計數據")
    print("="*50)
    print(f"• 監控狀態                 : {'🟢 運行中' if status['is_running'] else '🔴 已停止'}")
    print(f"• 識別進行中專案數         : {proj_count} 個")
    print(f"• 未結待辦事項 (Open Loops): {open_loops_count} 項")
    print(f"• AI 互動紀錄 (Prompts)    : {status['metrics']['ai_prompts_count']} 筆 (含真實 AI 回應: {ai_resp_count} 筆)")
    print(f"• 檔案與論文異動事件       : {status['metrics']['file_events_count']} 筆")
    print(f"• Git Commit 紀錄          : {status['metrics']['git_commits_count']} 筆")
    print(f"• 視窗焦點時間統計         : {status['metrics']['window_events_count']} 筆")
    print(f"• 已生成每日摘要報告       : {status['metrics']['daily_summaries_count']} 篇")
    print("="*50 + "\n")


def cmd_notify_telegram(action: str, date_str: Optional[str] = None, dry_run: bool = False):
    """測試或手動發送 Telegram 通知 (支援 --dry-run 預覽)"""
    notifier = TelegramNotifier()
    if dry_run:
        now = get_local_now()
        projects = get_active_projects_list()
        open_loops = get_open_loops_list()
        print("\n" + "="*50)
        print("📱 Telegram 推播預覽 (Dry-run Mode)")
        print("="*50)
        if action == "briefing":
            active_projs = [p for p in projects if p["status"] == "active"][:5]
            print(f"<b>🌅 OmniContext 晨間簡報 ({now.strftime('%Y-%m-%d')})</b>\n")
            print("<b>🔥 今日重點活躍專案：</b>")
            for p in active_projs:
                print(f"• <b>[{p['category']}] {p['display_name']}</b>\n  └─ {p['last_action_summary']}")
            print(f"\n<b>📌 待跟進未結事項 ({len(open_loops)} 項)：</b>")
            for ol in open_loops[:6]:
                print(f"• [ ] <b>[{ol['project_key']}]</b> {ol['title']}")
        elif action == "stagnation":
            stagnant = [p for p in projects if p["status"] in ["idle", "stale"] and p["idle_days"] >= 3][:4]
            print("<b>⚠️ OmniContext 專案停滯提醒</b>\n")
            for p in stagnant:
                print(f"• <b>{p['display_name']}</b> (已閒置 {p['idle_days']} 天)\n  └─ 上次動態: {p['last_action_summary']}")
        print("="*50 + "\n")
        return

    if action == "summary":
        d = date_str or get_local_now().strftime("%Y-%m-%d")
        print(f"正在發送 {d} 的每日日報至 Telegram...")
        success = notifier.send_daily_summary(d)
        print("✅ 發送成功" if success else "❌ 發送失敗，請確認 config.yaml 或環境變數中的 bot_token 與 chat_id")
    elif action == "briefing":
        print("正在發送晨間簡報至 Telegram...")
        success = notifier.send_morning_briefing()
        print("✅ 發送成功" if success else "❌ 發送失敗")
    elif action == "stagnation":
        print("正在檢查並發送停滯專案警示至 Telegram...")
        success = notifier.send_stagnation_alert()
        print("✅ 發送成功" if success else "❌ 發送失敗")


def cmd_notify_desktop(action: str, dry_run: bool = False):
    """發送 Windows 桌面通知"""
    notifier = DesktopNotifier()
    if action in ("briefing", "summary"):
        ok = notifier.send_morning_briefing(dry_run=dry_run)
    elif action == "evening":
        ok = notifier.send_evening_summary(dry_run=dry_run)
    elif action == "stagnation":
        ok = notifier.send_stagnation_alert(dry_run=dry_run)
    else:
        print(f"未知的通知類型: {action}")
        return

    if not dry_run:
        print("✅ 桌面通知已送出" if ok else "❌ 桌面通知發送失敗，請查看日誌")


def cmd_brief(output_dir: Optional[str] = None, notify: bool = False):
    """產出每日簡報檔案，並可選擇同時發送桌面通知"""
    res = export_daily_brief(output_dir)
    print("\n" + "=" * 60)
    print(f"📌 每日簡報已更新 ({res['generated_at']})")
    print("=" * 60)
    print(f"• 進行中專案 : {res['active_count']} 個")
    print(f"• 尚未收尾   : {res['open_loops_count']} 項")
    print(f"• 停滯專案   : {res['stagnant_count']} 個")
    print("-" * 60)
    for f in res["files"]:
        print(f"  → {f}")
    print("=" * 60 + "\n")

    if notify:
        DesktopNotifier().send_morning_briefing()


def cmd_github(action: str):
    """GitHub 雲端整合狀態與手動同步"""
    from integrations.github_client import get_github_client
    client = get_github_client()
    if action in ["status", None]:
        status = client.test_connection()
        print("\n" + "="*50)
        print("🐙 GitHub 雲端認證狀態")
        print("="*50)
        if status.get("connected"):
            print(f"• 連線狀態 : 🟢 已認證 (@{status.get('username')})")
            print(f"• 姓名     : {status.get('name')}")
            print(f"• 公開倉庫 : {status.get('public_repos')} 個")
            print(f"• 私有倉庫 : {status.get('total_private_repos')} 個")
            print(f"• Token 權限: {', '.join(status.get('scopes', []))}")
            if status.get("rate_limit"):
                print(f"• API 額度 : {status['rate_limit']['remaining']} / {status['rate_limit']['limit']}")
        else:
            print(f"• 連線狀態 : 🔴 未連線 ({status.get('message')})")
        print("="*50 + "\n")
    elif action == "sync":
        print("正在同步 GitHub 所有 Public / Private 倉庫與 PRs...")
        res = client.sync_all(max_repos=50)
        print(f"✅ 同步完成！已同步 {res.get('synced_repos_count')} 個專案與 {res.get('synced_prs_count')} 筆 PR 狀態。")


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
    summary_parser = subparsers.add_parser("summary", help="生成每日或自訂區間以專案為主軸的 AI 總結報告")
    summary_parser.add_argument("--date", help="指定單一日期 (YYYY-MM-DD)")
    summary_parser.add_argument("--start", help="自訂區間起始日期 (YYYY-MM-DD)")
    summary_parser.add_argument("--end", help="自訂區間結束日期 (YYYY-MM-DD)")
    summary_parser.add_argument("--provider", help="指定 LLM 供應商 (gemini, anthropic, openai, ollama)")
    summary_parser.add_argument("--force", action="store_true", help="強制重新生成已存在的摘要")

    # checkpoint 指令
    cp_parser = subparsers.add_parser("checkpoint", help="手動生成近時段的活動快照 Log")
    cp_parser.add_argument("--hours", type=int, default=2, help="回溯時數 (預設 2 小時)")

    # github 指令
    gh_parser = subparsers.add_parser("github", help="GitHub 雲端專案與 PR 同步管理")
    gh_parser.add_argument("action", choices=["status", "sync"], default="status", nargs="?", help="動作 (status, sync)")

    # notify 指令
    notify_parser = subparsers.add_parser("notify", help="手動觸發提醒推播 (預設桌面通知)")
    notify_parser.add_argument("type", choices=["summary", "briefing", "evening", "stagnation"], help="通知類型")
    notify_parser.add_argument("--channel", choices=["desktop", "telegram"], default="desktop", help="推播通道 (預設 desktop)")
    notify_parser.add_argument("--date", help="指定日期 (YYYY-MM-DD)")
    notify_parser.add_argument("--dry-run", action="store_true", help="不實際發送，僅在終端機預覽推播格式")

    # brief 指令
    brief_parser = subparsers.add_parser("brief", help="產出每日簡報檔案 (寫入每日入口目錄)")
    brief_parser.add_argument("--dir", help="覆寫輸出目錄")
    brief_parser.add_argument("--notify", action="store_true", help="產出後同時發送桌面通知")

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
        cmd_summary(args.date, getattr(args, "start", None), getattr(args, "end", None), args.provider, args.force)
    elif args.command == "github":
        cmd_github(args.action)
    elif args.command == "checkpoint":
        cmd_checkpoint(args.hours)
    elif args.command == "notify":
        if getattr(args, "channel", "desktop") == "telegram":
            cmd_notify_telegram(args.type, getattr(args, "date", None), getattr(args, "dry_run", False))
        else:
            cmd_notify_desktop(args.type, getattr(args, "dry_run", False))
    elif args.command == "brief":
        cmd_brief(getattr(args, "dir", None), getattr(args, "notify", False))
    elif args.command == "clear-demo":
        cleanup_noise_and_demo_data()
    elif args.command == "status":
        cmd_status()
    else:
        parser.print_help()



if __name__ == "__main__":
    main()
