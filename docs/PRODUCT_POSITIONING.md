# OmniContext 產品定位：跨 AI、應用與 Repository 的個人工作脈絡層

**定位日期：**2026-08-25（2026-09-16 補「下一階段」更新）

## 一句話定位

> OmniContext 不把個人的工作歷史搬進某一家 AI；它在本機建立跨 AI、跨應用、跨 Repository 的可追溯工作脈絡，讓不同 AI 都能從同一份 evidence-aware context 接手。

## 市場變化

AI provider 正逐步強化各自產品內的 continuity：

- ChatGPT Projects 能在同一個 Project 內組織 chats、files、instructions 與 project memory。
- Gemini Apps 已提供從其他 AI platform 匯入 memory 或完整 chat export 的功能，但匯入結果會成為 Gemini Activity 內的資料。
- Claude 提供 Projects/RAG 與帳號 conversation export。
- Grok 官方產品頁已描述跨 conversations 的 memory，Grok Build 也以 project workspace 與 session 為核心。

這些能力解決的是「讓某個 AI 更了解使用者」。OmniContext 要解決的是另一個問題：**使用者的工作並不只存在於某個 AI 裡。** 真正的專案狀態還散落在 local files、Git commits、branches、PR、IDE/terminal、foreground applications、不同 AI sessions 與尚未收尾的 Open Loops。

官方功能參考：

- [OpenAI — Projects in ChatGPT](https://help.openai.com/en/articles/10169521-using-projects-in-chatgpt)
- [Google — Import from other AI platforms to Gemini Apps](https://support.google.com/gemini/answer/16868299?hl=en)
- [Anthropic — Export Claude data](https://support.anthropic.com/en/articles/9450526-how-can-i-export-my-claude-data)
- [Anthropic — RAG for Projects](https://support.anthropic.com/en/articles/11473015-retrieval-augmented-generation-rag-for-projects)
- [xAI — Grok product overview](https://x.ai/grok)

## OmniContext 的差異化

| 維度 | Provider-centric memory/import | OmniContext |
|---|---|---|
| 主要目的 | 改善單一 AI 內的 continuity | 建立個人跨工具的工作 continuity |
| 資料範圍 | 該 AI 的 chats、files、connected sources | 多 AI sessions、browser conversations、files、Git、GitHub、window activity、Open Loops |
| 專案依據 | AI 產品內建立的 Project | Local repository/path、Git history 與 canonical project resolver |
| 儲存與控制 | 依 provider account／cloud policy | Local-first SQLite；cloud synthesis opt-in |
| 可信度 | Provider 決定 memory synthesis | 保留 source provenance、response status、coverage 與 lifecycle receipt |
| 跨 AI 接手 | 通常匯入或複製到特定 provider | 產生 provider-neutral Context Handoff；P3 將提供本機語意查詢 |

## 現行能力與證據邊界

目前已具備：

- Claude Code、Codex、Antigravity transcript parser。
- ChatGPT、Gemini、Claude.ai Browser Extension ingestion bridge。
- Local file、Git commit、GitHub repository/PR、window foreground activity。
- Canonical project resolver、Project State、Open Loop lifecycle、Context Handoff。
- Local SQLite、versioned migration、backup/restore drill 與 partial/unavailable coverage semantics。

目前不得宣稱：

- 已完整匯入每個 provider 的所有歷史 conversations。
- Browser ingestion 已涵蓋所有支援網站或所有 UI 版本。
- Foreground time 等於工作時間、生產力或注意力。
- 跨裝置同步或 autonomous executor 已完成（P3 semantic memory 與 `omni ask` 已於 2026-08-25 以 Alpha 落地，見 ADR-005；閘門式執行器 ADR-008 是有批准的例外，不是 autonomous）。

## 產品護城河方向

1. **Repository-aware context**：以實際 repo、branch、commit、file diff 與 PR 作為專案事實，不只依 AI 自己的摘要。
2. **Cross-source continuity**：同一 workstream 可串接不同 AI、IDE、terminal、文件與 GitHub 活動。
3. **Evidence-aware memory**：每個結論可回查 source、時間、coverage 與 final/partial 狀態。
4. **Provider-neutral handoff**：Context Handoff 可交給 Claude、Codex、ChatGPT、Gemini、Grok 或本機 Ollama，而不把 canonical state 綁在單一 provider。
5. **Local-first governance**：個人完整工作脈絡預設留在本機；使用 cloud LLM synthesis 時才明確 opt-in。

## 下一階段

> 本節 2026-08-25 的原文（先關 Browser bridge／milestone Toast／release matrix 的 evidence gates，再建 P3-2／P3-3）**均已完成**，成果見 [ROADMAP §11.2](../ROADMAP.md)。

**2026-09-22 更新**：2026-09-16 那句「真正沒有替代品的是**來源**（本機 AI agent transcript）」**要收回一半**——
外部檢視（[REVIEW-2026-09-22](REVIEW-2026-09-22-competitive-landscape-and-P9.md) §2.4／§2.5）指出讀本機 transcript
與跨 agent 交接都已有他人在做。護城河因此改記在第 3 項（**Evidence-aware memory**）：
不是「只有我們讀得到這些檔案」，而是「讀完之後的每個結論都指得回一筆 SQLite row」。
**不宣稱**：不宣稱是唯一或第一個讀本機 AI agent transcript 的工具；本專案沒有安裝比對過任何同類工具，
§2 的競品資料一律是未經本機驗證的外部快照。

**2026-09-16 更新**：定位本身不變，但檢視後確認——上表五個護城河方向裡，最沒有替代品的是第 1／2 項的**來源**：本機 AI agent transcript。
下一階段不是加功能，而是把這個核心從大系統裡抽出來、讓預設安裝不帶 RAG 依賴鏈（[ROADMAP §13](../ROADMAP.md)）。
價值評估（用處／學術／教學）與障礙排序見 [REVIEW-2026-09-16-project-assessment.md](REVIEW-2026-09-16-project-assessment.md) §3。
