import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.database import get_db
from core.models import Base
from core.agent_dispatcher import get_dispatcher

db = get_db()
Base.metadata.create_all(db._engine)

dispatcher = get_dispatcher()
dispatcher.start()

print("Submitting job...")
proposal = {
    "proposal_id": "test_123",
    "project_key": "test_project",
    "title": "Test Execution",
    "risk_level": "L0_READ_ONLY",
    "command": "python --version"
}
job_id = dispatcher.submit_job(proposal)
print(f"Job submitted with ID {job_id}")

time.sleep(3)
dispatcher.stop()

with db.session_scope() as session:
    from core.models import AgentExecutionJob
    job = session.query(AgentExecutionJob).get(job_id)
    print(f"Status: {job.status}")
    print(f"Output: {job.output_text}")
    print(f"Error: {job.error_message}")
