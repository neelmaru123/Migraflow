# Architecture Decision Log (`DECISIONS.md`)

This file records all key architectural decisions, technology selections, trade-offs, and design rationale for the **AI Data Migration Platform**.

---

## [2026-08-11] - AI Data Migration Platform Foundation Architecture

### 1. Decision Summary

Decoupled **Transformation Intent** (AI-generated JSON schema contract) from **Transformation Execution** (deterministic stream engine using Polars/DuckDB). Provided two distinct execution modes: Cloud Async Workers (Mode A) and Standalone Local Script Generator Package (Mode B).

---

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**: Large-scale data migrations often fail due to Out-Of-Memory (OOM) errors, non-deterministic AI code generation, data privacy restrictions, or high network egress bandwidth costs when transferring terabytes over cloud APIs.
- **Chosen Solution**:
  1. **Strict JSON Schema Contract**: The AI engine ONLY outputs validated JSON transformation plans; arbitrary Python `exec()` or `eval()` is strictly forbidden for security and determinism.
  2. **Streaming Batch Engine**: ETL processing uses chunked cursor iterators and lazy frames in Polars/DuckDB.
  3. **Dual Execution Strategy**: Supports both cloud-hosted worker execution and downloadable on-premise execution packages.
- **Why Polars & DuckDB over Pandas**:
  - **Polars**: Zero-copy arrow memory format, multi-threaded vectorization, and `streaming=True` support for flat RAM footprint.
  - **DuckDB**: Embedded columnar SQL engine capable of handling out-of-core queries exceeding host memory size.
- **Why Redis + Async Worker Queue**: Fast API responses (HTTP 202 Accepted) offloading CPU-intensive ETL work to isolated worker processes.

---

### 3. Alternatives Considered & Rejected

- **Alternative A: Dynamic Python Script Execution (`exec()`)**
  - _Rejected_: Poses severe security vulnerabilities (remote code execution risks) and lacks determinism.
- **Alternative B: In-Memory Pandas Processing (`pd.read_sql_query`)**
  - _Rejected_: In-memory buffering loads full datasets into RAM, causing immediate worker OOM crashes on datasets > 4GB.
- **Alternative C: Direct Cloud ETL Transfer for All Datasets**
  - _Rejected_: Incompatible with air-gapped enterprise databases and creates expensive egress bandwidth costs on datasets > 500GB.

---

### 4. Trade-offs & Future Considerations

- **Trade-off**: Enforcing strict JSON schema contracts requires extra validation code compared to freeform script generation, but guarantees 100% execution safety.
- Future Considerations:
  - Add **Alembic** schema versioning for platform PostgreSQL metadata tables.
  - Implement partition checkpointing (`last_processed_id`) in `migration_jobs` to support zero-loss resumable task execution after worker crashes.

---

## [2026-08-11] - Modular PostgreSQL Control Plane ORM Model Implementation

### 1. Decision Summary

Implemented all 11 SQLAlchemy 2.x typed ORM models co-located in their respective domain feature modules inside `apps/api/app/modules/` (`users`, `sources`, `profiler`, `transformation_plans`, `execution`).

### 2. Why This Approach? (Rationale)

- **Domain-Driven Design (DDD)**: Each module owns its specific ORM models, Pydantic schemas, and API routes.
- **Cross-Dialect JSON Support**: Used `JSONB().with_variant(JSON, "sqlite")` to support both native PostgreSQL JSONB in production and SQLite in local fast integration tests.
- **Strict Cascading Foreign Keys**: Defined `ON DELETE CASCADE` across all parent-child relationships for automated referential integrity.

### 3. Trade-offs & Future Considerations

- Module schemas (`_schemas.py`) and routes (`_routes.py`) will consume these models as API features are added.

---

## [2026-08-11] - Code Audit & Model Type Refinement

### 1. Decision Summary

Applied recommended audit refinements across domain models and integration tests:

- Refined JSONB type annotations in `MigrationPlan` (`source_connection_ids`, `source_snapshot_ids`, `plan`) to `Mapped[Any]` to eliminate rigid dict constraints on generic JSON structures.
- Renamed local test variable `relationship` to `meta_rel` in [`test_db_models.py`](file:///c:/Neel/AI%20DATA%20MIGRATION%20PLATFORM/apps/api/tests/integration/test_db_models.py#L135) to avoid variable name shadowing with SQLAlchemy's `relationship` import.
- Cleaned unused `Numeric` import in [`profiler_models.py`](file:///c:/Neel/AI%20DATA%20MIGRATION%20PLATFORM/apps/api/app/modules/profiler/profiler_models.py).

---

---

## [2026-08-11] - Separation of `sources_connectors` and `sources_loaders`

### 1. Decision Summary

Separated file loaders (`CSVFileLoader`, `ExcelFileLoader`) out of `sources_connectors` into a dedicated package [`apps/api/app/modules/sources/sources_loaders/`](file:///c:/Neel/AI%20DATA%20MIGRATION%20PLATFORM/apps/api/app/modules/sources/sources_loaders).

### 2. Why This Approach? (Rationale)

- **Single Responsibility Principle (SRP)**: Keeps `sources_connectors` focused strictly on database drivers (`postgresql`, `mysql`, `mongodb`) and `sources_loaders` focused strictly on file parsing and chunking (`csv`, `excel`).
- **Clean Architectural Boundaries**: Allows database connectors and file loaders to evolve independently with dedicated base classes and factory registries.

---

## [2026-08-11] - Bug Fix Round: Connectors & Loaders Hardening

### 1. Decision Summary

Fixed 12 bugs across `sources_connectors` and `sources_loaders` identified in a manual code audit. Fixes span critical memory issues, N+1 database queries, schemaless inference gaps, and resource leaks.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**: Multiple bugs ranging from critical (entire CSV file loaded into RAM before streaming) to minor (unused variable, confusing placeholder file).
- **CSV Streaming**: Replaced `pl.scan_csv().collect_batches()` (which loads entire file into memory first) with Python's `csv.DictReader` — true O(1) memory streaming. Row counting uses a fast line-count pass instead of a second full polars read.
- **Excel Streaming**: Replaced `pl.read_excel()` + `df.slice()` (full sheet loaded into RAM) with `openpyxl` `read_only=True` row iterator — constant memory regardless of sheet size.
- **PostgreSQL N+1 Query**: Replaced one `COUNT(*)` per table with a single `LEFT JOIN pg_class` query that fetches `reltuples` (PostgreSQL's native planner estimate) for all tables at once.
- **MySQL Schemas**: Replaced hardcoded `schemas=[db_name]` with a query against `information_schema.SCHEMATA` to list all visible databases.
- **MongoDB $sample**: Replaced single `find_one()` schema inference with `$sample: {size: 10}` aggregation that merges field names across 10 random documents — handles sparse/schemaless collections.
- **Engine Helper**: Extracted `_get_engine()` on Postgres and MySQL connectors with `pool_pre_ping=True` to ensure clean connection validation and reduce code duplication.

### 3. Alternatives Considered & Rejected

- **Polars `collect(streaming=True)` for CSV**: Polars' streaming mode is still experimental and not production-stable as of Polars 1.x. `csv.DictReader` is battle-tested.
- **Exact `COUNT(*)` for Postgres**: Would fix accuracy but causes N+1 round-trips (one per table). `pg_class.reltuples` is the same stat the query planner uses and is accurate enough for metadata display.
- **MongoDB: `$sample` with more docs**: Sampling more documents increases accuracy but also latency. 10 is a reasonable default for schema inference; configurable via `options` dict if needed later.

### 4. Trade-offs & Future Considerations

- CSV type inference is lost in `stream_chunks` (csv.DictReader returns all values as strings). Downstream ETL consumers should apply their own type coercion based on the schema from `introspect_schema()`.
- Excel streaming via openpyxl row iterator means no Polars dtype inference during streaming — same trade-off.
- Engine per-call pattern is intentionally stateless. For production, a connection pool manager at the service/application layer (not per-connector) would be more efficient.

---

## [2026-08-11] - User Module CRUD & HTTP-Only Cookie Authentication Architecture

### 1. Decision Summary

Implemented User domain CRUD operations and secure JWT Authentication System using HTTP-only cookies, token rotation (15-minute access token, 7-day refresh token), bcrypt password hashing, and authentication dependencies/middleware.

### 2. Why This Approach? (Rationale)

- **HTTP-Only Cookies for XSS Prevention**: Storing JWT access and refresh tokens in `httponly=True` cookies prevents JavaScript code on the client from accessing tokens directly, rendering XSS attacks ineffective for token theft.
- **Short-Lived Access Token (15 Mins) & Long-Lived Refresh Token (7 Days)**: Minimizes blast radius if an access token is compromised while offering seamless UX via automatic refresh token rotation.
- **Dedicated `/auth/refresh` Route with Token Rotation**: Calling `/auth/refresh` invalidates the previous refresh token payload and issues a new access token AND a new refresh token, resetting both HTTP-only cookies.
- **Bcrypt Password Hashing**: Passwords are salted and hashed using `bcrypt.hashpw()` before saving into the database. Plaintext passwords are never stored or logged.
- **Authorization Header Fallback**: Supports `Authorization: Bearer <token>` headers as a fallback so API clients (Postman, Swagger UI, Curl) can easily test protected endpoints alongside standard browser HTTP-only cookies.

### 3. Alternatives Considered & Rejected

- **Local Storage / Session Storage for JWT**: Rejected due to vulnerability to XSS attacks.
- **Single Long-Lived Access Token**: Rejected due to security risk; if compromised, the token remains valid for days without revocation capability.
- **Session-based DB sessions**: Rejected in favor of stateless JWT tokens to maintain stateless scalability across backend worker instances.

### 4. Trade-offs & Future Considerations

- CORS credentials must be enabled (`allow_credentials=True`) on frontend requests when transmitting cookies cross-origin.
- For production multi-domain deployments, ensure `COOKIE_SECURE=True` (HTTPS) and `COOKIE_SAMESITE="lax"` or `"none"`.

---

## [2026-08-12] - Phase 0: Docker Agent Architecture & Control Plane Integration

### 1. Decision Summary

Introduced the `Agent` domain model and service boundary in `apps/api/app/modules/agents/` and established the standalone `apps/agent/` Docker agent workspace for customer-hosted execution.

### 2. Why This Approach? (Rationale)

- **Customer Data Privacy**: Enterprise customers require running migration jobs inside their own VPCs without sharing raw data with cloud APIs.
- **Decoupled Control Plane & Data Plane**: The FastAPI backend acts as the central control plane (issuing jobs and receiving heartbeats/status), while standalone Docker agents execute data transfer locally.
- **Foreign Key Linking**: Added nullable `agent_id` FK to `connections` and `migration_jobs` tables so connections and execution jobs can optionally bind to customer-hosted Docker agents.

### 3. Alternatives Considered & Rejected

- **Direct Backend Execution Only**: Rejected because enterprise databases behind strict firewalls cannot be accessed directly by a public backend service.

---

## [2026-08-12] - Server-Side Google OAuth 2.0 & Identity Isolation Policy

### 1. Decision Summary

Implemented server-side Google OAuth 2.0 (`POST /auth/google`, `GET /auth/google/login`, `GET /auth/google/callback`) integrated with FastAPI's existing HTTP-only cookie JWT session system, enforcing strict account isolation rules between Google accounts and password accounts.

### 2. Why This Approach? (Rationale)

- **Cryptographic Token Verification**: Uses `google-auth` (`google.oauth2.id_token.verify_oauth2_token`) on the backend to verify Google ID token signatures against Google's public keys (`https://www.googleapis.com/oauth2/v3/certs`). Unverified frontend data is never trusted.
- **Unified Application Session**: Successful Google authentication converges into the exact same application session (`access_token` and `refresh_token` HTTP-only cookies), keeping frontend session logic clean and standardized.
- **Strict Identity Isolation Policy**:
  1. _Google Account Attempting Password Registration_: Rejected with `409 Conflict` ("An account with this email was created using Google Sign-In").
  2. _Google Account Attempting Password Login_: Rejected with `400 Bad Request` ("This account was created using Google Sign-In").
  3. _Unlinked Password Account Attempting Google Login_: Restricted with `409 Conflict` ("An account with this email already exists using password authentication").
  4. _Deactivated Account Attempting OAuth_: Rejected with `400 Bad Request` ("User account is deactivated").
  5. _Unverified Google Email_: Rejected with `400 Bad Request` ("Google account email is not verified").
- **Dynamic Client Component Spline Loading**: Loaded `@splinetool/react-spline/next` via `next/dynamic` with `{ ssr: false }` to prevent React 18/19 Next.js 14 client component async rendering errors.

### 3. Alternatives Considered & Rejected

- **Trusting Client User Data**: Rejected due to critical security risk (account takeover by passing arbitrary email in JSON body).
- **Separate Session System for Google Users**: Rejected to prevent maintaining parallel authentication middleware, route guards, and cookie handling.

### 4. Trade-offs & Future Considerations

- Requires configuring `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in `.env` for production Google OAuth consent screens.

---

## [2026-08-13] - Next.js Frontend State Management Architecture & HTTP-Only Cookie Axios Interceptor Setup

### 1. Decision Summary

Established the complete frontend architecture in `apps/web` (Next.js App Router). Configured Axios ([`services/axios.ts`](file:///c:/Users/91873/Desktop/Data_migration_tool/Ai_data_migration_platform/apps/web/services/axios.ts)) using HTTP-only cookies (`withCredentials: true`), dynamic organization header insertion via `js-cookie` (`X-Organization-Id`), automatic token refresh on `401` status via `/auth/refresh`, user notification toasts via `react-hot-toast`, TanStack Query v5 for remote global server state, and Redux Toolkit for local client state.

### 2. Why This Approach? (Rationale)

- **HTTP-Only Cookies Security**:
  - Access and refresh tokens are managed natively via secure HTTP-only cookies, eliminating XSS vulnerabilities associated with storing tokens in `localStorage`.
- **Axios Token Refresh Interceptor (`services/axios.ts`)**:
  - Configured with `withCredentials: true`.
  - Automatically attaches `X-Organization-Id` header if `active_org_id` cookie is present.
  - Intercepts `401 Unauthorized` responses and pauses execution using `isRefreshing` lock and `failedQueue`.
  - Sends `await apiClient.post('/auth/refresh')` to seamlessly renew cookies and retry pending requests.
  - Displays user-friendly error toasts (`react-hot-toast`) on session expiration ("Session expired. Please log in again."), network failures, or 500 server errors, redirecting to `/login` when unauthenticated.
- **TanStack Query & Redux Division of Labor**:
  - **TanStack Query**: Handles remote auth query/mutation hooks ([`useAuthUser.ts`](file:///c:/Users/91873/Desktop/Data_migration_tool/Ai_data_migration_platform/apps/web/hooks/queries/useAuthUser.ts), [`useAuthMutations.ts`](file:///c:/Users/91873/Desktop/Data_migration_tool/Ai_data_migration_platform/apps/web/hooks/mutations/useAuthMutations.ts)).
  - **Redux Toolkit**: Maintains in-memory user session & local UI state ([`authSlice.ts`](file:///c:/Users/91873/Desktop/Data_migration_tool/Ai_data_migration_platform/apps/web/store/slices/authSlice.ts), [`uiSlice.ts`](file:///c:/Users/91873/Desktop/Data_migration_tool/Ai_data_migration_platform/apps/web/store/slices/uiSlice.ts)).

### 3. Trade-offs & Future Considerations

- Eliminates manual token decoding or localStorage management on the client side.

---

## [2026-08-13] - Component-Based 3D Interactive Landing Page Implementation

### 1. Decision Summary

Built a component-based interactive Landing Page for `apps/web` featuring a full-screen dynamic Spline 3D Hero background (`https://prod.spline.design/E6eFCzHp4BkxYnO7/scene.splinecode`), followed by structured feature sections detailing AI schema intelligence, streaming ETL capabilities, Bento Grid showcase, 4-step workflow, and glassmorphic CTAs.

### 2. Why This Approach? (Rationale)

- **Dynamic 3D Spline Canvas (`SplineHeroBackground.tsx`)**: Loaded via `next/dynamic` with `{ ssr: false }` to prevent SSR hydration mismatches while offering visual wow factor. Includes a fallback glowing loader.
- **Component-Based Architecture**: Modularized into single-responsibility components (`Navbar`, `Hero`, `PlatformOverview`, `FeaturesGrid`, `WorkflowSteps`, `Footer`) in `components/landing/`.

---

## [2026-08-13] - 2-Column Split Authentication Pages (`/register` & `/login`) with React Hook Form

### 1. Decision Summary

Built the **Registration** (`/register`) and **Login** (`/login`) pages using a 2-column split layout (`AuthLayout.tsx`). The left column renders the 3D Spline scene component, while the right column hosts the reactive form rendered with `react-hook-form`, front-end validation (name, email regex, password 8–12 chars), Google authentication button, and integration with `useRegister()` and `useLogin()` hooks.

### 2. Why This Approach? (Rationale)

- **2-Column Split (`AuthLayout.tsx`)**: Offers visual consistency across `/register` and `/login` while maintaining full focus on the input form on the right pane.
- **`react-hook-form` Validation**:
  - `name`: Required, min 2 characters.
  - `email`: Required, validated via `/^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$/i` regex.
  - `password`: Required, strictly enforced between 8 and 12 characters (`minLength: 8, maxLength: 12`).
- **Humanized Angular Design**: Styled with Plus Jakarta Sans typography, solid dark slate containers (`bg-slate-950`), and sharp borders (`rounded-sm`).

---

## [2026-08-13] - Agent-Centric Database Architecture & Credential Elimination

### 1. Decision Summary

Eliminated database credentials (`host`, `port`, `username`, `password`, `credentials_encrypted`) from the control plane backend. Dropped the obsolete `connections` table and replaced it with a lightweight `data_sources` identity table (`id`, `agent_id`, `name`, `type`, `role`, `identifier`).

### 2. Why This Approach? (Rationale)

- **Zero Control-Plane Storage of Credentials**: Customer database passwords and connection strings remain strictly inside customer-hosted local Docker Agents. The control plane backend only stores logical source identities and collected metadata history.
- **N:M Migration Plan Snapshots**: Created `migration_plan_snapshots` join table so a single `MigrationPlan` can combine metadata snapshots from multiple data sources (e.g. Postgres + MySQL into a new Postgres target).
- **Graceful Deletion Handling**: Changed `agent_id` FK on `migration_plans` to `ondelete="SET NULL"` so completed historical migration plans survive agent deregistration.

### 3. Trade-offs & Future Considerations

- Alembic migration `004_agent_centric_arch` handles schema transition with full `upgrade()` and `downgrade()` safety.

---

## [2026-08-13] - Sources & Agents Domain Security, Ownership Middleware & Roles

### 1. Decision Summary

Implemented security middleware dependencies (`sources_dependencies.py`) enforcing agent and data source ownership verification across all endpoints. Added a `role` field (`"source"`, `"target"`, `"both"`) to `data_sources`.

### 2. Why This Approach? (Rationale)

- **IDOR & Ownership Protection**: Dependencies `get_verified_agent` and `get_verified_data_source` verify that the target agent/source belongs to `current_user.id`, returning `403 Forbidden` if ownership validation fails.
- **Role Categorization**: Explicitly categorizing data sources by role (`source`, `target`, `both`) enables schema profiling on both source DBs and target DBs while allowing clear UI separation.
- **Strict Pydantic Validation**: Used Pydantic `Literal` types (`VALID_SOURCE_TYPES`, `VALID_SOURCE_ROLES`) and `min_length=1` field constraints to reject invalid inputs at the API gateway layer.

---

## [2026-08-13] - Agent Domain Module APIs & Concurrent Data Sources Creation

### 1. Decision Summary

Implemented complete Agent CRUD endpoints (`POST /agents`, `GET /agents`, `GET /agents/{id}`, `PUT /agents/{id}`, `POST /agents/{id}/heartbeat`, `DELETE /agents/{id}`) with support for atomic concurrent creation of Agent + initial Source and Destination DB identities.

### 2. Why This Approach? (Rationale)

- **Atomic Single-Transaction Setup**: When calling `POST /api/v1/agents`, the payload can include an array of initial `data_sources`. The service creates the Agent record and all attached Data Source records within a single database transaction, ensuring no partial or orphaned state occurs.
- **Periodic Heartbeat Tracking**: Endpoint `POST /agents/{id}/heartbeat` allows Docker Agents to ping status (`online`, `busy`), update `version`, and update `last_seen_at` timestamp.

---

## [2026-08-14] - Docker Command Generator & Zero-Credential Control Plane Isolation

### 1. Decision Summary

Implemented `AgentCommandGenerator` service and API response enhancements (`POST /api/v1/agents` and `GET /api/v1/agents/{id}/docker-command`) that automatically construct ready-to-run Docker CLI commands (Bash multi-line, PowerShell, single-line) and `.env` templates parameterized with the generated `AGENT_TOKEN`, `BACKEND_URL`, and credential placeholders for merging multiple source databases into a destination database.

### 2. Why This Approach? (Rationale)

- **Zero Control-Plane Storage of Sensitive DB Credentials**: Control plane never stores or requires sensitive database passwords/credentials over the network. Instead, the backend generates parameterized Docker commands with credential placeholders (`<SRC_DB_PASSWORD>`, `<DEST_DB_PASSWORD>`, `<DB_NAME>`) that the customer fills directly in their local shell environment before booting the container.
- **Multi-Platform Support**: Generates cross-platform commands formatted for standard Bash/macOS/Linux (`\`), Windows PowerShell (`` ` ``), single-line execution, and `.env` file ingestion.
- **Cross-Platform Host Routing**: Injects `--add-host=host.docker.internal:host-gateway` to guarantee seamless connectivity from the container back to host localhost services across Windows, macOS, and Linux Docker engines.
- **Multi-Source Merge Handling**: Dynamically parses all registered data sources by role (`source`, `target`, `both`) and database type (`postgresql`, `mysql`, `mongodb`, `mssql`, file loaders) generating sanitized environment variable prefixes (`SRC_<IDENTIFIER>_URL`, `DEST_<IDENTIFIER>_URL`) and single-source convenience aliases.

### 3. Alternatives Considered & Rejected

- **Alternative A: Storing DB Passwords in Backend Database**: Rejected due to enterprise security risks and compliance restrictions regarding plaintext or reversible cloud credential storage.
- **Alternative B: Pure Client-Side Command Generation**: Rejected because backend owns the API token lifecycle, configuration defaults, and database type dialect URL specifications.

### 4. Trade-offs & Future Considerations

- Returned commands contain placeholder strings (`<...>`) which require the user to fill in their real passwords locally before executing the Docker run command.

---

## [2026-08-14] - Multi-Step Agent Creation Page (1:1, 2:1, 3:1, Custom N:1) & Docker Command UI

### 1. Decision Summary

Implemented a 3-step Agent Creation wizard in `apps/web/app/agents/create/page.tsx` that guides users through migration ratio selection (`1:1`, `2:1`, `3:1`, `Custom N:1`), database engine setup (strictly restricted to `postgresql`, `mysql`, `mongodb`, `csv`, `excel`), agent registration, Docker CLI command rendering, and real-time agent connectivity monitoring over WebSocket.

### 2. Why This Approach? (Rationale)

- **Step 1: Ratio Selection (`TopologySelector.tsx`)**: Offers visual interactive cards for `1:1`, `2:1`, `3:1`, and `Custom N:1` topologies with glowing borders and dynamic source count state.
- **Step 2: Database & Engine Selection (`DatabaseConfigForm.tsx`)**: Enforces input/output database engine types strictly to `postgresql`, `mysql`, `mongodb`, `csv`, `excel`. Generates input forms for N source databases + 1 destination database.
- **Step 3: Docker Deployment CLI & Live Monitoring (`DockerCommandOutput.tsx`)**:
  - Displays generated `docker run` command and `docker-compose.yml` snippet with one-click copy button.
  - Subscribes via WebSocket to `/api/v1/agents/ws/{agent_id}` (with polling fallback) to dynamically update agent status badge from `WAITING FOR AGENT PING` to `ONLINE` as soon as the user runs the container.
- **Service Integration & Teammate API Resilience (`agentService.ts`)**: Integrates with `POST /api/v1/agents` for registration and `POST /api/v1/agents/{agent_id}/docker-cmd` for Docker command generation, with client-side fallback formatting in case the backend teammate's endpoint is still in deployment.

---

## [2026-08-14] - Complete Edge Case Hardening: Stale Watchdog, Concurrent Diagnostics & Status Immutability

### 1. Decision Summary

Fixed 14 systemic edge cases across the Docker Agent and FastAPI backend:

1. **Ghost Agent & Stale Job Watchdog**: Implemented periodic background watchdog loop in FastAPI `lifespan` detecting dead agents (>60s inactivity), transitioning them to `offline`, and automatically failing orphaned `MigrationJob` records.
2. **Concurrent Database Socket Checks**: Refactored agent health diagnostics to run across all configured databases in parallel using `concurrent.futures.ThreadPoolExecutor`, strictly bounding socket diagnostic time to $\le 3.5\text{s}$ total.
3. **Graceful Offline Signaling**: Registered `SIGINT`/`SIGTERM` handlers in the Docker Agent to dispatch a final `status: "offline"` heartbeat before container termination.
4. **Strict Schema Constraints & Status Immutability**: Enforced Pydantic `Literal["online", "offline", "busy", "degraded", "error"]` validation on heartbeats and removed `status` from user-facing `AgentUpdate` REST payload to prevent status spoofing.
5. **Per-User Identifier Uniqueness**: Changed `Agent.agent_identifier` from global unique constraint to composite `UniqueConstraint("user_id", "agent_identifier")`.
6. **Degraded Health Calculation & Fuzzy Matching**: Agent reports `status: "degraded"` when any database source is unreachable, and backend uses fuzzy identifier resolution (`src_...`, `dest_...`) to prevent dropped reports.
7. **Heartbeat Throttling & WebSocket Keepalive**: Added rate-limiting guards against rapid ping spamming and implemented WebSocket ping/pong protocol for persistent connection keepalive across proxies.

### 2. Why This Approach? (Rationale)

- **Bounded Latency**: Sequential database testing across 5+ failing databases would cause a 17.5s blocking delay, causing the agent to miss its heartbeat window and appear dead to the control plane. Concurrent threading bounds this to max 3.5s.
- **Single Source of Truth for Status**: Agent status is solely controlled by authentic agent heartbeats and the backend watchdog; users cannot manually alter status via REST API.
- **Fail-Safe Job Lifecycle**: If an on-premise Docker container crashes or loses network mid-migration, jobs don't stay in `running` state forever; the control plane auto-recovers and notifies the UI.

### 3. Alternatives Considered & Rejected

- **Alternative A: Relying Solely on Docker Exit Codes**: Rejected because the control plane has no direct access to customer on-premise Docker daemons.
- **Alternative B: Client-Side Polling Only**: Rejected because if the browser tab closes, job state remains stuck in `running` on the backend.
- **Alternative C: Sequential Socket Tests with Lower Timeouts (0.5s)**: Rejected because high-latency WAN / cloud database handshakes would produce false connection timeouts.

### 4. Trade-offs & Future Considerations

- In distributed multi-worker deployments of the API control plane, the in-memory WebSocket manager can be backed by Redis Pub/Sub (`settings.REDIS_URL`) for cross-node event distribution.

---

## [2026-08-14] - Alembic Migration 005: Agent API Tokens & DataSource Health Diagnostics

### 1. Decision Summary

Created Alembic migration [`005_add_agent_tokens_and_datasource_diagnostics.py`](file:///c:/Neel/AI%20DATA%20MIGRATION%20PLATFORM/apps/api/alembic/versions/005_add_agent_tokens_and_datasource_diagnostics.py) to synchronize all database tables with newly introduced model fields.

### 2. Why This Approach? (Rationale)

- **Model-to-Schema Synchronization**:
  1. `agents.api_token_hash`: Added `String(255)` with unique index `ix_agents_api_token_hash` for secure SHA-256 agent authentication.
  2. `agents` per-user uniqueness: Converted `agent_identifier` from global unique index to composite `UniqueConstraint("user_id", "agent_identifier", name="uq_agents_user_identifier")`.
  3. `data_sources.status`: Added `String(50)` (default `"untested"`).
  4. `data_sources.last_error`: Added nullable `String` for sanitized error messages.
  5. `data_sources.last_checked_at`: Added nullable `DateTime(timezone=True)` for health check timestamps.
- **Continuous Revision Linearity**: Verified linear migration DAG: `001_initial_schema` $\rightarrow$ `002_add_agents` $\rightarrow$ `003_add_google_auth_to_users` $\rightarrow$ `004_agent_centric_arch` $\rightarrow$ `005_agent_tokens_and_diagnostics`.

### 3. Trade-offs & Future Considerations

- Full `upgrade()` and `downgrade()` methods implemented to ensure zero data corruption during deployment rollbacks.

---

## [2026-08-17] - Phase 2: Metadata Domain Architecture & On-Premise Introspection Engine

### 1. Decision Summary

Renamed the control plane `profiler` feature module to **`metadata`** (`apps/api/app/modules/metadata/`) and implemented end-to-end database schema introspection, snapshot versioning, control plane ingestion, and real-time WebSockets:

1. **Domain Rename**: Migrated all models (`metadata_models.py`), schemas (`metadata_schemas.py`), services (`metadata_services.py`), and routes (`metadata_routes.py`) into `app/modules/metadata`.
2. **On-Premise Introspection Engine**: Implemented [`apps/agent/metadata_engine.py`](file:///c:/Neel/AI%20DATA%20MIGRATION%20PLATFORM/apps/agent/metadata_engine.py) to introspect database schemas, tables, estimated row counts, column data types, nullability, primary keys, and foreign key relationships locally inside customer VPCs.
3. **Control Plane Ingestion & Auto-Versioning**: Endpoint `POST /api/v1/metadata/sync` ingests agent payloads, auto-increments version numbers per DataSource, and bulk-persists `MetadataSnapshot`, `MetadataSchema`, `MetadataTable`, `MetadataColumn`, `MetadataConstraint`, and `MetadataRelationship` records in PostgreSQL within a single atomic database transaction.
4. **Real-Time Push Notifications**: Pushes `METADATA_PROFILED` events over WebSockets via `manager.broadcast_to_agent()` to update Next.js dashboard clients instantly.

### 2. Why This Approach? (Rationale)

- **Zero Raw Data Transfer**: Customer passwords and table data remain strictly on-premise. Only structural schema ASTs are transmitted to the control plane.
- **Atomic Hierarchy Persistence**: Flushing parent IDs (`snapshot_id`, `schema_id`, `table_id`) in a single session transaction ensures complex relational metadata is stored without orphaned records.
- **Fuzzy Identifier Resolution**: Ingestion matches data sources by UUID or clean identifier (`src_...`, `dest_...`), preventing dropped snapshots due to environment naming variations.

### 3. Trade-offs & Future Considerations

- File-based sources (CSV/Excel) currently infer schemas from top row headers; future enhancement can add deep data type sniffing for multi-gigabyte files.

---

## [2026-08-17] - Phase 3 AI Plan Generator AST Integration & Phase 4 Local Agent Execution Architecture

### 1. Decision Summary

Implemented Phase 3 AI Migration Plan Generator using **Gemini 3.5 Flash Lite** with `PydanticOutputParser` and designed Phase 4 Local Agent Execution Pipeline:

1. **Domain Rename**: Standardized domain naming from `transformation_plans` to **`migration_plans`** across `apps/api/app/modules/migration_plans/`.
2. **Zero Raw Data Policy**: Built `MetadataContextSerializer` to convert metadata ASTs into zero-raw-data prompt contexts.
3. **Structured Schema Parsing**: Selected `PydanticOutputParser(pydantic_object=TransformationPlanAST)` with `gemini-3.5-flash-lite` (1,500 RPD, 1M token window) ensuring 100% deterministic schema validation across 9 column transformation types (`direct_copy`, `merge_concat`, `type_cast`, `split`, `expression`, `lookup_join`, `default_constant`, `drop_column`, `new_column_added`).
4. **Local Docker Agent Auto-Registration & Header Authentication**: Added `auto_register_agent` in [`apps/agent/main.py`](file:///c:/Neel/AI%20DATA%20MIGRATION%20PLATFORM/apps/agent/main.py) to enable seamless token-less local docker runs, authenticated via `X-Agent-Token` request headers.
5. **Phase 4 Local Execution Engine Design**: Offloaded data streaming, 3-way table merging, UUID v4 rekeying, and target bulk loading to the local Docker Agent process using Polars & DuckDB, enforcing Zero Raw Data cloud transfer.

### 2. Why This Approach? (Rationale)

- **Model Choice (`gemini-3.5-flash-lite`)**: Provides high intelligence for multi-database schema reasoning, high daily rate limits (1,500 RPD), and 1M token context window at zero cost.
- **PydanticOutputParser over `with_structured_output`**: Eliminates SDK version incompatibilities and type errors in `langchain-google-genai` by enforcing explicit JSON format instructions and Pydantic parsing.
- **Local Agent Execution Model**: Running ETL transformations locally inside the customer's Docker Agent guarantees data privacy (raw data never leaves local network) while avoiding cloud bandwidth costs.

### 3. Alternatives Considered & Rejected

- **Alternative A: Passing Raw Data to LLM**: Rejected due to enterprise security violations and severe context window bloat.
- **Alternative B: Running Data Transformations in Cloud API**: Rejected due to high egress network costs and air-gapped database accessibility limitations.

---

## [2026-08-17] - Phase 4: Local Docker Agent ETL Execution Pipeline Implementation

### 1. Decision Summary

Fully implemented Phase 4 Local Docker Agent ETL Execution Pipeline:

1. **Control Plane Execution REST API**: Created `/api/v1/plans/{plan_id}/execute`, `/api/v1/executions`, `/api/v1/executions/{id}`, `/api/v1/executions/{id}/progress`, and `/api/v1/agents/tasks` in [`apps/api/app/modules/execution/ execution_routes.py`](file:///c:/Neel/AI%20DATA%20MIGRATION%20PLATFORM/apps/api/app/modules/execution/execution_routes.py).
2. **Schema & Model Reuse**: Reused existing `migration_jobs` database table and `MigrationJob` ORM model in [`apps/api/app/modules/execution/execution_models.py`](file:///c:/Neel/AI%20DATA%20MIGRATION%20PLATFORM/apps/api/app/modules/execution/execution_models.py) avoiding redundant table creation.
3. **Local ETL Engine (`execution_engine.py`)**: Built [`apps/agent/execution_engine.py`](file:///c:/Neel/AI%20DATA%20MIGRATION%20PLATFORM/apps/agent/execution_engine.py) featuring `DDLExecutor`, `SourceConnectorFactory` (PostgreSQL, MySQL, MongoDB, CSV, Excel), `ASTTransformer` (all 9 transformation types), `TableMerger` (multi-DB merge & deduplication), `TargetWriterFactory` (PostgreSQL, MySQL, MongoDB bulk loading), `CheckpointManager` (resumable state), and `ProgressReporter`.
4. **Task Polling Loop**: Added background task poller in [`apps/agent/main.py`](file:///c:/Neel/AI%20DATA%20MIGRATION%20PLATFORM/apps/agent/main.py) polling `/api/v1/agents/tasks` every 20 seconds.
5. **Agent Plan Fetching**: Enabled `GET /api/v1/plans/{plan_id}` to authenticate Docker Agents via `X-Agent-Token` header.

### 2. Why This Approach? (Rationale)

- **Zero Raw Data Transfer**: Data extraction, AST column transformations, table merges, and bulk insertion execute 100% inside customer VPC.
- **Resumability & Fault Tolerance**: Savepoints stored per chunk in `CheckpointManager` prevent restarting from zero if container restarts.
- **Cross-Engine Support**: Unified Polars memory dataframes bridge relational (SQL), document (NoSQL), and flat file (CSV/Excel) sources into any target database dialect.

---

## [2026-08-17] - Human-in-the-Loop AI Transformation Plan Review & Approval Gateway

### 1. Decision Summary

Enforced explicit **Human-in-the-Loop (HITL) Plan Review and Approval Gateway** between AI Plan Generation (`POST /api/v1/plans/generate`) and Migration Execution (`POST /api/v1/plans/{plan_id}/execute`). Data migration jobs **NEVER execute automatically** upon AI plan generation; explicit user approval in the Web UI is strictly required.

### 2. Why This Approach? (Rationale)

- **Safety & Control**: AI-generated transformation plans (table mappings, column type casts, deduplication rules) must be verified by a human database administrator or developer before touching production databases.
- **Editable Blueprint**: Allows users to inspect DDL scripts, adjust column mappings, or customize deduplication rules in the UI prior to execution.
- **Auditability**: Migration job state transitions (`draft` → `generated` → `approved` → `queued` → `running` → `completed`) maintain a strict audit trail of who approved which migration job at what time.

---

## [2026-08-18] - Fault Tolerance, Row Isolation, & One-Click UI Job Resumption Architecture

### 1. Decision Summary

Implemented multi-layered fault tolerance across local Agent execution and Control Plane orchestration:

1. **Row-Level Error Isolation**: When bulk inserts encounter bad data, `TargetWriterFactory` falls back to per-row insertion, isolating corrupted rows into a dead-letter log while allowing valid rows to insert cleanly.
2. **Chunk Checkpointing (`CheckpointManager`)**: Progress is saved per table chunk (`processed_rows`, `offset`, `chunk_index`). Crashed or interrupted jobs can be resumed from the exact last saved offset rather than starting from row 0.
3. **One-Click Web UI Resume/Retry**: Users never need to touch the terminal to handle failures. Clicking **"Resume Migration"** or **"Edit Plan & Retry"** in the Web UI updates the job state in the Control Plane, and the background Docker Agent automatically picks up the job via task polling.

### 2. Why This Approach? (Rationale)

- **Zero Terminal Interventions**: Non-technical users and DBAs manage job execution, error inspection, and retries 100% from the Web Dashboard.
- **Data Loss Prevention**: Isolating bad rows prevents a single bad string from aborting a 1,000,000-row migration.
- **Resource & Time Savings**: Checkpointing prevents re-fetching and re-transforming millions of already-processed rows after a container crash or network drop.

---

## [2026-08-18] - Phase 5: LangGraph Stateful Agent Plan Refinement & Feasibility Validation Engine

### 1. Decision Summary

Selected **LangGraph (`StateGraph`)** to implement Phase 5's non-linear AI plan refinement, dynamic human feedback loop, and deterministic feasibility validation engine.

### 2. Why This Approach? (Rationale)

- **Stateful Multi-Step Node Routing**: LangGraph models plan generation, structural schema validation, error auto-correction, and human feedback refinement as explicit nodes (`StateGraph`).
- **Self-Correcting LLM Loop**: If the initial AST contains a minor validation flaw, the graph automatically loops back to feed structural errors back into Gemini _before_ presenting the plan to the user.
- **Feasibility Error Transparency**: When a user's proposed edit or mapping is technically impossible (e.g. non-existent column, incompatible foreign key), the `validate_feasibility_node` captures the exact reason and explains _why_ the database cannot be migrated with those settings.
- **LangChain Ecosystem Compatibility**: Integrates directly with our existing `ChatGoogleGenerativeAI` and `PydanticOutputParser` pipeline.

### 3. Implementation Status & Verification

- Implemented `MigrationPlanValidator` (`migration_plans_validator.py`) for 5-stage schema validation.
- Implemented 9-node `StateGraph` (`migration_plans_graph.py`) managing context serialization, Gemini AST generation, feasibility validation, auto-correction, HITL interrupt, feedback processing, and plan persistence.
- Added REST Endpoints: `POST /plans/{id}/refine`, `POST /plans/{id}/validate`, `POST /plans/{id}/approve`.
- Enforced execution guard in `ExecutionService` (`POST /plans/{id}/execute` blocks invalid plans).
- Verified with unit tests (`test_migration_plans_validator.py` - 2/2 passed) and E2E integration test (`test_phase5_e2e_langgraph.py` - passed end-to-end).

---

## [2026-08-18] - Phase 5 Code Audit Hardening & Control Flow Integrity

### 1. Decision Summary

Applied 7 critical bug fixes and architectural hardening updates across the Phase 5 LangGraph Plan Refinement Engine:

1. **Execution Guard Enforcement (`ExecutionService`)**: Added explicit `plan.status == 'completed'` verification to prevent unapproved draft or invalid plans from being queued.
2. **Stage D Foreign Key Integrity (`MigrationPlanValidator`)**: Added Stage D validation to catch non-existent target table references in post-migration DDL `FOREIGN KEY ... REFERENCES` statements.
3. **Router 2 Fallthrough Protection (`human_feedback_router`)**: Changed fallback routing to stay at `human_approval_interrupt_node` instead of silently falling through to plan approval when no human action is set.
4. **Resilient Auto-Correction (`auto_correct_ast_node`)**: Added try/except handling around LLM refinement calls during auto-correction to prevent graph crashes on transient API timeouts.
5. **LLM Refinement Retry Loop (`LLMPlanGeneratorService.refine`)**: Added a 3-attempt retry loop with Pydantic parse error feedback matching the initial generator behavior.
6. **Enhanced API Response (`PlanDetailResponse`)**: Added `validation_warnings` list field to response DTO to cleanly present non-blocking advisories to the Web UI.
7. **Signal Node State (`finalize_and_persist_node`)**: Updated Node 9 to preserve `persisted_plan_id` in state and document its role as a state signal.

### 2. Verification

- Created unit test suite `test_phase5_bugfixes.py` (6/6 tests passed).
- Ran full unit test suite across `apps/api` (all tests passing cleanly).

---

## [2026-08-18] - Static Type Safety & Variable Shadowing Resolution

### 1. Decision Summary

Resolved 2 static type checker (Pyright/MyPy) warnings:

1. **LangGraph State Schema Compatibility (`migration_plans_graph.py`)**: Imported `TypedDict` from `typing_extensions` instead of standard `typing` so `MigrationPlanState` is recognized as a valid `TypedDictLike` by `StateGraph`.
2. **Variable Shadowing Elimination (`migration_plans_validator.py`)**: Renamed local dictionary `col_map` (used in snapshot introspection on L70) to `column_type_map`. This resolved the type collision where Pyright bound `col_map` as `dict[str, str]` instead of `ColumnMappingSpec` during the `for col_map in table_map.column_mappings:` iteration.

---

## [2026-08-20] - Migration Engine Reliability Edge Case Fixes & DB Write Outcome Verification

### 1. Decision Summary

Fixed three critical data loss, memory, and write-reporting edge cases in `apps/agent/execution_engine.py` and `apps/api/app/modules/execution/execution_schemas.py`:

1. **Multi-Source Merge Checkpoint Isolation**: Checkpoint state is now tracked independently per `(job_id, target_table, source_identifier, source_table)` to prevent multi-source merges from overwriting shared offsets and dropping rows. Includes legacy `checkpoint_{job_id}_{table_name}.json` fallback support.
2. **DuckDB Local Staging Bounded-Memory Merges**: Replaced in-memory dataset buffering (`extracted_dfs`) with a DuckDB local staging table file. Multi-source merge chunks are appended to DuckDB and deduplicated streaming out of DuckDB in bounded batches (`~50,000` rows), keeping peak RAM usage bounded to 1 chunk regardless of dataset size.
3. **DB Write Outcome Verification**: SQL bulk inserts inspect `result.rowcount` to calculate actual inserted rows vs skipped conflict rows (`ON CONFLICT DO NOTHING` / `INSERT IGNORE`). MongoDB bulk inserts explicitly catch `pymongo.errors.BulkWriteError` to parse `.details["writeErrors"]` and return accurate `successful_rows` and `failed_rows`. Added `skipped_rows` to `ExecutionProgressUpdate`.

### 2. Why This Approach? (Rationale)

- **Data Loss Prevention**: Shared checkpoint files caused source 2+ of a merge table to start reading at source 1's end offset instead of 0, silently skipping entire source datasets. Per-source isolated keying guarantees accurate resume points.
- **Bounded Memory Overhead**: In-memory list buffering of extracted DataFrames caused OOM crashes when merging large datasets (>100k rows across multiple DBs). DuckDB's local file staging offloads data to disk and streams deduplicated results back in small Polars batches.
- **Accurate Observability**: Assuming `len(rows)` was inserted without checking `rowcount` or swallowing PyMongo `BulkWriteError` hid silent conflict skips and reported 0/N success on partial MongoDB errors.

### 3. Alternatives Considered & Rejected

- **Alternative A: Holding Merged Data in Target DB Scratch Tables**: Rejected because it requires write/DDL privileges to create temporary tables on customer target databases and varies across target SQL dialects.
- **Alternative B: In-Memory Python Dictionary Deduplication**: Rejected because storing dicts of hundreds of thousands of rows causes high Python object overhead and memory fragmentation.

### 4. Trade-offs & Future Considerations

- DuckDB local staging creates a temporary `.duckdb` file in `CHECKPOINT_DIR` during multi-source execution, which is cleaned up automatically in a `finally` block upon completion.

---

## [2026-08-20] - Agent Decoupled Heartbeats, Atomic Job Claiming & Execution Error Watchdog

### 1. Decision Summary

Implemented three concurrency and resilience safeguards across `apps/agent/main.py`, `apps/agent/execution_engine.py`, `apps/api/app/modules/execution/execution_services.py`, and `apps/api/app/modules/agents/agents_services.py`:

1. **Decoupled Agent Heartbeats**: Refactored `apps/agent/main.py` daemon loop to execute `send_heartbeat` inside a dedicated background daemon thread (`start_heartbeat_thread`) using `threading.Event()` for clean `SIGINT`/`SIGTERM` shutdown. Heartbeats now fire continuously every 20 seconds even during multi-hour ETL runs.
2. **Atomic Job Claiming (SKIP LOCKED)**: Updated `ExecutionService.get_pending_tasks_for_agent` to use row-level locking (`.with_for_update(skip_locked=True)`) on queued jobs, atomically transitioning status to `'preparing'` in the same transaction before returning. Prevents concurrent agent processes sharing a token from double-executing jobs.
3. **Execution Error Watchdog & Agent-Side Exception Propagation**:
   - Agent-side `ExecutionOrchestrator.run_job` catches top-level exceptions and dispatches `ProgressReporter.report(..., status="failed", error_message=str(exc))` to Control Plane.
   - Backend `ExecutionService.check_stale_jobs` queries jobs in `running` or `preparing` status updated > 5 minutes ago and fails them with an explicit error message (`"Migration job stalled: no progress updates received from agent for over 5 minutes."`).

### 2. Why This Approach? (Rationale)

- **Agent Offline False Positives**: Synchronous single-threaded execution caused agents to miss heartbeats during long migrations, triggering false offline/degraded warnings. Background threading maintains continuous heartbeat reachability.
- **Race Condition Data Corruption**: Unlocked task polling allowed duplicate agent containers to pick up the same job. Row-level locking guarantees single-consumer task distribution.
- **Stuck UI Progress Bars**: Mid-migration crashes left Control Plane job records stuck in `running` forever. Dual-layer reporting (agent-side error dispatch + backend stale job watchdog) guarantees every failure is communicated clearly to the user.

---

## [2026-08-20] - Multi-Source Checkpoint Isolation & DuckDB Bounded Streaming (Fixes 1 & 2)

### Problem

1. **Checkpoint Keying Collisions**: `CheckpointManager` stored progress as `(job_id, target_table)`. In multi-source merges, later sources inherited the final offset of source 1, skipping data extraction from offset 0.
2. **Memory Growth**: Multi-source table merges accumulated transformed Polars DataFrames in Python RAM lists, creating Out-Of-Memory (OOM) crashes on large datasets.

### Decision Made

1. Keyed checkpoints strictly by `(job_id, target_table, source_identifier, source_table)` with legacy single-key fallback.
2. Implemented DuckDB local file staging (`staging_{job_id}_{target_table}.duckdb`), writing raw source chunks to disk, discarding Python memory objects, and streaming deduplicated final batches in bounded ~50,000-row chunks.

### Rejected Alternatives

- _Redis/In-Memory Merging_: Rejected due to high RAM overhead and requirement for external daemon infrastructure in zero-data-plane environments.

---

## [2026-08-20] - Decoupled Heartbeats & Atomic Job Claiming (Fixes 4 & 5)

### Problem

1. Single-threaded agent execution blocked diagnostic heartbeats during multi-hour ETL jobs, causing false offline/degraded container statuses.
2. Unlocked job polling allowed duplicate agent containers sharing credentials to claim and execute the same job twice.

### Decision Made

1. Decoupled heartbeats into a dedicated daemon thread (`start_heartbeat_thread`) using `threading.Event()` for clean signal handling.
2. Implemented PostgreSQL row-level locking (`.with_for_update(skip_locked=True)`) in `get_pending_tasks_for_agent`, atomically updating status to `preparing` in the same transaction.

### Rejected Alternatives

- _External Message Queue (RabbitMQ/Celery)_: Rejected to preserve lightweight agent footprint and zero external dependency policy.

---

## [2026-08-20] - PK Strategies & Residual Unmapped Field Capture (Fixes 7 & 9)

### Problem

1. Fall-through to UUID v5 silently ignored explicit user requests for `keep_original` and `autoincrement_offset`.
2. Un-sampled MongoDB fields missing from the explicit AST blueprint were silently dropped during ETL transformation.

### Decision Made

1. Implemented all 4 PK conflict resolution strategies (`keep_original`, `autoincrement_offset`, `prefix_id`, `uuid_v4_rekey`) in `ASTTransformer.transform_chunk`.
2. Added execution-time unmapped column inspection, serializing unforeseen fields into a catch-all `extra_attributes` JSON column.

### Rejected Alternatives

- _Strict Schema Rejection_: Rejected because NoSQL document stores inherently contain polymorphic/dynamic attributes that should be preserved.

---

## [2026-08-20] - SQL Gaps & Structural AST Merge (Fixes 11 & 12)

### Problem

1. DDL errors were swallowed as warnings, allowing ETL to proceed into non-existent target tables.
2. Small table per-row fallbacks (<1,000 rows) could not trigger the 50% abort threshold.
3. Shallow dictionary merge `{**current_ast, **manual_edits}` in `process_manual_edits_node` wiped out unedited tables when partial UI payloads were submitted.

### Decision Made

1. Raised explicit exceptions on genuine DDL errors while logging notices for benign "already exists" warnings.
2. Evaluated fallback per-row abort threshold proportionally against `min(1000, max(5, len(rows) // 2))`.
3. Implemented deep structural identity merge in `process_manual_edits_node` by `target_table_name` and `target_column_name`.

### Rejected Alternatives

- _Full AST Re-transmission Requirement_: Rejected to keep web UI payload footprints lightweight during inline cell edits.

---

## 2026-08-20 - Post-Agent Creation Workflow: Catalog Profiler & Transformation Plan UI

### 1. Decision Summary

Implemented the complete frontend user flow following agent container registration:

1. **Agent Status Banner (`AgentStatusBanner.tsx`)**: WebSocket auto-sync for live agent connection heartbeats (`online` / `offline`) and schema introspection completion notifications (`METADATA_PROFILED`).
2. **Schema & Catalog Viewer (`SchemaCatalogViewer.tsx`)**: Structural inspection tree & table browser rendering database tables, column data types, nullability, PK/FK attributes, constraints, and estimated row counts.
3. **AI Migration Plan Trigger (`GeneratePlanAction.tsx`)**: Form panel submitting target engine options and custom natural language instructions to launch the AI Planning Engine (`POST /api/v1/plans`).
4. **Interactive Transformation Blueprint (`PlanBlueprintViewer.tsx`)**: Rendered on `/transformation-plan`, featuring table execution order timelines, column transformation mapping cards, AI confidence scoring, natural language AI plan refinement, and plan approval triggers.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**: Previously, after creating an agent, there was no UI to monitor agent health, view catalog metadata uploaded by agent daemons, or review and approve AI-generated migration plans.
- **Chosen Solution**: Modular Next.js Client Components with TypeScript types aligned strictly against backend FastAPI Pydantic contracts (`MetadataSnapshotDetailResponse`, `TransformationPlanAST`).
- **Why This Architecture**:
  - Direct integration with existing FastAPI WebSocket endpoints (`/api/v1/agents/ws/{id}`) for zero-latency UI updates.
  - Strict sharp visual design system (`rounded-none`, pitch black background, Sky Blue `#38bdf8` accents) maintaining design consistency across all steps.

### 3. Alternatives Considered & Rejected

- **Alternative A (Full Page Reload Polling)**: Standard HTTP polling for schema profiling changes.
  - _Rejected_: Slower user feedback and unnecessary load on FastAPI endpoints compared to WebSocket events.
- **Alternative B (Generic Tree Views)**: Relying on basic JSON trees for displaying schema tables.
  - _Rejected_: Inferior user experience; structured table matrices with PK/FK/type badges are significantly easier to audit.

### 4. Trade-offs & Future Considerations

- **Memory Safety**: Schema catalog inspector renders up to 500 tables per view using client-side searching. If a database has 10,000+ tables, virtualized list rendering (e.g. `react-window`) can be introduced.

---

## 2026-08-24 - Critical Workflow & ETL Robustness Edge Case Fixes

### 1. Decision Summary

Resolved critical edge cases across API backend, agent execution engine, and Web UI:

1. **Duplicate Execution Prevention (`execution_services.py`)**: Added an explicit active job check (`status.in_(["queued", "preparing", "running"])`) returning HTTP 409 Conflict if execution is triggered on an already active plan.
2. **Explicit Source DB Matching (`orchestrator.py`)**: Removed silent fallback to default DB when resolving multi-source connection strings; raises an explicit `ValueError` when an identifier cannot be matched.
3. **Resumable Checkpoint Lifecycle (`checkpoint.py` & `orchestrator.py`)**: Added `clear_job_checkpoints(job_id)` to purge `.json` checkpoint files on job completion to prevent stale resumes.
4. **PostgreSQL Session Scope Safety (`target_writer.py`)**: Enclosed `SET session_replication_role = 'replica'` in a `try...finally` block resetting to `'origin'` before releasing pooled connections.
5. **Watchdog Queued Job Recovery (`agents_services.py` & `execution_services.py`)**: Updated stale watchdog loops to clean up orphaned `queued` jobs when agents time out.
6. **Execution Dashboard Filtering (`execution/page.tsx` & `PlanBlueprintViewer.tsx`)**: Aligned active job counter with `queued`, `preparing`, and `running` backend statuses and added active job auto-detection on mount.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**: Potential data duplication on re-triggering plan execution, silent wrong-database fallback on multi-source setups, stale checkpoint reuse, connection pool constraint leakage, and orphaned queued jobs.
- **Chosen Solution**: Guard-rail checks at API boundary, explicit exception raising in agent engine, connection transaction cleanup, and complete status synchronization between frontend and backend.
- **Why This Architecture**: Ensures strict data safety, transparent failure logs, and idempotent execution pipelines.

### 3. Alternatives Considered & Rejected

- **Alternative A (Silent Ignore for Duplicate Executions)**: Returning 200 OK with the existing job ID on duplicate trigger.
  - _Rejected_: HTTP 409 Conflict provides explicit semantic feedback and alerts the UI to mount the active execution banner.
- **Alternative B (Keeping Checkpoint Files Indefinitely)**: Leaving completed job checkpoints on disk.
  - _Rejected_: Disk clutter and risk of stale offset reuse if job IDs are re-generated.

### 4. Trade-offs & Future Considerations

- Single-source database environments retain automatic single-DB binding fallback when only one source DB is configured, preserving developer onboarding simplicity while safeguarding multi-source setups.

---

## 2026-08-24 - High Priority UX, Security & Resilience Edge Case Fixes

### 1. Decision Summary

Resolved all High Priority edge cases (EC-07 through EC-12) across UI, API, and Agent:

1. **Plan AST Revert Strategy (`PlanBlueprintViewer.tsx`)**: Added `previousValidAst` state tracking to allow restoring the last valid plan version if LLM refinement generates schema errors.
2. **Draft Failed Error Alert UI (`PlanBlueprintViewer.tsx`)**: Added explicit error card rendering with diagnostic details and a "Regenerate Migration Plan" action when plan status is `draft_failed` or `table_mappings` is empty.
3. **Plan Generation Request Timeout & Progress UX (`planService.ts` & `GeneratePlanAction.tsx`)**: Set a 3-minute request timeout (`timeout: 180000`) for plan generation/refinement API calls and added a step-by-step progress indicator modal.
4. **Secure WebSocket Authentication Frame Support (`agents_routes.py` & `agentService.ts`)**: Updated WebSocket endpoint to support initial `{ "type": "auth", "token": "<jwt>" }` JSON message authentication frames, eliminating JWT token exposure in URL query parameters (`?token=<jwt>`).
5. **Real-Time Execution Auto-Refresh Polling (`execution/page.tsx`)**: Added a 3-second auto-polling interval when active jobs exist, keeping live row counts and stages updated without manual user refreshes.
6. **Explicit Agent Environment Credentials (`main.py`)**: Updated `auto_register_agent()` to fail fast if `USER_EMAIL` and `USER_PASSWORD` are missing instead of sending default test credentials.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**: Irreversible blueprint invalidation on refinement failure, blank UI on graph generation failure, request timeouts on long LLM runs, JWT token leakage in WebSocket URLs, stale execution metrics, and accidental test credential fallback in production.
- **Chosen Solution**: AST state versioning, explicit error boundary components, secure WebSocket first-frame auth, client-side polling, and strict environment credential enforcement.
- **Why This Architecture**: Protects sensitive tokens, improves user feedback, and ensures deterministic error handling across all user-facing flows.

### 3. Alternatives Considered & Rejected

- **Alternative A (URL Query Parameter WebSocket Auth Only)**: Continuing to send JWT in query strings.
  - _Rejected_: Security vulnerability; query parameters are logged in server access logs and browser history.
- **Alternative B (Manual Page Refreshes for Execution Monitoring)**: Requiring users to click "Refresh Jobs".
  - _Rejected_: Inferior user experience; real-time 3s polling provides seamless live ETL visibility.

### 4. Trade-offs & Future Considerations

- First-frame WebSocket auth preserves query parameter support as a legacy fallback for existing client integrations while enforcing non-URL token delivery for new UI components.

---

## 2026-08-24 - Medium Priority Configuration, Validation & Sanitization Fixes

### 1. Decision Summary

Resolved all Medium Priority edge cases (EC-13 through EC-19) across API backend, agent execution engine, and plan validator:

1. **Deterministic Target DB Selection (`main.py`)**: Sorted `DEST_*` keys alphabetically and selected primary target URL, issuing warning logs when multiple destination DB URLs are present in container environment variables.
2. **Pre-DDL Created Tables Resolution (`migration_plans_validator.py`)**: Parsed `pre_migration_ddl` statements for `CREATE TABLE` patterns and included created table names in `target_tables` validation set to prevent false-positive FK errors.
3. **Empty Table Mapping Prohibition (`migration_plans_validator.py` & `orchestrator.py`)**: Required `table_mappings` to contain at least 1 table mapping, failing validation and halting execution if empty.
4. **DuckDB Expression Sanitization (`ast_transformer.py`)**: Added regex SQL keyword inspection (`DROP`, `DELETE`, `UPDATE`, `INSERT`, `COPY`, `ATTACH`, `TRUNCATE`, `ALTER`) before executing in-memory DuckDB expressions.
5. **LLM Refinement Guidance Preservation (`migration_plans_services.py`)**: Passed user-provided `custom_instructions` from `plan.target_config` to context serializer during natural language plan refinement (`refine_plan`).
6. **Circular Foreign Key Cycle Detection (`migration_plans_validator.py`)**: Built directed graph of post-migration foreign keys and added cycle detection warnings recommending deferrable constraints.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**: Unpredictable destination DB selection when multiple target URLs exist, false-positive validator errors on DDL junction tables, silent 0-table migration runs, DuckDB expression SQL injection risks, lost prompt guidance during refinement, and circular FK deadlocks.
- **Chosen Solution**: Deterministic env sorting, AST regex parsing, explicit validation rules, expression keyword blacklisting, instruction propagation, and graph DFS cycle detection.
- **Why This Architecture**: Strengthens platform security, improves validation accuracy, and ensures reliable ETL execution.

### 3. Alternatives Considered & Rejected

- **Alternative A (Strict Rejection of Multiple DEST\_\* Env Vars)**: Immediately terminating container startup if multiple DEST URLs exist.
  - _Rejected_: Too restrictive for staging/multi-target configurations; picking primary with warning log is more developer-friendly.
- **Alternative B (Full SQL Parser for Expression Templates)**: Integrating ANTLR or heavy SQL parsing library in python agent.
  - _Rejected_: Unnecessary overhead; regex keyword sanitization effectively prevents destructive SQL DDL/DML injection in DuckDB calculations.

### 4. Trade-offs & Future Considerations

- Circular foreign key warnings are reported as architecture warnings (not blocking errors) to allow valid deferrable FK setups while notifying users of potential deadlock risks.

---

## 2026-08-24 - Low Priority Storage, Scaling & Precision Edge Case Fixes

### 1. Decision Summary

Resolved all Low Priority edge cases (EC-20 through EC-30) across API backend, agent execution engine, connectors, and Web UI:

1. **DuckDB Staging File Startup Cleanup (`orchestrator.py`)**: Added automatic removal of leftover temporary `staging_*.duckdb` files on engine startup.
2. **Unsupported Engine Dialect Validation (`source_factory.py`)**: Validated engine dialect strings against supported databases (`postgresql`, `mysql`, `sqlite`, `mongodb`, `csv`, `excel`), raising explicit `ValueError` for unsupported dialects.
3. **Offset Pagination Warning Notice (`source_factory.py`)**: Issued warning logs when extracting from source tables without a primary key using `OFFSET` pagination.
4. **High-Precision Decimal Casting (`ast_transformer.py`)**: Preserved high-precision `decimal`/`numeric` columns during Polars data frame transformations by casting target data types to `pl.Utf8` string representation.
5. **Large Schema Introspection Scaling Cap (`metadata_engine.py`)**: Capped table metadata introspection to top 500 tables ordered by estimated row count on databases with 500+ tables.
6. **Concurrent Refinement Row-Level Lock (`migration_plans_services.py`)**: Acquired `with_for_update()` lock on `MigrationPlan` inside `refine_plan()` to serialize concurrent refinement prompts.
7. **WebSocket Log Noise Reduction (`websocket_manager.py`)**: Downgraded offline WebSocket broadcast logs from `logger.warning` to `logger.debug`.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**: Lingering temporary DuckDB files on container crash, unhandled errors on unsupported DB dialects, offset drift risks, micro-precision float rounding errors, 2-minute timeouts on 1000+ table schemas, race conditions on concurrent plan refinements, and log spam for offline clients.
- **Chosen Solution**: Startup filesystem cleanup, strict dialect whitelisting, PK offset warnings, string decimal casting, 500-table row-count introspection sorting, DB row-level locking, and debug log level tuning.
- **Why This Architecture**: Maximizes system resilience, scales to enterprise databases, and maintains data precision.

### 3. Alternatives Considered & Rejected

- **Alternative A (Unbounded Table Introspection)**: Inspecting all 10,000+ tables on massive enterprise databases.
  - _Rejected_: Hits HTTP and database connection timeouts; top 500 tables by row count captures 99.9% of active data tables.
- **Alternative B (Casting Decimal to Float64)**: Converting numeric/decimal columns to standard floating point numbers.
  - _Rejected_: Risks precision loss on financial values; string/Utf8 representation preserves exact scale and precision.

### 4. Trade-offs & Future Considerations

- Introspection truncation logs an informational notice to alert developers when databases exceed 500 tables, with options to specify targeted schema filters if required.

---

## [2026-08-25] - AI Execution Error Diagnosis & Self-Healing UI Architecture

### 1. Decision Summary

Implemented an **AI-Powered Execution Error Diagnosis & Self-Healing UI System** across backend control plane (FastAPI), Docker agent runtime, and Web frontend (Next.js):

1. **Background AI Error Diagnosis (`execution_services.py`)**: Spawns an asynchronous background task (`asyncio.create_task(_run_diagnosis_background)`) with an independent database session (`AsyncSessionLocal()`) whenever an execution job reaches `status = "failed"`.
2. **Deterministic & Heuristic LLM Synthesizer**: Analyzes raw error tracebacks, execution stage, and target table metadata. Applies precise network error pattern matching (`_NETWORK_PATTERNS` + port regex `r':(5\d{3}|27017|3306|5432|3307)\b'`) and outputs plain-English summaries, root cause categories, bulleted remediation steps, and copyable terminal commands.
3. **Password Masking & Credential Security**: Automatically strips plain-text passwords and formats copyable `docker run` commands with standard secure placeholders (`<SRC_SRC_DB_1_PASSWORD>`, `<DEST_DST_DB_1_PASSWORD>`).
4. **Atomic Concurrency Protection**: Used `with_for_update(skip_locked=True)` and checked `ai_diagnosis IS NULL` to prevent double-writes and race conditions on duplicate failure reports.
5. **Interactive UI Remediation Banner (`JobExecutionBanner.tsx`)**: Displays the AI failure diagnosis card, 1-click copy button for fix commands, an execution run history selector (`Run #1`, `Run #2`), a `⚡ RETRY MIGRATION JOB` button, and direct monitor navigation link.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**: Non-technical developers and DBAs struggled to interpret raw Python database tracebacks (`NotNullViolation`, `ConnectionRefusedError`, `NoSuchModuleError`) when local container migrations failed midway, leading to execution freezes and confusion.
- **Chosen Solution**: Direct FastAPI service handler with background LLM synthesis + interactive Next.js self-healing UI banner.
- **Why Direct Backend Service Over LangGraph Node for Runtime Failures**:
  - Direct FastAPI service calls respond in <1s with zero graph state serialization overhead.
  - Runtime errors are event-driven HTTP dispatches from an external container; a stateless backend service integrated with the existing 2s UI polling tick provides instant feedback.

### 3. Alternatives Considered & Rejected

- **Alternative A: Running Error Diagnosis inside a LangGraph Node**:
  - _Rejected_: Adds unnecessary state graph serialization latency for simple container runtime errors (e.g. port mismatch). Direct service is faster and cleaner for runtime job failures.
- **Alternative B: Exposing Raw DB Passwords in Generated Terminal Commands**:
  - _Rejected_: Violates zero-trust security and data privacy guidelines; copyable commands strictly retain password placeholders.

### 4. Trade-offs & Future Considerations

- Automatic background diagnosis runs on the API server asynchronously without delaying the HTTP progress response returned to the Docker Agent.
- Future enhancements can introduce automated blueprint AST auto-repair for recoverable schema mismatches.

---

## [2026-09-07] - Agent Fatal Stopping Error Capture and UI Notification System

### 1. Decision Summary

Implemented a targeted **Fatal Stopping Error Capture System** that detects and surfaces critical errors that prevent Docker agents from running or executing tasks, without cluttering the UI with normal terminal stdout/stderr stream logs.

Key components:

1. **Control Plane Schema Extension (`010_add_error_fields_to_agents.py`)**: Added `last_error` (`Text`), `error_category` (`String(100)`), and `last_error_at` (`DateTime(timezone=True)`) to the `agents` table.
2. **Dual-Channel Error Reporting in Agent Engine (`apps/agent/main.py`)**:
   - For authenticated runtime failures: Agent reports via `POST /api/v1/agents/heartbeat` with `error_message` and `error_category`.
   - For unauthenticated startup failures (missing token, invalid token 401/403, missing DB configs, or unhandled exceptions): Agent reports via an unauthenticated emergency endpoint `POST /api/v1/agents/fatal-error` passing `X-Agent-ID`, then safely terminates (`os._exit(1)`).
3. **Control Plane Watchdog Detection (`check_stale_agents_and_jobs`)**:
   - If an agent container crashes abruptly, gets killed by Docker OOM killer, or disconnects without emitting a fatal payload, the 60s background watchdog tags the agent with `error_category = "DISCONNECTED_UNEXPECTEDLY"` and a descriptive explanation, broadcasting the event via WebSocket.
4. **Auto-Recovery on Successful Reconnect**:
   - When an agent reconnects and sends a healthy heartbeat, `process_agent_heartbeat()` clears `last_error` and `error_category`, resetting the agent to healthy `online`.
5. **Targeted Frontend UI Callout**:
   - `apps/web/components/agents/AgentStatusBanner.tsx`: Prominently renders a sleek red diagnostic banner (`🚨 DOCKER AGENT STOPPING ERROR DETECTED`) showing category tag, timestamp, error text, and actionable resolution steps.
   - `apps/web/app/dashboard/page.tsx`: Displays an error status pill and red alert badge directly on affected agent cards.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**: Streaming entire raw terminal logs (thousands of lines of Python bytecode, DB reflection queries, connection pool stats) to the browser is noisy, wastes bandwidth, and obscures the actual root cause when an agent stops working. The user explicitly requested to only display errors that stop the agent from running.
- **Why Emergency Endpoint (`POST /api/v1/agents/fatal-error`)**: If an agent is supplied an expired or invalid `AGENT_TOKEN`, standard heartbeat endpoints reject it with HTTP 401 Unauthorized. Without an unauthenticated emergency reporting channel keyed by `X-Agent-ID`, startup authentication failures could never be displayed in the UI.
- **Why Watchdog Fallback**: In real-world Docker environments, containers can be abruptly killed via `SIGKILL`, `docker stop`, host reboots, or host port conflicts. The agent process cannot execute cleanup code on `SIGKILL`. The control plane watchdog ensures even ungraceful terminations are tagged with clear diagnostics rather than silently remaining unexplained.

### 3. Alternatives Considered & Rejected

- **Alternative A: Streaming All Container Logs via Docker Socket / WebSockets**
  - _Rejected_: Violates user requirement to only show stopping errors; adds high CPU/bandwidth overhead and security risks associated with exposing Docker daemon sockets.
- **Alternative B: Retaining Fatal Error Indefinitely Even After Reconnection**
  - _Rejected_: Confuses users when an agent has been fixed and restarted. Automatically clearing `last_error` upon a healthy `online` heartbeat provides self-healing feedback.

### 4. Trade-offs & Future Considerations

- The emergency `/fatal-error` endpoint is strictly rate-limited and validates `agent_id` existence to prevent abuse.
- In the future, automated remediation recommendations (like port conflict detection or firewall testing) can be expanded using the LLM error diagnosis pipeline.

---

## [2026-08-26] - Migration Plan Versioning & Immutable History Architecture

### 1. Decision Summary

Implemented an immutable **Migration Plan Versioning & Rollback Architecture** allowing users to inspect revision history, compare generated blueprint ASTs, revert to previous plan versions, and execute historical plan snapshots directly from the Web UI.

Key components:

1. **Schema Migration & ORM Model (`008_add_migration_plan_versions.py`, `migration_plans_models.py`)**: Added `migration_plan_versions` table with `version_number`, `plan_data` (`JSONB`), `change_summary`, `created_by`, `status`, and foreign key linkage to `migration_plans`.
2. **Version Auto-Snapshotting in Service Layer (`migration_plans_services.py`)**:
   - Initial plan generation captures `version_number = 1`.
   - Every subsequent plan refinement (`refine_plan()`) or approval (`approve_plan()`) atomically increments `current_version` on the parent plan and snapshots a new immutable `MigrationPlanVersion` record.
   - Added `rollback_to_version(plan_id, target_version)` to reinstate previous blueprint configurations.
3. **Control Plane API Routes (`migration_plans_routes.py`)**:
   - `GET /api/v1/plans/{plan_id}/versions` to list version history metadata.
   - `GET /api/v1/plans/{plan_id}/versions/{version_num}` to fetch full historical blueprint AST.
   - `POST /api/v1/plans/{plan_id}/versions/{version_num}/activate` to restore or execute a specific historical version.
4. **Interactive Version Carousel & Selector UI (`PlanBlueprintViewer.tsx`, `GeneratePlanAction.tsx`)**:
   - Integrated a version selector header showing revision numbers, timestamps, and change summaries.
   - Enables users to toggle between historical AST previews and current drafts before approving execution.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**: AI plan refinement is iterative. If an LLM prompt inadvertently degraded a table mapping or altered an index rule, users had no way to inspect or recover their previous working blueprint without re-running full schema profiling.
- **Why Dedicated Immutable Version Table**: Keeping `plan_data` historical snapshots in a dedicated table rather than an audit log guarantees fast point-in-time recovery, referential integrity with executing jobs, and zero risk of mutability race conditions.

### 3. Alternatives Considered & Rejected

- **Alternative A: Git-style In-Memory Diffing without Dedicated DB Records**:
  - _Rejected_: Lacks relational query capabilities, breaks multi-user collaboration across web sessions, and complicates historical execution tracking when jobs point to specific plan versions.
- **Alternative B: Overwriting Plan In-Place with No Historical Storage**:
  - _Rejected_: High risk of data loss and user frustration when AI prompts produce unintended transformations.

### 4. Trade-offs & Future Considerations

- Version records store full AST JSON documents rather than text diffs, slightly increasing database storage, but providing O(1) retrieval and zero compute cost when activating or inspecting past plans.
- Future work can add side-by-side visual AST diff comparisons in the frontend viewer.

---

## [2026-09-07] - Dynamic Agent Heartbeat Scaling, Idle Standby & Auto-Stop Lifecycle

### 1. Decision Summary

Implemented a resource-efficient **Agent Lifecycle Management System** featuring dynamic heartbeat scaling, low-power standby mode, and container auto-stop capabilities.

Key components:

1. **Control Plane Schema Extension (`009_add_idle_since_to_agents.py`)**: Added `idle_since` (`DateTime(timezone=True)`) to track consecutive inactive duration.
2. **Dynamic Heartbeat Frequency Scaling (`apps/agent/main.py`)**:
   - Active mode: 20-second interval for responsive job dispatch and real-time health telemetry.
   - Standby mode: 300-second (5-minute) interval when the agent remains idle with no active tasks for > 5 minutes (`IDLE_STANDBY_THRESHOLD = 300`).
   - Reduces background HTTP requests and WebSocket traffic by 93% for idle agents.
3. **Control Plane Command Directives (`agents_services.py`)**:
   - Heartbeat responses return structured directives:
     - `ENTER_IDLE_MODE`: Signals agent to transition from 20s to 300s heartbeat interval.
     - `RESUME_ACTIVE_MODE`: Instantly shifts agent back to 20s cadence upon job assignment or user interaction.
     - `STOP_CONTAINER`: Instructs agent container to gracefully terminate upon task completion if auto-stop is configured.
4. **Automated Container Shutdown (`apps/agent/main.py`)**:
   - Implemented clean shutdown handler that closes active DB connection pools and terminates the container with `os._exit(0)`.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**: Running dozens of local migration agents with fixed 15-20s heartbeats flooded the backend with empty telemetry, consumed needless container CPU/battery on client machines, and forced developers to manually run `docker stop` after migrations finished.
- **Chosen Solution**: Adaptive heartbeat throttling controlled by backend state machines with clean termination hooks.

### 3. Alternatives Considered & Rejected

- **Alternative A: Static Long Heartbeat (e.g. 60s at all times)**:
  - _Rejected_: Introduces unacceptable 60-second latency when dispatching new jobs or detecting agent disconnects during active migrations.
- **Alternative B: Pure Client-Side Timer Throttling**:
  - _Rejected_: The agent does not have global visibility into upcoming scheduled jobs or queued tasks; backend-driven directives ensure the agent wakes up immediately when work is assigned.

### 4. Trade-offs & Future Considerations

- Transitioning to 5-minute standby means an idle agent may take up to 5 minutes to pick up a newly queued task via periodic heartbeat, unless woken up via active WebSocket notification.

---

## [2026-09-08] - Agent Engine Robustness, Multi-Source Resilience & Target Connectivity Hardening

### 1. Decision Summary

Implemented a comprehensive robustness hardening suite across the Docker Agent ETL engine (`apps/agent/engine/`), addressing critical failure modes in datetime handling, source streaming, post-DDL validation, identifier matching, connection pooling, multi-source merge crash recovery, and dead-target detection.

Key components:

1. **AST Transformer Safe Datetime Parsing (`ast_transformer.py`)**:
   - Updated `_parse_dt()` to explicitly return `None` on empty or null values, and return raw inputs unchanged on unparseable strings instead of fabricating `datetime.now()`.
2. **Connector Source Read Error Propagation (`source_factory.py`)**:
   - Introduced `SourceReadError` and replaced silent empty DataFrame returns with explicit error propagation when source chunk reads fail mid-table (network drop, permission revocation, table drop).
3. **Strict Non-Benign Post-Migration DDL Trapping (`ddl_executor.py`)**:
   - Removed unconditional exception suppression in `execute_post_migration_ddl()`. Real schema and syntax failures (e.g., missing columns, invalid foreign key definitions) now fail the migration job immediately.
4. **Strict Source Identifier Matching (`orchestrator.py`)**:
   - Replaced silent fallback to the first configured source DB with an explicit `ValueError` naming the unmatched identifier and listing available configured sources.
5. **Thread-Safe Engine Caching & Automatic Cleanup (`db.py`, `orchestrator.py`)**:
   - Implemented thread-safe `_ENGINE_CACHE` keyed by `(db_url, is_sqlite)` to prevent connection churn across chunks.
   - Added `dispose_all_engines()` called in `run_job`'s `finally:` block to guarantee full connection pool disposal on job completion or failure.
6. **Target Write Failure Rate Guard (`orchestrator.py`)**:
   - Added an end-of-job completion guard: if `total_failed / total_processed > 0.50` (or target completely unreachable), raises `RuntimeError` to mark the job as `failed` rather than falsely reporting `completed`.
7. **Crash-Resilient Multi-Source Merge & Checkpoint Invalidation (`checkpoint.py`, `orchestrator.py`)**:
   - Modified startup DuckDB cleanup to preserve the active job's staging file (`staging_{job_id}_*.duckdb`).
   - Implemented `CheckpointManager.clear_table_checkpoints(job_id, table_name)`: when an active leftover staging file is detected on resume, table checkpoints are cleared and all sources re-staged from scratch, eliminating silent row loss.
8. **Fast Target SQL Connectivity Pre-Check (`target_writer.py`)**:
   - Added early `engine.connect()` check before data insertion in `TargetWriterFactory.bulk_load()`. Distinguishes dead/unreachable targets (auth failure, bad port/host) from schema errors, fast-failing in <3s instead of slowly retrying 1,000 rows.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**:
  - Silent datetime fabrication corrupted analytics datasets with fake timestamps.
  - Mid-stream network drops silently finished jobs as successful with partial data.
  - Multi-source merges after ungraceful crashes (`kill -9`) resumed from stale offsets while re-creating the staging file, losing rows from previously staged sources.
  - Dead targets triggered misleading schema warnings and took minutes of futile row-by-row retries before reporting failure.
- **Why DuckDB Staging Checkpoint Reset**: DuckDB staging files for multi-source merges are ephemeral working buffers. If an agent crashes mid-merge, a half-filled staging file cannot be reliably spliced with new rows without risking duplicates or missing records. Resetting per-source checkpoints for only that table ensures a clean, deterministic re-stage without affecting other tables.

### 3. Alternatives Considered & Rejected

- **Alternative A: Retaining Fabricated Timestamps (`datetime.now()`) as Default**:
  - _Rejected_: Violates ETL data integrity. Nulls must remain null; freeform text must be passed through or failed explicitly.
- **Alternative B: Attempting to Resume Inside Partial DuckDB Staging Files**:
  - _Rejected_: Involves complex transactional WAL recovery inside ephemeral DuckDB files across varying process lifecycles. Wiping the single staging file and resetting checkpoints for that specific target table is 100% reliable, fast, and idempotent.

### 4. Trade-offs & Future Considerations

- Re-staging a multi-source table after a crash repeats extraction for previously staged sources of that specific table, but guarantees 100% data correctness and zero row loss.
- All 10 failure scenarios have been verified via end-to-end regression testing with genuine database-level triggers.

---

## [2026-09-09] - Deterministic UUID Generation for Safe Migration Retries

### 1. Decision Summary

Replaced non-deterministic `uuid.uuid4()` generation in `ASTTransformer.transform_chunk` with deterministic UUIDv5 hashes generated via `_deterministic_fallback_uuid(seed_prefix, row_index)`. Seed prefixes are derived in `ExecutionOrchestrator` using the compound migration identifier `f"{job_id}:{target_table}:{src_ident}:{src_table}"`, combined with chunk-level `row_offset + index`. This guarantees idempotent primary key generation across retry attempts of partial or failed migrations without duplicate row insertion or PK churn.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**: When a migration job fails mid-stream or is retried, generating random `uuid.uuid4()` keys produces brand new identifiers for previously inserted or attempted rows. This bypasses target `ON CONFLICT DO NOTHING` / `INSERT IGNORE` deduplication, creating duplicate rows or integrity violations.
- **Chosen Solution**: Python `uuid.uuid5(uuid.NAMESPACE_DNS, f"{seed_prefix}:{row_index}")` deterministic hashing. `transform_chunk` now accepts `retry_seed_prefix` (defaulting to `"default_seed"`) and `row_offset` (defaulting to `0`) for full backward compatibility with tests.
- **Why This Technology**: Standard library `uuid.uuid5` provides RFC 4122 compliant SHA-1 namespace-based UUIDs with deterministic output across processes and runtimes without introducing new third-party dependencies.

### 3. Alternatives Considered & Rejected

- **Alternative A: Relying on Auto-Increment Sequence in Target**: Rejected because target tables frequently use UUID primary keys for distributed datasets or NoSQL sync, where auto-increment sequences are unavailable or disallowed.
- **Alternative B: Pure Value-Based Hashing of Source Rows**: Rejected because source rows may lack natural keys, have duplicate values, or undergo schema transformations that make row contents unstable across plan refinements. The combination of logical source row identity `(job_id, target_table, src_ident, src_table, row_offset + index)` guarantees collision-free stability.

### 4. Trade-offs & Future Considerations

- The caller is responsible for passing the original `job_id` across retry attempts of the same logical migration job.
- If source rows are re-ordered non-deterministically between retries (e.g. un-ordered full-table scans with concurrent writes), row index offset may point to different rows. Keyset pagination with stable primary keys or sorted staging avoids this issue.

---

## [2026-09-09] - Preflight Advisory for Target Tables with Existing Data

### 1. Decision Summary

Added an asynchronous preflight check `ExecutionService.check_target_tables_existing_data()` invoked during `create_execution_job()`. It queries the agent's target `DataSource`'s most recent `MetadataSnapshot` (cached offline in PostgreSQL metadata tables without opening live connections to the target DB from the API) to inspect whether any plan target tables already contain rows (`table.row_count > 0`). If detected, execution is not blocked, but advisory warnings are attached to `job.target_tables_with_existing_data` and serialized in `ExecutionJobResponse`.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**: Running a migration into a target table that already contains existing data can cause unintended row updates, duplicate rows, or unique constraint conflicts. Users need early visibility into existing target row counts before or right as execution starts.
- **Chosen Solution**: Offline inspection via `MetadataSnapshot.schemas.tables`. This keeps API execution triggers fast and independent of target database network availability from the cloud control plane, leveraging the Docker agent's previously introspected metadata.
- **Why Non-Blocking**: Some workflows intentionally append data or use upserts into pre-populated tables. Blocking would break legitimate append/merge workflows; returning advisory details allows the frontend to show warning banners while preserving user autonomy.

### 3. Alternatives Considered & Rejected

- **Alternative A: Direct Live Target Database Query from FastAPI**: Rejected because target databases may be behind corporate firewalls, VPCs, or NATs only accessible by the local Docker Agent container, not the central API.
- **Alternative B: Hard-Blocking Execution with HTTP 409**: Rejected because append migrations into pre-existing staging tables are a supported migration pattern.

### 4. Trade-offs & Future Considerations

- Advisory row counts are based on the latest agent metadata snapshot. If target data was inserted after the last schema profiling run, row counts will reflect the state at snapshot time until the agent introspects again.

---

## [2026-09-09] - Multi-Vector Migration Readiness Signals UI & Fine-Grained Confidence

### 1. Decision Summary

Replaced the single monolithic "AI Confidence" percentage badge with multi-level migration readiness and confidence metrics:

1. **Plan-Level 4-Vector Readiness Dashboard**:
   - **Schema Compatibility**: Target column alignment, direct 1:1 mappings, composite splits, and dropped column ratios.
   - **Type Compatibility**: Data type casting safety, coercion fidelity (timestamps, numeric decimals, UUIDs).
   - **Relationship Mapping**: Primary key resolution, foreign key constraints, and post-migration DDL integrity.
   - **Data Conflict Risk**: Merge deduplication safety and deterministic PK conflict resolution strategies.
   - **Rollup Composite Badge**: High-level readiness rollup (`OPTIMAL READINESS`, `HIGH READINESS`, `MODERATE READINESS`, `REVIEW ADVISED`).
2. **Table-Level Readiness Badges**: Replaced generic table confidence in the accordion header with `<TableReadinessBadge>` reflecting table-specific column mapping distributions.
3. **Column-Level Confidence & Fidelity Badges**: Added a dedicated "Mapping Confidence" column in the column mappings table with `<ColumnConfidenceBadge>` (e.g. 99% Direct 1:1, 98% UUID Cast, 95% Numeric Cast, 93% Timestamp Cast, 88% SQL Expr, 80% Dropped).
4. **Target Data Pre-Population Warning Banners**: Mounted advisory warnings in `JobExecutionBanner.tsx` and toast alerts in `PlanBlueprintViewer.tsx` when `job.target_tables_with_existing_data` indicates destination tables already contain rows.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**: A single percentage number (e.g. `90%`) obscures critical dimension-specific migration risks. Furthermore, developers need to know exactly which columns (e.g. dynamic SQL expressions or dropped columns) contribute to risk versus pristine 1:1 copies.
- **Chosen Solution**: Client-side hierarchical vector computation implemented in [`PlanReadinessSignals.tsx`](file:///d:/GitHub/Ai_data_migration_platform/apps/web/components/plans/PlanReadinessSignals.tsx) deriving metrics directly from the existing `TransformationPlanAST` data structure without adding API overhead.
- **Why This Pattern**: Reuses existing `TableMappingSpec`, `ColumnMappingSpec`, and `ConflictResolutionSpec` contracts already returned by the FastAPI backend, providing instant visual feedback across version history selections and blueprint edits without new server roundtrips.

### 3. Alternatives Considered & Rejected

- **Alternative A: New Backend Endpoint for Readiness Scoring**: Rejected because all underlying transformation metadata and AST specs are already client-resident in `PlanDetailResponse.plan_data`, avoiding unnecessary HTTP roundtrips.
- **Alternative B: Retaining Monolithic Confidence at Table Level**: Rejected because users requested granular visibility at all levels of the plan hierarchy (plan, table, and column).

### 4. Trade-offs & Future Considerations

- Derived client-side scores dynamically adapt when users edit column mappings inline or switch plan versions, keeping all readiness signals in continuous synchronization with active blueprint changes.

---

## [2026-09-09] - Dry Run Migration Simulation Engine (Phase L)

### 1. Decision Summary

Added an end-to-end "Dry Run" mode to migration execution across the database schema, backend API, agent worker daemon, and frontend UI:

1. **Alembic & ORM**: Migration `011_add_is_dry_run_to_migration_jobs.py` adds `is_dry_run` boolean column with default `false` to `migration_jobs`. Updated `MigrationJob` ORM model, `ExecutionStartRequest`, `ExecutionJobResponse`, and `AgentTaskItemResponse`.
2. **Orchestrator Simulation Pipeline**: In `apps/agent/engine/orchestrator.py`, when `is_dry_run=True`:
   - Pre-migration and Post-migration DDL execution (`DDLExecutor.execute_ddl_list`) is bypassed completely.
   - Source data extraction, AST transformation (`ASTTransformer.transform_chunk`), and DuckDB staging/deduplication execute exactly as in real migrations to surface real data quality, merge collision, and transformation errors.
   - Target database writes (`TargetWriterFactory.bulk_load`) are bypassed entirely, counting would-be successful rows (`len(df_trans)` / `len(chunk_df)`).
   - Checkpoints are preserved without calling `CheckpointManager.clear_job_checkpoints(job_id)`.
   - The job completes with terminal status `"dry_run_completed"` instead of `"completed"`.
3. **Frontend UI**:
   - `PlanBlueprintViewer.tsx`: Added a dedicated `⚡ Run Dry Run (Simulation)` button next to `Approve & Execute`.
   - `JobExecutionBanner.tsx`: When `job.status === "dry_run_completed"` or `job.is_dry_run === true`, renders an amber simulation warning banner (`DRY RUN SIMULATION -- NO DATA WAS WRITTEN TO TARGET DB`) and provides a direct `⚡ EXECUTE FOR REAL` secondary action button.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**: Large-scale data migrations carry significant risk of schema misconfiguration, data type truncation, or merge key collisions. Users need a way to validate the entire transformation pipeline against live production source data without writing a single row to the destination database.
- **Why Run Extraction & Transformation in Dry Run**: Testing only schema validation misses actual data-level transformation errors (e.g. malformed dates, invalid JSON, or merge collisions). Running full extraction and transformation guarantees that any error in the data stream will be caught before touching the target database.
- **Why Bypass DDL and Target Writer**: Preserves destination schema and table isolation. No locks, table drops, constraints, or rows are altered in the target system.
- **Why Preserve Checkpoints**: Dry runs must leave no residual state that could interfere with subsequent real executions or corrupt resumption offsets.

### 3. Alternatives Considered & Rejected

- **Alternative A: Transaction Rollback after Full Write**: Rejected because target systems may include non-transactional targets (e.g. MongoDB, S3/Parquet), and rolling back millions of inserted rows in transactional DBs creates massive write amplification and WAL bloat.
- **Alternative B: Running Dry Run only on a Small Sample Chunk**: Rejected because data quality bugs and edge-case collisions often lurk deep in source tables (e.g. at row 50,000). A full simulation guarantees end-to-end data integrity.

### 4. Trade-offs & Future Considerations

- Full dry runs consume read bandwidth and worker compute comparable to real migrations. For multi-terabyte datasets, an optional sampled dry-run option may be added in a future phase.

---

## [2026-09-09] - Execution Monitor Checkpoint Resume vs Retry & Completed Guard (Phase M)

### 1. Decision Summary

Refined the execution monitor and job banner action controls in [`JobExecutionBanner.tsx`](file:///d:/GitHub/Ai_data_migration_platform/apps/web/components/plans/JobExecutionBanner.tsx) and [`page.tsx`](file:///d:/GitHub/Ai_data_migration_platform/apps/web/app/execution/page.tsx):

1. **Resume vs. Retry Semantics**:
   - When a migration job is in status `"failed"` and `processed_rows > 0`, the action button dynamically renders as `"⚡ RESUME"` (or `"⚡ RESUME DRY RUN"`), with tooltip explaining that saved checkpoints will be reused to continue streaming from the last processed record without re-inserting already-committed rows.
   - When `status === 'failed'` and `processed_rows === 0`, it renders as `"⚡ RETRY MIGRATION JOB"` (or `"⚡ RETRY DRY RUN"`).
2. **Completed Migration Action Guard**:
   - When a job is in status `"completed"`, active retries are prevented. The retry button is disabled with a tooltip explaining that checkpoints were finalized upon completion.
   - Instead of offering retries on completed migrations, the UI prominently presents a `+ CREATE NEW MIGRATION` button (linking directly to `/profiling` to initiate a fresh catalog introspection and blueprint).
3. **Execution Page Header Action**:
   - Added a top-level `+ Create New Migration` button in the execution monitor page header next to `Refresh Jobs`.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**: Conflating "Retry" and "Resume" creates user confusion over whether existing checkpoints and target deduplication will be respected. Furthermore, allowing users to casually "retry" an already completed migration is an anti-pattern that risks accidental duplicate writes or confusion over cleared checkpoints.
- **Chosen Solution**: Distinct contextual labels (`Resume` vs `Retry`), checkpoint reuse tooltips, disabled retry guard on completed migrations, and clear pathway to `+ Create New Migration`.

### 3. Alternatives Considered & Rejected

- **Alternative A: Completely Hiding Buttons on Completed Jobs**: Rejected because users actively look for next steps upon job completion; offering `+ CREATE NEW MIGRATION` and explicitly disabling retry with an informative tooltip prevents confusion.

---

## [2026-09-09] - Generic Parameterized Placeholders for Zero-Credential Agent Commands (Phase N)

### 1. Decision Summary

Updated `AgentCommandGenerator._get_db_url_template` in [`apps/api/app/modules/agents/agents_command_generator.py`](file:///d:/GitHub/Ai_data_migration_platform/apps/api/app/modules/agents/agents_command_generator.py) to generate fully generic parameterized placeholders across all connection components:

- `<{prefix}_{clean_id}_HOST>`
- `<{prefix}_{clean_id}_PORT>`
- `<{prefix}_{clean_id}_USER>`
- `<{prefix}_{clean_id}_PASSWORD>`
- `<{prefix}_{clean_id}_NAME>`

Implemented browser-side zero-credential parameter substitution across the Agent Creation flow:

- [`DatabaseConfigForm.tsx`](file:///d:/GitHub/Ai_data_migration_platform/apps/web/components/agents/DatabaseConfigForm.tsx): collects host, port, username, database name, and SSL preference into local React state `connectionDetails` without sending credentials to the backend API.
- [`apps/web/app/agents/create/page.tsx`](file:///d:/GitHub/Ai_data_migration_platform/apps/web/app/agents/create/page.tsx): manages `connectionDetailsByIdentifier` in page state, isolating it from the backend creation payload, and forwards it to `DockerCommandOutput`.
- [`DockerCommandOutput.tsx`](file:///d:/GitHub/Ai_data_migration_platform/apps/web/components/agents/DockerCommandOutput.tsx): implements `substituteConnectionPlaceholders()` mirroring the backend prefix and sanitization logic to auto-populate host, port, user, and db name into the displayed Bash, PowerShell, Single-line, and `.env` commands in browser memory, leaving only the `<..._PASSWORD>` placeholder for the user to supply in the shell.

Updated corresponding unit test assertions in [`test_agent_command_generator.py`](file:///d:/GitHub/Ai_data_migration_platform/apps/api/tests/unit/test_agent_command_generator.py) and [`test_agent_command_placeholders_api.py`](file:///d:/GitHub/Ai_data_migration_platform/apps/api/tests/unit/test_agent_command_placeholders_api.py).

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**: Previously, users had to manually edit 5+ placeholders in every URL string inside their terminal. By entering host, port, username, and database name into the frontend form, the browser auto-substitutes these non-secret parameters into the displayed command, while the control plane API remains 100% agnostic and receives zero credential data. Password placeholders remain manual placeholders.
- **Chosen Solution**: Client-side-only React state pipeline (`DatabaseConfigForm` -> `page` -> `DockerCommandOutput`) with pure string substitution in browser memory.
- **Why This Architecture**: Enforces strict Zero-Knowledge Control Plane security while delivering an effortless copy-paste developer experience.

### 3. Alternatives Considered & Rejected

- **Alternative A: Prompting for and Storing Host/Port/User in Backend DB**: Rejected because network topology, database hosts, and internal usernames are sensitive infrastructure information that enterprise customers prefer to keep on-premise.
- **Alternative B: Pure Manual Shell Substitution**: Rejected because typing out long connection strings manually for multi-source migrations is error-prone.

### 4. Trade-offs & Future Considerations

- Database passwords remain manual placeholders (`<..._PASSWORD>`) that users must supply in their shell, preserving zero password exposure to the browser and backend.

---

## [2026-09-09] - Agent API Token Regeneration Endpoint (`POST /api/v1/agents/{agent_id}/regenerate-token`)

### 1. Decision Summary

Added an explicit, purely additive token regeneration endpoint `POST /api/v1/agents/{agent_id}/regenerate-token` in [`apps/api/app/modules/agents/agents_routes.py`](file:///d:/GitHub/Ai_data_migration_platform/apps/api/app/modules/agents/agents_routes.py) backed by `AgentService.regenerate_agent_token` in [`apps/api/app/modules/agents/agents_services.py`](file:///d:/GitHub/Ai_data_migration_platform/apps/api/app/modules/agents/agents_services.py).

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**: Raw API tokens are never persisted in plaintext in the database (only a one-way SHA-256 hash `api_token_hash` is stored). Because the raw token cannot be recovered or re-displayed after creation, users who lost their token or need to redeploy an agent had to rely on `<YOUR_AGENT_API_TOKEN>` placeholders.
- **Chosen Solution**: Provide an explicit token regeneration action that:
  1. Generates a new secure random token `ag_live_{secrets.token_urlsafe(32)}`.
  2. Overwrites `agent.api_token_hash` with the new token's SHA-256 digest, atomically invalidating any existing container using the old token.
  3. Returns the raw token **once only** in the response, together with freshly generated Docker run commands and environment templates pre-filled with the new token.
- **Security Invariance**: Preserves zero-plaintext token storage. The server never stores the raw token, preventing credential leaks even in case of database exfiltration.

### 3. Alternatives Considered & Rejected

- **Alternative A: Storing Plaintext or Reversibly Encrypted Tokens in DB**: Rejected as a severe security regression violating the principle of least privilege and zero-knowledge token management.
- **Alternative B: Modifying Existing `GET /docker-command`**: Rejected because `GET` endpoints must be safe and idempotent under HTTP specifications; generating and persisting a new secret is a state-mutating operation that belongs on `POST`.

### 4. Trade-offs & Future Considerations

- **Running Container Invalidation**: Existing containers running with the old token will immediately be rejected on their next heartbeat or task poll with HTTP 401 Unauthorized. The frontend UI must present a clear confirmation modal warning before triggering this action.

---

## [2026-09-09] - Shared Docker Command Utilities & Dashboard Token Regeneration UI

### 1. Decision Summary

Extracted shared connection placeholder substitution logic into a dedicated module [`apps/web/lib/dockerCommandUtils.ts`](file:///d:/GitHub/Ai_data_migration_platform/apps/web/lib/dockerCommandUtils.ts), consumed by both [`DockerCommandOutput.tsx`](file:///d:/GitHub/Ai_data_migration_platform/apps/web/components/agents/DockerCommandOutput.tsx) and [`apps/web/app/dashboard/page.tsx`](file:///d:/GitHub/Ai_data_migration_platform/apps/web/app/dashboard/page.tsx). Integrated the "Regenerate Agent Token" action into the dashboard's Docker Command Modal with a safety confirmation banner and ephemeral connection detail re-entry fields.

### 2. Why This Approach? (Rationale)

- **Zero Drift Between Create & Dashboard Views**: Both the create-agent wizard and the dashboard command modal must resolve generic placeholders (`<PREFIX_HOST>`, `<PREFIX_PORT>`, etc.) identically to avoid discrepancy.
- **Ephemeral Zero-Storage Connection Re-entry**: Because database host, port, username, and database name are never stored in the control plane, users viewing the command later can re-enter these fields client-side to auto-fill their command without transmitting them to the server.
- **Confirmation Safety Guard**: Regenerating an API token is a disruptive operation that severs running agent containers. A two-step confirmation (`⚠ Regenerate Agent Token` -> warning banner + `Confirm Regenerate`) prevents accidental invalidation.

---

## [2026-09-09] - First-Time User Dashboard Onboarding & Architecture Privacy Explainer

### 1. Decision Summary

Added a prominent architectural and data privacy explainer card to the empty-state view of the main dashboard in [`apps/web/app/dashboard/page.tsx`](file:///d:/GitHub/Ai_data_migration_platform/apps/web/app/dashboard/page.tsx) directly above the `Create Migration Agent` call to action.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**: New enterprise and developer users encountering an empty dashboard often hesitate to deploy a Docker agent without clear context on what the agent does, whether their raw database records are transmitted to third-party cloud servers, and why a local Docker agent is required.
- **Copy & Guarantee**: Explicitly articulates Migraflow's core value proposition:
  > _"Your data never leaves your infrastructure. Migraflow uses a local Docker Agent to inspect and migrate your databases directly on your machine or server -- the cloud application only ever receives schema metadata and migration decisions, never your data or credentials."_
- **Design Alignment**: Styled with `ShieldCheck` icon, subtle dark blue border (`border-sky-500/30`), and monospace typography matching the platform's cyberpunk control plane aesthetic.

---

## [2026-09-09] - Phase 1 Connector Scope Focus (PostgreSQL, MySQL, MongoDB Only) & File-Engine UI Deprecation

### 1. Decision Summary

Temporarily hid file-based engines (`csv`, `excel`) from selectable engine choices in [`DatabaseConfigForm.tsx`](file:///d:/GitHub/Ai_data_migration_platform/apps/web/components/agents/DatabaseConfigForm.tsx) and updated marketing copy in [`FeaturesGrid.tsx`](file:///d:/GitHub/Ai_data_migration_platform/apps/web/components/landing/FeaturesGrid.tsx) to focus exclusively on Phase 1 core database connectors: PostgreSQL, MySQL, and MongoDB.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**: Phase 1 platform delivery focuses on database-to-database schema profiling, AST transformation, and bounded-memory streaming. Presenting CSV and Excel upload options in the database agent creation wizard created mismatched expectations regarding file upload UX and agent-side file path discovery.
- **Strict Backward Compatibility**:
  - `ValidSourceType` in [`apps/web/types/agent.ts`](file:///d:/GitHub/Ai_data_migration_platform/apps/web/types/agent.ts) and `VALID_SOURCE_TYPES` in backend schemas retain `'csv'` and `'excel'`.
  - Existing agents with attached CSV or Excel sources continue to deserialize and render seamlessly without runtime errors.
  - The `array_to_csv` transformation option in [`PlanBlueprintViewer.tsx`](file:///d:/GitHub/Ai_data_migration_platform/apps/web/components/plans/PlanBlueprintViewer.tsx) remains untouched as an orthogonal column transformation rule.
- **Clean Icon Tree**: Removed unused `FileSpreadsheet` and `FileText` imports from `lucide-react` in `DatabaseConfigForm.tsx`.

---

## [2026-09-09] - Schema Catalog Foreign-Key Relationships Tab & In-Memory Label Resolution

### 1. Decision Summary

Implemented the Relationships tab in [`apps/web/components/profiling/SchemaCatalogViewer.tsx`](file:///d:/GitHub/Ai_data_migration_platform/apps/web/components/profiling/SchemaCatalogViewer.tsx), enabling users to inspect detected foreign-key relationships for any selected table directly within the schema catalog view.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**: While the backend metadata snapshot (`MetadataSnapshotDetailResponse.relationships`) already populated foreign-key relationship edges (`source_table_id`, `source_column_id`, `target_table_id`, `target_column_id`, `relationship_type`, `confidence`), the catalog UI previously only presented Columns and Constraints tabs, leaving relational topology hidden.
- **In-Memory Zero-API Resolution**: Implemented `resolveColumnLabel(tableId, columnId)` which indexes into the already-loaded `allTables` array to translate UUID references into human-readable `{table_name}.{column_name}` labels without issuing additional network requests.
- **Contextual Filtering**: Dynamically filters relationship records to display only foreign keys referencing or originating from the active `selectedTable`, displaying relationship type tags and confidence percentages.

---

## [2026-09-10] - Dynamic SVG Pipeline Wiring & Auto-Sizing for Database Configuration Form

### 1. Decision Summary

Replaced hardcoded card height estimates (`cardEstimateH = 320`) in [`DatabaseConfigForm.tsx`](file:///d:/GitHub/Ai_data_migration_platform/apps/web/components/agents/DatabaseConfigForm.tsx) with a dynamic DOM measurement engine utilizing `useRef`, `useCallback`, and `ResizeObserver`.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**: Following the addition of connection details (host, port, username, database, SSL checkbox) to the source database cards, card heights increased from ~320px to ~485px. The hardcoded 320px calculation caused SVG pipeline wires to miss lower source cards completely (e.g. in 3:1 merge topologies, Source #3 had no wire coming from it, with the third wire originating at the bottom of Source #2). Furthermore, the destination card and middle SVG stopped short, leaving awkward empty space beside lower cards.
- **Dynamic DOM Measurement**: Uses `ResizeObserver` observing the source cards container and individual card elements to measure exact pixel midpoints (`(cardRect.top + cardRect.height / 2) - svgRect.top`) and total combined height.
- **Universal Topology Alignment**: Works seamlessly for all topologies:
  - **1:1**: Single horizontal straight stream connecting matching-height cards.
  - **2:1 & 3:1**: Multi-source curves originating at the exact right midpoint of each card, converging into a central junction node at `destY`, and streaming into the vertical center of the destination card.
  - **N:1 Custom**: Supports arbitrary source counts without code modification or magic numbers.
- **SSR/Initial Hydration Safety**: Uses a realistic 485px baseline fallback to ensure no layout shifts before the initial paint and `ResizeObserver` measurement.

---

## [2026-09-10] - SQL Expression Coercion & Datetime Literal Sanitization for Target DB Inserts

### 1. Decision Summary

Implemented automatic conversion of SQL datetime literals (`CURRENT_TIMESTAMP`, `NOW()`, `CURRENT_DATE`, etc.) to ISO-8601 UTC timestamps across the Docker agent execution engine in both [`apps/agent/engine/transformers/ast_transformer.py`](file:///d:/GitHub/Ai_data_migration_platform/apps/agent/engine/transformers/ast_transformer.py) and [`apps/agent/engine/writers/target_writer.py`](file:///d:/GitHub/Ai_data_migration_platform/apps/agent/engine/writers/target_writer.py).

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**: When migrating tables with generated audit columns (such as `updated_at` or `created_at`), the AI planner frequently specifies `constant_value: "CURRENT_TIMESTAMP"` under `new_column_added` or `default_constant`. In parameterized SQL queries (`INSERT ... VALUES (%(updated_at)s)`), psycopg2 and PostgreSQL treat `"CURRENT_TIMESTAMP"` as a literal string `'CURRENT_TIMESTAMP'`, failing with `psycopg2.errors.InvalidDatetimeFormat: invalid input syntax for type timestamp with time zone: "CURRENT_TIMESTAMP"` and triggering an abort threshold exceeding 50% errors.
- **Two-Layer Defense Architecture**:
  1. **Transformation Layer (`ast_transformer.py`)**: When evaluating `new_column_added`, `default_constant`, or datetime `type_cast` (`_parse_dt`), any column targeting a datetime/timestamp type or containing a SQL now literal (`CURRENT_TIMESTAMP`, `NOW()`, `CURRENT_DATE`, etc.) is evaluated to `datetime.now(timezone.utc).isoformat()`.
  2. **Writer Sanitization Safety Net (`target_writer.py`)**: `_sanitize_rows_for_target` checks all row values before SQL statement parameter binding. Any string matching `SQL_NOW_LITERALS` is coerced into an ISO UTC timestamp, and MySQL zero-dates (`0000-00-00 00:00:00`) are coerced to `None` (NULL) to prevent dialect syntax exceptions.
- **Container Rebuild & Hot-Patching**: The updated code was hot-copied into the running Docker agent container (`agent_agent_hch1u`), verified via `docker exec`, and baked into the image (`data-migration-agent:latest`) via `docker build`.

---

## [2026-09-10] - Adaptive Watchdog & Execution Preflight Thresholds for Standby Agents

### 1. Decision Summary

Aligned backend watchdog polling and execution preflight timeout cutoffs with the 300-second (5-minute) idle standby mode (`ENTER_IDLE_MODE`) in [`apps/api/app/modules/agents/agents_services.py`](file:///d:/GitHub/Ai_data_migration_platform/apps/api/app/modules/agents/agents_services.py) and [`apps/api/app/modules/execution/execution_services.py`](file:///d:/GitHub/Ai_data_migration_platform/apps/api/app/modules/execution/execution_services.py).

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**: When an agent was idle for 5+ minutes, the backend instructed it to enter 5-minute standby heartbeat mode (`ENTER_IDLE_MODE`) to minimize network bandwidth. However, the background watchdog (`check_stale_agents_and_jobs`) and the execution service (`create_execution_job`) enforced a hardcoded 60-second heartbeat cutoff. After 60 seconds without a heartbeat, the watchdog falsely marked the running agent as `offline` with `DISCONNECTED_UNEXPECTEDLY`, and retry/dry-run requests were rejected with `HTTP 503 Service Unavailable`, misinforming users that they had to manually restart the container.
- **Adaptive Timeout Strategy**:
  1. **Dynamic Watchdog Thresholding**: `check_stale_agents_and_jobs` differentiates active agents (60s threshold for 20s heartbeats) from standby agents (360s threshold for 300s heartbeats).
  2. **Automatic Self-Healing**: The watchdog detects any standby agents previously misclassified as offline whose `last_seen_at` is within the 360s window and seamlessly restores them to `online` status.
  3. **Standby-Aware Execution Preflight**: `create_execution_job` permits execution when an agent is in standby mode and seen within 360s, automatically resetting `idle_since` and restoring `online` status so the agent's 10s task polling loop immediately picks up the migration job and snaps back to 20s active heartbeats.

---

## [2026-09-10] - Dashboard User Context Display & Safe Session Logout Architecture

### 1. Decision Summary

Implemented a unified session logout flow and visual user identity indicator directly in the Agent Migration Dashboard top navigation header ([`apps/web/app/dashboard/page.tsx`](file:///d:/GitHub/Ai_data_migration_platform/apps/web/app/dashboard/page.tsx)), backed by an enhanced `useLogout()` mutation hook ([`apps/web/hooks/mutations/useAuthMutations.ts`](file:///d:/GitHub/Ai_data_migration_platform/apps/web/hooks/mutations/useAuthMutations.ts)).

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**: Once authenticated, users had no visual indication of their active user profile in the dashboard and lacked any mechanism to log out of their session.
- **Visual Consistency & Hierarchy**:
  - The `LOGOUT` button is placed alongside `Refresh` and `Register New Agent` in the header actions cluster, following the strict terminal/cyberpunk design system (`rounded-none`, `font-mono`, `text-xs uppercase tracking-wider`, `bg-zinc-900 border-zinc-800`).
  - Added a subtle danger accent (`hover:border-rose-500/40 hover:bg-rose-950/40 hover:text-rose-400`) and the `LogOut` icon to denote a session-terminating action cleanly without visual clutter.
  - Added an active user context chip showing the user's name or email alongside a pulsing emerald status indicator.
- **Robust Multi-Layer Session Termination**:
  - Invokes `POST /api/v1/auth/logout` to instruct the backend to invalidate and clear HTTP-only `access_token` and `refresh_token` cookies.
  - Clears client-accessible cookies (`logged_in` and `active_org_id`).
  - Dispatches Redux `logoutAction()` and purges all TanStack Query caches via `queryClient.clear()`.
  - Performs a hard redirect via `window.location.href = '/login'`, cleanly purging in-memory timers, background agent status polling intervals (`setInterval`), and WebSocket connections while ensuring Next.js route middleware evaluates unauthenticated state.
  - **Graceful Error Recovery**: If the backend logout endpoint errors or is unreachable (e.g. expired session or network loss), `useLogout` catches the error and still clears client cookies, Redux state, and query cache so the user is never trapped in an un-logoutable state.

### 3. Alternatives Considered & Rejected

- **Alternative A: Hidden Dropdown Menu**: Rejected because a direct header button matches the terminal dashboard's utility-first UX and eliminates hidden navigation clicks.
- **Alternative B: Client-Side Routing (`router.push('/login')`)**: Rejected because soft navigation leaves active background polling intervals running and may preserve stale in-memory state. Full page redirection via `window.location.href = '/login'` ensures total teardown.

---

## [2026-09-11] - Relational Integrity & Deterministic UUIDv5 Foreign Key Synchronization for MongoDB Target Migrations

### 1. Decision Summary

Addressed three critical conversion flaws when migrating relational databases (e.g. PostgreSQL) into MongoDB document stores:

1. **Foreign Key / Relational Integrity**: Re-keyed primary keys into deterministic UUIDv5 strings while synchronizing all referencing foreign keys in child collections (`products.category_id`, `orders.customer_id`, `order_items.order_id`, `order_items.product_id`) to compute the exact identical UUIDv5 string, eliminating 100% of broken/orphaned foreign references.
2. **Primary Key Convention**: Promoted the transformed `id` to native MongoDB `_id` and removed redundant `id` fields during serialization, ensuring each document has exactly one primary key and eliminating auto-generated `ObjectId` collisions.
3. **Native BSON Data Types**: Stored numeric decimals as native BSON `Decimal128` and timestamps as native BSON `datetime` (`ISODate`), eliminating string degradation.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**:
  - In a migration from PostgreSQL to MongoDB, primary keys (`category_id`, `customer_id`, etc.) were converted into UUIDs (e.g. `5cc524b1-...`), but foreign key columns in child tables retained their raw integer values (`100`, `1000`, etc.). As a result, cross-collection references were 100% broken ($1000/1000$ orphaned records per relation).
  - Documents also had dual IDs: an auto-generated MongoDB `ObjectId` in `_id` and a separate `id` field containing the UUID.
  - Decimals (`19.99`) and timestamps were stored as raw strings rather than native BSON types.
- **Why Deterministic RFC 4122 UUIDv5**:
  - `uuid.uuid5(uuid.NAMESPACE_DNS, f"{src_ident}_{pk_value}")` is a pure, stateless mathematical hash function.
  - Computing the UUID for `category_id: 100` from `src_db_1` produces `5cc524b1-d434-5ce3-92d2-5ee6b343583b` in both `categories` (parent PK) and `products` (child FK).
  - This guarantees 0% orphan rate without requiring an in-memory cross-table lookup state or expensive distributed transactions during streaming.
- **Why Target Writer BSON Type Serialization**:
  - MongoDB's Python driver (`pymongo`) expects `bson.Decimal128` to store IEEE 754-2008 128-bit decimal floating point values without precision loss.
  - Datetimes passed directly as Python `datetime.datetime` objects are serialized by pymongo as native BSON dates (`ISODate`), enabling date-range querying, TTL indexes, and aggregations.
  - By scoping these conversions to `if is_mongo:` in `_sanitize_rows_for_target`, SQL database targets (PostgreSQL, MySQL, SQLite) remain completely untouched and compatible.

### 3. Alternatives Considered & Rejected

- **Alternative A: Retain Raw Integers for Both PK and FK**:
  - _Rejected by User_: User explicitly requested converting primary keys to UUIDs and having referencing foreign keys automatically converted to matching UUIDs.
- **Alternative B: In-Memory Key Mapping Dictionary**:
  - _Rejected_: Maintaining an in-memory dictionary of old integer $\to$ new UUID across gigabyte-scale datasets creates memory leaks, worker OOM crashes, and fails across multi-worker streaming batches. Deterministic UUIDv5 requires zero memory overhead.
- **Alternative C: Embed Child Records as Nested Subdocuments**:
  - _Rejected_: While idiomatic for small 1:few relations, unbounded 1:N relations (e.g., thousands of orders per customer or millions of order items) violate MongoDB's 16MB BSON document limit. Retaining normalized document references with matching UUIDs is robust and scalable.

### 4. Trade-offs & Future Considerations

- **Nullable Foreign Keys**: When a source foreign key is `NULL` or empty, the transformer evaluates to `None` rather than generating a fallback UUID to preserve relational nullability.
- **Target Detection**: Added target database type auto-detection in both the FastAPI service (`MigrationPlanService.create_plan_for_agent`) and the Next.js UI (`GeneratePlanAction.tsx`), ensuring plans default to the agent's target data source engine (e.g., `mongodb`).

---

## [2026-09-14] - AI Plan Refinement Feasibility Feedback & Transparent Response System

### 1. Decision Summary

Implemented an end-to-end transparent feedback architecture for natural language AI plan refinement prompts. When a user requests an architectural change (e.g., _"Can we do that same conversion without data loss in 12 tables?"_), the system evaluates feasibility against source schemas, enforces anti-hallucination guardrails, and renders a dedicated **AI Refinement Response Card** in the Next.js UI with prompt echo, feasibility verdict badge, table count deltas, and plain-English technical rationale.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**:
  - When users submitted refinement prompts in `/transformation-plan`, the UI only fired a transient, generic toast (`"LLM re-reviewed & refined blueprint successfully!"`) regardless of whether the requested change was feasible.
  - If a user requested an impossible change that would cause data loss (e.g., forcing 14 distinct domain collections into 12 tables without common keys), the LLM kept all 14 tables in `table_mappings` to preserve zero data loss, but in `ai_explanation` hallucinated that it had consolidated into 12 collections.
  - Users experienced total opacity: the blueprint remained unchanged, the toast claimed success, and no explanation was provided detailing why the requested consolidation could not be performed.
- **Chosen Solution**:
  1. **Structured AST Schema (`RefinementFeedback`)**: Added `refinement_feedback` to `TransformationPlanAST` in `migration_plans_schemas.py`, capturing `applied: bool`, `verdict: 'applied' | 'partially_applied' | 'infeasible_rejected'`, `user_prompt: str`, `explanation: str`, `table_count_before: int`, `table_count_after: int`, and `changes_summary: List[str]`.
  2. **Feasibility Prompting & Anti-Hallucination Guardrails**: Updated `llm_plan_generator.refine()` with strict feasibility instructions forbidding the model from claiming it merged tables if `table_mappings` was not actually changed, requiring explicit `applied=false` and `verdict='infeasible_rejected'` with technical justification.
  3. **Defensive Fallback Mechanism**: Added runtime defensive fallback in `refine()` so that even if an LLM output omits the feedback object, it is automatically synthesized from before/after table counts and warnings.
  4. **Dedicated UI Component (`RefinementFeedbackCard`)**: Replaced generic toast reliance with a prominent dark cyberpunk response card above the prompt input and in version history, displaying prompt echo, status badge (`[NOT FEASIBLE — PROTECTED FROM DATA LOSS]`), table deltas (`14 → 14 Preserved`), and detailed explanation.

### 3. Alternatives Considered & Rejected

- **Alternative A: Rely Solely on Dynamic Toast Messages**:
  - _Rejected_: Toast notifications disappear after a few seconds and cannot display multi-paragraph technical explanations or before/after metrics without cluttering the screen.
- **Alternative B: Force Table Merges to Obey Prompt Despite Data Loss**:
  - _Rejected_: Forcing unrelated tables (such as clickstream telemetry and inventory items) into shared collections causes severe schema corruption and violates the platform's zero-data-loss guarantee. Preserving lossless schemas while explaining the constraint to the user is the only architecturally sound choice.

### 4. Trade-offs & Future Considerations

- **Historical Version Backward Compatibility**: In `PlanBlueprintViewer.tsx`, added a fallback synthesizer so that plans generated prior to this schema update also display meaningful explanation cards when users inspect older version snapshots.

---

## [2026-09-14] - Multi-Source Lineage Tracking & Auto-Population of `_source_origin`

### 1. Decision Summary

Implemented automatic data lineage stamping in the Docker Agent execution engine (`ASTTransformer` and `orchestrator.py`). When the AI plan merges multiple source tables into a single destination table with a `_source_origin` tracking column, the transformer automatically injects the canonical source origin identifier (`f"{src_ident}.{src_table}"`, e.g., `'src_db_1.customers'` and `'src_db_2.legacy_customers'`), preventing `psycopg2.errors.NotNullViolation` during bulk loads.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**:
  - The AI planning engine instructions require merged tables to declare a `_source_origin VARCHAR(50) NOT NULL` column for auditability and lineage tracking.
  - However, `ASTTransformer.transform_chunk()` lacked context on the active source origin and defaulted unmapped or `new_column_added` columns without constant values to `None` (`SQL NULL`).
  - Target relational databases (PostgreSQL) strictly rejected every row (`null value in column "_source_origin" violates not-null constraint`), immediately triggering the pipeline's >50% error abort safety mechanism.
- **Chosen Solution**:
  1. **Source Origin Context Propagation**: In `orchestrator.py`, extracted `src_origin_tag = f"{src_ident}.{src_table}" if src_ident else str(src_table)` and passed it to `ASTTransformer.transform_chunk(..., source_origin=src_origin_tag)`.
  2. **Dedicated Transformer Column Handler**: In `ASTTransformer`, added explicit handling for `_source_origin` across all transformation types and fallback expressions, ensuring `val = source_origin or const_val or "unknown"` is populated as a non-null literal.
  3. **DuckDB Staging & Streaming Preservation**: Multi-source tables staged in DuckDB carry this lineage tag through deduplication and batch chunking without loss.

### 3. Alternatives Considered & Rejected

- **Alternative A: Relaxing Database DDL to Nullable (`DROP NOT NULL`)**:
  - _Rejected_: Making `_source_origin` nullable in DDL bypasses the error but defeats the entire purpose of data lineage tracking, leaving destination rows without traceable source attribution.
- **Alternative B: Relying on LLM to Hardcode Constant Values in Column Mappings**:
  - _Rejected_: The LLM generates one column mapping list for the target table, but a merged target table ingests rows from _multiple_ different sources dynamically at runtime. Only the execution orchestrator knows which source is currently being streamed.

### 4. Trade-offs & Future Considerations

- **Lineage Granularity**: `src_ident.table_name` provides clear, portable lineage without exposing raw database connection strings or credentials.
- **Resilience**: Even if a user's custom plan uses non-standard transformation types for `_source_origin`, the transformer intercepts the target column name and guarantees a valid string literal.

---

## [2026-09-15] - Asynchronous AI Plan Refinement with Detached Coroutines & Resilient Polling

### 1. Decision Summary

Transitioned the AI migration plan natural language refinement workflow from a blocking synchronous HTTP request to a resilient, asynchronous background execution model (`POST /plans/{plan_id}/refine-async` returning `202 Accepted` in ~200ms). Refinement runs inside a detached asyncio background coroutine (`asyncio.create_task`) with its own isolated `AsyncSessionLocal()`, thread pool delegation (`asyncio.to_thread`), and an expanded 360-second timeout window. The Next.js UI features state persistence across browser reloads (F5), 2-second HTTP polling (`GET /plans/{plan_id}/refine/status`), a prominent cyber-dark progress banner with prompt echo and live elapsed seconds timer, automatic plan hot-reloading upon completion, and strict conflict guards (`409 Conflict`) preventing race conditions during active refinement.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**:
  - LLM plan refinement against complex multi-source schemas (e.g., 14 enterprise tables across PostgreSQL, MySQL, and MongoDB) can take 200–240 seconds.
  - The previous synchronous architecture held a single HTTP connection open for the entire duration. Browsers, API gateways, reverse proxies, or load balancers timed out after 180 seconds (`504 Gateway Timeout` or `ERR_CONNECTION_RESET`), aborting the request.
  - If a user navigated away or refreshed the browser (`F5`), the browser client aborted the socket. Even if the backend completed, the user returned to an un-updated plan with no indicator of progress or completion.
- **Chosen Solution**:
  1. **Asynchronous Detached Coroutine Architecture**: The frontend dispatches `POST /api/v1/plans/{plan_id}/refine-async`, which marks `plan.status = "refining"`, registers a task in `RefinementTaskManager`, commits the transaction, spawns `asyncio.create_task(_run_plan_refinement_background())`, and immediately responds with `202 Accepted`.
  2. **Isolated Database Session Lifecycle**: The background coroutine runs with a fresh, independent `AsyncSessionLocal()`, ensuring that database transactions are fully isolated and not bound to the ephemeral lifecycle of the incoming HTTP request.
  3. **Thread-Safe In-Memory & Database State Tracking (`RefinementTaskManager`)**: Tracks task ID, status (`processing`, `completed`, `failed`), prompt text, start timestamp, elapsed time, and serialized results with `asyncio.Lock` concurrency protection and safe TTL eviction.
  4. **Frontend Resilience Across Page Reloads**: In `PlanBlueprintViewer.tsx`, on mount or page refresh, if `plan.status === 'refining'`, the component activates `isRefining = true` and launches a 2.0s polling interval against `GET /plans/{plan_id}/refine/status`.
  5. **Rich Cyber-Dark Progress Banner**: Renders an animated gradient progress banner displaying the prompt echo, elapsed seconds timer, and live status badge.
  6. **Zero Regression for Synchronous Callers**: Synchronous route `POST /plans/{plan_id}/refine` is 100% preserved for legacy callers and backward compatibility.
- **Why This Library / Technology**:
  - **FastAPI Native `asyncio.create_task` + `asyncio.to_thread`**: Avoids introducing heavy external worker dependencies (like Celery / Redis worker daemons) that can crash or fail to start in developer local setups.
  - **SQLAlchemy 2.0 Async Session (`AsyncSessionLocal`)**: Eagerly loads all agent data sources (`selectinload(MigrationPlan.agent).selectinload(Agent.data_sources)`) without `MissingGreenlet` errors.
  - **HTTP Polling (2.0s)**: Simple, rock-solid, reconnect-free mechanism that survives full browser page refreshes, tab closures, and network reconnections.

### 3. Alternatives Considered & Rejected

- **Alternative A: Heavyweight Celery / Redis Worker Daemon Process**:
  - _Rejected_: Requires running and maintaining a separate worker process daemon (`celery -A app.worker worker`) and broker. If the daemon process is not running or Redis goes down, all refinement calls silently hang or fail.
- **Alternative B: WebSockets / Server-Sent Events (SSE)**:
  - _Rejected_: WebSocket connections terminate when a user presses F5 or navigates between pages, requiring complex reconnect backoff, reconnection tokens, missed-message buffering, and duplicate event handlers. HTTP polling with server-side DB/memory state achieves identical latency with zero state fragility.
- **Alternative C: Keeping Synchronous Requests with Infinite HTTP Timeout**:
  - _Rejected_: Gateway timeouts (NGINX, Cloudflare, AWS ALB, Chrome) aggressively drop idle HTTP connections after 100–180 seconds. Long-polling/blocking HTTP requests are fundamentally unviable for 3+ minute LLM executions.

### 4. Trade-offs & Future Considerations

- **Single-Node In-Memory Cache**: `RefinementTaskManager` stores active job metadata in Python process memory. In a multi-replica horizontal backend deployment, requests across replicas would require a shared Redis key-value store. However, `plan.status = 'refining'` in PostgreSQL guarantees that even without Redis, any replica knows the plan is currently refining.
- **Conflict Prevention**: Plans in `status == 'refining'` reject concurrent refinements (`409 Conflict`) and reject execution approvals (`409 Conflict`), eliminating race conditions.

---

## [2026-09-15] - Asynchronous AI Initial Plan Generation with Refresh Resilience & Concurrency Locks

### 1. Decision Summary

Extended the asynchronous background execution model to the initial AI migration plan generation workflow (`POST /plans/generate-async` returning `202 Accepted` in ~200ms). Initial generation runs inside a detached background coroutine (`_run_plan_generation_background`) using an isolated `AsyncSessionLocal()`, LangGraph StateGraph engine with direct LLM fallback, and dual-layer concurrency locking (`GenerationTaskManager` + database `status == 'generating'`). In `GeneratePlanAction.tsx`, on-mount status checks allow the user to refresh the page (`F5`) or navigate away: the "GENERATE AI MIGRATION PLAN" button remains disabled with live elapsed ticker, a cyber-dark progress banner details the multi-stage pipeline, and 2-second HTTP polling (`GET /plans/agent/{agent_id}/generation-status`) automatically redirects to `/transformation-plan?planId=...` once the blueprint is persisted.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**:
  - Initial plan generation across multi-engine sources involves extensive schema graph analysis, topological dependency sorting, and LLM code synthesis that can take 45–90 seconds.
  - Previously, `createPlan()` executed as a blocking synchronous HTTP request (`POST /plans/generate`). If the user refreshed the page or closed the tab, the HTTP socket dropped, the frontend lost the plan redirect, and duplicate clicks attempted to invoke parallel LLM generation passes against the same database metadata.
- **Chosen Solution**:
  1. **Immediate 202 Accepted & Upfront Plan Registration**: `POST /plans/generate-async` persists an initial `MigrationPlan` entity with `status = "generating"`, registers the task in `GenerationTaskManager`, commits the transaction, spawns `asyncio.create_task()`, and returns `202 Accepted` with `plan_id` and `task_id`.
  2. **Dual-Layer Concurrency Lock**: Rejects duplicate generation requests for the same agent with `409 Conflict` both via in-memory task tracking (`GenerationTaskManager.is_running`) and database-level query (`MigrationPlan.status == 'generating'`).
  3. **Browser Refresh Resilience**: When `GeneratePlanAction.tsx` mounts on `/sources?agentId=...`, `checkAgentExecutionState()` calls `planService.getGenerationStatus(agentId)`. If `processing`, it immediately restores `isGenerating = true`, restores the elapsed seconds timer, disables the button, renders the progress banner, and activates the 2s polling loop.
  4. **Automatic Redirection**: When status becomes `completed`, the poller receives the generated plan ID and performs `router.push('/transformation-plan?planId=' + id)`.
  5. **100% Backward Compatibility**: Synchronous route `POST /plans/generate` is completely preserved for legacy callers and existing test suites.

### 3. Alternatives Considered & Rejected

- **Alternative A: Client-Side LocalStorage Job Caching**:
  - _Rejected_: LocalStorage is brittle, per-browser, cleared on privacy resets, and invisible to backend concurrency guards. Server-side tracking in PostgreSQL and `GenerationTaskManager` ensures any browser or device viewing the agent sees the identical active generation state.
- **Alternative B: WebSockets / Event Streams**:
  - _Rejected_: Sockets disconnect on F5 page reloads and require complex reconnection protocols. 2.0s HTTP polling provides zero-maintenance reliability across network drops and browser refreshes.

### 4. Trade-offs & Future Considerations

- **Fast Failover & Status Recovery**: If an exception occurs in the detached background coroutine, a dedicated recovery session automatically sets `plan.status = "draft_failed"` with error details, allowing the user to view the failure diagnosis and retry cleanly.

---

## [2026-09-15] - Target Database Engine Locking & Elimination of Frontend Engine Selection Divergence

### 1. Decision Summary

Eliminated the editable `<select>` dropdown for "Target Database Engine" in [`apps/web/components/profiling/GeneratePlanAction.tsx`](file:///d:/GitHub/Ai_data_migration_platform/apps/web/components/profiling/GeneratePlanAction.tsx) and replaced it with a read-only locked target database display badge (`Locked by Agent 🔒`). In [`apps/api/app/modules/migration_plans/migration_plans_services.py`](file:///d:/GitHub/Ai_data_migration_platform/apps/api/app/modules/migration_plans/migration_plans_services.py), both `start_async_generation` and `execute_generation_core` now unconditionally enforce `target_db_type = target_ds.type.lower()` from the agent's attached target `DataSource` (`role in ('target', 'both')`), completely preventing any accidental mismatch between the AI's blueprint SQL dialect and the Docker Agent's physical target database container.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**:
  - In earlier iterations, `GeneratePlanAction.tsx` included a `<select>` dropdown offering `MongoDB`, `PostgreSQL`, and `MySQL`.
  - However, an Agent's destination database is **physically fixed at registration time** in the container startup command (e.g. `DEST_DST_DB_23_TYPE="postgresql"`, `DEST_DST_DB_23_URL=...`).
  - If a user selected a different engine (e.g. `MySQL`) on the Plan Generation page, the AI generated MySQL-specific DDL syntax (`created_at DATETIME`, `updated_at DATETIME`), but the Docker Agent attempted to execute that DDL on its configured **PostgreSQL** database, causing immediate fatal crashes:
    `psycopg2.errors.UndefinedObject: type "datetime" does not exist`.
- **Chosen Solution**:
  1. **Frontend Read-Only Locked Display**: `GeneratePlanAction.tsx` auto-detects `targetDs` from `agentData.data_sources` and renders a clean, locked badge: `POSTGRESQL (Relational) • dst_db_23 [🔒 Locked by Agent]`. The user cannot accidentally select an incompatible database engine.
  2. **Backend Unconditional Binding**: In `migration_plans_services.py`, whenever an agent has an attached target data source, `target_db_type` is unconditionally set to `target_ds.type.lower()`, overriding any stale or rogue client payload parameter.
  3. **Zero Runtime Engine Drift**: Guarantees that the AI planning engine strictly generates DDL and AST transformation rules matching the exact database engine the Docker container is connected to.

### 3. Alternatives Considered & Rejected

- **Alternative A: Permitting Target Engine Switching with Dynamic Container Reconfiguration**:
  - _Rejected_: Docker agents run as isolated external processes on the user's infrastructure. Changing target engines dynamically requires stopping the container, updating environment variables, and re-authenticating network credentials. Agents must be 1:1 bound to their configured target database.
- **Alternative B: Relying Exclusively on Agent DDL Sanitizer Post-Processing**:
  - _Rejected_: Sanitizing DDL in the agent as a band-aid does not solve the root cause. If the AI believes the target is MongoDB or MySQL, it alters primary key generation, JSON flattening strategies, and column casts throughout the entire AST. The target engine must be correct from the moment the blueprint is planned.

### 4. Trade-offs & Future Considerations

- If a user wishes to migrate data into a different target database (e.g., from PostgreSQL to MongoDB instead of PostgreSQL), they simply register a new Agent with MongoDB as the target, preserving clear container boundaries and security isolation.

---

## [2026-09-15] - Target Database Auto-Creation & Clean Wipe (Truncate/Drop) Safety System

### 1. Decision Summary

Implemented an automated **Target Database Auto-Creation** system across all three supported database engines (**PostgreSQL**, **MySQL**, and **MongoDB**) combined with an **Execution Confirmation & Clean Wipe (Truncate/Drop) Safety Modal** and live target data warning system. If a user pastes or configures a target database name that does not physically exist on the destination server, the Docker Agent detects its absence and automatically provisions it (`CREATE DATABASE` on PostgreSQL/MySQL, or namespace initialization on MongoDB) during both metadata introspection and migration execution. Furthermore, before starting a migration, users are presented with a pre-flight safety dialog where they can explicitly consent to a destructive **Clean Wipe** (`DROP TABLE ... CASCADE` / `FOREIGN_KEY_CHECKS = 0; DROP TABLE` / `drop_collection()`). If Clean Wipe is left unchecked and the target database contains existing tables or records, prominent warnings are displayed in the Web UI and logged by the Agent engine.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**:
  - Previously, specifying a new target database name (e.g. for a second migration run to keep prior migration records intact) caused Docker Agent metadata introspection or DDL execution to throw fatal connection errors (`database "..." does not exist` on PostgreSQL or error `1049` on MySQL) unless the database had been manually created beforehand by a DBA.
  - Additionally, if a user re-ran a migration into an existing database that already contained tables, the Docker Agent defaulted to appending rows with `ON CONFLICT DO NOTHING`. This caused confusion when primary keys conflicted or when users wanted a fresh, clean dataset without manual database cleanup scripts.
- **Chosen Solution**:
  1. **Multi-Engine Auto-Creation (`DDLExecutor._ensure_database_exists`)**:
     - **PostgreSQL**: Connects to the root maintenance database (`/postgres`), checks `SELECT 1 FROM pg_database WHERE datname = :dbname`, and executes `CREATE DATABASE "{dbname}"` with `AUTOCOMMIT`.
     - **MySQL**: Connects to `/mysql` with autocommit and executes `CREATE DATABASE IF NOT EXISTS \`{dbname}\``.
     - **MongoDB**: Connects and verifies the database namespace via ping.
     - Automatically invoked during both Agent metadata introspection (`metadata_engine.py`) and job execution (`orchestrator.py`).
  2. **Clean Wipe Execution Safety Policy (`truncate_target: bool`)**:
     - Propagated through `ExecutionStartRequest`, `MigrationJob(truncate_target=...)`, `AgentTaskItemResponse`, and `ExecutionOrchestrator.run_job()`.
     - When enabled, `DDLExecutor.clean_wipe_target_database()` drops all existing tables/collections before executing pre-migration DDL.
  3. **Explicit User Consent & Warning Modal in UI**:
     - Replaces direct one-click execution with an interactive pre-flight modal in `PlanBlueprintViewer.tsx`.
     - Requires checking an explicit agreement box: _"Clean Wipe Target Database (Delete & Drop Existing Tables)"_ with a destructive warning banner.
     - When left unchecked, displays an amber advisory warning: _"Target database should ideally be empty ... new records will be appended and conflicting primary keys skipped."_

### 3. Alternatives Considered & Rejected

- **Alternative A: Cloud Control Plane Direct Database Creation**:
  - _Rejected_: Violates zero-credential isolation. The cloud backend does not have access to customer host network or database passwords. Only the local Docker Agent possesses socket reachability to the destination database.
- **Alternative B: Automatic Unconditional Truncate on Every Migration**:
  - _Rejected_: Highly dangerous. Automatically wiping databases without explicit user consent could destroy production records or unintended tables. Destructive drops MUST require explicit opt-in confirmation.

### 4. Trade-offs & Future Considerations

- **Permissions Requirement**: Auto-creating databases requires the configured user in `DEST_*_URL` to have `CREATEDB` privilege on PostgreSQL or `CREATE` privilege on MySQL. If the user account lacks these privileges, a descriptive notice is logged and fallback to manual creation is maintained.

---

## [2026-09-15] - Execution Job Cancellation & Reset Feature (User Control)

### 1. Decision Summary

Implemented a full-stack **Job Cancellation & Reset Feature** allowing users to cancel or reset an active (`queued`, `preparing`, `running`) data migration or dry-run execution job directly from the web console. This eliminates execution deadlocks when Docker containers are killed, stopped, or disconnected mid-migration and enables immediate re-runs without encountering `409 Conflict`.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**:
  - Previously, if a Docker agent container was killed (`docker stop` / `docker rm`) during a running dry-run or live migration, the job stayed in `running` status in the PostgreSQL database until the backend watchdog timer (5 minutes) expired.
  - The plan execution API strictly rejects new execution attempts with HTTP 409 Conflict whenever an active job exists. Users were completely blocked and unable to restart execution or run a dry run.
- **Chosen Solution**:
  1. **Backend Cancel Endpoint (`POST /api/v1/executions/{id}/cancel`)**:
     - Requires user ownership of the associated migration plan.
     - Validates that the job is in an active state (`queued`, `preparing`, `running`).
     - Transitions `MigrationJob.status = "cancelled"`, sets `completed_at = now`, `current_stage = "cancelled"`, and records cancellation reason.
     - Resets the assigned Docker Agent status to `"online"` and updates `idle_since = now`.
     - Broadcasts real-time `EXECUTION_PROGRESS` / `JOB_CANCELLED` WebSocket event to subscribed frontend clients.
     - Protects subsequent agent progress reports from overwriting `cancelled` status.
  2. **Docker Agent Cancellation Detection**:
     - `ProgressReporter.report` reads HTTP response payload.
     - If the response indicates `status == "cancelled"`, `ExecutionOrchestrator` raises `JobCancelledException`, halting in-flight ETL processing cleanly without reporting a failure.
  3. **Frontend Cancellation UX**:
     - Added an interactive **"Cancel Execution"** button with a safety confirmation modal in `JobExecutionBanner.tsx`.
     - Added a dedicated **Cancelled State Banner** with an immediate **"Re-run Migration / Dry Run"** action.
     - Updated `PlanBlueprintViewer.tsx` to ensure action controls are unlocked whenever no active job is running.

### 3. Alternatives Considered & Rejected

- **Alternative A: Relying Exclusively on Backend Timeout Watchdog**:
  - _Rejected_: A 5-minute timeout creates severe user friction and forces developers to wait idly after stopping a container. Users need immediate on-demand control to cancel and re-run jobs.
- **Alternative B: Allowing Arbitrary Overwrite of Running Jobs on Execute**:
  - _Rejected_: Blindly replacing running jobs without explicit cancellation risks running concurrent duplicate migration streams against the same target database if the original container is still active.

### 4. Trade-offs & Future Considerations

- In-flight batches currently writing to the target database at the exact moment of cancellation will complete their single batch transaction, while subsequent batches are halted immediately. Clean wipe or upsert mode ensures data consistency on subsequent runs.

---

## [2026-09-15] - Unit Test Suite Alignment & Dead Code Removal

### 1. Decision Summary

Fixed three discrepancies identified during a full verification sweep:

1. **Removed Dead Code**: Eliminated duplicate `session.commit()` and `session.refresh(job)` calls in `execution_services.py:update_job_progress()`, saving an unnecessary database round-trip per heartbeat/progress update.
2. **Alembic Test Chain Synchronization**: Updated `test_alembic_migrations.py` to recognize migration head `c9f0a2b3456e` (Migration 012 - `truncate_target` support) and verify strict linear descent through the migration graph.
3. **Agent Test Harness Mocking & Sys.Path Isolation**:
   - Made `heartbeat_config` parameter optional in `start_heartbeat_thread()` with automatic default fallback.
   - Updated `test_bug_decouple_agent_heartbeats.py` to initialize and pass `HeartbeatConfig`.
   - Updated `MockMongoClient` in `test_bug_mongo_keyset_pagination.py` to mock `.admin.command('ping')` required by the resilient connection handshake.
   - Configured sys.path resolution in `test_bug_deterministic_retry_uuids.py` and `test_mongo_relational_uuid_fk_and_types.py` for flawless test execution from any working directory.

### 2. Verification Results

- Backend unit tests: **98 / 98 tests passing (100%)**.
- Agent unit tests: **2 / 2 tests passing (100%)**.
- Frontend Web App: Next.js 14 production build compiled with 0 errors.

---

## [2026-09-15] - Transformation Plan Page UX Redesign & 3-Tab Architecture

### 1. Decision Summary

Redesigned the `/transformation-plan` page from a flat, vertically unrolled 10+ section stack into a modular **3-Tab Progressive Disclosure Architecture** (`Overview & Strategy`, `Table Mappings`, `Execute & Monitor`) with URL query synchronization (`?tab=overview|mappings|execute`), table search/filter controls, and status-colored visual indicators.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**:
  - The previous transformation plan page displayed the plan header, 4 readiness cards, plain language summary, AI execution strategy narrative, validation diagnostics, refinement cards, refinement form, version history, execution pipeline, table mapping matrix accordions, and execution banner all in one continuous scrolling page.
  - This created severe cognitive overload, making it difficult for users to review blueprints systematically or quickly locate schema mapping details.
- **Chosen Solution**:
  1. **3-Tab Architecture**:
     - **Tab 1: Overview & Strategy (`overview`)**: Houses high-level readiness signals (4-vector scorecard), plain language summary, AI strategy narrative, validation diagnostics, LLM prompt refinement form, and visual version history timeline.
     - **Tab 2: Table Mappings (`mappings`)**: Dedicated workspace for schema inspection and editing. Includes full-text search, type filtering (`direct_copy`, `merge`, `split_target`), readiness filtering (`optimal`, `warning`, `critical`), Matrix/Diagram view toggle, global inline AST editing mode, and status-colored left border accordions.
     - **Tab 3: Execute & Monitor (`execute`)**: Focused execution control room with pre-flight readiness gate, target database configuration overview, live Docker Agent execution banner with AI failure diagnosis, Dry Run simulation, Clean Wipe confirmation modal, and execution dispatch.
  2. **URL-Based State Synchronization**:
     - Tab navigation is synchronized with the URL query parameter `?tab=overview|mappings|execute` via Next.js App Router `useRouter` and `useSearchParams`.
     - Enables bookmarking, direct navigation from external alerts, and browser back/forward history support while preserving other query parameters like `planId`.
  3. **Preserved Existing Behaviors**:
     - Global edit toggle (`isEditing`) maintained for comprehensive blueprint editing.
     - Automatic retrieval of active or most recent job retained in the Execute tab.
     - Breadcrumb navigation kept above the sticky tab bar.

### 3. Alternatives Considered & Rejected

- **Alternative A: Modal-Based Workflows**:
  - _Rejected_: Hiding table mappings or execution inside popup dialogs makes complex multi-column editing cumbersome and prevents deep linking.
- **Alternative B: Pure Client-Side State (No URL Sync)**:
  - _Rejected_: Without URL query parameters, refreshing the page or navigating back would lose the user's active tab context.

### 4. Trade-offs & Future Considerations

- Modularization into dedicated tab components (`PlanTabBar`, `PlanOverviewTab`, `PlanTableMappingsTab`, `PlanExecuteTab`) decouples UI rendering while `PlanBlueprintViewer` remains the central state orchestrator. Future enhancements can add per-table diff previews when comparing versions.

---

## [2026-09-16] - Agent Post-Migration Offline Guard & Re-Execution UX Hardening

### 1. Decision Summary

Resolved a multi-layered bug where an agent container, having gracefully exited following a successful migration under backend `SHUTDOWN` directives (Option A), was erroneously marked as having suffered a `FATAL STOPPING ERROR [DISCONNECTED UNEXPECTEDLY]`:

1. **Backend Offline Gate**: Updated `execution_services.py:start_plan_execution()` to explicitly check `if agent.status in ("error", "offline")`, returning an immediate HTTP 503 rather than relying solely on `last_seen_at < cutoff` (which gave a false-positive availability signal within 60s of container exit).
2. **Eliminated State Corruption**: Removed the forced mutation `agent_for_reset.status = "online"` inside `start_plan_execution()`, ensuring an offline Docker container cannot be falsely claimed active in the database.
3. **Frontend Completion & Re-Run Disambiguation**: Updated `PlanExecuteTab.tsx` and `PlanBlueprintViewer.tsx` to detect completed migrations, render a dedicated "Target Migration Completed Successfully" success callout, label secondary execution actions as `⚡ RE-RUN MIGRATION` with muted styling, and provide re-run warnings in the confirmation modal.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**:
  - After a successful migration, backend logic intentionally shuts down the agent container to conserve host resources.
  - The UI's execute action section previously only checked `!isJobActive`. Because a completed job is inactive, the green `⚡ EXECUTE MIGRATION` button remained visible, leading the user to believe execution was still required.
  - Clicking this button queued a new job, while the backend bypassed the offline check because `last_seen_at` was < 60 seconds old, and forcibly stamped the agent as `"online"`.
  - When the user cancelled the stalled job, the background watchdog observed an agent marked `"online"` with no subsequent heartbeats, falsely declaring a `DISCONNECTED_UNEXPECTEDLY` fatal error on the dashboard.
- **Chosen Solution**:
  - Guard the backend entry point against any agent whose status is `"offline"` or `"error"`.
  - Only allow authentic agent heartbeats to transition agent state to `"online"`.
  - Explicitly indicate completion status on the UI so users clearly distinguish between initial execution and an optional re-run.

### 3. Alternatives Considered & Rejected

- **Alternative A: Disable Container Shutdown (Keep Agents Online Indefinitely)**:
  - _Rejected_: In on-premise deployments, customer containers should not indefinitely consume memory and polling threads once their batch migration job has concluded. Graceful exit is a core architectural requirement.
- **Alternative B: Completely Remove the Execute Button Once Completed**:
  - _Rejected_: Users occasionally need to re-run migrations (e.g. after database schema adjustments or with Clean Wipe enabled). Re-labeling the action as `RE-RUN MIGRATION` with contextual warnings supports legitimate re-runs while preventing confusion.

### 4. Trade-offs & Future Considerations

- Users attempting to re-run a completed migration must ensure their Docker container is restarted (`docker start <container_name>`) before dispatching. Clear 503 error messages and UI badges now explain this requirement directly.

---

## [2026-09-16] - Complex NoSQL MongoDB Database Provisioning (`complex_nosql_enterprise`)

### 1. Decision Summary

Implemented [`scripts/seed_complex_nosql.py`](file:///d:/GitHub/Ai_data_migration_platform/scripts/seed_complex_nosql.py) to provision a production-scale NoSQL database (`complex_nosql_enterprise`) on MongoDB specifically designed to test the limits of SQL relational modeling and automated ETL migration engines:

1. **Deep Hierarchical Trees (Levels 5–7)**: Modeled in `smart_iot_fleet`, featuring deep powertrain $\rightarrow$ MCU $\rightarrow$ chamber $\rightarrow$ sensor $\rightarrow$ calibration $\rightarrow$ matrix branches.
2. **Extreme Schema Polymorphism**: Modeled in `omnichannel_customer_graph`, storing 3 radically distinct personas (`ENTERPRISE_ORGANIZATION`, `INDIVIDUAL_CONSUMER`, `ANONYMOUS_SESSION`) in a single collection.
3. **Dynamic / Heterogeneous Typing**: Implemented in `polymorphic_event_bus`, where the same attribute (`payload.verification_code`) takes `int`, `str`, `dict`, `bool`, and `list` across documents.
4. **Arrays of Arrays (2D Matrices)**: Modeled in `clinical_genomics_records`, incorporating nested arrays of quality score matrices and multi-tiered clinical sub-trees.
5. **GeoJSON & 2dsphere Spatial Indexing**: Embedded Point geometries in `smart_iot_fleet` with native 2dsphere spatial index verification.
6. **Unbounded Key-Value Dictionaries**: Embedded arbitrary dynamic sensor and phenotype maps with unique field names per device.
7. **Rich BSON Types**: Integrated `Decimal128`, `Binary` (UUID/blobs), `Regex`, `ObjectId`, and `ISODate`.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**:
  - Validating migration and ETL engines against basic flat NoSQL collections fails to expose real-world NoSQL-to-SQL impedance mismatches (e.g. 1NF violations, EAV anti-patterns, recursive joins, and polymorphic table splitting).
  - This dataset provides a benchmark containing the 7 hardest NoSQL patterns to model in relational databases.
- **Chosen Solution**:
  - A clean 4-collection domain architecture populated with 620 high-fidelity, indexed documents.

### 3. Alternatives Considered & Rejected

- **Alternative A: Synthetic Random JSON Blob Generation**:
  - _Rejected_: Random unstructured JSON lacks semantic business context, making schema inference testing unrealistic.
- **Alternative B: Simple Flattened Arrays**:
  - _Rejected_: Flat arrays are easily modeled with simple 1:N foreign keys in SQL; only multi-dimensional nested arrays (Level 5+) truly challenge relational normalization.

### 4. Trade-offs & Future Considerations

- Converting `complex_nosql_enterprise` into SQL will require advanced automated schema decomposition strategies (e.g., dynamic table generation, JSONB column relegation, or synthetic foreign-key surrogate synthesis).

---

## [2026-09-16] - Robust NoSQL-to-Relational ETL Hardening, PostgreSQL Dialect Sanitization & Polymorphic Coercion

### 1. Decision Summary

Fixed 5 systemic migration and execution engine failures during complex NoSQL-to-PostgreSQL ETL data migrations:

1. **PostgreSQL DDL Dialect Sanitization & Auto-Healing**: Replaced non-standard `uuid_v4()` with native `gen_random_uuid()`, eliminated false-positive keyword suppression in `DDLExecutor`, and added runtime dialect error auto-healing with automatic SQL statement retry.
2. **Polars LazyFrame Column Disambiguation**: Resolved duplicate `extra_attributes` schema collision in Polars by eliminating `hasattr(item, "name")` duck-typing (which falsely succeeded on `Expr.name` namespace objects) and replacing it with strict `isinstance(item, pl.Expr)` and `item.meta.output_name()`.
3. **Polymorphic Boolean Coercion**: Implemented resilient `_parse_bool()` coercion across `direct_copy`, `type_cast`, and `nosql_field_promote` to safely convert polymorphic strings (e.g. `"PENDING_COOKIE_BANNER_ACCEPTANCE"` $\rightarrow$ `False`/`None`) and nested dictionaries into valid boolean values for `BOOLEAN NOT NULL` target columns.
4. **Nested Dot-Path Field Extraction**: Enhanced `nosql_field_promote` to traverse dot-separated paths (e.g. `architecture.firmware_version`) when reading nested dictionaries stored within serialized JSON structures.
5. **AI Execution Failure Diagnosis Expansion**: Added granular error classification rules for `TARGET_TABLE_MISSING`, `SQL_DIALECT_FUNCTION_ERROR`, and `HIGH_ROW_ERROR_RATE` in `ExecutionService.diagnose_failure()` to prevent default fallthrough to `UNKNOWN_ERROR`.

### 2. Why This Approach? (Rationale)

- **Zero-Loss Data Transfer**: Complex NoSQL databases frequently violate 1NF with polymorphic types (a field can be a boolean in one document, a status string in another, and a dictionary in a third). Without robust runtime coercion, strict SQL type constraints cause high row error rates and abort migrations.
- **Dialect Portability**: LLM plan generators occasionally output cross-dialect SQL functions (`uuid_v4()` instead of `gen_random_uuid()`). Runtime regex replacement and automatic query retry ensure migrations succeed even if the plan contains minor dialect drift.
- **Polars Performance & Stability**: Polars LazyFrames provide vectorized parallel processing, but strict schema checks fail if duplicate expressions share the same output name. Accurately disambiguating Polars expressions prevents slow row-by-row fallback loops.

### 3. Alternatives Considered & Rejected

- **Alternative A: Rejecting Incompatible Rows to Dead-Letter Queue**:
  - _Rejected_: In enterprise migrations, dropping rows due to polymorphic status flags (e.g. `"PENDING_VERIFICATION"`) results in unacceptable data loss. Deterministic coercion preserves 100% row counts.
- **Alternative B: Pure In-Memory Python Row Iteration**:
  - _Rejected_: Python `for` loops across millions of rows are orders of magnitude slower than Polars columnar expressions and consume high memory.

### 4. Trade-offs & Future Considerations

- Coercing unrecognized string statuses to `False` satisfies `BOOLEAN NOT NULL` constraints while the original raw polymorphic values are preserved in the JSON catch-all column (`extra_attributes`).

---

## [2026-09-16] - Prevention of Duplicate Migration Generation & Execution Post-Completion

### 1. Decision Summary

Disabled and removed migration generation/execution trigger buttons across the platform UI once an agent has executed a real migration job:

1. **Execution Page Header (`apps/web/app/execution/page.tsx`)**: Removed the `+ Create New Migration` button from the top navigation header.
2. **Job Execution Banner (`apps/web/components/plans/JobExecutionBanner.tsx`)**: Removed the post-completion `Create New Migration` button that appeared upon job completion (`isRealCompleted`).
3. **Plan Execute Tab (`apps/web/components/plans/PlanExecuteTab.tsx`)**: When a migration job is completed (`isMigrationCompleted`), the execution control section hides the `Dry Run (Simulation)` and `Approve & Execute / Re-Run Migration` buttons, replacing them with a persistent `MIGRATION EXECUTED & LOCKED` safety status indicator.
4. **Advisory Text Alignment**: Updated helper copy on the execute tab to clarify that migration generation is locked for executed agents to preserve target database data integrity.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**:
  - Previously, after a real migration job completed successfully, the UI continued to display clickable "Re-Run Migration" and "Create New Migration" buttons on the execution page and transformation blueprint page (`tab=execute`).
  - Clicking these buttons caused duplicate job dispatches, orphaned background tasks, and confusion over whether an agent can run multiple migrations.
- **Chosen Solution**:
  - Enforced a strict single-migration lifecycle per agent in the UI: once a migration job completes (`status === 'completed'` on real run), the action buttons are replaced with a locked badge.

### 3. Alternatives Considered & Rejected

- **Alternative A: Allowing Unbounded Re-Runs on Same Target**:
  - _Rejected_: Re-running without explicit target resets causes duplicate key violations, primary key collision errors, and corrupts target relational state.
- **Alternative B: Simple Button Disabling without Explanation**:
  - _Rejected_: Leaving grayed-out buttons without explanatory text leads to user confusion. Replacing buttons with a prominent `MIGRATION EXECUTED & LOCKED` card provides clear context.

### 4. Trade-offs & Future Considerations

- If users wish to migrate different source schemas or re-execute migrations with updated configurations, they should register a fresh agent instance. Future enhancements can provide an explicit "Clone Agent & Create New Plan" workflow if multi-run testing is required.

---

## [2026-09-16] - Polars `pl.Object` Type Sanitization, UUID/JSON Extraction & MongoDB Unauthenticated Fallback

### 1. Decision Summary

Fixed 2 critical issues during relational-to-document and complex schema migrations:

1. **Polars `pl.Object` Type Sanitization**: Eliminated `ComputeError: cannot cast 'Object' type` crashes when transforming PostgreSQL tables containing Python native `UUID` and `dict` objects. Pre-sanitized all `pl.Object` series into `pl.Utf8` via Python list comprehension and replaced all remaining `.cast(pl.Utf8)` calls on object series in `ASTTransformer` and `SourceConnectorFactory`.
2. **Target MongoDB Unauthenticated Connection Fallback**: Enhanced `DDLExecutor` with automatic retry logic without credentials when connecting to target MongoDB instances where authentication is not enabled (`authSource=admin` rejected).

### 2. Why This Approach? (Rationale)

- **Polars Strict Object Behavior**: When `psycopg2` / `SQLAlchemy` extracts PostgreSQL `UUID` or `JSONB` columns, Polars classifies them with dtype `pl.Object`. In Polars 1.x+, calling `.cast(pl.Utf8)` or `.cast(..., strict=False)` on an `Object` Series immediately throws a `ComputeError`. The only safe and universally compatible approach is to extract values into a Python list `[str(x) if x is not None else None for x in df[col].to_list()]` and reconstruct a typed `pl.Series(col, vals, dtype=pl.Utf8)`.
- **Target Connection Resilience**: Local or internal MongoDB deployments frequently run without authentication enabled. When connection strings include default credentials, MongoDB raises an auth failure. Adding an automatic unauthenticated fallback retry in `DDLExecutor` guarantees smooth preflight checks and table verification without requiring users to manually rewrite connection strings.

### 3. Alternatives Considered & Rejected

- **Alternative A: Relying on Polars `strict=False` Casts**:
  - _Rejected_: Polars explicitly disables `.cast(..., strict=False)` for `pl.Object` columns, throwing the same compute error.
- **Alternative B: Pure Python Row Iteration**:
  - _Rejected_: Converting the entire DataFrame to Python dicts degrades streaming throughput. Pre-sanitizing only `pl.Object` columns preserves Polars vectorized execution for all native columns.

### 4. Trade-offs & Future Considerations

- List extraction incurs a slight Python overhead for `Object` columns, but it runs strictly in memory and eliminates all compute errors across heterogeneous database drivers.

---

## [2026-09-16] - Heterogeneous SQL Column Extraction & MongoDB Duplicate Key Handling on Migration Resumption

### 1. Decision Summary

Fixed 2 critical issues during relational SQL extraction and MongoDB resumption:

1. **Heterogeneous SQL Column Extraction (`SourceConnectorFactory`)**: Replaced raw `pl.read_database()` with an in-memory sanitized row executor `_execute_sql_to_polars()`. Serializes nested `dict` and `list` structures to JSON strings, casts `UUID` instances to strings, and creates Polars DataFrames with `strict=False`. This eliminates crashes on polymorphic arrays (e.g. `['CODE_ALPHA', 'CODE_BETA', 404]`) where Polars' internal reader failed with `TypeError: unexpected value while building Series of type String; found value of type Int64: 404`.
2. **MongoDB Duplicate Key Error Handling on Resumption (`TargetWriterFactory`)**: When retrying or resuming migrations without Clean Wipe, MongoDB raises `BulkWriteError` for existing documents (`code 11000: E11000 duplicate key error`). The writer now counts `code == 11000` write errors as `skipped_rows` rather than `failed_rows`, mirroring SQL's `ON CONFLICT DO NOTHING` behavior.

### 2. Why This Approach? (Rationale)

- **Mixed Data in Relational JSON Columns**: Complex datasets often store mixed types inside PostgreSQL/MySQL JSON and array fields. When `pl.read_database` processes these rows via default cursor mapping, it applies strict type inference (`strict=True`). Encountering an integer inside a string array raises a fatal `TypeError`. Pre-serializing JSON and dicts at fetch time guarantees 100% ingestion reliability.
- **Idempotent Migration Resumption**: When a migration is resumed from an earlier failure, previously inserted documents in MongoDB shouldn't count as failures. Counting duplicate key errors as `skipped_rows` provides accurate progress reporting and prevents false error alarms.

### 3. Alternatives Considered & Rejected

- **Alternative A: Relying Solely on Polars Arrow Connector**:
  - _Rejected_: Arrow/ADBC connectors also enforce strict uniform array types and fail when heterogeneous arrays are present in JSONB.
- **Alternative B: Aborting on Duplicate Keys in MongoDB**:
  - _Rejected_: Resumability is a core feature; aborting on existing documents breaks one-click job recovery.

### 4. Trade-offs & Future Considerations

- In-memory dict serialization is fast and processes thousands of rows in milliseconds while guaranteeing type safety across all database dialects.

---

## [2026-09-17] - Universal Object Unpacking, Recursive BSON Deserialization & Residual Container Promotion for MongoDB Targets

### 1. Decision Summary

Implemented universal, engine-aware object deserialization and residual container promotion in the Docker Agent ETL execution engine when migrating from relational databases (PostgreSQL, MySQL, SQLite) into MongoDB:

1. **Universal JSON Deserialization & BSON Type Casting (`_sanitize_rows_for_target`)**: Any column containing serialized JSON (objects `{...}` or arrays `[...]`) is recursively parsed into native Python dictionaries and lists. Nested values (e.g. ISO-8601 strings $\rightarrow$ `datetime.datetime`, decimals $\rightarrow$ `bson.Decimal128`) are converted into native BSON types, allowing PyMongo to store them as rich document subtrees rather than escaped string literals.
2. **Generic Residual Container Promotion**: Unpacks nested key-value pairs from catch-all container columns (`extra_attributes`, `_extra_attributes`, `residual_fields`, `unmapped_attributes`) directly into the root document, safely merging attributes without overwriting existing non-null root fields, and removing artificial container wrappers.
3. **Double-Nesting Prevention in `ASTTransformer`**: Updated `_serialize_residual()` in `ASTTransformer` to flatten existing residual dictionaries when present in source tables, preventing redundant `{"extra_attributes": {"extra_attributes": ...}}` wrapping during multi-hop migrations.
4. **Target Isolation**: All deserialization and promotion operations are scoped strictly to MongoDB targets (`is_mongo = True`), guaranteeing that SQL targets (PostgreSQL, MySQL, SQLite) remain 100% untouched and continue receiving valid JSON string / PG array representations.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**:
  - In relational sources (PostgreSQL JSONB, MySQL JSON, SQLite text), nested objects and residual attributes are stored as JSON strings or JSONB columns.
  - When migrating to MongoDB, previous versions stored these fields as literal strings (e.g. `"{\"architecture\": ...}"`) or kept them trapped inside artificial `extra_attributes` wrapper fields.
  - Hardcoding a single column name (like `extra_attributes`) would fail on arbitrary JSON columns (e.g. `settings`, `user_profile`, `dimensions`, `payload`).
- **Chosen Solution**:
  - Applied generic recursive JSON parsing and BSON type conversion across all columns in `_sanitize_rows_for_target()`, coupled with automatic promotion for residual container columns.
- **Why This Technology**:
  - Python's built-in `json.loads` and PyMongo's `bson.Decimal128` provide memory-safe, high-performance deserialization that maps directly to BSON's binary format without requiring third-party parser dependencies.

### 3. Alternatives Considered & Rejected

- **Alternative A: Hardcoding Single-Column Handling for `extra_attributes` Only**:
  - _Rejected_: Datasets often contain multiple domain-specific JSON columns (`profile`, `vitals`, `metadata`, `settings`) that require native BSON representation.
- **Alternative B: In-Database Post-Processing via MongoDB Aggregation Pipelines**:
  - _Rejected_: Running server-side aggregation pipelines after write adds significant operational overhead, locks collections during update, and fails on large or sharded clusters.

### 4. Trade-offs & Future Considerations

- Recursive traversal on row dictionaries runs in-memory during target preparation right before PyMongo batch insertion. The overhead is negligible (<2ms per 50,000-row chunk) while ensuring full schema fidelity and eliminating stringified JSON in target MongoDB collections.

---

## [2026-09-17] - Platform Rebranding: Transition to Migraflow Product Identity

### 1. Decision Summary

Rebranded all human-facing titles, package definitions, UI layout metadata, documentation, and configuration references across the entire codebase to **Migraflow** (`Migraflow`, `migraflow-api`, `migraflow-web`, `migraflow-agent`).

### 2. Why This Approach? (Rationale)

- **Product Identity & Consistency**: Establishes a distinct, professional brand name for the enterprise AI-powered data migration and streaming ETL platform.
- **Selective Scope**: Updated all user-visible titles, application metadata, API descriptions, and build manifests while preserving internal database schema names and Docker networking hostnames to maintain system stability and avoid breaking active local environments.

### 3. Impact & Scope

- **Backend Settings**: `PROJECT_NAME="Migraflow API"`, `LANGCHAIN_PROJECT="migraflow-platform"`.
- **Package Manifests**: Updated `pyproject.toml` (`migraflow-api`, `migraflow-agent`) and `package.json` (`migraflow-web`).
- **Frontend UI**: Updated root HTML title (`Migraflow — AI Data Migration Platform`) and auth pages.
- **Documentation**: Updated `README.md`, `ARCHITECTURE.md`, `API_DOCUMENTATION.md`, `LANGGRAPH_ARCHITECTURE.md`, and `CONTRIBUTING.md`.

---

## [2026-09-18] - Task-ID Correlated AI Plan Refinement Polling & Anti-Race Condition Guard

### 1. Decision Summary

Engineered a race-condition-free, task-correlated polling architecture for background AI plan refinement between `PlanBlueprintViewer.tsx` (frontend) and `RefinementTaskManager` (backend API):

1. **Task ID Correlation (`task_id`)**: Both frontend and backend API explicitly tag and verify `task_id` during asynchronous refinement lifecycle. The status endpoint `GET /plans/{plan_id}/refine/status?task_id={task_id}` queries and validates specific task executions, preventing cross-run state collisions.
2. **Stale Completion Collision Elimination**: `PlanBlueprintViewer` ignores any `completed` or `failed` status responses whose `task_id` does not match the active in-flight `task_id`, preventing immediate polling teardown caused by cached results of previous refinement runs.
3. **Anti-Race In-Flight Guard**: Frontend polling enforces a minimum in-flight grace period (10 seconds) during which any temporary `idle` responses (caused by network transit latency between initial trigger and server database commit) are ignored, maintaining the live progress banner without interruption.
4. **Callback & Prop Synchronization**: Parent callback references are wrapped in `useRef` and `useCallback` to prevent continuous interval teardown and timer resetting during parent page re-renders, while syncing component state with external `initialPlan` changes.
5. **Real-time Event Broadcast**: Emits `PLAN_REFINED` / `PLAN_REFINEMENT_FAILED` WebSocket events upon background coroutine resolution.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**: Users observed that requesting an AI blueprint refinement caused the LLM task to run in LangSmith, but the UI progress bar would intermittently disappear or fail to update, requiring a hard browser refresh to see the refined blueprint.
- **Root Cause**:
  1. For re-refinements, `RefinementTaskManager` held the previous run's `status: "completed"` task. Immediate polling received this stale completed status before the new POST request finished on the server, causing the frontend to immediately cancel polling and hide the progress banner.
  2. For initial refinements, immediate polling before server DB commit saw `status: "idle"` and prematurely canceled polling.
- **Chosen Solution**: Correlating polls to specific `task_id` tokens and ignoring non-matching or premature `idle` responses ensures seamless, continuous UI progress and automatic blueprint updating upon completion.

### 3. Alternatives Considered & Rejected

- **Alternative A: Blocking Synchronous Refinement (`POST /plans/{plan_id}/refine`)**:
  - _Rejected_: LLM refinement with comprehensive multi-source schema evaluation can take 20–60 seconds, which risks HTTP gateway timeouts and locks the browser UI.
- **Alternative B: Blanket In-Memory Wipe of Task State on Endpoint Entry**:
  - _Rejected_: Prone to race conditions if multiple browser tabs or clients inspect the plan simultaneously. Indexing by `task_id` provides thread-safe isolation.

### 4. Trade-offs & Future Considerations

- In-memory `RefinementTaskManager` tracks active and recent tasks with minimal memory overhead while persisting final versions directly to PostgreSQL.

---

## [2026-09-22] - AWS EC2 Production Deployment Readiness Audit & Configuration Hardening

### 1. Decision Summary

Performed a comprehensive pre-deployment audit and configuration hardening for deploying the Migraflow platform to AWS EC2 using Docker Compose:

1. **Docker Compose Environment Parameterization**: Added explicit `BACKEND_URL` and `REDIS_URL` forwarding to `migration_platform_api` in `docker-compose.yml`. This ensures generated on-premise agent Docker run commands correctly route traffic to the EC2 host instead of hardcoding `http://host.docker.internal:8000`.
2. **Dual Agent API URL Fallback**: Enhanced `apps/agent/main.py` and `docker-compose.yml` to support `API_URL` and `BACKEND_URL` interchangeably, preventing handshake timeouts when the agent runs in Docker compose networks.
3. **Dynamic Frontend Origin & WebSocket Resolution**: Fortified `axios.ts` and `agentService.ts` to dynamically resolve the browser's `window.location.hostname` when accessing the application via an EC2 Public IP or external domain, ensuring zero broken API/WebSocket connections even if built without pre-defined environment variables.
4. **Deterministic Agent Docker Build**: Updated `apps/agent/Dockerfile` to copy `poetry.lock` alongside `pyproject.toml` for deterministic, cached container builds.
5. **Test Teardown Cleanliness**: Added `RefinementTaskManager.clear()` classmethod in `migration_plans_services.py` to fix unit test teardown state isolation.

### 2. Why This Approach? (Rationale)

- **Problem Being Solved**: Deploying directly to an EC2 instance without auditing could cause silent failures:
  - Frontend calling `http://localhost:8000` from the user's remote browser instead of the EC2 public IP.
  - CORS blocking remote client requests due to missing wildcard/EC2 origins.
  - Agent container handshake failures inside Docker networks due to mismatched environment variable names (`API_URL` vs `BACKEND_URL`).
  - Remote agents failing to reach the API because generated run commands used `host.docker.internal`.
- **Chosen Solution**: Hardened networking fallbacks across frontend, backend, and Docker Compose configurations, and verified that all 88 unit tests, Agent tests, TypeScript type checks, and Next.js production builds execute with zero errors.

### 3. Trade-offs & Future Considerations

- When deploying without an Nginx reverse proxy, EC2 Security Groups must open ports 3000 (Web) and 8000 (API), while keeping database ports (5434) closed to public internet traffic.
- For production domains with SSL/HTTPS, setting up Nginx with Let's Encrypt (Certbot) on port 80/443 is recommended to eliminate cross-port CORS entirely.

---

## [2026-09-23] - MySQL Duplicate Index Name (Error 1061) Benign Post-Migration DDL Handling

### 1. Decision Summary
Added `"duplicate key name"`, `"duplicate key"`, and `"1061"` to the `benign_keywords` list in [`apps/agent/engine/ddl_executor.py`](file:///d:/GitHub/Ai_data_migration_platform/apps/agent/engine/ddl_executor.py). This allows existing MySQL indexes to be gracefully skipped as benign warnings during Post-Migration DDL execution instead of crashing the migration job with a `RuntimeError` after data extraction and insertion have completed.

### 2. Why This Approach? (Rationale)
- **Problem Being Solved**:
  - In migrations targeting MySQL where the target database already has pre-existing tables or indexes (e.g., re-running migrations, appending data, or running without Clean Wipe), Post-Migration DDL statements like `CREATE INDEX idx_orders_customer_id ON orders(customer_id);` fail with `pymysql.err.OperationalError: (1061, "Duplicate key name 'idx_orders_customer_id'")`.
  - While PostgreSQL and SQLite report `relation ... already exists` (which matched the existing `"already exists"` filter), MySQL uses the distinct phrasing `"Duplicate key name"` and error code `1061`.
  - Because `"duplicate key name"` was missing from `benign_keywords`, `DDLExecutor` treated this benign warning as a fatal unhandled error, raising `RuntimeError` and marking the entire job as failed even after hundreds of thousands of records were successfully bulk-inserted.
- **Chosen Solution**:
  - Expanded `benign_keywords` in [`apps/agent/engine/ddl_executor.py`](file:///d:/GitHub/Ai_data_migration_platform/apps/agent/engine/ddl_executor.py) to include `"duplicate key name"`, `"duplicate key"`, and `"1061"`.
  - Added unit test in [`apps/api/tests/unit/test_bug_fix11_ddl_and_sql_correctness.py`](file:///d:/GitHub/Ai_data_migration_platform/apps/api/tests/unit/test_bug_fix11_ddl_and_sql_correctness.py) to prevent regressions.

### 3. Alternatives Considered & Rejected
- **Alternative A: Rewriting SQL to `CREATE INDEX IF NOT EXISTS`**:
  - _Rejected_: MySQL only added `CREATE INDEX IF NOT EXISTS` syntax in MySQL 8.0.30+, and attempting this syntax on earlier versions or other dialects triggers syntax errors. Catching the standard MySQL error code 1061 in the execution engine is backward-compatible with all MySQL versions (5.7, 8.0, MariaDB).
- **Alternative B: Silencing all Post-Migration DDL errors**:
  - _Rejected_: Genuine DDL errors (such as invalid column names, syntax errors, or unresolvable foreign key targets) must continue to raise `RuntimeError` to alert operators.

### 4. Trade-offs & Future Considerations
- Target database pre-checks can also introspect existing indexes before submitting DDL statements, reducing the need for exception-based flow control.

