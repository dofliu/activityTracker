import sys
from pathlib import Path
from datetime import timedelta

# 將專案根目錄加入 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import get_db
from core.models import FileActivityEvent, WindowEvent, AIPromptEvent, GitActivityEvent


def migrate_timezone():
    """一次性遷移修正舊事件時區 (UTC -> 本地時間 +8hr)"""
    db = get_db()
    with db.session_scope() as session:
        # 建立遷移標記檢查，避免重複執行
        flag_file = Path(__file__).parent / ".tz_migrated"
        if flag_file.exists():
            print("ℹ️ 時區遷移先前已執行完畢，略過重複遷移。")
            return

        file_events = session.query(FileActivityEvent).all()
        for f in file_events:
            f.timestamp = f.timestamp + timedelta(hours=8)

        win_events = session.query(WindowEvent).all()
        for w in win_events:
            w.start_time = w.start_time + timedelta(hours=8)
            w.end_time = w.end_time + timedelta(hours=8)

        ai_events = session.query(AIPromptEvent).all()
        for a in ai_events:
            a.timestamp = a.timestamp + timedelta(hours=8)

        flag_file.write_text("migrated", encoding="utf-8")
        print(f"✅ 時區遷移完成：已將 {len(file_events)} 筆檔案、{len(win_events)} 筆視窗與 {len(ai_events)} 筆 AI 事件時間校準為本地時間。")


if __name__ == "__main__":
    migrate_timezone()
