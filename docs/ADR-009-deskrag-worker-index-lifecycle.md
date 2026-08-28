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
