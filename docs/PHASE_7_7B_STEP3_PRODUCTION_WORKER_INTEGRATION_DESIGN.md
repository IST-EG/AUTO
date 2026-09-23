# Phase 7.7-B — Step 3: Production Worker Integration Design
## Native Google Chrome for Testing ARM64 Integration into Production Worker Architecture

**Document Status**: `DESIGN STATUS: READY_FOR_APPROVAL`  
**Target Release**: Phase 7.7-B (Step 3)  
**Date**: September 22, 2026 (Revision 2 — Post-Review Corrections Applied)  
**Environment**: Oracle Cloud Always Free A1 Flex (`aarch64` / ARM64, Ubuntu 24.04, 2 OCPU, 12 GB RAM, 4 GB Swap)  
**Cost Model**: Strict $0.00/month Target (Zero Paid Services, Zero Paid Proxies/VPS, Zero Paid APIs)  
**Compliance & Anti-Evasion**: Zero User-Agent Spoofing, Zero Fingerprint Spoofing, Zero Stealth Plugins, Zero CAPTCHA/Anti-Ban Bypasses  
**Verification Milestones Passed**:
- Phase 7.7-B Step 2A: `PAIRING_SUCCESS` (1920x1080 Full-Screen Evidence & QR Verification)
- Phase 7.7-B Step 2B: `SESSION_PERSISTED` (Controlled Browser + Driver Restart)
- Phase 7.7-B Step 2C: `SESSION_PERSISTED_ACROSS_VM_REBOOT` (Full Oracle OS Reboot Persistence)

---

## 1. Current Architecture Overview

The WhatsApp Outreach Automation system is organized across three primary tiers:

```
+-------------------------------------------------------------+
|                 Vercel Web Control Plane                    |
|  - FastAPI (Serverless ASGI runtime)                        |
|  - Server-Side Rendered Integra Design System (IDS) UI      |
|  - Zero Selenium, Zero Browser, Zero Process Forking        |
+-------------------------------------------------------------+
                              |
                              | SSL / TLS (Pooler / Session)
                              v
+-------------------------------------------------------------+
|                   Supabase PostgreSQL                       |
|  - Core Data: campaigns, contacts, messages, audit_logs     |
|  - State Coordination: app_settings (CAS command protocol,  |
|    telemetry, emergency stop, process lock heartbeats)     |
+-------------------------------------------------------------+
                              ^
                              | Direct Connection (Port 5432)
                              |
+-------------------------------------------------------------+
|               Oracle Cloud Always Free ARM64 Worker         |
|  - Ubuntu 24.04 aarch64 (2 OCPU, 12 GB RAM)                |
|  - ProductionRunner daemon (polls DB queue, executes tasks) |
|  - QueueWorker & WhatsAppWebProvider                        |
|  - PREVIOUS: Snap Chromium in Headless Mode (FAILED)        |
+-------------------------------------------------------------+
```

### The Previous Failure Mode (Snap Chromium Headless)
Prior to Phase 7.7-B, the worker attempted to execute WhatsApp Web automation using Ubuntu Snap-packaged Chromium in headless mode (`--headless=new`). This failed catastrophically due to:
1. **Snap Sandboxing**: Snap confinement conflicts with systemd daemon execution, dynamic library resolution issues (`execvp` failures), and permissions friction.
2. **WhatsApp Web Anti-Headless Detection**: WhatsApp Web detects Chromium headless mode via missing browser features, WebGL renderer signatures, and canvas fingerprinting, presenting a blocking screen ("*Update Google Chrome - WhatsApp works with Google Chrome 49+*") that prohibited session establishment.
3. **Renderer Memory Exhaustion**: Headless Chromium on ARM64 suffered repeated renderer crashes without software rasterization fallbacks.

---

## 2. Proposed Architecture Overview

The proposed integration embeds the proven Google Chrome for Testing ARM64 runtime into the existing production worker daemon without altering application business logic or database schemas:

```
+-----------------------------------------------------------------------------------------+
|                         Oracle Cloud Always Free ARM64 VM (aarch64)                     |
|                                                                                         |
|  +-------------------------------------+      +--------------------------------------+  |
|  |     systemd: xvfb.service           |      |    systemd: outreach-runner.service  |  |
|  |  - Xvfb :99 1920x1080x24            |      |  - User: ubuntu                      |  |
|  |  - -nolisten tcp (Local socket only)|      |  - WorkingDir: /opt/whatsapp-outreach|  |
|  |  - Auto-start on boot (Enabled)     |      |  - EnvFile: /opt/whatsapp-outreach/.env| |
|  +-------------------------------------+      |  - Environment: DISPLAY=:99          |  |
|                     |                         |  - After: network.target xvfb.service|  |
|                     | Display Socket :99      |  - Requires: xvfb.service            |  |
|                     |                         |  - ExecStartPre: xdpyinfo probe      |  |
|                     v                         +--------------------------------------+  |
|  +---------------------------------------------------------------+                      |
|  |                                                               |                      |
|  |  ProductionRunner Daemon                                      v                      |
|  |  - Authoritative OS ProcessLock (/opt/whatsapp-outreach/data/runner.lock)            |
|  |  - SignalCoordinator (SIGINT/SIGTERM graceful shutdown)                              |
|  |  - Preflight Inspection (Runtime, DB, Chrome, Profile, Locks, Emergency Stop)        |
|  |  - QueueWorker & PersistentQueueService                                              |
|  |  - WhatsAppCommandHandler (Phase 7.6 CAS Protocol: 60s lease, Telemetry pub)         |
|  |                                                                                      |
|  |  WhatsAppWebProvider & WhatsAppBrowser                                               |
|  |  - Chrome for Testing Stable (153.0.8010.52 ARM64 linux)                             |
|  |    Binary: /opt/google/chrome-for-testing/chrome                                     |
|  |  - Matching ChromeDriver (153.0.8010.52 ARM64 linux)                                 |
|  |    Binary: /usr/local/bin/chromedriver                                               |
|  |  - Execution Mode: Non-headless (WHATSAPP_HEADLESS=False) rendered onto Xvfb :99     |
|  |  - Browser Flags: --no-sandbox, --disable-dev-shm-usage, --disable-gpu,              |
|  |                   --disable-extensions, --remote-debugging-port=0                    |
|  |  - Clean Production Profile: /opt/whatsapp-outreach/data/whatsapp_session            |
|  |    (Permissions: 700, Owned by ubuntu:ubuntu)                                        |
|  +--------------------------------------------------------------------------------------+
```

### Architectural Guarantees Preserved:
1. **Strict Cost Cap ($0/month)**: Exclusively uses Oracle Always Free VM resources, official Google Chrome for Testing binaries, and Supabase free-tier database. Zero paid proxies, commercial anti-detect browsers, or remote browser grids.
2. **Strict Identity Integrity**: Absolutely no User-Agent overrides, zero canvas/WebGL noise injection, zero stealth scripts, zero CAPTCHA bypass scripts. Chrome for Testing reports its genuine identity.
3. **Control Plane / Worker Physical Plane Isolation**: Vercel contains 0 Selenium imports, 0 WebDriver instantiations, and executes 0 browser processes. All automation is hosted on the worker VM.
4. **Clean Profile Singularity**: One single browser process accesses the persistent profile directory at any given time, enforced by authoritative OS `flock` and active PID validation.

---

## 3. Exact Files Inspected

The following codebase files were inspected to formulate this design:

| File Path | Role & Invariants Inspected |
| :--- | :--- |
| `app/runner/production_runner.py` | Daemon lifecycle, OS file locking (`ProcessLock`), preflight check integration, provider initialization, safe cancellation points, heartbeat updates (15s), message pacing, graceful shutdown. |
| `app/runner/whatsapp_command_handler.py` | Worker-side executor for Phase 7.6 commands (`HEALTH_CHECK`, `RECONNECT`, `DISCONNECT`, `LOGOUT`), telemetry publisher (`system:whatsapp_telemetry`), orphan lease recovery. |
| `app/providers/whatsapp_web/provider.py` | `MessageProvider` implementation, delegates browser control to `WhatsAppSessionManager` and `WhatsAppBrowser`, send confirmation verification, error classification mapping. |
| `app/providers/whatsapp_web/session_manager.py` | Session lifecycle state machine (`DISCONNECTED`, `AUTHENTICATING`, `CONNECTED`, etc.), QR scan awaiting, health monitoring (`check_health()`), clean browser restart (`restart_session()`), shutdown. |
| `app/providers/whatsapp_web/browser.py` | Selenium controller, passes Chrome options, binary path, chromedriver path, persistent `--user-data-dir`, DOM selectors, message typing, send click, send confirmation checking. |
| `app/providers/whatsapp_web/error_mapper.py` | Error classification engine mapping exceptions to `PERMANENT`, `TEMPORARY`, and `UNKNOWN_OUTCOME`. |
| `app/queue/service.py` | Message lifecycle, state transitions (`PENDING`, `CLAIMED`, `FAILED`, `SENT`), `mark_failed` with `error_type`, lease recovery. |
| `app/readiness/preflight.py` | Production readiness engine, validates Python 3.8+, DB connection, core schema, Chrome binary existence, session profile presence, process lock singularity, emergency stop status. |
| `app/readiness/health.py` | Health check probes for worker services, database latency, and WhatsApp session health. |
| `app/utils/settings.py` | Central `Settings` class (`pydantic-settings`), defines canonical settings: `WHATSAPP_CHROME_BINARY`, `WHATSAPP_CHROMEDRIVER_PATH`, `WHATSAPP_SESSION_PATH`, `WHATSAPP_HEADLESS`. |
| `app/cli/commands/session.py` | CLI commands for operator session login (`outreach session login`), session status check (`outreach session status`), and session logout (`outreach session logout`). |
| `app/services/whatsapp_command_service.py` | Control-plane CAS command coordinator (`submit_command`, `claim_command`, `mark_executing`, `complete_command`, `fail_command`, `recover_stale_or_orphaned_command`). |
| `app/web/routes/api/whatsapp.py` | REST API endpoints for session control operations (GET status, diagnostics, POST health-check, reconnect, disconnect, logout, clear-stale). |
| `app/web/schemas/whatsapp.py` | Pydantic DTOs for host diagnostics, command lifecycle, and status envelopes. |
| `deploy/systemd/outreach-runner.service` | Systemd service unit template for production runner daemon. |
| `deploy/worker/env.worker.example` | Template environment configuration file for worker host. |
| Oracle VM: `/etc/systemd/system/xvfb.service` | Existing live systemd unit managing Xvfb virtual framebuffer on display `:99`. |
| Oracle VM: `/opt/whatsapp-outreach/.env` | Live worker configuration file. |

---

## 4. Exact Settings Used

All settings are managed via `/opt/whatsapp-outreach/.env` and parsed by `app.utils.settings.settings`:

| Setting Name | Canonical Value | Description / Rationale |
| :--- | :--- | :--- |
| `APP_ENV` | `production` | Deployment mode flag. |
| `APP_TIMEZONE` | `Africa/Cairo` | Business day boundary and scheduled dispatch timezone. |
| `DATABASE_URL` | `postgresql+psycopg2://...:5432/postgres?sslmode=require` | Direct Supabase PostgreSQL connection string. |
| `GLOBAL_DAILY_LIMIT` | `100` | Safety cap for daily message dispatches across all campaigns. |
| `LOG_LEVEL` | `INFO` | Standard logging verbosity. |
| `LOG_TO_FILE` | `True` | Directs logging to rotating files. |
| `LOG_FILE` | `/opt/whatsapp-outreach/logs/app.log` | Standard text log file path. |
| `LOG_JSON_FILE` | `/opt/whatsapp-outreach/logs/app.json.log` | Structured JSON log file path. |
| `WHATSAPP_SESSION_PATH` | `/opt/whatsapp-outreach/data/whatsapp_session` | Path to persistent Chrome user-data-dir for production. |
| `WHATSAPP_HEADLESS` | `False` | **CRITICAL**: Must be `False` to run real Chrome for Testing against Xvfb `:99`. WhatsApp blocks headless mode. |
| `WHATSAPP_BROWSER_TIMEOUT` | `30` | Explicit DOM element wait timeout in seconds. |
| `WHATSAPP_PAGE_LOAD_TIMEOUT`| `45` | Maximum page load wait time in seconds. |
| `WHATSAPP_QR_TIMEOUT` | `120` | Operator QR code scan wait timeout during authentication. |
| `WHATSAPP_CHROME_BINARY` | `/opt/google/chrome-for-testing/chrome` | Verified official Google Chrome for Testing ARM64 ELF binary. |
| `WHATSAPP_CHROMEDRIVER_PATH`| `/usr/local/bin/chromedriver` | Verified matching ChromeDriver ARM64 binary (153.0.8010.52). |
| `DISPLAY` | `:99` | X11 display provided by `xvfb.service` (configured in systemd unit). |
| `RUNNER_POLL_INTERVAL_SECONDS`| `5` | Queue polling cadence when no claimable messages are pending. |
| `RUNNER_HEARTBEAT_SECONDS` | `15` | Cadence for updating process lock and database telemetry. |
| `RUNNER_LOCK_FILE` | `/opt/whatsapp-outreach/data/runner.lock` | Authoritative OS file lock ensuring single runner execution. |
| `RUNNER_REMOTE_COORDINATION`| `False` | Worker directly manages execution loop; polls DB commands. |
| `CLI_PAGE_SIZE` | `20` | Output pagination limit for CLI inspection commands. |

---

## 5. Production Profile Strategy & Strict 9-Step Cutover

### 5.1 Preservation of Verified Benchmark Profile
The test profile used during Steps 2A, 2B, and 2C located at:
```
/home/ubuntu/cft_poc/pairing_test_profile
```
contains an active, authenticated session verified across VM reboots (203 MB, 24 MB IndexedDB).
**Strict Invariants**:
1. This directory **MUST REMAIN COMPLETELY UNTOUCHED** as an immutable benchmark and diagnostic reference.
2. **DO NOT COPY** authentication or session files from this test directory into the production directory. The production profile must be established independently.

### 5.2 Clean Independent Production Profile Cutover Workflow
The production profile path is:
```
/opt/whatsapp-outreach/data/whatsapp_session
```
The previous contents are contaminated from earlier failed Snap Chromium headless attempts that caused the *"Couldn't link"* error. 

The cutover follows this strict 9-step sequence:

```
[1. Archive Old Profile]
   └── mv /opt/whatsapp-outreach/data/whatsapp_session -> whatsapp_session_backup_<timestamp>
[2. Create Fresh Directory]
   └── mkdir -p /opt/whatsapp-outreach/data/whatsapp_session
[3. Set Owner & Permissions]
   └── chown -R ubuntu:ubuntu /opt/whatsapp-outreach/data/whatsapp_session && chmod -R 700
[4. Start Chrome Operationally]
   └── Launch Chrome for Testing on DISPLAY=:99 with fresh production user-data-dir
[5. Perform ONE Manual QR Pairing]
   └── Physical operator scans QR code via outreach session login / SSH-tunnel VNC
[6. Verify Authenticated State]
   └── WhatsApp chat interface renders; IndexedDB leveldb and session tokens populate
[7. Verify Browser Restart Persistence]
   └── Stop Chrome, restart with same production profile, confirm instant login without QR
[8. Verify VM Reboot Persistence]
   └── Perform controlled sudo reboot, verify auto-login on display :99 without QR prompt
[9. ProductionRunner Integration Eligible]
   └── ONLY after Steps 1-8 PASS is outreach-runner.service permitted to start
```

### 5.3 Prohibition of Auto-Pairing
**The ProductionRunner daemon is strictly prohibited from attempting automated pairing or QR capture.** If the session is unauthenticated, the runner must immediately exit with `ExitCode.AUTHENTICATION_REQUIRED` (83).

---

## 6. Systemd Process Model, User Ownership & Dependency Ordering

### 6.1 Host Ownership & User Account Audit
An exhaustive live audit of the Oracle ARM64 worker filesystem confirms:
- **Service User**: The Unix user `outreach` **does not exist** on this system (`getent passwd outreach` returns null).
- **Active System User**: The single dedicated non-root operator account is `ubuntu` (`uid=1001, gid=1001`).
- **Filesystem Permissions Audit**:
  - `/opt/whatsapp-outreach`: Owned by `ubuntu:ubuntu` (`drwxr-xr-x`).
  - `/opt/whatsapp-outreach/app`: Owned by `ubuntu:ubuntu` (`drwxrwxr-x`).
  - `/opt/whatsapp-outreach/.env`: Owned by `ubuntu:ubuntu` with strict `600` permissions (`-rw-------`).
  - `/opt/whatsapp-outreach/data`: Owned by `ubuntu:ubuntu` (`drwxr-xr-x`).
  - `/opt/whatsapp-outreach/data/whatsapp_session`: Owned by `ubuntu:ubuntu` with strict `700` permissions (`drwx------`).
  - Python virtual environment: `/opt/whatsapp-outreach/app/.venv` owned by `ubuntu:ubuntu`.
  - Display server: `xvfb.service` runs under `User=ubuntu` (PID 940), and `/tmp/.X11-unix/X99` is owned by `ubuntu:ubuntu`.
- **Files Created by ProductionRunner**:
  - Lockfile: `/opt/whatsapp-outreach/data/runner.lock`
  - Logs: `/opt/whatsapp-outreach/logs/app.log` and `app.json.log`
  - Chrome cache and storage under `/opt/whatsapp-outreach/data/whatsapp_session`
- **Application Assumptions**: The application code contains **0** assumptions or hardcoded references to a Unix user named `outreach`.
- **Conclusion**: `User=ubuntu` and `Group=ubuntu` are not only safe, but are **strictly required** to prevent `217/USER` execution errors and permission denials.

### 6.2 Systemd Dependency & Startup Ordering Evaluation
In systemd semantics:
1. `Requires=xvfb.service` establishes a strict requirement (if `xvfb.service` fails or stops, `outreach-runner.service` is stopped). However, `Requires=` **does not enforce ordering**; systemd starts both units concurrently.
2. `After=network.target xvfb.service` is **strictly required** to guarantee systemd does not invoke `outreach-runner.service` until `xvfb.service` has initiated execution.
3. **Display Readiness Guarantee**: `xvfb.service` uses `Type=simple`. Systemd considers it active immediately upon `execve()`, but Xvfb requires ~100ms to open `/tmp/.X11-unix/X99` and accept clients.
   To eliminate any race condition, `outreach-runner.service` must enforce an `ExecStartPre=` probe verifying that `DISPLAY=:99` is responsive before launching the Python daemon.

### 6.3 Validated Systemd Unit Definition (`outreach-runner.service`)
```ini
[Unit]
Description=WhatsApp Outreach Automation Production Runner Daemon
After=network.target xvfb.service
Requires=xvfb.service

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/whatsapp-outreach/app
EnvironmentFile=/opt/whatsapp-outreach/.env
Environment=DISPLAY=:99

# Deterministic Display Readiness Probe: verifies Xvfb accepts connections
ExecStartPre=/bin/sh -c 'for i in $(seq 1 30); do /usr/bin/xdpyinfo -display :99 >/dev/null 2>&1 && exit 0; sleep 0.2; done; echo "ERROR: Xvfb :99 display not responding" >&2; exit 1'

ExecStart=/opt/whatsapp-outreach/app/.venv/bin/python -m app.cli.main runner start --campaign-id 1
Restart=on-failure
RestartSec=10
KillSignal=SIGTERM
TimeoutStopSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### 6.4 Process Singularity & Exclusivity Enforcement
1. **OS File Lock**: The daemon instantiates `ProcessLock(lock_file_path="/opt/whatsapp-outreach/data/runner.lock")`.
2. **Atomic `flock`**: Uses non-blocking OS lock (`fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)`).
3. **PID Validation**: If a lock exists, it inspects `/proc/<pid>` to verify if the process is alive. If alive and belonging to another runner, the daemon logs `Concurrency error: duplicate runner` and exits immediately with `ExitCode.CONCURRENCY_ERROR` (84).
4. **Heartbeat Protocol**: While active, the runner updates the lock file's `mtime` and writes its heartbeat to `app_settings` every 15 seconds.

### 6.5 Graceful Shutdown Lifecycle
Upon receiving `SIGTERM` or `SIGINT` from systemd:
1. `SignalCoordinator` catches the signal and sets `shutdown_requested = True`.
2. Any in-flight message send currently inside `WhatsAppBrowser.click_send()` completes its UI verification before loop exit (Safe Cancellation Point).
3. The runner enters `_shutdown_gracefully()`:
   - Calls `provider.disconnect()` -> `session_manager.shutdown()` -> `browser.quit()`, closing ChromeDriver and Chrome cleanly.
   - Releases OS file lock `/opt/whatsapp-outreach/data/runner.lock`.
   - Records `RUNNER_STOPPED` in `audit_logs`.
   - Transitions lifecycle state to `STOPPED` and terminates with `ExitCode.SUCCESS` (0).
4. `TimeoutStopSec=30` in systemd ensures ample time for browser teardown before any kernel `SIGKILL`.

---

## 7. Startup Sequence & Strict Readiness Contract

The production runner enforces a fail-fast startup sequence. **Under no circumstances does message dequeuing begin if any preflight or authentication check fails.**

```
+---------------------------------------------------------------------------------+
|                               Startup Sequence                                  |
+---------------------------------------------------------------------------------+
                                      |
                                      v
  [1. Acquire ProcessLock] --------> FAIL? ---> Exit ExitCode.CONCURRENCY_ERROR (84)
                                      | PASS
                                      v
  [2. Register Signal Handlers] ----> Trap SIGINT / SIGTERM
                                      |
                                      v
  [3. Run Preflight Inspection] ----> FAIL? ---> Exit Preflight ExitCode
      - Python >= 3.8, 64-bit         |
      - DB Connectivity (SELECT 1)    |
      - DB Schema (6 core tables)     |
      - Chrome Binary exists          |
      - Profile Directory exists      |
      - Singularity check             |
      - Emergency Stop inactive       |
      - Circuit Breaker closed        |
                                      | PASS
                                      v
  [4. Validate Target Campaign] ----> Not Found? -> Exit ExitCode.NOT_FOUND (81)
      - Must exist                    | Not RUNNING? -> Exit ExitCode.INVALID_STATE (82)
      - Must be status 'RUNNING'      |
                                      | PASS
                                      v
  [5. Initialize WhatsApp Provider] -> Instantiates WhatsAppWebProvider
      - Chrome for Testing binary     |
      - ChromeDriver binary           |
      - Non-headless (DISPLAY=:99)    |
                                      |
                                      v
  [6. Connect & Health Check] ------> Connect Error? -> Exit PROVIDER_UNAVAILABLE (70)
      - session_manager.initialize()  |
      - provider.health_check()       | Health Check Failed? (Unauthenticated / QR)
                                      +----------------> Exit AUTHENTICATION_REQUIRED (83)
                                      | PASS
                                      v
  [7. Startup Stale Lease Recovery]-> Reclaims expired message leases
  [8. Orphan Command Recovery] -----> Reconciles orphaned Phase 7.6 commands
  [9. Publish Telemetry] -----------> Publishes initial state (CONNECTED / HEALTHY)
                                      |
                                      v
  [10. Enter Main Polling Loop] ----> Begin Campaign Message Processing
```

---

## 8. Authentication States & Operator Notification

The session state follows the formal state machine in `app/providers/whatsapp_web/state.py`:

```
              +---------------+
              | DISCONNECTED  |
              +---------------+
                      |
                      | initialize_session()
                      v
              +---------------+
              |  CONNECTING   |
              +---------------+
                 /         \
   Session valid/           \ Session invalid /
   Cookies restored          \ QR required
               v               v
       +---------------+  +---------------+
       |   CONNECTED   |  | AUTHENTICATING| <--- Requires Operator QR Scan
       +---------------+  +---------------+
          |          ^         |
QR appears|          | Re-auth | Chat pane rendered
remotely  v          |         v
       +---------------+  +---------------+
       | SESSION_LOST  |  |    ERROR      |
       +---------------+  +---------------+
               \              /
                \            /
                 v          v
              +---------------+
              |    STOPPED    |
              +---------------+
```

### Behavior When Authentication is Required (`AUTH_REQUIRED`):
1. **Daemon Mode**: The production runner daemon cannot scan QR codes. Upon detecting `AUTHENTICATING` without active session cookies, the health check fails and the runner terminates with exit code 83.
2. **Telemetry Synchronization**: `WhatsAppCommandHandler.publish_telemetry()` writes `state: "AUTHENTICATING"` or `"DISCONNECTED"` to `system:whatsapp_telemetry`.
3. **Web Control Plane UI**:
   - The `/whatsapp` dashboard renders the Session State card with a prominent amber warning badge: **`AUTH_REQUIRED`**.
   - Action buttons for message dispatch remain disabled or blocked.
   - Runbook instructions alert the operator: *"Physical QR code scan required on Worker host via 'outreach session login'"*.
4. **Zero Automated Bypasses**: The worker never attempts simulated scans, headless scraping, or bypass algorithms.

---

## 9. Renderer Crash Handling & `UNKNOWN_OUTCOME` Semantics

### 9.1 Root Cause Analysis of Step 2A Renderer Crash
In Step 2A, after QR scanning, Chrome experienced a child renderer process crash (`SIGSEGV` or `SIGBUS` in child pid) during the high-volume burst synchronization of 24 MB+ of historical chats into IndexedDB without GPU hardware acceleration.
**Subsequent Verification**:
- In Step 2B (browser restart), loading historical chats was incremental; 0 crashes occurred.
- In Step 2C (full VM reboot), the session restored instantly (+13s) without crashes.
- The crash is an initialization phenomenon during bulk history hydration, not an ongoing steady-state issue.

### 9.2 Strict Preservation of Existing `UNKNOWN_OUTCOME` Semantics
The existing architecture (Phase 4 queue engine, Phase 5 CLI, Phase 7.4 analytics, Phase 7.6 operations) defines `UNKNOWN_OUTCOME` as a **distinct operational state**. It is **NOT** an ordinary permanent failure and must never be collapsed into generic failure semantics.

#### Core Semantics:
1. **Distinguishable from Confirmed Failure**:
   - A confirmed permanent failure (`status = "FAILED", error_type = "PERMANENT"`) occurs when a delivery definitively failed (e.g., `WhatsAppInvalidNumberError` where destination number is invalid or not registered).
   - An `UNKNOWN_OUTCOME` failure (`status = "FAILED", error_type = "UNKNOWN_OUTCOME"`) occurs when the send action was initiated (`click_send()` executed), but the confirmation checkmark could not be verified due to a browser crash, tab freeze, navigation interruption, or sudden network severance.
2. **A Post-Click Crash is NOT a Confirmed Delivery Failure**:
   - The WhatsApp Web client may have transmitted the message payload to the server before the local renderer crashed. Therefore, treating it as a confirmed delivery failure is factually false and corrupts analytics.
3. **Never Blindly Retried**:
   - Because the message may have reached the contact's phone, automatic retry is strictly forbidden to prevent duplicate outreach.
4. **Operator Inspection & Reconciliation Workflow**:
   - `UNKNOWN_OUTCOME` messages are quarantined from normal queue processing. Standard retry commands are blocked (`ExitCode.UNKNOWN_OUTCOME_BLOCKED` = 10).
   - They remain eligible for operator inspection and audited reconciliation per the existing Phase 5 CLI and Phase 7.4/7.6 Web UI:
     - **CLI**: `outreach queue reconcile` (lists quarantined items) and `outreach queue override <id> --verified-reason "<reason>"` (requires human operator rationale).
     - **Web UI**: `/queue/<id>` displays prominent warning pill, presents diagnostic snapshot, and requires explicit operator override.

```
                                  Message Dispatch Attempted
                                              |
                                              v
                              Did an Exception Occur?
                                     /                 \
                                   NO                   YES
                                   |                     |
                                   v                     v
                        Return SendResult(success=True)  |
                                                         v
                                  Was send_action_attempted == True?
                                  (i.e., Did click_send() execute?)
                                      /                     \
                                    NO                       YES
                                    |                         |
                                    v                         v
                       Temporary Failure Eligible    CRITICAL OPERATIONAL QUARANTINE:
                       - is_temporary_error = True   - Category: UNKNOWN_OUTCOME
                       - Message returned to queue   - error_type = "UNKNOWN_OUTCOME"
                       - Safe automatic retry        - NOT a confirmed delivery failure
                                                     - NEVER auto-retried
                                                     - Requires manual reconciliation
```

### 9.3 Self-Healing Recovery Protocol
If the browser renderer or ChromeDriver dies during loop execution:
1. At the next loop iteration, `provider.health_check()` probes `browser.is_alive()`.
2. Because the driver connection is severed, `is_alive()` returns `False`.
3. The runner invokes `session_manager.restart_session()`:
   - Gracefully terminates any dead ChromeDriver/Chrome zombie processes.
   - Relaunches Chrome for Testing with the persistent profile directory.
   - Probes `is_chat_ready(timeout=8.0)`.
   - If the chat restores cleanly without QR: resumes queue operations seamlessly.
   - If the chat fails or requests QR: marks provider `ERROR`, halts dispatching, and notifies operator via telemetry.

---

## 10. Phase 7.6 Operations Command Mapping

The worker daemon polls and processes operational commands issued from the Vercel Control Plane via the CAS protocol on `system:desired_whatsapp_command`.

### 10.1 Command Execution Mechanics

```
Control Plane (Vercel)                    PostgreSQL                         Worker (Oracle VM)
        |                                     |                                       |
        | 1. Submit Command                   |                                       |
        |---> Atomic INSERT / UPDATE -------->|                                       |
        |     (state: REQUESTED)              |                                       |
        |                                     | 2. Poll & CAS Claim                   |
        |                                     |<--- claim_command() ------------------|
        |                                     |     (state: CLAIMED, lease: 60s)      |
        |                                     |                                       |
        |                                     | 3. mark_executing()                   |
        |                                     |<--- (state: EXECUTING) ---------------|
        |                                     |                                       |
        |                                     | 4. Execute Chrome/Driver Operation    |
        |                                     |    (HEALTH_CHECK / RECONNECT / etc.)  |
        |                                     |                                       |
        |                                     | 5. complete_command() / fail_command()|
        |                                     |<--- (state: COMPLETED, result) -------|
        | 6. Poll Status (GET /status)        |                                       |
        |<--- Reads updated telemetry --------|                                       |
```

### 10.2 Detailed Action Mapping to Chrome for Testing + Xvfb Runtime

| Command Action | Worker Method Call | Concrete Runtime Execution on Oracle ARM64 | Result & State Updates |
| :--- | :--- | :--- | :--- |
| **`HEALTH_CHECK`** | `_execute_health_check()` | 1. Calls `provider.health_check()`.<br>2. Probes `browser.is_alive()`.<br>3. Checks absence of QR canvas (`!is_qr_present()`).<br>4. Checks presence of chat pane (`is_chat_ready()`). | Updates `system:whatsapp_telemetry`. If healthy: `COMPLETED` (`"HEALTHY (Chat interface verified responsive)"`). If unhealthy: `COMPLETED` (`"DEGRADED (Session not ready or QR scan required)"`). |
| **`RECONNECT`** | `_execute_reconnect()` | 1. Calls `session_manager.restart_session()`.<br>2. Calls `browser.quit()` to terminate existing Chrome/ChromeDriver.<br>3. Relaunches Chrome for Testing with persistent profile on `DISPLAY=:99`.<br>4. Waits up to 8s for `is_chat_ready()`. | If chat restores: `COMPLETED` (`"Session restored cleanly from cached profile"`).<br>If QR appears: `FAILED` (`"Browser restart succeeded but session requires QR scan"`). |
| **`DISCONNECT`** | `_execute_disconnect()` | 1. Calls `provider.disconnect()`.<br>2. Gracefully closes ChromeDriver and Chrome.<br>3. Transitions session state to `STOPPED`.<br>4. **Profile data is left completely intact on disk.** | Updates telemetry state to `STOPPED`. Marks command `COMPLETED` (`"Browser session disconnected gracefully"`). |
| **`LOGOUT`** | `_execute_logout()` | 1. Requires admin confirmation phrase `CONFIRM-LOGOUT`.<br>2. Calls `session_manager.shutdown()`.<br>3. If `clear_cache=True`: removes `/opt/whatsapp-outreach/data/whatsapp_session`. | Telemetry state becomes `DISCONNECTED`. Marks command `COMPLETED` (`"Controlled logout completed"`). |

### 10.3 Emergency Stop Interaction
- **Target Response**: < 500 ms propagation.
- **Execution Mechanism**: `EmergencyStop.is_active()` inspects `app_settings` (`key="emergency_stop"`).
- **Behavior**: The runner immediately halts message dequeuing and pauses queue operations (`lifecycle.transition_to(PAUSED)`).
- **Process Preservation**: Emergency Stop **never** kills the Chrome process or Xvfb. The browser session remains connected and idling, avoiding profile corruption or unnecessary session renegotiations.

---

## 11. System Health Model

The worker and session health are monitored via 7 discrete states:

```
+---------------+---------------------------------------------------------------------------------------+
| Health State  | Operational Definition & Criteria                                                     |
+---------------+---------------------------------------------------------------------------------------+
| STOPPED       | Runner process is inactive, or browser session has been gracefully disconnected.      |
| STARTING      | Runner daemon is launching, acquiring process lock, and verifying preflight checks.  |
| AUTH_REQUIRED | Persistent profile has no session cookies, or QR code canvas is rendered.            |
| CONNECTING    | Browser launched, navigating to web.whatsapp.com, awaiting chat pane / WebSocket.    |
| READY         | Authenticated, chat pane verified, active WebSocket connected, queue dispatch active. |
| DEGRADED      | WebSocket reconnecting, transient network latency, or non-fatal DOM search delay.     |
| FAILED        | Unrecoverable error, process lock contention, crash loop, or DB connectivity loss.   |
+---------------+---------------------------------------------------------------------------------------+
```

### Telemetry & Heartbeat Specifications:
1. **Heartbeat Cadence**: Every 15 seconds (`RUNNER_HEARTBEAT_SECONDS`), the active runner writes to `system:whatsapp_telemetry`:
   ```json
   {
     "state": "CONNECTED",
     "last_health_check": "2026-09-22T19:00:00.000000+00:00",
     "diagnostic_snippet": "URL=https://web.whatsapp.com/ | Title=(184) WhatsApp Business",
     "worker_id": "runner_camp1_68d1f2a4",
     "updated_at": "2026-09-22T19:00:00.000000+00:00"
   }
   ```
2. **Staleness Threshold**: The Vercel Control Plane considers worker telemetry stale if `updated_at` is older than 60 seconds.
3. **Circuit Breaker Threshold**: If consecutive dispatch failures reach `campaign.error_threshold` (default: 3), the Circuit Breaker trips (`campaign.status = 'PAUSED'`), suspending further dispatches while keeping the runner alive.

---

## 12. Security & Isolation Model

| Security Dimension | Implementation & Hardening Invariant |
| :--- | :--- |
| **Physical Plane Separation** | Vercel Control Plane contains 0 browser/Selenium code. 100% of automation resides on the Oracle Worker VM. |
| **X11 Display Hardening** | `xvfb.service` runs with `-nolisten tcp`. Display `:99` communicates solely through Unix domain sockets (`/tmp/.X11-unix/X99`). No network port is opened. |
| **Remote Operator Access** | Operators access the GUI strictly via encrypted SSH port forwarding: `ssh -i <key> -L 5900:localhost:5900 ubuntu@84.13.139.20` paired with `x11vnc -localhost`. Port 5900 is **never** opened in Oracle Security Lists or UFW. |
| **Chrome Debugging Port** | Launched with `--remote-debugging-port=0` (ephemeral local port assigned by OS) or disabled. Never exposed to external interfaces. |
| **Profile File Permissions** | `/opt/whatsapp-outreach/data/whatsapp_session` is locked to mode `700` (`drwx------`) owned by `ubuntu:ubuntu`. |
| **Environment Permissions** | `/opt/whatsapp-outreach/.env` is locked to mode `600` (`-rw-------`) owned by `ubuntu:ubuntu`. |
| **Path Sanitization** | `WhatsAppHostDiagnosticsDTO` strips all host filesystem paths before sending to API or UI; exposes only categorical indicators (`profile_storage_state`, `profile_writable`). |

---

## 13. Observability, Logging & PII Redaction

### 13.1 Log Aggregation & Formats
1. **Systemd Journal**: `journalctl -u outreach-runner.service -f` captures stdout/stderr, startup milestones, and crash traces.
2. **Rotating File Logs**:
   - `/opt/whatsapp-outreach/logs/app.log`: Standard log output rotated at 10 MB (up to 5 backups).
   - `/opt/whatsapp-outreach/logs/app.json.log`: Structured JSON logs for automated indexing.

### 13.2 PII Protection & Data Sanitization
- **Phone Numbers**: All logs redact recipient telephone numbers using standard masking:
  `+201012345678` $\longrightarrow$ `+2010****5678`.
- **Message Content**: Message body texts are truncated to a maximum of 20 characters in debug logs, and omitted entirely from info/audit logs.
- **Authentication Artifacts**: Raw QR image bytes and raw base64 canvas buffers are **never** written to database tables or file logs.

---

## 14. Comprehensive Test & Verification Plan

```
+-----------------------------------------------------------------------------------+
|                            4-Stage Verification Plan                              |
+-----------------------------------------------------------------------------------+
|  Stage 1: Pre-Deployment Test Suite (Workstation)                                  |
|  - Unit tests: mock provider, session manager state machine                       |
|  - Boundary tests: AST scan asserting 0 Selenium imports in app/web/               |
|  - Command service tests: CAS atomic claim, single in-flight 409 conflict         |
+-----------------------------------------------------------------------------------+
                                      |
                                      v
+-----------------------------------------------------------------------------------+
|  Stage 2: Worker Preflight & Environment Audit (Oracle VM)                         |
|  - python -m app.cli.main preflight --strict                                      |
|  - Verify Chrome binary (/opt/google/chrome-for-testing/chrome --version)          |
|  - Verify ChromeDriver binary (/usr/local/bin/chromedriver --version)             |
|  - Verify Xvfb display socket (/tmp/.X11-unix/X99) via xdpyinfo                   |
+-----------------------------------------------------------------------------------+
                                      |
                                      v
+-----------------------------------------------------------------------------------+
|  Stage 3: Production Profile 1-Time Manual Pairing (Oracle VM)                    |
|  - Operator scans QR via outreach session login with DISPLAY=:99                  |
|  - Verify chat pane rendered (is_chat_ready() == True)                            |
|  - Verify session persistence across controlled browser quit & restart            |
+-----------------------------------------------------------------------------------+
                                      |
                                      v
+-----------------------------------------------------------------------------------+
|  Stage 4: Systemd Service & CAS Operations Verification (Oracle VM & Vercel)      |
|  - Start outreach-runner.service via systemctl                                     |
|  - Verify journalctl startup milestones and 15s heartbeat publication             |
|  - Submit HEALTH_CHECK via Vercel UI -> verify CAS completion in < 5s             |
|  - Submit RECONNECT via Vercel UI -> verify browser restart in < 15s              |
|  - Submit Controlled DISCONNECT -> verify clean browser shutdown                  |
+-----------------------------------------------------------------------------------+
```

---

## 15. The `PRODUCTION_BROWSER_READY` Gate & Staged Rollout

### 15.1 The `PRODUCTION_BROWSER_READY` Gate Definition
Before normal production runner queue execution is permitted to process eligible campaign messages, the worker must satisfy the **`PRODUCTION_BROWSER_READY`** gate.

**Mandatory Conditions (All Must Pass)**:
1. **Chrome for Testing Executable**: Successfully launches without sandbox/execvp errors.
2. **Xvfb Available**: Virtual framebuffer on `DISPLAY=:99` is active and responsive to `xdpyinfo`.
3. **Clean Production Profile Loaded**: User-data-dir at `/opt/whatsapp-outreach/data/whatsapp_session` mounts cleanly with lock exclusivity.
4. **WhatsApp Authenticated**: Document title indicates authenticated account (e.g., `WhatsApp Business`), chat search bar and chat pane are present.
5. **No QR Required**: `is_qr_present()` evaluates to `False`.
6. **WebSocket Healthy**: Active connection established (`wss://web.whatsapp.com/ws/chat` returning HTTP 101 Switching Protocols).
7. **Readiness / Preflight Passes**: Full execution of `python -m app.cli.main preflight --strict` exits with code 0 (`SUCCESS`).
8. **No Renderer Crash During Observation Window**: Browser remains fully responsive over a minimum 120-second observation window.

**Only after `PRODUCTION_BROWSER_READY` is confirmed may campaign queue processing begin.**

### 15.2 Staged Rollout Schedule

| Stage | Action / Gate | Verification Criteria | Rollback Trigger |
| :---: | :--- | :--- | :--- |
| **0** | **Design Approval** | Human operator reviews and approves this design document. | Any unaddressed architectural ambiguity. |
| **1** | **Repository Configuration** | Update `deploy/systemd/outreach-runner.service` and `deploy/worker/env.worker.example`. Commit & push. | Syntax or lint failure in git CI. |
| **2** | **Worker VM Synchronization** | Pull latest git master onto `/opt/whatsapp-outreach/app`. Verify Python venv dependencies. | Merge conflict or dependency discrepancy. |
| **3** | **Production Profile Initialization** | Archive old `/opt/whatsapp-outreach/data/whatsapp_session`. Create fresh directory with mode 700. | Inability to create clean directory. |
| **4** | **1-Time Operator Authentication** | Run `outreach session login` under `DISPLAY=:99`. Operator scans QR code. | Scan failure or QR timeout. |
| **5** | **Offline Persistence Audit** | Run `outreach session status`. Verify `is_chat_ready()` passes without QR prompt. | QR reappearance. |
| **6** | **`PRODUCTION_BROWSER_READY` Gate** | Confirm all 8 conditions of Section 15.1 pass on the production profile. | Any gate condition failure. |
| **7** | **Systemd Service Activation** | Install unit file, `systemctl daemon-reload`, enable and start `outreach-runner.service`. | Daemon start failure, preflight failure, or crash loop. |
| **8** | **Control Plane Verification** | Monitor `/whatsapp` in Vercel UI. Verify telemetry heartbeat updates every 15s. Submit test `HEALTH_CHECK`. | Heartbeat missing > 60s, or CAS command timeout. |
| **9** | **Canary Campaign Execution** | Authorize Campaign 1 queue dispatch. Monitor send confirmation checkmark on first message. | Any `UNKNOWN_OUTCOME` or browser freeze. |

---

## 16. Deterministic Rollback Plan

If any regression occurs during rollout:

1. **Immediate Service Deactivation**:
   ```bash
   sudo systemctl stop outreach-runner.service
   sudo systemctl disable outreach-runner.service
   ```
2. **Emergency Stop Activation**:
   Issue database Emergency Stop to guarantee no runner process pops messages:
   ```bash
   python -m app.cli.main emergency-stop --reason "Rollback initiated during Phase 7.7-B"
   ```
3. **Profile Restoration**:
   If the clean production profile experiences issues:
   ```bash
   rm -rf /opt/whatsapp-outreach/data/whatsapp_session
   cp -rp /opt/whatsapp-outreach/data/whatsapp_session_backup_* /opt/whatsapp-outreach/data/whatsapp_session
   ```
4. **Benchmark Reference**:
   The Step 2 test profile at `/home/ubuntu/cft_poc/pairing_test_profile` remains pristine and can be inspected to diagnose environment variances.
5. **Data Integrity Guarantee**:
   Zero customer messages are lost or duplicated. Messages in `CLAIMED` state revert to `PENDING` upon startup lease reconciliation (`recover_stale_leases()`).

---

## 17. Risks & Mitigations

| Risk | Impact | Probability | Concrete Mitigation |
| :--- | :--- | :---: | :--- |
| **Long-Running Memory Creep** | Chrome memory growth over days leading to VM OOM. | Low (VM has 12 GB RAM) | Systemd memory monitoring; `session_manager.restart_session()` can be invoked periodically via scheduled `RECONNECT` command during campaign maintenance windows. |
| **WhatsApp Web DOM Selector Mutation** | Selector mismatch leading to failure finding message compose box or send button. | Low | Multi-fallback CSS selector list in `WhatsAppSelectors`; `WhatsAppErrorMapper` captures full DOM diagnostic snippet and raises classified error without hanging. |
| **Transient Network / WebSocket Drops** | Browser disconnects from WhatsApp servers while idle. | Medium | WhatsApp Web automatically attempts WebSocket reconnection. `session_manager.check_health()` detects drop; runner halts dispatch until restored. |
| **Duplicate Runner Spawning** | Multiple processes fighting over the same Chrome profile. | Zero | Enforced by OS `fcntl.flock` on `/opt/whatsapp-outreach/data/runner.lock` and active PID validation in `ProcessLock`. |
| **Accidental Headless Flag Activation** | Operator setting `WHATSAPP_HEADLESS=True` triggering WhatsApp anti-headless block screen. | Low | Preflight checks can validate `WHATSAPP_HEADLESS=False` when running on Linux ARM64; documentation strictly mandates `False`. |

---

## 18. Proposed File Changes

### 18.1 File Changes in Repository (Minimal & Non-Invasive)

#### 1. `deploy/systemd/outreach-runner.service`
```diff
--- a/deploy/systemd/outreach-runner.service
+++ b/deploy/systemd/outreach-runner.service
@@ -1,18 +1,23 @@
 [Unit]
 Description=WhatsApp Outreach Automation Production Runner Daemon
-After=network.target
+After=network.target xvfb.service
+Requires=xvfb.service
 
 [Service]
 Type=simple
-User=outreach
-Group=outreach
-WorkingDirectory=/opt/whatsapp-outreach
+User=ubuntu
+Group=ubuntu
+WorkingDirectory=/opt/whatsapp-outreach/app
 EnvironmentFile=/opt/whatsapp-outreach/.env
-ExecStart=/opt/whatsapp-outreach/.venv/bin/python -m app.cli.main runner start --campaign-id 1
-Restart=always
+Environment=DISPLAY=:99
+ExecStartPre=/bin/sh -c 'for i in $(seq 1 30); do /usr/bin/xdpyinfo -display :99 >/dev/null 2>&1 && exit 0; sleep 0.2; done; echo "ERROR: Xvfb :99 display not responding" >&2; exit 1'
+ExecStart=/opt/whatsapp-outreach/app/.venv/bin/python -m app.cli.main runner start --campaign-id 1
+Restart=on-failure
 RestartSec=10
 KillSignal=SIGTERM
 TimeoutStopSec=30
 StandardOutput=journal
 StandardError=journal
 
 [Install]
 WantedBy=multi-user.target
```

#### 2. `deploy/worker/env.worker.example`
```diff
--- a/deploy/worker/env.worker.example
+++ b/deploy/worker/env.worker.example
@@ -19,16 +19,13 @@
 # WhatsApp Web Automation Settings
 WHATSAPP_SESSION_PATH=/opt/whatsapp-outreach/data/whatsapp_session
-WHATSAPP_HEADLESS=True
+WHATSAPP_HEADLESS=False
 WHATSAPP_BROWSER_TIMEOUT=30
 WHATSAPP_PAGE_LOAD_TIMEOUT=45
 WHATSAPP_QR_TIMEOUT=120
 # Browser and Driver Binaries:
-# - Standard x86_64 Ubuntu / Google Chrome:
-#   WHATSAPP_CHROME_BINARY=/usr/bin/google-chrome
-#   WHATSAPP_CHROMEDRIVER_PATH=/usr/bin/chromedriver
-# - Oracle Cloud Always Free ARM64 (Ubuntu 24.04 Snap Chromium):
-#   NOTE: Do NOT use /snap/bin/chromium wrapper script directly (fails with execvp error).
-#   Use the stable 'current' symlink pointing to the raw ELF binary:
-#   WHATSAPP_CHROME_BINARY=/snap/chromium/current/usr/lib/chromium-browser/chrome
-#   WHATSAPP_CHROMEDRIVER_PATH=/usr/bin/chromedriver
+# - Oracle Cloud Always Free ARM64 (Google Chrome for Testing Stable):
+WHATSAPP_CHROME_BINARY=/opt/google/chrome-for-testing/chrome
+WHATSAPP_CHROMEDRIVER_PATH=/usr/local/bin/chromedriver
```

### 18.2 Application Source Code Changes
**ZERO changes required in `app/`.**  
The existing codebase (`ProductionRunner`, `WhatsAppWebProvider`, `WhatsAppBrowser`, `WhatsAppSessionManager`, `preflight.py`, and `settings.py`) already contains 100% of the wiring for custom Chrome binary locations, custom ChromeDriver paths, headless toggling, and X11 display binding.

---

## 19. Database Migration Assessment

**Database Migrations Required**: **EXACTLY 0**

### Technical Justification:
1. **Existing Tables**: All required tables (`campaigns`, `contacts`, `campaign_contacts`, `messages`, `app_settings`, `audit_logs`) already exist and are fully populated.
2. **Command Protocol Storage**: The Phase 7.6 CAS command protocol utilizes dynamic rows in `app_settings` (`key="system:desired_whatsapp_command"` and `key="system:whatsapp_telemetry"`).
3. **Audit Trail**: Operational events are logged directly to the existing `audit_logs` table without schema adjustments.
4. **No Schema Alterations**: No new columns, indexes, foreign keys, or enum types are required.

---

## 20. Explicit Implementation Approval Checklist

Before implementing Step 4 (Production Deployment & Canary Verification), the operator must review and confirm each of the following gates:

- [ ] **UNKNOWN_OUTCOME Semantics Confirmed**: Confirm `UNKNOWN_OUTCOME` is quarantined as a distinct operational state, never collapsed into confirmed delivery failure, never blindly retried, and preserved for operator reconciliation.
- [ ] **Systemd User & Ownership Confirmed**: Confirm `User=ubuntu` matches the Oracle VM filesystem audit (`ubuntu:ubuntu`), and that the template's `User=outreach` is safely superseded.
- [ ] **Xvfb Ordering & Readiness Probe Confirmed**: Confirm both `After=network.target xvfb.service` and `Requires=xvfb.service` are set, supplemented by `ExecStartPre` with `/usr/bin/xdpyinfo -display :99`.
- [ ] **Benchmark Profile Preservation Confirmed**: Confirm `/home/ubuntu/cft_poc/pairing_test_profile` remains untouched and is NOT copied into production.
- [ ] **9-Step Clean Production Profile Cutover Confirmed**: Confirm fresh independent profile at `/opt/whatsapp-outreach/data/whatsapp_session` with single manual operator QR pairing.
- [ ] **`PRODUCTION_BROWSER_READY` Gate Confirmed**: Confirm all 8 conditions of Section 15.1 must pass before runner queue execution becomes eligible.
- [ ] **Zero Cost Target Confirmed**: Confirm $0/month infrastructure (Oracle Always Free, Google Chrome for Testing builds, zero paid proxies/APIs).
- [ ] **Zero Evasion Invariant Confirmed**: Confirm standard browser flags only; zero User-Agent spoofing, zero fingerprint injection, zero stealth scripts.
- [ ] **Database Schema Invariant Confirmed**: Confirm exactly 0 database migrations are required.

---

```
================================================================================
DESIGN STATUS: READY_FOR_APPROVAL
================================================================================
```

### Concise Summary Lists

#### Final Decisions Summary:
1. **`UNKNOWN_OUTCOME` Semantics**: Maintained strictly as a distinct operational quarantine category. A post-click crash is **not** treated as a confirmed delivery failure (`error_type != "PERMANENT"`). It is barred from automatic retries and preserved for audited operator inspection/reconciliation via CLI (`outreach queue reconcile / override`) and Web UI.
2. **Systemd User**: Formally confirmed as `User=ubuntu` and `Group=ubuntu`. Audit verified all `/opt/whatsapp-outreach` directories, `.env`, virtual environment, and Xvfb display socket are owned by `ubuntu:ubuntu`. The Unix user `outreach` does not exist on the VM.
3. **Xvfb Ordering & Readiness**: Set `After=network.target xvfb.service` and `Requires=xvfb.service`, reinforced by an `ExecStartPre=` loop probing `/usr/bin/xdpyinfo -display :99` (up to 6s) to ensure the X11 server is actively accepting connections before `ProductionRunner` starts.
4. **Production Profile Cutover**: Benchmark profile `/home/ubuntu/cft_poc/pairing_test_profile` is preserved untouched. Production profile at `/opt/whatsapp-outreach/data/whatsapp_session` is initialized clean, followed by a strict 9-step cutover with ONE manual operator QR scan. Zero auto-pairing in the runner daemon.
5. **Production Readiness Gate (`PRODUCTION_BROWSER_READY`)**: Established as a mandatory hard gate requiring 8 verification conditions (executable starts, Xvfb available, profile loaded, authenticated title, no QR, WebSocket active, preflight passed, 120s crash-free observation) before campaign queue polling may commence.
6. **DB Migration Requirement**: **EXACTLY 0** migrations. Existing schema and dynamic `app_settings` rows fully support all operations.
