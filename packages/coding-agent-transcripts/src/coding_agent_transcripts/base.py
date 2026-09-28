"""transcript parser 的共同介面與跨平台工具（ADR-025，TODO D9）。

每個平台模組（`claude_code`／`claude_desktop`／`codex`／`antigravity`）只實作兩件事：

    discover(cfg, *, full_history) -> Iterable[Path]        這個平台的 transcript 檔在哪
    parse(path, *, cfg)            -> Iterator[TranscriptTurn]   這個檔案裡有哪些輪次

`parse` 是產生器而且**不碰資料庫**：它把一輪對話描述成 :class:`TranscriptTurn`，由
`AgentLogWatcherService` 決定 checkpoint 與寫入。這樣每個 parser 都能在沒有資料庫、
沒有設定檔的情況下單獨測。

這個模組本身不認識任何一種格式——只有四種格式都用得到的東西才放這裡。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 契約
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TurnEvidence:
    """背景 Agent 任務的時間證據（ADR-010）。

    只有在**來源本身**帶了可驗證的起始時間時才附上——掃描時間不能拿來補值，
    所以 parser 沒把握就不要建這個物件。
    """

    started_at: datetime
    start_position: Optional[int] = None
    session_id: Optional[str] = None
    cwd: Optional[str] = None
    completed_at: Optional[datetime] = None
    end_position: Optional[int] = None
    completion_evidence_kind: Optional[str] = None


@dataclass(frozen=True)
class TranscriptTurn:
    """一輪「使用者提問 ＋ 助理回應」，連同它的出處。

    `response_status` 已由 parser 依 :func:`classify_response_status` 或
    :func:`eof_response_status` 判定——服務層只做最後的健全性檢查，不重新猜。
    """

    platform: str
    prompt: str
    timestamp: datetime
    source_path: str
    response: Optional[str] = None
    response_status: Optional[str] = None
    source_position: Optional[int] = None
    turn_key: Optional[str] = None
    conv_id: Optional[str] = None
    cwd: Optional[str] = None
    url: Optional[str] = None
    evidence: Optional[TurnEvidence] = None
    # 行程內去重用的鍵（目前只有 Claude Code 的 history.jsonl 備援需要）；
    # 跨重啟的去重靠 turn_key 與 SQLite，不靠這個。
    dedupe_key: Optional[str] = None


@dataclass(frozen=True)
class TranscriptSource:
    """一個平台的採集契約：設定鍵、人類看的名字，以及探索與解析兩個函式。"""

    key: str
    label: str
    discover: Callable[..., Iterable[Path]]
    parse: Callable[..., Iterator[TranscriptTurn]]


# ---------------------------------------------------------------------------
# 時間與文字
# ---------------------------------------------------------------------------


def parse_timestamp_safe(val: Any) -> Optional[datetime]:
    """精準解析各種格式的時間戳 (Epoch s, Epoch ms, ISO string)，並轉為本地無時區 datetime"""
    if not val:
        return None
    try:
        if isinstance(val, (int, float)):
            if val > 1e11:
                return datetime.fromtimestamp(val / 1000.0)
            else:
                return datetime.fromtimestamp(val)

        if isinstance(val, str):
            val_clean = val.strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(val_clean)
            if dt.tzinfo:
                dt = dt.astimezone().replace(tzinfo=None)
            return dt
    except Exception:
        pass
    return None


def extract_text_from_content(content: Any) -> str:
    """從不同 AI 的 message content 結構中提取純文字字串"""
    if not content:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                # Claude / Codex content items
                txt = item.get("text") or item.get("input_text") or item.get("output_text") or ""
                if txt:
                    parts.append(txt)
        return "\n".join(parts).strip()
    if isinstance(content, dict):
        return str(content.get("text") or content.get("content") or "").strip()


def normalize_assistant_candidate(text: str | None) -> str:
    """只保留可作為人類可讀回應的 assistant message。"""
    candidate = (text or "").strip()
    if len(candidate) < 3 or candidate.startswith("[") or candidate.startswith("<"):
        return ""
    return candidate


def select_last_assistant_message(messages: List[str]) -> str:
    """同一 turn 以最後一個有效 assistant message 作為 final candidate。"""
    for message in reversed(messages):
        candidate = normalize_assistant_candidate(message)
        if candidate:
            return candidate
    return ""


def build_turn_key(platform: str, source_path: str, source_position: int) -> str:
    raw = f"{platform}|{Path(source_path).resolve()}|{source_position}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def eof_response_status(file_path: Path, response: str | None, settle_seconds: int = 120) -> str:
    if not response:
        return "missing"
    return "partial" if time.time() - file_path.stat().st_mtime < settle_seconds else "final_candidate"


def classify_response_status(
    response: str | None,
    *,
    explicit_final: bool = False,
    boundary_closed: bool = False,
) -> str:
    """明確 final marker 優先；沒有 marker 時，只有下一個 user turn 能封閉前一輪。"""
    if not response:
        return "missing"
    if explicit_final or boundary_closed:
        return "final_candidate"
    return "partial"


def iter_jsonl_records(file_path: Path):
    """逐行解析 JSONL；任何壞行都讓 checkpoint 保持 error，禁止靜默前移。"""
    malformed: List[Tuple[int, str]] = []
    with open(file_path, "r", encoding="utf-8", errors="replace") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                yield line_number, json.loads(line)
            except json.JSONDecodeError as exc:
                malformed.append((line_number, exc.msg))
    if malformed:
        preview = ", ".join(f"line {line}: {reason}" for line, reason in malformed[:3])
        raise ValueError(f"Malformed JSONL ({len(malformed)} lines): {preview}")


# CLI 內部訊息的包裹標籤：這些是 Agent 工具自己產生的系統訊息，不是使用者的提問。
# 若不在採集端過濾，它們會出現在活動流，並被當成「今日提問」餵進 LLM 日報。
CLI_ARTIFACT_PREFIXES = (
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<local-command-stdout>",
    "<local-command-stderr>",
    "<local-command-caveat>",
    "<task-notification>",
    "<system-reminder>",
    "<bash-input>",
    "<bash-stdout>",
    "<bash-stderr>",
    "<user-memory-input>",
    "caveat: the messages below were generated",
    "[request interrupted by user",
    # Codex CLI 內部訊息
    "<codex_internal",
    "<scheduled-task",
    "<environment_context>",
    "<heartbeat>",
    "<turn_aborted>",
    "<create-pr-command>",
    "<image>",
    "<skill>",
    "<in-app-browser-context",
)

# Antigravity 會把真正的提問包在標籤裡，這些內容要保留，只是需要脫殼
_UNWRAP_PATTERNS = (
    re.compile(r"</?USER_REQUEST>", re.IGNORECASE),
    re.compile(r"<ADDITIONAL_METADATA>.*?</ADDITIONAL_METADATA>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<ATTACHED_FILES>.*?</ATTACHED_FILES>", re.IGNORECASE | re.DOTALL),
)


def clean_prompt_text(text: str) -> str:
    """脫去 Agent 加在使用者提問外層的包裹標籤，保留真正的內容"""
    if not text:
        return ""
    cleaned = text
    for pattern in _UNWRAP_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    return cleaned.strip()


# 無參數的斜線指令（/login、/compact、/model…）只代表操作，不帶工作內容
BARE_SLASH_COMMAND = re.compile(r"^/[a-zA-Z][\w-]*\s*$")


def is_cli_artifact(text: str) -> bool:
    """判斷一段文字是否為 Agent CLI 的內部訊息而非真實使用者提問"""
    if not text:
        return True

    lowered = text.strip().lower()
    if lowered.startswith(CLI_ARTIFACT_PREFIXES):
        return True
    if BARE_SLASH_COMMAND.match(text.strip()):
        return True
    return False
