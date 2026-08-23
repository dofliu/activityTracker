"""每日簡報輸出：把「進行中工作 + 未結事項」寫進你每天會打開的地方

輸出兩份檔案到設定的目錄（預設 D:/Project_CodingSimulation）：
  • OMNICONTEXT_TODAY.md    — 純文字，可被其他工具或 AI 直接讀取
  • OMNICONTEXT_TODAY.html  — 自帶樣式的單檔頁面，可釘選為瀏覽器書籤

若設定了 inject_into（指向既有的 HTML 儀表板），則改為在該檔案的
<!-- OMNICONTEXT:START --> 與 <!-- OMNICONTEXT:END --> 之間注入區塊。
"""
import logging
import re
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import get_config
from core.time_utils import get_local_now
from core.project_engine import get_active_projects_list, get_open_loops_list, is_bucket_project

logger = logging.getLogger("OmniContext.DailyBrief")

MARKER_START = "<!-- OMNICONTEXT:START -->"
MARKER_END = "<!-- OMNICONTEXT:END -->"

_STATUS_LABEL = {
    "active": ("進行中", "#16a34a"),
    "idle": ("稍歇", "#d97706"),
    "stale": ("停滯", "#dc2626"),
}


def _collect() -> Dict[str, Any]:
    """彙整簡報所需資料"""
    projects = [p for p in get_active_projects_list() if not is_bucket_project(p.get("project_key"))]
    active = [p for p in projects if p["status"] == "active"]
    stagnant = [p for p in projects if p["status"] in ("idle", "stale")]

    return {
        "generated_at": get_local_now(),
        "active": active[:8],
        "stagnant": sorted(stagnant, key=lambda p: -p["idle_days"])[:5],
        "open_loops": get_open_loops_list(),
    }


# ----------------------------------------------------------------------
# Markdown
# ----------------------------------------------------------------------
def render_markdown(data: Dict[str, Any]) -> str:
    ts = data["generated_at"].strftime("%Y-%m-%d %H:%M")
    lines = [
        "# 📌 我現在在做什麼",
        "",
        f"> 由 OmniContext 於 {ts} 自動更新",
        "",
        f"## 🔥 進行中（{len(data['active'])}）",
        "",
    ]

    if data["active"]:
        for p in data["active"]:
            lines.append(f"- **{p['display_name']}** `{p['category']}` — 最後動作 {p['last_activity_at']}")
            lines.append(f"  - {p['last_action_summary']}")
    else:
        lines.append("- （目前沒有偵測到活躍專案）")

    lines.extend(["", f"## 📋 尚未收尾（{len(data['open_loops'])}）", ""])
    if data["open_loops"]:
        for ol in data["open_loops"]:
            lines.append(f"- [ ] **[{ol['project_key']}]** {ol['title']}")
    else:
        lines.append("- （沒有待收尾事項）")

    if data["stagnant"]:
        lines.extend(["", "## ⚠️ 太久沒碰", ""])
        for p in data["stagnant"]:
            lines.append(f"- **{p['display_name']}**（已 {p['idle_days']} 天）— {p['last_action_summary']}")

    lines.extend(["", "---", "", "完整儀表板：<http://127.0.0.1:8765>", ""])
    return "\n".join(lines)


# ----------------------------------------------------------------------
# HTML
# ----------------------------------------------------------------------
def render_html_fragment(data: Dict[str, Any]) -> str:
    """可注入既有頁面的 HTML 區塊"""
    ts = data["generated_at"].strftime("%Y-%m-%d %H:%M")
    parts = [
        '<section class="omnicontext-brief" style="font-family:system-ui,\'Microsoft JhengHei\',sans-serif;'
        'max-width:900px;margin:24px auto;padding:20px;border:1px solid #e5e7eb;border-radius:12px;'
        'background:#fff;color:#111827;">',
        '<h2 style="margin:0 0 4px;font-size:20px;">📌 我現在在做什麼</h2>',
        f'<p style="margin:0 0 16px;color:#6b7280;font-size:13px;">由 OmniContext 於 {escape(ts)} 自動更新</p>',
    ]

    parts.append(f'<h3 style="font-size:15px;margin:16px 0 8px;">🔥 進行中（{len(data["active"])}）</h3>')
    if data["active"]:
        parts.append('<ul style="margin:0;padding-left:20px;line-height:1.7;">')
        for p in data["active"]:
            label, color = _STATUS_LABEL.get(p["status"], ("—", "#6b7280"))
            parts.append(
                f'<li><strong>{escape(p["display_name"])}</strong> '
                f'<span style="color:{color};font-size:12px;">[{label}]</span><br>'
                f'<span style="color:#4b5563;font-size:13px;">{escape(str(p["last_action_summary"])[:160])}</span><br>'
                f'<span style="color:#9ca3af;font-size:12px;">{escape(p["last_activity_at"])}</span></li>'
            )
        parts.append("</ul>")
    else:
        parts.append('<p style="color:#9ca3af;">（目前沒有偵測到活躍專案）</p>')

    parts.append(f'<h3 style="font-size:15px;margin:20px 0 8px;">📋 尚未收尾（{len(data["open_loops"])}）</h3>')
    if data["open_loops"]:
        parts.append('<ul style="margin:0;padding-left:20px;line-height:1.7;">')
        for ol in data["open_loops"]:
            parts.append(
                f'<li><span style="background:#eef2ff;color:#4338ca;padding:1px 6px;border-radius:4px;'
                f'font-size:12px;">{escape(ol["project_key"])}</span> {escape(ol["title"])}</li>'
            )
        parts.append("</ul>")
    else:
        parts.append('<p style="color:#9ca3af;">（沒有待收尾事項）</p>')

    if data["stagnant"]:
        parts.append('<h3 style="font-size:15px;margin:20px 0 8px;">⚠️ 太久沒碰</h3>')
        parts.append('<ul style="margin:0;padding-left:20px;line-height:1.7;">')
        for p in data["stagnant"]:
            parts.append(
                f'<li><strong>{escape(p["display_name"])}</strong> '
                f'<span style="color:#dc2626;font-size:12px;">已 {p["idle_days"]} 天</span></li>'
            )
        parts.append("</ul>")

    parts.append(
        '<p style="margin-top:20px;font-size:13px;">'
        '<a href="http://127.0.0.1:8765" style="color:#2563eb;">開啟完整儀表板 →</a></p>'
    )
    parts.append("</section>")
    return "\n".join(parts)


def render_html_page(data: Dict[str, Any]) -> str:
    """獨立的單檔頁面（可設為瀏覽器書籤或首頁）"""
    return (
        '<!DOCTYPE html>\n<html lang="zh-TW">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<meta http-equiv="refresh" content="300">\n'
        "<title>我現在在做什麼 · OmniContext</title>\n"
        "</head>\n"
        '<body style="margin:0;padding:16px;background:#f9fafb;">\n'
        f"{render_html_fragment(data)}\n"
        "</body>\n</html>\n"
    )


# ----------------------------------------------------------------------
# 寫出
# ----------------------------------------------------------------------
def _inject_into_file(target: Path, fragment: str) -> bool:
    """把區塊注入既有 HTML 的標記之間；找不到標記則附加到 </body> 前"""
    try:
        content = target.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        logger.error(f"Cannot read inject target {target}: {e}")
        return False

    block = f"{MARKER_START}\n{fragment}\n{MARKER_END}"

    if MARKER_START in content and MARKER_END in content:
        content = re.sub(
            re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END),
            lambda _: block,
            content,
            flags=re.DOTALL,
        )
    elif "</body>" in content:
        content = content.replace("</body>", f"{block}\n</body>", 1)
    else:
        content = content + "\n" + block

    target.write_text(content, encoding="utf-8")
    logger.info(f"Injected daily brief into {target}")
    return True


def export_daily_brief(output_dir: Optional[str] = None) -> Dict[str, Any]:
    """產生每日簡報並寫入設定的位置"""
    cfg = get_config()
    data = _collect()

    out_dir_str = output_dir or cfg.get("exporters.daily_brief.output_dir", "")
    if not out_dir_str:
        out_dir_str = str(Path(__file__).parent.parent / "reports")

    out_dir = Path(out_dir_str)
    out_dir.mkdir(parents=True, exist_ok=True)

    written: List[str] = []

    md_path = out_dir / "OMNICONTEXT_TODAY.md"
    md_path.write_text(render_markdown(data), encoding="utf-8")
    written.append(str(md_path))

    html_path = out_dir / "OMNICONTEXT_TODAY.html"
    html_path.write_text(render_html_page(data), encoding="utf-8")
    written.append(str(html_path))

    # 選用：注入既有的 HTML 儀表板
    inject_target = cfg.get("exporters.daily_brief.inject_into", "")
    if inject_target:
        target = Path(inject_target)
        if target.exists():
            if _inject_into_file(target, render_html_fragment(data)):
                written.append(str(target))
        else:
            logger.warning(f"inject_into target not found: {target}")

    return {
        "status": "success",
        "generated_at": data["generated_at"].strftime("%Y-%m-%d %H:%M:%S"),
        "active_count": len(data["active"]),
        "open_loops_count": len(data["open_loops"]),
        "stagnant_count": len(data["stagnant"]),
        "files": written,
    }
