# Phase 7.7-B — Step 7: Control Plane Handshake Design
## Vercel ↔ Supabase ↔ Oracle ARM64 Worker

**Document Status**: `DESIGN STATUS: READY_FOR_REVIEW`  
**Phase**: 7.7-B Step 7  
**Date**: September 23, 2026  
**Scope**: DESIGN ONLY — No code changes, no migrations, no commits.

---

## 1. Current Architecture

### 1.1 Deployed Components

| Component | Platform | Role |
| :--- | :--- | :--- |
| Web Control Plane | Vercel (serverless, Node.js / FastAPI) | UI, REST API, RBAC, command submission |
| Database | Supabase (PostgreSQL 15, managed) | Authoritative state, audit log, command protocol, heartbeat |
| Execution Worker | Oracle Cloud A1 ARM64 (`84.13.139.20`) | Chrome for Testing, Xvfb, WhatsApp session, ProductionRunner |

### 1.2 Communication Path (Current)

```
Operator Browser
      │
      ▼
Vercel Control Plane (FastAPI, HTTPS)
      │  write to app_settings
      ▼
Supabase PostgreSQL
      │  poll SELECT app_settings WHERE key = 'system:whatsapp_command:active'
      ▼
Oracle ARM64 Worker (ProductionRunner poll loop, 5s interval)
      │  execute command via WhatsAppCommandHandler
      ▼
Chrome for Testing + WhatsApp Web Provider
```

### 1.3 Existing AppSetting Keys (Phase 7.6)

| Key | Owner | Purpose |
| :--- | :--- | :--- |
| `system:whatsapp_command:active` | Vercel writes, Worker claims | In-flight command CAS slot |
| `system:whatsapp_command:last_completed` | Worker writes | Terminal command outcome |
| `system:whatsapp_telemetry` | Worker writes | Live session/health telemetry |
| `system:active_runner` | Worker writes (ProcessLock) | Heartbeat: `{pid, worker_id, campaign_id, started_at, last_heartbeat}` |
| `system:desired_runner_state` | Vercel writes | Desired state: `RUNNING` or `STOPPED` |
| `system:desired_runner_campaign_id` | Vercel writes | Target campaign for runner |

---

## 2. Existing Phase 7.6 Protocol

### 2.1 Command State Machine

```
REQUESTED  (set by Vercel, polled by Worker)
    │
    │  Worker claims via SELECT ... FOR UPDATE (CAS), version++
    ▼
CLAIMED    (set by Worker, lease_expires_at = now + 60s)
    │
    │  Worker transitions immediately before execution
    ▼
EXECUTING  (set by Worker, executed_at = now)
    │
    ├──[success]──► COMPLETED  (result written, audit log emitted, slot freed)
    └──[error]────► FAILED     (error_message written, audit log emitted, slot freed)
```

**Invariants:**
- Single in-flight slot (HTTP 409 Conflict if another REQUESTED/CLAIMED/EXECUTING exists)
- 60-second lease with automatic orphan recovery on expiry
- 120-second REQUESTED timeout (unclaimed command treated as orphaned)
- Monotonic version counter incremented on each state transition
- Immutable audit log row per lifecycle event

### 2.2 Implemented Command Actions

| Action | RBAC | Description |
| :--- | :--- | :--- |
| `HEALTH_CHECK` | OPERATOR+ | Probes browser health via `provider.health_check()` |
| `RECONNECT` | OPERATOR+ | Restarts browser session from cached profile |
| `DISCONNECT` | OPERATOR+ | Graceful browser session shutdown |
| `LOGOUT` | ADMIN+ | Session unlink + optional profile cache clear |

### 2.3 Telemetry

`WhatsAppCommandHandler.publish_telemetry()` writes to `system:whatsapp_telemetry` on every heartbeat cycle (default: 15s). Payload:
```json
{
  "state": "CONNECTED",
  "last_health_check": "2026-09-23T...",
  "diagnostic_snippet": "...",
  "worker_id": "runner_camp1_abc123",
  "updated_at": "2026-09-23T..."
}
```

### 2.4 Remote Coordination Mode

`RUNNER_REMOTE_COORDINATION=True` (or `VERCEL=1`) activates database-mediated desired state. In this mode:
- `RunnerControlService.start_runner()` writes `system:desired_runner_state = RUNNING` instead of `subprocess.Popen`.
- `RunnerControlService.stop_runner()` writes `system:desired_runner_state = STOPPED` and emits SIGTERM-equivalent through DB signal only.
- The ProductionRunner main loop polls `system:desired_runner_state` on every iteration and stops cleanly when it sees `STOPPED`.

This mechanism is **already partially implemented** and constitutes the foundation of the Step 7 handshake.

---

## 3. Proposed Worker Identity (Question A)

### 3.1 Current Situation

The worker currently generates a transient ID on each runner start:
```python
self.worker_id = worker_id or f"runner_camp{campaign_id}_{hex(int(time.time()))[2:]}"
```
This is ephemeral. No persistent, stable worker identity exists.

### 3.2 Proposed Worker Identity Model

Introduce a **stable persistent worker identity** stored in the worker's `.env` file and published into Supabase on heartbeat.

**New `WORKER_INSTANCE_ID` env var** (set once during deployment, never rotated):
```
WORKER_INSTANCE_ID=oracle-arm64-worker-01
```

This ID is:
- Human-readable, operator-assigned
- Stable across reboots, upgrades, and runner restarts
- Not a security secret (it is only a label; authentication is handled separately)
- Published in heartbeat as a correlation anchor

**Worker identity record published to `system:worker_identity`:**
```json
{
  "instance_id": "oracle-arm64-worker-01",
  "environment": "production",
  "platform": "Oracle Cloud Always Free A1 Flex",
  "arch": "aarch64",
  "os": "Ubuntu 24.04 LTS",
  "python_version": "3.12.3",
  "chrome_binary": "/opt/google/chrome-for-testing/chrome",
  "chrome_version": "153.0.8010.52",
  "chromedriver_version": "153.0.8010.52",
  "xvfb_display": ":99",
  "capabilities": ["whatsapp_web_automation", "chrome_for_testing", "xvfb_display"],
  "app_version": "Phase 7.7-B",
  "registered_at": "2026-09-23T...",
  "last_seen": "2026-09-23T..."
}
```

**Exposed to Vercel dashboard:** `instance_id`, `environment`, `arch`, `chrome_version`, `capabilities`, `app_version`, `last_seen`.

**Never exposed:** filesystem paths beyond what preflight reports, session data, credentials, `.env` values.

---

## 4. Heartbeat Design (Question B)

### 4.1 Existing Heartbeat

`ProcessLock.update_heartbeat()` already writes to:
1. **OS lock file** (`/opt/whatsapp-outreach/data/runner.lock`) — authoritative on-host
2. **`system:active_runner`** AppSetting — cross-plane visibility

This runs every `RUNNER_HEARTBEAT_SECONDS` (default: 15s) inside the ProductionRunner main loop.

### 4.2 Proposed Heartbeat Extension

The existing heartbeat covers the **runner process**. We extend it to also cover **worker infrastructure health** independently of whether the runner is active. This enables the dashboard to show infrastructure health even when the runner is stopped (waiting for command or in standby).

**New heartbeat key: `system:worker_heartbeat`**

Published by a lightweight heartbeat loop embedded in the runner that fires even when the runner is in `IDLE` or `PAUSED` state:

```json
{
  "instance_id": "oracle-arm64-worker-01",
  "last_seen": "2026-09-23T22:00:00+00:00",
  "runner_state": "IDLE",
  "campaign_id": 1,
  "worker_id": "runner_camp1_abc123",
  "xvfb_healthy": true,
  "chrome_reachable": true,
  "session_profile_present": true,
  "uptime_seconds": 3600,
  "iteration_count": 720
}
```

**Heartbeat Semantics:**

| Parameter | Value |
| :--- | :--- |
| Heartbeat source | `ProductionRunner` main loop (existing heartbeat slot extended) |
| Heartbeat interval | 15s (`RUNNER_HEARTBEAT_SECONDS`) |
| `last_seen` timezone | UTC, ISO 8601 |
| Stale threshold | 60s (no heartbeat ≡ worker offline or unresponsive) |
| Degraded threshold | 30s (heartbeat delayed, monitor with caution) |

**State Transitions After Runner Restart:**
- On restart, worker immediately publishes a heartbeat with `runner_state: STARTING`
- `system:worker_identity` updated with new `registered_at` timestamp
- Stale command orphan recovery executes before first heartbeat of new run

**If Vercel Is Temporarily Unavailable:**
- Worker continues operating. It reads desired state from Supabase, not from Vercel directly.
- Heartbeats continue being written to Supabase.
- No impact on message dispatch.

**If Supabase Is Temporarily Unavailable:**
- Worker operates in **degraded mode**: continues processing queue messages already in memory.
- Heartbeat writes fail silently with `logger.warning`. No crash, no loop break.
- Command polling returns `None` (no new commands accepted during outage).
- Vercel dashboard will show worker as `STALE` after 60s without heartbeat update.

---

## 5. Health Model (Question C)

The existing `WhatsAppWebService.get_status()` collapses infrastructure and session health into a single state. This design separates them into **five distinct layers**.

### 5.1 Layer 1: Infrastructure Health

Sourced from: `system:worker_heartbeat`

| Field | Description | Type |
| :--- | :--- | :--- |
| `xvfb_healthy` | Xvfb `:99` responds to xdpyinfo | bool |
| `chrome_reachable` | Chrome binary present at configured path | bool |
| `chromedriver_reachable` | ChromeDriver binary present at configured path | bool |
| `last_seen` | Last heartbeat timestamp | ISO 8601 UTC |
| `heartbeat_age_seconds` | Seconds since last heartbeat | float |
| `uptime_seconds` | Runner process uptime | float |
| `infrastructure_state` | `HEALTHY` / `DEGRADED` / `OFFLINE` | str |

**Infrastructure states:**
- `HEALTHY`: `heartbeat_age_seconds < 30`, xvfb_healthy, chrome_reachable
- `DEGRADED`: `30 ≤ heartbeat_age_seconds < 60`, OR any binary check failing
- `OFFLINE`: `heartbeat_age_seconds ≥ 60` or no heartbeat ever received

### 5.2 Layer 2: Browser Health

Sourced from: `system:whatsapp_telemetry` (existing)

| Field | Description |
| :--- | :--- |
| `browser_state` | Session manager state string (`CONNECTED`, `DISCONNECTED`, `AUTHENTICATING`, `SESSION_LOST`, `ERROR`) |
| `last_health_check_at` | Timestamp of last `health_check()` call |
| `last_health_check_age_seconds` | Age of last health probe |
| `diagnostic_snippet` | Sanitized DOM diagnostic fragment |

### 5.3 Layer 3: WhatsApp Session Health

Sourced from: `system:whatsapp_telemetry` (existing) + profile inspection

| Field | Description |
| :--- | :--- |
| `session_authenticated` | `is_chat_ready()` last returned True |
| `qr_required` | QR code detected on last check |
| `websocket_healthy` | WebSocket 101 active |
| `profile_storage_state` | `PRESENT` / `EMPTY` / `MISSING` |
| `profile_size_bytes` | Profile directory size |

### 5.4 Layer 4: Runner Health

Sourced from: `system:active_runner` + `system:desired_runner_state` (existing)

| Field | Description |
| :--- | :--- |
| `runner_state` | `STOPPED` / `STARTING` / `AUTHENTICATING` / `RUNNING` / `IDLE` / `PAUSED` / `STOPPING` / `FAILED` |
| `runner_pid` | OS PID (not exposed to browser) |
| `runner_worker_id` | Worker ID string |
| `runner_campaign_id` | Active campaign |
| `runner_is_alive` | PID alive check result |
| `desired_state` | `RUNNING` or `STOPPED` |
| `heartbeat_age_seconds` | Seconds since last runner heartbeat |

### 5.5 Layer 5: Queue Health

Sourced from: `messages` table (existing)

Existing `queue status` CLI already provides full breakdown. The dashboard reads from the database directly. No new fields required.

---

## 6. Command Delivery Design (Question D)

The existing Phase 7.6 protocol is **fully preserved**. No structural change to the command state machine.

### 6.1 Sequence Diagram

```
Operator ──► Vercel API (POST /api/v1/whatsapp/health-check)
                  │
                  │ require_operator, verify_csrf
                  │
                  ▼
         WhatsAppCommandService.submit_command()
                  │
                  │ SET app_settings[system:whatsapp_command:active]
                  │   = {request_id, action, status: REQUESTED, version: 1, ...}
                  │
                  ▼
         Vercel returns {request_id, status: REQUESTED} to operator
                  │
                  │ ... (next Oracle poll cycle, ≤ 5s) ...
                  ▼
Oracle ProductionRunner.main_loop()
        ↳ WhatsAppCommandHandler.poll_and_execute()
             ↳ WhatsAppCommandService.claim_command()
                  │ SELECT ... FOR UPDATE (atomic CAS)
                  │ status: REQUESTED → CLAIMED, version++, lease_expires_at set
                  │
                  ↓
             mark_executing()  [status: CLAIMING → EXECUTING]
                  │
                  ↓
             _execute_health_check() / _execute_reconnect() / etc.
                  │
                  ├──[success]──► complete_command()
                  │               status = COMPLETED, last_completed updated
                  └──[failure]──► fail_command()
                                  status = FAILED, error_message written
```

### 6.2 Request Correlation

Every command carries:

| Field | Format | Owner |
| :--- | :--- | :--- |
| `request_id` | `req_wa_{unix_ts}_{8hex}` | Vercel (on submit) |
| `requested_by` | Username string | Vercel |
| `requested_by_id` | User UUID | Vercel |
| `claimed_by` | `worker_id` string | Worker |
| `version` | Monotonic int | Both (incremented on each transition) |

### 6.3 Lease & Orphan Recovery

| Scenario | Recovery |
| :--- | :--- |
| CLAIMED/EXECUTING lease expires (> 60s) | `recover_stale_or_orphaned_command()` marks FAILED on next submit or runner startup |
| REQUESTED not claimed in > 120s | Same recovery |
| Worker crashes mid-EXECUTING | On next runner startup, orphan recovery fires before first poll |
| Vercel polls for command status | Reads `get_command_by_id()` (active → last_completed → audit log fallback) |

### 6.4 No Semantic Change to UNKNOWN_OUTCOME

UNKNOWN_OUTCOME is a **message queue** state (table `messages`, column `status`), completely separate from the WhatsApp command protocol. It is not affected by this design.

---

## 7. Command Types & RBAC (Question E)

### 7.1 Existing Commands (Retained Unchanged)

| Action | Route | RBAC | Effect |
| :--- | :--- | :--- | :--- |
| `HEALTH_CHECK` | `POST /api/v1/whatsapp/health-check` | OPERATOR+ | Probes browser health |
| `RECONNECT` | `POST /api/v1/whatsapp/reconnect` | OPERATOR+ | Restarts browser session from cached profile |
| `DISCONNECT` | `POST /api/v1/whatsapp/disconnect` | OPERATOR+ | Graceful browser session shutdown |
| `LOGOUT` | `POST /api/v1/whatsapp/logout` | ADMIN+ | Unlinks session + optional cache clear |

### 7.2 Proposed New Command: `PREFLIGHT`

This command allows the operator to trigger a remote preflight check from the dashboard without SSHing into the VM.

| Action | Route | RBAC | Effect |
| :--- | :--- | :--- | :--- |
| `PREFLIGHT` | `POST /api/v1/whatsapp/preflight` | OPERATOR+ | Runs `run_preflight()` on Oracle, writes result to `system:last_preflight_result` |

**Existing file**: `app/runner/whatsapp_command_handler.py` — add `_execute_preflight()` handler.  
**Existing file**: `app/readiness/preflight.py` — already implements `run_preflight()`, just call it.  
**No new endpoints** except this one route.

### 7.3 Prohibited Commands (Explicitly)

The following are not designed, not implemented, and not permitted:

- Any SSH execution command
- Any raw shell command (`eval`, `exec`, `subprocess`)
- Any arbitrary Python execution
- Any `kill -9` or process force-kill
- Any browser profile copy or export
- Any WhatsApp session data export
- Any send-message command from Vercel control plane

### 7.4 RBAC Summary

| Role | Read Status | Submit Command | Admin Commands |
| :--- | :--- | :--- | :--- |
| VIEWER | ✓ | ✗ | ✗ |
| OPERATOR | ✓ | ✓ (HEALTH_CHECK, RECONNECT, DISCONNECT, PREFLIGHT) | ✗ |
| ADMIN | ✓ | ✓ all above | ✓ (LOGOUT, clear-stale) |
| OWNER | ✓ | ✓ all above | ✓ all |

---

## 8. Polling vs Realtime Evaluation (Question F)

### 8.1 Option 1: Oracle Worker Polls Supabase

The worker's main loop polls `app_settings` for commands on every iteration (existing implementation, 5s interval).

**Advantages:**
- Already implemented and verified working
- Crash recovery is natural: worker re-polls on restart
- No long-lived connections (compatible with PostgreSQL connection pooling)
- Vercel compatible: Vercel writes to database, no long-lived connection needed
- Oracle compatible: outbound-only; no inbound ports needed
- Duplicate prevention: CAS SELECT FOR UPDATE prevents double claims
- Simple deployment: no additional services

**Disadvantages:**
- Command execution latency is up to `RUNNER_POLL_INTERVAL_SECONDS` (5s)
- Polling generates minor database load (mitigated by Supabase's managed infrastructure)

### 8.2 Option 2: Supabase Realtime Push

Use Supabase Realtime (PostgreSQL logical replication events via WebSocket) to push change notifications to the Oracle worker.

**Advantages:**
- Near-instant command delivery (< 500ms)

**Disadvantages:**
- Requires persistent WebSocket connection from Oracle worker to Supabase Realtime endpoint
- Supabase Realtime requires Supabase client library or custom WebSocket implementation
- Connection drops require reconnect logic, backoff, and re-subscribe
- Significantly more operational complexity
- Duplicate events possible on reconnect; duplicate prevention logic required in addition to CAS
- Current architecture has no Supabase client dependency (uses raw SQLAlchemy + psycopg2)
- Adds a new class of failure (realtime subscription loss without awareness)

### 8.3 Decision: **Polling (Option 1)**

**Rationale:**
1. The existing polling architecture is fully operational and verified.
2. 5-second command latency is operationally acceptable for the defined command set (health check, reconnect, disconnect, logout). None require sub-second response.
3. Zero new dependencies.
4. Crash recovery is deterministic and already proven.
5. Realtime would add significant complexity for marginal latency benefit.

> **Architectural constraint:** Supabase Realtime can be adopted in a future phase if sub-second command delivery becomes a genuine requirement. The command protocol is designed to accommodate either delivery model.

---

## 9. Authentication Model (Question G)

### 9.1 Current Authentication Landscape

| Plane | Mechanism | Secret Type |
| :--- | :--- | :--- |
| Browser → Vercel | Cookie-based session (`WEB_SESSION_COOKIE_NAME`) | HMAC-signed session token |
| Vercel → Supabase | `DATABASE_URL` (env var, server-only) | PostgreSQL credentials |
| Oracle → Supabase | `DATABASE_URL` (env var, server-only, in `.env`) | PostgreSQL credentials |
| Oracle → Vercel | None (Oracle never calls Vercel) | N/A |

### 9.2 Gap: Oracle Worker Has No Authenticated Identity With Vercel

Currently the Oracle worker is **authenticated to Supabase** (via `DATABASE_URL`), but it has **no distinct authenticated identity** vis-à-vis Vercel. The `worker_id` is a generated label, not an authenticated credential.

This is acceptable **because the worker never calls Vercel directly**. All communication flows through Supabase.

### 9.3 Proposed Worker Authentication Boundary

```
Browser (user)
    │ Cookie session token (HMAC signed)
    ▼
Vercel Control Plane
    │ DATABASE_URL (Supabase, server-only)
    ▼
Supabase PostgreSQL
    │ DATABASE_URL (Supabase, server-only, in /opt/whatsapp-outreach/.env)
    ▼
Oracle Worker (reads/writes app_settings via SQLAlchemy)
```

**No new authentication token is required** for the Supabase-mediated pull model. The Oracle worker is already authenticated to Supabase via `DATABASE_URL`.

### 9.4 Optional: Worker API Key (For Future Inbound API)

If a future step introduces an inbound HTTP API on Oracle (e.g., for webhook or push), a **dedicated `WORKER_API_KEY`** must be introduced:

- Stored **only in Oracle `.env`** and **Vercel server-only env vars** (not `NEXT_PUBLIC_*`)
- Used as a bearer token in `Authorization: Bearer <WORKER_API_KEY>` header
- Generated once with `openssl rand -hex 32`
- Never logged, never stored in database, never exposed in frontend JS
- Rotatable without redeployment (just update both sides)

For the current polling-only design, this key is **not required**.

### 9.5 Credential Isolation Rules

| Secret | Location | Exposure |
| :--- | :--- | :--- |
| `DATABASE_URL` (Supabase) | Vercel env vars (server), Oracle `.env` | Server-only. Never in `NEXT_PUBLIC_*`, never in browser JS |
| `WEB_SESSION_COOKIE_NAME` / `WEB_SECRET_KEY` | Vercel env vars (server) | Cookie-only, server-only |
| `WORKER_INSTANCE_ID` | Oracle `.env` | Safe to expose in dashboard (it's a label) |
| `WORKER_API_KEY` (future) | Oracle `.env` + Vercel server env | Never in browser, never in logs |

---

## 10. Network Model (Question H)

### 10.1 Current Network Posture

```
Oracle VM (84.13.139.20)
  UFW: default deny inbound
  Allowed inbound: TCP 22 (SSH, key-auth only), fail2ban active
  Allowed outbound: all (443 for Supabase, 443 for WhatsApp Web)
```

### 10.2 Recommended Architecture: Outbound-Only (Polling)

The selected polling architecture requires **zero inbound port changes** on Oracle.

```
Oracle Worker
    │ outbound HTTPS :443
    ▼
Supabase (aws-1-eu-west-1.pooler.supabase.com:5432 or :6543)
    │
    ▼ (read/write app_settings)
Vercel Control Plane (reads from Supabase)
```

**No new firewall rules.** No new open ports. UFW configuration unchanged.

### 10.3 If Inbound Oracle API Were Added (Not Recommended Currently)

If a future inbound webhook or push API were added to Oracle:

| Requirement | Specification |
| :--- | :--- |
| Port | 8443 (non-standard HTTPS) |
| TLS | Let's Encrypt via certbot, auto-renew, ECDSA |
| Authentication | `Authorization: Bearer <WORKER_API_KEY>` (constant-time comparison) |
| Rate limiting | nginx / UFW per-source rate limit (10 req/min) |
| Firewall | UFW allow from Vercel CIDR ranges only |
| Failure behavior | Vercel falls back to polling if Oracle inbound is unreachable |
| Abuse protection | fail2ban on auth failures; IP allowlist |

> **Recommendation:** Do NOT open inbound ports at this stage. The polling model is functionally sufficient and operationally simpler. Revisit if sub-second command latency becomes a concrete requirement.

---

## 11. State Model (Question I)

### 11.1 Three Independent State Dimensions

The dashboard must display **three orthogonal state dimensions**, not collapse them:

#### A. Worker Lifecycle State

Published in `system:worker_heartbeat`:

| State | Condition |
| :--- | :--- |
| `OFFLINE` | No heartbeat in ≥ 60s or no heartbeat ever |
| `STARTING` | Heartbeat age < 60s, `runner_state = STARTING` |
| `READY` | Heartbeat age < 30s, xvfb_healthy, chrome_reachable, no runner yet started |
| `ACTIVE` | Heartbeat age < 30s, runner is RUNNING or IDLE |
| `DEGRADED` | Heartbeat age 30–60s, OR xvfb or chrome check failing |
| `ERROR` | Heartbeat present but runner state = FAILED |

**Transition conditions:**
```
OFFLINE ──[heartbeat received]──► STARTING
STARTING ──[preflight passes]──► READY
READY ──[runner started]──► ACTIVE
ACTIVE ──[heartbeat drops > 30s]──► DEGRADED
ACTIVE ──[runner fails]──► ERROR
DEGRADED ──[heartbeat resumes]──► ACTIVE or READY
DEGRADED ──[heartbeat drops > 60s]──► OFFLINE
ERROR ──[runner restarted]──► STARTING
```

#### B. WhatsApp Session State

Published in `system:whatsapp_telemetry` (existing):

| State | Condition |
| :--- | :--- |
| `DISCONNECTED` | Runner not active |
| `AUTHENTICATING` | Runner active, profile empty or QR displayed |
| `CONNECTED` | `is_chat_ready()` = True, WebSocket healthy |
| `SESSION_LOST` | Runner active but session invalidated remotely |
| `ERROR` | Provider threw unrecoverable exception |

#### C. Runner State

Published in `system:active_runner` (existing, via `ProcessLock`):

| State | Source |
| :--- | :--- |
| `STOPPED` | No lock file / no DB heartbeat |
| `STARTING` | Lock acquired, preflight running |
| `AUTHENTICATING` | Provider `connect()` in progress |
| `RUNNING` | Processing messages |
| `IDLE` | Running, queue empty |
| `PAUSED` | Emergency stop or campaign paused |
| `STOPPING` | Graceful shutdown in progress |
| `FAILED` | Unhandled exception or preflight failure |

These three dimensions are independent. For example:
- Worker `ACTIVE`, Session `DISCONNECTED`, Runner `STOPPED` = infrastructure online but not processing
- Worker `ACTIVE`, Session `CONNECTED`, Runner `RUNNING` = fully operational
- Worker `DEGRADED`, Session `CONNECTED`, Runner `RUNNING` = processing but heartbeat slow

---

## 12. Dashboard Observability (Question J)

### 12.1 Worker Card

```
┌─────────────────────────────────────────────────────────┐
│  WORKER: oracle-arm64-worker-01                         │
│  Status: ACTIVE                  Last Seen: 12s ago     │
│  Environment: Production (ARM64)                        │
│  Chrome: 153.0.8010.52          ChromeDriver: 153.0.8010.52 │
│  Xvfb Display: :99 ✓             App Version: Phase 7.7-B │
└─────────────────────────────────────────────────────────┘
```

**API source**: `GET /api/v1/whatsapp/status` (existing), extended with worker identity fields from `system:worker_heartbeat`.

### 12.2 Session Card

```
┌──────────────────────────────────────────────────────────┐
│  WHATSAPP SESSION                                        │
│  State: CONNECTED               Profile: PRESENT (176M) │
│  Authenticated: ✓               QR Required: No         │
│  WebSocket: Active              Health Check: 8m ago    │
│  Disclaimer: SEND_CONFIRMED = UI confirmation only       │
└──────────────────────────────────────────────────────────┘
```

### 12.3 Runner Card

```
┌──────────────────────────────────────────────────────────┐
│  PRODUCTION RUNNER                                       │
│  State: IDLE                    Campaign: #1             │
│  Desired State: RUNNING         Uptime: 2h 34m           │
│  Heartbeat: 8s ago              Worker: runner_camp1_abc │
└──────────────────────────────────────────────────────────┘
```

### 12.4 Active Command Card

```
┌──────────────────────────────────────────────────────────┐
│  ACTIVE COMMAND: HEALTH_CHECK                            │
│  Request ID: req_wa_1727123456_a1b2c3d4                  │
│  Status: EXECUTING               Version: 3              │
│  Requested By: operator@company.com                      │
│  Requested At: 2026-09-23T22:00:00Z                      │
│  Claimed At:   2026-09-23T22:00:04Z                      │
│  Lease Expires: 2026-09-23T22:01:04Z                     │
└──────────────────────────────────────────────────────────┘
```

### 12.5 Last Completed Command Card

```
┌──────────────────────────────────────────────────────────┐
│  LAST COMMAND: HEALTH_CHECK                              │
│  Status: COMPLETED                                       │
│  Result: HEALTHY (Chat interface verified responsive)    │
│  Completed At: 2026-09-23T22:00:07Z                      │
│  Duration: ~3s                                           │
└──────────────────────────────────────────────────────────┘
```

### 12.6 Fields Never Exposed to Dashboard

- Raw filesystem paths beyond `WHATSAPP_SESSION_PATH` flag (present/absent)
- Chrome PID
- OS-level PID (runner PID may be shown as opaque ID to ADMIN only)
- WhatsApp session cookies / tokens
- `DATABASE_URL` or any credential
- Raw `diagnostic_snippet` containing DOM elements with phone numbers
- SSH key fingerprints

---

## 13. Failure & Recovery Matrix (Question K)

| Scenario | Detection | Automatic Recovery | Dashboard State | Operator Action |
| :--- | :--- | :--- | :--- | :--- |
| **Oracle VM offline** | Heartbeat age > 60s | None (requires human intervention) | Worker: `OFFLINE`, Session: `DISCONNECTED`, Runner: `STOPPED` | SSH in, investigate |
| **Worker process crashed** | Heartbeat age > 60s; lock file has dead PID | Runner auto-restarts via systemd `Restart=on-failure` | Worker: `DEGRADED`→`STARTING` | Monitor; alert if not recovering |
| **Chrome crashed** | `system:whatsapp_telemetry` state = `ERROR`; health check fails | Runner exits `AUTHENTICATING` with `PROVIDER_UNAVAILABLE` exit code; systemd restarts | Session: `ERROR`, Worker: `STARTING` | Check Xvfb; run RECONNECT command |
| **Xvfb unavailable** | `ExecStartPre` probe fails; runner does not start | systemd `Restart=on-failure` after 10s | Worker: `STARTING` (repeatedly) | SSH in; check `systemctl status xvfb.service` |
| **WhatsApp session lost** | Telemetry state = `SESSION_LOST`; QR appears | None automatic (requires re-auth) | Session: `AUTHENTICATING` | SSH + VNC; re-scan QR |
| **Supabase unavailable** | DB write fails; heartbeat not updated | Worker continues processing in-memory; DB writes fail silently | Worker: `DEGRADED`→`OFFLINE` (if > 60s) | No action if transient; investigate if persistent |
| **Vercel unavailable** | N/A to Oracle | N/A (Oracle operates independently) | Dashboard unreachable | Wait for Vercel recovery |
| **Command lease expires** | `lease_expires_at < now` on next submit or status read | `recover_stale_or_orphaned_command()` marks FAILED | Command: `FAILED` (orphan recovered) | Check audit log; resubmit if needed |
| **Worker restarts during EXECUTING** | On restart, orphan recovery finds EXECUTING command with expired lease | Marks FAILED with "Orphaned command recovered" | Command: `FAILED` | Review audit log; do NOT assume execution succeeded |
| **Command result persisted but response lost** | Operator polls `GET /commands/{request_id}` | `get_command_by_id()` finds it in `last_completed` or audit log | N/A (result is in DB) | Poll for result |
| **Duplicate command delivery** | CAS check prevents concurrent REQUESTED slots | HTTP 409 Conflict returned | N/A | Wait for first command to complete |
| **Stale command** | `recover_stale_or_orphaned_command()` | Auto-fails on next submit or startup | Command: `FAILED` | Review audit log |
| **Clock skew** | UTC enforcement in all timestamps | ISO 8601 UTC only; no local time comparison | Potential timestamp display issue | Ensure Oracle runs NTP (`timedatectl show`) |
| **Database connection failure** | SQLAlchemy exception in heartbeat write | Retry via DB pool pre-ping; heartbeat fails gracefully | Worker: `DEGRADED`→`OFFLINE` | Check Supabase status; check network |

### 13.1 UNKNOWN_OUTCOME Preservation

This design makes **zero changes** to `UNKNOWN_OUTCOME` semantics:
- `UNKNOWN_OUTCOME` is a `messages.status` value, not a command protocol status
- A browser crash during a send transitions the message to `UNKNOWN_OUTCOME`, not `FAILED`
- `UNKNOWN_OUTCOME` is never automatically retried
- `UNKNOWN_OUTCOME` is never resolved to `SEND_CONFIRMED` without operator reconciliation
- The command protocol's `FAILED` terminal state (for control commands) is completely independent

---

## 14. Security Model (Question G+)

### 14.1 Least Privilege

| Component | Database Permission |
| :--- | :--- |
| Vercel (via pooler) | Read/write `app_settings`, `messages`, `campaigns`, `contacts`, `audit_logs` |
| Oracle Worker (via pooler) | Read/write same tables; no DDL privileges |
| Neither | Can create or drop tables, alter schema, or access Supabase management API |

### 14.2 Server-Only Secrets

| Secret | Vercel | Oracle | Browser |
| :--- | :--- | :--- | :--- |
| `DATABASE_URL` | ✓ server env | ✓ `.env` | ✗ never |
| `WEB_SECRET_KEY` | ✓ server env | ✗ | ✗ never |
| `WORKER_INSTANCE_ID` | Optional env | ✓ `.env` | ✓ (label only) |
| `WORKER_API_KEY` (future) | ✓ server env | ✓ `.env` | ✗ never |

**Enforcement:**
- No `NEXT_PUBLIC_*` prefix for any credential
- Vercel server env vars loaded only in server-side FastAPI context
- Oracle `.env` is `chmod 600` owned by `ubuntu:ubuntu`
- No secret is written to stdout or structured logs

### 14.3 CSRF Protection (Existing, Unchanged)

All state-changing POST endpoints already enforce double-submit CSRF via `verify_csrf` dependency:
- Cookie token: `X-CSRF-Token-Value` cookie
- Header token: `X-CSRF-Token` request header
- Constant-time comparison

No change required.

### 14.4 Audit Logging (Existing, Unchanged)

Every command lifecycle event emits an immutable `AuditLog` row with:
- `event_type`: `WHATSAPP_{ACTION}_{REQUESTED|COMPLETED|FAILED}`
- `status`: lifecycle status
- `result`: sanitized outcome string with `request_id` embedded
- `error_message`: sanitized error text (no secrets, no session data, no phone numbers)

### 14.5 Replay Protection

The `request_id` format `req_wa_{unix_ts}_{8hex}` provides natural replay resistance:
- Collision probability is negligible (`10^-9` per second of overlapping requests)
- The CAS lock prevents re-execution of a completed `request_id`
- Expired commands cannot be re-claimed (orphan recovery marks them FAILED first)

### 14.6 No Arbitrary Remote Execution

The command handler's `poll_and_execute()` dispatches to a closed set of named handler methods only. Unknown actions fail with `"Unrecognized action '{action}'"`. No eval, no exec, no subprocess invocation from command params.

---

## 15. Database Impact (Question)

### 15.1 Preferred Approach: Zero Migrations

The following new `app_settings` keys are proposed:

| Key | Purpose | Migration Required? |
| :--- | :--- | :--- |
| `system:worker_identity` | Stable worker instance metadata | **No** (key-value in existing `app_settings`) |
| `system:worker_heartbeat` | Extended heartbeat with infra health | **No** (key-value in existing `app_settings`) |
| `system:last_preflight_result` | Latest preflight check outcome | **No** (key-value in existing `app_settings`) |

All new state can be stored as JSON blobs in the existing `app_settings` table using the existing `AppSetting` ORM model. **Zero database migrations required.**

### 15.2 Future Migration: If Heartbeat History Is Needed

If time-series heartbeat history is required (e.g., for uptime SLA charts), a new `worker_heartbeat_history` table with a time-based index would be needed. This is **not required for Phase 7.7-B Step 7** and is deferred.

| Potential Table | Columns | Migration Impact |
| :--- | :--- | :--- |
| `worker_heartbeat_history` | `id, instance_id, recorded_at, runner_state, xvfb_healthy, chrome_reachable, heartbeat_age_s` | 1 new table, 1 index; backward-compatible |

---

## 16. Deployment Impact (Question)

### 16.1 Vercel Deployment

| Change | File | Impact |
| :--- | :--- | :--- |
| New API route: `POST /preflight` | `app/web/routes/api/whatsapp.py` | Add 1 route |
| New web service method: `get_worker_identity()` | `app/web/services/whatsapp_service.py` | Add 1 method |
| Extended status DTO | `app/web/schemas/whatsapp.py` | Add fields to existing DTO |
| New `WORKER_INSTANCE_ID` env var (optional) | Vercel dashboard env vars | Read-only label |

No Vercel constraint violations. All new routes are FastAPI serverless-compatible.

### 16.2 Oracle Worker Deployment

| Change | File | Impact |
| :--- | :--- | :--- |
| New env var: `WORKER_INSTANCE_ID` | `/opt/whatsapp-outreach/.env` | Add 1 line |
| Extended heartbeat publisher | `app/runner/whatsapp_command_handler.py` | Extend `publish_telemetry()` |
| New worker identity publisher | `app/runner/whatsapp_command_handler.py` | New method `publish_identity()` |
| New `_execute_preflight()` handler | `app/runner/whatsapp_command_handler.py` | Add 1 handler method |
| New preflight command action | `poll_and_execute()` dispatch switch | Add 1 case |
| New env var in `.env.example` | `deploy/worker/env.worker.example` | Add documentation |

**No systemd changes required.** The existing `outreach-runner.service` unit is sufficient.

---

## 17. Testing Strategy

### 17.1 Unit Tests (Non-Live)

| Test | File | Scope |
| :--- | :--- | :--- |
| Worker identity serialization | `tests/test_whatsapp_command_handler.py` | Mock DB write of identity record |
| Heartbeat extended payload | `tests/test_whatsapp_command_handler.py` | Verify new fields in `system:worker_heartbeat` |
| PREFLIGHT command dispatch | `tests/test_whatsapp_command_handler.py` | Mock preflight call, verify result written |
| PREFLIGHT command RBAC | `tests/test_whatsapp_api.py` | VIEWER gets 403, OPERATOR gets 202 |
| Status DTO extended fields | `tests/test_whatsapp_status.py` | Verify worker identity fields present |
| State model transitions | `tests/test_runner_lifecycle.py` | State machine invariants |

### 17.2 Integration Test (Live Oracle, Before Production Use)

1. Start `outreach-runner.service` on Oracle.
2. Wait for heartbeat in `system:worker_heartbeat` (< 20s).
3. Submit `PREFLIGHT` command from Vercel dashboard.
4. Verify command transitions REQUESTED → CLAIMED → EXECUTING → COMPLETED.
5. Verify result stored in `system:last_preflight_result`.
6. Stop runner. Verify heartbeat age increases to `DEGRADED` (30s) then `OFFLINE` (60s).
7. Verify dashboard reflects state changes accurately.

### 17.3 Test Baseline Preservation

The existing **412/0 non-live-browser test suite** must continue passing after implementation. New tests add to this baseline. No existing tests may be modified to accommodate new code.

---

## 18. Rollback Strategy

All proposed changes are:
1. **Additive**: New `app_settings` keys, new handler method, new command type.
2. **Non-breaking**: Existing keys and behavior are unchanged.
3. **Soft-deployable**: If the new heartbeat key is never written, the dashboard shows "No worker identity available" gracefully.

**Rollback procedure:**
1. Remove `WORKER_INSTANCE_ID` from `.env`.
2. Revert `app/runner/whatsapp_command_handler.py` changes.
3. Revert `app/web/routes/api/whatsapp.py` (remove `PREFLIGHT` route).
4. Existing Phase 7.6 functionality is fully restored without any migration rollback.

No database rollback migration is required because all new state is in the schemaless `app_settings` table.

---

## 19. Risks & Open Questions

### 19.1 Risks

| Risk | Severity | Mitigation |
| :--- | :--- | :--- |
| Oracle worker loses DB connectivity silently | Medium | Heartbeat age monitoring; dashboard OFFLINE alert threshold at 60s |
| Stale identity published after partial update | Low | Atomic JSON replace of entire record; no partial write |
| `PREFLIGHT` command takes > 60s (Chrome launch during preflight) | Low | Extend `PREFLIGHT` lease to 120s via `lease_duration_seconds` param |
| Clock drift between Oracle and Supabase | Low | Both use UTC; Oracle syncs via NTP (default Oracle Ubuntu config) |
| Supabase connection pool exhaustion | Low | `DB_POOL_SIZE=5`, `DB_POOL_RECYCLE=1800`; sufficient for current load |

### 19.2 Open Questions

1. **WORKER_INSTANCE_ID format**: Should it be operator-assigned (human readable) or auto-generated UUID? Recommendation: operator-assigned human-readable string (easier to read in dashboards).
2. **Preflight Chrome launch during PREFLIGHT command**: Should `_execute_preflight()` launch Chrome or run preflight in `no-browser` mode? Recommendation: run preflight with `strict=True` but without launching the WhatsApp browser session (infrastructure checks only), unless the runner's existing provider is active.
3. **PREFLIGHT lease duration**: 60s may be too short if Chrome cold-start takes 20s. Recommend 120s lease for `PREFLIGHT` action.
4. **Dashboard polling interval**: How frequently should the Vercel dashboard poll `/api/v1/whatsapp/status`? Recommendation: every 15s (matches heartbeat interval).
5. **Worker identity update on Chrome version change**: Should `publish_identity()` re-read Chrome version on every heartbeat or only on startup? Recommendation: only on startup for performance.

---

## 20. Implementation Plan

### Phase 7.7-B Step 7 Implementation Scope

**Estimated scope**: Small — primarily extending existing code, no schema changes, no new tables.

#### File-Level Change Summary

| File | Change Type | Change |
| :--- | :--- | :--- |
| `app/runner/whatsapp_command_handler.py` | MODIFY | Add `publish_identity()`, extend `publish_telemetry()` with infra checks, add `_execute_preflight()` handler |
| `app/web/routes/api/whatsapp.py` | MODIFY | Add `POST /api/v1/whatsapp/preflight` route |
| `app/web/services/whatsapp_service.py` | MODIFY | Extend `get_status()` to include worker identity and infra health from `system:worker_heartbeat` |
| `app/web/schemas/whatsapp.py` | MODIFY | Extend `WhatsAppStatusDTO` with worker identity and infra health fields |
| `app/utils/settings.py` | MODIFY | Add `WORKER_INSTANCE_ID: str = ""` setting |
| `deploy/worker/env.worker.example` | MODIFY | Add `WORKER_INSTANCE_ID` documentation |

**No new files required.** No migrations. No new dependencies.

#### Implementation Order

1. `app/utils/settings.py` — Add `WORKER_INSTANCE_ID` setting.
2. `app/runner/whatsapp_command_handler.py` — Add identity publisher and extended telemetry.
3. `app/web/schemas/whatsapp.py` — Extend DTO.
4. `app/web/services/whatsapp_service.py` — Read and surface new heartbeat fields.
5. `app/web/routes/api/whatsapp.py` — Add `PREFLIGHT` route.
6. `deploy/worker/env.worker.example` — Document new env var.
7. Tests — Add unit test coverage for new command and new telemetry fields.
8. Oracle VM — Add `WORKER_INSTANCE_ID` to `/opt/whatsapp-outreach/.env`.

---

## Appendix A: AppSetting Key Registry (Complete)

| Key | Owner | Schema |
| :--- | :--- | :--- |
| `system:active_runner` | Worker (ProcessLock) | `{pid, worker_id, campaign_id, started_at, last_heartbeat}` |
| `system:desired_runner_state` | Vercel (RunnerControlService) | `"RUNNING"` or `"STOPPED"` |
| `system:desired_runner_campaign_id` | Vercel (RunnerControlService) | Integer string |
| `system:whatsapp_command:active` | Vercel/Worker (WhatsAppCommandService) | Full command object |
| `system:whatsapp_command:last_completed` | Worker (WhatsAppCommandService) | Terminal command object |
| `system:whatsapp_telemetry` | Worker (WhatsAppCommandHandler) | `{state, last_health_check, diagnostic_snippet, worker_id, updated_at}` |
| `system:emergency_stop` | Vercel/CLI | Boolean-string flag |
| `system:worker_identity` | **[NEW] Worker** | `{instance_id, environment, arch, chrome_version, capabilities, app_version, registered_at, last_seen}` |
| `system:worker_heartbeat` | **[NEW] Worker** | `{instance_id, last_seen, runner_state, xvfb_healthy, chrome_reachable, session_profile_present, uptime_seconds}` |
| `system:last_preflight_result` | **[NEW] Worker** | Preflight JSON output blob |

---

```
DESIGN STATUS:
READY_FOR_REVIEW
```
