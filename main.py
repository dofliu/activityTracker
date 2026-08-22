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
from core.models import AIPromptEvent, FileActivityEvent, GitActivityEvent, WindowEvent, DailySummary
from core.manager import get_manager
from synthesizer.aggregator import generate_daily_summary_pipeline, generate_periodic_checkpoint

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
    print(f"👉 請在瀏覽器中打開 http://{host}:{port} 查看儀表板與配置監控項目！")
    print("="*70 + "\n")

    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    except KeyboardInterrupt:
        logger.info("\nStopping services...")
    finally:
        manager.stop_all()
        logger.info("OmniContext stopped.")


def cmd_summary(target_date: str | None, provider: str | None, force: bool):
    """手動觸發生成特定日期的 AI 總結報告"""
    date_str = target_date or date.today().isoformat()
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
    """查看本地 SQLite 資料庫累積事件統計"""
    manager = get_manager()
    status = manager.get_status()

    print("\n" + "="*50)
    print("📊 OmniContext 系統統計數據")
    print("="*50)
    print(f"• 監控狀態                 : {'🟢 運行中' if status['is_running'] else '🔴 已停止'}")
    print(f"• AI 互動紀錄 (Prompts)    : {status['metrics']['ai_prompts_count']} 筆")
    print(f"• 檔案與論文異動事件       : {status['metrics']['file_events_count']} 筆")
    print(f"• Git Commit 紀錄          : {status['metrics']['git_commits_count']} 筆")
    print(f"• 視窗焦點時間統計         : {status['metrics']['window_events_count']} 筆")
    print(f"• 已生成每日摘要報告       : {status['metrics']['daily_summaries_count']} 篇")
    print("="*50 + "\n")


def cmd_seed_demo():
    """寫入一組逼真的範例數據（包含論文、ChatGPT、Claude、Manus、Git 異動）以便立即體驗"""
    db = get_db()
    now_dt = datetime.now()

    with db.session_scope() as session:
        session.query(AIPromptEvent).delete()
        session.query(FileActivityEvent).delete()
        session.query(GitActivityEvent).delete()
        session.query(WindowEvent).delete()

        # 1. AI Prompts
        session.add(AIPromptEvent(
            platform="claude",
            url="https://claude.ai",
            prompt_text="請幫我檢查這篇 AAAI 論文中關於多智能體協作定理 2 的數學證明是否存在邊界條件反例？",
            response_text="我檢查了定理 2 的證明，發現當智能體數量 N=1 時，公式第 4 行的分母可能為零。建議加入邊界假設條件 (N >= 2) 並更新引理 1.2。",
            project_tag="AAAI Paper",
            timestamp=now_dt - timedelta(hours=6)
        ))
        session.add(AIPromptEvent(
            platform="gemini",
            url="https://gemini.google.com",
            prompt_text="在 Python FastAPI 中處理高頻本機日誌寫入 SQLite 時，如何避免 database locked 錯誤？",
            response_text="建議啟用 SQLite 的 WAL (Write-Ahead Logging) 模式，並在連線時加上 check_same_thread=False 與適當的 timeout 參數。",
            project_tag="OmniContext Dev",
            timestamp=now_dt - timedelta(hours=4)
        ))
        session.add(AIPromptEvent(
            platform="manus",
            url="https://manus.im",
            prompt_text="自動搜尋 2025-2026 年關於 Personal Context Agent 與 Life-logging 的頂會論文並整理表格",
            response_text="已為您搜尋並整理出 5 篇相關頂會論文，包含 Screenpipe、MemGPT-v2 與 ActivityAgent 的核心架構比較表。",
            project_tag="Literature Review",
            timestamp=now_dt - timedelta(hours=2)
        ))

        # 2. File Events
        session.add(FileActivityEvent(
            file_path="C:/Users/user/Desktop/Papers/aaai2026_draft.tex",
            file_name="aaai2026_draft.tex",
            file_type=".tex",
            action="modified",
            size_bytes=45200,
            diff_summary="更新 Section 3 Methodology，新增字數約 850 字",
            project_name="Papers",
            timestamp=now_dt - timedelta(hours=5)
        ))

        # 3. Git Events
        session.add(GitActivityEvent(
            repo_name="omni-context",
            repo_path="C:/Users/user/.gemini/antigravity/scratch/omni-context",
            commit_hash="a1b2c3d4",
            branch="main",
            author="User",
            message="feat: implement multi-source watchers and sqlite storage",
            files_changed_count=6,
            insertions=350,
            deletions=20,
            timestamp=now_dt - timedelta(hours=3)
        ))

        # 4. Window Events
        session.add(WindowEvent(
            app_name="Code.exe",
            window_title="omni-context - Visual Studio Code",
            duration_seconds=7200,
            category="Coding / Development",
            start_time=now_dt - timedelta(hours=4),
            end_time=now_dt - timedelta(hours=2)
        ))
        session.add(WindowEvent(
            app_name="TeXstudio.exe",
            window_title="aaai2026_draft.tex - TeXstudio",
            duration_seconds=5400,
            category="Research / Paper Writing",
            start_time=now_dt - timedelta(hours=6),
            end_time=now_dt - timedelta(hours=4, minutes=30)
        ))

    print("[SUCCESS] 成功寫入逼真範例數據！現在可以啟動 Web 儀表板或執行 `python main.py summary` 體驗！")


def main():
    parser = argparse.ArgumentParser(description="OmniContext - 個人全景上下文與 AI 每日回顧系統")
    subparsers = parser.add_subparsers(dest="command", help="子指令")

    # run / web
    run_parser = subparsers.add_parser("run", help="啟動 Web 儀表板與後台監控服務")
    run_parser.add_argument("--no-autostart", action="store_true", help="啟動伺服器但不自動開始監控 (待網頁上點擊開始)")

    web_parser = subparsers.add_parser("web", help="啟動 Web 視覺化儀表板 (同 run)")
    web_parser.add_argument("--no-autostart", action="store_true")

    summary_parser = subparsers.add_parser("summary", help="手動生成每日 AI 總結報告")
    summary_parser.add_argument("--date", help="指定日期 (YYYY-MM-DD)，預設為今天")
    summary_parser.add_argument("--provider", help="指定 LLM 供應商 (gemini, anthropic, openai, ollama)")
    summary_parser.add_argument("--force", action="store_true", help="強制重新生成已存在的摘要")

    cp_parser = subparsers.add_parser("checkpoint", help="手動生成近時段的活動快照 Log")
    cp_parser.add_argument("--hours", type=int, default=2, help="回溯時數 (預設 2 小時)")

    subparsers.add_parser("status", help="查看當前數據庫統計與監控狀態")
    subparsers.add_parser("seed-demo", help="寫入範例活動數據供測試")

    args = parser.parse_args()

    if args.command in ["run", "web", None]:
        autostart = not getattr(args, "no_autostart", False) if args.command else True
        run_web_and_services(autostart_monitoring=autostart)
    elif args.command == "summary":
        cmd_summary(args.date, args.provider, args.force)
    elif args.command == "checkpoint":
        cmd_checkpoint(args.hours)
    elif args.command == "status":
        cmd_status()
    elif args.command == "seed-demo":
        cmd_seed_demo()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
