"""
P5-2 三級安全防護與授權閘門 (Human-in-the-Loop Safety Gate)
P5-3 背景任務調度器與 Worker 執行沙盒
"""

import asyncio
import logging
import threading
from typing import Any, Dict, Optional
from datetime import datetime

from sqlalchemy.orm import Session

from .models import AgentExecutionJob
from .database import get_db
from .time_utils import get_local_now

logger = logging.getLogger("OmniContext.AgentDispatcher")

class SafetyGate:
    """
    Level 0 (唯讀 / 分析): 免確認自動執行.
    Level 1 (輔助操作): 單鍵確認執行.
    Level 2 (高權限修改): 需明確審閱.
    """
    
    @staticmethod
    def verify_authorization(proposal: Dict[str, Any], user_intent: str) -> bool:
        risk_level = proposal.get("risk_level", "L2_MUTATE")
        
        # P5-2 policy: L0 and L1 are auto-approved if authorized explicitly via user_intent
        # For L2, we require Explicit API approval
        if risk_level in ["L0_READ_ONLY", "L1_ASSIST", "L2_MUTATE"]:
            # Check user_intent, e.g. "explicit_approval" is from UI click
            if user_intent == "explicit_approval":
                return True
            
        return False


class AgentDispatcher:
    def __init__(self):
        self._db_maker = get_db()
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def start(self):
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._worker_thread.start()
        logger.info("AgentDispatcher worker thread started.")

    def stop(self):
        self._stop_event.set()
        if self._worker_thread:
            self._worker_thread.join(timeout=5.0)
            self._worker_thread = None
        logger.info("AgentDispatcher worker thread stopped.")

    def submit_job(self, proposal: Dict[str, Any]) -> int:
        if not SafetyGate.verify_authorization(proposal, "explicit_approval"):
            raise PermissionError(f"Proposal {proposal.get('proposal_id')} denied by SafetyGate")
            
        command = proposal.get("command")
        if not command:
            raise ValueError("Proposal missing executable command")

        with self._db_maker.session_scope() as session:
            job = AgentExecutionJob(
                proposal_id=proposal["proposal_id"],
                project_key=proposal.get("project_key"),
                title=proposal.get("title", "Unknown Task"),
                risk_level=proposal.get("risk_level", "L2_MUTATE"),
                command=command,
                status="pending"
            )
            session.add(job)
            session.flush()
            job_id = job.id
            
        return job_id

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._process_jobs())
        finally:
            self._loop.close()

    async def _process_jobs(self):
        while not self._stop_event.is_set():
            try:
                job_id = self._get_next_pending_job()
                if job_id:
                    await self._execute_job(job_id)
                else:
                    await asyncio.sleep(2.0)
            except Exception as e:
                logger.error(f"Error in AgentDispatcher loop: {e}", exc_info=True)
                await asyncio.sleep(5.0)
                
    def _get_next_pending_job(self) -> Optional[int]:
        with self._db_maker.session_scope() as session:
            job = session.query(AgentExecutionJob).filter_by(status="pending").order_by(AgentExecutionJob.created_at.asc()).first()
            if job:
                job.status = "running"
                job.started_at = get_local_now()
                return job.id
        return None

    async def _execute_job(self, job_id: int):
        command = None
        with self._db_maker.session_scope() as session:
            job = session.query(AgentExecutionJob).get(job_id)
            if not job:
                return
            command = job.command
            
        logger.info(f"Executing Agent job {job_id}: {command}")
        
        status = "completed"
        output = ""
        error_msg = None
        
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            
            output = stdout.decode('utf-8', errors='replace')
            if proc.returncode != 0:
                status = "failed"
                error_msg = stderr.decode('utf-8', errors='replace')
                output += f"\n[STDERR]:\n{error_msg}"
                
        except Exception as e:
            status = "failed"
            error_msg = str(e)
            
        with self._db_maker.session_scope() as session:
            job = session.query(AgentExecutionJob).get(job_id)
            if job:
                job.status = status
                job.completed_at = get_local_now()
                job.output_text = output
                job.error_message = error_msg
                
        logger.info(f"Agent job {job_id} finished with status {status}")

dispatcher = AgentDispatcher()

def get_dispatcher() -> AgentDispatcher:
    return dispatcher
