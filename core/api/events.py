"""採集寫入端點：AI／檔案／Git／視窗事件（TODO D4，ROADMAP §13 R1）。

Browser Extension 與本機採集器唯一的寫入路徑。AI 事件的去重與狀態判定在 `core/ingest.py`，
這裡只負責接收、驗證與寫入。

路徑與 handler 名稱與切分前完全相同，由 `tests/test_api_route_snapshot.py` 鎖住。
"""

import hashlib
import json

from core.config import get_config
from core.database import get_db
from core.ingest import browser_conversation_key, browser_response_status
from core.models import AIPromptEvent
from core.models import FileActivityEvent
from core.models import GitActivityEvent
from core.models import WindowEvent
from core.schemas import AIPromptCreate, FileActivityCreate, GitActivityCreate, WindowEventCreate
from core.time_utils import get_local_now
from datetime import timedelta
from fastapi import APIRouter
from fastapi import HTTPException


router = APIRouter()


@router.post("/api/v1/events/ai", status_code=201)
def create_or_update_ai_event(payload: AIPromptCreate):
    cfg = get_config()

    # D6 假開關修復：檢查該瀏覽器平台是否啟用 (支援別名比對)
    plat = (payload.platform or "").lower().strip()
    if plat in ("claude", "claude_web"):
        browser_enabled = cfg.get("watchers.browser.claude_web", cfg.get("watchers.browser.claude", True))
    elif plat in ("chatgpt", "chatgpt_web"):
        browser_enabled = cfg.get("watchers.browser.chatgpt", True)
    elif plat in ("gemini", "gemini_web"):
        browser_enabled = cfg.get("watchers.browser.gemini", True)
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported browser platform: {plat}")

    if not browser_enabled:
        return {"status": "skipped", "message": f"{payload.platform} monitoring is disabled in settings"}

    db = get_db()
    clean_prompt = payload.prompt_text.strip()
    if len(clean_prompt) < 2:
        raise HTTPException(status_code=422, detail="prompt_text is too short")
    clean_response = (payload.response_text or "").strip() or None
    now = get_local_now()
    conversation_key = browser_conversation_key(payload.conversation_id, payload.url)
    capture_state = str((payload.metadata or {}).get("capture_state", "")).lower()
    response_status = browser_response_status(clean_response, capture_state)

    with db.session_scope() as session:
        # 尋找最近 10 分鐘內、相同平台與 Prompt 的記錄 (Upsert 邏輯)
        recent_cutoff = get_local_now() - timedelta(minutes=10)
        existing = (
            session.query(AIPromptEvent)
            .filter(
                AIPromptEvent.platform == payload.platform,
                AIPromptEvent.conversation_id == conversation_key,
                AIPromptEvent.prompt_text == clean_prompt,
                AIPromptEvent.timestamp >= recent_cutoff
            )
            .order_by(AIPromptEvent.timestamp.desc())
            .first()
        )

        if existing:
            # 若已有記錄且新 payload 帶有回應內容，則更新回應
            if clean_response:
                existing.response_text = clean_response
                existing.response_status = response_status
                if payload.url: existing.url = payload.url
                if payload.project_tag: existing.project_tag = payload.project_tag
                if payload.cwd: existing.cwd = payload.cwd
            return {"status": "updated", "message": "Existing AI event updated with response"}

        # 否則新增記錄
        bucket = int(now.timestamp() // 600)
        browser_turn_key = hashlib.sha256(
            f"browser|{plat}|{conversation_key}|{clean_prompt}|{bucket}".encode("utf-8")
        ).hexdigest()
        event = AIPromptEvent(
            platform=payload.platform,
            url=payload.url,
            conversation_id=conversation_key,
            prompt_text=clean_prompt,
            response_text=clean_response,
            project_tag=payload.project_tag,
            cwd=payload.cwd,
            metadata_json=json.dumps(payload.metadata, ensure_ascii=False) if payload.metadata else None,
            timestamp=now,
            turn_key=browser_turn_key,
            source_path=payload.url,
            response_status=response_status,
        )
        session.add(event)
    return {"status": "created", "message": "New AI event logged"}


@router.post("/api/v1/events/file", status_code=201)
def create_file_event(payload: FileActivityCreate):
    db = get_db()
    with db.session_scope() as session:
        event = FileActivityEvent(
            file_path=payload.file_path,
            file_name=payload.file_name,
            file_type=payload.file_type,
            action=payload.action,
            size_bytes=payload.size_bytes,
            diff_summary=payload.diff_summary,
            project_name=payload.project_name,
            timestamp=get_local_now()
        )
        session.add(event)
    return {"status": "success", "message": "File event logged"}


@router.post("/api/v1/events/git", status_code=201)
def create_git_event(payload: GitActivityCreate):
    db = get_db()
    with db.session_scope() as session:
        existing = session.query(GitActivityEvent).filter_by(commit_hash=payload.commit_hash).first()
        if existing:
            return {"status": "exists", "message": "Commit already logged"}

        event = GitActivityEvent(
            repo_name=payload.repo_name,
            repo_path=payload.repo_path,
            commit_hash=payload.commit_hash,
            branch=payload.branch,
            author=payload.author,
            message=payload.message,
            files_changed_count=payload.files_changed_count,
            insertions=payload.insertions,
            deletions=payload.deletions,
            timestamp=get_local_now()
        )
        session.add(event)
    return {"status": "success", "message": "Git event logged"}


@router.post("/api/v1/events/window", status_code=201)
def create_window_event(payload: WindowEventCreate):
    db = get_db()
    with db.session_scope() as session:
        event = WindowEvent(
            start_time=payload.start_time or get_local_now(),
            end_time=payload.end_time or get_local_now(),
            duration_seconds=payload.duration_seconds,
            app_name=payload.app_name,
            window_title=payload.window_title,
            category=payload.category
        )
        session.add(event)
    return {"status": "success", "message": "Window event logged"}
