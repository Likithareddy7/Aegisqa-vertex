# AegisQA

Enter a URL, then chat in plain English -- "generate test cases for the login page",
"test an invalid password" -- and AegisQA's agent inspects the real page in a browser,
writes structured test cases, runs them, and shows steps, screenshots, logs, failures and
suggested fixes. Edit and approve test cases, rerun failures, and export any test case as
a standalone Playwright script.

Also included: login with per-project roles and an audit log, named environments with
encrypted credentials, self-healing selectors with a review queue, console/network log
and trace/video capture, a reporting dashboard, batched "run all", CSV/JSON data-driven
runs with secret masking, reusable versioned flows, visual regression (baselines,
pixel-diffs, approve/reject), CI export (spec zip, CLI runner, JUnit/HTML reports) and
failure webhooks.

## Architecture

```
Next.js UI (packages/frontend)
   │  REST + WebSocket (/ws)
   ▼
Python backend (packages/backend) ── FastAPI ───────────────────────────────┐
   │   auth + roles, projects, test cases, runs, reports, exports            │
   │                                                                         │
   ├── Google ADK agents ──► Gemini on Vertex AI                             │
   │      test generation · self-healing · failure analysis · step builder   │
   │                                                                         │
   ├── Playwright MCP browsers (npx @playwright/mcp, or a remote MCP server) │
   │      chat exploration · test execution                                  │
   │                                                                         │
   ├── Memory (aegisqa/memory/) -- learning loop + hybrid search:            │
   │      exact: Postgres · semantic: pgvector | Vertex AI Vector Search     │
   │      keyword: Postgres full-text | Firestore BM25                       │
   │                                                                         │
   ├── Artifacts: local disk | Cloud Storage                                 │
   ├── Test runs: in-process | Pub/Sub + GKE worker                         │
   │                                                                         │
   └── PostgreSQL (Cloud SQL on GCP) via SQLAlchemy + Alembic ───────────────┘
```

Every GCP service is optional locally: with the defaults, the backend needs only
PostgreSQL (with pgvector) and, for the AI features, Gemini credentials. On GCP each piece
is switched on by environment variables -- see
[`docs/GCP_HANDOFF.md`](./docs/GCP_HANDOFF.md).

- **Memory** -- everything the platform learns is remembered as it happens: test cases
  (created, edited, approved), failed runs (with an error code and the suggested fix),
  approved self-heals and filed defects. Search combines keyword and semantic matching
  (reciprocal rank fusion). Used by the test generation agent (its
  `search_project_memory` tool, so it checks what exists before writing tests) and by
  `GET /api/projects/{id}/memory`. If
  embeddings aren't available, it falls back to keyword search.
- **Background work** -- with `TASK_QUEUE=pubsub`, test runs go through Pub/Sub to
  `python -m aegisqa.worker` (GKE); a run request returns a pending run, and progress
  streams back live.

- **Frontend** -- Next.js (App Router) + React. Talks to the backend over REST and one
  WebSocket per project for chat streaming and live run progress.
- **Backend** -- Python 3.10+, FastAPI. One service, deployable as one container.
- **Agents** (`aegisqa/agents/`) -- four [Google ADK](https://google.github.io/adk-docs/)
  `LlmAgent`s on Gemini:
  - `test_generation_agent` drives the chat. Its tools are the live Playwright MCP tools
    (via ADK's `McpToolset`; one isolated browser per user per project) plus
    `propose_test_cases` / `update_test_case`, which save to the database.
  - `self_healing_agent` re-locates an element whose selector broke during a run.
  - `failure_analysis_agent` explains a failed step and suggests a fix.
  - `step_builder_agent` turns one instruction into one step in the editor.
- **Execution** (`aegisqa/execution/`) -- replays a test case's steps in a fresh browser.
  Selectors are resilient descriptors (role+name, label, placeholder, text, test id; CSS
  or XPath only as a last resort), re-resolved against a live accessibility snapshot on
  every run. If a selector no longer matches: previously-approved alternates, then raw
  CSS/XPath, then the self-healing agent. Anything recovered is queued as a
  `HealingEvent` for a human to approve -- nothing changes on its own.
- **Database** -- PostgreSQL. Models in `aegisqa/db/models.py`, migrations in
  `migrations/` (Alembic).

### Behaviour worth knowing

- **Privacy**: chat history and the agent's activity timeline are private per user;
  only the test cases a chat produces are shared with the project.
- **Roles**: a site-wide `admin` (always treated as project owner) or `member`; per
  project `owner` (members, credentials), `editor` (create/edit/run),
  `reviewer` (approve/reject -- never your own test case) and `viewer` (read + run).
  Every route checks project membership. Security-relevant actions go to an audit log.
- **Credentials**: environment credentials and integration tokens are AES-256-GCM
  encrypted at rest and never returned by the API. Steps reference credentials as
  `{{env.KEY}}`; they're substituted server-side just before the browser action, so they
  never reach chat or the model, and are masked in stored results.
- **Data-driven runs**: a CSV/JSON data set per test case; steps reference
  `{{data.COLUMN}}`. "Run with data" runs once per row. Secret columns are masked in
  results and become `process.env` references in exports.
- **Continue from chat / rerun from step N**: a run can start from the chat browser's
  cookies, or from the browser state saved after step N-1 of an earlier run.
- **Visual regression**: `assertVisualMatch` pixel-diffs against a stored baseline; a
  diff beyond the threshold fails the step and waits for approve/reject.
- **Flows**: named, versioned step sequences. Inserting one copies its steps into a test
  case; a newer version is only applied on an explicit "update".
- **Cost tracking**: every model call records tokens and an estimated cost
  (`TokenUsage`). The per-model rates in `aegisqa/db/records.py` are placeholders --
  set your contracted Gemini prices before relying on the cost figures.

## Setup

Prerequisites: Node.js 20+, Python 3.10+, PostgreSQL 15+, and either Vertex AI access
(`gcloud auth application-default login`) or a Google AI Studio API key for local use.

```bash
# PostgreSQL (macOS): brew install postgresql@17 && brew services start postgresql@17
createdb aegisqa

npm install                                              # frontend + Playwright MCP
npx @playwright/mcp install-browser chrome-for-testing   # the browser it drives

cd packages/backend
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"     # Windows: .venv\Scripts\pip install -e ".[dev]"
cp .env.example .env                  # then fill it in -- see below
cd ../..

npm run db:migrate    # create the tables
npm run seed          # create the admin login (npm run seed:demo also adds a demo project)
```

In `packages/backend/.env` set at least `DATABASE_URL`, `GOOGLE_CLOUD_PROJECT` (or
`GOOGLE_API_KEY`), `ADMIN_USERNAME` / `ADMIN_PASSWORD` and `CREDENTIAL_ENCRYPTION_KEY`.
Every setting is described in [`packages/backend/.env.example`](./packages/backend/.env.example).

## Run

```bash
npm run dev:backend    # http://localhost:4000
npm run dev:frontend   # http://localhost:3000
```

Sign in with the admin account, create a project with a target URL, and start chatting.
`GET http://localhost:4000/api/health` shows the backend is up and which model it uses.
To try a single agent in ADK's dev UI: `npm run adk:web`.

## Project structure

```
packages/
  frontend/   Next.js UI
  shared/     TypeScript wire types used by the frontend (the API contract)
  backend/    Python backend
    aegisqa/
      main.py          app factory, WebSocket handler, static files
      api/             REST routes
      auth/            passwords, sessions, role checks (where SSO plugs in)
      agents/          ADK agents, prompts, tool schemas, browser sessions (McpToolset)
      chat.py          chat turns, the save-test-case tools, one-shot agent calls
      memory/          memory layer: stores, hybrid search, learning loop, backfill
      storage.py       artifact storage (local disk or Cloud Storage)
      tasks.py         Pub/Sub publishing and the /internal endpoints
      worker.py        test-run worker for queued execution (GKE)
      execution/       test runner, selector resolution, visual diffs, exporter, reports
      integrations/    Azure DevOps (API only; no UI)
      db/              models, queries, serializers
      cli/run_tests.py CI runner
      seed.py          first admin login
    migrations/        Alembic migrations
    adk_apps/          entry points for `adk web`
    tests/             unit, in-process app, real-browser and API contract tests
docs/GCP_HANDOFF.md    deployment notes for the GCP engineer
```

## Tests

```bash
npm run test:backend                       # scripted model -- no Gemini calls needed
TEST_DATABASE_URL=postgresql://localhost/aegisqa_test npm run test:backend   # + database tests
npm run typecheck                          # frontend + shared
```

- Tests use a scripted stand-in for Gemini, so they run without credentials.
  `-m "not browser"` skips the ones that start a real Chromium.
- Database tests drop and recreate the schema of `TEST_DATABASE_URL`, so they refuse
  any database whose name doesn't contain "test".
- `tests/contract/` is a black-box suite for a *running* backend (set
  `CONTRACT_BASE_URL`, `CONTRACT_DATABASE_URL`, `CONTRACT_ADMIN_USERNAME`,
  `CONTRACT_ADMIN_PASSWORD`). It was used to prove the Python backend serves the same
  API as the original Node one.

## CI/CD

- **Export**: "Export CI package" on a project (`GET /api/projects/{id}/export-ci`) zips
  every approved test case as a standalone `@playwright/test` project: config,
  `package.json`, one spec per test case and a README listing required env vars.
- **CLI runner**: `aegisqa-run-tests` (installed with the backend;
  `aegisqa/cli/run_tests.py`) signs in, runs every approved test case, writes
  `junit-results.xml` and exits non-zero on failure. Flags or `CI_*` env vars.
- **Reports**: `GET /api/testruns/batch/{batchId}/junit` and `.../html`.
- **Webhook**: set `WEBHOOK_URL` for a Slack-compatible post when a run doesn't pass.

## Known limitations

- **Live updates and chat browsers are per backend instance.** WebSocket rooms and open
  chat browsers live in process memory, so run one backend instance, or use session
  affinity. See [`docs/GCP_HANDOFF.md`](./docs/GCP_HANDOFF.md). With inline background
  work, a run interrupted by a restart is marked failed and can be rerun.
- **Login is username/password** for now. The client's SSO plugs in at
  `aegisqa/auth/sessions.py`.
- **"Continue from chat session" needs local browsers.** It reads a file Playwright MCP
  writes, so it doesn't work when `PLAYWRIGHT_MCP_URL` points at a remote server.
- **"Rerun from step N" restores cookies/localStorage and the URL only**, not in-memory
  page state.
- **Exported specs aren't checked for ambiguity.** `text`/`css`/`xpath` locators are
  flagged with a comment, but uniqueness is only enforced by Playwright at run time.
- **Trace/video capture depends on the browser supporting Playwright MCP's `devtools`
  capability**; otherwise those fields stay empty.

## Roadmap

From the target architecture, not built yet:
- **Knowledge graph** of runs and defects with risk scores. Today they're memory records
  with error codes.
- **Multi-instance WebSocket fan-out**, so the backend can scale beyond one instance.
- **SSO** (the client's provider).
