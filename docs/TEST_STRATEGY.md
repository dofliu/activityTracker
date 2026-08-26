# OmniContext P2.5 Test Strategy

## 目標

測試優先保護五條 business-critical 路徑：Local API 安全、transcript pairing、ingestion idempotency、Open Loop lifecycle、跨平台降級。測試不得讀寫正式 `omni_context.db`。

## Testing Pyramid

### Unit tests

- Origin allowlist、secret redaction、URL/path validation。
- Codex 同一 turn 多個 assistant messages 選擇最後有效回應。
- Claude Code 與 Claude Desktop 共用 user/assistant boundary parser，但 platform provenance 與 stable turn key 必須分離。
- 每個 Agent source 都是獨立 fault boundary；Claude Desktop `PermissionError` 不得阻止同輪 Codex、Claude Code 與 Antigravity scan。
- Window probe 持續 unavailable 達設定門檻後才降級，成功 probe 必須恢復 healthy；diagnostics 不得含 window title 或 exception message。
- Agent source diagnostics 必須保留成功／失敗狀態與 sanitized error code，不得洩漏本機 path。
- Windows 超長 Claude Desktop session path 必須以 extended path 可讀；一般 cloud-chat cache 只能標示 detected/unparsed。
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
- Restart E2E 必須比較重啟前後 `last_events`、event count、usage `data_updated_at` 與 Dashboard render；thread `running` 或 Health API 200 不能替代資料實際前進。
- Dashboard 必須區分 `8/8 CONTRACT` 與 `RUNTIME OK / N DEGRADED`；degraded 模擬只操作 DOM，不寫正式資料庫，並在 494px 驗證無 page-level horizontal overflow 與 console error。

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
- Secret resolver 優先使用目前 Process 環境，Windows 才允許 fallback 到 User／Machine registry；無效的環境變數名稱必須 fail-closed。

### Integration tests

- `GET /api/v1/usage/today` 回傳 interface rows、coverage、goal 與 milestone state。
- `POST /api/v1/usage/milestones/evaluate` 在相同日期／門檻重送時不建立第二筆 receipt。
- `GET /api/v1/extension/status` 不洩漏 token，並區分 configured、enabled、observed event。
- `GET /api/v1/capture/status` 必須分開回傳 Desktop Focus、Web Capture、Transcript，且不得輸出 prompt、response、URL 或本機 path。
- `GET /api/v1/llm/status` 只能回傳 configured、source 與環境變數名稱，不得回傳 API key；Windows 長時間執行的舊 Process 仍可安全偵測後來建立的 User／Machine key。
- Extension origin 未帶或帶錯 token 時 pairing probe 為 403；正確 token 才可通過。
- `POST /api/v1/extension/heartbeat` 即使沒有 Origin 也必須要求 token；payload 不得包含 URL、Prompt、Response 或 token。
- Heartbeat receipt 必須區分 recent/stale；離開 SQLAlchemy session 後仍可安全產生 status snapshot。
- Live verification baseline 只保留在目前 server process，不寫入 SQLite；server restart 後舊 verification ID 必須失效。
- 歷史 event、response、Content Ready 與 heartbeat 不得使新 verification run 通過；每個選定平台都必須在開始後新增 Content Ready timestamp、event 與非空 response。
- Verification timeout 必須 fail-closed；API request 採 `extra=forbid`、只允許 localhost dashboard，且 receipt 不得輸出 token、URL、Prompt、Response 或本機 path。

### Frontend / smoke tests

- localhost `/extension-monitor` 不依賴 `chrome.storage`。
- 主頁顯示「前景使用時間」與 `partial / unavailable`，不使用「工時／生產力」措辭。
- 主頁 `DATA CAPTURE` 必須以緊湊的 `FOCUS / WEB / LOG` 矩陣顯示，不得以 Desktop Focus 的 observed 狀態冒充 conversation transcript coverage。
- 主頁不得重複顯示完整 Extension Monitor；token、heartbeat 與逐站診斷只保留在 `/extension-monitor`。
- popup 分開顯示 service health 與 token pairing status。
- MV3 background 必須使用 `chrome.alarms`，每個 content script 回報 ready，console 不得輸出 Prompt preview。
- Extension Monitor 必須支援逐站選擇、5 秒 polling、RUNNING／PASS／FAILED、event／response delta 與 JSON receipt 下載。
- 監控配置的通用欄位寬度規則不得套用到 checkbox；desktop、tablet 與窄螢幕都必須維持水平標籤且無水平 overflow。
- 494px live-verification smoke 必須維持單欄、無頁面水平 overflow，且 console 無 error／warning。
- Windows live API 與 dashboard render smoke；macOS/Linux 驗證 `unavailable` graceful degradation。

## P3 Semantic Index / `omni ask` / Context Memory Matrix

- Fresh/legacy DB 必須到 schema 7/7；`semantic_documents` source identity 唯一且 embedding input mode 可追溯。
- AI response 只有 `final_candidate` 可進入 response evidence；partial/legacy response 不得升格。
- Incremental rerun 必須以 content hash/model 跳過未變來源；成功 batch 原子提交，中斷後可續跑。
- Ollama URL 預設 loopback-only；remote URL 在 `allow_remote=false` 時 fail-closed。
- Retrieval 每筆保留 SQLite `source_ref`、project、timestamp、trust、score；similarity 不作 truth/coverage claim。
- Derived session 必須以 project + inactivity gap deterministic 分群；跨 project／超過 gap 必須切開，同一 session 成長時 ID 不變。
- Session item 必須保留 `source_ref` 與 trust status；Window focus 無 canonical project 時明確排除，span 不得宣稱為工時或專注度。
- Related History query 不持久化、不 fallback 到 cloud；結果保留 score/source/trust，門檻以下必須回報 `no_strong_match` 而不是「歷史不存在」。
- API request 採 `extra=forbid`；Ollama/index unavailable 時回傳明確 503，Dashboard graceful degradation。
- 真實 Windows Alpha receipt：`bge-m3` 1024 維、4,102/4,102、3 筆可見 `ascii_fallback`、第二次 `0 changed / 4102 unchanged`；`llama3.1:8b` 回答含 `[S1]` citations。
- Context Memory live smoke：24 小時 332 筆可歸戶 observations；session view 正常。Related query top score 約 0.50–0.59，明顯無關 query 約 0.33–0.35；0.50 只作本 corpus Alpha 起點。
- Dashboard E2E 必須以真實點擊觸發 Related History，確認來源／score／trust 可見且 console 無 error；窄螢幕需驗證 Context Memory 收成單欄、頂部操作列換行且整頁無水平 overflow。

## P5-1 Proposal-only Secretary Matrix

- Engine 只直接讀取 Project State、`open` Open Loops 與非敏感 Extension status；不得呼叫會 refresh/write 的 helper。
- 相同 evidence 產生穩定 proposal ID 與 deterministic order；`stale/resolved/superseded` 不得進入 actionable proposal。
- 所有 proposal 必須附 `evidence_refs`，且回應不得包含 Open Loop 原文、prompt/response 全文、token、local path 或 executable command。
- API 必須維持 `execution_available=false`、`cloud_llm_used=false`、`query_persisted=false`，hostile Origin 為 403。
- Dashboard 必須顯示 `PROPOSAL ONLY`、risk level 與「不執行」邊界；桌面及 494px viewport 無 page-level horizontal overflow，console 無錯誤。
- 任何 approve/execute endpoint、Agent Dispatcher 或 mutation 不在本矩陣授權範圍。

## Platform CI Matrix

`.github/workflows/platform-matrix.yml` 在 Windows、Ubuntu、macOS 的 Python 3.10/3.12 執行 pytest、compileall、Extension JS syntax、build、artifact privacy/content 與 installed writable-home/API/assets smoke。2026-08-25 GitHub Actions run `32757498004` 的六個 jobs 全數通過；未來 commit 仍須以各自 run receipt 判定，不沿用本次結果。

2026-08-26 本機完整 `pytest` 為 **85/85**。Collector runtime diagnostics、Extension timestamped Content Ready、response counts 與 fail-closed live verifier contracts 均通過。localhost Claude.ai run 可建立 baseline 並正確維持 RUNNING：歷史 `10 events / 6 responses`、今日 0、heartbeat 0 不會升格為 PASS；487px Monitor 無頁面水平 overflow且 console 無錯誤。這證明 verifier 與 UI 行為，不代表 Extension 已在使用者 Chrome 連線，也不代表全天 continuous coverage。

Claude Desktop Cowork／local-agent Windows incremental scan曾新增 148 turns、125 筆非空 response、117 筆 `final_candidate`，stable parser 重跑不新增重複 turn。這是本機 Cowork／local-agent receipt，不外推為一般 Claude 雲端聊天 coverage。

同批資料完成 incremental semantic index：來源與索引均為 4,380，`indexed=285 / unchanged=4095 / failures=0`；新增數包含掃描期間其他合法來源，不等同全部來自 Claude Desktop。
