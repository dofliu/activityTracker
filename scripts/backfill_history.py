import sys
from pathlib import Path

# 強制 Windows 控制台輸出支援 UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 將專案根目錄加入 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from watchers.agent_log_watcher import AgentLogWatcherService
from watchers.git_watcher import GitWatcherService
from core.project_engine import refresh_project_states
from core.database import get_db
from core.models import AIPromptEvent, GitActivityEvent, ProjectState, FileActivityEvent, DailySummary


def run_clean_backfill():
    print("🧹 清除先前時間戳被污染的舊資料庫紀錄...")
    db = get_db()
    with db.session_scope() as session:
        session.query(AIPromptEvent).delete()
        session.query(GitActivityEvent).delete()
        session.query(ProjectState).delete()
        session.query(DailySummary).delete()

    print("🚀 開始執行本機全歷史日誌【精準時間戳】回填 (Claude Code, Codex, Antigravity, Git Repos)...")

    # 1. 回填 Agent 日誌 (嚴格解析原始時間戳)
    agent_watcher = AgentLogWatcherService()
    print("🤖 正在掃描本機 Claude Code, Codex 與 Antigravity 歷史記錄...")
    agent_watcher.scan_all_agents(full_history=True)

    # 2. 遞迴掃描 Git 倉庫 Commits
    git_watcher = GitWatcherService()
    print("💻 正在遞迴掃描所有 Git 倉庫 Commits...")
    git_watcher.scan_repositories()

    # 3. 重新計算專案狀態
    print("📊 正在計算專案狀態與歸戶...")
    refresh_project_states()

    # 4. 統計成果與日期分佈
    with db.session_scope() as session:
        ai_count = session.query(AIPromptEvent).count()
        git_count = session.query(GitActivityEvent).count()
        proj_count = session.query(ProjectState).count()

        # 統計各日期筆數
        ai_events = session.query(AIPromptEvent).all()
        dates_stat = {}
        for a in ai_events:
            d = a.timestamp.strftime("%Y-%m-%d")
            dates_stat[d] = dates_stat.get(d, 0) + 1

        print("\n" + "="*60)
        print("🎉 歷史回填完成！真實數據庫統計：")
        print("="*60)
        print(f"• AI 問答/指令總數 (精準歷史時間): {ai_count} 筆")
        print(f"• Git Commits 總數: {git_count} 筆")
        print(f"• 識別之活躍專案數: {proj_count} 個")
        print("\n📅 最近 5 天的真實 AI 活動筆數分佈：")
        for d, cnt in sorted(dates_stat.items(), reverse=True)[:5]:
            print(f"  - {d}: {cnt} 筆")
        print("="*60 + "\n")


if __name__ == "__main__":
    run_clean_backfill()
