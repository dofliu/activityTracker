"""API 請求／回應的 Pydantic 結構（TODO D4，ROADMAP §13 R1）。

以前這 33 個 model 散在 `core/server.py` 的 1,995 行裡（從第 230 行到第 1,824 行都有），
跟 route handler 交錯，要找一個欄位定義得先找到它宣告在哪一段。集中在這裡之後，
「對外 API 收什麼欄位、上下限是多少」就是一份可以從頭讀到尾的清單。

這裡只放結構與驗證規則，不放任何業務邏輯——欄位怎麼被使用看對應的 `core/api/*.py`。
"""

from datetime import datetime
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from typing import Any
from typing import Dict
from typing import List
from typing import Literal
from typing import Optional


class AIPromptCreate(BaseModel):
    platform: str = Field(..., description="gemini, chatgpt, claude, claude_code, codex, antigravity")
    url: Optional[str] = None
    conversation_id: Optional[str] = None
    prompt_text: str = Field(..., description="使用者輸入的 Prompt")
    response_text: Optional[str] = Field(None, description="AI 回應文本摘要")
    project_tag: Optional[str] = None
    cwd: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class ExtensionContentReadyReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: str = Field(..., min_length=1, max_length=20)
    seen_at: datetime


class ExtensionHeartbeatCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: str = Field(..., min_length=1, max_length=64)
    extension_version: str = Field(..., min_length=1, max_length=32)
    ready_platforms: list[str] = Field(default_factory=list, max_length=4)
    ready_platform_receipts: list[ExtensionContentReadyReceipt] = Field(
        default_factory=list,
        max_length=4,
    )
    last_capture_status: str = Field("none", max_length=40)
    last_capture_at: Optional[datetime] = None
    last_error_code: Optional[str] = Field(None, max_length=80)
    offline_queue_size: int = Field(0, ge=0, le=100)


class ExtensionVerificationStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platforms: list[str] = Field(..., min_length=1, max_length=4)
    timeout_seconds: int = Field(600, ge=60, le=1800)


class FileActivityCreate(BaseModel):
    file_path: str
    file_name: str
    file_type: str
    action: str
    size_bytes: int = 0
    diff_summary: Optional[str] = None
    project_name: Optional[str] = None


class GitActivityCreate(BaseModel):
    repo_name: str
    repo_path: str
    commit_hash: str
    branch: str = "main"
    author: Optional[str] = None
    message: str
    files_changed_count: int = 0
    insertions: int = 0
    deletions: int = 0


class WindowEventCreate(BaseModel):
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration_seconds: float
    app_name: str
    window_title: str
    category: str = "Uncategorized"


class GenerateSummaryRequest(BaseModel):
    target_date: Optional[str] = Field(None, description="格式 YYYY-MM-DD，若無則為今天")
    start_date: Optional[str] = Field(None, description="自訂區間起始日期 YYYY-MM-DD")
    end_date: Optional[str] = Field(None, description="自訂區間結束日期 YYYY-MM-DD")
    provider: Optional[str] = Field(None, description="指定 LLM 供應商 (gemini, anthropic, openai, ollama)")
    force_refresh: bool = Field(False, description="是否覆蓋已存在的摘要")


class BrowseFolderRequest(BaseModel):
    initial_dir: Optional[str] = None


class GenerateCheckpointRequest(BaseModel):
    hours: int = Field(2, ge=1, le=24, description="回溯時數")


class UsageMilestoneEvaluateRequest(BaseModel):
    date: Optional[str] = Field(None, description="本機日期 YYYY-MM-DD；僅允許當日通知")
    dry_run: bool = Field(False, description="只回傳預覽，不發送通知或寫入 receipt")


class RelatedMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., min_length=2, max_length=4000)
    project: Optional[str] = Field(None, max_length=255)
    threshold: Optional[float] = Field(None, ge=0.0, le=1.0)
    top_k: int = Field(8, ge=1, le=20)


class SnoozeProposalRequest(BaseModel):
    proposal_type: str
    project_key: str
    subject_ref: str = ""
    days: Optional[int] = 7
    dismissed: bool = False
    note: Optional[str] = None


class ExecuteProposalRequest(BaseModel):
    template_id: Optional[str] = None
    confirm_code: Optional[str] = None


class ScheduledTaskCreateRequest(BaseModel):
    template_id: str
    params: Dict[str, Any] = Field(default_factory=dict)
    schedule_kind: str
    run_time: str = "08:30"
    weekday: Optional[int] = None
    day_of_month: Optional[int] = None
    enabled: bool = True


class ScheduledTaskUpdateRequest(BaseModel):
    enabled: Optional[bool] = None
    schedule_kind: Optional[str] = None
    run_time: Optional[str] = None
    weekday: Optional[int] = None
    day_of_month: Optional[int] = None
    params: Optional[Dict[str, Any]] = None


class MemoryNoteRequest(BaseModel):
    kind: str = "user_note"
    body: str
    project_key: Optional[str] = None
    title: Optional[str] = None
    pinned: bool = False
    source: str = "web"


class MeetingFollowupRequest(BaseModel):
    note_id: int
    index: int
    action: str = "accept"
    project_key: Optional[str] = None


class SystemMaintenanceRequest(BaseModel):
    max_backups: Optional[int] = 7
    retention_days: Optional[int] = 90
    dry_run: Optional[bool] = False


class AcceptanceConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(max_length=8)
    confirmed: bool = True
    note: str = Field("", max_length=500)


class OpenPathRequest(BaseModel):
    path: Optional[str] = None
    action: Optional[str] = "explorer"  # "explorer" | "vscode" | "terminal" | "browser"
    url: Optional[str] = None


class OpenLoopTransitionRequest(BaseModel):
    status: str = Field(..., description="open, stale, resolved, superseded")
    note: Optional[str] = None


class TelegramTestRequest(BaseModel):
    bot_token: Optional[str] = None
    chat_id: Optional[str] = None
    send_test_message: bool = True


class TelegramConnectRequest(BaseModel):
    bot_token: Optional[str] = None
    chat_id: Optional[str] = None
    enabled: bool = True
    morning_briefing_time: Optional[str] = None
    evening_summary_time: Optional[str] = None


class TelegramDetectChatRequest(BaseModel):
    bot_token: Optional[str] = None


class LineConnectRequest(BaseModel):
    access_token: Optional[str] = None
    to: Optional[str] = None
    enabled: bool = True


class LineTestRequest(BaseModel):
    access_token: Optional[str] = None
    to: Optional[str] = None
    send_test_message: bool = True


class OpenLoopCreate(BaseModel):
    project_key: str
    title: str
    source_type: Optional[str] = "manual"


class GitHubConnectRequest(BaseModel):
    method: str = Field("gh_cli", description="gh_cli 或 token")
    token: Optional[str] = None


class RepositorySyncActionRequest(BaseModel):
    """本機 Git 寫入動作必須以已列出的 repo_id 與明確確認發出。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    repo_id: str = Field(..., pattern=r"^[a-f0-9]{16}$")
    action: Literal["fetch", "pull_ff_only", "push", "commit_staged"]
    confirmation: Literal["confirmed"]
    commit_message: Optional[str] = Field(default=None, max_length=300)


class RepositorySyncFetchAllRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation: Literal["confirmed"]


class RepositorySyncBatchRequest(BaseModel):
    """批次動作只接受清單內的 repo_id；沒有路徑、沒有 force、沒有 commit。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: Literal["pull_ff_only", "push"]
    repo_ids: List[str] = Field(..., min_length=1, max_length=200)
    confirmation: Literal["confirmed"]


class RepoOnboardingActionRequest(BaseModel):
    """P4.3：單一目標、id-based、明確確認；不接受任何本機路徑或 URL。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: Literal["init_folder", "attach_remote", "clone_repo", "create_remote"]
    confirmation: Literal["confirmed"]
    folder_id: Optional[str] = Field(default=None, pattern=r"^[a-f0-9]{16}$")
    repo_id: Optional[str] = Field(default=None, pattern=r"^[a-f0-9]{16}$")
    root_id: Optional[str] = Field(default=None, pattern=r"^[a-f0-9]{16}$")
    github_full_name: Optional[str] = Field(default=None, max_length=200)
    name: Optional[str] = Field(default=None, max_length=100)
    private: bool = True
