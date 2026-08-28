# 🌐 OmniContext — Personal Context Intelligence & Workstream Hub

[![Language](https://img.shields.io/badge/Language-English%20%7C%20%E7%B9%81%E9%AB%94%E4%B8%AD%E6%96%87-orange)](#-language)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-green)](https://fastapi.tiangolo.com/)

> **[English Documentation](README_en.md) | [繁體中文說明文件](README.md)**

> **Current status: Personal Alpha.** Windows milestone WinRT Toast E2E, schema 7/7, formal package+database rollback, P3-2 through P3-5 Context Memory Alpha, P5-1 proposal-only Alpha, collector runtime diagnostics, the Extension 1.3.1 live-verification harness, and the cross-platform CI matrix have passed. ChatGPT live DOM selectors were repaired, and Claude Desktop Cowork/local-agent transcript capture passed a Windows E2E. Claude.ai still lacks a current-run PASS receipt, and a live Extension heartbeat still requires logged-in Chrome verification, so this is not release-ready.

**Documentation:** [Traditional Chinese usage guide](docs/USAGE.md) · [Roadmap](ROADMAP.md) · [Current status](STATUS.yaml) · [Test strategy](docs/TEST_STRATEGY.md)

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
│  • 01 · Workstreams      • PDF/Docx/Pptx/Xlsx/Md   • Multi-Day Reviews   │
│  • 02 · Live Feed        • FastEmbed + ChromaDB    • Periodic Snapshots  │
│  • 03 · Knowledge & RAG  • Jieba + BM25 Keyword    • Telegram Push       │
│  • 04 · Settings         • Hybrid RRF Retrieval    • Multi-LLM (Gemini/  │
│  • 05 · Summaries        • Multi-LLM SSE Chat        Claude/OpenAI/Ollama│
│  • 06 · Checkpoints      • Native Explorer Reveal                        │
│  • 🌐 Bilingual (EN/ZH)                                                  │
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
* Foreground time is not productivity or actual work time. Coverage remains `partial` until a continuous coverage ledger exists.
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

### 9. 🔗 Related History and Derived Work Sessions (P3-4 / P3-5 Alpha)
* `Recent Work Sessions` derives project-scoped clusters from AI turns, Git commits, and file events using a configurable inactivity gap. It is a read-only view and adds no new session table.
* `Related History` searches the local semantic index from the dashboard or CLI, returns traceable source references and trust status, and does not persist the query.
* Session grouping is not actual work time, focus, or productivity. Similarity is not proof that work is duplicated, correct, or reusable.

### 10. 🧩 Proposal-only Secretary (P5-1 Alpha)
* The first Alpha derives traceable next-step suggestions from local Project State, actionable Open Loops, and non-sensitive Extension diagnostics.
* It does not call a cloud LLM, persist proposals, modify files, execute commands, or expose an approval action. See [ADR-007](docs/ADR-007-proposal-only-secretary.md) for the safety contract.
* The localhost smoke produced two suggestions with three evidence references, blocked a hostile Origin with 403, and passed desktop plus 494px responsive rendering. This receipt does not authorize an executor.

### 11. 📚 DeskRAG Local Knowledge Base & Document Chat (Single-Server Embedded)
* **Single Server Integration**: Seamlessly integrated into the single OmniContext daemon (`http://127.0.0.1:8765`), eliminating dual-server operational overhead.
* **Universal Parser Hub**: High-precision text extraction with page/slide/sheet metadata for PDF (PyMuPDF), Word (`.docx`), PowerPoint (`.pptx`), Excel (`.xlsx`), and source/markdown text files.
* **Sliding Window Hierarchical Chunker**: Preserves paragraph headers, page numbers, slide titles, and sheet names.
* **Hybrid Retrieval Engine**: Combines FastEmbed (ONNX) + ChromaDB vector embeddings with Jieba + BM25 keyword matching using Reciprocal Rank Fusion (RRF) and Weighted Fusion.
* **Multi-LLM SSE Streaming Chat**: Interactive chat with token-level SSE streams and interactive citation source cards (with page/sheet badges).
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
python -m pip install omnicontext-1.3.0a4-py3-none-any.whl
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
├── config.yaml                     # System configuration with hot reload
├── main.py                         # Main entry point and CLI dispatcher
├── pyproject.toml                  # Packaging, CLI entry point, and pytest config
├── requirements.txt                # Python package dependencies
├── README.md                       # Traditional Chinese Documentation
├── README_en.md                    # English Documentation
├── docs/USAGE.md                   # Setup, pairing, daily operation, backups, troubleshooting
├── docs/ADR-003-versioned-sqlite-migrations.md  # Schema migration decision record
│
├── core/                           # Core service modules
│   ├── database.py                 # SQLite session & engine management
│   ├── migrations.py               # Append-only registry, checksums, and upgrade guard
│   ├── models.py                   # SQLAlchemy models (Events, Projects, PRs)
│   ├── server.py                   # FastAPI REST API & static file server
│   ├── security.py                 # Origin, secret redaction, and extension token boundary
│   ├── platform_services.py        # Cross-platform argv-based OS integration
│   ├── data_lifecycle.py           # SQLite online backup and integrity receipt
│   ├── project_engine.py           # Canonical project resolver & open loop engine
│   ├── project_paths.py            # Config-driven project-root resolution
│   ├── semantic_index.py           # Local embeddings, provenance retrieval, and omni ask
│   ├── context_memory.py            # Derived work sessions and related-history retrieval
│   ├── fs_utils.py                 # Native Windows folder picker utilities
│   └── time_utils.py               # Unified timezone helpers
│
├── integrations/                   # External integrations
│   └── github_client.py            # GitHub API client (Repos, PRs, CI, Reviews)
│
├── watchers/                       # Data collection watchers
│   ├── file_watcher.py             # Watchdog file activity tracker with word counts
│   ├── git_watcher.py              # Recursive Git scanner & commit tracker
│   ├── window_watcher.py           # Active window focus & time tracker
│   ├── agent_log_watcher.py        # Claude Code / Codex / Antigravity log parser
│   └── browser_extension/          # Chrome MV3 extension (ChatGPT/Gemini/Claude)
│
├── synthesizer/                    # AI synthesis & scheduling engine
│   ├── aggregator.py               # Multi-day range event aggregation pipeline
│   ├── prompt_templates.py         # Structured prompt templates
│   ├── llm_client.py               # Multi-provider LLM client (Gemini/Claude/GPT/Ollama)
│   └── scheduler.py                # Daily synthesis & periodic checkpoint timer
│
├── notifiers/                      # Notification modules
│   └── telegram_notifier.py        # Telegram bot for briefings & stagnation alerts
│
├── web/                            # Web UI Dashboard
│   ├── index.html                  # Dashboard HTML with i18n data tags
│   ├── app.js                      # UI controller (i18n engine, GitHub badges, accordions)
│   └── style.css                   # Dark orange aesthetic theme
│
├── scripts/                        # Automation scripts
│   ├── install_autostart.ps1       # Windows startup scheduler installer
│   └── uninstall_autostart.ps1     # Uninstaller script
├── tests/                          # Security/data/lifecycle/portability contracts
├── docs/                           # ADR, usage, and test strategy
│   └── ADR-006-derived-context-sessions-and-related-history.md
│
├── logs/checkpoints/               # Periodic activity checkpoint logs
└── reports/                        # Daily & range Markdown reports
```

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
