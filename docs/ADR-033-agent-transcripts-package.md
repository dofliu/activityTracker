# ADR-033：把 transcript parser 抽成可單獨安裝的套件

- 狀態：**Accepted**（2026-09-28）
- 對應：TODO **E5**、[ROADMAP §14](../ROADMAP.md) 推廣路線
- 前置：[ADR-025](ADR-025-transcript-parsers-and-drift.md)（D9 已把共同介面收斂成 `discover`／`parse`，parser 不碰資料庫）

---

## Context

`watchers/transcripts/` 現在有 1,040 行、七個模組，解四種別家工具的私有格式
（Claude Code、Claude Desktop、Codex、Antigravity）。這是本專案少數**對別人也有用**的
東西：任何想讀 coding agent 逐字稿的人都得重寫一次同樣的四份解析。E5 要把它抽出去。

先量再說。**六件動工前查證過的事實，其中兩件直接改變了 TODO 的預設做法：**

### 1. `agent-transcripts` 這個名字在 PyPI 上已經有人了——而且做的是同一件事

TODO 寫的套件名是 `agent-transcripts`。實查：

```
GET https://pypi.org/pypi/agent-transcripts/json  ->  HTTP 200
  name    : agent-transcripts
  version : 0.0.2
  summary : Capture coding-agent transcripts to disk and expose them as SQL.
  author  : Kevin Scott <me@thekevinscott.com>
  wheel   : agent_transcripts-0.0.2-py3-none-*.whl
```

兩件事同時成立：**發行名被佔用**，而且 **import 路徑 `agent_transcripts` 也被佔用**。
後者才是致命的——這正是 [ADR-032](ADR-032-readonly-mcp-context-server.md) 決策一量到的同一個陷阱：
兩個發行版提供同名頂層模組時，誰先在 `sys.path` 上誰贏，症狀是「在我機器上好好的」。
一個既存的、功能重疊的同名套件，是這個陷阱最容易發生的形狀。

### 2. 這個套件的第三方相依是**零**

`watchers/transcripts/` 只 import 標準函式庫（`json`／`re`／`hashlib`／`time`／
`dataclasses`／`datetime`／`pathlib`／`typing`），外加本專案的兩個東西：

| 借用 | 實際內容 | 行數 |
| :--- | :--- | --: |
| `core.time_utils.get_local_now` | `datetime.now()` | 1 |
| `core.desktop_sources` 的 2 支 | `default_claude_desktop_logs_dir`、`iter_claude_desktop_project_logs` | 86（整檔，純標準函式庫） |

所以「能不能獨立」不是依賴問題，是**這兩處借用**的問題。

### 3. 真的抽出去，在乾淨 venv 裡跑得動——已經做過一次原型

不是推論。把七個模組複製出去、把上面兩處借用改成套件內的 `_compat`、`python -m build`
出 wheel、裝進一個**沒有本專案**的乾淨 venv：

```
import OK；平台數 = 4 ('claude_code', 'claude_desktop', 'codex', 'antigravity')
第三方相依： 零
core 有沒有被拉進來： 沒有
```

四種格式各餵一份**完全合成**的樣本（沒有任何真實 prompt／路徑／機器名），全部解得開：

```
平台                 輪次  第一輪的 prompt / status / turn_key 前 12 碼
claude_code         2  '合成提問：把排序改成穩定排序' / final_candidate / 3bc91ddef003
claude_desktop      2  '合成提問：把排序改成穩定排序' / final_candidate / 873e80f769a3
codex               1  '合成提問：這個函式為什麼回 None' / final_candidate / 8f9e3f8a520c
antigravity         1  '合成提問：幫我畫一張流程圖' / final_candidate / 26de856cd773
```

### 4. `turn_key` 抽出去之後**逐位元組相同**——這是整個遷移最重要的一條

`turn_key` 是 `sha256(platform|resolve 後的路徑|source_position)`，它同時是 SQLite 的
跨重啟去重鍵。**只要它變一個位元，既有使用者的資料庫就會把所有歷史逐字稿重新灌一次。**
同一份輸入分別餵給 repo 內的 parser 與乾淨 venv 裡的套件：

```
repo 內   : [('fbdfe024ae99…1e91c', 1, 'partial')]
獨立套件  : [('fbdfe024ae99…1e91c', 1, 'partial')]
一致
```

### 5. 兩支測試「一字不改」這個判準，決定了 repo 端必須留轉接層

TODO 的完成判準寫著 `tests/test_transcript_parsers_and_drift.py` 與
`tests/test_transcript_contracts.py` **一字不改**全綠。那兩支 import 的是：

```python
from watchers.transcripts import SOURCES, SOURCE_KEYS
from watchers.transcripts import antigravity, claude_code, claude_desktop, codex
from watchers.transcripts.drift import DRIFT_WINDOW_DAYS, empty_drift, evaluate_drift
```

所以 `watchers.transcripts` 這條 import 路徑**必須繼續活著**。而轉接層要長成什麼樣不能用猜的：

| 轉接形狀 | `from …transcripts import SOURCES` | `from …transcripts import codex` | `from …transcripts.drift import X` |
| :--- | :-: | :-: | :-: |
| ① 只有 `__init__.py`，用屬性轉接 | ✅ | ✅ | ❌ `ModuleNotFoundError` |
| ② `__init__.py` 裡塞 `sys.modules` | ✅ | ✅ | ✅ |
| ③ 每個子模組留一個薄轉接檔 | ✅ | ✅ | ✅ |

（三種形狀都實際跑過。①之所以壞，是因為 `from a.b.c import X` 會真的去 import 模組
`a.b.c`，不是對 `a.b` 做屬性查找——而 `from a.b import c` 會。這個差別看程式碼看不出來。）

### 6. `core/desktop_sources.py` 不是只有 transcript 在用

它還被 `core/capture_coverage.py` 與 `scripts/verify_release_artifacts.py` 引用。
所以「把它整個搬進套件」會讓 `core` 反過來相依套件——方向錯了。

---

## Decision

### 決策一：套件名是 `coding-agent-transcripts`，import 路徑是 `coding_agent_transcripts`

**不能用 TODO 原本寫的 `agent-transcripts`**（Context 1：發行名與 import 路徑都被佔用，
而且對方做的是同一件事，撞名的後果不是「換個名字」而是「裝了兩個之後誰贏看運氣」）。

查過的候選（全部 HTTP 404 ＝ 可用）：

| 候選 | 取捨 |
| :--- | :--- |
| **`coding-agent-transcripts`** | ✅ 採用。說得出自己是什麼、不綁本專案（E5 的目的是推廣，不是宣示所有權）、import 路徑 `coding_agent_transcripts` 與 PyPI 上任何東西都不撞 |
| `omnicontext-transcripts` | 名字就寫著「這是 OmniContext 的內部套件」，對「給別人用」這個目的是反效果 |
| `agenttranscripts`／`ai-transcripts` | 前者只是把被佔用的名字拿掉連字號——`pip install` 時兩個名字長得幾乎一樣，是在製造混淆；後者太泛 |
| `transcript-sources`／`agent-transcript-parsers` | 沒有不好，只是「coding agent」這個限定詞是這個套件最重要的資訊 |

**import 路徑不得取名 `agent_transcripts`**，即使發行名不同——那正是要避開的那個碰撞。

> 這一條是本 ADR 唯一「TODO 寫了 A、我改成 B」的決定。改的理由是查證結果，不是偏好；
> 如果你希望用別的名字，這是最該覆寫的一條，改名的成本也只有這一輪最低。

### 決策二：套件自足靠**內化**那兩處借用，不靠 vendor 整個 `core`

- `get_local_now()` 是一行 `datetime.now()`。套件自己寫一份（`_clock.py`），**不從 `core` 借**。
  這不是重複程式碼的問題：一個解析函式庫不該為了取現在幾點而相依一個活動追蹤器。
- `core/desktop_sources.py` 的兩支探索函式**移進套件**（它們本來就只在講 Claude Desktop
  的目錄結構，是格式知識不是應用知識），然後 **`core` 反過來從套件 import**。
  方向是對的：`core` 是使用端，使用端相依套件天經地義（Context 6 的三個呼叫點因此各改一行）。
  `claude_desktop_cloud_cache_detected` 與 `has_claude_desktop_project_logs` 留在 `core`——
  那兩支在回答「這台機器上有沒有東西可採」，是應用問題不是格式問題。

### 決策三：`cfg` 從「本專案的 Config 物件」收斂成一個**兩個方法的 protocol**

四個 `discover` 目前吃的是本專案的 `Config`，實際只用到兩個方法：

```python
cfg.get("watchers.agent_log_watcher.antigravity_logs_path")          # antigravity
cfg.get_path("watchers.agent_log_watcher.claude_code_logs_path", …)  # claude_code
cfg.get_path("watchers.agent_log_watcher.claude_desktop_logs_path", …)
cfg.get("watchers.agent_log_watcher.claude_desktop_initial_lookback_days", 30)
```

套件定義一個 `TranscriptConfig` protocol（`get(key, default)` 與 `get_path(key, default)`），
並附一個 `dict` 實作當預設，讓外部使用者不必生出一個 `Config`。本專案的 `Config` 天然
滿足這個 protocol，所以 repo 端一行都不用改。

**設定鍵名照抄不改**（`watchers.agent_log_watcher.*`）。那些鍵名帶著本專案的味道，
但改名會讓既有使用者的設定檔失效，而這一輪的目的是搬家不是改介面。鍵名前綴留給下一版。

### 決策四：repo 端留**每個子模組一個薄轉接檔**（形狀③），不用 `sys.modules`

形狀②（在 `__init__.py` 裡寫 `sys.modules[__name__ + ".drift"] = drift`）也能過，
但它把「這個模組存在」變成一個副作用——讀 `watchers/transcripts/` 的人看不到 `drift.py`，
卻 import 得到它。形狀③多六個三行檔案，換到的是「看得到才 import 得到」。

轉接層**明說自己是轉接層**，並標注預定移除的時機（本 repo 內部改完直接引用套件之後）。

### 決策五：套件自帶的測試樣本一律**合成**，並由契約測試守門

TODO 的判準是「套件自帶的 fixture 不得夾帶任何真實 prompt／路徑／機器名」。
落地成一條**掃描**而不是一條叮嚀：掃 `tests/` 與 `samples/` 底下所有檔案，
命中 `/home/<非 runner 的名字>`、`/Users/`、`C:\Users\`、`sk-`／`ghp_`／`AIza` 樣式，
或本機主機名，即失敗。**掃描必須是內容掃描 ＋ 白名單例外**，白名單只准放
刻意用來測「會咬人的字串」的那幾筆，且每一筆要寫明理由。

現況有利：repo 內那兩支測試的樣本全部是 `tmp_path` 裡就地造出來的，沒有一份是真的
逐字稿——抽出去時直接沿用同一種做法即可。

### 決策六：版本相依寫成 `>=` 下限 ＋ 上限鎖大版本

`pyproject.toml` 寫 `coding-agent-transcripts>=0.1,<1.0`。理由是格式漂移的風險屬於套件
（ADR-025 的整個前提），所以**要拿得到 patch**；但四種格式都是別家私有格式，
套件有權在大版本裡改介面，那時候本 repo 要有一次明確的升級動作，不是被動吃下。

套件本身 `requires-python = ">=3.10"`（與本 repo 相同），`dependencies = []`。

### 不做的事

- **不做 SQL 層**：PyPI 上那個同名套件把「expose them as SQL」放進了自己的範圍。我們不做——
  ADR-025 的整個設計就是「parser 不碰資料庫」，加上 SQL 等於把那條線抹掉。
- **不把 `agent_log_watcher` 一起抽出去**：它管 checkpoint、寫入、diagnostics，是應用邏輯。
- **不趁機改介面**：`discover`／`parse` 的簽章、`TranscriptTurn` 的欄位、設定鍵名全部照舊。
  搬家與改介面同時做，出事時分不出是哪一件造成的。
- **不在這一輪動 CI 的發佈流程**：套件怎麼發版是套件 repo 的事，本 repo 只負責當使用端。

---

## 落地順序（E5 的實作分片）

1. **建立套件**：七個模組 ＋ `_clock.py` ＋ 移入的 desktop 探索 ＋ protocol ＋ 合成樣本
   ＋ 自己的測試 ＋ `pyproject.toml`。
2. **本 repo 轉成使用端**：`watchers/transcripts/` 換成七個薄轉接檔；`core/desktop_sources.py`
   的兩支改成從套件 re-export；`pyproject.toml` 加相依。
3. **收據**：兩支測試一字不改全綠、`turn_key` 不變（與本 ADR 同一條實驗）、
   乾淨 venv 解四種格式、`python -m build` ＋ `verify_release_artifacts.py` 通過、
   `python main.py verify` 與基底相同。

**第 1 步的產出不在本 repo 裡**——那是另一個 repo／另一個發行版。本 repo 這一側只有第 2 步。
這件事值得寫出來：E5 是本專案第一個**跨 repo** 的項目，而「跨 repo」本身就是新的失敗面
（版本錯配、套件還沒發版時 repo 端怎麼跑測試）。落地時要先回答「套件還沒上 PyPI 之前，
本 repo 的 CI 從哪裝它」——可行解是 `pip install <git-url>` 或 path dependency，
但那會讓 `verify_release_artifacts.py` 的封閉性假設變複雜，屬於實作階段要量的東西。

---

## Consequences

**買到的**

- 四份 parser 對外部使用者可用，不必連著一個活動追蹤器一起裝（實測：零第三方相依、
  乾淨 venv 裝得起來、四種格式解得開）。
- 格式知識與應用邏輯的界線從「同一個 repo 的兩個目錄」升級成「兩個發行版」——
  ADR-025 立的那條線第一次有了機械保證：套件裡根本沒有資料庫可碰。
- `core/desktop_sources.py` 裡的格式知識搬到它該去的地方。

**付出的**

- **多一個 repo 要維護**，而且本 repo 從此有一個外部相依會漂移。ADR-025 說
  「自己維護的東西會 drift」，抽出去之後變成「別人（就算那個人是我們）維護的東西會 drift」，
  漂移風險沒有消失，只是換了位置——差別是現在它有版本號。
- **轉接層是實打實的技術債**。六個三行檔案不貴，但「兩條 import 路徑指向同一份程式碼」
  這件事本身會誤導人。ADR 記下它的移除條件：repo 內部全部改成直接引用套件之後。
- **設定鍵名還帶著本專案的味道**（`watchers.agent_log_watcher.*`），外部使用者會覺得突兀。
  這是為了不破壞既有設定檔而付的價，記在這裡，不假裝它不存在。

**這個 ADR 沒有證的事**

- 沒有證「外部真的有人要用」。E5 是推廣路線的一步，而推廣的效果本 ADR 量不到。
- 沒有證抽出去之後**行為完全相同**。量到的是 `turn_key` 一致與四種格式解得開；
  完整的等價要靠第 3 步那兩支一字不改的測試全綠，而那要等實作。
- 沒有回答「套件還沒上 PyPI 時 CI 從哪裝」。那是落地順序裡點名的第一個未解問題。
