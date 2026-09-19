"""驗收清單本體：每一項的規格，加上「查到什麼就算什麼」的階梯（ADR-028，TODO D12）。

這張表是 `docs/TODO.md` A 段的可執行副本。一列就是一項驗收，看一眼就能回答三個問題：

- **怎麼做**（``how``）、**完成判準是什麼**（``criterion``）——給人看的規格。
- **去查什麼**（``read``）——`readings.py` 裡的那個函式。
- **查到什麼就算什麼**（``rules``）——由上往下，第一條成立的說了算。
  **順序就是語意**：後面那條之所以到得了，正是因為前面那些都不成立。

敘述（第三欄）可以是固定字串，或一個吃 :class:`Reading` 的函式；字面一個字都不能改，
它們會直接出現在 CLI 與儀表板上。
"""

from __future__ import annotations

from typing import Any

from . import readings as r
from .rules import (
    NEEDS_HUMAN, NOT_CONFIGURED, OTHERWISE, PARTIAL, PASSED, PENDING, RUNTIME_ONLY,
    Ladder, all_of, fact, has, missing, no_fact, not_,
)

ITEMS: tuple[dict[str, Any], ...] = (
    {
        "id": "A1",
        "title": "全天 coverage ledger",
        "priority": "P0",
        "blocks_release": True,
        "how": "讓實機跨午夜連續運行一整天",
        "criterion": "任一**已結束**的日子 ledger coverage 達門檻（今天的比例不算，分母只到現在）",
        "probe": Ladder(r.a1_coverage, (
            (fact("met"), PASSED,
             lambda x: f"{x.facts['met'][0]['date']}（完整的一天）ledger coverage 達門檻（{x.facts['met'][0]['coverage_ratio']:.2%}）。"),
            (no_fact("observed"), PENDING,
             lambda x: "近 7 個完整日沒有任何 coverage interval——服務還沒在這台機器跨午夜連續運行過。"
                       + x.facts["today_note"]),
            (OTHERWISE, PENDING,
             lambda x: f"最好的完整日是 {x.facts['best']['date']}（{x.facts['best']['coverage_ratio']:.2%}），"
                       f"還沒達到 {x.facts['threshold']:.0%} 門檻。" + x.facts["today_note"]),
        )),
    },
    {
        "id": "A2",
        "title": "RAG 雲端 provider 複測",
        "priority": "P0",
        "blocks_release": True,
        "how": "在小秘書／知識庫分頁選 Gemini（或 OpenAI／Claude）問一題",
        "criterion": "rag_chat_messages 有一筆雲端 provider 的非錯誤回答",
        "probe": Ladder(r.a2_cloud_provider, (
            (has("cloud_replies"), PASSED,
             lambda x: f"最近一次雲端回答：{x.evidence['latest_ok']['provider']}（{x.evidence['latest_ok']['created_at']}）。"),
            (has("cloud_error_replies"), PARTIAL,
             "有雲端對話紀錄，但存下來的都是錯誤訊息——金鑰、網路或逾時三者之一。"),
            (OTHERWISE, PENDING, "還沒有任何以雲端 provider 產生的回答紀錄。"),
        )),
    },
    {
        "id": "A3",
        "title": "Telegram 設定 + inline 批准",
        "priority": "P1",
        "blocks_release": False,
        "how": "設定 Telegram → 開 inline 批准 → 解鎖 → 實批一次 L1 動作",
        "criterion": "有 approved_via=telegram_inline 的成功 receipt",
        "probe": Ladder(r.a3_telegram_inline, (
            (has("succeeded"), PASSED,
             lambda x: f"已有 {x.evidence['succeeded']} 筆 approved_via=telegram_inline 的成功收據。"),
            (has("telegram_inline_receipts"), PARTIAL,
             "有 telegram_inline 收據但沒有成功的；看收據的 error_code。"),
            (missing("approvals_enabled"), NOT_CONFIGURED,
             "inline 批准預設關閉（需執行器與批准通道兩個開關都開）。"),
            (OTHERWISE, PENDING, "批准通道已開，還沒批過任何一筆。"),
        )),
    },
    {
        "id": "A4",
        "title": "L2 執行器實機試用",
        "priority": "P1",
        "blocks_release": False,
        "how": "開三個執行器開關，實跑 draft →（可選）confirm → apply",
        "criterion": "agent_draft_plan 有 succeeded receipt",
        "probe": Ladder(r.a4_l2_executor, (
            (has("draft_succeeded"), PASSED,
             lambda x: f"agent_draft_plan 已有 {x.evidence['draft_succeeded']} 筆成功收據。"),
            (has("draft_receipts"), PARTIAL, "有 draft 收據但都不是 succeeded；看 error_code。"),
            (not_(all_of(has("executor_enabled"), has("l2_enabled"))), NOT_CONFIGURED,
             "L2 預設關閉（executor 與 l2 兩個開關都要開）。"),
            (OTHERWISE, PENDING, "L2 已開，還沒跑過 draft。"),
        )),
    },
    {
        "id": "A5",
        "title": "P4.3 對帳實操",
        "priority": "P1",
        "blocks_release": False,
        "how": "Git 同步中心 → 掃描對帳 → 各實跑一種動作（init／attach／clone）",
        "criterion": "三類分類符合預期、拒絕條件如實擋下（人眼確認）",
        "probe": Ladder(r.a5_reconciliation, (
            (OTHERWISE, NEEDS_HUMAN,
             "onboarding 動作不留本機收據（TODO B4），對帳掃描也不在唯讀便宜查詢範圍內。"),
        )),
    },
    {
        "id": "A6",
        "title": "檢索 worker 大索引實測",
        "priority": "P1",
        "blocks_release": False,
        "how": "啟動服務，等檢索 worker 卡片變「就緒」，再問一題",
        "criterion": "worker state=ready 且載入計數與實際索引一致",
        "probe": Ladder(r.a6_retrieval_worker, (
            (fact("runtime_only"), RUNTIME_ONLY,
             "worker 狀態是服務程序內的記憶體狀態；請在儀表板的驗收中心看這一項。"),
            (fact("extra_absent"), NOT_CONFIGURED,
             lambda x: "知識庫的選用依賴未安裝（缺 " + x.facts["missing_text"]
                       + "）；要用檢索 worker 請先 " + x.facts["install_hint"] + "。"),
            (lambda x: x.facts["worker"].get("state") == "ready" and x.facts["chunks"] > 0
                       and x.facts["source_chunks"]
                       and (x.evidence["vector_chunks"] or 0) < x.facts["source_chunks"],
             PARTIAL,
             lambda x: f"worker 載入的是舊索引：記憶體裡 vector={x.evidence['vector_chunks']}，"
                       f"但索引現在有 {x.facts['source_chunks']} 個 chunk。索引重建後要重新預熱"
                       "（POST /api/v1/rag/retrieval/warmup；舊版若沒重載請先 shutdown）。"),
            (lambda x: x.facts["worker"].get("state") == "ready" and x.facts["chunks"] > 0, PASSED,
             lambda x: f"worker 就緒，已載入 bm25={x.evidence['bm25_chunks']}／vector={x.evidence['vector_chunks']}。"),
            (missing("index_present"), NOT_CONFIGURED, "本機還沒有索引，沒有可預熱的東西。"),
            (has("last_error"), PARTIAL, lambda x: f"預熱留下錯誤：{x.evidence['last_error']}"),
            # index_present() 只看檔案／目錄存不存在，不看有幾個 chunk。所以「索引目錄在、
            # 但裡面是空的」會走到這裡——此時預熱其實已經完成，叫使用者「等預熱」是指錯方向
            # （2026-09-07 實機：chroma 開了 2.5 秒、embedding 模型載好、chunks 仍是 0）。
            (all_of(lambda x: x.evidence["state"] == "ready", has("warmup_at")), NOT_CONFIGURED,
             "worker 已預熱完成，但索引裡是 0 個 chunk——索引目錄存在不代表有內容。"
             "請先到「02 知識庫」加資料夾並建索引，再回來看這一項。"),
            (OTHERWISE, PENDING,
             lambda x: f"worker 目前是 {x.evidence['state']}；預熱完成後這裡會顯示載入計數。"),
        )),
    },
    {
        "id": "A7",
        "title": "Repo 同步全覽與批次實操",
        "priority": "P1",
        "blocks_release": False,
        "how": "載入全覽 → 全部 Fetch → 批次 Pull；另跑一次 repo_sync_report",
        "criterion": "reports/repo_sync 有報告，且 repo_pull_ff 有成功 receipt",
        "probe": Ladder(r.a7_repo_sync, (
            (all_of(fact("reports_count"), fact("pull_ok")), PASSED,
             lambda x: f"已有 {x.facts['reports_count']} 份同步報告，且 repo_pull_ff 有 {x.facts['pull_ok']} 筆成功收據。"),
            (lambda x: x.facts["reports_count"] or x.facts["report_receipts"] or x.facts["snapshot"],
             PARTIAL, "同步報告已產生，但還沒有批准後的 repo_pull_ff 成功收據。"),
            (OTHERWISE, PENDING, "還沒跑過 repo_sync_report，也沒有同步快照。"),
        )),
    },
    {
        "id": "A8",
        "title": "小秘書每日包實機收據",
        "priority": "P1",
        "blocks_release": False,
        "how": "建立每日排程，或對 morning_pack 按立即執行",
        "criterion": "morning_pack 與 handoff_active_projects 都有 succeeded receipt",
        "probe": Ladder(r.a8_daily_packs, (
            (fact("both"), PASSED,
             lambda x: f"morning_pack 與 handoff_active_projects 都有成功收據（最近一次 {x.facts['latest_success_at']}）。"),
            (fact("any"), PARTIAL, "兩個 L0 動作只跑成功了一個；缺的那個看收據 errors。"),
            (OTHERWISE, PENDING, "還沒建立或執行過每日排程。"),
        )),
    },
    {
        "id": "A9",
        "title": "小秘書記憶區實機收據",
        "priority": "P1",
        "blocks_release": False,
        "how": "對話框「記下來：…」「偏好：…」→ 跑一次早晨包 → 刪一則觀察",
        "criterion": "記憶區同時有 user_note、preference 與 observation",
        "probe": Ladder(r.a9_memory_area, (
            (fact("complete"), PASSED, "筆記、偏好與秘書觀察三種都存在——一輪記憶區流程走完了。"),
            (fact("have"), PARTIAL,
             lambda x: f"還缺：{'、'.join(x.facts['missing'])}（observation 由早晨包這類 L0 收據產生）。"),
            (OTHERWISE, PENDING, "記憶區還是空的。"),
        )),
    },
    {
        "id": "A10",
        "title": "手機 Telegram 對話實機收據",
        "priority": "P1",
        "blocks_release": False,
        "how": "啟用小秘書對話 → 在手機送 /today、記下來與一句提問",
        "criterion": "記憶區出現 source=telegram 的筆記",
        "probe": Ladder(r.a10_telegram_chat, (
            (has("notes_from_telegram"), PASSED,
             lambda x: f"記憶區有 {x.evidence['notes_from_telegram']} 筆來自 Telegram 的筆記——手機那條管線真的通了。"),
            (missing("chat_enabled"), NOT_CONFIGURED, "Telegram 對話預設關閉（通知與對話兩個開關都要開）。"),
            (OTHERWISE, PENDING, "對話已開啟，但還沒有從手機寫進來的筆記。"),
        )),
    },
    {
        "id": "A11",
        "title": "LINE 推播實機收據",
        "priority": "P1",
        "blocks_release": False,
        "how": "設定 LINE channel → 測試並儲存啟用 → 推一則晨報",
        "criterion": "手機收到純文字晨報（人眼確認）；push_ready 含 line",
        "probe": Ladder(r.a11_line_push, (
            (no_fact("ready"), NOT_CONFIGURED, "LINE 尚未設定或未啟用（push_ready 沒有 line）。"),
            # 設定齊備只證明「送得出去」；「手機上收到且是純文字」只有你看得到。
            (OTHERWISE, NEEDS_HUMAN, "LINE 已就緒；推一則晨報後由你確認手機收到、且沒有裸 HTML 標籤。"),
        )),
    },
    {
        "id": "A12",
        "title": "小秘書問候卡實機收據",
        "priority": "P2",
        "blocks_release": False,
        "how": "填問候稱呼 → 回 01 分頁看「小秘書的話」→ 切近 2 小時視窗",
        "criterion": "卡上每個數字都能在其他分頁對得上（人眼確認）",
        "probe": Ladder(r.a12_greeting_card, (
            (OTHERWISE, NEEDS_HUMAN, "問候卡的數字要由你對照 03／04 分頁核對；這裡只回報設定狀態。"),
        )),
    },
    {
        "id": "A13",
        "title": "本機行事曆實機收據",
        "priority": "P1",
        "blocks_release": False,
        "how": "加入 .ics 路徑 → 儲存並套用 → 看系統健康與 01 首頁",
        "criterion": "行事曆來源運作中、視野內有行程且沒有來源錯誤",
        "probe": Ladder(r.a13_calendar, (
            (no_fact("paths"), NOT_CONFIGURED, "沒有設定任何 .ics 路徑——行事曆等於停用。"),
            (has("degraded_sources_count"), PARTIAL,
             lambda x: f"有 {x.evidence['degraded_sources_count']} 個來源檔解析失敗；看 degraded_sources 的 error。"),
            (has("events"), PASSED,
             lambda x: f"{x.evidence['source_files']} 個來源檔、視野內 {x.evidence['events']} 筆行程已入庫。"),
            (OTHERWISE, PENDING, "路徑已設定但還沒採到任何行程；等下一次掃描或檢查檔案內容。"),
        )),
    },
    {
        "id": "A14",
        "title": "同步中心 pull/push 修正複測",
        "priority": "P1",
        "blocks_release": False,
        "how": "Git 同步中心 → 載入全覽 → 全部 Fetch → 對有 .lock 但落後遠端的 repo 按 Pull",
        "criterion": "該 repo 可 pull 且本機 .lock／build 檔不受影響；不能 pull 者顯示帶數字的具體理由（人眼確認）",
        "probe": Ladder(r.a14_sync_center_pull, (
            (OTHERWISE, NEEDS_HUMAN, "要按下 Pull 才知道；驗收中心不跑 git，也不替你按（見 ADR-016 D1）。"),
        )),
    },
    {
        "id": "A15",
        "title": "每日工作誌實機收據",
        "priority": "P1",
        "blocks_release": False,
        "how": "排程任務新增 daily_digest 或按立即執行，隔天看 01 記憶區並問「我昨天做了什麼」",
        "criterion": "記憶區有至少兩天的工作誌（同一天重跑不重複）",
        "probe": Ladder(r.a15_daily_digest, (
            (lambda x: len(x.facts["days"]) >= 2, PASSED,
             lambda x: f"記憶區已有 {len(x.facts['days'])} 天的工作誌（{x.facts['days'][0]} ～ {x.facts['days'][-1]}）。"),
            (fact("days"), PARTIAL,
             lambda x: f"只有 {x.facts['days'][0]} 一天的工作誌；連跑幾天才看得出去重與累積。"),
            (missing("enabled"), NOT_CONFIGURED, "每日工作誌已關閉。"),
            (OTHERWISE, PENDING, "還沒跑過 daily_digest。"),
        )),
    },
    {
        "id": "A16",
        "title": "模式感知提案實機收據",
        "priority": "P1",
        "blocks_release": False,
        "how": "連續使用幾天後看 01 的秘書提案：沒有每日排程／被冷落的專案／主線加權",
        "criterion": "提案裡的天數與專案符合你的印象；建好排程後 no_daily_routine 消失（人眼確認）",
        "probe": Ladder(r.a16_pattern_proposals, (
            (missing("enabled"), NOT_CONFIGURED, "模式感知提案已關閉。"),
            (no_fact("active_days"), PENDING,
             "近一週（不含今天）沒有任何活動紀錄，模式層沒有東西可算；用幾天再看。"),
            (OTHERWISE, NEEDS_HUMAN,
             lambda x: f"模式層算出 {x.facts['signals']} 筆提案、近一週 {x.facts['active_days']} 天有活動；"
                       "天數與「被冷落」的專案是否符合你的印象，要由你確認。"),
        )),
    },
    {
        "id": "A17",
        "title": "宣告式個人檔案實機收據",
        "priority": "P1",
        "blocks_release": False,
        "how": "打「偏好：優先：<專案>」「偏好：語氣：簡潔」→ 看 01 提案排序、問候卡與記憶區頂端的個人檔案列",
        "criterion": "優先專案的提案排前面並附理由；問候只少鼓勵語、標題與數字不變；刪掉筆記就恢復（人眼確認）",
        "probe": Ladder(r.a17_declared_profile, (
            (no_fact("declared"), PENDING,
             "還沒有任何「優先：」或「語氣：」宣告；在對話框打「偏好：優先：<專案>」或「偏好：語氣：簡潔」再看。"),
            (OTHERWISE, NEEDS_HUMAN,
             lambda x: "已宣告 " + "／".join(x.facts["parts"])
                       + "；優先專案的提案是否排前面、問候是否只改措辭不改數字，要由你確認。"),
        )),
    },
    {
        "id": "A18",
        "title": "秘書桌面（01 首頁）實機收據",
        "priority": "P1",
        "blocks_release": False,
        "how": "用 01 幾天：看桌面的焦點與「記得」、按詳情 chip 展開或跳分頁、看底下「今天離開首頁 N 次」",
        "criterion": "焦點與記得是你會挑的；離開首頁的次數幾天後變少；詳情面板展開狀態會記住（人眼確認）",
        "probe": Ladder(r.a18_secretary_home, (
            (fact("errors"), PARTIAL,
             lambda x: "桌面有一節讀不到："
                       + "、".join(f"{k}={v}" for k, v in x.facts["errors"].items())
                       + "；其他節照出。"),
            (lambda x: not x.facts["has_focus"] and not x.facts["has_note"], PENDING,
             "桌面還沒有東西可挑（沒有提案、也沒有筆記）；用幾天、記幾則再看。"),
            (OTHERWISE, NEEDS_HUMAN,
             lambda x: (f"桌面挑出焦點「{x.facts['focus_title']}」" if x.facts["has_focus"] else "桌面目前沒有焦點提案")
                       + (f"、記得一則（{x.facts['why_this']}）" if x.facts["has_note"] else "、沒有可挑的記憶")
                       + "；是不是你會挑的、以及一天離開首頁幾次，要由你確認。"),
        )),
    },
    {
        "id": "A19",
        "title": "每週回顧（說的 vs 做的）實機收據",
        "priority": "P1",
        "blocks_release": False,
        "how": "宣告本期優先後用一週；下週一看記憶區的「W## 回顧」與 01 桌面有沒有「你說 X 優先，上週它只有 N 天」",
        "criterion": "回顧的天數與你的印象相符；偏移提案只在真的沒做時出現，且改宣告或排時間後消失（人眼確認）",
        "probe": Ladder(r.a19_weekly_review, (
            (fact("disabled"), NOT_CONFIGURED, "每週回顧已關閉。"),
            (no_fact("weeks"), PENDING,
             "還沒有任何一週的回顧；讓早晨包多跑一天（它會補上週的），或對 weekly_review 排程按立即執行。"),
            (OTHERWISE, NEEDS_HUMAN,
             lambda x: f"已寫 {x.facts['weeks']} 週的回顧（最近 {x.facts['latest']}）；{x.facts['said']}。"
                       "天數與你的印象是否相符、偏移提案是否合理，要由你確認。"),
        )),
    },
    {
        "id": "A20",
        "title": "文件落後偵測與文件更新 L2 實機收據",
        "priority": "P1",
        "blocks_release": False,
        "how": "開 L2 三個開關 → 01 看「X 的文件落後了」→ 起草文件更新計畫 → 讀過再批准實際改檔 → 自己 review 後 commit",
        "criterion": "落後的 commit 數與你的印象相符；起草的計畫沒有編造進度；改檔後 git diff 只動文件且沒有被 commit（人眼確認）",
        "probe": Ladder(r.a20_docs_freshness, (
            (fact("disabled"), NOT_CONFIGURED, "文件落後偵測已關閉。"),
            (no_fact("any_repo"), PENDING, "近期沒有任何有文件異動紀錄的 repo，文件層沒有東西可比；用幾天再看。"),
            (no_fact("signals"), PENDING,
             lambda x: f"有 {x.facts['repos']} 個 repo 在比對範圍內，但都還沒超過門檻"
                       f"（{x.facts['min_commits']} 個 commit／{x.facts['min_days']} 天）——文件目前跟得上。"),
            (no_fact("gates_open"), PARTIAL,
             lambda x: f"已提出 {x.facts['signals']} 張文件落後提案，但 L2 寫入的三道門沒全開"
                       f"（executor={x.evidence['executor_enabled']}、l2={x.evidence['l2_enabled']}、"
                       f"l2_write={x.evidence['l2_write_enabled']}），只能看提案不能請秘書改檔。"),
            (OTHERWISE, NEEDS_HUMAN,
             lambda x: f"已提出 {x.facts['signals']} 張文件落後提案且 L2 寫入三道門全開；"
                       "起草的計畫是否值得批准、改出來的文件對不對，要由你讀過再 commit。"),
        )),
    },
    {
        "id": "A21",
        "title": "Chroma 空間回收",
        "priority": "P2",
        "blocks_release": False,
        "how": "02 知識庫 →「🧹 回收 Chroma 空間」（大目錄可能要一兩分鐘；回收後按預熱重載）",
        "criterion": "worker 收據顯示回收前後的位元組數與刪掉的孤兒片段數；現有索引仍可檢索（重新預熱後計數不變）",
        "probe": Ladder(r.a21_chroma_compaction, (
            (fact("running"), PENDING,
             lambda x: f"回收正在進行中（{x.facts['running']}）。大目錄光 VACUUM 就要一兩分鐘，"
                       "等它跑完再看這一項；不用再按一次（同時只能有一個索引工作）。"),
            (fact("never_ran"), PENDING,
             "還沒跑過 Chroma 空間回收。索引重建過幾輪之後，舊片段目錄與 SQLite 空頁"
             "會一直留著（刪 collection 不會讓磁碟變小）——到「02 知識庫」按"
             "「🧹 回收 Chroma 空間」，這裡就會有收據。"),
            (fact("incomplete"), PARTIAL,
             lambda x: f"最近一次 Chroma 回收沒有完成（{x.facts['job_status']}）；再跑一次或看工作訊息。"),
            (has("failed_dirs"), PARTIAL,
             lambda x: f"有 {len(x.evidence['failed_dirs'])} 個孤兒片段刪不掉（多半是檔案還被開著）。"
                       "先按「💤 釋放記憶體」讓檢索 worker 放手，再回收一次。"),
            (lambda x: x.facts["reclaimed"] <= 0, PASSED,
             "已跑過回收，當時沒有可回收的空間——目錄是乾淨的（這也是有效收據）。"),
            (OTHERWISE, PASSED,
             lambda x: f"已回收 {x.facts['reclaimed']:,} bytes（{x.evidence['before_bytes']:,} → {x.evidence['after_bytes']:,}），"
                       f"刪掉 {x.evidence['removed_dirs']} 個不被引用的片段目錄。"),
        )),
    },
    {
        "id": "A22",
        "title": "會議秘書（會後逐字稿）實機收據",
        "priority": "P2",
        "blocks_release": False,
        "how": "設 meetings.transcript_dir → 會後把逐字稿放進去 → 執行 meeting_notes → 01 的「會議紀錄」卡點一條「加入未結事項」",
        "criterion": "摘要沒有編造你沒說過的事；配對到的會議標題正確（或如實寫未配對）；點過的才出現在未結事項（人眼確認）",
        "probe": Ladder(r.a22_meeting_secretary, (
            (fact("disabled"), NOT_CONFIGURED,
             "還沒設 meetings.transcript_dir——設一個資料夾、把 Teams 匯出的逐字稿放進去，這一項才有東西可查。"),
            (lambda x: not x.facts["files"] and not x.facts["notes"], PENDING,
             lambda x: f"資料夾已設（{x.facts['path']}）但裡面沒有逐字稿；開了轉錄的會議結束後把檔案放進去再看。"),
            (no_fact("notes"), PENDING,
             lambda x: f"找到 {x.facts['files']} 份逐字稿，但還沒整理過——到「排程任務」對 meeting_notes 按立即執行（或排一個時間）。"),
            (OTHERWISE, NEEDS_HUMAN,
             lambda x: f"已整理 {x.facts['notes']} 場會議（{x.facts['files']} 份逐字稿在資料夾裡）；"
                       f"候選待辦已加入 {x.evidence['followups_accepted']} 條、還有 {x.evidence['followups_pending']} 條沒處理。"
                       "摘要有沒有編造、配對到的會議對不對，要你親眼看過。"),
        )),
    },
)

ITEM_IDS: tuple[str, ...] = tuple(item["id"] for item in ITEMS)
