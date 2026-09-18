# Migraflow — AI Data Migration Platform

An enterprise-grade, AI-assisted platform for data migration, data merging, data profiling, cleaning, and transformation.

> **Core Paradigm**:
> 
> $$\text{ONE TRANSFORMATION PLAN} \longrightarrow \text{MULTIPLE EXECUTION MODES}$$
> 
> The platform supports two execution modes driven by the **SAME** transformation plan:
> 1. **Cloud Execution Mode**: Asynchronous deterministic data streaming via Polars/DuckDB managed by background worker processes.
> 2. **Local Script Package Mode**: Self-contained generated `.zip` package containing `run.py`, `migration_plan.json`, and setup dependencies for local/on-premise execution.

---

## 1. Technology Stack

### Frontend Stack (`apps/web`)
- **Framework**: Next.js 14 (App Router)
- **Language**: TypeScript
- **State Management**: Redux Toolkit & TanStack Query (`@tanstack/react-query`)
- **Styling**: Tailwind CSS & CSS Variables
- **Interactive Visual Mapping**: React Flow (`reactflow`)
- **Icons & UI Utilities**: Lucide React (`lucide-react`), `clsx`, `tailwind-merge`

### Backend Stack (`apps/api`)
- **Language**: Python 3.11+
- **API Framework**: FastAPI & Uvicorn
- **Dependency Management**: Poetry (`pyproject.toml` & `poetry.lock`)
- **Data Validation & Schemas**: Pydantic v2 & Pydantic-Settings
- **ORM & Database**: SQLAlchemy v2 (AsyncPG driver for PostgreSQL metadata)
- **Data Processing Stack**: Polars, DuckDB, PyArrow, OpenPyXL
- **Task Queue & Workers**: Celery / RQ with Redis backend
- **AI Reasoning Engine**: Google Gemini (`google-generativeai`) with Pydantic structured output enforcement
- **Testing & Tooling**: Pytest, Pytest-Asyncio, HTTPX, Ruff

---

## 2. Monorepo Architectural Pattern

This project follows a **Feature-Wise Modular Architecture** (Domain-Driven Feature Layout).

### Key Architectural Rules
1. **Self-Contained Feature Modules**: Every feature domain lives in `apps/api/app/modules/<feature_name>/` and encapsulates its own models, schemas, services, API routes, processing engines, and background tasks.
2. **Explicit File Naming Convention**: File names inside a feature module are explicitly prefixed with the feature name (`<feature_name>_<layer>.py`) to prevent ambiguity across imports (e.g. `profiler_models.py`, `profiler_services.py`).
3. **Thin API Routes**: Routes only handle HTTP request parsing, status codes, and dependency injection. Business logic resides strictly in `*_services.py` or `*_engine.py`.
4. **No Dynamic AI Code Execution**: The AI module outputs strongly typed, Pydantic-validated JSON plans (`TransformationPlan`). Raw code execution (`exec()`, `eval()`) is strictly prohibited.

---

## 3. Directory Structure & File Roles

```text
data-migration-platform/
│
├── apps/
│   │
│   ├── web/                                  # Frontend Web Application (Next.js 14 App Router)
│   │   ├── app/                              # Pages: /login, /register, /dashboard, /agents/create, /sources, /profiling, /transformation-plan, /execution
│   │   ├── components/                       # UI components: AgentStatusBanner, SchemaCatalogViewer, PlanBlueprintViewer, DockerCommandOutput
│   │   ├── services/                         # Axios client with HTTP-only cookies, 401 refresh queue, and WebSocket manager
│   │   └── store/                            # Redux Toolkit & TanStack Query store context
│   │
│   ├── agent/                                # Customer On-Premise Docker Agent Daemon
│   │   ├── main.py                           # Daemon loop, heartbeat thread, 20s task poller & auto-registration
│   │   ├── metadata_engine.py                # Schema introspection engine (capping introspection at 500 tables)
│   │   └── execution_engine.py               # Local ETL execution engine (DuckDB staging staging_*.duckdb, Keyset pagination, ASTTransformer, TargetWriterFactory)
│   │
│   └── api/                                  # Control Plane FastAPI Backend Service
│       ├── app/
│       │   ├── core/                         # Config, db session, security, structured logging, WebSocket manager
│       │   │
│       │   └── modules/                      # Domain Modules
│       │       ├── users/                    # User identity, JWT, HTTP-only cookie auth & OAuth 2.0
│       │       ├── agents/                   # Docker Agent lifecycle, SHA-256 token auth, CLI command generator, watchdog
│       │       ├── sources/                  # Data source registration & connection drivers
│       │       ├── metadata/                 # Introspection snapshot storage & versioning
│       │       ├── migration_plans/          # AI Plan generator (Gemini 3.5), LangGraph StateGraph, 5-stage feasibility validator
│       │       └── execution/                # Job dispatcher, active execution locks (409 Conflict), SKIP LOCKED task queue & watchdog
│       │
│       └── main.py                           # FastAPI main app & background watchdog task startup
│       │
│       ├── tests/                            # Automated Test Suites
│       │   ├── unit/                         # Unit tests for each feature module
│       │   ├── integration/                  # FastAPI integration & route tests
│       │   └── e2e/                          # End-to-end pipeline stub test
│       │
│       ├── Dockerfile                        # Multi-stage production container for API backend
│       ├── poetry.lock                       # Locked Python dependency versions
│       ├── pyproject.toml                    # Poetry project configuration & dependencies
│       └── README.md                         # Backend module documentation
│
├── infra/                                    # Infrastructure & DevOps Support
│   ├── docker/                               # Production Docker configs
│   └── scripts/                              # Database initialization & deployment scripts
│
├── docs/                                     # System Documentation
│   ├── ai_planner.md
│   ├── api.md
│   ├── connectors.md
│   ├── profiler.md
│   ├── script_generation.md
│   ├── transformation_engine.md
│   └── workers.md
│
├── docker-compose.yml                        # Docker Compose orchestrating web, api, worker, postgres, redis
├── .env.example                              # Environment variable template
├── .gitignore                                # Git ignore configuration
├── ARCHITECTURE.md                           # Comprehensive architecture specification
├── CONTRIBUTING.md                           # Developer contribution guidelines
└── README.md                                 # Monorepo root README
```

---

## 4. What Code Each File Type Must Contain

| File Type / Extension Pattern | Responsibility & Code Contents |
| :--- | :--- |
| `*_models.py` | **SQLAlchemy ORM Models**: Database table definitions (e.g. `User`, `DataSource`, `DatasetProfileModel`, `TransformationPlanModel`). Defines column types, keys, relationships, and metadata persistence. |
| `*_schemas.py` | **Pydantic Schemas & DTOs**: Request/response contracts, API payloads, and strongly typed JSON specifications (e.g. `TransformationPlan`, `ColumnMetadata`, `DataSourceCreate`). |
| `*_services.py` | **Application Services**: Business logic & orchestration between ORM models, domain engines, connectors, and AI modules. |
| `*_routes.py` | **FastAPI Route Handlers**: HTTP route endpoints (e.g., `@router.get("")`, `@router.post("")`). Must remain thin by calling services. |
| `*_engine.py` | **Processing Engines**: High-performance computations (e.g., chunked data profiling or Polars/DuckDB ETL data transformations). |
| `*_connectors/` | **Data Source Drivers**: Abstractions and drivers for reading metadata and streaming batches from database/file sources. |
| `*_ai/` | **AI Providers & Reasoning**: Gemini SDK interactions and AI column matching logic. |
| `*_tasks.py` | **Celery / RQ Worker Tasks**: Asynchronous background jobs executed outside the HTTP request/response loop. |
| `*_policy.py` | **Execution Policy Rules**: Threshold rules evaluating row counts and data size to determine `CLOUD` vs `LOCAL_SCRIPT` mode. |
| `*_script_generator/` | **Package Packager**: Generates downloadable ZIP archives containing `run.py`, `migration_plan.json`, and setup instructions for local execution. |

---

## 5. Quickstart Guide

### Running with Docker Compose (Recommended)

```bash
# 1. Copy environment variables template
cp .env.example .env

# 2. Start all services (Next.js, FastAPI, Worker, Postgres, Redis)
docker compose up --build
```

- **Frontend Application**: `http://localhost:3000`
- **FastAPI Documentation**: `http://localhost:8000/docs`
- **API Health Check**: `http://localhost:8000/api/v1/health`
