# Project Context: WhatsApp Outreach Automation

## Current Status
- **Phase 1: Project Scaffold & Database Foundation** — **IMPLEMENTED & APPROVED**
- **Phase 2: Campaign Management & Message Templates** — **IMPLEMENTED & APPROVED**
- **Phase 3: Persistent Queue, Scheduler, Rate Limiting & Provider Abstraction** — **IMPLEMENTED & APPROVED**
- **Phase 4: WhatsApp Web / Browser Automation Provider Integration** — **IMPLEMENTED & APPROVED**
- **Phase 5: CLI Operational Interface & Live Production Runner** — **IMPLEMENTED & APPROVED**
- **Phase 6: Logging, Analytics & Production Readiness / Deployment / Monitoring** — **IMPLEMENTED & APPROVED**
- **Phase 7.1: Web Foundation & Authentication Control Center** — **IMPLEMENTED & APPROVED**
- **Phase 7.2: Dashboard & System Supervision Control Center** — **IMPLEMENTED & APPROVED**
- **Phase 7.3: Campaigns, Templates & Contacts Control Center** — **IMPLEMENTED & APPROVED**
- **Phase 7.4: Queue & Message Operations Control Center** — **IMPLEMENTED & APPROVED**
- **Production Deployment Checkpoint: Vercel + Supabase + Dedicated Worker VPS** — **IMPLEMENTED & VERIFIED — AWAITING HUMAN APPROVAL**

---

## 1. IMPLEMENTED Architecture & Components (Phases 1–4)

### Architectural Separation
```
Contact Management
  ↓ (ContactManager: PhoneValidator, CSVHandler)
Campaign Management
  ↓ (CampaignManager: StateMachine, CampaignContactManager, Stats)
Eligibility & Frequency Limits
  ↓ (CampaignEligibilityService & FrequencyLimitService)
Persistent Message Queue
  ↓ (PersistentQueueService: DB Table 'messages', QueueStateMachine)
Scheduler & Worker Orchestration
  ↓ (QueueWorker: RateLimiter, BatchManager, CircuitBreaker, EmergencyStop, RetryManager)
MessageProvider Interface
  ↓ (MessageProvider ABC)
  ├── MockMessageProvider (Deterministic Testing)
  └── WhatsAppWebProvider (Selenium Automation, WhatsAppSessionManager, WhatsAppBrowser)
```

The system is strictly provider-agnostic. All browser and WhatsApp Web automation logic is fully isolated inside `app/providers/whatsapp_web/`. Zero Selenium dependencies leak into the domain or scheduling layers.

---

### Phase 4 Components Implemented

#### 1. Provider Layer (`app/providers/whatsapp_web/`)
- **`WhatsAppWebProvider(MessageProvider)`**:
  - Implements the abstract contract: `connect()`, `health_check()`, `send_message(ProviderMessage) -> SendResult`, `disconnect()`.
  - Injected directly into the existing `QueueWorker` without modifying Phase 3 code.
- **`WhatsAppSessionManager`**:
  - Manages browser lifecycle, persistent profile loading, and session state transitions.
  - Handles initial navigation to WhatsApp Web and awaits operator QR code authentication.
  - Monitors session health and detects remote logout / session invalidation.
  - Recovers cleanly from browser crashes using persistent profiles without clearing cookies.
- **`WhatsAppBrowser`**:
  - Encapsulates Selenium WebDriver operations, Chrome options, and DOM interactions.
  - Handles direct chat navigation via deep links (`https://web.whatsapp.com/send?phone=...`).
  - Detects and dismisses invalid phone number dialogs.
  - Types message content into compose box and triggers send actions.
  - Verifies send confirmation checkmarks against strict UI criteria.
- **`WhatsAppSelectors` (`selectors.py`)**:
  - Centralizes all DOM selectors with multi-tier fallback chains (`data-testid`, `aria-label`, role, class).
- **`WhatsAppErrorMapper` (`error_mapper.py`)**:
  - Formal error classification contract mapping raw exceptions and DOM states to:
    - `PERMANENT`: Invalid numbers, blocked contacts, selector defects $\rightarrow$ marked `FAILED`, 0 retries.
    - `TEMPORARY`: Pre-send crashes, network drops, checkmark timeouts $\rightarrow$ marked `RETRY_PENDING`, exponential backoff with jitter.
    - `UNKNOWN_OUTCOME`: Send action was clicked but confirmation was interrupted by crash/reload $\rightarrow$ marked `FAILED` with `error_type='UNKNOWN_OUTCOME'`, **no blind automated retry** to prevent duplicate delivery.
    - `UNKNOWN`: Unrecognized states $\rightarrow$ fails safely without infinite loops.

#### 2. Session State Machine (`app/providers/whatsapp_web/state.py`)
- States: `DISCONNECTED`, `AUTHENTICATING`, `CONNECTED`, `SESSION_LOST`, `ERROR`, `STOPPED`.
- Explicit state transitions enforced by `WhatsAppSessionStateMachine`.

#### 3. Duplicate Delivery Semantics
- **Best-effort duplicate prevention**:
  - Internal database uniqueness (`uq_messages_idempotency_key`) and atomic worker claiming guarantee internal idempotency.
  - However, **exactly-once external delivery cannot be guaranteed for an external browser-based provider** because WhatsApp Web lacks an atomic server-side idempotency API.
  - The architecture mitigates duplicate risk through pre-send health checks, atomic leases, and by classifying post-send interruptions as `UNKNOWN_OUTCOME` requiring manual review.

#### 4. Send Success Semantics
- Success is defined strictly as:
  **"WhatsApp Web send operation confirmed according to the configured UI confirmation criteria (`SEND_CONFIRMED`)."**
- Explicitly distinguishes:
  - `SEND_CONFIRMED`: Verified in UI (input cleared, outgoing bubble rendered, status icon confirmed). Only this state is reported by the provider.
  - `DELIVERED`: Recipient device receipt (double checkmark, asynchronous).
  - `READ`: Recipient read receipts (blue checkmark, asynchronous).

#### 5. Safety, Compliance & Anti-Evasion Policy
- The provider does NOT attempt to bypass authentication, solve CAPTCHAs, or conceal automation.
- No user-agent spoofing, canvas fingerprint forgery, or multi-account rotation to evade platform restrictions.
- All pacing is transparently governed by `RateLimiter` and `FrequencyLimitService`.
- If an account restriction is encountered, the provider halts immediately, transitions to `SESSION_LOST`, trips the `CircuitBreaker`, and alerts the operator.

#### 6. Configuration Parameters (`app/utils/settings.py`)
- `WHATSAPP_SESSION_PATH`: Filesystem directory for persistent Chrome profile.
- `WHATSAPP_HEADLESS`: Boolean (default False for QR visibility).
- `WHATSAPP_BROWSER_TIMEOUT`: Element search wait timeout (default 30s).
- `WHATSAPP_PAGE_LOAD_TIMEOUT`: Initial page load timeout (default 45s).
- `WHATSAPP_QR_TIMEOUT`: Max seconds to await operator QR scan (default 120s).
- `WHATSAPP_CHROME_BINARY`: Custom Chrome binary path.
- `WHATSAPP_CHROMEDRIVER_PATH`: Custom chromedriver binary path.

---

## 2. Test Suite & Coverage

- **Complete Test Suite**: **90 automated tests passed, 1 deselected (isolated live browser), 0 failures, 0 regressions**.
  - `tests/test_batch_manager.py` (2 tests)
  - `tests/test_contact_manager.py` (3 tests)
  - `tests/test_eligibility_service.py` (5 tests)
  - `tests/test_emergency_stop.py` (3 tests)
  - `tests/test_frequency_limits.py` (4 tests)
  - `tests/test_manager.py` (6 tests)
  - `tests/test_phase3_edge_cases.py` (8 tests)
  - `tests/test_queue_concurrency.py` (1 test)
  - `tests/test_queue_service.py` (6 tests)
  - `tests/test_queue_state_machine.py` (3 tests)
  - `tests/test_rate_limiter.py` (4 tests)
  - `tests/test_retry_and_circuit_breaker.py` (2 tests)
  - `tests/test_statistics.py` (1 test)
  - `tests/test_template_service.py` (4 tests)
  - `tests/test_whatsapp_browser_mock.py` (9 tests)
  - `tests/test_whatsapp_error_mapper.py` (4 tests)
  - `tests/test_whatsapp_provider.py` (7 tests)
  - `tests/test_whatsapp_session_manager.py` (8 tests)
  - `tests/test_whatsapp_state_machine.py` (2 tests)
  - `tests/test_whatsapp_worker_integration.py` (4 tests)
  - `tests/test_worker_orchestrator.py` (4 tests)
  - `tests/integration/test_whatsapp_live_browser.py` (1 isolated live browser test, `@pytest.mark.live_browser`)

- **Domain Coverage**:
  - `app/providers/whatsapp_web/state.py`: **100%**
  - `app/providers/whatsapp_web/selectors.py`: **100%**
  - `app/providers/whatsapp_web/exceptions.py`: **100%**
  - `app/providers/whatsapp_web/error_mapper.py`: **97%**
  - `app/providers/whatsapp_web/provider.py`: **93%**
  - `app/providers/whatsapp_web/session_manager.py`: **92%**
  - `app/providers/whatsapp_web/browser.py`: **56%** (driver logic covered via mocks; live Chrome launch isolated in integration test)

- **Verification Scripts**:
  - `python -m app.campaigns.verify_e2e`: Passed (Phase 2).
  - `python -m app.scheduler.verify_phase3_e2e`: Passed (Phase 3).
  - `python -m app.providers.whatsapp_web.verify_phase4`: Passed (Phase 4).
  - `python -m app.cli.verify_phase5_e2e`: Passed (Phase 5).

---

## 3. Phase 5 Implementation & Operational Guarantees

### Architecture & Components
- **Top-Level CLI (`outreach`) (`app/cli/main.py`)**:
  - `outreach session <login|status|logout>`: Interactive QR scan, persistent profile checks, and controlled logout.
  - `outreach campaign <run|status|pause|resume|stop>`: Domain state transitions only. Does NOT launch daemon processes.
  - `outreach runner <start|status|stop>`: Dedicated single-campaign worker daemon process.
  - `outreach queue <status|inspect|reconcile|override>`: Backlog inspection, stale lease recovery, and safe `UNKNOWN_OUTCOME` override.
  - `outreach emergency-stop|emergency-status|emergency-resume`: System-wide dispatch killswitch.
- **Process Singularity & Locking (`app/runner/process_lock.py`)**:
  - Authoritative OS-level file lock (`data/runner.lock` via `msvcrt.locking` on Windows, `fcntl.flock` on Unix).
  - Strictly prevents duplicate runner instances on the same machine (exit code `CONCURRENCY_ERROR = 9`).
  - **Stale Lock Recovery Semantics**:
    - The OS file lock itself is held at the kernel level and automatically released by the operating system whenever the owning runner process terminates (cleanly or abruptly).
    - Stale runner metadata/PID in `data/runner.lock` and database heartbeat state (`AppSetting: system:active_runner`) from crashed or terminated runners are detected via PID liveliness checks (`is_pid_alive()`) and cleanly recovered when a new runner acquires the lock.
  - Non-authoritative database heartbeat in `AppSetting: system:active_runner` provides visibility, stale detection, and monitoring only; it is never the authoritative distributed lock.
- **Single-Campaign Production Runner (`app/runner/production_runner.py`)**:
  - Executes exclusively via `outreach runner start --campaign-id <id>`.
  - Enforces pre-claim and post-claim emergency stop checks.
  - Monitors campaign status changes (e.g. `outreach campaign pause` or `stop`) and trips gracefully.
  - Monitors circuit breaker thresholds to halt dispatches upon consecutive errors.
  - Applies randomized pacing delay (`min_delay_seconds` to `max_delay_seconds`).
  - Restores stale message leases on startup.
- **Emergency Stop Safety Semantics**:
  - Target latency <500ms applies to signal propagation and blocking NEW message claims (verified at 2.59ms). It is not an absolute guarantee to forcibly kill an in-flight browser send.
  - In-flight sends complete cleanly to a safe cancellation/confirmation point to avoid browser corruption or avoidable ambiguous states.
- **Safe UNKNOWN_OUTCOME Manual Reconciliation**:
  - `outreach queue override <message_id> --reason <text>`:
  - Requires non-empty reason and interactive confirmation `CONFIRM-NOT-DELIVERED`.
  - No generic `--force` bypass.
  - Displays explicit warning about duplicate delivery risk.
  - Writes permanent audit log with operator identity, confirmation flag, and explicit rationale.
- **Deterministic Exit Codes (`app/cli/exit_codes.py`)**:
  - `0`: SUCCESS, `1`: GENERAL_ERROR, `2`: INVALID_ARGUMENT, `3`: NOT_FOUND, `4`: INVALID_STATE, `5`: AUTHENTICATION_REQUIRED, `6`: PROVIDER_UNAVAILABLE, `7`: EMERGENCY_STOP_ACTIVE, `8`: CIRCUIT_BREAKER_OPEN, `9`: CONCURRENCY_ERROR, `10`: UNKNOWN_OUTCOME_BLOCKED.

### Final Verification Results (Exact)
- **Phase 5**: 77 passed
- **Full regression**: 167 passed, 1 deselected, 0 failed
- **Phase 2 E2E**: PASS
- **Phase 3 E2E**: PASS
- **Phase 4 E2E**: PASS
- **Phase 5 E2E**: PASS
- **Phase 5 coverage**: 90%

---

## 4. Phase 6 Implementation & Operational Readiness (Awaiting Human Approval)

### Architecture & Components
- **Dual Logging (`logs/app.log` & `logs/app.json.log`)**:
  - `SafeRotatingFileHandler` with 10 MB limit (`10,485,760 bytes`), 10 backup archives.
  - Zero-crash guarantee on Windows file lock contention (`PermissionError`) or storage exhaustion.
  - Structured JSON Lines schema: timestamp, level, event, component, message, correlation_id, campaign_id, campaign_contact_id, message_id, runner_id, provider, operation, result, error_code, duration_ms, details.
  - Message bodies omitted from logs by default.
- **Handler-Safe Redaction & Privacy Engine (`app/utils/logger.py`)**:
  - E.164 phone masking with specialized Egyptian mobile normalization (`+201012345678` $\to$ `+2010******78`, `01012345678` $\to$ `010******78`).
  - Passwords, Bearer/JWT tokens, API keys, cookies scrubbed to `[REDACTED]`.
  - Independent formatting stage inside persistent file handlers ensures child loggers cannot bypass sanitization.
- **Production Preflight Readiness Matrix (`app/readiness/preflight.py`)**:
  - 10 checks: Python runtime, settings, directories & permissions, DB connectivity, DB schema tables, Chrome binary, session profile, process lock singularity, emergency stop, circuit breaker.
  - Invoked via `outreach preflight [--campaign-id <id>] [--strict] [--json]`.
- **Live System Health Engine (`app/readiness/health.py`)**:
  - Evaluates operational status into `HEALTHY`, `DEGRADED`, `UNHEALTHY`, `STOPPED`.
  - Probes DB responsiveness, runner heartbeats, stale leases, and `UNKNOWN_OUTCOME` items.
  - Invoked via `outreach system health [--json]`.
- **Operational Analytics Subsystem (`app/services/analytics_service.py` & CLI `outreach analytics`)**:
  - Authoritative Confirmed Send Rate:
    $$\text{Confirmed Send Rate} = \frac{\text{Confirmed Sends}}{\text{Confirmed Sends} + \text{Failed} + \text{Unknown Outcome}} \times 100$$
  - Subcommands: `campaign`, `queue`, `runner`, `provider`, `system` supporting ASCII formatting and `--json`.
- **Runner Startup Order Enforced**:
  1. CLI argument validation
  2. Acquire authoritative OS process lock (`data/runner.lock`). If held, exit with `ExitCode.CONCURRENCY_ERROR (9)`.
  3. Runtime readiness / preflight checks.
  4. If preflight fails, release lock and exit cleanly.
  5. ProductionRunner daemon loop.

### Phase 6 Final Verification Results
- **Phase 6 tests**: 52 passed, 0 failed
- **Phase 6 code coverage**: 91%
- **Phase 2, 3, 4, 5, 6 E2E scripts**: ALL PASSED CLEANLY

---

## 4. IMPLEMENTED Web Control Center Foundation (Phase 7.1)

### Core Components Implemented
- **FastAPI Modular Foundation (`app/web/app.py`, `config.py`, `routes/`, `schemas/`)**:
  - Application factory with CORS, OWASP security headers middleware, and centralized sanitized error handlers.
  - Sub-app routers: `/api/v1/health`, `/api/v1/setup`, `/api/v1/auth`, and HTML views (`/`, `/setup`, `/login`, `/dashboard`, `/logout`).
- **Database Architecture & Migration**:
  - `users` table: UUID PK, normalized `username` (unique index), `email`, `password_hash` (bcrypt), `role` (`OWNER`, `ADMIN`, `OPERATOR`, `VIEWER`), `is_active`, timestamps.
  - `user_sessions` table: UUID PK, `user_id` FK (CASCADE), `session_token_hash` (SHA-256 unique index), client IP, user agent, sliding `last_active_at`, `expires_at`.
  - Bidirectional Alembic migration `9a1b2c3d4e5f_phase_7_1_auth_and_sessions.py`.
- **First-Run Atomic OWNER Bootstrap (`app/web/services/bootstrap_service.py`)**:
  - Zero default credentials. Setup is only available when 0 users exist.
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

### Phase 7.1 Verification Results
- **Phase 7.1 tests**: 50 passed, 0 failed
- **Full regression suite**: 269 passed, 1 deselected, 0 failed (100% pass rate)
- **Phase 7.1 code coverage**: 92% across all `app/web` modules
- **Alembic migration**: Bidirectional test (downgrade -1 / upgrade head) verified cleanly.

---

## 5. IMPLEMENTED Web Control Center Dashboard & System Control (Phase 7.2)

### Core Components Implemented
- **Operational Dashboard Aggregator (`app/web/services/dashboard_service.py`)**:
  - Unified snapshot combining:
    - Phase 6 System Health Engine (`evaluate_system_health`).
    - Database responsiveness and latency.
    - WhatsApp Web session profile detection and status.
    - Emergency Stop killswitch state.
    - Circuit Breaker threshold and tripped campaign status.
    - Active campaign telemetry and strict Confirmed Send Rate:
      $$\text{Confirmed Send Rate} = \frac{\text{Confirmed Sends}}{\text{Confirmed Sends} + \text{Failed} + \text{Unknown Outcome}} \times 100$$
    - Queue summary tracking `QUEUED`, `PROCESSING`, `RETRY_PENDING`, `SENT`, `FAILED`, and prominently isolating `UNKNOWN_OUTCOME` unreconciled items.
    - Production runner supervisor state from authoritative OS file lock.
    - Dynamic operational alerts synthesis (database down, emergency stop armed, tripped circuit breaker, unknown outcome backlog, stale leases, runner status degradation, missing WhatsApp profile).
  - Zero N+1 queries; bounded, high-performance database reads.
- **Safe Runner Supervisor Service (`app/web/services/runner_control_service.py`)**:
  - Enforces OS file lock singularity (`data/runner.lock`).
  - Strict campaign lifecycle validation: only campaigns in `RUNNING` status can be started.
  - Preflight readiness check execution before process launch.
  - Graceful stop via SIGTERM with configurable safe cancellation window (default 15s); never forcefully terminates in-flight browser dispatches.
  - Automatic dead PID detection and stale lock cleanup.
  - Immutable audit trail recording `RUNNER_START_REQUESTED` and `RUNNER_STOP_REQUESTED`.
  - Dependency injection support for runner spawners and stoppers enabling fully deterministic unit testing without spawning real daemons.
- **Control Plane REST APIs (`app/web/routes/api/`)**:
  - Dashboard: `GET /api/v1/dashboard/summary`, `GET /health`, `GET /queue`, `GET /campaign`, `GET /runner`.
  - Safe Runner: `GET /api/v1/runner/status`, `POST /api/v1/runner/start`, `POST /api/v1/runner/stop`. Role-gated to `OPERATOR+` with double-submit CSRF protection.
  - Emergency Control: `GET /api/v1/system/emergency-status`, `POST /api/v1/system/emergency-stop` (`OPERATOR+`, CSRF-protected, requires reason), `POST /api/v1/system/emergency-resume` (`ADMIN+`, CSRF-protected, requires reason).
  - Telemetry Stream: `GET /api/v1/events/stream` (SSE streaming with keep-alive pings, `X-Accel-Buffering: no`, and authenticated session validation).
- **Modern Dashboard UI Shell & Real-Time Client (`app/web/templates/dashboard.html`, `app/web/static/`)**:
  - Top KPI cards: System Health, Confirmed Send Rate, Queue Depth, Runner Process state.
  - Subsystems telemetry grid: Database, WhatsApp Profile, Emergency Stop, Circuit Breaker.
  - Active Campaign card with progress bar, contact breakdown, and strict semantic disclaimer.
  - Queue Breakdown grid highlighting `UNKNOWN_OUTCOME` items requiring operator attention.
  - Interactive Safe Runner and Emergency Stop control cards with confirmation modals and reason inputs.
  - Real-time client via EventSource SSE with automatic 5-second polling fallback if SSE is interrupted.
  - Strict semantic compliance: prohibited terms like "delivery rate", "delivered", or "read" are completely excluded.

### Phase 7.2 Verification Results
- **Phase 7.2 tests in `tests/web`**: 90 passed, 0 failed
- **Full regression test suite**: 309 passed, 1 deselected, 0 failed (100% pass rate)
- **Phase 7.2 code coverage**: 91% across all `app/web` modules
- **Zero Phase 1–6 database migrations or schema modifications**.

---

## 5. IMPLEMENTED Architecture & Components (Phase 7.3: Campaigns, Templates, Contacts)

### Reusable Template Library & Immutable Versioning
- **ORM Models (`app/models/template.py`)**:
  - `MessageTemplate`: Logical template container (`id`, `name` unique, `description`, timestamps).
  - `MessageTemplateVersion`: Immutable, write-once append-only version entity (`id`, `template_id` FK CASCADE, `version_number` monotonically incremented 1, 2, 3..., `body`, `created_by`, timestamps).
  - Unique constraint on `(template_id, version_number)`.
  - Bidirectional Alembic migration `a1b2c3d4e5f6_phase_7_3_templates.py`.
- **Template Web Service & API (`app/web/services/template_service.py`, `app/web/routes/api/templates.py`)**:
  - `POST /api/v1/templates`: Creates container + initial v1.
  - `POST /api/v1/templates/{id}/versions`: Appends new immutable version.
  - `POST /api/v1/templates/validate`: Validates template variables against `MessageTemplateService.ALLOWED_VARIABLES` (`{{name}}`, `{{company}}`, `{{city}}`, `{{campaign}}`).
  - `POST /api/v1/templates/preview`: Renders live message preview with dummy contact/campaign data.
  - Role enforcement: `VIEWER` can read/preview; `OPERATOR` and `ADMIN` can create templates/versions with double-submit CSRF verification.

### Campaign Management & Template Snapshotting
- **Template Snapshot Invariant**:
  - At campaign creation time (`POST /api/v1/campaigns`), the selected template version body is snapshotted into `campaigns.message_template`.
  - Future updates to the template library do not affect active or historical campaigns.
  - Setting campaign status to `RUNNING` is strictly a domain state transition; it does not start the production runner daemon.
- **Campaign Web Service & API (`app/web/services/campaign_service.py`, `app/web/routes/api/campaigns.py`)**:
  - Search, filter by status, and pagination.
  - Update allowed only in `DRAFT` state.
  - Detailed statistics breakdown using `CampaignStatisticsService`.
  - State machine transitions via `CampaignManager.transition_state()`.

### Contact Management, Privacy Masking & CSV Ingestion
- **Privacy Phone Masking**:
  - `ContactService.list_contacts`: Non-privileged roles (`VIEWER`, `OPERATOR`) receive masked phone numbers (`+201******678`).
  - `ADMIN` and `OWNER` receive full unmasked E.164 strings (`+201012345678`).
- **Two-Phase CSV Import (`app/web/services/contact_service.py`, `app/web/routes/api/contacts.py`)**:
  - Phase 1 Dry-Run (`POST /api/v1/contacts/import/dry-run`): Validates headers, formats, duplicate detection against DB and file, phone syntax, returns preview rows without persisting.
  - Phase 2 Commit (`POST /api/v1/contacts/import/commit`): Reuses `CSVHandler` to ingest contacts and emits `CONTACT_CSV_IMPORTED` audit log.
- **CSV Export**:
  - `GET /api/v1/contacts/export`: Streams CSV export file with attachment header, role-gated to `OPERATOR+`.

### Campaign-Contact Membership & Eligibility
- **CampaignContactService (`app/web/services/campaign_contact_service.py`)**:
  - Evaluates contact eligibility via `CampaignEligibilityService` on enrollment (`ELIGIBLE`, `EXCLUDED`, `PENDING`).
  - Strict draft safety: Contacts can only be removed from campaigns while in `DRAFT` state.

### Phase 7.3 Verification Results
- **Full test suite**: 337 passed, 1 deselected, 0 failed (100% pass rate across entire repository).
- **Phase 7.3 web tests**: 28 new tests in `tests/web/` (total 118 web tests passed).
- **Code coverage**: 93% across all `app/web` modules (`app/web/services`: 94%–98%).
- **Zero live browser calls or WhatsApp dispatches**.

---

## 6. IMPLEMENTED Architecture & Components (Phase 7.4: Queue & Message Operations)

### Queue & Message Control Plane
- **Web Service & APIs (`app/web/services/queue_service.py`, `app/web/routes/api/queue.py`)**:
  - `GET /api/v1/queue`: Server-side filtering by campaign, status (including `UNKNOWN_OUTCOME`), contact search, retry filter (`HAS_RETRY`, `NO_RETRY`, `RETRY_PENDING`), and date range. Eager loads relationships with `joinedload` to prevent N+1 queries. Server-side pagination.
  - `GET /api/v1/queue/stats`: Grouped query calculating status counts, stale worker leases (>120s), Emergency Stop and Circuit Breaker state, and locked Confirmed Send Rate.
  - `GET /api/v1/queue/{message_id}`: Granular message inspection with lifecycle timestamps, retry metrics, idempotency key, lease state, rendered content (read-only, never logged), and recent audit logs.
  - `POST /api/v1/queue/reconcile`: Reconciles expired worker leases (>120s) using authoritative Phase 3 `PersistentQueueService.recover_stale_leases()`. Audits `QUEUE_RECONCILE_REQUESTED`.
  - `POST /api/v1/queue/{message_id}/cancel`: Cancels non-terminal messages using Phase 3 `cancel_message()`. Audits `QUEUE_CANCEL_REQUESTED`.
  - `POST /api/v1/queue/{message_id}/resolve-unknown`: Strictly audited manual reconciliation for `UNKNOWN_OUTCOME` messages following Phase 5 semantics. Requires explicit reason and exact phrase `CONFIRM-NOT-DELIVERED`. Audits `UNKNOWN_OUTCOME_RESOLUTION_REQUESTED`.

### Autonomous Retry Semantics Preservation (Inspection Only)
- In Phase 3, retries are strictly automated, governed by exponential backoff with jitter (`RetryManager`) and claimed by `QueueWorker`.
- Manual retry mutations are deferred to avoid bypassing backoff schedules or violating process singularity. The UI provides inspection only.

### Privacy, Security & Operational UI
- Role-based privacy masking: `VIEWER` and `OPERATOR` receive masked phone numbers (`+201******678`); `ADMIN` and `OWNER` receive unmasked E.164.
- Double-submit CSRF enforcement on all mutation endpoints (`/reconcile`, `/{id}/cancel`, `/{id}/resolve-unknown`).
- Operational UI views: `/queue` dashboard and `/queue/{id}` detailed message inspection.
- Active `/queue` navigation links across all Web Control Center views.
- Zero database migrations required; fully compatible with existing indexed `messages` schema.

---

## 7. IMPLEMENTED Production Deployment Architecture (Vercel + Supabase + Dedicated Worker VPS)

### Architectural Division
```text
┌────────────────────────────────────────────────────────────────┐
│               CONTROL PLANE (Vercel Serverless)                │
│  - Custom domain: auto.integra-ist.com                         │
│  - FastAPI Web Control Center (SSR Jinja2 UI + REST APIs)      │
│  - Authentication, Sessions, RBAC, CSRF, Security Headers     │
│  - Supabase Transaction Pooler (PgBouncer port 6543)          │
│  - Zero background workers, zero Selenium, stateless           │
│  - Remote Runner Coordination via database desired state       │
└───────────────────────────────┬────────────────────────────────┘
                                │
                                ▼
┌────────────────────────────────────────────────────────────────┐
│              DATA PLANE (Supabase PostgreSQL 15+)              │
│  - Authoritative Relational Store (tables, indexes, audit logs)│
│  - AppSettings (emergency_stop, desired_runner_state, etc.)    │
│  - Dialect-safe Alembic migrations (PostgreSQL + SQLite test)  │
└───────────────────────────────▲────────────────────────────────┘
                                │
                                │ Direct Session connection (port 5432)
                                │
┌────────────────────────────────────────────────────────────────┐
│             EXECUTION PLANE (Dedicated Worker VPS)             │
│  - Persistent Linux Host (Ubuntu 22.04 LTS / 24.04 LTS)        │
│  - ProductionRunner daemon managed via systemd                 │
│  - Process Singularity enforced via OS file lock (runner.lock) │
│  - Google Chrome & ChromeDriver with persistent profile        │
│  - Emergency Stop (<500ms assert), Circuit Breaker, Pacing     │
│  - Desired-state watcher in polling loop (graceful stop)       │
└────────────────────────────────────────────────────────────────┘
```

### Component Implementation Details

1. **PostgreSQL Compatibility & Connection Pooling (Phases D-1 & D-3)**:
   - Added `psycopg2-binary>=2.9.9` driver support in `requirements.txt`.
   - Updated `app/database/connection.py` with dialect inspection:
     - SQLite: maintains `connect_args={"check_same_thread": False}` and `WAL` / `foreign_keys=ON` PRAGMAs.
     - PostgreSQL: enables `NullPool` on Vercel (`VERCEL=1`) to eliminate frozen lambda connection slot exhaustion against Supabase Transaction Pooler (port 6543); on Worker VPS, enables SQLAlchemy `QueuePool` with production-tuned settings: `DB_POOL_SIZE` (default 5), `DB_MAX_OVERFLOW` (default 10), `DB_POOL_RECYCLE` (1800s), and `DB_POOL_PRE_PING=True`.
2. **Dialect-Safe Alembic Migrations (Phase D-2)**:
   - `dfc1d4304168_initial_schema.py`: Replaced SQLite-specific `datetime('now')` with standard `sa.text('CURRENT_TIMESTAMP')`.
   - `230fa779a978_phase_2_updates.py`: Guarded redundant unique constraints (`uq_setting_key`, `uq_campaign_name`) so they are only emitted during SQLite batch table reconstruction, avoiding PostgreSQL duplicate constraint exceptions.
   - `9a1b2c3d4e5f_phase_7_1_auth_and_sessions.py`: Replaced integer boolean default `sa.text('1')` with dialect-safe `sa.text('true')`.
   - Verified via `alembic upgrade head --sql` under PostgreSQL compilation (378 lines clean DDL/DML) and live SQLite test execution.
3. **Serverless Console Logging (Phase D-4)**:
   - Updated `app/utils/logger.py` to inspect `VERCEL` environment variable and `LOG_TO_FILE` setting.
   - Automatically disables rotating file handlers in serverless runtimes, routing all sanitized, redacted log records directly to `sys.stdout` and `sys.stderr` for Vercel Log Drains.
4. **Vercel Serverless Entrypoint (Phase D-5)**:
   - Created `vercel.json` configuring `@vercel/python` builder, root routing for `api/index.py`, and direct static file routing for `/app/web/static/`.
   - Created `api/index.py` exporting the ASGI application.
   - Added `ProxyHeadersMiddleware(trusted_hosts="*")` to `app/web/app.py` for accurate HTTPS scheme and client IP detection behind Vercel edge proxies.
5. **Remote Runner Desired-State Coordination & CLI Owner Provisioning (Phase D-6)**:
   - Added `RUNNER_REMOTE_COORDINATION: bool = False` configuration in `app/utils/settings.py` (auto-detected when `VERCEL="1"`).
   - In remote coordination mode, `RunnerControlService` avoids invoking local `subprocess.Popen` or `os.kill`:
     - `start_runner()` sets `system:desired_runner_state` to `RUNNING` and persists `system:desired_runner_campaign_id`.
     - `stop_runner()` sets `system:desired_runner_state` to `STOPPED`.
     - `get_status()` returns `desired_state` and checks remote worker heartbeat timestamps for liveness.
   - `ProductionRunner` polls `system:desired_runner_state` on each iteration; when set to `STOPPED`, initiates graceful shutdown without orphan leases.
   - Added secure CLI command `outreach auth bootstrap-owner --username "admin" --email "admin@integra-ist.com" --password-prompt` in `app/cli/commands/auth.py` for headless initial owner creation directly against Supabase prior to public DNS traffic.
7. **Vercel Control Plane Production Configuration & Security (Phase D-8)**:
   - Added production configuration aliases in `app/web/config.py` and `app/utils/settings.py`:
     - `APP_ENV` ↔ `ENVIRONMENT` (bidirectional sync; `production` automatically enforces `COOKIE_SECURE=True`).
     - `COOKIE_SECURE` ↔ `WEB_COOKIE_SECURE` (enforces `Secure` attribute on session and CSRF cookies).
     - `SECRET_KEY` ↔ `WEB_SECRET_KEY` (non-breaking alias for JWT/cookie signatures).
     - `DEBUG: bool = False`, `VERCEL: Optional[str] = None`.
     - `ALLOWED_HOSTS: str = "*"` (default `*` for local dev/testing; production template configured for `auto.integra-ist.com,*.vercel.app`).
   - Integrated Starlette `TrustedHostMiddleware` into `app/web/app.py`, conditionally activated when `ALLOWED_HOSTS != "*"`, returning HTTP 400 for unauthorized Host headers.
   - Updated `deploy/vercel/env.vercel.example` with safe production templates for `auto.integra-ist.com`.
   - Added `tests/web/test_vercel_config_and_security.py` (10 tests) verifying alias synchronization, cookie security, host header filtering, and strict execution boundary preservation (zero Selenium/browser imports or subprocess spawns).

### Verification Results
- **Full Test Suite**: 368 passed, 1 deselected, 0 failed (100% pass rate).
- **Vercel Config & Security Suite**: 10 passed in `tests/web/test_vercel_config_and_security.py`.
- **Remote Runner & CLI Auth Tests**: 5 tests in `tests/web/test_remote_runner_and_auth_cli.py` passing cleanly (including `--password-prompt`).
- **Alembic Configuration**: Verified percent-safe interpolation handling in `app/database/migrations/env.py`.
- **PostgreSQL DDL Generation**: Verified via `alembic upgrade head --sql`.
