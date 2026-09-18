# Migraflow — Complete REST API Reference (`API_DOCUMENTATION.md`)

This document provides a comprehensive, production-grade reference for **100% of all REST & WebSocket API endpoints** provided by the **Migraflow Control Plane Backend**.

---

## 📋 Overview of API Architecture

- **Base URL**: `http://localhost:8000/api/v1` (or production host URL)
- **Interactive OpenAPI Documentation**: `http://localhost:8000/docs` (Swagger UI) & `http://localhost:8000/redoc` (ReDoc)
- **Authentication Mechanisms**:
  1. **User JWT Bearer Token / Cookie**: Used by Web UI Frontend requests (`Authorization: Bearer <token>` or HTTP-only cookies).
  2. **Agent Bearer Token (`X-Agent-Token`)**: Used by local Docker Agents (`X-Agent-Token: ag_live_...`).
  3. **Public**: Open access for authentication & health check routes.

---

## 🗂️ Table of Contents

1. [Authentication & Users API (`/api/v1/auth` & `/api/v1/users`)](#1-authentication--users-api-apiv1auth--apiv1users)
2. [Data Sources API (`/api/v1/data-sources`)](#2-data-sources-api-apiv1data-sources)
3. [Agents Management API (`/api/v1/agents`)](#3-agents-management-api-apiv1agents)
4. [Metadata Introspection API (`/api/v1/metadata`)](#4-metadata-introspection-api-apiv1metadata)
5. [AI Migration Plans API (`/api/v1/plans`)](#5-ai-migration-plans-api-apiv1plans)
6. [ETL Execution & Task Polling API (`/api/v1/executions` & `/api/v1/plans/{id}/execute`)](#6-etl-execution--task-polling-api-apiv1executions--apiv1plansidexecute)
7. [System & Health API](#7-system--health-api)
8. [Complete API Summary Matrix](#8-complete-api-summary-matrix)

---

## 1. Authentication & Users API (`/api/v1/auth` & `/api/v1/users`)

Handles user registration, authentication, token refresh, HTTP-only cookies, Google OAuth 2.0 single sign-on, and user profile management.

---

### 1.1 User Registration
- **HTTP Method & Path**: `POST /api/v1/auth/register`
- **Purpose**: Registers a new user account, hashes password via bcrypt, issues access (15 min) and refresh (7 day) JWT tokens, and attaches tokens via HTTP-only cookies.
- **Where Used**: Called by Web UI Signup Page (`/signup`).
- **Auth**: Public.
- **Request Body**:
```json
{
  "email": "user@example.com",
  "password": "SecurePassword123!",
  "full_name": "Jane Doe"
}
```
- **Response** (`201 Created`):
```json
{
  "message": "User account created successfully.",
  "user": {
    "id": "c831a839-eb48-4ba2-8bbd-f5ff75234bd9",
    "email": "user@example.com",
    "full_name": "Jane Doe",
    "is_active": true,
    "created_at": "2026-08-18T10:00:00Z"
  }
}
```

---

### 1.2 User Login
- **HTTP Method & Path**: `POST /api/v1/auth/login`
- **Purpose**: Authenticates credentials, generates JWT access and refresh tokens, and attaches HTTP-only cookies.
- **Where Used**: Called by Web UI Login Page (`/login`).
- **Auth**: Public.
- **Request Body**:
```json
{
  "email": "user@example.com",
  "password": "SecurePassword123!"
}
```
- **Response** (`200 OK`):
```json
{
  "message": "Login successful.",
  "user": {
    "id": "c831a839-eb48-4ba2-8bbd-f5ff75234bd9",
    "email": "user@example.com",
    "full_name": "Jane Doe"
  }
}
```

---

### 1.3 Initiate Google OAuth Login
- **HTTP Method & Path**: `GET /api/v1/auth/google/login`
- **Purpose**: Generates a Google OAuth 2.0 authorization URL with a secure state token for CSRF protection.
- **Where Used**: Called when clicking "Sign in with Google" on Web UI.
- **Auth**: Public.
- **Response** (`307 Temporary Redirect`): Redirects user to Google OAuth consent screen.

---

### 1.4 Google OAuth Callback
- **HTTP Method & Path**: `GET /api/v1/auth/google/callback`
- **Purpose**: Handles code callback from Google, exchanges authorization code for Google ID Token, registers or signs in user, and issues HTTP-only session cookies.
- **Where Used**: Google OAuth redirect handler.
- **Auth**: Public.

---

### 1.5 Sign In with Google ID Token
- **HTTP Method & Path**: `POST /api/v1/auth/google`
- **Purpose**: Verifies Google credential ID token sent from frontend Google One Tap widget, creating user or logging them in.
- **Where Used**: Called by frontend Google One Tap component.
- **Auth**: Public.
- **Request Body**:
```json
{
  "credential": "eyJhbGciOiJSUzI1Ni..."
}
```
- **Response** (`200 OK`): TokenResponse with user object.

---

### 1.6 User Logout
- **HTTP Method & Path**: `POST /api/v1/auth/logout`
- **Purpose**: Invalidates HTTP-only session cookies (`access_token` and `refresh_token`).
- **Where Used**: Called when clicking "Log Out" in Web UI header.
- **Auth**: Public / User JWT.
- **Response** (`200 OK`): `{"message": "Successfully logged out"}`

---

### 1.7 Refresh Access Token
- **HTTP Method & Path**: `POST /api/v1/auth/refresh`
- **Purpose**: Reads HTTP-only `refresh_token` cookie and issues a new 15-minute `access_token` cookie.
- **Where Used**: Automatically called by frontend HTTP interceptor on 401 token expiry.
- **Auth**: Refresh Token Cookie.
- **Response** (`200 OK`): `{"message": "Access token refreshed successfully"}`

---

### 1.8 Get Current User Profile
- **HTTP Method & Path**: `GET /api/v1/users/me`
- **Purpose**: Returns current active user profile information.
- **Where Used**: Called on Web UI load to check session validity and display user info.
- **Auth**: User JWT.
- **Response** (`200 OK`):
```json
{
  "id": "c831a839-eb48-4ba2-8bbd-f5ff75234bd9",
  "email": "user@example.com",
  "full_name": "Jane Doe",
  "is_active": true
}
```

---

### 1.9 Update User Profile
- **HTTP Method & Path**: `PUT /api/v1/users/me`
- **Purpose**: Updates current user's profile details (full name, password).
- **Where Used**: Web UI Account Settings page.
- **Auth**: User JWT.

---

### 1.10 Delete User Account
- **HTTP Method & Path**: `DELETE /api/v1/users/me`
- **Purpose**: Permanently deletes user account and associated workspace data.
- **Where Used**: Web UI Account Deletion modal.
- **Auth**: User JWT.

---

### 1.11 List All Users (Admin)
- **HTTP Method & Path**: `GET /api/v1/users`
- **Purpose**: Returns paginated list of registered users.
- **Where Used**: Admin Dashboard.
- **Auth**: Superuser JWT.

---

### 1.12 Get User by ID (Admin)
- **HTTP Method & Path**: `GET /api/v1/users/{user_id}`
- **Purpose**: Retrieves user details by UUID.
- **Where Used**: Admin User Management page.
- **Auth**: Superuser JWT.

---

## 2. Data Sources API (`/api/v1/data-sources`)

Manages data source entities attached to agents.

---

### 2.1 Create Data Source
- **HTTP Method & Path**: `POST /api/v1/data-sources`
- **Purpose**: Creates a Data Source entity (PostgreSQL, MySQL, MongoDB, CSV, Excel) in user workspace.
- **Where Used**: Web UI Data Source setup wizard.
- **Auth**: User JWT.
- **Request Body**:
```json
{
  "name": "Production Postgres Monolith",
  "type": "postgresql",
  "role": "source",
  "identifier": "src_db_1"
}
```
- **Response** (`201 Created`):
```json
{
  "id": "a918e742-9908-4122-8110-091a11812831",
  "name": "Production Postgres Monolith",
  "type": "postgresql",
  "role": "source",
  "identifier": "src_db_1"
}
```

---

### 2.2 List Data Sources for Agent
- **HTTP Method & Path**: `GET /api/v1/data-sources/agent/{agent_id}`
- **Purpose**: Lists all Data Sources attached to a specific Agent.
- **Where Used**: Web UI Agent detail view (`/agents/[id]`).
- **Auth**: User JWT.

---

### 2.3 Get Data Source by ID
- **HTTP Method & Path**: `GET /api/v1/data-sources/{source_id}`
- **Purpose**: Retrieves Data Source details.
- **Where Used**: Web UI Data Source view.
- **Auth**: User JWT.

---

### 2.4 Update Data Source
- **HTTP Method & Path**: `PUT /api/v1/data-sources/{source_id}`
- **Purpose**: Updates Data Source name or configuration parameters.
- **Where Used**: Web UI Data Source edit modal.
- **Auth**: User JWT.

---

### 2.5 Delete Data Source
- **HTTP Method & Path**: `DELETE /api/v1/data-sources/{source_id}`
- **Purpose**: Deletes a Data Source entity.
- **Where Used**: Web UI Data Source deletion button.
- **Auth**: User JWT.

---

## 3. Agents Management API (`/api/v1/agents`)

Manages Agent lifecycles, CLI commands, status heartbeats, WebSocket streaming, and task polling.

---

### 3.1 Create Agent & Generate Docker Run Commands
- **HTTP Method & Path**: `POST /api/v1/agents`
- **Purpose**: Creates Agent entity, generates raw API token (`ag_live_...`), attaches data sources, and builds Docker/PowerShell run commands.
- **Where Used**: Web UI Create Agent wizard (`/agents/new`).
- **Auth**: User JWT.
- **Request Body**:
```json
{
  "name": "E-Commerce Migration Agent",
  "agent_identifier": "agent_prod_01",
  "data_sources": [
    { "name": "Monolith DB", "type": "postgresql", "role": "source", "identifier": "src_db_1" },
    { "name": "Target DB", "type": "postgresql", "role": "target", "identifier": "dest_db_4" }
  ]
}
```
- **Response** (`201 Created`):
```json
{
  "id": "38207dfd-7f8f-4d73-ae4a-9ffd8a06053a",
  "name": "E-Commerce Migration Agent",
  "agent_token": "ag_live_MnZp-FVhM9RVW1ereyWifVPi87Pi91ftY_GSkA_7rXE",
  "status": "pending",
  "docker_run_command": "docker run -d -e AGENT_TOKEN=ag_live_... data-migration-agent:latest"
}
```

---

### 3.2 List User Agents
- **HTTP Method & Path**: `GET /api/v1/agents`
- **Purpose**: Returns list of user's Agents with real-time status (`pending`, `online`, `offline`).
- **Where Used**: Web UI Agents Dashboard page (`/agents`).
- **Auth**: User JWT.

---

### 3.3 Regenerate Docker CLI Command
- **HTTP Method & Path**: `GET /api/v1/agents/{agent_id}/docker-command`
- **Purpose**: Regenerates ready-to-run Docker and PowerShell commands for an agent.
- **Where Used**: Web UI "View Run Command" modal.
- **Auth**: User JWT.

---

### 3.4 Regenerate Agent API Token
- **HTTP Method & Path**: `POST /api/v1/agents/{agent_id}/regenerate-token`
- **Purpose**: Rotates an agent's API authentication token. Generates a new cryptographically secure token (`ag_live_...`), updates the SHA-256 digest in `api_token_hash`, and immediately invalidates any running container using the old token (`401 Unauthorized`). Returns the new raw token once only, embedded in freshly generated Docker CLI commands and `.env` template.
- **Where Used**: Web UI "View Docker Setup Commands" modal ("Regenerate Agent Token" action).
- **Auth**: User JWT (verifies agent ownership).
- **Response** (`200 OK`):
```json
{
  "id": "38207dfd-7f8f-4d73-ae4a-9ffd8a06053a",
  "name": "E-Commerce Migration Agent",
  "agent_identifier": "agent_prod_01",
  "api_token": "ag_live_9xQ8...new_secret_token...",
  "docker_command": "docker run -d --name agent_prod_01 -e AGENT_TOKEN=\"ag_live_9xQ8...\" ...",
  "docker_command_powershell": "docker run -d --name agent_prod_01 `\n  -e AGENT_TOKEN=\"ag_live_9xQ8...\" ...",
  "docker_command_oneline": "docker run -d --name agent_prod_01 -e AGENT_TOKEN=\"ag_live_9xQ8...\" ...",
  "env_template": "AGENT_TOKEN=ag_live_9xQ8...\nBACKEND_URL=http://localhost:8000 ...",
  "status": "offline",
  "token_last_regenerated_at": "2026-09-09T17:30:00Z"
}
```

---

### 3.5 Get Agent Details by ID
- **HTTP Method & Path**: `GET /api/v1/agents/{agent_id}`
- **Purpose**: Fetches agent details, data source list, and status.
- **Where Used**: Web UI Agent detail page (`/agents/[id]`).
- **Auth**: User JWT.

---

### 3.6 Update Agent Configuration
- **HTTP Method & Path**: `PUT /api/v1/agents/{agent_id}`
- **Purpose**: Renames agent or updates configuration settings.
- **Where Used**: Web UI Edit Agent settings.
- **Auth**: User JWT.

---

### 3.7 Agent Status Heartbeat Sync
- **HTTP Method & Path**: `POST /api/v1/agents/heartbeat`
- **Purpose**: Periodic heartbeat sent by local Docker Agent to report container online status, version, stopping errors, and database health. Returns backend control directives (`action` and `action_reason`) for dynamic heartbeat throttling (standby mode) or container shutdown.
- **Where Used**: Local Docker Agent background loop (`main.py`) — runs every 20s in active mode, or 300s (5-min) in idle standby mode.
- **Auth**: Agent Token (`X-Agent-Token` header).
- **Request Body**:
```json
{
  "status": "online",
  "version": "1.0.1",
  "data_sources": [
    {
      "identifier": "src_db_1",
      "is_healthy": true,
      "latency_ms": 4.2,
      "database_name": "ecommerce_prod"
    }
  ],
  "error_message": null,
  "error_category": null
}
```
- **Response** (`200 OK`):
```json
{
  "id": "38207dfd-7f8f-4d73-ae4a-9ffd8a06053a",
  "name": "E-Commerce Migration Agent",
  "agent_identifier": "agent_prod_01",
  "status": "online",
  "version": "1.0.1",
  "last_seen_at": "2026-09-08T12:00:00Z",
  "last_error": null,
  "error_category": null,
  "last_error_at": null,
  "action": "ENTER_IDLE_MODE",
  "action_reason": "Agent idle for 300s — entering low-power standby mode."
}
```
*Possible `action` directives:*
- `null`: Continue regular 20s heartbeat cadence.
- `"ENTER_IDLE_MODE"`: Agent switches heartbeat interval to 300s (5-min) standby.
- `"RESUME_ACTIVE_MODE"`: Agent resumes 20s heartbeat upon job dispatch.
- `"SHUTDOWN"`: Agent gracefully terminates container via `os._exit(0)`.

---

### 3.7 Agent Real-Time WebSocket Channel
- **HTTP Method & Path**: `WS /api/v1/agents/ws/{agent_id}`
- **Purpose**: Bi-directional WebSocket connection for streaming live agent logs, progress updates, and connection status events.
- **Where Used**: Web UI live monitoring page.
- **Auth**: Query parameter token / session.

---

### 3.8 Agent Task Polling
- **HTTP Method & Path**: `GET /api/v1/agents/tasks`
- **Purpose**: Polled by local Docker Agent to discover queued execution jobs.
- **Atomic Job Claiming (`SKIP LOCKED`)**: To prevent race conditions and duplicate execution when multiple agent containers share credentials, this endpoint uses PostgreSQL row-level locking (`.with_for_update(skip_locked=True)`). Returned jobs are atomically transitioned from `status = 'queued'` to `status = 'preparing'` within the same transaction, guaranteeing single-consumer task assignment.
- **Where Used**: Local Docker Agent background task loop (`main.py`).
- **Auth**: Agent Token (`X-Agent-Token` header).
- **Response** (`200 OK`):
```json
[
  {
    "job_id": "5fe8eedf-0a2a-44ed-ac64-ba2c024bbb17",
    "migration_plan_id": "52458d8e-889e-4244-91ec-364641262889",
    "status": "preparing",
    "created_at": "2026-09-08T12:00:00Z"
  }
]
```

---

### 3.9 Report Agent Fatal Stopping Error (Emergency Endpoint)
- **HTTP Method & Path**: `POST /api/v1/agents/fatal-error`
- **Purpose**: Emergency reporting endpoint used by Docker agent when stopping due to startup crashes, authentication rejection (401/403), missing configurations, or unhandled exceptions. Does not require an authenticated bearer token; resolves agent identity via `X-Agent-ID` header or `agent_id` query parameter.
- **Where Used**: Agent `report_fatal_error_and_exit()` in `apps/agent/main.py`.
- **Auth**: Public / Header `X-Agent-ID` (or query param `?agent_id=...`).
- **Request Body**:
```json
{
  "error_message": "Invalid AGENT_TOKEN: Backend rejected credentials with 401 Unauthorized.",
  "error_category": "AUTH_ERROR"
}
```
- **Response** (`200 OK`): `AgentResponse` object with `status: "error"`, `last_error`, `error_category`, and `last_error_at`.

---

### 3.10 Delete Agent
- **HTTP Method & Path**: `DELETE /api/v1/agents/{agent_id}`
- **Purpose**: Deletes Agent entity and cascade-detaches data sources.
- **Where Used**: Web UI Delete Agent button.
- **Auth**: User JWT.

---

## 4. Metadata Introspection API (`/api/v1/metadata`)

Manages structural metadata snapshots enforcing **Zero Raw Data Policy**.

---

### 4.1 Sync Metadata Snapshot
- **HTTP Method & Path**: `POST /api/v1/metadata/sync`
- **Purpose**: Ingests structural metadata snapshots (table schemas, columns, PKs, FKs, row counts) introspected by local Docker Agent.
- **Where Used**: Local Docker Agent after container startup (`metadata_engine.py`).
- **Auth**: Agent Token (`X-Agent-Token` header).
- **Response** (`201 Created`): MetadataSnapshotDetailResponse object.

---

### 4.2 List Snapshots for Data Source
- **HTTP Method & Path**: `GET /api/v1/metadata/sources/{source_id}/snapshots`
- **Purpose**: Returns historical list of metadata snapshots for a Data Source.
- **Where Used**: Web UI Schema history tab.
- **Auth**: User JWT.

---

### 4.3 Get Latest Snapshot for Data Source
- **HTTP Method & Path**: `GET /api/v1/metadata/sources/{source_id}/snapshots/latest`
- **Purpose**: Retrieves the most recent structural metadata snapshot for a Data Source.
- **Where Used**: Web UI Data Source schema inspector.
- **Auth**: User JWT.

---

### 4.4 Get Snapshot Detail by ID
- **HTTP Method & Path**: `GET /api/v1/metadata/snapshots/{snapshot_id}`
- **Purpose**: Returns complete schema tree for a specific snapshot ID.
- **Where Used**: Web UI Schema Explorer modal.
- **Auth**: User JWT.

---

## 5. AI Migration Plans API (`/api/v1/plans`)

Handles AI plan generation using Google Gemini 3.5 Flash Lite and AST plan retrieval/customization.

---

### 5.1 Generate AI Migration Plan
- **HTTP Method & Path**: `POST /api/v1/plans/generate`
- **Purpose**: Serializes metadata snapshots to zero-raw-data context, calls Gemini AI model, parses output `TransformationPlanAST`, and persists `MigrationPlan` entity.
- **Where Used**: Web UI "Generate Migration Plan" button.
- **Auth**: User JWT.
- **Request Body**:
```json
{
  "agent_id": "38207dfd-7f8f-4d73-ae4a-9ffd8a06053a",
  "target_database_config": {
    "database_type": "postgresql",
    "custom_instructions": "Merge legacy_users and customers into users table matching on email."
  }
}
```
- **Response** (`201 Created`): PlanDetailResponse object.

---

### 5.2 List User Migration Plans
- **HTTP Method & Path**: `GET /api/v1/plans`
- **Purpose**: Lists all Migration Plans generated by user.
- **Where Used**: Web UI Migration Plans page (`/plans`).
- **Auth**: User JWT.

---

### 5.3 Get Migration Plan by ID
- **HTTP Method & Path**: `GET /api/v1/plans/{plan_id}`
- **Purpose**: Returns full `TransformationPlanAST` JSON blueprint.
- **Where Used**: 
  1. Web UI Plan Reviewer page.
  2. Local Docker Agent (authenticated via `X-Agent-Token`) before starting local ETL.
- **Auth**: User JWT **OR** Agent Token (`X-Agent-Token`).

---

### 5.4 Update Plan Data (User Customization)
- **HTTP Method & Path**: `PUT /api/v1/plans/{plan_id}`
- **Purpose**: Allows users to customize table/column mappings, DDL SQL, or deduplication rules in Web UI before execution. Re-evaluates feasibility validator.
- **Where Used**: Web UI Visual Plan Editor ("Save Plan Changes").
- **Auth**: User JWT.

---

### 5.5 Refine Migration Plan with Natural Language Prompt Feedback
- **HTTP Method & Path**: `POST /api/v1/plans/{plan_id}/refine`
- **Purpose**: Accepts prompt feedback (e.g. *"rename table users to customer_accounts"*), invokes LLM refinement, runs feasibility validator, and updates plan.
- **Where Used**: Web UI "AI Plan Assistant / Prompt Feedback" bar.
- **Auth**: User JWT.
- **Request Body**:
```json
{
  "user_feedback": "Rename target table users to customer_accounts and drop password_hash"
}
```

---

### 5.6 Run Standalone Migration Feasibility Check
- **HTTP Method & Path**: `POST /api/v1/plans/{plan_id}/validate`
- **Purpose**: Evaluates plan blueprint against metadata snapshots and returns deterministic feasibility report (errors, warnings, explanation).
- **Where Used**: Web UI "Check Migration Feasibility" button.
- **Auth**: User JWT.

---

### 5.7 Approve Migration Plan
- **HTTP Method & Path**: `POST /api/v1/plans/{plan_id}/approve`
- **Purpose**: Marks plan as human-approved (`status: completed`), validating feasibility before approval.
- **Where Used**: Web UI Plan Approval Gateway button.
- **Auth**: User JWT.

---

### 5.8 List Migration Plan Versions
- **HTTP Method & Path**: `GET /api/v1/plans/{plan_id}/versions`
- **Purpose**: Lists all immutable historical version snapshots for a migration plan. Returns lightweight metadata (version number, creation timestamp, author, optional changelog comment) without the bulky AST payload for efficient history timeline rendering.
- **Where Used**: Web UI Plan Version History drawer / timeline (`/plans/[id]`).
- **Auth**: User JWT.
- **Response** (`200 OK`):
```json
[
  {
    "id": "8f5a11c0-3f41-4e7a-9a99-b1d55e8df1a0",
    "migration_plan_id": "52458d8e-889e-4244-91ec-364641262889",
    "version_number": 2,
    "comment": "Added customer email anonymization transformer",
    "created_by": "user_2wK...",
    "created_at": "2026-09-08T10:15:00Z"
  },
  {
    "id": "7e4b22b1-2e30-4d6a-8b88-a0c44d7ce09f",
    "migration_plan_id": "52458d8e-889e-4244-91ec-364641262889",
    "version_number": 1,
    "comment": "Initial AI generated plan blueprint",
    "created_by": "user_2wK...",
    "created_at": "2026-09-08T09:30:00Z"
  }
]
```

---

### 5.9 Get Migration Plan Version Detail
- **HTTP Method & Path**: `GET /api/v1/plans/{plan_id}/versions/{version_number}`
- **Purpose**: Retrieves full AST blueprint details (`plan_data`) of a specific historical version snapshot for read-only preview and side-by-side diffing against the active plan.
- **Where Used**: Web UI Plan Version Diff / Preview modal.
- **Auth**: User JWT.
- **Response** (`200 OK`):
```json
{
  "id": "8f5a11c0-3f41-4e7a-9a99-b1d55e8df1a0",
  "migration_plan_id": "52458d8e-889e-4244-91ec-364641262889",
  "version_number": 2,
  "comment": "Added customer email anonymization transformer",
  "created_by": "user_2wK...",
  "created_at": "2026-09-08T10:15:00Z",
  "plan_data": {
    "version": "1.0",
    "steps": [ ... ]
  }
}
```

---

### 5.10 Restore Historical Migration Plan Version
- **HTTP Method & Path**: `POST /api/v1/plans/{plan_id}/versions/{version_number}/restore`
- **Purpose**: Restores active migration plan blueprint AST to the exact state captured in historical version `{version_number}`. Automatically creates a new version snapshot documenting the rollback operation.
- **Where Used**: Web UI "Rollback to this Version" button.
- **Auth**: User JWT.
- **Response** (`200 OK`): Standard `PlanDetailResponse` containing restored `plan_data` and incremented `current_version`.

---

## 6. ETL Execution & Task Polling API (`/api/v1/executions` & `/api/v1/plans/{id}/execute`)

Handles Human-in-the-Loop plan approval, job queuing, progress reporting, failure diagnosis, and 1-click job resumption.

---

### 6.1 Trigger / Approve Plan Execution (Live or Dry Run Simulation)
- **HTTP Method & Path**: `POST /api/v1/plans/{plan_id}/execute`
- **Purpose**: **Human-in-the-Loop Gateway**. Marks plan as approved and queues a new `MigrationJob` (`status: queued`) for local Docker Agent execution. Accepts optional query parameter `is_dry_run: bool = False` or JSON request body `{"is_dry_run": true}`. In Dry Run mode, the agent tests connectivity, extracts and transforms rows, validates schema mappings, but rolls back transactions and avoids committing data to target sinks.
- **Where Used**: Web UI "Approve & Start Migration Job" and "⚡ Run Dry Run (Simulation)" buttons.
- **Auth**: User JWT.
- **Request Body** *(Optional)*:
```json
{
  "is_dry_run": false
}
```
- **Response** (`201 Created`):
```json
{
  "job_id": "5fe8eedf-0a2a-44ed-ac64-ba2c024bbb17",
  "plan_id": "52458d8e-889e-4244-91ec-364641262889",
  "status": "queued",
  "is_dry_run": false
}
```

---

### 6.2 List Execution Jobs
- **HTTP Method & Path**: `GET /api/v1/executions`
- **Purpose**: Returns list of all Migration Jobs owned by user.
- **Where Used**: Web UI Executions Dashboard (`/executions`).
- **Auth**: User JWT.

---

### 6.3 Get Execution Job Detail
- **HTTP Method & Path**: `GET /api/v1/executions/{execution_id}`
- **Purpose**: Retrieves real-time job status, row counts, progress percentage, error messages, checkpoint details, simulation flags (`is_dry_run`), and AI automated failure diagnosis (`ai_diagnosis`).
- **Where Used**: Web UI Live Execution Monitoring page (`/executions/[id]`).
- **Auth**: User JWT.
- **Response** (`200 OK`):
```json
{
  "id": "5fe8eedf-0a2a-44ed-ac64-ba2c024bbb17",
  "plan_id": "52458d8e-889e-4244-91ec-364641262889",
  "status": "failed",
  "is_dry_run": false,
  "progress": 42.5,
  "processed_rows": 425000,
  "successful_rows": 425000,
  "failed_rows": 1,
  "skipped_rows": 0,
  "total_rows": 1000000,
  "current_table": "orders",
  "current_stage": "extract_transform",
  "error_message": "Target database connection failed: password authentication failed for user 'app_user'",
  "checkpoint_data": {
    "step_id": "step_2_orders",
    "last_processed_offset": 425000
  },
  "ai_diagnosis": {
    "summary": "Target PostgreSQL authentication failed. Credentials in container environment variable DEST_DB_URL rejected.",
    "root_cause_category": "auth_failure",
    "is_user_environment_issue": true,
    "fix_steps": [
      "Verify target database password in your local .env or Docker run command.",
      "Ensure host.docker.internal is reachable from the agent container.",
      "Restart the agent container with corrected credentials."
    ],
    "copyable_fix_command": "docker run -d --name agent_xxx -e DEST_DB_URL=\"postgresql://app_user:corrected_pass@host.docker.internal:5432/target_db\" ... data-migration-agent:latest",
    "raw_error_snippet": "password authentication failed for user 'app_user'..."
  },
  "created_at": "2026-09-08T10:00:00Z",
  "updated_at": "2026-09-08T10:05:30Z"
}
```

---

### 6.4 Report Execution Progress & Errors
- **HTTP Method & Path**: `POST /api/v1/executions/{execution_id}/progress`
- **Purpose**: Updates job status, processed row counts, throughput metrics, and error stack traces sent by local Docker Agent. Broadcasts WebSocket progress events and automatically triggers background AI root-cause diagnosis if status transitions to `failed`.
- **Where Used**: Local Docker Agent [`ProgressReporter`](file:///d:/GitHub/Ai_data_migration_platform/apps/agent/engine/progress_reporter.py) after every processed chunk.
- **Auth**: Agent Token (`X-Agent-Token` header).
- **Request Body**:
```json
{
  "status": "running",
  "progress": 85.0,
  "processed_rows": 850000,
  "successful_rows": 845000,
  "failed_rows": 0,
  "skipped_rows": 5000,
  "total_rows": 1000000,
  "current_table": "users",
  "current_stage": "extract_transform",
  "error_message": null
}
```

---

### 6.5 One-Click Resume Migration Job (From Checkpoint)
- **HTTP Method & Path**: `POST /api/v1/executions/{execution_id}/resume`
- **Purpose**: Resumes a failed/interrupted migration job from the exact last saved chunk offset (`CheckpointManager`), avoiding starting from row 0.
- **Where Used**: Web UI Error Card "Resume Migration" button.
- **Auth**: User JWT.
- **Response** (`200 OK`): `{"job_id": "...", "status": "queued", "resumed_from_offset": 850000}`

---

## 7. System & Health API

---

### 7.1 Root Welcome Endpoint
- **HTTP Method & Path**: `GET /`
- **Purpose**: Returns API welcome message and documentation links.
- **Where Used**: Browser / API root check.
- **Auth**: Public.

---

### 7.2 API Health Check
- **HTTP Method & Path**: `GET /api/v1/health`
- **Purpose**: Returns backend health, database connection state, and uptime.
- **Where Used**: Kubernetes liveness/readiness probes & Docker healthchecks.
- **Auth**: Public.
- **Response** (`200 OK`):
```json
{
  "status": "healthy",
  "service": "AI Data Migration Platform API",
  "environment": "development"
}
```

---

## 8. Complete API Summary Matrix

| # | HTTP Method | Endpoint Path | Caller / Caller Role | Purpose | Auth Required |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1** | `POST` | `/api/v1/auth/register` | Web UI Signup Page | Register user account & set cookies | Public |
| **2** | `POST` | `/api/v1/auth/login` | Web UI Login Page | Authenticate user & issue JWT cookies | Public |
| **3** | `GET` | `/api/v1/auth/google/login` | Web UI Login Page | Initiate Google OAuth 2.0 flow | Public |
| **4** | `GET` | `/api/v1/auth/google/callback`| Google Redirect | Handle Google OAuth code callback | Public |
| **5** | `POST` | `/api/v1/auth/google` | Web UI Google Widget | Authenticate via Google ID Token | Public |
| **6** | `POST` | `/api/v1/auth/logout` | Web UI Header | Clear HTTP-only session cookies | Public / User JWT |
| **7** | `POST` | `/api/v1/auth/refresh` | Frontend Interceptor | Refresh access token cookie | Refresh Cookie |
| **8** | `GET` | `/api/v1/users/me` | Web UI Page Load | Fetch active user profile | User JWT |
| **9** | `PUT` | `/api/v1/users/me` | Web UI Settings | Update user profile | User JWT |
| **10**| `DELETE`| `/api/v1/users/me` | Web UI Settings | Delete user account | User JWT |
| **11**| `GET` | `/api/v1/users` | Admin Dashboard | List all users | Superuser JWT |
| **12**| `GET` | `/api/v1/users/{user_id}` | Admin Dashboard | Get user details by ID | Superuser JWT |
| **13**| `POST` | `/api/v1/data-sources` | Web UI Wizard | Register data source entity | User JWT |
| **14**| `GET` | `/api/v1/data-sources/agent/{id}`| Web UI Agent View| List data sources for an agent | User JWT |
| **15**| `GET` | `/api/v1/data-sources/{id}`| Web UI Data Source | Get data source details | User JWT |
| **16**| `PUT` | `/api/v1/data-sources/{id}`| Web UI Data Source | Update data source configuration | User JWT |
| **17**| `DELETE`| `/api/v1/data-sources/{id}`| Web UI Data Source | Delete data source entity | User JWT |
| **18**| `POST` | `/api/v1/agents` | Web UI Wizard | Create agent & Docker commands | User JWT |
| **19**| `GET` | `/api/v1/agents` | Web UI Dashboard | List user agents & status | User JWT |
| **20**| `GET` | `/api/v1/agents/{id}/docker-command`| Web UI Modal | Regenerate Docker run command | User JWT |
| **21**| `POST`| `/api/v1/agents/{id}/regenerate-token`| Web UI Modal | Rotate API token & invalidate old container | User JWT |
| **22**| `GET` | `/api/v1/agents/{id}` | Web UI Agent Detail | Get agent details by ID | User JWT |
| **23**| `PUT` | `/api/v1/agents/{id}` | Web UI Agent Settings| Update agent settings | User JWT |
| **24**| `POST` | `/api/v1/agents/heartbeat` | Local Docker Agent | Send background status heartbeat & receive control directives | Agent Token |
| **25**| `WS` | `/api/v1/agents/ws/{id}` | Web UI Live Page | Live WebSocket log/status stream | Session / Token |
| **26**| `GET` | `/api/v1/agents/tasks` | Local Docker Agent | Poll for pending execution jobs | Agent Token |
| **27**| `POST` | `/api/v1/agents/fatal-error` | Local Docker Agent | Emergency notification for fatal container stopping error | Agent Token / Header |
| **28**| `DELETE`| `/api/v1/agents/{id}` | Web UI Agent Detail | Delete agent entity | User JWT |
| **29**| `POST` | `/api/v1/metadata/sync` | Local Docker Agent | Upload zero-raw-data schema snapshot | Agent Token |
| **30**| `GET` | `/api/v1/metadata/sources/{id}/snapshots`| Web UI Schema History| List snapshots for a source | User JWT |
| **31**| `GET` | `/api/v1/metadata/sources/{id}/snapshots/latest`| Web UI Schema Inspector| Get latest snapshot for a source | User JWT |
| **32**| `GET` | `/api/v1/metadata/snapshots/{id}`| Web UI Schema Modal| Get snapshot details by ID | User JWT |
| **33**| `POST` | `/api/v1/plans/generate` | Web UI Plan Generator| Trigger Gemini AI AST generation | User JWT |
| **34**| `GET` | `/api/v1/plans` | Web UI Plans Page | List user migration plans | User JWT |
| **35**| `GET` | `/api/v1/plans/{id}` | Web UI / Docker Agent| Get AST blueprint payload | User JWT / Agent Token |
| **36**| `PUT` | `/api/v1/plans/{id}` | Web UI Plan Editor | Save customized plan mappings | User JWT |
| **37**| `POST` | `/api/v1/plans/{id}/validate` | Web UI Plan Feasibility | Run deterministic pre-flight feasibility checks | User JWT |
| **38**| `POST` | `/api/v1/plans/{id}/approve` | Web UI Plan Approval | Human approval gateway for plan blueprint | User JWT |
| **39**| `GET` | `/api/v1/plans/{id}/versions` | Web UI Version History | List historical version snapshots (metadata only) | User JWT |
| **40**| `GET` | `/api/v1/plans/{id}/versions/{v_num}` | Web UI Version Diff | Get full AST blueprint of a historical snapshot | User JWT |
| **41**| `POST` | `/api/v1/plans/{id}/versions/{v_num}/restore` | Web UI Version History | Rollback plan blueprint to a historical version | User JWT |
| **42**| `POST` | `/api/v1/plans/{id}/execute`| **Web UI Dashboard**| **Human-in-the-Loop Plan Approval & Execution (Live / Dry Run)**| User JWT |
| **43**| `GET` | `/api/v1/executions` | Web UI Dashboard | List user execution jobs | User JWT |
| **44**| `GET` | `/api/v1/executions/{id}` | Web UI Execution Page| Get execution job details, checkpoint & AI diagnosis | User JWT |
| **45**| `POST` | `/api/v1/executions/{id}/progress`| Local Docker Agent | Send chunk progress, errors & trigger AI diagnosis | Agent Token |
| **46**| `POST` | `/api/v1/executions/{id}/resume`| Web UI Error Card | One-click resume from checkpoint | User JWT |
| **47**| `GET` | `/` | Browser / Root Check| Root welcome endpoint | Public |
| **48**| `GET` | `/api/v1/health` | K8s / Health Check | API service health check | Public |
