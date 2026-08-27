import re

with open('core/models.py', 'rb') as f:
    content = f.read()

idx = content.find(b'class AgentExecutionJob')
if idx != -1:
    content = content[:idx]
else:
    idx = content.find(b'class RAGChatMessage')
    if idx != -1:
        idx2 = content.find(b'get_local_now)', idx)
        if idx2 != -1:
            content = content[:idx2 + len(b'get_local_now)')]

with open('core/models.py', 'wb') as f:
    f.write(content)

with open('core/models.py', 'a', encoding='utf-8') as f:
    f.write('''

class AgentExecutionJob(Base):
    """P5-3 背景任務調度器與 Worker 執行沙盒任務紀錄"""
    __tablename__ = "agent_execution_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    proposal_id = Column(String(64), nullable=False, index=True)
    project_key = Column(String(255), nullable=True, index=True)
    title = Column(String(255), nullable=False)
    risk_level = Column(String(50), nullable=False)
    command = Column(Text, nullable=False)
    status = Column(String(50), default="pending", index=True)  # pending, running, completed, failed, rejected
    output_text = Column(Text, nullable=True)
    created_at = Column(DateTime, default=get_local_now)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
''')
