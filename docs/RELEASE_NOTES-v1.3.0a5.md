# OmniContext v1.3.0a5

> **Personal Alpha（pre-release）**，2026-08-31。這是 OmniContext 第一個公開的 GitHub Release；wheel/sdist 由 CI 於 tag 建置，附 `release-artifacts-receipt.json`（含 SHA-256 與隱私排除驗證）。

## 這是什麼

**OmniContext** 是本機優先（local-first）、provider-neutral 的個人工作脈絡中樞：把跨平台 AI 對話（Claude Code／Claude Desktop／Codex／Antigravity／ChatGPT／Gemini）、Git/GitHub、檔案異動、視窗時間與 Open Loops 收進同一條可追溯時間線，並提供本機語意檢索、DeskRAG 文件知識庫與 provider-neutral Context Handoff。

**定位：Personal Alpha（pre-release）。** 資料留在本機 SQLite；只有主動選用 cloud LLM 摘要時，組裝後的脈絡才會送往該 provider。

## 自 1.2.0 以來的亮點

### 記憶與檢索（P3 / P7）
- **Context Handoff**：`omni resume <project>` 一鍵產出可貼給任何 AI 的接續脈絡。
- **本機語意記憶**：loopback Ollama `bge-m3` 索引 + `omni ask`（附 `[S1]` 來源引用）、Related History 與 derived work sessions——全程不送 cloud。
- **DeskRAG 知識庫（單一伺服器）**：PDF/Office/文字解析、FastEmbed+ChromaDB＋Jieba+BM25 hybrid RRF 檢索、多模型 SSE 串流問答、對話自動標題；大型索引由獨立 worker 執行，主服務只讀 receipt。

### 可信度與驗證（P2.5–P2.7）
- **Append-only SQLite migration 13/13**，checksum fail-closed，升級前自動 verified backup；formal package+DB rollback 已演練。
- **Continuous coverage ledger（新）**：記錄視窗採集器實際被觀測運作的時間段；當日覆蓋率達門檻（預設 0.95）usage coverage 才顯示 `observed`，中斷/休眠永不回補。
- **可驗證背景 Agent 任務時間**：只結算成對 start+final receipt；三平台 live 驗收已 PASS（2026-08-29：codex 29／claude_code 7／claude_desktop 12 筆 completed，union 28,838.971 秒）。
- **Extension 1.3.1 live PASS**（2026-08-31）：已登入 Chrome 實機取得新 heartbeat 與 ChatGPT/Claude.ai 本輪 event+response 收據。
- 驗收工具隨附：`scripts/background_task_live_acceptance.py`、`scripts/extension_live_acceptance.py`。

### 穩健化與維運（P8 / P4.2）
- WAL 自動 checkpoint、90 天歷史修剪、滾動 verified backup。
- 採集器局部容錯（單一損壞 repo／單一 AI 來源不拖垮全系統）與 `supervise_and_heal` 自我修復。
- Dashboard `07 · 系統健康與維護`：診斷矩陣、一鍵修復、維護收據 Action Console。
- **受控本機 Git 同步中心**：逐 repo 確認的 fetch / ff-pull / staged-commit / push；無自動同步、無 force push。

### 主動秘書（P5-1，proposal-only）
- 從本機 Project State、Open Loops 與 Extension 診斷產生附 evidence refs 的建議；不呼叫 cloud、不寫 DB、不執行任何動作（ADR-007 安全契約）。

## 驗證收據摘要

| 項目 | 結果 |
| :--- | :--- |
| Contract tests | 147 passed |
| Schema migration | 13/13（fresh／legacy／live upgrade） |
| 跨平台 CI | Windows/Ubuntu/macOS × Py3.10/3.12 六 jobs 綠（run 32757498004） |
| Windows Toast E2E | passed（隔離 DB、duplicate suppression） |
| Formal rollback | passed（package+DB、WAL/SHM handling） |
| 發佈預演 | Windows（08-25）＋ Linux container（08-30）：build、artifact 隱私排除、fresh install、HTTP smoke 全過 |
| Extension live PASS | 2026-08-31，ChatGPT＋Claude.ai，heartbeat 已驗證 |
| 背景任務 live receipts | 三平台全數 PASS（2026-08-29 資料） |

## 已知限制（誠實邊界）

- 視窗採集、桌面通知與開機排程僅支援 **Windows**；其他平台明確降級。
- Gemini 的 Extension live 驗證未在本輪範圍（僅有歷史觀察 3 event／2 response）。
- 單輪 Extension PASS 與單日背景 receipt 不代表連續或全天 coverage；coverage ledger 的實機全天 `observed` receipt 尚待取得。
- 前景時間≠生產力；similarity≠真實性；P5 executor 維持 blocked（proposal-only）。
- 一般 Claude Desktop 雲端聊天僅偵測 cache 存在，不解析內容。

## 安裝（快速開始）

```console
python -m pip install omnicontext-1.3.0a5-py3-none-any.whl
omnicontext init --watch "/your/project/root"
omnicontext            # 開啟 http://127.0.0.1:8765
```

設定、Extension 配對、備份與故障排查見 [docs/USAGE.md](USAGE.md)；文件總覽見 [docs/INDEX.md](INDEX.md)。

## Artifact 校驗

本 Release 附件包含 CI 建置時由 `scripts/verify_release_artifacts.py` 產出的 `release-artifacts-receipt.json`：內含 wheel/sdist 的 SHA-256 與「不含 config.yaml、資料庫或本機 secrets」的隱私排除驗證結果。下載安裝檔後請比對 SHA-256 一致。
