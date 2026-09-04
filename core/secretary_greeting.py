"""小秘書的問候卡：今天（或近兩小時）做了什麼，加一句鼓勵或貼心的話。

這張卡只做兩件事：**誠實地數** 採集器看到的活動（commit、PR、AI 對話、檔案、
專案、前景時間、收掉的未結事項），然後用**確定性的規則**挑一句話。

契約：

- 每個數字都來自本機資料庫，沒被採集到的工作不會被編出來；卡片永遠帶一句
  claim boundary 說明統計範圍（郵件目前不在採集範圍）。
- 什麼都沒看到時如實說「還沒偵測到活動」，不會硬擠讚美。
- 鼓勵語由規則挑選、以日期為種子——同一天同一視窗看到的是同一句（不會每次
  重整都換），隔天才換。
- 可選的 LLM 潤飾（``proactive_secretary.greeting.llm.enabled``，預設關閉、沿用
  llm_advisor 的 provider）只能改寫語氣，**不得新增事實**：輸出裡任何數字都必
  須出現在統計裡，否則整段丟掉、退回規則版。失敗一律退回規則版。
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import threading
from datetime import datetime, time as dtime, timedelta
from typing import Any, Callable

from core.config import get_config
from core.database import get_db
from core.time_utils import get_local_now

logger = logging.getLogger("OmniContext.SecretaryGreeting")

WINDOWS: dict[str, str] = {"today": "今天", "2h": "過去兩小時"}
DEFAULT_LLM_TIMEOUT_SECONDS = 20
LLM_MAX_CHARS = 320

GREETING_CLAIM_BOUNDARY = (
    "只統計採集器看到的活動（Git commit、GitHub PR、AI 對話、檔案異動、專案、前景時間、"
    "未結事項）；沒被採集到的工作不代表沒做，郵件與行事曆目前不在採集範圍。"
)

WRITING_TYPES = {".tex", ".bib", ".docx", ".doc", ".md", ".txt", ".rst", ".pptx", ".xlsx"}
CODE_TYPES = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c", ".cpp", ".h", ".css", ".html", ".yaml", ".yml", ".json", ".toml", ".sh"}

_LLM_CACHE_LOCK = threading.Lock()
_LLM_CACHE: dict[str, tuple[datetime, str]] = {}


class GreetingRejected(ValueError):
    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = error_code
        self.http_status = 422


def _naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=None) if value.tzinfo else value


def window_start(window: str, now: datetime) -> datetime:
    if window == "today":
        return datetime.combine(now.date(), dtime.min)
    if window == "2h":
        return now - timedelta(hours=2)
    raise GreetingRejected("invalid_window", f"window 必須是 {', '.join(WINDOWS)} 之一")


def display_name(cfg: Any | None = None) -> str:
    cfg = cfg or get_config()
    return str(cfg.get("proactive_secretary.greeting.display_name", "") or "").strip()[:40]


# ---------------------------------------------------------------- 統計


def collect_activity_stats(
    *,
    window: str = "today",
    now: datetime | None = None,
    database: Any | None = None,
    cfg: Any | None = None,
    include_usage: bool = True,
) -> dict[str, Any]:
    """在視窗內數一遍本機資料庫；每個數字都可回溯到一張表。"""
    from sqlalchemy import func

    from core.models import (
        ActivityMicroSummary,
        AIPromptEvent,
        FileActivityEvent,
        GitActivityEvent,
        GitHubPREvent,
        OpenLoop,
        ProjectState,
    )

    cfg = cfg or get_config()
    database = database or get_db()
    now = _naive(now or get_local_now())
    since = window_start(window, now)
    stats: dict[str, Any] = {
        "window": window,
        "window_label": WINDOWS[window],
        "since": since.isoformat(timespec="seconds"),
        "now": now.isoformat(timespec="seconds"),
        "sources": {},
    }
    first_seen: list[datetime] = []

    with database.session_scope() as session:
        commits = session.query(GitActivityEvent).filter(GitActivityEvent.timestamp >= since).all()
        stats["commits"] = len(commits)
        stats["commit_repos"] = sorted({c.repo_name for c in commits if c.repo_name})
        stats["insertions"] = int(sum(int(c.insertions or 0) for c in commits))
        first_seen.extend(_naive(c.timestamp) for c in commits if c.timestamp)
        stats["sources"]["commits"] = "git_activity_events"

        prs = session.query(GitHubPREvent).filter(
            (GitHubPREvent.created_at >= since) | (GitHubPREvent.merged_at >= since) | (GitHubPREvent.updated_at >= since)
        ).all()
        stats["prs_opened"] = sum(1 for p in prs if p.created_at and _naive(p.created_at) >= since)
        stats["prs_merged"] = sum(1 for p in prs if p.merged_at and _naive(p.merged_at) >= since)
        stats["prs_touched"] = len(prs)
        stats["pr_repos"] = sorted({p.repo_name for p in prs if p.repo_name})
        stats["sources"]["prs"] = "github_pr_events"

        ai_rows = session.query(AIPromptEvent.platform, AIPromptEvent.timestamp).filter(AIPromptEvent.timestamp >= since).all()
        stats["ai_turns"] = len(ai_rows)
        stats["ai_platforms"] = sorted({str(p or "").strip() for p, _ in ai_rows if p})
        first_seen.extend(_naive(ts) for _, ts in ai_rows if ts)
        stats["sources"]["ai_turns"] = "ai_prompt_events"

        files = session.query(FileActivityEvent.file_type, FileActivityEvent.timestamp).filter(
            FileActivityEvent.timestamp >= since
        ).all()
        types = [str(t or "").lower() for t, _ in files]
        stats["files_changed"] = len(files)
        stats["files_writing"] = sum(1 for t in types if t in WRITING_TYPES)
        stats["files_code"] = sum(1 for t in types if t in CODE_TYPES)
        first_seen.extend(_naive(ts) for _, ts in files if ts)
        stats["sources"]["files"] = "file_activity_events"

        projects = (
            session.query(ProjectState)
            .filter(ProjectState.last_activity_at >= since)
            .order_by(ProjectState.last_activity_at.desc())
            .all()
        )
        stats["projects_touched"] = len(projects)
        stats["project_names"] = [p.display_name or p.project_key for p in projects[:3]]
        stats["project_categories"] = sorted({str(p.category) for p in projects if p.category})
        stats["sources"]["projects"] = "project_states"

        resolved = session.query(func.count(OpenLoop.id)).filter(
            OpenLoop.status == "resolved",
            func.coalesce(OpenLoop.resolved_at, OpenLoop.updated_at) >= since,
        ).scalar()
        stats["loops_resolved"] = int(resolved or 0)
        stats["sources"]["loops_resolved"] = "open_loops"

        micro = (
            session.query(ActivityMicroSummary)
            .filter(ActivityMicroSummary.period_end >= since)
            .order_by(ActivityMicroSummary.period_start.desc())
            .limit(2)
            .all()
        )
        stats["recent_summaries"] = [str(m.summary_text)[:160] for m in micro]
        stats["sources"]["recent_summaries"] = "activity_micro_summaries"

    stats["first_activity_at"] = min(first_seen).isoformat(timespec="seconds") if first_seen else None
    stats["hours_since_first_activity"] = (
        round((now - min(first_seen)).total_seconds() / 3600, 1) if first_seen else None
    )

    stats["foreground_minutes"] = None
    stats["background_tasks"] = None
    if include_usage and window == "today":
        try:
            from core.usage_analytics import get_usage_summary

            usage = get_usage_summary(database=database, cfg=cfg, now=now)
            stats["foreground_minutes"] = float(((usage.get("goal") or {}).get("foreground_minutes")) or 0.0)
            stats["sources"]["foreground_minutes"] = "usage_analytics"
        except Exception as exc:  # noqa: BLE001 — 使用時間讀不到不該讓卡片消失
            logger.debug("usage summary unavailable for greeting: %s", type(exc).__name__)
        try:
            from core.background_tasks import get_background_task_summary

            background = get_background_task_summary(database=database, cfg=cfg, now=now)
            stats["background_tasks"] = int(background.get("completed_count") or background.get("count") or 0)
            stats["sources"]["background_tasks"] = "background_task_runs"
        except Exception as exc:  # noqa: BLE001
            logger.debug("background summary unavailable for greeting: %s", type(exc).__name__)

    stats["observed_anything"] = any(
        int(stats.get(key) or 0) > 0
        for key in ("commits", "prs_touched", "ai_turns", "files_changed", "projects_touched", "loops_resolved")
    )
    return stats


# ---------------------------------------------------------------- 規則版文案


def _time_greeting(now: datetime) -> str:
    hour = now.hour
    if 5 <= hour < 11:
        return "早安"
    if 11 <= hour < 14:
        return "午安"
    if 14 <= hour < 18:
        return "下午好"
    if 18 <= hour < 22:
        return "晚上好"
    return "夜深了"


def _join(names: list[str], limit: int = 3) -> str:
    shown = [str(n) for n in names[:limit] if n]
    text = "、".join(shown)
    if len(names) > limit:
        text += f" 等 {len(names)} 個"
    return text


def achievement_lines(stats: dict[str, Any]) -> list[str]:
    """把統計變成可讀的短句；順序＝重要程度。只描述看到的東西。"""
    lines: list[str] = []
    if stats.get("projects_touched"):
        names = _join(stats.get("project_names") or [])
        lines.append(f"推進了 {stats['projects_touched']} 個專案" + (f"（{names}）" if names else ""))
    merged, opened = int(stats.get("prs_merged") or 0), int(stats.get("prs_opened") or 0)
    if merged or opened:
        parts = []
        if opened:
            parts.append(f"開了 {opened} 個 PR")
        if merged:
            parts.append(f"合併了 {merged} 個 PR")
        lines.append("、".join(parts))
    elif stats.get("prs_touched"):
        lines.append(f"動了 {stats['prs_touched']} 個 PR")
    if stats.get("commits"):
        repos = len(stats.get("commit_repos") or [])
        lines.append(
            f"{stats['commits']} 個 commit" + (f" 落在 {repos} 個 repo" if repos > 1 else "")
            + (f"，＋{stats['insertions']} 行" if stats.get("insertions") else "")
        )
    if stats.get("files_writing"):
        lines.append(f"改了 {stats['files_writing']} 個文件檔（論文／文檔類）")
    if stats.get("files_code") and not stats.get("commits"):
        lines.append(f"改了 {stats['files_code']} 個程式檔")
    if stats.get("ai_turns"):
        platforms = _join(stats.get("ai_platforms") or [], limit=3)
        lines.append(f"和 AI 對話 {stats['ai_turns']} 輪" + (f"（{platforms}）" if platforms else ""))
    if stats.get("loops_resolved"):
        lines.append(f"收掉 {stats['loops_resolved']} 個未結事項")
    minutes = stats.get("foreground_minutes")
    if minutes and minutes >= 15:
        hours, rem = divmod(int(minutes), 60)
        lines.append("前景專注 " + (f"{hours} 小時 " if hours else "") + f"{rem} 分")
    if stats.get("background_tasks"):
        lines.append(f"背景 agent 任務完成 {stats['background_tasks']} 件")
    return lines


ENCOURAGEMENT_POOLS: dict[str, tuple[str, ...]] = {
    "nothing": (
        "還沒偵測到活動——慢慢開始也很好，先從今日行動清單挑一件小事吧。",
        "目前還沒看到任何動靜。不急，先喝口水，想清楚今天最想推進的一件事。",
    ),
    "fast_start": (
        "才開工幾個小時就有這樣的進度，節奏很好；記得中間站起來走一走。",
        "一早就把事情往前推了不少，接下來可以放慢一點，把品質顧好。",
    ),
    "strong": (
        "這是很紮實的一段時間。做得好，也別忘了留一點力氣給明天。",
        "產出很多，辛苦了。收尾時把下一步寫下來，明天接得更順。",
    ),
    "steady": (
        "穩穩地在推進，很好。有什麼卡住的，交辦框隨時在。",
        "今天走得踏實。若有想記住的事，一句「記下來：…」就好。",
    ),
    "long_hours": (
        "已經專注很久了，眼睛和肩膀都需要休息一下——收尾後早點下班。",
        "工作時間不短了，剩下的留給明天也沒關係；今天做的已經夠多。",
    ),
    "late_night": (
        "夜深了。把手邊這件事收個尾就好，其他的明天再說，早點休息。",
        "這麼晚還在忙，辛苦了。記得把進度存好，然後好好睡一覺。",
    ),
    "weekend": (
        "週末還在推進專案，辛苦了；也記得留一段時間給自己。",
        "假日的進度都是加分。做到一個段落就放下，好好休息。",
    ),
}


def _pick(pool: str, seed: str) -> str:
    options = ENCOURAGEMENT_POOLS[pool]
    rng = random.Random(hashlib.sha256(seed.encode("utf-8")).hexdigest())
    return options[rng.randrange(len(options))]


def choose_encouragement(stats: dict[str, Any], *, now: datetime, seed: str) -> tuple[str, str]:
    """依觀察到的狀況選一個池子；回傳 (文字, 池名) 讓收據能說明為什麼是這句。"""
    hour = now.hour
    minutes = float(stats.get("foreground_minutes") or 0)
    output = (
        int(stats.get("commits") or 0) + int(stats.get("prs_opened") or 0) * 2
        + int(stats.get("prs_merged") or 0) * 2 + int(stats.get("loops_resolved") or 0)
        + int(stats.get("files_writing") or 0) // 3
    )
    since_first = stats.get("hours_since_first_activity")
    if not stats.get("observed_anything"):
        pool = "nothing"
    elif hour >= 22 or hour < 5:
        pool = "late_night"
    elif minutes >= 6 * 60:
        pool = "long_hours"
    elif now.weekday() >= 5:
        pool = "weekend"
    elif since_first is not None and since_first <= 4 and output >= 3:
        pool = "fast_start"
    elif output >= 8 or int(stats.get("projects_touched") or 0) >= 4:
        pool = "strong"
    else:
        pool = "steady"
    return _pick(pool, seed), pool


def compose_greeting(
    stats: dict[str, Any],
    *,
    now: datetime,
    name: str = "",
) -> dict[str, Any]:
    """規則版：問候＋成就清單＋鼓勵語；全部可回溯到 stats。"""
    window = stats.get("window", "today")
    seed = f"{now.date().isoformat()}:{window}"
    who = f"{name}，" if name else ""
    headline = f"{who}{_time_greeting(now)}。"
    lines = achievement_lines(stats)
    since_first = stats.get("hours_since_first_activity")

    if not lines:
        lead = f"{WINDOWS[window]}還沒偵測到活動。"
    elif window == "today" and since_first is not None and since_first <= 4:
        elapsed = f"{int(since_first)} 小時" if since_first >= 1 else f"{int(since_first * 60)} 分鐘"
        lead = f"今天才開工約 {elapsed}，你已經："
    elif window == "today":
        lead = "今天到目前為止，你已經："
    else:
        lead = "過去兩小時，你："
    encouragement, pool = choose_encouragement(stats, now=now, seed=seed)
    recent = stats.get("recent_summaries") or []
    return {
        "window": window,
        "window_label": WINDOWS[window],
        "headline": headline,
        "lead": lead,
        "achievements": lines,
        "recent_summary": recent[0] if (window == "2h" and recent) else None,
        "encouragement": encouragement,
        "encouragement_pool": pool,
        "source": "rules",
        "generated_at": now.isoformat(timespec="seconds"),
        "stats": {k: v for k, v in stats.items() if k not in ("sources",)},
        "evidence": stats.get("sources", {}),
        "claim_boundary": GREETING_CLAIM_BOUNDARY,
    }


# ---------------------------------------------------------------- 可選 LLM 潤飾


def llm_settings(cfg: Any | None = None) -> dict[str, Any]:
    cfg = cfg or get_config()
    from core.secretary_advisor import advisor_settings

    advisor = advisor_settings(cfg)
    try:
        timeout = int(cfg.get("proactive_secretary.greeting.llm.timeout_seconds", DEFAULT_LLM_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        timeout = DEFAULT_LLM_TIMEOUT_SECONDS
    try:
        cache_minutes = int(cfg.get("proactive_secretary.greeting.llm.cache_minutes", 30))
    except (TypeError, ValueError):
        cache_minutes = 30
    return {
        "enabled": bool(cfg.get("proactive_secretary.greeting.llm.enabled", False)),
        "provider": advisor["provider"],
        "cloud": advisor["cloud"],
        "timeout_seconds": max(5, min(timeout, 120)),
        "cache_minutes": max(0, min(cache_minutes, 240)),
    }


_NUMBER_RE = re.compile(r"\d+")


def llm_text_is_safe(text: str, stats: dict[str, Any]) -> bool:
    """LLM 只能潤飾語氣：輸出裡任何數字都必須是統計裡出現過的數字。"""
    if not text or len(text) > LLM_MAX_CHARS or "\n\n\n" in text:
        return False
    allowed: set[str] = set()
    for value in stats.values():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            allowed.add(str(int(value)))
            if isinstance(value, float):
                hours, rem = divmod(int(value), 60)
                allowed.update({str(hours), str(rem)})
        elif isinstance(value, list):
            allowed.add(str(len(value)))
    allowed.update({"1", "2"})  # 「兩小時」「一件事」這類常見用語
    return all(number in allowed for number in _NUMBER_RE.findall(text))


def polish_with_llm(
    greeting: dict[str, Any],
    *,
    cfg: Any | None = None,
    now: datetime | None = None,
    generate: Callable[[str, str], str] | None = None,
) -> dict[str, Any]:
    """關閉或失敗都原樣回傳規則版；成功才把 source 標成 llm。"""
    cfg = cfg or get_config()
    now = _naive(now or get_local_now())
    settings = llm_settings(cfg)
    if not settings["enabled"]:
        return greeting
    facts = {
        "name_prefix": greeting["headline"],
        "lead": greeting["lead"],
        "achievements": greeting["achievements"],
        "encouragement": greeting["encouragement"],
        "window": greeting["window_label"],
    }
    digest = hashlib.sha256(json.dumps(facts, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    cache_key = f"{greeting['window']}:{digest}"
    with _LLM_CACHE_LOCK:
        cached = _LLM_CACHE.get(cache_key)
    if cached and now - cached[0] <= timedelta(minutes=settings["cache_minutes"]):
        return {**greeting, "text": cached[1], "source": "llm", "llm_provider": settings["provider"], "llm_cached": True}

    system_prompt = (
        "你是一位溫暖、簡潔的工作秘書。把下面的事實改寫成 2 到 3 句自然的繁體中文問候，"
        "語氣親切但不誇張。規則：只能使用給你的事實與數字，不得新增任何未列出的成就、數字或建議；"
        "不要條列；不要加標題；不超過 120 字。"
    )
    user_prompt = json.dumps(facts, ensure_ascii=False, indent=1)
    try:
        if generate is None:
            from core.secretary_advisor import _default_generate

            generate = _default_generate(settings["provider"], settings["timeout_seconds"])
        text = str(generate(system_prompt, user_prompt) or "").strip()
    except Exception as exc:  # noqa: BLE001 — 潤飾失敗就用規則版，不影響卡片
        logger.warning("greeting LLM polish failed: %s", type(exc).__name__)
        return {**greeting, "llm_error": type(exc).__name__}
    if not llm_text_is_safe(text, greeting["stats"]):
        logger.info("greeting LLM output rejected by fact guard; using rules text.")
        return {**greeting, "llm_rejected": "fact_guard"}
    with _LLM_CACHE_LOCK:
        _LLM_CACHE[cache_key] = (now, text)
    return {**greeting, "text": text, "source": "llm", "llm_provider": settings["provider"], "llm_cached": False}


def _reset_llm_cache_for_tests() -> None:
    with _LLM_CACHE_LOCK:
        _LLM_CACHE.clear()


# ---------------------------------------------------------------- 入口


def build_greeting(
    *,
    window: str = "today",
    now: datetime | None = None,
    database: Any | None = None,
    cfg: Any | None = None,
    name: str | None = None,
    use_llm: bool = True,
) -> dict[str, Any]:
    """卡片、Telegram /today 與晨報共用的單一入口。"""
    cfg = cfg or get_config()
    now = _naive(now or get_local_now())
    if window not in WINDOWS:
        raise GreetingRejected("invalid_window", f"window 必須是 {', '.join(WINDOWS)} 之一")
    stats = collect_activity_stats(window=window, now=now, database=database, cfg=cfg)
    greeting = compose_greeting(stats, now=now, name=display_name(cfg) if name is None else name)
    greeting["text"] = plain_text(greeting)
    if use_llm:
        greeting = polish_with_llm(greeting, cfg=cfg, now=now)
    return greeting


def plain_text(greeting: dict[str, Any]) -> str:
    """規則版的一段話（Telegram、晨報、LLM 關閉時的卡片正文都用它）。"""
    parts = [greeting["headline"], greeting["lead"]]
    if greeting["achievements"]:
        parts.append("；".join(greeting["achievements"]) + "。")
    if greeting.get("recent_summary"):
        parts.append(f"剛剛在做：{greeting['recent_summary']}")
    parts.append(greeting["encouragement"])
    return " ".join(p for p in parts if p)
