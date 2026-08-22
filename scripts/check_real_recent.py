import sys
import json
from pathlib import Path
from datetime import datetime, timedelta

# 強制 Windows 控制台輸出支援 UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).parent.parent))

def parse_iso_or_epoch(val):
    if not val:
        return None
    if isinstance(val, (int, float)):
        # epoch ms vs epoch s
        if val > 1e11:
            return datetime.fromtimestamp(val / 1000.0)
        else:
            return datetime.fromtimestamp(val)
    if isinstance(val, str):
        # 2026-06-15T07:59:35.410Z or 2026-06-17T16:49:35Z
        val_clean = val.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(val_clean)
            # 轉為本地無時區時間 (UTC+8)
            if dt.tzinfo:
                dt = dt.astimezone().replace(tzinfo=None)
            return dt
        except Exception:
            return None
    return None

now = datetime.now()
recent_cutoff = now - timedelta(days=2)
print(f"Checking events since {recent_cutoff}...")

# 1. Claude history
claude_hist = Path.home() / ".claude" / "history.jsonl"
claude_recent = []
if claude_hist.exists():
    with open(claude_hist, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if not line.strip(): continue
            try:
                d = json.loads(line)
                dt = parse_iso_or_epoch(d.get("timestamp"))
                if dt and dt >= recent_cutoff:
                    claude_recent.append((dt, d.get("display") or d.get("text")))
            except Exception: pass

print(f"Claude history in last 2 days: {len(claude_recent)}")
for t, msg in claude_recent[-10:]:
    print(f"  [{t}] {str(msg)[:60]}")

# 2. Claude projects
claude_proj_recent = []
claude_proj = Path.home() / ".claude" / "projects"
if claude_proj.exists():
    for p in claude_proj.glob("**/*.jsonl"):
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if not line.strip(): continue
                try:
                    d = json.loads(line)
                    if d.get("type") == "user":
                        dt = parse_iso_or_epoch(d.get("timestamp"))
                        if dt and dt >= recent_cutoff:
                            msg = d.get("message", {})
                            content = msg.get("content") if isinstance(msg, dict) else str(msg)
                            claude_proj_recent.append((dt, d.get("cwd"), content))
                except Exception: pass

print(f"Claude projects in last 2 days: {len(claude_proj_recent)}")
for t, cwd, msg in claude_proj_recent[-10:]:
    print(f"  [{t}] [{Path(cwd).name if cwd else ''}] {str(msg)[:60]}")

# 3. Antigravity transcripts
agy_recent = []
agy_dir = Path.home() / ".gemini" / "antigravity" / "brain"
if agy_dir.exists():
    for tp in agy_dir.glob("**/transcript.jsonl"):
        with open(tp, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "USER_INPUT" in line:
                    try:
                        d = json.loads(line)
                        dt = parse_iso_or_epoch(d.get("created_at") or d.get("timestamp"))
                        if dt and dt >= recent_cutoff:
                            agy_recent.append((dt, d.get("content")))
                    except Exception: pass

print(f"Antigravity transcripts in last 2 days: {len(agy_recent)}")
for t, msg in agy_recent[-10:]:
    print(f"  [{t}] {str(msg)[:60]}")

# 4. Git Commits
import git
from watchers.git_watcher import discover_git_repos
repos = discover_git_repos(["D:\\Project_CodingSimulation"])
git_recent = []
for r in repos:
    try:
        repo = git.Repo(str(r))
        for c in repo.iter_commits(max_count=20):
            dt = datetime.fromtimestamp(c.committed_date)
            if dt >= recent_cutoff:
                git_recent.append((dt, r.name, c.summary))
    except Exception: pass

print(f"Git commits in last 2 days: {len(git_recent)}")
for t, rname, sm in git_recent[-10:]:
    print(f"  [{t}] [{rname}] {sm[:60]}")
