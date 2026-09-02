# 下一個 Session 接手指南

> 最後更新:2026-09-02(session `claude/this-has-error-kn9w42`:Windows CI 紅燈根因修復 + PR CI trigger)。
> 這頁是給「下一個開發 session(人或 AI)」的最短接手路徑;現況以
> [STATUS.yaml](../STATUS.yaml) 與 [ROADMAP.md](../ROADMAP.md) §11 為準,
> **待辦清單一律以 [TODO.md](TODO.md) 為準**(含每項的完成判準)。

## ⚠️ 接手前先看:分支狀態

`main` 目前是綠的,但**有一個 commit 還沒併進去**:

| 分支 | 狀態 |
| :--- | :--- |
| `main` | `5f3e285`(含 Windows UTF-8 修復);Platform Matrix run #50 六個 job 全綠 |
| `claude/this-has-error-kn9w42` | 比 main 多一個 `63ce028`(CI 加 `pull_request` trigger),**尚未開 PR、尚未併入** |

接手第一件事:決定要不要把 `63ce028` 併進 main(見 [TODO.md](TODO.md) §0)。在它併入前,**新開的 PR 不會有任何 CI**。

## 一分鐘現況

- **版本**:v1.3.0a5 已發佈為 GitHub pre-release(release workflow 自動建置,SHA-256 receipt 交叉驗證);`release_ready: false`,唯一缺口是全天 coverage ledger 實測。
- **Schema**:migration 16/16(append-only + checksum;新表勿繞過 registry)。
- **測試**:262 項 contract tests(44 個模組);容器/雲端環境**依賴裝齊**時跑 `pytest tests/` 會有 1 個已知環境失敗(`test_open_command_is_argv_not_shell_string`,缺 xdg-open;Windows 實機會過)。若只裝部分依賴(系統 pip 讓 `jieba` 建置失敗、改手動裝 pytest/fastapi/… 時),會**另外**多出 collector、RAG 相關的失敗與 collection error——那是缺套件不是程式問題,判斷前先確認依賴是否齊全。
- **CI**:`platform-matrix.yml` 在 push to main、**PR to main**(2026-09-02 新增,還在分支上)與手動 dispatch 觸發,3 OS × 2 Python 共 6 個 job。最新綠燈收據:main 的 [run #50](https://github.com/dofliu/activityTracker/actions/runs/33566553321)(`5f3e285`,6/6 success)。
- **秘書(P5)**:R1 LLM 註解 → R2 L1 白名單代辦 → R3 L2 subprocess dispatcher(三道門+冷卻)→ 寫入 Addendum(`agent_apply_plan` 兩段式改檔、永不 commit)→ R4a 晨報 → R4b Telegram inline 批准(getUpdates 長輪詢 outbound-only;批准通道需 execution token 解鎖、in-memory TTL 重啟即失效;只批 L0/L1)→ **R5 自訂排程任務**(僅 L0 唯讀 template 可排程:Handoff/週報/月報 rollup/STATUS 過期點名草稿;migration 016;錯過只補跑一次;每次執行寫 audit receipt),**ADR-008 R1–R5 全階段實作完成、全部預設關閉**;開關集中在儀表板「設定」分頁(小秘書執行器 + Telegram 通知兩張常用卡片,其餘設定預設收合)。契約見 [ADR-008](ADR-008-gated-agent-executor.md)。
- **摘要**:兩層增量(checkpoint 微摘要 map @本機 Ollama → 日報 reduce),`synthesizer.daily_from_micro` 預設開;日報 prompt 有逐事件截斷與總量上限;週/月報 rollup 只彙整既有每日摘要(`synthesizer/rollup.py`,缺日誠實列出、LLM 失敗回退 deterministic)。
- **UI**(2026-09-01 資訊架構重整,兩輪):導覽 6 分頁分主次——01 小秘書與知識庫(RAG 完整對話/引用/索引管理併入同一分頁的折疊區,共用對話)/02 進行中工作/03 摘要與快照為主,04 情報流/05 設定/06 系統健康弱化為次要樣式。設定分頁分兩區:「秘書與自動化(常用)」預設展開(執行器內的排程任務、Telegram 連線設定為巢狀折疊;Telegram 已連線時連線設定自動收合,批准區塊成為主體)、「其他設定」預設收合。活動快照併入 04(底部折疊卡);**本機 Git 同步中心+對帳搬到 02 進行中工作**(折疊卡,展開才做 git 掃描);「設定」分頁頂部固定「儲存並套用」列,常用卡(執行器含排程任務、Telegram 含解鎖批准)預設展開,設定一次即不動的卡(監控路徑/採集來源/摘要與 LLM/使用時間/GitHub)預設收合並記住展開狀態(localStorage)。折疊卡用原生 details/summary,各分頁桌面與 494px 皆無水平溢出(Playwright 實測)。**外觀為兩個獨立的軸**:`data-theme`(dark/light)× `data-accent`(naruto/forest/ocean),CSS 全面走 `var(--accent)`(66 處)、`--accent-hover`、`--accent-ink`;新配色只需加一組 `html[data-theme=X][data-accent=Y]` 變數區塊,不動任何元件樣式。偏好存 localStorage(`omni-theme`/`omni-palette`),extension-monitor 以 head 內小 script 讀同一個 key。
- **P4.3 Repo Onboarding**:已實作(同步中心「掃描對帳」:未 init 資料夾/無 remote repo/未 clone 的 GitHub repo;已 clone 與否只認 remote URL、同名僅提示不自動配對;init/attach/clone/create 皆單一目標確認式、不覆寫非空目錄、永不代為 push;契約在 ADR-011 Addendum)。既定 next milestone 已完成,STATUS `next_milestone` 改為「收使用者側 live 收據後重評 release_ready」。
- **RAG 對話契約**(2026-09-01 修):`resolve_secret_env` 回傳 `SecretResolution` 物件,**必須取 `.value`**——rag/ 內 4 處漏取導致 Gemini/OpenAI/Claude 走 RAG 一律失敗(物件恆為真值使「未設金鑰」判斷失效,且 repr 被帶進 URL)。金鑰現改走 header 不進 URL;SSE `event_generator` 全程 try/finally **保證送出 done**(瀏覽器只靠它解除「回覆中」),檢索移入 generator 並有 60 秒逾時,前端另有 120 秒閒置 abort。契約由 `tests/test_rag_chat_stream.py`(10 項)鎖住。
- **Agent dispatch 編碼契約**(2026-09-01 修):父行程固定以 UTF-8 解碼子行程 stdout/stderr(`_truncate()`),所以 `build_subprocess_env()` **強制覆寫** `PYTHONUTF8=1` 與 `PYTHONIOENCODING=utf-8`。沒有這兩個變數時,Windows 的 Python 子行程會沿用 ANSI code page(cp1252/cp950),agent CLI 一輸出中文就 `UnicodeEncodeError` → exit 1 → receipt 記成 `failed`;Linux/macOS 預設 UTF-8 所以看不出來,**這正是 main 從 run #44 連紅到 #48 的原因**。這兩個變數只決定編碼、不帶機密,allowlist 的安全邊界不變。回歸測試 `test_subprocess_child_emits_utf8_even_under_a_legacy_ansi_locale` 以繼承的 `PYTHONIOENCODING=cp1252` 重現,在任何平台都會抓到。
- **介紹影片**:3 分鐘 MP4 已交付使用者;場景源檔在 [`promo/`](../promo/)(單景可重渲,見其 README)。

## 待辦與下一步

**一律看 [TODO.md](TODO.md)**（每項都附完成判準）。目前的形狀是：

- **A. 等待使用者側 live 收據**(👤 需在 Windows 實機操作,不是程式工作):全天 coverage ledger(唯一還擋 `release_ready` 的能力缺口)、RAG 雲端 provider 複測、Telegram 設定＋inline 批准、L2 執行器試用、P4.3 對帳實操。
- **B. 已知問題與技術債**:大型 RAG 索引首次檢索仍在主程序載入(已用逾時緩解,根因未解)、容器缺 xdg-open 的已知測試失敗、legacy AI rows、Extension 覆蓋邊界。
- **C. 功能候選**:更多 L2／可排程 template、P4 其餘採集來源、更多配色。

> 這頁只保留「現況與環境」;新增待辦請寫進 TODO.md,不要在這裡另開清單。

## 工程慣例(照舊)

- **分支**:在當次 session 的指定分支開發 → push → 併入 `main`(使用者要求所有成果都落在 main)。2026-09-01 起也走 PR 流程(第一個是已合併的 [PR #1](https://github.com/dofliu/activityTracker/pull/1));**指定分支的 PR 一旦合併就不能再沿用**,後續工作要從最新的 main 重開同名分支(`git fetch origin main && git checkout -B <branch> origin/main`)。
- **CI**:`platform-matrix.yml` 原本只在 push to main 觸發,所以跨平台問題只會在**進了 main 之後**才炸(#44–#48 連紅五次即是如此)。`63ce028` 已補上 `pull_request` trigger,併入後 PR 就會在合併前跑完整矩陣。push trigger 仍只限 main,所以一個 PR 的每個 head commit 只會有一次 run,不會重複。
- **誠實文化**:每個聲明附 receipt/claim boundary;測試失敗如實回報;migration 永遠 append-only;危險能力預設關閉。
- **文件同步**:功能落地時同步 USAGE / ROADMAP §11 / STATUS(quality gate + known_blockers)/ 必要時 README 與 ADR。

## 遠端容器環境備忘(在 Claude Code 雲端 session 內開發時)

- 依賴裝在 scratchpad venv(系統 pip 缺新 setuptools,jieba 會建置失敗);`pip install -e .` 後跑測試。
- Playwright 用 `executable_path=/opt/pw-browsers/chromium` + `--no-sandbox`;**Playwright 內建 ffmpeg 沒有 PNG 解碼器**,要完整 ffmpeg 用 `pip install imageio-ffmpeg`。
- `pkill -f` 的 pattern 會殺到自己的 shell(exit 144),用 `main[.]py` 這種寫法。
- 容器沒有 xdg-open(上述已知測試失敗)、git 憑證只能推分支不能推 tag(發佈走 release workflow 的 workflow_dispatch)。
- E2E 對 localhost server 要用 port 8765(Origin allowlist 綁定預設埠)。
