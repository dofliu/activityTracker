"""小秘書：提案、執行、記憶區、會議、排程（TODO D4，ROADMAP §13 R1）。

ADR-007／008／012～022 的對外端點。危險能力（執行器、排程）仍走 `_require_execution_token`，
閘門不因為換了模組而改變。

路徑與 handler 名稱與切分前完全相同，由 `tests/test_api_route_snapshot.py` 鎖住。
"""

from core.agent_executor import ExecutionRejected
from core.agent_executor import attach_execution_actions
from core.agent_executor import cancel_execution
from core.agent_executor import execute_proposal
from core.agent_executor import list_execution_receipts
from core.api.deps import _require_execution_token
from core.config import get_config
from core.proactive_secretary import build_action_proposals
from core.proactive_secretary import snooze_proposal
from core.scheduled_tasks import create_scheduled_task
from core.scheduled_tasks import delete_scheduled_task
from core.scheduled_tasks import list_scheduled_tasks
from core.scheduled_tasks import run_scheduled_task_now
from core.scheduled_tasks import update_scheduled_task
from core.schemas import ExecuteProposalRequest, MeetingFollowupRequest, MemoryNoteRequest, ScheduledTaskCreateRequest, ScheduledTaskUpdateRequest, SnoozeProposalRequest
from core.secretary_advisor import annotate_action_proposals
from datetime import datetime
from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi.responses import JSONResponse
from typing import Optional


router = APIRouter()


@router.get("/api/v1/secretary/proposals")
def get_secretary_proposals(
    limit: int = Query(6, ge=1, le=12),
):
    """P5-1 proposal-only derived view；不保存任何建議。

    P5-R1：可選的 LLM advisory 層只能對既有 proposal 附加唯讀註解
    （預設關閉；本機 Ollama 優先；失敗自動回退 deterministic）。
    P5-R2：executor 啟用時（預設關閉）標記白名單動作；執行仍需
    execution token 與使用者逐項批准（ADR-008）。
    """
    return attach_execution_actions(
        annotate_action_proposals(build_action_proposals(limit=limit))
    )


@router.post("/api/v1/secretary/proposals/snooze")
def snooze_secretary_proposal(payload: SnoozeProposalRequest):
    """記錄「先不要再提醒我」；只寫 proposal_snoozes，不觸碰事件資料。

    這是分流清單能變準的唯一途徑：沒有回饋，系統會一直重推已被判斷為不重要的事。
    """
    return snooze_proposal(
        proposal_type=payload.proposal_type,
        project_key=payload.project_key,
        subject_ref=payload.subject_ref,
        days=payload.days,
        dismissed=payload.dismissed,
        note=payload.note,
    )


@router.post("/api/v1/secretary/proposals/{proposal_id}/execute")
def execute_secretary_proposal(
    proposal_id: str,
    request: Request,
    payload: Optional[ExecuteProposalRequest] = None,
):
    """ADR-008 D1：只接受 proposal_id，動作由 server 端白名單 template 決定。

    body 只認兩個欄位：``template_id`` 在 server 已註冊的動作中選擇
    （預設 primary）、``confirm_code`` 供 L2 二次確認（P5-R3）；其餘
    欄位一律忽略——任何呼叫端提供的 command / path / argv 都沒有效果。
    L2 第一次呼叫（未附 confirm code）回 428 與一次性確認碼。
    """
    _require_execution_token(request)
    try:
        result = execute_proposal(
            proposal_id,
            approved_via="web_click",
            template_id=payload.template_id if payload else None,
            confirm_code=payload.confirm_code if payload else None,
        )
    except ExecutionRejected as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.error_code) from exc
    if result.get("status") == "confirmation_required":
        return JSONResponse(status_code=428, content=result)
    return result


@router.get("/api/v1/secretary/executions")
def get_secretary_executions(limit: int = Query(20, ge=1, le=100)):
    """Audit receipts（非敏感摘要與 digest）；唯讀，不需 execution token。"""
    return list_execution_receipts(limit)


@router.post("/api/v1/secretary/executions/{receipt_id}/cancel")
def cancel_secretary_execution(receipt_id: int, request: Request):
    _require_execution_token(request)
    try:
        return cancel_execution(receipt_id)
    except ExecutionRejected as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.error_code) from exc


@router.get("/api/v1/secretary/greeting")
def get_secretary_greeting(window: str = Query("today")):
    """小秘書問候卡：今天／近兩小時做了什麼＋一句鼓勵。只讀本機統計，數字皆可回溯。"""
    from core.secretary_greeting import GreetingRejected, build_greeting

    try:
        return build_greeting(window=window)
    except GreetingRejected as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.error_code) from exc


@router.get("/api/v1/calendar/agenda")
def get_calendar_agenda(date: Optional[str] = Query(None, max_length=10)):
    """ADR-015：某一天（預設今天）的本機行事曆行程，唯讀；每個回應都帶 claim boundary。"""
    from core.calendar_agenda import day_agenda

    target = None
    if date:
        try:
            target = datetime.strptime(date, "%Y-%m-%d")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="date must use YYYY-MM-DD") from exc
    return day_agenda(target)


@router.get("/api/v1/secretary/today")
def get_secretary_today():
    """「01 今天」的唯讀彙整：上次做到哪、最近一次早晨包收據、預設排程狀態。"""
    from core.secretary_packs import build_today_view

    return build_today_view()


@router.get("/api/v1/secretary/memory")
def get_secretary_memory(
    kind: Optional[str] = Query(None),
    project_key: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
):
    """記憶區筆記清單與各類計數；唯讀。observation 一律標記可刪除。"""
    from core.secretary_memory import MemoryRejected, list_notes

    try:
        return list_notes(kind=kind, project_key=project_key, limit=limit)
    except MemoryRejected as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.error_code) from exc


@router.post("/api/v1/secretary/memory")
def add_secretary_memory(payload: MemoryNoteRequest):
    """「記下來」：只寫使用者自己輸入的短文字到本機 secretary_notes；不觸碰任何 repo。

    這不是 L1 動作（沒有外部效果），因此沿用 loopback 邊界即可、不需 execution token。
    kind 只接受 user_note / preference / decision；observation 由秘書自己的 L0 收據產生。
    """
    from core.secretary_memory import USER_KINDS, MemoryRejected, add_note

    if payload.kind not in USER_KINDS:
        raise HTTPException(status_code=422, detail="kind_not_user_writable")
    try:
        return add_note(
            kind=payload.kind,
            body=payload.body,
            project_key=payload.project_key,
            title=payload.title,
            pinned=payload.pinned,
            source=(payload.source or "web")[:40],
        )
    except MemoryRejected as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.error_code) from exc


@router.delete("/api/v1/secretary/memory/{note_id}")
def delete_secretary_memory(note_id: int):
    """一鍵刪除單筆（含秘書觀察）。"""
    from core.secretary_memory import delete_note

    result = delete_note(note_id)
    if not result.get("deleted"):
        raise HTTPException(status_code=404, detail="note_not_found")
    return result


@router.delete("/api/v1/secretary/memory")
def clear_secretary_memory(kind: str = Query("observation")):
    """整類清除；預設只清秘書自己的觀察，使用者筆記需明確指定 kind。"""
    from core.secretary_memory import MemoryRejected, clear_notes

    try:
        return clear_notes(kind=kind)
    except MemoryRejected as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.error_code) from exc


@router.get("/api/v1/secretary/memory/context")
def get_secretary_memory_context():
    """秘書「當下記得什麼」：與注入對話 system prompt 完全相同的文字與收據；唯讀。"""
    from core.secretary_memory import memory_context

    return memory_context()


@router.post("/api/v1/secretary/meetings/followups")
def resolve_meeting_followup(payload: MeetingFollowupRequest):
    """把一條會議候選待辦變成未結事項，或忽略它（ADR-022 D4）。

    **只有這條路徑會把候選變成 open loop**——秘書自己永遠不會。
    """
    from .meeting_transcripts import accept_followup

    try:
        return accept_followup(
            payload.note_id,
            payload.index,
            action=payload.action,
            project_key=payload.project_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/v1/secretary/meetings")
def get_meeting_context():
    """現在是不是在開會（行事曆 ＋ 前景應用程式名稱）與逐字稿資料夾現況。唯讀。"""
    from .meeting_transcripts import list_transcripts, meeting_context, summary_provider

    cfg = get_config()
    files, meta = list_transcripts(cfg=cfg, limit=10)
    return {
        "context": meeting_context(cfg=cfg),
        "provider": summary_provider(cfg),
        "transcripts": [
            {
                "name": item["name"],
                "sha1": item["sha1"],
                "modified_at": item["modified_at"].isoformat(timespec="minutes"),
                "bytes": item["bytes"],
            }
            for item in files
        ],
        "sources": meta,
    }


@router.get("/api/v1/secretary/profile")
def get_secretary_profile():
    """ADR-018 宣告式個人檔案：從偏好筆記解析出的「本期優先」與「語氣」；唯讀。

    沒有寫入端點——要改就在對話框或 Telegram 打「偏好：優先：…」「偏好：語氣：…」，
    或刪掉那則偏好筆記；個人檔案永遠只是偏好筆記的一種讀法，不是第二套資料。
    """
    from core.secretary_profile import load_profile

    return load_profile()


@router.get("/api/v1/secretary/home")
def get_secretary_home():
    """ADR-019 秘書桌面：01 首頁「現在該看的」——焦點提案、一則記憶、上次做到哪、各詳情計數。

    只重新排列既有唯讀資料，規則確定性、不呼叫 LLM、不寫任何東西；每一節各自隔離失敗。
    """
    from core.secretary_home import build_home

    return build_home()


@router.post("/api/v1/secretary/scheduled-tasks/presets")
def create_secretary_schedule_presets(request: Request):
    """一鍵建立預設每日排程（早晨包 07:30、晚間 Handoff 21:30）；已存在者跳過。"""
    _require_execution_token(request)
    from core.secretary_packs import ensure_default_schedules

    try:
        return ensure_default_schedules()
    except ExecutionRejected as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.error_code) from exc


@router.get("/api/v1/secretary/scheduled-tasks")
def get_secretary_scheduled_tasks():
    """P5-R5 排程任務與可排程 template 清單；唯讀，不需 execution token。"""
    return list_scheduled_tasks()


@router.post("/api/v1/secretary/scheduled-tasks")
def create_secretary_scheduled_task(payload: ScheduledTaskCreateRequest, request: Request):
    """只能排程 server 註冊的 L0 唯讀 template；params 需通過白名單驗證。"""
    _require_execution_token(request)
    try:
        return create_scheduled_task(payload.model_dump())
    except ExecutionRejected as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.error_code) from exc


@router.patch("/api/v1/secretary/scheduled-tasks/{task_id}")
def update_secretary_scheduled_task(
    task_id: int, payload: ScheduledTaskUpdateRequest, request: Request
):
    _require_execution_token(request)
    try:
        return update_scheduled_task(task_id, payload.model_dump(exclude_unset=True))
    except ExecutionRejected as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.error_code) from exc


@router.delete("/api/v1/secretary/scheduled-tasks/{task_id}")
def delete_secretary_scheduled_task(task_id: int, request: Request):
    _require_execution_token(request)
    try:
        return delete_scheduled_task(task_id)
    except ExecutionRejected as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.error_code) from exc


@router.post("/api/v1/secretary/scheduled-tasks/{task_id}/run")
def run_secretary_scheduled_task(task_id: int, request: Request):
    """立即執行一次（寫 audit receipt，approved_via=web_click）。"""
    _require_execution_token(request)
    try:
        return run_scheduled_task_now(task_id, approved_via="web_click")
    except ExecutionRejected as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.error_code) from exc
