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
from core.models import AIPromptEvent, GitActivityEvent, ProjectState


def run_full_backfill():
    print("🚀 開始執行本機全歷史日誌回填 (Claude Code, Codex, Antigravity, Git Repos)...")

    # 1. 回填 Agent 日誌
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

    # 4. 統計成果
    db = get_db()
    with db.session_scope() as session:
        ai_count = session.query(AIPromptEvent).count()
        git_count = session.query(GitActivityEvent).count()
        proj_count = session.query(ProjectState).count()

        print("\n" + "="*60)
        print("🎉 歷史回填完成！當前真實數據庫統計：")
        print("="*60)
        print(f"• AI 問答/指令總數 (Claude Code, Codex, Antigravity): {ai_count} 筆")
        print(f"• Git Commits 總數 (遞迴倉庫掃描): {git_count} 筆")
        print(f"• 識別之活躍專案數: {proj_count} 個")
        print("="*60 + "\n")


if __name__ == "__main__":
    run_full_backfill()
