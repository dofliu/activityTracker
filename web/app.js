// web/app.js — OmniContext Intel Board controller

const API = "";
const POLL_MS = 4000;

let currentLang = localStorage.getItem("omni-lang") || "zh-TW";
let currentConfig = null;
let activeFilter = "all";
let isMonitoring = false;
let expandedProject = null;
let recentEvents = [];
let projectsCache = [];
let loopsCache = [];
let summariesCache = [];
let currentSummaryMarkdown = "";
let currentCheckpointMarkdown = "";
let summaryView = "day";
let configDirs = [];
let configCalendarPaths = [];
let configRepos = [];
let githubStatus = null;
let showAllProjects = false;
let llmStatusCache = null;
let contextSessionsCache = null;
let relatedContextCache = null;
let secretaryProposalsCache = null;
let focusCarouselItems = [];
let focusCarouselIndex = 0;
let focusCarouselTimer = null;
let focusCarouselUserPaused = false;
let focusCarouselPointerPaused = false;
let repositorySyncCache = [];
let acceptanceCache = null;

const $ = (id) => document.getElementById(id);
const esc = (t) => String(t == null ? "" : t)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;").replace(/'/g, "&#039;");

// ---------------------------------------------------------------- i18n
const I18N = {
  "zh-TW": {
    lang_btn: "🌐 English",
    status_connected: "已連線",
    status_disconnected: "未連線",
    status_monitoring: "MONITORING 監控中",
    status_degraded: "MONITORING 部分採集異常",
    status_paused: "PAUSED 已暫停",
    btn_start_monitor: "開始監控",
    btn_pause_monitor: "暫停監控",
    btn_theme_light: "☀ 淺色",
    btn_theme_dark: "☾ 深色",
    palette_naruto: "🟠 火影橘",
    palette_forest: "🟢 森林綠",
    palette_ocean: "🔵 海洋藍",
    btn_quick_checkpoint: "⏱️ 快照",
    btn_quick_summary: "⚡ 生成今日摘要",
    tab_assistant: "01 · 🤖 小秘書",
    tab_knowledge: "02 · 📚 知識庫",
    tab_projects: "03 · 進行中工作",
    tab_repos: "04 · 🔁 Git 同步中心",
    tab_summaries: "05 · 摘要與統計",
    rail_stats_title: "今日統計",
    today_title: "TODAY · 今日行動清單",
    greeting_title: "🤗 小秘書的話",
    greeting_win_today: "今天",
    greeting_win_2h: "近 2 小時",
    greeting_name_label: "問候卡稱呼",
    greeting_name_ph: "例如 Dof（留空就不帶名字）",
    ph_loading_greeting: "小秘書正在整理你今天做了什麼…",
    btn_create_presets: "📦 建立每日排程",
    today_resume_label: "上次做到哪",
    why_now_label: "為什麼是現在",
    memory_title: "🧠 小秘書記憶區",
    btn_memory_add: "記下來",
    btn_memory_context: "👁 現在記得什麼",
    btn_memory_clear_obs: "🧹 清除觀察",
    memory_kind_user_note: "筆記",
    memory_kind_preference: "偏好",
    memory_kind_decision: "決定",
    memory_project_ph: "專案（選填）",
    memory_input_ph: "要小秘書記住的事；偏好可寫「不要提醒 <專案或提案類型>」",
    memory_boundary: "記憶區只存您輸入的短文字與秘書從本機唯讀收據推出的觀察（可一鍵刪除）；每次對話注入的脈絡有字數上限並附收據。在對話框輸入「記下來：…」也會寫到這裡。",
    memory_note_label: "你之前記過",
    ph_loading_memory: "載入記憶區…",
    related_history_note: "輸入目前的問題，從本機 semantic index 找相似歷史",
    sec_sessions: "近期工作階段",
    tab_settings: "06 · ⚙️ 系統設定",
    snav_secretary: "🤖 秘書與自動化",
    snav_telegram: "✈️ Telegram 通知",
    snav_line: "💬 LINE 通知",
    snav_paths: "📁 監控路徑",
    snav_sources: "🧲 採集來源",
    snav_llm: "🧠 摘要與 LLM",
    snav_usage: "⏱️ 使用時間與里程碑",
    snav_github: "🐙 GitHub 雲端整合",
    snav_group_ops: "維運",
    snav_feed: "📡 即時情報流",
    snav_health: "🛡️ 系統健康",
    snav_acceptance: "🧾 驗收中心",
    acceptance_p1_title: "實機收據總覽與發佈收斂條件",
    acceptance_p2_title: "逐項收據（docs/TODO.md A 段）",
    acceptance_loading: "正在檢查本機收據…",
    acceptance_intro: "這裡只讀本機已經存在的收據：SQLite 紀錄、設定值與檔案是否存在。它不會替你執行任何驗收動作，也不跑 git、不連網、不載入索引。「已取得收據」代表找到了符合判準的證據，不代表功能在所有情境下都正確。",
    btn_refresh_acceptance: "重新檢查",
    acceptance_st_passed: "已取得收據",
    acceptance_st_attested: "已人工確認",
    acceptance_st_partial: "部分收據",
    acceptance_st_pending: "尚未取得",
    acceptance_st_needs_human: "待你親眼確認",
    acceptance_st_not_configured: "未啟用",
    acceptance_st_runtime_only: "服務程序內才看得到",
    acceptance_how: "怎麼做",
    acceptance_criterion: "完成判準",
    acceptance_evidence: "查到的證據",
    acceptance_confirm_btn: "🖊 我親眼確認過",
    acceptance_unconfirm_btn: "↩ 取消確認",
    acceptance_confirm_ask: "這會記下一筆「人工署名確認」，與機器找到的證據分開記帳，且不會覆蓋機器判定。確定嗎？",
    acceptance_attested_by_you: "由你確認於",
    acceptance_blocking: "仍擋 release_ready",
    acceptance_gates_title: "release gates（ROADMAP §12.3）",
    acceptance_outstanding: "待辦",
    settings_nav_hint: "左側選擇要檢視的區塊；「儲存並套用」只影響設定類區塊。",
    rag_merged_title: "知識庫與 RAG（完整對話、引用與索引管理）",
    rag_merged_note: "與 01 小秘書交辦框共用同一條對話與歷史",
    tg_setup_summary: "連線設定（bot token／chat id／測試）",
    checkpoints_panel_title: "活動快照（週期 checkpoint 日誌）",
    checkpoints_panel_note: "需要回看原始時段紀錄時再展開",
    assistant_title: "🤖 小秘書 · 交辦與提問",
    assistant_input_ph: "請小秘書查資料、寫摘要、建議下一步…",
    btn_assistant_send: "交辦 ⚡",
    assistant_chat_empty: "問文件、查專案進度、請我建議下一步——回答會引用本機知識庫與工作紀錄。",
    assistant_chat_boundary: "對話使用本機知識庫（RAG）與所選模型；完整引用卡片與對話歷史在下方「知識庫與 RAG」區塊。",
    system_health_p1_title: "系統運行總覽與生命週期控制",
    system_health_p2_title: "採集器容錯隔離與健康矩陣",
    system_health_p2_note: "各採集器獨立隔離，單一損壞不中斷全域監控",
    system_health_p3_title: "最近維護收據與備份保險庫",
    system_health_p4_title: "維護操作即時終端 (Action Console)",
    label_system_state: "MONITORING STATE",
    label_db_size: "DATABASE SIZE",
    label_managed_projects: "MANAGED WORKSTREAMS",
    label_self_healing_status: "SELF-HEALING SUPERVISOR",
    btn_trigger_heal: "一鍵自我修復 (Self-Heal)",
    btn_trigger_wal: "執行 WAL Checkpoint",
    btn_trigger_maintain: "執行資料庫完整維護",
    btn_refresh_health: "重新整理",
    btn_clear_console: "清空輸出",
    resume_head: "RESUME HERE",
    resume_sub: "上次做到哪",
    context_sessions_title: "RECENT WORK SESSIONS",
    btn_refresh_sessions: "重新整理",
    ph_loading_sessions: "正在整理近期工作階段…",
    context_sessions_boundary: "依專案與事件間隔推定，不代表實際工時、連續專注或成果品質。",
    related_memory_title: "RELATED HISTORY",
    related_memory_placeholder: "輸入目前要處理的問題或工作…",
    btn_related_search: "查相似歷史",
    related_memory_empty: "輸入一段工作描述，從本機 semantic index 尋找相似紀錄。",
    related_memory_boundary: "查詢只送往本機 Ollama 且不保存；similarity 不代表工作重複或歷史結論正確。",
    secretary_title: "SECRETARY SUGGESTIONS",
    btn_refresh_proposals: "重新整理",
    ph_loading_proposals: "正在整理可追溯建議…",
    secretary_boundary: "只呈現本機 evidence 衍生建議；不保存、不會自動執行。LLM 註解（若啟用）僅供參考，預設使用本機模型。",
    btn_reindex_projects: "🔄 重新整理",
    ph_loading_projects: "載入進行中工作…",
    ph_no_projects: "尚未識別到專案活動。進行程式開發、論文寫作或在 Claude / Codex 發問後將自動建立。",
    active_workstreams: "ACTIVE WORKSTREAMS",
    feed_title: "LIVE FEED",
    filter_all: "全部",
    filter_ai: "AI",
    filter_file: "檔案",
    filter_git: "Git",
    filter_window: "視窗",
    ph_loading_feed: "載入即時活動…",
    ph_no_feed: "目前尚無活動紀錄。",
    collectors_title: "COLLECTORS",
    collector_file: "檔案監控",
    collector_git: "Git 掃描",
    collector_window: "視窗焦點",
    collector_agent: "Agent 日誌",
    collector_calendar: "行事曆（.ics）",
    collector_scheduler: "定時排程",
    cal_title: "📅 本機行事曆（.ics，唯讀，ADR-015）",
    cal_enabled_label: "啟用行事曆採集（沒有路徑時等於停用）",
    cal_paths_label: "ICS 檔或含 .ics 的資料夾",
    ph_ics_path: "例如 C:/Users/you/Calendars/work.ics 或整個資料夾",
    cal_horizon_label: "重複行程展開未來天數",
    cal_store_titles_label: "保存標題與地點（關閉則只留時間）",
    cal_note: "只讀本機 .ics 的時間／標題／地點／狀態；描述、與會者、連結一律不落地，不連任何雲端 API、不寫回檔案。把 Outlook／Google／Apple 匯出或同步的 .ics 放到本機資料夾即可；晨報與問候卡會如實帶入今天的行程。",
    collector_enabled: "● 運作中",
    collector_disabled: "○ 已關閉",
    settings_p1_title: "監控路徑",
    label_file_dirs: "FILE DIRS (檔案目錄)",
    label_git_roots: "GIT ROOTS (Git 倉庫根目錄)",
    btn_browse: "📁 瀏覽…",
    btn_add: "新增",
    ph_abs_path: "絕對路徑…",
    ph_git_path: "Git 專案根目錄…",
    git_recursive_note: "已啟用遞迴探索模式，根目錄下所有子倉庫均會自動納入掃描。",
    settings_p2_title: "採集來源",
    settings_p2_note: "右欄標示目前可信度",
    settings_p3_title: "摘要與 LLM",
    llm_key_status_title: "API KEY 狀態",
    llm_key_env_label: "環境變數名稱",
    btn_recheck_llm_key: "重新檢查",
    llm_key_boundary: "金鑰只由 OmniContext 本機後端讀取；瀏覽器不會取得、顯示或寫入金鑰內容。建議保存在作業系統的使用者環境變數。",
    btn_save_apply: "儲存並套用",
    settings_save_note: "寫回 config.yaml 後即時熱更新",
    settings_p4_title: "GitHub 雲端整合 (全專案與 PR 追蹤)",
    repo_sync_title: "本機 Git 同步中心",
    repo_sync_manual: "逐項手動確認",
    repo_sync_intro_title: "檢查本機 branch 與遠端追蹤差異",
    repo_sync_intro_detail: "狀態只讀取本機已保存的 remote-tracking refs。先按 Fetch 更新遠端參照，再依條件執行 fast-forward Pull、staged Commit 或 Push。",
    btn_repo_sync_refresh: "↻ 重讀同步狀態",
    repo_sync_loading: "正在讀取設定範圍內的 repositories…",
    repo_sync_boundary: "不會自動同步、不會自動 git add、不會 force push；Commit 只會提交您已明確 staged 的檔案。Onboarding 動作不覆寫非空目錄、不批次 create/clone、永不代為 push。",
    onboarding_title: "Repo Onboarding／對帳（P4.3）",
    onboarding_intro: "找出尚未 git init 的資料夾、沒有 remote 的 repo、以及尚未 clone 的 GitHub repo。已 clone 與否只以 remote URL 比對；同名僅提示、不自動配對。每個動作都是單一目標、逐一確認。",
    btn_onboarding_scan: "🔍 掃描對帳",
    usage_title: "TODAY · 前景使用與里程碑",
    btn_refresh_usage: "重新整理",
    usage_goal_label: "AI 協作前景使用時間",
    background_tasks_title: "BACKGROUND AGENT TASKS",
    background_tasks_label: "可驗證背景 agent／CLI 執行時間",
    background_tasks_boundary: "只納入本機來源可確認的開始與最終完成收據；不與前景使用時間混算，也不代表生產力或所有電腦工作。",
    extension_monitor_title: "EXTENSION MONITOR",
    btn_open_monitor: "開啟完整監控",
    capture_status_title: "DATA CAPTURE",
    btn_extension_details: "診斷",
    extension_pairing_boundary: "localhost 可觀察採集狀態；ingest token 仍由 Extension popup 安全保存。",
    settings_usage_title: "每日使用時間與里程碑",
    settings_usage_note: "前景時間，不代表生產力或實際工時",
    usage_tracking_enabled: "啟用使用時間統計",
    usage_notifications_enabled: "啟用里程碑通知",
    usage_daily_goal: "DAILY GOAL / MIN",
    usage_milestones: "MILESTONES / MIN",
    usage_tone: "TONE",
    usage_quiet_start: "QUIET FROM",
    usage_quiet_end: "QUIET UNTIL",
    usage_cooldown: "COOLDOWN / MIN",
    usage_local_note: "預設只在本機聚合，不呼叫 cloud LLM；介面分類規則可在 config.yaml 調整。",
    settings_executor_title: "小秘書執行器（ADR-008）",
    settings_executor_note: "預設關閉；每次執行仍需 execution token 與逐項批准",
    executor_enabled_label: "啟用執行器（L0/L1 白名單動作）",
    executor_l2_label: "啟用 L2：調度本機 agent CLI（需一次性確認碼）",
    executor_l2_write_label: "允許 L2 依已批准計畫修改檔案（不 commit）",
    executor_cli_label: "AGENT CLI",
    executor_boundary: "L2 會用您本機已登入的 CLI 消耗訂閱／API 額度；子行程禁 shell、僅限白名單動作與該專案 repo 目錄，不會拿到任何 API key。執行前仍需 execution token（python main.py init --show-token）＋單鍵批准＋回填一次性確認碼。",
    sched_tasks_enabled_label: "啟用自訂排程任務（僅 L0 唯讀動作）",
    sched_tasks_title: "自訂排程任務（P5-R5）",
    sched_tasks_loading: "載入中…",
    sched_template_label: "TEMPLATE",
    sched_project_label: "PROJECT KEY",
    sched_kind_label: "SCHEDULE",
    sched_kind_daily: "每日",
    sched_kind_weekly: "每週",
    sched_kind_monthly: "每月",
    sched_weekday_label: "WEEKDAY",
    sched_day_label: "DAY (1–28)",
    sched_time_label: "TIME",
    btn_add_sched_task: "＋ 新增排程",
    sched_tasks_boundary: "排程只會自動執行 L0 唯讀白名單動作（Handoff／週報／月報／STATUS 草稿）並寫 audit receipt；L1/L2 永不可排程。管理排程需 execution token。",
    settings_telegram_title: "Telegram 通知（遠端推播）",
    tg_step1_help: "① 在 Telegram 搜尋 @BotFather → /newbot 建立機器人 → 複製 API Token 貼到下方。",
    tg_token_label: "BOT TOKEN",
    tg_chat_label: "CHAT ID",
    btn_tg_detect: "🔍 偵測 CHAT ID",
    tg_step2_help: "② 先在 Telegram 對您的 bot 送出任意訊息（如 /start），再按「偵測 CHAT ID」選擇對話。",
    tg_morning_label: "晨報時間",
    tg_evening_label: "晚報時間",
    btn_tg_test: "📡 測試連線",
    btn_tg_connect: "✅ 測試並儲存啟用",
    btn_tg_disconnect: "解除",
    tg_boundary: "③ 「測試連線」會即時呼叫 getMe 驗證 token，並向所選對話實發一則固定內容的測試訊息；全部通過才會寫入本機 config.yaml 並啟用晨報／晚報推播。token 與 chat id 只存在本機，瀏覽器永遠拿不回明文（顯示為 ***REDACTED***）；若已設定環境變數 TELEGRAM_BOT_TOKEN／TELEGRAM_CHAT_ID 則優先使用且不會複製進檔案。inline 批准只處理綁定 chat 的按鈕、只能執行 L0/L1 白名單動作（L2 一律回儀表板）；批准通道需以 execution token 解鎖，重啟服務即自動上鎖。",
    tg_approvals_label: "啟用 inline 批准（晨報／晚報附「✅ 批准」按鈕，僅 L0/L1）",
    tg_secretary_chat_label: "啟用小秘書對話（手機上直接提問／記筆記；提問與回答會經過 Telegram）",
    btn_tg_arm_code: "🔑 產生解鎖碼",
    settings_line_title: "LINE 通知（只能推播）",
    line_token_label: "CHANNEL ACCESS TOKEN",
    line_to_label: "收件 USER ID",
    line_step1_help: "① 在 LINE Developers Console 建立 Messaging API channel → 發行「Channel access token（long-lived）」貼到下方。",
    line_step2_help: "② 用手機把這個官方帳號加為好友，再從 Console 的「Basic settings → Your user ID」複製 userId（U 開頭）貼上——那不是 LINE ID（@xxxx）。",
    btn_line_test: "📡 測試連線",
    btn_line_connect: "✅ 測試並儲存啟用",
    btn_line_disconnect: "解除",
    line_boundary: "LINE 只做推播（晨報／晚報／日報／停滯提醒）：LINE Messaging API 沒有輪詢介面，要接收你的訊息必須由 LINE 平台 webhook 連到一個公開網址，那會打破本專案「只在 127.0.0.1」的邊界——所以提問、記筆記與批准仍走 Telegram。另請注意 LINE 官方帳號免費方案有每月推播則數上限。token 與 userId 只存本機，瀏覽器永遠拿不回明文。",
    tg_remote_arm_label: "允許用 /arm <token> 從手機解鎖批准（訊息會被自動刪除；不開則只能在這裡解鎖）",
    tg_chat_boundary: "小秘書對話：手機上直接打字即可提問（會帶今日狀態、建議與記憶區筆記），「記下來：…」直接寫進記憶區不送 LLM，/today /notes /status /proposals 為指令。這是本專案唯一會把「你的提問與秘書的回答」送出本機的通道——內容會經過 Telegram 伺服器（引用只送檔名，不送文件內容）；若對話 provider 選雲端供應商，內容另會送往該供應商。預設關閉。",
    btn_tg_arm: "🔓 解鎖遠端批准（需 execution token）",
    btn_tg_disarm: "🔒 上鎖",
    gh_opt1_title: "快捷方式 1 (推薦)：本機 GITHUB CLI",
    btn_gh_auto_connect: "🔑 一鍵從本機 gh CLI 同步認證",
    gh_opt1_sub: "免手動輸入 Token，自動讀取本機登入之 GitHub 帳號",
    gh_opt2_title: "快捷方式 2：PERSONAL ACCESS TOKEN (PAT)",
    ph_gh_token: "ghp_xxxxxxxxxxxx (需勾選 repo 權限)",
    btn_connect: "連線",
    gh_opt2_sub: "支援 Fine-Grained 或 Classic PAT (讀取 Public & Private Repos/PRs)",
    btn_gh_sync: "🔄 立即同步 GitHub 專案與 PR 狀態",
    btn_gh_disconnect: "解除連線",
    label_date_range: "日期範圍 (RANGE)",
    chip_today: "今日",
    chip_yesterday: "昨日",
    chip_this_week: "本週",
    chip_7d: "近 7 天",
    chip_30d: "近 30 天",
    btn_generate_range: "⚡ 生成區間回顧",
    ph_loading_summaries: "載入歷史報告…",
    view_day: "日",
    view_week: "週",
    view_month: "月",
    btn_copy_markdown: "複製 Markdown",
    ph_summary_click: "請從左側點選報告日期，或點「生成」產出今日摘要。",
    btn_trigger_cp_now: "+ 產出快照",
    ph_loading_checkpoints: "載入快照日誌…",
    btn_copy_log: "複製 Log",
    ph_cp_click: "請從左側點選快照日誌以檢視期間活動細節。",
    rail_open_loops: "OPEN LOOPS",
    rail_focus_now: "FOCUS NOW",
    ph_loading_loops: "載入重點事項…",
    ph_no_loops: "目前沒有可輪播的重點事項。",
    focus_observed: "已觀測 · OPEN LOOP",
    focus_proposal: "唯讀建議 · PROPOSAL ONLY",
    focus_view_project: "查看專案 ↗",
    focus_resolve: "✓ 完成",
    focus_snooze: "7 天不提醒",
    focus_previous: "上一項",
    focus_next: "下一項",
    focus_pause: "暫停輪播",
    focus_play: "繼續輪播",
    focus_count: "第 {current} / {total} 張 · 未結 {open}",
    focus_boundary: "僅輪播前 5 項；已觀測事項可結案，建議不會自動執行。",
    data_trust_title: "DATA TRUST",
    data_trust_desc: "8 項 contract gates 已通過；runtime 仍以 collector probes 判定，coverage 不完整時不得升格。",
    trust_d1: "統一時區 (Local Timezone)",
    trust_d2: "檔案防手震與噪音過濾 (Debounce)",
    trust_d3: "Git 遞迴多倉庫掃描 (64 Repos)",
    trust_d4: "Claude / Codex / Antigravity 日誌",
    trust_d5: "AI 提問與完整回應解析",
    trust_d6: "設定熱套用與開關聯動",
    trust_d7: "GitHub 雲端倉庫與 PR 狀態整合",
    trust_d8: "中英文多語言 i18n 與專案智能歸戶",
    status_active: "活躍中",
    status_idle: "閒置 {days} 天",
    open_loop_count: "未結",
    sec_files_modified: "RECENT MODIFIED FILES (本次工作異動檔案)",
    sec_timeline: "ACTIVITY TIMELINE (活動時間軸)",
    sec_open_loops: "OPEN LOOPS (未結事項)",
    sec_gh_pr: "GITHUB REPO & PULL REQUESTS (遠端倉庫與 PR 追蹤)",
    btn_snapshot_now: "產出此刻快照",
    files_modified_summary: "異動 {files} 等共 {count} 個檔案",
    btn_copy_handoff: "📋 複製接續 Prompt",
    btn_copy_handoff_short: "📋 接續 Prompt",
    btn_show_more_projects: "▼ 展開更多 ({count} 個超過 60 天未活躍專案)",
    btn_collapse_projects: "▲ 收合超過 60 天未活躍專案",
    title_click_to_open_project: "點擊跳轉並展開此專案",
    title_mark_resolved: "標記為已結案"
  },
  "en": {
    lang_btn: "🌐 繁體中文",
    status_connected: "Connected",
    status_disconnected: "Disconnected",
    status_monitoring: "MONITORING",
    status_degraded: "MONITORING DEGRADED",
    status_paused: "PAUSED",
    btn_start_monitor: "Start Monitoring",
    btn_pause_monitor: "Pause Monitoring",
    btn_theme_light: "☀ Light",
    btn_theme_dark: "☾ Dark",
    palette_naruto: "🟠 Naruto Orange",
    palette_forest: "🟢 Forest Green",
    palette_ocean: "🔵 Ocean Blue",
    btn_quick_checkpoint: "⏱️ Checkpoint",
    btn_quick_summary: "⚡ Today's Summary",
    tab_assistant: "01 · 🤖 Assistant",
    tab_knowledge: "02 · 📚 Knowledge Base",
    tab_projects: "03 · Active Workstreams",
    tab_repos: "04 · 🔁 Git Sync Center",
    tab_summaries: "05 · Summaries & Stats",
    rail_stats_title: "Today's stats",
    today_title: "TODAY · Action list",
    greeting_title: "🤗 From your secretary",
    greeting_win_today: "Today",
    greeting_win_2h: "Last 2 h",
    greeting_name_label: "Greeting name",
    greeting_name_ph: "e.g. Dof (leave empty for no name)",
    ph_loading_greeting: "Your secretary is tallying what you did today…",
    btn_create_presets: "📦 Create daily schedules",
    today_resume_label: "Resume here",
    why_now_label: "Why now",
    memory_title: "🧠 Secretary memory",
    btn_memory_add: "Remember",
    btn_memory_context: "👁 What I remember now",
    btn_memory_clear_obs: "🧹 Clear observations",
    memory_kind_user_note: "Note",
    memory_kind_preference: "Preference",
    memory_kind_decision: "Decision",
    memory_project_ph: "Project (optional)",
    memory_input_ph: "Something the secretary should remember; a preference may say “mute <project or proposal type>”",
    memory_boundary: "Memory holds only short text you typed plus observations the secretary derives from local read-only receipts (deletable). The context injected into each chat is capped and comes with a receipt. Typing “remember: …” in the chat box writes here too.",
    memory_note_label: "You noted",
    ph_loading_memory: "Loading memory…",
    related_history_note: "Describe your current question to find similar history in the local semantic index",
    sec_sessions: "Recent work sessions",
    tab_settings: "06 · ⚙️ System Settings",
    snav_secretary: "🤖 Secretary & Automation",
    snav_telegram: "✈️ Telegram",
    snav_line: "💬 LINE",
    snav_paths: "📁 Monitored Paths",
    snav_sources: "🧲 Collector Sources",
    snav_llm: "🧠 Synthesis & LLM",
    snav_usage: "⏱️ Usage & Milestones",
    snav_github: "🐙 GitHub Integration",
    snav_group_ops: "Operations",
    snav_feed: "📡 Live Feed",
    snav_health: "🛡️ System Health",
    snav_acceptance: "🧾 Acceptance",
    acceptance_p1_title: "Live receipts and release convergence",
    acceptance_p2_title: "Item by item (docs/TODO.md section A)",
    acceptance_loading: "Checking local receipts…",
    acceptance_intro: "This only reads receipts that already exist on this machine: SQLite rows, config values, and whether files are there. It runs no acceptance step for you, and it never shells out to git, goes online, or loads an index. \"Receipt found\" means matching evidence exists — not that the feature is correct in every situation.",
    btn_refresh_acceptance: "Re-check",
    acceptance_st_passed: "Receipt found",
    acceptance_st_attested: "Attested by you",
    acceptance_st_partial: "Partial receipt",
    acceptance_st_pending: "Not yet",
    acceptance_st_needs_human: "Needs your eyes",
    acceptance_st_not_configured: "Not enabled",
    acceptance_st_runtime_only: "Visible only in the running service",
    acceptance_how: "How",
    acceptance_criterion: "Done when",
    acceptance_evidence: "Evidence found",
    acceptance_confirm_btn: "🖊 I checked this myself",
    acceptance_unconfirm_btn: "↩ Undo confirmation",
    acceptance_confirm_ask: "This records a human-signed confirmation, kept separate from machine evidence and never overriding a machine verdict. Continue?",
    acceptance_attested_by_you: "Confirmed by you at",
    acceptance_blocking: "still blocks release_ready",
    acceptance_gates_title: "Release gates (ROADMAP §12.3)",
    acceptance_outstanding: "Outstanding",
    settings_nav_hint: "Pick a section on the left; “Save & Apply” only affects configuration sections.",
    rag_merged_title: "Knowledge & RAG (full conversation, citations, index management)",
    rag_merged_note: "Shares the same conversation and history as the 01 Assistant chat box",
    tg_setup_summary: "Connection setup (bot token / chat id / test)",
    checkpoints_panel_title: "Activity Snapshots (periodic checkpoint logs)",
    checkpoints_panel_note: "Expand only when you need the raw per-period logs",
    assistant_title: "🤖 ASSISTANT · ASK OR DELEGATE",
    assistant_input_ph: "Ask for documents, summaries, or the next step…",
    btn_assistant_send: "Send ⚡",
    assistant_chat_empty: "Ask about documents or project progress, or request a next step — answers cite the local knowledge base and work history.",
    assistant_chat_boundary: "Chats use the local knowledge base (RAG) and the selected model; full citation cards and history live in the Knowledge & RAG block below.",
    system_health_p1_title: "System Runtime & Lifecycle Control",
    system_health_p2_title: "Collector Fault Isolation & Diagnostics Matrix",
    system_health_p2_note: "Isolated collectors; single failure never halts overall monitoring",
    system_health_p3_title: "Latest Maintenance Receipt & Backup Vault",
    system_health_p4_title: "Action Console & Live Output",
    label_system_state: "MONITORING STATE",
    label_db_size: "DATABASE SIZE",
    label_managed_projects: "MANAGED WORKSTREAMS",
    label_self_healing_status: "SELF-HEALING SUPERVISOR",
    btn_trigger_heal: "Trigger Self-Heal",
    btn_trigger_wal: "WAL Checkpoint",
    btn_trigger_maintain: "Full DB Maintenance",
    btn_refresh_health: "Refresh",
    btn_clear_console: "Clear Output",
    resume_head: "RESUME HERE",
    resume_sub: "Where You Left Off",
    context_sessions_title: "RECENT WORK SESSIONS",
    btn_refresh_sessions: "Refresh",
    ph_loading_sessions: "Grouping recent work sessions…",
    context_sessions_boundary: "Inferred from project labels and event gaps; not actual work time, continuous focus, or result quality.",
    related_memory_title: "RELATED HISTORY",
    related_memory_placeholder: "Describe the task or question you are working on…",
    btn_related_search: "Find Related History",
    related_memory_empty: "Enter a task description to search the local semantic index.",
    related_memory_boundary: "The query goes only to local Ollama and is not stored; similarity does not prove duplicate work or correct conclusions.",
    secretary_title: "SECRETARY SUGGESTIONS",
    btn_refresh_proposals: "Refresh",
    ph_loading_proposals: "Deriving traceable suggestions…",
    secretary_boundary: "Local evidence-derived suggestions; never persisted or auto-executed. Optional LLM notes are advisory only (local model by default).",
    btn_reindex_projects: "🔄 Refresh",
    ph_loading_projects: "Loading active workstreams…",
    ph_no_projects: "No active projects detected yet. Edits, commits, and Claude / Codex / Antigravity sessions will populate this view.",
    active_workstreams: "ACTIVE WORKSTREAMS",
    feed_title: "LIVE FEED",
    filter_all: "ALL",
    filter_ai: "AI",
    filter_file: "FILE",
    filter_git: "GIT",
    filter_window: "WINDOW",
    ph_loading_feed: "Loading live feed…",
    ph_no_feed: "No activity recorded yet.",
    collectors_title: "COLLECTORS",
    collector_file: "File Watcher",
    collector_git: "Git Scanner",
    collector_window: "Window Focus",
    collector_agent: "Agent Logs",
    collector_calendar: "Calendar (.ics)",
    collector_scheduler: "Scheduler",
    cal_title: "📅 Local calendar (.ics, read-only, ADR-015)",
    cal_enabled_label: "Collect calendar events (no paths = off)",
    cal_paths_label: "ICS files or folders containing .ics",
    ph_ics_path: "e.g. C:/Users/you/Calendars/work.ics or a folder",
    cal_horizon_label: "Expand recurring events (days ahead)",
    cal_store_titles_label: "Store titles and locations (off = times only)",
    cal_note: "Reads only time, title, location and status from local .ics files; descriptions, attendees and links are never stored, no cloud API is called and nothing is written back. Export or sync your Outlook/Google/Apple calendar to a local folder; the briefing and greeting card will reflect today's schedule.",
    collector_enabled: "● Active",
    collector_disabled: "○ Off",
    settings_p1_title: "Monitored Paths",
    label_file_dirs: "FILE DIRS",
    label_git_roots: "GIT ROOTS",
    btn_browse: "📁 Browse…",
    btn_add: "Add",
    ph_abs_path: "Absolute path…",
    ph_git_path: "Git root path…",
    git_recursive_note: "Recursive scanning enabled: all nested repositories under root will be indexed.",
    settings_p2_title: "Collector Sources",
    settings_p2_note: "Right column indicates current validation status",
    settings_p3_title: "Synthesis & Schedules",
    llm_key_status_title: "API KEY STATUS",
    llm_key_env_label: "ENVIRONMENT VARIABLE",
    btn_recheck_llm_key: "Recheck",
    llm_key_boundary: "Keys are read only by the local OmniContext backend. The browser never receives, displays, or writes secret values. Store them in the OS user environment.",
    btn_save_apply: "Save & Apply",
    settings_save_note: "Hot reloaded directly into config.yaml",
    settings_p4_title: "GitHub Cloud Integration (Public & Private Repos + PRs)",
    repo_sync_title: "Local Git Sync Center",
    repo_sync_manual: "Manual confirmation per repository",
    repo_sync_intro_title: "Check local branches against tracked remotes",
    repo_sync_intro_detail: "Status reads locally cached remote-tracking refs only. Fetch refreshes refs, then Fast-forward Pull, staged Commit, or Push is enabled only when safe.",
    btn_repo_sync_refresh: "↻ Refresh sync status",
    repo_sync_loading: "Reading repositories within configured scope…",
    repo_sync_boundary: "No scheduled automatic sync, git add, or force push. Commit only includes files you explicitly staged. Batch Pull/Push only runs on the list you confirmed and rechecks each repository; batch Push is off by default. Onboarding actions never overwrite non-empty directories, never batch create/clone, and never push on your behalf.",
    repo_overview_title: "Overview & batch",
    repo_overview_intro: "Lists every repository in the configured roots. “Fetch all” only refreshes remote-tracking refs; batch Pull/Push first shows the list of repositories that currently qualify, rechecks each one when it runs, skips the rest and never forces.",
    btn_repo_overview_load: "📋 Load overview",
    btn_repo_fetch_all: "🔄 Fetch all",
    btn_repo_batch_pull: "⬇ Batch Pull (FF only)",
    btn_repo_batch_push: "⬆ Batch Push",
    onboarding_title: "Repo Onboarding / Reconciliation (P4.3)",
    onboarding_intro: "Finds folders without git init, repos without a remote, and GitHub repos not cloned locally. Cloned-or-not is decided by remote URL only; same names are just hints, never auto-paired. Every action targets one repo with explicit confirmation.",
    btn_onboarding_scan: "🔍 Scan & reconcile",
    usage_title: "TODAY · FOREGROUND USE & MILESTONES",
    btn_refresh_usage: "Refresh",
    usage_goal_label: "AI collaboration foreground time",
    background_tasks_title: "BACKGROUND AGENT TASKS",
    background_tasks_label: "Verified background agent / CLI execution time",
    background_tasks_boundary: "Only paired local start and explicit final-completion receipts are included. This is separate from foreground time, productivity, and all computer work.",
    extension_monitor_title: "EXTENSION MONITOR",
    btn_open_monitor: "Open Full Monitor",
    capture_status_title: "DATA CAPTURE",
    btn_extension_details: "Details",
    extension_pairing_boundary: "localhost can observe capture state; the ingest token remains in the Extension popup.",
    settings_usage_title: "Daily Usage & Milestones",
    settings_usage_note: "Foreground time, not productivity or actual work hours",
    usage_tracking_enabled: "Enable usage analytics",
    usage_notifications_enabled: "Enable milestone notifications",
    usage_daily_goal: "DAILY GOAL / MIN",
    usage_milestones: "MILESTONES / MIN",
    usage_tone: "TONE",
    usage_quiet_start: "QUIET FROM",
    usage_quiet_end: "QUIET UNTIL",
    usage_cooldown: "COOLDOWN / MIN",
    usage_local_note: "Aggregated locally without a cloud LLM; interface rules remain configurable in config.yaml.",
    settings_executor_title: "Secretary Executor (ADR-008)",
    settings_executor_note: "Off by default; every run still needs the execution token and per-item approval",
    executor_enabled_label: "Enable executor (L0/L1 whitelist actions)",
    executor_l2_label: "Enable L2: dispatch local agent CLI (one-time confirm code)",
    executor_l2_write_label: "Allow L2 to edit files per an approved plan (never commits)",
    executor_cli_label: "AGENT CLI",
    executor_boundary: "L2 spends your locally signed-in CLI quota; subprocesses are shell-free, restricted to whitelist actions and the project's repo directory, and never receive any API key. Runs still require the execution token (python main.py init --show-token), one-click approval, and a one-time confirm code.",
    sched_tasks_enabled_label: "Enable custom scheduled tasks (L0 read-only actions only)",
    sched_tasks_title: "Custom Scheduled Tasks (P5-R5)",
    sched_tasks_loading: "Loading…",
    sched_template_label: "TEMPLATE",
    sched_project_label: "PROJECT KEY",
    sched_kind_label: "SCHEDULE",
    sched_kind_daily: "Daily",
    sched_kind_weekly: "Weekly",
    sched_kind_monthly: "Monthly",
    sched_weekday_label: "WEEKDAY",
    sched_day_label: "DAY (1–28)",
    sched_time_label: "TIME",
    btn_add_sched_task: "＋ Add schedule",
    sched_tasks_boundary: "Schedules only auto-run L0 read-only whitelist actions (Handoff / weekly / monthly rollup / STATUS draft) and always leave an audit receipt; L1/L2 can never be scheduled. Managing schedules requires the execution token.",
    settings_telegram_title: "Telegram Notifications (Remote Push)",
    tg_step1_help: "① In Telegram, find @BotFather → /newbot to create a bot → paste its API token below.",
    tg_token_label: "BOT TOKEN",
    tg_chat_label: "CHAT ID",
    btn_tg_detect: "🔍 Detect CHAT ID",
    tg_step2_help: "② Send any message (e.g. /start) to your bot in Telegram first, then press Detect CHAT ID and pick the chat.",
    tg_morning_label: "MORNING",
    tg_evening_label: "EVENING",
    btn_tg_test: "📡 Test connection",
    btn_tg_connect: "✅ Test, save & enable",
    btn_tg_disconnect: "Disconnect",
    tg_boundary: "③ Test connection calls getMe live to validate the token and sends one fixed test message to the selected chat; only when everything passes are the settings written to local config.yaml and the morning/evening pushes enabled. Token and chat id stay on this machine — the browser never gets the plaintext back (shown as ***REDACTED***). If TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID env vars exist they take precedence and are never copied into the file. Inline approvals only accept buttons from the bound chat and only run L0/L1 whitelist actions (L2 always goes back to the dashboard); the approval channel must be unlocked with the execution token and re-locks on every service restart.",
    tg_approvals_label: "Enable inline approvals (✅ buttons on morning/evening pushes, L0/L1 only)",
    tg_secretary_chat_label: "Enable secretary chat (ask and take notes from your phone; questions and answers pass through Telegram)",
    btn_tg_arm_code: "🔑 Issue unlock code",
    settings_line_title: "LINE notifications (push only)",
    line_token_label: "CHANNEL ACCESS TOKEN",
    line_to_label: "RECIPIENT USER ID",
    line_step1_help: "① In the LINE Developers Console create a Messaging API channel, then issue a long-lived channel access token and paste it below.",
    line_step2_help: "② Add the official account as a friend on your phone, then copy the userId (starts with U) from Basic settings → Your user ID — that is not the LINE ID (@xxxx).",
    btn_line_test: "📡 Test connection",
    btn_line_connect: "✅ Test and save",
    btn_line_disconnect: "Disconnect",
    line_boundary: "LINE is push-only (morning/evening briefings, daily report, stagnation alerts). The LINE Messaging API has no polling endpoint: receiving your messages would require LINE to reach a public webhook, which would break this project's 127.0.0.1-only boundary — so asking, note-taking and approvals stay on Telegram. Note also that the free LINE official account plan caps monthly push messages. Token and userId stay on this machine; the browser never gets the plaintext back.",
    tg_remote_arm_label: "Allow /arm <token> to unlock approvals from the phone (the message is deleted automatically; otherwise unlock here only)",
    tg_chat_boundary: "Secretary chat: type in the bound chat to ask (today's status, proposals and your notes are included); “remember: …” writes straight to memory without calling the LLM; /today /notes /status /proposals are commands. This is the only channel that sends your questions and the secretary's answers off this machine — content passes through Telegram's servers (citations send filenames only, never document content), and a cloud chat provider also receives the content. Off by default.",
    btn_tg_arm: "🔓 Unlock remote approvals (execution token)",
    btn_tg_disarm: "🔒 Lock",
    gh_opt1_title: "Option 1 (Recommended): Local GITHUB CLI",
    btn_gh_auto_connect: "🔑 1-Click Auth via Local gh CLI",
    gh_opt1_sub: "No manual PAT needed, automatically reads local logged-in GitHub account",
    gh_opt2_title: "Option 2: PERSONAL ACCESS TOKEN (PAT)",
    ph_gh_token: "ghp_xxxxxxxxxxxx (with repo scope)",
    btn_connect: "Connect",
    gh_opt2_sub: "Supports Fine-Grained or Classic PAT (fetches Public & Private Repos/PRs)",
    btn_gh_sync: "🔄 Sync GitHub Repos & PRs Now",
    btn_gh_disconnect: "Disconnect",
    label_date_range: "DATE RANGE",
    chip_today: "Today",
    chip_yesterday: "Yesterday",
    chip_this_week: "This Week",
    chip_7d: "Last 7 Days",
    chip_30d: "Last 30 Days",
    btn_generate_range: "⚡ Generate Range Review",
    ph_loading_summaries: "Loading history summaries…",
    view_day: "Day",
    view_week: "Week",
    view_month: "Month",
    btn_copy_markdown: "Copy Markdown",
    ph_summary_click: "Select a date on the left, or click Generate to synthesize today's review.",
    btn_trigger_cp_now: "+ New Checkpoint",
    ph_loading_checkpoints: "Loading checkpoint logs…",
    btn_copy_log: "Copy Log",
    ph_cp_click: "Select a checkpoint log on the left to inspect detailed activity records.",
    rail_open_loops: "OPEN LOOPS",
    rail_focus_now: "FOCUS NOW",
    ph_loading_loops: "Loading focus items…",
    ph_no_loops: "No focus items to rotate.",
    focus_observed: "OBSERVED · OPEN LOOP",
    focus_proposal: "READ-ONLY · PROPOSAL",
    focus_view_project: "View project ↗",
    focus_resolve: "✓ Done",
    focus_snooze: "Snooze 7d",
    focus_previous: "Previous item",
    focus_next: "Next item",
    focus_pause: "Pause rotation",
    focus_play: "Resume rotation",
    focus_count: "Card {current} / {total} · {open} open",
    focus_boundary: "Only the top five rotate. Observed items can be resolved; proposals never execute automatically.",
    data_trust_title: "DATA TRUST",
    data_trust_desc: "Eight contract gates passed; runtime still depends on collector probes and incomplete coverage is never promoted.",
    trust_d1: "Unified Timezone (Local TZ)",
    trust_d2: "File Debounce & Noise Filter",
    trust_d3: "Git Recursive Multi-Repo Scan (64 Repos)",
    trust_d4: "Claude / Codex / Antigravity Logs",
    trust_d5: "AI Prompts & Full Assistant Responses",
    trust_d6: "Settings Hot Reload & Sync",
    trust_d7: "GitHub Cloud Integration & PR Tracking",
    trust_d8: "Bilingual i18n & Canonical Hierarchy",
    status_active: "Active",
    status_idle: "Idle {days}d",
    open_loop_count: "Open",
    sec_files_modified: "RECENT MODIFIED FILES",
    sec_timeline: "ACTIVITY TIMELINE",
    sec_open_loops: "OPEN LOOPS",
    sec_gh_pr: "GITHUB REPO & PULL REQUESTS",
    btn_snapshot_now: "Snapshot Now",
    files_modified_summary: "Modified {files} ({count} files total)",
    btn_copy_handoff: "📋 Copy Handoff Prompt",
    btn_copy_handoff_short: "📋 Handoff"
  }
};

function t(key, vars = {}) {
  const dict = I18N[currentLang] || I18N["zh-TW"];
  let str = dict[key] || (I18N["zh-TW"] && I18N["zh-TW"][key]) || key;
  for (const [k, v] of Object.entries(vars)) {
    str = str.replace(new RegExp(`\\{${k}\\}`, 'g'), v);
  }
  return str;
}

function applyLanguage(lang) {
  currentLang = lang;
  localStorage.setItem("omni-lang", lang);
  document.documentElement.lang = lang === "zh-TW" ? "zh-TW" : "en";

  // 更新所有 data-i18n 節點文字
  document.querySelectorAll("[data-i18n]").forEach(el => {
    const k = el.dataset.i18n;
    if (k && I18N[currentLang][k]) {
      el.textContent = t(k);
    }
  });

  // 更新所有 placeholder
  document.querySelectorAll("[data-i18n-ph]").forEach(el => {
    const k = el.dataset.i18nPh;
    if (k && I18N[currentLang][k]) {
      el.placeholder = t(k);
    }
  });

  // 更新語言按鈕標籤
  const langBtn = $("btn-lang");
  if (langBtn) langBtn.textContent = t("lang_btn");

  paintThemeBtn();
  refreshStatus();
  renderResume();
  renderProjects();
  renderOpenLoops();
  renderContextSessions();
  renderSecretaryProposals();
  renderRepositorySyncStatus();
  if (relatedContextCache) renderRelatedContext(relatedContextCache);
  if (acceptanceCache) renderAcceptance();
  if ($("llm-key-status-badge")) renderLLMStatus();
}

function initLanguage() {
  const langBtn = $("btn-lang");
  if (langBtn) {
    langBtn.addEventListener("click", () => {
      const nextLang = currentLang === "zh-TW" ? "en" : "zh-TW";
      applyLanguage(nextLang);
    });
  }
  applyLanguage(currentLang);
}

async function getJSON(url) {
  const res = await fetch(API + url);
  if (!res.ok) throw new Error(url + " → " + res.status);
  return res.json();
}
async function postJSON(url, body) {
  const res = await fetch(API + url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  if (!res.ok) {
    let detail = "";
    try { detail = (await res.json()).detail || ""; } catch (_) { /* 保留 HTTP fallback */ }
    throw new Error(detail || (url + " → " + res.status));
  }
  return res.json();
}

document.addEventListener("DOMContentLoaded", () => {
  initLanguage();
  initTheme();
  initTabs();
  initSettingsNav();
  initControls();
  initSettingsForm();
  initGitHubSection();
  initRepositorySyncSection();
  initSummariesTab();
  initCheckpointsTab();
  initRAGTab();
  initSystemHealthTab();
  initFocusCarousel();
  initAssistantHome();

  initCollapsiblePanels();

  refreshStatus();
  refreshFeed();
  loadProjects();
  loadOpenLoops();
  loadConfig();
  loadGitHubStatus();
  loadSummaries();
  loadCheckpoints();
  loadUsagePanels();
  loadSecretaryProposals();
  loadAssistantStrip();
  loadTodayView();
  loadMemoryPanel();
  initMemoryPanel();
  loadRepoSnapshot();
  loadRAGFolders();
  loadRAGSessions();
  loadRAGStrategies();
  loadSystemHealth();

  setInterval(() => { refreshStatus(); refreshFeed(); }, POLL_MS);
  setInterval(loadUsagePanels, 30000);
  setInterval(loadAssistantStrip, 30000);
  // 問候卡：小秘書每 10 分鐘自動重新整理（統計只讀本機，成本很低）
  loadGreeting();
  initGreetingCard();
  setInterval(loadGreeting, 10 * 60 * 1000);
});

// ---------------------------------------------------- collapsible panels
function initCollapsiblePanels() {
  document.querySelectorAll("details.panel-collapsible[id], details.sub-collapsible[id]").forEach(panel => {
    const key = `omni-panel-open:${panel.id}`;
    try {
      const saved = localStorage.getItem(key);
      if (saved === "1") panel.open = true;
      if (saved === "0") panel.open = false;
    } catch (_) { /* localStorage 不可用時維持 HTML 預設 */ }
    panel.addEventListener("toggle", () => {
      try { localStorage.setItem(key, panel.open ? "1" : "0"); } catch (_) { /* 同上 */ }
    });
  });
}

// ---------------------------------------------------------------- theme
// 外觀 = 明暗（data-theme）× 配色（data-accent）兩個獨立的軸；
// 兩者都只存在瀏覽器 localStorage，不寫入 config.yaml、不送往後端。
const PALETTES = ["naruto", "forest", "ocean"];

function initTheme() {
  const saved = localStorage.getItem("omni-theme");
  if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
  paintThemeBtn();
  $("btn-theme").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("omni-theme", next);
    paintThemeBtn();
  });

  const select = $("select-palette");
  let palette = "naruto";
  try {
    const savedPalette = localStorage.getItem("omni-palette");
    if (PALETTES.includes(savedPalette)) palette = savedPalette;
  } catch (_) { /* localStorage 不可用時維持預設配色 */ }
  applyPalette(palette);
  if (select) {
    select.value = palette;
    select.addEventListener("change", () => applyPalette(select.value));
  }
}

function applyPalette(palette) {
  const next = PALETTES.includes(palette) ? palette : "naruto";
  // naruto 是 CSS 的預設值，不需要屬性；移除屬性可讓舊版樣式完全一致。
  if (next === "naruto") delete document.documentElement.dataset.accent;
  else document.documentElement.dataset.accent = next;
  try { localStorage.setItem("omni-palette", next); } catch (_) { /* 同上 */ }
}
function paintThemeBtn() {
  const isDark = document.documentElement.dataset.theme === "dark";
  $("btn-theme").textContent = isDark ? t("btn_theme_light") : t("btn_theme_dark");
}

// ---------------------------------------------------------------- tabs
function initTabs() {
  document.querySelectorAll(".tab").forEach(tab => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
      document.querySelectorAll(".pane").forEach(p => p.classList.remove("active"));
      tab.classList.add("active");
      const id = tab.dataset.tab;
      $(id).classList.add("active");
      if (id === "tab-assistant") { loadSecretaryProposals(); loadAssistantStrip(); syncAssistantModelControls(); loadProjects(); loadTodayView(); loadMemoryPanel(); loadGreeting(); }
      if (id === "tab-knowledge") { loadRAGFolders(); loadRAGSessions(); loadRAGProgress(); }
      if (id === "tab-projects") { loadProjects(); loadRepoSnapshot(); }
      if (id === "tab-repos") loadRepositorySyncStatus();  // 切到分頁才掃描本機 Git，不在開頁時付這個成本
      if (id === "tab-settings") { loadConfig(); activateSettingsPane(currentSettingsPane()); }
      if (id === "tab-summaries") { loadSummaries(); loadCheckpoints(); loadUsagePanels(); }
    });
  });
}

// ---------------------------------------------------------------- 06 系統設定：左欄切換
const SETTINGS_PANE_KEY = "omni-settings-pane";
const SETTINGS_CONFIG_PANES = new Set(["secretary", "telegram", "line", "paths", "sources", "llm", "usage", "github"]);

function currentSettingsPane() {
  let saved = null;
  try { saved = localStorage.getItem(SETTINGS_PANE_KEY); } catch (_) { /* 無 localStorage 時用預設 */ }
  return saved && document.querySelector(`.settings-pane[data-pane="${saved}"]`) ? saved : "secretary";
}

function activateSettingsPane(key) {
  const pane = document.querySelector(`.settings-pane[data-pane="${key}"]`);
  if (!pane) return;
  document.querySelectorAll(".settings-pane").forEach(p => p.classList.toggle("active", p === pane));
  document.querySelectorAll(".settings-nav-item").forEach(b => b.classList.toggle("active", b.dataset.pane === key));
  try { localStorage.setItem(SETTINGS_PANE_KEY, key); } catch (_) { /* 同上 */ }
  // 儲存列只對設定類區塊有意義；情報流與系統健康是唯讀觀察
  const bar = document.querySelector(".settings-actionbar");
  if (bar) bar.hidden = !SETTINGS_CONFIG_PANES.has(key);
  if (key === "feed") { refreshStatus(); refreshFeed(); }
  if (key === "health") loadSystemHealth();
  if (key === "acceptance") loadAcceptance();
}

function initSettingsNav() {
  document.querySelectorAll(".settings-nav-item").forEach(btn => {
    btn.addEventListener("click", () => activateSettingsPane(btn.dataset.pane));
  });
  activateSettingsPane(currentSettingsPane());
}

// 其他分頁若要導到系統健康／情報流，走這個入口而不是直接切分頁
function openSettingsPane(key) {
  const tab = document.querySelector('.tab[data-tab="tab-settings"]');
  if (tab) tab.click();
  activateSettingsPane(key);
}

// ---------------------------------------------------------------- controls
function initControls() {
  $("btn-toggle-monitor").addEventListener("click", async () => {
    try {
      await postJSON(isMonitoring ? "/api/v1/control/stop" : "/api/v1/control/start");
      refreshStatus();
    } catch (e) { console.error(e); }
  });

  $("btn-quick-checkpoint").addEventListener("click", triggerCheckpoint);
  $("btn-trigger-cp-now").addEventListener("click", triggerCheckpoint);
  $("btn-quick-summary").addEventListener("click", () => generateSummary(null));
  $("btn-refresh-projects").addEventListener("click", () => loadProjects(true));
  $("btn-refresh-usage").addEventListener("click", loadUsagePanels);
  const acceptanceBtn = $("btn-refresh-acceptance");
  if (acceptanceBtn) acceptanceBtn.addEventListener("click", loadAcceptance);
  const presetBtn = $("btn-create-presets");
  if (presetBtn) presetBtn.addEventListener("click", createSchedulePresets);
  $("btn-refresh-proposals").addEventListener("click", loadSecretaryProposals);
  $("btn-related-search").addEventListener("click", searchRelatedContext);
  $("input-related-question").addEventListener("keydown", event => {
    if (event.key === "Enter") searchRelatedContext();
  });

  document.querySelectorAll(".filters .chip").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".filters .chip").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      activeFilter = btn.dataset.filter;
      refreshFeed();
    });
  });
}

async function refreshStatus() {
  try {
    const data = await getJSON("/api/v1/control/status");
    isMonitoring = data.is_running;
    const monitoringState = data.monitoring_state || (isMonitoring ? "healthy" : "stopped");

    const pill = $("status-pill");
    pill.className = "pill " + (monitoringState === "degraded" ? "pill-warn" : isMonitoring ? "pill-on" : "pill-off");
    $("status-text").textContent = monitoringState === "degraded"
      ? t("status_degraded")
      : isMonitoring ? t("status_monitoring") : t("status_paused");
    $("btn-toggle-monitor").textContent = isMonitoring ? t("btn_pause_monitor") : t("btn_start_monitor");

    renderStats(data.metrics);
    renderCollectors(data.watchers, data.last_events || {}, data.collector_health || {}, data.collector_diagnostics || {});
    renderRuntimeTrust(monitoringState, data.degraded_collectors || []);
    $("last-refresh").textContent = "updated " + new Date().toLocaleTimeString();
  } catch (e) {
    $("status-text").textContent = t("status_disconnected");
    $("status-pill").className = "pill pill-off";
    renderRuntimeTrust("disconnected", []);
  }
}

function renderRuntimeTrust(state, degradedCollectors) {
  const badge = $("data-trust-runtime-badge");
  if (!badge) return;
  const degraded = state === "degraded";
  const healthy = state === "healthy";
  badge.className = `mono-mini runtime-trust-badge ${healthy ? "runtime-ok" : degraded ? "runtime-degraded" : "runtime-stopped"}`;
  if (healthy) {
    badge.textContent = "8/8 CONTRACT · RUNTIME OK ▾";
  } else if (degraded) {
    badge.textContent = `8/8 CONTRACT · ${degradedCollectors.length} DEGRADED ▾`;
  } else {
    badge.textContent = `8/8 CONTRACT · ${state === "stopped" ? "STOPPED" : "DISCONNECTED"} ▾`;
  }
}

function formatUsageDuration(seconds) {
  const totalMinutes = Math.max(0, Math.round(Number(seconds || 0) / 60));
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (!hours) return `${minutes}m`;
  return minutes ? `${hours}h ${minutes}m` : `${hours}h`;
}

async function loadUsagePanels() {
  const [usageResult, extensionResult, captureResult, backgroundTaskResult] = await Promise.allSettled([
    getJSON("/api/v1/usage/today"),
    getJSON("/api/v1/extension/status"),
    getJSON("/api/v1/capture/status"),
    getJSON("/api/v1/background-tasks/today")
  ]);
  if (usageResult.status === "fulfilled") renderUsagePanel(usageResult.value);
  else renderUsagePanelError();
  if (captureResult.status === "fulfilled") renderCaptureCoverage(
    captureResult.value,
    extensionResult.status === "fulfilled" ? extensionResult.value : null
  );
  else renderCaptureCoverageError();
  if (backgroundTaskResult.status === "fulfilled") renderBackgroundTaskPanel(backgroundTaskResult.value);
  else renderBackgroundTaskPanelError();
}

function renderUsagePanel(data) {
  const goal = data.goal || {};
  const coverage = data.coverage_status || "unavailable";
  const coverageBadge = $("usage-coverage");
  coverageBadge.className = "trust " + (coverage === "complete" ? "ok" : coverage === "partial" ? "noisy" : "broken");
  coverageBadge.textContent = coverage.toUpperCase();

  $("usage-goal-value").textContent = formatUsageDuration(goal.foreground_seconds || 0);
  const progress = Number(goal.progress_percent || 0);
  const goalText = currentLang === "zh-TW"
    ? `${goal.label || "AI 協作"}：${Number(goal.foreground_minutes || 0).toFixed(1)} / ${goal.daily_goal_minutes || 0} 分鐘（${progress.toFixed(1)}%）`
    : `${goal.label || "AI collaboration"}: ${Number(goal.foreground_minutes || 0).toFixed(1)} / ${goal.daily_goal_minutes || 0} min (${progress.toFixed(1)}%)`;
  $("usage-goal-progress").textContent = goalText;
  $("usage-progress-bar").style.width = `${Math.min(100, Math.max(0, progress))}%`;
  const dataUpdated = data.data_updated_at ? new Date(data.data_updated_at).toLocaleString() : "—";
  $("usage-boundary").textContent = currentLang === "zh-TW"
    ? `只代表已觀察到的前景時間，不代表生產力或實際工時。Coverage：${data.coverage_note || coverage}；最後資料：${dataUpdated}`
    : `Observed foreground time only; not productivity or actual work hours. Coverage: ${data.coverage_note || coverage}; last data: ${dataUpdated}`;

  const rows = (data.interfaces || [])
    .filter(item => Number(item.foreground_seconds || 0) > 0 || Number(item.ai_interaction_count || 0) > 0)
    .slice(0, 8);
  $("usage-interface-list").innerHTML = rows.length ? rows.map(item => `
    <div class="usage-row">
      <span class="usage-interface">${esc(item.name)}</span>
      <span class="usage-duration">${formatUsageDuration(item.foreground_seconds)}</span>
      <span class="usage-interactions">${Number(item.ai_interaction_count || 0)} turns</span>
    </div>`).join("") : `<div class="placeholder">${currentLang === "zh-TW" ? "今日尚無已觀察到的介面使用資料。" : "No observed interface usage today."}</div>`;
}

function renderUsagePanelError() {
  $("usage-coverage").className = "trust broken";
  $("usage-coverage").textContent = "UNAVAILABLE";
  $("usage-interface-list").innerHTML = `<div class="placeholder">${currentLang === "zh-TW" ? "無法載入使用時間。" : "Unable to load usage data."}</div>`;
}

function captureStateLabel(state) {
  const labels = currentLang === "zh-TW" ? {
    observed: "已觀察", waiting: "等待資料", available_waiting: "可讀取／待掃描",
    cache_detected_unparsed: "快取存在／未解析", unsupported: "目前不支援", not_applicable: "不適用"
  } : {
    observed: "OBSERVED", waiting: "WAITING", available_waiting: "READY TO SCAN",
    cache_detected_unparsed: "CACHE / UNPARSED", unsupported: "UNSUPPORTED", not_applicable: "N/A"
  };
  return labels[state] || String(state || "UNKNOWN").toUpperCase();
}

function renderCaptureCoverage(data, extensionData) {
  const platforms = data.platforms || [];
  const observed = platforms.reduce((total, item) => total + [item.desktop_focus, item.web_capture, item.transcript_capture].filter(channel => channel.state === "observed").length, 0);
  const badge = $("capture-coverage-badge");
  badge.className = "trust " + (observed > 0 ? "ok" : "noisy");
  badge.textContent = `${observed} OBSERVED`;
  const extension = (extensionData && extensionData.extension) || null;
  $("capture-extension-summary").textContent = extension
    ? (currentLang === "zh-TW"
      ? `Browser Extension：今日 ${Number(extension.events_today || 0)} events · ${extension.heartbeat_verified ? "近期已連線" : "尚無近期 heartbeat"}`
      : `Browser Extension: ${Number(extension.events_today || 0)} events today · ${extension.heartbeat_verified ? "recent heartbeat" : "no recent heartbeat"}`)
    : (currentLang === "zh-TW" ? "Browser Extension 狀態目前無法取得。" : "Browser Extension status is unavailable.");
  const signal = (label, item, detail) => `<div class="capture-signal ${esc(item.state || "waiting")}" title="${esc(captureStateLabel(item.state))}">
    <span class="capture-signal-label">${label}</span>
    <span class="capture-signal-value">${detail}</span>
  </div>`;
  $("capture-coverage-list").innerHTML = platforms.map(item => {
    const focus = item.desktop_focus || {};
    const web = item.web_capture || {};
    const transcript = item.transcript_capture || {};
    return `<div class="capture-coverage-row">
      <div class="capture-platform-name">${esc(item.label)}</div>
      ${signal("FOCUS", focus, focus.state === "observed" ? formatUsageDuration(focus.foreground_seconds_today || 0) : "—")}
      ${signal("WEB", web, web.state === "observed" ? `${Number(web.turns_today || 0)} / ${Number(web.responses_today || 0)}` : "—")}
      ${signal("LOG", transcript, transcript.state === "observed" ? `${Number(transcript.turns_today || 0)} / ${Number(transcript.responses_today || 0)}` : "—")}
    </div>`;
  }).join("");
  $("capture-coverage-boundary").textContent = currentLang === "zh-TW"
    ? "FOCUS 只記前景時間；WEB 為 Extension turns／responses；LOG 為本機 transcript turns／responses。三者不能互相替代。"
    : "FOCUS is foreground time; WEB is Extension turns/responses; LOG is local transcript turns/responses. These signals are independent.";
}

function renderCaptureCoverageError() {
  $("capture-coverage-badge").className = "trust broken";
  $("capture-coverage-badge").textContent = "UNAVAILABLE";
  $("capture-extension-summary").textContent = currentLang === "zh-TW" ? "採集狀態暫時無法取得。" : "Capture status is unavailable.";
  $("capture-coverage-list").innerHTML = `<div class="placeholder">${currentLang === "zh-TW" ? "無法取得採集 coverage。" : "Capture coverage is unavailable."}</div>`;
}

function renderStats(m) {
  const items = [
    { tag: "AI TURNS", value: m.ai_prompts_count, label: "Claude / Codex / Web", trust: "ok" },
    { tag: "FILES", value: m.file_events_count, label: currentLang === "zh-TW" ? "論文與檔案異動" : "Paper & File Events", trust: "ok" },
    { tag: "COMMITS", value: m.git_commits_count, label: currentLang === "zh-TW" ? "Git commits 提交" : "Git Commits", trust: "ok" },
    { tag: "FOCUS", value: m.window_events_count, label: currentLang === "zh-TW" ? "視窗焦點切換" : "Window Focus", trust: "ok" },
    { tag: "STREAMS", value: projectsCache.length, label: currentLang === "zh-TW" ? "進行中工作" : "Active Workstreams", trust: "ok" }
  ];
  const dot = { ok: "var(--orange)", noisy: "var(--warn)", broken: "var(--danger)" };
  $("stats-strip").innerHTML = items.map(s => `
    <div class="stat">
      <div class="stat-top">
        <span class="mono-mini muted">${s.tag}</span>
        <span class="stat-dot" style="background:${dot[s.trust]}" title="${s.trust}"></span>
      </div>
      <div class="stat-value">${Number(s.value || 0).toLocaleString()}</div>
      <div class="stat-label">${s.label}</div>
    </div>`).join("");
}

function renderCollectors(w, lastEvents = {}, health = {}, diagnostics = {}) {
  const items = [
    { key: "file_watcher", name: t("collector_file"), on: w.file_watcher, last: lastEvents.file_watcher, h: health.file_watcher || "stale", d: diagnostics.file_watcher },
    { key: "git_watcher", name: t("collector_git"), on: w.git_watcher, last: lastEvents.git_watcher, h: health.git_watcher || "stale", d: diagnostics.git_watcher },
    { key: "window_watcher", name: t("collector_window"), on: w.window_watcher, last: lastEvents.window_watcher, h: health.window_watcher || "stale", d: diagnostics.window_watcher },
    { key: "agent_log_watcher", name: t("collector_agent"), on: w.agent_log_watcher, last: lastEvents.agent_log_watcher, h: health.agent_log_watcher || "stale", d: diagnostics.agent_log_watcher },
    { key: "calendar_watcher", name: t("collector_calendar"), on: w.calendar_watcher, last: lastEvents.calendar_watcher, h: health.calendar_watcher || "disabled", d: diagnostics.calendar_watcher },
    { key: "scheduler", name: t("collector_scheduler"), on: w.scheduler, last: null, h: health.scheduler || "healthy", d: diagnostics.scheduler }
  ];

  const colorMap = {
    healthy: "var(--success, #22c55e)",
    idle: "var(--warn, #eab308)",
    degraded: "var(--danger, #ef4444)",
    stopped: "var(--danger, #ef4444)",
    stale: "var(--danger, #ef4444)",
    disabled: "var(--mu, #888)"
  };

  const labelMap = {
    healthy: currentLang === "zh-TW" ? "運作中" : "Active",
    idle: currentLang === "zh-TW" ? "待命中" : "Idle",
    degraded: currentLang === "zh-TW" ? "部分採集異常" : "Degraded",
    stopped: currentLang === "zh-TW" ? "已停止" : "Stopped",
    stale: currentLang === "zh-TW" ? "無有效資料" : "Stale",
    disabled: currentLang === "zh-TW" ? "已停用" : "Disabled"
  };

  $("watchers-grid").innerHTML = items.map(it => {
    let lastTimeStr = "";
    if (it.last) {
      const timePart = it.last.includes(" ") ? it.last.split(" ")[1] : it.last;
      lastTimeStr = `<span class="mono-mini" style="font-size:10px; opacity:0.85; display:block; margin-top:2px; color:var(--text-dim);">${currentLang === "zh-TW" ? "最後寫入" : "Last"}: ${timePart}</span>`;
    } else if (it.on && it.key !== "scheduler") {
      lastTimeStr = `<span class="mono-mini" style="font-size:10px; opacity:0.85; display:block; margin-top:2px; color:var(--danger, #ef4444); font-weight:600;">${currentLang === "zh-TW" ? "尚無紀錄 (待排查)" : "No data (Pending)"}</span>`;
    }
    const dotColor = colorMap[it.h] || "var(--mu)";
    const statusText = labelMap[it.h] || (it.on ? t("collector_enabled") : t("collector_disabled"));
    let diagnosticText = "";
    if (it.key === "window_watcher" && it.d && ["unavailable", "error"].includes(it.d.state)) {
      diagnosticText = currentLang === "zh-TW"
        ? `前景 probe 不可用 · ${Number(it.d.unavailable_seconds || 0)}s`
        : `Foreground probe unavailable · ${Number(it.d.unavailable_seconds || 0)}s`;
    }
    if (it.key === "calendar_watcher" && it.d) {
      if (it.d.state === "unconfigured") {
        diagnosticText = currentLang === "zh-TW" ? "尚未設定 .ics 路徑（系統設定 → 採集來源）" : "No .ics paths configured (Settings → Sources)";
      } else if (Number(it.d.degraded_sources_count || 0) > 0) {
        const names = (it.d.degraded_sources || []).map(s => s.source_name).join(", ");
        diagnosticText = `${currentLang === "zh-TW" ? "來源錯誤" : "Source error"}: ${names}`;
      } else if (it.d.scan_count) {
        diagnosticText = currentLang === "zh-TW"
          ? `${it.d.last_scan_files} 個檔 · 視野內 ${it.d.last_scan_instances} 筆`
          : `${it.d.last_scan_files} file(s) · ${it.d.last_scan_instances} in horizon`;
      }
    }
    if (it.key === "agent_log_watcher" && it.d && it.d.sources) {
      const failed = Object.entries(it.d.sources).filter(([, value]) => value.state === "error").map(([key]) => key);
      if (failed.length) diagnosticText = `${currentLang === "zh-TW" ? "來源錯誤" : "Source error"}: ${failed.join(", ")}`;
    }

    return `
    <div class="collector">
      <div class="collector-name" style="display:flex; align-items:center; gap:6px;">
        <span style="width:7px; height:7px; border-radius:50%; background:${dotColor}; display:inline-block;"></span>
        ${it.name}
      </div>
      <div class="collector-state" style="color:${dotColor}">
        ${statusText}
        ${lastTimeStr}
        ${diagnosticText ? `<span class="collector-diagnostic">${esc(diagnosticText)}</span>` : ""}
      </div>
    </div>`;
  }).join("");
}

// ---------------------------------------------------------------- P3 context memory
async function loadContextSessions() {
  const box = $("context-sessions-list");
  try {
    contextSessionsCache = await getJSON("/api/v1/context/sessions");
    renderContextSessions();
  } catch (e) {
    if (box) box.innerHTML = `<div class="placeholder">${currentLang === "zh-TW" ? "近期工作階段暫時無法讀取。" : "Recent work sessions are temporarily unavailable."}</div>`;
  }
}

function formatContextTime(value) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleString(currentLang === "zh-TW" ? "zh-TW" : "en", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"
  });
}

function renderContextSessions() {
  const box = $("context-sessions-list");
  const badge = $("context-sessions-badge");
  if (!box || !badge) return;
  if (!contextSessionsCache) {
    badge.textContent = "LOADING";
    return;
  }
  const sessions = contextSessionsCache.sessions || [];
  badge.textContent = `${sessions.length} SESSIONS`;
  badge.className = `trust ${sessions.length ? "ok" : "noisy"}`;
  if (!sessions.length) {
    box.innerHTML = `<div class="placeholder">${currentLang === "zh-TW" ? "近 72 小時沒有可歸戶的 AI、Git 或檔案事件。" : "No canonical AI, Git, or file events in the last 72 hours."}</div>`;
    return;
  }
  box.innerHTML = sessions.map(session => {
    const counts = session.event_counts || {};
    const chips = [
      counts.ai_turn ? `AI ${counts.ai_turn}` : "",
      counts.git_commit ? `GIT ${counts.git_commit}` : "",
      counts.file_activity ? `FILE ${counts.file_activity}` : "",
      `${session.events_observed || 0} EVENTS`
    ].filter(Boolean).map(label => `<span class="context-memory-chip">${esc(label)}</span>`).join("");
    return `
      <article class="context-session" data-session-id="${esc(session.session_id)}">
        <div class="context-session-top">
          <span class="context-session-project">${esc(session.project_key)}</span>
          <span class="context-session-time">${esc(formatContextTime(session.ended_at))}</span>
        </div>
        <div class="context-session-headline">${esc(session.headline || session.narrative || "—")}</div>
        <div class="context-session-meta">${chips}</div>
      </article>`;
  }).join("");
}

async function searchRelatedContext() {
  const input = $("input-related-question");
  const button = $("btn-related-search");
  const box = $("related-memory-results");
  const question = (input.value || "").trim();
  if (question.length < 2) {
    showToast(currentLang === "zh-TW" ? "請先輸入至少兩個字。" : "Enter at least two characters.");
    return;
  }
  button.disabled = true;
  button.textContent = currentLang === "zh-TW" ? "查詢中…" : "Searching…";
  box.innerHTML = `<div class="placeholder">${currentLang === "zh-TW" ? "正在查詢本機 semantic index…" : "Searching the local semantic index…"}</div>`;
  try {
    relatedContextCache = await postJSON("/api/v1/context/related", {
      question,
      top_k: 8
    });
    renderRelatedContext(relatedContextCache);
  } catch (e) {
    relatedContextCache = null;
    box.innerHTML = `<div class="placeholder">${currentLang === "zh-TW" ? "本機 Ollama 或 semantic index 目前不可用；查詢內容未保存。" : "Local Ollama or the semantic index is unavailable; the query was not stored."}</div>`;
  } finally {
    button.disabled = false;
    button.textContent = t("btn_related_search");
  }
}

function renderRelatedContext(data) {
  const box = $("related-memory-results");
  if (!box || !data) return;
  const matches = data.matches || [];
  const advisory = currentLang === "zh-TW"
    ? (matches.length ? "找到語意相近的歷史紀錄；請檢視來源後再決定是否可沿用。" : "沒有超過門檻的相似紀錄，但不代表歷史中一定沒有相關工作。")
    : (matches.length ? "Semantically related history found. Review each source before reuse." : "No match crossed the threshold; related history may still exist outside current coverage.");
  const rows = matches.map(item => `
    <div class="related-memory-match">
      <div class="related-memory-match-title">${esc(item.title || item.source_ref)}</div>
      <div class="related-memory-match-meta">${esc(item.source_ref)} · SCORE ${Number(item.score || 0).toFixed(3)} · ${esc(item.trust_status || "observed")}${item.project_key ? ` · ${esc(item.project_key)}` : ""}</div>
    </div>`).join("");
  box.innerHTML = `<div class="related-memory-advisory">${esc(advisory)}</div>${rows || ""}`;
}

// ---------------------------------------------------------------- P5-1 proposal-only secretary
async function loadSecretaryProposals() {
  const box = $("secretary-proposals-list");
  try {
    secretaryProposalsCache = await getJSON("/api/v1/secretary/proposals?limit=6");
    renderSecretaryProposals();
    if (projectsCache.length) renderProjects();  // 專案卡的 💡 建議 chip 依提案快取更新
  } catch (e) {
    if (box) box.innerHTML = `<div class="placeholder">${currentLang === "zh-TW" ? "建議暫時無法讀取。" : "Suggestions are temporarily unavailable."}</div>`;
  }
}

function renderSecretaryProposals() {
  const box = $("secretary-proposals-list");
  const badge = $("secretary-proposals-badge");
  if (!box || !badge) return;
  if (!secretaryProposalsCache) return;
  renderFocusCarousel();
  // 小秘書首頁的 advisor 徽章：LLM 註解啟用且成功時顯示 provider，否則 RULES
  const advisorBadge = $("assistant-advisor-badge");
  if (advisorBadge) {
    const adv = secretaryProposalsCache.advisor || null;
    if (adv && (adv.status === "annotated" || adv.status === "cached")) {
      advisorBadge.textContent = `LLM · ${String(adv.provider || "").toUpperCase()}`;
      advisorBadge.className = "trust ok";
    } else {
      advisorBadge.textContent = "RULES";
      advisorBadge.className = "trust noisy";
    }
  }
  const proposals = secretaryProposalsCache.proposals || [];
  badge.textContent = `${proposals.length} ${currentLang === "zh-TW" ? "項" : "ITEMS"}`;
  badge.className = `trust ${proposals.length ? "noisy" : "ok"}`;
  if (!proposals.length) {
    box.innerHTML = `<div class="placeholder">${currentLang === "zh-TW" ? "目前沒有超過規則門檻的建議；不代表所有工作都已完成。" : "No suggestion crossed the current rule threshold; this does not prove all work is complete."}</div>`;
    return;
  }
  const zh = currentLang === "zh-TW";
  // P5-R1 advisory：LLM 只能註解，不能增刪或執行；fallback 時完全不顯示
  const advisor = secretaryProposalsCache.advisor || null;
  const advisorActive = advisor && (advisor.status === "annotated" || advisor.status === "cached");
  const advisorBanner = advisorActive
    ? `<div class="advisor-summary">
         <span class="advisor-tag">🧠 ${zh ? "LLM 參考註解" : "LLM ADVISORY"} · ${esc(advisor.provider || "")}${advisor.model ? ` / ${esc(advisor.model)}` : ""}</span>
         ${advisor.summary ? `<div class="advisor-text">${esc(advisor.summary)}</div>` : ""}
         <span class="advisor-boundary">${zh ? "僅供參考；不會執行任何動作，也不保存" : "Advisory only; nothing is executed or persisted"}</span>
       </div>`
    : "";
  box.innerHTML = advisorBanner + proposals.map(item => {
    const refs = (item.evidence_refs || []).map(ref => `<span class="proposal-ref">${esc(ref)}</span>`).join("");
    const priority = String(item.priority || "medium").toUpperCase();
    // detail 是 PR/issue 標題；title 只有 repo#number，兩者都要顯示才看得懂是什麼事
    const detail = item.detail ? `<div class="proposal-detail">${esc(item.detail)}</div>` : "";
    const llmHint = item.llm_priority_hint && item.llm_priority_hint !== item.priority
      ? ` <span class="advisor-hint">${zh ? "LLM 建議優先序" : "LLM hint"}: ${esc(String(item.llm_priority_hint).toUpperCase())}</span>`
      : "";
    const llmNote = item.llm_note
      ? `<div class="proposal-llm-note">🧠 ${esc(item.llm_note)}${llmHint}</div>`
      : "";
    // P5-R2/R3：executor 啟用且此 proposal 有白名單動作時才出現批准按鈕；
    // 動作內容由 server 端 template 決定，前端只傳 proposal_id（可選 template_id）。
    // L2 動作需二次確認（server 回 428 + 一次性確認碼）。
    const actions = item.execution_available
      ? (item.actions && item.actions.length ? item.actions : (item.action ? [item.action] : []))
      : [];
    const execTag = actions.length
      ? actions.map(act => {
          const tier = esc(String(act.risk_level || "").split("_")[0] || "L?");
          const confirmMark = act.requires_confirmation ? "🛡️ " : "⚡ ";
          return `<button class="btn btn-ghost btn-sm proposal-exec-btn" onclick="window.executeProposal('${esc(item.proposal_id)}', '${esc(act.template_id)}')">${confirmMark}${zh ? "批准執行" : "Approve"}（${tier}）</button>`;
        }).join("")
      : `<span>${zh ? "不執行" : "NOT EXECUTABLE"}</span>`;
    const actionLabel = actions.length
      ? actions.map(act =>
          `<div class="proposal-exec-label">${zh ? "可代辦" : "Available action"}：${esc(act.label || act.template_id)}${act.requires_confirmation ? (zh ? "（L2 需輸入確認碼）" : " (L2 requires confirm code)") : ""}</div>`
        ).join("")
      : "";
    const link = item.url
      ? `<a class="proposal-link" href="${esc(item.url)}" target="_blank" rel="noopener">${zh ? "在 GitHub 開啟 →" : "Open on GitHub →"}</a>`
      : "";
    // 更主動：停滯／未收尾事項若尚無 L2 起草動作，直接告訴使用者開啟 L2 就能請小秘書先起草計畫
    const executorInfo = secretaryProposalsCache.executor || {};
    const stalledType = item.proposal_type === "stalled_open_loop" || item.proposal_type === "unfinished_recent";
    const hasDraft = actions.some(act => act.template_id === "agent_draft_plan");
    const l2Hint = stalledType && !hasDraft
      ? `<div class="proposal-l2-hint">🛡️ ${executorInfo.enabled && !executorInfo.l2_available
          ? (zh ? "開啟 L2（設定 → 小秘書執行器）後，小秘書可先為這件事起草重啟計畫，批准＋確認碼才執行。" : "Enable L2 (Settings → Executor) and the secretary can draft a restart plan for this; runs only after approval + confirm code.")
          : (zh ? "開啟執行器與 L2 後，小秘書可先為這件事起草重啟計畫（需批准＋確認碼）。" : "With the executor and L2 enabled, the secretary can draft a restart plan (approval + confirm code).")}</div>`
      : "";
    // 被每專案上限折疊掉的數量：讓使用者知道那裡還有多少事，而不是以為只有這些
    const pending = item.same_project_pending
      ? `<span class="proposal-pending">${zh ? `此專案另有 ${item.same_project_pending} 項` : `+${item.same_project_pending} more here`}</span>`
      : "";
    const snoozeArgs = [item.proposal_type, item.project_key, item.subject_ref || ""]
      .map(v => `'${esc(String(v)).replace(/'/g, "\'")}'`).join(", ");
    return `
      <article class="proposal-card">
        <div class="proposal-card-top">
          <span class="proposal-project">${esc(item.project_key || "OmniContext")}</span>
          <span class="trust ${item.priority === "high" ? "broken" : "noisy"}">${esc(priority)}</span>
        </div>
        <div class="proposal-title">${esc(item.title)}</div>
        ${detail}
        <div class="proposal-reason">${esc(item.reason)}</div>
        ${item.why_now ? `<div class="proposal-why"><span>${esc(t("why_now_label"))}：</span>${esc(item.why_now)}</div>` : ""}
        ${item.memory_note ? `<div class="proposal-memory"><span>🧠 ${esc(t("memory_note_label"))}：</span>${esc(item.memory_note)}</div>` : ""}
        <div class="proposal-action"><span>${zh ? "建議" : "Suggested"}</span>${esc(item.suggested_action)}</div>
        ${llmNote}
        ${actionLabel}
        ${l2Hint}
        <div class="proposal-meta">
          <span>${esc(item.risk_level || "L0_READ_ONLY")}</span>
          ${execTag}
          ${pending}
          ${link}
          <button class="btn btn-ghost btn-sm" onclick="window.snoozeProposal(${snoozeArgs}, 7)">${zh ? "7 天內不再提醒" : "Snooze 7d"}</button>
          <button class="btn btn-ghost btn-sm" onclick="window.snoozeProposal(${snoozeArgs}, null)">${zh ? "不再提醒" : "Dismiss"}</button>
        </div>
        <div class="proposal-refs">${refs}</div>
      </article>`;
  }).join("");
}

// 回饋迴路：使用者說「這個先不用提醒」。沒有這個，分流清單永遠不會變準。
window.snoozeProposal = async function (proposalType, projectKey, subjectRef, days) {
  const zh = currentLang === "zh-TW";
  const permanent = days === null;
  const label = permanent
    ? (zh ? "確定不再提醒這一項？" : "Dismiss this suggestion permanently?")
    : (zh ? `${days} 天內不再提醒這一項？` : `Snooze this suggestion for ${days} days?`);
  if (!confirm(label)) return;
  try {
    const res = await fetch("/api/v1/secretary/proposals/snooze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        proposal_type: proposalType,
        project_key: projectKey,
        subject_ref: subjectRef,
        days: permanent ? null : days,
        dismissed: permanent,
      }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    await loadSecretaryProposals();
  } catch (err) {
    alert((zh ? "設定失敗：" : "Failed: ") + err.message);
  }
};

// P5-R2：批准執行白名單動作。前端只送 proposal_id + execution token；
// 執行什麼由 server 端 template 決定，token 只留在 sessionStorage（關分頁即失效）。
window.executeProposal = async function (proposalId, templateId = null, confirmCode = null) {
  const zh = currentLang === "zh-TW";
  const item = ((secretaryProposalsCache || {}).proposals || [])
    .find(p => p.proposal_id === proposalId);
  const acts = item ? (item.actions && item.actions.length ? item.actions : (item.action ? [item.action] : [])) : [];
  const act = templateId ? acts.find(a => a.template_id === templateId) : acts[0];
  const label = act ? (act.label || act.template_id) : proposalId;
  if (!confirmCode && !confirm((zh ? "批准執行：" : "Approve action: ") + label + (zh ? "？" : "?"))) return;

  let token = sessionStorage.getItem("omni_execution_token") || "";
  if (!token) {
    token = prompt(zh
      ? "輸入 execution token（在終端機執行 `omnicontext init --show-token` 取得）："
      : "Enter execution token (shown by `omnicontext init --show-token`):") || "";
    token = token.trim();
    if (!token) return;
    sessionStorage.setItem("omni_execution_token", token);
  }

  try {
    const body = {};
    if (templateId) body.template_id = templateId;
    if (confirmCode) body.confirm_code = confirmCode;
    const res = await fetch(`/api/v1/secretary/proposals/${encodeURIComponent(proposalId)}/execute`, {
      method: "POST",
      headers: {
        "x-omnicontext-execution-token": token,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) {
      sessionStorage.removeItem("omni_execution_token");
      alert(zh ? "execution token 無效，請重試。" : "Invalid execution token.");
      return;
    }
    if (res.status === 428 && data.confirm) {
      // P5-R3 L2 二次確認：顯示 server 的一次性確認碼，要求使用者親手回填。
      const code = data.confirm.confirm_code || "";
      const typed = prompt(zh
        ? `此為 L2 動作（${data.confirm.label || ""}）。\n確認碼：${code}\n請輸入上方 6 碼以確認執行（${data.confirm.expires_in_seconds || 300} 秒內有效）：`
        : `L2 action (${data.confirm.label || ""}).\nConfirm code: ${code}\nType the 6-digit code to proceed:`) || "";
      if (!typed.trim()) return;
      await window.executeProposal(proposalId, data.confirm.template_id, typed.trim());
      return;
    }
    if (!res.ok) {
      alert((zh ? "未執行：" : "Rejected: ") + (data.detail || `HTTP ${res.status}`));
      return;
    }
    const receipt = data.receipt || {};
    let message = (zh ? "執行狀態：" : "Execution status: ") + (receipt.status || "?");
    if (data.result && data.result.handoff_markdown) {
      try {
        await navigator.clipboard.writeText(data.result.handoff_markdown);
        message += zh ? "\nContext Handoff 已複製到剪貼簿。" : "\nContext Handoff copied to clipboard.";
      } catch (e) {
        message += zh ? "\n（Handoff 產生成功，請由回應複製）" : "\n(Handoff generated.)";
      }
    }
    if (data.result && data.result.plan_markdown) {
      try {
        await navigator.clipboard.writeText(data.result.plan_markdown);
        message += zh ? "\n行動計畫已複製到剪貼簿。" : "\nDraft plan copied to clipboard.";
      } catch (e) { /* 剪貼簿被拒不影響結果 */ }
      if (data.result.output_path) {
        message += zh ? `\n完整輸出：${data.result.output_path}` : `\nFull output: ${data.result.output_path}`;
      }
    }
    if (data.result && typeof data.result.files_changed === "number" && data.result.changed_files) {
      const changed = data.result.changed_files;
      message += zh
        ? `\nAgent 修改了 ${data.result.files_changed} 個檔案（未 commit）`
        : `\nAgent modified ${data.result.files_changed} file(s) (not committed)`;
      if (changed.length) message += "\n- " + changed.slice(0, 8).join("\n- ");
      message += zh
        ? "\n請用 git diff 檢視；git checkout . 可整批還原。"
        : "\nReview with git diff; revert everything with git checkout .";
    }
    if (receipt.error_code) message += `\n(${receipt.error_code})`;
    alert(message);
    await loadSecretaryProposals();
  } catch (err) {
    alert((zh ? "執行失敗：" : "Execution failed: ") + err.message);
  }
};

// ---------------------------------------------------------------- 01 小秘書首頁（assistant home）
// 對話與 RAG 分頁共用同一條 session／歷史；首頁只是入口與精簡鏡像。
function syncAssistantModelControls() {
  const providerSrc = $("select-rag-provider");
  const providerDst = $("select-assistant-provider");
  if (providerSrc && providerDst) {
    providerDst.innerHTML = providerSrc.innerHTML;
    providerDst.value = providerSrc.value;
  }
  const modelSrc = $("select-rag-model");
  const modelDst = $("select-assistant-model");
  if (modelSrc && modelDst) {
    modelDst.innerHTML = modelSrc.innerHTML;
    if (modelSrc.value) modelDst.value = modelSrc.value;
  }
}

function sendAssistantMessage() {
  const input = $("input-assistant-prompt");
  if (!input) return;
  // 送出前把首頁的 provider/model 同步回 RAG 分頁控制項，走同一條發送流程。
  const providerDst = $("select-assistant-provider");
  const modelDst = $("select-assistant-model");
  const providerSrc = $("select-rag-provider");
  const modelSrc = $("select-rag-model");
  if (providerDst && providerSrc && providerDst.value) providerSrc.value = providerDst.value;
  if (modelDst && modelSrc && modelDst.value) modelSrc.value = modelDst.value;
  sendRAGChatMessage(input);
}

function initAssistantHome() {
  syncAssistantModelControls();
  const providerDst = $("select-assistant-provider");
  if (providerDst) {
    providerDst.addEventListener("change", () => {
      const src = $("select-rag-provider");
      if (src) src.value = providerDst.value;
      updateRAGModelSelect(providerDst.value);
      syncAssistantModelControls();
    });
  }
  const modelDst = $("select-assistant-model");
  if (modelDst) {
    modelDst.addEventListener("change", () => {
      const src = $("select-rag-model");
      if (src) src.value = modelDst.value;
    });
  }
  const sendBtn = $("btn-assistant-send");
  const input = $("input-assistant-prompt");
  if (sendBtn) sendBtn.addEventListener("click", sendAssistantMessage);
  if (input) {
    input.addEventListener("keydown", event => {
      if (event.key === "Enter") sendAssistantMessage();
    });
  }
}

function renderAssistantChatMirror() {
  const box = $("assistant-chat-messages");
  if (!box) return;
  const zh = currentLang === "zh-TW";
  if (!ragChatHistory.length) {
    box.innerHTML = `<div class="placeholder">${esc(t("assistant_chat_empty"))}</div>`;
    return;
  }
  const recent = ragChatHistory.slice(-12);
  box.innerHTML = recent.map(msg => {
    const isUser = msg.role === "user";
    const cites = !isUser && Array.isArray(msg.citations) && msg.citations.length
      ? `<div class="assistant-cite-chip">📎 ${msg.citations.length} ${zh ? "則引用 · 詳見知識庫分頁" : "citations · see RAG tab"}</div>`
      : "";
    const mem = !isUser && msg.memory && msg.memory.included
      ? `<span class="assistant-memory-chip" title="${esc((msg.memory.sections || []).join(", "))}">🧠 ${zh ? `參考記憶區 ${msg.memory.notes_used || 0} 筆` : `memory: ${msg.memory.notes_used || 0} notes`}${msg.memory.truncated ? (zh ? "（已截斷）" : " (truncated)") : ""}</span>`
      : "";
    return `
      <div class="assistant-msg ${isUser ? "user" : "bot"}">
        <span class="assistant-msg-role">${isUser ? (zh ? "您" : "YOU") : "🤖"}</span>
        <div class="assistant-msg-body">${esc(msg.content || "…")}${mem}${cites}</div>
      </div>`;
  }).join("");
  box.scrollTop = box.scrollHeight;
}

function assistantChip(label, value, tone) {
  return `
    <div class="assistant-chip">
      <span class="mono-mini muted">${esc(label)}</span>
      <span class="assistant-chip-value ${tone || ""}">${esc(value)}</span>
    </div>`;
}

async function loadAssistantStrip() {
  const box = $("assistant-strip");
  if (!box) return;
  const zh = currentLang === "zh-TW";
  const [usage, background] = await Promise.all([
    getJSON("/api/v1/usage/today").catch(() => null),
    getJSON("/api/v1/background-tasks/today").catch(() => null),
  ]);
  const chips = [];
  if (usage && usage.goal) {
    chips.push(assistantChip(
      zh ? "AI 協作前景" : "AI FOREGROUND",
      `${usage.goal.foreground_minutes ?? 0} min`,
      ""
    ));
    chips.push(assistantChip(
      "COVERAGE",
      String(usage.coverage_status || "?").toUpperCase(),
      usage.coverage_status === "observed" ? "ok" : ""
    ));
  }
  if (background) {
    chips.push(assistantChip(
      zh ? "背景任務" : "BG TASKS",
      `${background.verified_minutes ?? 0} min · ${background.completed_task_count ?? 0} ${zh ? "件" : "done"}`,
      ""
    ));
  }
  const proposals = secretaryProposalsCache && Array.isArray(secretaryProposalsCache.proposals)
    ? secretaryProposalsCache.proposals.length
    : null;
  if (proposals !== null) {
    chips.push(assistantChip(zh ? "待判斷建議" : "SUGGESTIONS", String(proposals), proposals ? "" : "ok"));
  }
  box.innerHTML = chips.join("");
}

// ---------------------------------------------------------------- live feed
async function refreshFeed() {
  try {
    const events = await getJSON(`/api/v1/events/recent?limit=60&event_type=${activeFilter}`);
    if (activeFilter === "all") recentEvents = events;
    const box = $("feed-list");
    if (!events.length) { box.innerHTML = `<div class="placeholder">${t("ph_no_feed")}</div>`; return; }
    box.innerHTML = events.map(e => `
      <div class="frow">
        <span class="ftime">${esc((e.timestamp || "").split(" ")[1] || "")}</span>
        <span class="fbadge ${e.type}">${esc(String(e.badge || e.type).toUpperCase())}</span>
        <div class="fbody">
          <span class="ftitle">${esc(e.title)}</span>
          <span class="fdetail">${esc(e.detail || "")}</span>
        </div>
      </div>`).join("");
  } catch (e) { /* 保留上次畫面 */ }
}

// ---------------------------------------------------------------- projects
async function loadProjects(force) {
  try {
    projectsCache = await getJSON("/api/v1/projects/active");
    if (force || !recentEvents.length) {
      try { recentEvents = await getJSON("/api/v1/events/recent?limit=200&event_type=all"); } catch (e) {}
    }
    renderResume();
    renderProjects();
    $("projects-count").textContent = `${t("active_workstreams")} · ${projectsCache.length}`;
  } catch (e) {
    $("projects-list").innerHTML = `<div class="placeholder">${t("ph_loading_projects")}</div>`;
  }
}

function statusLabel(p) {
  if (p.status === "active") return t("status_active");
  return t("status_idle", { days: p.idle_days });
}

// ---------------------------------------------------------------- Quick Actions & Toast
function showToast(msg, duration = 3200) {
  let box = $("toast-container");
  if (!box) {
    box = document.createElement("div");
    box.id = "toast-container";
    document.body.appendChild(box);
  }
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transition = "opacity 0.3s ease";
    setTimeout(() => el.remove(), 300);
  }, duration);
}

async function runOpenAction(path, action = "explorer", url = null) {
  try {
    const res = await postJSON("/api/v1/control/open_path", { path, action, url });
    if (res && res.status === "success") {
      showToast("⚡ " + res.message);
    } else {
      showToast("⚠️ " + (res.message || "無法開啟目標"));
    }
  } catch (e) {
    showToast("⚠️ 操作失敗: " + e.message);
  }
}

function renderActionGroup(p) {
  if (!p) return "";
  const ghUrl = p.github_url || (p.github && p.github.html_url);
  const path = p.local_path;
  const vsCodeUri = path ? "vscode://file/" + encodeURI(path.replace(/\\/g, "/")) : "";
  const folderUri = path ? "openfolder:///" + encodeURI(path.replace(/\\/g, "/")) : "";

  const folderBtn = path
    ? `<a class="action-btn" href="${esc(folderUri)}" data-act="folder" data-path="${esc(path)}" title="${currentLang === 'zh-TW' ? '開啟本機資料夾 (' + esc(path) + ')' : 'Open Folder (' + esc(path) + ')'}">📁</a>`
    : `<button class="action-btn disabled" title="${currentLang === 'zh-TW' ? '尚未定位到本機路徑' : 'No local path'}">📁</button>`;

  const vsCodeBtn = path
    ? `<a class="action-btn" href="${esc(vsCodeUri)}" title="${currentLang === 'zh-TW' ? '在 VS Code 中開啟專案' : 'Open in VS Code'}">💻</a>`
    : `<button class="action-btn disabled" title="${currentLang === 'zh-TW' ? '尚未定位到本機路徑' : 'No local path'}">💻</button>`;

  const ghBtn = ghUrl
    ? `<a class="action-btn" href="${esc(ghUrl)}" target="_blank" rel="noopener noreferrer" title="${currentLang === 'zh-TW' ? '前往 GitHub 專案頁面' : 'Open on GitHub'}">🐙</a>`
    : `<button class="action-btn disabled" title="${currentLang === 'zh-TW' ? '未綁定 GitHub 倉庫' : 'No GitHub repo'}">🐙</button>`;

  return `
    <div class="action-group" onclick="event.stopPropagation()">
      ${folderBtn}
      ${vsCodeBtn}
      ${ghBtn}
    </div>`;
}

function attachActionGroupListeners(parentEl) {
  parentEl.querySelectorAll("[data-act]").forEach(btn => {
    btn.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      const act = btn.dataset.act;
      if (act === "folder") {
        const pth = btn.dataset.path;
        if (pth) {
          try {
            await navigator.clipboard.writeText(pth);
          } catch (_) {}
        }
        runOpenAction(pth, "explorer");
      }
    });
  });
}

async function copyProjectHandoff(projectKey, displayName) {
  try {
    showToast(currentLang === "zh-TW" ? "⏳ 正在提煉專案接續記憶..." : "⏳ Building context handoff...");
    const res = await getJSON(`/api/v1/projects/${encodeURIComponent(projectKey)}/handoff?turns=5`);
    if (res && res.markdown) {
      await navigator.clipboard.writeText(res.markdown);
      const name = displayName || res.display_name || projectKey;
      showToast(currentLang === "zh-TW" ? `⚡ 已複製 [${name}] 接續 Prompt！可直接貼入任何 AI 開工` : `⚡ Copied [${name}] handoff prompt to clipboard!`);
    } else {
      showToast("⚠️ 無法生成接續 Prompt");
    }
  } catch (e) {
    showToast("⚠️ 複製失敗: " + e.message);
  }
}

function renderResume() {
  // 「上次做到哪」現在住在 01 今日行動清單最上方（02 只留專案卡）。
  const box = $("today-resume");
  if (!box) return;
  const p = projectsCache[0];
  if (!p) {
    box.innerHTML = `<div class="placeholder">${t("ph_no_projects")}</div>`;
    return;
  }
  box.innerHTML = `
    <div style="min-width:0">
      <div class="today-resume-label">${esc(t("today_resume_label"))}</div>
      <div class="today-resume-title">${esc(p.display_name)}</div>
      <div class="today-resume-action">${esc(p.last_action_summary || "無紀錄")}</div>
      <div class="today-resume-meta">${esc(p.last_activity_at)} · ${esc(p.category || "")} · ${t("open_loop_count")} ${p.open_loops_count}</div>
    </div>
    <div class="today-resume-buttons">
      ${renderActionGroup(p)}
      <button class="btn" data-copy-handoff="${esc(p.project_key)}" data-name="${esc(p.display_name)}" style="background:var(--s2); border:1px solid var(--bd); color:var(--tx); font-weight:600; font-size:12px; padding:6px 12px; cursor:pointer;" title="${currentLang === 'zh-TW' ? '一鍵複製結構化接續 Prompt 貼入 AI 開工' : 'Copy structured handoff prompt for AI'}">${t("btn_copy_handoff")}</button>
      <button class="btn btn-primary" data-resume="${esc(p.project_key)}">${currentLang === "zh-TW" ? "接續 →" : "Resume →"}</button>
    </div>`;
  attachActionGroupListeners(box);
  const copyBtn = box.querySelector("[data-copy-handoff]");
  if (copyBtn) {
    copyBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      copyProjectHandoff(p.project_key, p.display_name);
    });
  }
  const btn = box.querySelector("[data-resume]");
  if (btn) btn.addEventListener("click", () => focusProject(p.project_key));
}

// ---------------------------------------------------------------- 01 今天：早晨包摘要與預設排程
let todayViewCache = null;

async function loadTodayView() {
  const pack = $("today-pack");
  const presetBtn = $("btn-create-presets");
  try {
    todayViewCache = await getJSON("/api/v1/secretary/today");
  } catch (e) {
    todayViewCache = null;
    if (pack) pack.hidden = true;
    return;
  }
  const zh = currentLang === "zh-TW";
  const sched = todayViewCache.schedules || {};
  const calBox = $("today-calendar");
  if (calBox) {
    const cal = todayViewCache.calendar || {};
    if (cal.enabled && cal.line) {
      calBox.textContent = `📅 ${cal.line}`;
      calBox.title = cal.claim_boundary || "";
      calBox.hidden = false;
    } else {
      calBox.hidden = true;
    }
  }
  if (pack) {
    if (todayViewCache.pack_line) {
      const when = String((todayViewCache.pack || {}).finished_at || "").slice(5, 16).replace("T", " ");
      pack.innerHTML = `<strong>${esc(todayViewCache.pack_line)}</strong> · ${esc(when)}`;
      pack.hidden = false;
    } else if (sched.scheduled_tasks_enabled && !sched.all_present) {
      pack.textContent = zh
        ? "尚未建立每日排程；按「📦 建立每日排程」讓小秘書每天 07:30 產同步報告與 Handoff。"
        : "No daily schedules yet. Click “📦 Create daily schedules” for a 07:30 morning pack.";
      pack.hidden = false;
    } else if (!sched.executor_enabled || !sched.scheduled_tasks_enabled) {
      pack.textContent = zh
        ? "小秘書排程未啟用（設定 → 小秘書執行器 → 執行器與排程任務開關）；啟用後可一鍵建立每日早晨包。"
        : "Scheduled tasks are off (Settings → Executor); enable them to create the daily morning pack.";
      pack.hidden = false;
    } else {
      pack.hidden = true;
    }
  }
  if (presetBtn) {
    if (sched.all_present) {
      presetBtn.textContent = zh ? "✅ 每日排程已建立" : "✅ Daily schedules ready";
      presetBtn.disabled = true;
    } else {
      presetBtn.textContent = t("btn_create_presets");
      presetBtn.disabled = false;
    }
  }
}

async function createSchedulePresets() {
  const zh = currentLang === "zh-TW";
  if (!confirm(zh
    ? "建立兩個每日排程？\n• 07:30 早晨包：Repo 同步報告＋STATUS 草稿＋活躍專案 Handoff\n• 21:30 晚間：今天有活動的專案各產一份 Handoff\n全部是 L0 唯讀動作，不會 fetch、不改任何 repo。"
    : "Create two daily schedules?\n• 07:30 morning pack (repo sync report, STATUS draft, active-project handoffs)\n• 21:30 evening handoffs for today's active projects\nAll L0 read-only.")) return;
  let token = sessionStorage.getItem("omni_execution_token") || "";
  if (!token) {
    token = prompt(zh
      ? "輸入 execution token（在終端機執行 `omnicontext init --show-token` 取得）："
      : "Enter execution token (shown by `omnicontext init --show-token`):") || "";
    token = token.trim();
    if (!token) return;
    sessionStorage.setItem("omni_execution_token", token);
  }
  try {
    const res = await fetch("/api/v1/secretary/scheduled-tasks/presets", {
      method: "POST", headers: { "x-omnicontext-execution-token": token },
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) { sessionStorage.removeItem("omni_execution_token"); alert(zh ? "execution token 無效。" : "Invalid execution token."); return; }
    if (!res.ok) { alert((zh ? "未建立：" : "Not created: ") + (data.detail || `HTTP ${res.status}`)); return; }
    showToast(zh ? `已建立 ${(data.created || []).length} 個排程（${(data.already_present || []).length} 個原本就有）` : `${(data.created || []).length} schedules created (${(data.already_present || []).length} already existed)`);
    loadTodayView();
  } catch (e) {
    alert((zh ? "建立失敗：" : "Failed: ") + e.message);
  }
}

// ---------------------------------------------------------------- 小秘書問候卡（今天／近 2 小時做了什麼＋一句鼓勵）
let greetingWindow = "today";
let greetingCache = null;

async function loadGreeting() {
  const textBox = $("greeting-text");
  if (!textBox) return;
  try {
    greetingCache = await getJSON(`/api/v1/secretary/greeting?window=${encodeURIComponent(greetingWindow)}`);
  } catch (e) {
    textBox.innerHTML = `<span class="placeholder">${currentLang === "zh-TW" ? "問候卡暫時讀不到。" : "Greeting unavailable."}</span>`;
    return;
  }
  renderGreeting();
}

function renderGreeting() {
  const g = greetingCache;
  const textBox = $("greeting-text");
  const statsBox = $("greeting-stats");
  const source = $("greeting-source");
  const boundary = $("greeting-boundary");
  if (!g || !textBox) return;
  const zh = currentLang === "zh-TW";
  const stats = g.stats || {};
  if (g.source === "llm" && g.text) {
    textBox.innerHTML = `<p class="greeting-line">${esc(g.text)}</p>`;
  } else {
    const items = (g.achievements || []).map(a => `<li>${esc(a)}</li>`).join("");
    textBox.innerHTML = `
      <p class="greeting-line greeting-headline">${esc(g.headline)}</p>
      <p class="greeting-line">${esc(g.lead)}</p>
      ${items ? `<ul class="greeting-list">${items}</ul>` : ""}
      ${g.recent_summary ? `<p class="greeting-line muted">${zh ? "剛剛在做：" : "Just now: "}${esc(g.recent_summary)}</p>` : ""}
      ${g.schedule_line ? `<p class="greeting-line greeting-schedule">📅 ${esc(g.schedule_line)}</p>` : ""}
      <p class="greeting-line greeting-encourage">${esc(g.encouragement)}</p>`;
  }
  if (source) {
    source.textContent = g.source === "llm" ? `LLM · ${String(g.llm_provider || "").toUpperCase()}` : "RULES";
    source.className = "trust ok";
    source.title = zh ? `鼓勵語池：${g.encouragement_pool || ""}` : `pool: ${g.encouragement_pool || ""}`;
  }
  if (statsBox) {
    const chips = [];
    const add = (label, value, title) => { if (value) chips.push(`<span class="pchip" title="${esc(title || "")}">${esc(label)} ${esc(String(value))}</span>`); };
    add(zh ? "專案" : "projects", stats.projects_touched, g.evidence && g.evidence.projects);
    add("commit", stats.commits, g.evidence && g.evidence.commits);
    add("PR", stats.prs_touched, g.evidence && g.evidence.prs);
    add(zh ? "AI 對話" : "AI turns", stats.ai_turns, g.evidence && g.evidence.ai_turns);
    add(zh ? "文件" : "docs", stats.files_writing, g.evidence && g.evidence.files);
    add(zh ? "收掉" : "resolved", stats.loops_resolved, g.evidence && g.evidence.loops_resolved);
    add(zh ? "會議" : "meetings", stats.meetings, g.evidence && g.evidence.meetings);
    if (stats.foreground_minutes >= 15) add(zh ? "專注" : "focus", `${Math.round(stats.foreground_minutes)} min`, g.evidence && g.evidence.foreground_minutes);
    statsBox.innerHTML = chips.join("");
    statsBox.hidden = chips.length === 0;
  }
  if (boundary) boundary.textContent = g.claim_boundary || "";
  document.querySelectorAll(".greeting-tools .chip").forEach(c => c.classList.toggle("active", c.dataset.window === greetingWindow));
}

function initGreetingCard() {
  document.querySelectorAll(".greeting-tools .chip").forEach(chip => {
    chip.addEventListener("click", () => { greetingWindow = chip.dataset.window || "today"; loadGreeting(); });
  });
  const refresh = $("btn-greeting-refresh");
  if (refresh) refresh.addEventListener("click", loadGreeting);
}

// ---------------------------------------------------------------- ADR-012 小秘書記憶區
let memoryCache = null;
const MEMORY_COMMANDS = [
  { kind: "user_note", re: /^\s*(?:記下來|記住|筆記|\/note|remember)\s*[:：]?\s*([\s\S]+)$/ },
  { kind: "preference", re: /^\s*(?:偏好|\/pref(?:erence)?)\s*[:：]?\s*([\s\S]+)$/ },
  { kind: "decision", re: /^\s*(?:決定|\/decision|decide)\s*[:：]?\s*([\s\S]+)$/ },
];

// 與後端 core/secretary_memory.parse_note_command 同一套前綴；只在前端先判斷要不要送 LLM。
function parseMemoryCommand(text) {
  for (const cmd of MEMORY_COMMANDS) {
    const m = cmd.re.exec(text || "");
    if (!m) continue;
    let body = m[1].trim();
    let projectKey = null;
    const at = /^@([\w.\-]+)\s*[:：]?\s*([\s\S]*)$/.exec(body);
    const bracket = /^\[([^\]]{1,120})\]\s*([\s\S]*)$/.exec(body);
    if (at) { projectKey = at[1]; body = at[2].trim(); }
    else if (bracket) { projectKey = bracket[1].trim(); body = bracket[2].trim(); }
    if (!body) return null;
    return { kind: cmd.kind, body, project_key: projectKey };
  }
  return null;
}

function memoryKindLabel(kind) {
  const zh = currentLang === "zh-TW";
  return ({
    user_note: zh ? "筆記" : "note",
    preference: zh ? "偏好" : "preference",
    decision: zh ? "決定" : "decision",
    observation: zh ? "觀察" : "observation",
  })[kind] || kind;
}

async function rememberFromChat(cmd, rawPrompt) {
  const zh = currentLang === "zh-TW";
  ragChatHistory.push({ role: "user", content: rawPrompt, time: new Date().toLocaleTimeString() });
  let reply;
  try {
    const note = await postJSON("/api/v1/secretary/memory", { ...cmd, source: "chat" });
    reply = zh
      ? `🧠 已記下（${memoryKindLabel(note.kind)}${note.project_key ? ` · ${note.project_key}` : ""}）：${note.body}\n之後回答與提案都會參考；可在下方記憶區刪除。`
      : `🧠 Remembered (${memoryKindLabel(note.kind)}${note.project_key ? ` · ${note.project_key}` : ""}): ${note.body}\nFuture answers and proposals will use it; delete it in the memory panel below.`;
    loadMemoryPanel();
  } catch (e) {
    reply = (zh ? "沒有記下：" : "Not saved: ") + e.message;
  }
  ragChatHistory.push({ role: "assistant", content: reply, citations: [], time: new Date().toLocaleTimeString() });
  renderRAGMessages();
}

async function loadMemoryPanel() {
  const list = $("memory-list");
  const badge = $("memory-badge");
  if (!list) return;
  try {
    memoryCache = await getJSON("/api/v1/secretary/memory?limit=60");
  } catch (e) {
    memoryCache = null;
    list.innerHTML = `<div class="placeholder">${currentLang === "zh-TW" ? "記憶區暫時讀不到。" : "Memory unavailable."}</div>`;
    if (badge) { badge.textContent = "—"; badge.className = "trust noisy"; }
    return;
  }
  renderMemoryList();
}

function renderMemoryList() {
  const list = $("memory-list");
  const badge = $("memory-badge");
  const clearBtn = $("btn-memory-clear-obs");
  if (!list || !memoryCache) return;
  const zh = currentLang === "zh-TW";
  const notes = memoryCache.notes || [];
  const counts = memoryCache.counts || {};
  if (badge) {
    badge.textContent = `${memoryCache.total || 0} ${zh ? "筆" : "NOTES"}`;
    badge.className = `trust ${memoryCache.total ? "ok" : "noisy"}`;
  }
  if (clearBtn) clearBtn.disabled = !(counts.observation > 0);
  if (!notes.length) {
    list.innerHTML = `<div class="placeholder">${zh
      ? "還沒有任何記憶。上方輸入或在對話框打「記下來：…」；早晨包跑過後秘書也會留下觀察。"
      : "Nothing remembered yet. Use the form above or type “remember: …” in the chat; the morning pack also leaves observations."}</div>`;
    return;
  }
  list.innerHTML = notes.map(n => {
    const when = String(n.created_at || "").slice(0, 16).replace("T", " ");
    const proj = n.project_key ? `<span class="pchip">${esc(n.project_key)}</span>` : "";
    const title = n.title ? `<strong>${esc(n.title)}</strong> · ` : "";
    const src = n.kind === "observation" ? ` · ${esc(n.source || "")}` : "";
    return `
      <div class="memory-item" data-note-id="${n.id}">
        <span class="memory-kind ${esc(n.kind)}">${esc(memoryKindLabel(n.kind))}</span>
        <div class="memory-item-body">${title}${esc(n.body)}${proj}<div class="memory-item-meta">${esc(when)}${src}</div></div>
        <button class="btn btn-ghost btn-sm btn-delete" data-delete-note="${n.id}" title="${zh ? "刪除這筆" : "Delete"}">✕</button>
      </div>`;
  }).join("");
  list.querySelectorAll("[data-delete-note]").forEach(btn => {
    btn.addEventListener("click", () => deleteMemoryNote(Number(btn.getAttribute("data-delete-note"))));
  });
}

async function deleteMemoryNote(noteId) {
  const zh = currentLang === "zh-TW";
  try {
    const res = await fetch(API + `/api/v1/secretary/memory/${noteId}`, { method: "DELETE" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    showToast(zh ? "已刪除" : "Deleted");
    loadMemoryPanel();
    loadSecretaryProposals();
  } catch (e) {
    alert((zh ? "刪除失敗：" : "Delete failed: ") + e.message);
  }
}

async function addMemoryNote() {
  const zh = currentLang === "zh-TW";
  const bodyInput = $("input-memory-body");
  const projectInput = $("input-memory-project");
  const kindSelect = $("select-memory-kind");
  const body = (bodyInput && bodyInput.value || "").trim();
  if (!body) return;
  try {
    await postJSON("/api/v1/secretary/memory", {
      kind: kindSelect ? kindSelect.value : "user_note",
      body,
      project_key: (projectInput && projectInput.value || "").trim() || null,
      source: "web",
    });
    if (bodyInput) bodyInput.value = "";
    showToast(zh ? "已記下" : "Remembered");
    loadMemoryPanel();
    loadSecretaryProposals();
  } catch (e) {
    alert((zh ? "沒有記下：" : "Not saved: ") + e.message);
  }
}

async function clearMemoryObservations() {
  const zh = currentLang === "zh-TW";
  if (!confirm(zh ? "刪除秘書自己寫的所有觀察？您的筆記、偏好與決定不受影響。" : "Delete every secretary observation? Your notes, preferences and decisions are untouched.")) return;
  try {
    const res = await fetch(API + "/api/v1/secretary/memory?kind=observation", { method: "DELETE" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    showToast(zh ? `已刪除 ${data.deleted || 0} 則觀察` : `${data.deleted || 0} observations deleted`);
    loadMemoryPanel();
  } catch (e) {
    alert((zh ? "清除失敗：" : "Clear failed: ") + e.message);
  }
}

async function toggleMemoryContext() {
  const box = $("memory-context-box");
  if (!box) return;
  if (!box.hidden) { box.hidden = true; return; }
  const zh = currentLang === "zh-TW";
  box.textContent = zh ? "整理中…" : "Loading…";
  box.hidden = false;
  try {
    const data = await getJSON("/api/v1/secretary/memory/context");
    const r = data.receipt || {};
    const head = r.included
      ? (zh ? `（${r.chars} 字 · ${r.notes_used} 筆記憶${r.truncated ? " · 已截斷" : ""}）\n` : `(${r.chars} chars · ${r.notes_used} notes${r.truncated ? " · truncated" : ""})\n`)
      : (zh ? `（目前沒有可注入的脈絡：${r.reason || "empty"}）` : `(nothing to inject: ${r.reason || "empty"})`);
    box.textContent = head + (data.text || "");
  } catch (e) {
    box.textContent = (zh ? "讀不到：" : "Unavailable: ") + e.message;
  }
}

function initMemoryPanel() {
  const addBtn = $("btn-memory-add");
  const bodyInput = $("input-memory-body");
  const clearBtn = $("btn-memory-clear-obs");
  const ctxBtn = $("btn-memory-context");
  if (addBtn) addBtn.addEventListener("click", addMemoryNote);
  if (bodyInput) bodyInput.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); addMemoryNote(); } });
  if (clearBtn) clearBtn.addEventListener("click", (ev) => { ev.preventDefault(); ev.stopPropagation(); clearMemoryObservations(); });
  if (ctxBtn) ctxBtn.addEventListener("click", (ev) => { ev.preventDefault(); ev.stopPropagation(); toggleMemoryContext(); });
}

// ---------------------------------------------------------------- 02 專案卡：git 狀態 chip（來自 L0 同步報告快照）
let repoSnapshotCache = null;

function normalizePathKey(value) {
  return String(value || "").replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

async function loadRepoSnapshot() {
  try {
    repoSnapshotCache = await getJSON("/api/v1/repos/sync-snapshot");
  } catch (e) {
    repoSnapshotCache = null;
  }
  if (projectsCache.length) renderProjects();
}

function repoSnapshotFor(project) {
  if (!repoSnapshotCache || !repoSnapshotCache.available) return null;
  const repos = repoSnapshotCache.repositories || [];
  const pathKey = normalizePathKey(project.local_path);
  if (pathKey) {
    const byPath = repos.find(r => normalizePathKey(r.path) === pathKey);
    if (byPath) return byPath;
  }
  const byName = repos.filter(r => r.name === project.display_name || r.name === project.project_key);
  return byName.length === 1 ? byName[0] : null;  // 同名多個 clone 屬歧義，不亂配
}

function projectChips(p) {
  const zh = currentLang === "zh-TW";
  const chips = [];
  const repo = repoSnapshotFor(p);
  if (repo) {
    const map = {
      behind: [`↓${repo.behind ?? "?"} ${zh ? "待 pull" : "pull"}`, "pchip-behind"],
      ahead: [`↑${repo.ahead ?? "?"} ${zh ? "待 push" : "push"}`, "pchip-ahead"],
      diverged: [`↑${repo.ahead ?? "?"} ↓${repo.behind ?? "?"} ${zh ? "分歧" : "diverged"}`, "pchip-diverged"],
      synced: [zh ? "已同步" : "synced", "pchip-synced"],
    };
    const entry = map[repo.sync_state];
    if (entry) {
      const dirty = repo.clean === false ? (zh ? " · 未提交" : " · dirty") : "";
      const title = (zh ? "來自最近一次同步報告快照（cached remote-tracking ref）" : "From the latest sync report snapshot (cached remote-tracking ref)")
        + (repo.last_fetch_at ? ` · fetch ${String(repo.last_fetch_at).slice(0, 16)}` : "");
      chips.push(`<span class="pchip ${entry[1]}" title="${esc(title)}">${esc(entry[0])}${dirty}</span>`);
    }
  }
  const proposals = ((secretaryProposalsCache || {}).proposals || []).filter(
    item => item.project_key === p.project_key || item.project_key === p.display_name
  );
  if (proposals.length) {
    chips.push(`<span class="pchip pchip-proposals" title="${esc(proposals.map(i => i.title).join(" / "))}">💡 ${proposals.length} ${zh ? "建議" : (proposals.length > 1 ? "suggestions" : "suggestion")}</span>`);
  }
  return chips.join("");
}


function renderProjects() {
  const box = $("projects-list");
  if (!projectsCache.length) {
    box.innerHTML = `<div class="placeholder">${t("ph_no_projects")}</div>`;
    return;
  }

  const activeProjects = projectsCache.filter(p => (p.idle_days == null || p.idle_days <= 60));
  const idleProjects = projectsCache.filter(p => (p.idle_days != null && p.idle_days > 60));

  // 決定渲染之專案清單
  let listToRender = projectsCache;
  if (!showAllProjects) {
    if (expandedProject && !activeProjects.some(p => p.project_key === expandedProject)) {
      const exp = idleProjects.find(p => p.project_key === expandedProject);
      listToRender = exp ? [...activeProjects, exp] : activeProjects;
    } else {
      listToRender = activeProjects;
    }
  }

  const pCountEl = $("projects-count");
  if (pCountEl) {
    pCountEl.textContent = `${t("active_workstreams")} · ${showAllProjects ? projectsCache.length : activeProjects.length + ' / ' + projectsCache.length}`;
  }

  let projectsHtml = listToRender.map(p => {
    const bar = p.status === "active" ? "var(--orange)"
      : (p.open_loops_count > 0 ? "var(--warn)" : "var(--bd)");
    const loopColor = p.open_loops_count >= 3 ? "var(--orange)"
      : (p.open_loops_count > 0 ? "var(--tx)" : "var(--mu)");
    const open = expandedProject === p.project_key;

    let ghBadge = "";
    if (p.github) {
      const prs = p.github.prs || [];
      if (prs.length) {
        const latest = prs[0];
        const isMerged = latest.state === "merged";
        const bg = isMerged ? "rgba(168, 85, 247, 0.16)" : "rgba(242, 106, 15, 0.16)";
        const color = isMerged ? "#c084fc" : "var(--orange)";
        ghBadge = `<span class="trust" style="background:${bg}; color:${color}; font-size:9.5px; margin-left:6px;" title="${esc(latest.title)}">PR #${latest.number} ${latest.state.toUpperCase()}</span>`;
      } else {
        ghBadge = `<span class="trust ok" style="font-size:9.5px; margin-left:6px;">🐙 ${p.github.is_private ? "PRIVATE" : "PUBLIC"}</span>`;
      }
    }

    const isIdleOver60 = p.idle_days != null && p.idle_days > 60;
    const idleBadge = isIdleOver60 ? `<span class="trust" style="background:var(--s2); color:var(--mu); font-size:9px; margin-left:4px;">>60d</span>` : "";

    return `
      <div class="pitem" data-key="${esc(p.project_key)}">
        <div class="prow">
          <span class="pbar" style="background:${bar}"></span>
          <div style="min-width:0">
            <div class="pname" style="display:flex; align-items:center;">
              <span>${esc(p.display_name)}</span>
              ${ghBadge}
              ${idleBadge}
            </div>
            <div class="pmeta">${esc(p.category || "")} · ${statusLabel(p)}${projectChips(p)}</div>
          </div>
          <div class="paction">${esc(p.last_action_summary || "無紀錄")}</div>
          ${renderActionGroup(p)}
          <div class="ploops" style="color:${loopColor}">${p.open_loops_count}<span>${t("open_loop_count")}</span></div>
          <div class="plast">${esc((p.last_activity_at || "").replace(/^\d{4}-/, ""))}</div>
          <div class="pchev">${open ? "▾" : "▸"}</div>
        </div>
        <div class="pdetail-slot"></div>
      </div>`;
  }).join("");

  let toggleHtml = "";
  if (idleProjects.length > 0) {
    toggleHtml = `
      <div style="text-align:center; padding:12px 0 10px; margin-top:6px; border-top:1px dashed var(--bd);">
        <button id="btn-toggle-idle-projects" class="btn" style="background:var(--s2); border:1px solid var(--bd); color:var(--tx); font-size:11.5px; font-weight:600; padding:6px 16px; cursor:pointer;" title="切換超過 60 天未活躍的專案">
          ${showAllProjects
            ? t("btn_collapse_projects")
            : t("btn_show_more_projects", { count: idleProjects.length })
          }
        </button>
      </div>`;
  }

  box.innerHTML = projectsHtml + toggleHtml;

  attachActionGroupListeners(box);

  const toggleBtn = box.querySelector("#btn-toggle-idle-projects");
  if (toggleBtn) {
    toggleBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      showAllProjects = !showAllProjects;
      renderProjects();
    });
  }

  box.querySelectorAll(".pitem").forEach(item => {
    item.querySelector(".prow").addEventListener("click", () => {
      const key = item.dataset.key;
      expandProject(expandedProject === key ? null : key);
    });
  });

  if (expandedProject) renderProjectDetail(expandedProject);
}

function expandProject(key, scroll) {
  if (key) {
    // 若要展開的專案屬於 60 天以上閒置專案，自動切換至顯示全部
    const isIdle = projectsCache.some(p => p.project_key === key && p.idle_days > 60);
    if (isIdle) showAllProjects = true;
  }
  expandedProject = key;
  renderProjects();
  if (key && scroll) {
    setTimeout(() => {
      const el = document.querySelector(`.pitem[data-key="${CSS.escape(key)}"]`);
      if (el) window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - 160, behavior: "smooth" });
    }, 50);
  }
}

async function renderProjectDetail(key) {
  const item = document.querySelector(`.pitem[data-key="${CSS.escape(key)}"]`);
  if (!item) return;
  const slot = item.querySelector(".pdetail-slot");
  slot.innerHTML = `<div class="pdetail"><div class="placeholder">${t("ph_loading_projects")}</div></div>`;

  const proj = projectsCache.find(p => p.project_key === key);
  let events = [];
  try {
    events = await getJSON(`/api/v1/events/recent?limit=30&project=${encodeURIComponent(key)}`);
  } catch (e) {
    events = recentEvents.filter(e => (e.project || "") === key || (proj && (e.project || "") === proj.display_name));
  }

  let loops = [];
  try { loops = await getJSON(`/api/v1/open-loops?project=${encodeURIComponent(key)}`); } catch (e) {}

  // 近期工作階段（原本是 02 的獨立面板；現在只在該專案展開時顯示，減少重複）
  let sessions = [];
  try {
    const payload = await getJSON(`/api/v1/context/sessions?project=${encodeURIComponent(key)}&limit=5`);
    sessions = payload.sessions || [];
  } catch (e) {}
  const sessionsHtml = sessions.length
    ? sessions.map(session => {
        const counts = session.event_counts || {};
        const chips = [
          counts.ai_turn ? `AI ${counts.ai_turn}` : "",
          counts.git_commit ? `GIT ${counts.git_commit}` : "",
          counts.file_activity ? `FILE ${counts.file_activity}` : "",
        ].filter(Boolean).map(label => `<span class="context-memory-chip">${esc(label)}</span>`).join("");
        return `<article class="context-session">
          <div class="context-session-top"><span class="context-session-time">${esc(formatContextTime(session.started_at || session.ended_at))} → ${esc(formatContextTime(session.ended_at))}</span></div>
          <div class="context-session-headline">${esc(session.headline || session.narrative || "—")}</div>
          <div class="context-session-meta">${chips}</div>
        </article>`;
      }).join("")
    : `<div class="placeholder" style="padding:0">${currentLang === "zh-TW" ? "近 72 小時沒有可歸戶的工作階段。" : "No canonical work session in the last 72 hours."}</div>`;

  // 提取該專案近期異動的檔案清單
  const fileEvents = events.filter(e => e.type === "file");
  const distinctFiles = [];
  const seenPaths = new Set();
  for (const f of fileEvents) {
    if (!seenPaths.has(f.detail || f.title)) {
      seenPaths.add(f.detail || f.title);
      distinctFiles.push(f);
    }
  }

  const dotOf = { ai: "var(--orange)", git: "var(--warn)", window: "var(--mu)", file: "var(--bd)" };
  const tl = events.length
    ? events.slice(0, 10).map(e => `
        <div class="tl">
          <span class="tl-time">${esc((e.timestamp || "").split(" ")[1] || "")}</span>
          <span class="tl-dot" style="background:${dotOf[e.type] || "var(--bd)"}"></span>
          <span class="tl-text">${esc(e.title)} <small class="muted">(${esc(e.response || "")})</small></span>
        </div>`).join("")
    : `<div class="placeholder" style="padding:0">${t("ph_no_feed")}</div>`;

  const fileListHtml = distinctFiles.length
    ? distinctFiles.map(f => `
        <div style="padding: 5px 8px; border: 1px solid var(--bd); margin-bottom: 4px; background: var(--s2); font-size: 11px; display: flex; justify-content: space-between; align-items: center;">
          <div style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 80%;">
            <strong style="color: var(--tx);">${esc(f.title.replace(/^\[[^\]]+\]\s*/, ''))}</strong>
            <span class="muted mono-mini" style="margin-left: 6px; font-size: 9.5px;">${esc(f.detail || '')}</span>
          </div>
          <span class="mono-mini accent" style="font-size: 9.5px; flex-shrink: 0;">${esc(f.response || '')}</span>
        </div>`).join("")
    : '<div class="placeholder" style="padding:0">無檔案異動紀錄。</div>';

  const ll = loops.length
    ? loops.map(l => `<div class="pl"><b>·</b><span>${esc(l.title)}</span></div>`).join("")
    : `<div class="placeholder" style="padding:0">${t("ph_no_loops")}</div>`;

  let ghSection = "";
  if (proj && proj.github) {
    const gh = proj.github;
    const prsHtml = (gh.prs && gh.prs.length)
      ? gh.prs.map(pr => {
          const isMerged = pr.state === "merged";
          const stateClass = isMerged ? "ok" : (pr.state === "open" ? "noisy" : "broken");
          const ciBadge = pr.ci_status !== "neutral" ? ` · CI: ${esc(pr.ci_status.toUpperCase())}` : "";
          return `
            <div style="padding: 7px 9px; border: 1px solid var(--bd); margin-bottom: 6px; background: var(--s2); font-size: 11.5px;">
              <div style="display:flex; justify-content:space-between; align-items:center; gap: 8px;">
                <a href="${esc(pr.html_url)}" target="_blank" style="color:var(--orange); font-weight:700; text-decoration:none; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
                  #${pr.number} ${esc(pr.title)} ↗
                </a>
                <span class="trust ${stateClass}" style="flex-shrink:0;">${esc(pr.state.toUpperCase())}</span>
              </div>
              <div class="mono-mini muted mt-2" style="font-size:10px;">
                ${esc(pr.branch)} · ${esc(pr.author || "")}${ciBadge}
              </div>
            </div>`;
        }).join("")
      : '<div class="placeholder" style="padding:0">此倉庫目前無近期 PR 紀錄。</div>';

    ghSection = `
      <div style="grid-column: 1 / -1; margin-top: 10px; padding-top: 10px; border-top: 1px dashed var(--bd);">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
          <span class="mono-label">${t("sec_gh_pr")}</span>
          <a href="${esc(gh.html_url)}" target="_blank" class="mono-mini" style="color:var(--orange); text-decoration:none;">${esc(gh.full_name)} (${gh.is_private ? "Private" : "Public"}) ↗</a>
        </div>
        <div>${prsHtml}</div>
      </div>`;
  }

  // 快捷本機操作路徑條
  let quickBar = "";
  if (proj) {
    quickBar = `
      <div style="grid-column: 1 / -1; margin-bottom: 14px; padding: 10px 14px; background: var(--s1); border: 1px solid var(--bd); display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap;">
        <div style="min-width:0; display:flex; align-items:center; gap:8px;">
          <span class="mono-mini accent" style="font-weight:700; flex-shrink:0;">LOCAL PATH:</span>
          <code style="font-size:11.5px; background:transparent; color:var(--tx); word-break:break-all;">${esc(proj.local_path || '尚未定位到本機路徑')}</code>
        </div>
        <div style="display:flex; gap:8px; align-items:center; flex-shrink:0;">
          <button class="btn" data-detail-handoff="${esc(proj.project_key)}" data-name="${esc(proj.display_name)}" style="background:var(--s2); border:1px solid var(--bd); color:var(--tx); font-weight:600; font-size:11.5px; padding:4px 10px; cursor:pointer;" title="${currentLang === 'zh-TW' ? '一鍵複製結構化接續 Prompt 貼入 AI 開工' : 'Copy structured handoff prompt for AI'}">${t("btn_copy_handoff")}</button>
          ${renderActionGroup(proj)}
        </div>
      </div>`;
  }

  slot.innerHTML = `
    <div class="pdetail">
      ${quickBar}
      <div>
        <span class="mono-label">${t("sec_files_modified")}</span>
        ${fileListHtml}
      </div>
      <div>
        <span class="mono-label">${t("sec_timeline")}</span>
        ${tl}
      </div>
      <div>
        <span class="mono-label">${t("sec_open_loops")}</span>
        ${ll}
        <div class="pdetail-actions">
          <button class="btn btn-primary btn-sm" data-cp>${t("btn_snapshot_now")}</button>
        </div>
      </div>
      <div class="pdetail-sessions">
        <span class="mono-label">${t("sec_sessions")}</span>
        ${sessionsHtml}
        <div class="context-memory-boundary">${currentLang === "zh-TW" ? "依專案與事件間隔推定，不代表實際工時。" : "Inferred from event gaps; not actual working time."}</div>
      </div>
      ${ghSection}
    </div>`;

  attachActionGroupListeners(slot);
  const detailHandoffBtn = slot.querySelector("[data-detail-handoff]");
  if (detailHandoffBtn && proj) {
    detailHandoffBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      copyProjectHandoff(proj.project_key, proj.display_name);
    });
  }
  const cpBtn = slot.querySelector("[data-cp]");
  if (cpBtn) cpBtn.addEventListener("click", (ev) => { ev.stopPropagation(); triggerCheckpoint(); });
}


// ---------------------------------------------------------------- focus carousel (observed open loops + proposal-only suggestions)
async function loadOpenLoops() {
  try {
    loopsCache = await getJSON("/api/v1/open-loops");
  } catch (e) {
    loopsCache = [];
  }
  renderOpenLoops();
}

function focusDateMs(value) {
  const parsed = Date.parse(String(value || "").replace(" ", "T"));
  return Number.isFinite(parsed) ? parsed : 0;
}

function buildFocusCarouselItems() {
  const now = Date.now();
  const candidates = [];

  for (const loop of loopsCache) {
    const lastSeen = focusDateMs(loop.last_seen_at || loop.created_at);
    const ageDays = lastSeen ? Math.max(0, (now - lastSeen) / 86400000) : 30;
    // Open Loop 沒有人工 priority 欄位，因此只用可檢查的來源信心與時間排序，
    // 不把這個畫面偽裝成 AI 已判定的事實重要度。
    const score = 0.42 + Math.min(0.16, ageDays / 180) + Math.min(0.10, Number(loop.confidence || 0) * 0.10);
    candidates.push({
      key: `open-loop:${loop.id}`,
      kind: "open_loop",
      priority: "observed",
      score,
      project_key: loop.project_key,
      title: loop.title,
      detail: loop.created_at || "",
      updated_at: lastSeen,
      loop_id: loop.id,
    });
  }

  for (const proposal of secretaryProposalsCache?.proposals || []) {
    candidates.push({
      key: `proposal:${proposal.proposal_id}`,
      kind: "proposal",
      priority: proposal.priority || "medium",
      score: Number(proposal.score || 0),
      project_key: proposal.project_key || "OmniContext",
      title: proposal.detail || proposal.title,
      detail: proposal.suggested_action || proposal.reason || "",
      updated_at: 0,
      proposal_type: proposal.proposal_type,
      subject_ref: proposal.subject_ref || "",
    });
  }

  candidates.sort((left, right) => (
    right.score - left.score || right.updated_at - left.updated_at || left.key.localeCompare(right.key)
  ));

  // 同一專案最多兩張，避免單一大量待辦或 repository 佔滿整個焦點輪播。
  const projectCounts = new Map();
  const selected = [];
  for (const item of candidates) {
    const count = projectCounts.get(item.project_key) || 0;
    if (count >= 2) continue;
    projectCounts.set(item.project_key, count + 1);
    selected.push(item);
    if (selected.length === 5) break;
  }
  return selected;
}

function renderOpenLoops() {
  renderFocusCarousel();
}

function renderFocusCarousel() {
  const box = $("open-loops-list");
  const tally = $("loop-tally");
  if (!box || !tally) return;

  focusCarouselItems = buildFocusCarouselItems();
  tally.textContent = `${focusCarouselItems.length}/5 · ${loopsCache.length}`;
  if (!focusCarouselItems.length) {
    box.innerHTML = `<div class="placeholder">${t("ph_no_loops")}</div>`;
    return;
  }
  focusCarouselIndex = Math.min(focusCarouselIndex, focusCarouselItems.length - 1);
  const item = focusCarouselItems[focusCarouselIndex];
  const observed = item.kind === "open_loop";
  const source = observed ? t("focus_observed") : t("focus_proposal");
  const priority = observed ? source : String(item.priority || "medium").toUpperCase();
  const action = observed
    ? `<button class="focus-action focus-resolve-btn" data-resolve="${item.loop_id}">${t("focus_resolve")}</button>`
    : `<button class="focus-action focus-secondary-action" data-focus-snooze="1">${t("focus_snooze")}</button>`;
  const dots = focusCarouselItems.map((candidate, index) => `
    <button class="focus-dot ${index === focusCarouselIndex ? "active" : ""}" data-focus-index="${index}" aria-label="${esc(t("focus_count", {current: index + 1, total: focusCarouselItems.length, open: loopsCache.length}))}" aria-current="${index === focusCarouselIndex ? "true" : "false"}"></button>`
  ).join("");

  box.innerHTML = `
    <article class="focus-card ${observed ? "focus-observed" : "focus-proposal"}" data-id="${observed ? item.loop_id : ""}">
      <div class="focus-card-top">
        <span class="focus-source">${esc(source)}</span>
        <span class="focus-priority ${esc(String(item.priority || "medium").toLowerCase())}">${esc(priority)}</span>
      </div>
      <div class="focus-project">${esc(item.project_key)}</div>
      <h2 class="focus-title" title="${esc(item.title)}">${esc(item.title)}</h2>
      <div class="focus-detail">${esc(item.detail || (currentLang === "zh-TW" ? "可回到專案查看來源與下一步。" : "Open the project to review its source and next step."))}</div>
      <div class="focus-card-bottom">
        <div class="focus-controls">
          <button class="focus-nav" data-focus-prev aria-label="${esc(t("focus_previous"))}">←</button>
          <div class="focus-dots">${dots}</div>
          <button class="focus-nav" data-focus-next aria-label="${esc(t("focus_next"))}">→</button>
          <button class="focus-nav focus-toggle" data-focus-toggle aria-label="${esc(focusCarouselUserPaused ? t("focus_play") : t("focus_pause"))}" aria-pressed="${focusCarouselUserPaused}">${focusCarouselUserPaused ? "▶" : "Ⅱ"}</button>
        </div>
        <div class="focus-actions">
          <button class="focus-action focus-primary-action" data-focus-project="${esc(item.project_key)}">${t("focus_view_project")}</button>
          ${action}
        </div>
        <div class="focus-boundary">${t("focus_boundary")} · ${t("focus_count", {current: focusCarouselIndex + 1, total: focusCarouselItems.length, open: loopsCache.length})}</div>
      </div>
    </article>`;
}

function focusProject(projectKey) {
  if (!projectKey) return;
  const tabBtn = document.querySelector('.tabs button[data-tab="tab-projects"]');
  if (tabBtn) tabBtn.click();
  const isIdle = projectsCache.some(project => project.project_key === projectKey && project.idle_days > 60);
  if (isIdle) showAllProjects = true;
  expandProject(projectKey, true);
  showToast(currentLang === "zh-TW" ? `🎯 已定位並展開專案: ${projectKey}` : `🎯 Focused project: ${projectKey}`);
}

function initFocusCarousel() {
  const box = $("open-loops-list");
  if (!box) return;

  box.addEventListener("mouseenter", () => { focusCarouselPointerPaused = true; });
  box.addEventListener("mouseleave", () => { focusCarouselPointerPaused = false; });
  box.addEventListener("focusin", () => { focusCarouselPointerPaused = true; });
  box.addEventListener("focusout", () => { focusCarouselPointerPaused = false; });
  box.addEventListener("click", event => {
    const target = event.target.closest("button");
    if (!target) return;
    if (target.dataset.focusIndex !== undefined) {
      focusCarouselIndex = Number(target.dataset.focusIndex);
      renderFocusCarousel();
    } else if (target.hasAttribute("data-focus-prev")) {
      focusCarouselIndex = (focusCarouselIndex - 1 + focusCarouselItems.length) % focusCarouselItems.length;
      renderFocusCarousel();
    } else if (target.hasAttribute("data-focus-next")) {
      focusCarouselIndex = (focusCarouselIndex + 1) % focusCarouselItems.length;
      renderFocusCarousel();
    } else if (target.hasAttribute("data-focus-toggle")) {
      focusCarouselUserPaused = !focusCarouselUserPaused;
      renderFocusCarousel();
    } else if (target.dataset.focusProject) {
      focusProject(target.dataset.focusProject);
    } else if (target.dataset.resolve) {
      resolveLoop(target.closest(".focus-card"));
    } else if (target.dataset.focusSnooze) {
      const item = focusCarouselItems[focusCarouselIndex];
      if (item) window.snoozeProposal(item.proposal_type, item.project_key, item.subject_ref, 7);
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) renderFocusCarousel();
  });
  if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    focusCarouselTimer = window.setInterval(() => {
      if (document.hidden || focusCarouselUserPaused || focusCarouselPointerPaused || focusCarouselItems.length < 2) return;
      focusCarouselIndex = (focusCarouselIndex + 1) % focusCarouselItems.length;
      renderFocusCarousel();
    }, 9000);
  }
}

async function resolveLoop(el) {
  const id = el?.dataset.id;
  if (!id || el.classList.contains("done")) return;
  el.classList.add("done");
  const resBtn = el.querySelector("[data-resolve]");
  if (resBtn) {
    resBtn.style.background = "var(--ok)";
    resBtn.style.borderColor = "var(--ok)";
    resBtn.style.color = "#fff";
  }
  try {
    await postJSON(`/api/v1/open-loops/${id}/resolve`);
    loopsCache = loopsCache.filter(loop => String(loop.id) !== String(id));
    showToast(currentLang === "zh-TW" ? "⚡ 未結事項已標記為已結案！" : "⚡ Marked open loop as resolved!");
    setTimeout(() => { renderOpenLoops(); loadSecretaryProposals(); loadProjects(); }, 550);
  } catch (e) {
    el.classList.remove("done");
    if (resBtn) {
      resBtn.style.background = "";
      resBtn.style.borderColor = "";
      resBtn.style.color = "";
    }
    showToast("⚠️ 結案失敗: " + e.message);
  }
}

// ---------------------------------------------------------------- settings
function initSettingsForm() {
  $("btn-add-dir").addEventListener("click", () => {
    const input = $("input-new-dir");
    const v = input.value.trim();
    if (v && !configDirs.includes(v)) { configDirs.push(v); renderTagList("dir-list", configDirs, removeDir); input.value = ""; }
  });
  $("btn-browse-dir").addEventListener("click", async () => {
    try {
      const res = await postJSON("/api/v1/utils/browse-folder");
      if (res && res.status === "success" && res.path) {
        $("input-new-dir").value = res.path;
        if (!configDirs.includes(res.path)) {
          configDirs.push(res.path);
          renderTagList("dir-list", configDirs, removeDir);
        }
      }
    } catch (e) { console.error("Browse dir error", e); }
  });

  $("btn-add-calendar-path").addEventListener("click", () => {
    const input = $("input-new-calendar-path");
    const v = input.value.trim();
    if (v && !configCalendarPaths.includes(v)) { configCalendarPaths.push(v); renderTagList("calendar-path-list", configCalendarPaths, removeCalendarPath); input.value = ""; }
  });
  $("btn-browse-calendar").addEventListener("click", async () => {
    try {
      const res = await postJSON("/api/v1/utils/browse-folder");
      if (res && res.status === "success" && res.path) {
        $("input-new-calendar-path").value = res.path;
        if (!configCalendarPaths.includes(res.path)) {
          configCalendarPaths.push(res.path);
          renderTagList("calendar-path-list", configCalendarPaths, removeCalendarPath);
        }
      }
    } catch (e) { console.error("Browse calendar error", e); }
  });

  $("btn-add-repo").addEventListener("click", () => {
    const input = $("input-new-repo");
    const v = input.value.trim();
    if (v && !configRepos.includes(v)) { configRepos.push(v); renderTagList("repo-list", configRepos, removeRepo); input.value = ""; }
  });
  $("btn-browse-repo").addEventListener("click", async () => {
    try {
      const res = await postJSON("/api/v1/utils/browse-folder");
      if (res && res.status === "success" && res.path) {
        $("input-new-repo").value = res.path;
        if (!configRepos.includes(res.path)) {
          configRepos.push(res.path);
          renderTagList("repo-list", configRepos, removeRepo);
        }
      }
    } catch (e) { console.error("Browse repo error", e); }
  });

  $("btn-save-settings").addEventListener("click", saveSettings);
  $("btn-recheck-llm-key").addEventListener("click", loadLLMStatus);
  $("select-llm-provider").addEventListener("change", (e) => {
    const p = e.target.value;
    const defaults = { gemini: "gemini-3.7-flash", anthropic: "claude-3-5-sonnet-20241022", openai: "gpt-4o", ollama: "llama3.1:8b" };
    const envDefaults = { gemini: "GEMINI_API_KEY", anthropic: "ANTHROPIC_API_KEY", openai: "OPENAI_API_KEY", ollama: "" };
    $("input-model-name").value = (currentConfig && currentConfig.synthesizer && currentConfig.synthesizer[p] && currentConfig.synthesizer[p].model) || defaults[p] || "";
    $("input-llm-key-env").value = (currentConfig && currentConfig.synthesizer && currentConfig.synthesizer[p] && currentConfig.synthesizer[p].api_key_env) || envDefaults[p] || "";
    $("input-llm-key-env").disabled = p === "ollama";
    renderLLMStatus();
  });

  // P5-R5 自訂排程任務：template／schedule kind 切換對應欄位，新增走 token API。
  $("select-sched-template").addEventListener("change", () => {
    $("sched-project-field").hidden = $("select-sched-template").value !== "generate_handoff";
  });
  $("select-sched-kind").addEventListener("change", () => {
    const kind = $("select-sched-kind").value;
    $("sched-weekday-field").hidden = kind !== "weekly";
    $("sched-day-field").hidden = kind !== "monthly";
  });
  $("btn-add-sched-task").addEventListener("click", addScheduledTask);

  // Telegram 設定流程（P5-R4b 前置）：偵測 chat id → 即時測試 → 通過才儲存。
  $("btn-tg-detect").addEventListener("click", tgDetectChat);
  $("btn-tg-test").addEventListener("click", tgTest);
  $("btn-tg-connect").addEventListener("click", tgConnect);
  $("btn-tg-disconnect").addEventListener("click", tgDisconnect);
  // P5-R4b inline 批准：解鎖／上鎖批准通道（解鎖需 execution token）。
  $("btn-tg-arm").addEventListener("click", tgArm);
  $("btn-tg-arm-code").addEventListener("click", tgIssueArmCode);
  $("btn-line-test").addEventListener("click", () => lineTest(false));
  $("btn-line-connect").addEventListener("click", () => lineTest(true));
  $("btn-line-disconnect").addEventListener("click", lineDisconnect);
  $("btn-tg-disarm").addEventListener("click", tgDisarm);
}

function renderTagList(id, list, onRemove) {
  const box = $(id);
  if (!list.length) { box.innerHTML = '<div class="muted small">尚未設定</div>'; return; }
  box.innerHTML = list.map((v, i) => `<div class="tag"><span>${esc(v)}</span><span class="tag-x" data-i="${i}">✕</span></div>`).join("");
  box.querySelectorAll(".tag-x").forEach(x => x.addEventListener("click", () => onRemove(Number(x.dataset.i))));
}
function removeDir(i) { configDirs.splice(i, 1); renderTagList("dir-list", configDirs, removeDir); }
function removeRepo(i) { configRepos.splice(i, 1); renderTagList("repo-list", configRepos, removeRepo); }
function removeCalendarPath(i) { configCalendarPaths.splice(i, 1); renderTagList("calendar-path-list", configCalendarPaths, removeCalendarPath); }

async function loadConfig() {
  try {
    currentConfig = await getJSON("/api/v1/config");
    const w = currentConfig.watchers || {};
    const s = currentConfig.synthesizer || {};
    const usage = currentConfig.usage_tracking || {};

    configDirs = (w.file_watcher && w.file_watcher.watch_directories) || [];
    configRepos = (w.git_watcher && w.git_watcher.repositories) || [];
    renderTagList("dir-list", configDirs, removeDir);
    renderTagList("repo-list", configRepos, removeRepo);

    $("input-schedule-time").value = (s.schedule && s.schedule.time) || "23:30";
    $("input-checkpoint-interval").value = (s.periodic_checkpoint && s.periodic_checkpoint.interval_hours) || 2;
    const provider = s.provider || "gemini";
    $("select-llm-provider").value = provider;
    $("input-model-name").value = (s[provider] && s[provider].model) || "gemini-3.7-flash";
    const envDefaults = { gemini: "GEMINI_API_KEY", anthropic: "ANTHROPIC_API_KEY", openai: "OPENAI_API_KEY", ollama: "" };
    $("input-llm-key-env").value = (s[provider] && s[provider].api_key_env) || envDefaults[provider] || "";
    $("input-llm-key-env").disabled = provider === "ollama";

    const exts = (w.file_watcher && w.file_watcher.extensions) || [];
    document.querySelectorAll("#ext-checkboxes input").forEach(cb => { cb.checked = exts.includes(cb.value); });

    const agent = w.agent_log_watcher || {};
    const browser = w.browser || {};
    $("toggle-claude-code").checked = agent.claude_code !== false;
    $("toggle-claude-desktop").checked = agent.claude_desktop !== false;
    $("toggle-codex").checked = agent.codex !== false;
    $("toggle-antigravity").checked = agent.antigravity !== false;
    $("toggle-gemini").checked = browser.gemini !== false;
    $("toggle-chatgpt").checked = browser.chatgpt !== false;
    $("toggle-claude-web").checked = browser.claude_web !== false;
    $("toggle-window-focus").checked = !(w.window_watcher && w.window_watcher.enabled === false);
    const calendar = w.calendar_watcher || {};
    $("toggle-calendar").checked = calendar.enabled !== false;
    configCalendarPaths = Array.isArray(calendar.paths) ? calendar.paths.slice() : [];
    renderTagList("calendar-path-list", configCalendarPaths, removeCalendarPath);
    $("input-calendar-horizon").value = Number(calendar.horizon_days || 30);
    $("toggle-calendar-titles").checked = calendar.store_titles !== false;
    const executor = (currentConfig.proactive_secretary || {}).executor || {};
    $("toggle-executor-enabled").checked = executor.enabled === true;
    $("input-greeting-name").value = ((currentConfig.proactive_secretary || {}).greeting || {}).display_name || "";
    $("toggle-executor-l2").checked = !!(executor.l2 && executor.l2.enabled === true);
    $("toggle-executor-l2-write").checked = !!(executor.l2 && executor.l2.allow_write === true);
    $("select-agent-cli").value = (executor.agent_cli && executor.agent_cli.binary) === "codex" ? "codex" : "claude";
    $("toggle-scheduled-tasks").checked = !!(executor.scheduled_tasks && executor.scheduled_tasks.enabled === true);
    $("toggle-tg-approvals").checked = !!(executor.telegram_approvals && executor.telegram_approvals.enabled === true);
    $("toggle-tg-remote-arm").checked = !!(executor.telegram_approvals && executor.telegram_approvals.allow_remote_arm === true);
    const tgChat = ((currentConfig.notifiers || {}).telegram || {}).chat || {};
    $("toggle-tg-chat").checked = tgChat.enabled === true;
    loadScheduledTasks();
    loadTelegramStatus();
    loadTelegramApprovalsStatus();
    loadLineStatus();
    $("toggle-usage-tracking").checked = usage.enabled === true;
    const usageNotifications = usage.notifications || {};
    $("toggle-usage-notifications").checked = usageNotifications.enabled === true;
    $("input-usage-daily-goal").value = usage.daily_goal_minutes || 360;
    $("input-usage-milestones").value = (usage.milestones_minutes || [120, 240, 360]).join(", ");
    $("select-usage-tone").value = usageNotifications.tone || "encouraging";
    $("input-usage-quiet-start").value = usageNotifications.quiet_hours_start || "22:00";
    $("input-usage-quiet-end").value = usageNotifications.quiet_hours_end || "08:00";
    $("input-usage-cooldown").value = usageNotifications.cooldown_minutes ?? 60;
    await loadLLMStatus();
  } catch (e) { console.error("config load failed", e); }
}

async function loadLLMStatus() {
  const badge = $("llm-key-status-badge");
  badge.className = "trust noisy";
  badge.textContent = "CHECKING";
  try {
    llmStatusCache = await getJSON("/api/v1/llm/status");
    renderLLMStatus();
  } catch (e) {
    llmStatusCache = null;
    badge.className = "trust broken";
    badge.textContent = "UNAVAILABLE";
    $("llm-key-status-text").textContent = currentLang === "zh-TW"
      ? "目前無法取得 API key 偵測狀態。"
      : "API key detection status is unavailable.";
  }
}

function renderLLMStatus() {
  if (!llmStatusCache) return;
  const provider = $("select-llm-provider").value;
  const item = (llmStatusCache.providers || {})[provider] || {};
  const badge = $("llm-key-status-badge");
  const sourceLabels = currentLang === "zh-TW" ? {
    process: "目前執行程序環境",
    windows_user: "Windows 使用者環境變數",
    windows_machine: "Windows 系統環境變數",
    local_service: "本機 Ollama",
    missing: "未偵測"
  } : {
    process: "current process environment",
    windows_user: "Windows user environment",
    windows_machine: "Windows machine environment",
    local_service: "local Ollama",
    missing: "not detected"
  };
  badge.className = "trust " + (item.configured ? "ok" : "broken");
  badge.textContent = provider === "ollama" ? "LOCAL" : item.configured ? "DETECTED" : "MISSING";
  const envName = item.env_var || $("input-llm-key-env").value.trim() || "—";
  const source = sourceLabels[item.source] || item.source || sourceLabels.missing;
  $("llm-key-status-text").textContent = item.configured
    ? (currentLang === "zh-TW"
      ? `已偵測 ${envName}，來源：${source}。金鑰內容不會傳到瀏覽器。`
      : `${envName} detected from the ${source}. The secret value is not sent to the browser.`)
    : (currentLang === "zh-TW"
      ? `尚未偵測 ${envName}。請在作業系統使用者環境變數設定後按「重新檢查」。`
      : `${envName} was not detected. Set it in the OS user environment, then select Recheck.`);
}

async function saveSettings() {
  if (!currentConfig) currentConfig = {};
  const cfg = currentConfig;
  const exts = Array.from(document.querySelectorAll("#ext-checkboxes input:checked")).map(cb => cb.value);
  const provider = $("select-llm-provider").value;

  cfg.watchers = cfg.watchers || {};
  cfg.watchers.file_watcher = cfg.watchers.file_watcher || { enabled: true };
  cfg.watchers.file_watcher.watch_directories = configDirs;
  cfg.watchers.file_watcher.extensions = exts;

  cfg.watchers.git_watcher = cfg.watchers.git_watcher || { enabled: true };
  cfg.watchers.git_watcher.repositories = configRepos;

  cfg.watchers.agent_log_watcher = cfg.watchers.agent_log_watcher || { enabled: true };
  cfg.watchers.agent_log_watcher.claude_code = $("toggle-claude-code").checked;
  cfg.watchers.agent_log_watcher.claude_desktop = $("toggle-claude-desktop").checked;
  cfg.watchers.agent_log_watcher.codex = $("toggle-codex").checked;
  cfg.watchers.agent_log_watcher.antigravity = $("toggle-antigravity").checked;

  cfg.watchers.browser = cfg.watchers.browser || {};
  cfg.watchers.browser.gemini = $("toggle-gemini").checked;
  cfg.watchers.browser.chatgpt = $("toggle-chatgpt").checked;
  cfg.watchers.browser.claude_web = $("toggle-claude-web").checked;

  cfg.watchers.window_watcher = cfg.watchers.window_watcher || { enabled: true };
  cfg.watchers.window_watcher.enabled = $("toggle-window-focus").checked;

  cfg.watchers.calendar_watcher = cfg.watchers.calendar_watcher || { enabled: true, scan_interval_seconds: 900 };
  cfg.watchers.calendar_watcher.enabled = $("toggle-calendar").checked;
  cfg.watchers.calendar_watcher.paths = configCalendarPaths;
  cfg.watchers.calendar_watcher.horizon_days = Math.max(1, Math.min(366, Number($("input-calendar-horizon").value) || 30));
  cfg.watchers.calendar_watcher.store_titles = $("toggle-calendar-titles").checked;

  cfg.synthesizer = cfg.synthesizer || {};
  cfg.synthesizer.schedule = cfg.synthesizer.schedule || { enabled: true };
  cfg.synthesizer.schedule.time = $("input-schedule-time").value.trim();
  cfg.synthesizer.periodic_checkpoint = cfg.synthesizer.periodic_checkpoint || { enabled: true };
  cfg.synthesizer.periodic_checkpoint.interval_hours = parseInt($("input-checkpoint-interval").value, 10) || 2;
  cfg.synthesizer.provider = provider;
  cfg.synthesizer[provider] = cfg.synthesizer[provider] || {};
  cfg.synthesizer[provider].model = $("input-model-name").value.trim();
  if (provider !== "ollama") {
    const envName = $("input-llm-key-env").value.trim();
    if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(envName)) {
      cfg.synthesizer[provider].api_key_env = envName;
    }
  }

  // P5-R2/R3 執行器：開關與 CLI 選擇；換 CLI 時同步重設對應的預設 args，
  // 使用者自訂的 args 只要不換 binary 就原樣保留。
  cfg.proactive_secretary = cfg.proactive_secretary || {};
  cfg.proactive_secretary.executor = cfg.proactive_secretary.executor || {};
  const executorCfg = cfg.proactive_secretary.executor;
  executorCfg.enabled = $("toggle-executor-enabled").checked;
  cfg.proactive_secretary.greeting = cfg.proactive_secretary.greeting || {};
  cfg.proactive_secretary.greeting.display_name = ($("input-greeting-name").value || "").trim().slice(0, 40);
  executorCfg.l2 = executorCfg.l2 || {};
  executorCfg.l2.enabled = $("toggle-executor-l2").checked;
  executorCfg.l2.allow_write = $("toggle-executor-l2-write").checked;
  executorCfg.agent_cli = executorCfg.agent_cli || {};
  const cliChoice = $("select-agent-cli").value === "codex" ? "codex" : "claude";
  if (executorCfg.agent_cli.binary !== cliChoice) {
    executorCfg.agent_cli.binary = cliChoice;
    executorCfg.agent_cli.args = cliChoice === "codex" ? ["exec", "{prompt}"] : ["-p", "{prompt}"];
  }
  executorCfg.scheduled_tasks = executorCfg.scheduled_tasks || {};
  executorCfg.scheduled_tasks.enabled = $("toggle-scheduled-tasks").checked;
  executorCfg.telegram_approvals = executorCfg.telegram_approvals || {};
  executorCfg.telegram_approvals.enabled = $("toggle-tg-approvals").checked;
  executorCfg.telegram_approvals.allow_remote_arm = $("toggle-tg-remote-arm").checked;
  cfg.notifiers = cfg.notifiers || {};
  cfg.notifiers.telegram = cfg.notifiers.telegram || {};
  cfg.notifiers.telegram.chat = cfg.notifiers.telegram.chat || {};
  cfg.notifiers.telegram.chat.enabled = $("toggle-tg-chat").checked;

  cfg.usage_tracking = cfg.usage_tracking || {};
  cfg.usage_tracking.enabled = $("toggle-usage-tracking").checked;
  cfg.usage_tracking.daily_goal_minutes = Math.max(15, parseInt($("input-usage-daily-goal").value, 10) || 360);
  const milestones = $("input-usage-milestones").value
    .split(",")
    .map(value => parseInt(value.trim(), 10))
    .filter(value => Number.isInteger(value) && value > 0);
  cfg.usage_tracking.milestones_minutes = [...new Set(milestones)].sort((a, b) => a - b);
  cfg.usage_tracking.notifications = cfg.usage_tracking.notifications || {};
  cfg.usage_tracking.notifications.enabled = $("toggle-usage-notifications").checked;
  cfg.usage_tracking.notifications.tone = $("select-usage-tone").value;
  cfg.usage_tracking.notifications.quiet_hours_start = $("input-usage-quiet-start").value || "22:00";
  cfg.usage_tracking.notifications.quiet_hours_end = $("input-usage-quiet-end").value || "08:00";
  cfg.usage_tracking.notifications.cooldown_minutes = Math.max(0, parseInt($("input-usage-cooldown").value, 10) || 0);

  const btn = $("btn-save-settings");
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = "儲存中…";
  try {
    await postJSON("/api/v1/config", cfg);
    btn.textContent = "✓ 已套用";
    refreshStatus();
    loadLLMStatus();
  } catch (e) {
    btn.textContent = "儲存失敗";
  } finally {
    setTimeout(() => { btn.disabled = false; btn.textContent = label; }, 1600);
  }
}

// ------------------------------------------------------ P5-R5 scheduled tasks
let scheduledTasksCache = null;

function requireExecutionToken() {
  const zh = currentLang === "zh-TW";
  let token = sessionStorage.getItem("omni_execution_token") || "";
  if (!token) {
    token = (prompt(zh
      ? "輸入 execution token（在終端機執行 `omnicontext init --show-token` 取得）："
      : "Enter execution token (shown by `omnicontext init --show-token`):") || "").trim();
    if (!token) return null;
    sessionStorage.setItem("omni_execution_token", token);
  }
  return token;
}

async function schedRequest(url, method, body) {
  const zh = currentLang === "zh-TW";
  const token = requireExecutionToken();
  if (!token) return null;
  const res = await fetch(API + url, {
    method,
    headers: { "x-omnicontext-execution-token": token, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) {
    sessionStorage.removeItem("omni_execution_token");
    alert(zh ? "execution token 無效，請重試。" : "Invalid execution token.");
    return null;
  }
  if (!res.ok) {
    alert((zh ? "操作被拒絕：" : "Rejected: ") + (data.detail || res.status));
    return null;
  }
  return data;
}

function schedScheduleLabel(task) {
  const zh = currentLang === "zh-TW";
  const weekdays = zh
    ? ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
    : ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  if (task.schedule_kind === "weekly") {
    return (zh ? "每週" : "Weekly ") + (weekdays[task.weekday] || "?") + " " + task.run_time;
  }
  if (task.schedule_kind === "monthly") {
    return zh ? `每月 ${task.day_of_month} 日 ${task.run_time}` : `Monthly day ${task.day_of_month} ${task.run_time}`;
  }
  return (zh ? "每日 " : "Daily ") + task.run_time;
}

async function loadScheduledTasks() {
  const box = $("sched-task-list");
  if (!box) return;
  try {
    scheduledTasksCache = await getJSON("/api/v1/secretary/scheduled-tasks");
    renderScheduledTasks();
    const select = $("select-sched-template");
    const templates = scheduledTasksCache.templates || [];
    select.innerHTML = templates
      .map(item => `<option value="${esc(item.template_id)}">${esc(item.label)}</option>`)
      .join("");
    $("sched-project-field").hidden = select.value !== "generate_handoff";
  } catch (e) {
    box.innerHTML = `<span class="muted small">${esc(String(e.message || e))}</span>`;
  }
}

function renderScheduledTasks() {
  const zh = currentLang === "zh-TW";
  const box = $("sched-task-list");
  if (!box || !scheduledTasksCache) return;
  const tasks = scheduledTasksCache.tasks || [];
  if (!tasks.length) {
    box.innerHTML = `<span class="muted small">${zh ? "尚未建立任何排程任務。" : "No scheduled tasks yet."}</span>`;
    return;
  }
  box.innerHTML = tasks.map(task => {
    const params = task.params && task.params.project_key ? ` · ${esc(task.params.project_key)}` : "";
    const last = task.last_run_at
      ? `${zh ? "上次" : "last"} ${esc(task.last_run_at.replace("T", " "))} → ${esc(task.last_status || "?")}`
      : (zh ? "尚未執行" : "not run yet");
    const stateLabel = task.enabled ? (zh ? "停用" : "Disable") : (zh ? "啟用" : "Enable");
    const registered = task.template_registered
      ? ""
      : ` <span class="trust broken">${zh ? "TEMPLATE 已下架" : "TEMPLATE UNREGISTERED"}</span>`;
    return `<div class="tag" style="justify-content: space-between; width: 100%; margin-bottom: 4px;">
      <span>${task.enabled ? "🟢" : "⚪"} <b>${esc(task.template_label || task.template_id)}</b>${params}
        · ${esc(schedScheduleLabel(task))} · <span class="muted small">${last}</span>${registered}</span>
      <span>
        <button class="btn btn-ghost btn-sm" onclick="runScheduledTaskNow(${task.id})">${zh ? "立即執行" : "Run now"}</button>
        <button class="btn btn-ghost btn-sm" onclick="toggleScheduledTask(${task.id}, ${task.enabled ? "false" : "true"})">${stateLabel}</button>
        <button class="btn btn-ghost btn-sm" onclick="deleteScheduledTask(${task.id})">✕</button>
      </span>
    </div>`;
  }).join("");
}

window.runScheduledTaskNow = async function (taskId) {
  const zh = currentLang === "zh-TW";
  const data = await schedRequest(`/api/v1/secretary/scheduled-tasks/${taskId}/run`, "POST");
  if (data) {
    alert((zh ? "已執行：" : "Executed: ") + (data.status || "?")
      + (data.result && data.result.output_path ? `\n${data.result.output_path}` : ""));
    loadScheduledTasks();
  }
};

window.toggleScheduledTask = async function (taskId, enabled) {
  const data = await schedRequest(`/api/v1/secretary/scheduled-tasks/${taskId}`, "PATCH", { enabled });
  if (data) loadScheduledTasks();
};

window.deleteScheduledTask = async function (taskId) {
  const zh = currentLang === "zh-TW";
  if (!confirm(zh ? "刪除此排程任務？" : "Delete this scheduled task?")) return;
  const data = await schedRequest(`/api/v1/secretary/scheduled-tasks/${taskId}`, "DELETE");
  if (data) loadScheduledTasks();
};

async function addScheduledTask() {
  const kind = $("select-sched-kind").value;
  const payload = {
    template_id: $("select-sched-template").value,
    params: {},
    schedule_kind: kind,
    run_time: $("input-sched-time").value || "08:30",
  };
  if (payload.template_id === "generate_handoff") {
    payload.params.project_key = $("input-sched-project").value.trim();
  }
  if (kind === "weekly") payload.weekday = parseInt($("select-sched-weekday").value, 10);
  if (kind === "monthly") payload.day_of_month = parseInt($("input-sched-day").value, 10) || 1;
  const data = await schedRequest("/api/v1/secretary/scheduled-tasks", "POST", payload);
  if (data) loadScheduledTasks();
}

// ------------------------------------------------------ telegram setup flow
let telegramStatusCache = null;

async function loadTelegramStatus() {
  const badge = $("tg-status-badge");
  if (!badge) return;
  const zh = currentLang === "zh-TW";
  try {
    telegramStatusCache = await getJSON("/api/v1/telegram/status");
    const st = telegramStatusCache;
    const sourceLabel = (source) => ({
      env: zh ? "環境變數" : "env var",
      config: "config.yaml",
      provided: zh ? "本次輸入" : "just entered",
      missing: zh ? "未設定" : "not set",
    })[source] || source;
    if (st.enabled && st.token_configured && st.chat_id_configured) {
      badge.className = "trust ok";
      badge.textContent = zh ? "已啟用" : "ENABLED";
    } else if (st.token_configured) {
      badge.className = "trust noisy";
      badge.textContent = zh ? "已設定未啟用" : "CONFIGURED";
    } else {
      badge.className = "trust broken";
      badge.textContent = zh ? "未設定" : "NOT SET";
    }
    $("input-tg-token").placeholder = st.token_configured
      ? (zh ? `已設定（${sourceLabel(st.token_source)}）；留空沿用` : `configured (${sourceLabel(st.token_source)}); leave blank to keep`)
      : "123456789:AA...";
    $("input-tg-chat").placeholder = st.chat_id_configured
      ? (zh ? `已設定（${sourceLabel(st.chat_id_source)}）；留空沿用` : `configured (${sourceLabel(st.chat_id_source)}); leave blank to keep`)
      : "—";
    $("input-tg-morning").value = st.morning_briefing_time || "09:00";
    $("input-tg-evening").value = st.evening_summary_time || "23:30";
    const setup = $("panel-tg-setup");
    if (setup) {
      let preference = null;
      try { preference = localStorage.getItem("omni-panel-open:panel-tg-setup"); } catch (_) { /* 保持預設 */ }
      if (preference === null) {
        // 已連線且啟用 → 連線設定收合，讓批准區塊成為卡片主體
        setup.open = !(st.enabled && st.token_configured && st.chat_id_configured);
      }
    }
  } catch (e) {
    badge.className = "trust broken";
    badge.textContent = "UNAVAILABLE";
  }
}

function tgRenderResult(receipt) {
  const zh = currentLang === "zh-TW";
  const box = $("tg-test-result");
  if (!box) return;
  if (!receipt) { box.textContent = ""; return; }
  if (receipt.ok) {
    const parts = [
      (zh ? "✅ token 有效，bot：@" : "✅ token valid, bot: @") + (receipt.bot_username || "?"),
    ];
    if (receipt.message_sent === true) {
      parts.push(zh ? "測試訊息已送達" : "test message delivered");
    }
    if (receipt.saved === true) {
      parts.push(zh ? "已儲存並啟用（排程已重載）" : "saved & enabled (scheduler reloaded)");
    }
    if (receipt.hint) parts.push(receipt.hint);
    box.textContent = parts.join(" · ");
    box.style.color = "var(--ok, #4caf50)";
  } else {
    box.textContent = `❌ ${receipt.error_code || "error"}：${receipt.hint || ""}`;
    box.style.color = "var(--danger, #e57373)";
  }
}

function tgPayload() {
  const token = $("input-tg-token").value.trim();
  const chat = $("input-tg-chat").value.trim();
  return {
    bot_token: token || null,
    chat_id: chat || null,
  };
}

async function tgDetectChat() {
  const zh = currentLang === "zh-TW";
  const box = $("tg-chat-candidates");
  box.hidden = false;
  box.innerHTML = `<span class="muted small">${zh ? "偵測中…" : "Detecting…"}</span>`;
  try {
    const res = await postJSON("/api/v1/telegram/detect-chat-id", { bot_token: tgPayload().bot_token });
    if (!res.ok) { tgRenderResult(res); box.hidden = true; return; }
    const candidates = res.candidates || [];
    if (!candidates.length) {
      box.innerHTML = `<span class="muted small">${esc(res.hint || (zh ? "沒有偵測到對話" : "No chats detected"))}</span>`;
      return;
    }
    box.innerHTML = candidates.map(c =>
      `<button class="btn btn-ghost btn-sm tg-chat-pick" data-chat="${esc(c.chat_id)}">💬 ${esc(c.display_name)}（${esc(c.chat_type)} · ${esc(c.chat_id)}）</button>`
    ).join(" ");
    box.querySelectorAll(".tg-chat-pick").forEach(btn => btn.addEventListener("click", () => {
      $("input-tg-chat").value = btn.dataset.chat;
      box.hidden = true;
    }));
  } catch (e) {
    box.innerHTML = `<span class="muted small">${esc(String(e.message || e))}</span>`;
  }
}

async function tgTest() {
  const zh = currentLang === "zh-TW";
  $("tg-test-result").textContent = zh ? "測試中…" : "Testing…";
  try {
    tgRenderResult(await postJSON("/api/v1/telegram/test", tgPayload()));
  } catch (e) {
    tgRenderResult({ ok: false, error_code: "request_failed", hint: String(e.message || e) });
  }
}

async function tgConnect() {
  const zh = currentLang === "zh-TW";
  $("tg-test-result").textContent = zh ? "驗證並儲存中…" : "Validating & saving…";
  try {
    const payload = tgPayload();
    payload.enabled = true;
    payload.morning_briefing_time = $("input-tg-morning").value || "09:00";
    payload.evening_summary_time = $("input-tg-evening").value || "23:30";
    const receipt = await postJSON("/api/v1/telegram/connect", payload);
    tgRenderResult(receipt);
    if (receipt.saved) {
      $("input-tg-token").value = "";
      $("input-tg-chat").value = "";
      loadTelegramStatus();
    }
  } catch (e) {
    tgRenderResult({ ok: false, error_code: "request_failed", hint: String(e.message || e) });
  }
}

async function loadTelegramApprovalsStatus() {
  const box = $("tg-approvals-status");
  if (!box) return;
  const zh = currentLang === "zh-TW";
  try {
    const st = await getJSON("/api/v1/telegram/approvals/status");
    const parts = [];
    if (!st.enabled) {
      parts.push(zh ? "批准通道：未啟用（勾選上方選項並儲存設定）" : "Approvals: disabled (tick the option above and save settings)");
    } else if (st.armed) {
      parts.push((zh ? "🔓 已解鎖至 " : "🔓 unlocked until ") + String(st.armed_until || "").replace("T", " "));
    } else {
      parts.push(zh ? "🔒 已上鎖（按「解鎖遠端批准」啟用）" : "🔒 locked (press Unlock to enable)");
    }
    parts.push((zh ? "輪詢器：" : "poller: ") + (st.poller_running ? (zh ? "運行中" : "running") : (zh ? "未運行（啟用後重載設定）" : "not running")));
    if (st.arm_code && st.arm_code.pending) {
      parts.push((zh ? "解鎖碼有效至 " : "unlock code until ") + String(st.arm_code.expires_at || "").slice(11, 16));
    }
    try {
      const chat = await getJSON("/api/v1/telegram/chat/status");
      parts.push((zh ? "對話：" : "chat: ") + (chat.enabled ? (zh ? "開" : "on") : (zh ? "關" : "off")));
      if (chat.enabled && chat.remote_arm_enabled) parts.push(zh ? "允許 /arm" : "/arm allowed");
    } catch (e) {}
    box.textContent = parts.join(" · ");
  } catch (e) {
    box.textContent = "";
  }
}

async function tgIssueArmCode() {
  const zh = currentLang === "zh-TW";
  const box = $("tg-arm-code-box");
  const data = await schedRequest("/api/v1/telegram/approvals/arm-code", "POST");
  if (!data) return;
  if (box) {
    box.innerHTML = `<span class="arm-code">${esc(data.code)}</span>` +
      `<span class="muted small">${zh
        ? `${Math.round((data.ttl_seconds || 300) / 60)} 分鐘內有效、只能用一次。在手機傳「/arm ${esc(data.code)}」解鎖。`
        : `Valid for ${Math.round((data.ttl_seconds || 300) / 60)} min, single use. Send “/arm ${esc(data.code)}” from your phone.`}</span>`;
    box.hidden = false;
    setTimeout(() => { box.hidden = true; box.innerHTML = ""; }, (data.ttl_seconds || 300) * 1000);
  }
  loadTelegramApprovalsStatus();
}

// ---------------------------------------------------------------- ADR-014 LINE（只能推播）
async function loadLineStatus() {
  const badge = $("line-status-badge");
  if (!badge) return;
  const zh = currentLang === "zh-TW";
  try {
    const st = await getJSON("/api/v1/line/status");
    if (st.enabled && st.token_configured && st.to_configured) {
      badge.textContent = zh ? "已啟用（推播）" : "ENABLED (PUSH)";
      badge.className = "trust ok";
    } else if (st.token_configured) {
      badge.textContent = zh ? "待設定收件 ID" : "NEEDS USER ID";
      badge.className = "trust noisy";
    } else {
      badge.textContent = zh ? "未設定" : "NOT SET";
      badge.className = "trust broken";
    }
    if (st.token_source === "env" && $("input-line-token")) {
      $("input-line-token").placeholder = zh ? "（已由環境變數提供）" : "(provided by env var)";
    }
  } catch (e) {
    badge.textContent = zh ? "讀不到" : "UNKNOWN";
    badge.className = "trust broken";
  }
}

function lineRenderResult(receipt) {
  const box = $("line-test-result");
  if (!box) return;
  const zh = currentLang === "zh-TW";
  if (receipt.ok) {
    const name = receipt.bot_display_name || receipt.bot_basic_id || "";
    box.textContent = (receipt.message_sent
      ? (zh ? `✅ 連線成功，已發出測試訊息 ${name}` : `✅ Connected, test message sent ${name}`)
      : (zh ? `✅ token 有效 ${name}｜${receipt.hint || ""}` : `✅ Token valid ${name} | ${receipt.hint || ""}`))
      + (receipt.saved ? (zh ? "｜已儲存並啟用" : " | saved and enabled") : "");
  } else {
    box.textContent = `❌ ${receipt.error_code || "failed"}：${receipt.hint || ""}`;
  }
}

async function lineTest(save) {
  const zh = currentLang === "zh-TW";
  const box = $("line-test-result");
  if (box) box.textContent = zh ? "測試中…" : "Testing…";
  const body = {
    access_token: ($("input-line-token").value || "").trim() || null,
    to: ($("input-line-to").value || "").trim() || null,
  };
  try {
    const receipt = await postJSON(save ? "/api/v1/line/connect" : "/api/v1/line/test", body);
    lineRenderResult(receipt);
    if (receipt.saved) {
      $("input-line-token").value = "";
      loadLineStatus();
      loadNotificationChannels();
    }
  } catch (e) {
    lineRenderResult({ ok: false, error_code: "request_failed", hint: String(e.message || e) });
  }
}

async function lineDisconnect() {
  const zh = currentLang === "zh-TW";
  if (!confirm(zh ? "停用 LINE 推播並清除本機儲存的 token 與收件 ID？" : "Disable LINE push and clear the stored token and recipient id?")) return;
  try {
    const receipt = await postJSON("/api/v1/line/disconnect", {});
    lineRenderResult({ ok: true, hint: receipt.hint });
    loadLineStatus();
    loadNotificationChannels();
  } catch (e) {
    lineRenderResult({ ok: false, error_code: "request_failed", hint: String(e.message || e) });
  }
}

async function loadNotificationChannels() {
  const box = $("tg-approvals-status");
  if (!box) return;
  try {
    await getJSON("/api/v1/notifications/channels");
  } catch (e) {}
}

async function tgArm() {
  const data = await schedRequest("/api/v1/telegram/approvals/arm", "POST");
  if (data) loadTelegramApprovalsStatus();
}

async function tgDisarm() {
  try {
    await postJSON("/api/v1/telegram/approvals/disarm");
    loadTelegramApprovalsStatus();
  } catch (e) { /* 狀態列會反映實況 */ }
}

async function tgDisconnect() {
  const zh = currentLang === "zh-TW";
  if (!confirm(zh ? "停用 Telegram 推播並清除本機保存的 token／chat id？" : "Disable Telegram push and clear the locally stored token / chat id?")) return;
  try {
    tgRenderResult(await postJSON("/api/v1/telegram/disconnect"));
    loadTelegramStatus();
  } catch (e) {
    tgRenderResult({ ok: false, error_code: "request_failed", hint: String(e.message || e) });
  }
}

// ------------------------------------------------------ local repository sync
function repoSyncLabels() {
  const zh = currentLang === "zh-TW";
  return zh ? {
    noRepo: "尚未在監控設定中加入 Git repository root。",
    cached: "ahead / behind 比較的是本機已保存的 remote-tracking refs；按 Fetch 才會更新遠端參照。",
    truncated: "清單已達安全上限，請縮小 repositories 設定範圍或調高 config 上限。",
    clean: "CLEAN",
    dirty: "WORKTREE CHANGED",
    untrackedOnly: "untracked only (does not block pull/push)",
    untrackedOnly: "只有 untracked（不影響 pull／push）",
    synced: "已同步",
    ahead: "待 Push",
    behind: "待 Pull",
    diverged: "已分歧",
    no_upstream: "未設定 upstream",
    detached_head: "Detached HEAD",
    upstream_unavailable: "Upstream 不可用",
    unavailable: "無法讀取",
    unknown: "未知狀態",
    fetch: "Fetch",
    pull: "Pull (FF only)",
    commit: "Commit staged",
    push: "Push",
    ovLoading: "正在讀取全部 repo 的本機 Git 狀態（不連網）…",
    ovEmpty: "沒有符合此篩選的 repo。",
    ovColumns: ["Repo", "branch → upstream", "狀態", "worktree", "上次 fetch", "動作"],
    ovFilters: { all: "全部", behind: "需 pull", ahead: "需 push", diverged: "分歧", dirty: "worktree 未提交", no_upstream: "無 upstream", synced: "已同步" },
    ovNeverFetched: "從未",
    confirmFetchAll: "對全部 repo 執行 fetch --prune？只更新遠端參照，不改任何 worktree、branch 或遠端。",
    fetchAllDone: (c) => `全部 Fetch 完成：成功 ${c.success}、失敗 ${c.failed}、跳過 ${c.skipped}`,
    batchNone: (a) => `目前沒有符合 ${a === "push" ? "Push" : "Pull (FF only)"} 前置條件的 repo。`,
    batchConfirm: (a, names, excluded, skipped = []) => `將對以下 ${names.length} 個 repo 執行 ${a === "push" ? "Push（不 force）" : "fast-forward Pull"}：\n\n${names.join("\n")}\n\n另有 ${excluded} 個 repo 因前置條件不符會被跳過${skipped.length ? `，例如：\n${skipped.join("\n")}` : "。"}\n\n執行時每個 repo 仍會重檢一次。繼續？`,
    batchDone: (a, c) => `批次 ${a === "push" ? "Push" : "Pull"} 完成：成功 ${c.success}、跳過 ${c.skipped}、失敗 ${c.failed}`,
    pushDisabled: "批次 Push 未啟用（config: repository_sync.batch.allow_push）；單一 repo 的 Push 仍可逐一執行。",
    staged: "staged",
    unstaged: "unstaged",
    untracked: "untracked",
    conflicts: "conflicts",
    confirmFetch: "只更新此 repository 的遠端參照（不改變 worktree）？",
    confirmPull: "只允許 fast-forward Pull。worktree 將前進到遠端既有 commit，繼續？",
    confirmPush: "推送此 repository 目前 branch 的既有 commits（不會 force push），繼續？",
    commitPrompt: "輸入 commit message（只會提交已 staged 的檔案）：",
    confirmCommit: "確認建立 staged-only commit？\n\n",
    working: "執行中…",
    success: "已完成",
    failed: "未執行：",
    actionHint: "每個動作都會在執行前重新檢查 branch、upstream、worktree 與分歧狀態。",
  } : {
    noRepo: "No Git repository root is configured for monitoring.",
    cached: "Ahead / behind uses locally cached remote-tracking refs. Fetch refreshes those refs.",
    truncated: "The safe list limit was reached. Narrow the configured roots or raise the config limit.",
    clean: "CLEAN",
    dirty: "WORKTREE CHANGED",
    synced: "Synced",
    ahead: "Push pending",
    behind: "Pull pending",
    diverged: "Diverged",
    no_upstream: "No upstream",
    detached_head: "Detached HEAD",
    upstream_unavailable: "Upstream unavailable",
    unavailable: "Unavailable",
    unknown: "Unknown",
    fetch: "Fetch",
    pull: "Pull (FF only)",
    commit: "Commit staged",
    push: "Push",
    ovLoading: "Reading local Git status for every repository (offline)…",
    ovEmpty: "No repository matches this filter.",
    ovColumns: ["Repo", "branch → upstream", "State", "worktree", "last fetch", "Actions"],
    ovFilters: { all: "All", behind: "needs pull", ahead: "needs push", diverged: "diverged", dirty: "dirty worktree", no_upstream: "no upstream", synced: "synced" },
    ovNeverFetched: "never",
    confirmFetchAll: "Run fetch --prune on every repository? Only remote-tracking refs change; no worktree, branch or remote is modified.",
    fetchAllDone: (c) => `Fetch all done: ${c.success} ok, ${c.failed} failed, ${c.skipped} skipped`,
    batchNone: (a) => `No repository currently meets the preconditions for ${a === "push" ? "Push" : "Pull (FF only)"}.`,
    batchConfirm: (a, names, excluded, skipped = []) => `Run ${a === "push" ? "Push (never force)" : "fast-forward Pull"} on these ${names.length} repositories:\n\n${names.join("\n")}\n\n${excluded} other repositories are excluded by preconditions${skipped.length ? `, for example:\n${skipped.join("\n")}` : "."}\n\nEach repository is rechecked before it runs. Continue?`,
    batchDone: (a, c) => `Batch ${a === "push" ? "Push" : "Pull"} done: ${c.success} ok, ${c.skipped} skipped, ${c.failed} failed`,
    pushDisabled: "Batch push is disabled (config: repository_sync.batch.allow_push); single-repo Push still works.",
    staged: "staged",
    unstaged: "unstaged",
    untracked: "untracked",
    conflicts: "conflicts",
    confirmFetch: "Refresh this repository's remote refs only (does not change the worktree)?",
    confirmPull: "Only fast-forward Pull is allowed. The worktree will advance to existing remote commits. Continue?",
    confirmPush: "Push the current branch's existing commits (never force push)?",
    commitPrompt: "Enter a commit message (only explicitly staged files will be committed):",
    confirmCommit: "Create a staged-only commit?\n\n",
    working: "Working…",
    success: "Completed",
    failed: "Not executed: ",
    actionHint: "Every action rechecks branch, upstream, worktree, and divergence before it runs.",
  };
}

function repoSyncStateText(repo, labels) {
  const state = repo.sync_state || "unknown";
  const base = labels[state] || labels.unknown;
  if (state === "ahead" && Number.isInteger(repo.ahead)) return `${base} ↑${repo.ahead}`;
  if (state === "behind" && Number.isInteger(repo.behind)) return `${base} ↓${repo.behind}`;
  if (state === "diverged") return `${base} ↑${repo.ahead ?? "?"} ↓${repo.behind ?? "?"}`;
  return base;
}

// 這個 repo 現在有事情要做、但按鈕是灰的——把後端給的具體理由直接顯示出來。
// （使用者回報：repo 明明落後遠端卻沒得按 pull，灰按鈕沒有任何可見說明。）
function repoBlockedReason(repo) {
  const actions = repo.actions || {};
  const state = repo.sync_state;
  const pull = actions.pull_ff_only || {};
  const push = actions.push || {};
  if (state === "behind" || state === "diverged") return pull.allowed ? "" : (pull.reason || "");
  if (state === "ahead") return push.allowed ? "" : (push.reason || "");
  if (state === "no_upstream" || state === "detached_head" || state === "upstream_unavailable") {
    return pull.reason || "";
  }
  return "";
}

function repoSyncActionButton(repo, action, label) {
  const actionState = (repo.actions || {})[action] || {};
  const allowed = actionState.allowed === true;
  const hint = allowed ? label : (actionState.reason || "Unavailable");
  return `<button class="btn btn-ghost btn-sm repo-sync-action ${allowed ? "" : "is-disabled"}"
      data-repo-id="${esc(repo.repo_id)}" data-repo-action="${esc(action)}"
      title="${esc(hint)}" ${allowed ? "" : "disabled"}>${esc(label)}</button>`;
}

function renderRepositorySyncStatus() {
  const list = $("repo-sync-list");
  const summary = $("repo-sync-summary");
  if (!list || !summary) return;
  const labels = repoSyncLabels();
  if (!repositorySyncCache.length) {
    summary.textContent = labels.noRepo;
    list.innerHTML = "";
    return;
  }

  const needsAttention = repositorySyncCache.filter(repo =>
    repo.sync_state !== "synced" || !repo.clean
  ).length;
  summary.innerHTML = `<strong>${repositorySyncCache.length}</strong> repositories · <strong>${needsAttention}</strong> ${currentLang === "zh-TW" ? "項需要處理" : "need attention"}<br><span>${esc(labels.cached)}</span>`;
  list.innerHTML = repositorySyncCache.map(repo => {
    const worktree = repo.worktree || {};
    const counts = [
      worktree.staged_files ? `${worktree.staged_files} ${labels.staged}` : "",
      worktree.unstaged_files ? `${worktree.unstaged_files} ${labels.unstaged}` : "",
      worktree.untracked_files ? `${worktree.untracked_files} ${labels.untracked}` : "",
      worktree.conflicted_files ? `${worktree.conflicted_files} ${labels.conflicts}` : "",
    ].filter(Boolean);
    const stateClass = `state-${String(repo.sync_state || "unknown").replace(/[^a-z_]/g, "")}`;
    const branch = repo.branch || "—";
    const upstream = repo.upstream || "—";
    const statusHint = repo.error || (repo.operation_in_progress ? `Git: ${repo.operation_in_progress}` : "");
    return `<article class="repo-sync-row ${stateClass}">
      <div class="repo-sync-main">
        <div class="repo-sync-name">${esc(repo.name)} <span class="repo-sync-state">${esc(repoSyncStateText(repo, labels))}</span></div>
        <div class="repo-sync-meta"><code>${esc(branch)}</code> → <code>${esc(upstream)}</code></div>
        <div class="repo-sync-path" title="${esc(repo.path || "")}">${esc(repo.path || "")}</div>
        <div class="repo-sync-worktree ${repo.tracked_clean === false ? "is-dirty" : "is-clean"}">${repo.clean ? labels.clean : `${repo.tracked_clean === false ? labels.dirty : labels.untrackedOnly}${counts.length ? ` · ${esc(counts.join(" · "))}` : ""}`}</div>
        ${statusHint ? `<div class="repo-sync-warning">${esc(statusHint)}</div>` : ""}
        ${repoBlockedReason(repo) ? `<div class="repo-sync-blocked">⛔ ${esc(repoBlockedReason(repo))}</div>` : ""}
      </div>
      <div class="repo-sync-actions">
        ${repoSyncActionButton(repo, "fetch", labels.fetch)}
        ${repoSyncActionButton(repo, "pull_ff_only", labels.pull)}
        ${repoSyncActionButton(repo, "commit_staged", labels.commit)}
        ${repoSyncActionButton(repo, "push", labels.push)}
      </div>
    </article>`;
  }).join("");
}

async function loadRepositorySyncStatus() {
  const summary = $("repo-sync-summary");
  try {
    const data = await getJSON("/api/v1/repos/sync-status");
    repositorySyncCache = Array.isArray(data.repositories) ? data.repositories : [];
    renderRepositorySyncStatus();
    if (data.truncated && summary) {
      summary.insertAdjacentHTML("beforeend", `<br><span class="repo-sync-warning">${esc(repoSyncLabels().truncated)}</span>`);
    }
  } catch (e) {
    if (summary) summary.textContent = currentLang === "zh-TW" ? "無法讀取本機 Git 狀態。" : "Unable to read local Git status.";
  }
}

async function runRepositorySyncAction(repoId, action) {
  const labels = repoSyncLabels();
  const result = $("repo-sync-result");
  let commitMessage = null;
  let confirmation = labels.confirmFetch;
  if (action === "pull_ff_only") confirmation = labels.confirmPull;
  if (action === "push") confirmation = labels.confirmPush;
  if (action === "commit_staged") {
    commitMessage = window.prompt(labels.commitPrompt, "");
    if (commitMessage === null || !commitMessage.trim()) return;
    confirmation = labels.confirmCommit + commitMessage.trim();
  }
  if (!window.confirm(confirmation)) return;

  if (result) result.textContent = labels.working;
  try {
    const receipt = await postJSON("/api/v1/repos/sync-action", {
      repo_id: repoId,
      action,
      confirmation: "confirmed",
      commit_message: commitMessage,
    });
    if (result) result.textContent = `${labels.success} · ${receipt.repo_name} · ${action}`;
    await loadRepositorySyncStatus();
    loadProjects(true);
  } catch (e) {
    if (result) result.textContent = `${labels.failed}${e.message}`;
    await loadRepositorySyncStatus();
  }
}

// ------------------------------------------------ ADR-011 Addendum B: overview + batch
let repoOverviewCache = null;   // null = 尚未載入；[] = 已載入但沒有 repo
let repoOverviewBatch = { fetch_all: true, pull_ff_only: true, push: false };
let repoOverviewFilter = "all";

function repoOverviewMatches(repo, filter) {
  switch (filter) {
    case "behind": return repo.sync_state === "behind";
    case "ahead": return repo.sync_state === "ahead";
    case "diverged": return repo.sync_state === "diverged";
    case "dirty": return repo.clean === false;
    case "no_upstream": return ["no_upstream", "detached_head", "upstream_unavailable"].includes(repo.sync_state);
    case "synced": return repo.sync_state === "synced";
    default: return true;
  }
}

function formatFetchTime(value, labels) {
  if (!value) return labels.ovNeverFetched;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value).slice(0, 16);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function renderRepoOverview() {
  const table = $("repo-overview-table");
  const filters = $("repo-overview-filters");
  if (!table || !filters || repoOverviewCache === null) return;
  const labels = repoSyncLabels();
  const pushBtn = $("btn-repo-batch-push");
  if (pushBtn) {
    pushBtn.disabled = !repoOverviewBatch.push;
    pushBtn.title = repoOverviewBatch.push ? "" : labels.pushDisabled;
  }
  filters.hidden = false;
  filters.innerHTML = Object.entries(labels.ovFilters).map(([key, text]) => {
    const count = repoOverviewCache.filter(r => repoOverviewMatches(r, key)).length;
    return `<button type="button" class="repo-overview-chip ${key === repoOverviewFilter ? "is-active" : ""}" data-filter="${key}">${esc(text)} ${count}</button>`;
  }).join("");
  const rows = repoOverviewCache.filter(r => repoOverviewMatches(r, repoOverviewFilter));
  if (!rows.length) {
    table.innerHTML = `<div class="placeholder" style="padding:10px;">${esc(labels.ovEmpty)}</div>`;
    return;
  }
  const worktreeText = (repo) => {
    const w = repo.worktree || {};
    const parts = [
      w.staged_files ? `${w.staged_files} ${labels.staged}` : "",
      w.unstaged_files ? `${w.unstaged_files} ${labels.unstaged}` : "",
      w.untracked_files ? `${w.untracked_files} ${labels.untracked}` : "",
      w.conflicted_files ? `${w.conflicted_files} ${labels.conflicts}` : "",
    ].filter(Boolean);
    if (repo.clean) return `<span class="is-clean">${esc(labels.clean)}</span>`;
    const cls = repo.tracked_clean === false ? "is-dirty" : "is-clean";
    return `<span class="${cls}">${esc(parts.join(" · ") || labels.dirty)}</span>`;
  };
  table.innerHTML = `<table><thead><tr>${labels.ovColumns.map(c => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${rows.map(repo => {
    const stateClass = `state-${String(repo.sync_state || "unknown").replace(/[^a-z_]/g, "")}`;
    return `<tr class="${stateClass}">
      <td class="repo-overview-name" title="${esc(repo.path || "")}">${esc(repo.name)}</td>
      <td><code>${esc(repo.branch || "—")}</code> → <code>${esc(repo.upstream || "—")}</code></td>
      <td>${esc(repoSyncStateText(repo, labels))}${repo.error ? `<div class="repo-sync-warning">${esc(repo.error)}</div>` : ""}${repoBlockedReason(repo) ? `<div class="repo-sync-blocked">⛔ ${esc(repoBlockedReason(repo))}</div>` : ""}</td>
      <td>${worktreeText(repo)}</td>
      <td>${esc(formatFetchTime(repo.last_fetch_at, labels))}</td>
      <td class="repo-overview-actions">${repoSyncActionButton(repo, "fetch", labels.fetch)} ${repoSyncActionButton(repo, "pull_ff_only", labels.pull)} ${repoSyncActionButton(repo, "push", labels.push)}</td>
    </tr>`;
  }).join("")}</tbody></table>`;
}

async function loadRepoOverview() {
  const table = $("repo-overview-table");
  const result = $("repo-overview-result");
  const labels = repoSyncLabels();
  if (table) table.innerHTML = `<div class="placeholder" style="padding:10px;">${esc(labels.ovLoading)}</div>`;
  try {
    const data = await getJSON("/api/v1/repos/sync-status?scope=all");
    repoOverviewCache = Array.isArray(data.repositories) ? data.repositories : [];
    repoOverviewBatch = data.batch || repoOverviewBatch;
    renderRepoOverview();
    if (result) {
      const s = data.summary || {};
      result.textContent = `${data.displayed_count}/${data.repository_count} repositories · ${labels.ovFilters.behind} ${s.behind || 0} · ${labels.ovFilters.ahead} ${s.ahead || 0} · ${labels.ovFilters.diverged} ${s.diverged || 0} · ${labels.ovFilters.dirty} ${s.dirty || 0}${data.truncated ? ` · ${labels.truncated}` : ""}`;
    }
  } catch (e) {
    if (table) table.innerHTML = `<div class="placeholder" style="padding:10px;">${esc(labels.failed)}${esc(e.message)}</div>`;
  }
}

async function runRepoFetchAll() {
  const labels = repoSyncLabels();
  const result = $("repo-overview-result");
  if (!window.confirm(labels.confirmFetchAll)) return;
  if (result) result.textContent = labels.working;
  try {
    const receipt = await postJSON("/api/v1/repos/sync-fetch-all", { confirmation: "confirmed" });
    const failed = (receipt.results || []).filter(r => r.status !== "success");
    if (result) result.textContent = labels.fetchAllDone(receipt.counts || {}) + (failed.length ? ` · ${failed.map(r => `${r.repo_name}: ${r.reason || r.status}`).slice(0, 5).join(" · ")}` : "");
    await loadRepoOverview();
    loadRepositorySyncStatus();
  } catch (e) {
    if (result) result.textContent = `${labels.failed}${e.message}`;
  }
}

async function runRepoBatch(action) {
  const labels = repoSyncLabels();
  const result = $("repo-overview-result");
  if (result) result.textContent = labels.working;
  try {
    const plan = await getJSON(`/api/v1/repos/sync-batch-plan?action=${encodeURIComponent(action)}`);
    const eligible = plan.eligible || [];
    if (!eligible.length) {
      const why = (plan.excluded || [])
        .filter(r => r.sync_state !== "synced")
        .slice(0, 3)
        .map(r => `${r.name}: ${r.reason || "—"}`);
      if (result) result.textContent = labels.batchNone(action) + (why.length ? ` · ${why.join(" · ")}` : "");
      return;
    }
    const names = eligible.map(r => `• ${r.name} (${r.branch || "—"}${action === "push" ? ` ↑${r.ahead ?? "?"}` : ` ↓${r.behind ?? "?"}`})`);
    const shown = names.length > 20 ? [...names.slice(0, 20), `… +${names.length - 20}`] : names;
    // 被排除的 repo 也要說明為什麼，否則「我的專案怎麼不在清單裡」無從查起。
    const skipped = (plan.excluded || [])
      .filter(r => r.sync_state !== "synced")
      .slice(0, 8)
      .map(r => `• ${r.name}: ${r.reason || "—"}`);
    if (!window.confirm(labels.batchConfirm(action, shown, plan.excluded_count || 0, skipped))) {
      if (result) result.textContent = "";
      return;
    }
    const receipt = await postJSON("/api/v1/repos/sync-batch", {
      action, confirmation: "confirmed", repo_ids: eligible.map(r => r.repo_id),
    });
    const problems = (receipt.results || []).filter(r => r.status !== "success");
    if (result) result.textContent = labels.batchDone(action, receipt.counts || {}) + (problems.length ? ` · ${problems.map(r => `${r.repo_name || r.repo_id}: ${r.reason || r.status}`).slice(0, 5).join(" · ")}` : "");
    await loadRepoOverview();
    loadRepositorySyncStatus();
    loadProjects(true);
  } catch (e) {
    if (result) result.textContent = `${labels.failed}${e.message}`;
  }
}

function initRepositorySyncSection() {
  const refresh = $("btn-repo-sync-refresh");
  if (refresh) refresh.addEventListener("click", () => loadRepositorySyncStatus());
  const list = $("repo-sync-list");
  if (list) list.addEventListener("click", (event) => {
    const button = event.target.closest(".repo-sync-action");
    if (!button || button.disabled) return;
    runRepositorySyncAction(button.dataset.repoId, button.dataset.repoAction);
  });
  const overview = $("repo-overview-table");
  if (overview) overview.addEventListener("click", async (event) => {
    const button = event.target.closest(".repo-sync-action");
    if (!button || button.disabled) return;
    await runRepositorySyncAction(button.dataset.repoId, button.dataset.repoAction);
    loadRepoOverview();
  });
  const filters = $("repo-overview-filters");
  if (filters) filters.addEventListener("click", (event) => {
    const chip = event.target.closest(".repo-overview-chip");
    if (!chip) return;
    repoOverviewFilter = chip.dataset.filter || "all";
    renderRepoOverview();
  });
  const load = $("btn-repo-overview-load");
  if (load) load.addEventListener("click", loadRepoOverview);
  const fetchAll = $("btn-repo-fetch-all");
  if (fetchAll) fetchAll.addEventListener("click", runRepoFetchAll);
  const batchPull = $("btn-repo-batch-pull");
  if (batchPull) batchPull.addEventListener("click", () => runRepoBatch("pull_ff_only"));
  const batchPush = $("btn-repo-batch-push");
  if (batchPush) batchPush.addEventListener("click", () => runRepoBatch("push"));
  const scan = $("btn-onboarding-scan");
  if (scan) scan.addEventListener("click", loadOnboardingReport);
}

// ------------------------------------------------ P4.3 repo onboarding
let onboardingReportCache = null;

async function loadOnboardingReport() {
  const zh = currentLang === "zh-TW";
  const box = $("onboarding-report");
  if (!box) return;
  box.innerHTML = `<span class="muted small">${zh ? "掃描中…" : "Scanning…"}</span>`;
  try {
    onboardingReportCache = await getJSON("/api/v1/repos/onboarding-report");
    renderOnboardingReport();
  } catch (e) {
    box.innerHTML = `<span class="muted small">${esc(String(e.message || e))}</span>`;
  }
}

function renderOnboardingReport() {
  const zh = currentLang === "zh-TW";
  const box = $("onboarding-report");
  const report = onboardingReportCache;
  if (!box || !report) return;
  const roots = report.roots || [];
  const rootOptions = roots.map(r => `<option value="${esc(r.root_id)}">${esc(r.path)}</option>`).join("");
  const ghOptions = (report.github_not_cloned || [])
    .map(g => `<option value="${esc(g.full_name)}">${esc(g.full_name)}</option>`).join("");
  const sections = [];

  const folders = report.plain_folders || [];
  sections.push(`<div class="mono-mini muted mb-6">${zh ? "① 尚未 git init 的資料夾" : "① Folders without git init"}（${folders.length}${report.plain_folders_truncated ? "+" : ""}）</div>`);
  sections.push(folders.length ? folders.map(f =>
    `<div class="tag" style="justify-content: space-between; width: 100%; margin-bottom: 4px;">
      <span>📁 ${esc(f.path)}</span>
      <button class="btn btn-ghost btn-sm" onclick="onboardingInit('${esc(f.folder_id)}', '${esc(f.name)}')">${zh ? "git init" : "git init"}</button>
    </div>`).join("") : `<div class="muted small mb-6">${zh ? "（沒有）" : "(none)"}</div>`);

  const noRemote = report.repos_without_remote || [];
  sections.push(`<div class="mono-mini muted mb-6" style="margin-top:8px;">${zh ? "② 沒有 remote 的本機 repo" : "② Local repos without a remote"}（${noRemote.length}）</div>`);
  sections.push(noRemote.length ? noRemote.map(r =>
    `<div class="tag" style="justify-content: space-between; width: 100%; margin-bottom: 4px; flex-wrap: wrap; gap: 4px;">
      <span>📦 ${esc(r.path)}</span>
      <span>
        <select id="ob-attach-${esc(r.repo_id)}" class="mono" style="max-width: 220px;">${ghOptions}</select>
        <button class="btn btn-ghost btn-sm" onclick="onboardingAttach('${esc(r.repo_id)}', '${esc(r.name)}')">${zh ? "連結為 origin" : "Attach as origin"}</button>
        <button class="btn btn-ghost btn-sm" onclick="onboardingCreate('${esc(r.repo_id)}', '${esc(r.name)}')">${zh ? "建立 GitHub repo(private)" : "Create GitHub repo (private)"}</button>
      </span>
    </div>`).join("") : `<div class="muted small mb-6">${zh ? "（沒有）" : "(none)"}</div>`);

  const notCloned = report.github_not_cloned || [];
  sections.push(`<div class="mono-mini muted mb-6" style="margin-top:8px;">${zh ? "③ 尚未 clone 的 GitHub repo（以 remote URL 比對）" : "③ GitHub repos not cloned (matched by remote URL)"}（${notCloned.length}）</div>`);
  sections.push(notCloned.length ? notCloned.map(g => {
    const hint = g.name_match_hint
      ? ` <span class="muted small">${zh ? "⚠ 本機有同名目錄（不自動配對）：" : "⚠ same-name local dir (not auto-paired): "}${esc(g.name_match_hint)}</span>`
      : "";
    return `<div class="tag" style="justify-content: space-between; width: 100%; margin-bottom: 4px; flex-wrap: wrap; gap: 4px;">
      <span>☁️ ${esc(g.full_name)}${g.private ? " 🔒" : ""}${hint}</span>
      <span>
        <select id="ob-clone-${esc(g.full_name)}" class="mono" style="max-width: 220px;">${rootOptions}</select>
        <button class="btn btn-ghost btn-sm" onclick="onboardingClone('${esc(g.full_name)}')">${zh ? "clone 到選定 root" : "Clone into root"}</button>
      </span>
    </div>`;
  }).join("") : `<div class="muted small mb-6">${zh ? "（沒有）" : "(none)"}</div>`);

  box.innerHTML = sections.join("");
}

async function onboardingAction(payload, confirmText) {
  const zh = currentLang === "zh-TW";
  if (!confirm(confirmText)) return;
  const result = $("onboarding-result");
  result.textContent = zh ? "執行中…" : "Running…";
  try {
    const receipt = await postJSON("/api/v1/repos/onboarding-action", { ...payload, confirmation: "confirmed" });
    result.textContent = `✅ ${receipt.action}: ${receipt.status}${receipt.note ? " — " + receipt.note : ""}`;
    await loadOnboardingReport();
    await loadRepositorySyncStatus();
  } catch (e) {
    result.textContent = `❌ ${String(e.message || e)}`;
  }
}

window.onboardingInit = function (folderId, name) {
  const zh = currentLang === "zh-TW";
  onboardingAction(
    { action: "init_folder", folder_id: folderId },
    zh ? `對「${name}」執行 git init？只建立空的 .git，不 commit、不設 remote、不發布。` : `Run git init on "${name}"? Creates an empty .git only.`
  );
};

window.onboardingAttach = function (repoId, name) {
  const zh = currentLang === "zh-TW";
  const select = $(`ob-attach-${repoId}`);
  const fullName = select ? select.value : "";
  if (!fullName) { alert(zh ? "沒有可選的 GitHub repo；請先同步 GitHub 整合。" : "No GitHub repos to pick; sync the GitHub integration first."); return; }
  onboardingAction(
    { action: "attach_remote", repo_id: repoId, github_full_name: fullName },
    zh ? `把 ${fullName} 設為「${name}」的 origin？不會 fetch、不會 push。` : `Set ${fullName} as origin of "${name}"? No fetch, no push.`
  );
};

window.onboardingCreate = function (repoId, name) {
  const zh = currentLang === "zh-TW";
  onboardingAction(
    { action: "create_remote", repo_id: repoId, name: name, private: true },
    zh ? `在 GitHub 建立 private repo「${name}」並設為 origin？遠端為空 repo，本機不會推送任何內容；首次發布由您自行 git push -u。` : `Create private GitHub repo "${name}" and set it as origin? Nothing is pushed; you do the first push yourself.`
  );
};

window.onboardingClone = function (fullName) {
  const zh = currentLang === "zh-TW";
  const select = document.getElementById(`ob-clone-${fullName}`);
  const rootId = select ? select.value : "";
  if (!rootId) { alert(zh ? "請先在監控設定加入 Git root。" : "Add a Git root in settings first."); return; }
  const rootPath = select ? select.options[select.selectedIndex].textContent : "";
  onboardingAction(
    { action: "clone_repo", github_full_name: fullName, root_id: rootId },
    zh ? `把 ${fullName} clone 到 ${rootPath} 之下？目的地已存在時會拒絕，不覆寫任何目錄。` : `Clone ${fullName} into ${rootPath}? Refused if the destination already exists.`
  );
};

// ---------------------------------------------------------------- github
function initGitHubSection() {
  $("github-pill").addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".pane").forEach(p => p.classList.remove("active"));
    const tabBtn = document.querySelector('.tab[data-tab="tab-settings"]');
    if (tabBtn) tabBtn.classList.add("active");
    $("tab-settings").classList.add("active");
    loadConfig();
    const githubPanel = $("panel-github");
    if (githubPanel) githubPanel.open = true;  // 從徽章跳轉時自動展開收合的 GitHub 卡片
    const el = $("btn-gh-auto-connect");
    if (el) window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - 160, behavior: "smooth" });
  });

  $("btn-gh-auto-connect").addEventListener("click", async () => {
    const btn = $("btn-gh-auto-connect");
    const label = btn.textContent;
    btn.disabled = true; btn.textContent = "偵測認證中…";
    try {
      const res = await postJSON("/api/v1/github/connect", { method: "gh_cli" });
      $("gh-sync-result").textContent = `✓ 已連線 @${res.auth.username}，已同步 ${res.sync.synced_repos_count} 個專案與 ${res.sync.synced_prs_count} 筆 PR！`;
      loadGitHubStatus();
      loadProjects(true);
    } catch (e) {
      $("gh-sync-result").textContent = `連線失敗：${e.message}`;
    } finally {
      setTimeout(() => { btn.disabled = false; btn.textContent = label; }, 1500);
    }
  });

  $("btn-gh-token-connect").addEventListener("click", async () => {
    const token = $("input-gh-token").value.trim();
    if (!token) return;
    const btn = $("btn-gh-token-connect");
    const label = btn.textContent;
    btn.disabled = true; btn.textContent = "驗證中…";
    try {
      const res = await postJSON("/api/v1/github/connect", { method: "token", token });
      $("gh-sync-result").textContent = `✓ 已連線 @${res.auth.username}，已同步 ${res.sync.synced_repos_count} 個專案與 ${res.sync.synced_prs_count} 筆 PR！`;
      $("input-gh-token").value = "";
      loadGitHubStatus();
      loadProjects(true);
    } catch (e) {
      $("gh-sync-result").textContent = `Token 驗證失敗：${e.message}`;
    } finally {
      setTimeout(() => { btn.disabled = false; btn.textContent = label; }, 1500);
    }
  });

  $("btn-gh-sync").addEventListener("click", async () => {
    const btn = $("btn-gh-sync");
    const label = btn.textContent;
    btn.disabled = true; btn.textContent = "⏳ 同步中…";
    try {
      const res = await postJSON("/api/v1/github/sync");
      $("gh-sync-result").textContent = `✓ 同步完成！已更新 ${res.synced_repos_count} 個倉庫與 ${res.synced_prs_count} 筆 PR 狀態。`;
      loadProjects(true);
    } catch (e) {
      $("gh-sync-result").textContent = `同步失敗：${e.message}`;
    } finally {
      setTimeout(() => { btn.disabled = false; btn.textContent = label; }, 1500);
    }
  });

  $("btn-gh-disconnect").addEventListener("click", async () => {
    try {
      await postJSON("/api/v1/github/disconnect");
      $("gh-sync-result").textContent = "已解除 GitHub 連線。";
      loadGitHubStatus();
      loadProjects(true);
    } catch (e) { console.error(e); }
  });
}

async function loadGitHubStatus() {
  try {
    const data = await getJSON("/api/v1/github/status");
    githubStatus = data;
    const pill = $("github-pill");
    const pillText = $("github-status-text");
    const badge = $("gh-auth-badge");
    const info = $("gh-account-info");

    if (data.connected) {
      pill.className = "pill pill-on";
      pillText.textContent = `🐙 @${data.username} (${data.public_repos + data.total_private_repos} Repos)`;
      badge.className = "trust ok";
      badge.textContent = "CONNECTED";

      const scopesStr = (data.scopes || []).join(", ") || "基本讀取";
      const limitStr = data.rate_limit ? `API 額度: ${data.rate_limit.remaining}/${data.rate_limit.limit}` : "";

      info.innerHTML = `
        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px;">
          <img src="${esc(data.avatar_url)}" style="width: 28px; height: 28px; border-radius: 50%; border: 1px solid var(--bd);">
          <div>
            <strong><a href="${esc(data.html_url)}" target="_blank" style="color: var(--orange); text-decoration: none;">@${esc(data.username)}</a></strong>
            <span style="color: var(--mu); font-size: 11.5px;">(${esc(data.name || "")}) · 擁有 ${data.public_repos} 公開 / ${data.total_private_repos} 私有倉庫</span>
          </div>
        </div>
        <div class="mono-mini muted" style="font-size: 10.5px;">
          權限範圍: <code>${esc(scopesStr)}</code> · ${esc(limitStr)}
        </div>`;
    } else {
      pill.className = "pill pill-off";
      pillText.textContent = "🐙 GitHub: 未連線";
      badge.className = "trust broken";
      badge.textContent = "未連線";
      info.textContent = data.message || "尚未啟用 GitHub 認證。連線後可自動讀取所有 Public / Private 倉庫、PR 進度與 CI 狀態。";
    }
  } catch (e) {
    $("github-status-text").textContent = "🐙 GitHub: 離線";
  }
}


// ---------------------------------------------------------------- summaries
function initSummariesTab() {
  const todayStr = iso(new Date());
  if ($("input-summary-start-date")) $("input-summary-start-date").value = todayStr;
  if ($("input-summary-end-date")) $("input-summary-end-date").value = todayStr;

  document.querySelectorAll(".quick-ranges .chip").forEach(chip => {
    chip.addEventListener("click", () => {
      document.querySelectorAll(".quick-ranges .chip").forEach(c => c.classList.remove("active"));
      chip.classList.add("active");
      const r = chip.dataset.range;
      const now = new Date();
      if (r === "today") {
        $("input-summary-start-date").value = iso(now);
        $("input-summary-end-date").value = iso(now);
      } else if (r === "yesterday") {
        const y = new Date(now); y.setDate(y.getDate() - 1);
        $("input-summary-start-date").value = iso(y);
        $("input-summary-end-date").value = iso(y);
      } else if (r === "this_week") {
        const mon = new Date(now); mon.setDate(now.getDate() - ((now.getDay() + 6) % 7));
        $("input-summary-start-date").value = iso(mon);
        $("input-summary-end-date").value = iso(now);
      } else if (r === "7d") {
        const past = new Date(now); past.setDate(now.getDate() - 6);
        $("input-summary-start-date").value = iso(past);
        $("input-summary-end-date").value = iso(now);
      } else if (r === "30d") {
        const past = new Date(now); past.setDate(now.getDate() - 29);
        $("input-summary-start-date").value = iso(past);
        $("input-summary-end-date").value = iso(now);
      }
    });
  });

  $("btn-generate-custom-summary").addEventListener("click", () => {
    const start = $("input-summary-start-date") ? $("input-summary-start-date").value : null;
    const end = $("input-summary-end-date") ? $("input-summary-end-date").value : null;
    generateSummary(start, end);
  });

  $("btn-copy-markdown").addEventListener("click", () => {
    if (currentSummaryMarkdown) navigator.clipboard.writeText(currentSummaryMarkdown);
  });

  document.querySelectorAll(".viewswitch .chip").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".viewswitch .chip").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      summaryView = btn.dataset.view;
      paintSummaryView();
    });
  });
}

async function loadSummaries() {
  try {
    summariesCache = await getJSON("/api/v1/summaries?limit=60");
    const box = $("summary-history-list");
    if (!summariesCache.length) {
      box.innerHTML = '<div class="placeholder">尚未產生歷史摘要。</div>';
    } else {
      box.innerHTML = summariesCache.map((s, i) => `
        <div class="sideitem ${i === 0 ? "active" : ""}" data-date="${esc(s.date_str)}">
          <div class="sideitem-title">${esc(s.date_str)}</div>
          <div class="sideitem-sub">${esc((s.llm_provider || "").toUpperCase())} · ${esc((s.created_at || "").split(" ")[1] || "")}</div>
        </div>`).join("");
      box.querySelectorAll(".sideitem").forEach(el => {
        el.addEventListener("click", () => selectSummary(el.dataset.date));
      });
      showSummary(summariesCache[0]);
    }
    paintSummaryView();
  } catch (e) {
    $("summary-history-list").innerHTML = '<div class="placeholder">無法讀取歷史報告。</div>';
  }
}

async function selectSummary(dateStr) {
  document.querySelectorAll("#summary-history-list .sideitem").forEach(el => {
    el.classList.toggle("active", el.dataset.date === dateStr);
  });
  try {
    const data = await getJSON(`/api/v1/summaries/${encodeURIComponent(dateStr)}`);
    summaryView = "day";
    document.querySelectorAll(".viewswitch .chip").forEach(b => b.classList.toggle("active", b.dataset.view === "day"));
    showSummary(data);
    paintSummaryView();
  } catch (e) { console.error(e); }
}

function showSummary(s) {
  currentSummaryMarkdown = s.raw_markdown || s.markdown || "";
  $("summary-meta").textContent = `${s.date_str} · ${(s.llm_provider || "").toUpperCase()}${s.model_name ? " / " + s.model_name : ""}`;
  const view = $("summary-view-day");
  view.innerHTML = window.marked
    ? marked.parse(currentSummaryMarkdown)
    : `<pre>${esc(currentSummaryMarkdown)}</pre>`;
}

async function generateSummary(startDate, endDate) {
  const btn = $("btn-generate-custom-summary") || $("btn-quick-summary");
  const topBtn = $("btn-quick-summary");
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = "⏳ AI 分析中…";
  if (topBtn) { topBtn.disabled = true; topBtn.textContent = "⏳ 分析中…"; }
  try {
    const payload = { force_refresh: true };
    if (startDate && endDate) {
      payload.start_date = startDate;
      payload.end_date = endDate;
    } else if (startDate) {
      payload.target_date = startDate;
    } else {
      payload.target_date = iso(new Date());
    }

    const data = await postJSON("/api/v1/summaries/generate", payload);
    showSummary({ date_str: data.date_str, raw_markdown: data.markdown || data.raw_markdown, llm_provider: data.llm_provider || "", model_name: data.model_name || "" });
    loadSummaries();
    loadOpenLoops();
  } catch (e) {
    btn.textContent = "生成失敗";
  } finally {
    setTimeout(() => {
      btn.disabled = false; btn.textContent = label;
      if (topBtn) { topBtn.disabled = false; topBtn.textContent = "⚡ 生成今日摘要"; }
    }, 1400);
  }
}


// 週／月檢視：以「哪幾天有 AI 回顧報告」為軸，資料全部來自 /api/v1/summaries
function paintSummaryView() {
  const day = $("summary-view-day"), week = $("summary-view-week"), month = $("summary-view-month");
  day.hidden = summaryView !== "day";
  week.hidden = summaryView !== "week";
  month.hidden = summaryView !== "month";
  if (summaryView === "week") renderWeekView();
  if (summaryView === "month") renderMonthView();
}

const dayNames = ["日", "一", "二", "三", "四", "五", "六"];
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

function renderWeekView() {
  const today = new Date();
  const monday = new Date(today);
  monday.setDate(today.getDate() - ((today.getDay() + 6) % 7));
  const have = new Set(summariesCache.map(s => s.date_str));

  let cells = "", covered = 0;
  for (let i = 0; i < 7; i++) {
    const d = new Date(monday); d.setDate(monday.getDate() + i);
    const key = iso(d);
    const has = have.has(key);
    if (has) covered++;
    cells += `
      <div class="weekcell ${has ? "has" : ""} ${key === iso(today) ? "today" : ""}" ${has ? `data-date="${key}"` : ""}>
        <div class="weekcell-day">${dayNames[d.getDay()]}</div>
        <div class="weekcell-date">${String(d.getDate()).padStart(2, "0")}</div>
        <div class="weekcell-state ${has ? "has" : "none"}">${has ? "有報告" : "無"}</div>
      </div>`;
  }

  const notes = summariesCache
    .filter(s => s.date_str >= iso(monday))
    .slice(0, 7)
    .map(s => `<div class="pl"><b>·</b><span><strong>${esc(s.date_str)}</strong> — ${esc(firstLine(s.raw_markdown))}</span></div>`)
    .join("") || '<div class="placeholder" style="padding:0">本週尚無報告。</div>';

  $("summary-view-week").innerHTML = `
    <h1>${iso(monday).slice(5)} – ${iso(new Date(monday.getTime() + 6 * 864e5)).slice(5)} 週檢視</h1>
    <div class="rule"></div>
    <div class="weekgrid">${cells}</div>
    <div class="rangestats">
      <div class="rangestat"><div class="mono-mini muted">REPORTS</div><div class="rangestat-value">${covered} / 7</div><div class="rangestat-sub">本週已產出</div></div>
      <div class="rangestat"><div class="mono-mini muted">OPEN LOOPS</div><div class="rangestat-value">${loopsCache.length}</div><div class="rangestat-sub">目前未結</div></div>
      <div class="rangestat"><div class="mono-mini muted">STREAMS</div><div class="rangestat-value">${projectsCache.length}</div><div class="rangestat-sub">進行中工作</div></div>
      <div class="rangestat"><div class="mono-mini muted">ACTIVE</div><div class="rangestat-value">${projectsCache.filter(p => p.status === "active").length}</div><div class="rangestat-sub">兩天內有活動</div></div>
    </div>
    <span class="mono-label">本週報告 / REPORTS</span>
    ${notes}`;

  $("summary-view-week").querySelectorAll("[data-date]").forEach(el => {
    el.addEventListener("click", () => selectSummary(el.dataset.date));
  });
}

function renderMonthView() {
  const today = new Date();
  const first = new Date(today.getFullYear(), today.getMonth(), 1);
  const days = new Date(today.getFullYear(), today.getMonth() + 1, 0).getDate();
  const lead = (first.getDay() + 6) % 7;
  const have = new Set(summariesCache.map(s => s.date_str));

  let cells = "";
  for (let i = 0; i < lead; i++) cells += '<div class="mcell blank"></div>';
  let covered = 0;
  for (let d = 1; d <= days; d++) {
    const key = iso(new Date(today.getFullYear(), today.getMonth(), d));
    const has = have.has(key);
    if (has) covered++;
    cells += `<div class="mcell ${has ? "has" : ""}" ${has ? `data-date="${key}"` : ""}>${String(d).padStart(2, "0")}${has ? '<span class="mcell-mark"></span>' : ""}</div>`;
  }

  const heads = ["一", "二", "三", "四", "五", "六", "日"]
    .map(h => `<div class="mono-mini muted" style="text-align:center">${h}</div>`).join("");

  const streams = projectsCache.length
    ? projectsCache.map(p => `
        <div class="defect" style="border-bottom:1px solid var(--bd)">
          <span>${esc(p.display_name)}</span>
          <span class="mono-mini muted">${esc(p.last_activity_at)} · 未結 ${p.open_loops_count}</span>
        </div>`).join("")
    : '<div class="placeholder">尚無進行中工作。</div>';

  $("summary-view-month").innerHTML = `
    <h1>${today.getFullYear()} 年 ${today.getMonth() + 1} 月 月檢視</h1>
    <div class="rule"></div>
    <div class="monthgrid" style="margin-bottom:6px">${heads}</div>
    <div class="monthgrid">${cells}</div>
    <div class="rangestats">
      <div class="rangestat"><div class="mono-mini muted">REPORTS</div><div class="rangestat-value">${covered} / ${days}</div><div class="rangestat-sub">本月已產出</div></div>
      <div class="rangestat"><div class="mono-mini muted">GAPS</div><div class="rangestat-value">${days - covered}</div><div class="rangestat-sub">未產出天數</div></div>
      <div class="rangestat"><div class="mono-mini muted">OPEN LOOPS</div><div class="rangestat-value">${loopsCache.length}</div><div class="rangestat-sub">目前未結</div></div>
      <div class="rangestat"><div class="mono-mini muted">STREAMS</div><div class="rangestat-value">${projectsCache.length}</div><div class="rangestat-sub">進行中工作</div></div>
    </div>
    <span class="mono-label">工作重心 / STREAMS</span>
    <div class="panel">${streams}</div>`;

  $("summary-view-month").querySelectorAll("[data-date]").forEach(el => {
    el.addEventListener("click", () => selectSummary(el.dataset.date));
  });
}

function firstLine(md) {
  if (!md) return "（無內容）";
  const line = md.split("\n").map(l => l.replace(/^[#>*\-\s]+/, "").trim()).find(l => l.length > 4);
  return (line || "").slice(0, 80);
}

// ---------------------------------------------------------------- checkpoints
function initCheckpointsTab() {
  $("btn-copy-cp").addEventListener("click", () => {
    if (currentCheckpointMarkdown) navigator.clipboard.writeText(currentCheckpointMarkdown);
  });
}

async function loadCheckpoints() {
  try {
    const list = await getJSON("/api/v1/logs/checkpoints");
    const box = $("checkpoint-history-list");
    if (!list.length) {
      box.innerHTML = '<div class="placeholder">目前尚無快照日誌。</div>';
      return;
    }
    box.innerHTML = list.map((c, i) => `
      <div class="sideitem ${i === 0 ? "active" : ""}" data-file="${esc(c.file_name)}">
        <div class="sideitem-title">${esc(c.file_name.replace(/^checkpoint_/, "").replace(/\.md$/, ""))}</div>
        <div class="sideitem-sub">${esc(c.created_at || "")} · ${(c.size_bytes / 1024).toFixed(1)} KB</div>
      </div>`).join("");
    box.querySelectorAll(".sideitem").forEach(el => {
      el.addEventListener("click", () => selectCheckpoint(el.dataset.file));
    });
    selectCheckpoint(list[0].file_name);
  } catch (e) {
    $("checkpoint-history-list").innerHTML = '<div class="placeholder">無法讀取快照日誌。</div>';
  }
}

async function selectCheckpoint(fileName) {
  document.querySelectorAll("#checkpoint-history-list .sideitem").forEach(el => {
    el.classList.toggle("active", el.dataset.file === fileName);
  });
  try {
    const data = await getJSON(`/api/v1/logs/checkpoints/${encodeURIComponent(fileName)}`);
    currentCheckpointMarkdown = data.content || "";
    $("cp-title").textContent = fileName;
    $("checkpoint-markdown-viewer").innerHTML = window.marked
      ? marked.parse(currentCheckpointMarkdown)
      : `<pre>${esc(currentCheckpointMarkdown)}</pre>`;
  } catch (e) { console.error(e); }
}

async function triggerCheckpoint() {
  const btn = $("btn-quick-checkpoint");
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = "⏳ 產出中…";
  try {
    const hours = parseInt($("input-checkpoint-interval").value, 10) || 2;
    await postJSON("/api/v1/logs/checkpoints/generate", { hours });
    loadCheckpoints();
  } catch (e) {
    btn.textContent = "產出失敗";
  } finally {
    setTimeout(() => { btn.disabled = false; btn.textContent = label; }, 1400);
  }
}

// ---------------------------------------------------------------- DeskRAG Knowledge & Chat
let ragSessionsCache = [];
let currentRagSessionId = "";
let ragChatHistory = [];
let isRagStreaming = false;
// RAG 串流的介面安全網：閒置逾時上限與目前的 AbortController。
const RAG_STREAM_IDLE_TIMEOUT_MS = 120000;
let streamAbort = null;
let streamTimedOut = false;

const RAG_PROVIDER_MODELS = {
  ollama: [
    { value: "llama3.1:8b", label: "llama3.1:8b (預設)" },
    { value: "mistral:7b", label: "mistral:7b" },
    { value: "gemma4:e4b", label: "gemma4:e4b" },
    { value: "qwen3:4b", label: "qwen3:4b" }
  ],
  gemini: [
    { value: "gemini-3.7-flash", label: "gemini-3.7-flash (推薦)" },
    { value: "gemini-2.5-flash", label: "gemini-2.5-flash" },
    { value: "gemini-2.5-pro", label: "gemini-2.5-pro" }
  ],
  claude: [
    { value: "claude-3-5-sonnet-20241022", label: "claude-3-5-sonnet (推薦)" },
    { value: "claude-3-5-haiku-20241022", label: "claude-3-5-haiku" }
  ],
  openai: [
    { value: "gpt-4o", label: "gpt-4o (推薦)" },
    { value: "gpt-4o-mini", label: "gpt-4o-mini" }
  ]
};

function updateRAGModelSelect(provider) {
  const modelSelect = $("select-rag-model") || $("input-rag-model");
  if (!modelSelect) return;
  const prov = provider || ($("select-rag-provider") ? $("select-rag-provider").value : "ollama");
  const models = RAG_PROVIDER_MODELS[prov] || RAG_PROVIDER_MODELS.ollama;
  if (modelSelect.tagName === "SELECT") {
    modelSelect.innerHTML = models.map((m, idx) => `
      <option value="${esc(m.value)}" ${idx === 0 ? "selected" : ""}>${esc(m.label || m.value)}</option>
    `).join("");
  } else {
    modelSelect.value = models[0] ? models[0].value : "llama3.1:8b";
  }
}

function initRAGTab() {
  updateRAGModelSelect("ollama");
  // 新增目錄
  const addBtn = $("btn-rag-add-folder");
  if (addBtn) {
    addBtn.addEventListener("click", async () => {
      const pathInput = $("input-rag-folder-path");
      const nameInput = $("input-rag-folder-name");
      const path = (pathInput.value || "").trim();
      const name = (nameInput.value || "").trim();
      if (!path) {
        alert("請輸入有效的本機目錄絕對路徑！");
        return;
      }
      try {
        addBtn.disabled = true;
        addBtn.textContent = "⏳ 加入中…";
        await postJSON("/api/v1/rag/folders", {
          path,
          name: name || undefined,
          max_files: readRAGNumber("input-rag-max-files", 500, 1),
          throttle_ms: readRAGNumber("input-rag-throttle-ms", 25, 0),
        });
        pathInput.value = "";
        nameInput.value = "";
        loadRAGFolders();
        startRAGProgressPolling();
      } catch (e) {
        alert("加入目錄失敗: " + e.message);
      } finally {
        addBtn.disabled = false;
        addBtn.textContent = "+ 加入";
      }
    });
  }

  // 立即全量掃描
  const scanBtn = $("btn-rag-scan-now");
  if (scanBtn) {
    scanBtn.addEventListener("click", async () => {
      try {
        scanBtn.disabled = true;
        scanBtn.textContent = "⏳ 啟動中…";
        await postJSON("/api/v1/rag/scan", {
          max_files: readRAGNumber("input-rag-max-files", 500, 1),
          throttle_ms: readRAGNumber("input-rag-throttle-ms", 25, 0),
        });
        startRAGProgressPolling();
      } catch (e) {
        alert("啟動掃描失敗: " + e.message);
      } finally {
        setTimeout(() => { scanBtn.disabled = false; scanBtn.textContent = "⚡ 掃描索引"; }, 1500);
      }
    });
  }

  const jobAction = (buttonId, action) => {
    const button = $(buttonId);
    if (!button) return;
    button.addEventListener("click", async () => {
      const job = await getJSON("/api/v1/rag/jobs/current");
      if (!job.id) return;
      try {
        await postJSON(`/api/v1/rag/jobs/${encodeURIComponent(job.id)}/${action}`, {});
        startRAGProgressPolling();
      } catch (e) {
        alert("工作控制失敗: " + e.message);
      }
    });
  };
  jobAction("btn-rag-pause", "pause");
  jobAction("btn-rag-resume", "resume");
  jobAction("btn-rag-cancel", "cancel");

  const clearIndexBtn = $("btn-rag-clear-index");
  if (clearIndexBtn) {
    clearIndexBtn.addEventListener("click", async () => {
      if (!confirm("確定清空所有 RAG 索引嗎？只會刪除切片、向量與資料夾索引紀錄，不會刪除原始檔案或對話。")) return;
      if (prompt("請輸入 CLEAR 以確認清空所有 RAG 索引：") !== "CLEAR") return;
      try {
        await postJSON("/api/v1/rag/clear-index", { confirm: true });
        startRAGProgressPolling();
      } catch (e) {
        alert("清空索引失敗: " + e.message);
      }
    });
  }

  const verifyIndexBtn = $("btn-rag-verify-index");
  if (verifyIndexBtn) {
    verifyIndexBtn.addEventListener("click", async () => {
      try {
        await postJSON("/api/v1/rag/storage/verify", {});
        startRAGProgressPolling();
      } catch (e) {
        alert("無法啟動索引驗證: " + e.message);
      }
    });
  }

  const memorySyncBtn = $("btn-rag-memory-sync");
  if (memorySyncBtn) {
    memorySyncBtn.addEventListener("click", async () => {
      if (!confirm("把小秘書記憶區筆記、每日時段摘要、Handoff、同步報告與 STATUS 草稿併入知識庫？\n只讀本機資料，在獨立 worker 執行；重跑會覆蓋同一批切片。")) return;
      try {
        await postJSON("/api/v1/rag/memory/sync", {});
        startRAGProgressPolling();
      } catch (e) {
        alert("無法啟動記憶併入: " + e.message);
      }
    });
  }

  const rebuildBM25Btn = $("btn-rag-rebuild-bm25");
  if (rebuildBM25Btn) {
    rebuildBM25Btn.addEventListener("click", async () => {
      if (!confirm("確定從既有 Chroma 向量庫重建 BM25 嗎？不會重新掃描來源檔案，但大型索引仍需要一些時間。")) return;
      try {
        await postJSON("/api/v1/rag/storage/rebuild-bm25", {});
        startRAGProgressPolling();
      } catch (e) {
        alert("無法啟動 BM25 重建: " + e.message);
      }
    });
  }

  const warmupRetrievalBtn = $("btn-rag-retrieval-warmup");
  if (warmupRetrievalBtn) {
    warmupRetrievalBtn.addEventListener("click", async () => {
      try {
        await postJSON("/api/v1/rag/retrieval/warmup", {});
        startRAGRetrievalPolling();
      } catch (e) {
        alert("無法啟動檢索 worker 預熱: " + e.message);
      }
    });
  }

  const releaseRetrievalBtn = $("btn-rag-retrieval-release");
  if (releaseRetrievalBtn) {
    releaseRetrievalBtn.addEventListener("click", async () => {
      try {
        await postJSON("/api/v1/rag/retrieval/shutdown", {});
        await refreshRAGRetrieval();
      } catch (e) {
        alert("無法釋放檢索 worker: " + e.message);
      }
    });
  }

  // 搜尋檔案
  const fileSearchInput = $("input-rag-file-search");
  if (fileSearchInput) {
    let debounceTimer = null;
    fileSearchInput.addEventListener("input", (e) => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        loadRAGFiles(e.target.value.trim());
      }, 300);
    });
  }

  // 切換對話 Session
  const sessionSelect = $("select-rag-session");
  if (sessionSelect) {
    sessionSelect.addEventListener("change", (e) => {
      const sId = e.target.value;
      currentRagSessionId = sId;
      if (sId) {
        loadRAGMessages(sId);
      } else {
        ragChatHistory = [];
        renderRAGMessages();
      }
    });
  }

  // 開新對話
  const newChatBtn = $("btn-rag-new-chat");
  if (newChatBtn) {
    newChatBtn.addEventListener("click", () => {
      currentRagSessionId = "";
      if (sessionSelect) sessionSelect.value = "";
      ragChatHistory = [];
      renderRAGMessages();
      $("input-rag-prompt").focus();
    });
  }

  // 刪除對話
  const delChatBtn = $("btn-rag-del-chat");
  if (delChatBtn) {
    delChatBtn.addEventListener("click", async () => {
      if (!currentRagSessionId) return;
      if (!confirm("確定要刪除此對話紀錄嗎？")) return;
      try {
        await fetch(API + `/api/v1/rag/chat/sessions/${encodeURIComponent(currentRagSessionId)}`, { method: "DELETE" });
        currentRagSessionId = "";
        ragChatHistory = [];
        renderRAGMessages();
        loadRAGSessions();
      } catch (e) {
        alert("刪除失敗: " + e.message);
      }
    });
  }

  // 切換提供者自動帶出模型選單
  const providerSelect = $("select-rag-provider");
  if (providerSelect) {
    providerSelect.addEventListener("change", (e) => {
      updateRAGModelSelect(e.target.value);
    });
  }

  // 發送訊息
  const sendBtn = $("btn-rag-send");
  const promptInput = $("input-rag-prompt");
  if (sendBtn && promptInput) {
    sendBtn.addEventListener("click", sendRAGChatMessage);
    promptInput.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        e.preventDefault();
        sendRAGChatMessage();
      }
    });
  }

  // 清空視窗
  const clearBtn = $("btn-rag-clear");
  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      ragChatHistory = [];
      renderRAGMessages();
    });
  }
}

async function loadRAGFolders() {
  try {
    const folders = await getJSON("/api/v1/rag/folders");
    const container = $("rag-folders-list");
    if (!container) return;
    if (!folders.length) {
      container.innerHTML = '<div class="placeholder">尚未加入任何目錄。請在上方輸入目錄路徑以建立知識庫。</div>';
      return;
    }
    container.innerHTML = folders.map(f => `
      <div class="rag-folder-card">
        <div class="rag-folder-info">
          <div class="rag-folder-name">${esc(f.name || Path_basename(f.path))} <span class="mono-mini muted">(${f.file_count || 0} 檔案)</span></div>
          <div class="rag-folder-path" title="${esc(f.path)}">${esc(f.path)}</div>
        </div>
        <button class="btn btn-ghost btn-sm" onclick="deleteRAGFolder(${f.id})" title="移除此資料夾的索引，保留原始檔案" style="color:var(--danger); padding:2px 6px;">移除索引</button>
      </div>
    `).join("");
  } catch (e) {
    console.error("loadRAGFolders error:", e);
  }
}

function Path_basename(p) {
  if (!p) return "";
  const parts = p.replace(/[\\/]+$/, "").split(/[\\/]/);
  return parts[parts.length - 1] || p;
}

async function deleteRAGFolder(id) {
  if (!confirm("確定移除這個資料夾的 RAG 索引嗎？原始檔案不會被刪除，完成後會執行 SQLite 空間回收與一致性檢查。")) return;
  try {
    await postJSON(`/api/v1/rag/folders/${id}/remove-index`, { confirm: true });
    startRAGProgressPolling();
  } catch (e) {
    alert("刪除失敗: " + e.message);
  }
}

let ragProgressPollTimer = null;

function readRAGNumber(id, fallback, minimum) {
  const value = Number($(id)?.value);
  return Number.isFinite(value) && value >= minimum ? Math.floor(value) : fallback;
}

function renderBackgroundTaskPanel(data) {
  const enabled = data.enabled !== false;
  const status = data.evidence_status || "not_observed";
  const badge = $("background-tasks-evidence");
  badge.className = "trust " + (!enabled ? "broken" : status === "verified_receipts" ? "ok" : "noisy");
  badge.textContent = !enabled ? "DISABLED" : status === "verified_receipts" ? "VERIFIED" : "WAITING";
  $("background-tasks-value").textContent = formatUsageDuration(data.verified_seconds || 0);

  const completed = Number(data.completed_task_count || 0);
  const awaiting = Number(data.awaiting_final_count || 0);
  const untrusted = Number(data.untrusted_duration_count || 0);
  $("background-tasks-meta").textContent = currentLang === "zh-TW"
    ? `已完成 ${completed} 件 · 等待 final receipt ${awaiting} 件${untrusted ? ` · 異常時長未計入 ${untrusted} 件` : ""}`
    : `${completed} completed · ${awaiting} awaiting final receipt${untrusted ? ` · ${untrusted} excluded duration` : ""}`;
  $("background-tasks-boundary").textContent = currentLang === "zh-TW"
    ? "只納入本機來源可確認的 prompt 開始與明確 final completion；與前景使用時間完全分開，不代表生產力、一般 Terminal 或全部工作。"
    : "Only local prompt-start and explicit final-completion receipts are included. It is separate from foreground time, productivity, generic terminal time, and all work.";

  const rows = (data.recent_tasks || []).slice(0, 6);
  $("background-tasks-list").innerHTML = rows.length ? rows.map(item => {
    const completedAt = item.completed_at ? new Date(item.completed_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—";
    const project = item.project_tag || (currentLang === "zh-TW" ? "未歸戶專案" : "Unassigned project");
    return `<div class="background-task-row">
      <span class="background-task-platform">${esc(item.label)}</span>
      <span class="background-task-project" title="${esc(project)}">${esc(project)}</span>
      <span class="background-task-duration">${formatUsageDuration(item.duration_seconds)}</span>
      <span class="background-task-completed">${completedAt}</span>
    </div>`;
  }).join("") : `<div class="placeholder">${currentLang === "zh-TW" ? "今日尚無可由開始與 final receipt 成對驗證的背景任務。" : "No background task has paired start and final receipts today."}</div>`;
}

function renderBackgroundTaskPanelError() {
  $("background-tasks-evidence").className = "trust broken";
  $("background-tasks-evidence").textContent = "UNAVAILABLE";
  $("background-tasks-list").innerHTML = `<div class="placeholder">${currentLang === "zh-TW" ? "無法載入背景任務收據。" : "Unable to load background task receipts."}</div>`;
}

function formatRAGBytes(bytes) {
  if (bytes === null || bytes === undefined) return "待驗證";
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let scaled = value / 1024;
  let unit = 0;
  while (scaled >= 1024 && unit < units.length - 1) { scaled /= 1024; unit += 1; }
  return `${scaled.toFixed(scaled >= 100 ? 0 : 1)} ${units[unit]}`;
}

function renderRAGJob(data) {
  const box = $("rag-progress-box");
  const totalChunks = $("rag-total-chunks-count");
  if (totalChunks) totalChunks.textContent = `${data.total_indexed_chunks || 0} 切片`;
  if (!data.id && !data.is_running) return false;
  if (box) box.style.display = "block";
  const isActive = Boolean(data.is_running);
  const statusMap = {
    scanning: "掃描目錄中…", indexing: "正在建立切片向量…", paused: "⏸ 已暫停",
    cancelling: "正在取消…", completed: "✅ 索引完成", completed_limited: "✅ 本次上限完成",
    cancelled: "已取消", failed: "索引失敗",
  };
  const status = data.status || "idle";
  const isCompleted = status === "completed" || status === "completed_limited";
  const percent = isCompleted ? 100 : (data.progress_percent || 0);
  if ($("rag-progress-bar")) $("rag-progress-bar").style.width = `${percent}%`;
  if ($("rag-progress-percent")) $("rag-progress-percent").textContent = `${percent}%`;
  if ($("rag-progress-status")) $("rag-progress-status").textContent = statusMap[status] || status;
  if ($("rag-progress-detail")) {
    const detail = data.message || `處理中：${data.current_file || ""} (${data.processed_files || 0}/${data.total_files || 0})`;
    $("rag-progress-detail").textContent = `${detail}${data.current_file ? ` · ${data.current_file}` : ""}`;
  }
  if ($("btn-rag-pause")) $("btn-rag-pause").disabled = !isActive || status === "paused" || status === "cancelling";
  if ($("btn-rag-resume")) $("btn-rag-resume").disabled = status !== "paused";
  if ($("btn-rag-cancel")) $("btn-rag-cancel").disabled = !isActive || status === "cancelling";
  return isActive;
}

async function refreshRAGStorage() {
  const card = $("rag-storage-card");
  if (!card) return;
  try {
    const data = await getJSON("/api/v1/rag/storage");
    const vectorCount = data.vector_chunks === null || data.vector_chunks === undefined ? "待驗證" : Number(data.vector_chunks).toLocaleString();
    card.innerHTML = `<div class="rag-storage-grid">
      <span>來源檔案 ${Number(data.source_files || 0).toLocaleString()}</span><span>來源大小 ${formatRAGBytes(data.source_bytes)}</span>
      <span>資料庫切片 ${Number(data.source_chunks || 0).toLocaleString()}</span><span>向量 ${vectorCount}</span>
      <span>索引空間 ${formatRAGBytes(data.index_bytes)}</span><span>SQLite ${formatRAGBytes(data.sqlite_bytes)}</span>
    </div>${data.consistency === "matched" ? "" : `<div class="rag-storage-alert">${data.consistency === "unverified" ? "尚未以獨立 worker 驗證 Chroma／BM25，請按「驗證索引與空間」。" : `索引計數待檢查：向量差異 ${Number(data.vector_delta || 0).toLocaleString()}。`}</div>`}`;
  } catch (e) {
    card.textContent = "索引儲存空間暫時無法取得。";
  }
}

let ragRetrievalPollTimer = null;

function describeRAGRetrieval(data) {
  const mode = data.mode === "in_process" ? "in_process（在主服務內檢索）" : "常駐 worker";
  const stateMap = {
    cold: "尚未啟動（第一次提問時才載入索引）",
    starting: "啟動中…",
    loading: "已啟動，尚未預熱",
    warming: "預熱中：正在載入 BM25／Chroma／embedding…",
    ready: "就緒",
    failed: "失敗",
  };
  const lines = [`<span>模式 ${mode}</span><span>狀態 ${stateMap[data.state] || data.state}</span>`];
  if (data.warmup) {
    const w = data.warmup;
    const d = w.durations || {};
    lines.push(`<span>BM25 ${Number(w.bm25_chunks || 0).toLocaleString()} 切片</span><span>向量 ${Number(w.vector_chunks || 0).toLocaleString()}</span>`);
    lines.push(`<span>預熱耗時 ${d.total_ms !== undefined ? `${(d.total_ms / 1000).toFixed(1)} s` : "—"}</span><span>worker 記憶體 ${w.worker_rss_mb !== null && w.worker_rss_mb !== undefined ? `${w.worker_rss_mb} MB` : "—"}</span>`);
    if (w.embedding_ready === false) lines.push(`<span style="grid-column: 1 / -1;">embedding 模型未就緒：${w.embedding_error || "未知原因"}</span>`);
  }
  if (data.requests_served) {
    lines.push(`<span>已服務 ${data.requests_served} 次</span><span>最近檢索 ${data.last_retrieval_ms !== null && data.last_retrieval_ms !== undefined ? `${data.last_retrieval_ms} ms` : "—"}</span>`);
  }
  let alert = "";
  if (data.state === "failed" && data.last_error) alert = `最近錯誤：${data.last_error}`;
  else if (data.mode === "worker" && data.state === "cold" && !data.index_present) alert = "尚無索引，不需預熱。";
  else if (data.mode === "worker" && data.state === "cold" && data.restarts) alert = `worker 曾重啟 ${data.restarts} 次；${data.last_error || ""}`;
  return `<div class="rag-storage-grid">${lines.join("")}</div>${alert ? `<div class="rag-storage-alert">${alert}</div>` : ""}`;
}

async function refreshRAGRetrieval() {
  const card = $("rag-retrieval-card");
  if (!card) return null;
  try {
    const data = await getJSON("/api/v1/rag/retrieval/status");
    card.innerHTML = describeRAGRetrieval(data);
    const warmBtn = $("btn-rag-retrieval-warmup");
    const releaseBtn = $("btn-rag-retrieval-release");
    if (warmBtn) warmBtn.disabled = data.mode !== "worker" || data.state === "warming" || data.state === "ready";
    if (releaseBtn) releaseBtn.disabled = data.mode !== "worker" || !data.pid;
    return data;
  } catch (e) {
    card.textContent = "檢索 worker 狀態暫時無法取得。";
    return null;
  }
}

function startRAGRetrievalPolling() {
  if (ragRetrievalPollTimer) clearInterval(ragRetrievalPollTimer);
  ragRetrievalPollTimer = setInterval(async () => {
    const data = await refreshRAGRetrieval();
    if (!data || !["starting", "warming", "loading"].includes(data.state)) {
      clearInterval(ragRetrievalPollTimer);
      ragRetrievalPollTimer = null;
    }
  }, 2000);
}

async function pollRAGProgress() {
  const data = await getJSON("/api/v1/rag/progress");
  const isActive = renderRAGJob(data);
  if (!isActive) {
    if (ragProgressPollTimer) clearInterval(ragProgressPollTimer);
    ragProgressPollTimer = null;
    loadRAGFolders();
    loadRAGFiles();
    refreshRAGStorage();
  }
}

function startRAGProgressPolling() {
  if (ragProgressPollTimer) clearInterval(ragProgressPollTimer);
  pollRAGProgress().catch(() => {});
  ragProgressPollTimer = setInterval(() => pollRAGProgress().catch(() => {}), 1000);
}

async function loadRAGProgress() {
  try {
    const data = await getJSON("/api/v1/rag/progress");
    const active = renderRAGJob(data);
    if (active) startRAGProgressPolling();
    refreshRAGStorage();
    refreshRAGRetrieval().then((retrieval) => {
      if (retrieval && ["starting", "warming", "loading"].includes(retrieval.state)) startRAGRetrievalPolling();
    });
  } catch (e) {}
}

async function loadRAGFiles(search = "") {
  try {
    const url = `/api/v1/rag/files?page=1&page_size=50${search ? `&search=${encodeURIComponent(search)}` : ""}`;
    const res = await getJSON(url);
    const container = $("rag-files-list");
    const countSpan = $("rag-total-files-count");
    if (countSpan) countSpan.textContent = res.total || 0;
    if (!container) return;

    if (!res.items || !res.items.length) {
      container.innerHTML = '<div class="placeholder">目前無符合條件的檔案。</div>';
      return;
    }

    container.innerHTML = res.items.map(file => `
      <div class="rag-file-item" onclick="openRAGFileInExplorer('${esc(file.path).replace(/'/g, "\\'")}')" title="點擊在 Windows 總管開啟: ${esc(file.path)}">
        <div class="rag-file-name">📄 ${esc(file.filename)}</div>
        <div style="display:flex; gap:4px; align-items:center;">
          <span class="mono-mini muted">${file.chunk_count || 0} 切片</span>
          <span class="rag-file-badge ${file.status === "indexed" ? "indexed" : "failed"}">${file.status === "indexed" ? "OK" : "ERR"}</span>
        </div>
      </div>
    `).join("");
  } catch (e) {
    console.error("loadRAGFiles error:", e);
  }
}

async function openRAGFileInExplorer(path) {
  try {
    await postJSON("/api/v1/rag/open-file", { path });
  } catch (e) {
    alert("無法開啟總管: " + e.message);
  }
}

async function loadRAGSessions() {
  try {
    const sessions = await getJSON("/api/v1/rag/chat/sessions");
    ragSessionsCache = Array.isArray(sessions) ? sessions : [];
    const select = $("select-rag-session");
    if (!select) return;

    select.innerHTML = '<option value="">➕ 建立新對話</option>' + ragSessionsCache.map(s => {
      const title = (s.title || "").trim();
      const displayTitle = title && title !== "新對話" ? title : (currentLang === "zh-TW" ? "對話紀錄" : "Chat");
      return `<option value="${esc(s.id)}" ${s.id === currentRagSessionId ? "selected" : ""}>💬 ${esc(displayTitle)}</option>`;
    }).join("");
    if (currentRagSessionId) {
      select.value = currentRagSessionId;
    }
  } catch (e) {}
}

async function loadRAGStrategies() {
  try {
    const data = await getJSON("/api/v1/rag/strategies");
    const select = $("select-rag-strategy");
    if (!select || !data.strategies) return;
    select.innerHTML = data.strategies.map(st => `
      <option value="${esc(st.name)}" ${st.name === data.default ? "selected" : ""}>${esc(st.display_name)}</option>
    `).join("");
  } catch (e) {}
}

async function loadRAGMessages(sessionId) {
  try {
    const messages = await getJSON(`/api/v1/rag/chat/messages/${encodeURIComponent(sessionId)}`);
    ragChatHistory = messages.map(m => ({
      role: m.role,
      content: m.content,
      citations: m.citations || [],
      provider: m.provider,
      model: m.model,
      time: m.created_at
    }));
    renderRAGMessages();
  } catch (e) {
    console.error("loadRAGMessages error:", e);
  }
}

function renderRAGMessages() {
  renderAssistantChatMirror(); // 小秘書首頁同步顯示同一條對話
  const container = $("rag-chat-messages");
  if (!container) return;

  if (!ragChatHistory.length) {
    container.innerHTML = `
      <div class="placeholder" style="margin: auto; text-align: center;">
        <div style="font-size: 28px; margin-bottom: 8px;">📚</div>
        <div style="font-weight: 600; margin-bottom: 4px;">DeskRAG 本地文件智慧助手</div>
        <div class="muted small">請在下方輸入問題，系統將結合本地知識庫與活動記錄進行精準問答與引文標註。</div>
      </div>
    `;
    return;
  }

  container.innerHTML = ragChatHistory.map((msg, idx) => {
    const isUser = msg.role === "user";
    const renderedContent = isUser
      ? esc(msg.content).replace(/\n/g, "<br>")
      : (window.marked ? marked.parse(msg.content) : esc(msg.content).replace(/\n/g, "<br>"));

    let citationsHtml = "";
    if (!isUser && msg.citations && msg.citations.length) {
      citationsHtml = `
        <div class="rag-citations-box">
          <div class="rag-citations-title">📌 參考文檔來源 (${msg.citations.length} 個切片)：</div>
          ${msg.citations.map(c => `
            <div class="rag-citation-card">
              <div class="rag-citation-head">
                <div class="rag-citation-filename">《${esc(c.filename || c.title || "文件")}》</div>
                <div class="rag-citation-tags">
                  ${c.page ? `<span class="rag-citation-tag">第 ${c.page} 頁</span>` : ""}
                  ${c.slide ? `<span class="rag-citation-tag">第 ${c.slide} 投影片</span>` : ""}
                  ${c.sheet ? `<span class="rag-citation-tag">工作表 ${esc(c.sheet)}</span>` : ""}
                  <span class="rag-citation-tag score">相關度 ${c.score}</span>
                  <button class="rag-btn-open" onclick="openRAGFileInExplorer('${esc(c.file_path).replace(/'/g, "\\'")}')" title="在 Windows 總管開啟並選中">📂 在總管開啟</button>
                </div>
              </div>
              <div class="rag-citation-content">${esc(c.content || "")}</div>
            </div>
          `).join("")}
        </div>
      `;
    }

    return `
      <div class="rag-msg ${isUser ? "user" : "assistant"}">
        <div class="rag-msg-bubble markdown">${renderedContent}${citationsHtml}</div>
        <div class="rag-msg-meta">
          <span>${isUser ? "YOU" : `AI (${esc(msg.provider || "ollama")} · ${esc(msg.model || "")})`}</span>
          ${msg.time ? `<span>${esc(msg.time)}</span>` : ""}
        </div>
      </div>
    `;
  }).join("");

  container.scrollTop = container.scrollHeight;
}

async function sendRAGChatMessage(fromInput) {
  if (isRagStreaming) return;
  // 小秘書首頁與 RAG 分頁共用同一條對話流（同 session、同歷史）。
  const promptInput = fromInput || $("input-rag-prompt");
  const prompt = (promptInput.value || "").trim();
  if (!prompt) return;

  // 「記下來：…」「偏好：…」「決定：…」直接寫進記憶區，不送 LLM（ADR-012）。
  const noteCmd = parseMemoryCommand(prompt);
  if (noteCmd) {
    promptInput.value = "";
    await rememberFromChat(noteCmd, prompt);
    return;
  }

  promptInput.value = "";
  const provider = $("select-rag-provider") ? $("select-rag-provider").value : "ollama";
  const modelSelect = $("select-rag-model") || $("input-rag-model");
  const model = modelSelect ? modelSelect.value : "llama3.1:8b";
  const strategy = $("select-rag-strategy") ? $("select-rag-strategy").value : "hybrid_rrf";
  const enableRag = $("toggle-enable-rag") ? $("toggle-enable-rag").checked : true;

  // 1. 建立或確保 Session
  if (!currentRagSessionId) {
    try {
      const sTitle = prompt.slice(0, 24);
      const sRes = await postJSON("/api/v1/rag/chat/sessions", { title: sTitle });
      currentRagSessionId = sRes.session_id;
      loadRAGSessions();
    } catch (e) {
      currentRagSessionId = "session_" + Date.now();
    }
  }

  // 2. 加入 User 訊息
  const userMsg = {
    role: "user",
    content: prompt,
    provider,
    model,
    time: new Date().toLocaleTimeString()
  };
  ragChatHistory.push(userMsg);

  // 儲存 User Message 到後端
  postJSON("/api/v1/rag/chat/messages", {
    session_id: currentRagSessionId,
    role: "user",
    content: prompt,
    provider,
    model
  }).catch(() => {});

  // 3. 準備 Assistant 訊息佔位
  const assistantMsg = {
    role: "assistant",
    content: "",
    citations: [],
    provider,
    model,
    time: new Date().toLocaleTimeString()
  };
  ragChatHistory.push(assistantMsg);
  renderRAGMessages();

  // 4. 開始 SSE 串流
  isRagStreaming = true;
  const sendBtn = $("btn-rag-send");
  if (sendBtn) {
    sendBtn.disabled = true;
    sendBtn.textContent = "串流中…";
  }
  const assistantBtn = $("btn-assistant-send");
  if (assistantBtn) {
    assistantBtn.disabled = true;
    assistantBtn.textContent = currentLang === "zh-TW" ? "回覆中…" : "Streaming…";
  }

  try {
    const apiMessages = ragChatHistory.slice(0, -1).map(m => ({ role: m.role, content: m.content }));
    // 安全網：後端若完全沒回應（網路卡住、程序被砍），介面也不能永遠停在
    // 「回覆中」。閒置逾時只在「一段時間沒有任何新位元組」時才觸發，
    // 正常的長回答會不斷刷新它。
    streamAbort = new AbortController();
    let idleTimer = null;
    const resetIdleTimer = () => {
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(() => {
        streamTimedOut = true;
        streamAbort.abort();
      }, RAG_STREAM_IDLE_TIMEOUT_MS);
    };
    resetIdleTimer();
    const response = await fetch(API + "/api/v1/rag/chat", {
      signal: streamAbort.signal,
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: currentRagSessionId,
        messages: apiMessages,
        provider,
        model,
        enable_rag: enableRag,
        retrieval_strategy: strategy
      })
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      resetIdleTimer();

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n\n");
      buffer = lines.pop(); // keep remainder

      for (const block of lines) {
        if (!block.trim()) continue;
        const lineParts = block.split("\n");
        let eventType = "";
        let eventData = "";

        for (const lp of lineParts) {
          if (lp.startsWith("event: ")) eventType = lp.replace("event: ", "").trim();
          if (lp.startsWith("data: ")) eventData = lp.replace("data: ", "").trim();
        }

        if (eventType === "citations" && eventData) {
          try {
            assistantMsg.citations = JSON.parse(eventData);
            renderRAGMessages();
          } catch (e) {}
        } else if (eventType === "memory" && eventData) {
          try {
            assistantMsg.memory = JSON.parse(eventData);
            renderRAGMessages();
          } catch (e) {}
        } else if (eventType === "message" && eventData) {
          try {
            const tokenObj = JSON.parse(eventData);
            assistantMsg.content += tokenObj.token || "";
            renderRAGMessages();
          } catch (e) {}
        } else if (eventType === "done") {
          if (idleTimer) clearTimeout(idleTimer);
          break;
        }
      }
    }

    // 儲存 Assistant Message 到後端
    postJSON("/api/v1/rag/chat/messages", {
      session_id: currentRagSessionId,
      role: "assistant",
      content: assistantMsg.content,
      citations: assistantMsg.citations,
      provider,
      model
    }).then(() => loadRAGSessions()).catch(() => {});

  } catch (e) {
    const zh = currentLang === "zh-TW";
    assistantMsg.content += streamTimedOut
      ? (zh
        ? `\n\n[逾時：${RAG_STREAM_IDLE_TIMEOUT_MS / 1000} 秒內沒有收到任何回應。請確認所選 provider 的 API key 與網路，或改用本機 Ollama。]`
        : `\n\n[Timed out: no response for ${RAG_STREAM_IDLE_TIMEOUT_MS / 1000}s. Check the selected provider's API key and network, or switch to local Ollama.]`)
      : `\n\n[串流發生錯誤: ${e.message}]`;
    renderRAGMessages();
  } finally {
    if (streamAbort) streamAbort = null;
    streamTimedOut = false;
    isRagStreaming = false;
    if (sendBtn) {
      sendBtn.disabled = false;
      sendBtn.textContent = "發送 ⚡";
    }
    if (assistantBtn) {
      assistantBtn.disabled = false;
      assistantBtn.textContent = t("btn_assistant_send");
    }
  }
}

// ---------------------------------------------------------------- 07 System Health & Maintenance Hub
function initSystemHealthTab() {
  const refreshBtn = $("btn-refresh-system-health");
  if (refreshBtn) refreshBtn.addEventListener("click", loadSystemHealth);

  const healBtn = $("btn-trigger-heal-ui");
  if (healBtn) healBtn.addEventListener("click", triggerSystemHeal);

  const walBtn = $("btn-trigger-wal-ui");
  if (walBtn) walBtn.addEventListener("click", triggerSystemWalCheckpoint);

  const maintainBtn = $("btn-trigger-maintain-ui");
  if (maintainBtn) maintainBtn.addEventListener("click", triggerSystemMaintenance);

  const clearBtn = $("btn-clear-system-console");
  if (clearBtn) clearBtn.addEventListener("click", () => {
    const con = $("system-action-console");
    if (con) con.textContent = "// 終端輸出已清空。";
  });
}

function appendSystemConsole(title, data) {
  const con = $("system-action-console");
  if (!con) return;
  const time = new Date().toLocaleTimeString();
  const formatted = typeof data === "string" ? data : JSON.stringify(data, null, 2);
  const block = `\n[${time}] >>> ${title}\n${formatted}\n`;
  if (con.textContent.startsWith("//")) {
    con.textContent = block.trim();
  } else {
    con.textContent = (con.textContent + "\n" + block).trim();
  }
  if (con.parentElement) con.parentElement.scrollTop = con.parentElement.scrollHeight;
}

// ---------------------------------------------------------------- 驗收中心
// 只呈現後端查到的收據；狀態字彙與 core/acceptance.py 一一對應，前端不自己判斷通過與否。
const ACCEPTANCE_STATUS_META = {
  passed:         { icon: "✅", trust: "ok" },
  attested:       { icon: "🖊", trust: "ok" },
  partial:        { icon: "🟡", trust: "noisy" },
  pending:        { icon: "⬜", trust: "" },
  needs_human:    { icon: "👤", trust: "noisy" },
  not_configured: { icon: "➖", trust: "" },
  runtime_only:   { icon: "🌐", trust: "" }
};

function acceptanceStatusLabel(status) {
  return t("acceptance_st_" + status) || status;
}

async function loadAcceptance() {
  try {
    acceptanceCache = await getJSON("/api/v1/acceptance/checklist");
    renderAcceptance();
  } catch (e) {
    console.error("loadAcceptance error:", e);
    const box = $("acceptance-items");
    if (box) box.innerHTML = `<div class="placeholder">${currentLang === "zh-TW" ? "無法讀取驗收現況。" : "Acceptance status is unavailable."}</div>`;
  }
}

function renderAcceptance() {
  const data = acceptanceCache;
  if (!data) return;
  const zh = currentLang === "zh-TW";
  const summary = data.summary || {};
  const settled = (summary.passed || 0) + (summary.attested || 0);

  const badge = $("acceptance-summary-badge");
  if (badge) {
    const blocking = (summary.blocking_release || []).length;
    badge.className = "trust " + (blocking ? "broken" : settled === summary.total ? "ok" : "noisy");
    badge.textContent = `${settled}/${summary.total || 0}`;
  }
  const stamp = $("acceptance-generated-at");
  if (stamp) stamp.textContent = data.generated_at ? `${data.generated_at} · ${data.mode}` : "";

  const gatesBox = $("acceptance-gates");
  if (gatesBox) {
    const blocking = summary.blocking_release || [];
    const banner = blocking.length
      ? `<div class="acceptance-banner broken">🔴 ${esc(blocking.join("、"))} ${esc(t("acceptance_blocking"))}</div>`
      : "";
    const gates = (data.release_gates || []).map(gate => {
      const meta = ACCEPTANCE_STATUS_META[gate.status] || { icon: "•", trust: "" };
      const pendingItems = gate.outstanding || [];
      const shown = pendingItems.slice(0, 4).join("、") + (pendingItems.length > 4 ? ` … (${pendingItems.length})` : "");
      return `<div class="acceptance-gate">
        <div class="acceptance-gate-head">
          <span class="acceptance-icon">${meta.icon}</span>
          <span class="mono-mini muted">${esc(gate.id)}</span>
          <span class="acceptance-gate-text">${esc(gate.text)}</span>
        </div>
        ${pendingItems.length ? `<div class="acceptance-gate-pending mono-mini muted">${esc(t("acceptance_outstanding"))}：${esc(shown)}</div>` : ""}
      </div>`;
    }).join("");
    gatesBox.innerHTML = banner + `<div class="acceptance-gates-title mono-mini muted">${esc(t("acceptance_gates_title"))}</div>` + (gates || `<div class="placeholder">${zh ? "只查了部分項目，gate 需要完整清單。" : "Partial run — gates need the full checklist."}</div>`);
  }

  const box = $("acceptance-items");
  if (!box) return;
  box.innerHTML = (data.items || []).map(item => {
    const meta = ACCEPTANCE_STATUS_META[item.status] || { icon: "•", trust: "" };
    const flag = item.blocks_release && item.status !== "passed" && item.status !== "attested"
      ? `<span class="trust broken">P0</span>` : "";
    const attestable = item.status === "needs_human" || item.status === "attested";
    const attested = item.status === "attested";
    const button = attestable
      ? `<button class="btn btn-ghost btn-sm acceptance-confirm" data-item="${esc(item.id)}" data-confirmed="${attested ? "1" : "0"}">${esc(t(attested ? "acceptance_unconfirm_btn" : "acceptance_confirm_btn"))}</button>`
      : "";
    const attestation = item.attestation
      ? `<div class="acceptance-attestation mono-mini">🖊 ${esc(t("acceptance_attested_by_you"))} ${esc(item.attestation.confirmed_at)}${item.attestation.note ? " · " + esc(item.attestation.note) : ""}</div>`
      : "";
    return `<div class="acceptance-row">
      <div class="acceptance-row-head">
        <span class="acceptance-icon">${meta.icon}</span>
        <span class="mono-mini muted">${esc(item.id)}</span>
        <span class="acceptance-title">${esc(item.title)}</span>
        <span class="trust ${meta.trust}">${esc(acceptanceStatusLabel(item.status))}</span>
        ${flag}
        <span class="head-right">${button}</span>
      </div>
      <div class="acceptance-detail">${esc(item.detail)}</div>
      <div class="acceptance-meta mono-mini muted">${esc(t("acceptance_how"))}：${esc(item.how)}</div>
      <div class="acceptance-meta mono-mini muted">${esc(t("acceptance_criterion"))}：${esc(item.criterion)}</div>
      ${attestation}
      <details class="acceptance-evidence">
        <summary class="mono-mini muted">${esc(t("acceptance_evidence"))}</summary>
        <pre>${esc(JSON.stringify(item.evidence || {}, null, 2))}</pre>
      </details>
    </div>`;
  }).join("") || `<div class="placeholder">${zh ? "沒有項目。" : "No items."}</div>`;

  box.querySelectorAll(".acceptance-confirm").forEach(btn => {
    btn.addEventListener("click", () => confirmAcceptanceItem(btn.dataset.item, btn.dataset.confirmed !== "1"));
  });
}

async function confirmAcceptanceItem(itemId, confirmed) {
  if (confirmed && !confirm(t("acceptance_confirm_ask"))) return;
  try {
    await postJSON("/api/v1/acceptance/confirm", { item_id: itemId, confirmed, note: "" });
    loadAcceptance();
  } catch (e) {
    alert(e.message);
  }
}

async function loadSystemHealth() {
  try {
    const data = await getJSON("/api/v1/system/health");
    renderSystemOverview(data);
    renderCollectorDiagnosticsMatrix(data);
    loadLatestMaintenanceReceipt();
  } catch (e) {
    console.error("loadSystemHealth error:", e);
  }
}

function renderSystemOverview(data) {
  const stateBadge = $("system-overall-badge");
  const stateVal = $("sys-stat-state");
  const dbSizeVal = $("sys-stat-dbsize");
  const walVal = $("sys-stat-wal");
  const projectsVal = $("sys-stat-projects");
  const dbPathVal = $("sys-stat-dbpath");
  const healedCountVal = $("sys-stat-healed-count");
  const lastHealVal = $("sys-stat-last-heal");

  const status = data.status || "healthy";
  const degradedList = data.degraded_collectors || [];

  if (stateBadge) {
    stateBadge.className = "trust " + (status === "healthy" ? "ok" : status === "degraded" ? "noisy" : "broken");
    stateBadge.textContent = status.toUpperCase();
  }
  if (stateVal) {
    stateVal.className = "system-stat-val " + (status === "healthy" ? "text-success" : status === "degraded" ? "text-warning" : "text-danger");
    stateVal.textContent = status === "healthy" ? "HEALTHY (正常)" : status === "degraded" ? `DEGRADED (${degradedList.length} 異常)` : "STOPPED (已暫停)";
  }

  if (data.database) {
    const sizeMb = (Number(data.database.size_bytes || 0) / (1024 * 1024)).toFixed(2);
    const walKb = (Number(data.database.wal_size_bytes || 0) / 1024).toFixed(1);
    if (dbSizeVal) dbSizeVal.textContent = `${sizeMb} MB`;
    if (walVal) walVal.textContent = `WAL: ${walKb} KB`;
    if (projectsVal) projectsVal.textContent = `${data.database.active_projects_count || 0} 專案`;
    if (dbPathVal) {
      dbPathVal.textContent = Path_basename(data.database.path || "omni_context.db");
      dbPathVal.title = data.database.path || "";
    }
  }

  if (data.self_healing) {
    const totalHealed = data.self_healing.total_healing_events_count || 0;
    if (healedCountVal) healedCountVal.textContent = `${totalHealed} 次自動修復`;
    const lastEvent = data.self_healing.last_healing_event;
    if (lastHealVal) {
      lastHealVal.textContent = lastEvent ? `最近修復: ${new Date(lastEvent.timestamp).toLocaleTimeString()}` : "常駐守護中";
    }
  }
}

function renderCollectorDiagnosticsMatrix(data) {
  const container = $("collectors-diagnostics-grid");
  if (!container) return;

  const watchers = data.watchers || {};
  const health = data.collector_health || {};
  const runtime = data.collector_runtime || {};
  const diag = data.collector_diagnostics || {};

  const collectors = [
    {
      key: "file_watcher",
      title: "📁 檔案變更監控 (File Watcher)",
      on: watchers.file_watcher,
      h: health.file_watcher || "healthy",
      r: runtime.file_watcher || "stopped",
      d: diag.file_watcher || {},
    },
    {
      key: "git_watcher",
      title: "🐙 Git 倉庫掃描 (Git Scanner)",
      on: watchers.git_watcher,
      h: health.git_watcher || "healthy",
      r: runtime.git_watcher || "stopped",
      d: diag.git_watcher || {},
    },
    {
      key: "window_watcher",
      title: "🪟 前景視窗焦點 (Window Focus)",
      on: watchers.window_watcher,
      h: health.window_watcher || "healthy",
      r: runtime.window_watcher || "stopped",
      d: diag.window_watcher || {},
    },
    {
      key: "agent_log_watcher",
      title: "🤖 AI 日誌採集 (Agent Log Watcher)",
      on: watchers.agent_log_watcher,
      h: health.agent_log_watcher || "healthy",
      r: runtime.agent_log_watcher || "stopped",
      d: diag.agent_log_watcher || {},
    },
    {
      key: "scheduler",
      title: "⏰ 背景合成與排程 (Scheduler)",
      on: watchers.scheduler,
      h: health.scheduler || "healthy",
      r: runtime.scheduler || "stopped",
      d: diag.scheduler || {},
    },
  ];

  const colorMap = {
    healthy: "var(--success, #22c55e)",
    idle: "var(--warn, #eab308)",
    degraded: "var(--danger, #ef4444)",
    stopped: "var(--danger, #ef4444)",
    stale: "var(--danger, #ef4444)",
    disabled: "var(--mu, #888)",
  };

  const labelMap = {
    healthy: "運作中 (Healthy)",
    idle: "待命中 (Idle)",
    degraded: "部分降級 (Degraded)",
    stopped: "已停止 (Stopped)",
    stale: "無有效資料 (Stale)",
    disabled: "已停用 (Disabled)",
  };

  container.innerHTML = collectors.map(c => {
    const isDegraded = c.h === "degraded";
    const isStopped = !c.on || c.r === "stopped";
    const dotColor = colorMap[c.h] || "var(--mu)";
    const statusLabel = labelMap[c.h] || (c.on ? "Active" : "Off");

    let metricHtml = "";
    let isolatedAlert = "";

    if (c.key === "file_watcher") {
      const scheduled = (c.d.monitored_directories || []).length;
      const failed = (c.d.failed_directories || []).length;
      const restarts = (c.d.healing_history || []).length;
      metricHtml = `
        <div class="collector-diag-metric-row"><span>已排程監控目錄</span><span>${scheduled} 個</span></div>
        <div class="collector-diag-metric-row"><span>異常失效目錄</span><span>${failed} 個</span></div>
        <div class="collector-diag-metric-row"><span>自動重啟修復次數</span><span>${restarts} 次</span></div>
      `;
      if (failed > 0) {
        isolatedAlert = `<div class="collector-diag-isolated-box">⚠️ 失敗目錄: ${esc((c.d.failed_directories || []).join(", "))}</div>`;
      }
    } else if (c.key === "git_watcher") {
      const scanned = c.d.scanned_repositories_count || 0;
      const degraded = (c.d.degraded_repositories || []).length;
      const restarts = (c.d.healing_history || []).length;
      metricHtml = `
        <div class="collector-diag-metric-row"><span>已掃描正常倉庫</span><span>${scanned} 個</span></div>
        <div class="collector-diag-metric-row"><span>局部隔離損壞倉庫</span><span>${degraded} 個</span></div>
        <div class="collector-diag-metric-row"><span>自動修復線程次數</span><span>${restarts} 次</span></div>
      `;
      if (degraded > 0) {
        isolatedAlert = `<div class="collector-diag-isolated-box">⚠️ 隔離倉庫: ${esc((c.d.degraded_repositories || []).join(", "))}</div>`;
      }
    } else if (c.key === "window_watcher") {
      const probeState = c.d.state || "normal";
      const app = c.d.current_app || "None";
      const unavail = c.d.unavailable_seconds || 0;
      metricHtml = `
        <div class="collector-diag-metric-row"><span>前景探測狀態</span><span>${esc(probeState)}</span></div>
        <div class="collector-diag-metric-row"><span>當前焦點應用</span><span>${esc(app)}</span></div>
        <div class="collector-diag-metric-row"><span>不可用持續秒數</span><span>${unavail}s</span></div>
      `;
    } else if (c.key === "agent_log_watcher") {
      const restarts = (c.d.healing_history || []).length;
      const errCode = c.d.last_error_code || "None";
      metricHtml = `
        <div class="collector-diag-metric-row"><span>多 AI 來源隔離解析</span><span>Claude / Codex / Antigravity</span></div>
        <div class="collector-diag-metric-row"><span>最後錯誤代碼</span><span>${esc(errCode)}</span></div>
        <div class="collector-diag-metric-row"><span>自動修復線程次數</span><span>${restarts} 次</span></div>
      `;
    } else if (c.key === "scheduler") {
      const restarts = (c.d.healing_history || []).length;
      metricHtml = `
        <div class="collector-diag-metric-row"><span>每小時 WAL Checkpoint</span><span>已註冊 (TRUNCATE)</span></div>
        <div class="collector-diag-metric-row"><span>每日 03:30 深夜維護</span><span>已註冊 (7天輪替/90天修剪)</span></div>
        <div class="collector-diag-metric-row"><span>排程自動修復次數</span><span>${restarts} 次</span></div>
      `;
    }

    return `
      <div class="collector-diag-card ${isDegraded ? "degraded" : isStopped ? "stopped" : ""}">
        <div class="collector-diag-head">
          <span class="collector-diag-title">
            <span class="pill-dot" style="background:${dotColor}"></span>
            ${c.title}
          </span>
          <span class="trust ${c.h === "healthy" ? "ok" : c.h === "degraded" ? "noisy" : "broken"}">${statusLabel}</span>
        </div>
        <div class="collector-diag-metrics">
          ${metricHtml}
        </div>
        ${isolatedAlert}
      </div>
    `;
  }).join("");
}

async function loadLatestMaintenanceReceipt() {
  const container = $("maintenance-receipt-container");
  const badge = $("maintenance-integrity-badge");
  if (!container) return;

  try {
    const res = await getJSON("/api/v1/system/maintenance/receipt");
    if (!res.has_receipt || !res.receipt) {
      container.innerHTML = '<div class="placeholder">尚未產生維護收據。可點擊上方「執行資料庫完整維護」立即建立。</div>';
      if (badge) { badge.className = "trust noisy"; badge.textContent = "NO RECEIPT"; }
      return;
    }

    const r = res.receipt;
    const isOk = r.status === "passed" && r.integrity === "ok";
    if (badge) {
      badge.className = "trust " + (isOk ? "ok" : "broken");
      badge.textContent = isOk ? "INTEGRITY OK" : "WARNING";
    }

    const pruneCount = r.pruning ? (r.pruning.total_pruned || 0) : 0;
    const backupPath = r.backup ? r.backup.backup_path || "無" : "線上備份建立成功";
    const timeStr = r.created_at ? new Date(r.created_at).toLocaleString() : "未知";

    container.innerHTML = `
      <div class="receipt-grid">
        <div class="receipt-item">
          <div class="receipt-item-title">LAST MAINTENANCE TIME</div>
          <div class="receipt-item-val">${esc(timeStr)}</div>
        </div>
        <div class="receipt-item">
          <div class="receipt-item-title">SQLITE INTEGRITY CHECK</div>
          <div class="receipt-item-val text-success">${esc(r.integrity || "ok")}</div>
        </div>
        <div class="receipt-item">
          <div class="receipt-item-title">HISTORICAL EVENTS PRUNED</div>
          <div class="receipt-item-val">${pruneCount} 筆記錄</div>
        </div>
        <div class="receipt-item">
          <div class="receipt-item-title">BACKUP VAULT ROTATION</div>
          <div class="receipt-item-val" title="${esc(backupPath)}">保留最新 7 份</div>
        </div>
      </div>
    `;
  } catch (e) {
    container.innerHTML = `<div class="placeholder">載入維護收據失敗: ${esc(e.message)}</div>`;
  }
}

async function triggerSystemHeal() {
  const btn = $("btn-trigger-heal-ui");
  const orig = btn ? btn.innerHTML : "";
  if (btn) { btn.disabled = true; btn.innerHTML = "⏳ 正在巡檢修復中…"; }
  appendSystemConsole("觸發全域採集器自我修復巡檢 (POST /api/v1/system/heal)...", "正在檢測並修復掛掉的 Observer 與工作線程...");
  try {
    const res = await postJSON("/api/v1/system/heal");
    appendSystemConsole("✅ 自我修復巡檢完成收據", res);
    loadSystemHealth();
    refreshStatus();
  } catch (e) {
    appendSystemConsole("❌ 自我修復巡檢失敗", e.message);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = orig;
    }
  }
}

async function triggerSystemWalCheckpoint() {
  const btn = $("btn-trigger-wal-ui");
  const orig = btn ? btn.innerHTML : "";
  if (btn) { btn.disabled = true; btn.innerHTML = "⏳ 截斷 WAL 中…"; }
  appendSystemConsole("執行 SQLite WAL Checkpoint (POST /api/v1/system/wal-checkpoint)...", { mode: "TRUNCATE" });
  try {
    const res = await postJSON("/api/v1/system/wal-checkpoint", { mode: "TRUNCATE" });
    appendSystemConsole("✅ WAL Checkpoint 執行結果", res);
    loadSystemHealth();
  } catch (e) {
    appendSystemConsole("❌ WAL Checkpoint 失敗", e.message);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = orig;
    }
  }
}

async function triggerSystemMaintenance() {
  const btn = $("btn-trigger-maintain-ui");
  const orig = btn ? btn.innerHTML : "";
  if (btn) { btn.disabled = true; btn.innerHTML = "⏳ 正在執行維護…"; }
  appendSystemConsole("執行資料庫完整健康維護 (POST /api/v1/system/maintenance)...", {
    max_backups: 7,
    retention_days: 90,
    dry_run: false
  });
  try {
    const res = await postJSON("/api/v1/system/maintenance", {
      max_backups: 7,
      retention_days: 90,
      dry_run: false
    });
    appendSystemConsole("✅ 資料庫完整維護收據 (Latest Maintenance Receipt)", res);
    loadSystemHealth();
  } catch (e) {
    appendSystemConsole("❌ 資料庫維護失敗", e.message);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = orig;
    }
  }
}

