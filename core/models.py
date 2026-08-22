from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    Float,
    Index
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class AIPromptEvent(Base):
    """記錄所有 AI 相關的問答與指令互動 (Gemini, ChatGPT, Claude, Manus, CLI 等)"""
    __tablename__ = "ai_prompt_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    platform = Column(String(50), nullable=False, index=True)  # gemini, chatgpt, claude, manus, claude_code, etc.
    url = Column(String(500), nullable=True)
    conversation_id = Column(String(100), nullable=True, index=True)
    prompt_text = Column(Text, nullable=False)
    response_text = Column(Text, nullable=True)
    project_tag = Column(String(100), nullable=True, index=True)
    metadata_json = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_ai_platform_time", "platform", "timestamp"),
    )


class FileActivityEvent(Base):
    """記錄論文、文檔與檔案總管的異動活動"""
    __tablename__ = "file_activity_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    file_path = Column(String(1000), nullable=False)
    file_name = Column(String(255), nullable=False)
    file_type = Column(String(50), nullable=False, index=True)  # .tex, .docx, .md, .pdf, .py
    action = Column(String(50), nullable=False)  # created, modified, deleted, moved
    size_bytes = Column(Integer, default=0)
    diff_summary = Column(Text, nullable=True)  # 例如字數增減、新增行數
    project_name = Column(String(100), nullable=True, index=True)


class GitActivityEvent(Base):
    """記錄程式碼倉庫的 Commit 與變更紀錄"""
    __tablename__ = "git_activity_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    repo_name = Column(String(100), nullable=False, index=True)
    repo_path = Column(String(1000), nullable=False)
    commit_hash = Column(String(50), unique=True, nullable=False)
    branch = Column(String(100), default="main")
    author = Column(String(100), nullable=True)
    message = Column(Text, nullable=False)
    files_changed_count = Column(Integer, default=0)
    insertions = Column(Integer, default=0)
    deletions = Column(Integer, default=0)


class WindowEvent(Base):
    """記錄前景視窗切換與時間分配"""
    __tablename__ = "window_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    start_time = Column(DateTime, default=datetime.utcnow, index=True)
    end_time = Column(DateTime, default=datetime.utcnow)
    duration_seconds = Column(Float, default=0.0)
    app_name = Column(String(100), nullable=False, index=True)
    window_title = Column(String(500), nullable=False)
    category = Column(String(50), default="Uncategorized", index=True)  # Research, Coding, AI, Comm, etc.


class DailySummary(Base):
    """記錄每日 AI 彙整與分析報告"""
    __tablename__ = "daily_summaries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date_str = Column(String(10), unique=True, nullable=False, index=True)  # YYYY-MM-DD
    created_at = Column(DateTime, default=datetime.utcnow)
    llm_provider = Column(String(50), nullable=False)
    model_name = Column(String(100), nullable=False)
    raw_markdown = Column(Text, nullable=False)
    highlights_json = Column(Text, nullable=True)
    action_items_json = Column(Text, nullable=True)
