# 下一個 Session 接手指南

> 最後更新:2026-09-01(session `claude/next-session-docs-fy0jca`:P5-R5 + Telegram 設定流程 + P5-R4b + P4.3 Repo Onboarding 落地)。
> 這頁是給「下一個開發 session(人或 AI)」的最短接手路徑;現況以
> [STATUS.yaml](../STATUS.yaml) 與 [ROADMAP.md](../ROADMAP.md) §11 為準。

## 一分鐘現況

- **版本**:v1.3.0a5 已發佈為 GitHub pre-release(release workflow 自動建置,SHA-256 receipt 交叉驗證);`release_ready: false`,唯一缺口是全天 coverage ledger 實測。
- **Schema**:migration 16/16(append-only + checksum;新表勿繞過 registry)。
- **測試**:251 項 contract tests;容器/雲端環境跑 `pytest tests/` 會有 1 個已知環境失敗(`test_open_command_is_argv_not_shell_string`,缺 xdg-open;Windows 實機會過)。
- **秘書(P5)**:R1 LLM 註解 → R2 L1 白名單代辦 → R3 L2 subprocess dispatcher(三道門+冷卻)→ 寫入 Addendum(`agent_apply_plan` 兩段式改檔、永不 commit)→ R4a 晨報 → R4b Telegram inline 批准(getUpdates 長輪詢 outbound-only;批准通道需 execution token 解鎖、in-memory TTL 重啟即失效;只批 L0/L1)→ **R5 自訂排程任務**(僅 L0 唯讀 template 可排程:Handoff/週報/月報 rollup/STATUS 過期點名草稿;migration 016;錯過只補跑一次;每次執行寫 audit receipt),**ADR-008 R1–R5 全階段實作完成、全部預設關閉**;開關集中在儀表板「設定」分頁(小秘書執行器 + Telegram 通知兩張常用卡片,其餘設定預設收合)。契約見 [ADR-008](ADR-008-gated-agent-executor.md)。
- **摘要**:兩層增量(checkpoint 微摘要 map @本機 Ollama → 日報 reduce),`synthesizer.daily_from_micro` 預設開;日報 prompt 有逐事件截斷與總量上限;週/月報 rollup 只彙整既有每日摘要(`synthesizer/rollup.py`,缺日誠實列出、LLM 失敗回退 deterministic)。
- **UI**(2026-09-01 資訊架構重整,兩輪):導覽 6 分頁分主次——01 小秘書與知識庫(RAG 完整對話/引用/索引管理併入同一分頁的折疊區,共用對話)/02 進行中工作/03 摘要與快照為主,04 情報流/05 設定/06 系統健康弱化為次要樣式。設定分頁分兩區:「秘書與自動化(常用)」預設展開(執行器內的排程任務、Telegram 連線設定為巢狀折疊;Telegram 已連線時連線設定自動收合,批准區塊成為主體)、「其他設定」預設收合。活動快照併入 04(底部折疊卡);**本機 Git 同步中心+對帳搬到 02 進行中工作**(折疊卡,展開才做 git 掃描);「設定」分頁頂部固定「儲存並套用」列,常用卡(執行器含排程任務、Telegram 含解鎖批准)預設展開,設定一次即不動的卡(監控路徑/採集來源/摘要與 LLM/使用時間/GitHub)預設收合並記住展開狀態(localStorage)。折疊卡用原生 details/summary,各分頁桌面與 494px 皆無水平溢出(Playwright 實測)。**外觀為兩個獨立的軸**:`data-theme`(dark/light)× `data-accent`(naruto/forest/ocean),CSS 全面走 `var(--accent)`(66 處)、`--accent-hover`、`--accent-ink`;新配色只需加一組 `html[data-theme=X][data-accent=Y]` 變數區塊,不動任何元件樣式。偏好存 localStorage(`omni-theme`/`omni-palette`),extension-monitor 以 head 內小 script 讀同一個 key。
- **P4.3 Repo Onboarding**:已實作(同步中心「掃描對帳」:未 init 資料夾/無 remote repo/未 clone 的 GitHub repo;已 clone 與否只認 remote URL、同名僅提示不自動配對;init/attach/clone/create 皆單一目標確認式、不覆寫非空目錄、永不代為 push;契約在 ADR-011 Addendum)。既定 next milestone 已完成,STATUS `next_milestone` 改為「收使用者側 live 收據後重評 release_ready」。
- **介紹影片**:3 分鐘 MP4 已交付使用者;場景源檔在 [`promo/`](../promo/)(單景可重渲,見其 README)。

## 等待中的使用者側收據(不是程式工作)

1. **全天 coverage ledger**:使用者讓 Windows 實機跨午夜連續運行一天 → 儀表板 coverage 轉 `OBSERVED` 或隔日 `GET /api/v1/usage/coverage?date=YYYY-MM-DD` 回 `meets_full_coverage: true`;取得後更新 STATUS(`continuous_coverage_ledger` gate 與 known_blockers、release_ready 評估)。
2. **L2 實機試用**:使用者在自己機器開三個執行器開關 + `python main.py init --show-token`,實跑一次 draft→confirm→(可選 apply)。
3. Ollama 鏈路已有 live 診斷收據(llm-test:reachable、llama3.1:8b、8.36s),不用再驗。
4. **P4.3 onboarding 實操**:在實機按「掃描對帳」並各實跑一種動作(init/attach 或 clone),確認對帳分類與拒絕條件符合預期。
5. **Telegram live 驗收**:使用者在「設定 → Telegram 通知」卡片走完設定流程(BotFather 建 bot → 貼 token → 偵測 chat id → 測試訊息送達 → 儲存啟用),再開「inline 批准」+「🔓 解鎖遠端批准」,實批一次 L1 動作取得 approved_via=telegram_inline receipt。

## 下一步候選(依 ADR-008 階段)

| 候選 | 內容 | 前置 |
| :--- | :--- | :--- |
| **收使用者側 live 收據** | 全天 coverage ledger、Telegram 設定+inline 批准、L2 draft→apply、P4.3 onboarding 一輪實操;齊備後重評 release_ready 與下一個 pre-release | 全部需要使用者在 Windows 實機操作 |
| 更多 L2 template | 依 ADR-008 Addendum 模式逐一審查新增(一次一個 template) | 依需求 |
| 更多 schedulable template | 依 P5-R5 模式新增 L0 唯讀排程動作(一次一個,L1/L2 永不可排程) | 依需求 |

## 工程慣例(照舊)

- **分支**:在當次 session 的指定分支開發 → push → `main` fast-forward → push(使用者要求所有成果都落在 main)。
- **誠實文化**:每個聲明附 receipt/claim boundary;測試失敗如實回報;migration 永遠 append-only;危險能力預設關閉。
- **文件同步**:功能落地時同步 USAGE / ROADMAP §11 / STATUS(quality gate + known_blockers)/ 必要時 README 與 ADR。

## 遠端容器環境備忘(在 Claude Code 雲端 session 內開發時)

- 依賴裝在 scratchpad venv(系統 pip 缺新 setuptools,jieba 會建置失敗);`pip install -e .` 後跑測試。
- Playwright 用 `executable_path=/opt/pw-browsers/chromium` + `--no-sandbox`;**Playwright 內建 ffmpeg 沒有 PNG 解碼器**,要完整 ffmpeg 用 `pip install imageio-ffmpeg`。
- `pkill -f` 的 pattern 會殺到自己的 shell(exit 144),用 `main[.]py` 這種寫法。
- 容器沒有 xdg-open(上述已知測試失敗)、git 憑證只能推分支不能推 tag(發佈走 release workflow 的 workflow_dispatch)。
- E2E 對 localhost server 要用 port 8765(Origin allowlist 綁定預設埠)。
