# ADR-024：秘書叢集四層化——訊號／聚合／呈現／記憶，外加兩個定型的契約

- 狀態：**Accepted**（2026-09-16 起草並於同日實作，TODO D8）
- 關聯：[ADR-007](ADR-007-proposal-only-secretary.md) proposal-only 秘書、[ADR-012](ADR-012-secretary-memory.md) 記憶區、[ADR-017](ADR-017-pattern-aware-proposals.md) 模式提案、[ADR-018](ADR-018-declared-profile.md) 宣告式個人檔案、[ADR-023](ADR-023-one-activity-memory.md) 一份活動記憶
- 依據：[docs/REVIEW-2026-09-16-project-assessment.md](REVIEW-2026-09-16-project-assessment.md) §4.3 第 4、6 點

## Context

秘書相關的程式散在 `core/` 底下十一個平輩模組（約 4,100 行），彼此的關係只能靠讀原始碼推：

- **沒有共同型別**：五個訊號收集器回傳 `dict[str, Any]`，`build_action_proposals` 直接對那些 dict 取鍵。
  少一個鍵是 `KeyError`，多一個鍵沒人發現；要知道一個「訊號」到底有哪些欄位，只能把六個收集器全讀一遍。
- **真的有一個環**：`proactive_secretary ↔ secretary_memory ↔ secretary_home ↔ agent_executor`。
  環用**函式內延遲 import** 撐著——`core/` 裡有 **116 處**指向 `core.*` 的函式內 import（另有 73 處指向第三方，
  那些是刻意的：LLM 供應商 SDK、選用的 `[rag]` 套件、只有 Windows 才載入的東西）。
- **模組邊界不反映職責**：`secretary_ask`（206 行）、`secretary_profile`（150 行）、`secretary_home`（242 行）
  各自一個模組，但它們是同一件事的三個片段；`secretary_memory` 反過來又 import 了 `secretary_packs` 與
  `proactive_secretary`，只為了在記憶脈絡裡塞一行早晨包摘要與 top 提案。

結果是：要改一個提案欄位，得同時打開六個檔案；要知道誰會被影響，只能靠全域搜尋。

## Decision

### 1. 四層，方向只准往下

```
types   ← 兩個 dataclass，任何人都能 import
memory  ← 筆記／偏好／決定／觀察／個人檔案（只讀寫自己的表）
signals ← 從既有 domain 模組收集訊號，正規化成 Signal
aggregate ← 把 Signal 變成 Proposal：加權、去重、排序、上限
present ← 給人看的：今日首頁、問候卡、回答、早晨包
```

上層可以 import 下層；**下層永遠不准 import 上層**。需要上層資料時由呼叫端注入
（例如 `memory_context(..., proposals=…)`，而不是 memory 自己去 import aggregate）。
`core/agent_executor.py`（閘門式執行器）不屬於這四層：它在 aggregate 之上、present 之下，
可以 import aggregate，永遠不被 aggregate import。

### 2. 兩個定型的契約

```python
@dataclass(frozen=True)
class Signal:      # 收集器產出、aggregate 消費
    signal_type: str; project_key: str; subject_ref: str; title: str
    evidence_ref: str; score: float; reasons: tuple[str, ...]
    ...（其餘皆有預設值的可選欄位）

@dataclass(frozen=True)
class Proposal:    # aggregate 產出；`to_dict()` 是 API 回傳的形狀
```

`Signal.from_dict()` 讓既有收集器不必同時改寫；`Proposal.to_dict()` 保證 API 的 JSON
形狀**一個鍵都不動**（有契約測試逐鍵比對）。

### 3. 檔案配置（以及一個明說的偏離）

| 檔案 | 來自 | 行數量級 |
| :--- | :--- | :--- |
| `core/secretary/types.py` | 新增 | 小 |
| `core/secretary/memory.py` | `secretary_memory` ＋ `secretary_profile` | ~650 |
| `core/secretary/signals.py` | `triage_signals` ＋ 各來源的正規化 | ~400 |
| `core/secretary/aggregate.py` | `proactive_secretary` ＋ `secretary_advisor` | ~900 |
| `core/secretary/present.py` | `secretary_home` ＋ `secretary_ask` | ~450 |
| `core/secretary/greeting.py` | `secretary_greeting` | ~620 |
| `core/secretary/packs.py` | `secretary_packs` | ~420 |

TODO D8 寫的是「四個檔案」。這裡**四層是依賴方向，不是四個檔案**：問候卡（有 LLM 潤飾與事實閘）
與早晨包（L0 template 的編排）各自成檔，硬塞進 `present.py` 會做出一個 1,500 行的新 god object——
那正是 D4 剛拆掉的東西。偏離寫在這裡，不藏在程式裡。

`activity_patterns`、`docs_freshness`、`meeting_transcripts`、`triage` 用到的資料查詢等
**domain 模組留在原地**：它們不是秘書的一部分，是秘書讀的資料來源。

## 邊界（不變）

- 提案仍是唯讀：`build_action_proposals` 不寫資料庫、不呼叫外部 LLM（ADR-007）。
- 記憶區仍只存短文字與可刪除的觀察，不存 prompt／response 原文（ADR-012）。
- LLM 註解層仍是 annotate-only、預設關閉、失敗回退規則版（ADR-011）。
- 執行仍需 execution token 與逐項批准（ADR-008）；這次沒有動任何閘門。
- API 路由、JSON 形狀、job 型別全部不變（路由快照測試與逐鍵比對把關）。

## Consequences

- 想知道「一個訊號有哪些欄位」只要讀 `types.py`；想加一種訊號只要寫一個收集器並回傳 `Signal`。
- 指向 `core.*` 的函式內延遲 import 從 116 降到個位數；剩下的第三方延遲 import 是刻意的，寫在註解裡。
- 代價：`from core.proactive_secretary import ...` 這類既有 import 路徑改了。這是內部模組路徑，
  不是對外 API；所有呼叫端（`core/api/*`、`notifiers/`、`synthesizer/`、`main.py`、測試）一次改完，
  斷言一字未改。
