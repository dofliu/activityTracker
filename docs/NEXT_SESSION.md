# 下一個 Session 接手指南

> 最後更新:2026-08-31(session `claude/next-session-docs-fy0jca`:P5-R5 落地)。
> 這頁是給「下一個開發 session(人或 AI)」的最短接手路徑;現況以
> [STATUS.yaml](../STATUS.yaml) 與 [ROADMAP.md](../ROADMAP.md) §11 為準。

## 一分鐘現況

- **版本**:v1.3.0a5 已發佈為 GitHub pre-release(release workflow 自動建置,SHA-256 receipt 交叉驗證);`release_ready: false`,唯一缺口是全天 coverage ledger 實測。
- **Schema**:migration 16/16(append-only + checksum;新表勿繞過 registry)。
- **測試**:229 項 contract tests;容器/雲端環境跑 `pytest tests/` 會有 1 個已知環境失敗(`test_open_command_is_argv_not_shell_string`,缺 xdg-open;Windows 實機會過)。
- **秘書(P5)**:R1 LLM 註解 → R2 L1 白名單代辦 → R3 L2 subprocess dispatcher(三道門+冷卻)→ 寫入 Addendum(`agent_apply_plan` 兩段式改檔、永不 commit)→ R4a 晨報 → **R5 自訂排程任務**(僅 L0 唯讀 template 可排程:Handoff/週報/月報 rollup/STATUS 過期點名草稿;migration 016;錯過只補跑一次;每次執行寫 audit receipt),全部實作完成、**全部預設關閉**;開關集中在儀表板「07 監控配置 → 小秘書執行器」。契約見 [ADR-008](ADR-008-gated-agent-executor.md)(含 Addendum 與 R5 條目)。
- **摘要**:兩層增量(checkpoint 微摘要 map @本機 Ollama → 日報 reduce),`synthesizer.daily_from_micro` 預設開;日報 prompt 有逐事件截斷與總量上限;週/月報 rollup 只彙整既有每日摘要(`synthesizer/rollup.py`,缺日誠實列出、LLM 失敗回退 deterministic)。
- **UI**:01 分頁為小秘書首頁(交辦對話框整合 RAG + 建議收件匣);導覽 01–08 已重排;執行器卡片含排程任務管理(新增/停用/刪除/立即執行,mutation 需 execution token);新增「06 Telegram 通知」卡片(貼 token → 偵測 chat id → 即時連線測試 → 通過才存 config;secret 永不回流瀏覽器)。
- **介紹影片**:3 分鐘 MP4 已交付使用者;場景源檔在 [`promo/`](../promo/)(單景可重渲,見其 README)。

## 等待中的使用者側收據(不是程式工作)

1. **全天 coverage ledger**:使用者讓 Windows 實機跨午夜連續運行一天 → 儀表板 coverage 轉 `OBSERVED` 或隔日 `GET /api/v1/usage/coverage?date=YYYY-MM-DD` 回 `meets_full_coverage: true`;取得後更新 STATUS(`continuous_coverage_ledger` gate 與 known_blockers、release_ready 評估)。
2. **L2 實機試用**:使用者在自己機器開三個執行器開關 + `python main.py init --show-token`,實跑一次 draft→confirm→(可選 apply)。
3. Ollama 鏈路已有 live 診斷收據(llm-test:reachable、llama3.1:8b、8.36s),不用再驗。
4. **Telegram 連線**(P5-R4b 前置):使用者在「07 監控配置 → Telegram 通知」卡片走完設定流程(BotFather 建 bot → 貼 token → 偵測 chat id → 測試訊息送達 → 儲存啟用);完成後即可動工 R4b inline 批准。

## 下一步候選(依 ADR-008 階段)

| 候選 | 內容 | 前置 |
| :--- | :--- | :--- |
| **P5-R4b** | Telegram inline 批准(同一 execution token 邊界)+ 晚間交接推播 | **設定流程已就緒**:使用者在「07 監控配置 → Telegram 通知」卡片貼 token → 偵測 chat id → 即時測試通過即完成連線;剩 bot 端 inline keyboard 互動實作 |
| 更多 L2 template | 依 Addendum 模式逐一審查新增(一次一個 template) | 依需求 |
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
