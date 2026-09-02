# ADR-011：受控本機 Repository 同步

## Context

既有 GitHub Integration 僅同步 GitHub API 的 repository／PR metadata；它不會檢查或改變本機 Git worktree，也不會執行 `fetch`、`pull`、`commit` 或 `push`。把兩種功能混為同一個「同步」按鈕，會讓使用者誤判本機是否已與遠端一致。

本機 Git 動作可能覆蓋 worktree、觸發 hooks、送出 commit 或造成遠端歷史變更，因此不能由排程或 Open Loop 自動執行。

## Decision

新增 Local Git Sync Center，並採下列契約：

1. repository 只從 `watchers.git_watcher.repositories` 設定的 roots 遞迴探索；API 接收的是 canonical `repo_id`，不接受瀏覽器傳入任意本機 path。
2. 載入狀態完全唯讀；ahead/behind 只比較本機的 `remote-tracking ref`，介面明確標示為 cached，不會自動連網或 fetch。
3. `fetch --prune`、`pull --ff-only`、`commit -m`、`push` 都必須由使用者在單一 repo 卡片中確認，並在執行前重新檢查前置條件。
4. Pull 僅在 clean worktree、只落後遠端且沒有分歧時啟用；Push 僅在 clean worktree、只領先遠端且沒有分歧時啟用；force push 永不提供。
5. Commit 只允許已 staged 的檔案，必須輸入 commit message，系統絕不執行 `git add` 或批次 commit。
6. 指令一律以 argv 形式執行、禁止 shell、停用互動式 credential prompt、對輸出設長度與 secret 遮蔽上限，並對每個 repo 加鎖避免並行操作。

## Alternatives considered

- **自動背景雙向同步**：拒絕。它無法安全解決 dirty worktree、divergence、credential prompt、Git hooks 與 commit 意圖。
- **只做 GitHub Cloud Sync**：保留，但不能解決本機 branch 與遠端 commit 不一致問題。
- **讓瀏覽器傳入 path 直接執行 Git**：拒絕。會把 local-only dashboard 變成任意目錄 command launcher。

## Consequences

- 使用者能在同一儀表板看見 repository 的 branch、upstream、ahead/behind、staged／unstaged／untracked／conflict 狀態並逐一同步。
- 初次顯示可能與真正遠端有時間差；按 Fetch 後才取得該 repo 最新遠端參照。
- 此功能不處理 merge conflict、rebase、檔案 staging、force push、credential 設定或排程自動同步；這些保留在使用者的 Git/IDE 工作流程中。

## 2026-09-01 Addendum：P4.3 Repo Onboarding／Reconciliation（FEATURE-009）

`core/repo_onboarding.py` 在同一同步中心之下補齊三種「尚未進入同步範圍」的情境，契約疊加在上述決策之上：

1. **對帳（唯讀）**：`GET /api/v1/repos/onboarding-report` 列出（a）root 第一層尚未 `git init` 的一般資料夾、（b）沒有任何 remote 的本機 repo、（c）已同步 GitHub metadata 中「本機沒有對應 clone」的 repo。**已 clone 與否只以 remote URL 正規化比對（https／ssh／`.git` 變體歸一）為準；名稱相同只作為 `name_match_hint` 顯示，永不自動配對。**
2. **四個確認式動作**（`POST /api/v1/repos/onboarding-action`；schema `extra=forbid`＋`confirmation: "confirmed"`，一次一個目標）：
   - `init_folder`：對掃描到的資料夾 `git init`——只建立空 `.git`，不 commit、不設 remote、不發布。
   - `attach_remote`：把**已同步清單內**的 GitHub repo 設為無 remote repo 的 origin（呼叫端無法注入任意 URL）；不 fetch、不 push。已有任何 remote 的 repo 一律拒絕。
   - `clone_repo`：clone 到使用者選定的設定 root 之下；目的地路徑已存在（含空目錄）一律拒絕，絕不覆寫；一律使用 https URL 且不夾帶 token（私有 repo 由使用者本機 credential manager 認證，失敗如實回報）。
   - `create_remote`：為無 remote 的 repo 建立 GitHub repo（**預設 private**）並 `remote add`；遠端保持空 repo，**永不代為 push**——首次發布由使用者自行 `git push -u`。
3. 延續既有邊界：目標一律以 canonical-path hash id 引用（不接受瀏覽器傳入路徑）、單一目標 lock、argv 禁 shell、`GIT_TERMINAL_PROMPT=0`、輸出長度與 secret 遮蔽、永不 force。

## 2026-09-02 Addendum B：全覽、批次與小秘書同步報告

使用者要的是「一張表看到所有 repo 是否同步、需要 pull 還是 push，然後一一或批次處理，並讓小秘書每天幫忙確認」。原決策只提供近期 N 個 repo 的卡片與逐一動作，且明文拒絕「自動背景雙向同步」。這份 Addendum 在**不放寬任何單一動作前置條件**的前提下補齊全覽與批次，並把小秘書的參與限制在「唯讀報告＋批准式執行」：

1. **全覽（唯讀）**：`GET /api/v1/repos/sync-status?scope=all` 回傳設定範圍內**全部** repo 的狀態（仍是 cached remote-tracking ref、不連網），每筆附 `last_fetch_at`（`FETCH_HEAD` 修改時間；從未 fetch 為 null）與 `summary` 計數，讓「需要 pull／push」的判斷附帶它所依據的時間點。
2. **全部 Fetch**：`POST /api/v1/repos/sync-fetch-all` 對全部有 remote 的 repo 執行 `fetch --prune`。這是唯一允許「一鍵全部、不列清單」的動作，因為它只更新 remote-tracking refs，不改 worktree、本機 branch 或遠端；正在執行其他動作的 repo 跳過並如實列出。
3. **批次 Pull／Push 採「清單確認」而非「全面執行」**：`GET /api/v1/repos/sync-batch-plan?action=…` 先列出**目前**符合該動作前置條件的 repo（與被排除者的原因）；使用者確認的是這份清單，`POST /api/v1/repos/sync-batch` 只接受清單內的 `repo_id`（`extra=forbid`、16 hex、單次上限 50）。執行為逐一（非並行），每個 repo 都走既有 `execute()` 的 lock 與前置條件重檢——執行當下不符者跳過，永不 force，也不會為了「讓批次成功」放寬條件。
4. **批次 Push 有獨立開關 `repository_sync.batch.allow_push`，預設關閉**；關閉時 plan／execute 回 409，單一 repo 的手動 Push 不受影響。
5. **小秘書只做報告與批准式執行**：新增 L0 排程 template `repo_sync_report`（`core/repo_sync_report.py`）——掃描全部 repo 的 cached 狀態、寫 markdown 報告與 JSON 快照（`reports/repo_sync/`），**不 fetch、不連網**；`build_action_proposals` 只讀新鮮快照（預設 36 小時內）產生 `repo_needs_pull`／`repo_needs_push`／`repo_diverged` 提案。`repo_needs_pull` 對應新的 **L1** template `repo_pull_ff`（批准後才執行，仍走 `execute()` 重檢），`repo_needs_push` 只提供 `repo_fetch`（push 留在同步中心確認），`repo_diverged` 不對應任何 Git 寫入。依 ADR-008，L1 永不可排程；「每天自動 pull」仍然不存在。
6. 「無自動同步」的措辭調整為「**無排程自動同步**」：所有會改動 worktree 或遠端的動作仍需使用者在場確認，差別只在確認的粒度可以是一份清單。

不變的邊界：repo 只從設定 root 探索、API 只認 `repo_id`、argv 禁 shell、`GIT_TERMINAL_PROMPT=0`、輸出長度與 secret 遮蔽、每 repo lock、永不 force、永不 `git add`、不處理 merge conflict／rebase。

