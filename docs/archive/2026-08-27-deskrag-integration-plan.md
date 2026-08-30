> **📦 歸檔文件**（2026-08-30 歸檔）：本文件為 DeskRAG 整合（P7）動工前的一次性規劃書，內容反映 2026-08-27 時點的提案狀態，僅供歷史參考。實際完成成果見 [完成報告](2026-08-27-deskrag-integration-walkthrough.md) 與 [ROADMAP.md P7 章節](../../ROADMAP.md)。

# DeskRAG 整合至 OmniContext (activityTracker) 單一伺服器規劃書

本規劃書旨在將 `D:\Project_CodingSimulation\PersonalHelper\deskRAG` 完整的本地文件 RAG 知識庫系統無縫重構並整合至目前的 `OmniContext (activityTracker)` 專案中，**達成單一進程、單一伺服器（Single Server）同時提供「工作脈絡追蹤」與「實體知識庫檢索」的統一智慧助理系統**。

---

## 系統定位與架構願景

### 1. 雙層融合架構（Two-Tier Unified Architecture）
* **脈絡層（Activity Context Layer）**：由 OmniContext 負責追蹤跨平台 AI 對話、Git Commits、檔案修改時間線、視窗專注時間與 Open Loops 待辦事項。
* **知識庫層（Document Knowledge Layer）**：由移植後的 DeskRAG 核心負責針對指定目錄進行全格式檔案解析（PDF/Word/PPT/Excel/程式碼）、FastEmbed 本地向量化、Jieba+BM25 關鍵字索引與 Hybrid RRF 混合檢索。
* **統一調度中樞（Omni-Ask & Chat Gateway）**：統一在 `http://127.0.0.1:8765` 運作，提供多模型（OpenAI, Claude, Gemini, Ollama）SSE 即時打字機串流問答，支援「全景工作脈絡 + 原始文件頁碼切片」的雙重精確引文。

```
+---------------------------------------------------------------------------------------+
|                      OmniContext 統一 Web 儀表板 (http://127.0.0.1:8765)              |
|        [進行中工作] [即時情報流] [日報回顧] [時間統計] [監控配置] [📚 知識庫與 RAG 對話]        |
+-------------------------------------------+-------------------------------------------+
                                            | REST API & SSE 打字機串流
+-------------------------------------------v-------------------------------------------+
|                          OmniContext FastAPI 統一伺服器                               |
+-------------------------------------------+-------------------------------------------+
|  [活動採集器 Watchers & Aggregator]       |  [DeskRAG 文件知識庫子系統 (rag/)]         |
|  • AI 對話 / Git / 檔案異動 / 視窗心跳    |  • 遞迴掃描器 & 增量 MD5 變更感知         |
|  • 專案歸戶 & 日報生成 & 桌面通知         |  • 多格式解析 (PDF/Office/Code/OCR)       |
|                                           |  • 階層滑動切分器 (Chunker)               |
|                                           |  • 模組化檢索器 (Hybrid RRF / BM25 / Vec) |
+-------------------------------------------+-------------------------------------------+
|                                  統一儲存層                                           |
|  • 本機 SQLite (omni_context.db) -> 活動事件表 + RAG 資料夾/檔案狀態 + 對話歷史表記錄 |
|  • 本機 ChromaDB (data/chroma/) -> FastEmbed 稠密向量庫                               |
|  • 本機 BM25 (data/bm25_index.pkl) -> Jieba 稀疏關鍵字倒排索引庫                      |
+---------------------------------------------------------------------------------------+
```

---

## User Review Required

> [!IMPORTANT]
> **主要變更與審查重點**：
> 1. **單一服務與連接埠**：所有 DeskRAG 的 API 將整併於 OmniContext 的 FastAPI 實例（預設 `8765` 埠），啟動 `python main.py web` 即可同時享有活動追蹤與知識庫問答，不再需要另外執行 DeskRAG 的 `run.bat`。
> 2. **套件依賴增加**：OmniContext 的 `requirements.txt` 將加入 DeskRAG 的核心依賴（`chromadb`, `fastembed`, `rank-bm25`, `jieba`, `pymupdf`, `python-docx`, `python-pptx`, `openpyxl`, `pandas`, `pillow`, `sse-starlette`, `rapidocr-onnxruntime`）。
> 3. **資料庫遷移與整合**：DeskRAG 的 4 張資料表（`rag_indexed_folders`, `rag_indexed_files`, `rag_chat_sessions`, `rag_chat_messages`）將透過 SQLAlchemy 併入 `omni_context.db`，享受單一資料庫實例鎖與自動備份。

---

## Open Questions

> [!NOTE]
> 1. **前端整合形式**：
>    - **選項 A（推薦）**：在 OmniContext 現有原生 Web 介面（`web/index.html`, `web/app.js`）直接新增第 6 個專屬分頁「📚 知識庫與 RAG 對話」，包含資料夾管理、即時進度條、檔案清單與 SSE 串流問答，風格與 OmniContext 深淺色主題完美一致。
>    - **選項 B**：同時保留 DeskRAG 原版 React/Vite 前端（打包後掛載於 `/deskrag/` 子路由），讓您在同一個伺服器下也可隨時切換至 DeskRAG 原生獨立視圖。
> 2. **OCR 圖片文字辨識**：
>    - `rapidocr-onnxruntime` 可解析圖片文字，若無圖片 OCR 需求可設為選用套件以加速安裝。

---

## Proposed Changes

### 1. 知識庫核心子系統 (`rag/`)

將 DeskRAG 的後端核心邏輯遷移至 `activityTracker/rag/`，重構路徑與設定依賴，使其與 OmniContext 的 `runtime_paths` 與 `config.yaml` 深度結合。

#### [NEW] [rag/\_\_init\_\_.py](file:///d:/Project_CodingSimulation/PersonalHelper/activityTracker/rag/__init__.py)
- 初始化 `rag` 模組導出。

#### [NEW] [rag/config.py](file:///d:/Project_CodingSimulation/PersonalHelper/activityTracker/rag/config.py)
- 定義 RAG 專屬常數（支援副檔名、系統忽略黑名單、切分參數、預設 FastEmbed 模型）。
- 與 OmniContext `core/config.py` 整合，動態讀取 `config.yaml` 內的 `rag` 設定。

#### [NEW] [rag/parsers/](file:///d:/Project_CodingSimulation/PersonalHelper/activityTracker/rag/parsers)
- `base.py`：解析器基礎抽象類別。
- `parser_hub.py`：多格式檔案解析路由中樞。
- `pdf_parser.py`：PyMuPDF 高效能 PDF 頁面解析器。
- `office_parser.py`：Word (`.docx`)、PowerPoint (`.pptx`)、Excel (`.xlsx`/`.csv`) 解析器。
- `text_parser.py`：Markdown、TXT 與各類原始碼檔解析。
- `image_parser.py`：RapidOCR 圖片光學字元辨識。

#### [NEW] [rag/chunker.py](file:///d:/Project_CodingSimulation/PersonalHelper/activityTracker/rag/chunker.py)
- 階層滑動窗口切分器，為每個 chunk 標註精準的來源資訊（頁碼 `page`、投影片編號 `slide`、工作表名稱 `sheet`、章節標題 `title`）。

#### [NEW] [rag/embeddings.py](file:///d:/Project_CodingSimulation/PersonalHelper/activityTracker/rag/embeddings.py)
- 本地 FastEmbed (ONNX `bge-small-zh-v1.5`)、Ollama、OpenAI 向量嵌入服務。

#### [NEW] [rag/vector_store.py](file:///d:/Project_CodingSimulation/PersonalHelper/activityTracker/rag/vector_store.py)
- 本地 ChromaDB 向量資料庫封裝，支援增量 Upsert、批次寫入、單檔刪除與餘弦相似度檢索。
- 自動將向量存於 `runtime_data_root / "chroma"`。

#### [NEW] [rag/retriever.py](file:///d:/Project_CodingSimulation/PersonalHelper/activityTracker/rag/retriever.py)
- Jieba 中文分詞 + BM25 稀疏關鍵字索引服務，支援本地 Pickle 持久化與增量更新。

#### [NEW] [rag/retrieval/](file:///d:/Project_CodingSimulation/PersonalHelper/activityTracker/rag/retrieval)
- 檢索策略模式（Strategy Pattern）：
  - `base.py`：`BaseRetriever` 與 `CitationSource` 結構定義。
  - `registry.py`：策略註冊表（支援動態擴充與列舉）。
  - `hybrid_rrf_retriever.py`：倒數排名融合（RRF）混合檢索。
  - `weighted_fusion_retriever.py`：線性加權融合檢索。
  - `vector_retriever.py`：純稠密向量檢索。
  - `bm25_retriever.py`：純 BM25 關鍵字檢索。

#### [NEW] [rag/scanner.py](file:///d:/Project_CodingSimulation/PersonalHelper/activityTracker/rag/scanner.py)
- 本地目錄遞迴掃描引擎、MD5 增量變更感知、非同步背景工作排程與即時進度狀態廣播。

#### [NEW] [rag/llm_gateway.py](file:///d:/Project_CodingSimulation/PersonalHelper/activityTracker/rag/llm_gateway.py)
- 多模型調度器（OpenAI, Claude, Gemini, Ollama）與 SSE 串流打字機產生器。

---

### 2. 資料庫與模型層 (`core/`)

#### [MODIFY] [core/models.py](file:///d:/Project_CodingSimulation/PersonalHelper/activityTracker/core/models.py)
- 新增 RAG 相關 SQLAlchemy 資料表模型：
  - `RAGIndexedFolder` (`rag_indexed_folders`)
  - `RAGIndexedFile` (`rag_indexed_files`)
  - `RAGChatSession` (`rag_chat_sessions`)
  - `RAGChatMessage` (`rag_chat_messages`)

#### [MODIFY] [core/database.py](file:///d:/Project_CodingSimulation/PersonalHelper/activityTracker/core/database.py)
- 確保新模型在 `init_db()` 時自動建立或遷移，並支援 RAG 所需的 Helper 查詢。

---

### 3. API 路由與伺服器整合 (`core/server.py`)

#### [MODIFY] [core/server.py](file:///d:/Project_CodingSimulation/PersonalHelper/activityTracker/core/server.py)
- 整合 DeskRAG 的所有 API 路由，掛載於 `/api/v1/rag/`：
  - `GET /api/v1/rag/folders`：列出已索引資料夾
  - `POST /api/v1/rag/folders`：新增監控資料夾並觸發掃描
  - `DELETE /api/v1/rag/folders/{id}`：移除資料夾並清理 ChromaDB/BM25 索引
  - `POST /api/v1/rag/scan`：手動觸發重新掃描與索引
  - `GET /api/v1/rag/progress`：取得當前掃描進度、處理檔案數與日誌
  - `GET /api/v1/rag/files`：分頁查詢已索引檔案清單與狀態
  - `POST /api/v1/rag/open-file`：呼叫 Windows 檔案總管並選中目標檔案
  - `GET /api/v1/rag/file-content`：解析並預覽特定檔案切片
  - `POST /api/v1/rag/chat`：SSE 即時串流問答（含 Citations 引文與 Token 串流）
  - `GET /api/v1/rag/chat/sessions`：取得對話歷史列表
  - `POST /api/v1/rag/chat/sessions`：建立/更新對話 Session
  - `DELETE /api/v1/rag/chat/sessions/{id}`：刪除對話 Session
  - `GET /api/v1/rag/strategies`：取得可用的檢索策略列表

---

### 4. 設定檔整合 (`config.yaml` & `config.example.yaml`)

#### [MODIFY] [config.example.yaml](file:///d:/Project_CodingSimulation/PersonalHelper/activityTracker/config.example.yaml)
- 新增 `rag` 區塊設定：
  ```yaml
  rag:
    enabled: true
    chroma_dir: "data/chroma"
    bm25_path: "data/bm25_index.pkl"
    embedding_provider: "fastembed"   # fastembed, ollama, openai
    embedding_model: "BAAI/bge-small-zh-v1.5"
    default_top_k: 6
    default_hybrid_alpha: 0.65
    chunk_size: 800
    chunk_overlap: 150
    active_provider: "ollama"         # ollama, gemini, claude, openai
    active_model: "llama3.2:latest"
  ```

---

### 5. 前端 Web 儀表板 (`web/`)

#### [MODIFY] [web/index.html](file:///d:/Project_CodingSimulation/PersonalHelper/activityTracker/web/index.html)
- 頂部導航列新增 `📚 知識庫與 RAG 對話 (Knowledge & Chat)` 標籤。
- 新增 RAG 對話與資料夾管理面板：
  - 左側：已索引目錄管理（新增目錄、一鍵掃描、即時進度條、檔案清單手風琴）。
  - 右側：AI 對話視窗（模型切換、檢索策略下拉選單、Top-K / Alpha 調整、即時打字機訊息流、引用來源卡片與「在總管開啟」按鈕）。

#### [MODIFY] [web/app.js](file:///d:/Project_CodingSimulation/PersonalHelper/activityTracker/web/app.js)
- 實作 SSE 串流解析客戶端（處理 `event: citations`, `event: message`, `event: done`）。
- 實作資料夾新增、刪除、掃描進度定時輪詢與引文卡片點擊喚起 Windows 總管。
- 整合 i18n 繁中/英文雙語系對應。

#### [MODIFY] [web/style.css](file:///d:/Project_CodingSimulation/PersonalHelper/activityTracker/web/style.css)
- 增加 RAG 聊天氣泡、引文來源標籤（頁碼/投影片/相關度得分）、進度指示條與響應式排版樣式。

---

### 6. 相依套件更新 (`requirements.txt` & `pyproject.toml`)

#### [MODIFY] [requirements.txt](file:///d:/Project_CodingSimulation/PersonalHelper/activityTracker/requirements.txt)
- 加入必要套件：
  ```txt
  chromadb>=0.4.24
  fastembed>=0.2.7
  rank-bm25>=0.2.2
  jieba>=0.42.1
  pymupdf>=1.24.0
  python-docx>=1.1.0
  python-pptx>=0.6.23
  openpyxl>=3.1.2
  pandas>=2.2.0
  pillow>=10.2.0
  sse-starlette>=2.0.0
  rapidocr-onnxruntime>=1.3.8
  ```

---

## Verification Plan

### Automated Tests
1. **RAG 核心解析與檢索單元測試**：
   - 建立 `tests/test_rag_parsers.py`：測試 TXT, Markdown, PDF, Office 檔案解析。
   - 建立 `tests/test_rag_chunker.py`：驗證滑動窗口切分與 Metadata 頁碼標註。
   - 建立 `tests/test_rag_retrieval.py`：驗證 FastEmbed 本地向量化、BM25 關鍵字檢索與 Hybrid RRF 融合效果。
2. **API 整合測試**：
   - 建立 `tests/test_rag_api.py`：測試 `/api/v1/rag/folders`、`/api/v1/rag/scan`、`/api/v1/rag/chat`（SSE 串流）與 `/api/v1/rag/open-file`。
3. **OmniContext 原有測試回歸**：
   - 執行 `pytest tests/` 確保現有活動追蹤、Git 掃描、AI 對話採集測試 100% 通過。

### Manual Verification
1. **一鍵啟動測試**：
   - 執行 `python main.py web`，確認伺服器於 `http://127.0.0.1:8765` 正常啟動，且無多進程/多伺服器衝突。
2. **目錄索引流程驗證**：
   - 在 Web UI 進入「📚 知識庫與 RAG 對話」頁籤，添加本機資料夾（例如 `sample_knowledge_base` 或本機專案目錄）。
   - 觀察即時進度條由 0% -> 100%，確認檔案總數與 ChromaDB 向量切片數量正確。
3. **問答與引文卡片驗證**：
   - 在對話框輸入測試問題，驗證 SSE 即時打字機串流回覆。
   - 確認引文卡片正確顯示來源檔名、頁碼/章節與匹配度得分。
   - 點擊「在總管開啟」，確認 Windows 檔案總管正確被喚起並選中目標檔案。
