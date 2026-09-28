"""**轉接層**：`watchers.transcripts` 已經搬到 `coding_agent_transcripts` 套件（ADR-033）。

格式知識住在套件裡，這裡只負責讓既有的 import 路徑繼續有效。**新程式碼請直接 import
套件**，不要走這一層。移除條件：本 repo 內部全部改成直接引用套件之後就刪掉。
目前還指著這裡的是 `watchers/agent_log_watcher.py` 與兩支測試——而那兩支測試**一字不改**
正是 TODO E5 的完成判準，所以它們會是最後搬的。

---

**轉接層必須給的是「同一個模組」，不是「一個長得一樣的模組」。**

ADR-033 決策四原本選的是「每個子模組留一個薄轉接檔」，理由是它比在這裡塞 `sys.modules`
誠實（看得到才 import 得到）。那個判斷是根據**三種 import 形式都過得了**做出來的，
而那個實驗漏掉了一件事：**同一性**。

實測（先做了才知道）：

    watchers.transcripts.codex is coding_agent_transcripts.codex   -> False
    轉接層上 monkeypatch codex_home 之後，套件裡的 discover     -> 看不到

因為薄轉接檔是把套件的命名空間**複製**進自己的 globals，而 `discover()` 在執行時是去
**套件模組**的 globals 找 `codex_home`。所以 `monkeypatch.setattr(codex, "codex_home", …)`
打在複製品上，打不到本尊——`tests/test_transcript_parsers_and_drift.py` 的漂移測試就是這樣
打樁的，它當場紅給我看。

所以改用 `sys.modules` 別名：`watchers.transcripts.codex` **就是**
`coding_agent_transcripts.codex` 那個物件。代價是「這個模組存在」變成這裡的一個副作用
（目錄裡看不到 `codex.py`），那個代價是真的，寫在這裡不假裝沒有。

`from a.b.c import X` 會真的去 import 模組 `a.b.c`，而 import `watchers.transcripts.codex`
必定先 import 父套件 `watchers.transcripts`——也就是先跑完下面這段——所以別名一定來得及。
"""

from __future__ import annotations

import sys

import coding_agent_transcripts as _pkg
from coding_agent_transcripts import (
    DRIFT_WINDOW_DAYS,
    SOURCE_KEYS,
    SOURCES,
    TranscriptSource,
    TranscriptTurn,
    TurnEvidence,
    build_turn_key,
    classify_response_status,
    clean_prompt_text,
    empty_drift,
    eof_response_status,
    evaluate_drift,
    is_cli_artifact,
    iter_jsonl_records,
    normalize_assistant_candidate,
    parse_timestamp_safe,
    select_last_assistant_message,
)
from coding_agent_transcripts import (  # noqa: F401 - 這些名字就是舊的子模組
    antigravity,
    base,
    claude_code,
    claude_desktop,
    codex,
    drift,
)

# 舊的子模組路徑 → 套件裡**同一個**模組物件（不是複製品，見上面的實測）。
for _name in ("base", "antigravity", "claude_code", "claude_desktop", "codex", "drift"):
    sys.modules[f"{__name__}.{_name}"] = getattr(_pkg, _name)
del _name

__all__ = [
    "SOURCES",
    "SOURCE_KEYS",
    "TranscriptSource",
    "TranscriptTurn",
    "TurnEvidence",
    "DRIFT_WINDOW_DAYS",
    "build_turn_key",
    "classify_response_status",
    "clean_prompt_text",
    "empty_drift",
    "eof_response_status",
    "evaluate_drift",
    "is_cli_artifact",
    "iter_jsonl_records",
    "normalize_assistant_candidate",
    "parse_timestamp_safe",
    "select_last_assistant_message",
]
