"""AI agent transcript 的採集服務（ADR-025，TODO D9）。

這個模組**不認識任何一種 transcript 格式**。格式住在 `watchers/transcripts/` 底下，一個平台一個
模組，各自實作 `discover`／`parse` 兩個函式。這裡只負責四件採集服務的事：

1. 執行緒與自我修復；
2. `IngestionCheckpoint`——成功才前移簽章，失敗只寫 error 並保留可重試狀態；
3. 把 parser 產出的 :class:`TranscriptTurn` 寫成 `AIPromptEvent`（含 CLI 雜訊過濾與背景工作證據）；
4. 來源層級的故障隔離、診斷，以及「檔案在動、事件是零」的漂移警示。
"""

import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Set, Tuple

from sqlalchemy import func

from core.background_tasks import BackgroundTaskEvidence, record_background_task_evidence
from core.config import get_config
from core.database import get_db
from core.models import AIPromptEvent, IngestionCheckpoint
from core.time_utils import get_local_now
from watchers.transcripts import SOURCES, TranscriptTurn, empty_drift, evaluate_drift
from watchers.transcripts.base import clean_prompt_text, is_cli_artifact, normalize_assistant_candidate

logger = logging.getLogger("OmniContext.AgentLogWatcher")

# 設定鍵 → 對外的掃描方法名。掃描一定要繞過這張表呼叫具名方法，
# 這樣「單一來源失敗不拖垮其他來源」才測得到（測試會替換其中一個方法）。
_SCAN_METHODS = {
    "claude_code": "scan_claude_code_logs",
    "claude_desktop": "scan_claude_desktop_logs",
    "codex": "scan_codex_logs",
    "antigravity": "scan_antigravity_logs",
}
_SOURCE_BY_KEY = {source.key: source for source in SOURCES}


class AgentLogWatcherService:
    def __init__(self):
        self.cfg = get_config()
        self._running = False
        self._thread: threading.Thread | None = None
        self._processed_hashes: Set[str] = set()
        # Process cache 只是加速；SQLite checkpoint 才是跨重啟的可信狀態。
        self._file_states: Dict[str, Tuple[int, int]] = {}
        self._diagnostics_lock = threading.Lock()
        self._source_diagnostics: Dict[str, Dict[str, Any]] = {
            source.key: {
                "state": "not_started",
                "last_attempt_at": None,
                "last_success_at": None,
                "consecutive_errors": 0,
                "last_error_code": None,
            }
            for source in SOURCES
        }
        # 每個來源這次探索到的最新檔案 mtime——漂移判定的「檔案在動」那一半。
        self._newest_file_at: Dict[str, Optional[datetime]] = {}
        self._drift: Dict[str, Any] = empty_drift()

    # ------------------------------------------------------------------
    # 診斷
    # ------------------------------------------------------------------

    @staticmethod
    def _diagnostic_error_code(exc: Exception) -> str:
        """只輸出非敏感錯誤類型，不回傳 path 或 exception message。"""
        if isinstance(exc, PermissionError):
            return "permission_denied"
        if isinstance(exc, OSError):
            return "os_error"
        name = exc.__class__.__name__
        return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower() or "unknown_error"

    def _set_source_disabled(self, source: str) -> None:
        with self._diagnostics_lock:
            item = self._source_diagnostics[source]
            item["state"] = "disabled"
            item["consecutive_errors"] = 0
            item["last_error_code"] = None
            # 關掉的來源不留下舊的檔案觀察，否則它會繼續參與漂移判定。
            self._newest_file_at.pop(source, None)

    def _mark_source_attempt(self, source: str) -> None:
        with self._diagnostics_lock:
            item = self._source_diagnostics[source]
            item["state"] = "scanning"
            item["last_attempt_at"] = get_local_now()

    def _mark_source_result(self, source: str, error: Exception | None = None) -> None:
        with self._diagnostics_lock:
            item = self._source_diagnostics[source]
            if error is None:
                item["state"] = "healthy"
                item["last_success_at"] = get_local_now()
                item["consecutive_errors"] = 0
                item["last_error_code"] = None
                return
            item["state"] = "error"
            item["consecutive_errors"] = int(item["consecutive_errors"]) + 1
            item["last_error_code"] = self._diagnostic_error_code(error)

    def get_diagnostics(self) -> Dict[str, Any]:
        """供 localhost status API 使用的非敏感 source probe snapshot。"""
        with self._diagnostics_lock:
            sources = {
                source: {
                    **item,
                    "last_attempt_at": (
                        item["last_attempt_at"].isoformat(timespec="seconds")
                        if item["last_attempt_at"]
                        else None
                    ),
                    "last_success_at": (
                        item["last_success_at"].isoformat(timespec="seconds")
                        if item["last_success_at"]
                        else None
                    ),
                }
                for source, item in self._source_diagnostics.items()
            }
            drift = dict(self._drift)
        states = [item["state"] for item in sources.values()]
        if "error" in states:
            state = "degraded"
        elif "scanning" in states:
            state = "scanning"
        elif "healthy" in states:
            state = "healthy"
        elif states and all(value == "disabled" for value in states):
            state = "disabled"
        else:
            state = "not_started"
        return {"state": state, "sources": sources, "drift": drift}

    # ------------------------------------------------------------------
    # 執行緒
    # ------------------------------------------------------------------

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

    def check_health_and_heal(self) -> Dict[str, Any]:
        """自我修復：若 Agent 日誌監控線程異常終止，自動重啟"""
        enabled = self.cfg.get("watchers.agent_log_watcher.enabled", True)
        if not enabled:
            return {"status": "disabled", "healed": False}

        if self._thread and self._thread.is_alive():
            return {"status": "healthy", "healed": False}

        logger.warning("AgentLogWatcher thread dead. Initiating self-healing restart...")
        try:
            self._running = True
            self._thread = threading.Thread(target=self._scan_loop, daemon=True)
            self._thread.start()
            receipt = {
                "timestamp": get_local_now().isoformat(),
                "action": "restart_agent_log_thread",
                "status": "success"
            }
            logger.info("AgentLogWatcher self-healing restart succeeded.")
            return {"status": "healed", "healed": True, "receipt": receipt}
        except Exception as e:
            logger.error(f"AgentLogWatcher self-healing failed: {e}", exc_info=True)
            return {"status": "error", "error": str(e), "healed": False}

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

    # ------------------------------------------------------------------
    # Checkpoint
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # 掃描
    # ------------------------------------------------------------------

    def scan_all_agents(self, full_history: bool = False):
        cfg = get_config()

        # 每個來源都是獨立的故障邊界；單一目錄權限或壞檔不可中止其他來源的採集。
        for source in SOURCES:
            if not cfg.get(f"watchers.agent_log_watcher.{source.key}", True):
                self._set_source_disabled(source.key)
                logger.debug("%s watcher is disabled in config.", source.label)
                continue
            self._mark_source_attempt(source.key)
            try:
                getattr(self, _SCAN_METHODS[source.key])(full_history=full_history)
            except Exception as exc:
                self._mark_source_result(source.key, exc)
                logger.error(
                    "%s source scan skipped; remaining agent sources will continue: %s",
                    source.label,
                    exc,
                )
            else:
                self._mark_source_result(source.key)

        self._refresh_drift()

    def scan_claude_code_logs(self, full_history: bool = False):
        self._scan_source(_SOURCE_BY_KEY["claude_code"], full_history=full_history)

    def scan_claude_desktop_logs(self, full_history: bool = False):
        self._scan_source(_SOURCE_BY_KEY["claude_desktop"], full_history=full_history)

    def scan_codex_logs(self, full_history: bool = False):
        self._scan_source(_SOURCE_BY_KEY["codex"], full_history=full_history)

    def scan_antigravity_logs(self, full_history: bool = False):
        self._scan_source(_SOURCE_BY_KEY["antigravity"], full_history=full_history)

    def _scan_source(self, source, *, full_history: bool) -> None:
        """一個平台的完整掃描：探索 → checkpoint 過濾 → 解析 → 寫入。

        檔案 mtime 在 **checkpoint 過濾之前**就記下來：被跳過的檔案一樣算「檔案在動」，
        漂移判定要的是「使用者有在用這個平台」，不是「這次掃了幾個檔」。
        """
        db = get_db()
        newest: Optional[datetime] = None
        for path in source.discover(self.cfg, full_history=full_history, now=get_local_now):
            mtime = self._file_mtime(path)
            if mtime and (newest is None or mtime > newest):
                newest = mtime
            if not self._should_scan_file(path, full_history):
                continue
            try:
                self.ingest_turns(db, source.parse(path, cfg=self.cfg, now=get_local_now))
                self._mark_file_scanned(path)
            except Exception as exc:
                self._mark_file_scanned(path, str(exc))
                logger.debug("Error reading %s transcript %s: %s", source.label, path, exc)
        with self._diagnostics_lock:
            self._newest_file_at[source.key] = newest

    @staticmethod
    def _file_mtime(path: Path) -> Optional[datetime]:
        try:
            return datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            return None

    def ingest_turns(self, db, turns: Iterable[TranscriptTurn]) -> int:
        """把 parser 產出的輪次寫進資料庫；回傳處理過的輪次數。

        解析途中拋例外時，**已經產出的輪次留在資料庫**（與拆分前相同）：checkpoint 會記下
        error，下次重掃時 upsert 會把同一批 turn_key 覆蓋回去。
        """
        processed = 0
        for turn in turns:
            if turn.dedupe_key and turn.dedupe_key in self._processed_hashes:
                continue
            self._upsert_ai_event(
                db,
                platform=turn.platform,
                conv_id=turn.conv_id,
                prompt=turn.prompt,
                response=turn.response,
                cwd=turn.cwd,
                url=turn.url,
                timestamp=turn.timestamp,
                turn_key=turn.turn_key,
                source_path=turn.source_path,
                source_position=turn.source_position,
                response_status=turn.response_status,
            )
            if turn.dedupe_key:
                self._processed_hashes.add(turn.dedupe_key)
            if turn.evidence:
                record_background_task_evidence(
                    BackgroundTaskEvidence(
                        platform=turn.platform,
                        source_path=turn.source_path,
                        started_at=turn.evidence.started_at,
                        start_position=turn.evidence.start_position,
                        session_id=turn.evidence.session_id,
                        cwd=turn.evidence.cwd,
                        completed_at=turn.evidence.completed_at,
                        end_position=turn.evidence.end_position,
                        completion_evidence_kind=turn.evidence.completion_evidence_kind,
                    ),
                    database=db,
                    cfg=self.cfg,
                )
            processed += 1
        return processed

    # ------------------------------------------------------------------
    # 漂移警示
    # ------------------------------------------------------------------

    def _refresh_drift(self) -> None:
        """比對「檔案在動」與「事件是零」；沒掃過任何來源就不查資料庫。"""
        with self._diagnostics_lock:
            newest_file_at = dict(self._newest_file_at)
        if not newest_file_at:
            return
        try:
            last_event_at = self._load_last_event_times(get_db())
        except Exception as exc:
            logger.debug("Drift check skipped; last-event lookup failed: %s", exc)
            return
        drift = evaluate_drift(
            newest_file_at=newest_file_at,
            last_event_at=last_event_at,
            now=get_local_now(),
        )
        with self._diagnostics_lock:
            self._drift = drift
        if drift["platforms"]:
            logger.warning(
                "Transcript drift suspected (files updated, zero events in %d days): %s",
                drift["window_days"],
                ", ".join(item["platform"] for item in drift["platforms"]),
            )

    @staticmethod
    def _load_last_event_times(db) -> Dict[str, Optional[datetime]]:
        with db.session_scope() as session:
            rows = (
                session.query(AIPromptEvent.platform, func.max(AIPromptEvent.timestamp))
                .group_by(AIPromptEvent.platform)
                .all()
            )
        return {platform: latest for platform, latest in rows if platform}

    # ------------------------------------------------------------------
    # 通用 Upsert 方法：建立或更新 AI 對話與助理回應
    # ------------------------------------------------------------------

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
