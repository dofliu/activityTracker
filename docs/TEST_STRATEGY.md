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
- Data lifecycle：online backup 通過 integrity；restore drill 的 table list、schema fingerprint、row counts 必須相同，且不得保留或覆蓋 live DB。
- Versioned migration：fresh DB 與 legacy DB 到達相同 latest version；舊資料與 lifecycle backfill 保留；重跑不新增 receipt。
- Migration safety：checksum mismatch、未知較新版本與 migration exception 都必須 fail-closed；失敗版本不得寫入 applied receipt。
- Open Loop API：resolve、reopen、stale、supersede；actionable list 只含 open。
- Packaging runtime：`OMNICONTEXT_HOME`／`OMNICONTEXT_CONFIG`、relative data path、config template、Web/Extension assets 與 `init` writable-home contract。

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
python main.py backup
python main.py restore-drill
python main.py migration-status
python main.py assets-status
python main.py extension-path
```

## P6 Wheel/SDist Release Matrix

### Artifact content

- Wheel 必須包含 config template、Web Dashboard、Extension Monitor、Browser Extension 與 console entry point。
- SDist 必須包含 README、STATUS、ADR/usage docs、source assets 與 build manifest。
- Wheel/sdist 不得包含 `config.yaml`、SQLite database、API keys、local reports 或 private receipts。
- `python scripts/verify_release_artifacts.py <dist-dir>` 必須產生 `passed` receipt 與 SHA-256。

### Installed package

- Fresh wheel install 必須在 package 外建立 writable application home、config 與 schema `7/7` database。
- 上一版 wheel upgrade 必須確實替換 distribution version，保留 package 外的 data boundary。
- Health、Dashboard、Extension Monitor 與 `/static/app.js` 必須回傳 HTTP 200。
- `assets-status` 與 `extension-path` 必須在 source checkout／installed wheel 都可用。
- Windows isolated venv 通過不能替代 macOS/Linux CI／實機與 unsupported-feature graceful degradation。

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
- `POST /api/v1/extension/heartbeat` 即使沒有 Origin 也必須要求 token；payload 不得包含 URL、Prompt、Response 或 token。
- Heartbeat receipt 必須區分 recent/stale；離開 SQLAlchemy session 後仍可安全產生 status snapshot。

### Frontend / smoke tests

- localhost `/extension-monitor` 不依賴 `chrome.storage`。
- 主頁顯示「前景使用時間」與 `partial / unavailable`，不使用「工時／生產力」措辭。
- popup 分開顯示 service health 與 token pairing status。
- MV3 background 必須使用 `chrome.alarms`，每個 content script 回報 ready，console 不得輸出 Prompt preview。
- Windows live API 與 dashboard render smoke；macOS/Linux 驗證 `unavailable` graceful degradation。

## P3 Semantic Index / `omni ask` Matrix

- Fresh/legacy DB 必須到 schema 7/7；`semantic_documents` source identity 唯一且 embedding input mode 可追溯。
- AI response 只有 `final_candidate` 可進入 response evidence；partial/legacy response 不得升格。
- Incremental rerun 必須以 content hash/model 跳過未變來源；成功 batch 原子提交，中斷後可續跑。
- Ollama URL 預設 loopback-only；remote URL 在 `allow_remote=false` 時 fail-closed。
- Retrieval 每筆保留 SQLite `source_ref`、project、timestamp、trust、score；similarity 不作 truth/coverage claim。
- 真實 Windows Alpha receipt：`bge-m3` 1024 維、4,102/4,102、3 筆可見 `ascii_fallback`、第二次 `0 changed / 4102 unchanged`；`llama3.1:8b` 回答含 `[S1]` citations。

## Platform CI Matrix

`.github/workflows/platform-matrix.yml` 在 Windows、Ubuntu、macOS 的 Python 3.10/3.12 執行 pytest、compileall、Extension JS syntax、build、artifact privacy/content 與 installed writable-home/API/assets smoke。Workflow YAML 存在不等於通過；必須保存 GitHub Actions run/job 結果。
