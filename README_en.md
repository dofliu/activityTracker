# 🌐 OmniContext — Personal Context Intelligence & Workstream Hub

[![Language](https://img.shields.io/badge/Language-English%20%7C%20%E7%B9%81%E9%AB%94%E4%B8%AD%E6%96%87-orange)](#-language)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-green)](https://fastapi.tiangolo.com/)

> **[English Documentation](README_en.md) | [繁體中文說明文件](README.md)**

**OmniContext** is a **local-first, privacy-focused** personal context intelligence and activity tracking hub. It automatically captures your cross-platform AI interactions (Claude Code, Codex, Antigravity, ChatGPT, Gemini, etc.), code commits, paper and file modifications, window time allocation, and deeply integrates with your GitHub repositories and Pull Request (PR) statuses.

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
│                      (omni_context.db · Zero Leak)                       │
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
  * Features a 10-minute sliding window upsert to guarantee persistence of both user queries and complete AI replies.

### 4. ⚡ Custom Date-Range AI Synthesis Engine
* **Flexible Date Ranges**: Choose any start and end dates (`FROM ~ TO`) or use quick chips (`Today`, `Yesterday`, `This Week`, `Last 7 Days`, `Last 30 Days`) to synthesize executive multi-day review reports.
* **Multi-Model Support**: Defaults to Google Gemini (`gemini-3.7-flash`), with built-in support for Anthropic Claude 3.5 Sonnet, OpenAI GPT-4o, and local Ollama.
* **Open Loops Extraction**: Automatically analyzes daily activities and extracts actionable tasks into an interactive checklist.

### 5. 🌐 Full Bilingual (EN / 繁中) i18n & Theme Switcher
* Topbar button provides seamless 1-click switching between `English` and `繁體中文`.
* Full Dark / Light theme support with preferences persisted in `localStorage`.

### 6. 🔔 Telegram Push Notifications & Background Autostart
* Automated daily digests, morning briefings, and project stagnation alerts sent directly to Telegram.
* Includes a PowerShell background autostart installer (`scripts/install_autostart.ps1`).

---

## 🚀 Getting Started

### 1. Prerequisites & Installation

Requires **Python 3.10+**

```bash
# Clone the repository
git clone https://github.com/dofliu/activityTracker.git
cd activityTracker

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure LLM API Keys

OmniContext uses `Google Gemini` by default. Set the environment variable or configure it in `config.yaml`:

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

---

## 💻 CLI Command Reference

| Command | Description | Example |
| :--- | :--- | :--- |
| `python main.py` | Start Web dashboard and background collectors | `python main.py` |
| `python main.py now` | Instant 1-second view of active projects, last 5 events, and open loops | `python main.py now` |
| `python main.py summary` | Synthesize AI daily or range review | `python main.py summary --start 2026-08-20 --end 2026-08-23` |
| `python main.py github status` | Inspect GitHub connection status, repo count, and rate limits | `python main.py github status` |
| `python main.py github sync` | Trigger immediate synchronization of GitHub repos and PRs | `python main.py github sync` |
| `python main.py checkpoint` | Manually generate a Markdown activity checkpoint log | `python main.py checkpoint --hours 2` |
| `python main.py notify` | Trigger Telegram report or briefing push | `python main.py notify summary` |
| `python main.py status` | View database metrics and collector states | `python main.py status` |

---

## ⚙️ Configuration (`config.yaml`)

```yaml
app:
  port: 8765
  host: "127.0.0.1"

watchers:
  file_watcher:
    enabled: true
    watch_directories:
      - "D:/Project_CodingSimulation"
      - "D:/Dropbox/Project_Academic/Paper_and_Patent/01.JournalPapers"
    extensions: [".tex", ".docx", ".md", ".pdf", ".py", ".txt"]
  
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
  provider: "gemini"
  gemini:
    model: "gemini-3.7-flash"
  schedule:
    enabled: true
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
4. Select the `watchers/browser_extension/` directory in this project.
5. All conversations on ChatGPT, Gemini, Claude.ai, and Manus will automatically sync locally!

---

## 📂 Repository Structure

```text
activityTracker/
├── config.yaml                     # System configuration with hot reload
├── main.py                         # Main entry point and CLI dispatcher
├── requirements.txt                # Python package dependencies
├── README.md                       # Traditional Chinese Documentation
├── README_en.md                    # English Documentation
│
├── core/                           # Core service modules
│   ├── database.py                 # SQLite session & engine management
│   ├── models.py                   # SQLAlchemy models (Events, Projects, PRs)
│   ├── server.py                   # FastAPI REST API & static file server
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
│
├── logs/checkpoints/               # Periodic activity checkpoint logs
└── reports/                        # Daily & range Markdown reports
```

---

## 🔒 Privacy & Security

* **100% Local Storage**: All events are stored exclusively in your local SQLite database (`omni_context.db`).
* **Zero Telemetry**: No third-party tracking, analytics, or remote logging.
* **Git Commit Protection**: Database files, API keys, and personal Markdown reports are strictly ignored via `.gitignore`.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
