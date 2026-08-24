# 🌐 OmniContext — Personal Context Intelligence & Workstream Hub

[![Language](https://img.shields.io/badge/Language-English%20%7C%20%E7%B9%81%E9%AB%94%E4%B8%AD%E6%96%87-orange)](#-language)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-green)](https://fastapi.tiangolo.com/)

> **[English Documentation](README_en.md) | [繁體中文說明文件](README.md)**

> **Current status: Personal Alpha.** Windows Dashboard/API, the Extension token boundary, the verified-heartbeat contract, P2.6 usage milestones, SQLite schema migration 5/5, wheel/sdist fresh/upgrade/assets smoke, an isolated restore drill, and 52 contract tests are verified. Gemini browser ingestion has produced 3 events (2 with responses), while a live receipt from the new Extension heartbeat, the other supported sites, a real milestone Toast, a formal rollback rehearsal, and the macOS/Linux matrix remain incomplete; this is not release-ready.

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
│          ┌───────────────────────┴───────────────────────┐               │
│          ▼                                               ▼               │
│  [ Web Dashboard UI ]                        [ AI Synthesis Engine ]     │
│  • 01 · Active Workstreams                   • Custom Multi-Day Reviews  │
│  • 02 · Live Intel Feed                      • Periodic Activity Logs    │
│  • 03 · Settings & Hot Reload                • Telegram Push Briefings   │
│  • 04 · Daily & Range Summaries              • Multi-LLM (Gemini 3.7 /   │
│  • 05 · Checkpoint Logs                        Claude / GPT-4o / Ollama) │
│  • 🌐 Bilingual i18n Switch (EN/ZH) & Dark/Light Theme                   │
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
  * **Codex** (`~/.codex/sessions/**`): Parses rollout JSONL records and complete assistant message turns.
  * **Antigravity** (`.gemini/brain/**`): Captures real-time sessions and tool outputs.
* **Browser Extension (Chrome MV3)**:
  * Supports **ChatGPT**, **Google Gemini**, **Claude.ai**, and **Manus**.
  * Uses a dedicated ingest token, stable turn key, and write-only capability boundary.

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
* The dashboard shows observed foreground time and AI turns for Claude, Codex, ChatGPT, Gemini, Manus, Antigravity, VS Code, and other configured interfaces.
* Daily goals, milestones, notification tone, quiet hours, and cooldown are configurable; SQLite receipts prevent duplicate notifications after restart.
* Foreground time is not productivity or actual work time. Coverage remains `partial` until a continuous coverage ledger exists.
* `http://127.0.0.1:8765/extension-monitor` exposes enabled/observed ingestion state, while token pairing remains inside the Extension popup.

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
python -m pip install omnicontext-1.3.0a2-py3-none-any.whl
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

With an installed wheel, replace `python main.py` with `omnicontext`.

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

watchers:
  file_watcher:
    enabled: true
    watch_directories:
      - "D:/Project_CodingSimulation"
      - "D:/Dropbox/Project_Academic/Paper_and_Patent/01.JournalPapers"
    extensions: [".tex", ".docx", ".md", ".pdf", ".py"]
  
  git_watcher:
    enabled: true
    repositories:
      - "D:/Project_CodingSimulation"
  
  agent_log_watcher:
    enabled: true
    claude_code: true
    codex: true
    antigravity: true

  browser:
    gemini: true
    chatgpt: true
    claude_web: true
    manus: true

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
│   └── browser_extension/          # Chrome MV3 extension (ChatGPT/Gemini/Claude/Manus)
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
├── docs/                           # ADR and test strategy
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
