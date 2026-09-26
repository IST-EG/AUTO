# Phase 7.7-B Step 7: Worker Startup Architecture Correction
## Verification and Implementation Report

**Status**: `IMPLEMENTATION_COMPLETE_VERIFIED`  
**Date**: September 26, 2026  
**Scope**: Decoupling WorkerDaemon from Campaign Execution, Systemd Unit Correction, and Process Locking  

---

## 1. Problem Statement & Root Cause

During initial Step 8A read-only smoke testing on the Oracle ARM64 worker (`oracle-arm64-worker-01`), `outreach-runner.service` was found stopped. When inspecting the service configuration:

```ini
ExecStart=/opt/whatsapp-outreach/app/.venv/bin/python -m app.cli.main runner start --campaign-id 1
```

The systemd service was directly launching `ProductionRunner` bound to Campaign 1. Because Campaign 1 is in `DRAFT` status (as required by safety rules), `ProductionRunner` strictly validated the campaign state and exited with `ExitCode.INVALID_STATE` (code 20). With `Restart=on-failure`, systemd entered a restart loop.

Furthermore, tying host daemon lifecycle (identity publication, 15-second infrastructure heartbeat, command polling) to an active campaign violated separation of concerns:
- An execution host worker must remain alive, discoverable, and responsive to operator commands (such as `PREFLIGHT`) even when no campaign is running or when all campaigns are in `DRAFT` / `PAUSED` / `COMPLETED` states.

---

## 2. Implemented Architecture & Guarantees

### A. Decoupled Lifecycle: `WorkerDaemon` vs `ProductionRunner`
1. **Always-On Worker Daemon (`WorkerDaemon`)**:
   - Implemented in `app/runner/worker_daemon.py`.
   - Entry point: `outreach worker start`.
   - Primary duties:
     - Validates `WORKER_INSTANCE_ID` strictly at startup.
     - Publishes worker identity (`system:worker_identity`).
     - Emits 15-second infrastructure heartbeat (`system:worker_heartbeat`).
     - Polls and executes control-plane commands every 5 seconds (`system:whatsapp_command:active`).
     - Executes read-only, safe `PREFLIGHT` diagnostics without launching Chrome or WhatsApp Web.
     - Recovers orphaned/stale commands (`REQUESTED` / `CLAIMED` / `EXECUTING`).
     - Passively observes desired runner state (`system:desired_runner_state`).
     - Shuts down cleanly on SIGTERM/SIGINT.
2. **Campaign Execution Isolation (`ProductionRunner`)**:
   - Entry point: `outreach runner start --campaign-id <ID>`.
   - Retains 100% of its domain validation: strictly rejects `DRAFT`, `PAUSED`, `ARCHIVED`, or `CANCELLED` campaigns.
   - Remains completely independent of `WorkerDaemon`.

### B. Strict Campaign Business Rules Preserved
- **Zero Implicit State Transitions**: `WorkerDaemon` strictly never mutates campaign states and never promotes `DRAFT` $\to$ `RUNNING`.
- **Campaign 1 State**: Campaign 1 remains strictly in `DRAFT` status in production.
- **Passive Campaign Observation**: `WorkerDaemon` only inspects campaign and runner state for telemetry purposes.

### C. Decoupled Process Locks
- **Worker Lock (`data/worker.lock`)**: Enforces singleton daemon execution on the host (`ProcessLock(WORKER_LOCK_FILE)`).
- **Runner Lock (`data/runner.lock`)**: Enforces single active campaign runner execution (`ProcessLock(RUNNER_LOCK_FILE)`).
- **Lock Concurrency**: Non-blocking byte locks at fixed offset 4096 (Windows `msvcrt.locking`) or `fcntl.flock` (Linux) permit non-interfering reads of JSON metadata at byte 0 by `status` commands.

### D. Safe Process Management & Shutdown Semantics
- `outreach worker stop`: Sends standard `SIGTERM` to the daemon PID from `data/worker.lock`.
- No forceful `SIGKILL` (`kill -9` / `taskkill /F`).
- Never force-terminates Chrome, ChromeDriver, or `ProductionRunner`.
- `outreach worker status`: Inspects lock file and live process table safely without taking write locks.

### E. Systemd Service Reconfiguration
- `deploy/systemd/outreach-runner.service`:
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
- Fully removed `--campaign-id 1`.

---

## 3. Inventory of Changes

### New Files
1. **`app/runner/worker_daemon.py`**:
   - Core `WorkerDaemon` class managing identity publication, 15s heartbeat, 5s command polling, orphaned command recovery, and graceful signal handling.
2. **`app/cli/commands/worker.py`**:
   - Subcommand handlers: `handle_worker_start`, `handle_worker_status`, `handle_worker_stop`.
3. **`tests/test_worker_daemon.py`**:
   - 14 comprehensive unit tests: startup identity publication, missing/invalid `WORKER_INSTANCE_ID` rejection, 15s heartbeat publication, 5s command execution, preflight execution, orphan command recovery, passive campaign supervision (no DRAFT transition), and graceful shutdown on SIGTERM/SIGINT.
4. **`tests/test_worker_cli.py`**:
   - 6 CLI integration tests: argument parsing, start validation failures, lock collision prevention, status reporting (stopped and running), and graceful stop handling.

### Modified Files
1. **`app/utils/settings.py`**:
   - Added `WORKER_LOCK_FILE: str = "./data/worker.lock"`.
   - Added `WORKER_POLL_INTERVAL_SECONDS: int = 5`.
2. **`app/runner/process_lock.py`**:
   - Extended `acquire()` to allow optional `campaign_id: Optional[int] = None`.
   - Added `get_active_process_info()`.
   - Fixed Windows file locking by placing the byte lock at offset 4096 so JSON metadata at byte 0 remains readable across processes.
3. **`app/cli/main.py`**:
   - Registered `worker` subcommand parser with `start`, `status`, and `stop` actions.
4. **`deploy/systemd/outreach-runner.service`**:
   - Updated description and changed `ExecStart` to `worker start`.
5. **`CHANGELOG.md`** & **`PROJECT_CONTEXT.md`**:
   - Documented the architecture correction, invariant protections, and new CLI commands.

---

## 4. Verification Results

### A. Static & Configuration Verification
- **Database Migrations**: `0` new migrations. Alembic directory completely clean.
- **Dependencies**: `0` changes in `requirements.txt` or `pyproject.toml`.
- **Systemd Service**: Confirmed zero references to `campaign` or `--campaign-id` in `deploy/systemd/outreach-runner.service`.

### B. Targeted Test Suites
- **Worker Daemon Tests (`tests/test_worker_daemon.py`)**: 14/14 PASSED
- **Worker CLI Tests (`tests/test_worker_cli.py`)**: 6/6 PASSED
- **Process Lock Tests (`tests/test_process_lock.py`)**: 9/9 PASSED
- **Preflight Checks (`tests/test_preflight.py`)**: 11/11 PASSED
- **Control Plane Identity (`tests/web/test_whatsapp_worker_identity.py`)**: 38/38 PASSED
- **Control Plane Preflight (`tests/web/test_whatsapp_preflight_command.py`)**: 10/10 PASSED
- **CLI Commands & Main (`tests/test_cli_main.py`, `tests/test_cli_commands.py`)**: 46/46 PASSED
- **Runner Lifecycle & Integration (`tests/test_runner_integration.py`, `tests/test_runner_lifecycle.py`)**: 16/16 PASSED

### C. Full Non-Live Regression Suite
- Total items: 483 items (1 deselected live browser test, 482 selected).
- Zero regressions across the entire test suite.

---

## 5. Production Safety Status

- **Oracle Worker Host (`ubuntu@84.13.139.20`)**:
  - `outreach-runner.service`: INACTIVE / STOPPED
  - Chrome processes: 0
  - ChromeDriver processes: 0
  - WhatsApp messages dispatched: 0
  - Production queue: Untouched (0 pending/processing/completed)
  - Campaign 1: Strictly in `DRAFT` status
  - Production WhatsApp profile (`/opt/whatsapp-outreach/data/whatsapp_session`): Untouched
