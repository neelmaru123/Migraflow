# Contributing Guidelines — Migraflow Platform

Welcome to the **Migraflow** repository. Please adhere to these guidelines during development.

---

## Core Development Rules

1. **Inspect Before Modifying**: Always inspect existing code and architecture before making additions or updates.
2. **Do Not Overbuild**: Focus on established MVP capabilities. Avoid adding unrequested microservices, billing systems, or dynamic plugins.
3. **Strict Dependency Management**:
   - Use **Poetry** for Python dependencies (`apps/api/pyproject.toml` and `apps/agent/pyproject.toml`).
   - Do NOT add top-level `requirements.txt` or manually edit lockfiles.
4. **No Dynamic AI Code Execution**:
   - The AI module MUST only return structured data conforming to the `TransformationPlanAST` schema.
   - NEVER call `exec()`, `eval()`, or execute raw AI-generated code. All transformations are deterministically executed by Polars and DuckDB.
5. **No Full Dataset In-Memory Loading**:
   - Always use streaming iterators, chunking (e.g. 50,000 rows per batch), or lazy evaluation in Polars/DuckDB.
6. **Modular Architecture & Decoupled Handlers**:
   - Backend routes must remain thin and reside in `apps/api/app/modules/<feature>/` (`routes`, `services`, `models`, `schemas`).
   - Agent execution logic must adhere to the modular structure under `apps/agent/engine/` (`orchestrator.py`, `connectors/`, `transformers/`, `staging/`, `writers/`, `checkpoint.py`, `db.py`). Never reintroduce monolithic files.
7. **Database Engine & Connection Pool Management**:
   - In the agent engine, always retrieve SQLAlchemy engines via `_get_engine(url)` in `apps/agent/engine/db.py`.
   - NEVER leak open connections or engine instances. Always call `dispose_all_engines()` in `finally:` blocks inside `ExecutionOrchestrator` to release connection pools and file handles.
8. **Target Database Pre-Flight Connectivity Checks**:
   - Target database writers must implement fast, proactive connectivity checks (`engine.connect()` with a short 3-second timeout) before beginning row extraction. Unreachable targets must fail immediately with clear diagnostic errors rather than falling into slow row retry loops.
9. **Zero Timestamp Fabrication Policy**:
   - When transforming date or timestamp fields in `ASTTransformer`, missing, empty, or unparseable date values MUST evaluate strictly to `None` (SQL `NULL`).
   - NEVER fabricate `datetime.utcnow()` or arbitrary default timestamps. Preserving source nullability and data truthfulness is non-negotiable.
10. **Alembic Schema Versioning**:
    - Any changes to Control Plane database models must be paired with an Alembic migration script in `apps/api/alembic/versions/`.
    - Never modify existing committed migrations; always generate a new sequential migration script.
11. **Immutable Plan Versioning**:
    - Any operation that alters a migration plan blueprint (initial AI generation, user prompt refinement, manual edits, or version rollbacks) must record an immutable `MigrationPlanVersion` snapshot with incremented `version_number`.
12. **Test Requirements**:
    - Write unit tests for all domain packages inside `apps/api/tests/` and `apps/agent/tests/`.
    - Run tests before submitting pull requests: `poetry run pytest`.
13. **Linting and Formatting**:
    - Format and lint Python code using `ruff`: `poetry run ruff check .`.
14. **Zero-Knowledge & Client-Side Credential Substitution**:
    - Database passwords and internal network hostnames are customer private property. The browser auto-substitutes connection placeholders locally in memory via `dockerCommandUtils.ts`; database passwords must NEVER be entered in form fields or transmitted to the Control Plane API.
15. **Phase 1 Supported Connectors Scope**:
    - Phase 1 concentrates strictly on database engines: PostgreSQL, MySQL, and MongoDB. Do not expose file-based engines (CSV/Excel) in user creation forms.
16. **Frontend Type Verification**:
    - Always verify Next.js/React code with `npx tsc --noEmit` inside `apps/web` prior to submitting changes.

---

## Workflow & Git Branching

- Main branch: `main`
- Feature branches: `feature/<feature-name>` (e.g. `feature/connectors`, `feature/plan-versioning`, `feature/resilient-merges`)
- Never commit credentials, `.env` files, `.venv`, `node_modules`, or database dumps.

