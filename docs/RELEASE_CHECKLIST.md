# OmniContext Release Checklist

**Candidate:** `1.3.0a5`
**Date:** 2026-08-31
**Scope:** Packaging Alpha；publish 已於 2026-08-31 獲使用者授權（GitHub Release pre-release；不含 PyPI）

## Pre-Deploy

- [x] 147 個 contract tests 通過（含 coverage ledger 與 live-acceptance script 測試）；Python compile、Extension JS syntax、TOML/YAML parse 通過。
- [x] SQLite migration fresh/legacy/live upgrade 為 `13/13`（013 = continuous coverage ledger）。
- [x] Wheel/sdist build 在隔離 PEP 517 environment 通過（Windows 2026-08-25；Linux container 2026-08-30 重演）。
- [x] Artifact receipt 確認 config template、Dashboard、Extension assets 與 CLI entry point 均存在。
- [x] Artifact 不含 `config.yaml`、SQLite database 或 local secrets。
- [x] Fresh wheel install 使用 package 外的 writable application home（Windows 與 Linux 均驗）。
- [x] `1.2.0 → 1.3.0a2` isolated upgrade smoke 通過。
- [x] Linux container 發佈預演：build、artifact content/privacy receipt、fresh venv 安裝、schema 13/13、HTTP smoke 與 `verify_installed_package` checks 全數通過（2026-08-30）。
- [x] Windows／Ubuntu／macOS × Python 3.10／3.12 GitHub Actions matrix 六個 jobs 通過（run `32757498004`）。
- [x] Gemini Browser ingestion 已觀察 3 筆 event，其中 2 筆有 response（歷史觀察；本輪 live 驗證未含 Gemini）。
- [x] Extension 1.3.1 已登入 Chrome live verification PASS receipt（2026-08-31 01:03：開始後 heartbeat、Content Ready、event 與非空 response delta，ChatGPT 與 Claude.ai 均通過；verification `857027de…`）。
- [x] Claude.ai authenticated real-browser ingestion 已隨上述 PASS 驗證（本輪 3 event／2 response delta）。
- [x] ChatGPT 真實 DOM prompt/response selector 與繁中 send click 修復，且本輪 live PASS 已含 DB capture delta。
- [x] 真實 Windows WinRT milestone Toast E2E、DB receipt 與 duplicate suppression 通過。
- [x] Formal package + SQLite rollback rehearsal 通過，包含 WAL/SHM handling。
- [x] P3-2 local semantic index 與 P3-3 local `omni ask` 通過（4,380/4,380）。
- [x] P2.7 三平台背景任務 live 驗收 PASS（2026-08-29 資料：codex 29／claude_code 7／claude_desktop 12 筆 completed，union 28,838.971 秒）。
- [ ] P2.6 coverage ledger 的 Windows 實機全天 receipt（服務跑滿一日、dashboard coverage 轉 `observed`）。

## Deploy / Publish

- [x] bump `core/__init__.py` 至 `1.3.0a5` 並重建 wheel/sdist，重跑 `scripts/verify_release_artifacts.py`。
- [x] 乾淨 venv 安裝與 service smoke 已於 Linux 預演通過（2026-08-30）；乾淨 Windows VM 重跑列為 post-release 追蹤項。
- [ ] 建立 tagged release（`git tag v1.3.0a5` + push tag，自動觸發 `.github/workflows/release.yml`）。
- [ ] Release workflow 建立 GitHub Release（pre-release），附 wheel/sdist 與 `release-artifacts-receipt.json`（SHA-256）；PyPI 不在本次範圍。
- [ ] 下載附件並比對 SHA-256 與 receipt 一致。
- [ ] 監看啟動錯誤、migration state、HTTP health 與 collector health。

Publish 已獲使用者授權（2026-08-31）；`release_ready` 指「穩定版就緒」，alpha pre-release 發佈後仍維持 `false`。

## Post-Deploy

- [ ] 驗證 `omnicontext init`、`assets-status`、`extension-path` 與 `migration-status`。
- [ ] 驗證 Dashboard、Extension Monitor 與 `/static/app.js` HTTP 200。
- [ ] 確認 Browser event、foreground coverage 與 notification claim boundary 未被放寬。
- [ ] 更新 release notes、STATUS 與遠端 tag SHA。

## Rollback Triggers

- Runtime 寫入 `site-packages` 或其他非 application-home 目錄。
- Config、database 或 secret 被打入 artifact。
- Schema state 不是 `up_to_date 13/13`，或出現 checksum/newer-version error。
- Health、Dashboard、Extension Monitor 或 static assets 任一無法讀取。
- Upgrade 後既有 config/database 不可讀或 row-count/schema contract 失敗。

## Rollback Procedure

1. 停止確切的 OmniContext process，不終止未確認的 port owner。
2. 保留 application home 與 verified SQLite online backup；不得用檔案複製覆蓋執行中的 DB。
3. 解除候選 package並安裝已驗證 previous wheel；同時回復與 previous runtime 相容的 pre-migration DB。`1.2.0` wheel 本身缺 assets，不作完整 packaged rollback target。
4. 在確認 service 已停止且目標限於 application home 後，清除 candidate `.db-wal/.db-shm`；否則新 WAL 可能把新 schema 重新套回舊 DB。
5. 執行 `migration-status`、backup integrity 與 isolated restore drill；可用 `scripts/formal_rollback_rehearsal.py` 在隔離環境重演。
6. 只有 health、schema 與資料 contract 都恢復後才重啟 collectors。
