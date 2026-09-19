# Changelog: WhatsApp Outreach Automation

All notable changes to this project are documented in this file.

## [Phase 7.7] - 2026-09-20 — IMPLEMENTED & VERIFIED
### Added
- **Oracle Cloud A1 ARM64 Browser POC & Binary Configuration Plumbing**:
  - Validated headless Chromium execution on Oracle Cloud Always Free A1 Flex (Ubuntu 24.04 LTS, aarch64, 2 OCPU, 12 GB RAM, 4 GB swap).
  - Identified and resolved Canonical Snap wrapper limitation: `/snap/bin/chromium` fails with `execvp: /snap/bin/chromium`; verified raw ELF binary path `/snap/chromium/current/usr/lib/chromium-browser/chrome` and system ChromeDriver `/usr/bin/chromedriver`.
  - Wired canonical settings (`WHATSAPP_CHROME_BINARY` and `WHATSAPP_CHROMEDRIVER_PATH`) through `Settings` $\to$ `WhatsAppWebProvider` $\to$ `WhatsAppBrowser` $\to$ Selenium `Service(executable_path=...)` and `options.binary_location`.
  - Propagated browser and driver configuration in `app/runner/production_runner.py` and `app/cli/commands/session.py`.
  - Preserved strict backward compatibility for Windows and standard Google Chrome environments when custom paths are left unconfigured.
  - Documented Snap Chromium symlink and ChromeDriver configuration in `deploy/worker/env.worker.example`.
  - Added comprehensive unit tests in `tests/test_whatsapp_browser_mock.py` and `tests/test_whatsapp_provider.py` covering default, explicit, and settings-fallback driver and binary resolution.

## [Phase 7.6] - 2026-09-15 — APPROVED
### Added
- **Deterministic WhatsApp Command Protocol**:
  - Implemented single in-flight command serialization with optimistic CAS (`version` check) in `app/services/whatsapp_command_service.py`.
  - State lifecycle transitions: `REQUESTED` $\to$ `CLAIMED` $\to$ `EXECUTING` $\to$ `COMPLETED` / `FAILED`.
  - Enforced single in-flight serialization returning HTTP 409 Conflict upon concurrent request submissions.
  - 60-second lease with automatic stale and orphaned command recovery on worker startup and main loop.
- **WhatsApp Web Operations REST API (`/api/v1/whatsapp/*`)**:
  - `GET /api/v1/whatsapp/status`: Live connection, session state, in-flight command, and sanitized storage status.
  - `GET /api/v1/whatsapp/diagnostics`: Fully sanitized host diagnostics (Chrome binary availability, major version, profile writable state, profile directory size).
  - `GET /api/v1/whatsapp/commands/{request_id}`: Granular lifecycle tracking of in-flight or completed commands.
  - `POST /api/v1/whatsapp/health-check`: OPERATOR+ non-destructive UI element ping.
  - `POST /api/v1/whatsapp/reconnect`: OPERATOR+ soft recovery reloading DOM and re-authenticating.
  - `POST /api/v1/whatsapp/disconnect`: OPERATOR+ controlled graceful session shutdown releasing Chrome and profile locks.
  - `POST /api/v1/whatsapp/logout`: ADMIN/OWNER destructive session unlink requiring confirmation phrase `CONFIRM-LOGOUT`.
  - `POST /api/v1/whatsapp/commands/clear-stale`: ADMIN/OWNER administrative lease override with audit logging.
- **Worker-Side WhatsApp Command Handler (`app/runner/whatsapp_command_handler.py`)**:
  - Integrated into `ProductionRunner` for polling, claiming, executing domain operations, and emitting telemetry to `system:whatsapp_telemetry`.
  - Startup orphan recovery reconciling crashed or abandoned command leases.
- **Integra Design System (IDS) UI Views (`/whatsapp`)**:
  - Top provider status banner with semantic disclaimer guarantee.
  - 4 primary KPI cards: Session State, In-Flight Command, Profile Storage, Lease Expiration.
  - Controlled action panels with confirmation modals and CSRF token protection.
  - Operational runbook guidance and sanitized diagnostics card.
  - Operational audit trail displaying real-time command dispatch history.
  - Complete removal of legacy "Emergency Disconnect".
  - Active sidebar navigation link in `app/web/templates/base.html`.
- **Testing & Verification**:
  - 22 new tests across command service, operations API, UI rendering, and architectural boundaries.
  - Complete regression run: 408 tests passing, 0 failing (100% pass rate).

---

## [Phase 7.5] - 2026-09-12 — APPROVED
### Added
- **Analytics & Reporting Control Center (`/analytics`, `/analytics/campaigns/{id}`)**:
  - Executive analytics dashboard featuring time-window filtering (`today`, `yesterday`, `last_7_days`, `last_30_days`, `custom`), 4 primary KPI cards, live queue health strip, responsive SVG throughput histogram, and campaign performance table.
  - Granular campaign analytics deep-dive view featuring audience funnel, cardinality-safe contact completion % progress, 8-state message outcome grid, and pacing safeguards.
  - Activated `/analytics` sidebar navigation item in `app/web/templates/base.html`.
- **Authoritative Confirmed Send Rate & Invariant Enforcement**:
  - Locked formula: `confirmed_sends / (confirmed_sends + failed + unknown_outcome) * 100`.
  - Strict exclusion of `RETRY_PENDING`, `SKIPPED`, `CANCELLED`, `QUEUED` from the rate denominator.
  - Zero occurrences of prohibited terms (`Delivered`, `Delivery Rate`, `Read`, `Success Rate`, `Archived`, `Sending`) on the feature surface.
  - Prominent mandatory semantic disclaimer banner rendered on all UI views and API payloads.
- **Cardinality-Safe Campaign Completion**:
  - Maintained $1:N$ integrity between `CampaignContact` and `Message`.
  - Contact Outreach Completion % calculated exclusively at `campaign_contacts` level: `COUNT(status IN ('SENT','FAILED','SKIPPED','EXCLUDED')) / COUNT(id) * 100`.
  - Queue Terminal % tracked separately from `messages`: `COUNT(status IN ('SENT','FAILED','SKIPPED','CANCELLED')) / COUNT(id) * 100`.
- **Timezone Authority (`app/utils/timezone.py`)**:
  - Anchored all calendar semantics to `APP_TIMEZONE` (`Africa/Cairo`).
  - Converts start/end of day boundaries to UTC half-open intervals `[start_utc, end_utc)` before database queries.
- **Decoupled Queue Analytics (`/api/v1/analytics/queue/live` vs `/historical`)**:
  - `GET /api/v1/analytics/queue/live`: Live point-in-time snapshot with zero date filtering.
  - `GET /api/v1/analytics/queue/historical`: Time-windowed metrics (dispatches, failures, lease duration, retry distribution, timeline).
- **Fail-Safe Streaming CSV Export (`/export/campaign/{id}`, `/export/summary`)**:
  - RBAC: `VIEWER` gets 403 Forbidden; `OPERATOR` gets masked phone numbers (`+201******678`); `ADMIN` and `OWNER` get full E.164.
  - Message body content strictly omitted.
  - 5-stage lifecycle: emits `AuditLog(event_type="ANALYTICS_REPORT_EXPORTED", status="SUCCESS")` on clean cursor exhaustion; emits `AuditLog(event_type="ANALYTICS_REPORT_EXPORT_FAILED", status="INTERRUPTED")` if disconnected early.
- **Testing & Verification**:
  - Comprehensive 18-test suite in `tests/web/test_analytics_service_and_api.py`.
  - Full regression run: 386 tests passing (100% pass rate).

---

## [Production Deployment Checkpoint] - 2026-09-12 — IMPLEMENTED & VERIFIED (AWAITING HUMAN APPROVAL)
### Added
- **PostgreSQL Database Support & Engine Abstraction**:
  - Added `psycopg2-binary>=2.9.9` driver dependency in `requirements.txt`.
  - Dialect-aware engine configuration in `app/database/connection.py`: SQLite maintains thread checking disabled and WAL/foreign-key PRAGMAs; PostgreSQL activates QueuePool with configurable `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_RECYCLE` (1800s), and `DB_POOL_PRE_PING` (True).
  - Dialect-safe Alembic migrations (`dfc1d4304168`, `230fa779a978`, `9a1b2c3d4e5f`) verified under PostgreSQL offline DDL generation (`alembic upgrade head --sql`) and SQLite runtime execution.
- **Serverless Logging Adaptation**:
  - Updated `app/utils/logger.py` to inspect `VERCEL` and `LOG_TO_FILE`.
  - Automatically disables rotating file handlers in serverless contexts, streaming sanitized structured JSON/text records to stdout/stderr for native Vercel Log Drains.
- **Vercel Control Plane Integration**:
  - Root `vercel.json` configuring `@vercel/python` runtime for ASGI entrypoint `api/index.py` and static asset routing to `/app/web/static/`.
  - Added `ProxyHeadersMiddleware` in `app/web/app.py` for client IP and HTTPS reverse proxy detection.
- **Remote Runner Desired-State Coordination**:
  - Decoupled serverless web control plane from worker process management via `system:desired_runner_state` and `system:desired_runner_campaign_id` in `AppSetting`.
  - Serverless web control plane avoids invoking local `subprocess.Popen` or `os.kill`, setting desired state and tracking remote heartbeat liveness.
  - `ProductionRunner` checks desired state on every polling iteration, enabling safe remote stops without orphan leases.
- **Secure Owner CLI Bootstrap**:
  - Added `outreach auth bootstrap-owner` CLI command to provision the initial `OWNER` directly against the database, avoiding exposure of initial setup credentials over public HTTP.
- **Worker VPS Deployment Unit**:
  - Production systemd service unit `deploy/systemd/outreach-runner.service` with process isolation, environment variables, and graceful termination handling.
  - Configuration templates `deploy/worker/env.worker.example` (direct port 5432) and `deploy/vercel/env.vercel.example` (transaction pooler port 6543).

---

## [Phase 7.4] - 2026-09-12 — APPROVED
### Added
- **Queue & Message Operations Control Plane**:
  - Operational Queue dashboard at `/queue` and granular message inspection at `/queue/{message_id}`.
  - Activated `/queue` navigation links across all Web Control Center views.
  - Authoritative message state representation: `PENDING`, `QUEUED`, `PROCESSING`, `SENT`, `FAILED`, `RETRY_PENDING`, `CANCELLED`, `SKIPPED`, and ambiguous `UNKNOWN_OUTCOME`.
- **Operational Queue Web Services & APIs (`app/web/services/queue_service.py`, `app/web/routes/api/queue.py`)**:
  - `GET /api/v1/queue`: Server-side filtering by status, campaign, contact search, retry status, and date range with bounded pagination.
  - `GET /api/v1/queue/stats`: Real-time status breakdown, stale worker lease tracking, Emergency Stop / Circuit Breaker telemetry, and locked Confirmed Send Rate.
  - `GET /api/v1/queue/{message_id}`: Comprehensive inspection including lifecycle timestamps, retry metrics, idempotency key, lease state, rendered content (read-only, never logged), and recent audit history.
  - `POST /api/v1/queue/reconcile`: Stale worker lease recovery (>120s) using authoritative Phase 3 `PersistentQueueService.recover_stale_leases()` (`OPERATOR+`, CSRF-protected, immutable audit trail).
  - `POST /api/v1/queue/{message_id}/cancel`: Cancellation of non-terminal messages using Phase 3 `cancel_message()` (`OPERATOR+`, CSRF-protected, immutable audit trail).
  - `POST /api/v1/queue/{message_id}/resolve-unknown`: Strictly audited manual reconciliation for `UNKNOWN_OUTCOME` messages following Phase 5 semantics (`OPERATOR+`, CSRF-protected, mandatory reason, and exact phrase `CONFIRM-NOT-DELIVERED`).
- **Autonomous Retry Semantics Preservation (Inspection Only)**:
  - Preserved Phase 3 automated retry architecture: Retries remain strictly autonomous, governed by exponential backoff with jitter (`RetryManager`) and claimed by `QueueWorker`.
  - Manual retry mutations deferred; zero ad-hoc bypasses of backoff or retry caps.
- **Privacy & Security Enforcement**:
  - Contact phone number masking (`+201******678`) for `VIEWER` and `OPERATOR` roles; raw E.164 reserved for `ADMIN` and `OWNER`.
  - Double-submit CSRF protection on all mutation endpoints.
  - Zero database migrations required; fully compatible with existing indexed `messages` schema.
- **Verification & Test Suite**:
  - 13 comprehensive unit and integration tests in `tests/web/test_queue_service_and_api.py` and `tests/web/test_queue_ui_views.py`.
  - Zero live browser calls or external WhatsApp dispatches.

---

## [Phase 7.3] - 2026-09-12 — APPROVED
### Added
- **Reusable Message Template Library & Immutable Versioning**:
  - `message_templates` & `message_template_versions` database tables with bidirectional migration `a1b2c3d4e5f6_phase_7_3_templates.py`.
  - Immutable version history: Versions are strictly append-only; historical versions cannot be altered or deleted.
  - Template syntax and variable validation: Reuses `MessageTemplateService` with allowed variable set (`{{name}}`, `{{company}}`, `{{city}}`, `{{campaign}}`).
  - Template preview rendering using ephemeral contact and campaign mocks.
- **Campaign Management Web Service & APIs**:
  - `CampaignService` & `/api/v1/campaigns/*` endpoints for listing, filtering, creation, detail inspection, and status transitions.
  - Immutable template snapshotting: At creation time, template content is snapshotted into `campaigns.message_template`, preventing live template edits from mutating active outreach.
  - Strict domain state machine enforcement: DRAFT -> RUNNING, RUNNING -> PAUSED, etc.
  - Domain isolation invariant: Setting a campaign to `RUNNING` is strictly a domain state transition; it does not start the runner daemon.
- **Contact Management & Privacy Masking**:
  - `ContactService` & `/api/v1/contacts/*` endpoints for CRUD, search, and consent/status filtering.
  - Role-based privacy masking: Phone numbers are masked for non-privileged viewers (`+201******678`), visible in full E.164 only for `ADMIN` and `OWNER` roles.
  - Two-phase CSV Import: Phase 1 Dry-Run validation (reports valid count, duplicates against DB and batch, phone syntax errors, missing fields, and preview rows) followed by Phase 2 explicit Commit.
  - Standard CSV export of active contacts with audit trail logging.
- **Campaign-Contact Membership & Eligibility Service**:
  - `CampaignContactService` linking contacts to campaigns with eligibility rules (`ELIGIBLE`, `EXCLUDED`, `PENDING`).
  - Draft state protection: Contacts can only be removed while a campaign is in `DRAFT` state.
- **Modern Web Control Center UI**:
  - HTML templates for Campaigns (`list.html`, `create.html`, `detail.html`), Templates (`list.html`, `create.html`, `detail.html`), and Contacts (`list.html`, `import.html`, `detail.html`).
  - Active navigation bar tabs for Campaigns, Contacts, and Templates.
- **Verification & Testing**:
  - 28 new tests in `tests/web/`, bringing test suite to 337 passing tests (100% pass rate).
  - 93% test coverage across all `app/web` modules.
  - Zero live browser dispatches or external WhatsApp communications during tests.

---

## [Phase 7.2] - 2026-09-11 — APPROVED
### Added
- **Operational Dashboard Aggregation (`app/web/services/dashboard_service.py`)**:
  - Unified operational telemetry service aggregating Phase 6 health checks, database latency, WhatsApp profile verification, emergency stop state, circuit breaker status, active campaign performance, queue summary, runner telemetry, and dynamic alert generation.
  - Zero N+1 queries; bounded, high-performance database reads.
- **Safe Runner Supervisor Service (`app/web/services/runner_control_service.py`)**:
  - Authoritative OS file lock enforcement (`data/runner.lock`).
  - Campaign state validation (`RUNNING` status enforced).
  - Preflight check verification before process launch.
  - Graceful stop via SIGTERM with 15s safe cancellation window; preserves in-flight browser sends.
  - Stale lock detection and recovery for dead PIDs.
  - Immutable audit trail recording `RUNNER_START_REQUESTED` and `RUNNER_STOP_REQUESTED`.
- **Control Plane REST APIs (`app/web/routes/api/`)**:
  - `/api/v1/dashboard/summary`, `/health`, `/queue`, `/campaign`, `/runner`.
  - `/api/v1/runner/status`, `/start`, `/stop` (role-gated to `OPERATOR+` with double-submit CSRF verification).
  - `/api/v1/system/emergency-status`, `/emergency-stop` (`OPERATOR+`, CSRF-protected, mandatory reason), `/emergency-resume` (`ADMIN+`, CSRF-protected, mandatory reason).
  - `/api/v1/events/stream` (authenticated Server-Sent Events telemetry stream with `X-Accel-Buffering: no` and keep-alive ping).
- **Interactive Dashboard UI & Telemetry Client (`app/web/templates/dashboard.html`, `app/web/static/`)**:
  - KPI summary cards (System Health, Confirmed Send Rate, Queue Depth, Runner State).
  - Subsystems telemetry grid (DB, WhatsApp, Emergency Stop, Circuit Breaker).
  - Active Campaign card with progress bar, completed contacts breakdown, and strict semantic disclaimer.
  - Queue Breakdown with prominent highlighted isolation of `UNKNOWN_OUTCOME` items requiring physical verification.
  - Safe Runner and Emergency Stop control cards with confirmation modals and reason inputs.
  - SSE telemetry client with automatic 5-second polling fallback on connection interruption.
  - Strict semantic enforcement: Prohibited terms like "delivery rate", "delivered", or "read" are completely excluded.
- **Verification & Testing**:
  - 40 new unit and integration tests added, bringing `tests/web/` to 90 passed tests (100% pass rate).
  - 91% code coverage across all `app/web` modules.
  - 309 total tests passing across entire repository (100% pass rate, 0 regressions against Phases 1–7.1).
  - Zero database schema migrations or changes to Phase 1–6 tables.

---

## [Phase 7.1] - 2026-09-11 — APPROVED
### Added
- **FastAPI Web Application Foundation (`app/web/app.py`, `config.py`, `routes/`, `schemas/`)**:
  - Application factory with CORS, OWASP security headers middleware, and centralized sanitized error handlers.
  - Sub-app routers: `/api/v1/health`, `/api/v1/setup`, `/api/v1/auth`, and HTML views (`/`, `/setup`, `/login`, `/dashboard`, `/logout`).
- **Database Architecture & Migration**:
  - `users` table: UUID PK, normalized `username` (unique index), `email`, `password_hash` (bcrypt), `role` (`OWNER`, `ADMIN`, `OPERATOR`, `VIEWER`), `is_active`, timestamps.
  - `user_sessions` table: UUID PK, `user_id` FK (CASCADE), `session_token_hash` (SHA-256 unique index), client IP, user agent, sliding `last_active_at`, `expires_at`.
  - Bidirectional Alembic migration `9a1b2c3d4e5f_phase_7_1_auth_and_sessions.py`.
- **First-Run Atomic OWNER Bootstrap (`app/web/services/bootstrap_service.py`)**:
  - Zero default credentials. Setup is only available when 0 users exist in the system.
  - Atomic SQLite & PostgreSQL-safe concurrency lock using sentinel record in `app_settings` (`system_bootstrap_owner_lock`).
  - Strict password complexity validation (min 12 chars, upper, lower, digit, symbol).
- **Session Management & Anti-Fixation (`app/web/security/session.py`)**:
  - Hashed session storage (raw tokens never saved to disk).
  - Session rotation on authentication (anti-fixation).
  - Sliding inactivity timeout (30 mins) and absolute expiration (12 hours).
  - Maximum 2 concurrent active sessions enforced per user; older sessions evicted with `SESSION_REVOKED_CONCURRENT_LIMIT` audit logs.
- **Brute-Force Login Defense (`app/web/security/brute_force.py`)**:
  - In-memory thread-safe attempt tracker per username + client IP.
  - 5 consecutive failed attempts trigger a 15-minute lockout with HTTP 429 and `ACCOUNT_LOCKED` audit trail without leaking account existence.
- **CSRF & Security Hardening (`app/web/security/csrf.py`, `middleware.py`)**:
  - HMAC-SHA256 signed double-submit cookie pattern (`outreach_csrf_token` cookie + `X-CSRF-Token` header).
  - Hardened headers: `Content-Security-Policy`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Strict-Transport-Security`.
- **UI Shell & Templates (`app/web/templates/`, `app/web/static/`)**:
  - Clean responsive dark-theme design matching brand colors.
  - Dynamic first-run redirection to `/setup`, authentication flows, and session logout.
- **Verification & Testing**:
  - 50 unit and integration tests across `tests/web/` covering auth, sessions, RBAC, bootstrap, CSRF, security headers, and UI views.
  - 92% test coverage on `app/web` modules.
  - 269 total tests passing across entire repository (100% pass rate, 0 regressions).

---

## [Phase 6] - 2026-09-11 — APPROVED
### Added
- **Dual Logging Architecture (`logs/app.log` & `logs/app.json.log`)**:
  - `SafeRotatingFileHandler` with 10 MB sizing (`10,485,760 bytes`) and 10 backups.
  - Zero application crashes on storage failures (`ENOSPC`, unwritable dirs, Windows rollover `PermissionError`).
  - Strict single emission per logical event with deterministic structured JSON schema.
  - Zero message body logging by default.
- **Handler-Safe Redaction & Privacy Engine (`app/utils/logger.py`)**:
  - E.164 phone masking with native support for Egyptian mobile numbers (`+201012345678` $\to$ `+2010******78`, `01012345678` $\to$ `010******78`), international numbers, and already-masked idempotence.
  - Credential and secret scrubbing (passwords, tokens, cookies, Bearer/JWT).
  - Independent handler-level sanitization ensuring propagated records from child loggers cannot bypass sanitization.
- **Production Preflight Readiness Matrix (`app/readiness/preflight.py`)**:
  - 10-point inspection matrix covering Python runtime, config, directory permissions, database connectivity, schema tables, Chrome binary, session profile, process lock singularity, emergency stop, and circuit breaker.
  - Standard and Strict verification modes via `outreach preflight [--campaign-id <id>] [--strict] [--json]`.
- **Live Operational System Health Engine (`app/readiness/health.py`)**:
  - Real-time classification into `HEALTHY`, `DEGRADED`, `UNHEALTHY`, `STOPPED`.
  - Comprehensive inspection of database responsiveness, runner heartbeats, stale leases, and `UNKNOWN_OUTCOME` items.
  - Accessible via CLI `outreach system health [--json]`.
- **Operational Analytics Subsystem (`app/services/analytics_service.py` & CLI `outreach analytics`)**:
  - Authoritative Confirmed Send Rate formula:
    $$\text{Confirmed Send Rate} = \frac{\text{Confirmed Sends}}{\text{Confirmed Sends} + \text{Failed} + \text{Unknown Outcome}} \times 100$$
  - Strict penalization of `UNKNOWN_OUTCOME` and exclusion of `RETRY_PENDING`, `SKIPPED`, and `CANCELLED` from denominator.
  - Subcommands: `campaign`, `queue`, `runner`, `provider`, `system` supporting ASCII cards/tables and `--json`.
- **Operational Documentation**:
  - Created `PRODUCTION_RUNBOOK.md` with complete startup, monitoring, emergency stop, reconciliation, and troubleshooting playbooks.
- **Verification Results**:
  - Phase 6 test suite: 52 passed, 0 failures, 91% code coverage.
  - Full regression suite: 219 passed, 1 deselected, 0 failures.
  - E2E scripts: Phase 2, 3, 4, 5, 6 all passed cleanly.

---

## [Phase 5] - 2026-09-11 — APPROVED
### Added
- **Operational CLI Interface (`app/cli/`)**:
  - Implemented top-level `outreach` CLI entrypoint in `app/cli/main.py`.
  - Comprehensive operational subcommands:
    - `outreach session <login|status|logout>`: Interactive QR authentication, session health inspection, and controlled logout.
    - `outreach campaign <run|status|pause|resume|stop>`: Domain state transitions (strictly decoupled from process execution; no daemon started by `campaign run`).
    - `outreach runner <start|status|stop>`: Long-running production runner daemon execution for single campaign.
    - `outreach queue <status|inspect|reconcile|override>`: Queue backlog monitoring, message inspection, stale lease reconciliation, and manual override.
    - `outreach emergency-stop|emergency-status|emergency-resume`: System-wide dispatch killswitch with <500ms target claim prevention.
  - Deterministic exit codes in `app/cli/exit_codes.py` (0–10).
  - Clean CLI ASCII formatting utilities in `app/cli/output.py` with phone masking (`mask_phone`) and zero external UI dependencies.
- **Production Runner Daemon (`app/runner/`)**:
  - `ProductionRunner`: Single-campaign worker loop with pre-claim and post-claim emergency stop checks, campaign state polling, circuit breaker monitoring, pacing delays, and graceful teardown.
  - `ProcessLock`: Authoritative OS-level file locking (`msvcrt.locking` on Windows, `fcntl.flock` on Unix) enforcing process singularity (exactly one runner per machine).
  - Stale lock recovery semantics: OS kernel automatically releases the file lock on process termination; stale runner metadata/PID in lock files and database heartbeat state (`AppSetting: system:active_runner`) are recovered via PID liveliness checks (`is_pid_alive()`).
  - `SignalCoordinator`: Graceful shutdown interception for `SIGINT` and `SIGTERM` ensuring in-flight sends complete without browser corruption.
  - `RunnerLifecycle`: Formal runner state machine (`STOPPED`, `STARTING`, `AUTHENTICATING`, `RUNNING`, `IDLE`, `PAUSED`, `STOPPING`, `FAILED`).
- **Safe Reconciliation & Manual Override Workflow**:
  - Dedicated `outreach queue override <message_id> --reason <text>` command for `UNKNOWN_OUTCOME` messages.
  - Requires explicit verification reason, interactive confirmation `CONFIRM-NOT-DELIVERED` (no generic `--force`), anti-duplicate delivery operator warning, and full audit logging (`MANUAL_RECONCILIATION_OVERRIDE`).
- **Final Verification Results (Exact)**:
  - Phase 5: 77 passed
  - Full regression: 167 passed, 1 deselected, 0 failed
  - Phase 2 E2E: PASS
  - Phase 3 E2E: PASS
  - Phase 4 E2E: PASS
  - Phase 5 E2E: PASS
  - Phase 5 coverage: 90%

---

## [Phase 4] - 2026-09-11
### Added
- **WhatsApp Web Provider (`WhatsAppWebProvider`)**:
  - Implemented concrete `MessageProvider` interface in `app/providers/whatsapp_web/provider.py`.
  - Integrates with existing Phase 3 `QueueWorker` and `RateLimiter` via pure dependency injection.
- **Session Management (`WhatsAppSessionManager`)**:
  - Encapsulates browser lifecycle, persistent Chrome user data profile, QR authentication awaiting, and health checks.
  - Implemented explicit session state machine (`DISCONNECTED`, `AUTHENTICATING`, `CONNECTED`, `SESSION_LOST`, `ERROR`, `STOPPED`).
- **Browser Abstraction (`WhatsAppBrowser`)**:
  - Encapsulates Selenium WebDriver operations, deep link navigation, compose box keystroke typing, and confirmation checks.
- **DOM Selector Repository (`WhatsAppSelectors`)**:
  - Centralized multi-tier CSS selector chains in `selectors.py` (`data-testid`, `aria-label`, role fallbacks).
- **Error Classification Contract (`WhatsAppErrorMapper`)**:
  - Maps DOM dialogs and browser exceptions into `PERMANENT`, `TEMPORARY`, `UNKNOWN`, and `UNKNOWN_OUTCOME`.
  - Enforces safe non-retry policy on `UNKNOWN_OUTCOME` to avoid duplicate external deliveries.
- **Configuration**:
  - Added `WHATSAPP_SESSION_PATH`, `WHATSAPP_HEADLESS`, `WHATSAPP_BROWSER_TIMEOUT`, `WHATSAPP_PAGE_LOAD_TIMEOUT`, `WHATSAPP_QR_TIMEOUT` to `Settings`.
- **Tests**:
  - Added 34 automated unit/mock tests for state machine, error mapper, session manager, provider, and worker integration.
  - Added isolated live-browser integration test suite (`tests/integration/test_whatsapp_live_browser.py`) marked with `@pytest.mark.live_browser`.
- **E2E Verification Script**:
  - Created `python -m app.providers.whatsapp_web.verify_phase4`.

### Changed
- Refined duplicate delivery semantics: Documented "Best-effort duplicate prevention" acknowledging that external browser automation cannot guarantee exactly-once external delivery.
- Refined send success semantics: Defined success strictly as `SEND_CONFIRMED` via UI status indicators, distinguishing from asynchronous `DELIVERED` and `READ`.

---

## [Phase 3] - 2026-09-11
### Added
- Persistent database-backed message queue with atomic claiming and worker leases.
- Extensible idempotency keying (`cc_{campaign_contact_id}_seq_{sequence_number}`).
- Strict Queue State Machine (`PENDING`, `QUEUED`, `PROCESSING`, `SENT`, `FAILED`, `RETRY_PENDING`, `CANCELLED`, `SKIPPED`).
- `FrequencyLimitService` supporting cross-campaign daily and 30-day limits.
- `RateLimiter` managing interval delays, daily caps, and batch pauses.
- `BatchManager` tracking persistent batch progression and crash reconciliation.
- `RetryManager` differentiating temporary failures with exponential backoff from permanent errors.
- `CircuitBreaker` pausing campaigns upon reaching consecutive error thresholds.
- `EmergencyStop` with target latency <500ms (verified at 5.95ms).
- `MessageProvider` abstract base class and `MockMessageProvider`.

---

## [Phase 2] - 2026-09-11
### Added
- Campaign domain layer in `app/campaigns/`.
- `CampaignManager` with state machine (`DRAFT`, `SCHEDULED`, `RUNNING`, `PAUSED`, `COMPLETED`, `CANCELLED`, `FAILED`).
- `CampaignContactManager` and `CampaignEligibilityService`.
- `MessageTemplateService` with variable validation and safe fallbacks.
- Database migration for campaign and campaign contact constraints.
- Aggregated database-level statistics service.

---

## [Phase 1] - 2026-09-10
### Added
- Initial project scaffold and SQLAlchemy ORM models.
- Contact management with phone number validation (E.164) and CSV bulk handling.
- Alembic database migration setup with SQLite WAL mode.
