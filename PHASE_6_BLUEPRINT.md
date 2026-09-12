# Phase 6 — Logging, Analytics & Production Readiness

## Status
PLANNED / DESIGN PENDING APPROVAL

## Governance
- Phase 1: Foundation — **APPROVED**
- Phase 2: Campaign Management & Templates — **APPROVED**
- Phase 3: Persistent Queue, Scheduler, Rate Limiting & Provider Abstraction — **APPROVED**
- Phase 4: WhatsApp Web Provider Integration — **APPROVED**
- Phase 5: CLI Operational Interface & Live Production Runner — **APPROVED**
- **Phase 6: Logging, Analytics & Production Readiness — DESIGN ONLY (NO CODE IMPLEMENTATION YET)**

---

## 1. Objective

Phase 6 elevates the WhatsApp Outreach Automation system from a verified dispatch pipeline to an operationally robust, observable, diagnosable, secure, and production-ready application.

The core objective is to wrap the existing execution engine in comprehensive production scaffolding without altering its dispatch architecture, queue state machine, provider abstraction, or operational controls.

Phase 6 delivers:
1. **Production-Grade Structured Logging**: Unified JSON and human-readable logging with strict PII masking (phone numbers) and automated credential/token redaction.
2. **Comprehensive Event Taxonomy**: Standardized operational events covering process singularity, runner lifecycle, dispatch confirmations, failures, session recovery, circuit breaker trips, and emergency stop actions.
3. **On-Demand Operational Analytics**: High-performance SQL aggregations delivering deep insights into campaign progression, queue backlogs, runner utilization, and provider error distributions—**with zero schema changes**.
4. **Operator Analytics CLI**: Native `outreach analytics` and `outreach system health` subcommands with human-friendly ASCII formatting and machine-readable JSON output.
5. **System Preflight & Runtime Health Model**: Startup validation of Python runtime, configuration, database connectivity, directory permissions, provider profiles, and kernel process locks.
6. **Production Deployment & Disaster Recovery Runbook**: Standardized production directory layout, environment configuration, backup/restore procedures, and step-by-step incident recovery guides.

---

## 2. Current Architecture Baseline

The Phase 6 design strictly respects and preserves the verified architecture established across Phases 1 through 5:

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

### Verified Guarantees Preserved
- `outreach campaign run <campaign_id>` is strictly a domain state-transition command and **never** spawns a runner daemon.
- `outreach runner start --campaign-id <id>` is the **exclusive** production worker execution entrypoint.
- Phase 6 maintains **single-runner / single-campaign** execution. Multi-campaign concurrency remains deferred.
- The OS file lock (`data/runner.lock`) is **authoritative** for process singularity. The database heartbeat is strictly for visibility and recovery.
- Emergency stop latency `<500ms` applies to signal propagation and blocking **new** queue claims. In-flight sends complete to a safe cancellation/confirmation point.
- `UNKNOWN_OUTCOME` dispatches are **never blindly retried**. Manual reconciliation requires explicit reason, `CONFIRM-NOT-DELIVERED` confirmation, and comprehensive audit logging.
- Zero Selenium imports outside `app/providers/whatsapp_web/`.
- Zero anti-ban evasion, CAPTCHA bypass, stealth automation, or fingerprint spoofing.
- 167 automated regression tests pass with 0 failures; Phase 5 domain coverage stands at 90%.

---

## 3. Design Principles

1. **Non-Invasive Observability**: Logging, analytics, and monitoring must observe and report without altering dispatch logic, state machine rules, or database schemas.
2. **Zero Schema Modification**: All operational analytics and health indicators must derive on-demand from existing database tables (`campaigns`, `campaign_contacts`, `messages`, `campaign_batches`, `audit_logs`, `app_settings`, `send_sessions`).
3. **Zero New External Dependencies**: Standard Python libraries (`logging`, `logging.handlers`, `json`, `re`, `shutil`, `os`, `pathlib`, `sys`, `platform`, `time`, `datetime`) and existing project packages (`sqlalchemy`, `pydantic-settings`, `alembic`, `selenium`, `pytest`, `hypothesis`) fulfill all Phase 6 requirements.
4. **Privacy & Security by Default**: Automated redaction of tokens, passwords, cookies, session artifacts, and phone numbers in all log outputs.
5. **Deterministic Exit Codes**: All new operational commands strictly follow the Phase 5 exit code contract (0–10).
6. **No Distributed Lock Ambiguity**: The kernel-level OS file lock remains the sole authority for runner singularity.

---

## 4. Logging Architecture

### 4.1 Log Levels & Operational Semantics

| Level | When to Use | Examples | Default Production Visibility |
|---|---|---|---|
| `DEBUG` | Fine-grained technical details useful during development or root-cause troubleshooting. | DOM element query evaluation, polling sleep intervals, SQL statements, raw checkmark inspection, heartbeat writes. | Suppressed in production (enabled via `LOG_LEVEL=DEBUG`). |
| `INFO` | High-level milestones confirming normal operational progress. | Application startup/shutdown, runner lifecycle transitions, campaign state changes, message claimed, `SEND_CONFIRMED`, successful reconciliation, provider connection. | **Enabled (Default)**. |
| `WARNING` | Recoverable anomalies, degraded operations, or transient interruptions. | Temporary delivery failure scheduled for backoff retry, circuit breaker nearing threshold, stale worker lease recovered, emergency stop engaged, QR scan timeout, session recovery triggered. | **Enabled**. |
| `ERROR` | Non-recoverable failures for a specific entity or operation requiring attention. | Permanent dispatch failure, invalid phone number, session unauthenticated, invalid state transition rejected, `UNKNOWN_OUTCOME` encountered, failed database commit. | **Enabled**. |
| `CRITICAL` | System-wide or process-terminating incidents halting normal operations. | Unhandled daemon exception, authoritative OS lock acquisition blocked by conflict, database connectivity lost, persistent browser crash on startup, unrecoverable disk corruption. | **Enabled**. |

### 4.2 Dual-Logging Contract & Pipeline Architecture

Phase 6 implements a strict dual-destination logging architecture:
- **Destination 1 (Human-Readable)**: `logs/app.log` (Rotating text file) & `stdout` (Console).
  Format: `%(asctime)s | %(levelname)-8s | %(name)s | %(message)s`
- **Destination 2 (Machine-Readable)**: `logs/app.json.log` (Rotating JSON Lines file).
  Format: Deterministic, single-line JSON schema per event.

```text
               Application Logger (Root or Any Child Logger)
               [e.g. app.runner, app.providers.whatsapp_web]
                                     │
                                     ▼
                             LogRecord created
                                     │
                                     ▼
                Propagation directly to Root Handlers
               (Bypasses intermediate Logger-level filters)
                                     │
             ┌───────────────────────┴───────────────────────┐
             ▼                                               ▼
 ┌───────────────────────────────────────┐       ┌───────────────────────────────────────┐
 │ Handler: logs/app.log                 │       │ Handler: logs/app.json.log            │
 │ (Independent Sanitization Stage)      │       │ (Independent Sanitization Stage)      │
 │ - Plaintext Redacting Formatter       │       │ - JSONLines Redacting Formatter       │
 │ - Phone Masking (E.164 & Egyptian)    │       │ - Phone Masking (E.164 & Egyptian)    │
 │ - Scrub credentials, tokens, cookies  │       │ - Scrub credentials, tokens, cookies  │
 │ - Strictly OMIT message body          │       │ - Strictly OMIT message body          │
 └───────────────────┬───────────────────┘       └───────────────────┬───────────────────┘
                     │                                               │
                     ▼                                               ▼
                logs/app.log                                  logs/app.json.log
            (Strictly Sanitized)                            (Strictly Sanitized)
```

#### The Handler-Safe Redaction Security Invariant
> [!IMPORTANT]
> **PRIMARY SECURITY INVARIANT:**
> **NO UNSANITIZED `LogRecord` may reach either persistent log destination.**
>
> In Python's standard `logging` library, records emitted by child loggers (e.g. `logging.getLogger("app.runner.worker")`) propagate directly to root logger handlers via `callHandlers()`, completely bypassing any filter attached solely to the root `Logger` instance.
>
> To guarantee total privacy and security:
> 1. **Handler-Level Guarantee**: Both persistent handlers (`logs/app.log` and `logs/app.json.log`) independently guarantee sanitization prior to writing to disk.
> 2. **Non-Destructive Sanitization**: Formatters apply redaction during stringification / serialization without destructively mutating the shared `LogRecord` in place. This avoids race conditions or inconsistent output between simultaneous handlers.
> 3. **Verification of Invariant**:
>    - `logs/app.log` receives strictly sanitized data.
>    - `logs/app.json.log` receives strictly sanitized data.
>    - Zero raw credentials, tokens, cookies, passwords, or API keys reach either destination.
>    - Zero raw phone numbers reach either persistent destination.
>    - Outbound message body content remains excluded by default.
> 4. **Child Logger Security Test**: A dedicated automated security test will emit sensitive credentials and raw phone numbers through deeply nested child loggers and verify that neither persistent file contains original sensitive strings.

#### Dual-Logging Emission Guarantees
1. **Single Emission per Logical Event**: One logical application event is passed to the logging subsystem exactly once. Handlers serialize and write the single event independently to their respective destinations.
2. **Zero Duplicate Handler Registration**: `configure_logging()` is strictly idempotent. Handlers are inspected prior to addition (`if any(isinstance(h, ...) for h in root.handlers)`) to guarantee handlers are never registered more than once, completely preventing duplicate log entries.
3. **No Double Emission**: Application components log once via standard `logger.<level>()` or structured `log_event()`. Neither destination triggers a re-emission to the other.

### 4.3 Deterministic Structured JSON Schema Contract

Every structured event written to `logs/app.json.log` conforms deterministically to this contract:

```json
{
  "timestamp": "2026-09-11T17:45:00.123Z",
  "level": "INFO",
  "event": "MESSAGE_SEND_CONFIRMED",
  "component": "runner",
  "message": "Message 108 send confirmed on WhatsApp Web.",
  "correlation_id": "run-6a52ccbf-108",
  "campaign_id": 5,
  "campaign_contact_id": 42,
  "message_id": 108,
  "runner_id": "runner_camp5_1789128",
  "provider": "WhatsAppWebProvider",
  "operation": "send_message",
  "result": "SUCCESS",
  "error_code": null,
  "duration_ms": 3420.5,
  "details": {
    "phone": "+2010******78",
    "attempts": 1,
    "status": "SENT"
  }
}
```

#### Field Schema Contract:
- `timestamp` (string, ISO-8601 UTC with milliseconds, required): e.g. `"2026-09-11T17:45:00.123Z"`.
- `level` (string, enum, required): `"DEBUG"`, `"INFO"`, `"WARNING"`, `"ERROR"`, `"CRITICAL"`.
- `event` (string, required): Standardized taxonomy identifier (e.g. `"MESSAGE_SEND_CONFIRMED"`).
- `component` (string, required): Subsystem identifier (`"cli"`, `"runner"`, `"queue"`, `"scheduler"`, `"provider"`, `"campaign"`, `"readiness"`, `"database"`).
- `message` (string, required): Human-readable operational description.
- `correlation_id` (string or null): Execution run ID / trace ID linking related lifecycle actions.
- `campaign_id` (integer or null): Associated campaign identifier, or `null` if system-wide.
- `campaign_contact_id` (integer or null): Associated campaign contact identifier, or `null`.
- `message_id` (integer or null): Associated message identifier, or `null`.
- `runner_id` (string or null): Active runner worker identifier, or `null`.
- `provider` (string or null): Active message provider name (`"WhatsAppWebProvider"`, `"MockMessageProvider"`, or `null`).
- `operation` (string or null): Action name (`"claim"`, `"send_message"`, `"health_check"`, `"reconcile"`, `"lock"`, etc.).
- `result` (string or null): Outcome classification (`"SUCCESS"`, `"TEMPORARY_FAILURE"`, `"PERMANENT_FAILURE"`, `"UNKNOWN_OUTCOME"`, `"BLOCKED"`, `"ABORTED"`).
- `error_code` (string or null): Standardized classification or exit code name, or `null`.
- `duration_ms` (float or null): Execution elapsed duration in milliseconds, or `null`.
- `details` (object, optional): Additional non-sensitive context key-value pairs.

*Contract Rule*: Fields that are unavailable for a specific event (e.g. `message_id` during application startup) are explicitly emitted as `null` or omitted per schema consistency.

### 4.4 Security, Privacy & Sensitive Data Policy

Phase 6 enforces strict privacy and security policies directly in the logging pipeline:

1. **NO Message Body Content Logged**:
   - Outbound rendered message content is **NOT logged by default** in either `app.log` or `app.json.log`.
   - Outbound content is tracked by `message_id` and character length only (`content_length: 45`). Full content remains securely isolated in the database `messages.rendered_content` column.
2. **Zero Credentials, Tokens, or Session Artifacts**:
   - API keys, passwords, bearer tokens, and secrets matching `r"(?i)(api[-_]?key|secret|token|password|auth|credential)"` are scrubbed and replaced with `[REDACTED]`.
   - Browser session artifacts, raw WhatsApp cookies, session tokens, and localStorage contents are strictly suppressed.
3. **E.164 & Egyptian-Aware Phone Number Masking**:
   - Phone masking is **NOT North-America-specific**. It leverages the application's existing E.164 phone parsing logic (`app.utils.phone` and `PhoneValidator`).
   - The masking strategy safely and deterministically handles:
     - **Egyptian Numbers**: e.g. `+201012345678` $\to$ `+2010******78`, `+20 101 234 5678` $\to$ `+2010******78`.
     - **International E.164 Numbers**: e.g. `+14155552671` $\to$ `+1415****71`, `+447911123456` $\to$ `+4479****56`, `+966501234567` $\to$ `+9665****67`.
     - **Common Formatting Characters**: Handles numbers containing spaces, hyphens, dots, and parentheses by extracting the underlying phone sequence and masking it cleanly.
     - **Already-Masked Values (Idempotence)**: Strings that already contain masking asterisks (e.g. `+2010******78`) are recognized and left untouched to prevent corruption or double-masking.
   - **Deterministic Masking Algorithm**:
     - Preserves the country code and initial routing prefix (first 4–5 characters).
     - Preserves the final 2 digits for minimal debugging traceability.
     - Replaces all intermediate digits with asterisks (`*`).
     - Preserves string length and formatting context.
   - **Security Invariant**: **No full phone number may appear in persisted production logs.**

### 4.5 Log Rotation, Sizing & Storage Failure Semantics

- **Rotation Sizing (Unambiguous Byte Semantics)**:
  - Maximum File Size per file: Exactly **`10,485,760 bytes`** (`10 MB`).
  - Backup Retention Count: Exactly **`10`** rotated backup files (`app.log.1` through `app.log.10`, and `app.json.log.1` through `app.json.log.10`).
  - Total Maximum Disk Footprint: `104,857,600 bytes` (~100 MB) per log stream (`~200 MB` total for dual streams).
  - *Rationale*: Guarantees bounded disk consumption while preserving sufficient operational history for post-incident audits.

#### Handling Storage Failures & Degraded Environments:
1. **Disk Full (`ENOSPC` / `errno 28`)**:
   - `RotatingFileHandler` write errors are caught by `logging.Handler.handleError`.
   - A fallback handler attempts a single warning to `sys.stderr`.
   - **Crucial Safety Rule**: A logging write failure **MUST NOT automatically crash the production runner daemon loop**. Dispatches and queue claims may continue safely unless filesystem corruption threatens database integrity.
2. **Log Directory Unavailable / Unwritable**:
   - If `logs/` directory cannot be created or is read-only at startup, preflight immediately raises an error.
   - At runtime, the logging layer redirects log output to `sys.stderr` and sets an internal `logging_degraded = True` diagnostic flag.
3. **File Lock Contention During Rollover (Windows File Locking)**:
   - On Windows, if a log file is momentarily opened by a backup agent or indexer during rollover, `os.rename` can raise `PermissionError [Errno 13]`.
   - The handler catches rollover permission errors, continues appending to the existing active file, and defers rollover to the next write cycle, preventing process termination.
4. **Corrupted Log Files**:
   - Files are opened with `errors='replace'` and UTF-8 encoding to prevent Unicode decode/encode crashes.

---

## 5. Event Taxonomy

Standardized operational events emitted across subsystems:

| Event Identifier | Component | Level | Description |
|---|---|---|---|
| `SYSTEM_STARTUP` | `cli` | `INFO` | Application CLI or command started. |
| `SYSTEM_SHUTDOWN` | `cli` | `INFO` | Application execution completed cleanly. |
| `PREFLIGHT_CHECK_PASSED` | `readiness` | `INFO` | All preflight system checks satisfied. |
| `PREFLIGHT_CHECK_FAILED` | `readiness` | `ERROR` | One or more preflight validation checks failed. |
| `RUNNER_STARTING` | `runner` | `INFO` | Production runner initialization begun. |
| `RUNNER_STARTED` | `runner` | `INFO` | Production runner entered active dispatch loop. |
| `RUNNER_HEARTBEAT` | `runner` | `DEBUG` | Runner heartbeat written to lockfile and DB. |
| `RUNNER_IDLE` | `runner` | `DEBUG` | No claimable messages in queue; runner sleeping. |
| `RUNNER_PAUSED` | `runner` | `WARNING` | Runner paused by campaign state, circuit breaker, or e-stop. |
| `RUNNER_STOPPING` | `runner` | `INFO` | Termination signal intercepted; commencing graceful shutdown. |
| `RUNNER_STOPPED` | `runner` | `INFO` | Production runner stopped cleanly at safe point. |
| `RUNNER_FAILED` | `runner` | `CRITICAL` | Runner loop terminated due to fatal error. |
| `PROCESS_LOCK_ACQUIRED` | `runner` | `INFO` | Authoritative OS file lock acquired. |
| `PROCESS_LOCK_CONFLICT` | `runner` | `CRITICAL` | Duplicate runner process detected on host. |
| `PROCESS_LOCK_RELEASED` | `runner` | `INFO` | OS file lock released cleanly. |
| `STALE_METADATA_RECOVERED` | `runner` | `WARNING` | Inactive PID metadata cleared from lock/DB. |
| `CAMPAIGN_STATE_TRANSITION` | `campaign` | `INFO` | Campaign status changed (e.g. DRAFT $\to$ RUNNING). |
| `QUEUE_CLAIM_SUCCESS` | `queue` | `DEBUG` | Message lease atomically claimed by worker. |
| `STALE_LEASE_RECOVERED` | `queue` | `WARNING` | Expired message lease reset to RETRY_PENDING. |
| `MESSAGE_SEND_CONFIRMED` | `provider` | `INFO` | Message confirmed sent via UI checkmarks (`SEND_CONFIRMED`). |
| `MESSAGE_TEMPORARY_FAILURE` | `provider` | `WARNING` | Transient send failure; scheduled for backoff retry. |
| `MESSAGE_PERMANENT_FAILURE` | `provider` | `ERROR` | Permanent send failure; marked FAILED (no retry). |
| `MESSAGE_UNKNOWN_OUTCOME` | `provider` | `CRITICAL` | Post-send interruption; duplicate risk; blind retry blocked. |
| `MESSAGE_MANUAL_OVERRIDE` | `queue` | `INFO` | Operator manually reconciled UNKNOWN_OUTCOME message. |
| `PROVIDER_CONNECTED` | `provider` | `INFO` | Browser automation initialized and connected. |
| `PROVIDER_HEALTH_CHECK_FAIL` | `provider` | `ERROR` | Provider health check failed (unauthenticated or unresponsive). |
| `SESSION_AUTHENTICATING` | `provider` | `INFO` | Awaiting operator QR code scan. |
| `SESSION_CONNECTED` | `provider` | `INFO` | WhatsApp Web authenticated successfully. |
| `SESSION_LOST` | `provider` | `CRITICAL` | WhatsApp Web disconnected, logged out, or crashed. |
| `CIRCUIT_BREAKER_TRIPPED` | `scheduler` | `CRITICAL` | Consecutive error threshold exceeded; campaign PAUSED. |
| `CIRCUIT_BREAKER_RESET` | `scheduler` | `INFO` | Successful send observed; consecutive errors reset. |
| `EMERGENCY_STOP_ACTIVATED` | `scheduler` | `CRITICAL` | Emergency stop killswitch engaged system-wide. |
| `EMERGENCY_STOP_RESUMED` | `scheduler` | `INFO` | Emergency stop cleared; queue dispatches permitted. |

---

## 6. Analytics Architecture

### 6.1 Database Query Strategy (Zero Materialization)
All metrics are computed on-demand via efficient SQL aggregations against existing tables and indexes:
- `messages`: Filtered on `campaign_id`, `status`, `error_type`, `locked_at`, `created_at`. Indexed by status and campaign.
- `campaign_contacts`: Grouped by `status`.
- `campaigns`: Pacing, threshold, and status parameters.
- `audit_logs`: Filtered on `event_type` and timestamps for event counts and error distributions.
- `send_sessions`: Execution durations and batch statistics.

### 6.2 Delivery Semantics & Lifecycle Classification

Phase 6 analytics explicitly enforce strict delivery semantics. In browser-automated messaging via WhatsApp Web, the application observes UI events rather than network delivery receipts. Therefore:

> [!IMPORTANT]
> **DELIVERY SEMANTIC BOUNDARIES:**
> - `SEND_CONFIRMED` is based **strictly on WhatsApp Web UI evidence** (the compose box was submitted and an outbound message bubble / confirmation checkmark was observed in the active chat thread).
> - `SEND_CONFIRMED` **MUST NEVER** be represented, labeled, or reported as "delivered", "read", or "received" by the recipient device. WhatsApp Web UI automation cannot provide synchronous recipient receipt signals.
> - The application makes **zero claim of exactly-once external delivery**. External duplicate prevention is best-effort bounded by internal database idempotency and single-worker lease locking.
> - `UNKNOWN_OUTCOME` is **strictly excluded from success calculations** and must never be counted as a confirmed send.

#### Message Lifecycle State Definitions in Analytics:
| Lifecycle State | Strict Definition & Evidence Basis | Handling in Analytics |
|---|---|---|
| `SEND_CONFIRMED` | Message successfully submitted via compose box and outbound message bubble/checkmark confirmed in UI. | Counted as successful confirmed dispatch. |
| `UNKNOWN_OUTCOME` | Send action initiated, but browser crashed, timed out, or disconnected before confirmation checkmark appeared. Recipient may or may not have received message. Blind retry is strictly blocked. | Counted in attempts and failures; excluded from confirmed sends. Alerts operator. |
| `FAILED` | Definitive terminal failure: invalid recipient phone number, recipient blocked, or retry limit exceeded without success. | Counted as permanent failure. |
| `RETRY_PENDING` | Transient failure (timeout, network blip, UI lag) awaiting backoff retry. | Counted in active queue backlog; not terminal. |
| `SKIPPED` | Contact excluded by eligibility rules, frequency limits, or manual skip flag. | Counted as non-dispatched exclusion. |
| `CANCELLED` | Message dequeued or aborted prior to claiming due to campaign cancellation or manual intervention. | Counted as terminal cancellation. |

### 6.3 Analytics Dimensions

#### 1. Campaign Analytics
- **Total Contacts**: `count(campaign_contacts.id)`.
- **Eligibility Breakdown**: Contacts in `ELIGIBLE`, `EXCLUDED`, `PENDING`.
- **Queue Progression**: Contacts in `QUEUED`, `PROCESSING`, `RETRY_PENDING`, `SENT` (`SEND_CONFIRMED`), `FAILED`, `SKIPPED`, `CANCELLED`.
- **Retry Backlog**: Messages with `status='RETRY_PENDING'`.
- **Unknown Outcomes**: Messages with `status='FAILED' AND error_type='UNKNOWN_OUTCOME'`.
- **CONFIRMED SEND RATE**:
  ```text
  confirmed_send_rate = (confirmed_sends / (confirmed_sends + failed + unknown_outcome)) * 100
  ```
  Where:
  - `confirmed_sends`: Count of `SEND_CONFIRMED` terminal outcomes (WhatsApp Web UI checkmark observed).
  - `failed`: Count of terminal `FAILED` outcomes (permanent failure or retry exhaustion).
  - `unknown_outcome`: Count of `UNKNOWN_OUTCOME` terminal/blocked outcomes (ambiguous post-send disruption).
  
  *Critical Metric Rules*:
  - **`RETRY_PENDING` must NOT be included in the denominator** because transient failures have not yet reached a terminal outcome.
  - **`SKIPPED` and `CANCELLED` must NOT be included in this metric** because they represent non-dispatched exclusions/cancellations rather than message dispatch outcomes (tracked separately in `exclusion_rate` and `completion_percentage`).
  - **`UNKNOWN_OUTCOME` is strictly excluded from confirmed sends** and included in the denominator, penalizing the rate until manually investigated.
  - **Naming & Scope**: This metric is strictly named **CONFIRMED SEND RATE**. It must **never** be labeled or referred to as "delivery rate", "received rate", or "read rate". It represents internal send-operation confirmation based strictly on Phase 4 provider UI semantics.

- **Failure Rate**:
  ```text
  failure_rate = ((failed + unknown_outcome) / (confirmed_sends + failed + unknown_outcome)) * 100
  ```
- **Completion Percentage**:
  ```text
  completion_percentage = ((confirmed_sends + failed + skipped + excluded) / total_contacts) * 100
  ```
- **Duration**: `completed_at - started_at` (or `now - started_at` if active).

#### 2. Queue Analytics
- **Queue Depth**: Immediate backlog ready for claiming (`status='QUEUED'`).
- **Processing Count**: Messages currently locked by workers (`status='PROCESSING'`).
- **Retry Backlog**: Messages awaiting retry backoff (`status='RETRY_PENDING'`).
- **Stale Leases**: Messages in `PROCESSING` with `locked_at < now - lease_timeout`.
- **Terminal Counts**: Total `SENT` (`SEND_CONFIRMED`), `FAILED`, `SKIPPED`, `CANCELLED`.
- **Confirmed Throughput**: Confirmed dispatches per hour over the last 1h, 6h, and 24h windows.
- **Average UI Confirmation Latency**: `avg(updated_at - locked_at)` for `SENT` messages.

#### 3. Runner Analytics
- **State**: `RUNNING`, `PAUSED`, `IDLE`, `STOPPED`, `FAILED`.
- **Uptime**: Time elapsed since runner start timestamp.
- **Active Campaign**: ID and name of target campaign.
- **Heartbeat Age**: Seconds elapsed since last heartbeat write.
- **Messages Processed**: Confirmed sends and failures during active execution.
- **Circuit Breaker Status**: Consecutive error count vs. campaign threshold.

#### 4. Provider Analytics
- **Connection & Session State**: `CONNECTED`, `AUTHENTICATING`, `DISCONNECTED`, `SESSION_LOST`.
- **Health Check Status**: `HEALTHY` or `UNHEALTHY`.
- **Dispatch Distribution**:
  - Total Attempts
  - Confirmed Sends (`SEND_CONFIRMED`)
  - Temporary Errors (Rate-limited, Timeout, Pre-send Crash)
  - Permanent Errors (Invalid Number, Blocked)
  - Ambiguous Outcomes (`UNKNOWN_OUTCOME`)
- **Average UI Confirmation Latency**: Elapsed time between compose-box send click and UI checkmark appearance.

#### 5. Operational & Incident Analytics
- **Emergency Stop Events**: Historical trigger count, last reason, active status.
- **Circuit Breaker Events**: Total trips in last 24h, associated campaigns.
- **Stale Recoveries**: Message lease recoveries and stale runner metadata cleanups.
- **Manual Reconciliation Events**: Count of `UNKNOWN_OUTCOME` overrides with operator IDs.

---

## 7. Analytics CLI (`outreach analytics`)

Phase 6 introduces the `outreach analytics` command hierarchy:

```text
outreach analytics
  ├── campaign <campaign_id>   # Detailed campaign performance & contact breakdown
  ├── queue [--campaign-id <id>] # Queue backlog, leases, throughput, and state distribution
  ├── runner                   # Active runner uptime, heartbeat age, and dispatches
  ├── provider                 # WhatsApp provider connectivity, health, and error breakdown
  └── system                   # High-level operational executive summary dashboard
```

### 7.1 Command Specifications

#### `outreach analytics campaign <campaign_id> [--json]`
- **Arguments**: `campaign_id` (integer, required).
- **Options**: `--json` (machine-readable JSON output).
- **Output**:
  - ASCII Card: Campaign name, status, start/end timestamps, total duration.
  - Table: Contacts summary (Eligible, Excluded, Queued, Confirmed Sent, Failed, Unknown Outcome).
  - Pacing & Rate Limits: Configured delays, daily limit, errors vs. threshold.
  - Performance: Confirmed Send Rate %, Failure Rate %, Completion Percentage %.
  - Semantic Note: Clearly displays that "Confirmed Sent" reflects UI confirmation, not device receipt.
- **Exit Codes**: `SUCCESS (0)`, `NOT_FOUND (3)` if campaign missing, `GENERAL_ERROR (1)`.

#### `outreach analytics queue [--campaign-id <id>] [--json]`
- **Arguments**: `campaign_id` (integer, optional filter).
- **Output**:
  - Backlog Table: Counts for PENDING, QUEUED, PROCESSING, RETRY_PENDING, SENT (CONFIRMED), FAILED, UNKNOWN_OUTCOME.
  - Throughput Metric: Confirmed messages dispatched in last 1h and 24h.
  - Health Warnings: Warning box if stale leases $> 0$ or UNKNOWN_OUTCOME $> 0$.
- **Exit Codes**: `SUCCESS (0)`, `GENERAL_ERROR (1)`.

#### `outreach analytics runner [--json]`
- **Output**:
  - Active Runner Card: State, PID, Worker ID, Campaign ID, Started At, Uptime.
  - Heartbeat Status: Last heartbeat timestamp, age in seconds, liveliness status (informational only).
  - Session Throughput: Confirmed dispatches completed during current runner lifecycle.
- **Exit Codes**: `SUCCESS (0)`, `GENERAL_ERROR (1)`.

#### `outreach analytics provider [--json]`
- **Output**:
  - Session Status Card: State (`CONNECTED`), Profile path, Headless mode, Health check.
  - Error Distribution Table: Temporary errors vs. Permanent errors vs. UNKNOWN_OUTCOME.
  - Timing: Average send confirmation checkmark latency.
- **Exit Codes**: `SUCCESS (0)`, `GENERAL_ERROR (1)`.

#### `outreach analytics system [--json]`
- **Output**:
  - Executive Dashboard combining System Health, Active Runner, Queue Depth, Provider State, Emergency Stop status, and 24h throughput.
- **Exit Codes**: `SUCCESS (0)`, `GENERAL_ERROR (1)`.

---

## 8. Production Preflight & Readiness

Phase 6 provides a comprehensive preflight validation suite that operates in two contexts:
1. **Standalone Diagnostic Probe (`outreach preflight [--strict]`)**: Non-locking command used by operators or deployment scripts to evaluate environment readiness prior to launching services.
2. **Runner Startup Preflight Guard**: Automated internal gate inside `outreach runner start`.

> [!IMPORTANT]
> **RUNNER STARTUP LOCK & PREFLIGHT ORDER:**
> Inside `outreach runner start`, the startup order strictly enforces:
> 1. **Basic Configuration Validation**: Quick check of CLI arguments and basic environment variables.
> 2. **Acquire Authoritative OS Process Lock (`data/runner.lock`)**: The runner MUST acquire the OS process lock **FIRST** before executing runtime readiness checks. If another live runner process holds the lock, execution terminates immediately with `ExitCode.CONCURRENCY_ERROR (9)`, preventing wasteful or conflicting preflight probing.
> 3. **Execute Preflight / Runtime Readiness Checks**: Full suite of checks (DB connectivity, migrations, directories, browser binary, session profile, emergency stop, circuit breaker).
> 4. **Preflight Failure Release**: If ANY preflight check fails after the OS lock is acquired, the runner **cleanly releases the OS process lock**, purges any transient startup metadata, and halts with the corresponding specific exit code.
> 5. **Launch Daemon Loop**: Only when all preflight checks pass does the runner initialize worker threads, register signal handlers, record the informational database heartbeat, and begin queue processing.
> - The OS lock remains strictly authoritative for process singularity; the database heartbeat remains strictly informational for visibility and recovery.

### 8.1 Preflight Inspection Matrix

| Check Category | Validation Step | Standalone Probe Behavior | Runner Startup Behavior |
|---|---|---|---|
| **Python Runtime** | Python $\ge 3.8.0$, 64-bit architecture, UTF-8 filesystem encoding. | Halts with `GENERAL_ERROR (1)`. | Releases lock; halts with `GENERAL_ERROR (1)`. |
| **Configuration** | Pydantic settings validation (`DATABASE_URL`, `APP_TIMEZONE`, `GLOBAL_DAILY_LIMIT` $> 0$). | Halts with `INVALID_ARGUMENT (2)`. | Releases lock; halts with `INVALID_ARGUMENT (2)`. |
| **Directories & Permissions** | Verify existence and write permissions for `data/`, `logs/`, and parent of `data/whatsapp_session`. | Halts with `GENERAL_ERROR (1)`. | Releases lock; halts with `GENERAL_ERROR (1)`. |
| **Database Connectivity** | Test connection (`SELECT 1`), verify SQLite WAL mode / PostgreSQL read-write transaction. | Halts with `GENERAL_ERROR (1)`. | Releases lock; halts with `GENERAL_ERROR (1)`. |
| **Database Schema** | Verify Alembic current migration head is applied. | Halts with `GENERAL_ERROR (1)`. | Releases lock; halts with `GENERAL_ERROR (1)`. |
| **Browser Environment** | Chrome executable located; chromedriver or Selenium Manager operational. | Halts with `PROVIDER_UNAVAILABLE (6)`. | Releases lock; halts with `PROVIDER_UNAVAILABLE (6)`. |
| **Session Authentication** | Check persistent profile directory exists and contains valid session files. | Returns `AUTHENTICATION_REQUIRED (5)`. | Releases lock; returns `AUTHENTICATION_REQUIRED (5)`. |
| **Process Lock Singularity** | Verify `data/runner.lock` is not locked by another live process. | Non-locking probe; returns `CONCURRENCY_ERROR (9)` if locked. | **Precedes preflight**: acquires kernel byte lock; halts with `CONCURRENCY_ERROR (9)` on conflict. |
| **Emergency Stop Status** | Verify emergency stop is not engaged in database. | Returns `EMERGENCY_STOP_ACTIVE (7)`. | Releases lock; returns `EMERGENCY_STOP_ACTIVE (7)`. |
| **Circuit Breaker Status** | Verify target campaign circuit breaker is not tripped. | Returns `CIRCUIT_BREAKER_OPEN (8)`. | Releases lock; returns `CIRCUIT_BREAKER_OPEN (8)`. |

---

## 9. Runtime Health Model

Phase 6 formalizes the overall application health into four distinct operational states:

```
  ┌─────────────────────────────────────────────────────────────┐
  │                    RUNTIME HEALTH STATES                    │
  ├───────────────┬───────────────┬───────────────┬─────────────┤
  │    HEALTHY    │   DEGRADED    │   UNHEALTHY   │   STOPPED   │
  └───────────────┴───────────────┴───────────────┴─────────────┘
```

### 9.1 State Definitions & Evaluation Rules

1. **`HEALTHY`**:
   - Production runner is alive and actively processing.
   - Heartbeat age $< 30$ seconds.
   - Provider connected and `health_check() == True`.
   - Emergency stop is inactive.
   - Circuit breaker is closed.
   - Stale leases $= 0$.
   - Unreconciled `UNKNOWN_OUTCOME` $= 0$.
2. **`DEGRADED`**:
   - Heartbeat age between $30$ and $60$ seconds.
   - Recent temporary errors observed in last 10 dispatches, but below circuit breaker threshold.
   - Queue backlog elevated ($> 500$ messages).
   - Unreconciled `UNKNOWN_OUTCOME` count $> 0$ (requires operator review, but dispatches continue for other contacts).
   - Rate limiter batch pauses actively throttling dispatches.
3. **`UNHEALTHY`**:
   - Production runner crashed or PID dead with orphaned lock.
   - Heartbeat age $> 60$ seconds while runner is supposedly active.
   - Provider health check failing or remote session logged out (`SESSION_LOST`).
   - Circuit breaker tripped (`PAUSED`).
   - Database connection timeout or I/O failure.
   - Stale worker leases detected ($> 0$).
4. **`STOPPED`**:
   - Production runner intentionally stopped by operator.
   - Emergency stop actively engaged system-wide.
   - No active campaign running.

---

## 10. Monitoring Design

Monitoring in Phase 6 provides operator visibility into four key vectors without imposing high database load:

### 10.1 Monitoring Vectors & Collection Frequencies

1. **Runner Vector** (Frequency: every 15s via heartbeat or on-demand CLI):
   - Metric: Process alive boolean (`is_pid_alive(pid)`).
   - Metric: Heartbeat freshness (`now - last_heartbeat_timestamp`).
   - Metric: Memory footprint & CPU usage.
2. **Queue Backlog Vector** (Frequency: on-demand or periodic poll):
   - Metric: Queue Depth (`status='QUEUED'`).
   - Metric: Processing In-Flight (`status='PROCESSING'`).
   - Metric: Expired Leases (`locked_at < now - 300s`).
   - Metric: Retry Backlog (`status='RETRY_PENDING'`).
3. **Provider Health Vector** (Frequency: pre-claim check in runner):
   - Metric: Provider connected boolean.
   - Metric: Chat compose box DOM accessibility.
   - Metric: Browser crash / disconnect events.
4. **Reliability & Safety Vector** (Frequency: per-dispatch):
   - Metric: Circuit breaker consecutive failures counter.
   - Metric: Emergency stop database flag.
   - Metric: Total `UNKNOWN_OUTCOME` count.

---

## 11. Alerting Design

Phase 6 defines operational alert thresholds categorized strictly into `WARNING` and `CRITICAL`.

> [!NOTE]
> External notification integrations (e.g. Webhooks, Slack, PagerDuty, Email) are explicitly **deferred** to future phases. Phase 6 establishes the detection criteria, log events, and CLI diagnostic output.

### 11.1 Alert Matrix

| Alert Condition | Severity | Trigger Condition | Automated System Action | Operator Action Required |
|---|---|---|---|---|
| **Stale Runner Heartbeat** | `CRITICAL` | Heartbeat age $> 60$s while lockfile present. | Runner marked dead in diagnostics. | Inspect host; restart runner via `outreach runner start`. |
| **Duplicate Runner Conflict** | `CRITICAL` | Second runner attempts startup on same host. | Second runner blocked immediately (exit code 9). | Verify single runner policy. |
| **Emergency Stop Active** | `CRITICAL` | `emergency_stop == True` in DB. | All new message claims halted (<500ms). | Resolve incident; run `outreach emergency-resume`. |
| **Circuit Breaker Tripped** | `CRITICAL` | Consecutive send errors $\ge$ campaign threshold. | Campaign transitioned to `PAUSED`; dispatches halted. | Inspect WhatsApp Web for restriction/disconnect; resume campaign. |
| **UNKNOWN_OUTCOME Detected** | `CRITICAL` | Send interrupted mid-confirmation. | Message marked FAILED; blind retry blocked. | Inspect WhatsApp chat; run `outreach queue override`. |
| **WhatsApp Session Lost** | `CRITICAL` | WhatsApp Web shows QR code or remote logout dialog. | Provider enters `SESSION_LOST`; runner halts. | Run `outreach session login` to re-authenticate. |
| **Database Unavailable** | `CRITICAL` | DB query fails with `OperationalError`. | Runner retries with backoff, then shuts down cleanly. | Inspect SQLite file permissions or PostgreSQL service. |
| **Stale Message Leases** | `WARNING` | Messages in `PROCESSING` past lease timeout. | Recovered automatically to `RETRY_PENDING` on startup/poll. | Informational; verify worker stability. |
| **Elevated Temporary Failures**| `WARNING` | Error count $> 50\%$ of threshold. | Logged as warning; exponential backoff applied. | Check recipient numbers and network latency. |
| **Queue Backlog Exceeded** | `WARNING` | Queued messages $> 1,000$. | Throttled by `RateLimiter` daily caps. | Plan batch sizing or adjust campaign limits. |

---

## 12. Deployment Architecture

### 12.1 Standardized Directory Layout
```text
whatsapp-outreach-automation/
├── app/                           # Application source code
│   ├── cli/                       # Operational CLI entrypoint and commands
│   ├── runner/                    # Production runner daemon and process locking
│   ├── scheduler/                 # Queue worker, rate limiter, circuit breaker
│   ├── campaigns/                 # Campaign manager and domain logic
│   ├── providers/                 # WhatsApp Web and mock providers
│   ├── models/                    # SQLAlchemy ORM models
│   └── utils/                     # Settings, logging, phone formatters
├── config/                        # Configuration templates
│   ├── .env.example               # Sanitized environment template
│   └── alembic.ini                # Migration configuration
├── data/                          # Runtime persistent state (Excluded from Git)
│   ├── whatsapp_outreach.db       # SQLite database (WAL mode)
│   ├── runner.lock                # Authoritative OS process lockfile
│   └── whatsapp_session/          # Chrome user profile, session cookies & cache
├── logs/                          # Rotating operational logs (Excluded from Git)
│   ├── app.log                    # Plaintext formatted log
│   └── app.json.log               # Structured JSON lines event log
├── backups/                       # Database backup snapshots (Excluded from Git)
└── tests/                         # Automated unit, integration, and E2E tests
```

### 12.2 Production Configuration (`.env`)
```bash
# Database Configuration (SQLite WAL or PostgreSQL)
DATABASE_URL=sqlite:///./data/whatsapp_outreach.db

# Timezone Configuration (IANA)
APP_TIMEZONE=Africa/Cairo

# Logging Configuration
LOG_LEVEL=INFO
LOG_FILE=logs/app.log

# Global Rate Limits
GLOBAL_DAILY_LIMIT=100

# WhatsApp Web Automation Settings
WHATSAPP_SESSION_PATH=./data/whatsapp_session
WHATSAPP_HEADLESS=false
WHATSAPP_BROWSER_TIMEOUT=30
WHATSAPP_PAGE_LOAD_TIMEOUT=45
WHATSAPP_QR_TIMEOUT=120
WHATSAPP_CHROME_BINARY=
WHATSAPP_CHROMEDRIVER_PATH=

# Production Runner Daemon Settings
RUNNER_POLL_INTERVAL_SECONDS=5
RUNNER_HEARTBEAT_SECONDS=15
RUNNER_LOCK_FILE=./data/runner.lock
CLI_PAGE_SIZE=20
```

---

## 13. Startup & Shutdown Procedures

### 13.1 Production Startup Sequence

#### 1. Operator Deployment Workflow
```
  Step 1: Environment & Config Validation
          outreach preflight
                  ↓
  Step 2: Database Migration Check
          alembic upgrade head
                  ↓
  Step 3: Session Authentication Verification
          outreach session status
          (If unauthenticated: outreach session login)
                  ↓
  Step 4: Campaign Activation
          outreach campaign run <campaign_id>
          (Domain state transition to RUNNING)
                  ↓
  Step 5: Production Runner Daemon Start
          outreach runner start --campaign-id <campaign_id>
                  ↓
  Step 6: Operational Verification
          outreach runner status
          outreach analytics system
```

#### 2. Internal Runner Execution Pipeline (`outreach runner start`)

Inside the runner process, startup strictly obeys the following ordered execution pipeline:

```
  outreach runner start --campaign-id <id>
                  │
                  ▼
  [Stage 1] Basic Argument & Config Validation
  - Validate campaign ID exists in database
  - Validate basic environment settings
                  │
                  ▼
  [Stage 2] Acquire Authoritative OS Process Lock
  - Open data/runner.lock and acquire kernel byte lock (msvcrt / fcntl)
  - If locked by another active PID:
    → Halt IMMEDIATELY with ExitCode.CONCURRENCY_ERROR (9)
    → Preflight checks are NOT executed pointlessly
                  │ (Lock Acquired Successfully)
                  ▼
  [Stage 3] Runtime Readiness / Preflight Checks
  - Verify database connectivity & applied migrations
  - Verify data/ and logs/ directory write permissions
  - Verify Chrome browser executable and Selenium driver
  - Verify persistent WhatsApp session profile exists
  - Verify system-wide emergency stop is INACTIVE
  - Verify target campaign circuit breaker is CLOSED
  - Recover any stale message leases from crashed workers
                  │
          ┌───────┴───────┐
      [Passed]        [Failed]
          │               │
          │               ▼
          │       [Preflight Failure Cleanup]
          │       - Cleanly release kernel OS file lock
          │       - Purge any transient startup state
          │       - Exit with specific preflight failure code
          │         (e.g. 1, 2, 5, 6, 7, 8)
          ▼
  [Stage 4] Launch ProductionRunner Daemon Loop
  - Register OS termination signals (SIGTERM, SIGINT)
  - Record informational database heartbeat in app_settings
    (Heartbeat is strictly for visibility & recovery; NOT lock authority)
  - Emit RUNNER_STARTED event to dual log destinations
  - Enter message claiming, rate-limited dispatch, and monitoring loop
```

### 13.2 Production Shutdown Sequence
1. **Operator Signals Shutdown**:
   ```bash
   outreach runner stop
   ```
2. **Signal Interception**:
   - `SignalCoordinator` intercepts `SIGTERM`/`SIGINT`.
   - Sets `shutdown_requested = True`.
3. **Safe Cancellation Point**:
   - If worker is currently idle or sleeping: terminates immediately.
   - If worker is in the middle of a WhatsApp Web send: **allows the send action to complete** to avoid corrupting browser state or creating an avoidable ambiguous outcome.
   - Ambiguous outcome (if browser fails during confirmation checkmark): classified as `UNKNOWN_OUTCOME`.
4. **Lock & Resource Release**:
   - OS file lock handle closed; kernel releases byte lock.
   - DB heartbeat setting removed.
   - Browser driver cleanly closed via `session_manager.shutdown()`.
   - Exit code `0` returned.

---

## 14. Backup & Disaster Recovery Design

### 14.1 Backup Classification

| Asset | Criticality | Backup Method | Restoration Strategy |
|---|---|---|---|
| **Database (`data/whatsapp_outreach.db`)** | **CRITICAL** | Automated online SQLite backup (`VACUUM INTO 'backups/backup_YYYYMMDD_HHMMSS.db'`). | Restore database file; run `alembic upgrade head` to verify schema. |
| **Configuration (`.env`)** | **CRITICAL** | Encrypted secret vault / configuration management. | Recreate `.env` from template and vault secrets. |
| **Persistent Session (`data/whatsapp_session`)** | **LOW / CAUTION** | **Do NOT replicate across machines.** | If corrupted or lost: delete directory and run `outreach session login` to re-authenticate with QR code. |
| **Operational Logs (`logs/`)** | **MEDIUM** | Standard log rotation with optional external archiving. | Rotated automatically (10 backups $\times$ 10MB). |

### 14.2 Disaster Recovery Procedure
1. **Server / Host Loss**:
   - Provision fresh environment with Python 3.8+ and Chrome.
   - Deploy repository codebase.
   - Restore latest database snapshot into `data/whatsapp_outreach.db`.
   - Run `alembic upgrade head`.
   - Run `outreach session login` to re-authenticate WhatsApp Web.
   - Resume runner: `outreach runner start --campaign-id <id>`.
2. **Database Corruption**:
   - Stop active runner: `outreach runner stop`.
   - Replace corrupt file with latest snapshot from `backups/`.
   - Run `outreach queue reconcile` to recover any interrupted leases.
   - Restart runner.

---

## 15. Failure & Recovery Runbook

### Incident 1: Production Runner Process Crash (Dead PID / Orphaned Lock)
- **Symptom**: `outreach runner status` reports dead PID, or runner stopped unexpectedly.
- **Root Cause**: Host killed process (OOM), power outage, or unhandled exception.
- **Recovery Procedure**:
  1. Verify process is truly dead: `outreach runner status`.
  2. The kernel automatically releases the OS file lock upon process death.
  3. Start the runner: `outreach runner start --campaign-id <id>`.
  4. `ProcessLock` will detect the dead PID in the lock file, clean up stale metadata, recover expired message leases, and resume dispatches seamlessly.

### Incident 2: WhatsApp Web Browser Crash / Driver Disconnection
- **Symptom**: Runner logs `PROVIDER_UNAVAILABLE` or `WhatsAppBrowserCrashError`.
- **Root Cause**: Chrome crashed or exceeded memory limit.
- **Recovery Procedure**:
  1. `WhatsAppSessionManager` attempts in-memory restart reusing the persistent profile in `data/whatsapp_session`.
  2. If browser remains unresponsive, stop runner: `outreach runner stop`.
  3. Test session: `outreach session status`.
  4. If disconnected, run: `outreach session login`.
  5. Restart runner: `outreach runner start --campaign-id <id>`.

### Incident 3: Circuit Breaker Tripped
- **Symptom**: Campaign status changed to `PAUSED`; runner logs `Circuit breaker tripped`.
- **Root Cause**: Consecutive errors exceeded campaign threshold (e.g. 3 consecutive timeouts or network disconnects).
- **Recovery Procedure**:
  1. Inspect recent errors: `outreach queue status --campaign-id <id>`.
  2. Check WhatsApp Web connectivity: `outreach session status`.
  3. If network issue resolved, resume campaign: `outreach campaign resume <id>`.
  4. Active runner will detect `RUNNING` status and resume dispatches automatically.

### Incident 4: Emergency Stop Activated
- **Symptom**: Dispatches halted across all campaigns; `outreach emergency-status` reports ACTIVE.
- **Root Cause**: Operator engaged killswitch.
- **Recovery Procedure**:
  1. Confirm reason: `outreach emergency-status`.
  2. Resolve underlying condition (e.g. verify recipient list, fix messaging template).
  3. Resume system: `outreach emergency-resume --reason "Incident resolved"`.
  4. Note: Campaigns paused individually remain `PAUSED` and must be resumed with `outreach campaign resume <id>`.

### Incident 5: UNKNOWN_OUTCOME Message Encountered
- **Symptom**: `outreach queue status` alerts `UNKNOWN_OUTCOME MESSAGES PENDING`.
- **Root Cause**: Browser crashed or network dropped after send click before confirmation checkmark appeared.
- **Recovery Procedure**:
  1. Inspect the message: `outreach queue inspect <message_id>`. Note recipient phone and timestamp.
  2. **Manual Inspection**: Open WhatsApp Web in browser or on phone; manually search recipient phone number.
  3. **Case A (Message was delivered)**:
     - Do NOT retry. Message was already delivered externally. Leave as FAILED or mark SKIPPED.
  4. **Case B (Message was NOT delivered)**:
     - Execute override:
       ```bash
       outreach queue override <message_id> --reason "Manually inspected chat thread; confirmed message not delivered."
       ```
     - When prompted, type: `CONFIRM-NOT-DELIVERED`.
     - Message transitions to `QUEUED` and records a full `AuditLog` entry.

---

## 16. Security Review

| Security Dimension | Requirement | Implementation in Phase 6 |
|---|---|---|
| **Confidentiality** | Sensitive credentials, tokens, and cookies never logged. | Centralized `RedactingFilter` scrubs passwords, tokens, cookies, and secrets. |
| **Privacy (PII)** | Recipient phone numbers masked in logs and displays. | `mask_phone()` applied across CLI tables, cards, and log formatters (`+1555****123`). |
| **Repository Integrity** | No credentials, databases, sessions, or lockfiles committed. | `.gitignore` covers `.env`, `data/`, `logs/`, `backups/`, `*.lock`, `whatsapp_session/`. |
| **Access Control** | Runtime data and persistent browser profile protected. | Directory permissions restricted (`0700` on Unix; user-only ACL on Windows). |
| **Operator Safety** | Prevent accidental duplicate external message delivery. | Mandatory reason argument + exact string confirmation `CONFIRM-NOT-DELIVERED` on override. |
| **Zero Anti-Ban / Zero Evasion** | Strict compliance with platform terms; no adversarial evasion. | **Zero CAPTCHA bypass, zero fingerprint spoofing, zero canvas noise, zero account rotation.** |

---

## 17. Database Impact

> [!IMPORTANT]
> **VERDICT: NO DATABASE SCHEMA CHANGES REQUIRED.**

### Technical Justification
Phase 6 requirements were thoroughly evaluated against the existing schema:
1. **Campaign Analytics**: Derived completely from `campaign_contacts`, `campaigns`, and `messages`.
2. **Queue Analytics**: Derived completely from `messages` (statuses, attempt counts, timestamps, error types).
3. **Runner Analytics**: Active state tracked in kernel lock file (`data/runner.lock`) and `app_settings` (`system:active_runner`); history tracked in `audit_logs`.
4. **Provider Analytics**: Derived from `audit_logs` (event types `MESSAGE_SENT`, `MESSAGE_FAILED`, `PROVIDER_HEALTH_*`) and runtime session state.
5. **Operational State**: `EmergencyStop` already persists to `app_settings: emergency_stop`. Circuit breaker errors track in-memory per campaign and persist to `audit_logs`.
6. **Zero Migration Needed**: No new tables, columns, indexes, or constraints are added. The database structure remains 100% stable.

---

## 18. Dependency Impact

> [!IMPORTANT]
> **VERDICT: ZERO NEW EXTERNAL DEPENDENCIES.**

### Technical Justification
All Phase 6 requirements are implemented using existing standard libraries and established project packages:
- **Logging & Rotation**: Python standard library `logging`, `logging.handlers.RotatingFileHandler`.
- **Structured JSON**: Python standard library `json`.
- **Redaction & Regex**: Python standard library `re`.
- **System & Runtime Inspection**: Python standard library `os`, `sys`, `platform`, `pathlib`, `shutil`.
- **Time & Scheduling**: Python standard library `time`, `datetime`.
- **Database Aggregations**: Existing `sqlalchemy` ORM and expression functions (`func.count`, `func.avg`).
- **Configuration**: Existing `pydantic-settings`.
- **Testing**: Existing `pytest`, `pytest-cov`, `hypothesis`.

---

## 19. Testing Strategy

### 19.1 Test Categories & Scope

1. **Unit & Security Tests (`tests/test_logging.py`, `tests/test_redaction.py`)**:
   - **Dedicated Child Logger Security Test**: Emits sensitive values (API keys, bearer tokens, passwords, cookies, raw Egyptian E.164 numbers, raw international numbers) through deeply nested child loggers (e.g. `logging.getLogger("app.runner.worker.sub")`). Inspects actual persisted bytes in BOTH `logs/app.log` and `logs/app.json.log` and asserts that neither file contains any original sensitive values.
   - **Dual Handler Sanitization Invariant Test**: Verifies that both `logs/app.log` and `logs/app.json.log` independently guarantee sanitization and that formatters do not mutate shared `LogRecord` instances destructively.
   - **E.164 & Egyptian Phone Masking Test**: Verifies deterministic masking across Egyptian mobile numbers (`+201012345678` $\to$ `+2010******78`), formatted numbers (`+20 101 234 5678`, `(010) 1234-5678`), international numbers (`+14155552671`, `+447911123456`, `+966501234567`), and already-masked values (idempotence check).
   - **Message Body Exclusion Test**: Emits message dispatch events and verifies outbound rendered content is omitted by default from both logs.
   - **JSON Schema Contract Test**: Validates that all structured lines in `logs/app.json.log` strictly parse against the JSON schema contract.
   - **Log Rotation Sizing Test**: Verifies exact byte cutoff at `10,485,760 bytes` (10 MB) and 10 rotated backups.
2. **Unit Tests (`tests/test_preflight.py`, `tests/test_health_model.py`)**:
   - Preflight validator correctly identifies missing directories, invalid configurations, database disconnects, and lock conflicts.
   - Runner startup lock ordering test: verifies OS process lock is acquired before preflight checks, and released cleanly if preflight fails.
   - Runtime health model accurately computes `HEALTHY`, `DEGRADED`, `UNHEALTHY`, and `STOPPED` based on heartbeat and error metrics.
3. **Integration Tests (`tests/test_analytics_service.py`, `tests/test_cli_analytics.py`)**:
   - **Confirmed Send Rate Formula Test**: Verifies mathematical correctness:
     `confirmed_sends / (confirmed_sends + failed + unknown_outcome) * 100`.
   - **Denominator Exclusion Test**: Asserts that `RETRY_PENDING`, `SKIPPED`, and `CANCELLED` are strictly excluded from the `confirmed_send_rate` denominator.
   - **Unknown Outcome Penalization Test**: Asserts that `UNKNOWN_OUTCOME` is counted in the denominator and excluded from `confirmed_sends`.
   - Queue analytics verify accurate depth, throughput, and stale lease counts.
   - Runner analytics verify correct uptime and heartbeat calculations.
   - CLI commands (`outreach analytics campaign`, `queue`, `runner`, `provider`, `system`, `outreach preflight`) return expected exit codes (0–10) and formatted cards/tables.
   - `--json` flag output validates against JSON schema.
4. **Failure & Incident Simulation Tests (`tests/test_operational_failure.py`)**:
   - Runner crash simulation (dead PID) verifies stale metadata cleanup and lease recovery.
   - Browser disconnect simulation verifies health check failure detection.
   - Stale lease recovery simulation verifies expired leases reset to `RETRY_PENDING`.
   - Circuit breaker trip simulation verifies alerting state.
   - Storage failure simulation (unwritable directory / disk full) verifies non-crashing daemon behavior.
5. **Regression Verification**:
   - Full test suite execution across Phases 1–5 (all 167 tests must pass).
   - No live browser required for automated regression runs.

---

## 20. Documentation Plan

Upon Phase 6 approval and implementation, the following documentation will be updated or created:
1. **`PROJECT_CONTEXT.md`**: Update status to `Phase 6: IMPLEMENTED & APPROVED`, add logging and analytics architecture.
2. **`README.md`**: Add operator guide for `outreach analytics` and `outreach preflight`.
3. **`CHANGELOG.md`**: Record Phase 6 additions under `[Phase 6]`.
4. **`walkthrough.md`**: Update walkthrough artifact with verification metrics and CLI examples.
5. **`PRODUCTION_RUNBOOK.md` (NEW)**: Complete standalone operations manual for deployment, incident recovery, and disaster recovery.

---

## 21. Out of Scope

The following features are explicitly **excluded** from Phase 6:
- **Multi-campaign concurrent runner execution**: Phase 6 maintains single-runner / single-campaign exclusivity.
- **Changing WhatsApp provider architecture**: Selenium and `WhatsAppWebProvider` remain authoritative.
- **Replacing Selenium**: No Puppeteer, Playwright, or unofficial WhatsApp API integration.
- **Anti-ban, stealth automation, CAPTCHA bypass, canvas spoofing, or account rotation**: Strictly prohibited.
- **External notification webhooks**: Direct Slack/PagerDuty/Email API integrations are deferred.
- **Phase 7 packaging**: Systemd services, Docker containers, and binary distribution belong to Phase 7.

---

## 22. Acceptance Criteria

Phase 6 implementation will be accepted when:
1. **Child Logger Sensitive-Data Redaction**: Sensitive data emitted through child loggers (e.g. `app.runner`, `app.providers.whatsapp_web`) is guaranteed to be intercepted and redacted; propagation to root handlers cannot bypass sanitization.
2. **Both Persistent Destinations Receive Only Sanitized Records**: Both `logs/app.log` and `logs/app.json.log` independently guarantee sanitization without destructively mutating shared `LogRecord` instances in place.
3. **Egyptian & International E.164 Phone Masking**: Phone masking handles Egyptian numbers (`+201012345678` $\to$ `+2010******78`), international E.164 numbers (`+1...`, `+44...`, `+966...`), common formatting characters (spaces, hyphens, dots, parentheses), and already-masked values idempotently.
4. **Strict Prohibition of Message Body Logging**: Outbound rendered message content is strictly omitted from both plaintext and JSON logs by default; dispatches are tracked by `message_id` and character length only.
5. **Deterministic Structured JSON Schema**: `logs/app.json.log` strictly conforms to the documented JSON schema contract with all required metadata fields (`timestamp`, `level`, `event`, `component`, `message`, `correlation_id`, `campaign_id`, `runner_id`, `operation`, `result`, `duration_ms`).
6. **Log Rotation Byte Sizing & Storage Failure Resilience**: Files rotate at exactly `10,485,760 bytes` (10 MB) with 10 rotated backups (`104,857,600 bytes` max per stream). Storage errors (`ENOSPC`, unwritable directories, Windows file rollover lock contention) fall back to `stderr` and **MUST NOT crash the production runner daemon loop**.
7. **Confirmed Send Rate Formula Correctness**: Confirmed send rate is strictly computed as:
   $$\text{confirmed\_send\_rate} = \frac{\text{confirmed\_sends}}{\text{confirmed\_sends} + \text{failed} + \text{unknown\_outcome}} \times 100$$
8. **`RETRY_PENDING` Excluded from Confirmed Send Rate Denominator**: Transient pending retries are strictly excluded from the denominator as they have not reached a terminal outcome.
9. **`UNKNOWN_OUTCOME` Excluded from Confirmed Sends**: Ambiguous send outcomes are counted in the denominator and excluded from confirmed sends, penalizing the metric until investigated.
10. **No Delivery, Read, or Received Claims**: The metric is strictly named **CONFIRMED SEND RATE** and explicitly documented as reflecting Phase 4 provider UI confirmation checkmarks, with zero claim of external device delivery, read receipt, or exactly-once delivery.
11. **Authoritative OS Lock Order Preceding Preflight**: `outreach runner start` acquires the authoritative kernel OS process lock (`data/runner.lock`) **FIRST** before executing preflight and runtime readiness checks. If another live runner holds the lock, it terminates immediately with `ExitCode.CONCURRENCY_ERROR (9)`. If preflight fails after lock acquisition, the OS lock is cleanly released before process exit.
12. **Informational Database Heartbeat**: The database heartbeat (`app_settings: system:active_runner`) remains strictly informational for visibility, stale detection, and recovery. The OS file lock remains the sole authoritative lock on the host.
13. **Standardized Event Taxonomy**: Operational event taxonomy covers all 30+ standardized operational milestones across subsystems.
14. **On-Demand SQL Analytics (Zero Schema Changes)**: Campaign, queue, runner, provider, and system metrics are computed on-demand via SQL aggregations with **ZERO database schema modifications or migrations**.
15. **Analytics CLI Subcommands**: `outreach analytics <campaign|queue|runner|provider|system>` subcommands are fully implemented with ASCII cards/tables, semantic notes, and valid `--json` output.
16. **Comprehensive Preflight Diagnostics**: `outreach preflight` validates Python runtime, config, DB, directories, browser environment, session files, and locks with deterministic exit codes (0–10).
17. **Runtime Health State Machine**: Runtime health model accurately computes `HEALTHY`, `DEGRADED`, `UNHEALTHY`, and `STOPPED` states.
18. **Operational Alert Boundaries**: Alert conditions clearly separate `WARNING` from `CRITICAL` without introducing external notification dependencies.
19. **Deployment & Operational Runbooks**: Standardized deployment layout, production `.env` defaults, backup/restore procedures, and step-by-step incident recovery runbooks are documented.
20. **Preservation of Phase 5 Separation & Safety**: `outreach campaign run` remains strictly a business state transition; `outreach runner start` remains the exclusive worker entrypoint. Single-runner / single-campaign exclusivity is strictly preserved. In-flight sends complete to a safe cancellation/confirmation point without forceful termination.
21. **Zero Anti-Ban & Compliance**: Zero CAPTCHA bypass, zero fingerprint spoofing, zero canvas noise, and zero account rotation.
22. **Zero New External Dependencies**: All Phase 6 functionality is achieved using Python standard libraries and existing packages.
23. **Testing & Coverage**: Unit, integration, failure simulation, child logger redaction, and Egyptian E.164 phone masking tests achieve **$\ge 90\%$ test coverage on new Phase 6 code**, with **all 167 Phase 1–5 regression tests passing with 0 failures**.
24. **Standalone Phase 6 E2E Verification**: Standalone verification script executes and confirms all logging, analytics, preflight, and health checks end-to-end.

---

## 23. Implementation Order

When approved, Phase 6 implementation should proceed in this sequence:

```
  Stage 1: Logging Foundation & Redaction
           ├── app/utils/logger.py (RedactingFilter, JSONFormatter, RotatingFileHandler)
           └── tests/test_logging.py, tests/test_redaction.py
                   ↓
  Stage 2: Operational Event Taxonomy
           ├── app/utils/events.py (Event definitions and emitter helper)
           └── Integration into runner, queue worker, session manager
                   ↓
  Stage 3: Analytics Service Layer
           ├── app/services/analytics_service.py (SQL aggregation queries)
           └── tests/test_analytics_service.py
                   ↓
  Stage 4: Analytics CLI Commands
           ├── app/cli/commands/analytics.py
           ├── app/cli/main.py (Router registration)
           └── tests/test_cli_analytics.py
                   ↓
  Stage 5: Preflight Validation & Health Model
           ├── app/readiness/preflight.py
           ├── app/readiness/health.py
           ├── app/cli/commands/preflight.py & health.py
           └── tests/test_preflight.py, tests/test_health.py
                   ↓
  Stage 6: Operations & Disaster Recovery Documentation
           ├── PRODUCTION_RUNBOOK.md
           └── Documentation updates (PROJECT_CONTEXT, README, CHANGELOG)
                   ↓
  Stage 7: E2E Verification & Test Suite Validation
           ├── app/cli/verify_phase6_e2e.py
           └── Execute full test suite + coverage verification (≥90%)
```

---

## 24. Risks & Mitigations

| Identified Risk | Severity | Technical Mitigation Strategy |
|---|---|---|
| **Log Volume / Disk Exhaustion** | `Medium` | Capped at 10 MB per file with 10 rotating backups (100 MB max). Debug logs disabled by default in production. |
| **Analytics Query Latency on Large Queues** | `Low` | All queries leverage existing indexed columns (`status`, `campaign_id`, `created_at`, `locked_at`). Aggregations use `group_by` and `count()` without fetching table rows. |
| **Accidental Secret or Phone Leakage** | `Medium` | Centralized `RedactingFilter` intercepts log records at the root handler level before write, scrubbing text via regex regardless of which module logged it. |
| **In-Flight Dispatch Corruption on Shutdown** | `Medium` | `SignalCoordinator` and `ProductionRunner` ensure the runner reaches a safe cancellation point before terminating browser handles. |
| **Orphaned Process Lock After Crash** | `Low` | OS kernel releases file locks automatically; `ProcessLock` inspects PID liveliness (`is_pid_alive()`) to clean up stale metadata on subsequent startup. |

---

## 25. Open Questions & Design Decisions for Review

1. **Structured Log Destination**:
   - *Design Proposal*: Emits plaintext logs to `logs/app.log` (for operator terminal/tail viewing) and structured JSON lines to `logs/app.json.log` (for log shippers).
   - *Alternative*: Single log file with JSON only, or single file with plaintext only.
   - *Recommendation*: Approve dual-file strategy with root `RedactingFilter` and single emission guarantee.

2. **Preflight Enforcement and Lock Order on Runner Start**:
   - *Design Decision*: `outreach runner start` executes basic argument validation, acquires the authoritative OS process lock (`data/runner.lock`) **FIRST**, and then executes preflight checks. If another runner holds the lock, it terminates immediately with `CONCURRENCY_ERROR (9)`. If preflight fails, the OS lock is cleanly released before exiting. DB heartbeat remains strictly informational.
   - *Status*: Approved in design review.

3. **Analytics Command Hierarchy**:
   - *Design Proposal*: Group all analytics under `outreach analytics <campaign|queue|runner|provider|system>`. Add `outreach system health` and `outreach preflight` as top-level operational commands alongside `outreach emergency-stop`.
   - *Recommendation*: Approve command hierarchy.
