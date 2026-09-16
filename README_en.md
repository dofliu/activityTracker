# 🌐 OmniContext — Personal Context Intelligence & Workstream Hub

[![Language](https://img.shields.io/badge/Language-English%20%7C%20%E7%B9%81%E9%AB%94%E4%B8%AD%E6%96%87-orange)](README.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-green)](https://fastapi.tiangolo.com/)

> **[English Documentation](README_en.md) | [繁體中文說明文件](README.md)**

**OmniContext** is a **local-first personal context memory hub** with explicit data boundaries. It captures cross-platform AI conversations (Claude Code, Codex, Antigravity, ChatGPT, Gemini), code commits, file and paper-writing activity, and window time allocation, integrates GitHub repositories and pull request state — and then has a **secretary that only proposes and never acts on its own** turn those signals into "what should I do next".

Unlike a single vendor's memory/chat-import feature: **OmniContext's canonical context belongs to you and your projects, not to any AI provider.** Full positioning and evidence boundaries: [Product Positioning](docs/PRODUCT_POSITIONING.md).

It answers three questions at any moment:

1. **"Which projects am I actively working on?"**
2. **"Where did I leave off, and which files did I touch?"**
3. **"What open loops are still unresolved?"**

---

## 📊 Current Status

**Personal Alpha — v1.3.0a5** (published as a [GitHub pre-release](https://github.com/dofliu/activityTracker/releases) with a SHA-256 receipt)

| Area | State |
| :--- | :--- |
| Code | P0–P8 and all ADR-008 executor stages landed; 22 ADRs record the boundary behind each decision |
| Tests | **67 contract test modules, 684 tests** (683 passed + 1 skipped; 671 passed + 11 skipped without the `[rag]` extra); Windows / Ubuntu / macOS × Python 3.10 / 3.12 CI green across all six jobs, plus a dedicated "no `[rag]` extra" job |
| Data | SQLite schema migration **18/18** (append-only + checksum, verified backup before upgrade) |
| Release | `release_ready: false` |

**What is left is almost never "code not yet written" — it is receipts that can only be obtained on the user's own machine.** This project does not treat "tests pass" as "works in production". The one remaining *capability* gap blocking release is a full-day coverage ledger measurement.

Don't rely on memory: `python main.py verify` (or dashboard "06 Settings → Acceptance Center") reports which receipts exist right now ([ADR-016](docs/ADR-016-acceptance-center.md)).

**2026-09-16 project review**: the feature set is large enough; what comes next is **subtraction** — remove dead code and unused dependencies, make RAG an optional install, merge the two LLM clients and the two vector-memory stacks, then extract the one capability nothing else offers (reading local AI-agent transcripts) into a standalone package. Feature candidates (remote access, two-way LINE, more collectors) are paused. **R0 landed the same day: dead code and unused deps removed, marked vendored, one source of truth for status numbers, and the knowledge-base dependencies moved to an optional `[rag]` extra (core install 721 MB → 176 MB); R1 has landed D2 (one `core/llm_client.py`), D3 (activity sources in `core/activity_sources.py`) and D4 (`server.py` went from 1,995 to 134 lines, split into 9 domain routers).** Full assessment (in Traditional Chinese): [docs/REVIEW-2026-09-16-project-assessment.md](docs/REVIEW-2026-09-16-project-assessment.md); three-phase plan: [ROADMAP §13](ROADMAP.md#13-架構整頓與推廣方向2026-09-16-檢視).

**Documentation:** [📚 Index](docs/INDEX.md) · [User Guide](docs/USAGE.md) · [Roadmap & Results](ROADMAP.md) · [Backlog](docs/TODO.md) · [Machine-readable status](STATUS.yaml) · [Project review 2026-09-16](docs/REVIEW-2026-09-16-project-assessment.md)

![OmniContext Architecture & Roadmap](docs/assets/omnicontext-architecture-roadmap-card-v1.png)

---

## 🌟 Key Features

> This section is **what exists**. **How to use it** lives in the [User Guide](docs/USAGE.md); **why it is designed this way** lives in the corresponding ADR.

### 1. 🎯 Canonical Hierarchy Project Resolver

* **No more subdirectory fragmentation**: nested directories (`core/`, `synthesizer/`, `Draft_Paper/`) are resolved to their real project or paper root (`activityTracker`, `AI_PapersResearch`).
* **Multi-file session aggregation**: files touched in the same working session collapse into a single card entry, expandable to per-file word-count deltas and paths.

### 2. 🤖 Cross-Platform AI Conversation Capture

* **Local CLI / IDE agents**: Claude Code (`~/.claude/projects/`), Claude Desktop local-agent, Codex (`~/.codex/sessions/**`), Antigravity (`.gemini/brain/**`).
* **Chrome MV3 extension**: ChatGPT, Gemini, Claude.ai — write-only capability boundary enforced with a dedicated ingest token, upserted on a stable turn key.
* **Explicit boundary**: ordinary Claude Desktop cloud chat is only detected as *cache present*. The Chromium LevelDB is **not** parsed and no claim is made that the conversation was captured.
* **Per-source fault isolation**: a permission or parse error in one source skips that source only; the rest of the collection round continues.

### 3. 🐙 GitHub Cloud Intel & 🔁 Local Git Sync Center

* **Dual auth**: auto-detect the local `gh` CLI credential, or use a fine-grained / classic PAT.
* **Cloud metadata**: all public/private repositories and PR titles, state, branch flow, CI results and review status.
* **Local sync center (per-repository confirmation)**: branch, upstream, ahead/behind and worktree changes per repo, with `Fetch` → conditional `Pull --ff-only` / `Commit staged` / `Push`.
* **Safe defaults**: no scheduled auto-sync, never runs `git add`, no force push. A disabled button explains why **with that repository's actual numbers**, not a generic sentence.
* **Overview & batch**: one table for every repository; batch Pull/Push lists the qualifying set for confirmation and re-checks each repo at execution time. Batch push has its own switch, off by default. The secretary can schedule the L0 `repo_sync_report`. See [ADR-011](docs/ADR-011-safe-local-repository-sync.md).
* **Repo onboarding**: folder not yet `git init`-ed, repo without a remote, GitHub repo not yet cloned — each has a single-target confirmation flow. Never overwrites a non-empty directory, never batch-creates or batch-clones, never pushes on your behalf.

### 4. ⏱️ Interface Usage Time, Background Tasks & Coverage

* **Daily interface usage**: foreground active time and AI turns for Claude, Codex, ChatGPT, Gemini, Antigravity, VS Code and more, with configurable daily goals, milestones, tone, quiet hours and cooldown.
* **Verified background agent / CLI task time**: settled **only** when a prompt start and an explicit final completion timestamp exist as a pair; parallel tasks are counted as a time union to avoid double counting ([ADR-010](docs/ADR-010-verified-background-agent-task-time.md)).
* **Continuous coverage ledger**: records the intervals collectors were actually observed running. Above threshold it reads `observed`; otherwise `partial` with the real ratio. **Gaps are never backfilled.**
* **Boundary**: these numbers represent observed foreground time only — **not productivity, not billable hours**. The `FOCUS` / `WEB` / `LOG` signals never substitute for one another.

### 5. 🧠 Memory Layer: Semantic Index, `omni ask`, Related History

* A 1024-dimension **local** index over AI turns, git commits, file metadata, open loops and project state, built with loopback Ollama `bge-m3`. Nothing is sent to a cloud provider ([ADR-005](docs/ADR-005-local-semantic-index-and-ask.md)).
* Incremental on `content_hash + embedding_model`; every row keeps its SQLite `source_ref`, project, timestamp, trust status and embedding-input degradation mode.
* `omni ask` runs retrieval-only, or has local Ollama generate an answer carrying `[S1]` citations.
* **Related History and work sessions**: derived by project + inactivity gap, adding no tables and rewriting no source events ([ADR-006](docs/ADR-006-derived-context-sessions-and-related-history.md)).
* **Boundary**: similarity is not proof of source truth or coverage; a session span is the first-to-last event delta, not real working time or focus quality.

### 6. 📚 DeskRAG Local Knowledge Base & Document Chat (optional: `pip install "omnicontext[rag]"`)

* **One web entry point, isolated index worker**: the dashboard and API stay on `http://127.0.0.1:8765`, while scanning, parsing, embedding, deletion and space maintenance run in a separate local process ([ADR-009](docs/ADR-009-deskrag-worker-index-lifecycle.md)).
* **Parser hub**: PDF (PyMuPDF, page numbers preserved), Word / PowerPoint / Excel, Markdown and code, WebVTT transcripts, plus virtual chunks synthesized from project state and open loops.
* **Hybrid retrieval**: FastEmbed (ONNX, `BAAI/bge-small-zh-v1.5`) + ChromaDB vectors, Jieba + BM25Okapi keywords, fused via Hybrid RRF, weighted fusion, vector-only or BM25-only.
* **Resident retrieval worker**: retrieval runs in a subprocess; the main service **never imports** Chroma / BM25 / embedding libraries (guarded by a clean-interpreter contract test). It warms up in the background and restarts automatically on timeout.
* **Multi-model chat**: local Ollama or cloud Gemini / Claude / OpenAI, SSE token streaming with citation cards; on Windows a citation opens File Explorer with the file selected.
* **Controlled lifecycle, honest capacity**: removing a folder index or clearing all indexes requires explicit confirmation and never deletes source files or chat history. Capacity figures come from the worker's latest verification receipt — unverified values display as pending rather than **passing an estimate off as a measurement**.
* **Space reclamation**: Chroma's `delete_collection` is a logical delete only — dropping an index does not shrink the directory. "Compact Chroma" answers two questions separately: is it logically gone, and did the disk actually shrink? If the internal structure cannot be read, nothing is deleted (fail-closed).

### 7. 🧩 Proactive Secretary: Propose → Approve → Act

* **Proposal-only core**: project state, actionable open loops and diagnostics become next-step proposals **with evidence refs**. The rule engine writes no event data and executes no commands ([ADR-007](docs/ADR-007-proposal-only-secretary.md)).
* **LLM advisory notes (optional, off by default)**: the LLM may only annotate existing proposals — it **cannot add, remove or execute** anything, and falls back to pure rules when unavailable.
* **Tiered executor L0 / L1 / L2 (three independent switches, all off by default)** ([ADR-008](docs/ADR-008-gated-agent-executor.md)):
  * **L0 / L1**: per-item approval for allowlisted actions (generate a handoff, `git fetch`, fast-forward pull, mark stale). The execute API accepts only a `proposal_id`; the action is chosen by a server-side allowlisted template, no shell is opened, a separate execution token is required, and every run leaves an audit receipt.
  * **L2 local agent CLI dispatch**: behind three gates (token + single-click approval + a one-time 6-digit confirm code) plus a cooldown, it dispatches **your own already-signed-in** Claude Code / Codex CLI to draft an action plan. Subprocess argv is allowlisted with no shell, cwd is confined to that project, the environment is rebuilt from an allowlist (**no API key is ever forwarded**), timeouts kill the process, and runs are cancellable.
  * **L2 write mode**: two-stage approval — you read the drafted plan first, then let the CLI modify files according to **that exact plan text**. The worktree must be clean before dispatch, it **never commits or pushes**, and changes are left for your `git diff` review.
* **Schedulable L0 tasks**: morning pack, daily digest, sync report, weekly/monthly rollups, weekly review, meeting notes and other **read-only** templates can be scheduled. **L1/L2 can never be scheduled** — enforced at module load and covered by an allowlist test.
* **Docs-behind-code detection**: compares a doc file's last modification against commits since, turning "the docs are stale" into an actionable card ([ADR-021](docs/ADR-021-docs-behind-code.md)). Repositories with no doc baseline are never mentioned.

### 8. 💬 Personalization: It Remembers, and It Does What You Declared

* **Memory ("the brain")** ([ADR-012](docs/ADR-012-secretary-memory.md)): typing "remember: …", "preference: don't remind me about repo_needs_push" or "decision @project: …" writes local notes. Every question injects today's state, the top three proposals and your notes (bounded in size, with a receipt, inspectable), and secretary observations can be deleted with one click.
* **Daily digest**: the L0 `daily_digest` template reduces each day's activity into a work log plus per-project observations — **collected ≠ known**. For the secretary to remember something, it has to land in `secretary_notes`.
* **Pattern-aware proposals** ([ADR-017](docs/ADR-017-pattern-aware-proposals.md)): a (project × day) activity matrix surfaces "you worked N days this week but have no daily schedule" and "X has been neglected", and weights your main line of work. **Only completed days are counted.**
* **Declared profile** ([ADR-018](docs/ADR-018-declared-profile.md)): "preference: priority: <project>" and "preference: tone: concise" — **what you said, not what was inferred**. Declared weight outranks inferred weight; tone changes wording only, never numbers.
* **Secretary desk (01 is the home page)** ([ADR-019](docs/ADR-019-secretary-desk-home.md)): deterministic rules pick one focus card and one "remember" item. The tool's own reminders (e.g. extension heartbeat) never take the focus slot; the full list is demoted to detail.
* **Weekly review — said vs. done** ([ADR-020](docs/ADR-020-weekly-review-said-vs-done.md)): places "you said X is the priority" next to "X moved on 1 day last week". **Two facts side by side are the insight — no speculation about why.**
* **Greeting card**: the top of tab 01 says what you did today and adds one line of encouragement. Every number traces back to a table; anything not collected (e.g. email) is stated as such on the card. LLM polish is off by default and **may not introduce a number absent from the statistics** — violations fall back to the rule-based version.

### 9. 📅 Calendar & Meeting Secretary

* **Local calendar (read-only .ics)** ([ADR-015](docs/ADR-015-local-calendar-source.md)): drop an Outlook / Google / Apple export or sync folder in place. The morning briefing gains a "today's agenda" section and the home page a "next up at 14:00 …" line. **Only start/end time, title, location and status are read** — descriptions, attendees and links never land on disk. **No cloud API is contacted.** No path configured means disabled.
* **Meeting secretary (layer 1: post-meeting transcripts)** ([ADR-022](docs/ADR-022-meeting-secretary.md)): put transcripts exported from Teams (or similar) in one folder and `meeting_notes` produces a summary plus **candidate** follow-ups, time-matched to that day's calendar event.
  * "You're in a meeting" uses **two deterministic signals only**: an in-progress calendar event, and the foreground window being a meeting app (application name only).
  * **Summaries default to a local provider (`ollama`)**. Choosing a cloud provider means sending other participants' words to that vendor — the settings page and the card both say so plainly. Prompt and response text is never persisted.
  * **A candidate follow-up becomes an open loop only when you click it** — unclicked ones appear in no count.
  * **Deliberately not done**: recording audio, reading meeting-app window contents, calling Teams/Graph APIs, auto-downloading transcripts. **Live captioning / translation is layer 2** and requires its own ADR plus the five gates in ADR-022 D6.

### 10. 🔔 Notifications: Desktop, Telegram, LINE & Daily Entry File

* **Native Windows desktop notifications**: WinRT Toast called directly — **no package to install, no account to register**. Morning briefing, evening recap, stalled-project alerts; `--dry-run` previews the content.
* **Daily entry brief**: `OMNICONTEXT_TODAY.md` / `.html` written to a folder you open every day; the HTML version refreshes every 5 minutes and works as a browser home page.
* **Multi-channel push** ([ADR-014](docs/ADR-014-multi-channel-push-and-arm-code.md), all off by default): one payload, rendered per platform. **Capability boundary**: the LINE Messaging API has no polling interface, and receiving messages would need a public webhook (breaking the "127.0.0.1 only" boundary), so **LINE is push-only**.
* **Secretary on your phone (Telegram, off by default)** ([ADR-013](docs/ADR-013-telegram-secretary-chat.md)): typing in the bound chat asks a question through the same pipeline as the dashboard; `/today` `/notes` `/status` `/proposals` are commands, and L0/L1 can be approved inline. **Boundary**: this is the only channel that sends questions and answers off your machine, which is why it ships disabled.
* **One-time unlock code**: `/arm` uses a dashboard-issued 6-digit short-lived code (single use, 5-minute expiry, burned on a wrong guess) so the phone never holds a long-lived secret. `/disarm` always works.

### 11. ⚡ Synthesis Engine: Custom Ranges & Two-Tier Incremental Summaries

* **Arbitrary date ranges** from the web UI, or the `Today` / `Yesterday` / `This week` / `Last 7 days` / `Last 30 days` shortcuts.
* **Two-tier (map-reduce) daily report**: after each periodic checkpoint, local Ollama compresses that window into a ≤100-character micro-summary (zero API cost); the daily report reads the micro-summary timeline plus raw fallback for gaps — roughly an order of magnitude less cloud token usage, with automatic fallback to raw excerpts when Ollama is unavailable, so **the report always gets produced**.
* **Multiple providers**: local Ollama by default; Google Gemini, Anthropic Claude and OpenAI also supported. `python main.py llm-test` diagnoses provider connectivity.
* **Open-loop extraction**: summaries distill unresolved items into the open-loop list for check-off.

### 12. ✅ Acceptance Center: Which Receipts Are Still Missing

* Turns every completion criterion in [docs/TODO.md](docs/TODO.md) section A into a **re-runnable read-only query** ([ADR-016](docs/ADR-016-acceptance-center.md)): `python main.py verify`, or "06 Settings → Acceptance Center".
* **Read-only**: it performs no acceptance action for you, runs no git, contacts no network.
* Its vocabulary strictly separates "**did not happen**" from "**cannot be observed**"; a human sign-off never overrides a machine verdict; values that exist only in memory (e.g. retrieval worker state) are marked `runtime_only` rather than falsely reported as "not done".

### 13. 🌐 Interface: Bilingual × Light/Dark × Accent

* One-click `🌐 English` / `🌐 繁體中文` in the top bar.
* Appearance is two independent axes: `data-theme` (dark/light) × `data-accent` (Naruto orange / forest green / ocean blue) — six combinations.
* Preferences live in browser `localStorage` only — **never written to `config.yaml`, never sent to the backend**.

---

## 🚀 Getting Started

Requires **Python 3.10+**.

```console
# Clone the repository
git clone https://github.com/dofliu/activityTracker.git
cd activityTracker

# Source checkout / development mode (includes the knowledge-base extra and test tools)
python -m pip install -e ".[dev]"
# Core only (collectors, secretary, Git sync, notifications; no knowledge-base indexing): ~170 MB
#   python -m pip install -e .
# Add the knowledge base later with: python -m pip install -e ".[rag]"

# Create local config, directories and a browser ingest token
python main.py init --watch "/your/project/root"

# Launch the dashboard and background collectors
python main.py
```

Then open **[http://127.0.0.1:8765](http://127.0.0.1:8765)**.

Using a prebuilt alpha wheel:

```console
python -m pip install omnicontext-1.3.0a5-py3-none-any.whl            # core
python -m pip install "omnicontext-1.3.0a5-py3-none-any.whl[rag]"     # core + knowledge base (DeskRAG)
omnicontext init --watch "/your/project/root"
omnicontext assets-status
```

**The knowledge base (DeskRAG) is an optional install**: the `[rag]` extra pulls in ChromaDB / FastEmbed / BM25 / jieba and the PDF / Office parsers (~550 MB). Without it everything else works; the "02 Knowledge Base" tab states which packages are missing and the install command, and chat still answers, just without document context.

An installed wheel keeps config, database and reports under the user-writable `~/OmniContext` rather than `site-packages`; override with `OMNICONTEXT_HOME` or `OMNICONTEXT_CONFIG`.

### LLM API Keys (optional)

Everything defaults to local Ollama. To use a cloud provider, store the key in an **OS environment variable** — `config.yaml` only records the `api_key_env` variable *name*, never the key itself:

```powershell
[Environment]::SetEnvironmentVariable("GEMINI_API_KEY", "your-gemini-api-key", "User")
```

On Windows the backend re-reads the User/Machine environment even when the OmniContext parent process started earlier; press "Re-check" under Settings → Summary & LLM.

> **Extension pairing, milestone configuration, backup and troubleshooting: see [docs/USAGE.md](docs/USAGE.md).**

---

## 💻 CLI Reference

With an installed wheel, replace `python main.py` with `omnicontext` or the shorter `omni`.

| Command | Description |
| :--- | :--- |
| `python main.py` / `run` / `web` | Start the web dashboard and background collectors |
| `init` | Create/update cross-platform config and the extension token (`--show-token`) |
| `now` | One-second view of active projects, recent activity and open loops |
| `resume` | Produce a project Context Handoff (`--copy` to clipboard) |
| `summary` | Generate an AI daily report (custom ranges, force refresh) |
| `checkpoint` | Snapshot the recent window into a Markdown log |
| `brief` | Write the daily brief to the daily entry directory |
| `notify` | Trigger a notification (`--dry-run`, `--channel`) |
| `status` | Database metrics and collector runtime state |
| `github status` / `github sync` | Inspect or sync GitHub repositories and PRs |
| `index` / `ask` | Build the local semantic index / query your history with sources |
| `sessions` / `recall` | Derived work sessions / similar history (query is never stored) |
| `open-loop` / `open-loop-reconcile` | Review open-loop lifecycle / backfill fingerprints and merge duplicates |
| `verify` | **Acceptance center**: check local receipts for TODO section A (read-only) |
| `llm-test` | Diagnose LLM provider connectivity and configuration |
| `backup` / `restore-drill` | Create and verify a backup / validate it in an isolated DB (live DB untouched) |
| `migration-status` | Read-only schema version and compatibility check |
| `maintain` / `heal` / `wal-checkpoint` | Lifecycle maintenance / collector self-healing / WAL truncation |
| `assets-status` / `extension-path` | Verify packaged assets / print the extension "Load unpacked" directory |

---

## ⚙️ Configuration

`main.py init` generates a local `config.yaml` from **[config.example.yaml](config.example.yaml)**, which is the **single authoritative list of settings** — every block carries inline comments describing its boundary, so they are not duplicated here. Most settings are editable with hot reload under dashboard "06 Settings".

A first install usually only needs these:

| Setting | Purpose |
| :--- | :--- |
| `project_resolution.search_roots` | Your project roots (drives resolution — **set this explicitly**) |
| `watchers.file_watcher.watch_directories` / `extensions` | Folders and file extensions to monitor |
| `watchers.git_watcher.repositories` | Git roots to scan recursively |
| `synthesizer.provider` | Summary provider (defaults to `ollama`, fully local) |
| `integrations.github.token` | Leave empty to use the local `gh auth token` |

**Every dangerous capability is off by default**: the secretary executor (which now also gates L0-only custom scheduling), L2, L2 write mode, Telegram chat, Telegram inline approvals (including `/arm`), LINE and greeting-card LLM polish. Calendar and meeting secretary are enabled but inert until a path is configured.

---

## 🧩 Chrome Extension

`python main.py extension-path` prints the "Load unpacked" directory; `python main.py init --show-token` yields the ingest token to paste into the popup. Only events from supported sites carrying a valid token may write to the local `/api/v1/events/ai`.

After pairing, `http://127.0.0.1:8765/extension-monitor` shows per-site observed state. **Full steps and the live verification flow: [docs/USAGE.md §3](docs/USAGE.md).**

---

## 📂 Repository Structure

```text
activityTracker/
├── main.py                     # Entry point and CLI dispatch
├── config.example.yaml         # Config template (init generates config.yaml from it)
├── pyproject.toml              # Packaging, CLI entry points, pytest settings
├── README.md / README_en.md    # Traditional Chinese / English documentation
├── ROADMAP.md / STATUS.yaml    # Plan and results / machine-readable status snapshot
│
├── docs/                       # 📚 Documentation (start at docs/INDEX.md)
│   ├── USAGE.md                # User guide
│   ├── TODO.md                 # Backlog with completion criteria
│   ├── NEXT_SESSION.md         # Developer handoff guide
│   ├── ADR-001 ~ ADR-022       # Architecture decision records
│   └── archive/                # Archived one-off plans and completion reports
│
├── core/                       # Core services
│   ├── server.py               # FastAPI REST API and static server
│   ├── llm_client.py           # the one LLM client: Ollama / Gemini / Claude / OpenAI, sync + streaming
│   ├── activity_sources.py     # the one definition of activity: which tables, project attribution, day bounds
│   ├── manager.py              # Collector supervision and supervise_and_heal
│   ├── database.py / migrations.py / models.py   # SQLite and append-only migrations
│   ├── security.py / secret_resolver.py          # Origin boundary and secret resolution
│   ├── data_lifecycle.py       # Online backup, WAL checkpoint, pruning, integrity receipts
│   ├── project_engine.py / project_paths.py      # Project resolution and root detection
│   ├── semantic_index.py / context_memory.py     # Local embeddings and related history
│   ├── handoff_engine.py       # Provider-neutral context handoff
│   ├── proactive_secretary.py / secretary_advisor.py   # Proposal engine and LLM advisory
│   ├── agent_executor.py / agent_dispatch.py / scheduled_tasks.py  # L0/L1/L2 and scheduling
│   ├── secretary_memory.py / secretary_profile.py      # Memory and declared profile
│   ├── secretary_home.py / secretary_greeting.py / secretary_packs.py  # Desk / greeting / packs
│   ├── activity_digest.py / activity_patterns.py / weekly_review.py    # Digest / patterns / review
│   ├── meeting_transcripts.py  # Meeting secretary (WebVTT, pairing, fact gate, follow-ups)
│   ├── docs_freshness.py       # Docs-behind-code detection
│   ├── ics_parser.py / calendar_agenda.py        # Local read-only .ics calendar
│   ├── repo_sync.py / repo_onboarding.py / repo_sync_report.py  # Git sync center
│   ├── acceptance.py           # Acceptance center (executable copy of TODO section A)
│   ├── usage_analytics.py / capture_coverage.py / coverage_ledger.py
│   ├── background_tasks.py / triage_signals.py / status_draft.py
│   └── platform_services.py / runtime_paths.py / fs_utils.py / time_utils.py
│
├── rag/                        # 📚 DeskRAG subsystem
│   ├── router.py               # /api/v1/rag/* REST API and SSE streaming chat
│   ├── scanner.py / index_worker.py / jobs.py / lifecycle.py   # Controlled index worker
│   ├── retrieval_worker.py / retrieval_client.py               # Resident retrieval worker
│   ├── parsers/ chunker.py embeddings.py vector_store.py retriever.py
│   ├── storage.py              # Capacity reporting and Chroma space reclamation
│   ├── activity_indexer.py     # Project state and open-loop virtual chunks
│
├── watchers/                   # Collectors
│   ├── file_watcher.py git_watcher.py window_watcher.py
│   ├── agent_log_watcher.py    # Claude Code/Desktop, Codex, Antigravity
│   ├── calendar_watcher.py     # Local .ics
│   └── browser_extension/      # Chrome MV3 extension
│
├── synthesizer/                # Synthesis and scheduling
│   ├── aggregator.py prompt_templates.py scheduler.py
│   └── micro_summarizer.py / rollup.py    # Two-tier micro-summaries, weekly/monthly rollups
│
├── notifiers/                  # Notification channels
│   ├── messages.py / channels.py          # Content/presentation split, adapter capabilities
│   ├── desktop_notifier.py                # Windows WinRT Toast transport (zero dependency)
│   ├── telegram_chat.py / telegram_approvals.py / telegram_setup.py
│   └── line_setup.py / secretary_push.py
├── integrations/github_client.py          # GitHub API client
├── exporters/daily_brief.py               # OMNICONTEXT_TODAY.md/.html
│
├── web/                        # Dashboard frontend (6 tabs + extension-monitor)
│   └── index.html / app.js / style.css
│
├── scripts/                    # Verification, cleanup, autostart and E2E scripts
├── tests/                      # 67 contract test modules (684 tests)
├── logs/checkpoints/           # Periodic activity snapshots
└── reports/                    # Daily / range Markdown reports
```

---

## 🗺️ What Makes This Different

Comparable tools (ActivityWatch, RescueTime, Timing) track **time**; Rewind and Screenpipe record the screen and OCR it, at a high privacy and resource cost.

**No mainstream tool currently reads local AI agent transcripts.** `~/.claude/projects/`, `~/.codex/sessions/` and `.gemini/antigravity/brain/` are already on disk — no screen recording, no extra permissions — and what they contain is the actual reasoning: what was asked, how the AI answered, what was decided.

Going from *log* to *memory* is the through-line of this project: the point now is not to collect more, but to make what exists **retrievable and usable by the secretary**.

> More collection is not more useful: file events went from 3,575 noisy rows → 4,327 → down to 789.
> **Every new collection source must first pass the "can this change a decision?" test.**

Full phase plan, measured comparisons and the results log: **[ROADMAP.md](ROADMAP.md)**; next-stage direction and trade-offs in §12.

### Current Operating Assumptions

This is a **personal-first** project at this stage:

* Window capture, desktop notifications and startup scheduling are **Windows-only**; everything else is cross-platform (CI covers Windows / Ubuntu / macOS).
* `project_resolution.search_roots` falls back to the file/Git watcher roots when unset, so a first install should still set it explicitly.
* PyPI publishing is out of scope; releases are GitHub pre-releases only (wheel/sdist + SHA-256 receipt).

---

## 🔒 Privacy & Security

* **Events stay local**: activity events live in a local SQLite database (`omni_context.db`) with no third-party analytics telemetry.
* **LLM data boundary**: choosing Gemini / Anthropic / OpenAI for synthesis sends the assembled work context to that provider; **only Ollama is fully local inference**. The meeting-transcript summary provider is configured separately and defaults to `ollama`.
* **Local API**: deny-by-default origin boundary, loopback-only default, sensitive-setting redaction and a browser-extension ingestion capability ([ADR-001](docs/ADR-001-p2-5-trust-boundary.md)).
* **Data trust**: a canonical AI event must carry `turn_key`, source provenance and `response_status`; partial/legacy responses never become a summary or handoff conclusion.
* **Backup lifecycle**: `backup` uses the SQLite Online Backup API and emits integrity / SHA-256; `restore-drill` validates schema and row counts in an isolated temporary DB and **never overwrites the live DB**.
* **Schema migration**: an append-only registry records version/name/checksum and takes a verified backup before upgrading; checksum mismatch or an unknown newer version **fails closed** ([ADR-003](docs/ADR-003-versioned-sqlite-migrations.md)).
* **Artifact boundary**: the wheel/sdist content receipt rejects any bundled `config.yaml`, SQLite database or local secret.
* **Commit protection**: database files, API keys and personal Markdown reports are in `.gitignore` by default.

---

## 📄 License

Released under the [MIT License](LICENSE).
