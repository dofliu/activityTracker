# coding-agent-transcripts

把 **Claude Code**、**Claude Desktop**、**Codex**、**Antigravity** 四種逐字稿讀成同一個
turn 模型。零第三方相依，只要 Python 3.10+。

```bash
pip install coding-agent-transcripts
```

```python
from coding_agent_transcripts import SOURCES

for source in SOURCES:
    for path in source.discover(None):          # 不給設定就用各平台的預設位置
        for turn in source.parse(path):
            print(turn.platform, turn.timestamp, turn.prompt[:40], turn.response_status)
```

## 它做什麼、不做什麼

| 做 | 不做 |
| :--- | :--- |
| 找到四種平台的逐字稿檔（`discover`） | 不寫資料庫、不做 SQL |
| 把一個檔案讀成一串 `TranscriptTurn`（`parse`，產生器） | 不管 checkpoint、不管排程 |
| 判斷一輪的回應是 `final_candidate`／`partial`／`missing` | 不判斷「這輪講得對不對」 |
| 過濾 CLI 自己產生的內部訊息（`is_cli_artifact`） | 不修改、不搬動來源檔案 |
| 「檔案在動、事件是零」的漂移偵測（純函式） | 不替你決定漂移了要做什麼 |

**`parse` 不碰資料庫**是刻意的：要不要寫、寫去哪，是使用端的事。這讓每個 parser 都能在
沒有資料庫、沒有設定檔的情況下單獨測。

## 幾個值得先知道的設計

**`turn_key` 是對外契約。** 它是 `sha256(platform|resolve 後的路徑|source_position)`，
設計上就是拿來當跨重啟去重鍵的——所以它的值**不會因為重構而改變**，套件自己有一條測試釘住它。

**回應狀態分三級，而且不會亂猜。** 沒有明確的 final marker 時，只有「下一個 user turn」
能封閉前一輪（`classify_response_status`）；檔案結尾那一輪則看檔案多久沒動
（`eof_response_status`）。不確定就說 `partial`，不會為了好看而升級成 `final_candidate`。

**壞掉的 JSONL 不會被靜默跳過。** `iter_jsonl_records` 會把壞行收集起來然後拋 `ValueError`
——讓使用端的 checkpoint 保持錯誤狀態，而不是靜悄悄前移把資料跳掉。

**「沒解到東西」跟「沒發生」不一樣。** 四種格式都是別家工具的私有格式，沒有版本也沒有 schema。
格式一變，parser 不會拋例外，它會**正常跑完、產出零筆事件**。`evaluate_drift()` 就是那個
最小判準：檔案在動 ∧ 視窗內沒有任何事件 ⟹ 漂移。兩個條件缺一不可——沒用過的平台不會有
檔案更新，所以「沒在用」不會被誤報成「壞了」。

## 設定

只有 `discover()` 需要設定，而且只用兩個方法（`get`／`get_path`）：

```python
from coding_agent_transcripts import DictConfig, CONFIG_KEYS

cfg = DictConfig({"watchers.agent_log_watcher.antigravity_logs_path": "~/antigravity"})
print(CONFIG_KEYS)   # 這四個鍵就是全部
```

鍵名的 `watchers.agent_log_watcher.` 前綴帶著第一個使用端的味道。留著不改是為了不讓
既有使用者的設定檔失效；要改會在下一個大版本一起改。

## 來源

從 [OmniContext](https://github.com/dofliu/activityTracker) 抽出來，邊界寫在
[ADR-033](https://github.com/dofliu/activityTracker/blob/main/docs/ADR-033-agent-transcripts-package.md)。
