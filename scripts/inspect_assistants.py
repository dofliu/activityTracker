import json
from pathlib import Path

print("=== 1. Test Claude Project Assistant extraction ===")
claude_proj = Path.home() / ".claude" / "projects"
if claude_proj.exists():
    for f in list(claude_proj.glob("**/*.jsonl"))[-2:]:
        print(f"\nClaude Project file: {f.name}")
        with open(f, 'r', encoding='utf-8', errors='ignore') as fp:
            for line in fp:
                d = json.loads(line)
                if d.get("type") == "assistant":
                    msg = d.get("message", {})
                    content = msg.get("content") if isinstance(msg, dict) else str(msg)
                    print("  Claude Assistant msg type:", type(content))
                    if isinstance(content, list):
                        for item in content:
                            if isinstance(item, dict) and item.get("type") == "text":
                                print("  Claude text:", item.get("text")[:100])
                    elif isinstance(content, str):
                        print("  Claude text:", content[:100])
                    break

print("\n=== 2. Test Codex Sessions Assistant extraction ===")
codex_sessions = Path.home() / ".codex" / "sessions"
if codex_sessions.exists():
    for f in list(codex_sessions.glob("2026/**/*.jsonl"))[-2:]:
        print(f"\nCodex Session file: {f.name}")
        with open(f, 'r', encoding='utf-8', errors='ignore') as fp:
            for line in fp:
                d = json.loads(line)
                payload = d.get("payload", {})
                if isinstance(payload, dict):
                    if payload.get("role") == "assistant" or payload.get("type") == "agent_message":
                        content = payload.get("content") or payload.get("message") or payload.get("text")
                        print("  Codex Assistant msg:", str(content)[:120])
                        break

print("\n=== 3. Test Antigravity Assistant extraction ===")
agy_brain = Path.home() / ".gemini" / "antigravity" / "brain"
if agy_brain.exists():
    for tp in list(agy_brain.glob("**/transcript.jsonl"))[-2:]:
        print(f"\nAntigravity transcript: {tp.parent.parent.name}")
        with open(tp, 'r', encoding='utf-8', errors='ignore') as fp:
            for line in fp:
                d = json.loads(line)
                if d.get("type") == "PLANNER_RESPONSE":
                    content = d.get("content", "")
                    tool_calls = d.get("tool_calls", [])
                    print(f"  PLANNER_RESPONSE content: {content[:100]}, tool_calls: {len(tool_calls)}")
                    break
