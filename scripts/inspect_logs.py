import json
from pathlib import Path
from datetime import datetime

print("=== 1. Claude history.jsonl ===")
claude_hist = Path.home() / ".claude" / "history.jsonl"
if claude_hist.exists():
    with open(claude_hist, "r", encoding="utf-8", errors="ignore") as f:
        lines = [line.strip() for line in f if line.strip()]
        print(f"Total lines in claude history: {len(lines)}")
        for l in lines[-5:]:
            d = json.loads(l)
            ts = d.get("timestamp")
            dt = datetime.fromtimestamp(ts/1000.0) if ts else None
            disp = d.get("display") or ""
            print(f"  [{dt}] {disp[:60]}")

print("\n=== 2. Claude projects/*.jsonl ===")
claude_proj = Path.home() / ".claude" / "projects"
if claude_proj.exists():
    jsonls = list(claude_proj.glob("**/*.jsonl"))
    print(f"Total project jsonl files: {len(jsonls)}")
    if jsonls:
        with open(jsonls[-1], "r", encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f):
                if i > 5: break
                d = json.loads(line)
                print("  Keys:", list(d.keys()), "created:", d.get("createdAt") or d.get("timestamp"))

print("\n=== 3. Codex sessions / history ===")
codex_dir = Path.home() / ".codex"
if codex_dir.exists():
    codex_hist = codex_dir / "history.jsonl"
    if codex_hist.exists():
        with open(codex_hist, "r", encoding="utf-8", errors="ignore") as f:
            lines = [line.strip() for line in f if line.strip()]
            print(f"Total lines in codex history: {len(lines)}")
            for l in lines[-5:]:
                d = json.loads(l)
                print("  Codex item:", d)

print("\n=== 4. Antigravity transcripts ===")
agy_dir = Path.home() / ".gemini" / "antigravity" / "brain"
if agy_dir.exists():
    transcripts = list(agy_dir.glob("**/transcript.jsonl"))
    print(f"Total transcripts: {len(transcripts)}")
    if transcripts:
        # Check first and last
        for tp in [transcripts[0], transcripts[-1]]:
            mtime = datetime.fromtimestamp(tp.stat().st_mtime)
            print(f"Transcript path: {tp.parent.parent.name}, mtime: {mtime}")
            with open(tp, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if "USER_INPUT" in line:
                        d = json.loads(line)
                        print("  keys in transcript step:", list(d.keys()))
                        print("  time fields:", d.get("timestamp") or d.get("time") or d.get("created_at"))
                        break

print("\n=== 5. Database ai_prompt_events date distribution ===")
from core.database import get_db
from core.models import AIPromptEvent
db = get_db()
with db.session_scope() as session:
    events = session.query(AIPromptEvent).all()
    dates = {}
    for e in events:
        d_str = e.timestamp.strftime("%Y-%m-%d")
        dates[d_str] = dates.get(d_str, 0) + 1
    print("AI Prompt Events date distribution:", sorted(dates.items()))
