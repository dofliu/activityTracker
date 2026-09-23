"""`omni demo`：匿名化、可拋棄的示範資料集，只寫進獨立家目錄（ADR-031，TODO E1）。

這裡定義**兩件事**：

1. ``seed_demo_home()``——唯一的寫入入口。先做安全檢查（ADR-031 D1：目標路徑
   不得是使用者實機的家目錄），再把完全虛構的 git／AI／檔案事件灌進一個全新的
   SQLite（沿用既有 migration registry，不繞過），接著在另開的子行程裡依序重算
   project_states、跑一次 `daily_digest`、跑一次 `handoff_active_projects`
   （見 :func:`_run_post_seed_steps_in_subprocess`），最後寫下
   :func:`core.runtime_paths.demo_marker_path` 旗標檔——這是 ``demo_mode`` 判定
   唯一認的依據，不是靠猜資料長什麼樣子。
2. ``DEMO_PROJECTS``——固定內容的假資料集，橫跨兩週：一個「近一週持續在動」的專案
   （``aurora-notes``）與一個「前一週活躍、近一週歸零」的專案（``lighthouse-api``），
   讓 ADR-017 模式感知提案、ADR-020 每週回顧、ADR-019 秘書桌面都有東西可挑。

**不做的事**（刻意不做，見 docs/TODO.md E1）：
- 不呼叫 `morning_pack`（它還會跑 repo 同步報告與 STATUS 過期草稿，那兩項對一個
  沒有真實 repo 的示範家目錄沒有意義）——只呼叫其中對示範資料有意義的兩個 L0：
  `daily_digest`（讓「記得」面板有東西）與 `handoff_active_projects`（讓
  `reports/handoffs/` 有檔）。
- 不寫 `config.yaml`——留空讓所有危險能力沿用預設關閉，不必在這裡重複一份設定判斷。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.migrations import upgrade_sqlite_database
from core.models import AIPromptEvent, FileActivityEvent, GitActivityEvent
from core.runtime_paths import (
    DEMO_MARKER_FILENAME,
    demo_marker_path,
    is_demo_home,
    source_checkout_root,
)
from core.time_utils import get_local_now

DATASET_VERSION = 1
DEFAULT_DEMO_DIRNAME = ".omnicontext-demo"
DEMO_DB_FILENAME = "omni_context.db"


class DemoSafetyError(RuntimeError):
    """`omni demo` 拒絕在這個路徑上動手（fail-closed，見 ADR-031）。"""


# ---------------------------------------------------------------- 路徑安全

def default_demo_home() -> Path:
    return (Path.home() / DEFAULT_DEMO_DIRNAME).resolve()


def _protected_homes() -> set[Path]:
    """絕不可被 `omni demo` 清空或覆寫的路徑——使用者真實資料可能就在這裡。"""
    protected = {(Path.home() / "OmniContext").resolve()}
    checkout = source_checkout_root()
    if checkout is not None:
        protected.add(checkout.resolve())
    ambient = os.environ.get("OMNICONTEXT_HOME", "").strip()
    if ambient:
        protected.add(Path(os.path.expandvars(os.path.expanduser(ambient))).resolve())
    return protected


def resolve_demo_target(home: str | Path | None) -> Path:
    """算出這次要寫入的示範家目錄；撞到受保護路徑就直接拒絕，不繼續算下去。"""
    if home:
        target = Path(os.path.expandvars(os.path.expanduser(str(home)))).resolve()
    else:
        target = default_demo_home()
    protected = _protected_homes()
    if target in protected:
        raise DemoSafetyError(
            f"{target} 是你的實機／目前作用中的家目錄，omni demo 拒絕清空或寫入。"
            "請用 --home 指定另一個路徑（例如 ~/.omnicontext-demo-2）。"
        )
    return target


def _read_marker(target: Path) -> dict[str, Any] | None:
    marker = demo_marker_path(target)
    if not marker.is_file():
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------- 假資料集

@dataclass(frozen=True)
class DemoCommit:
    days_ago: int
    hour: int
    message: str
    files_changed: int
    insertions: int
    deletions: int


@dataclass(frozen=True)
class DemoFileEvent:
    days_ago: int
    hour: int
    minute: int
    file_name: str
    file_type: str
    action: str
    diff_summary: str


@dataclass(frozen=True)
class DemoAITurn:
    days_ago: int
    hour: int
    minute: int
    platform: str
    prompt_text: str
    response_text: str


@dataclass(frozen=True)
class DemoProject:
    key: str
    repo_path_suffix: str
    commits: tuple[DemoCommit, ...]
    file_events: tuple[DemoFileEvent, ...]
    ai_turns: tuple[DemoAITurn, ...]


# aurora-notes：橫跨兩週持續在動，包含「今天」——秘書桌面與問候卡的近況都能挑到它。
_AURORA = DemoProject(
    key="aurora-notes",
    repo_path_suffix="demo-workspace/aurora-notes",
    commits=(
        DemoCommit(13, 10, "設計本機優先的筆記同步協定", 6, 210, 12),
        DemoCommit(10, 15, "加入標籤搜尋索引", 4, 88, 5),
        DemoCommit(7, 11, "修正同步衝突時的合併規則", 3, 42, 30),
        DemoCommit(5, 16, "筆記編輯器加入 Markdown 即時預覽", 5, 130, 8),
        DemoCommit(3, 9, "調整離線佇列的重試間隔", 2, 20, 6),
        DemoCommit(1, 14, "補上標籤搜尋的邊界情境測試", 3, 66, 2),
        DemoCommit(0, 10, "修正深色主題下標籤顏色對比度", 1, 14, 3),
    ),
    file_events=(
        DemoFileEvent(13, 10, 20, "sync_protocol.md", ".md", "created", "新增協定草案，約 3 段"),
        DemoFileEvent(7, 11, 30, "merge_rules.py", ".py", "modified", "+42/-30"),
        DemoFileEvent(5, 16, 20, "editor_preview.py", ".py", "modified", "+130/-8"),
        DemoFileEvent(0, 10, 5, "theme_dark.css", ".css", "modified", "+14/-3"),
    ),
    ai_turns=(
        DemoAITurn(
            13, 9, 40, "claude_code",
            "aurora-notes 要做本機優先的同步，兩台裝置離線各自編輯同一則筆記時要怎麼合併？",
            "建議用向量時鐘標記每則筆記的編輯序，衝突時保留兩個版本讓使用者選，"
            "不要自動覆蓋任何一邊——同步協定草案我先寫進 sync_protocol.md。",
        ),
        DemoAITurn(
            5, 15, 50, "chatgpt",
            "編輯器要加 Markdown 即時預覽，但打字時不能卡頓，有什麼簡單做法？",
            "用 debounce（例如 150ms）延遲重新渲染預覽區，只有停止輸入才重繪，"
            "大多數筆記長度下不會感覺到延遲。",
        ),
        DemoAITurn(
            1, 13, 30, "claude_code",
            "標籤搜尋在標籤名稱有前後空白或大小寫不同時會找不到，要怎麼補測試？",
            "正規化時先 strip 再轉小寫比對，並補三個邊界案例：全空白標籤、"
            "純大寫標籤、標籤中間有全形空格。",
        ),
        DemoAITurn(
            0, 9, 50, "codex",
            "深色主題下標籤顏色跟背景太接近，看不清楚，這是對比度問題嗎？",
            "是，深色背景下建議把標籤前景色的對比度拉到至少 4.5:1；"
            "把目前的 #444 改成 #9aa0a6 應該就夠用了。",
        ),
    ),
)

# lighthouse-api：前一週活躍、近一週完全沒動——用來示範「被冷落的專案」訊號。
_LIGHTHOUSE = DemoProject(
    key="lighthouse-api",
    repo_path_suffix="demo-workspace/lighthouse-api",
    commits=(
        DemoCommit(13, 14, "加入服務健康檢查的重試上限設定", 3, 54, 4),
        DemoCommit(11, 10, "修正逾時判定使用了錯誤的時區", 2, 18, 9),
        DemoCommit(9, 17, "補上健康檢查歷史的分頁查詢", 4, 76, 11),
    ),
    file_events=(
        DemoFileEvent(13, 14, 10, "healthcheck_retry.py", ".py", "modified", "+54/-4"),
        DemoFileEvent(9, 17, 5, "history_pagination.py", ".py", "created", "新增分頁邏輯"),
    ),
    ai_turns=(
        DemoAITurn(
            11, 9, 40, "claude_code",
            "健康檢查的逾時判定好像跨時區會算錯，是哪裡出的問題？",
            "看起來是拿伺服器的 naive datetime 直接跟 UTC 時間比較——"
            "建議統一轉成 UTC 再比較，或者全程只用 UTC 不落地成本地時間。",
        ),
    ),
)

DEMO_PROJECTS: tuple[DemoProject, ...] = (_AURORA, _LIGHTHOUSE)


def _project_home(target: Path, project: DemoProject) -> Path:
    return target / project.repo_path_suffix


def _commit_hash(project_key: str, index: int) -> str:
    return hashlib.sha1(f"omni-demo:{project_key}:{index}".encode("utf-8")).hexdigest()


def _event_time(today: date, now: datetime, days_ago: int, hour: int, minute: int = 0) -> datetime:
    """算事件時間；`omni demo` 可能在一天中任何時刻執行，「今天」的固定時刻偶爾會落在
    現在之後（例如寫死 10:00 但這一刻才早上八點）——那樣就不是「已經發生的活動」了，
    一律夾回 now 之前，其餘天數本來就整天過去了不受影響。"""
    when = datetime.combine(today - timedelta(days=days_ago), dtime(hour, minute))
    return min(when, now - timedelta(minutes=1)) if when >= now else when


def _insert_dataset(session: Any, target: Path, today: date, now: datetime) -> dict[str, int]:
    counts = {"git": 0, "ai": 0, "file": 0}
    for project in DEMO_PROJECTS:
        repo_path = _project_home(target, project)
        # `core.project_engine.resolve_project_from_path` 認 `.git` 目錄存在與否來判定
        # 檔案事件歸戶到哪個專案（優先於 file_activity_events.project_name 欄位）；
        # 沒有這個 stub，檔案事件會被歸戶到示範家目錄本身的資料夾名稱，而不是專案名。
        (repo_path / ".git").mkdir(parents=True, exist_ok=True)
        for index, commit in enumerate(project.commits):
            when = _event_time(today, now, commit.days_ago, commit.hour)
            session.add(
                GitActivityEvent(
                    timestamp=when,
                    repo_name=project.key,
                    repo_path=str(repo_path),
                    commit_hash=_commit_hash(project.key, index),
                    branch="main",
                    author="Demo Author",
                    message=commit.message,
                    files_changed_count=commit.files_changed,
                    insertions=commit.insertions,
                    deletions=commit.deletions,
                )
            )
            counts["git"] += 1
        for file_event in project.file_events:
            when = _event_time(today, now, file_event.days_ago, file_event.hour, file_event.minute)
            file_path = repo_path / file_event.file_name
            session.add(
                FileActivityEvent(
                    timestamp=when,
                    file_path=str(file_path),
                    file_name=file_event.file_name,
                    file_type=file_event.file_type,
                    action=file_event.action,
                    size_bytes=1200,
                    diff_summary=file_event.diff_summary,
                    project_name=project.key,
                )
            )
            counts["file"] += 1
        for turn_index, turn in enumerate(project.ai_turns):
            when = _event_time(today, now, turn.days_ago, turn.hour, turn.minute)
            session.add(
                AIPromptEvent(
                    timestamp=when,
                    platform=turn.platform,
                    conversation_id=f"omni-demo-{project.key}",
                    prompt_text=turn.prompt_text,
                    response_text=turn.response_text,
                    project_tag=project.key,
                    cwd=str(repo_path),
                    turn_key=f"omni-demo:{project.key}:{turn_index}",
                    response_status="final_candidate",
                )
            )
            counts["ai"] += 1
    return counts


_POST_SEED_SCRIPT = """
import json

from core.project_engine import refresh_project_states
from core.activity_digest import build_daily_digest
from core.secretary.packs import build_active_handoffs

refresh_project_states(force=True)
digest = build_daily_digest(days_back=1)
handoffs = build_active_handoffs(hours=24 * 7, max_projects=10)
print(json.dumps({
    "digest_date": digest.get("date"),
    "digest_notes_written": digest.get("notes_written", 0),
    "handoffs_written": handoffs.get("handoffs_written", 0),
    "handoffs_projects": handoffs.get("projects", []),
}))
"""


def _run_post_seed_steps_in_subprocess(target: Path) -> dict[str, Any]:
    """在全新的子行程裡重算 project_states，並各跑一次 `daily_digest`／`handoff_active_projects`。

    這三步都走行程內單例 `get_db()`／`get_config()`，不接受注入——與其在這個行程裡動
    那兩個單例的私有狀態，不如比照 `core.agent_dispatch.run_agent_subprocess` 的作法
    另開一個全新行程，用 `OMNICONTEXT_HOME` 指到示範家目錄，行為與真正啟動服務時完全一致。
    `daily_digest`／`handoff_active_projects` 都依賴 `refresh_project_states` 先跑過，
    所以三步同一個子行程依序做，不拆成三次子行程呼叫。
    """
    env = dict(os.environ)
    env["OMNICONTEXT_HOME"] = str(target)
    result = subprocess.run(
        [sys.executable, "-c", _POST_SEED_SCRIPT],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise DemoSafetyError(
            "示範資料寫入後，重算 project_states／記憶區觀察失敗，示範家目錄可能不完整：\n"
            f"{result.stderr.strip()[-2000:]}"
        )
    last_line = next((line for line in reversed(result.stdout.splitlines()) if line.strip()), "")
    try:
        payload = json.loads(last_line)
    except (ValueError, TypeError) as exc:
        raise DemoSafetyError(
            "示範資料寫入後，記憶區觀察步驟沒有回報可解析的結果：\n"
            f"{result.stdout.strip()[-2000:]}"
        ) from exc
    return payload if isinstance(payload, dict) else {}


def seed_demo_home(
    *,
    home: str | Path | None = None,
    now: datetime | None = None,
    refresh_project_states: bool = True,
) -> dict[str, Any]:
    """把 :data:`DEMO_PROJECTS` 灌進一個獨立的示範家目錄；回傳摘要供 CLI 印出。

    ``refresh_project_states=False`` 只給測試用——契約測試不需要真的另開子行程，
    只要驗證事件表與旗標檔正確即可；為 ``True`` 時同一個子行程也會依序跑
    `daily_digest`／`handoff_active_projects`，回傳摘要的 ``memory`` 欄位帶著寫入
    的記憶區觀察數與 Handoff 檔案數（``False`` 時 ``memory`` 是 ``None``，代表這兩步
    沒有跑，不是跑了但沒東西）。
    """
    target = resolve_demo_target(home)
    now = now or get_local_now()
    today = now.date()

    if target.exists():
        marker = _read_marker(target)
        if marker is None:
            raise DemoSafetyError(
                f"{target} 已存在，但找不到 {DEMO_MARKER_FILENAME}（不是 omni demo 建立的示範家目錄），"
                "拒絕清空或覆寫。請指定一個新路徑。"
            )
        print(f"即將清空 {target} 並重新灌入示範資料...")
        shutil.rmtree(target)

    target.mkdir(parents=True, exist_ok=True)
    db_path = target / DEMO_DB_FILENAME
    upgrade_sqlite_database(db_path, backup_before=False)

    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    try:
        factory = sessionmaker(bind=engine)
        session = factory()
        try:
            counts = _insert_dataset(session, target, today, now)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    finally:
        engine.dispose()

    memory: dict[str, Any] | None = None
    if refresh_project_states:
        memory = _run_post_seed_steps_in_subprocess(target)

    marker_payload = {
        "is_demo": True,
        "dataset_version": DATASET_VERSION,
        "created_at": get_local_now().isoformat(timespec="seconds"),
        "projects": [project.key for project in DEMO_PROJECTS],
    }
    demo_marker_path(target).write_text(
        json.dumps(marker_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    assert is_demo_home(target)
    return {
        "home": str(target),
        "database": str(db_path),
        "counts": counts,
        "marker": marker_payload,
        "projects": [project.key for project in DEMO_PROJECTS],
        "memory": memory,
    }
