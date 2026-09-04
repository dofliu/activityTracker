import os
import sys
import time
import argparse
import json
import logging
import secrets
import shutil
import threading
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any
from pathlib import Path
import yaml

# 強制 Windows 控制台輸出支援 UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 將專案根目錄加入 sys.path
sys.path.insert(0, str(Path(__file__).parent))

from core.config import get_config
from core.database import get_db
from core.runtime_paths import (
    browser_extension_assets_dir,
    config_template_path,
    default_config_path,
    resolve_runtime_path,
    runtime_asset_status,
    runtime_data_root,
)
from core.models import (
    AIPromptEvent,
    FileActivityEvent,
    GitActivityEvent,
    WindowEvent,
    DailySummary,
    ProjectState,
    OpenLoop,
    IngestionCheckpoint,
)
from core.manager import get_manager
from core.time_utils import get_local_now
from core.project_engine import (
    get_active_projects_list,
    get_open_loops_list,
    create_open_loop,
    reconcile_open_loop_lifecycle,
    transition_open_loop,
)
from synthesizer.aggregator import generate_daily_summary_pipeline, generate_periodic_checkpoint
from scripts.cleanup_noise import cleanup_noise_and_demo_data
from notifiers.desktop_notifier import DesktopNotifier
from exporters.daily_brief import export_daily_brief

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("OmniContext.Main")

LOCK_FILE = resolve_runtime_path(".instance.lock")


def acquire_instance_lock() -> bool:
    """單一實例鎖 (Single Instance Lock)：防止重複啟動造成資料庫衝突"""
    try:
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        if LOCK_FILE.exists():
            content = LOCK_FILE.read_text().strip()
            if content:
                try:
                    pid = int(content)
                    import psutil
                    if psutil.pid_exists(pid):
                        # 確認是否為 python process
                        proc = psutil.Process(pid)
                        if "python" in proc.name().lower():
                            logger.error(f"⚠️ 另一個 OmniContext 實例正在運行中 (PID: {pid})。請勿重複啟動！")
                            return False
                except Exception:
                    pass
        # 寫入當前 process PID
        LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
        return True
    except Exception as e:
        logger.warning(f"Failed to acquire instance lock: {e}")
        return True


def release_instance_lock():
    """釋放單一實例鎖"""
    try:
        if LOCK_FILE.exists():
            LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def run_web_and_services(autostart_monitoring: bool = True):
    """一鍵啟動 FastAPI Web 伺服器與監控服務"""
    if not acquire_instance_lock():
        sys.exit(1)

    try:
        import uvicorn
        from core.server import app
    except ImportError as e:
        logger.error(f"Missing web server dependencies: {e}. Please run `pip install -r requirements.txt`.")
        release_instance_lock()
        return

    cfg = get_config()
    db = get_db()
    manager = get_manager()
    logger.info("Initializing OmniContext Engine...")

    if autostart_monitoring:
        manager.start_all()

    # 檢索 worker 預熱：有索引時在背景把 Chroma/BM25/embedding 載進子程序，
    # 主服務不等待也不載入任何索引；沒有索引或設定關閉時只記錄略過原因。
    try:
        from rag.retrieval_client import maybe_warmup_on_start
        warmup_state = maybe_warmup_on_start()
        logger.info("RAG retrieval worker warm-up: %s (%s)", warmup_state["warmup"], warmup_state["reason"])
    except Exception as e:  # noqa: BLE001 — 預熱失敗不能擋住服務啟動
        logger.warning(f"RAG retrieval worker warm-up skipped: {e}")

    host = cfg.get("server.host", "127.0.0.1")
    port = cfg.get("server.port", 8765)

    print("\n" + "="*70)
    print(f"🌐 OmniContext 視覺化 Web 儀表板已就緒: http://{host}:{port}")
    print(f"👉 請在瀏覽器中打開 http://{host}:{port} 查看進行中專案、即時活動流與回顧！")
    print("="*70 + "\n")

    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    except KeyboardInterrupt:
        logger.info("\nStopping services...")
    finally:
        manager.stop_all()
        try:
            from rag.retrieval_client import retrieval_client
            retrieval_client.shutdown()
        except Exception:  # noqa: BLE001 — 收尾時的清理失敗不影響退出
            pass
        release_instance_lock()
        logger.info("OmniContext stopped.")


def cmd_now():
    """隨時查詢：一秒回答「我現在在做什麼、剛剛做了什麼、有什麼沒收尾」 (P2 核心)"""
    db = get_db()
    now = get_local_now()

    print("\n" + "="*70)
    print(f"🎯 OmniContext 即時工作全景 ({now.strftime('%Y-%m-%d %H:%M:%S')})")
    print("="*70)

    # 1. 取得當前活躍專案 (近 3 天)
    projects = get_active_projects_list()
    active_projs = [p for p in projects if p["status"] == "active"][:6]

    print("\n🔥 當前活躍專案 (Active Workstreams):")
    if active_projs:
        for p in active_projs:
            print(f"  • [{p['category']}] {p['display_name']} (最後活動: {p['last_activity_at']})")
            print(f"    └─ 動態: {p['last_action_summary']}")
    else:
        print("  (目前尚無 48 小時內的高頻活動專案)")

    # 2. 剛剛做了什麼 (最近 5 筆跨源事件)
    print("\n⚡ 最近 5 筆活動 (Recent Activities):")
    with db.session_scope() as session:
        ai_latest = session.query(AIPromptEvent).order_by(AIPromptEvent.timestamp.desc()).limit(3).all()
        git_latest = session.query(GitActivityEvent).order_by(GitActivityEvent.timestamp.desc()).limit(2).all()
        file_latest = session.query(FileActivityEvent).order_by(FileActivityEvent.timestamp.desc()).limit(2).all()

        recent_all = []
        for a in ai_latest:
            resp_info = f" -> {a.response_text[:40]}..." if a.response_text else ""
            recent_all.append((a.timestamp, f"[AI:{a.platform.upper()}] {a.prompt_text[:45]}...{resp_info}"))
        for g in git_latest:
            recent_all.append((g.timestamp, f"[Git:{g.repo_name}] {g.message[:50]}..."))
        for f in file_latest:
            recent_all.append((f.timestamp, f"[File:{f.action.upper()}] {f.file_name}"))

        recent_all.sort(key=lambda x: x[0], reverse=True)
        for t, desc in recent_all[:5]:
            print(f"  [{t.strftime('%H:%M:%S')}] {desc}")

    # 3. 未結事項 (Open Loops)
    open_loops = get_open_loops_list()
    print(f"\n📌 待收尾與未結事項 (Open Loops - 共 {len(open_loops)} 項):")
    if open_loops:
        for ol in open_loops[:8]:
            print(f"  [ ] ({ol['project_key']}) {ol['title']} (建立於 {ol['created_at']})")
    else:
        print("  (目前無待辦事項)")
    print("="*70 + "\n")


def cmd_summary(target_date: str | None, start_date: str | None, end_date: str | None, provider: str | None, force: bool):
    """手動觸發生成特定日期或區間的 AI 總結報告"""
    from synthesizer.aggregator import generate_summary_pipeline
    start_d = start_date or target_date or get_local_now().strftime("%Y-%m-%d")
    end_d = end_date or target_date or get_local_now().strftime("%Y-%m-%d")
    
    logger.info(f"Generating summary for {start_d} ~ {end_d} using provider '{provider or 'default'}'...")
    res = generate_summary_pipeline(
        start_date_str=start_d,
        end_date_str=end_d,
        provider_override=provider,
        force_refresh=force
    )
    print("\n" + "="*70)
    print(f"[工作回顧生成完成] 區間/日期: {res.get('date_str')}")
    print("="*70)
    print(res.get("markdown"))
    if "report_path" in res:
        print("\n" + "-"*70)
        print(f"[存檔路徑] {res['report_path']}")
        print("-"*70)


def cmd_checkpoint(hours: int = 2):
    """手動產出最近 N 小時的活動快照日誌"""
    logger.info(f"Generating checkpoint log for past {hours} hours...")
    res = generate_periodic_checkpoint(hours=hours)
    print("\n" + "="*70)
    print(f"⏱️ 活動快照日誌已產生: {res.get('file_name')}")
    print("="*70)
    print(res.get("content"))
    print("\n" + "-"*70)
    print(f"[存檔路徑] {res.get('file_path')}")
    print("-"*70)


def cmd_status():
    """查看本地 SQLite 資料庫累積事件統計與專案狀態"""
    import json
    import urllib.request
    from sqlalchemy import func

    cfg = get_config()
    status_source = "local fallback"
    try:
        host = cfg.get("server.host", "127.0.0.1")
        port = int(cfg.get("server.port", 8765))
        live_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
        with urllib.request.urlopen(
            f"http://{live_host}:{port}/api/v1/control/status",
            timeout=2,
        ) as response:
            status = json.loads(response.read().decode("utf-8"))
            status_source = "live API"
    except Exception:
        status = get_manager().get_status()

    db = get_db()

    with db.session_scope() as session:
        project_counts = dict(
            session.query(ProjectState.status, func.count(ProjectState.id))
            .group_by(ProjectState.status)
            .all()
        )
        open_loops_count = session.query(OpenLoop).filter(OpenLoop.status == "open").count()
        ai_nonempty_count = session.query(AIPromptEvent).filter(
            AIPromptEvent.response_text.isnot(None),
            func.length(func.trim(AIPromptEvent.response_text)) > 0,
            ~AIPromptEvent.response_text.like("[%Session]"),
            ~AIPromptEvent.response_text.like("[%Agent Session]")
        ).count()
        ai_final_candidate_count = session.query(AIPromptEvent).filter(
            AIPromptEvent.response_status == "final_candidate",
            AIPromptEvent.response_text.isnot(None),
            func.length(func.trim(AIPromptEvent.response_text)) > 0,
        ).count()
        checkpoint_errors = session.query(IngestionCheckpoint).filter(
            IngestionCheckpoint.last_error.isnot(None),
            func.length(func.trim(IngestionCheckpoint.last_error)) > 0,
        ).count()

    print("\n" + "="*50)
    print("📊 OmniContext 系統統計數據")
    print("="*50)
    print(f"• 監控狀態                 : {'🟢 運行中' if status['is_running'] else '🔴 已停止'} ({status_source})")
    print(f"• 專案狀態                 : active={project_counts.get('active', 0)}, idle={project_counts.get('idle', 0)}, stale={project_counts.get('stale', 0)}")
    print(f"• 未結待辦事項 (Open Loops): {open_loops_count} 項")
    print(f"• AI 互動紀錄 (Prompts)    : {status['metrics']['ai_prompts_count']} 筆")
    print(f"  ├─ 非空 assistant 回應   : {ai_nonempty_count} 筆")
    print(f"  └─ final candidate       : {ai_final_candidate_count} 筆")
    print(f"• 檔案與論文異動事件       : {status['metrics']['file_events_count']} 筆")
    print(f"• Git Commit 紀錄          : {status['metrics']['git_commits_count']} 筆")
    print(f"• 視窗焦點時間統計         : {status['metrics']['window_events_count']} 筆")
    print(f"• 已生成每日摘要報告       : {status['metrics']['daily_summaries_count']} 篇")
    print(f"• Ingestion checkpoint 錯誤: {checkpoint_errors} 個來源")
    runtime = status.get("collector_runtime", {})
    freshness = status.get("collector_health", {})
    if runtime:
        print("• Collector runtime         : " + ", ".join(f"{k}={v}" for k, v in runtime.items()))
    if freshness:
        print("• Data freshness            : " + ", ".join(f"{k}={v}" for k, v in freshness.items()))
    migration = status.get("database_migration", {})
    if migration:
        print(
            "• Database migration        : "
            f"{migration.get('state', 'unknown')} "
            f"({migration.get('current_version', '?')}/"
            f"{migration.get('latest_version', '?')})"
        )
    print("="*50 + "\n")


def cmd_open_loop(loop_id: int, status: str, note: Optional[str] = None):
    """人工複核 Open Loop lifecycle。"""
    result = transition_open_loop(loop_id, status, note)
    print(
        f"Open Loop #{result['id']}: "
        f"{result['old_status']} -> {result['status']}"
    )


def cmd_reconcile_open_loops():
    result = reconcile_open_loop_lifecycle()
    print(
        "Open Loop reconciliation: "
        f"fingerprints={result['backfilled']}, superseded={result['superseded']}"
    )


def cmd_backup(output_dir: Optional[str] = None):
    from core.data_lifecycle import create_configured_backup

    receipt = create_configured_backup(output_dir)
    print(f"backup: {receipt['path']}")
    print(f"integrity: {receipt['integrity']}")
    print(f"tables: {receipt['table_count']}")
    print(f"size_bytes: {receipt['size_bytes']}")
    print(f"sha256: {receipt['sha256']}")


def cmd_restore_drill(
    backup_path: Optional[str] = None,
    receipt_dir: Optional[str] = None,
):
    from core.data_lifecycle import run_configured_restore_drill

    receipt = run_configured_restore_drill(backup_path, receipt_dir)
    print(f"restore_drill: {receipt['status']}")
    print(f"source_backup: {receipt['source_backup']['path']}")
    print(f"integrity: {receipt['isolated_restore']['integrity']}")
    print(f"schema_match: {receipt['checks']['schema_match']}")
    print(f"row_counts_match: {receipt['checks']['row_counts_match']}")
    print(f"temporary_copy_retained: {receipt['isolated_restore']['temporary_copy_retained']}")
    print(f"receipt: {receipt['receipt_path']}")


def cmd_migration_status(db_path: Optional[str] = None):
    from core.data_lifecycle import configured_database_path
    from core.migrations import inspect_migration_status

    target = Path(db_path).expanduser().resolve() if db_path else configured_database_path()
    status = inspect_migration_status(target)
    print(f"database: {status['database_path']}")
    print(f"state: {status['state']}")
    print(f"schema_version: {status['current_version']}/{status['latest_version']}")
    print(f"applied_versions: {status['applied_versions']}")
    print(f"pending_versions: {status['pending_versions']}")
    if status["error"]:
        print(f"error: {status['error']}")


def cmd_init(
    watch_directories: List[str],
    show_token: bool = False,
    rotate_token: bool = False,
):
    """建立可攜式本機設定，且只在 token 空白時產生 browser ingest capability。"""
    root = runtime_data_root()
    config_path = default_config_path()
    example_path = config_template_path()
    root.mkdir(parents=True, exist_ok=True)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    created = False
    if not config_path.exists():
        if not example_path.exists():
            raise FileNotFoundError(f"找不到設定範本: {example_path}")
        shutil.copy2(example_path, config_path)
        created = True

    config_data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    security = config_data.setdefault("security", {})
    token = str(security.get("browser_extension_ingest_token", "") or "")
    token_created = False
    if not token or rotate_token:
        token = secrets.token_urlsafe(32)
        security["browser_extension_ingest_token"] = token
        token_created = True

    # ADR-008 D4：executor 專用憑證，與 ingest token 分開；只在空白時產生。
    execution_token = str(security.get("execution_token", "") or "")
    execution_token_created = False
    if not execution_token or rotate_token:
        execution_token = secrets.token_urlsafe(32)
        security["execution_token"] = execution_token
        execution_token_created = True

    if watch_directories:
        watcher = config_data.setdefault("watchers", {}).setdefault("file_watcher", {})
        normalized = []
        for raw_path in watch_directories:
            path = str(Path(raw_path).expanduser().resolve())
            if path not in normalized:
                normalized.append(path)
        watcher["watch_directories"] = normalized

    config_path.write_text(
        yaml.safe_dump(config_data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    for relative in ("reports", "logs/checkpoints"):
        resolve_runtime_path(relative).mkdir(parents=True, exist_ok=True)
    get_config().load(config_path)

    print(f"config: {config_path} ({'created' if created else 'updated'})")
    print(
        "database: "
        + str(resolve_runtime_path(
            config_data.get("database", {}).get("db_path", "omni_context.db")
        ))
    )
    if show_token:
        print("browser extension ingest token（請貼到擴充套件設定）:")
        print(token)
        print("executor execution token（批准執行秘書白名單動作時使用）:")
        print(execution_token)
    else:
        print(
            "browser extension ingest token: "
            + ("generated（以 --show-token 明確顯示）" if token_created else "configured")
        )
        print(
            "executor execution token: "
            + ("generated（以 --show-token 明確顯示）" if execution_token_created else "configured")
        )


def cmd_assets_status():
    """檢查 wheel/source 中必要 runtime assets，不輸出任何設定密鑰。"""
    status = runtime_asset_status()
    print(f"status: {status['status']}")
    print(f"application_home: {status['application_home']}")
    print(f"runtime_data_root: {status['runtime_data_root']}")
    print(f"config_path: {status['config_path']}")
    print(f"config_template: {status['config_template']}")
    print(f"web_assets: {status['web_assets']}")
    print(f"browser_extension: {status['browser_extension']}")
    for name, passed in status["checks"].items():
        print(f"{name}: {'ok' if passed else 'missing'}")
    if status["status"] != "ok":
        raise SystemExit(1)


def cmd_extension_path():
    """輸出可供 Chrome/Edge Load unpacked 使用的目錄。"""
    extension_dir = browser_extension_assets_dir().resolve()
    if not (extension_dir / "manifest.json").is_file():
        raise FileNotFoundError(f"找不到 Browser Extension assets: {extension_dir}")
    print(extension_dir)


def cmd_semantic_index(
    project: Optional[str] = None,
    rebuild: bool = False,
    limit: Optional[int] = None,
    as_json: bool = False,
):
    """P3-2：建立或增量更新本機 semantic index。"""
    from core.semantic_index import build_semantic_index

    result = build_semantic_index(project=project, rebuild=rebuild, limit=limit)
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f"status: {result.get('status')}")
    if result.get("status") == "indexed":
        print(f"embedding_model: {result['embedding_model']}")
        print(f"source_documents: {result['source_documents']}")
        print(f"indexed: {result['indexed']}")
        print(f"unchanged: {result['unchanged']}")
        print(f"dimensions: {result['dimensions']}")
        print(f"boundary: {result['claim_boundary']}")


def cmd_ask(
    question: str,
    project: Optional[str] = None,
    top_k: Optional[int] = None,
    no_synthesis: bool = False,
    as_json: bool = False,
):
    """P3-3：從本機 semantic evidence 回答並列出可追溯來源。"""
    from core.semantic_index import ask_local_context

    result = ask_local_context(
        question,
        project=project,
        top_k=top_k,
        synthesize=not no_synthesis,
    )
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(result.get("answer") or "已完成 semantic retrieval；未啟用答案生成。")
    print("\n來源：")
    for item in result.get("sources", []):
        print(
            f"[{item['citation']}] {item['source_ref']} · "
            f"score={item['score']:.4f} · trust={item['trust_status']}"
        )
    print(f"\nBoundary: {result['claim_boundary']}")


def cmd_sessions(
    project: Optional[str] = None,
    hours: Optional[int] = None,
    gap_minutes: Optional[int] = None,
    limit: Optional[int] = None,
    as_json: bool = False,
):
    """P3-5：將既有事件以 deterministic inactivity gap 聚合成工作階段。"""
    from core.context_memory import build_recent_work_sessions

    result = build_recent_work_sessions(
        project=project,
        hours=hours,
        gap_minutes=gap_minutes,
        limit=limit,
    )
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f"status: {result['status']} · sessions: {len(result['sessions'])}")
    for item in result["sessions"]:
        print(
            f"[{item['session_id']}] {item['project_key']} · "
            f"{item['started_at']} → {item['ended_at']} · "
            f"{item['events_observed']} events"
        )
        print(f"  {item['headline']}")
    print(f"\nBoundary: {result['claim_boundary']}")


def cmd_recall(
    question: str,
    project: Optional[str] = None,
    threshold: Optional[float] = None,
    top_k: int = 8,
    as_json: bool = False,
):
    """P3-4：提示相似的本機歷史 evidence，不保存查詢、不判定工作重複。"""
    from core.context_memory import find_related_work

    result = find_related_work(
        question,
        project=project,
        threshold=threshold,
        top_k=top_k,
    )
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(result["advisory"])
    for item in result["matches"]:
        print(
            f"[{item['citation']}] {item['source_ref']} · "
            f"score={item['score']:.4f} · trust={item['trust_status']}"
        )
        print(f"  {item['title']}")
    print(f"\nBoundary: {result['claim_boundary']}")


def cmd_notify_telegram(action: str, date_str: Optional[str] = None, dry_run: bool = False):
    """測試或手動發送推播（支援 --dry-run 預覽）。

    ADR-014：內容組裝與傳送已分離，因此預覽與實際送出的是**同一份**內容；
    實際送出會推到所有啟用的通道（Telegram／LINE）。
    """
    from notifiers.messages import (
        build_daily_summary,
        build_morning_briefing,
        build_stagnation_alert,
        render_plain,
    )
    from notifiers.secretary_push import (
        push_daily_summary,
        push_enabled,
        push_morning_briefing,
        push_stagnation_alert,
    )

    resolved_date = date_str or get_local_now().strftime("%Y-%m-%d")
    builders = {
        "summary": lambda: build_daily_summary(resolved_date),
        "briefing": build_morning_briefing,
        "stagnation": build_stagnation_alert,
    }
    if action not in builders:
        print(f"未知的通知類型: {action}")
        return

    if dry_run:
        message = builders[action]()
        print("\n" + "=" * 50)
        print("📱 推播預覽 (Dry-run Mode)")
        print("=" * 50)
        print(render_plain(message) if message else "（目前沒有內容可推播）")
        print("=" * 50 + "\n")
        return

    if not push_enabled():
        print("❌ 沒有已設定的推播通道；請先在儀表板「設定」完成 Telegram 或 LINE 連線")
        return

    label = {"summary": f"{resolved_date} 的每日日報", "briefing": "晨間簡報", "stagnation": "停滯專案警示"}[action]
    print(f"正在發送{label}…")
    receipt = {
        "summary": lambda: push_daily_summary(resolved_date),
        "briefing": push_morning_briefing,
        "stagnation": push_stagnation_alert,
    }[action]()
    if receipt.get("skipped"):
        print(f"⚠️ 未發送：{receipt['skipped']}")
        return
    for item in receipt["results"]:
        mark = "✅" if item.get("sent") else "❌"
        detail = "" if item.get("sent") else f"（{item.get('error')}）"
        print(f"  {mark} {item['channel']}{detail}")
    print(f"完成：{receipt['sent']}/{receipt['attempted']} 個通道送出")


def cmd_notify_desktop(action: str, dry_run: bool = False):
    """發送 Windows 桌面通知"""
    notifier = DesktopNotifier()
    if action in ("briefing", "summary"):
        ok = notifier.send_morning_briefing(dry_run=dry_run)
    elif action == "evening":
        ok = notifier.send_evening_summary(dry_run=dry_run)
    elif action == "stagnation":
        ok = notifier.send_stagnation_alert(dry_run=dry_run)
    else:
        print(f"未知的通知類型: {action}")
        return

    if not dry_run:
        print("✅ 桌面通知已送出" if ok else "❌ 桌面通知發送失敗，請查看日誌")


def cmd_brief(output_dir: Optional[str] = None, notify: bool = False):
    """產出每日簡報檔案，並可選擇同時發送桌面通知"""
    res = export_daily_brief(output_dir)
    print("\n" + "=" * 60)
    print(f"📌 每日簡報已更新 ({res['generated_at']})")
    print("=" * 60)
    print(f"• 進行中專案 : {res['active_count']} 個")
    print(f"• 尚未收尾   : {res['open_loops_count']} 項")
    print(f"• 停滯專案   : {res['stagnant_count']} 個")
    print("-" * 60)
    for f in res["files"]:
        print(f"  → {f}")
    print("=" * 60 + "\n")

    if notify:
        DesktopNotifier().send_morning_briefing()


def cmd_github(action: str):
    """GitHub 雲端整合狀態與手動同步"""
    from integrations.github_client import get_github_client
    client = get_github_client()
    if action in ["status", None]:
        status = client.test_connection()
        print("\n" + "="*50)
        print("🐙 GitHub 雲端認證狀態")
        print("="*50)
        if status.get("connected"):
            print(f"• 連線狀態 : 🟢 已認證 (@{status.get('username')})")
            print(f"• 姓名     : {status.get('name')}")
            print(f"• 公開倉庫 : {status.get('public_repos')} 個")
            print(f"• 私有倉庫 : {status.get('total_private_repos')} 個")
            print(f"• Token 權限: {', '.join(status.get('scopes', []))}")
            if status.get("rate_limit"):
                print(f"• API 額度 : {status['rate_limit']['remaining']} / {status['rate_limit']['limit']}")
        else:
            print(f"• 連線狀態 : 🔴 未連線 ({status.get('message')})")
        print("="*50 + "\n")
    elif action == "sync":
        print("正在同步 GitHub 所有 Public / Private 倉庫與 PRs...")
        res = client.sync_all(max_repos=50)
        print(f"✅ 同步完成！已同步 {res.get('synced_repos_count')} 個專案與 {res.get('synced_prs_count')} 筆 PR 狀態。")


def cmd_resume(project_key: Optional[str] = None, copy_to_clipboard: bool = False, turns: int = 5, as_json: bool = False):
    """產出結構化專案接續 Prompt (P3-1 Context Handoff)"""
    from core.handoff_engine import build_project_handoff, format_handoff_markdown
    import json

    if not project_key:
        active_list = get_active_projects_list()
        if active_list:
            project_key = active_list[0].get("project_key")
        else:
            project_key = "activityTracker"

    data = build_project_handoff(project_key, turns_limit=turns)

    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    md = format_handoff_markdown(data)
    print("\n" + "=" * 65)
    print(f"📋 專案接續 Context Handoff: {data.get('display_name') or project_key}")
    print("=" * 65 + "\n")
    print(md)
    print("=" * 65)

    if copy_to_clipboard:
        try:
            from core.platform_services import copy_text_to_clipboard

            copy_text_to_clipboard(md)
            print("⚡ [OK] 接續 Prompt 已成功複製到剪貼簿！可直接貼入任何 AI 視窗開工。")
        except Exception as e:
            logger.warning(f"Could not copy to clipboard: {e}")


def cmd_maintain(max_backups: int = 7, retention_days: int = 90, dry_run: bool = False):
    from core.data_lifecycle import run_database_maintenance
    print(f"🔧 Starting database maintenance (retention={retention_days}d, max_backups={max_backups}, dry_run={dry_run})...")
    res = run_database_maintenance(max_backups=max_backups, retention_days=retention_days, dry_run=dry_run)
    print(json.dumps(res, indent=2, ensure_ascii=False))


def cmd_heal():
    manager = get_manager()
    print("🩺 Running self-healing supervisor check on all collectors...")
    res = manager.supervise_and_heal()
    print(json.dumps(res, indent=2, ensure_ascii=False))


def cmd_wal_checkpoint(mode: str = "TRUNCATE"):
    from core.data_lifecycle import checkpoint_sqlite_database
    print(f"📦 Executing SQLite WAL checkpoint ({mode})...")
    res = checkpoint_sqlite_database(mode=mode)
    print(json.dumps(res, indent=2, ensure_ascii=False))


_ACCEPTANCE_ICONS = {
    "passed": "✅",
    "attested": "🖊️",
    "partial": "🟡",
    "pending": "⬜",
    "needs_human": "👤",
    "not_configured": "➖",
    "runtime_only": "🌐",
}


def _fetch_acceptance_report(item: Optional[str]) -> tuple[dict, str]:
    """服務在跑就用 live API（檢索 worker 這類記憶體狀態只有那個程序看得到）。"""
    import urllib.parse
    import urllib.request

    cfg = get_config()
    try:
        host = cfg.get("server.host", "127.0.0.1")
        port = int(cfg.get("server.port", 8765))
        live_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
        url = f"http://{live_host}:{port}/api/v1/acceptance/checklist"
        if item:
            url += "?" + urllib.parse.urlencode({"item": item})
        with urllib.request.urlopen(url, timeout=5) as response:
            return json.loads(response.read().decode("utf-8")), "live API"
    except Exception:
        from core.acceptance import build_acceptance_report

        only = [part.strip() for part in item.split(",") if part.strip()] if item else None
        return build_acceptance_report(runtime=False, only=only), "local read-only"


def cmd_verify(
    item: Optional[str] = None,
    as_json: bool = False,
    output: Optional[str] = None,
    confirm: Optional[str] = None,
    note: str = "",
    unconfirm: Optional[str] = None,
):
    """驗收中心：查 docs/TODO.md A 段每一項的本機收據現況（唯讀）。"""
    from core.acceptance import record_human_confirmation

    for target, confirmed in ((confirm, True), (unconfirm, False)):
        if not target:
            continue
        try:
            result = record_human_confirmation(target, confirmed=confirmed, note=note)
        except ValueError as exc:
            print(f"❌ {exc}")
            return
        verb = "已記下人工確認" if confirmed else "已取消人工確認"
        print(f"🖊️  {verb}：{result['item_id']}（{result['path']}）")

    report, source = _fetch_acceptance_report(item)

    if output:
        out_path = Path(output).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"📄 收據已寫入 {out_path}")

    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    summary = report["summary"]
    print("\n" + "=" * 62)
    print(f"🧾 驗收中心（來源：{report['source']}；讀取方式：{source}）")
    print("=" * 62)
    for entry in report["items"]:
        icon = _ACCEPTANCE_ICONS.get(entry["status"], "•")
        flag = " 🔴" if entry["blocks_release"] and entry["status"] not in ("passed", "attested") else ""
        print(f"{icon} {entry['id']:<3} {entry['title']}{flag}")
        print(f"     {entry['detail']}")
        if entry["status"] in ("pending", "partial", "needs_human", "runtime_only"):
            print(f"     怎麼做：{entry['how']}")
    print("-" * 62)
    counts = "、".join(f"{k}={v}" for k, v in sorted(summary["counts"].items()))
    print(f"合計 {summary['total']} 項：{counts}")
    if summary["blocking_release"]:
        print(f"🔴 仍擋 release_ready：{'、'.join(summary['blocking_release'])}")
    if report.get("release_gates_note"):
        print(f"ℹ️  {report['release_gates_note']}")
    if report["release_gates"]:
        print("release gates（ROADMAP §12.3）：")
    for gate in report["release_gates"]:
        icon = _ACCEPTANCE_ICONS.get(gate["status"], "•")
        pending_items = gate.get("outstanding") or []
        shown = "、".join(pending_items[:4])
        if len(pending_items) > 4:
            shown += f" 等 {len(pending_items)} 項"
        tail = f"（待辦：{shown}）" if pending_items else ""
        print(f"  {icon} {gate['id']} {gate['text']}{tail}")
    print(f"\n邊界：{report['claim_boundary']}")
    print("=" * 62 + "\n")


def main():
    parser = argparse.ArgumentParser(description="OmniContext - 個人全景上下文與進行中專案智慧中樞")
    subparsers = parser.add_subparsers(dest="command", help="子指令")

    # resume 指令 (P3-1)
    resume_parser = subparsers.add_parser("resume", help="產出專案接續 Prompt (Context Handoff)，一鍵接續任何 AI 開工")
    resume_parser.add_argument("project", nargs="?", help="專案名稱或識別碼 (未指定時預設當前最活躍專案)")
    resume_parser.add_argument("-c", "--copy", action="store_true", help="自動將生成的接續 Prompt 複製到剪貼簿")
    resume_parser.add_argument("--turns", type=int, default=5, help="納入之歷史 AI 對話回合數 (預設 5)")
    resume_parser.add_argument("--json", action="store_true", help="以 JSON 格式輸出結構化數據")

    init_parser = subparsers.add_parser("init", help="建立跨平台本機設定與 browser ingest token")
    init_parser.add_argument(
        "--watch",
        action="append",
        default=[],
        help="要監控的目錄，可重複指定；未指定時保留範本設定",
    )
    init_parser.add_argument("--show-token", action="store_true", help="顯示既有 browser ingest token")
    init_parser.add_argument("--rotate-token", action="store_true", help="旋轉 browser ingest token")

    llm_test_parser = subparsers.add_parser(
        "llm-test", help="診斷 LLM provider 連線與設定（ollama/gemini/anthropic/openai）"
    )
    llm_test_parser.add_argument(
        "--provider", default=None, help="要測試的 provider；預設用 synthesizer.provider"
    )
    llm_test_parser.add_argument(
        "--no-generate", action="store_true", help="只檢查連線與設定，不送測試生成請求"
    )

    # run / web
    run_parser = subparsers.add_parser("run", help="啟動 Web 儀表板與後台監控服務")
    run_parser.add_argument("--no-autostart", action="store_true")

    web_parser = subparsers.add_parser("web", help="啟動 Web 視覺化儀表板 (同 run)")
    web_parser.add_argument("--no-autostart", action="store_true")

    # now 指令
    subparsers.add_parser("now", help="一秒查詢當前進行中工作與最近動作")

    # summary 指令
    summary_parser = subparsers.add_parser("summary", help="生成每日或自訂區間以專案為主軸的 AI 總結報告")
    summary_parser.add_argument("--date", help="指定單一日期 (YYYY-MM-DD)")
    summary_parser.add_argument("--start", help="自訂區間起始日期 (YYYY-MM-DD)")
    summary_parser.add_argument("--end", help="自訂區間結束日期 (YYYY-MM-DD)")
    summary_parser.add_argument("--provider", help="指定 LLM 供應商 (gemini, anthropic, openai, ollama)")
    summary_parser.add_argument("--force", action="store_true", help="強制重新生成已存在的摘要")

    # checkpoint 指令
    cp_parser = subparsers.add_parser("checkpoint", help="手動生成近時段的活動快照 Log")
    cp_parser.add_argument("--hours", type=int, default=2, help="回溯時數 (預設 2 小時)")

    # github 指令
    gh_parser = subparsers.add_parser("github", help="GitHub 雲端專案與 PR 同步管理")
    gh_parser.add_argument("action", choices=["status", "sync"], default="status", nargs="?", help="動作 (status, sync)")

    # notify 指令
    notify_parser = subparsers.add_parser("notify", help="手動觸發提醒推播 (預設桌面通知)")
    notify_parser.add_argument("type", choices=["summary", "briefing", "evening", "stagnation"], help="通知類型")
    notify_parser.add_argument("--channel", choices=["desktop", "telegram"], default="desktop", help="推播通道 (預設 desktop)")
    notify_parser.add_argument("--date", help="指定日期 (YYYY-MM-DD)")
    notify_parser.add_argument("--dry-run", action="store_true", help="不實際發送，僅在終端機預覽推播格式")

    # brief 指令
    brief_parser = subparsers.add_parser("brief", help="產出每日簡報檔案 (寫入每日入口目錄)")
    brief_parser.add_argument("--dir", help="覆寫輸出目錄")
    brief_parser.add_argument("--notify", action="store_true", help="產出後同時發送桌面通知")

    # clear-demo 指令
    subparsers.add_parser("clear-demo", help="清除示範假資料與歷史噪音")

    # status 指令
    subparsers.add_parser("status", help="查看當前數據庫統計與監控狀態")

    index_parser = subparsers.add_parser("index", help="建立/更新本機 semantic index (P3-2)")
    index_parser.add_argument("--project", help="只索引指定專案")
    index_parser.add_argument("--rebuild", action="store_true", help="重建指定範圍的索引")
    index_parser.add_argument("--limit", type=int, help="限制本次來源筆數（smoke/test 用）")
    index_parser.add_argument("--json", action="store_true", help="輸出 JSON receipt")

    ask_parser = subparsers.add_parser("ask", help="查詢本機 semantic context (P3-3)")
    ask_parser.add_argument("question", help="要詢問的問題")
    ask_parser.add_argument("--project", help="限制指定專案")
    ask_parser.add_argument("--top-k", type=int, help="引用來源數量")
    ask_parser.add_argument("--no-synthesis", action="store_true", help="只回傳 retrieval，不呼叫生成模型")
    ask_parser.add_argument("--json", action="store_true", help="輸出 JSON")

    sessions_parser = subparsers.add_parser("sessions", help="列出 inferred work sessions (P3-5)")
    sessions_parser.add_argument("--project", help="限制指定專案")
    sessions_parser.add_argument("--hours", type=int, help="回溯時數（預設讀取 context_memory.recent_hours）")
    sessions_parser.add_argument("--gap-minutes", type=int, help="切分 session 的 inactivity gap")
    sessions_parser.add_argument("--limit", type=int, help="最多顯示 session 數")
    sessions_parser.add_argument("--json", action="store_true", help="輸出 JSON")

    recall_parser = subparsers.add_parser("recall", help="提示相似歷史工作 evidence (P3-4)")
    recall_parser.add_argument("question", help="要比對的工作或問題")
    recall_parser.add_argument("--project", help="限制指定專案")
    recall_parser.add_argument("--threshold", type=float, help="相似度門檻（預設讀取 context_memory.related_threshold）")
    recall_parser.add_argument("--top-k", type=int, default=8, help="候選來源數")
    recall_parser.add_argument("--json", action="store_true", help="輸出 JSON")

    loop_parser = subparsers.add_parser("open-loop", help="複核 Open Loop lifecycle")
    loop_parser.add_argument("id", type=int, help="Open Loop ID")
    loop_parser.add_argument("status", choices=["open", "stale", "resolved", "superseded"])
    loop_parser.add_argument("--note", help="狀態變更原因")
    subparsers.add_parser("open-loop-reconcile", help="回填 fingerprint 並收斂重複 Open Loops")
    backup_parser = subparsers.add_parser("backup", help="建立並驗證 SQLite online backup")
    backup_parser.add_argument("--dir", help="覆寫備份輸出目錄")
    restore_parser = subparsers.add_parser(
        "restore-drill",
        help="在隔離暫存資料庫執行非破壞性 restore drill",
    )
    restore_parser.add_argument("--backup", help="指定備份；未指定時使用最新備份")
    restore_parser.add_argument("--receipt-dir", help="覆寫 restore drill receipt 目錄")
    migration_status_parser = subparsers.add_parser(
        "migration-status",
        help="唯讀檢查 SQLite schema migration version 與相容性",
    )
    migration_status_parser.add_argument("--db", help="覆寫要檢查的 SQLite database")
    subparsers.add_parser(
        "assets-status",
        help="檢查 config/Web/Browser Extension runtime assets",
    )
    subparsers.add_parser(
        "extension-path",
        help="顯示 Chrome/Edge Load unpacked 的 Browser Extension 目錄",
    )
    maintain_parser = subparsers.add_parser("maintain", help="執行資料庫完整生命週期維護與線上備份輪替")
    maintain_parser.add_argument("--max-backups", type=int, default=7, help="保留備份數量")
    maintain_parser.add_argument("--retention-days", type=int, default=90, help="原始細碎事件保留天數")
    maintain_parser.add_argument("--dry-run", action="store_true", help="只預覽不實際刪除")

    subparsers.add_parser("heal", help="執行背景採集器自我修復與健康巡檢")
    wal_parser = subparsers.add_parser("wal-checkpoint", help="手動執行 SQLite WAL Checkpoint")
    wal_parser.add_argument("--mode", default="TRUNCATE", choices=["PASSIVE", "FULL", "RESTART", "TRUNCATE"])

    verify_parser = subparsers.add_parser(
        "verify", help="驗收中心：檢查 docs/TODO.md A 段每一項的本機收據（唯讀）"
    )
    verify_parser.add_argument("--item", help="只看指定項目，例如 A1 或 A1,A6")
    verify_parser.add_argument("--json", action="store_true", dest="as_json", help="輸出完整 JSON 收據")
    verify_parser.add_argument("--output", help="把 JSON 收據另存到這個路徑")
    verify_parser.add_argument("--confirm", help="記下「我親眼確認過」某一項（人工署名，不覆蓋機器判定）")
    verify_parser.add_argument("--unconfirm", help="取消某一項的人工確認")
    verify_parser.add_argument("--note", default="", help="人工確認的附註")

    args = parser.parse_args()

    if args.command in ["run", "web", None]:
        autostart = not getattr(args, "no_autostart", False) if args.command else True
        run_web_and_services(autostart_monitoring=autostart)
    elif args.command == "resume":
        cmd_resume(
            getattr(args, "project", None),
            getattr(args, "copy", False),
            getattr(args, "turns", 5),
            getattr(args, "json", False)
        )
    elif args.command == "init":
        cmd_init(
            getattr(args, "watch", []),
            getattr(args, "show_token", False),
            getattr(args, "rotate_token", False),
        )
    elif args.command == "now":
        cmd_now()
    elif args.command == "summary":
        cmd_summary(args.date, getattr(args, "start", None), getattr(args, "end", None), args.provider, args.force)
    elif args.command == "github":
        cmd_github(args.action)
    elif args.command == "checkpoint":
        cmd_checkpoint(args.hours)
    elif args.command == "notify":
        if getattr(args, "channel", "desktop") == "telegram":
            cmd_notify_telegram(args.type, getattr(args, "date", None), getattr(args, "dry_run", False))
        else:
            cmd_notify_desktop(args.type, getattr(args, "dry_run", False))
    elif args.command == "brief":
        cmd_brief(getattr(args, "dir", None), getattr(args, "notify", False))
    elif args.command == "clear-demo":
        cleanup_noise_and_demo_data()
    elif args.command == "status":
        cmd_status()
    elif args.command == "index":
        cmd_semantic_index(
            getattr(args, "project", None),
            getattr(args, "rebuild", False),
            getattr(args, "limit", None),
            getattr(args, "json", False),
        )
    elif args.command == "ask":
        cmd_ask(
            args.question,
            getattr(args, "project", None),
            getattr(args, "top_k", None),
            getattr(args, "no_synthesis", False),
            getattr(args, "json", False),
        )
    elif args.command == "sessions":
        cmd_sessions(
            getattr(args, "project", None),
            getattr(args, "hours", None),
            getattr(args, "gap_minutes", None),
            getattr(args, "limit", None),
            getattr(args, "json", False),
        )
    elif args.command == "recall":
        cmd_recall(
            args.question,
            getattr(args, "project", None),
            getattr(args, "threshold", None),
            getattr(args, "top_k", 8),
            getattr(args, "json", False),
        )
    elif args.command == "open-loop":
        cmd_open_loop(args.id, args.status, getattr(args, "note", None))
    elif args.command == "open-loop-reconcile":
        cmd_reconcile_open_loops()
    elif args.command == "backup":
        cmd_backup(getattr(args, "dir", None))
    elif args.command == "restore-drill":
        cmd_restore_drill(
            getattr(args, "backup", None),
            getattr(args, "receipt_dir", None),
        )
    elif args.command == "llm-test":
        from synthesizer.llm_client import diagnose_provider

        print(
            json.dumps(
                diagnose_provider(args.provider, generate_test=not args.no_generate),
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "migration-status":
        cmd_migration_status(getattr(args, "db", None))
    elif args.command == "assets-status":
        cmd_assets_status()
    elif args.command == "extension-path":
        cmd_extension_path()
    elif args.command == "maintain":
        cmd_maintain(
            getattr(args, "max_backups", 7),
            getattr(args, "retention_days", 90),
            getattr(args, "dry_run", False),
        )
    elif args.command == "heal":
        cmd_heal()
    elif args.command == "wal-checkpoint":
        cmd_wal_checkpoint(getattr(args, "mode", "TRUNCATE"))
    elif args.command == "verify":
        cmd_verify(
            getattr(args, "item", None),
            getattr(args, "as_json", False),
            getattr(args, "output", None),
            getattr(args, "confirm", None),
            getattr(args, "note", ""),
            getattr(args, "unconfirm", None),
        )
    else:
        parser.print_help()



if __name__ == "__main__":
    main()
