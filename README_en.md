# 🌐 OmniContext — Personal Context Intelligence & Workstream Hub

[![Language](https://img.shields.io/badge/Language-English%20%7C%20%E7%B9%81%E9%AB%94%E4%B8%AD%E6%96%87-orange)](#-language)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-green)](https://fastapi.tiangolo.com/)

> **[English Documentation](README_en.md) | [繁體中文說明文件](README.md)**

> **Current status: Personal Alpha (v1.3.0a5 published as a GitHub pre-release).** Windows milestone WinRT Toast E2E, schema 17/17, formal package+database rollback, P3-2 through P3-5 Context Memory Alpha, collector runtime diagnostics, the P2.6 continuous coverage ledger, and the cross-platform CI matrix have passed; Extension 1.3.1 obtained a live PASS receipt for ChatGPT and Claude.ai on 2026-08-31 and the P2.7 background-task live acceptance passed for all three platforms. **The secretary has completed all [ADR-008](docs/ADR-008-gated-agent-executor.md) stages P5-R1 through R5** (LLM notes, L0/L1 whitelist actions, L2 dispatch of your local agent CLI to draft plans / apply an approved plan, Telegram inline approvals with an evening handoff push, and L0 read-only custom scheduled tasks with weekly/monthly rollups and a STATUS draft — all off by default), plus the morning briefing (P5-R4a), two-tier incremental summaries, and the assistant-home UI. P4.3 repo onboarding/reconciliation (confirmed single-target init / attach-remote / clone / create-GitHub-repo flows) has landed, and the dashboard went through an information-architecture pass (6 tabs split into primary/secondary, set-once settings collapsed) plus optional color palettes (Naruto Orange / Forest Green / Ocean Blue x dark/light). 44 contract-test modules, 261 tests. Remaining gap: a real full-day coverage-ledger receipt plus the user's live on-machine acceptance receipts (release_ready stays false); the full backlog lives in [docs/TODO.md](docs/TODO.md).

**Documentation:** [📚 Documentation index](docs/INDEX.md) · [Traditional Chinese usage guide](docs/USAGE.md) · [Roadmap](ROADMAP.md) · [Current status](STATUS.yaml) · [Test strategy](docs/TEST_STRATEGY.md)

![OmniContext architecture and future roadmap](docs/assets/omnicontext-architecture-roadmap-card-v1.png)

**OmniContext** is a **local-first, privacy-focused** personal context intelligence and activity tracking hub. It automatically captures your cross-platform AI interactions (Claude Code, Codex, Antigravity, ChatGPT, Gemini, etc.), code commits, paper and file modifications, window time allocation, and deeply integrates with your GitHub repositories and Pull Request (PR) statuses.

Unlike provider-specific memory or chat import, **OmniContext keeps canonical context with the user and project rather than with one AI provider.** It combines multi-AI activity with local repositories, branches/commits, file changes, IDE/terminal and foreground activity, and Open Loops, then produces a provider-neutral Context Handoff. See [Product Positioning](docs/PRODUCT_POSITIONING.md) for the current capability and evidence boundaries.

It is purpose-built to answer three fundamental questions at any moment:
1. **"What projects and workstreams am I actively working on?"**
2. **"Where did I leave off in my last work session, and what files did I touch?"**
3. **"What open loops and unresolved tasks require my attention?"**

---

## 🌟 Key Features

```
┌──────────────────────────────────────────────────────────────────────────┐
│                      OmniContext Architecture Overview                   │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  [ Cross-Platform AI ]    [ Local Files / Git ]   [ GitHub Cloud Intel ] │
│  • Claude Code Logs       • Watchdog File Events  • 48+ Public & Private │
│  • Codex Sessions         • Recursive Git Scanner • PR States & Branches │
│  • Antigravity Brain      • Canonical Project     • Actions CI & Reviews │
│  • Chrome Extension (MV3)   Resolver                                     │
│          │                       │                       │               │
│          └───────────────────────┼───────────────────────┘               │
│                                  ▼                                       │
│                      [ Local SQLite Database ]                           │
│               (omni_context.db · local; cloud LLM is opt-in)            │
│                                  │                                       │
│          ┌───────────────────────┼───────────────────────┐               │
│          ▼                       ▼                       ▼               │
│  [ Web Dashboard UI ]    [ DeskRAG Subsystem ]     [ AI Synthesis ]      │
│  • 01 · 🤖 Assistant     • PDF/Docx/Pptx/Xlsx/Md   • Multi-Day Reviews   │
│    (chat + suggestions)  • FastEmbed + ChromaDB    • Periodic Snapshots  │
│  • 02 · Workstreams      • Jieba + BM25 Keyword    • Telegram Push       │
│  • 03 · Knowledge & RAG  • Hybrid RRF Retrieval    • Multi-LLM (Gemini/  │
│  • 04 · Summaries        • Multi-LLM SSE Chat        Claude/OpenAI/Ollama│
│  • 05 · Checkpoints · 06 · Live Feed · Explorer Reveal                   │
│  • 07 · Settings · 08 · System Health  · 🌐 Bilingual (EN/ZH)            │
└──────────────────────────────────────────────────────────────────────────┘
```

### 1. 🎯 Canonical Hierarchy Project Resolver
* **Zero Subfolder Fragmentation**: Automatically aggregates edits in deeply nested subfolders (e.g., `core/`, `synthesizer/`, `Draft_Paper/`, `Daily_Report/`) into their true parent project or paper root (e.g. `activityTracker`, `AI_PapersResearch`).
* **Session-Level Multi-File Batching**: Multiple files modified during the same session are grouped into a clean single line (e.g. `Modified file1.md, file2.py (6 files total)`). Clicking any item expands the full file list with diffs and word counts.

### 2. 🐙 GitHub Cloud & PR Intelligence
* **Dual Authentication**:
  * **1-Click Zero-Config Auth**: Automatically detects local `gh` CLI credentials (with `repo`, `read:org`, `workflow`, `gist` scopes) with no manual token creation required.
  * **Personal Access Token (PAT)**: Supports Fine-Grained and Classic PATs.
* **Full Repository & PR Tracking**:
  * Synchronizes all Public and Private repositories.
  * Captures PR titles, states (Open / Merged / Draft), branch flows (`head -> base`), GitHub Actions CI test results (`SUCCESS` / `PENDING` / `FAILURE`), and review approval statuses.
  * Web dashboard includes direct clickable links to GitHub PR pages.

### 2.1 🔁 Local Git Sync Center (per-repository confirmation)
* **Separate from GitHub Cloud Sync**: GitHub integration reads cloud repository/PR metadata; the Local Git Sync Center reports configured local repository branches, cached ahead/behind refs, and worktree state.
* **Controlled two-way workflow**: Refresh each repository with `Fetch`, then use `Pull --ff-only`, `Commit staged`, or `Push` only when its preflight is safe.
* **Safe defaults**: No scheduled sync, no automatic `git add`, and no force push. Pull/Push require a clean non-diverged worktree; Commit requires an explicit message and includes only pre-staged files. See [Usage Guide](docs/USAGE.md#13-本機-git-同步中心) and [ADR-011](docs/ADR-011-safe-local-repository-sync.md).
* **Current scope**: A plain local folder, a local Git repository without a remote, and a GitHub repository not yet cloned are intentionally left for the planned Repo Onboarding / Reconciliation flow. The current release never initializes a folder, creates a cloud repository, or clones automatically.

### 3. 🤖 Cross-Platform AI Conversation Capture (Full Prompts & Responses)
* **Local CLI / IDE Agents**:
  * **Claude Code** (`~/.claude/projects/`): Logs bash executions, tool calls, and user prompts.
  * **Claude Desktop Cowork/local-agent**: Auto-detects structured project JSONL under application data, with Windows extended-path support and a seven-day initial backfill.
  * **Codex** (`~/.codex/sessions/**`): Parses rollout JSONL records and complete assistant message turns.
  * **Antigravity** (`.gemini/brain/**`): Captures real-time sessions and tool outputs.
* **Browser Extension (Chrome MV3)**:
  * Supports **ChatGPT**, **Google Gemini**, and **Claude.ai**.
  * Uses a dedicated ingest token, stable turn key, and write-only capability boundary.
* **Boundary**: normal Claude Desktop cloud-chat Chromium LevelDB cache is detected but not parsed and is never claimed as captured transcript content.
* **Source fault isolation**: a permission or parser failure in one source such as Claude Desktop skips only that source; Codex, Claude Code, and Antigravity scans continue in the same cycle.

### 4. ⚡ Custom Date-Range AI Synthesis Engine
* **Flexible Date Ranges**: Choose any start and end dates (`FROM ~ TO`) or use quick chips (`Today`, `Yesterday`, `This Week`, `Last 7 Days`, `Last 30 Days`) to synthesize executive multi-day review reports.
* **Multi-Model Support**: The release template defaults to local Ollama with scheduled synthesis disabled; Gemini, Anthropic, and OpenAI remain explicitly selectable.
* **Open Loops Extraction**: Automatically analyzes daily activities and extracts actionable tasks into an interactive checklist.

### 5. 🌐 Full Bilingual (EN / 繁中) i18n & Theme Switcher
* Topbar button provides seamless 1-click switching between `English` and `繁體中文`.
* Full Dark / Light theme support with preferences persisted in `localStorage`.

### 6. 🔔 Local Desktop Notifications & Background Autostart
* Windows-native morning briefings, evening reviews, and project stagnation alerts; Telegram remains optional and disabled by default.
* Includes a PowerShell background autostart installer (`scripts/install_autostart.ps1`).

### 7. ⏱️ Daily Interface Usage & Milestones (P2.6 Alpha)
* The dashboard shows observed foreground time and AI turns for Claude, Codex, ChatGPT, Gemini, Antigravity, VS Code, and other configured interfaces.
* Daily goals, milestones, notification tone, quiet hours, and cooldown are configurable; SQLite receipts prevent duplicate notifications after restart.
* Foreground time is not productivity or actual work time. A continuous coverage ledger records when the window collector was actually observed running: coverage shows `observed` only when the day's ledger coverage meets the configured threshold (default 95%), otherwise `partial` with the measured ratio; interruptions and sleep are never back-filled.
* The dashboard `DATA CAPTURE` panel condenses three independent signals—`FOCUS`, `WEB`, and `LOG`—without treating one observed channel as proof of another.
* `http://127.0.0.1:8765/extension-monitor` is the advanced Browser Extension diagnostic page for enabled/observed, heartbeat, and per-site state; token pairing remains inside the Extension popup.

### 8. 🧾 Verified Background Agent / CLI Task Time (Alpha)
* `BACKGROUND AGENT TASKS` separately shows paired local-receipt execution time from Claude Code, Claude Desktop local-agent, and Codex sessions.
* A task is counted only when its local source contains both a prompt-start and an explicit final-completion timestamp. It can therefore appear after its window is minimized; generic Terminal/PowerShell work and tasks without a final receipt are never estimated.
* This metric remains separate from foreground time, AI turns, and milestones. Parallel tasks use an interval union for the total to avoid double counting. See [ADR-010](docs/ADR-010-verified-background-agent-task-time.md) for its evidence boundary.

### 9. 🧠 Local Semantic Index and `omni ask` (P3-2 / P3-3 Alpha)
* Loopback Ollama `bge-m3` indexes AI turns, Git commits, file-activity metadata, Open Loops, and Project State without sending the index to a cloud provider.
* Incremental updates use content hashes and retain SQLite source references, project, time, trust status, and embedding-input degradation provenance.
* `omni ask` supports retrieval-only mode or a local Ollama answer with `[S1]` citations. Similarity is not source validation or proof of coverage.

### 10. 🔗 Related History and Derived Work Sessions (P3-4 / P3-5 Alpha)
* `Recent Work Sessions` derives project-scoped clusters from AI turns, Git commits, and file events using a configurable inactivity gap. It is a read-only view and adds no new session table.
* `Related History` searches the local semantic index from the dashboard or CLI, returns traceable source references and trust status, and does not persist the query.
* Session grouping is not actual work time, focus, or productivity. Similarity is not proof that work is duplicated, correct, or reusable.

### 11. 🧩 Proposal-only Secretary (P5-1 Alpha + P5-R1 LLM notes)
* The first Alpha derives traceable next-step suggestions from local Project State, actionable Open Loops, and non-sensitive Extension diagnostics.
* The rule engine never persists proposals, modifies files, executes commands, or exposes an approval action. See [ADR-007](docs/ADR-007-proposal-only-secretary.md) for the safety contract and [ADR-008](docs/ADR-008-gated-agent-executor.md) for the executor-restart contract.
* **P5-R1 LLM advisory notes (optional, off by default)**: when enabled, an LLM (local Ollama by default; cloud is an explicit opt-in) adds one advisory note per existing suggestion plus a daily summary — annotate-only, it can never add, remove, or execute anything, and any LLM failure falls back to the pure rule output.
* **P5-R2 Gated Executor (optional, off by default)**: with per-item user approval the secretary can carry out whitelisted actions (generate a Handoff, `git fetch`, mark a stale open loop) — the execute API accepts only a proposal_id, actions come from server-side templates with no shell involved, a dedicated execution token is required, every run leaves an audit receipt, and proposals whose evidence changed expire automatically.
* **P5-R3 L2 dispatcher (optional, separate switch, off by default)**: behind three gates (token + per-item approval + one-time 6-digit confirm code) and a per-template cooldown, the secretary dispatches **your locally signed-in Claude Code / Codex CLI** to draft a restart plan for a stalled item; subprocesses run as argv lists with no shell, cwd is restricted to that project's repo, the environment is rebuilt from a location-only allowlist (no API key is ever forwarded), timeouts kill the process, and running jobs are cancellable.
* **L2 write mode (third switch, off by default; ADR-008 addendum)**: two-phase approval — you read the drafted plan first, then the CLI edits files following that exact plan; the worktree must be clean, the agent **never commits or pushes**, and changes stay in the worktree for `git diff` review (`git checkout .` reverts everything).
* **P5-R4a morning briefing**: the 08:30 desktop toast and the `OMNICONTEXT_TODAY` daily entry file now carry the top secretary suggestions with the optional LLM summary (read-only; failures never block the briefing). All switches live in the dashboard settings tab — no YAML editing required.
* **Two-tier incremental summaries**: each periodic checkpoint compresses its window into a ≤100-char micro-summary via local Ollama (zero API cost); the nightly report reads the micro-summary timeline and falls back to raw excerpts only for uncovered windows — cloud token usage drops by roughly an order of magnitude.
* The localhost smoke produced two suggestions with three evidence references, blocked a hostile Origin with 403, and passed desktop plus 494px responsive rendering. This receipt does not authorize an executor.
* **Secretary memory (2026-09-02, [ADR-012](docs/ADR-012-secretary-memory.md))**: the secretary now has a fixed "brain" — type "remember: …", "/pref mute repo_needs_push" or "/decision @project …" in the chat box to write a local note without calling the LLM; the morning pack leaves deletable observations; every question is answered with today's status, the top three proposals and your notes injected (capped, receipted, inspectable), proposal cards honour preferences and show the latest decision for that project; handoffs, sync reports, STATUS drafts and period summaries can be folded into the knowledge base in one click.
* **The secretary on your phone (2026-09-03, [ADR-013](docs/ADR-013-telegram-secretary-chat.md), off by default)**: the dashboard stays loopback-only, so the phone channel is Telegram — type in the bound chat to ask (same pipeline as the dashboard chat box; replies carry citation filenames and a "memory: N notes" line), "remember: …" / "/pref …" write straight to memory without calling the LLM, and `/today` `/notes` `/status` `/proposals` are commands. Approvals stay limited to whitelisted L0/L1 actions on an unlocked channel, and `/disarm` locks it from anywhere. **Boundary**: this is the only channel that sends your questions and the answers off the machine (content passes through Telegram; citations send filenames only), so it ships off by default.

### 12. 📚 DeskRAG Local Knowledge Base & Document Chat (Single-Server Embedded)
* **Single Server Integration**: Seamlessly integrated into the single OmniContext daemon (`http://127.0.0.1:8765`), eliminating dual-server operational overhead while executing indexing and storage maintenance in background workers.
* **Curated Local & Cloud Model Dropdowns**:
  * **Ollama Local Offline**: Dedicated dropdown selection across 4 curated offline models (`llama3.1:8b` default, `mistral:7b`, `gemma4:e4b`, `qwen3:4b`) for 100% private, offline inference.
  * **Cloud LLMs**: Optional integration with Google Gemini (`gemini-3.7-flash`), Anthropic Claude (`claude-3-5-sonnet`), and OpenAI (`gpt-4o`).
* **Intelligent Chat Session Lifecycle**:
  * **Auto-Titling**: First prompt sentence is automatically extracted as the session title (e.g. `💬 OPC UA Time Series Forecasting`), replacing generic titles.
  * **Seamless Session Switching & History**: Instant recall of past QA histories, cited chunk cards, and metadata.
  * **Session Management**: One-click new chat creation (`➕ Create New Chat`) and session deletion.
* **Universal Parser Hub & Activity Indexing**:
  * **Documents**: High-precision text extraction with page/slide/sheet metadata for PDF (PyMuPDF), Word (`.docx`), PowerPoint (`.pptx`), Excel (`.xlsx`), and source code/markdown text files.
  * **Project Activity Slices**: Maps local Project States and Open Loops into virtual chunks for unified semantic retrieval across work history and static documents.
* **Sliding Window Hierarchical Chunker**: Preserves paragraph headers, page numbers, slide titles, and sheet names.
* **Hybrid Retrieval Engine**: Combines FastEmbed (ONNX, 512-dim `BAAI/bge-small-zh-v1.5`) + ChromaDB vector embeddings with Jieba + BM25Okapi keyword matching using Reciprocal Rank Fusion (RRF), Weighted Fusion, Vector Only, and BM25 Only. Retrieval runs in a **resident worker subprocess** (the main service never loads Chroma/BM25/embedding models; warmed up in the background after start, killed and restarted on timeout).
* **Multi-LLM SSE Streaming Chat**: Interactive chat with token-level SSE streams and citation source cards (with page/slide/sheet badges).
* **Native Windows Explorer Reveal**: One-click opening and highlighting of cited source documents in Windows File Explorer.

---

## 🚀 Getting Started

### 1. Prerequisites & Installation

Requires **Python 3.10+**

```bash
# Clone the repository
git clone https://github.com/dofliu/activityTracker.git
cd activityTracker

# Source checkout / development mode
python -m pip install -e ".[dev]"

# Create local config, directories, and a browser ingest token
python main.py init --watch "/your/project/root"
```

For a locally built Alpha wheel:

```bash
python -m pip install omnicontext-1.3.0a5-py3-none-any.whl
omnicontext init --watch "/your/project/root"
omnicontext assets-status
```

The wheel is not publicly released. Installed wheels keep config, database, and reports under the writable `~/OmniContext` by default rather than `site-packages`; `OMNICONTEXT_HOME` and `OMNICONTEXT_CONFIG` can override this.

### 2. Configure LLM API Keys

The release template uses local Ollama. If you explicitly select Gemini, Anthropic, or OpenAI, set the relevant environment variable and provider in `config.yaml`:

```bash
# Windows PowerShell
$env:GEMINI_API_KEY="your-gemini-api-key"

# Or if using Anthropic / OpenAI
$env:ANTHROPIC_API_KEY="your-anthropic-api-key"
$env:OPENAI_API_KEY="your-openai-api-key"
```

### 3. Launch Dashboard & Collectors

```bash
python main.py
```

Open your browser and navigate to: **[http://127.0.0.1:8765](http://127.0.0.1:8765)**

For Extension pairing, milestone configuration, backups, and troubleshooting, see **[docs/USAGE.md](docs/USAGE.md)**.

---

## 💻 CLI Command Reference

| Command | Description | Example |
| :--- | :--- | :--- |
| `python main.py` | Start Web dashboard and background collectors | `python main.py` |
| `python main.py init` | Create/update portable config and browser ingest token | `python main.py init --watch D:/Projects` |
| `python main.py now` | Instant 1-second view of active projects, last 5 events, and open loops | `python main.py now` |
| `python main.py summary` | Synthesize AI daily or range review | `python main.py summary --start 2026-08-20 --end 2026-08-23` |
| `python main.py github status` | Inspect GitHub connection status, repo count, and rate limits | `python main.py github status` |
| `python main.py github sync` | Trigger immediate synchronization of GitHub repos and PRs | `python main.py github sync` |
| `python main.py checkpoint` | Manually generate a Markdown activity checkpoint log | `python main.py checkpoint --hours 2` |
| `python main.py notify` | Trigger Telegram report or briefing push | `python main.py notify summary` |
| `python main.py status` | View database metrics and collector states | `python main.py status` |
| `python main.py backup` | Create and verify an SQLite online backup | `python main.py backup` |
| `python main.py restore-drill` | Restore a backup into an isolated temporary DB without replacing the live DB | `python main.py restore-drill` |
| `python main.py migration-status` | Read current/latest schema versions, pending steps, and compatibility | `python main.py migration-status` |
| `python main.py assets-status` | Verify packaged config/Web/Extension assets | `python main.py assets-status` |
| `python main.py extension-path` | Print the Chrome/Edge Load unpacked directory | `python main.py extension-path` |
| `python main.py index` | Build or incrementally update the local semantic index | `python main.py index --json` |
| `python main.py ask` | Ask cross-AI/repository history with traceable sources | `python main.py ask "How did rollback work?" --project activityTracker` |
| `python main.py sessions` | Derive recent project work sessions from existing observations | `python main.py sessions --project activityTracker --hours 24` |
| `python main.py recall` | Find related local history without storing the query | `python main.py recall "rollback rehearsal" --project activityTracker` |
| `python main.py maintain` | Run SQLite health maintenance (Checkpoint, prune, backup, rotate) | `python main.py maintain --retention-days 90` |
| `python main.py heal` | Supervise and auto-restart degraded or dead collector workers | `python main.py heal` |
| `python main.py wal-checkpoint` | Manually checkpoint and truncate the SQLite WAL log | `python main.py wal-checkpoint --mode TRUNCATE` |

With an installed wheel, replace `python main.py` with `omnicontext` or `omni`.

---

## ⚙️ Configuration (`config.yaml`)

```yaml
server:
  port: 8765
  host: "127.0.0.1"

security:
  allowed_origins:
    - "http://127.0.0.1:8765"
    - "http://localhost:8765"
  allow_remote_clients: false
  browser_extension_ingest_token_env: "OMNICONTEXT_INGEST_TOKEN"

data_lifecycle:
  backups_dir: "~/OmniContext/backups"
  backup_retention_days: 30
  auto_backup_on_start: false

project_resolution:
  # Add your project roots; ~ and environment variables are supported.
  search_roots:
    - "~/Projects"
  # Optional; blank derives the OmniContext project path from its installation.
  self_project_path: ""

watchers:
  file_watcher:
    enabled: true
    watch_directories:
      - "~/Projects"
      - "~/Documents/Research"
    extensions: [".tex", ".docx", ".md", ".pdf", ".py"]
  
  git_watcher:
    enabled: true
    repositories:
      - "~/Projects"
  
  agent_log_watcher:
    enabled: true
    claude_code: true
    codex: true
    antigravity: true

  browser:
    gemini: true
    chatgpt: true
    claude_web: true

synthesizer:
  provider: "ollama"
  gemini:
    model: "gemini-3.7-flash"
  schedule:
    enabled: false
    time: "23:30"
  periodic_checkpoint:
    enabled: true
    interval_hours: 2

integrations:
  github:
    enabled: true
    token: ""  # When blank, automatically uses local gh auth token
```

---

## 🧩 Installing the Chrome Extension

1. Open Chrome or Edge and navigate to `chrome://extensions/`.
2. Toggle on **Developer mode** in the top right.
3. Click **Load unpacked**.
4. Run `python main.py extension-path` (or `omnicontext extension-path` for a wheel) and select the printed directory.
5. Run `python main.py init --show-token`, then paste the token into the extension popup and save it.
6. Only supported-site events carrying a valid token can write to the local ingestion endpoint.
7. After the popup reports a verified pairing, open `http://127.0.0.1:8765/extension-monitor` to inspect observed browser events.

---

## 📂 Repository Structure

```text
activityTracker/
├── config.yaml                     # System configuration with hot reload (created from config.example.yaml)
├── main.py                         # Main entry point and CLI dispatcher
├── pyproject.toml                  # Packaging, CLI entry point, and pytest config
├── MANIFEST.in                     # sdist assets and privacy exclusions
├── requirements.txt                # Python package dependencies
├── README.md / README_en.md        # Traditional Chinese / English documentation
├── ROADMAP.md / STATUS.yaml        # Development record and machine-readable status snapshot
│
├── docs/                           # 📚 Documentation (start at docs/INDEX.md)
│   ├── INDEX.md                    # Documentation index and reading map
│   ├── USAGE.md                    # User guide: setup, pairing, daily operation, backups, troubleshooting
│   ├── PRODUCT_POSITIONING.md      # Product positioning and evidence boundaries
│   ├── TEST_STRATEGY.md / RELEASE_CHECKLIST.md
│   ├── ADR-001 ~ ADR-011           # Architecture decision records
│   └── archive/                    # Archived one-off plans and completion reports
│
├── core/                           # Core service modules
│   ├── server.py                   # FastAPI REST API & static file server
│   ├── manager.py                  # Collector orchestration and supervise_and_heal self-healing
│   ├── database.py / migrations.py # SQLite sessions and append-only schema migration
│   ├── models.py                   # SQLAlchemy models (Events, Projects, PRs, RAG)
│   ├── security.py / secret_resolver.py  # Origin boundary, secret redaction, key resolution
│   ├── data_lifecycle.py           # Online backup, WAL checkpoint, history pruning, receipts
│   ├── project_engine.py / project_paths.py  # Canonical project resolver and root resolution
│   ├── semantic_index.py           # Local embeddings, provenance retrieval, and omni ask
│   ├── context_memory.py           # Derived work sessions and related-history retrieval
│   ├── handoff_engine.py           # Provider-neutral Context Handoff generator
│   ├── proactive_secretary.py      # Proposal-only secretary (ADR-007)
│   ├── repo_sync.py                # Safe local Git sync center (ADR-011)
│   ├── background_tasks.py         # Verified background agent task time (ADR-010)
│   ├── usage_analytics.py / capture_coverage.py  # Usage statistics and coverage signals
│   ├── extension_monitor.py / extension_verification.py  # Extension diagnostics and live verification
│   ├── triage_signals.py           # Cross-project triage signals (GitHub PRs/issues)
│   ├── platform_services.py        # Cross-platform argv-based OS integration
│   └── runtime_paths.py / fs_utils.py / time_utils.py  # Runtime paths, explorer, timezone helpers
│
├── rag/                            # 📚 DeskRAG local knowledge-base subsystem
│   ├── router.py                   # /api/v1/rag/* REST API and SSE streaming chat
│   ├── scanner.py / index_worker.py / jobs.py / lifecycle.py  # Controlled index worker lifecycle
│   ├── parsers/                    # PDF / Office / text / image Parser Hub
│   ├── chunker.py                  # Sliding-window hierarchical chunker
│   ├── embeddings.py / vector_store.py  # FastEmbed (ONNX) + ChromaDB vector store
│   ├── retriever.py / retrieval/   # Jieba+BM25 and Hybrid RRF / Weighted Fusion retrievers
│   ├── activity_indexer.py         # Project State and Open Loop virtual chunks
│   └── llm_gateway.py              # Ollama / Gemini / Claude / OpenAI gateway
│
├── integrations/                   # External integrations
│   └── github_client.py            # GitHub API client (Repos, PRs, CI, Reviews)
│
├── watchers/                       # Data collection watchers
│   ├── file_watcher.py             # Watchdog file activity tracker with word counts
│   ├── git_watcher.py              # Recursive Git scanner with per-repo fault isolation
│   ├── window_watcher.py           # Active window focus & time tracker
│   ├── agent_log_watcher.py        # Claude Code/Desktop, Codex, Antigravity log parser
│   └── browser_extension/          # Chrome MV3 extension (ChatGPT/Gemini/Claude)
│
├── synthesizer/                    # AI synthesis & scheduling engine
│   ├── aggregator.py               # Multi-day range event aggregation pipeline
│   ├── prompt_templates.py         # Structured prompt templates
│   ├── llm_client.py               # Multi-provider LLM client (Gemini/Claude/GPT/Ollama)
│   └── scheduler.py                # Daily synthesis & periodic checkpoint timer
│
├── notifiers/                      # Notification modules
│   ├── desktop_notifier.py         # Windows WinRT Toast desktop notifications (zero-dependency)
│   └── telegram_notifier.py        # Telegram bot for briefings & stagnation alerts (optional)
├── exporters/
│   └── daily_brief.py              # OMNICONTEXT_TODAY.md/.html daily entry brief
│
├── web/                            # Web UI Dashboard (tabs 01–07 + extension-monitor)
│   ├── index.html / app.js / style.css  # Layout, i18n controller, dark-orange theme
│   └── extension-monitor.html      # Browser Extension advanced diagnostics page
│
├── scripts/                        # Automation, verification, and maintenance scripts
├── tests/                          # 31 contract test modules (security/data/RAG/sync/lifecycle)
│
├── logs/checkpoints/               # Periodic activity checkpoint logs
└── reports/                        # Daily & range Markdown reports
```

---

## 🗺️ Roadmap & Status

Development is tracked in [ROADMAP.md](ROADMAP.md) (Traditional Chinese, phases P0–P8) with a machine-readable snapshot in [STATUS.yaml](STATUS.yaml). As of 2026-08-30: the P0–P2 daily-usable core, P3 memory layer (Alpha), P4.2 safe local Git sync, P5-1 proposal-only secretary (Alpha), P7 DeskRAG knowledge base, and P8 self-healing/maintenance hub are complete. The next milestone is P4.3 Repo Onboarding/Reconciliation, and the project is **not yet release-ready** — see `known_blockers` in STATUS.yaml for what remains (Extension live PASS receipt, coverage ledger, publish/tag).

---

## 🔒 Privacy & Security

* **Local event storage**: Events are stored in local SQLite (`omni_context.db`) without third-party analytics telemetry.
* **LLM boundary**: Selecting Gemini, Anthropic, or OpenAI sends the assembled work context to that provider. Ollama keeps synthesis local.
* **Local API boundary**: Loopback-only access, an exact Origin allowlist, secret redaction, and a browser-extension ingest token are enabled by default.
* **Trust contract**: Canonical AI events carry a stable turn key, source provenance, and response status; partial/legacy responses are not treated as conclusions.
* **Backup lifecycle**: `python main.py backup` uses SQLite's Online Backup API and emits integrity/SHA-256 evidence. `python main.py restore-drill` verifies schema and row counts in an isolated temporary DB and saves a JSON receipt without replacing the live DB. Windows wheel upgrade smoke has passed; automatic pruning and a formal production rollback rehearsal remain incomplete.
* **Schema migration**: An append-only registry records version/name/checksum. Existing databases receive a verified backup before upgrade; checksum mismatches and unknown newer versions fail closed.
* **Artifact boundary**: Wheel/sdist content receipts verify required assets and reject `config.yaml`, SQLite databases, and local secrets.
* **Git protection**: Database files, API keys, and personal Markdown reports are ignored by default to reduce accidental commits.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
