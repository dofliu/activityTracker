import os
import json
import time
import threading
from pathlib import Path
from datetime import datetime
import logging
from typing import Set, Dict, Any, Optional, List, Tuple

from core.config import get_config
from core.database import get_db
from core.models import AIPromptEvent
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


class AgentLogWatcherService:
    def __init__(self):
        self.cfg = get_config()
        self._running = False
        self._thread: threading.Thread | None = None
        self._processed_hashes: Set[str] = set()
        # 增量掃描快取：記錄檔案的 (mtime, size) 避免重複讀取無變更檔案
        self._file_states: Dict[str, Tuple[float, int]] = {}

    def start(self):
        enabled = self.cfg.get("watchers.agent_log_watcher.enabled", True)
        if not enabled:
            logger.info("Agent log watcher is disabled in config.")
            return

        self._running = True
        self._thread = threading.Thread(target=self._scan_loop, daemon=True)
        self._thread.start()
        logger.info("AgentLogWatcher service started (Claude Code, Codex sessions, Antigravity with Assistant response parsing).")

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
        """增量檔案檢查：未變更的檔案直接跳過"""
        if full_history:
            return True
        try:
            stat = file_path.stat()
            current_state = (stat.st_mtime, stat.st_size)
            path_str = str(file_path)
            if self._file_states.get(path_str) == current_state:
                return False
            self._file_states[path_str] = current_state
            return True
        except Exception:
            return True

    def scan_all_agents(self, full_history: bool = False):
        cfg = get_config()

        # D6 假開關修復：嚴格檢查各 Agent 獨立開關
        if cfg.get("watchers.agent_log_watcher.claude_code", True):
            self.scan_claude_code_logs(full_history=full_history)
        else:
            logger.debug("Claude Code watcher is disabled in config.")

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
        user_home = Path.home()
        claude_dir = user_home / ".claude"
        if not claude_dir.exists():
            return

        db = get_db()
        projects_dir = claude_dir / "projects"
        has_scanned_projects = False

        # 1. 優先讀取 projects/**/*.jsonl (成對解析 User 與 Assistant 完整回答)
        if projects_dir.exists():
            for proj_jsonl in projects_dir.glob("**/*.jsonl"):
                if not self._should_scan_file(proj_jsonl, full_history):
                    continue

                try:
                    current_user_prompt = ""
                    current_user_time = None
                    current_cwd = None
                    current_session_id = None
                    accumulated_responses: List[str] = []

                    with open(proj_jsonl, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                item = json.loads(line)
                                msg_type = item.get("type")
                                ts = parse_timestamp_safe(item.get("timestamp") or item.get("createdAt"))

                                if msg_type == "user":
                                    msg = item.get("message", {})
                                    content = msg.get("content") if isinstance(msg, dict) else item.get("content")
                                    user_text = extract_claude_user_text(content)

                                    if user_text and len(user_text) >= 2:
                                        if current_user_prompt and current_user_time:
                                            full_resp = "\n\n".join(accumulated_responses).strip() if accumulated_responses else None
                                            self._upsert_ai_event(
                                                db, platform="claude_code", conv_id=current_session_id,
                                                prompt=current_user_prompt, response=full_resp,
                                                cwd=current_cwd, timestamp=current_user_time
                                            )
                                        current_user_prompt = user_text
                                        current_user_time = ts or get_local_now()
                                        current_cwd = item.get("cwd") or (str(proj_jsonl.parent) if proj_jsonl.parent else None)
                                        current_session_id = item.get("sessionId")
                                        accumulated_responses = []

                                elif msg_type == "assistant":
                                    msg = item.get("message", {})
                                    content = msg.get("content") if isinstance(msg, dict) else item.get("content")
                                    assistant_text = extract_claude_assistant_text(content)
                                    if assistant_text and not assistant_text.startswith("["):
                                        accumulated_responses.append(assistant_text)
                            except Exception:
                                continue

                        # 處理最後一筆
                        if current_user_prompt and current_user_time:
                            full_resp = "\n\n".join(accumulated_responses).strip() if accumulated_responses else None
                            self._upsert_ai_event(
                                db, platform="claude_code", conv_id=current_session_id,
                                prompt=current_user_prompt, response=full_resp,
                                cwd=current_cwd, timestamp=current_user_time
                            )
                    has_scanned_projects = True
                except Exception as e:
                    logger.debug(f"Error reading Claude project log {proj_jsonl}: {e}")

        # 2. 僅在無 projects 目錄時才以 history.jsonl 作為備援回退
        history_file = claude_dir / "history.jsonl"
        if not has_scanned_projects and history_file.exists() and self._should_scan_file(history_file, full_history):
            try:
                with open(history_file, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            item = json.loads(line)
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
                                timestamp=event_time
                            )
                            self._processed_hashes.add(hash_key)
                        except Exception:
                            continue
            except Exception as e:
                logger.debug(f"Error reading Claude history.jsonl: {e}")

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
                with open(history_file, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            item = json.loads(line)
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
                                cwd=str(cwd) if cwd else None, timestamp=event_time
                            )
                        except Exception:
                            continue
            except Exception as e:
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
                except Exception as e:
                    logger.debug(f"Error parsing Codex session {s_file}: {e}")

    def _parse_codex_json_session(self, db, file_path: Path):
        """解析舊版格式 Codex .json 檔案"""
        with open(file_path, "r", encoding="utf-8", errors="ignore") as fp:
            data = json.load(fp)
            session_info = data.get("session", {})
            session_id = session_info.get("id")
            session_time = parse_timestamp_safe(session_info.get("timestamp"))
            items = data.get("items", [])

            current_prompt = ""
            current_time = session_time
            for it in items:
                role = it.get("role")
                content = extract_text_from_content(it.get("content"))
                if role == "user" and content:
                    if "<recommended_plugins>" in content or len(content) < 2:
                        continue
                    current_prompt = content
                elif role == "assistant" and current_prompt:
                    self._upsert_ai_event(
                        db, platform="codex", conv_id=session_id,
                        prompt=current_prompt, response=content if content else None,
                        cwd=None, timestamp=current_time or get_local_now()
                    )
                    current_prompt = ""

    def _parse_codex_jsonl_session(self, db, file_path: Path):
        """解析新版格式 Codex .jsonl 檔案 (2025/2026 rollout session)"""
        session_id = None
        session_cwd = None
        current_prompt = ""
        current_time = None

        with open(file_path, "r", encoding="utf-8", errors="ignore") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
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
                            current_prompt = content
                            current_time = ts or get_local_now()

                        elif role == "assistant" and current_prompt:
                            clean_cnt = (content or "").strip()
                            if not clean_cnt.startswith("[") and not clean_cnt.startswith("<") and len(clean_cnt) >= 3:
                                self._upsert_ai_event(
                                    db, platform="codex", conv_id=session_id,
                                    prompt=current_prompt, response=clean_cnt,
                                    cwd=session_cwd, timestamp=current_time or ts or get_local_now()
                                )
                                current_prompt = ""
                                current_time = None

                    elif t == "event_msg" and isinstance(payload, dict):
                        p_type = payload.get("type")
                        if p_type == "agent_message" and current_prompt:
                            msg_text = extract_text_from_content(payload.get("message") or payload.get("text"))
                            clean_msg = (msg_text or "").strip()
                            if clean_msg and not clean_msg.startswith("[") and not clean_msg.startswith("<") and len(clean_msg) >= 3:
                                self._upsert_ai_event(
                                    db, platform="codex", conv_id=session_id,
                                    prompt=current_prompt, response=clean_msg,
                                    cwd=session_cwd, timestamp=current_time or ts or get_local_now()
                                )
                                current_prompt = ""
                                current_time = None
                except Exception:
                    continue

        if current_prompt and current_time:
            self._upsert_ai_event(
                db, platform="codex", conv_id=session_id,
                prompt=current_prompt, response=None,
                cwd=session_cwd, timestamp=current_time
            )

    # =========================================================================
    # 3. Antigravity 日誌解析 (含 PLANNER_RESPONSE 真實助理回應提取)
    # =========================================================================
    def scan_antigravity_logs(self, full_history: bool = False):
        path_str = self.cfg.get("watchers.agent_log_watcher.antigravity_logs_path")
        if not path_str:
            return

        base_path = Path(path_str)
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
            latest_real_response = ""

            try:
                with open(transcript_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            item = json.loads(line)
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
                                            url=str_path, timestamp=current_time
                                        )
                                    current_prompt = clean_prompt
                                    current_time = ts or datetime.fromtimestamp(transcript_path.stat().st_mtime)
                                    latest_real_response = ""

                            elif step_type == "PLANNER_RESPONSE":
                                model_content = (item.get("content") or "").strip()
                                # 排除純空字串或工具調用字串，只保留實質結論
                                if model_content and len(model_content) >= 5 and not model_content.startswith("<") and not model_content.startswith("["):
                                    latest_real_response = model_content

                        except Exception:
                            continue

                    # 寫入最後一輪
                    if current_prompt and current_time:
                        self._upsert_ai_event(
                            db, platform="antigravity", conv_id=conv_id,
                            prompt=current_prompt, response=latest_real_response if latest_real_response else None,
                            url=str_path, timestamp=current_time
                        )
            except Exception as e:
                logger.debug(f"Could not read transcript {transcript_path}: {e}")

    # =========================================================================
    # 通用 Upsert 方法：建立或更新 AI 對話與助理回應
    # =========================================================================
    def _upsert_ai_event(
        self, db, platform: str, conv_id: Optional[str],
        prompt: str, response: Optional[str],
        cwd: Optional[str] = None, url: Optional[str] = None,
        timestamp: Optional[datetime] = None
    ):
        clean_prompt = prompt.strip()
        if len(clean_prompt) < 2:
            return

        # 清洗 response：嚴格過濾以 [ 開頭之工具調用字串與佔位符
        clean_resp = (response or "").strip()
        if clean_resp.startswith("[") or clean_resp.startswith("<") or clean_resp in ["[Executed in Antigravity Agent Session]", "[Codex CLI Session]"]:
            clean_resp = None
        if clean_resp and len(clean_resp) < 3:
            clean_resp = None

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
            # 依 platform + conversation_id (或 prompt) 查詢既有紀錄
            query = session.query(AIPromptEvent).filter(
                AIPromptEvent.platform == platform,
                AIPromptEvent.prompt_text == clean_prompt
            )
            if conv_id:
                query = query.filter(AIPromptEvent.conversation_id == conv_id)

            existing = query.first()
            if not existing:
                session.add(AIPromptEvent(
                    platform=platform,
                    url=url,
                    conversation_id=conv_id,
                    prompt_text=clean_prompt,
                    response_text=clean_resp,
                    project_tag=tag,
                    cwd=cwd,
                    timestamp=event_time
                ))
            else:
                # 若已有紀錄但先前沒有 response_text，或現在有了真實回應，進行補全
                if clean_resp and (not existing.response_text or existing.response_text.startswith("[")):
                    existing.response_text = clean_resp
                elif not clean_resp and existing.response_text and existing.response_text.startswith("["):
                    existing.response_text = None
                if cwd and not existing.cwd:
                    existing.cwd = cwd
                if tag and not existing.project_tag:
                    existing.project_tag = tag
                if url and not existing.url:
                    existing.url = url
