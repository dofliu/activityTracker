import os
import json
import time
import threading
from pathlib import Path
from datetime import datetime
import logging
from typing import Set, Dict, Any, Optional

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
            # 若為毫秒 (大於 1e11)
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


class AgentLogWatcherService:
    def __init__(self):
        self.cfg = get_config()
        self._running = False
        self._thread: threading.Thread | None = None
        self._processed_hashes: Set[str] = set()

    def start(self):
        enabled = self.cfg.get("watchers.agent_log_watcher.enabled", True)
        if not enabled:
            logger.info("Agent log watcher is disabled in config.")
            return

        self._running = True
        self._thread = threading.Thread(target=self._scan_loop, daemon=True)
        self._thread.start()
        logger.info("AgentLogWatcher service started (Claude Code, Codex, Antigravity).")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            logger.info("AgentLogWatcher service stopped.")

    def _scan_loop(self):
        while self._running:
            try:
                self.scan_all_agents()
            except Exception as e:
                logger.error(f"Error in AgentLogWatcher scan: {e}", exc_info=True)

            for _ in range(60):
                if not self._running:
                    break
                time.sleep(1)

    def scan_all_agents(self, full_history: bool = False):
        self.scan_antigravity_logs(full_history=full_history)
        self.scan_claude_code_logs(full_history=full_history)
        self.scan_codex_logs(full_history=full_history)

    def scan_claude_code_logs(self, full_history: bool = False):
        """解析 Claude Code (~/.claude/history.jsonl 與 ~/.claude/projects/**/*.jsonl)"""
        user_home = Path.home()
        claude_dir = user_home / ".claude"
        if not claude_dir.exists():
            return

        db = get_db()

        # 1. 讀取 history.jsonl
        history_file = claude_dir / "history.jsonl"
        if history_file.exists():
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

                            hash_key = f"claude_code:{clean_prompt[:50]}:{event_time.strftime('%Y%m%d%H%M')}"
                            if hash_key in self._processed_hashes:
                                continue

                            with db.session_scope() as session:
                                existing = session.query(AIPromptEvent).filter_by(
                                    platform="claude_code",
                                    prompt_text=clean_prompt
                                ).first()
                                if not existing:
                                    tag = Path(project_path).name if project_path else "Claude Code"
                                    session.add(AIPromptEvent(
                                        platform="claude_code",
                                        prompt_text=clean_prompt,
                                        response_text="[Claude Code CLI Session]",
                                        project_tag=tag,
                                        cwd=str(project_path) if project_path else None,
                                        timestamp=event_time
                                    ))
                            self._processed_hashes.add(hash_key)
                        except Exception:
                            continue
            except Exception as e:
                logger.debug(f"Error reading Claude history.jsonl: {e}")

        # 2. 讀取 projects/**/*.jsonl
        projects_dir = claude_dir / "projects"
        if projects_dir.exists():
            for proj_jsonl in projects_dir.glob("**/*.jsonl"):
                try:
                    with open(proj_jsonl, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                item = json.loads(line)
                                msg_type = item.get("type")
                                if msg_type == "user":
                                    msg = item.get("message", {})
                                    content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
                                    # 若是 tool_result 或 list，跳過或取純字串
                                    if isinstance(content, list):
                                        text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                                        content = " ".join(text_parts).strip()

                                    if not content or len(content.strip()) < 2:
                                        continue

                                    event_time = parse_timestamp_safe(item.get("timestamp") or item.get("createdAt"))
                                    if not event_time:
                                        continue

                                    cwd = item.get("cwd")
                                    session_id = item.get("sessionId")
                                    tag = Path(cwd).name if cwd else "Claude Project"
                                    clean_content = content.strip()

                                    with db.session_scope() as session:
                                        existing = session.query(AIPromptEvent).filter_by(
                                            platform="claude_code",
                                            conversation_id=session_id,
                                            prompt_text=clean_content
                                        ).first()
                                        if not existing:
                                            session.add(AIPromptEvent(
                                                platform="claude_code",
                                                conversation_id=session_id,
                                                prompt_text=clean_content,
                                                response_text=None,
                                                project_tag=tag,
                                                cwd=cwd,
                                                timestamp=event_time
                                            ))
                            except Exception:
                                continue
                except Exception as e:
                    logger.debug(f"Error reading Claude project log {proj_jsonl}: {e}")

    def scan_codex_logs(self, full_history: bool = False):
        """解析 Codex CLI (~/.codex/history.jsonl 與 ~/.codex/sessions/**)"""
        user_home = Path.home()
        codex_dir = user_home / ".codex"
        if not codex_dir.exists():
            return

        db = get_db()
        history_file = codex_dir / "history.jsonl"
        if history_file.exists():
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

                            with db.session_scope() as session:
                                existing = session.query(AIPromptEvent).filter_by(
                                    platform="codex",
                                    prompt_text=clean_prompt
                                ).first()
                                if not existing:
                                    tag = Path(cwd).name if cwd else "Codex Project"
                                    session.add(AIPromptEvent(
                                        platform="codex",
                                        prompt_text=clean_prompt,
                                        response_text="[Codex CLI Session]",
                                        project_tag=tag,
                                        cwd=str(cwd) if cwd else None,
                                        timestamp=event_time
                                    ))
                        except Exception:
                            continue
            except Exception as e:
                logger.debug(f"Error reading Codex history.jsonl: {e}")

    def scan_antigravity_logs(self, full_history: bool = False):
        """掃描 Antigravity 對話 transcript.jsonl (精準解析每一步驟的 created_at)"""
        path_str = self.cfg.get("watchers.agent_log_watcher.antigravity_logs_path")
        if not path_str:
            return

        base_path = Path(path_str)
        if not base_path.exists():
            return

        db = get_db()
        for transcript_path in base_path.glob("**/transcript.jsonl"):
            str_path = str(transcript_path)
            conv_id = transcript_path.parent.parent.name

            try:
                with open(transcript_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            item = json.loads(line)
                            step_type = item.get("type")
                            if step_type == "USER_INPUT":
                                prompt_text = item.get("content", "")
                                if not prompt_text or len(prompt_text.strip()) < 3:
                                    continue

                                event_time = parse_timestamp_safe(item.get("created_at") or item.get("timestamp"))
                                if not event_time:
                                    # Fallback 到檔案 mtime
                                    event_time = datetime.fromtimestamp(transcript_path.stat().st_mtime)

                                clean_prompt = prompt_text.strip()
                                if clean_prompt.startswith("<USER_REQUEST>"):
                                    clean_prompt = clean_prompt.replace("<USER_REQUEST>", "").replace("</USER_REQUEST>", "").strip()

                                if len(clean_prompt) < 2:
                                    continue

                                with db.session_scope() as session:
                                    existing = (
                                        session.query(AIPromptEvent)
                                        .filter_by(
                                            platform="antigravity",
                                            conversation_id=conv_id,
                                            prompt_text=clean_prompt
                                        )
                                        .first()
                                    )
                                    if not existing:
                                        session.add(AIPromptEvent(
                                            platform="antigravity",
                                            url=str_path,
                                            conversation_id=conv_id,
                                            prompt_text=clean_prompt,
                                            response_text="[Executed in Antigravity Agent Session]",
                                            project_tag="Agent Development",
                                            timestamp=event_time
                                        ))
                        except Exception:
                            continue
            except Exception as e:
                logger.debug(f"Could not read transcript {transcript_path}: {e}")
