# Phase 7 Architecture & Implementation Blueprint
# Web Control Center (`https://auto.integra-ist.com`)
## REVISION 1 — POST-DESIGN REVIEW

---

## 1. Status & Governance Gate

```text
================================================================================
GOVERNANCE STATUS: DESIGN ONLY — PENDING APPROVAL (REVISION 1)
================================================================================
Current Phase:     PHASE 7 — WEB CONTROL CENTER
Target Domain:     https://auto.integra-ist.com
Workflow Step:     DESIGN (REV 1) -> [HUMAN REVIEW & APPROVAL] -> IMPLEMENTATION
Code Status:       STRICTLY NO IMPLEMENTATION CODE WRITTEN
Database Status:   STRICTLY NO MIGRATIONS CREATED OR APPLIED
Dependency Status: STRICTLY NO PACKAGES INSTALLED
Phase 1–6 Status:  FROZEN, FUNCTIONAL, 100% PASSING (219 passed, 91% coverage)
================================================================================
```

### 1.1 Governance Compliance Statement
This document represents **Revision 1** of the Phase 7 Architectural Blueprint, incorporating all required corrections from the Phase 7 Design Review. In strict adherence to project governance:
- **No production or test code has been created or modified.**
- **No database migrations have been executed.**
- **No new dependencies have been installed.**
- **No existing behaviors or safety invariants from Phases 1 through 6 have been altered.**
- **Implementation work will begin ONLY after this revised blueprint is formally reviewed and explicitly approved by the human operator.**

---

## 2. Executive Objective & Architectural Principles

### 2.1 Primary Objective
Phase 7 introduces a secure, production-ready, browser-based **Web Control Center** deployed at `https://auto.integra-ist.com`. It provides authorized human operators with centralized visibility, real-time telemetry, campaign lifecycle controls, template authoring, contact directory management, queue inspection, session administration, and emergency safety mechanisms for the WhatsApp Outreach Automation system.

### 2.2 Core Architectural Axiom: Non-Duplication
The web application is strictly an **operational interface and control plane**. It does **NOT** replace, duplicate, or bypass the authoritative domain services established in Phases 1 through 6. 

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                             PRESENTATION LAYER                              │
│         Browser Client (Desktop / Tablet) @ https://auto.integra-ist.com   │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ HTTPS / SSE
┌──────────────────────────────────────▼──────────────────────────────────────┐
│                    EDGE & REVERSE PROXY LAYER (Nginx)                       │
│        TLS 1.3 Termination, Security Headers, Rate Limiting, Static Assets  │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ Local Reverse Proxy (127.0.0.1:8000)
┌──────────────────────────────────────▼──────────────────────────────────────┐
│                       APPLICATION & API LAYER (FastAPI)                     │
│      Auth & RBAC Middleware, Input Validation (Pydantic v2), REST API, SSE   │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ In-Process Python Service Calls
┌──────────────────────────────────────▼──────────────────────────────────────┐
│                   EXISTING DOMAIN SERVICES (Phases 1–6)                     │
│  CampaignManager  │ ContactManager │ QueueService │ RateLimiter │ CB/E-Stop  │
│  AnalyticsService │ HealthCheck    │ AuditLogger  │ TemplateSvc │ Validator  │
└───────────────────┬──────────────────────────────────┬──────────────────────┘
                    │                                  │
┌───────────────────▼──────────────────┐   ┌───────────▼──────────────────────┐
│         DATA PERSISTENCE LAYER       │   │        RUNNER CONTROL SERVICE    │
│  SQLite (WAL Mode) via SQLAlchemy    │   │  Process Supervision & Mediation │
│  Zero mutations to Phase 1-6 tables  │   │  OS File Lock (data/runner.lock) │
│  Isolated Phase 7 Auth / User Schema │   │  ProductionRunner (Single-Active)│
│  Immutable Template Library Version  │   │  WhatsAppWebProvider / Selenium  │
└──────────────────────────────────────┘   └──────────────────────────────────┘
```

### 2.3 Strict Invariants & Prohibited Terminology
1. **Absolute Prohibition on Delivery Claims**: The system interacts with WhatsApp Web purely via the client browser UI. WhatsApp Web does not provide an atomic message delivery or read-receipt confirmation API to automation tools.
   - The following terms are **STRICTLY PROHIBITED** across the entire UI, API schemas, documentation, and source code:
     - `CONFIRMED DELIVERED`
     - `DELIVERY CONFIRMED`
     - `DELIVERED`
     - `READ`
     - `DELIVERY RATE`
   - **`SEND_CONFIRMED`** means exclusively: *"WhatsApp Web UI evidence confirmed that the send operation was accepted/confirmed by the browser UI (e.g. checkmark icon rendered in chat DOM)."* It does **NOT** represent or guarantee verified delivery to the recipient's handset or that the message was read.
2. **Single-Runner Invariant**: Exactly one production runner operates against one active campaign at any given time. The web UI supervises runner execution via `RunnerControlService` but cannot launch concurrent competing runners.
3. **Authoritative OS File Lock**: The OS-level file lock (`data/runner.lock`) remains the sole authoritative concurrency lock. Web UI runner controls inspect and respect this lock.
4. **Domain State Separation**: Setting a campaign to `RUNNING` in the UI is strictly a domain state transition (`campaign.status = RUNNING`), making it eligible for runner ingestion. It does **not** silently spawn background daemons or bypass the runner.
5. **Provider Isolation**: No Selenium or WebDriver APIs are imported or manipulated outside of `app/providers/whatsapp_web/`.
6. **No Anti-Detection Hacks**: Zero fingerprint spoofing, zero CAPTCHA bypass, zero stealth scripts.
7. **No Blind Retries on `UNKNOWN_OUTCOME`**: Messages with ambiguous external delivery state are never retried automatically from the UI. Manual reconciliation requires operator verification, explicit justification, and the authoritative Phase 5 confirmation token `CONFIRM-NOT-DELIVERED`.

---

## 3. Existing Architecture Assessment & Service Reuse Inventory

The codebase contains a mature suite of hardened domain services. The Phase 7 Web Control Center wraps and consumes these services directly without duplicating business logic:

| Existing Component | Source Path | Phase Introduced | Phase 7 Reuse Role in Web Control Center |
| :--- | :--- | :--- | :--- |
| **`CampaignManager`** | `app/campaigns/manager.py` | Phase 1 / 2 | Campaign lifecycle management (create, update, schedule, archive, transition states). |
| **`MessageTemplateService`** | `app/campaigns/template_service.py` | Phase 2 | Template syntax validation, variable extraction (`{first_name}`, `{company}`), safe substitution, and preview rendering. |
| **`ContactEligibilityService`** | `app/campaigns/eligibility_service.py` | Phase 2 | Contact qualification, duplicate filtering, opt-out checking during campaign population. |
| **`CampaignStatisticsService`** | `app/campaigns/statistics_service.py` | Phase 2 | Basic campaign-level summary metrics (sent, failed, pending). |
| **`ContactManager`** | `app/contacts/manager.py` | Phase 1 | Contact CRUD, suppression, opt-out management. |
| **`PhoneValidator`** | `app/contacts/validator.py` | Phase 1 | E.164 standardization, country code validation, format sanitization for contact management and CSV import. |
| **`CSVHandler`** | `app/contacts/csv_handler.py` | Phase 1 | Parsing, header validation, dry-run checking, and batch ingestion of contact CSV uploads. |
| **`PersistentQueueService`** | `app/queue/service.py` | Phase 3 | Queue inspection, message state retrieval, batch generation, manual state reconciliation (`reconcile_unknown_outcome`). |
| **`MessageStateMachine`** | `app/queue/state_machine.py` | Phase 3 | Validation of permissible message state transitions (e.g., `PENDING` -> `CLAIMED` -> `SENT`). |
| **`RateLimiter`** | `app/limiter/rate_limiter.py` | Phase 3 | Workload pacing evaluation, interval delay calculations, daily quota checks. |
| **`FrequencyLimitService`** | `app/limiter/frequency_service.py` | Phase 3 | Contact cooldown checking (preventing re-contacting within $N$ days across campaigns). |
| **`CircuitBreaker`** | `app/scheduler/circuit_breaker.py` | Phase 3 | State inspection (`CLOSED`, `OPEN`, `HALF_OPEN`), failure rate tracking, manual reset from UI. |
| **`EmergencyStop`** | `app/scheduler/emergency_stop.py` | Phase 3 / 5 | Immediate stop signal propagation (<500ms target), blocking new claims, global abort trigger. |
| **`BatchManager`** | `app/scheduler/batch_manager.py` | Phase 3 | Batch sizing, boundary enforcement, inter-batch pause tracking. |
| **`ProcessLock`** | `app/runner/process_lock.py` | Phase 5 | OS-level file lock inspection (`data/runner.lock`), PID detection, runner liveness verification. |
| **`ProductionRunner`** | `app/runner/production_runner.py` | Phase 5 | Supervised runner process execution, heartbeat emission, orderly shutdown coordination. |
| **`WhatsAppWebProvider`** | `app/providers/whatsapp_web/provider.py` | Phase 4 | WhatsApp Web operations, health checks, message dispatch. |
| **`WhatsAppSessionManager`** | `app/providers/whatsapp_web/session_manager.py` | Phase 4 | Session lifecycle, profile directory management, authentication status detection. |
| **`WhatsAppBrowser`** | `app/providers/whatsapp_web/browser.py` | Phase 4 | Browser abstraction, QR canvas capture via screenshot for operator scanning, DOM health. |
| **`AnalyticsService`** | `app/services/analytics_service.py` | Phase 6 | Authoritative analytics engine providing all 5 operational views, strictly calculated send rates, throughput, and error categorizations. |
| **`evaluate_system_health`** | `app/readiness/health.py` | Phase 6 | Multi-component health evaluation (DB, Disk, Logs, Runner, Session) returning `HEALTHY`, `DEGRADED`, or `UNHEALTHY`. |
| **`run_preflight`** | `app/readiness/preflight.py` | Phase 6 | Preflight validation checks (DB migration status, lock availability, directory permissions, provider configuration). |
| **`AuditLogger` / `AuditLog`** | `app/campaigns/audit_logger.py`, `app/models/audit_log.py` | Phase 1 / 6 | Immutable audit trail recording all human operator mutations and security events. |
| **Structured Logger** | `app/utils/logger.py` | Phase 6 | Dual-destination logging (`logs/app.log`, `logs/app.json.log`) with handler-safe redaction and execution contexts. |

---

## 4. Proposed Web Architecture

### 4.1 Tiered Layering & System Boundaries

```text
┌────────────────────────────────────────────────────────────────────────┐
│ 1. CLIENT / PRESENTATION TIER (Single Page App / Modern Reactive UI)   │
│    - Accessible via https://auto.integra-ist.com                      │
│    - Responsive layout (Desktop-optimized, Tablet-capable)            │
│    - Real-time SSE listener for live telemetry updates                │
│    - Clean, modern UI system (Dashboard, Controls, Forms, Tables)     │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ HTTPS (TLS 1.3) / Strict CSP
┌───────────────────────────────────▼────────────────────────────────────┐
│ 2. REVERSE PROXY & SECURITY TIER (Nginx)                               │
│    - Host header validation (Host: auto.integra-ist.com)              │
│    - SSL/TLS Offloading with modern cipher suites                     │
│    - Rate Limiting (DDoS / Auth endpoint brute-force protection)      │
│    - Static asset caching & Gzip compression                          │
│    - SSE proxy buffering disabled (proxy_buffering off)               │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ HTTP / ASGI Protocol
┌───────────────────────────────────▼────────────────────────────────────┐
│ 3. API & APPLICATION CONTROL TIER (FastAPI)                            │
│    - Authentication & Session Middleware (Secure, HttpOnly, SameSite)  │
│    - RBAC Authorization Dependency Injection (Role privilege checks)   │
│    - Pydantic v2 Request/Response Validation & Serialization          │
│    - Global Exception Handlers (Standardized JSON error envelope)      │
│    - Audit Logging Middleware (Attaching operator ID & IP to audit)    │
│    - Server-Sent Events (SSE) Broadcast Hub                            │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ In-Process Service Invocations
┌───────────────────────────────────▼────────────────────────────────────┐
│ 4. SERVICE ADAPTER LAYER (`app/web/services/`)                         │
│    - `RunnerControlService`: Mediates runner start/stop/status        │
│    - `SettingsService`: Persists pacing settings to AppSetting         │
│    - `TemplateLibraryService`: Manages immutable template versions     │
│    - Manages transactional SQLAlchemy Session scope                   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Direct Method Calls
┌───────────────────────────────────▼────────────────────────────────────┐
│ 5. AUTHORITATIVE DOMAIN LAYER (Phases 1–6)                             │
│    - CampaignManager, PersistentQueueService, AnalyticsService, etc.  │
│    - ZERO modification to domain business logic                       │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│ 6. PERSISTENCE & RUNTIME TIER                                          │
│    - SQLite Database (WAL mode enabled)                                │
│    - OS File Lock: `data/runner.lock`                                  │
│    - Logs: `logs/app.log`, `logs/app.json.log`                         │
│    - Chrome User Profile: `data/whatsapp_profile`                      │
└────────────────────────────────────────────────────────────────────────┘
```

### 4.2 Prohibited Architectural Patterns
- **No Direct DB Mutations in Controllers**: All database interactions must route through existing domain services or dedicated application repositories.
- **No Web-Triggered Browser Spawning**: The web server process itself must **never** instantiate a Selenium WebDriver for message sending. Outreach message dispatch remains the exclusive responsibility of `ProductionRunner`. The only browser interaction permitted in the web context is an isolated session inspection/QR capture via `WhatsAppSessionManager`.
- **No Direct Subprocess Spawning from Route Handlers**: Route handlers must delegate process operations to `RunnerControlService`. Arbitrary `subprocess.Popen` calls inside FastAPI route handlers are strictly prohibited.

---

## 5. Route Map (UI Views & REST API Endpoints)

### 5.1 Web UI Views (Pages)

| URL Route | View Title | Description & Purpose | Minimum Role Required |
| :--- | :--- | :--- | :--- |
| `/setup` | First-Run Setup | One-time initial OWNER account creation (permanently disabled once user exists). | Public (Conditional) |
| `/login` | Authentication | Secure login interface with CSRF protection and brute-force backoff. | Public |
| `/dashboard` | System Overview | Real-time health, active campaign summary, performance indicators, active alerts. | `VIEWER` |
| `/campaigns` | Campaign Directory | Listing of all campaigns with status filters, search, and creation wizard. | `VIEWER` |
| `/campaigns/new` | Create Campaign | Wizard for campaign setup (name, template selection, contact assignment, pacing). | `OPERATOR` |
| `/campaigns/:id` | Campaign Details | Detailed metrics, state transitions (`SCHEDULE`, `RUN`, `PAUSE`, `CANCEL`), queue breakdown. | `VIEWER` |
| `/templates` | Template Library | Listing of reusable message templates with version history and variable validation. | `VIEWER` |
| `/templates/new` | Create Template | Template editor with real-time `{variable}` syntax validation and preview. | `OPERATOR` |
| `/templates/:id` | Edit Template | Create new immutable template version, inspect historical versions and diffs. | `OPERATOR` |
| `/contacts` | Contact Directory | Paginated, searchable contact directory with masked phone numbers and tags. | `VIEWER` |
| `/contacts/import`| CSV Contact Ingestion | CSV file upload, column mapping, dry-run validation report, and batch commit. | `OPERATOR` |
| `/contacts/:id` | Contact Profile | Contact details, suppression status, message history, campaign memberships. | `VIEWER` |
| `/queue` | Live Queue Inspector | Message queue state monitoring, in-flight tracking, and unknown outcome management. | `VIEWER` |
| `/whatsapp` | WhatsApp Session | WhatsApp Web connection status, QR code display, health check, session reset. | `OPERATOR` |
| `/runner` | Runner Control Center | OS file lock status, DB heartbeat, runner process supervisor (start/stop/restart). | `OPERATOR` |
| `/analytics` | Operational Analytics | 5 analytics views from Phase 6, Confirmed Send Rate, failure breakdowns, export. | `VIEWER` |
| `/audit` | Audit Trail | Immutable audit log viewer with actor/entity filters and detail JSON viewer. | `ADMIN` |
| `/settings` | System Settings | Application configuration, safety limits, circuit breaker settings, user management. | `ADMIN` |

### 5.2 REST API Specification (`/api/v1/`)

```text
/api/v1/
├── setup/
│   ├── GET    /status                 (Check if first-run setup is available: true/false)
│   └── POST   /bootstrap              (Create initial OWNER account - available ONLY when 0 users exist)
├── auth/
│   ├── POST   /login                  (Authenticate user, regenerate session, set HttpOnly session cookie)
│   ├── POST   /logout                 (Invalidate session, clear cookie)
│   ├── GET    /me                     (Get current user profile, role, permissions)
│   └── POST   /change-password        (Update password with current password verification)
├── health/
│   ├── GET    /live                   (Kubernetes/container liveness probe)
│   ├── GET    /ready                  (Readiness check: DB, lock, disk, config)
│   └── GET    /system                 (Comprehensive Phase 6 evaluate_system_health report)
├── campaigns/
│   ├── GET    /                       (List campaigns with pagination, search, status filter)
│   ├── POST   /                       (Create new campaign via CampaignManager with template snapshot)
│   ├── GET    /{id}                   (Get campaign details, progress statistics, pinned template version)
│   ├── PATCH  /{id}                   (Update campaign metadata, schedule, or pacing settings)
│   ├── POST   /{id}/status            (Trigger state transition: SCHEDULE, RUN, PAUSE, CANCEL, ARCHIVE)
│   ├── POST   /{id}/upgrade-template  (Explicitly upgrade campaign template to a newer library version)
│   ├── GET    /{id}/queue-summary     (Get counts by queue state for this campaign)
│   └── POST   /{id}/populate          (Populate campaign contacts via ContactEligibilityService)
├── templates/
│   ├── GET    /                       (List template entities with active version metadata)
│   ├── POST   /                       (Create template and initialize Version 1)
│   ├── GET    /{id}                   (Get template details and active version)
│   ├── GET    /{id}/versions          (List all immutable versions of this template)
│   ├── POST   /{id}/versions          (Create a new immutable version for this template)
│   ├── DELETE /{id}                   (Archive template if not referenced by active campaigns)
│   └── POST   /validate               (Dry-run syntax validation & variable extraction)
├── contacts/
│   ├── GET    /                       (List contacts with search, pagination, masked phone)
│   ├── POST   /                       (Create single contact via ContactManager & PhoneValidator)
│   ├── GET    /{id}                   (Get contact details and message history)
│   ├── PATCH  /{id}                   (Update contact details)
│   ├── POST   /{id}/opt-out           (Manually opt-out/suppress contact)
│   ├── POST   /upload-dry-run         (Validate CSV upload, return errors & preview)
│   ├── POST   /upload-commit          (Commit validated CSV contacts)
│   └── GET    /export                 (Export contacts to CSV - ADMIN/OWNER only)
├── queue/
│   ├── GET    /                       (Inspect queue items with filters by state/campaign)
│   ├── GET    /metrics                (Current queue distribution: PENDING, CLAIMED, SENT, etc.)
│   ├── POST   /emergency-stop         (Trigger Phase 3/5 EmergencyStop signal)
│   ├── POST   /emergency-stop/reset   (Reset EmergencyStop state - ADMIN/OWNER only)
│   ├── POST   /circuit-breaker/reset  (Reset CircuitBreaker to CLOSED - ADMIN/OWNER only)
│   └── POST   /messages/{id}/override (Reconcile UNKNOWN_OUTCOME with CONFIRM-NOT-DELIVERED token)
├── whatsapp/
│   ├── GET    /status                 (Session state: DISCONNECTED, CONNECTING, CONNECTED, etc.)
│   ├── GET    /qr-code                (Stream/fetch base64 QR code image when AUTHENTICATING)
│   ├── POST   /connect                (Initiate browser session in AUTHENTICATING mode)
│   ├── POST   /disconnect             (Orderly browser disconnect)
│   └── POST   /session-reset          (Clear profile directory and reset - ADMIN/OWNER only)
├── runner/
│   ├── GET    /status                 (Delegates to RunnerControlService.get_status())
│   ├── POST   /start                  (Delegates to RunnerControlService.start_runner())
│   ├── POST   /stop                   (Delegates to RunnerControlService.stop_runner())
│   └── POST   /pause                  (Transitions active campaign to PAUSED via CampaignManager)
├── analytics/
│   ├── GET    /overview               (High-level KPI metrics: Confirmed Send Rate, Volume)
│   ├── GET    /campaign-performance   (Phase 6 View 1: Campaign summaries)
│   ├── GET    /reliability            (Phase 6 View 3: Provider & UI reliability)
│   ├── GET    /throughput             (Phase 6 View 4: Messages/hr, latency percentiles)
│   ├── GET    /failures               (Phase 6 View 5: Error breakdown and categorization)
│   └── GET    /export                 (Export analytics dataset in CSV or JSON)
├── audit/
│   ├── GET    /                       (List audit logs with filtering by actor, entity, date)
│   └── GET    /{id}                   (Get full audit log record with JSON payload)
├── settings/
│   ├── GET    /                       (Get system settings categorized into tabs)
│   ├── PATCH  /                       (Update editable settings via SettingsService)
│   └── GET    /limits                 (Get current rate, pacing, and frequency limits)
├── users/
│   ├── GET    /                       (List users - ADMIN/OWNER only)
│   ├── POST   /                       (Create user - ADMIN/OWNER only)
│   ├── PATCH  /{id}                   (Update user role or active status - OWNER only)
│   └── DELETE /{id}                   (Deactivate user - OWNER only)
└── events/
    └── GET    /stream                 (Server-Sent Events stream for real-time telemetry)
```

---

## 6. Authentication Architecture & First-Run Setup

### 6.1 Password Hashing Abstraction: `PasswordHasher`
To avoid tight coupling to a single hashing implementation, the authentication system depends strictly on an explicit abstraction:

```python
from typing import Protocol

class PasswordHasher(Protocol):
    def hash(self, plain_password: str) -> str:
        """Hashes a plaintext password using a cryptographically secure algorithm."""
        ...

    def verify(self, plain_password: str, hashed_password: str) -> bool:
        """Verifies a plaintext password against a stored hash using constant-time comparison."""
        ...

    def needs_rehash(self, hashed_password: str) -> bool:
        """Evaluates whether the stored hash needs rehashing (e.g. work factor upgraded)."""
        ...
```

#### 6.1.1 Implementation: `BcryptPasswordHasher`
- Algorithm: `bcrypt` with work factor 12.
- Timing-safe verification: uses `bcrypt.checkpw()`, which performs internal constant-time comparison against timing attacks.
- Upgrade Strategy: `needs_rehash()` verifies whether the work factor matches the current application configuration. Upon successful login, if `needs_rehash()` is True, the application transparently rehashes the password and updates `users.password_hash`.
- Password Complexity Policy: minimum 12 characters, requiring at least one uppercase letter, one lowercase letter, one numeric digit, and one special symbol.

### 6.2 First-Run OWNER Bootstrap
To eliminate the risk of default credentials (`admin`/`admin`) or exposed bootstrap endpoints:

```text
Operator requests GET /api/v1/setup/status
                │
                ▼
[ DB Query: SELECT COUNT(id) FROM users ]
                │
      ┌─────────┴─────────┐
Count == 0           Count > 0
      │                   │
      ▼                   ▼
HTTP 200:           HTTP 200:
{"available": true} {"available": false}
(Setup page active) (Redirect to /login; setup permanently locked)
```

#### 6.2.1 Secure Bootstrap Flow & Race Condition Prevention
1. **Atomic Guard**: The setup endpoint `POST /api/v1/setup/bootstrap` executes inside a database transaction with an immediate table lock or atomic check:
   ```python
   with db.begin():
       user_count = db.query(func.count(User.id)).with_for_update().scalar()
       if user_count > 0:
           raise HTTPException(status_code=409, detail="Setup has already been completed.")
       
       # Validate password strength
       validate_password_policy(request.password)
       
       # Create initial OWNER account
       owner = User(
           id=str(uuid.uuid4()),
           username=request.username.strip().lower(),
           email=request.email.strip().lower(),
           password_hash=password_hasher.hash(request.password),
           role=UserRole.OWNER,
           is_active=True,
           created_at=datetime.now(timezone.utc),
           updated_at=datetime.now(timezone.utc)
       )
       db.add(owner)
       
       # Record immutable audit log
       audit_logger.record(
           event_type="SYSTEM_BOOTSTRAP_OWNER_CREATED",
           actor=owner.username,
           payload={"user_id": owner.id, "email": owner.email}
       )
   ```
2. **Permanent Invalidation**: Once the transaction commits, any subsequent request to `/api/v1/setup/bootstrap` immediately returns `409 Conflict`.
3. **No Default Credentials**: The initial password must be explicitly provided by the operator during setup and must pass all password complexity rules.

### 6.3 Session Management & Token Rotation
- **Session Cookie Storage**: Stored in `user_sessions` table.
- **Session Fixation Defense**: Upon successful login (`POST /api/v1/auth/login`), any pre-existing session token is invalidated, and a completely new cryptographically random session token (`secrets.token_urlsafe(32)`) is generated and set in the cookie.
- **Session Expiration**:
  - Inactivity Timeout: 30 minutes of idle time.
  - Absolute Lifetime: 12 hours max.
- **Concurrent Session Ceiling**: Enforces a maximum of 2 concurrent active sessions per user; oldest session is automatically revoked when ceiling is reached.
- **Logout Invalidation**: `POST /api/v1/auth/logout` immediately deletes the session record from `user_sessions` and clears the cookie.

### 6.4 Double-Submit CSRF Defense
- A signed, cryptographically random CSRF token is issued in a readable cookie (`outreach_csrf_token`).
- All mutating requests (`POST`, `PUT`, `PATCH`, `DELETE`) must supply this token in the `X-CSRF-Token` header.
- Middleware verifies that the token in the header matches the signed value in the cookie.

---

## 7. Role-Based Access Control (RBAC)

### 7.1 Role Hierarchy & Privilege Matrix
Four distinct roles are enforced across all application operations:

```text
====================================================================================================
CAPABILITY / ENDPOINT AREA                          VIEWER       OPERATOR       ADMIN        OWNER
====================================================================================================
First-Run Setup (When users = 0)                    PUBLIC       PUBLIC        PUBLIC       PUBLIC
Dashboard & Health Telemetry                           ✔             ✔            ✔            ✔
----------------------------------------------------------------------------------------------------
Campaigns: List & View Details                         ✔             ✔            ✔            ✔
Campaigns: Create & Edit Drafts                        ✖             ✔            ✔            ✔
Campaigns: State Transition (RUN, PAUSE)               ✖             ✔            ✔            ✔
Campaigns: Upgrade Pinned Template Version             ✖             ✔            ✔            ✔
Campaigns: Cancel or Archive                           ✖             ✖            ✔            ✔
----------------------------------------------------------------------------------------------------
Templates: View & Test Syntax                          ✔             ✔            ✔            ✔
Templates: Create New Version                          ✖             ✔            ✔            ✔
Templates: Archive Template                            ✖             ✖            ✔            ✔
----------------------------------------------------------------------------------------------------
Contacts: Search & View (Masked E.164)                 ✔             ✔            ✔            ✔
Contacts: Create / Edit Single Contact                 ✖             ✔            ✔            ✔
Contacts: CSV Upload (Dry-Run & Commit)                ✖             ✔            ✔            ✔
Contacts: CSV Export (Full Unmasked Data)              ✖             ✖            ✔            ✔
Contacts: Hard Delete / Suppression Flush              ✖             ✖            ✖            ✔
----------------------------------------------------------------------------------------------------
Queue: Inspect Queue Items & In-Flight                 ✔             ✔            ✔            ✔
Queue: Emergency Stop Trigger (<500ms)                 ✖             ✔            ✔            ✔
Queue: Emergency Stop Reset                            ✖             ✖            ✔            ✔
Queue: Circuit Breaker Reset                           ✖             ✖            ✔            ✔
Queue: UNKNOWN_OUTCOME Manual Override                 ✖             ✖            ✔            ✔
----------------------------------------------------------------------------------------------------
WhatsApp: View Session State                           ✔             ✔            ✔            ✔
WhatsApp: QR Code View & Scan Pairing                  ✖             ✔            ✔            ✔
WhatsApp: Disconnect Browser Session                   ✖             ✔            ✔            ✔
WhatsApp: Reset Session Profile (Clear Data)           ✖             ✖            ✔            ✔
----------------------------------------------------------------------------------------------------
Runner: View Status & Heartbeat                        ✔             ✔            ✔            ✔
Runner: Request Start / Stop via Service               ✖             ✔            ✔            ✔
Runner: Supervisor Configuration                       ✖             ✖            ✔            ✔
----------------------------------------------------------------------------------------------------
Analytics: View 5 Operational Views                    ✔             ✔            ✔            ✔
Analytics: Export Telemetry Dataset                    ✖             ✔            ✔            ✔
----------------------------------------------------------------------------------------------------
Audit Trail: View Immutable Logs                       ✖             ✖            ✔            ✔
Audit Trail: Export Audit History                      ✖             ✖            ✔            ✔
----------------------------------------------------------------------------------------------------
Settings: View Configuration                           ✖             ✔            ✔            ✔
Settings: Update Workload Pacing & Limits              ✖             ✖            ✔            ✔
Settings: Danger Zone Operations                       ✖             ✖            ✖            ✔
----------------------------------------------------------------------------------------------------
User Management: List & Create Users                   ✖             ✖            ✔            ✔
User Management: Modify Roles / Deactivate             ✖             ✖            ✖            ✔
====================================================================================================
```

---

## 8. Dashboard (`/dashboard`) Architecture

### 8.1 Visual Layout & Component Hierarchy

```text
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│  HEADER: Integra Outreach Control Center  │  Active Campaign: "Q3 Retail" [RUNNING]  │ User: admin │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│  BANNER: [ALERTS / WARNINGS] (e.g., Circuit Breaker HALF_OPEN, Runner Heartbeat Healthy)          │
├────────────────────────┬────────────────────────┬────────────────────────┬───────────────────────┤
│  SYSTEM HEALTH         │  RUNNER STATUS         │  CONFIRMED SEND RATE   │  TODAY'S VOLUME       │
│  [ HEALTHY ]           │  PID: 14822            │  98.4%                 │  482 / 500            │
│  DB: OK | WhatsApp: OK │  Heartbeat: 4s ago     │  Confirmed: 482        │  Remaining: 18        │
│  Disk: 42GB free       │  Active Batch: 5/10    │  Failed: 5 | Unknown: 3│  Hourly: 48/50        │
├────────────────────────┴────────────────────────┴────────────────────────┴───────────────────────┤
│  ACTIVE CAMPAIGN PROGRESS: "Q3 Retail Promotion"                                                 │
│  Progress: [██████████████████████████████████░░░░░░░░░░] 68.2% (682 / 1,000)                    │
│  Pending: 300  │  Claimed: 10  │  Confirmed: 670  │  Failed: 12  │  Unknown: 8                   │
├──────────────────────────────────────────────────┬───────────────────────────────────────────────┤
│  REAL-TIME THROUGHPUT & WORKLOAD PACING          │  SAFETY & RESILIENCE STATUS                   │
│  Throughput: 46.2 msgs/hr                        │  Circuit Breaker: CLOSED (Fail Rate: 1.2%)    │
│  Current Pacing Delay: 48s - 82s (Jitter active) │  Emergency Stop: INACTIVE                     │
│  Next Scheduled Send: 14:22:18 UTC               │  Suppression List: 142 contacts               │
├──────────────────────────────────────────────────┴───────────────────────────────────────────────┤
│  QUICK ACTION BAR                                                                                │
│  [ ⏸ Pause Campaign ]  [ ⏹ Stop Runner ]  [ 🚨 EMERGENCY STOP ]  [ 📋 View Queue ]             │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 9. Campaigns Management (`/campaigns`)

### 9.1 Campaign Lifecycle & State Transitions
Campaign state transitions are governed strictly by the existing `CampaignManager`:
- Supported states: `DRAFT` -> `SCHEDULED` -> `RUNNING` <-> `PAUSED` -> `COMPLETED` / `CANCELLED`.
- **Decoupled Execution**: Setting a campaign to `RUNNING` in the Web UI transitions the campaign record in the database. It **never** directly launches an outreach daemon.
- If the runner is running, it will automatically claim messages from the campaign. If no runner is active, an informational prompt alerts the operator to start the runner via `/runner`.

---

## 10. Message Templates & Deterministic Library Architecture

### 10.1 The Core Problem & Architectural Invariant
In Phase 2, `campaigns.message_template` was established as an inline text column on the `campaigns` table. In Phase 7, operators need a reusable **Template Library** where templates can be authored, validated, and updated over time.

**Fundamental Architectural Question**:
> *"If Template A is edited today, what exact message will an already-created Campaign using Template A send tomorrow?"*

**Architectural Answer**:
> The already-created Campaign will send the **exact message defined at the time the campaign was created**, completely unaffected by subsequent edits to Template A.

### 10.2 Deterministic Solution: Immutable Versioned Snapshots
To guarantee 100% determinism and prevent silent drifts in outreach campaigns:

```text
┌─────────────────────────────────┐
│        message_templates        │
│  - id: "tpl_welcome"            │
│  - name: "Welcome Message"      │
│  - active_version_id: "ver_2"   │
└───────────────┬─────────────────┘
                │ 1:N
┌───────────────▼─────────────────┐
│    message_template_versions    │
├─────────────────────────────────┤
│  ver_1: "Hello {first_name}..." │ ◄── [IMMUTABLE: Created 2026-08-01]
├─────────────────────────────────┤
│  ver_2: "Hi {first_name} from..."│ ◄── [IMMUTABLE: Created 2026-09-11]
└─────────────────────────────────┘
                ▲
                │ Pinned Reference & Copied Snapshot
┌───────────────┴─────────────────┐
│            campaigns            │
├─────────────────────────────────┤
│  id: 42                         │
│  name: "August Onboarding"      │
│  template_id: "tpl_welcome"     │
│  template_version_id: "ver_1"   │ ◄── [PINNED VERSION]
│  message_template:              │ ◄── [IMMUTABLE SNAPSHOT COLUMN]
│  "Hello {first_name}..."        │     (Exact string rendered at runtime)
└─────────────────────────────────┘
```

1. **`message_templates`**: Logical entity representing the template name, description, category, and reference to the current `active_version_id`.
2. **`message_template_versions`**: **Append-only, immutable version table**. Editing a template never overwrites an existing row; it writes a new row with `version_number = N + 1`.
3. **`campaigns.message_template` (Snapshot)**: When a campaign is created, the content of the selected template version is copied directly into `campaigns.message_template`.
4. **`campaigns.template_version_id` (Reference)**: Pinned foreign key identifying the exact historical version used.
5. **Runtime Rendering Behavior**: When `QueueWorker` renders a message, it continues reading `campaign.message_template` via `MessageTemplateService`. It does **not** query `message_templates` at runtime.
6. **Explicit Upgrade Workflow**: If an operator wishes to update an existing campaign to use a newer version of a template, they must explicitly navigate to the Campaign Detail view and click **"Upgrade Template to Version N"**, which creates an explicit audit event.

---

## 11. Contacts Management & CSV Ingestion (`/contacts`)

### 11.1 Privacy-Preserving Contact Directory
- **Phone Number Masking**: By default, phone numbers are masked across all table views (e.g., `+966 ••• ••• 1234`).
- **Unmasking Authorization**: Only `ADMIN` and `OWNER` roles can unmask full numbers; every unmask action is logged in `audit_logs`.
- Searchable by name, company, tag, and last 4 digits of phone number.

### 11.2 CSV Import Workflow
1. **Upload & In-Memory Parse**: Handled via `CSVHandler.parse_contacts_csv()`.
2. **Validation & Standardization**: Every phone number validated via `PhoneValidator` into E.164 format.
3. **Dry-Run Report**: Returns total count, valid count, invalid phone errors, existing duplicates, and suppressed numbers.
4. **Commit Step**: Operator reviews dry-run summary and authorizes batch database insert.

---

## 12. Queue Management & `UNKNOWN_OUTCOME` Reconciliation (`/queue`)

### 12.1 Queue State Inspection
Surfaces live counts and paginated tables of messages partitioned by state:
`PENDING`, `CLAIMED`, `SENT` (`SEND_CONFIRMED`), `FAILED`, `RETRY_PENDING`, `SKIPPED`, `CANCELLED`, `UNKNOWN_OUTCOME`.

### 12.2 Strict In-Flight & Ambiguity Handling
- `CLAIMED` items display claiming worker ID and timestamp. Stale claims (>300s) trigger a warning badge.
- `UNKNOWN_OUTCOME` occurs when an external browser send operation was initiated, but UI checkmark confirmation could not be verified (e.g. DOM timeout, browser disconnect).

### 12.3 Authoritative Manual Reconciliation Workflow
The Web Control Center implements the exact Phase 5 manual override specification:

```text
┌────────────────────────────────────────────────────────────────────────────────┐
│  ⚠️ MANUAL RECONCILIATION: AMBIGUOUS MESSAGE OUTCOME                           │
├────────────────────────────────────────────────────────────────────────────────┤
│  Message ID:        msg_9f82c01                                                │
│  Contact:           Ahmed Al-Rashid (+966 ••• ••• 4821)                        │
│  Campaign:          Q3 Retail Promotion                                        │
│  Dispatch Attempt:  2026-09-11 14:15:22 UTC                                    │
│  Failure Reason:    DOM checkmark timeout (UNKNOWN_OUTCOME)                    │
├────────────────────────────────────────────────────────────────────────────────┤
│  CRITICAL OPERATOR WARNING:                                                    │
│  An external send action occurred, but confirmation was interrupted.           │
│  Retrying this message MAY RESULT IN DUPLICATE EXTERNAL DELIVERY.              │
│                                                                                │
│  You MUST have manually verified in WhatsApp Web on the physical phone         │
│  that the recipient did NOT receive this message before confirming.            │
├────────────────────────────────────────────────────────────────────────────────┤
│  Mandatory Explanation Reason (minimum 15 characters):                         │
│  [ Verified on physical phone chat that message was NOT sent. Error occurred.] │
│                                                                                │
│  Type "CONFIRM-NOT-DELIVERED" to authorize override:                           │
│  [ CONFIRM-NOT-DELIVERED                                                     ] │
├────────────────────────────────────────────────────────────────────────────────┤
│  [ Cancel ]                                       [ Authorize Manual Override ]│
└────────────────────────────────────────────────────────────────────────────────┘
```

#### 12.3.1 Reconciliation Semantics & Audit Schema
- **Confirmation Token**: Strictly requires typing `CONFIRM-NOT-DELIVERED`.
- **Target State**: Resets message status to `QUEUED` (`PENDING`) with `error_type = None` and updates `last_error = "[MANUAL_OVERRIDE] {reason}"`.
- **Authoritative Audit Record**: Emits `AuditLog` with `event_type = "MANUAL_RECONCILIATION_OVERRIDE"` containing:
  - `message_id`: ID of the message.
  - `previous_state`: `"FAILED"`.
  - `new_state`: `"QUEUED"`.
  - `timestamp`: UTC ISO8601 timestamp.
  - `operator_identity`: Authenticated username and user ID.
  - `explicit_override_reason`: Operator justification text.
  - `manual_verification_confirmation`: `True`.
  - `override_action`: `"MANUAL_RECONCILIATION_OVERRIDE"`.
- **Prohibitions**:
  - No generic "Force Send" buttons.
  - No blind retries without physical phone verification.
  - No claims of "DELIVERED" or "READ".

---

## 13. WhatsApp Web Session Management (`/whatsapp`)

### 13.1 Session Lifecycle & QR Code Streaming
- Supported states from `app/providers/whatsapp_web/state.py`: `DISCONNECTED`, `CONNECTING`, `AUTHENTICATING`, `CONNECTED`, `DEGRADED`, `FAILED`.
- When operator clicks **"Initiate Session"**, `WhatsAppSessionManager.start()` launches Chrome with the persistent profile.
- When state reaches `AUTHENTICATING`, the server captures the QR code canvas via screenshot and streams the base64 payload to the Web UI over SSE/HTTP.
- Once the physical phone scans the QR code, the DOM detects the chat list (`.two`), transitions to `CONNECTED`, and clears the QR display.
- Disconnect: cleanly terminates the browser, preserving profile data.
- Reset (Danger Zone): flushes `data/whatsapp_profile` for fresh re-pairing (`ADMIN`/`OWNER` only).

---

## 14. Runner Control Service (`RunnerControlService`)

### 14.1 Architectural Role & Responsibilities
`RunnerControlService` acts as the dedicated mediator between the Web API layer and the underlying `ProductionRunner`:

```python
class RunnerControlService:
    """
    Controlled interface mediating between Web API and ProductionRunner.
    Guarantees that Web API cannot spawn duplicate runners or bypass lock invariants.
    """

    def get_status(self) -> RunnerStatusDTO:
        """
        Inspects authoritative OS file lock (data/runner.lock) and database heartbeat.
        Returns: IDLE, RUNNING_HEALTHY, RUNNING_DEGRADED, or STALE_LOCK_DETECTED.
        """
        ...

    def start_runner(self, campaign_id: int, operator_id: str) -> RunnerStartResult:
        """
        Validates target campaign is RUNNING and OS file lock is free.
        Executes preflight check, then initiates runner process via process supervisor.
        """
        ...

    def stop_runner(self, operator_id: str, timeout_seconds: int = 15) -> RunnerStopResult:
        """
        Locates active PID from lockfile and sends SIGTERM.
        Awaits graceful shutdown and release of OS file lock.
        """
        ...
```

### 14.2 Concurrency & Lifecycle Guarantees
- **Authoritative Lock**: `data/runner.lock` via `ProcessLock`. The Web API will **refuse** to issue a start command if the lockfile is currently held by an active PID.
- **Independent Lifecycles**:
  - A crash or restart of the FastAPI web process **does not affect** the running `ProductionRunner`.
  - A crash of `ProductionRunner` **does not crash** the Web API. The Web UI detects the stale lock/missing heartbeat and alerts the operator.
  - The Web API process **never** holds `data/runner.lock`. Only `ProductionRunner` acquires and holds `data/runner.lock`.

---

## 15. Settings & Workload Pacing Architecture (`/settings`)

### 15.1 Flow of Settings from UI to Domain Engines

```text
Web UI (/settings)
       │
       ▼ HTTP PATCH /api/v1/settings
Web API Controller
       │
       ▼
SettingsService
       │ Writes to app_settings / campaigns table & emits AuditLog
       ▼
Existing Domain Engines (Read at runtime)
- RateLimiter (pacing intervals, daily quotas)
- FrequencyLimitService (contact cooldowns, 30-day ceilings)
- BatchManager (batch size, pause durations)
- QueueWorker (dispatch loop execution)
```

### 15.2 Comprehensive Settings Configuration Matrix

| Setting Name | UI Field Label | Validation Rules | Storage Location | Responsible Service | Runtime Consumer | Default Value | Edit Role | Audit Event |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Minimum Interval** | `Min Delay (seconds)` | Integer $\ge 5$, $\le \text{Max Delay}$ | `campaigns.min_delay_seconds` & `app_settings` (`default_min_delay`) | `SettingsService` / `CampaignManager` | `RateLimiter.calculate_interval_delay()` | `45s` | `ADMIN` | `SETTING_UPDATED` |
| **Maximum Interval** | `Max Delay (seconds)` | Integer $\ge \text{Min Delay}$, $\le 600$ | `campaigns.max_delay_seconds` & `app_settings` (`default_max_delay`) | `SettingsService` / `CampaignManager` | `RateLimiter.calculate_interval_delay()` | `90s` | `ADMIN` | `SETTING_UPDATED` |
| **Batch Size** | `Messages per Batch` | Integer $\ge 1$, $\le 50$ | `campaigns.batch_size` & `app_settings` (`default_batch_size`) | `SettingsService` / `CampaignManager` | `BatchManager.create_batch()` | `10` | `ADMIN` | `SETTING_UPDATED` |
| **Batch Pause** | `Pause Between Batches (seconds)` | Integer $\ge 30$, $\le 3600$ | `app_settings` (`batch_pause_seconds`) | `SettingsService` | `ProductionRunner.dispatch_loop()` | `300s` | `ADMIN` | `SETTING_UPDATED` |
| **Max / Contact / Day** | `Max Messages / Contact / Day` | Integer $\ge 1$, $\le 5$ | `app_settings` (`freq_max_messages_per_day`) | `SettingsService` | `FrequencyLimitService.check_frequency()` | `1` | `ADMIN` | `SETTING_UPDATED` |
| **Max / Contact / 30D**| `Max Messages / Contact / 30 Days` | Integer $\ge 1$, $\le 20$ | `app_settings` (`freq_max_messages_30d`) | `SettingsService` | `FrequencyLimitService.check_frequency()` | `5` | `ADMIN` | `SETTING_UPDATED` |
| **Cooldown Hours** | `Contact Cooldown Window (hours)` | Integer $\ge 1$, $\le 720$ | `app_settings` (`freq_cooldown_hours`) | `SettingsService` | `FrequencyLimitService.check_frequency()` | `24h` | `ADMIN` | `SETTING_UPDATED` |
| **Retry Limit** | `Max Delivery Retries` | Integer $\ge 0$, $\le 5$ | `app_settings` (`queue_max_retries`) | `SettingsService` | `RetryManager.should_retry()` | `3` | `ADMIN` | `SETTING_UPDATED` |
| **Daily Quota** | `Global Daily Message Ceiling` | Integer $\ge 1$, $\le 2000$ | `app_settings` (`global_daily_limit`) | `SettingsService` | `RateLimiter.check_daily_quota()` | `300` | `ADMIN` | `SETTING_UPDATED` |

### 15.3 Framing Compliance
All UI tooltips, input descriptions, and API documentation must refer to these settings exclusively as **"Operational Workload Management"**, **"System Throughput Pacing"**, and **"Recipient Cooldown Protection"**. Never use terms such as "anti-ban", "stealth", "detection avoidance", or "evasion".

---

## 16. Operational Analytics & Performance (`/analytics`)

### 16.1 Native Integration with Phase 6 `AnalyticsService`
Surfaces the exact metrics calculated by `app/services/analytics_service.py`:

```text
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│  VIEW 1: CAMPAIGN PERFORMANCE OVERVIEW                                                           │
│  - Total Enqueued  │  Confirmed Dispatched  │  Failed Terminal  │  Skipped / Suppressed          │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│  VIEW 2: CONFIRMED SEND RATE & ACCURACY (Strict Phase 6 Formula)                                 │
│                                                                                                  │
│                         Confirmed Sends                                                          │
│  Send Rate % = ─────────────────────────────────────────────────── ✕ 100                          │
│                (Confirmed Sends + Failed Sends + Unknown Outcomes)                               │
│                                                                                                  │
│  * Note: RETRY_PENDING, SKIPPED, and CANCELLED are strictly excluded from the denominator.       │
│  * UNKNOWN_OUTCOME is explicitly included in the denominator as an unconfirmed penalty.          │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│  VIEW 3: PROVIDER & UI RELIABILITY                                                               │
│  - UI Checkmark Detection Rate (DOM evidence)                                                    │
│  - Selector Timeout Frequency  │  Navigation Latency (ms)  │  Session Reconnect Count            │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│  VIEW 4: THROUGHPUT & LATENCY DISTRIBUTION                                                       │
│  - Hourly Dispatch Volume (Bar Chart)                                                            │
│  - Latency Percentiles: p50 (3.2s), p90 (5.8s), p99 (11.4s)                                      │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│  VIEW 5: FAILURE & ERROR CATEGORIZATION                                                          │
│  - Phone Validation Error  │  Contact Unsubscribed  │  Circuit Breaker Trip  │  Unknown Outcome  │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 17. Immutable Audit Trail Inspection (`/audit`)

- Displays all records from `audit_logs` (`app/models/audit_log.py`).
- Filters: Actor, Entity Type (`CAMPAIGN`, `CONTACT`, `TEMPLATE`, `QUEUE`, `RUNNER`, `SETTING`, `SESSION`), Action, Date Range.
- Expanding a row opens a slide-over drawer showing full `payload_json` with sensitive values redacted.

---

## 18. Settings Management (`/settings`)

- Tabbed layout: General, Outreach & Pacing, Safety & Circuit Breaker, Environment (Read-Only), Danger Zone.
- Danger Zone requires `OWNER` privilege, password re-entry, and explicit typing of confirmation strings.

---

## 19. Alerts & Notification System Architecture

### 19.1 Alert Matrix & Severity Classification

| Alert ID | Severity | Trigger Condition | UI Presentation | Auto-Resolution Trigger |
| :--- | :--- | :--- | :--- | :--- |
| `ALERT_CB_TRIPPED` | **CRITICAL** | `CircuitBreaker` transitions to `OPEN`. | Red Banner + Audio Chime | Manual reset via UI or cooldown expiration into `HALF_OPEN`. |
| `ALERT_EMERGENCY_STOP` | **CRITICAL** | `EmergencyStop` signal engaged. | Full-width Flashing Red Banner | Manual reset by `ADMIN`/`OWNER`. |
| `ALERT_RUNNER_STALE` | **HIGH** | Lock held, heartbeat >30s old. | Amber Sticky Banner in Header | Runner recovers heartbeat or is restarted. |
| `ALERT_SESSION_REVOKED` | **HIGH** | WhatsApp Web session `DISCONNECTED`. | Yellow Warning Card on Dashboard | QR scan completed on `/whatsapp`. |
| `ALERT_HIGH_UNKNOWN_OUTCOME`| **MEDIUM** | Message results in `UNKNOWN_OUTCOME`. | Orange Badge on Queue Nav Item | Operator completes manual reconciliation on `/queue`. |
| `ALERT_CAMPAIGN_DEPLETED` | **INFO** | Campaign messages all terminal. | In-app Toast Notification | Operator dismisses toast. |

---

## 20. API Design & Data Contracts

### 20.1 Standardized Response Envelope
```json
{
  "success": true,
  "data": { ... },
  "error": null,
  "meta": {
    "timestamp": "2026-09-11T14:30:00.123Z",
    "correlation_id": "req_88f91a2b"
  }
}
```

### 20.2 Standardized Error Envelope
```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "INVALID_STATE_TRANSITION",
    "message": "Campaign cannot transition from PAUSED to COMPLETED directly.",
    "details": [
      { "field": "status", "issue": "Target state must be RUNNING or CANCELLED" }
    ]
  },
  "meta": {
    "timestamp": "2026-09-11T14:30:00.123Z",
    "correlation_id": "req_88f91a2b"
  }
}
```

---

## 21. Real-Time Telemetry & Event Strategy (SSE vs. Polling)

- **Selected**: **Server-Sent Events (SSE)** via `GET /api/v1/events/stream`.
- Unidirectional, low overhead, native Nginx proxy compatibility, automatic reconnection.
- Stream taxonomy: `health` (5s), `runner` (3s), `queue` (on change), `alert` (immediate).
- Fallback: Smart periodic polling (3s active / 10s idle) if SSE is interrupted.

---

## 22. Production Deployment & Process Model (`https://auto.integra-ist.com`)

### 22.1 Independent Production Process Model

```text
[ Internet Client ]
       │ HTTPS (TCP 443)
       ▼
┌─────────────────────────────────────────────────────────────┐
│ NGINX REVERSE PROXY (auto.integra-ist.com)                  │
│ - SSL/TLS 1.3 Termination (Let's Encrypt)                   │
│ - Security Headers, Static Asset Hosting                    │
│ - Proxy Pass to 127.0.0.1:8000 (proxy_buffering off)       │
└─────────────────────────────┬───────────────────────────────┘
                              │ Loopback HTTP
┌─────────────────────────────▼───────────────────────────────┐
│ PROCESS 1: FASTAPI WEB API (whatsapp-web.service)           │
│ - Supervised by systemd (User: appuser)                     │
│ - Bind: 127.0.0.1:8000 (Uvicorn ASGI)                       │
│ - Serves Web UI & REST API                                  │
│ - DOES NOT hold data/runner.lock                            │
│ - DOES NOT send outreach messages                           │
└─────────────────────────────┬───────────────────────────────┘
                              │ IPC / Signals via RunnerControlService
┌─────────────────────────────▼───────────────────────────────┐
│ PROCESS 2: PRODUCTION RUNNER (whatsapp-runner.service)      │
│ - Supervised by systemd (User: appuser)                     │
│ - Command: outreach runner start --campaign-id <id>         │
│ - Sole holder of authoritative lock: data/runner.lock       │
│ - Executes dispatch loop, pacing, and rate limiting         │
└─────────────────────────────┬───────────────────────────────┘
                              │ Selenium Chrome Controller
┌─────────────────────────────▼───────────────────────────────┐
│ PROCESS 3: WHATSAPP WEB BROWSER (Headless / Managed Chrome) │
│ - User Profile: data/whatsapp_profile                       │
│ - Managed exclusively by WhatsAppWebProvider & SessionMgr   │
└─────────────────────────────────────────────────────────────┘
```

### 22.2 Process Lifecycle Management Matrix

| Lifecycle Event | Process 1: Web API (`whatsapp-web.service`) | Process 2: Runner (`whatsapp-runner.service`) | Process 3: WhatsApp Browser |
| :--- | :--- | :--- | :--- |
| **Startup** | Started at boot by systemd. Checks DB connectivity; does NOT touch runner lock. | Started on demand via `RunnerControlService` or systemd. Acquires `data/runner.lock`. | Launched by Runner upon starting dispatch loop, or by Web API temporarily for QR pairing. |
| **Orderly Shutdown** | Systemd sends `SIGTERM`. Flushes active SSE streams; shuts down cleanly in <3s. | Systemd / Web sends `SIGTERM`. Finishes in-flight message at safe point; releases lock. | Closed cleanly by runner shutdown hook. Cookies and profile saved to disk. |
| **Process Crash** | Systemd automatically restarts Web API (`Restart=always`). Runner continues running! | Runner dies; lockfile becomes stale. Web API detects dead PID via `get_status()`. | Orphaned Chrome cleaned up on next runner startup preflight. |
| **Log Separation** | Logs to `logs/app.json.log` with `component="web_api"` & systemd journal. | Logs to `logs/app.json.log` with `component="runner"` & systemd journal. | Chrome driver logs directed to `logs/chromedriver.log`. |

---

## 23. Security Architecture & Hardening Matrix (OWASP Top 10)

| Security Domain / Threat | Vulnerability Vector | Phase 7 Defense & Mitigation Architecture |
| :--- | :--- | :--- |
| **First-Run Race Condition** | Two operators hitting `/setup` concurrently. | Immediate transactional lock with `with_for_update()`; second request rejected with `409 Conflict`; permanently disabled once 1 user exists. |
| **Session Fixation** | Session ID reused across unauthenticated & authenticated states. | Session ID regenerated immediately upon successful authentication; old session destroyed. |
| **Session Invalidation** | Zombie sessions remaining valid after logout. | `POST /api/v1/auth/logout` explicitly deletes the session record from `user_sessions` table in DB. |
| **Concurrent Sessions** | Compromised credentials used across multiple devices. | Enforces a hard ceiling of max 2 concurrent active sessions per user; oldest automatically revoked. |
| **CSRF** | Cross-site forged requests modifying campaign or runner state. | `SameSite=Lax` cookies combined with custom header `X-CSRF-Token` verified on all mutating requests. |
| **Brute-Force Attacks** | Credential stuffing on login endpoint. | Nginx rate limiting (5 req/min/IP) + application lockout (5 failed attempts locks user for 15 minutes) + 1s delay. |
| **Privilege Escalation** | Low-privilege user attempting admin actions. | Strict RBAC dependency injection checking role against DB on every request. |
| **Runner Control Auth** | Unauthorized start/stop of production runner. | Restricted to `OPERATOR`, `ADMIN`, `OWNER`. Blocked for `VIEWER`. Audited. |
| **Emergency Stop Auth** | Malicious or accidental emergency stop engagement/reset. | `OPERATOR`, `ADMIN`, `OWNER` can trigger; only `ADMIN` or `OWNER` can reset. Audited. |
| **UNKNOWN_OUTCOME Override** | Operator blindly re-queuing ambiguous messages. | Restricted to `ADMIN` and `OWNER`. Requires typing exact `CONFIRM-NOT-DELIVERED` token and written reason. |
| **Template Modification Auth**| Unauthorized tampering with message templates. | Modifying templates restricted to `OPERATOR`, `ADMIN`, `OWNER`. Creates immutable new version. |
| **Settings Modification Auth**| Changing pacing or safety limits arbitrarily. | Restricted to `ADMIN` and `OWNER`. Danger zone restricted strictly to `OWNER`. |

---

## 24. Database Schema Impact & Migration Plan

### 24.1 Preservation of Phase 1–6 Tables
**Zero modifications to existing tables**: `campaigns` (adds snapshot columns via safe nullable addition), `contacts`, `campaign_contacts`, `messages`, `campaign_batches`, `send_sessions`, `unsubscribes`, `audit_logs`, `app_settings`.

### 24.2 Proposed Phase 7 Schema Additions
Introduced via migration `phase_7_web_control_center.py`:

```sql
-- 1. Users Table
CREATE TABLE users (
    id VARCHAR(36) PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL, -- 'OWNER', 'ADMIN', 'OPERATOR', 'VIEWER'
    is_active BOOLEAN NOT NULL DEFAULT 1,
    last_login_at DATETIME NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
CREATE INDEX ix_users_username ON users(username);
CREATE INDEX ix_users_email ON users(email);

-- 2. User Sessions Table
CREATE TABLE user_sessions (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_token_hash VARCHAR(64) UNIQUE NOT NULL,
    ip_address VARCHAR(45) NOT NULL,
    user_agent VARCHAR(255) NOT NULL,
    expires_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL,
    last_active_at DATETIME NOT NULL
);
CREATE INDEX ix_user_sessions_token ON user_sessions(session_token_hash);
CREATE INDEX ix_user_sessions_user_id ON user_sessions(user_id);
CREATE INDEX ix_user_sessions_expires_at ON user_sessions(expires_at);

-- 3. Message Template Entities
CREATE TABLE message_templates (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description VARCHAR(255) NULL,
    active_version_id VARCHAR(36) NULL, -- Points to current active version
    created_by VARCHAR(36) NOT NULL REFERENCES users(id),
    is_archived BOOLEAN NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);

-- 4. Immutable Message Template Versions
CREATE TABLE message_template_versions (
    id VARCHAR(36) PRIMARY KEY,
    template_id VARCHAR(36) NOT NULL REFERENCES message_templates(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    content TEXT NOT NULL,
    variables_json TEXT NOT NULL,
    created_by VARCHAR(36) NOT NULL REFERENCES users(id),
    created_at DATETIME NOT NULL,
    UNIQUE(template_id, version_number)
);
CREATE INDEX ix_template_versions_lookup ON message_template_versions(template_id, version_number);
```

### 24.3 Session Expiration Cleanup Strategy
- A lightweight scheduled cleanup function runs on FastAPI startup and periodically every 1 hour, issuing `DELETE FROM user_sessions WHERE expires_at < CURRENT_TIMESTAMP`.

### 24.4 Migration Rollback Guarantee
- Alembic `downgrade()` cleanly drops `message_template_versions`, `message_templates`, `user_sessions`, and `users`. Zero impact on Phase 1–6 data.

---

## 25. Dependency Impact Analysis

All proposed dependencies are verified compatible with Python 3.8+:
- **`fastapi`** (`>=0.115.0`): ASGI web framework with Pydantic v2 support.
- **`uvicorn[standard]`** (`>=0.32.0`): High-performance production ASGI server.
- **`python-multipart`** (`>=0.0.12`): Multipart form parser for CSV contact uploads.
- **`bcrypt`** (`>=4.2.0`): Secure password hashing implementation for `BcryptPasswordHasher`.

---

## 26. Comprehensive Testing & Verification Strategy

- **Unit Tests (`tests/web/unit/`)**: Schema validation, `PasswordHasher` verification, RBAC privilege matrix (testing every role against every permission), template versioning determinism.
- **API Integration Tests (`tests/web/api/`)**: First-run bootstrap, login/logout, CSRF header rejection, campaign creation with template snapshot, CSV dry-run/commit, `UNKNOWN_OUTCOME` override requiring `CONFIRM-NOT-DELIVERED`.
- **Runner Control Tests (`tests/web/runner/`)**: Concurrency checks ensuring Web API cannot spawn duplicate runners; OS file lock respect.
- **Regression Safety**: All 219 existing tests must pass with 100% success; project coverage remains $\ge 90\%$.

---

## 27. Migration, Rollout & Recovery Strategy

1. Run `alembic upgrade head` to add Phase 7 tables.
2. Navigate to `https://auto.integra-ist.com/setup` (or run CLI bootstrap).
3. Create initial `OWNER` account with strong password. Setup permanently disables.
4. Verify system readiness report on `/dashboard`.
5. Pair WhatsApp session on `/whatsapp` via QR stream.
6. Configure workload pacing in `/settings`.
7. Launch campaign and monitor real-time telemetry.

---

## 28. Risks, Hazards & Mitigation Controls

| Identified Risk / Hazard | Probability | Impact | Architectural Mitigation Control |
| :--- | :---: | :---: | :--- |
| **Browser Session Contention** | Medium | Critical | Web API never launches send browser; runner owns active outreach browser exclusively. |
| **Accidental Message Blast** | Low | High | Pacing settings enforce mandatory intervals; campaign run state transition separated from runner start. |
| **Duplicate Outreach via Ambiguity** | Medium | High | `UNKNOWN_OUTCOME` cannot be retried without physical phone verification and typing `CONFIRM-NOT-DELIVERED`. |
| **Competing Runner Processes** | Low | Critical | `RunnerControlService` inspects authoritative OS file lock (`data/runner.lock`); refuses duplicate launch. |
| **Silent Template Mutation Drift** | High | High | Immutable template versioning + campaign template snapshot ensures existing campaigns never change content. |

---

## 29. Future Extension Points (Post-Phase 7)

- Multi-Factor Authentication (TOTP / Google Authenticator).
- External Alert Webhooks (Slack, PagerDuty, CRM).
- Two-Way Conversational Chat Inbox for operator manual replies.
- Multi-Account Phone Session Rotation across separate Chrome profiles.

---

## 30. Phased Implementation Roadmap (Phases 7.1–7.6)

1. **Phase 7.1**: Foundation, `PasswordHasher`, User & Session Models, Alembic Migration, First-Run Bootstrap, Auth & RBAC API.
2. **Phase 7.2**: Core Domain REST API (Campaigns with template snapshots, Template Library with immutable versions, Contacts, CSV Ingestion).
3. **Phase 7.3**: `RunnerControlService`, WhatsApp Session Management (QR streaming), and Settings Service (Pacing controls).
4. **Phase 7.4**: Responsive Web User Interface (Dashboard, Campaigns, Templates, Contacts, Queue, Settings).
5. **Phase 7.5**: Real-Time Telemetry (SSE stream, live dashboard counters, alert notification drawer).
6. **Phase 7.6**: End-to-End Testing, Security Hardening, Regression Pass, and Production Runbook update.

---

## 31. Acceptance Criteria Checklist

Phase 7 will be considered complete and ready for human approval only when:
- [ ] **No Delivery Claims**: The system and UI contain zero claims of recipient delivery or read receipts. Terms `CONFIRMED DELIVERED`, `DELIVERY RATE`, `READ` are completely absent.
- [ ] **`SEND_CONFIRMED` Semantics**: `SEND_CONFIRMED` strictly signifies UI confirmation of send acceptance.
- [ ] **Deterministic Template Versioning**: `message_template_versions` is immutable. Editing a template creates a new version. Existing campaigns use their immutable snapshot and pinned version, remaining 100% unaffected by subsequent template edits.
- [ ] **Authoritative `UNKNOWN_OUTCOME` Override**: Reconciling `UNKNOWN_OUTCOME` requires typing exact token `CONFIRM-NOT-DELIVERED` and providing a verified reason. No blind retries or generic force sends.
- [ ] **Workload Pacing Flow**: All 9 pacing and limit settings flow cleanly through `SettingsService` into existing domain services (`RateLimiter`, `FrequencyLimitService`, `BatchManager`).
- [ ] **`RunnerControlService` & Single Runner**: Web API cannot spawn duplicate runners; authoritative OS file lock (`data/runner.lock`) is strictly respected.
- [ ] **Independent Process Lifecycles**: Web API and `ProductionRunner` operate as independent systemd processes. Web API restart does not disrupt runner; runner crash does not take down Web API.
- [ ] **Secure First-Run Bootstrap**: Setup endpoint is available only when 0 users exist, handles concurrent attempts safely, requires strong password, and permanently locks after OWNER creation.
- [ ] **No Default Credentials**: Zero hardcoded or default credentials exist.
- [ ] **Complete Security & Audit Coverage**: All mutating actions, logins, overrides, and runner control actions emit structured records to `audit_logs`.
- [ ] **Regression & Coverage**: All 219 existing tests pass cleanly; project-wide coverage remains $\ge 90\%$.

---

## 32. Explicit Out-of-Scope Items

1. **Multi-Runner Concurrent Execution**: The system remains single-runner / single-campaign.
2. **Multi-Channel Messaging**: No SMS, Email, or RCS adapters.
3. **Automated Anti-Ban / CAPTCHA Bypass**: Strictly zero evasion, human mimicry, or stealth mechanisms.
4. **Public Multi-Tenant SaaS Billing**: No public self-registration or billing gateways.
5. **Automated AI Inbound Responders**: No automated chatbot responding to recipient replies.
