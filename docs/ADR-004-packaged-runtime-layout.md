# ADR-004：Wheel/SDist Packaged Runtime Layout

**Status:** Accepted

**Date:** 2026-08-24

**Candidate version:** `1.3.0a2`

## Context

原 `1.2.0` 雖能產生 wheel/sdist，但 wheel 不包含 `config.example.yaml`、Dashboard Web assets 或 Browser Extension assets。Runtime 也把 package root 當成可寫資料目錄，導致安裝到 `site-packages` 後的 `init`、SQLite、reports 與 single-instance lock 路徑不可靠。

這些問題不會在 source checkout 測試中自然出現，因此不能只用 `pytest` 或「build 成功」宣稱 packaging 可用。

## Decision

1. Source checkout 若同時存在 `pyproject.toml` 與 `main.py`，維持專案根目錄作為 application home，避免改變既有個人環境。
2. Installed wheel 預設使用使用者可寫的 `~/OmniContext`；`OMNICONTEXT_HOME` 可覆寫完整 application home，`OMNICONTEXT_CONFIG` 可指定 config 並使相對資料路徑落在其父目錄。
3. `config.example.yaml` 以 wheel data-file 安裝至 `share/omnicontext`；Web 與 Browser Extension 以 package-data 隨 wheel 發布。
4. `config.yaml`、SQLite database、API keys、transcripts、reports 與 local receipts 不得進入 wheel/sdist。
5. `assets-status` 提供非敏感 runtime asset 診斷；`extension-path` 回傳 Chrome/Edge 可用的 Load unpacked 路徑。
6. Candidate version 集中於 `core.__version__`，FastAPI 與 packaging metadata 使用同一來源。
7. Build success 不等於 release gate 通過；必須另驗證 artifact contents、fresh install、上一版 wheel upgrade、application-home boundary、schema 5/5 與 Dashboard endpoints。

## Consequences

- Source checkout 與 installed wheel 會有不同的預設 writable root，但透過相同 resolver contract 管理。
- Wheel 不需要在 `site-packages` 建立 Web 或資料目錄；缺少 packaged assets 時直接失敗，不以空目錄掩蓋 packaging error。
- 移除 package 不會自動刪除 `~/OmniContext` 的 config/database，降低 rollback 或重新安裝造成資料遺失的風險。
- `1.3.0a2` 仍是 Personal Alpha；Windows isolated smoke 通過不等於 macOS/Linux 或完整 Browser coverage 已驗證。

## Verification Evidence

- 修改前 `1.2.0` wheel：config template、Web index、Extension Monitor、Extension manifest 全部缺失。
- `1.3.0a2` wheel/sdist：必要 assets、CLI entry point 與 privacy exclusions通過 content receipt。
- Fresh install：config/database 均位於隔離 application home，schema `5/5`，health/dashboard/monitor/static endpoints 均為 HTTP 200。
- Upgrade install：`1.2.0 → 1.3.0a2` pip replacement、相同 runtime checks 與 schema `5/5` 通過。

## Remaining Gates

- macOS/Linux build、install、CLI import 與 graceful-degradation CI matrix。
- 真實 Chrome/Edge Extension pairing 與 browser event evidence。
- 正式 publish/tag、retention pruning 與 production rollback rehearsal。
