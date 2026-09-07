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

## Addendum B（2026-09-07）：明示預熱一律重載；載入的是舊索引不算就緒

### Context

`warmup_in_background()` 原本只要「worker 活著且預熱過」就直接回上一次的收據，不管索引在那之後是否重建過。實機因此出現這一串：使用者在 02 知識庫建索引（`source_chunks` 從 3 變成 4,839、`consistency: matched`），按下「🔥 預熱檢索 worker」，拿回的卻是重建**之前**的收據——`bm25_chunks: 3`、`vector_chunks: 3`，`pid` 與 `warmup_at` 都沒變。驗收中心 A6 只看「`state: ready` 且 chunk 數 > 0」，於是報 ✅ passed，寫著 bm25=3／vector=3。

兩件事都錯：預熱按鈕沒有預熱，而 A6 用一份舊收據判綠。

### Decision

11. `warmup_in_background(reason, force=False)`：**自動路徑**（服務啟動時的 `maybe_warmup_on_start`）維持 idempotent，不重複載入；**使用者明示要求的路徑**（`POST /api/v1/rag/retrieval/warmup`、儀表板按鈕）一律 `force=True`，即使 worker 已就緒也重新載入。預熱進行中仍然只有一個執行緒（重入回目前狀態），重載沿用同一個 worker 程序，不重啟。
12. 驗收中心 A6 在判綠之前比對「worker 記憶體裡的載入計數」與「SQLite 裡的索引來源切片總數」（`rag_indexed_files.chunk_count` 之和，唯讀、不 import 任何索引套件）。載入計數**小於**來源計數就回 `partial`，並直說「worker 載入的是舊索引：記憶體裡 vector=N，但索引現在有 M 個 chunk」。取不到來源計數時退回原本的判定，並把原因記在 evidence，不猜。

### Consequences

- 「建完索引 → 按預熱」現在會真的重載，代價是使用者按下按鈕時要重新等載入（大索引可能數十秒）；狀態卡片在這段時間顯示「預熱中」。
- A6 的完成判準因此比原本嚴格：不只要「有載入東西」，還要**載入的量不小於索引現在的內容**。它仍然不宣稱索引夠大或檢索結果正確——只是不再把過期的收據當成就緒。
- 這與 A6 前一輪的修法（0 chunk 不叫使用者等預熱）同一條原則：**狀態訊息指向錯誤的下一步，和假綠燈是同一類 bug**。
