# ADR-005：Local Semantic Index 與 `omni ask`

- 狀態：Accepted / Alpha
- 日期：2026-08-25

## 決策

P3-2 使用 loopback Ollama embedding 建立 SQLite `semantic_documents` 索引。索引來源限於 OmniContext 已採集並有原始 row identity 的 AI turns、Git commits、file activity metadata、Open Loops 與 Project State；不因建立索引而額外開啟檔案正文或擴大採集範圍。

每筆索引保存 `source_type/source_id/source_ref`、project、source timestamp、trust status、content hash、embedding model、dimensions、float32 BLOB 與 `embedding_input_mode`。`content_hash + model` 相同時增量執行跳過；每個成功 batch 各自原子提交，可在 Ollama 中斷後續跑。

`omni ask` 先做 cosine similarity retrieval，再選擇是否由本機 Ollama 生成答案。答案 prompt 要求使用 `[S1]` 引用；CLI 永遠列出原始 SQLite `source_ref`、similarity score 與 trust status。

## 安全與可信度邊界

- 預設只允許 `127.0.0.1`、`localhost` 或 `::1`；除非明確設定 `allow_remote: true`，否則拒絕 remote embedding/generation URL。
- `final_candidate` AI response 才能進入 response evidence；partial、missing 與 legacy response 只索引 prompt 與其 trust label。
- Similarity 只表示向量相近，不證明來源真實、完整或語意正確。
- Ollama 對特定 Unicode input 產生 NaN 時，先隔離單筆並縮短；最後才用 `ascii_fallback` 或 `metadata_only`，且 `embedding_input_mode` 必須保存此降級。
- Index 可由原始資料重建，不是 canonical source；備份/rollback 的 canonical boundary 仍以事件表與 migration receipt 為準。

## 驗收證據（2026-08-25）

- `bge-m3:latest`：1024 dimensions。
- 全量來源：4,102；成功索引：4,102；failure：0。
- 3 筆來源使用 `ascii_fallback`，沒有靜默遺失。
- 第二次增量執行：`indexed=0`、`unchanged=4102`。
- retrieval-only 與 `llama3.1:8b` 本機回答均回傳 `[S1]...` provenance。
