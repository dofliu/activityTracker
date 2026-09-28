"""`omni init --detect`：看看這台機器上有哪些 AI 逐字稿，**然後問**（TODO E6）。

這個模組**只看不寫**。它回傳一份候選清單，要不要寫進設定是 `main.py` 那一層的事，
而那一層一定要拿到明確的同意才會動設定檔。分開的理由很實際：偵測是可以隨時跑、
跑幾次都沒差的動作；寫設定不是。把它們放在同一個函式裡，遲早會有人為了方便加一個
`auto=True`。

**偵測不等於啟用。** 這裡回的每一筆都只講「這個位置存在、裡面有幾份逐字稿」，
一個字都沒有講「要不要採集」。採集器的開關（`watchers.agent_log_watcher.<key>`）與
危險能力開關完全不在這個模組的視野裡——`tests/test_source_detection.py` 用掃描守著。

位置知識來自 `coding_agent_transcripts`（ADR-033）：四個平台的預設位置住在套件裡，
這裡不再抄第二份。Antigravity 是唯一沒有官方固定位置的，套件給的那個是**候選**，
`discover()` 自己不會用它——猜一個位置去採集，跟提議一個位置請使用者確認，
差別就是同意權在誰手上。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

from coding_agent_transcripts import SOURCES, DictConfig

# 設定檔裡放路徑的鍵。**只有這四個**——偵測寫得進去的東西就這麼多，
# 開關類的鍵一個都不在名單上（契約測試會拿這張表去比對）。
PATH_KEYS = {
    "claude_code": "watchers.agent_log_watcher.claude_code_logs_path",
    "claude_desktop": "watchers.agent_log_watcher.claude_desktop_logs_path",
    "codex": "watchers.agent_log_watcher.codex_logs_path",
    "antigravity": "watchers.agent_log_watcher.antigravity_logs_path",
}

# 掃描上限。偵測要「一秒回得來」，不是把整個家目錄走一遍；數到這個數就停，
# 回傳的 `transcripts` 是「至少這麼多」而不是精確值（`truncated` 會說出來）。
SCAN_LIMIT = 200


@dataclass(frozen=True)
class Candidate:
    """一個平台的偵測結果。**全部是觀察，沒有一個欄位在講要不要採集。**"""

    key: str
    label: str
    path: Path
    transcripts: int
    truncated: bool
    already_configured: bool
    config_key: str

    @property
    def readable(self) -> bool:
        """列出來的一定要點得開——存在但讀不到（權限）等於沒有。"""
        return self.transcripts > 0 or self._listable()

    def _listable(self) -> bool:
        try:
            next(iter(self.path.iterdir()), None)
            return True
        except OSError:
            return False


def _count_transcripts(source: Any, path: Path) -> tuple[int, bool]:
    """用**該平台自己的 `discover()`** 去數，不自己寫一套 glob。

    自己寫 glob 就會跟 parser 漂移：Claude Code 是 `projects/**/*.jsonl` 但沒有
    projects 時退回 `history.jsonl`、Claude Desktop 埋在四層底下、Codex 要認兩種副檔名。
    那些規則已經有一份了，偵測沒有理由再抄。
    """
    cfg = DictConfig({PATH_KEYS[source.key]: str(path)})
    try:
        found = list(source.discover(cfg, full_history=True))
    except (OSError, ValueError):
        return 0, False
    if len(found) > SCAN_LIMIT:
        return SCAN_LIMIT, True
    return len(found), False


def detect_sources(cfg: Any | None = None) -> List[Candidate]:
    """這台機器上找得到的逐字稿來源。**不寫任何東西。**

    `cfg` 只用來判斷「這個平台的路徑是不是已經寫在設定裡了」——已經設過的仍然會列出來
    （使用者有權知道偵測到什麼），但會標成 `already_configured`，由上層決定不要重問。

    **不存在的不列。** 一個列出來的候選，代表那個目錄此刻真的在、而且讀得到。
    """
    out: List[Candidate] = []
    for source in SOURCES:
        default = source.default_logs_dir
        if default is None:  # pragma: no cover - 四個平台目前都有
            continue
        configured = _configured_path(cfg, source.key)
        path = configured or default()
        if not path.is_dir():
            continue
        count, truncated = _count_transcripts(source, path)
        candidate = Candidate(
            key=source.key,
            label=source.label,
            path=path,
            transcripts=count,
            truncated=truncated,
            already_configured=configured is not None,
            config_key=PATH_KEYS[source.key],
        )
        if not candidate.readable:
            continue  # 存在但點不開（權限）——列出來只會讓使用者以為可以用
        out.append(candidate)
    return out


def _configured_path(cfg: Any | None, key: str) -> Optional[Path]:
    if cfg is None:
        return None
    raw = cfg.get(PATH_KEYS[key])
    if not raw:
        return None
    try:
        return cfg.get_path(PATH_KEYS[key])
    except (AttributeError, TypeError, ValueError):
        return None


def describe(candidate: Candidate) -> str:
    """給人看的一行。**不把「有幾份」講成「會採集幾份」**——那是兩件事。"""
    if candidate.transcripts == 0:
        found = "目錄在，但還沒有逐字稿"
    elif candidate.truncated:
        found = f"至少 {candidate.transcripts} 份逐字稿"
    else:
        found = f"{candidate.transcripts} 份逐字稿"
    suffix = "（設定檔裡已經有這個路徑）" if candidate.already_configured else ""
    return f"{candidate.label}：{candidate.path}（{found}）{suffix}"
