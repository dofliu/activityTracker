"""會議秘書第一層（ADR-022）：把會後逐字稿變成可回溯的紀錄。

**不做**：錄音、讀會議軟體的視窗內容、呼叫 Teams／Graph API、自動下載逐字稿。
秘書只等你把 Teams 匯出的檔案放進 ``meetings.transcript_dir``（沒設路徑就是停用，
與 calendar_watcher 同一種預設）。

一場會議的紀錄＝**一個 RAG 文件（若你把資料夾加進知識庫）＋一則記憶區觀察**；
不新增任何資料表。摘要走與時段微摘要同一條 LLM 路徑（預設本機 ollama），
「候選待辦」要你點了才成為未結事項——秘書可以說「你好像答應了 X」，
但只有你能讓它變成承諾。
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

from core.config import get_config
from core.runtime_paths import resolve_runtime_path
from core.time_utils import get_local_now

SUPPORTED_SUFFIXES = (".vtt", ".txt", ".md", ".docx")
SOURCE_PREFIX = "meeting:"
# 逐字稿與行事曆只用時間配對：檔案修改時間落在事件結束後這麼久之內，取最近的一場。
PAIR_WINDOW_HOURS = 6
# 摘要送進 LLM 的逐字稿上限；超過就從頭截斷並在事實區塊註明。
MAX_TRANSCRIPT_CHARS = 12000
MAX_FOLLOWUPS = 6
FOLLOWUP_HEADING = "待辦候選"
FOLLOWUP_DONE = "已加入"
FOLLOWUP_IGNORED = "已忽略"
CLAIM_BOUNDARY = (
    "逐字稿是你自己匯出的檔案；摘要由 LLM 產生，候選待辦要你點了才成為未結事項。"
    "配對只用時間，不解讀會議內容是否重要。"
)
# 只看應用程式名稱，不讀視窗標題內容（ADR-022 D5）。
MEETING_APPS = ("teams", "ms-teams", "zoom", "webex", "meet", "lync", "skype")
MEETING_APP_IDLE_MINUTES = 10


def _naive(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


def transcript_dir(cfg: Any | None = None) -> Path | None:
    """設定裡的逐字稿資料夾；沒設定就是 None（＝停用，不是錯誤）。"""
    cfg = cfg or get_config()
    raw = str(cfg.get("meetings.transcript_dir", "") or "").strip()
    if not raw:
        return None
    return resolve_runtime_path(raw)


def meetings_enabled(cfg: Any | None = None) -> bool:
    return transcript_dir(cfg) is not None


def summary_provider(cfg: Any | None = None) -> str:
    """摘要用的 provider；**預設 ollama（全本機）**。

    逐字稿含其他與會者的發言，所以雲端 provider 一定要是明示選擇，
    而且介面與設定檔都要寫出「這會把與會者的話送到那裡」。
    """
    cfg = cfg or get_config()
    return str(cfg.get("meetings.provider", "ollama") or "ollama").strip().lower()


def provider_is_cloud(provider: str) -> bool:
    return provider.strip().lower() not in ("ollama", "local", "none", "")


# ---- WebVTT 解析 ---------------------------------------------------------
#
# Teams 匯出的 .vtt 長這樣（cue 編號可有可無、講者可能是 <v NAME> 或 NAME:）：
#
#   WEBVTT
#
#   1
#   00:00:01.000 --> 00:00:04.000
#   <v Dof Liu>那我們下週三對一次進度</v>
#
# 即時字幕存檔還常出現「同一句逐漸長出來」的重複 cue，所以要把「前一句是這一句的
# 前綴」視為同一句的成長並取代，否則摘要會讀到十遍半句話。

_TIMESTAMP = re.compile(r"-->")
_CUE_INDEX = re.compile(r"^\d+$")
_VOICE_TAG = re.compile(r"^<v\s+([^>]{1,80})>(.*)$", re.IGNORECASE)
_TAG = re.compile(r"</?[a-zA-Z][^>]*>")
_SPEAKER_PREFIX = re.compile(r"^([^:：]{1,40})[:：]\s*(.+)$")
_BLOCK_KEYWORDS = ("NOTE", "STYLE", "REGION", "WEBVTT")


def _clean_line(line: str) -> tuple[str | None, str]:
    """回傳 (講者, 內容)；沒有講者標記就是 (None, 內容)。"""
    text = line.strip()
    match = _VOICE_TAG.match(text)
    if match:
        speaker = match.group(1).strip()
        return (speaker or None), _TAG.sub("", match.group(2)).strip()
    text = _TAG.sub("", text).strip()
    prefix = _SPEAKER_PREFIX.match(text)
    if prefix and " " not in prefix.group(1).strip()[:1]:
        candidate = prefix.group(1).strip()
        # 「00:01」「http」這種不是講者；名字不含句號且不太長才採用
        if candidate and not candidate.endswith(("http", "https")) and "." not in candidate:
            return candidate, prefix.group(2).strip()
    return None, text


def parse_webvtt(content: str) -> list[dict[str, str | None]]:
    """把 WebVTT 變成 [{speaker, text}]：去掉編號與時間戳，合併同一講者的連續句。"""
    turns: list[dict[str, str | None]] = []
    skip_block = False
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            skip_block = False
            continue
        if any(line.upper().startswith(word) for word in _BLOCK_KEYWORDS):
            skip_block = True
            continue
        if skip_block or _CUE_INDEX.match(line) or _TIMESTAMP.search(line):
            continue
        speaker, text = _clean_line(line)
        if not text:
            continue
        if turns:
            last = turns[-1]
            same_speaker = (last["speaker"] or None) == (speaker or None)
            previous = str(last["text"] or "")
            if same_speaker and (text == previous or text.startswith(previous)):
                last["text"] = text          # 同一句在字幕裡長出來：取代，不重複
                continue
            if same_speaker:
                last["text"] = f"{previous} {text}".strip()
                continue
        turns.append({"speaker": speaker or None, "text": text})
    return turns


def _read_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        # 與知識庫同一個解析器；docx 相依只在需要時才 import（主服務不預載）。
        from rag.parsers.parser_hub import ParserHub

        return str(ParserHub().parse_file(str(path)).content or "")
    for encoding in ("utf-8-sig", "utf-8", "cp950", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_bytes().decode("utf-8", errors="replace")


def transcript_turns(path: Path) -> list[dict[str, str | None]]:
    """讀一份逐字稿；.vtt 走 WebVTT 解析，其餘按行當成一句一句的內容。"""
    content = _read_text(path)
    if path.suffix.lower() == ".vtt":
        return parse_webvtt(content)
    turns: list[dict[str, str | None]] = []
    for raw in content.splitlines():
        speaker, text = _clean_line(raw)
        if text:
            turns.append({"speaker": speaker or None, "text": text})
    return turns


def transcript_stats(turns: Iterable[dict[str, str | None]]) -> dict[str, Any]:
    rows = list(turns)
    speakers = sorted({str(item["speaker"]) for item in rows if item.get("speaker")})
    body = "\n".join(str(item["text"] or "") for item in rows)
    return {
        "turns": len(rows),
        "speakers": len(speakers),
        "speaker_names": speakers,
        "chars": len(body),
        "text": body,
    }


def file_digest(path: Path) -> str:
    digest = hashlib.sha1()  # noqa: S324 — 只用來當同一檔案的去重鍵，不是安全用途
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()[:16]


def list_transcripts(
    *, cfg: Any | None = None, now: datetime | None = None, limit: int = 20
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """資料夾裡的逐字稿（最近修改的在前）與一份如實的來源說明。"""
    cfg = cfg or get_config()
    now = _naive(now or get_local_now())
    directory = transcript_dir(cfg)
    if directory is None:
        return [], {"used": False, "reason": "no_transcript_dir"}
    if not directory.is_dir():
        return [], {"used": False, "reason": "transcript_dir_missing", "path": str(directory)}

    found: list[dict[str, Any]] = []
    unreadable: list[dict[str, str]] = []
    for entry in sorted(directory.iterdir()):
        if not entry.is_file() or entry.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        try:
            stat = entry.stat()
            found.append({
                "path": str(entry),
                "name": entry.name,
                "sha1": file_digest(entry),
                "modified_at": datetime.fromtimestamp(stat.st_mtime).replace(microsecond=0),
                "bytes": stat.st_size,
            })
        except OSError as exc:
            unreadable.append({"name": entry.name, "error": type(exc).__name__})

    found.sort(key=lambda item: item["modified_at"], reverse=True)
    meta = {
        "used": True,
        "path": str(directory),
        "files": len(found),
        "degraded_sources": unreadable,
        "claim_boundary": CLAIM_BOUNDARY,
    }
    return found[:limit], meta


def pair_with_calendar(
    modified_at: datetime,
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    window_hours: int = PAIR_WINDOW_HOURS,
) -> dict[str, Any] | None:
    """只用時間配對：檔案修改時間落在某場會議結束後 window_hours 內，取最近的一場。

    配不到就回 None——呼叫端要如實寫「未配對到行事曆事件」，不猜主題。
    """
    modified_at = _naive(modified_at)
    try:
        from core.calendar_agenda import events_between
    except Exception:  # noqa: BLE001 — 行事曆讀不到不影響逐字稿本身
        return None
    since = modified_at - timedelta(hours=window_hours + 12)
    rows = events_between(since, modified_at, database=database)
    best: dict[str, Any] | None = None
    for row in rows:
        start, end = _naive(row.instance_start), _naive(row.instance_end)
        if row.all_day or end is None or end > modified_at:
            continue
        if modified_at - end > timedelta(hours=window_hours):
            continue
        candidate = {
            "uid": row.uid,
            "summary": row.summary or "",
            "start": start.isoformat(timespec="minutes") if start else None,
            "end": end.isoformat(timespec="minutes"),
            "minutes_after_end": int((modified_at - end).total_seconds() // 60),
        }
        if best is None or end > datetime.fromisoformat(best["end"]):
            best = candidate
    return best


# ---- 摘要與候選待辦 -------------------------------------------------------

_SUMMARY_SYSTEM = (
    "你是使用者的會議記錄助理。只根據提供的逐字稿寫，"
    "不要編造沒有出現的數字、人名、日期或結論。"
    "若逐字稿不完整或看不出結論，就明說看不出來。"
)
_SUMMARY_USER = """以下是一場會議的逐字稿（可能經過截斷）。請輸出兩段，用繁體中文：

摘要：
- 三到五個短句，講這場會談了什麼、決定了什麼。沒有明確決定就寫「沒有明確結論」。

待辦候選：
- 每行一條「誰要做什麼」，只寫逐字稿裡真的有人答應或被交辦的事，最多 {max_followups} 條。
- 一條都找不到就寫「（無）」。

逐字稿事實：{facts}

逐字稿：
{transcript}
"""

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
# LLMClient 在供應商連不上時是**回傳錯誤字串**而不是丟例外（見 core/acceptance.py
# 的 _LLM_ERROR_MARKERS）。不先攔下來，錯誤訊息會被當成摘要送進事實閘，然後對使用者
# 說「摘要編造了數字 11434」——真正的原因是 ollama 沒開。訊息指錯原因也是 bug。
_LLM_ERROR_MARKERS = (
    "[LLMGateway", "[OpenAI API 錯誤]", "[Claude API 錯誤]", "[Gemini API 錯誤]",
    "[Ollama", "【尚未偵測到", "Traceback (most recent call last)",
    "[本機備援模式]",   # llm_gateway 連不上時回的降級抬頭（2026-09-08 容器實測看到的就是這個）
)


def looks_like_llm_error(reply: str) -> str | None:
    """回傳「這是供應商錯誤」的原因（截短），不是錯誤就回 None。"""
    text = str(reply or "").strip()
    if not text:
        return "供應商沒有回應任何內容"
    for marker in _LLM_ERROR_MARKERS:
        if marker in text[:400]:
            return text[:160].replace("\n", " ")
    lowered = text.lower()
    for marker in ("connection refused", "max retries exceeded", "failed to establish"):
        if marker in lowered:
            return text[:160].replace("\n", " ")
    return None


def _numbers(text: str) -> set[str]:
    return {match.group(0).replace(",", "") for match in _NUMBER.finditer(text or "")}


def fact_gate(summary: str, transcript: str, allowed: Iterable[str] = ()) -> dict[str, Any]:
    """摘要裡出現逐字稿沒有的數字就整段丟掉。

    只擋數字：這是機器查得準的部分。人名與結論的正確性仍要人眼——所以觀察卡上
    寫的是「候選」，而不是「你的待辦」。
    """
    extra = _numbers(summary) - _numbers(transcript) - {str(item) for item in allowed}
    return {"ok": not extra, "unsupported_numbers": sorted(extra)}


def parse_followups(body: str) -> list[dict[str, Any]]:
    """從觀察正文讀回候選待辦；已加入／已忽略的保留狀態，讓卡片只顯示還沒處理的。"""
    items: list[dict[str, Any]] = []
    in_section = False
    for raw in (body or "").splitlines():
        line = raw.strip()
        if line.startswith(FOLLOWUP_HEADING):
            in_section = True
            continue
        if not in_section:
            continue
        if not line.startswith("- "):
            if line:
                break
            continue
        text = line[2:].strip()
        status = "pending"
        for marker, name in ((FOLLOWUP_DONE, "accepted"), (FOLLOWUP_IGNORED, "ignored")):
            token = f"[{marker}]"
            if text.startswith(token):
                status, text = name, text[len(token):].strip()
                break
        if text and text not in ("（無）", "(無)", "無"):
            items.append({"index": len(items), "text": text, "status": status})
    return items


def mark_followup(body: str, index: int, status: str) -> str:
    """把某一條候選待辦標成已加入／已忽略；回傳改寫後的正文。"""
    if status not in ("accepted", "ignored"):
        raise ValueError("status 必須是 accepted 或 ignored")
    marker = FOLLOWUP_DONE if status == "accepted" else FOLLOWUP_IGNORED
    seen = -1
    lines = (body or "").splitlines()
    in_section = False
    for position, raw in enumerate(lines):
        line = raw.strip()
        if line.startswith(FOLLOWUP_HEADING):
            in_section = True
            continue
        if not in_section or not line.startswith("- "):
            continue
        text = line[2:].strip()
        for existing in (FOLLOWUP_DONE, FOLLOWUP_IGNORED):
            token = f"[{existing}]"
            if text.startswith(token):
                text = text[len(token):].strip()
        seen += 1
        if seen == index:
            lines[position] = f"- [{marker}] {text}"
            return "\n".join(lines)
    raise ValueError(f"找不到第 {index} 條候選待辦")


def _compose_body(
    *,
    summary_lines: list[str],
    followups: list[str],
    stats: dict[str, Any],
    event: dict[str, Any] | None,
    file_name: str,
    truncated: bool,
    provider: str,
    summary_error: str | None,
) -> str:
    parts: list[str] = []
    if event:
        parts.append(f"會議：{event['summary'] or '（無標題）'}（{event['start']} ~ {event['end'][-5:]}）")
    else:
        parts.append(f"會議：未配對到行事曆事件（逐字稿檔名 {file_name}）")
    parts.append(
        f"逐字稿：{stats['turns']} 段發言、{stats['speakers']} 位講者、{stats['chars']} 字"
        + ("（送進摘要前已截斷）" if truncated else "")
    )
    if summary_error:
        parts.append(f"摘要：失敗（{summary_error}）——逐字稿本身仍在，可自己讀或重跑。")
    else:
        parts.append("摘要：")
        parts.extend(f"- {line}" for line in summary_lines)
    parts.append(f"{FOLLOWUP_HEADING}（要你點了才會成為未結事項）：")
    if followups:
        parts.extend(f"- {line}" for line in followups)
    else:
        parts.append("- （無）")
    parts.append(f"摘要 provider：{provider}" + ("（雲端：與會者的發言曾送往該供應商）" if provider_is_cloud(provider) else "（本機）"))
    return "\n".join(parts)


def _split_summary(raw: str) -> tuple[list[str], list[str]]:
    """把 LLM 回應拆成摘要句與候選待辦；認不出段落就整段當摘要。"""
    summary: list[str] = []
    followups: list[str] = []
    bucket = summary
    for line in (raw or "").splitlines():
        text = line.strip()
        if not text:
            continue
        if text.startswith(FOLLOWUP_HEADING) or text.startswith("待辦"):
            bucket = followups
            continue
        if text.startswith("摘要"):
            bucket = summary
            continue
        bucket.append(text.lstrip("-•* ").strip())
    return summary[:5], [item for item in followups if item][:MAX_FOLLOWUPS]


def build_meeting_notes(
    *,
    database: Any | None = None,
    cfg: Any | None = None,
    now: datetime | None = None,
    limit: int = 5,
    llm_generate: Callable[[str, str], str] | None = None,
) -> dict[str, Any]:
    """L0 排程動作：把資料夾裡還沒整理過的逐字稿各寫成一則觀察。

    唯讀地讀檔案與行事曆；只寫記憶區觀察（可刪、有 TTL）。同一個檔案（sha1）
    只會有一則觀察，重跑不會產生第二則。
    """
    cfg = cfg or get_config()
    now = _naive(now or get_local_now())
    files, meta = list_transcripts(cfg=cfg, now=now, limit=max(limit, 20))
    provider = summary_provider(cfg)
    result: dict[str, Any] = {
        "operation": "meeting_notes",
        "transcript_dir": meta.get("path"),
        "files_seen": len(files),
        "provider": provider,
        "provider_is_cloud": provider_is_cloud(provider),
        "written": [],
        "skipped": [],
        "errors": [],
        "sources": meta,
        "claim_boundary": CLAIM_BOUNDARY,
    }
    if not meta.get("used"):
        result["skipped"].append({"reason": meta.get("reason")})
        return result

    from core.secretary_memory import memory_enabled, record_observation

    if not memory_enabled(cfg):
        result["skipped"].append({"reason": "memory_disabled"})
        return result

    written = 0
    for item in files:
        if written >= limit:
            break
        source_ref = f"{SOURCE_PREFIX}{item['sha1']}"
        try:
            turns = transcript_turns(Path(item["path"]))
            stats = transcript_stats(turns)
            if not stats["turns"]:
                result["skipped"].append({"name": item["name"], "reason": "empty_transcript"})
                continue
            event = pair_with_calendar(item["modified_at"], database=database, cfg=cfg)
            transcript = stats["text"]
            truncated = len(transcript) > MAX_TRANSCRIPT_CHARS
            if truncated:
                transcript = transcript[:MAX_TRANSCRIPT_CHARS]
            facts = (
                f"{stats['turns']} 段發言、{stats['speakers']} 位講者"
                + (f"、會議標題「{event['summary']}」" if event and event.get("summary") else "")
            )
            summary_lines: list[str] = []
            followups: list[str] = []
            summary_error: str | None = None
            generate = llm_generate
            if generate is None:
                # 與時段微摘要同一條 LLM 路徑（含逾時與執行緒隔離）
                from synthesizer.micro_summarizer import _default_generate

                generate = _default_generate(provider, int(cfg.get("meetings.timeout_seconds", 120) or 120))
            try:
                raw = generate(
                    _SUMMARY_SYSTEM,
                    _SUMMARY_USER.format(
                        max_followups=MAX_FOLLOWUPS, facts=facts, transcript=transcript
                    ),
                )
                provider_error = looks_like_llm_error(str(raw or ""))
                if provider_error:
                    # 供應商連不上／回錯誤字串：說出真正的原因，別讓事實閘代它背書
                    raise RuntimeError(f"provider 未回覆摘要：{provider_error}")
                summary_lines, followups = _split_summary(str(raw or ""))
                gate = fact_gate(
                    "\n".join(summary_lines + followups),
                    transcript,
                    allowed=[stats["turns"], stats["speakers"], stats["chars"]],
                )
                if not gate["ok"]:
                    summary_lines, followups = [], []
                    summary_error = f"事實閘擋下（逐字稿沒有的數字：{'、'.join(gate['unsupported_numbers'][:5])}）"
                elif not summary_lines:
                    summary_error = "LLM 回應無法解析"
            except Exception as exc:  # noqa: BLE001 — 摘要失敗不該讓逐字稿紀錄消失
                summary_error = f"{type(exc).__name__}: {exc}"[:200]

            body = _compose_body(
                summary_lines=summary_lines,
                followups=followups,
                stats=stats,
                event=event,
                file_name=item["name"],
                truncated=truncated,
                provider=provider,
                summary_error=summary_error,
            )
            title = f"會議紀錄：{(event or {}).get('summary') or item['name']}"
            note = record_observation(
                title=title[:180],
                body=body,
                source_ref=source_ref,
                source="meeting_notes",
                database=database,
                now=now,
            )
            if note is None:
                result["skipped"].append({"name": item["name"], "reason": "already_recorded"})
                continue
            written += 1
            result["written"].append({
                "name": item["name"],
                "source_ref": source_ref,
                "note_id": note.get("id"),
                "paired_event": (event or {}).get("summary"),
                "followups": len(followups),
                "summary_error": summary_error,
            })
        except Exception as exc:  # noqa: BLE001 — 一個檔案壞掉不影響其他檔（同 calendar_watcher）
            result["errors"].append({"name": item["name"], "error": f"{type(exc).__name__}: {exc}"[:200]})
    result["written_count"] = len(result["written"])
    return result


# ---- 「你在開會」與提案 ---------------------------------------------------


def meeting_context(
    *, database: Any | None = None, cfg: Any | None = None, now: datetime | None = None
) -> dict[str, Any]:
    """現在是不是在開會：兩個確定性訊號，兩個都有才算（ADR-022 D2）。

    (a) 行事曆有正在進行的非全天事件；(b) 近 10 分鐘前景視窗是會議軟體。
    只有其一時如實說差異，不猜；**只看應用程式名稱，不讀視窗標題內容**。
    """
    cfg = cfg or get_config()
    now = _naive(now or get_local_now())
    context: dict[str, Any] = {
        "enabled": True,
        "in_meeting": False,
        "calendar_event": None,
        "app": None,
        "line": None,
        "sources": {"calendar": "calendar_events", "window": "window_events"},
        "claim_boundary": "只用行事曆事件與前景應用程式名稱判斷；不讀會議內容、不錄音。",
    }
    try:
        from core.calendar_agenda import day_agenda

        agenda = day_agenda(now=now, database=database, cfg=cfg)
        context["enabled"] = bool(agenda.get("enabled"))
        ongoing = agenda.get("ongoing")
        if ongoing:
            context["calendar_event"] = {
                "summary": ongoing.get("summary"),
                "end": ongoing.get("end"),
                "minutes_left": max(
                    0, int((datetime.fromisoformat(ongoing["end"]) - now).total_seconds() // 60)
                ),
            }
    except Exception as exc:  # noqa: BLE001 — 行事曆讀不到就只回報應用程式訊號
        context["calendar_error"] = type(exc).__name__

    try:
        from core.database import get_db
        from core.models import WindowEvent

        db = database or get_db()
        since = now - timedelta(minutes=MEETING_APP_IDLE_MINUTES)
        with db.session_scope() as session:
            rows = (
                session.query(WindowEvent.app_name, WindowEvent.end_time)
                .filter(WindowEvent.end_time >= since)
                .order_by(WindowEvent.end_time.desc())
                .limit(50)
                .all()
            )
        for app_name, end_time in rows:
            lowered = str(app_name or "").lower()
            if any(keyword in lowered for keyword in MEETING_APPS):
                context["app"] = {
                    "app_name": app_name,
                    "last_seen_at": _naive(end_time).isoformat(timespec="minutes") if end_time else None,
                }
                break
    except Exception as exc:  # noqa: BLE001 — 視窗紀錄讀不到就只回報行事曆訊號
        context["window_error"] = type(exc).__name__

    event, app = context["calendar_event"], context["app"]
    if event and app:
        context["in_meeting"] = True
        context["line"] = (
            f"📞 會議中：{event['summary'] or '（無標題）'}（還剩 {event['minutes_left']} 分）"
        )
    elif event and not app:
        context["line"] = (
            f"📅 行事曆說現在是「{event['summary'] or '（無標題）'}」，但沒看到會議軟體在前景"
        )
    elif app and not event:
        context["line"] = f"💬 {app['app_name']} 在前景，但行事曆沒有對應的行程"
    return context


def collect_meeting_signals(
    *, database: Any | None = None, cfg: Any | None = None, now: datetime | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """兩種提案：待你處理的候選待辦、剛結束的會議沒有逐字稿。"""
    cfg = cfg or get_config()
    now = _naive(now or get_local_now())
    signals: list[dict[str, Any]] = []
    meta: dict[str, Any] = {"used": False}
    if not meetings_enabled(cfg):
        meta["reason"] = "no_transcript_dir"
        return signals, meta

    files, source_meta = list_transcripts(cfg=cfg, now=now, limit=50)
    meta = {"used": True, "transcript_dir": source_meta.get("path"), "files": len(files)}

    # (1) 記憶區裡的會議觀察還有沒有人沒處理的候選待辦
    pending_total = 0
    try:
        from core.secretary_memory import list_notes, memory_enabled

        if memory_enabled(cfg):
            listed = list_notes(kind="observation", limit=200, database=database)
            for note in listed.get("notes", []):
                if not str(note.get("source_ref") or "").startswith(SOURCE_PREFIX):
                    continue
                pending = [item for item in parse_followups(note.get("body", "")) if item["status"] == "pending"]
                if not pending:
                    continue
                pending_total += len(pending)
                age_days = 0.0
                created = note.get("created_at")
                if created:
                    try:
                        age_days = max(0.0, (now - datetime.fromisoformat(created)).total_seconds() / 86400)
                    except ValueError:
                        age_days = 0.0
                signals.append({
                    "signal_type": "meeting_followups",
                    "project_key": note.get("project_key") or "General",
                    "subject_ref": f"meeting_note:{note['id']}",
                    "title": note.get("title") or "會議紀錄",
                    "detail": "；".join(item["text"] for item in pending[:3]),
                    "url": None,
                    "reasons": [
                        f"這場會有 {len(pending)} 條候選待辦還沒處理",
                        "候選待辦要你點了才會成為未結事項",
                    ],
                    "age_days": round(age_days, 1),
                    "score": round(min(0.99, 0.72 + min(age_days, 3) * 0.02), 3),
                    "evidence_ref": f"secretary_notes:{note['id']}",
                    "observed_at": datetime.fromisoformat(created) if created else now,
                    "open_loop_refs": [],
                    "meeting_followups": pending,
                    "meeting_note_id": note["id"],
                })
    except Exception as exc:  # noqa: BLE001 — 記憶區讀不到不影響另一種提案
        meta["followups_error"] = type(exc).__name__
    meta["pending_followups"] = pending_total

    # (2) 剛結束的會議沒有逐字稿：結束後 15 分鐘才提，超過 4 小時就不再提
    try:
        from core.calendar_agenda import events_between

        window_start = now - timedelta(hours=4)
        rows = events_between(window_start, now, database=database)
        newest_file = files[0]["modified_at"] if files else None
        for row in sorted(rows, key=lambda item: item.instance_end or now, reverse=True):
            end = _naive(row.instance_end)
            if row.all_day or end is None or end > now - timedelta(minutes=15):
                continue
            if newest_file is not None and newest_file >= end:
                break   # 這場之後已經有新檔案落地
            minutes = int((now - end).total_seconds() // 60)
            signals.append({
                "signal_type": "meeting_transcript_missing",
                "project_key": "General",
                "subject_ref": f"meeting_event:{row.uid}",
                "title": f"剛結束的會議沒有逐字稿：{row.summary or '（無標題）'}",
                "detail": f"{minutes} 分鐘前結束",
                "url": None,
                "reasons": [
                    f"{minutes} 分鐘前結束，逐字稿資料夾沒有更新的檔案",
                    "若這場有開轉錄，從 Teams 下載到逐字稿資料夾我就會整理",
                ],
                "age_days": round(minutes / 1440, 2),
                "score": 0.45,
                "evidence_ref": f"calendar_events:{row.id}",
                "observed_at": end,
                "open_loop_refs": [],
            })
            break
    except Exception as exc:  # noqa: BLE001 — 行事曆讀不到不影響候選待辦提案
        meta["missing_error"] = type(exc).__name__
    return signals, meta


def accept_followup(
    note_id: int,
    index: int,
    *,
    action: str = "accept",
    project_key: str | None = None,
    database: Any | None = None,
) -> dict[str, Any]:
    """把一條候選待辦變成未結事項（或忽略它）。**只有這條路徑會寫 open_loops。**"""
    from core.database import get_db
    from core.models import SecretaryNote

    if action not in ("accept", "ignore"):
        raise ValueError("action 必須是 accept 或 ignore")
    db = database or get_db()
    with db.session_scope() as session:
        note = (
            session.query(SecretaryNote)
            .filter(SecretaryNote.id == int(note_id), SecretaryNote.kind == "observation")
            .first()
        )
        if note is None or not str(note.source_ref or "").startswith(SOURCE_PREFIX):
            raise ValueError("找不到這則會議觀察")
        items = parse_followups(note.body or "")
        match = next((item for item in items if item["index"] == int(index)), None)
        if match is None:
            raise ValueError(f"找不到第 {index} 條候選待辦")
        if match["status"] != "pending":
            return {"status": match["status"], "text": match["text"], "changed": False}
        note.body = mark_followup(note.body or "", int(index), "accepted" if action == "accept" else "ignored")
        title, body_project = match["text"], note.project_key

    loop_id = None
    if action == "accept":
        from core.project_engine import create_open_loop

        loop_id = create_open_loop(
            project_key=project_key or body_project or "General",
            title=title,
            source_type="meeting",
        )
    return {
        "status": "accepted" if action == "accept" else "ignored",
        "text": title,
        "open_loop_id": loop_id,
        "changed": True,
    }
