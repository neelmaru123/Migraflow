"""
Migration Plans Domain — Business Logic Service
Handles plan creation, retrieval, and lifecycle management.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.db import AsyncSessionLocal
from app.core.websocket_manager import manager
from app.modules.agents.agents_models import Agent
from app.modules.metadata.metadata_models import MetadataSnapshot, MetadataSchema, MetadataTable
from app.modules.migration_plans.migration_plans_engine.migration_plans_llm import (
    PROMPT_VERSION,
    MetadataContextSerializer,
    llm_plan_generator,
)
from app.modules.migration_plans.migration_plans_models import (
    MigrationPlan,
    MigrationPlanSnapshot,
    MigrationPlanVersion,
)
from app.modules.migration_plans.migration_plans_schemas import (
    PlanDetailResponse,
    PlanGenerationJobResponse,
    PlanGenerationStatusResponse,
    PlanRefinementJobResponse,
    PlanRefinementStatusResponse,
    PlanResponse,
    TargetDatabaseConfig,
    TransformationPlanAST,
)
from app.modules.sources.sources_models import DataSource

logger = logging.getLogger(__name__)


class RefinementTaskManager:
    """Thread-safe in-memory task tracker for long-running AI plan refinements."""
    _tasks_by_plan: Dict[uuid.UUID, Dict[str, Any]] = {}
    _tasks_by_id: Dict[str, Dict[str, Any]] = {}
    _lock: asyncio.Lock = asyncio.Lock()

    @classmethod
    def clear(cls) -> None:
        """Clears all stored tasks (primarily for testing teardown)."""
        cls._tasks_by_plan.clear()
        cls._tasks_by_id.clear()

    @classmethod
    async def start_task(cls, plan_id: uuid.UUID, user_prompt: str) -> str:
        async with cls._lock:
            task_id = str(uuid.uuid4())
            task_data = {
                "task_id": task_id,
                "plan_id": plan_id,
                "status": "processing",
                "user_prompt": user_prompt,
                "started_at": datetime.now(timezone.utc),
                "completed_at": None,
                "error": None,
                "plan": None,
            }
            cls._tasks_by_plan[plan_id] = task_data
            cls._tasks_by_id[task_id] = task_data
            return task_id

    @classmethod
    async def get_task(
        cls, plan_id: uuid.UUID, task_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        async with cls._lock:
            task = None
            if task_id and task_id in cls._tasks_by_id:
                task = cls._tasks_by_id[task_id]
            elif plan_id in cls._tasks_by_plan:
                candidate = cls._tasks_by_plan[plan_id]
                if task_id is None or candidate.get("task_id") == task_id:
                    task = candidate

            if not task:
                return None

            started = task.get("started_at")
            completed = task.get("completed_at")
            now = completed or datetime.now(timezone.utc)
            elapsed = (now - started).total_seconds() if started else 0.0
            task_copy = dict(task)
            task_copy["elapsed_seconds"] = round(elapsed, 1)
            return task_copy

    @classmethod
    async def complete_task(
        cls, plan_id: uuid.UUID, plan_detail: Any, task_id: Optional[str] = None
    ) -> None:
        async with cls._lock:
            task = None
            if task_id and task_id in cls._tasks_by_id:
                task = cls._tasks_by_id[task_id]
            elif plan_id in cls._tasks_by_plan:
                task = cls._tasks_by_plan[plan_id]

            if task:
                task["status"] = "completed"
                task["completed_at"] = datetime.now(timezone.utc)
                task["plan"] = plan_detail
                task["error"] = None

    @classmethod
    async def fail_task(
        cls, plan_id: uuid.UUID, error_message: str, task_id: Optional[str] = None
    ) -> None:
        async with cls._lock:
            task = None
            if task_id and task_id in cls._tasks_by_id:
                task = cls._tasks_by_id[task_id]
            elif plan_id in cls._tasks_by_plan:
                task = cls._tasks_by_plan[plan_id]

            if task:
                task["status"] = "failed"
                task["completed_at"] = datetime.now(timezone.utc)
                task["error"] = error_message

    @classmethod
    async def is_running(cls, plan_id: uuid.UUID) -> bool:
        async with cls._lock:
            task = cls._tasks_by_plan.get(plan_id)
            return bool(task and task.get("status") == "processing")


class GenerationTaskManager:
    """Thread-safe in-memory task tracker for long-running AI initial plan generation."""
    _tasks: Dict[uuid.UUID, Dict[str, Any]] = {}
    _lock: asyncio.Lock = asyncio.Lock()

    @classmethod
    async def start_task(
        cls, agent_id: uuid.UUID, plan_id: uuid.UUID, target_database_type: Optional[str] = None
    ) -> str:
        async with cls._lock:
            task_id = str(uuid.uuid4())
            cls._tasks[agent_id] = {
                "task_id": task_id,
                "agent_id": agent_id,
                "plan_id": plan_id,
                "status": "processing",
                "target_database_type": target_database_type,
                "started_at": datetime.now(timezone.utc),
                "completed_at": None,
                "error": None,
                "plan": None,
            }
            return task_id

    @classmethod
    async def get_task(cls, agent_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        async with cls._lock:
            task = cls._tasks.get(agent_id)
            if not task:
                return None
            started = task.get("started_at")
            completed = task.get("completed_at")
            now = completed or datetime.now(timezone.utc)
            elapsed = (now - started).total_seconds() if started else 0.0
            task_copy = dict(task)
            task_copy["elapsed_seconds"] = round(elapsed, 1)
            return task_copy

    @classmethod
    async def complete_task(cls, agent_id: uuid.UUID, plan_id: uuid.UUID, plan_detail: Any) -> None:
        async with cls._lock:
            if agent_id in cls._tasks:
                cls._tasks[agent_id]["status"] = "completed"
                cls._tasks[agent_id]["plan_id"] = plan_id
                cls._tasks[agent_id]["completed_at"] = datetime.now(timezone.utc)
                cls._tasks[agent_id]["plan"] = plan_detail
                cls._tasks[agent_id]["error"] = None

    @classmethod
    async def fail_task(cls, agent_id: uuid.UUID, error_message: str) -> None:
        async with cls._lock:
            if agent_id in cls._tasks:
                cls._tasks[agent_id]["status"] = "failed"
                cls._tasks[agent_id]["completed_at"] = datetime.now(timezone.utc)
                cls._tasks[agent_id]["error"] = error_message

    @classmethod
    async def is_running(cls, agent_id: uuid.UUID) -> bool:
        async with cls._lock:
            task = cls._tasks.get(agent_id)
            return bool(task and task.get("status") == "processing")


def to_plan_detail_dto(plan: MigrationPlan) -> PlanDetailResponse:
    """Converts a MigrationPlan ORM instance into PlanDetailResponse DTO."""
    warnings = []
    if plan.validation_errors and isinstance(plan.validation_errors, dict):
        warnings = plan.validation_errors.get("warnings", [])

    return PlanDetailResponse(
        id=plan.id,
        agent_id=plan.agent_id,
        status=plan.status,
        ai_model=plan.ai_model,
        confidence_score=plan.confidence_score,
        is_valid=plan.is_valid,
        validation_errors=plan.validation_errors,
        validation_warnings=warnings,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
        plan_data=plan.plan_data,
        target_config=plan.target_config,
        prompt_version=plan.prompt_version,
    )


async def _run_plan_refinement_background(
    plan_id: uuid.UUID, user_feedback: str, task_id: str
):
    """Background coroutine running AI refinement with a fresh AsyncSession."""
    async with AsyncSessionLocal() as session:
        try:
            plan = await MigrationPlanService.get_plan_by_id(session, plan_id)
            if not plan:
                await RefinementTaskManager.fail_task(plan_id, f"Plan '{plan_id}' not found.", task_id=task_id)
                return

            refined_plan = await MigrationPlanService.execute_refinement_core(
                session, plan, user_feedback
            )
            plan_dto = to_plan_detail_dto(refined_plan)
            await RefinementTaskManager.complete_task(plan_id, plan_dto, task_id=task_id)
            logger.info(f"Background AI refinement successfully completed for plan '{plan_id}' (task_id: {task_id}).")

            if plan.agent_id:
                try:
                    await manager.broadcast_to_agent(
                        agent_id=str(plan.agent_id),
                        message={
                            "event_type": "PLAN_REFINED",
                            "data": {
                                "plan_id": str(plan.id),
                                "agent_id": str(plan.agent_id),
                                "task_id": task_id,
                                "status": "completed",
                                "plan": plan_dto.model_dump(mode="json"),
                            },
                        },
                    )
                except Exception as b_err:
                    logger.debug(f"Optional broadcast skipped: {b_err}")
        except Exception as exc:
            logger.error(f"Background AI refinement failed for plan '{plan_id}' (task_id: {task_id}): {exc}", exc_info=True)
            await RefinementTaskManager.fail_task(plan_id, str(exc), task_id=task_id)
            try:
                async with AsyncSessionLocal() as recovery_session:
                    stmt = select(MigrationPlan).where(MigrationPlan.id == plan_id)
                    r_res = await recovery_session.execute(stmt)
                    r_plan = r_res.scalar_one_or_none()
                    if r_plan and r_plan.status == "refining":
                        r_plan.status = "edited" if r_plan.plan_data else "draft"
                        await recovery_session.commit()
            except Exception as rec_err:
                logger.warning(f"Failed to revert plan status on refinement error: {rec_err}")


async def _run_plan_generation_background(
    plan_id: uuid.UUID, agent_id: uuid.UUID, target_config_dict: dict
):
    """Background coroutine running initial AI plan generation with a fresh AsyncSession."""
    async with AsyncSessionLocal() as session:
        try:
            stmt_agent = (
                select(Agent)
                .where(Agent.id == agent_id)
                .options(selectinload(Agent.data_sources))
            )
            res_agent = await session.execute(stmt_agent)
            agent = res_agent.scalar_one_or_none()
            if not agent:
                await GenerationTaskManager.fail_task(agent_id, f"Agent '{agent_id}' not found.")
                return

            target_config = TargetDatabaseConfig(**target_config_dict)
            generated_plan = await MigrationPlanService.execute_generation_core(
                session, plan_id, agent, target_config
            )
            plan_dto = to_plan_detail_dto(generated_plan)
            await GenerationTaskManager.complete_task(agent_id, plan_id, plan_dto)
            logger.info(f"Background AI plan generation completed for agent '{agent_id}' (plan: '{plan_id}').")
        except Exception as exc:
            logger.error(f"Background AI plan generation failed for agent '{agent_id}': {exc}", exc_info=True)
            await GenerationTaskManager.fail_task(agent_id, str(exc))
            try:
                async with AsyncSessionLocal() as recovery_session:
                    stmt = select(MigrationPlan).where(MigrationPlan.id == plan_id)
                    r_res = await recovery_session.execute(stmt)
                    r_plan = r_res.scalar_one_or_none()
                    if r_plan and r_plan.status == "generating":
                        r_plan.status = "draft_failed"
                        r_plan.validation_errors = {"explanation": str(exc), "errors": [str(exc)]}
                        await recovery_session.commit()
            except Exception as rec_err:
                logger.warning(f"Failed to update draft_failed status on generation error: {rec_err}")


class MigrationPlanService:
    """Orchestrates AI plan generation, persistence, and queries for the Migration Plans domain."""

    @staticmethod
    async def _fetch_latest_snapshots_for_agent(
        session: AsyncSession, agent: Agent
    ) -> tuple[List[MetadataSnapshot], Dict[str, str]]:
        """
        Fetches the latest MetadataSnapshot for every source DataSource attached to the Agent.
        Returns (snapshots_list, alias_map) where alias_map maps data_source_id → logical alias.
        """
        data_sources = agent.data_sources or []
        if not data_sources:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Agent has no attached data sources. Add source databases before generating a plan.",
            )

        snapshots: List[MetadataSnapshot] = []
        alias_map: Dict[str, str] = {}

        source_count = 0
        for ds in data_sources:
            if ds.role == "target":
                continue
            # Build logical alias: src_db_1, src_db_2, etc.
            source_count += 1
            alias = ds.identifier if ds.identifier else f"source_db_{source_count}"
            alias_map[str(ds.id)] = alias

            # Fetch latest snapshot for this DataSource
            stmt = (
                select(MetadataSnapshot)
                .where(MetadataSnapshot.data_source_id == ds.id)
                .order_by(MetadataSnapshot.version.desc())
                .limit(1)
                .options(
                    selectinload(MetadataSnapshot.schemas)
                    .selectinload(MetadataSchema.tables)
                    .selectinload(MetadataTable.columns),
                    selectinload(MetadataSnapshot.schemas)
                    .selectinload(MetadataSchema.tables)
                    .selectinload(MetadataTable.constraints),
                    selectinload(MetadataSnapshot.relationships),
                )
            )
            res = await session.execute(stmt)
            snap = res.scalar_one_or_none()

            if snap:
                snapshots.append(snap)
            else:
                logger.warning(
                    f"DataSource '{ds.identifier}' (ID: {ds.id}) has no metadata snapshot. "
                    f"Ensure the Docker Agent has run introspection for this source."
                )

        if not snapshots:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "No metadata snapshots found for any of this agent's data sources. "
                    "Run the Docker Agent first to collect metadata before generating a plan."
                ),
            )

        return snapshots, alias_map

    @staticmethod
    async def execute_generation_core(
        session: AsyncSession,
        plan_id: uuid.UUID,
        agent: Agent,
        target_config: TargetDatabaseConfig,
    ) -> MigrationPlan:
        """Executes the AI plan generation pipeline and populates the given plan_id."""
        from app.modules.migration_plans.migration_plans_engine.migration_plans_graph import (
            migration_plan_graph,
        )
        from app.modules.migration_plans.migration_plans_engine.migration_plans_validator import (
            MigrationPlanValidator,
        )

        snapshots, alias_map = await MigrationPlanService._fetch_latest_snapshots_for_agent(
            session, agent
        )

        target_ds = next(
            (ds for ds in (agent.data_sources or []) if ds.role in ("target", "both") and ds.type),
            None,
        )
        if target_ds:
            target_db_type = target_ds.type.lower()
            target_config.database_type = target_db_type
            if not target_config.identifier:
                target_config.identifier = target_ds.identifier
        else:
            target_db_type = (target_config.database_type or "postgresql").lower()
            target_config.database_type = target_db_type

        custom_instructions = target_config.custom_instructions

        initial_state = {
            "agent_id": str(agent.id),
            "user_id": str(agent.user_id),
            "target_db_type": target_db_type,
            "custom_instructions": custom_instructions,
            "context_yaml": "",
            "snapshots": snapshots,
            "alias_map": alias_map,
            "current_ast": None,
            "validation_result": None,
            "user_feedback": None,
            "manual_edits": None,
            "attempt_count": 0,
            "is_approved": False,
            "feasibility_explanation": None,
            "persisted_plan_id": None,
        }

        plan_status = "draft"
        plan_ast_dict = None
        val_res_dict = None

        try:
            res_state = await migration_plan_graph.ainvoke(initial_state)
            plan_ast_dict = res_state.get("current_ast")
            val_res_dict = res_state.get("validation_result")
            if res_state.get("feasibility_explanation"):
                plan_status = "invalid"
            else:
                plan_status = "draft"
        except Exception as exc:
            logger.error(f"LangGraph execution exception: {exc}")
            context_str = MetadataContextSerializer.serialize(
                snapshots=snapshots,
                source_aliases=alias_map,
                target_db_type=target_db_type,
                custom_instructions=custom_instructions,
            )
            try:
                ast_obj = llm_plan_generator.generate(context_str, target_db_type)
                plan_ast_dict = ast_obj.model_dump(mode="json")
                val_res = MigrationPlanValidator.validate(plan_ast_dict, snapshots, alias_map)
                val_res_dict = val_res.model_dump(mode="json")
                plan_status = "draft"
            except Exception as inner_exc:
                logger.error(f"Fallback generation also failed: {inner_exc}")
                plan_status = "draft_failed"

        if not val_res_dict and plan_ast_dict:
            val_res = MigrationPlanValidator.validate(plan_ast_dict, snapshots, alias_map)
            val_res_dict = val_res.model_dump(mode="json")

        is_valid = val_res_dict.get("is_valid", False) if val_res_dict else False

        stmt = select(MigrationPlan).where(MigrationPlan.id == plan_id)
        res = await session.execute(stmt)
        migration_plan = res.scalar_one_or_none()
        if not migration_plan:
            migration_plan = MigrationPlan(
                id=plan_id,
                user_id=agent.user_id,
                agent_id=agent.id,
                status=plan_status,
                plan_data=plan_ast_dict or {"error": "Generation failed"},
                target_config=target_config.model_dump(mode="json"),
                ai_model=f"{settings_llm_provider()}:{settings_llm_model()}",
                prompt_version=PROMPT_VERSION,
                confidence_score=plan_ast_dict.get("confidence_score", 0.9) if plan_ast_dict else 0.0,
                is_valid=is_valid,
                validation_errors=val_res_dict,
            )
            session.add(migration_plan)
            await session.flush()
        else:
            migration_plan.status = plan_status
            migration_plan.plan_data = plan_ast_dict or {"error": "Generation failed"}
            migration_plan.target_config = target_config.model_dump(mode="json")
            migration_plan.ai_model = f"{settings_llm_provider()}:{settings_llm_model()}"
            migration_plan.prompt_version = PROMPT_VERSION
            migration_plan.confidence_score = plan_ast_dict.get("confidence_score", 0.9) if plan_ast_dict else 0.0
            migration_plan.is_valid = is_valid
            migration_plan.validation_errors = val_res_dict

        for snap in snapshots:
            join_row = MigrationPlanSnapshot(
                migration_plan_id=migration_plan.id,
                metadata_snapshot_id=snap.id,
            )
            session.add(join_row)

        initial_version = MigrationPlanVersion(
            migration_plan_id=migration_plan.id,
            version_number=1,
            edit_type="initial_ai_generation",
            user_feedback=target_config.custom_instructions if target_config else None,
            plan_data=plan_ast_dict or {"error": "Generation failed"},
            is_valid=is_valid,
            confidence_score=plan_ast_dict.get("confidence_score", 0.9) if plan_ast_dict else 0.0,
            validation_errors=val_res_dict,
        )
        session.add(initial_version)

        await session.commit()
        return migration_plan

    @staticmethod
    async def create_plan_for_agent(
        session: AsyncSession,
        agent: Agent,
        target_config: TargetDatabaseConfig,
    ) -> MigrationPlan:
        """Synchronously creates and generates a plan for an agent (for backward compatibility)."""
        plan_id = uuid.uuid4()
        initial_plan = MigrationPlan(
            id=plan_id,
            user_id=agent.user_id,
            agent_id=agent.id,
            status="generating",
            plan_data={},
            target_config=target_config.model_dump(mode="json"),
            ai_model=f"{settings_llm_provider()}:{settings_llm_model()}",
            prompt_version=PROMPT_VERSION,
            confidence_score=0.0,
            is_valid=False,
            validation_errors=None,
        )
        session.add(initial_plan)
        await session.flush()
        return await MigrationPlanService.execute_generation_core(
            session, plan_id, agent, target_config
        )

    @staticmethod
    async def start_async_generation(
        session: AsyncSession,
        agent: Agent,
        target_config: TargetDatabaseConfig,
    ) -> tuple[str, uuid.UUID]:
        """Initiates an asynchronous background plan generation task, returning (task_id, plan_id) immediately."""
        # 1. Concurrency Check
        if await GenerationTaskManager.is_running(agent.id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An AI migration plan generation is already in progress for this agent.",
            )

        # Check DB if a plan for this agent is already in 'generating' state
        stmt_gen = select(MigrationPlan).where(
            MigrationPlan.agent_id == agent.id, MigrationPlan.status == "generating"
        )
        res_gen = await session.execute(stmt_gen)
        if res_gen.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An AI migration plan generation is already in progress for this agent.",
            )

        # Validate agent has snapshots before creating record
        snapshots, _ = await MigrationPlanService._fetch_latest_snapshots_for_agent(session, agent)
        if not snapshots:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="No metadata snapshots found for any of this agent's data sources. Run the Docker Agent first to collect metadata before generating a plan.",
            )

        target_ds = next(
            (ds for ds in (agent.data_sources or []) if ds.role in ("target", "both") and ds.type),
            None,
        )
        if target_ds:
            target_db_type = target_ds.type.lower()
            target_config.database_type = target_db_type
            if not target_config.identifier:
                target_config.identifier = target_ds.identifier
        else:
            target_db_type = (target_config.database_type or "postgresql").lower()
            target_config.database_type = target_db_type

        # Create placeholder MigrationPlan record in DB
        initial_plan = MigrationPlan(
            user_id=agent.user_id,
            agent_id=agent.id,
            status="generating",
            plan_data={},
            target_config=target_config.model_dump(mode="json"),
            ai_model=f"{settings_llm_provider()}:{settings_llm_model()}",
            prompt_version=PROMPT_VERSION,
            confidence_score=0.0,
            is_valid=False,
            validation_errors=None,
        )
        session.add(initial_plan)
        await session.commit()

        task_id = await GenerationTaskManager.start_task(agent.id, initial_plan.id, target_db_type)

        # Launch detached background coroutine
        asyncio.create_task(
            _run_plan_generation_background(
                initial_plan.id, agent.id, target_config.model_dump(mode="json")
            )
        )
        return task_id, initial_plan.id

    @staticmethod
    async def get_generation_status(
        session: AsyncSession,
        agent_id: uuid.UUID,
    ) -> PlanGenerationStatusResponse:
        """Fetches the current status of an ongoing or completed plan generation for the given agent."""
        task = await GenerationTaskManager.get_task(agent_id)
        if task:
            return PlanGenerationStatusResponse(
                task_id=task.get("task_id"),
                agent_id=agent_id,
                plan_id=task.get("plan_id"),
                status=task.get("status", "idle"),
                target_database_type=task.get("target_database_type"),
                started_at=task.get("started_at"),
                completed_at=task.get("completed_at"),
                elapsed_seconds=task.get("elapsed_seconds"),
                error=task.get("error"),
                plan=task.get("plan"),
            )

        # Fallback to database if server restarted or memory evicted
        stmt = (
            select(MigrationPlan)
            .where(MigrationPlan.agent_id == agent_id)
            .order_by(MigrationPlan.created_at.desc())
        )
        res = await session.execute(stmt)
        latest_plan = res.scalars().first()
        if not latest_plan:
            return PlanGenerationStatusResponse(agent_id=agent_id, status="idle")

        if latest_plan.status == "generating":
            now = datetime.now(timezone.utc)
            created_aware = (
                latest_plan.created_at
                if latest_plan.created_at.tzinfo
                else latest_plan.created_at.replace(tzinfo=timezone.utc)
            )
            elapsed = (now - created_aware).total_seconds()
            return PlanGenerationStatusResponse(
                task_id=None,
                agent_id=agent_id,
                plan_id=latest_plan.id,
                status="processing",
                target_database_type=(
                    latest_plan.target_config.get("database_type")
                    if isinstance(latest_plan.target_config, dict)
                    else None
                ),
                started_at=latest_plan.created_at,
                elapsed_seconds=round(elapsed, 1),
            )

        # If latest plan was completed/drafted
        return PlanGenerationStatusResponse(
            task_id=None,
            agent_id=agent_id,
            plan_id=latest_plan.id,
            status="idle",
            target_database_type=None,
            plan=to_plan_detail_dto(latest_plan),
        )

    @staticmethod
    async def get_plan_by_id(
        session: AsyncSession, plan_id: uuid.UUID
    ) -> Optional[MigrationPlan]:
        """Fetch MigrationPlan by primary key UUID with agent and data_sources eagerly loaded."""
        stmt = (
            select(MigrationPlan)
            .where(MigrationPlan.id == plan_id)
            .options(
                selectinload(MigrationPlan.agent).selectinload(Agent.data_sources)
            )
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    @staticmethod
    async def list_plans_for_user(
        session: AsyncSession, user_id: uuid.UUID
    ) -> List[MigrationPlan]:
        """List all MigrationPlans owned by a user, ordered by newest first."""
        stmt = (
            select(MigrationPlan)
            .where(MigrationPlan.user_id == user_id)
            .order_by(MigrationPlan.created_at.desc())
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def _check_active_execution_lock(session: AsyncSession, plan_id: uuid.UUID):
        """Verifies that no active execution job is running for the given migration plan."""
        from app.modules.execution.execution_models import MigrationJob
        stmt = select(MigrationJob).where(
            MigrationJob.migration_plan_id == plan_id,
            MigrationJob.status.in_(["queued", "preparing", "running"]),
        )
        res = await session.execute(stmt)
        active_job = res.scalar_one_or_none()
        if active_job:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot edit migration plan while execution job '{active_job.id}' is active (status: '{active_job.status}').",
            )

    @staticmethod
    async def update_plan_data(
        session: AsyncSession,
        plan: MigrationPlan,
        plan_data: Dict[str, Any],
    ) -> MigrationPlan:
        """Save user-edited plan data and run feasibility validation."""
        from app.modules.migration_plans.migration_plans_engine.migration_plans_validator import (
            MigrationPlanValidator,
        )

        await MigrationPlanService._check_active_execution_lock(session, plan.id)

        plan.plan_data = plan_data
        if plan.agent:
            snapshots, alias_map = await MigrationPlanService._fetch_latest_snapshots_for_agent(
                session, plan.agent
            )
            val_res = MigrationPlanValidator.validate(plan_data, snapshots, alias_map)
            val_dict = val_res.model_dump(mode="json")
            plan.is_valid = val_res.is_valid
            plan.validation_errors = val_dict
            plan.status = "edited" if val_res.is_valid else "invalid_edits"
        else:
            plan.status = "edited"

        # Compute next version number and persist version snapshot
        stmt_ver = select(func.coalesce(func.max(MigrationPlanVersion.version_number), 0)).where(
            MigrationPlanVersion.migration_plan_id == plan.id
        )
        max_ver = (await session.execute(stmt_ver)).scalar_one()
        next_ver = max_ver + 1

        version_snapshot = MigrationPlanVersion(
            migration_plan_id=plan.id,
            version_number=next_ver,
            edit_type="manual_ast_edit",
            plan_data=plan_data,
            is_valid=plan.is_valid,
            confidence_score=plan_data.get("confidence_score", 1.0) if isinstance(plan_data, dict) else 1.0,
            validation_errors=plan.validation_errors,
        )
        session.add(version_snapshot)

        await session.commit()
        return plan

    @staticmethod
    async def execute_refinement_core(
        session: AsyncSession,
        plan: MigrationPlan,
        user_feedback: str,
    ) -> MigrationPlan:
        """Core execution logic for plan refinement, shared by sync and async pathways."""
        from app.modules.migration_plans.migration_plans_engine.migration_plans_validator import (
            MigrationPlanValidator,
        )

        await MigrationPlanService._check_active_execution_lock(session, plan.id)

        # Acquire row lock to serialize concurrent refinement updates (EC-24)
        stmt_lock = select(MigrationPlan).where(MigrationPlan.id == plan.id).with_for_update()
        res_lock = await session.execute(stmt_lock)
        locked_plan = res_lock.scalar_one_or_none()
        if locked_plan:
            plan = locked_plan

        if not plan.agent:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Migration plan has no attached agent.",
            )

        snapshots, alias_map = await MigrationPlanService._fetch_latest_snapshots_for_agent(
            session, plan.agent
        )

        target_db_type = (
            plan.target_config.get("database_type", "postgresql")
            if plan.target_config
            else "postgresql"
        )
        custom_instructions = (
            plan.target_config.get("custom_instructions", "")
            if plan.target_config
            else ""
        )

        context_str = MetadataContextSerializer.serialize(
            snapshots=snapshots,
            source_aliases=alias_map,
            target_db_type=target_db_type,
            custom_instructions=custom_instructions,
        )

        llm_timeout = float(getattr(settings, "LLM_TIMEOUT_SECONDS", 360.0))
        try:
            refined_ast_obj = await asyncio.wait_for(
                asyncio.to_thread(
                    llm_plan_generator.refine,
                    context_str=context_str,
                    current_ast_dict=plan.plan_data,
                    user_feedback=user_feedback,
                ),
                timeout=llm_timeout,
            )
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"LLM plan refinement timed out after {int(llm_timeout)} seconds. Please try again.",
            )

        refined_ast_dict = refined_ast_obj.model_dump(mode="json")
        val_res = MigrationPlanValidator.validate(refined_ast_dict, snapshots, alias_map)
        val_dict = val_res.model_dump(mode="json")

        plan.plan_data = refined_ast_dict
        plan.is_valid = val_res.is_valid
        plan.validation_errors = val_dict
        plan.status = "edited" if val_res.is_valid else "invalid_edits"

        # Compute next version number and insert version snapshot
        stmt_ver = select(func.coalesce(func.max(MigrationPlanVersion.version_number), 0)).where(
            MigrationPlanVersion.migration_plan_id == plan.id
        )
        max_ver = (await session.execute(stmt_ver)).scalar_one()
        next_ver = max_ver + 1

        version_snapshot = MigrationPlanVersion(
            migration_plan_id=plan.id,
            version_number=next_ver,
            edit_type="llm_refinement",
            user_feedback=user_feedback,
            plan_data=refined_ast_dict,
            is_valid=val_res.is_valid,
            confidence_score=refined_ast_dict.get("confidence_score", 0.9) if isinstance(refined_ast_dict, dict) else 0.9,
            validation_errors=val_dict,
        )
        session.add(version_snapshot)

        await session.commit()
        return plan

    @staticmethod
    async def refine_plan(
        session: AsyncSession,
        plan: MigrationPlan,
        user_feedback: str,
    ) -> MigrationPlan:
        """Synchronously refines a plan using natural language user feedback via LLM + Validator."""
        return await MigrationPlanService.execute_refinement_core(session, plan, user_feedback)

    @staticmethod
    async def start_async_refinement(
        session: AsyncSession,
        plan: MigrationPlan,
        user_feedback: str,
    ) -> str:
        """Initiates an asynchronous background refinement task, returning the task_id immediately."""
        await MigrationPlanService._check_active_execution_lock(session, plan.id)

        if await RefinementTaskManager.is_running(plan.id) or plan.status == "refining":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A plan refinement is already in progress for this plan.",
            )

        task_id = await RefinementTaskManager.start_task(plan.id, user_feedback)
        plan.status = "refining"
        await session.commit()

        # Launch detached background coroutine with task_id
        asyncio.create_task(_run_plan_refinement_background(plan.id, user_feedback, task_id))
        return task_id

    @staticmethod
    async def get_refinement_status(
        session: AsyncSession,
        plan: MigrationPlan,
        task_id: Optional[str] = None,
    ) -> PlanRefinementStatusResponse:
        """Fetches the current status of a refinement task for the given plan."""
        task = await RefinementTaskManager.get_task(plan.id, task_id=task_id)
        if task:
            return PlanRefinementStatusResponse(
                task_id=task.get("task_id"),
                plan_id=plan.id,
                status=task.get("status", "idle"),
                user_prompt=task.get("user_prompt"),
                started_at=task.get("started_at"),
                completed_at=task.get("completed_at"),
                elapsed_seconds=task.get("elapsed_seconds"),
                error=task.get("error"),
                plan=task.get("plan"),
            )

        # Fallback if server restarted or task finished before memory retention
        is_refining = plan.status == "refining"
        return PlanRefinementStatusResponse(
            task_id=task_id,
            plan_id=plan.id,
            status="processing" if is_refining else "idle",
            user_prompt=None,
            started_at=None,
            completed_at=None,
            elapsed_seconds=None,
            error=None,
            plan=to_plan_detail_dto(plan) if not is_refining else None,
        )

    @staticmethod
    async def list_plan_versions(
        session: AsyncSession,
        plan_id: uuid.UUID,
    ) -> List[MigrationPlanVersion]:
        """Fetch all versions of a migration plan, ordered from newest to oldest."""
        stmt = (
            select(MigrationPlanVersion)
            .where(MigrationPlanVersion.migration_plan_id == plan_id)
            .order_by(MigrationPlanVersion.version_number.desc())
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def get_plan_version(
        session: AsyncSession,
        plan_id: uuid.UUID,
        version_number: int,
    ) -> Optional[MigrationPlanVersion]:
        """Fetch a specific version of a migration plan by plan ID and version number."""
        stmt = select(MigrationPlanVersion).where(
            MigrationPlanVersion.migration_plan_id == plan_id,
            MigrationPlanVersion.version_number == version_number,
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    @staticmethod
    async def restore_plan_version(
        session: AsyncSession,
        plan: MigrationPlan,
        version_number: int,
    ) -> MigrationPlan:
        """Restores a migration plan to a historical AST version."""
        from app.modules.migration_plans.migration_plans_engine.migration_plans_validator import (
            MigrationPlanValidator,
        )

        await MigrationPlanService._check_active_execution_lock(session, plan.id)

        target_version = await MigrationPlanService.get_plan_version(
            session, plan.id, version_number
        )
        if not target_version:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Version {version_number} not found for migration plan '{plan.id}'.",
            )

        # Update live plan with historical AST data
        plan.plan_data = target_version.plan_data

        if plan.agent:
            snapshots, alias_map = await MigrationPlanService._fetch_latest_snapshots_for_agent(
                session, plan.agent
            )
            val_res = MigrationPlanValidator.validate(target_version.plan_data, snapshots, alias_map)
            val_dict = val_res.model_dump(mode="json")
            plan.is_valid = val_res.is_valid
            plan.validation_errors = val_dict
            plan.status = "edited" if val_res.is_valid else "invalid_edits"
        else:
            plan.status = "edited"

        # Compute next version number for recording the restoration event
        stmt_ver = select(func.coalesce(func.max(MigrationPlanVersion.version_number), 0)).where(
            MigrationPlanVersion.migration_plan_id == plan.id
        )
        max_ver = (await session.execute(stmt_ver)).scalar_one()
        next_ver = max_ver + 1

        restored_snapshot = MigrationPlanVersion(
            migration_plan_id=plan.id,
            version_number=next_ver,
            edit_type="version_restored",
            user_feedback=f"Restored from version {version_number}",
            plan_data=target_version.plan_data,
            is_valid=plan.is_valid,
            confidence_score=target_version.confidence_score,
            validation_errors=plan.validation_errors,
        )
        session.add(restored_snapshot)

        await session.commit()
        return plan

    @staticmethod
    async def validate_plan_by_id(
        session: AsyncSession,
        plan: MigrationPlan,
    ) -> Dict[str, Any]:
        """Runs instant feasibility check on a plan."""
        from app.modules.migration_plans.migration_plans_engine.migration_plans_validator import (
            MigrationPlanValidator,
        )

        if not plan.agent:
            return {
                "is_valid": True,
                "errors": [],
                "warnings": [],
                "explanation": "No agent attached for snapshot validation.",
            }

        snapshots, alias_map = await MigrationPlanService._fetch_latest_snapshots_for_agent(
            session, plan.agent
        )
        val_res = MigrationPlanValidator.validate(plan.plan_data, snapshots, alias_map)
        return val_res.model_dump(mode="json")

    @staticmethod
    async def approve_plan(
        session: AsyncSession,
        plan: MigrationPlan,
    ) -> MigrationPlan:
        """Approves a plan for execution."""
        from app.modules.migration_plans.migration_plans_engine.migration_plans_validator import (
            MigrationPlanValidator,
        )

        if plan.status == "refining":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot approve a migration plan while AI refinement is actively running.",
            )

        if plan.agent:
            snapshots, alias_map = await MigrationPlanService._fetch_latest_snapshots_for_agent(
                session, plan.agent
            )
            val_res = MigrationPlanValidator.validate(plan.plan_data, snapshots, alias_map)
            if not val_res.is_valid:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Cannot approve invalid plan: {val_res.explanation}",
                )

        plan.status = "approved"
        await session.commit()

        if plan.agent_id:
            await manager.broadcast_to_agent(
                agent_id=str(plan.agent_id),
                message={
                    "event_type": "PLAN_GENERATED",
                    "data": {
                        "plan_id": str(plan.id),
                        "agent_id": str(plan.agent_id),
                        "status": "approved",
                    },
                },
            )

        return plan


def settings_llm_provider() -> str:
    from app.core.config import settings
    return settings.LLM_PROVIDER


def settings_llm_model() -> str:
    from app.core.config import settings
    return settings.LLM_MODEL
