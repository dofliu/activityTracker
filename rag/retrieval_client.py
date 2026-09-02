"""Main-service side of the resident retrieval worker.

設計目標（呼應 ADR-009 精神：主服務不直接載入大型 Chroma／BM25）：

- **記憶體隔離**：索引、embedding 模型都活在子程序；主服務只持有一條 pipe。
- **卡住可救**：檢索逾時就 kill 子程序，下一次查詢自動重新啟動；主服務
  不會因為一次載入卡住而永久佔住 thread。
- **可預熱**：服務啟動後（或使用者按按鈕）在背景把索引載進 worker，第一次
  提問就不必等數十秒。預熱只在確實有索引時進行，避免空裝機觸發模型下載。
- **可觀測**：`status()` 回傳目前狀態、pid、預熱收據、重啟次數與最近錯誤，
  全部是程序內狀態，不宣稱檢索結果正確性。

所有狀態都在記憶體內；主服務重啟即歸零，worker 亦隨 stdin 關閉自行退出。
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Sequence

from core.config import get_config
from core.time_utils import get_local_now

logger = logging.getLogger("OmniContext.RAG.RetrievalClient")

VALID_MODES = ("worker", "in_process")
DEFAULT_TIMEOUT_SECONDS = 60


class RetrievalWorkerError(RuntimeError):
    """Worker 回傳錯誤、意外退出或無法啟動。"""


class RetrievalTimeoutError(TimeoutError):
    """檢索超過時限；worker 已被終止，下次查詢會重新啟動。"""


class RetrievalBusyError(RetrievalWorkerError):
    """另一個檢索或預熱仍占用 worker，等待逾時。"""


def retrieval_mode() -> str:
    mode = str(get_config().get("rag.retrieval.mode", "worker") or "worker").strip().lower()
    return mode if mode in VALID_MODES else "worker"


def warmup_on_start_enabled() -> bool:
    return bool(get_config().get("rag.retrieval.warmup_on_start", True))


def index_present() -> bool:
    """只在確實有東西可載入時才值得預熱；空索引預熱只會觸發模型下載。"""
    try:
        from rag.config import rag_settings

        if Path(rag_settings.BM25_PATH).exists():
            return True
        chroma_dir = Path(rag_settings.CHROMA_DIR)
        return chroma_dir.exists() and any(chroma_dir.iterdir())
    except Exception:  # noqa: BLE001 — 設定或磁碟問題不應讓啟動流程失敗
        return False


class RetrievalWorkerClient:
    def __init__(
        self,
        command: Optional[Sequence[str]] = None,
        cwd: Optional[Path] = None,
        stderr_tail: int = 20,
    ):
        project_root = Path(__file__).resolve().parent.parent
        self._command = list(command) if command else [sys.executable, "-m", "rag.retrieval_worker"]
        self._cwd = str(cwd or project_root)
        self._lock = threading.Lock()
        self._process: Optional[subprocess.Popen] = None
        self._lines: "queue.Queue[Optional[str]]" = queue.Queue()
        self._stderr_tail: Deque[str] = deque(maxlen=stderr_tail)
        self._state = "cold"
        self._pid: Optional[int] = None
        self._started_at: Optional[str] = None
        self._warmup: Optional[Dict[str, Any]] = None
        self._warmup_at: Optional[str] = None
        self._last_error: Optional[str] = None
        self._restarts = 0
        self._spawns = 0
        self._requests_served = 0
        self._last_retrieval_ms: Optional[int] = None
        self._last_retrieval_at: Optional[str] = None
        self._warmup_thread: Optional[threading.Thread] = None
        atexit.register(self.shutdown)

    # ---- process lifecycle -------------------------------------------------

    def _alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _spawn_locked(self) -> None:
        if self._alive():
            return
        if self._process is not None:
            self._restarts += 1
        self._lines = queue.Queue()
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._process = subprocess.Popen(
                self._command,
                cwd=self._cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=flags,
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except Exception as exc:  # noqa: BLE001 — 無法啟動要變成可讀狀態而不是 traceback
            self._state = "failed"
            self._last_error = f"spawn failed: {type(exc).__name__}: {exc}"
            self._process = None
            raise RetrievalWorkerError(self._last_error) from exc
        self._spawns += 1
        self._pid = self._process.pid
        self._started_at = get_local_now().isoformat(timespec="seconds")
        if self._state != "warming":
            self._state = "starting"
        self._warmup = None
        threading.Thread(target=self._pump_stdout, args=(self._process, self._lines), daemon=True).start()
        threading.Thread(target=self._pump_stderr, args=(self._process,), daemon=True).start()

    @staticmethod
    def _pump_stdout(process: subprocess.Popen, sink: "queue.Queue[Optional[str]]") -> None:
        try:
            for line in process.stdout:  # type: ignore[union-attr]
                sink.put(line)
        except Exception:  # noqa: BLE001
            pass
        finally:
            sink.put(None)

    def _pump_stderr(self, process: subprocess.Popen) -> None:
        try:
            for line in process.stderr:  # type: ignore[union-attr]
                text = line.rstrip()
                if text:
                    self._stderr_tail.append(text[:300])
        except Exception:  # noqa: BLE001
            pass

    def _kill_locked(self, reason: str) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            try:
                process.kill()
                process.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream:
                    stream.close()
            except Exception:  # noqa: BLE001
                pass
        self._state = "failed" if reason else "cold"
        self._last_error = reason or self._last_error
        self._pid = None

    def shutdown(self) -> Dict[str, Any]:
        """Stop the worker and release its memory; the next query starts it again."""
        with self._lock:
            process = self._process
            if process is not None and process.poll() is None:
                try:
                    self._write_locked({"id": "shutdown", "op": "shutdown"})
                    process.wait(timeout=3)
                except Exception:  # noqa: BLE001
                    pass
            self._kill_locked("")
            self._state = "cold"
            self._warmup = None
            self._process = None
            return self._status_locked()

    # ---- protocol ----------------------------------------------------------

    def _write_locked(self, payload: Dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise RetrievalWorkerError("retrieval worker is not running")
        try:
            process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise RetrievalWorkerError(f"retrieval worker pipe closed: {exc}") from exc

    def _request_locked(self, payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        self._spawn_locked()
        request_id = payload.setdefault("id", uuid.uuid4().hex)
        self._write_locked(payload)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._kill_locked(f"retrieval timed out after {int(timeout)}s; worker restarted")
                raise RetrievalTimeoutError(self._last_error or "retrieval timed out")
            try:
                line = self._lines.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                continue
            if line is None:
                code = self._process.poll() if self._process else None
                tail = self._stderr_tail[-1] if self._stderr_tail else ""
                self._kill_locked(f"retrieval worker exited (code {code}) {tail}".strip())
                raise RetrievalWorkerError(self._last_error or "retrieval worker exited")
            try:
                message = json.loads(line)
            except ValueError:
                continue  # 非協定輸出一律忽略
            if "event" in message:
                if message.get("event") == "hello" and self._state == "starting":
                    self._state = "loading"
                continue
            if message.get("id") != request_id:
                continue
            if not message.get("ok"):
                error = message.get("error") or {}
                detail = f"{error.get('type', 'Error')}: {error.get('message', '')}".strip()
                self._last_error = detail
                raise RetrievalWorkerError(detail)
            return message.get("result") or {}

    # ---- public API --------------------------------------------------------

    def retrieve(
        self,
        query: str,
        strategy: Optional[str] = None,
        top_k: Optional[int] = None,
        alpha: Optional[float] = None,
        score_threshold: float = 0.0,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> List[Dict[str, Any]]:
        started = time.monotonic()
        if not self._lock.acquire(timeout=timeout):
            raise RetrievalBusyError("retrieval worker is busy (warm-up or another query in progress)")
        try:
            remaining = max(1.0, timeout - (time.monotonic() - started))
            result = self._request_locked(
                {
                    "op": "retrieve",
                    "query": query,
                    "strategy": strategy,
                    "top_k": top_k,
                    "alpha": alpha,
                    "score_threshold": score_threshold,
                },
                timeout=remaining,
            )
            self._requests_served += 1
            self._last_retrieval_ms = int(result.get("elapsed_ms") or 0)
            self._last_retrieval_at = get_local_now().isoformat(timespec="seconds")
            self._state = "ready"
            if self._warmup is not None and result.get("worker_rss_mb") is not None:
                self._warmup["worker_rss_mb"] = result["worker_rss_mb"]
            return list(result.get("citations") or [])
        finally:
            self._lock.release()

    def warmup(self, timeout: float = 600) -> Dict[str, Any]:
        """Synchronously load the index inside the worker and keep its receipt."""
        if not self._lock.acquire(timeout=timeout):
            raise RetrievalBusyError("retrieval worker is busy")
        try:
            self._state = "warming"
            try:
                receipt = self._request_locked({"op": "warmup"}, timeout=timeout)
            except Exception as exc:
                self._state = "failed"
                self._last_error = str(exc)
                raise
            self._warmup = receipt
            self._warmup_at = get_local_now().isoformat(timespec="seconds")
            self._state = "ready"
            return receipt
        finally:
            self._lock.release()

    def warmup_in_background(self, reason: str = "manual") -> Dict[str, Any]:
        """Kick off warm-up on a daemon thread; idempotent while one is running."""
        with self._lock:
            if self._warmup_thread is not None and self._warmup_thread.is_alive():
                return self._status_locked()
            if self._state == "ready" and self._warmup is not None and self._alive():
                return self._status_locked()
            self._state = "warming"

        def _run() -> None:
            try:
                receipt = self.warmup()
                logger.info(
                    "RAG retrieval worker warmed up (%s): bm25=%s vectors=%s in %sms",
                    reason,
                    receipt.get("bm25_chunks"),
                    receipt.get("vector_chunks"),
                    (receipt.get("durations") or {}).get("total_ms"),
                )
            except Exception as exc:  # noqa: BLE001 — 背景預熱失敗只記錄，不影響主服務
                logger.warning("RAG retrieval worker warm-up failed (%s): %s", reason, exc)

        self._warmup_thread = threading.Thread(target=_run, name="rag-retrieval-warmup", daemon=True)
        self._warmup_thread.start()
        return self._status_locked()

    def _status_locked(self) -> Dict[str, Any]:
        alive = self._alive()
        state = self._state
        if not alive and state in ("ready", "loading", "starting"):
            state = "cold"
        return {
            "mode": retrieval_mode(),
            "state": state,
            "pid": self._pid if alive else None,
            "started_at": self._started_at if alive else None,
            "warmup": self._warmup if alive else None,
            "warmup_at": self._warmup_at if alive else None,
            "warmup_on_start": warmup_on_start_enabled(),
            "index_present": index_present(),
            "requests_served": self._requests_served,
            "last_retrieval_ms": self._last_retrieval_ms,
            "last_retrieval_at": self._last_retrieval_at,
            "restarts": self._restarts,
            "spawns": self._spawns,
            "last_error": self._last_error,
            "stderr_tail": list(self._stderr_tail)[-5:],
            "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
            "basis": "in_memory_process_state",
            "claim_boundary": "只描述檢索 worker 程序狀態與載入計數；不代表檢索結果正確或索引完整。",
        }

    def status(self) -> Dict[str, Any]:
        # 不搶 lock：狀態查詢不該被進行中的檢索或預熱擋住。
        return self._status_locked()


retrieval_client = RetrievalWorkerClient()


def maybe_warmup_on_start() -> Dict[str, Any]:
    """Called once by the server entry point after the web app is up.

    只有在 worker 模式、設定允許、RAG 啟用且索引存在時才啟動預熱；
    否則回傳說明為何略過（不啟動任何子程序）。
    """
    cfg = get_config()
    if not bool(cfg.get("rag.enabled", True)):
        return {"warmup": "skipped", "reason": "rag_disabled"}
    if retrieval_mode() != "worker":
        return {"warmup": "skipped", "reason": "in_process_mode"}
    if not warmup_on_start_enabled():
        return {"warmup": "skipped", "reason": "warmup_on_start_disabled"}
    if not index_present():
        return {"warmup": "skipped", "reason": "no_index_present"}
    retrieval_client.warmup_in_background(reason="startup")
    return {"warmup": "started", "reason": "startup"}
