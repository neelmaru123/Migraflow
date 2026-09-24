"""
Safety Controls and Destructive Operation Approval Subsystem.
Classifies operation risk levels (READ_ONLY, WRITE, DESTRUCTIVE),
enforces cryptographic/structural approval bindings for destructive DDL/DML,
and handles approval invalidation upon plan version changes.
"""

from datetime import datetime, timezone
import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.state import (
    ExecutionEventType,
    OperationRiskLevel,
)
from app.modules.execution.execution_models import (
    DestructiveOperationApproval,
    ExecutionEvent,
)
from app.modules.migration_plans.migration_plans_models import MigrationPlan

logger = logging.getLogger(__name__)


class SafetyClassifier:
    """
    Deterministically categorizes database operations and DDL statements
    into safety tiers: READ_ONLY, WRITE, or DESTRUCTIVE.
    """

    DESTRUCTIVE_PATTERNS = [
        re.compile(r'(?i)\bDROP\s+TABLE\b'),
        re.compile(r'(?i)\bTRUNCATE(\s+TABLE)?\b'),
        re.compile(r'(?i)\bDELETE\s+FROM\b'),
        re.compile(r'(?i)\bDROP\s+COLUMN\b'),
        re.compile(r'(?i)\bALTER\s+TABLE\s+[\w\.\"]+\s+DROP\b'),
        re.compile(r'(?i)\bDROP\s+DATABASE\b'),
        re.compile(r'(?i)\bDROP\s+SCHEMA\b'),
        re.compile(r'(?i)\bCASCADE\b'),
    ]

    WRITE_PATTERNS = [
        re.compile(r'(?i)\bCREATE\s+TABLE\b'),
        re.compile(r'(?i)\bALTER\s+TABLE\s+[\w\.\"]+\s+ADD\b'),
        re.compile(r'(?i)\bINSERT\s+INTO\b'),
        re.compile(r'(?i)\bUPDATE\b'),
        re.compile(r'(?i)\bCREATE\s+INDEX\b'),
        re.compile(r'(?i)\bCREATE\s+SCHEMA\b'),
    ]

    @classmethod
    def classify_statement(cls, sql_text: str) -> OperationRiskLevel:
        """
        Classifies a single SQL DDL or DML statement by risk level.
        """
        sql = sql_text.strip()
        if not sql:
            return OperationRiskLevel.READ_ONLY

        for pattern in cls.DESTRUCTIVE_PATTERNS:
            if pattern.search(sql):
                return OperationRiskLevel.DESTRUCTIVE

        for pattern in cls.WRITE_PATTERNS:
            if pattern.search(sql):
                return OperationRiskLevel.WRITE

        return OperationRiskLevel.READ_ONLY

    @classmethod
    def classify_plan_operations(cls, plan_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Scans a TransformationPlan AST blueprint for any destructive operations.
        Returns a list of structured destructive operation descriptors requiring approval.
        """
        destructive_ops: List[Dict[str, Any]] = []

        # 1. Check global or table-level truncate_target flags
        if plan_data.get("truncate_target", False):
            destructive_ops.append({
                "target_table": "*",
                "operation_type": "truncate_all_targets",
                "risk_level": OperationRiskLevel.DESTRUCTIVE.value,
                "statement": "TRUNCATE TARGET TABLES",
            })

        for mapping in plan_data.get("table_mappings", []):
            tbl = mapping.get("target_table_name") or mapping.get("target_table") or "unknown"
            cleanup = str(mapping.get("cleanup_action", "")).upper()
            if mapping.get("truncate_target", False) or cleanup in ["TRUNCATE", "DROP", "DELETE"]:
                op_name = f"{cleanup.lower()}_table" if cleanup else "truncate_table"
                destructive_ops.append({
                    "target_table": tbl,
                    "operation_type": op_name,
                    "risk_level": OperationRiskLevel.DESTRUCTIVE.value,
                    "statement": f"{cleanup or 'TRUNCATE'} TABLE {tbl}",
                })


        # 2. Check pre_migration_ddl and post_migration_ddl statements
        for stage_key in ["pre_migration_ddl", "post_migration_ddl"]:
            ddl_list = plan_data.get(stage_key, [])
            if isinstance(ddl_list, list):
                for stmt in ddl_list:
                    if isinstance(stmt, str):
                        risk = cls.classify_statement(stmt)
                        if risk == OperationRiskLevel.DESTRUCTIVE:
                            destructive_ops.append({
                                "target_table": cls._extract_table_from_sql(stmt),
                                "operation_type": "destructive_ddl",
                                "risk_level": OperationRiskLevel.DESTRUCTIVE.value,
                                "statement": stmt,
                            })

        return destructive_ops

    @classmethod
    def _extract_table_from_sql(cls, sql: str) -> str:
        """Extracts the target table name from DDL if possible."""
        match = re.search(r'(?i)\b(?:TABLE|FROM)\s+([\"`]?[\w\.]+[\"`]?)', sql)
        if match:
            return match.group(1).replace('"', '').replace('`', '')
        return "target"


class DestructiveApprovalManager:
    """
    Manages explicit, version-bound approvals for destructive operations.
    Enforces the rule: If the plan version changes, previous approvals are invalidated.
    """

    @classmethod
    async def get_or_create_approval_request(
        cls,
        session: AsyncSession,
        plan_id: uuid.UUID,
        plan_version_number: int,
        target_table: str,
        operation_type: str,
        metadata_snapshot: Optional[Dict[str, Any]] = None,
    ) -> DestructiveOperationApproval:
        """
        Creates or retrieves an approval record for a specific plan version and destructive operation.
        """
        stmt = (
            select(DestructiveOperationApproval)
            .where(
                DestructiveOperationApproval.migration_plan_id == plan_id,
                DestructiveOperationApproval.plan_version_number == plan_version_number,
                DestructiveOperationApproval.target_table == target_table,
                DestructiveOperationApproval.operation_type == operation_type,
                DestructiveOperationApproval.is_valid == True,
            )
        )
        res = await session.execute(stmt)
        existing = res.scalar_one_or_none()
        if existing:
            return existing

        now = datetime.now(timezone.utc)
        approval = DestructiveOperationApproval(
            id=uuid.uuid4(),
            migration_plan_id=plan_id,
            plan_version_number=plan_version_number,
            target_table=target_table,
            operation_type=operation_type,
            risk_level=OperationRiskLevel.DESTRUCTIVE.value,
            is_approved=False,
            is_valid=True,
            metadata_snapshot=metadata_snapshot or {},
            created_at=now,
            updated_at=now,
        )
        session.add(approval)
        await session.flush()
        return approval

    @classmethod
    async def grant_approval(
        cls,
        session: AsyncSession,
        approval_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> DestructiveOperationApproval:
        """
        Explicitly approves a destructive operation, binding the approval to the user and timestamp.
        """
        stmt = select(DestructiveOperationApproval).where(DestructiveOperationApproval.id == approval_id)
        res = await session.execute(stmt)
        approval = res.scalar_one_or_none()
        if not approval:
            raise ValueError(f"Destructive operation approval '{approval_id}' not found.")

        if not approval.is_valid:
            raise ValueError(
                f"Cannot grant approval '{approval_id}': It has been invalidated because the plan version changed."
            )

        now = datetime.now(timezone.utc)
        approval.is_approved = True
        approval.approved_by_user_id = user_id
        approval.approved_at = now
        approval.updated_at = now

        # Emit audit event
        event = ExecutionEvent(
            id=uuid.uuid4(),
            event_id=uuid.uuid4(),
            migration_plan_id=approval.migration_plan_id,
            event_type=ExecutionEventType.DESTRUCTIVE_APPROVAL_GRANTED.value,
            actor_type="user",
            actor_id=str(user_id),
            timestamp=now,
            payload={
                "approval_id": str(approval.id),
                "plan_version": approval.plan_version_number,
                "target_table": approval.target_table,
                "operation_type": approval.operation_type,
            },
            schema_version=1,
        )
        session.add(event)
        await session.commit()
        await session.refresh(approval)
        return approval

    @classmethod
    async def reject_approval(
        cls,
        session: AsyncSession,
        approval_id: uuid.UUID,
        user_id: uuid.UUID,
        reason: str,
    ) -> DestructiveOperationApproval:
        """
        Rejects a destructive operation approval request.
        """
        stmt = select(DestructiveOperationApproval).where(DestructiveOperationApproval.id == approval_id)
        res = await session.execute(stmt)
        approval = res.scalar_one_or_none()
        if not approval:
            raise ValueError(f"Destructive operation approval '{approval_id}' not found.")

        now = datetime.now(timezone.utc)
        approval.is_approved = False
        approval.rejection_reason = reason
        approval.updated_at = now

        event = ExecutionEvent(
            id=uuid.uuid4(),
            event_id=uuid.uuid4(),
            migration_plan_id=approval.migration_plan_id,
            event_type=ExecutionEventType.DESTRUCTIVE_APPROVAL_REJECTED.value,
            actor_type="user",
            actor_id=str(user_id),
            timestamp=now,
            payload={
                "approval_id": str(approval.id),
                "reason": reason,
            },
            schema_version=1,
        )
        session.add(event)
        await session.commit()
        await session.refresh(approval)
        return approval

    @classmethod
    async def invalidate_all_for_plan(
        cls,
        session: AsyncSession,
        plan_id: uuid.UUID,
        reason: str = "Plan version changed or replanned",
    ) -> int:
        """
        Invalidates all existing destructive approvals for a plan when the plan AST is modified or replanned.
        Approval integrity rule: Approvals do NOT carry over to new plan versions.
        """
        stmt = (
            update(DestructiveOperationApproval)
            .where(
                DestructiveOperationApproval.migration_plan_id == plan_id,
                DestructiveOperationApproval.is_valid == True,
            )
            .values(is_valid=False, rejection_reason=f"Invalidated: {reason}")
        )
        res = await session.execute(stmt)
        count = res.rowcount

        if count > 0:
            event = ExecutionEvent(
                id=uuid.uuid4(),
                event_id=uuid.uuid4(),
                migration_plan_id=plan_id,
                event_type=ExecutionEventType.DESTRUCTIVE_APPROVAL_REVOKED.value,
                actor_type="system",
                actor_id="DestructiveApprovalManager",
                timestamp=datetime.now(timezone.utc),
                payload={"invalidated_count": count, "reason": reason},
                schema_version=1,
            )
            session.add(event)
            await session.commit()
            logger.info(f"Invalidated {count} destructive approvals for plan '{plan_id}': {reason}")

        return count

    @classmethod
    async def check_plan_destructive_compliance(
        cls,
        session: AsyncSession,
        plan: MigrationPlan,
        current_version_number: int,
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        """
        Verifies that every destructive operation in the plan has a valid, active approval
        matching the EXACT current_version_number.
        Returns:
            (is_compliant: bool, pending_or_unapproved_operations: list)
        """
        plan_data = plan.plan_data or {}
        destructive_ops = SafetyClassifier.classify_plan_operations(plan_data)

        if not destructive_ops:
            return True, []

        stmt = (
            select(DestructiveOperationApproval)
            .where(
                DestructiveOperationApproval.migration_plan_id == plan.id,
                DestructiveOperationApproval.plan_version_number == current_version_number,
                DestructiveOperationApproval.is_valid == True,
                DestructiveOperationApproval.is_approved == True,
            )
        )
        res = await session.execute(stmt)
        approved_records = list(res.scalars().all())
        approved_map = {(a.target_table, a.operation_type) for a in approved_records}

        unapproved = []
        for op in destructive_ops:
            key = (op["target_table"], op["operation_type"])
            if key not in approved_map:
                unapproved.append(op)

        return (len(unapproved) == 0), unapproved

    @classmethod
    async def list_approvals_for_plan(
        cls,
        session: AsyncSession,
        plan_id: uuid.UUID,
        include_invalid: bool = False,
    ) -> List[DestructiveOperationApproval]:
        """Lists all destructive operation approval records for a migration plan."""
        stmt = (
            select(DestructiveOperationApproval)
            .where(DestructiveOperationApproval.migration_plan_id == plan_id)
            .order_by(DestructiveOperationApproval.created_at.desc())
        )
        if not include_invalid:
            stmt = stmt.where(DestructiveOperationApproval.is_valid == True)
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def get_approval_by_id(
        cls,
        session: AsyncSession,
        approval_id: uuid.UUID,
    ) -> Optional[DestructiveOperationApproval]:
        """Fetches a specific approval record by ID."""
        stmt = select(DestructiveOperationApproval).where(DestructiveOperationApproval.id == approval_id)
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

