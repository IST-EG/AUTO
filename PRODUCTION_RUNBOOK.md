# Production Runbook & Operational Procedures
**WhatsApp Outreach Automation System**
**Governance Phase**: Phase 6 — Production Readiness & Monitoring

---

## 1. Quick Reference: Operational Command Matrix

| Task / Objective | Command | Notes |
| :--- | :--- | :--- |
| **Preflight Inspection** | `outreach preflight [--campaign-id <id>] [--strict] [--json]` | Verifies 10 environmental and runtime vectors |
| **System Health Check** | `outreach system health [--json]` | Evaluates live state: HEALTHY, DEGRADED, UNHEALTHY, STOPPED |
| **Executive Dashboard** | `outreach analytics system [--json]` | Backlog, runner state, daily quota utilization, campaign totals |
| **Session Authentication** | `outreach session login [--headless]` | Launches Chrome for operator WhatsApp Web QR scan |
| **Session Status** | `outreach session status` | Checks persistent profile directory and authentication data |
| **Campaign State Transition** | `outreach campaign run <campaign_id>` | Transitions campaign from DRAFT/SCHEDULED to RUNNING |
| **Start Production Runner** | `outreach runner start --campaign-id <id>` | Starts single-campaign runner daemon under OS process lock |
| **Runner Process Status** | `outreach runner status` | Shows PID, liveliness, heartbeat age, and lease state |
| **Stop Runner Daemon** | `outreach runner stop` | Sends SIGINT / graceful shutdown signal to runner daemon |
| **Emergency Stop (Killswitch)**| `outreach emergency-stop [--reason <text>]` | Blocks all NEW message claims across the entire system |
| **Emergency Stop Status** | `outreach emergency-status` | Displays whether global killswitch is ACTIVE or INACTIVE |
| **Emergency Resume** | `outreach emergency-resume [--reason <text>]` | Resumes queue dispatches after incident resolution |
| **Queue Backlog & Leases** | `outreach analytics queue [--campaign-id <id>] [--json]` | Backlog depth, processing leases, and throughput |
| **Inspect Message Detail** | `outreach queue inspect <message_id>` | Detailed record, recipient, errors, and attempt count |
| **Reconcile Stale Leases** | `outreach queue reconcile [--campaign-id <id>]` | Resets abandoned leases (>300s) to QUEUED |
| **Manual Unknown Override** | `outreach queue override <id> --reason "<verified reason>"` | Reconciles UNKNOWN_OUTCOME after manual WhatsApp inspection |

---

## 2. Standard Production Startup Procedure

Follow this exact 5-step sequence when launching production outreach:

### Step 1: Run Production Preflight Inspection
```bash
python -m app.cli.main preflight
```
*Expected Output*: `[SUCCESS] Preflight inspection PASSED. System is ready for production runner execution.`  
If any check fails, do NOT proceed. Refer to Section 6 (Incident Playbooks) to resolve preflight failures.

### Step 2: Verify WhatsApp Web Session Authentication
```bash
python -m app.cli.main session status
```
If persistent session data is missing or expired, log in interactively:
```bash
python -m app.cli.main session login
```
Scan the displayed QR code in WhatsApp on your mobile device until authentication confirms.

### Step 3: Verify Subsystem Operational Health
```bash
python -m app.cli.main system health
```
Ensure the output reports `STOPPED` (or `HEALTHY`) with `Database Responsive: YES` and `Emergency Stop: INACTIVE`.

### Step 4: Transition Target Campaign to RUNNING
```bash
python -m app.cli.main campaign run <campaign_id>
```
*Note*: This is a business state transition only. It does not start worker threads or processes.

### Step 5: Start Single-Campaign Production Runner Daemon
```bash
python -m app.cli.main runner start --campaign-id <campaign_id>
```
*Startup Sequence*:
1. CLI argument validation
2. Authoritative OS process lock (`data/runner.lock`) acquisition. If held by another process, exits immediately with code `9`.
3. Preflight readiness check. If failed, releases lock and exits cleanly.
4. Initializes `WhatsAppWebProvider` and verifies connection.
5. Recovers any stale message leases from previous runs.
6. Enters persistent polling loop until campaign messages are depleted or SIGINT/SIGTERM is received.

---

## 3. Monitoring & Analytics Procedures

### Real-Time Health Monitoring
Check overall system state every 15–30 minutes:
```bash
python -m app.cli.main system health
```
- **HEALTHY**: Runner is actively dispatching, heartbeat is fresh (<30s), queue has zero stale leases.
- **DEGRADED**: Runner heartbeat is lagging (30–60s), or unreconciled `UNKNOWN_OUTCOME` messages exist.
- **UNHEALTHY**: Database is unresponsive, runner heartbeat is dead (>60s), or stale leases detected.
- **STOPPED**: Emergency stop is engaged, or no runner is currently active.

### Live Executive Metrics
```bash
python -m app.cli.main analytics system --json
```
Monitors:
- `daily_quota.sent_today` vs `daily_quota.global_daily_limit` (capacity remaining)
- `queue_summary.depth` (unprocessed queue backlog)
- `active_runner.uptime_seconds` and `active_runner.heartbeat_age_seconds`

### Campaign Performance & Confirmed Send Rate
```bash
python -m app.cli.main analytics campaign <campaign_id>
```
**Authoritative Confirmed Send Rate Formula**:
$$\text{Confirmed Send Rate} = \frac{\text{Confirmed Sends}}{\text{Confirmed Sends} + \text{Failed} + \text{Unknown Outcome}} \times 100$$
- **Terminology**: Measures internal send confirmation evidenced by WhatsApp Web UI checkmarks. Never describe as "delivered rate" or "read rate".
- **Penalization**: `UNKNOWN_OUTCOME` messages are penalized in the denominator.
- **Exclusions**: `RETRY_PENDING`, `SKIPPED`, and `CANCELLED` messages are strictly excluded from the denominator.

---

## 4. Operational Controls & Interventions

### Graceful Runner Pause & Resume
To pause campaign dispatches temporarily:
```bash
python -m app.cli.main campaign pause <campaign_id>
```
To resume dispatches:
```bash
python -m app.cli.main campaign resume <campaign_id>
```

### Graceful Runner Shutdown
To signal the active runner process to stop after completing its current message:
```bash
python -m app.cli.main runner stop
```
The runner finishes its in-flight send, closes the browser cleanly, releases `data/runner.lock`, and exits with code `0`.

### Global Emergency Stop (Killswitch)
If an abnormal event occurs (e.g. WhatsApp ban threat, message template corruption, runaway loop):
```bash
python -m app.cli.main emergency-stop --reason "Abnormal UI response detected"
```
**Guarantees & Semantics**:
- Stop signal propagates to workers and prevents NEW message claims (<500ms target).
- Never forcefully terminates an in-flight browser send to prevent socket corruption.
- In-flight operations reach a safe cancellation/confirmation point.
- Ambiguous outcomes are tagged as `UNKNOWN_OUTCOME` and never blind-retried.

To resume operations after review:
```bash
python -m app.cli.main emergency-resume --reason "Operator reviewed and cleared"
```

### Manual Reconciliation of UNKNOWN_OUTCOME
If a network disconnect, browser crash, or emergency stop interrupts an in-flight send:
```bash
# 1. Identify affected messages
python -m app.cli.main queue status --campaign-id <campaign_id>

# 2. Inspect message metadata and recipient
python -m app.cli.main queue inspect <message_id>

# 3. Physically check WhatsApp Web chat history on the phone or browser:
#    - If message WAS received by contact: DO NOT OVERRIDE.
#    - If message WAS NOT received:
python -m app.cli.main queue override <message_id> --reason "Verified recipient chat history on mobile; message was not delivered."
```
*Note*: The operator will be prompted to type `CONFIRM-NOT-DELIVERED`. The action creates an immutable `AuditLog` entry.

---

## 5. Logging, Privacy & Retention Architecture

### Dual Log Files
The system writes two synchronized, non-overlapping log streams:
1. `logs/app.log`: Formatted plaintext with ISO-8601 timestamps, component names, log levels, and messages.
2. `logs/app.json.log`: Structured JSON Lines format for log ingestion pipelines, SIEM, or operational alerting.

### Data Privacy & Redaction Invariants
- **Phone Number Masking**: All phone numbers in log messages, extra fields, and CLI displays are masked according to country format:
  - Egyptian mobiles (`+201012345678` $\to$ `+2010******78`, `01012345678` $\to$ `010******78`)
  - International numbers (`+15551234567` $\to$ `+155*****67`)
- **Credential Scrubbing**: Passwords, API tokens, cookies, secrets, and JWT tokens are unconditionally replaced with `[REDACTED]`.
- **Zero Message Body Logging**: Message body contents (`rendered_content`, `body`, `template`) are omitted from log events by default.
- **Handler-Safe Redaction**: Redaction formatters execute independently inside each persistent file handler, ensuring propagated records from child loggers cannot bypass sanitization.

### Log Rotation Specifications
- **Max File Size**: Exactly `10,485,760 bytes` (10 MB).
- **Backup Count**: `10` rotated backup archives (`app.log.1` ... `app.log.10`).
- **Resilience**: `SafeRotatingFileHandler` prevents process crashes on Windows file locking collisions (`PermissionError`) or disk exhaustion (`ENOSPC`).

---

## 6. Incident Response Playbooks

### Playbook A: Preflight Check Fails on Chrome Binary
- **Symptom**: `Browser Environment: [FAIL] Google Chrome binary not found.`
- **Remedy**:
  1. Verify Google Chrome is installed on the host.
  2. If Chrome is in a non-standard path, add to `.env`:
     ```env
     WHATSAPP_CHROME_BINARY="C:\Path\To\Google\Chrome\Application\chrome.exe"
     ```
  3. Re-run `outreach preflight`.

### Playbook B: Duplicate Runner Concurrency Blocked
- **Symptom**: `ExitCode 9: Cannot start runner: another production runner process is already active.`
- **Remedy**:
  1. Run `outreach runner status` to inspect the active PID.
  2. If the PID is alive, wait for it to complete or stop it via `outreach runner stop`.
  3. If the host crashed and left a stale lock file:
     ```bash
     python -m app.cli.main queue reconcile
     ```
     `ProcessLock` verifies PID liveness automatically; if the PID is dead, the stale lock will be cleanly reclaimed upon the next `runner start`.

### Playbook C: WhatsApp Web Session Logged Out / QR Expired
- **Symptom**: `ExitCode 5: WhatsApp Web is not authenticated. Please run 'outreach session login' first.`
- **Remedy**:
  1. Run `outreach session login` in non-headless mode.
  2. Scan the QR code on the physical mobile device.
  3. Verify via `outreach session status`.

### Playbook D: Stale Worker Leases Detected
- **Symptom**: System health reports `DEGRADED` or `UNHEALTHY` due to stale leases (>300s).
- **Remedy**:
  ```bash
  python -m app.cli.main queue reconcile
  ```
  Stale leases are reset to `QUEUED` and re-evaluated by the queue worker.

---

## 7. Deterministic CLI Exit Code Reference

| Exit Code | Name | Meaning / Resolution |
| :---: | :--- | :--- |
| **0** | `SUCCESS` | Normal successful completion |
| **1** | `GENERAL_ERROR` | Unhandled system exception or runtime failure |
| **2** | `INVALID_ARGUMENT` | Missing or invalid command line flags or inputs |
| **3** | `NOT_FOUND` | Specified Campaign or Message ID was not found |
| **4** | `INVALID_STATE` | Target resource is in an invalid lifecycle state (e.g. Campaign not RUNNING) |
| **5** | `AUTHENTICATION_REQUIRED` | WhatsApp Web session is missing or unauthenticated |
| **6** | `PROVIDER_UNAVAILABLE` | WhatsApp browser could not start or connection was refused |
| **7** | `EMERGENCY_STOP_ACTIVE` | Operation blocked because global Emergency Stop killswitch is engaged |
| **8** | `CIRCUIT_BREAKER_OPEN` | Campaign is PAUSED due to consecutive errors exceeding threshold |
| **9** | `CONCURRENCY_ERROR` | Process lock conflict: another production runner is active on this host |
| **10** | `UNKNOWN_OUTCOME_BLOCKED` | Attempt to retry or process an ambiguous dispatch without manual override |
