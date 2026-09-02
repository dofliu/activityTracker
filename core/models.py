from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    Float,
    Index,
    Boolean,
    LargeBinary,
)
from sqlalchemy.orm import declarative_base
from .time_utils import get_local_now

Base = declarative_base()


class AIPromptEvent(Base):
    """記錄所有 AI 相關的問答與指令互動 (Gemini, ChatGPT, Claude, CLI 等)"""
    __tablename__ = "ai_prompt_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=get_local_now, index=True)
    platform = Column(String(50), nullable=False, index=True)  # gemini, chatgpt, claude, claude_code, codex, antigravity
    url = Column(String(500), nullable=True)
    conversation_id = Column(String(100), nullable=True, index=True)
    prompt_text = Column(Text, nullable=False)
    response_text = Column(Text, nullable=True)
    project_tag = Column(String(255), nullable=True, index=True)
    cwd = Column(String(1000), nullable=True)
    metadata_json = Column(Text, nullable=True)
    turn_key = Column(String(128), unique=True, nullable=True, index=True)
    source_path = Column(String(1500), nullable=True)
    source_position = Column(Integer, nullable=True)
    response_status = Column(String(50), nullable=True, index=True)  # missing, partial, final_candidate

    __table_args__ = (
        Index("ix_ai_platform_time", "platform", "timestamp"),
        Index("ix_ai_project_time", "project_tag", "timestamp"),
    )


class FileActivityEvent(Base):
    """記錄論文、文檔與檔案總管的異動活動"""
    __tablename__ = "file_activity_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=get_local_now, index=True)
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
    timestamp = Column(DateTime, default=get_local_now, index=True)
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
    start_time = Column(DateTime, default=get_local_now, index=True)
    end_time = Column(DateTime, default=get_local_now)
    duration_seconds = Column(Float, default=0.0)
    app_name = Column(String(100), nullable=False, index=True)
    window_title = Column(String(500), nullable=False)
    category = Column(String(50), default="Uncategorized", index=True)  # Research, Coding, AI, Comm, etc.


class DailySummary(Base):
    """記錄每日 AI 彙整與分析報告"""
    __tablename__ = "daily_summaries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date_str = Column(String(50), unique=True, nullable=False, index=True)  # YYYY-MM-DD 或 YYYY-MM-DD ~ YYYY-MM-DD
    created_at = Column(DateTime, default=get_local_now)
    llm_provider = Column(String(50), nullable=False)
    model_name = Column(String(100), nullable=False)
    raw_markdown = Column(Text, nullable=False)
    highlights_json = Column(Text, nullable=True)
    action_items_json = Column(Text, nullable=True)


class ProjectState(Base):
    """記錄各專案/論文的進行中狀態與最後進展 (P1 專案歸戶層)"""
    __tablename__ = "project_states"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_key = Column(String(255), unique=True, nullable=False, index=True)  # 正規化路徑或標籤名稱
    display_name = Column(String(100), nullable=False, index=True)
    category = Column(String(50), default="Coding", index=True)  # Research, Coding, Paper, Personal
    last_activity_at = Column(DateTime, default=get_local_now, index=True)
    last_action_summary = Column(Text, nullable=True)
    status = Column(String(50), default="active", index=True)  # active, idle, stale, completed
    updated_at = Column(DateTime, default=get_local_now, onupdate=get_local_now)


class OpenLoop(Base):
    """記錄未結事項、卡點與待辦項目 (P1 Open Loops 層)"""
    __tablename__ = "open_loops"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_key = Column(String(255), nullable=False, index=True)
    title = Column(Text, nullable=False)
    source_type = Column(String(50), default="ai_dialogue")  # ai_dialogue, commit, file_edit, manual
    source_event_id = Column(Integer, nullable=True)
    confidence = Column(Float, default=1.0)
    created_at = Column(DateTime, default=get_local_now, index=True)
    resolved_at = Column(DateTime, nullable=True, index=True)
    status = Column(String(30), default="open", nullable=False, index=True)
    fingerprint = Column(String(64), nullable=True, index=True)
    last_seen_at = Column(DateTime, default=get_local_now, index=True)
    updated_at = Column(DateTime, default=get_local_now, onupdate=get_local_now)
    resolution_note = Column(Text, nullable=True)


class IngestionCheckpoint(Base):
    """只有成功解析後才前移的持久化 collector checkpoint。"""
    __tablename__ = "ingestion_checkpoints"

    id = Column(Integer, primary_key=True, autoincrement=True)
    collector = Column(String(80), nullable=False, index=True)
    source_path = Column(String(1500), unique=True, nullable=False)
    mtime_ns = Column(Integer, nullable=False, default=0)
    size_bytes = Column(Integer, nullable=False, default=0)
    source_position = Column(Integer, nullable=True)
    last_success_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=get_local_now, onupdate=get_local_now)


class BrowserExtensionHeartbeat(Base):
    """經 ingest token 驗證的 Extension 存活與非敏感診斷 receipt。"""

    __tablename__ = "browser_extension_heartbeats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    instance_id = Column(String(64), unique=True, nullable=False, index=True)
    extension_version = Column(String(32), nullable=False)
    ready_platforms_json = Column(Text, nullable=True)
    last_capture_status = Column(String(40), nullable=False, default="none")
    last_capture_at = Column(DateTime, nullable=True)
    last_error_code = Column(String(80), nullable=True)
    offline_queue_size = Column(Integer, nullable=False, default=0)
    first_seen_at = Column(DateTime, default=get_local_now, nullable=False)
    last_seen_at = Column(DateTime, default=get_local_now, nullable=False, index=True)


class MilestoneNotificationReceipt(Base):
    """每日里程碑通知 receipt；避免 scheduler 重啟或重送造成重複提醒。"""
    __tablename__ = "milestone_notification_receipts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    local_date = Column(String(10), nullable=False, index=True)
    milestone_minutes = Column(Integer, nullable=False)
    channel = Column(String(30), nullable=False, default="desktop")
    interface_group = Column(String(120), nullable=False, default="AI collaboration")
    observed_minutes = Column(Float, nullable=False, default=0.0)
    status = Column(String(30), nullable=False, default="sent")  # sent, coalesced
    message = Column(Text, nullable=True)
    notified_at = Column(DateTime, default=get_local_now, nullable=False)

    __table_args__ = (
        Index(
            "ux_milestone_receipt_date_threshold_channel",
            "local_date",
            "milestone_minutes",
            "channel",
            unique=True,
        ),
    )


class BackgroundTaskRun(Base):
    """具 start/end receipt 的本機 agent 背景任務。

    此表刻意不重複保存 prompt 或 response；內容仍只留在既有
    ``AIPromptEvent`` 的受控本機來源中。只有 start 與明確 completion
    timestamp 都存在的列，才可以做為可加總的背景執行時間。
    """
    __tablename__ = "background_task_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_key = Column(String(64), unique=True, nullable=False, index=True)
    platform = Column(String(50), nullable=False, index=True)
    session_id = Column(String(100), nullable=True, index=True)
    project_tag = Column(String(255), nullable=True, index=True)
    cwd = Column(String(1000), nullable=True)
    started_at = Column(DateTime, nullable=False, index=True)
    completed_at = Column(DateTime, nullable=True, index=True)
    duration_seconds = Column(Float, nullable=True)
    status = Column(String(40), nullable=False, default="awaiting_final", index=True)
    start_evidence_kind = Column(String(80), nullable=False, default="user_prompt")
    completion_evidence_kind = Column(String(80), nullable=True)
    source_path = Column(String(1500), nullable=False)
    start_position = Column(Integer, nullable=True)
    end_position = Column(Integer, nullable=True)
    observed_at = Column(DateTime, default=get_local_now, nullable=False, index=True)
    created_at = Column(DateTime, default=get_local_now, nullable=False)
    updated_at = Column(DateTime, default=get_local_now, onupdate=get_local_now)

    __table_args__ = (
        Index("ix_background_tasks_status_started", "status", "started_at"),
        Index("ix_background_tasks_platform_started", "platform", "started_at"),
    )


class AgentExecutionReceipt(Base):
    """ADR-008 gated executor 的 audit receipt。

    表名刻意避開 wip/p5-2 遺留的 ``agent_execution_jobs``（含自由字串
    command 欄位的被 revert 舊 schema），避免與使用者 DB 內的歷史遺留表
    衝突。此表不保存 command 字串、prompt/response 全文、token 或金鑰；
    ``action_call`` 是 server 端白名單 template 正規化後的呼叫描述。
    """
    __tablename__ = "agent_execution_receipts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    proposal_id = Column(String(64), nullable=False, index=True)
    template_id = Column(String(64), nullable=False, index=True)
    risk_level = Column(String(20), nullable=False)
    project_key = Column(String(255), nullable=True, index=True)
    action_call = Column(String(500), nullable=False)
    status = Column(String(20), nullable=False, default="queued", index=True)
    approved_via = Column(String(40), nullable=False, default="web_click")
    requested_at = Column(DateTime, nullable=False, index=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    output_digest = Column(String(64), nullable=True)
    output_summary = Column(String(500), nullable=True)
    error_code = Column(String(80), nullable=True)
    created_at = Column(DateTime, default=get_local_now, nullable=False)

    __table_args__ = (
        Index("ix_agent_exec_receipts_status_requested", "status", "requested_at"),
        Index("ix_agent_exec_receipts_proposal_requested", "proposal_id", "requested_at"),
    )


class ActivityMicroSummary(Base):
    """兩層增量摘要的 map 產物：單一 checkpoint 時段的本機微摘要。

    只保存壓縮後文字與非敏感統計，不重複保存 prompt/response 原文。
    日報 reduce 優先讀本表、缺漏時段回退原始節錄，因此本表缺損不影響
    日報可用性；同一時段重生成採 upsert。
    """
    __tablename__ = "activity_micro_summaries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    period_start = Column(DateTime, nullable=False, index=True)
    period_end = Column(DateTime, nullable=False)
    provider = Column(String(40), nullable=False)
    model = Column(String(120), nullable=True)
    summary_text = Column(String(800), nullable=False)
    input_chars = Column(Integer, nullable=False, default=0)
    event_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=get_local_now, nullable=False)
    updated_at = Column(DateTime, default=get_local_now, onupdate=get_local_now)

    __table_args__ = (
        Index("ux_micro_summary_period", "period_start", "period_end", unique=True),
    )


class CoverageLedgerInterval(Base):
    """P2.6 continuous coverage ledger：視窗採集器被觀測為運作中的時間段。

    interval 的結束時間永遠取最後一次 heartbeat；程序中斷、休眠或當機
    不會把中斷後的時間補進 coverage。此表刻意不儲存視窗標題、應用
    名稱或任何事件內容——它證明「採集器何時在觀測」，不證明使用者
    在場或生產力。
    """
    __tablename__ = "coverage_ledger_intervals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    collector = Column(String(64), nullable=False, index=True)
    started_at = Column(DateTime, nullable=False, index=True)
    last_heartbeat_at = Column(DateTime, nullable=False, index=True)
    heartbeat_count = Column(Integer, nullable=False, default=1)
    closed_at = Column(DateTime, nullable=True)
    close_reason = Column(String(40), nullable=True)
    created_at = Column(DateTime, default=get_local_now, nullable=False)
    updated_at = Column(DateTime, default=get_local_now, onupdate=get_local_now)

    __table_args__ = (
        Index("ix_coverage_ledger_collector_started", "collector", "started_at"),
        Index("ix_coverage_ledger_collector_open", "collector", "closed_at"),
    )


class SemanticDocument(Base):
    """P3-2 本機 semantic index；每筆向量可追溯到原始 SQLite row。"""
    __tablename__ = "semantic_documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_type = Column(String(40), nullable=False)
    source_id = Column(String(120), nullable=False)
    source_ref = Column(String(1500), nullable=False)
    source_updated_at = Column(DateTime, nullable=True, index=True)
    project_key = Column(String(255), nullable=True, index=True)
    title = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    trust_status = Column(String(50), nullable=False, default="observed")
    content_hash = Column(String(64), nullable=False)
    embedding_model = Column(String(120), nullable=False)
    embedding_input_mode = Column(String(50), nullable=False, default="normalized_full")
    embedding_dimensions = Column(Integer, nullable=False)
    embedding = Column(LargeBinary, nullable=False)
    indexed_at = Column(DateTime, default=get_local_now, nullable=False)

    __table_args__ = (
        Index(
            "ux_semantic_documents_source",
            "source_type",
            "source_id",
            unique=True,
        ),
        Index("ix_semantic_documents_model", "embedding_model"),
    )


class GitHubRepoState(Base):
    """記錄 GitHub 遠端專案 (Public / Private) 狀態與指標"""
    __tablename__ = "github_repo_states"

    id = Column(Integer, primary_key=True, autoincrement=True)
    repo_name = Column(String(100), unique=True, nullable=False, index=True)  # 如 activityTracker
    full_name = Column(String(200), nullable=False, index=True)  # 如 dofliu/activityTracker
    is_private = Column(Boolean, default=False)
    html_url = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    default_branch = Column(String(100), default="main")
    open_prs_count = Column(Integer, default=0)
    open_issues_count = Column(Integer, default=0)
    stars_count = Column(Integer, default=0)
    forks_count = Column(Integer, default=0)
    pushed_at = Column(DateTime, nullable=True, index=True)
    updated_at = Column(DateTime, default=get_local_now, onupdate=get_local_now)
    metadata_json = Column(Text, nullable=True)  # 儲存 CI 狀態、最新 PR 摘要等


class GitHubPREvent(Base):
    """記錄 GitHub Pull Request 狀態、審查意見與 CI 檢查結果"""
    __tablename__ = "github_pr_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    repo_name = Column(String(100), nullable=False, index=True)
    pr_number = Column(Integer, nullable=False, index=True)
    title = Column(Text, nullable=False)
    state = Column(String(50), nullable=False, index=True)  # open, closed, merged
    is_draft = Column(Boolean, default=False)
    author = Column(String(100), nullable=True)
    html_url = Column(String(500), nullable=False)
    branch_head = Column(String(100), nullable=True)
    branch_base = Column(String(100), nullable=True)
    additions = Column(Integer, default=0)
    deletions = Column(Integer, default=0)
    changed_files = Column(Integer, default=0)
    ci_status = Column(String(50), default="pending")  # success, failure, pending, neutral
    review_state = Column(String(50), default="PENDING")  # APPROVED, CHANGES_REQUESTED, COMMENTED, PENDING
    created_at = Column(DateTime, nullable=True, index=True)
    updated_at = Column(DateTime, nullable=True, index=True)
    merged_at = Column(DateTime, nullable=True, index=True)

    __table_args__ = (
        Index("ix_gh_repo_pr", "repo_name", "pr_number", unique=True),
    )


class RAGIndexedFolder(Base):
    """DeskRAG 已加入索引的本地目錄"""
    __tablename__ = "rag_indexed_folders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    path = Column(String(1000), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    is_active = Column(Integer, default=1)
    created_at = Column(DateTime, default=get_local_now)
    last_scanned_at = Column(DateTime, nullable=True)
    file_count = Column(Integer, default=0)
    total_size = Column(Integer, default=0)


class RAGIndexedFile(Base):
    """DeskRAG 已掃描與索引的檔案狀態"""
    __tablename__ = "rag_indexed_files"

    id = Column(Integer, primary_key=True, autoincrement=True)
    folder_id = Column(Integer, nullable=True, index=True)
    path = Column(String(1000), unique=True, nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    extension = Column(String(50), nullable=False, index=True)
    file_size = Column(Integer, default=0)
    last_modified = Column(Float, default=0.0)
    file_hash = Column(String(64), default="")
    chunk_count = Column(Integer, default=0)
    status = Column(String(50), default="pending", index=True)  # pending, indexed, error
    error_message = Column(Text, nullable=True)
    indexed_at = Column(DateTime, nullable=True)


class RAGIndexJob(Base):
    """DeskRAG 背景工作控制紀錄；僅保存工作狀態，不保存文件內容。"""
    __tablename__ = "rag_index_jobs"

    id = Column(String(36), primary_key=True)
    job_type = Column(String(32), nullable=False, index=True)  # index, remove_folder, clear_all
    folder_id = Column(Integer, nullable=True, index=True)
    status = Column(String(32), nullable=False, default="queued", index=True)
    requested_at = Column(DateTime, default=get_local_now)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=get_local_now, onupdate=get_local_now)
    worker_pid = Column(Integer, nullable=True)
    total_files = Column(Integer, default=0)
    processed_files = Column(Integer, default=0)
    indexed_chunks = Column(Integer, default=0)
    error_count = Column(Integer, default=0)
    max_files = Column(Integer, nullable=True)
    throttle_ms = Column(Integer, default=0)
    current_file = Column(String(1000), nullable=True)
    message = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    pause_requested = Column(Integer, default=0)
    cancel_requested = Column(Integer, default=0)
    result_json = Column(Text, nullable=True)


class RAGChatSession(Base):
    """DeskRAG 對話工作階段"""
    __tablename__ = "rag_chat_sessions"

    id = Column(String(100), primary_key=True)
    title = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=get_local_now)
    updated_at = Column(DateTime, default=get_local_now, onupdate=get_local_now)


class RAGChatMessage(Base):
    """DeskRAG 對話歷史與引文記錄"""
    __tablename__ = "rag_chat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(100), nullable=False, index=True)
    role = Column(String(50), nullable=False)
    content = Column(Text, nullable=False)
    citations = Column(Text, nullable=True)  # JSON 字串
    provider = Column(String(50), nullable=True)
    model = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=get_local_now)


class GitHubIssueEvent(Base):
    """記錄 GitHub Issue 明細

    repo 層的 open_issues_count 由 GitHub API 提供，但該數值把 PR 也計入，
    因此無法回答「有哪些 issue 待處理」。此表存 issue 本身。
    """
    __tablename__ = "github_issue_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    repo_name = Column(String(100), nullable=False, index=True)
    issue_number = Column(Integer, nullable=False, index=True)
    title = Column(Text, nullable=False)
    state = Column(String(20), nullable=False, index=True)  # open, closed
    author = Column(String(100), nullable=True)
    assignee = Column(String(100), nullable=True)
    html_url = Column(String(500), nullable=False)
    labels_json = Column(Text, nullable=True)
    comments_count = Column(Integer, default=0)
    created_at = Column(DateTime, nullable=True, index=True)
    updated_at = Column(DateTime, nullable=True, index=True)
    closed_at = Column(DateTime, nullable=True)


class SecretaryScheduledTask(Base):
    """P5-R5 使用者自訂排程任務（ADR-008 階段 5）。

    只允許排程 server 端已註冊的 L0_READ_ONLY schedulable template；
    L1/L2 永不可排程（自動執行僅限唯讀動作，見 ADR-008 D3）。此表只存
    排程設定與最近一次執行的非敏感狀態；每次執行仍寫
    ``agent_execution_receipts`` audit receipt（approved_via=schedule）。
    """
    __tablename__ = "secretary_scheduled_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    template_id = Column(String(64), nullable=False, index=True)
    params_json = Column(Text, nullable=True)  # 建立時已通過 template 白名單驗證
    schedule_kind = Column(String(16), nullable=False)  # daily / weekly / monthly
    run_time = Column(String(5), nullable=False, default="08:30")  # HH:MM 本機時間
    weekday = Column(Integer, nullable=True)  # weekly：0=週一 … 6=週日
    day_of_month = Column(Integer, nullable=True)  # monthly：1–28（避開月長歧義）
    enabled = Column(Boolean, nullable=False, default=True)
    last_run_at = Column(DateTime, nullable=True)
    last_status = Column(String(20), nullable=True)
    last_receipt_id = Column(Integer, nullable=True)
    last_error_code = Column(String(80), nullable=True)
    created_at = Column(DateTime, default=get_local_now, nullable=False)
    updated_at = Column(DateTime, default=get_local_now, onupdate=get_local_now)

    __table_args__ = (
        Index("ix_secretary_sched_tasks_enabled_template", "enabled", "template_id"),
    )


class ProposalSnooze(Base):
    """秘書提案的回饋：使用者說「這個先不要再提醒我」

    沒有這個回饋迴路，分流清單永遠不會變準——它會一直重推你已經判斷過不重要的事。
    """
    __tablename__ = "proposal_snoozes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    proposal_type = Column(String(64), nullable=False, index=True)
    project_key = Column(String(255), nullable=False, index=True)
    subject_ref = Column(String(255), nullable=False, default="")  # 例如 pr:repo#12
    snoozed_until = Column(DateTime, nullable=True)  # None 且 dismissed=True 代表永久忽略
    dismissed = Column(Boolean, default=False)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=get_local_now)


class SecretaryNote(Base):
    """小秘書記憶區（ADR-012）：使用者交代的筆記／偏好／決定，以及秘書自己的觀察。

    這是秘書回答與主動提案時的固定參考來源（「大腦」）。四種 kind 都是純文字：
    ``user_note``（記下來）、``preference``（偏好，例如「不要提醒 X」）、
    ``decision``（決定）、``observation``（秘書從 L0 收據推出的觀察，可一鍵刪除）。
    只存使用者自己輸入或由本機唯讀收據推出的短文字，不存 prompt／response 原文。
    """
    __tablename__ = "secretary_notes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String(24), nullable=False, index=True)
    project_key = Column(String(255), nullable=True, index=True)
    title = Column(String(200), nullable=True)
    body = Column(Text, nullable=False)
    source = Column(String(40), nullable=False, default="api")  # chat / web / api / morning_pack
    source_ref = Column(String(255), nullable=True, index=True)  # observation 去重用，例如 morning_pack:2026-09-02
    pinned = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=get_local_now, nullable=False, index=True)
    updated_at = Column(DateTime, default=get_local_now, onupdate=get_local_now)

    __table_args__ = (
        Index("ix_secretary_notes_kind_created", "kind", "created_at"),
    )
