# ADR-023：一份活動記憶——活動進 `semantic_index`，文件留給 DeskRAG

- 狀態：**Accepted**（2026-09-16 起草並於同日實作，TODO D7）
- 關聯：[ADR-005](ADR-005-local-semantic-index-and-ask.md) 本機 semantic index、[ADR-009](ADR-009-deskrag-worker-index-lifecycle.md) DeskRAG 索引生命週期與檢索 worker、[ADR-012](ADR-012-secretary-memory.md) 秘書記憶區、[ADR-001](ADR-001-p2-5-trust-boundary.md) 本機優先邊界
- 依據：[docs/REVIEW-2026-09-16-project-assessment.md](REVIEW-2026-09-16-project-assessment.md) §4.3「兩套向量記憶」

## Context

同一批活動目前被 embedding **兩次**，存在兩個不同的引擎裡：

| 來源 | `core/semantic_index`（核心） | `rag/activity_indexer`（DeskRAG） |
| :--- | :---: | :---: |
| AI turn（`ai_prompt_events`） | ✅ | ✅ |
| Git commit（`git_activity_events`） | ✅ | ✅ |
| 專案狀態（`project_states`） | ✅ | ✅ |
| 未結事項（`open_loops`） | ✅ | ✅ |
| 檔案事件（`file_activity_events`） | ✅ | ❌ |
| 秘書筆記（`secretary_notes`） | ❌ | ✅ |
| 時段微摘要（`activity_micro_summaries`） | ❌ | ✅ |
| 秘書寫出的報告檔（Handoff／同步報告／STATUS 草稿／每日入口） | ❌ | ✅ |

兩邊的技術堆疊也不同：核心用 **Ollama embedding ＋ SQLite BLOB ＋ 純 Python 餘弦**（零重依賴）；
DeskRAG 用 **Chroma ＋ FastEmbed ＋ BM25/jieba**，2026-09-16 起是選用依賴 `omnicontext[rag]`（TODO D1）。

代價有四項，都不是假設：

1. **同一筆活動算兩次 embedding**、佔兩份磁碟；改一次 prompt 文字要兩邊都重算才一致。
2. **「活動」的定義在兩邊已經不一樣**（見上表）：檔案事件只有核心看得到，秘書筆記與微摘要只有 RAG 看得到。
   同一個問題問 `omni ask` 與問知識庫對話，會拿到不同的證據集合，而且沒有任何東西會說出這件事。
3. **記憶綁在選用依賴上**：D1 之後，沒裝 `[rag]` extra 的安裝（預設安裝路徑之一，176 MB）等於少一半活動記憶。
   「秘書記得你做過什麼」是核心承諾，不該取決於有沒有裝 550 MB 的文件索引堆疊。
4. 兩套各自的重建／清理／狀態端點，兩份要維護的失敗模式。

## Decision

**依「這東西是什麼」分工，不依「哪個引擎剛好收了它」：**

| 種類 | 存哪裡 | 為什麼 |
| :--- | :--- | :--- |
| **活動記憶**：AI turn、commit、檔案事件、專案狀態、未結事項、秘書筆記、時段微摘要 | `core/semantic_index`（**核心**） | 都是 OmniContext 自己產生、有 provenance、可回溯到單一 SQLite row 的紀錄；每筆只 embedding 一次；沒有 `[rag]` extra 也必須能用 |
| **文件**：使用者指定資料夾裡的檔案、秘書自己寫出的報告 markdown | **DeskRAG**（選用 `[rag]`） | 是檔案：要分塊、要中文斷詞、要 BM25 與向量混合檢索——那正是 DeskRAG 的工作 |

三條具體規則：

1. `rag/activity_indexer.py` 不再為上述七種活動實體建切片；它縮成 `rag/report_indexer.py`，**只**索引秘書寫出的報告檔（白名單不變）。
2. `core/semantic_index.collect_source_documents` 補上 `secretary_note` 與 `micro_summary` 兩種來源——原本只有 RAG 看得到的東西，改由核心索引，**沒有任何一種來源在這次搬家中消失**。
3. 知識庫對話要引用活動時，查的是**同一份**核心索引（`semantic_search`），不再自己存一份；`omni ask` 與
   `/api/v1/rag/chat` 從此引用同一組活動證據。

### 反向選項（全部進 Chroma）為什麼不採用

把核心索引廢掉、活動也進 Chroma，一樣能消除重複，而且 BM25 對中文關鍵字確實比餘弦好。
但那會讓「秘書的記憶」變成選用依賴的一部分：沒裝 `[rag]` 的安裝連「找相似的歷史工作」都不能用，
`omni ask`、問候卡的相關脈絡、`/api/v1/context/related` 全部得跟著關掉。記憶是核心承諾，文件搜尋不是。

## 邊界（不變）

- Embedding 一律 loopback-only（`semantic_index.allow_remote` 預設 false）；不因為這次合併而多送任何東西出本機。
- 只索引**既有的**本機 evidence rows，不新增任何資料類別、不掃描未授權檔案內容。
- AI turn 的 response 文字仍只在 `final_candidate` 時納入（trust status 照舊寫進每筆向量）。
- 主服務程序仍不 import chromadb／fastembed／rank_bm25／jieba（ADR-009、TODO D1）。
- 檢索結果仍逐筆帶 `source_ref`、`trust_status` 與 `embedding_input_mode`，可回溯到原始 row。

## 遷移

- 已經進了 Chroma／BM25 的活動切片是**重複資料**：報告同步工作會依 `source_type` 一次性清掉它們，收據寫出刪了幾筆。
- 秘書筆記與微摘要改由核心索引；第一次 `omni index`（或排程重建）會把它們補進 `semantic_documents`。
- `semantic_documents` 的 schema 不變（`source_type` 本來就是字串欄位），**不需要新的 migration**。
- 對話端點、job 型別與路由路徑全部不變；使用者看到的按鈕與 API 形狀不變。

## Consequences

- 同一筆活動只 embedding 一次；「活動」只有一個定義，兩個入口講同一組證據。
- 沒裝 `[rag]` 的安裝也有完整的活動記憶；裝了 `[rag]` 的多的是**文件**檢索，而不是第二份活動記憶。
- 代價：知識庫對話要活動脈絡時多一次本機 embedding 呼叫（Ollama 沒開就只少了活動段，文件段照常，並如實說出來）；
  中文關鍵字檢索目前只有文件段享有 BM25，活動段是純向量——這一點如實記在這裡，之後若有需要再談是否給核心索引加關鍵字層。
