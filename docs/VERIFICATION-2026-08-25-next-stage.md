# 2026-08-25 下一階段驗證紀錄

## Windows milestone Toast E2E

- 隔離 SQLite 建立 2 分鐘 Codex foreground event，門檻設為 1 分鐘。
- 真實 milestone evaluator 回傳 `notified`。
- WinRT Toast submission：return code `0`、transport=`winrt_toast`。
- DB receipt：1 筆 `sent`；第二次 evaluator=`already_notified`。
- 正式 `omni_context.db` 未寫入測試 event。完整 JSON receipt 保留於執行機 OS Temp。

## Browser capture

- ChatGPT：2026-08-25 於 `chatgpt.com` 繁中 anonymous session 完成真實 DOM probe、送出最小 E2E prompt 並取得 `OK` response。確認 input `#prompt-textarea` 與 response `[data-message-author-role='assistant']`；發現繁中「傳送提示詞」不在原 click selector，已修復。
- 共用 capture core 新增 composer-scope keydown、form submit、localized send click，以及 response count/text baseline，避免新 prompt 誤配上一則 assistant response。
- Claude.ai、Manus：可用測試瀏覽器分別導向 login；未取得 authenticated conversation，因此目前只能通過 source/selector contract，不能宣稱真實 capture E2E。

## Formal rollback rehearsal

- 路徑：`1.3.0a1/schema 4 → 1.3.0a2/schema 5 → 1.3.0a1/schema 4`。
- 前版 wheel、candidate wheel、pre-upgrade DB 均保存 SHA-256。
- 初次 rehearsal 發現只覆蓋 `.db` 會被 candidate 的 WAL 重新套回 schema 5；正式腳本改用 SQLite online backup，並在隔離目標清除 `.db-wal/.db-shm` 後回復。
- 修正後 runtime/schema、database SHA 與 sentinel row 全部一致；JSON receipt 保留於執行機 OS Temp。

## Cross-platform matrix

- `.github/workflows/platform-matrix.yml`：Windows、Ubuntu、macOS × Python 3.10/3.12。
- 每個 job 執行 pytest、compileall、Extension JS syntax、wheel/sdist build、artifact privacy/content receipt、installed writable-home/API/assets smoke。
- 本文件建立時尚待推送後取得 GitHub Actions 真實結果；不得以 workflow YAML 存在宣稱多平台已通過。

## P3-2 / P3-3

- Schema 7/7：`semantic_documents` 與 embedding input provenance。
- 真實本機 `bge-m3:latest` 全量索引 4,102/4,102，1024 dimensions；3 筆 `ascii_fallback`，failure=0。
- 第二次 index：0 changed / 4,102 unchanged。
- `omni ask` retrieval-only 與 `llama3.1:8b` synthesis 均通過；來源引用回到原始 SQLite row，similarity 不作 truth/coverage 證明。
