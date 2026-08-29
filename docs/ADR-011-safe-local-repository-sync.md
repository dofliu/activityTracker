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
