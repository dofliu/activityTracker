# OmniContext P2.5 Test Strategy

## 目標

測試優先保護五條 business-critical 路徑：Local API 安全、transcript pairing、ingestion idempotency、Open Loop lifecycle、跨平台降級。測試不得讀寫正式 `omni_context.db`。

## Testing Pyramid

### Unit tests

- Origin allowlist、secret redaction、URL/path validation。
- Codex 同一 turn 多個 assistant messages 選擇最後有效回應。
- 同 conversation 重複 prompt 以 stable `turn_key` 分開。
- Open Loop title normalization、fingerprint 與狀態轉換。
- Windows/macOS/Linux platform command 組裝不得使用 shell string。

### Integration tests

- FastAPI：惡意 Origin 為 403；local Origin 正常；config response 遮蔽 secrets。
- SQLite：schema migration 後舊資料保留；checkpoint 成功才更新。
- Open Loop API：resolve、reopen、stale、supersede；actionable list 只含 open。

### Smoke tests

- `python main.py status`：live service 存在時回報 live 狀態；無服務時安全 fallback。
- `python main.py now` 與 `python main.py resume activityTracker --json` 可讀取既有資料。
- Windows 實機 watcher 啟動、停止與單一實例；macOS/Linux 先驗證 import 與 unsupported feature graceful degradation。

## Data Integrity Gates

- `response_non_null >= response_nonempty >= response_final_candidate`。
- placeholder response 不得計入 final candidate。
- checkpoint 解析失敗時不得更新 `mtime_ns` / `size_bytes`。
- 每個新 ingestion row 必須具備 timestamp、platform、prompt；session parser row 必須另具 `turn_key` 與 source provenance。
- lifecycle 狀態只允許 `open / stale / resolved / superseded`。

## Coverage Target

- P2.5 新增的 pure functions：branch coverage 90% 以上。
- Security boundary、transcript pairing、lifecycle：每個 critical rule 至少一個 positive 與一個 negative test。
- 不追求整體行數百分比；優先確保 contract 與失敗路徑。

## Commands

```powershell
python -m pytest -q
python main.py status
python main.py now
python main.py resume activityTracker --turns 3 --json
```

## P2.6 Extension Monitor 與 Usage Milestone Matrix

### Unit tests

- Config-driven app/title classification；title rule 優先於 generic process rule。
- Interval clipping、exact dedupe、overlap resolution、跨午夜拆分與空集合。
- AI interaction count 與 foreground duration 分離。
- Milestone 門檻、quiet hours、disabled state 與 notification message tone。

### Integration tests

- `GET /api/v1/usage/today` 回傳 interface rows、coverage、goal 與 milestone state。
- `POST /api/v1/usage/milestones/evaluate` 在相同日期／門檻重送時不建立第二筆 receipt。
- `GET /api/v1/extension/status` 不洩漏 token，並區分 configured、enabled、observed event。
- Extension origin 未帶或帶錯 token 時 pairing probe 為 403；正確 token 才可通過。

### Frontend / smoke tests

- localhost `/extension-monitor` 不依賴 `chrome.storage`。
- 主頁顯示「前景使用時間」與 `partial / unavailable`，不使用「工時／生產力」措辭。
- popup 分開顯示 service health 與 token pairing status。
- Windows live API 與 dashboard render smoke；macOS/Linux 驗證 `unavailable` graceful degradation。
