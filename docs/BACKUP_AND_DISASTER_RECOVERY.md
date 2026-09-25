# Migraflow Backup and Disaster Recovery Architecture

This document defines the operational recovery expectations, data durability tiers, authoritative systems of record, and disaster recovery (DR) procedures for the Migraflow platform across control-plane PostgreSQL, cached metadata, execution history, and checkpoints.

---

## 1. Data Classification and Authoritative Systems of Record

| Data Category | Tables / Stores | Durability Tier | Recovery Point Objective (RPO) | Recovery Time Objective (RTO) | Is Authoritative? |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Control-Plane Core** | `users`, `agents`, `data_sources` | Tier 1 (Critical) | < 5 minutes (WAL archiving) | < 30 minutes | **Authoritative**. System of record for accounts, agent API token hashes, and configured endpoints. |
| **Migration Plans & Blueprints** | `migration_plans`, `migration_plan_versions`, `destructive_approvals` | Tier 1 (Critical) | < 5 minutes | < 30 minutes | **Authoritative**. Contains generated plans, AST edits, approval signatures, and version trees. |
| **Execution State & Checkpoints** | `migration_jobs`, `migration_execution_plans`, `migration_execution_steps`, `execution_checkpoints` | Tier 1 (Critical) | 0 seconds (committed transactions) | < 15 minutes | **Authoritative**. Monotonically increasing `cursor_offset`, `rows_processed`, and version markers used by worker agents to resume without data loss or duplication. |
| **Execution History & Telemetry** | `agent_runs`, `execution_events`, `execution_traces`, `llm_call_records`, `resource_budgets`, `verification_results` | Tier 2 (Audit / Telemetry) | < 1 hour | < 2 hours | **Authoritative for Billing / Audits**. Immutable, append-only logs. Can be recovered from replica or cold backup without halting active migrations. |
| **Schema Metadata Snapshots** | `metadata_snapshots`, `database_metadata`, `table_metadata`, `column_metadata` | Tier 3 (Cache) | N/A (Can be refreshed on demand) | < 1 hour | **Cached Representation**. Reflects the remote source/destination database schemas at inspection time. If lost, the agent can re-introspect schemas in seconds. |
| **In-Memory Transport / Queues** | Redis (pub/sub, broker channels), WebSocket client sessions | Tier 4 (Ephemeral) | N/A (No persistent state) | Immediate / Instant restart | **Non-Authoritative**. Pure transient transport. If Redis crashes or restarts, agents re-establish connections and resume polling without data loss. |

---

## 2. Control-Plane PostgreSQL Backup Architecture

### 2.1 Continuous Archiving (WAL Streaming + Point-in-Time Recovery)
- **Primary Engine**: PostgreSQL 16 on Linux/Docker with `wal_level = replica`, `archive_mode = on`.
- **Archiving Target**: Encrypted object storage (AWS S3, Google Cloud Storage, or Azure Blob) via `pgBackRest` or `wal-g`.
- **Archive Frequency**: Continuous WAL shipping (`archive_timeout = 60s`).
- **Full Physical Backups**: Daily at 02:00 UTC with 30-day retention.
- **Differential Backups**: Every 6 hours.

### 2.2 Logical Backups (`pg_dump`)
- Automated daily logical dump:
  ```bash
  pg_dump -Fc -Z 6 -U postgres -d migration_platform -f /backups/migraflow_$(date +%Y%m%d_%H%M%S).dump
  ```
- Stored offsite with SHA-256 integrity checksums.

---

## 3. Disaster Recovery Expectations by Failure Mode

### 3.1 Total Control-Plane Database Crash / Restart
1. **Behavior During Outage**:
   - Running worker Docker Agents continue buffered chunk streaming if DB connection drops briefly.
   - Worker agents retry saving checkpoints with exponential backoff (up to 5 attempts, initial 2s delay).
   - If DB is unavailable for > 60 seconds, agents pause streaming and transition step to `retrying` or wait for API reconnect.
2. **Recovery Procedure**:
   - Restore database from latest WAL PITR snapshot.
   - Run `alembic upgrade head` to verify schema migration integrity.
   - The `stale_agent_watchdog` wakes up:
     - Evaluates agents whose heartbeats stopped during outage.
     - Jobs in `running` or `preparing` whose agents are offline are cleanly marked `failed` or `recovering`.
     - When agents reconnect, they query `ExecutionPlanService.get_checkpoints_for_step` and resume directly from the latest persisted `cursor_offset`.

### 3.2 Agent Container Crash or Host Destruction
1. **Behavior During Crash**:
   - Agent container terminates abruptly (OOM, node eviction, power cut).
   - The control plane's periodic `stale_agent_watchdog` runs every 20 seconds.
   - After 60 seconds of missed heartbeats, the agent is marked `offline` (`DISCONNECTED_UNEXPECTEDLY`).
   - The orphaned job is marked `failed` or transitioned to `recovering`.
   - The active execution step is placed in `retrying` status with `attempt_count` incremented.
2. **Recovery Procedure**:
   - Start replacement agent container using the agent command or run a standby agent in the same tenant fleet.
   - The new agent authenticates via `X-Agent-Token`.
   - Agent claims the retrying step via `POST /executions/plans/{plan_id}/steps/claim`.
   - Agent queries `GET /executions/steps/{step_id}/checkpoints`, reads the last written `cursor_offset` (e.g., row 50,000), and resumes streaming with `WHERE id > 50000` or keyset pagination.
   - Zero duplicated rows and zero skipped rows.

### 3.3 Network Partition Between Agent and API
1. **Behavior**:
   - Heartbeat updates fail.
   - Agent buffers up to 100 checkpoint events in memory.
   - Agent tests network connectivity to API `/api/v1/health`.
   - When partition resolves, agent flushes checkpoint buffer. Monotonic checkpoint protection (`cursor_offset >= existing_offset`) guarantees out-of-order retries never regress the durable cursor backwards.

### 3.4 Incomplete or Aborted Post-Migration Verification
1. **Behavior**:
   - If an agent or worker container terminates while a job is in `verifying`:
   - Watchdog detects the dead agent and transitions the job from `verifying` to `failed`.
   - The job is never falsely marked `completed`.
   - User or operator can re-trigger verification via `POST /executions/{id}/verify` once connectivity is restored.

---

## 4. Runbook: Full System Disaster Recovery

In the event of complete infrastructure failure (datacenter loss, volume corruption):

```bash
# 1. Provision new PostgreSQL 16 cluster
docker compose up -d postgres

# 2. Restore latest base backup and replay WALs to point of failure
pg_restore -U postgres -d migration_platform -v /backups/latest_migraflow.dump

# 3. Verify schema migrations
alembic current
alembic upgrade head

# 4. Start API service (non-root container)
docker compose up -d api

# 5. Validate system health probe
curl -f http://localhost:8000/api/v1/health
# Expected: {"success": true, "data": {"database_connected": true, "status": "ok"}}

# 6. Deploy worker agents
# Agents reconnect using persisted api_token_hash and poll pending tasks
docker compose --profile agent up -d
```
