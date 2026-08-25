import os
import re
import json
import time
import hashlib
import threading
from pathlib import Path
from datetime import datetime, timedelta
import logging
from typing import Set, Dict, Any, Optional, List, Tuple

from core.config import get_config
from core.database import get_db
from core.desktop_sources import (
    default_claude_desktop_logs_dir,
    iter_claude_desktop_project_logs,
)
from core.models import AIPromptEvent, IngestionCheckpoint
from core.time_utils import get_local_now

logger = logging.getLogger("OmniContext.AgentLogWatcher")


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


def extract_claude_user_text(content: Any) -> str:
    """提取 Claude Code 的 User Prompt 文字，並過濾純 tool_result 雜訊"""
    if not content:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "tool_result":
                    continue
                txt = item.get("text") or item.get("input_text") or ""
                if txt:
                    texts.append(txt.strip())
            elif isinstance(item, str):
                texts.append(item.strip())
        return "\n".join(texts).strip()
    if isinstance(content, dict):
        if content.get("type") == "tool_result":
            return ""
        return str(content.get("text") or "").strip()
    return ""


def extract_claude_assistant_text(content: Any) -> str:
    """提取 Claude Code 的 Assistant 文字回覆 (包含 block type == 'text')"""
    if not content:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    txt = item.get("text") or ""
                    if txt:
                        texts.append(txt.strip())
            elif isinstance(item, str):
                texts.append(item.strip())
        return "\n".join(texts).strip()
    if isinstance(content, dict):
        if content.get("type") == "text":
            return str(content.get("text") or "").strip()
        return str(content.get("text") or "").strip()
    return ""


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


class AgentLogWatcherService:
    def __init__(self):
        self.cfg = get_config()
        self._running = False
        self._thread: threading.Thread | None = None
        self._processed_hashes: Set[str] = set()
        # Process cache 只是加速；SQLite checkpoint 才是跨重啟的可信狀態。
        self._file_states: Dict[str, Tuple[int, int]] = {}

    def start(self):
        enabled = self.cfg.get("watchers.agent_log_watcher.enabled", True)
        if not enabled:
            logger.info("Agent log watcher is disabled in config.")
            return

        self._running = True
        self._thread = threading.Thread(target=self._scan_loop, daemon=True)
        self._thread.start()
        logger.info("AgentLogWatcher service started (Claude Desktop, Claude Code, Codex sessions, Antigravity with Assistant response parsing).")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            logger.info("AgentLogWatcher service stopped.")

    def _scan_loop(self):
        while self._running:
            try:
                self.scan_all_agents(full_history=False)
            except Exception as e:
                logger.error(f"Error in AgentLogWatcher scan: {e}", exc_info=True)

            for _ in range(60):
                if not self._running:
                    break
                time.sleep(1)

    def _should_scan_file(self, file_path: Path, full_history: bool) -> bool:
        """只比較 checkpoint，不在解析前前移狀態。"""
        if full_history:
            return True
        try:
            stat = file_path.stat()
            current_state = (stat.st_mtime_ns, stat.st_size)
            path_str = str(file_path.resolve())
            if self._file_states.get(path_str) == current_state:
                return False

            db = get_db()
            with db.session_scope() as session:
                checkpoint = session.query(IngestionCheckpoint).filter_by(
                    source_path=path_str
                ).first()
                if checkpoint and (
                    checkpoint.mtime_ns,
                    checkpoint.size_bytes,
                ) == current_state and not checkpoint.last_error:
                    self._file_states[path_str] = current_state
                    return False
            return True
        except Exception:
            return True

    def _mark_file_scanned(self, file_path: Path, error: str | None = None) -> None:
        """成功後才寫入 signature；失敗只寫 error 並保留可重試狀態。"""
        path_str = str(file_path.resolve())
        stat = None
        try:
            stat = file_path.stat()
        except OSError:
            if error is None:
                raise
        db = get_db()
        with db.session_scope() as session:
            checkpoint = session.query(IngestionCheckpoint).filter_by(
                source_path=path_str
            ).first()
            if not checkpoint:
                checkpoint = IngestionCheckpoint(
                    collector="agent_log_watcher",
                    source_path=path_str,
                    mtime_ns=0,
                    size_bytes=0,
                )
                session.add(checkpoint)
            checkpoint.last_error = error
            checkpoint.updated_at = get_local_now()
            if error is None and stat is not None:
                checkpoint.mtime_ns = stat.st_mtime_ns
                checkpoint.size_bytes = stat.st_size
                checkpoint.source_position = stat.st_size
                checkpoint.last_success_at = get_local_now()
                self._file_states[path_str] = (stat.st_mtime_ns, stat.st_size)

    def scan_all_agents(self, full_history: bool = False):
        cfg = get_config()

        # D6 假開關修復：嚴格檢查各 Agent 獨立開關
        if cfg.get("watchers.agent_log_watcher.claude_code", True):
            self.scan_claude_code_logs(full_history=full_history)
        else:
            logger.debug("Claude Code watcher is disabled in config.")

        if cfg.get("watchers.agent_log_watcher.claude_desktop", True):
            self.scan_claude_desktop_logs(full_history=full_history)
        else:
            logger.debug("Claude Desktop watcher is disabled in config.")

        if cfg.get("watchers.agent_log_watcher.codex", True):
            self.scan_codex_logs(full_history=full_history)
        else:
            logger.debug("Codex watcher is disabled in config.")

        if cfg.get("watchers.agent_log_watcher.antigravity", True):
            self.scan_antigravity_logs(full_history=full_history)
        else:
            logger.debug("Antigravity watcher is disabled in config.")

    # =========================================================================
    # 1. Claude Code 日誌解析 (以 projects/**/*.jsonl 為核心成對提取 User 與 Assistant 回應)
    # =========================================================================
    def scan_claude_code_logs(self, full_history: bool = False):
        claude_dir = self.cfg.get_path(
            "watchers.agent_log_watcher.claude_code_logs_path",
            Path.home() / ".claude",
        )
        if not claude_dir.exists():
            return

        projects_dir = claude_dir / "projects"
        project_files = list(projects_dir.glob("**/*.jsonl")) if projects_dir.exists() else []
        has_project_logs = bool(project_files)

        # 1. 優先讀取 projects/**/*.jsonl (成對解析 User 與 Assistant 完整回答)
        if has_project_logs:
            for proj_jsonl in project_files:
                if not self._should_scan_file(proj_jsonl, full_history):
                    continue

                try:
                    self._parse_claude_project_log(get_db(), proj_jsonl, platform="claude_code")
                    self._mark_file_scanned(proj_jsonl)
                except Exception as e:
                    self._mark_file_scanned(proj_jsonl, str(e))
                    logger.debug(f"Error reading Claude project log {proj_jsonl}: {e}")

        # 2. 僅在無 projects 目錄時才以 history.jsonl 作為備援回退
        history_file = claude_dir / "history.jsonl"
        if not has_project_logs and history_file.exists() and self._should_scan_file(history_file, full_history):
            try:
                db = get_db()
                for line_number, item in iter_jsonl_records(history_file):
                    prompt_text = item.get("display") or item.get("text") or item.get("prompt")
                    if not prompt_text or len(prompt_text.strip()) < 2:
                        continue

                    event_time = parse_timestamp_safe(item.get("timestamp"))
                    if not event_time:
                        continue

                    project_path = item.get("project") or item.get("cwd")
                    clean_prompt = prompt_text.strip()
                    hash_key = f"claude_code_hist:{clean_prompt[:50]}:{event_time.strftime('%Y%m%d%H%M')}"
                    if hash_key in self._processed_hashes:
                        continue

                    self._upsert_ai_event(
                        db, platform="claude_code", conv_id=None,
                        prompt=clean_prompt, response=None,
                        cwd=str(project_path) if project_path else None,
                        timestamp=event_time,
                        turn_key=build_turn_key("claude_code", str(history_file), line_number),
                        source_path=str(history_file.resolve()),
                        source_position=line_number,
                        response_status="missing",
                    )
                    self._processed_hashes.add(hash_key)
                self._mark_file_scanned(history_file)
            except Exception as e:
                self._mark_file_scanned(history_file, str(e))
                logger.debug(f"Error reading Claude history.jsonl: {e}")

    def scan_claude_desktop_logs(self, full_history: bool = False):
        """採集 Claude Desktop Cowork/local-agent transcript；不解析雲端聊天 LevelDB cache。"""
        logs_dir = self.cfg.get_path(
            "watchers.agent_log_watcher.claude_desktop_logs_path",
            default_claude_desktop_logs_dir(),
        )
        if not logs_dir.exists():
            return

        db = get_db()
        lookback_days = max(
            1,
            int(self.cfg.get("watchers.agent_log_watcher.claude_desktop_initial_lookback_days", 7)),
        )
        initial_cutoff = get_local_now() - timedelta(days=lookback_days)
        for transcript in iter_claude_desktop_project_logs(logs_dir):
            # 首次啟用只回補近期資料，避免啟動時一次讀取多年、數 GB 的 session 複本；
            # full_history 仍提供明確、可稽核的全量回補途徑。
            if not full_history:
                try:
                    if datetime.fromtimestamp(transcript.stat().st_mtime) < initial_cutoff:
                        continue
                except OSError:
                    continue
            if not self._should_scan_file(transcript, full_history):
                continue
            try:
                self._parse_claude_project_log(db, transcript, platform="claude_desktop")
                self._mark_file_scanned(transcript)
            except Exception as exc:
                self._mark_file_scanned(transcript, str(exc))
                logger.debug(f"Error reading Claude Desktop project log {transcript}: {exc}")

    def _parse_claude_project_log(self, db, project_log: Path, *, platform: str) -> None:
        """將 Claude JSONL 依 user boundary 配對，供 CLI 與 Desktop 共用。"""
        current_user_prompt = ""
        current_user_time = None
        current_cwd = None
        current_session_id = None
        current_user_position = None
        accumulated_responses: List[str] = []
        explicit_final_responses: List[str] = []

        def flush_turn(*, boundary_closed: bool) -> None:
            if not current_user_prompt or not current_user_time:
                return
            explicit_final = select_last_assistant_message(explicit_final_responses)
            full_response = explicit_final or select_last_assistant_message(accumulated_responses) or None
            self._upsert_ai_event(
                db,
                platform=platform,
                conv_id=current_session_id,
                prompt=current_user_prompt,
                response=full_response,
                cwd=current_cwd,
                timestamp=current_user_time,
                turn_key=build_turn_key(platform, str(project_log), current_user_position or 0),
                source_path=str(project_log.resolve()),
                source_position=current_user_position,
                response_status=classify_response_status(
                    full_response,
                    explicit_final=bool(explicit_final),
                    boundary_closed=boundary_closed,
                ),
            )

        for line_number, item in iter_jsonl_records(project_log):
            msg_type = item.get("type")
            timestamp = parse_timestamp_safe(item.get("timestamp") or item.get("createdAt"))
            message = item.get("message", {})
            content = message.get("content") if isinstance(message, dict) else item.get("content")

            if msg_type == "user":
                user_text = extract_claude_user_text(content)
                if user_text and len(user_text) >= 2:
                    flush_turn(boundary_closed=True)
                    current_user_prompt = user_text
                    current_user_time = timestamp or get_local_now()
                    current_cwd = item.get("cwd") or str(project_log.parent)
                    current_session_id = item.get("sessionId")
                    current_user_position = line_number
                    accumulated_responses = []
                    explicit_final_responses = []
            elif msg_type == "assistant":
                assistant_text = extract_claude_assistant_text(content)
                if assistant_text and not assistant_text.startswith("["):
                    accumulated_responses.append(assistant_text)
                    if isinstance(message, dict) and message.get("stop_reason") == "end_turn":
                        explicit_final_responses.append(assistant_text)

        flush_turn(boundary_closed=False)

    # =========================================================================
    # 2. Codex 日誌與 Sessions 全量解析 (支援 2025/2026 所有 Session 與 Assistant 回應)
    # =========================================================================
    def scan_codex_logs(self, full_history: bool = False):
        user_home = Path.home()
        codex_dir = user_home / ".codex"
        if not codex_dir.exists():
            return

        db = get_db()

        # 1. 讀取 history.jsonl
        history_file = codex_dir / "history.jsonl"
        if history_file.exists() and self._should_scan_file(history_file, full_history):
            try:
                for line_number, item in iter_jsonl_records(history_file):
                    prompt_text = item.get("prompt") or item.get("text") or item.get("display")
                    if not prompt_text or len(prompt_text.strip()) < 2:
                        continue

                    event_time = parse_timestamp_safe(item.get("ts") or item.get("timestamp") or item.get("time"))
                    if not event_time:
                        continue

                    cwd = item.get("cwd") or item.get("project")
                    clean_prompt = prompt_text.strip()
                    self._upsert_ai_event(
                        db, platform="codex", conv_id=item.get("session_id"),
                        prompt=clean_prompt, response=None,
                        cwd=str(cwd) if cwd else None, timestamp=event_time,
                        turn_key=build_turn_key("codex", str(history_file), line_number),
                        source_path=str(history_file.resolve()),
                        source_position=line_number,
                        response_status="missing",
                    )
                self._mark_file_scanned(history_file)
            except Exception as e:
                self._mark_file_scanned(history_file, str(e))
                logger.debug(f"Error reading Codex history.jsonl: {e}")

        # 2. 讀取 sessions/**/*.json 與 sessions/**/*.jsonl (包含 2026/08 活躍對話)
        sessions_dir = codex_dir / "sessions"
        if sessions_dir.exists():
            for s_file in sessions_dir.glob("**/*"):
                if not s_file.is_file() or s_file.suffix not in [".json", ".jsonl"]:
                    continue
                if not self._should_scan_file(s_file, full_history):
                    continue

                try:
                    if s_file.suffix == ".json":
                        self._parse_codex_json_session(db, s_file)
                    else:
                        self._parse_codex_jsonl_session(db, s_file)
                    self._mark_file_scanned(s_file)
                except Exception as e:
                    self._mark_file_scanned(s_file, str(e))
                    logger.debug(f"Error parsing Codex session {s_file}: {e}")

    def _parse_codex_json_session(self, db, file_path: Path):
        """解析舊版格式 Codex .json 檔案"""
        with open(file_path, "r", encoding="utf-8", errors="replace") as fp:
            data = json.load(fp)
            session_info = data.get("session", {})
            session_id = session_info.get("id")
            session_time = parse_timestamp_safe(session_info.get("timestamp"))
            items = data.get("items", [])
            current_prompt = ""
            current_time = session_time
            current_position: int | None = None
            assistant_messages: List[str] = []

            def flush_turn(boundary_closed: bool = False) -> None:
                nonlocal current_prompt, current_time, current_position, assistant_messages
                if not current_prompt:
                    return
                response = select_last_assistant_message(assistant_messages) or None
                self._upsert_ai_event(
                    db,
                    platform="codex",
                    conv_id=session_id,
                    prompt=current_prompt,
                    response=response,
                    cwd=None,
                    timestamp=current_time or get_local_now(),
                    turn_key=build_turn_key("codex", str(file_path), current_position or 0),
                    source_path=str(file_path.resolve()),
                    source_position=current_position,
                    response_status=classify_response_status(
                        response,
                        boundary_closed=boundary_closed,
                    ),
                )
                current_prompt = ""
                current_time = None
                current_position = None
                assistant_messages = []

            for item_index, it in enumerate(items, start=1):
                role = it.get("role")
                content = extract_text_from_content(it.get("content"))
                if role == "user" and content:
                    if "<recommended_plugins>" in content or len(content) < 2:
                        continue
                    flush_turn(boundary_closed=True)
                    current_prompt = content
                    current_position = item_index
                elif role == "assistant" and current_prompt:
                    candidate = normalize_assistant_candidate(content)
                    if candidate:
                        assistant_messages.append(candidate)

            flush_turn(boundary_closed=False)

    def _parse_codex_jsonl_session(self, db, file_path: Path):
        """解析 Codex rollout；同一 turn 保留最後一個有效 assistant message。"""
        session_id = None
        session_cwd = None
        current_prompt = ""
        current_time = None
        current_position: int | None = None
        assistant_messages: List[str] = []
        explicit_final_messages: List[str] = []

        def flush_turn(boundary_closed: bool = False) -> None:
            nonlocal current_prompt, current_time, current_position, assistant_messages, explicit_final_messages
            if not current_prompt:
                return
            final_response = select_last_assistant_message(explicit_final_messages)
            response = final_response or select_last_assistant_message(assistant_messages) or None
            self._upsert_ai_event(
                db,
                platform="codex",
                conv_id=session_id,
                prompt=current_prompt,
                response=response,
                cwd=session_cwd,
                timestamp=current_time or get_local_now(),
                turn_key=build_turn_key("codex", str(file_path), current_position or 0),
                source_path=str(file_path.resolve()),
                source_position=current_position,
                response_status=classify_response_status(
                    response,
                    explicit_final=bool(final_response),
                    boundary_closed=boundary_closed,
                ),
            )
            current_prompt = ""
            current_time = None
            current_position = None
            assistant_messages = []
            explicit_final_messages = []

        for line_number, d in iter_jsonl_records(file_path):
            t = d.get("type")
            ts = parse_timestamp_safe(d.get("timestamp"))
            payload = d.get("payload", {})

            if t == "session_meta" and isinstance(payload, dict):
                session_id = payload.get("id")
                session_cwd = payload.get("cwd")

            elif t == "response_item" and isinstance(payload, dict):
                role = payload.get("role")
                content = extract_text_from_content(payload.get("content"))

                if role == "user" and content:
                    if "<recommended_plugins>" in content or len(content) < 2:
                        continue
                    flush_turn(boundary_closed=True)
                    current_prompt = content
                    current_time = ts or get_local_now()
                    current_position = line_number

                elif role == "assistant" and current_prompt:
                    candidate = normalize_assistant_candidate(content)
                    if candidate and candidate not in assistant_messages:
                        assistant_messages.append(candidate)
                    if payload.get("phase") == "final_answer" and candidate:
                        if candidate not in explicit_final_messages:
                            explicit_final_messages.append(candidate)

            elif t == "event_msg" and isinstance(payload, dict):
                p_type = payload.get("type")
                if p_type == "agent_message" and current_prompt:
                    msg_text = extract_text_from_content(payload.get("message") or payload.get("text"))
                    candidate = normalize_assistant_candidate(msg_text)
                    if candidate and candidate not in assistant_messages:
                        assistant_messages.append(candidate)
                elif p_type == "item_completed" and current_prompt:
                    item = payload.get("item", {})
                    if isinstance(item, dict) and item.get("type") == "AgentMessage":
                        msg_text = extract_text_from_content(item.get("content"))
                        candidate = normalize_assistant_candidate(msg_text)
                        if candidate and candidate not in assistant_messages:
                            assistant_messages.append(candidate)
                        if item.get("phase") == "final_answer" and candidate:
                            if candidate not in explicit_final_messages:
                                explicit_final_messages.append(candidate)

        flush_turn(boundary_closed=False)

    # =========================================================================
    # 3. Antigravity 日誌解析 (含 PLANNER_RESPONSE 真實助理回應提取)
    # =========================================================================
    def scan_antigravity_logs(self, full_history: bool = False):
        path_str = self.cfg.get("watchers.agent_log_watcher.antigravity_logs_path")
        if not path_str:
            return

        base_path = self.cfg.expand_path(path_str)
        if not base_path.exists():
            return

        db = get_db()
        for transcript_path in base_path.glob("**/transcript.jsonl"):
            if not self._should_scan_file(transcript_path, full_history):
                continue

            str_path = str(transcript_path)
            conv_id = transcript_path.parent.parent.name
            current_prompt = ""
            current_time = None
            current_position: int | None = None
            latest_real_response = ""
            latest_response_explicit_final = False

            try:
                for line_number, item in iter_jsonl_records(transcript_path):
                    step_type = item.get("type")
                    ts = parse_timestamp_safe(item.get("created_at") or item.get("timestamp"))

                    if step_type == "USER_INPUT":
                        raw_prompt = item.get("content", "")
                        clean_prompt = raw_prompt.strip()
                        if clean_prompt.startswith("<USER_REQUEST>"):
                            clean_prompt = clean_prompt.replace("<USER_REQUEST>", "").replace("</USER_REQUEST>", "").strip()

                        # 過濾系統內部注入訊息與 Checkpoint Summary
                        if "<SYSTEM_MESSAGE>" in clean_prompt or "<CONTEXT_SUMMARY>" in clean_prompt:
                            continue

                        if len(clean_prompt) >= 2:
                            # 遇到新提問：先寫入上一輪提問與其最終真實回答
                            if current_prompt and current_time:
                                self._upsert_ai_event(
                                    db, platform="antigravity", conv_id=conv_id,
                                    prompt=current_prompt, response=latest_real_response if latest_real_response else None,
                                    url=str_path, timestamp=current_time,
                                    turn_key=build_turn_key("antigravity", str(transcript_path), current_position or 0),
                                    source_path=str(transcript_path.resolve()),
                                    source_position=current_position,
                                    response_status=classify_response_status(
                                        latest_real_response,
                                        explicit_final=latest_response_explicit_final,
                                        boundary_closed=True,
                                    ),
                                )
                            current_prompt = clean_prompt
                            current_time = ts or datetime.fromtimestamp(transcript_path.stat().st_mtime)
                            current_position = line_number
                            latest_real_response = ""
                            latest_response_explicit_final = False

                    elif step_type == "PLANNER_RESPONSE":
                        model_content = (item.get("content") or "").strip()
                        # 排除純空字串或工具調用字串，只保留實質結論
                        if model_content and len(model_content) >= 5 and not model_content.startswith("<") and not model_content.startswith("["):
                            latest_real_response = model_content
                            latest_response_explicit_final = item.get("status") == "DONE"

                # 寫入最後一輪
                if current_prompt and current_time:
                    self._upsert_ai_event(
                        db, platform="antigravity", conv_id=conv_id,
                        prompt=current_prompt, response=latest_real_response if latest_real_response else None,
                        url=str_path, timestamp=current_time,
                        turn_key=build_turn_key("antigravity", str(transcript_path), current_position or 0),
                        source_path=str(transcript_path.resolve()),
                        source_position=current_position,
                        response_status=classify_response_status(
                            latest_real_response,
                            explicit_final=latest_response_explicit_final,
                            boundary_closed=False,
                        ),
                    )
                self._mark_file_scanned(transcript_path)
            except Exception as e:
                self._mark_file_scanned(transcript_path, str(e))
                logger.debug(f"Could not read transcript {transcript_path}: {e}")

    # =========================================================================
    # 通用 Upsert 方法：建立或更新 AI 對話與助理回應
    # =========================================================================
    def _upsert_ai_event(
        self, db, platform: str, conv_id: Optional[str],
        prompt: str, response: Optional[str],
        cwd: Optional[str] = None, url: Optional[str] = None,
        timestamp: Optional[datetime] = None,
        turn_key: Optional[str] = None,
        source_path: Optional[str] = None,
        source_position: Optional[int] = None,
        response_status: Optional[str] = None,
    ):
        # 先脫殼再判斷：避免把包在標籤裡的真實提問誤判為雜訊
        clean_prompt = clean_prompt_text(prompt)
        if len(clean_prompt) < 2:
            return

        # 在寫入前就擋掉 CLI 內部訊息，避免污染活動流與 LLM 日報的輸入
        if is_cli_artifact(clean_prompt):
            return

        # 清洗 response：嚴格過濾以 [ 開頭之工具調用字串與佔位符
        clean_resp = normalize_assistant_candidate(response) or None
        normalized_status = response_status or ("final_candidate" if clean_resp else "missing")
        if normalized_status not in {"missing", "partial", "final_candidate"}:
            normalized_status = "partial" if clean_resp else "missing"

        # 針對 Documents/Codex 等一次性暫存目錄做正規化標籤
        tag = None
        if cwd:
            p_cwd = Path(cwd)
            if "Documents" in cwd and "Codex" in cwd:
                tag = "Codex Automations"
            else:
                tag = p_cwd.name or str(cwd)
        elif platform == "antigravity":
            tag = "Agent Development"

        event_time = timestamp or get_local_now()

        with db.session_scope() as session:
            # 有 provenance 時以 stable turn_key 區分同 conversation 的重複 prompt。
            if turn_key:
                query = session.query(AIPromptEvent).filter(AIPromptEvent.turn_key == turn_key)
            else:
                query = session.query(AIPromptEvent).filter(
                    AIPromptEvent.platform == platform,
                    AIPromptEvent.prompt_text == clean_prompt,
                )
                if conv_id:
                    query = query.filter(AIPromptEvent.conversation_id == conv_id)

            existing = query.first()
            if not existing and turn_key:
                legacy_query = session.query(AIPromptEvent).filter(
                    AIPromptEvent.turn_key.is_(None),
                    AIPromptEvent.platform == platform,
                    AIPromptEvent.prompt_text == clean_prompt,
                    AIPromptEvent.timestamp == event_time,
                )
                if conv_id:
                    legacy_query = legacy_query.filter(
                        AIPromptEvent.conversation_id == conv_id
                    )
                existing = legacy_query.first()
            if not existing:
                session.add(AIPromptEvent(
                    platform=platform,
                    url=url,
                    conversation_id=conv_id,
                    prompt_text=clean_prompt,
                    response_text=clean_resp,
                    project_tag=tag,
                    cwd=cwd,
                    timestamp=event_time,
                    turn_key=turn_key,
                    source_path=source_path,
                    source_position=source_position,
                    response_status=normalized_status,
                ))
            else:
                # stable turn 每次都依完整來源重算，允許錯誤 final 降回 partial。
                if clean_resp:
                    existing.response_text = clean_resp
                    existing.response_status = normalized_status
                elif not clean_resp and not existing.response_text:
                    existing.response_text = None
                    existing.response_status = "missing"
                if cwd and not existing.cwd:
                    existing.cwd = cwd
                if tag and not existing.project_tag:
                    existing.project_tag = tag
                if url and not existing.url:
                    existing.url = url
                if turn_key and not existing.turn_key:
                    existing.turn_key = turn_key
                if source_path:
                    existing.source_path = source_path
                if source_position is not None:
                    existing.source_position = source_position
