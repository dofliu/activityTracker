import re

with open('core/server.py', 'rb') as f:
    content = f.read()

execute_code = b'''
class ExecuteProposalRequest(BaseModel):
    proposal_id: str
    project_key: str = \"\"
    title: str = \"\"
    risk_level: str = \"L0_READ_ONLY\"
    command: str

@app.post(\"/api/v1/secretary/proposals/{proposal_id}/execute\")
def execute_secretary_proposal(proposal_id: str, payload: ExecuteProposalRequest):
    from core.agent_dispatcher import get_dispatcher
    dispatcher = get_dispatcher()
    
    proposal = payload.model_dump()
    if proposal[\"proposal_id\"] != proposal_id:
        raise HTTPException(status_code=400, detail=\"Proposal ID mismatch\")
        
    try:
        job_id = dispatcher.submit_job(proposal)
        return {\"status\": \"submitted\", \"job_id\": job_id, \"message\": \"Proposal execution started\"}
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get(\"/api/v1/secretary/jobs\")
def get_secretary_jobs(limit: int = Query(10, ge=1, le=50)):
    from core.models import AgentExecutionJob
    db = get_db()
    with db.session_scope() as session:
        jobs = session.query(AgentExecutionJob).order_by(AgentExecutionJob.created_at.desc()).limit(limit).all()
        return {
            \"status\": \"success\",
            \"jobs\": [
                {
                    \"id\": j.id,
                    \"proposal_id\": j.proposal_id,
                    \"title\": j.title,
                    \"status\": j.status,
                    \"created_at\": j.created_at.isoformat() if j.created_at else None,
                    \"started_at\": j.started_at.isoformat() if j.started_at else None,
                    \"completed_at\": j.completed_at.isoformat() if j.completed_at else None,
                    \"command\": j.command,
                    \"error_message\": j.error_message,
                    \"output_text\": j.output_text
                }
                for j in jobs
            ]
        }
'''

idx = content.find(b'@app.get(\"/api/v1/secretary/proposals\")')
if idx != -1:
    idx2 = content.find(b'def get_secretary_proposals(', idx)
    if idx2 != -1:
        idx3 = content.find(b'return build_action_proposals(limit=limit)', idx2)
        if idx3 != -1:
            idx4 = content.find(b'\n', idx3)
            new_content = content[:idx4+1] + execute_code + content[idx4+1:]
            with open('core/server.py', 'wb') as f:
                f.write(new_content)
            print('Successfully updated server.py')
