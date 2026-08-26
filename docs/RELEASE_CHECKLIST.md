# OmniContext Release Checklist

**Candidate:** `1.3.0a3`
**Date:** 2026-08-25
**Scope:** Packaging Alpha；不是公開 release 授權

## Pre-Deploy

- [x] 85 個 contract tests 通過；Python compile、Extension JS syntax、TOML/YAML parse 與 diff check 通過。
- [x] SQLite migration fresh/legacy/live upgrade 為 `7/7`。
- [x] Wheel/sdist build 在隔離 PEP 517 environment 通過。
- [x] Artifact receipt 確認 config template、Dashboard、Extension assets 與 CLI entry point 均存在。
- [x] Artifact 不含 `config.yaml`、SQLite database 或 local secrets。
- [x] Fresh wheel install 使用 package 外的 writable application home。
- [x] `1.2.0 → 1.3.0a2` isolated upgrade smoke 通過。
- [x] `1.3.0a3` wheel/sdist content/privacy receipt、fresh installed writable-home、schema 7/7 與 HTTP assets smoke 通過。
- [x] Windows／Ubuntu／macOS × Python 3.10／3.12 GitHub Actions matrix 六個 jobs 通過（run `32757498004`）。
- [x] Gemini Browser ingestion 已觀察 3 筆 event，其中 2 筆有 response。
- [ ] Extension `1.3.2` 重新載入後的 Claude.ai live verification PASS receipt 通過；需同時具備開始後 heartbeat、Content Ready、event 與非空 response delta。
- [x] ChatGPT 真實 DOM prompt/response selector probe 與繁中 send click 修復通過；該測試瀏覽器未載入 Extension，因此不等於 DB capture receipt。
- [ ] Claude.ai authenticated real-browser ingestion 尚未完成驗證。
- [x] 真實 Windows WinRT milestone Toast E2E、DB receipt 與 duplicate suppression 通過。
- [x] Formal package + SQLite rollback rehearsal 通過，包含 WAL/SHM handling。
- [x] P3-2 4,102/4,102 local semantic index 與 P3-3 local `omni ask` 通過。

## Deploy / Publish

- [ ] 在乾淨 Windows VM 重跑 install、service 與 Extension pairing。
- [ ] 建立 signed/tagged release candidate。
- [ ] 發布 wheel/sdist 到目標 registry。
- [ ] 驗證下載後 SHA-256 與本機 build receipt 一致。
- [ ] 監看啟動錯誤、migration state、HTTP health 與 collector health。

目前以上 publish 項目未獲授權也未執行，因此專案仍為 `release_ready: false`。

## Post-Deploy

- [ ] 驗證 `omnicontext init`、`assets-status`、`extension-path` 與 `migration-status`。
- [ ] 驗證 Dashboard、Extension Monitor 與 `/static/app.js` HTTP 200。
- [ ] 確認 Browser event、foreground coverage 與 notification claim boundary 未被放寬。
- [ ] 更新 release notes、STATUS 與遠端 tag SHA。

## Rollback Triggers

- Runtime 寫入 `site-packages` 或其他非 application-home 目錄。
- Config、database 或 secret 被打入 artifact。
- Schema state 不是 `up_to_date 7/7`，或出現 checksum/newer-version error。
- Health、Dashboard、Extension Monitor 或 static assets 任一無法讀取。
- Upgrade 後既有 config/database 不可讀或 row-count/schema contract 失敗。

## Rollback Procedure

1. 停止確切的 OmniContext process，不終止未確認的 port owner。
2. 保留 application home 與 verified SQLite online backup；不得用檔案複製覆蓋執行中的 DB。
3. 解除候選 package並安裝已驗證 previous wheel；同時回復與 previous runtime 相容的 pre-migration DB。`1.2.0` wheel 本身缺 assets，不作完整 packaged rollback target。
4. 在確認 service 已停止且目標限於 application home 後，清除 candidate `.db-wal/.db-shm`；否則新 WAL 可能把新 schema 重新套回舊 DB。
5. 執行 `migration-status`、backup integrity 與 isolated restore drill；可用 `scripts/formal_rollback_rehearsal.py` 在隔離環境重演。
6. 只有 health、schema 與資料 contract 都恢復後才重啟 collectors。
