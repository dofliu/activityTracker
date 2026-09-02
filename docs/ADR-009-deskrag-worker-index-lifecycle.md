# ADR-009：DeskRAG worker 索引生命週期

## Context

DeskRAG 原本以 FastAPI `BackgroundTasks` 執行 async function，但其中包含同步 `os.walk`、檔案解析、FastEmbed、Chroma 寫入與 SQLite 交易。大量資料夾會佔用主服務 event loop，連 Health、dashboard 與採集狀態都無法即時回應。

## Decision

1. Web process 只建立、控制與呈現 `rag_index_jobs`；每個 index、remove-folder、clear-all、audit、rebuild-BM25 工作由 `python -m rag.index_worker` 獨立執行。
2. Index job 預設有 500 檔、50 MB／檔與 25 ms／檔保護邊界；job 控制表提供 cooperative pause、resume 與 cancel。
3. 資料夾移除和全域清空一律需 API `confirm=true`；UI 全域清空另要求輸入 `CLEAR`。兩者只處理 RAG metadata、vectors 與 BM25，不處理原始來源檔或 RAG chat。
4. 刪除後 worker 批次更新 Chroma/BM25，執行 SQLite checkpoint + `VACUUM`，再以 Chroma count、BM25 chunk count、SQLite chunk sum 與 integrity check 建立不含文件內容的 result receipt。
5. Dashboard 不直接呼叫大型 Chroma count 或載入 BM25 pickle。它只讀 SQLite summary 與最近 worker receipt；尚無 receipt 時顯示 `unverified`／「待驗證」。

## Consequences

- 長時間索引不再阻塞主 API，但 worker 仍可能消耗顯著 CPU、RAM、磁碟與本機 embedding 資源；前端需如實呈現 job 狀態與進度。
- 單一資料夾的 Chroma delete 是 logical removal；global clear 透過 collection reset 取得完整 collection-level reclaim。SQLite physical reclaim 由 `VACUUM` 回報 before/after bytes。
- BM25 可由現有 Chroma 重建，修復 sparse/vector mismatch，而不重掃原始資料夾。
- Job result 不保存文件內容、embedding、prompt、response 或任何來源文件路徑；只保存計數、大小、SQLite integrity 與受控 job 狀態。來源檔仍受既有本機資料邊界約束。

## Addendum（2026-09-02）：檢索也離開主服務程序

### Context

原決策只把**寫入型**工作（索引、刪除、驗證、重建 BM25）移到 worker；查詢仍在主服務程序內執行，且 `bm25_service`／`vector_store` 採 lazy load。結果是：索引達 475k chunks 時，**第一次提問**要在主服務內載入 4.4 GB Chroma 與 559 MB BM25 pickle，可能數十秒；載入後這些資料就永久佔住主服務記憶體。2026-09-01 以 60 秒逾時與 `status` 事件緩解介面假死，但根因未解（docs/TODO.md B1）。

### Decision

6. 檢索（Chroma 向量查詢、BM25 評分、query embedding）預設由**常駐檢索 worker** `python -m rag.retrieval_worker` 執行；主服務以 stdin/stdout JSON lines 驅動（`rag/retrieval_client.py`），自己不 import `chromadb`、`fastembed`、`rank_bm25` 或 `jieba`（以乾淨直譯器 import `core.server` 的契約測試守門）。
7. worker 為 lazy 啟動；服務啟動後若已有索引（BM25 pickle 或 Chroma 目錄非空）且 `rag.retrieval.warmup_on_start` 為真，就在背景預熱（載入 BM25、Chroma collection、embedding 模型）並保留不含內容的收據（切片數、各步耗時、worker RSS）。沒有索引時不啟動任何子程序，避免空裝機觸發模型下載。
8. 檢索逾時（60 秒）即 **kill** worker，下一次提問自動重啟；worker 崩潰或回傳錯誤都轉成「不帶文件脈絡照常回答」，SSE 仍保證送出 `done`。重啟次數、最近錯誤、stderr 末幾行在 `GET /api/v1/rag/retrieval/status` 可見。
9. worker 的 stdout 只承載協定訊息（程序啟動即把 fd 1 改接到 stderr）；stdin 關閉即自行退出，不留孤兒程序。worker 不做任何寫入；索引生命週期仍由 `rag.index_worker` 的 job 負責。
10. `rag.retrieval.mode: in_process` 保留舊行為供除錯或極小索引使用。

### Consequences

- 主服務記憶體不再隨索引大小成長；索引載入的等待時間轉移到可觀測的預熱階段，且可由使用者「釋放記憶體」。
- 每次檢索多一次 JSON 序列化與 pipe 往返（毫秒級），相對於向量查詢可忽略。
- `/api/v1/rag/strategies` 改讀靜態目錄 `rag/retrieval/catalog.py`（有測試確認與 registry 一致），`rag/retrieval/__init__.py` 改為 lazy export，任何人新增 retriever 時要同步更新目錄。
- 狀態卡片與 API 只描述程序狀態與載入計數，不宣稱檢索結果正確或索引完整；一致性仍以 §5 的 worker 驗證收據為準。

