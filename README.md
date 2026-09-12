# WhatsApp Outreach Automation

A robust, enterprise-grade, provider-agnostic automated outreach system for WhatsApp Web with strict safety boundaries, persistent message queues, database-backed scheduling, process singularity protection, and operational CLI controls.

---

## Architecture Overview

```
                      OPERATIONAL CLI (outreach)
  ┌─────────────────┬───────────────────┬────────────────┬─────────────────┐
  │ session <cmd>   │ campaign <cmd>    │ runner <cmd>   │ queue <cmd>     │
  └────────┬────────┴─────────┬─────────┴────────┬───────┴────────┬────────┘
           │                  │                  │                │
           │         Domain State Only           │                │
           │         (No Daemon Started)         │                │
           ▼                  ▼                  ▼                ▼
┌──────────────────┐ ┌──────────────────┐ ┌───────────────┐ ┌───────────────┐
│ WhatsAppSession  │ │ CampaignManager  │ │ProductionRunnr│ │QueueService   │
│ Manager          │ │ StateMachine     │ │(Daemon Loop)  │ │State Machine  │
└────────┬─────────┘ └──────────────────┘ └───────┬───────┘ └───────┬───────┘
         │                                        │                 │
         │         Authoritative File Lock        │                 │
         │         ┌──────────────────────────────┴──────────────┐  │
         │         │ ProcessLock (Single Runner per System)      │  │
         │         │ OS-level Lock (msvcrt / fcntl) + Heartbeat  │  │
         │         └─────────────────────────────────────────────┘  │
         │                                                          │
         │                      DISPATCH ENGINE                     │
         │         ┌─────────────────────────────────────────────┐  │
         │         │ QueueWorker Orchestration                   │  │
         │         │ - EmergencyStop (<500ms target killswitch)  │  │
         │         │ - RateLimiter (pacing, daily caps, batches) │  │
         │         │ - CircuitBreaker (consecutive error trips)  │  │
         │         │ - FrequencyLimitService (cross-campaign)    │  │
         │         └──────────────────────┬──────────────────────┘  │
         │                                │                         │
         ▼                                ▼                         ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                      MessageProvider Interface (ABC)                      │
├─────────────────────────────────────┬─────────────────────────────────────┤
│ MockMessageProvider                 │ WhatsAppWebProvider                 │
│ (Automated Deterministic Testing)   │ (Selenium WebDriver Automation)     │
└─────────────────────────────────────┴─────────────────────────────────────┘
```

---

## Core Operational Principles

1. **Separation of Concerns**:
   - `outreach campaign run <id>` is strictly a **business state transition** command. It does **not** launch a runner or daemon process.
   - Production process execution is exclusively managed by `outreach runner start --campaign-id <id>`.
2. **Process Singularity**:
   - The system strictly supports **one production runner operating against one active campaign** at a time.
   - Process singularity is enforced by an authoritative OS-level file lock (`data/runner.lock`). Stale locks from crashed processes are automatically recovered. A non-authoritative database heartbeat (`AppSetting: system:active_runner`) provides visibility.
3. **Safety & Compliance Boundaries**:
   - **Zero Anti-Ban / Zero Evasion**: No CAPTCHA bypass, stealth patches, or anti-detection scripts.
   - **Deterministic Pacing**: Interval pacing between dispatches and daily contact quotas are enforced in the database.
   - **Emergency Stop**: System-wide killswitch halts all new queue claims in <500ms without forcefully killing in-flight browser sends.
   - **Ambiguous Outcomes**: Interrupted dispatches are classified as `UNKNOWN_OUTCOME` and are **never** blindly retried. Manual reconciliation requires explicit verification and confirmation string (`CONFIRM-NOT-DELIVERED`).

---

## Operational CLI Guide (`outreach`)

### 1. WhatsApp Web Session Management
```bash
# Interactive login (launches browser window for operator QR code scan)
python -m app.cli.main session login --timeout 120

# Inspect persistent session profile status and health
python -m app.cli.main session status

# Controlled session shutdown (optionally clears local profile cache)
python -m app.cli.main session logout [--clear-cache]
```

### 2. Campaign Lifecycle Management
```bash
# Transition campaign to RUNNING (business transition only; does not start daemon)
python -m app.cli.main campaign run <campaign_id>

# Inspect campaign configuration, pacing, and real-time queue breakdown
python -m app.cli.main campaign status <campaign_id>

# Pause active campaign (runner will pause dispatches at next safe point)
python -m app.cli.main campaign pause <campaign_id>

# Resume paused campaign
python -m app.cli.main campaign resume <campaign_id>

# Cancel campaign and cancel all remaining queued/pending messages
python -m app.cli.main campaign stop <campaign_id> --confirm
```

### 3. Production Runner Daemon
```bash
# Start the production runner daemon for a single campaign
python -m app.cli.main runner start --campaign-id <campaign_id> [--poll-interval 5] [--headless]

# Inspect active runner process status, PID, and last heartbeat
python -m app.cli.main runner status

# Send termination signal to active runner and await graceful shutdown
python -m app.cli.main runner stop
```

### 4. Message Queue & Manual Reconciliation
```bash
# View queue backlog summary (grouped by status) for a campaign
python -m app.cli.main queue status --campaign-id <campaign_id>

# Inspect message details, delivery attempts, leases, and error history
python -m app.cli.main queue inspect <message_id>

# Reconcile expired worker leases stuck in PROCESSING state
python -m app.cli.main queue reconcile [--campaign-id <campaign_id>]

# Safe manual reconciliation for UNKNOWN_OUTCOME messages
# (Requires explicit verification reason and interactive confirmation)
python -m app.cli.main queue override <message_id> --reason "Verified chat history in WhatsApp Web"
```

### 5. Emergency Stop Killswitch
```bash
# Immediately engage emergency stop (halts all new message claims <500ms)
python -m app.cli.main emergency-stop --reason "Operational halt"

# Check emergency stop status
python -m app.cli.main emergency-status

### 6. Production Preflight & System Health
```bash
# Run 10-point environmental and runtime readiness inspection
python -m app.cli.main preflight [--campaign-id <id>] [--strict] [--json]

# Inspect live operational system health (HEALTHY, DEGRADED, UNHEALTHY, STOPPED)
python -m app.cli.main system health [--json]
```

### 7. Operational Analytics Subsystem
```bash
# Campaign analytics, contact funnel, and authoritative Confirmed Send Rate
python -m app.cli.main analytics campaign <campaign_id> [--json]

# Queue backlog, processing leases, stale leases, and throughput
python -m app.cli.main analytics queue [--campaign-id <campaign_id>] [--json]

# Active runner PID, heartbeat age, and dispatches
python -m app.cli.main analytics runner [--json]

# WhatsApp Web provider session and dispatch error distribution
python -m app.cli.main analytics provider [--json]

# Executive system summary and daily compliance quotas
python -m app.cli.main analytics system [--json]
```

### 8. Initial Owner Provisioning (CLI Bootstrap)
```bash
# Securely bootstrap the initial OWNER account directly against the database (prompts securely for password)
python -m app.cli.main auth bootstrap-owner --username "admin" --email "admin@integra-ist.com" --password-prompt
```

---

## Logging & Data Privacy

- **Dual Log Streams**: Plaintext in `logs/app.log`, structured JSON Lines in `logs/app.json.log`.
- **Log Rotation**: 10 MB limit (`10,485,760 bytes`), 10 backup archives (`app.log.1` ... `app.log.10`).
- **Resilience**: `SafeRotatingFileHandler` catches storage errors and Windows lock contention without crashing the runner daemon.
- **Data Privacy**:
  - Automatic E.164 phone masking with Egyptian mobile normalization (`+201012345678` $\to$ `+2010******78`).
  - Passwords, Bearer/JWT tokens, cookies, secrets scrubbed to `[REDACTED]`.
  - Message bodies omitted by default.
  - Handler-safe sanitization ensures records from child loggers cannot bypass redaction.

---

## Production Runbook

Comprehensive operational guides, emergency stop procedures, incident response playbooks, and manual reconciliation workflows are available in [PRODUCTION_RUNBOOK.md](PRODUCTION_RUNBOOK.md).

---

## Exit Codes

All CLI commands return deterministic exit codes:

| Exit Code | Name | Description |
|---|---|---|
| `0` | `SUCCESS` | Command completed successfully. |
| `1` | `GENERAL_ERROR` | Unhandled operational exception. |
| `2` | `INVALID_ARGUMENT` | Missing or invalid command arguments. |
| `3` | `NOT_FOUND` | Target resource (campaign, message, etc.) was not found. |
| `4` | `INVALID_STATE` | Invalid state transition attempted. |
| `5` | `AUTHENTICATION_REQUIRED` | WhatsApp Web is not authenticated. |
| `6` | `PROVIDER_UNAVAILABLE` | WhatsApp provider could not connect or browser crashed. |
| `7` | `EMERGENCY_STOP_ACTIVE` | Operation blocked because emergency stop is active. |
| `8` | `CIRCUIT_BREAKER_OPEN` | Campaign circuit breaker is tripped. |
| `9` | `CONCURRENCY_ERROR` | Duplicate runner process rejected (process singularity). |
| `10` | `UNKNOWN_OUTCOME_BLOCKED` | Attempted unsafe automated retry of an UNKNOWN_OUTCOME message. |

---

## Verification & Testing

### Automated Test Suite
```bash
# Run full automated regression test suite (219 tests, 0 failures)
pytest -m "not live_browser"

# Run Phase 6 tests with coverage report (91% domain coverage)
pytest tests/test_redaction.py tests/test_logging.py tests/test_analytics_service.py tests/test_preflight.py tests/test_health.py tests/test_cli_analytics.py --cov=app.utils.logger --cov=app.services.analytics_service --cov=app.readiness --cov=app.cli.commands.analytics --cov=app.cli.commands.preflight --cov=app.cli.commands.health
```

### End-to-End Verification Scripts
```bash
# Phase 2: Campaign Management & Templates E2E
python -m app.campaigns.verify_e2e

# Phase 3: Persistent Queue, Rate Limiting & Scheduling E2E
python -m app.scheduler.verify_phase3_e2e

# Phase 4: WhatsApp Web Provider & Error Mapping E2E
python -m app.providers.whatsapp_web.verify_phase4

# Phase 5: Operational CLI & Live Production Runner E2E
python -m app.cli.verify_phase5_e2e

# Phase 6: Logging, Analytics & Production Readiness E2E
python -m app.cli.verify_phase6_e2e

# Phase 7.1, 7.2, 7.3 & 7.4: Web Control Center Test Suite (131 tests, 94% coverage)
pytest tests/web -v --cov=app/web
```

### End-to-End Verification Scripts
```bash
# Phase 2: Campaign Management & Templates E2E
python -m app.campaigns.verify_e2e

# Phase 3: Persistent Queue, Rate Limiting & Scheduling E2E
python -m app.scheduler.verify_phase3_e2e

# Phase 4: WhatsApp Web Provider & Error Mapping E2E
python -m app.providers.whatsapp_web.verify_phase4

# Phase 5: Operational CLI & Live Production Runner E2E
python -m app.cli.verify_phase5_e2e

# Phase 6: Logging, Analytics & Production Readiness E2E
python -m app.cli.verify_phase6_e2e
```

---

## Web Control Center (Phase 7.1, Phase 7.2 & Phase 7.3)

The Web Control Center (`https://auto.integra-ist.com`) provides a secure, web-based management interface for operational visibility and administration.

### Running the Web Server
```bash
# Start Web Control Center locally
uvicorn app.web.app:app --host 127.0.0.1 --port 8000 --reload
```

### Security & Operational Features (Phase 7.1)
- **First-Run Bootstrap**: Atomic OWNER account initialization at `/setup`. Automatically disabled once an OWNER exists.
- **RBAC Hierarchy**: `OWNER` > `ADMIN` > `OPERATOR` > `VIEWER`.
- **Brute-Force Protection**: 5 failed login attempts trigger a progressive 15-minute lockout.
- **Session Security**: Hashed SHA-256 tokens in database, session rotation upon login, sliding 30-min inactivity timeout, max 2 concurrent active sessions per user.
- **CSRF Defense**: Double-submit cookie pattern with HMAC-SHA256 tokens (`X-CSRF-Token` header).
- **OWASP Headers**: Strict Content Security Policy, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`.

### Dashboard & System Control (Phase 7.2)
- **Operational Dashboard (`/dashboard`)**:
  - Real-time KPI summary: System Health, Confirmed Send Rate, Queue Depth, Runner State.
  - Subsystems telemetry grid: Database responsiveness, WhatsApp Web profile status, Emergency Stop state, Circuit Breaker status.
  - Active Campaign summary: Progress bar, completed contacts breakdown, and strict semantic disclaimer.
  - Queue Breakdown: Complete status inventory with prominent amber isolation of `UNKNOWN_OUTCOME` items requiring physical verification.
  - Operational Alerts Panel: Live synthesis of critical system warnings.
- **Real-Time Telemetry Client**:
  - Connects to `/api/v1/events/stream` via Server-Sent Events (SSE).
  - Automatically falls back to 5-second polling of `/api/v1/dashboard/summary` if the SSE connection drops, reconnecting automatically.
- **Safe Runner Control Plane**:
  - Enforces OS file lock singularity (`data/runner.lock`). Exactly one runner daemon permitted.
  - Allows starting runner only for campaigns in `RUNNING` status after executing preflight checks.
  - Graceful termination sends SIGTERM with safe cancellation window without aborting in-flight browser sends.
  - Automatically clears stale locks from terminated runner PIDs.
  - Records immutable `RUNNER_START_REQUESTED` and `RUNNER_STOP_REQUESTED` audit logs.
- **Emergency Stop & Resume**:
  - Target <500ms halt of NEW queue claims.
  - Arming callable by `OPERATOR+` with mandatory reason.
  - Resuming restricted strictly to `ADMIN` or `OWNER` roles with mandatory reason.
- **Strict Semantic Guarantees**:
  - Metric is strictly labeled `CONFIRMED SEND RATE` with locked formula:
    $$\text{Confirmed Send Rate} = \frac{\text{Confirmed Sends}}{\text{Confirmed Sends} + \text{Failed} + \text{Unknown Outcome}} \times 100$$
  - Prohibited delivery/read confirmation terms are completely excluded.

### Campaigns, Templates & Contacts (Phase 7.3)
- **Message Template Library (`/templates`)**:
  - Reusable template catalog with strictly immutable, append-only versioning (`MessageTemplateVersion`).
  - Template version numbers increment monotonically; historical versions cannot be edited or deleted.
  - Variable syntax validation (`{name}`, `{company}`, etc.) and real-time live preview rendering with sample contact data.
- **Campaign Lifecycle Management (`/campaigns`)**:
  - Complete campaign CRUD with search, status filtering, and pagination.
  - Strict domain state transitions (`DRAFT` $\to$ `RUNNING` $\to$ `PAUSED` $\to$ `COMPLETED` / `CANCELLED`).
  - Setting a campaign to `RUNNING` is strictly a domain state transition; it does not launch the daemon runner process.
  - Configuration updates (name, pacing interval, daily quota) are strictly restricted to `DRAFT` status.
  - **Template Snapshot Invariant**: Campaigns snapshot the message template text into `campaigns.message_template` upon creation, tracking lineage via `template_version_id` while isolating active execution from subsequent template edits.
- **Contact Management & Privacy (`/contacts`)**:
  - Complete contact directory with search, tagging, and opt-out management.
  - Strict E.164 phone number normalization and validation.
  - **Privacy Phone Masking**: Non-privileged roles (`VIEWER`, `OPERATOR`) view masked phone numbers (`+201******678`) across all APIs and UI views, while `ADMIN` and `OWNER` view unmasked numbers.
  - **Two-Phase CSV Import (`/contacts/import`)**:
    1. *Dry-Run Validation*: Parses CSV rows, normalizes numbers, identifies invalid phone rows or existing duplicates without modifying the database.
    2. *Atomic Commit*: Persists valid contacts in a single transaction with audit logging.
  - **CSV Export**: Streamed contact export adhering to role-based phone masking rules.
- **Campaign Membership & Eligibility (`/campaigns/{id}`)**:
  - Add contacts to campaigns with automatic eligibility evaluation via `CampaignEligibilityService` (`ELIGIBLE`, `EXCLUDED`, `PENDING`).
  - Contact removal is strictly guarded and permitted only when the campaign is in `DRAFT` status.

### Queue & Message Operations (Phase 7.4)
- **Operational Queue Dashboard (`/queue`)**:
  - Real-time KPI summary: Queue Depth, Processing Leases, Retry Pending, Unknown Outcome, Failed, and Confirmed Send Rate.
  - Emergency Stop and Circuit Breaker alert banners.
  - Server-side bounded pagination and multi-dimensional filtering (Campaign, Status, Contact search, Retry status, Date range).
  - Prominent amber isolation for ambiguous `UNKNOWN_OUTCOME` messages.
- **Granular Message Inspection (`/queue/{id}`)**:
  - Identity, lifecycle timestamps, retry metadata, idempotency key, worker lease state, and rendered message content (read-only, never logged).
  - Immutable audit trail history for each message.
- **Role-Based Phone Privacy Masking**:
  - Non-privileged roles (`VIEWER`, `OPERATOR`) view masked phone numbers (`+201******678`); privileged roles (`ADMIN`, `OWNER`) view unmasked E.164.
- **Safe Queue Operations**:
  - **Stale Lease Recovery (`POST /api/v1/queue/reconcile`)**: Reconciles expired worker leases (>120s) using authoritative Phase 3 `PersistentQueueService.recover_stale_leases()`.
  - **Message Cancellation (`POST /api/v1/queue/{id}/cancel`)**: Safely cancels non-terminal messages using Phase 3 `cancel_message()`.
  - **Critical `UNKNOWN_OUTCOME` Resolution (`POST /api/v1/queue/{id}/resolve-unknown`)**: Strict manual reconciliation requiring explicit reason and exact phrase `CONFIRM-NOT-DELIVERED`. Creates comprehensive `UNKNOWN_OUTCOME_RESOLUTION_REQUESTED` audit record.
  - **Autonomous Retry Semantics Preservation (Inspection Only)**: Retries remain strictly autonomous, governed by exponential backoff with jitter (`RetryManager`) and claimed by `QueueWorker`. Manual retry mutations are deferred to avoid backoff bypasses.

---

## Production Deployment (Vercel + Supabase + Dedicated Worker VPS)

The production architecture separates the stateless web control plane from the stateful execution engine:

### 1. Web Control Plane (Vercel)
- **Domain**: `https://auto.integra-ist.com`
- **Builder**: `@vercel/python` executing ASGI entrypoint `api/index.py`.
- **Database Connection**: Supabase Transaction Pooler (PgBouncer) on port `6543`.
- **Stateless Operation**: No local file locks or background worker subprocesses. Remote runner coordination mediated via `AppSetting` (`system:desired_runner_state`).
- **Logging**: Serverless console streaming directly to `sys.stdout`/`sys.stderr` with automatic PII redaction.

### 2. Relational Database Plane (Supabase PostgreSQL 15+)
- Authoritative PostgreSQL storage for all campaigns, contacts, templates, queue messages, audit logs, and operational settings.
- Dialect-safe migrations verified across PostgreSQL and SQLite test environments.
- Zero synthetic or test data in production; clean schema initialized via Alembic.

### 3. Execution Plane (Dedicated Worker VPS)
- Dedicated Ubuntu Linux host executing `ProductionRunner` via systemd (`deploy/systemd/outreach-runner.service`).
- Dedicated Session connection directly to Supabase on port `5432`.
- Enforces process singularity via host OS file lock (`/opt/whatsapp-outreach/data/runner.lock`).
- Manages persistent Chrome user profile in `/opt/whatsapp-outreach/data/whatsapp_session`.
- Monitors database desired state for graceful stop commands initiated from the web dashboard.

