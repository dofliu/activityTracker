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

from core.database import get_db
from core.models import FileActivityEvent, AIPromptEvent, GitActivityEvent, WindowEvent


def cleanup_noise_and_demo_data():
    db = get_db()
    with db.session_scope() as session:
        # 1. 清理檔案噪音 (.codex, site-packages, node_modules, .venv, dist-info, Documents/Codex 暫存)
        noise_query = session.query(FileActivityEvent).filter(
            (FileActivityEvent.file_path.like("%/.codex/%")) |
            (FileActivityEvent.file_path.like("%\\.codex\\%")) |
            (FileActivityEvent.file_path.like("%/Documents/Codex/%")) |
            (FileActivityEvent.file_path.like("%\\Documents\\Codex\\%")) |
            (FileActivityEvent.file_path.like("%/site-packages/%")) |
            (FileActivityEvent.file_path.like("%\\.venv\\%")) |
            (FileActivityEvent.file_path.like("%\\__pycache__\\%")) |
            (FileActivityEvent.file_path.like("%\\.git\\%")) |
            (FileActivityEvent.file_path.like("%\\.gemini\\%"))
        )
        noise_count = noise_query.count()
        noise_query.delete(synchronize_session=False)

        # 2. 清理 seed-demo 假資料 (若存在)
        demo_ai = session.query(AIPromptEvent).filter(
            (AIPromptEvent.prompt_text.like("%多智能體協作定理 2%")) |
            (AIPromptEvent.prompt_text.like("%FastAPI 中處理高頻本機日誌%")) |
            (AIPromptEvent.prompt_text.like("%自動搜尋 2025-2026 年關於 Personal Context Agent%"))
        ).delete(synchronize_session=False)

        demo_git = session.query(GitActivityEvent).filter(
            GitActivityEvent.commit_hash == "a1b2c3d4"
        ).delete(synchronize_session=False)

        demo_files = session.query(FileActivityEvent).filter(
            FileActivityEvent.file_name == "aaai2026_draft.tex"
        ).delete(synchronize_session=False)

        print(f"[SUCCESS] 噪音清理完成：刪除 {noise_count} 筆檔案噪音事件，清除 {demo_ai} 筆示範 AI 資料與 {demo_git} 筆示範 Git commit。")


if __name__ == "__main__":
    cleanup_noise_and_demo_data()
