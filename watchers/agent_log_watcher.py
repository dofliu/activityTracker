import os
import json
import time
import threading
from pathlib import Path
from datetime import datetime, date
import logging
from typing import Set

from core.config import get_config
from core.database import get_db
from core.models import AIPromptEvent

logger = logging.getLogger("OmniContext.AgentLogWatcher")


class AgentLogWatcherService:
    def __init__(self):
        self.cfg = get_config()
        self._running = False
        self._thread: threading.Thread | None = None
        self._processed_files: Set[str] = set()

    def start(self):
        enabled = self.cfg.get("watchers.agent_log_watcher.enabled", True)
        if not enabled:
            logger.info("Agent log watcher is disabled in config.")
            return

        self._running = True
        self._thread = threading.Thread(target=self._scan_loop, daemon=True)
        self._thread.start()
        logger.info("AgentLogWatcher service started.")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            logger.info("AgentLogWatcher service stopped.")

    def _scan_loop(self):
        while self._running:
            try:
                self.scan_antigravity_logs()
                self.scan_claude_logs()
            except Exception as e:
                logger.error(f"Error in AgentLogWatcher scan: {e}")

            # Sleep 60 seconds between scans
            for _ in range(60):
                if not self._running:
                    break
                time.sleep(1)

    def scan_antigravity_logs(self):
        """掃描 Antigravity 的對話 transcript.jsonl"""
        path_str = self.cfg.get("watchers.agent_log_watcher.antigravity_logs_path")
        if not path_str:
            return

        base_path = Path(path_str)
        if not base_path.exists():
            return

        db = get_db()
        # 尋找所有 transcript.jsonl
        for transcript_path in base_path.glob("**/transcript.jsonl"):
            str_path = str(transcript_path)
            try:
                # 取得檔案修改時間
                mtime = datetime.fromtimestamp(transcript_path.stat().st_mtime)
                if mtime.date() < date.today():
                    continue

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
                                if not prompt_text or len(prompt_text) < 3:
                                    continue

                                # 寫入資料庫（防重覆）
                                with db.session_scope() as session:
                                    existing = (
                                        session.query(AIPromptEvent)
                                        .filter_by(
                                            platform="antigravity",
                                            prompt_text=prompt_text
                                        )
                                        .first()
                                    )
                                    if not existing:
                                        event = AIPromptEvent(
                                            platform="antigravity",
                                            url=str_path,
                                            conversation_id=transcript_path.parent.parent.name,
                                            prompt_text=prompt_text,
                                            response_text="[Executed in Antigravity Agent Session]",
                                            project_tag="Agent Development",
                                            timestamp=mtime
                                        )
                                        session.add(event)
                                        logger.info(f"Logged Antigravity prompt: {prompt_text[:50]}...")
                        except Exception:
                            continue
            except Exception as e:
                logger.debug(f"Could not read transcript {transcript_path}: {e}")

    def scan_claude_logs(self):
        """掃描 Claude Code 或桌面端日誌"""
        path_str = self.cfg.get("watchers.agent_log_watcher.claude_code_logs_path")
        if not path_str:
            return

        base_path = Path(path_str)
        if not base_path.exists():
            return
        # 預留 Claude Code 日誌擴充介面
