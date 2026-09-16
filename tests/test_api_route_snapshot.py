"""API 路由表快照：切分 `core/server.py` 不得改變任何一條對外路徑（TODO D4，ROADMAP §13 R1）。

`core/server.py` 原本是 1,995 行、98 條路由的單一模組。把它依領域切成多個 `APIRouter`
是純搬家——**路徑、方法與 handler 名稱一個都不能變**，因為前端（`web/app.js` 呼叫其中 97 條）、
Browser Extension、Telegram 與驗收中心全都直接依賴這些字面。

這支測試就是那個保證：下面的清單是切分**之前**從執行中的 app 抓下來的。搬完之後重跑，
少一條、多一條、路徑或方法變了、handler 改名了，都會在這裡失敗並列出差異。

清單也是對外 API 的實際目錄——要新增端點就在這裡多一行，刻意讓它成為一個需要說明的動作。
"""

from __future__ import annotations

from core.server import app

# (路徑, 逗號分隔的方法, handler 名稱)；HEAD／OPTIONS 由框架自動加，不列入。
EXPECTED_ROUTES: tuple[tuple[str, str, str], ...] = (
    ("/", "GET", "index_page"),
    ("/api/v1/acceptance/checklist", "GET", "get_acceptance_checklist"),
    ("/api/v1/acceptance/confirm", "POST", "confirm_acceptance_item"),
    ("/api/v1/background-tasks/today", "GET", "get_today_background_tasks"),
    ("/api/v1/calendar/agenda", "GET", "get_calendar_agenda"),
    ("/api/v1/capture/status", "GET", "get_capture_status"),
    ("/api/v1/config", "GET", "get_system_config"),
    ("/api/v1/config", "POST", "update_system_config"),
    ("/api/v1/context/related", "POST", "get_related_context"),
    ("/api/v1/context/sessions", "GET", "get_context_sessions"),
    ("/api/v1/control/open_path", "POST", "open_system_path"),
    ("/api/v1/control/start", "POST", "start_monitoring"),
    ("/api/v1/control/status", "GET", "get_control_status"),
    ("/api/v1/control/stop", "POST", "stop_monitoring"),
    ("/api/v1/events/ai", "POST", "create_or_update_ai_event"),
    ("/api/v1/events/file", "POST", "create_file_event"),
    ("/api/v1/events/git", "POST", "create_git_event"),
    ("/api/v1/events/recent", "GET", "get_recent_events"),
    ("/api/v1/events/window", "POST", "create_window_event"),
    ("/api/v1/extension/heartbeat", "POST", "receive_extension_heartbeat"),
    ("/api/v1/extension/status", "GET", "get_extension_monitor_status"),
    ("/api/v1/extension/verification", "POST", "start_extension_verification"),
    ("/api/v1/extension/verification/{verification_id}", "GET", "get_extension_verification"),
    ("/api/v1/github/connect", "POST", "connect_github"),
    ("/api/v1/github/disconnect", "POST", "disconnect_github"),
    ("/api/v1/github/prs", "GET", "list_github_prs"),
    ("/api/v1/github/repos", "GET", "list_github_repos"),
    ("/api/v1/github/status", "GET", "get_github_status"),
    ("/api/v1/github/sync", "POST", "trigger_github_sync"),
    ("/api/v1/health", "GET", "health_check"),
    ("/api/v1/line/connect", "POST", "connect_line"),
    ("/api/v1/line/disconnect", "POST", "disconnect_line_endpoint"),
    ("/api/v1/line/status", "GET", "get_line_status"),
    ("/api/v1/line/test", "POST", "test_line"),
    ("/api/v1/llm/status", "GET", "get_llm_secret_status"),
    ("/api/v1/logs/checkpoints", "GET", "get_checkpoint_logs"),
    ("/api/v1/logs/checkpoints/generate", "POST", "create_checkpoint_log"),
    ("/api/v1/logs/checkpoints/{filename}", "GET", "read_checkpoint_file"),
    ("/api/v1/notifications/channels", "GET", "get_notification_channels"),
    ("/api/v1/open-loops", "GET", "get_open_loops"),
    ("/api/v1/open-loops", "POST", "add_open_loop"),
    ("/api/v1/open-loops/{loop_id}/resolve", "POST", "resolve_open_loop"),
    ("/api/v1/open-loops/{loop_id}/transition", "POST", "update_open_loop_lifecycle"),
    ("/api/v1/projects/active", "GET", "get_active_projects"),
    ("/api/v1/projects/{project_key}/handoff", "GET", "get_project_handoff_api"),
    ("/api/v1/rag/chat", "POST", "chat_stream"),
    ("/api/v1/rag/chat/messages", "POST", "save_chat_message"),
    ("/api/v1/rag/chat/messages/{session_id}", "GET", "get_chat_messages"),
    ("/api/v1/rag/chat/sessions", "GET", "get_chat_sessions"),
    ("/api/v1/rag/chat/sessions", "POST", "create_or_update_session"),
    ("/api/v1/rag/chat/sessions/{session_id}", "DELETE", "delete_chat_session"),
    ("/api/v1/rag/clear-index", "POST", "clear_all_index"),
    ("/api/v1/rag/file-content", "GET", "get_file_content"),
    ("/api/v1/rag/files", "GET", "list_files"),
    ("/api/v1/rag/folders", "GET", "list_folders"),
    ("/api/v1/rag/folders", "POST", "add_folder"),
    ("/api/v1/rag/folders/{folder_id}", "DELETE", "delete_folder"),
    ("/api/v1/rag/folders/{folder_id}/remove-index", "POST", "remove_folder_index"),
    ("/api/v1/rag/jobs/current", "GET", "current_job"),
    ("/api/v1/rag/jobs/{job_id}", "GET", "job_detail"),
    ("/api/v1/rag/jobs/{job_id}/cancel", "POST", "cancel_job"),
    ("/api/v1/rag/jobs/{job_id}/pause", "POST", "pause_job"),
    ("/api/v1/rag/jobs/{job_id}/resume", "POST", "resume_job"),
    ("/api/v1/rag/memory/sync", "POST", "rag_memory_sync"),
    ("/api/v1/rag/open-file", "POST", "open_file_in_explorer"),
    ("/api/v1/rag/progress", "GET", "get_progress"),
    ("/api/v1/rag/retrieval/shutdown", "POST", "retrieval_worker_shutdown"),
    ("/api/v1/rag/retrieval/status", "GET", "retrieval_worker_status"),
    ("/api/v1/rag/retrieval/warmup", "POST", "retrieval_worker_warmup"),
    ("/api/v1/rag/scan", "POST", "trigger_scan"),
    ("/api/v1/rag/storage", "GET", "rag_storage"),
    ("/api/v1/rag/storage/chroma", "GET", "rag_storage_chroma"),
    ("/api/v1/rag/storage/compact-chroma", "POST", "rag_compact_chroma"),
    ("/api/v1/rag/storage/rebuild-bm25", "POST", "rag_rebuild_bm25"),
    ("/api/v1/rag/storage/verify", "POST", "rag_storage_verify"),
    ("/api/v1/rag/strategies", "GET", "list_strategies"),
    ("/api/v1/repos/onboarding-action", "POST", "run_repo_onboarding_action"),
    ("/api/v1/repos/onboarding-report", "GET", "get_repo_onboarding_report"),
    ("/api/v1/repos/sync-action", "POST", "run_local_repository_sync_action"),
    ("/api/v1/repos/sync-batch", "POST", "run_local_repository_batch"),
    ("/api/v1/repos/sync-batch-plan", "GET", "get_local_repository_batch_plan"),
    ("/api/v1/repos/sync-fetch-all", "POST", "run_local_repository_fetch_all"),
    ("/api/v1/repos/sync-snapshot", "GET", "get_local_repository_sync_snapshot"),
    ("/api/v1/repos/sync-status", "GET", "get_local_repository_sync_status"),
    ("/api/v1/secretary/executions", "GET", "get_secretary_executions"),
    ("/api/v1/secretary/executions/{receipt_id}/cancel", "POST", "cancel_secretary_execution"),
    ("/api/v1/secretary/greeting", "GET", "get_secretary_greeting"),
    ("/api/v1/secretary/home", "GET", "get_secretary_home"),
    ("/api/v1/secretary/meetings", "GET", "get_meeting_context"),
    ("/api/v1/secretary/meetings/followups", "POST", "resolve_meeting_followup"),
    ("/api/v1/secretary/memory", "DELETE", "clear_secretary_memory"),
    ("/api/v1/secretary/memory", "GET", "get_secretary_memory"),
    ("/api/v1/secretary/memory", "POST", "add_secretary_memory"),
    ("/api/v1/secretary/memory/context", "GET", "get_secretary_memory_context"),
    ("/api/v1/secretary/memory/{note_id}", "DELETE", "delete_secretary_memory"),
    ("/api/v1/secretary/profile", "GET", "get_secretary_profile"),
    ("/api/v1/secretary/proposals", "GET", "get_secretary_proposals"),
    ("/api/v1/secretary/proposals/snooze", "POST", "snooze_secretary_proposal"),
    ("/api/v1/secretary/proposals/{proposal_id}/execute", "POST", "execute_secretary_proposal"),
    ("/api/v1/secretary/scheduled-tasks", "GET", "get_secretary_scheduled_tasks"),
    ("/api/v1/secretary/scheduled-tasks", "POST", "create_secretary_scheduled_task"),
    ("/api/v1/secretary/scheduled-tasks/presets", "POST", "create_secretary_schedule_presets"),
    ("/api/v1/secretary/scheduled-tasks/{task_id}", "DELETE", "delete_secretary_scheduled_task"),
    ("/api/v1/secretary/scheduled-tasks/{task_id}", "PATCH", "update_secretary_scheduled_task"),
    ("/api/v1/secretary/scheduled-tasks/{task_id}/run", "POST", "run_secretary_scheduled_task"),
    ("/api/v1/secretary/today", "GET", "get_secretary_today"),
    ("/api/v1/summaries", "GET", "list_summaries"),
    ("/api/v1/summaries/generate", "POST", "generate_summary"),
    ("/api/v1/summaries/{date_str}", "GET", "get_summary_by_date"),
    ("/api/v1/system/heal", "POST", "trigger_system_heal"),
    ("/api/v1/system/health", "GET", "get_system_health"),
    ("/api/v1/system/maintenance", "POST", "trigger_system_maintenance"),
    ("/api/v1/system/maintenance/receipt", "GET", "get_system_maintenance_receipt"),
    ("/api/v1/system/wal-checkpoint", "POST", "trigger_wal_checkpoint"),
    ("/api/v1/telegram/approvals/arm", "POST", "arm_telegram_approvals"),
    ("/api/v1/telegram/approvals/arm-code", "POST", "issue_telegram_arm_code"),
    ("/api/v1/telegram/approvals/disarm", "POST", "disarm_telegram_approvals"),
    ("/api/v1/telegram/approvals/status", "GET", "telegram_approvals_status"),
    ("/api/v1/telegram/chat/status", "GET", "telegram_chat_status"),
    ("/api/v1/telegram/connect", "POST", "connect_telegram"),
    ("/api/v1/telegram/detect-chat-id", "POST", "detect_telegram_chat"),
    ("/api/v1/telegram/disconnect", "POST", "disconnect_telegram_endpoint"),
    ("/api/v1/telegram/status", "GET", "get_telegram_status"),
    ("/api/v1/telegram/test", "POST", "test_telegram"),
    ("/api/v1/usage/coverage", "GET", "get_usage_coverage"),
    ("/api/v1/usage/milestones/evaluate", "POST", "evaluate_usage_milestones"),
    ("/api/v1/usage/today", "GET", "get_today_usage"),
    ("/api/v1/utils/browse-folder", "POST", "api_browse_folder"),
    ("/docs", "GET", "swagger_ui_html"),
    ("/docs/oauth2-redirect", "GET", "swagger_ui_redirect"),
    ("/extension-monitor", "GET", "extension_monitor_page"),
    ("/openapi.json", "GET", "openapi"),
    ("/redoc", "GET", "redoc_html"),
)


def collect_routes() -> list[tuple[str, str, str]]:
    """走訪 app 的路由表，含以 `include_router` 掛上的子路由（FastAPI 不會把它們攤平）。"""

    def walk(routes):
        for route in routes:
            inner = getattr(route, "original_router", None)
            if inner is not None:
                yield from walk(inner.routes)
                continue
            methods = getattr(route, "methods", None)
            if methods:
                yield (
                    route.path,
                    ",".join(sorted(m for m in methods if m not in {"HEAD", "OPTIONS"})),
                    route.name,
                )

    return sorted(set(walk(app.routes)))


def test_route_table_is_unchanged():
    actual = collect_routes()
    expected = sorted(EXPECTED_ROUTES)
    missing = [row for row in expected if row not in actual]
    added = [row for row in actual if row not in expected]
    assert not missing, f"路由消失了：{missing}"
    assert not added, f"多了沒登記的路由：{added}"
    assert len(actual) == len(expected)


def test_every_route_has_a_unique_path_and_method_pair():
    seen: dict[tuple[str, str], str] = {}
    for path, methods, name in collect_routes():
        for method in methods.split(","):
            key = (path, method)
            assert key not in seen, f"{method} {path} 被 {seen[key]} 與 {name} 重複註冊"
            seen[key] = name


def test_rag_subsystem_is_still_mounted():
    """RAG 子系統是以 include_router 掛上的；切分主 server 時不得掉了這一句。"""
    rag_paths = {path for path, _m, _n in collect_routes() if path.startswith("/api/v1/rag/")}
    assert len(rag_paths) >= 25, sorted(rag_paths)
