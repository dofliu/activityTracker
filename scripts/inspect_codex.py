import json
from pathlib import Path
from datetime import datetime

codex_2026 = list((Path.home() / '.codex' / 'sessions').glob('2026/**/*.json*'))
print(f"Total 2026 codex session files: {len(codex_2026)}")
if codex_2026:
    for f in codex_2026[-3:]:
        print(f"\n=== File: {f.name} ===")
        with open(f, 'r', encoding='utf-8', errors='ignore') as fp:
            if f.suffix == '.json':
                d = json.load(fp)
                print("JSON format, keys:", list(d.keys()))
            else:
                for i, line in enumerate(fp):
                    if i > 12: break
                    try:
                        d = json.loads(line)
                        t = d.get("type")
                        ts = d.get("timestamp")
                        payload = d.get("payload", {})
                        p_type = payload.get("type") if isinstance(payload, dict) else None
                        role = payload.get("role") if isinstance(payload, dict) else None
                        print(f"Line {i} [{ts}]: type={t}, payload.type={p_type}, role={role}")
                        if t == 'session_meta':
                            print("  cwd:", payload.get("cwd"))
                        if role in ['user', 'assistant']:
                            c = payload.get("content")
                            print(f"  {role} content snippet: {str(c)[:120]}")
                    except Exception as e:
                        print("  err:", e)
