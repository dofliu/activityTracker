"""一次性清理歷史遺留的污染資料（冪等，可重複執行）

處理兩類問題：
1. seed-demo 殘留的假視窗事件（clear-demo 當初漏掉 window_events）
2. Agent CLI 內部訊息被當成使用者提問寫入 ai_prompt_events
"""
import sys
from pathlib import Path

# 強制 Windows 控制台輸出支援 UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import get_db
from core.models import AIPromptEvent, WindowEvent
from watchers.agent_log_watcher import is_cli_artifact, clean_prompt_text


def purge_demo_window_events(session) -> int:
    """刪除 seed-demo 寫入的假視窗事件

    這批資料的特徵是虛構的應用程式與檔名（aaai2026_draft.tex、omni-context），
    且微秒固定為 .370550（同一次 seed 迴圈產生）。
    """
    demo = session.query(WindowEvent).filter(
        (WindowEvent.window_title.like("%aaai2026_draft.tex%")) |
        (WindowEvent.window_title.like("%omni-context - Visual Studio Code%")) |
        (WindowEvent.app_name == "TestApp")
    )
    count = demo.count()
    demo.delete(synchronize_session=False)
    return count


def purge_cli_artifact_prompts(session) -> int:
    """刪除 Agent CLI 內部訊息（<command-name>、<local-command-stdout> 等）

    採集端已加上過濾，此處清理過濾機制上線前寫入的歷史資料。
    """
    removed = 0
    for event in session.query(AIPromptEvent).all():
        original = event.prompt_text or ""
        cleaned = clean_prompt_text(original)

        if len(cleaned) < 2 or is_cli_artifact(cleaned):
            session.delete(event)
            removed += 1
        elif cleaned != original:
            # 包裹標籤內是真實提問：脫殼保留，不刪除
            event.prompt_text = cleaned
    return removed


def main():
    db = get_db()
    with db.session_scope() as session:
        win_removed = purge_demo_window_events(session)
        cli_removed = purge_cli_artifact_prompts(session)

    print("=" * 60)
    print("🧹 歷史污染資料清理完成")
    print("=" * 60)
    print(f"• 假視窗事件 (seed-demo / TestApp)  : 刪除 {win_removed} 筆")
    print(f"• CLI 內部訊息誤存為提問             : 刪除 {cli_removed} 筆")
    print("=" * 60)
    if win_removed == 0 and cli_removed == 0:
        print("（資料庫已乾淨，本次無需清理）")


if __name__ == "__main__":
    main()
