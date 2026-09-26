# Phase 7.7-B Step 7: Worker Control Plane Startup Correction Design Report (Revised)
## Decoupling Always-On Oracle Worker Service from Campaign Execution

**Document Status**: `REVISED_DESIGN_PROPOSAL_AWAITING_APPROVAL`  
**Phase**: 7.7-B Step 7 (Startup Correction)  
**Date**: September 26, 2026  
**Scope**: Architectural Correction, Worker Service Decoupling, Health Semantics Clarification, Lock Isolation, Test Matrix, Rollback Plan, and Deployment Verification  

---

## 1. Executive Summary & Review Dispositions

This revised design report addresses the pre-commit architecture review findings for the Phase 7.7-B Step 7 Worker Control Plane startup correction.

### Production Baseline Finding (Oracle ARM64 Worker `84.13.139.20`)
- **OS**: Ubuntu 24.04.5 LTS ARM64 (`aarch64`)
- **Worker Instance ID**: `oracle-arm64-worker-01` (configured in `/opt/whatsapp-outreach/.env`, validated)
- **Strict Preflight**: 11 checks **PASSED**
- **Google Chrome for Testing ARM64 + ChromeDriver**: **AVAILABLE** (`/opt/google/chrome-for-testing/chrome`)
- **Xvfb `:99` Display**: **ACTIVE**
- **Persistent WhatsApp Session Profile**: **PRESENT** (`/opt/whatsapp-outreach/data/whatsapp_session`)
- **Database Connectivity**: **HEALTHY** (Supabase PostgreSQL)
- **Production Queue**: **EMPTY** (all queue statuses = 0)
- **Campaign 1**: **`DRAFT`** (must remain in `DRAFT` state)
- **outreach-runner.service**: **INACTIVE / STOPPED**

### Explicit Review Directives Incorporated in this Revision
1. **Strict Prohibition on Implicit Campaign Transitions**: `WorkerDaemon` must **NOT** implicitly transition campaigns from `DRAFT`, `SCHEDULED`, `PAUSED`, `COMPLETED`, or `CANCELLED` to `RUNNING`. Campaign 1 remains `DRAFT`.
2. **Definitive Scope of "Campaign Supervisor"**: Strictly defined as **orchestration/observation only** unless a campaign is already explicitly `RUNNING` through the existing campaign business lifecycle. The worker must **never** auto-start Campaign 1 merely because the worker daemon is running.
3. **Decoupled Session Health Semantics**: Storage profile presence (`profile_present = true`) is strictly decoupled from live session authentication (`session_authenticated = true`). When Chrome is not running, session status is reported as `DISCONNECTED` / `UNKNOWN`, never falsely reported as an authenticated live WhatsApp session.
4. **Distinction Between Executable Availability & Runtime Process State**: Binary presence (`chrome_reachable = true`) is separated from process execution (`browser_state = DISCONNECTED`). A quiescent standby state (`WorkerDaemon` active, `Xvfb` active, `Chrome` stopped, `Runner` standby, `Campaign 1` DRAFT, `Queue` 0) is formalized as a **valid healthy standby state**, not an error.
5. **Independent Process Locks**: `WORKER_LOCK_FILE` (`data/worker.lock`) protects `WorkerDaemon` singularity only. The existing `RUNNER_LOCK_FILE` (`data/runner.lock`) remains authoritative for `ProductionRunner`. The locks are **not** collapsed.
6. **Graceful Worker Stop Semantics**: `outreach worker stop` signals `WorkerDaemon` via SIGTERM and never force-kills, `os.kill`s, or abruptly terminates `ProductionRunner` or Chrome.
7. **Expanded Safety Test Matrix**: 14 explicit tests added verifying zero Chrome cold-starts, zero WhatsApp Web launches, zero QR flows, zero sent messages, no implicit campaign state mutations, and lock independence.
8. **Unchanged Command Protocol**: Full preservation of `REQUESTED` $\to$ `CLAIMED` $\to$ `EXECUTING` $\to$ `COMPLETED` / `FAILED`, atomic CAS, 60s/120s leases, orphan recovery, and RBAC. `PREFLIGHT` remains strictly read-only.
9. **Clean Systemd Startup**: Service runs `outreach worker start` with zero `--campaign-id` references.

---

## 2. Root Cause Analysis

### 2.1 The Tight Coupling Defect
In Phase 7.6 and early Step 7, operational WhatsApp commands and worker heartbeat mechanisms were integrated by instantiating `WhatsAppCommandHandler` inside `ProductionRunner`:
```python
# app/runner/production_runner.py
class ProductionRunner:
    def __init__(self, db, campaign_id, ...):
        ...
        self.whatsapp_command_handler = WhatsAppCommandHandler(db=db, worker_id=self.worker_id, provider=self.provider)

    def start(self, ...):
        ...
        # Step 4: Campaign check
        campaign = self.db.query(Campaign).filter(Campaign.id == self.campaign_id).first()
        if campaign.status != "RUNNING":
            return ExitCode.INVALID_STATE  # <--- ABORTS HERE BEFORE LOOP

        # Telemetry & Command polling loop only exists inside the runner loop:
        while not self.signals.shutdown_requested:
            self.whatsapp_command_handler.publish_telemetry()
            self.whatsapp_command_handler.poll_and_execute()
            ...
```

### 2.2 Systemd Configuration Failure
The systemd service unit `deploy/systemd/outreach-runner.service` was configured with:
```ini
ExecStart=/opt/whatsapp-outreach/app/.venv/bin/python -m app.cli.main runner start --campaign-id 1
Restart=on-failure
RestartSec=10
```
This configuration conflated **host-level worker provisioning** with **single-campaign message execution**:
- A worker host is an always-on infrastructure node.
- A campaign runner is a transient business dispatch worker that executes only when a specific campaign is in the `RUNNING` state.
- Because Campaign 1 is in `DRAFT` state, `ProductionRunner` aborted with exit code 5 (`INVALID_STATE`). Systemd treated this as a failure and restarted the process every 10 seconds, creating an infinite crash loop.
- `WhatsAppCommandHandler` was never invoked: no heartbeat was published, no worker identity was written, and no control-plane commands could be polled or executed.

---

## 3. Current vs. Corrected Architecture

### 3.1 Current Architecture (Coupled & Fragile)
```
systemd (outreach-runner.service)
  │
  ▼
`outreach runner start --campaign-id 1`
  │
  ▼
ProductionRunner(campaign_id=1)
  ├── Step 4: Checks campaign.status == "RUNNING"
  │     └── If DRAFT ──► EXIT CODE 5 (Fatal Crash Loop via systemd Restart)
  │
  └── [UNREACHABLE WHEN CAMPAIGN IS DRAFT]
        ├── Cold-start Chrome & launch WhatsApp Web
        ├── WhatsAppCommandHandler.publish_identity()
        ├── WhatsAppCommandHandler.publish_telemetry()
        ├── WhatsAppCommandHandler.poll_and_execute()
        └── QueueWorker.process_next_message()
```

### 3.2 Corrected Architecture (Decoupled & Resilient)
```
systemd (outreach-runner.service)
  │
  ▼
`outreach worker start`
  │
  ▼
WorkerDaemon (Always-On Host & Control Plane Daemon)
  │
  ├── 1. HOST SINGULARITY LOCK: data/worker.lock (Independent from runner.lock)
  ├── 2. WORKER IDENTITY: Reads & validates WORKER_INSTANCE_ID -> system:worker_identity
  ├── 3. ORPHAN RECOVERY: WhatsAppCommandHandler.recover_orphans()
  │
  ├── 4. ALWAYS-ON TICK LOOP (every 1s):
  │     │
  │     ├── [Every 15s] Worker Heartbeat -> system:worker_heartbeat
  │     │     ├── Uptime, Xvfb status, Chrome/ChromeDriver binary presence
  │     │     ├── Profile storage: PRESENT / MISSING
  │     │     └── Runner State: "STANDBY" (idle) / "RUNNING" (active)
  │     │
  │     ├── [Every 5s] Command Polling -> system:whatsapp_command:active
  │     │     ├── Atomic CAS claiming (60s lease standard, 120s PREFLIGHT)
  │     │     ├── PREFLIGHT: Read-only inspection (NO Chrome cold-start, NO messages)
  │     │     └── Operational: HEALTH_CHECK, RECONNECT, DISCONNECT, LOGOUT
  │     │
  │     └── [Campaign Supervision / Observation] Checks system:desired_runner_state
  │           ├── If STOPPED ──► Runner remains STANDBY
  │           └── If RUNNING ──► Inspects target campaign in database:
  │                 ├── If DRAFT / PAUSED / COMPLETED:
  │                 │     ├── Logs warning / audits non-engagement
  │                 │     ├── DOES NOT CHANGE CAMPAIGN STATUS
  │                 │     ├── DOES NOT SPAWN RUNNER
  │                 │     └── WorkerDaemon STAYS ALIVE & HEALTHY
  │                 └── If RUNNING:
  │                       └── Supervises ProductionRunner execution
  │
  └── 5. CLEAN SIGNAL SHUTDOWN: Intercepts SIGINT/SIGTERM -> Releases worker.lock
```

---

## 4. WhatsApp Session & Infrastructure Health Semantics

### 4.1 Independent Dimensions of WhatsApp Health
To prevent misleading reporting, the health model strictly decouples profile storage, browser process runtime, and session authentication:

```
┌─────────────────────────┐     ┌─────────────────────────┐     ┌─────────────────────────┐
│     Profile Storage     │     │     Browser Runtime     │     │  Session Authentication │
├─────────────────────────┤     ├─────────────────────────┤     ├─────────────────────────┤
│ PRESENT: Files on disk  │     │ CONNECTED: Process up   │     │ AUTHENTICATED: UI ready │
│ MISSING: No directory   │     │ DISCONNECTED: Proc dead │     │ UNAUTHENTICATED: QR req │
│ EMPTY: Directory empty  │     │                         │     │ UNKNOWN: Browser dead   │
└─────────────────────────┘     └─────────────────────────┘     └─────────────────────────┘
```

**Semantic Invariants:**
1. `profile_storage_state == "PRESENT"` indicates only that cache files exist in `/opt/whatsapp-outreach/data/whatsapp_session`. It does **not** imply that WhatsApp has authenticated the session or that the session is still valid.
2. `browser_state == "DISCONNECTED"` indicates no Chrome process is active.
3. When `browser_state == "DISCONNECTED"`, the session authentication state **cannot** be verified and is reported as `session_authenticated = false` and `session_state = "UNKNOWN"` (or `"DISCONNECTED"`). **A persistent profile existing while Chrome is stopped is NEVER reported as an authenticated live WhatsApp session.**
4. When `browser_state == "CONNECTED"`, `session_authenticated` is `true` only if WhatsApp Web has loaded the chat list/interface (`CONNECTED`), and `false` if it is awaiting QR scan (`AUTHENTICATING`).

### 4.2 Executable Availability vs. Process Runtime State
We explicitly distinguish static tool availability from live execution:
- `chrome_reachable: true`: Google Chrome for Testing binary is present and executable at configured path (`/opt/google/chrome-for-testing/chrome`).
- `chromedriver_reachable: true`: ChromeDriver binary is present and executable (`/opt/google/chrome-for-testing/chromedriver`).
- `xvfb_healthy: true`: Virtual X display `:99` responds to `xdpyinfo`.

### 4.3 Definition of the "Healthy Standby" State
When no campaign is active, the system is in **Healthy Standby**:
- `WorkerDaemon`: **ACTIVE** (heartbeat age < 15s)
- `Xvfb`: **ACTIVE** (`:99`)
- `Chrome`: **NOT RUNNING** (0 processes)
- `ProductionRunner`: **STANDBY / STOPPED** (`is_runner_active: false`)
- `Campaign 1`: **`DRAFT`**
- `Queue`: **0**
- `infra_health`: **`HEALTHY`**
- `browser_state`: **`DISCONNECTED`**
- `session_authenticated`: **`false`** (unverified while browser stopped)
- `profile_present`: **`true`**

> [!NOTE]
> Healthy Standby is the nominal, expected state of an idle worker host. It is reported as infrastructure `HEALTHY`, runner `STOPPED`, and browser `DISCONNECTED`. It is **never** classified as a failure or degraded state.

---

## 5. Decoupled Process Lock Architecture

To ensure process singularity without cross-component coupling, two independent lock mechanisms are enforced:

| Lock Name | Lock File Path | Owner Class | Scope & Singularity Guarantee |
| :--- | :--- | :--- | :--- |
| **Worker Lock** | `settings.WORKER_LOCK_FILE`<br>(`data/worker.lock`) | `WorkerDaemon` | Guarantees only **ONE** `WorkerDaemon` host process runs per VM. Does not govern campaign dispatch. |
| **Runner Lock** | `settings.RUNNER_LOCK_FILE`<br>(`data/runner.lock`) | `ProductionRunner` | Guarantees only **ONE** single-campaign message dispatcher executes at a time across the system. Authoritative on OS and reflected to `system:active_runner`. |

**Invariants:**
1. `WorkerDaemon` acquires `data/worker.lock` on startup and holds it for its entire lifecycle.
2. `ProductionRunner` acquires `data/runner.lock` only when dispatching a campaign.
3. The two locks are completely independent files and file handles. They must **never** be collapsed into a single lock.
4. If a worker daemon restarts, dead-PID recovery applies only to `data/worker.lock`. It never forcibly breaks or alters `data/runner.lock`.

---

## 6. Campaign Supervision & Observation Semantics

### 6.1 Strict Prohibition on Implicit Transitions
`WorkerDaemon` has **zero authority** to modify campaign lifecycle states:
- It must **never** execute `UPDATE campaigns SET status = 'RUNNING'`.
- It must **never** transition a campaign from `DRAFT`, `SCHEDULED`, `PAUSED`, `COMPLETED`, or `CANCELLED` to `RUNNING`.
- All campaign state transitions are strictly reserved for explicit operator actions (`outreach campaign run <id>`, `outreach campaign pause <id>`, or Web Control Center API endpoints governed by RBAC and audit logging).

### 6.2 Definition of "Campaign Supervisor"
The term "Campaign Supervisor" in `WorkerDaemon` denotes **passive observation and controlled spawning only**:
1. `WorkerDaemon` periodically queries `system:desired_runner_state` and `system:desired_runner_campaign_id`.
2. If `desired_runner_state == "RUNNING"`:
   - `WorkerDaemon` queries the target campaign in PostgreSQL:
     ```python
     campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
     ```
   - **If `campaign.status != "RUNNING"`** (e.g. `DRAFT`):
     - `WorkerDaemon` logs:
       `"Cannot engage runner: Target Campaign %s is in status '%s', must be 'RUNNING'. Runner remains in STANDBY."`
     - Emits audit log record: `RUNNER_ENGAGEMENT_REJECTED`.
     - Leaves `is_runner_active = False`.
     - **`WorkerDaemon` continues its loop cleanly and stays healthy.**
   - **If `campaign.status == "RUNNING"`**:
     - `WorkerDaemon` checks if a runner is already active via `data/runner.lock`.
     - If not active, launches `ProductionRunner(campaign_id=campaign_id)`.
3. If `desired_runner_state == "STOPPED"`:
   - If a child runner is active, signals it to terminate gracefully.

---

## 7. Worker Stop Semantics

### 7.1 Graceful Worker Stop Contract
Executing `outreach worker stop` must behave deterministically:
1. Reads `data/worker.lock` to discover the active `WorkerDaemon` PID.
2. If no active PID is found, reports that no worker daemon is active and exits cleanly.
3. Sends `SIGTERM` to the `WorkerDaemon` PID.
4. `WorkerDaemon` intercepts `SIGTERM` via `SignalCoordinator` and sets `shutdown_requested = True`.
5. `WorkerDaemon` completes its current tick:
   - If an operational command is currently executing, allows it to finish or timeout cleanly.
   - If a child `ProductionRunner` is running, signals it to stop via the existing database coordination (`desired_runner_state = "STOPPED"`).
   - Writes final heartbeat: `runner_state: "STOPPED"`.
   - Releases `data/worker.lock`.
6. **PROHIBITION**: `outreach worker stop` must **never** use `kill -9`, forceful SIGKILL, abrupt browser termination, or `os.kill` against Chrome or `ProductionRunner`. It must never interrupt an in-flight WhatsApp message send.

---

## 8. Command Protocol & PREFLIGHT Safety Invariants

### 8.1 Protocol Preservation
The existing command protocol is preserved without modification:
```
REQUESTED (Vercel) ──► CLAIMED (Worker CAS, lease) ──► EXECUTING (Worker) ──► COMPLETED / FAILED (Worker)
```
- **CAS Claims**: `SELECT ... FOR UPDATE` with version increment.
- **Lease Durations**: Standard commands (`HEALTH_CHECK`, `RECONNECT`, `DISCONNECT`, `LOGOUT`) = 60s; `PREFLIGHT` = 120s.
- **Orphan Recovery**: Unclaimed commands > 120s or expired leases recovered automatically.
- **Single In-Flight Slot**: HTTP 409 Conflict returned if another command is active.

### 8.2 Strict PREFLIGHT Safety Invariants
The `PREFLIGHT` diagnostic command enforces the following absolute constraints:
- **Zero Chrome Cold-Start**: `PREFLIGHT` must **never** invoke `WhatsAppWebProvider.connect()`, `webdriver.Chrome()`, or start a browser process if none is running.
- **Zero WhatsApp Web Activity**: No network calls to `web.whatsapp.com`.
- **Zero Messages**: Exactly 0 messages queued, claimed, or dispatched.
- **Zero QR Triggers**: No pairing flow initiated.
- **Read-Only Probing**: Inspects filesystem paths, binary executables, Xvfb display, database connectivity, and environment variables only.
- Results are saved to `system:last_preflight_result` and reported via `COMPLETED` status.

---

## 9. Systemd Service Unit Design

File: `deploy/systemd/outreach-runner.service`

```ini
[Unit]
Description=WhatsApp Outreach Automation Production Worker Daemon
After=network.target xvfb.service
Requires=xvfb.service

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/whatsapp-outreach/app
EnvironmentFile=/opt/whatsapp-outreach/.env
Environment=DISPLAY=:99
ExecStartPre=/bin/sh -c 'for i in $(seq 1 30); do /usr/bin/xdpyinfo -display :99 >/dev/null 2>&1 && exit 0; sleep 0.2; done; echo "ERROR: Xvfb :99 display not responding" >&2; exit 1'
ExecStart=/opt/whatsapp-outreach/app/.venv/bin/python -m app.cli.main worker start
Restart=on-failure
RestartSec=10
KillSignal=SIGTERM
TimeoutStopSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**Verification against requirements:**
- [x] Runs as `ubuntu:ubuntu`
- [x] WorkingDirectory: `/opt/whatsapp-outreach/app`
- [x] EnvironmentFile: `/opt/whatsapp-outreach/.env`
- [x] DISPLAY: `:99`
- [x] Requires and validates Xvfb before start
- [x] Uses project venv (`/opt/whatsapp-outreach/app/.venv/bin/python`)
- [x] Restarts on actual process failure
- [x] **Completely eliminates `--campaign-id 1`**
- [x] Starts `worker start`, decoupling the daemon from campaign state.

---

## 10. Proposed File Change Inventory

| File Path | Action | Description |
| :--- | :--- | :--- |
| `app/runner/worker_daemon.py` | **CREATE** | Core `WorkerDaemon` class: host lock, identity publishing, 15s heartbeat, 5s command polling, safe supervisor. |
| `app/cli/commands/worker.py` | **CREATE** | CLI command handlers: `handle_worker_start`, `handle_worker_status`, `handle_worker_stop`. |
| `app/cli/main.py` | **MODIFY** | Register `worker` subcommand parser and dispatch to worker handlers. |
| `app/utils/settings.py` | **MODIFY** | Add `WORKER_LOCK_FILE: str = "./data/worker.lock"` and `WORKER_POLL_INTERVAL_SECONDS: int = 5`. |
| `deploy/systemd/outreach-runner.service` | **MODIFY** | Update `ExecStart` to invoke `worker start`. |
| `tests/test_worker_daemon.py` | **CREATE** | Comprehensive unit & lifecycle test suite (14 safety tests). |
| `tests/test_worker_cli.py` | **CREATE** | CLI parsing and command invocation tests for `outreach worker`. |

**Zero Alembic Migrations**: All state stored in existing `app_settings` key-value table.  
**Zero New Dependencies**: Uses existing standard library and project dependencies.

---

## 11. Safety & Behavioral Test Matrix (14 Test Requirements)

The test suite in `tests/test_worker_daemon.py` and `tests/test_worker_cli.py` explicitly tests:

| # | Behavioral Invariant / Safety Test | Test Method / Verification Details |
| :--- | :--- | :--- |
| 1 | **Worker starts with Campaign 1 = DRAFT** | `test_worker_starts_cleanly_when_campaign_draft`: Seed Campaign 1 with status `DRAFT`. Run `WorkerDaemon.start(max_iterations=2)`. Asserts exit code == `ExitCode.SUCCESS`. |
| 2 | **Worker remains alive with zero queue** | `test_worker_remains_alive_with_zero_queue`: Assert queue message count == 0. Run worker loop. Asserts worker executes full iteration cycle without error or exit. |
| 3 | **Worker heartbeat is published** | `test_worker_publishes_heartbeat_periodically`: Query `system:worker_heartbeat`. Asserts `runner_state == "STANDBY"`, `uptime_seconds > 0`, and timestamp fresh. |
| 4 | **Worker identity is published** | `test_worker_publishes_identity_on_start`: Query `system:worker_identity`. Asserts `instance_id == "oracle-arm64-worker-01"`, arch == `aarch64` / test arch, and capabilities list populated. |
| 5 | **Campaign 1 remains DRAFT** | `test_campaign_state_immutable_under_worker`: Inspect Campaign 1 in database after worker run. Asserts `campaign.status == "DRAFT"` strictly. |
| 6 | **Queue remains unchanged** | `test_queue_untouched_by_worker`: Verify queue status before and after worker loop. Asserts 0 status mutations and 0 lease acquisitions. |
| 7 | **Zero Chrome cold-starts** | `test_zero_chrome_cold_start_on_worker_start`: Spy on `subprocess.Popen` / `WhatsAppWebProvider.connect`. Asserts 0 browser process launches. |
| 8 | **Zero WhatsApp Web launches** | `test_zero_whatsapp_web_launch`: Verify browser session manager is never initialized. Asserts no URL navigation or network calls. |
| 9 | **Zero QR flows** | `test_zero_qr_flow_initiated`: Verify QR capture / polling is never invoked during worker idle startup. |
| 10 | **Zero messages sent** | `test_zero_messages_dispatched`: Verify mock provider send method. Asserts call count == 0. |
| 11 | **ProductionRunner still refuses DRAFT** | `test_production_runner_still_refuses_draft_campaign`: Directly invoke `ProductionRunner(campaign_id=1).start()`. Asserts exit code == `ExitCode.INVALID_STATE` (5). |
| 12 | **No implicit DRAFT $\to$ RUNNING transition** | `test_worker_supervision_never_promotes_draft`: Set `system:desired_runner_state = "RUNNING"` while Campaign 1 is `DRAFT`. Run worker tick. Asserts campaign remains `DRAFT` and runner does not spawn. |
| 13 | **Independent process locks** | `test_locks_are_strictly_isolated`: Verify `WorkerDaemon` acquires `data/worker.lock`. Verify `ProductionRunner` lock file remains `data/runner.lock`. Asserts acquiring worker lock does not block runner lock and vice versa. |
| 14 | **Worker stop does not force-kill** | `test_worker_stop_graceful_shutdown`: Simulate `outreach worker stop`. Verify SIGTERM is handled gracefully by `WorkerDaemon` without killing active child processes or providers. |

---

## 12. Security & Operational Hardening

1. **Host Isolation**: Web control plane (Vercel) communicates with Oracle worker strictly via Supabase PostgreSQL. Zero inbound ports are opened on the Oracle VM.
2. **Credential Scrubbing**: Worker identity and heartbeat payloads are scrubbed of any sensitive tokens or database credentials before writing to `app_settings`.
3. **VM Permissions**: File permissions on `/opt/whatsapp-outreach/.env` remain strictly `-rw-------` (`0600`) owned by `ubuntu:ubuntu`.
4. **Anti-Evasion Compliance**: Zero user-agent spoofing, canvas spoofing, or bot evasion logic is implemented.

---

## 13. Rollback Plan

If any anomaly occurs:
1. **Local Repository**:
   ```bash
   git checkout master
   git revert HEAD -m 1
   ```
2. **Oracle Production Host**:
   ```bash
   # 1. Stop service
   sudo systemctl stop outreach-runner.service

   # 2. Revert repo
   cd /opt/whatsapp-outreach/app
   git checkout 1c19a36

   # 3. Restore previous unit file
   sudo cp deploy/systemd/outreach-runner.service /etc/systemd/system/outreach-runner.service
   sudo systemctl daemon-reload

   # 4. Verify stopped
   sudo systemctl status outreach-runner.service
   ```

---

## 14. Exact Oracle Deployment Commands

When approved for deployment:

```bash
# 1. Connect to Oracle Worker
ssh -i "path/to/key.key" ubuntu@84.13.139.20

# 2. Navigate to project root
cd /opt/whatsapp-outreach/app

# 3. Pull approved commit
git pull origin master

# 4. Verify commit and clean working tree
git status
git log -n 1 --oneline

# 5. Verify systemd unit file has no --campaign-id
grep "campaign-id" deploy/systemd/outreach-runner.service || echo "CONFIRMED: Clean unit file"

# 6. Copy unit file and reload systemd
sudo cp deploy/systemd/outreach-runner.service /etc/systemd/system/outreach-runner.service
sudo systemctl daemon-reload

# 7. Start worker service
sudo systemctl start outreach-runner.service

# 8. Check service status (active and NOT restarting)
sleep 5
sudo systemctl status outreach-runner.service --no-pager

# 9. Verify journal logs
journalctl -u outreach-runner.service -n 30 --no-pager
```

---

## 15. Exact Handshake Verification Procedure (Step 8A)

```bash
# 1. Check Healthy Standby State (Read-Only)
# From Control Plane or CLI:
python -m app.cli.main system health --json

# Expected Output:
# - instance_id: "oracle-arm64-worker-01"
# - infra_health: "HEALTHY"
# - runner_state: "STANDBY"
# - browser_state: "DISCONNECTED"
# - session_authenticated: false
# - profile_present: true
# - queue: total=0

# 2. Trigger Safe PREFLIGHT Diagnostic Command
POST /api/v1/whatsapp/preflight

# 3. Poll Status until Completed
GET /api/v1/whatsapp/status

# 4. Handshake Verification Criteria:
# - Command status transitions: REQUESTED -> CLAIMED -> EXECUTING -> COMPLETED
# - Lease duration: 120 seconds
# - Chrome cold-started: FALSE (0 processes)
# - WhatsApp Web launched: FALSE
# - Messages sent: 0
# - Production queue: untouched
# - outreach-runner.service: active, restart counter: 0
```

---

## 16. Request for Implementation Approval

This revised design strictly enforces all 9 architectural directives, preserves campaign state safety, and eliminates the systemd crash loop.

**STOP CONDITION**: We remain stopped awaiting explicit user approval before writing code changes or modifying the production environment.
