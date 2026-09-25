"""
Evaluation Dataset — 15 Representative Migration Scenarios
Phase 6: Provides synthetic metadata, AST benchmarks, and ground-truth specs for repeatable AI evaluation.
Strictly uses synthetic metadata; NEVER contains real customer or user data.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.modules.migration_plans.migration_plans_schemas import (
    ColumnMapping,
    ConflictResolution,
    SourceColumnRef,
    SourceTableRef,
    TableMapping,
    TransformationPlanAST,
)


class ScenarioGroundTruth(BaseModel):
    """Ground truth assertions for evaluating planning, recovery, and safety."""
    expected_table_count: int
    expected_mappings: Dict[str, str] = Field(default_factory=dict)  # source_table -> target_table
    expected_type_casts: Dict[str, str] = Field(default_factory=dict)  # column -> target_type
    expected_is_valid: bool = True
    expected_error_substrings: List[str] = Field(default_factory=list)
    expected_warning_substrings: List[str] = Field(default_factory=list)
    expected_failure_category: Optional[str] = None
    expected_recovery_action: Optional[str] = None  # retry, recover, replan, ask_user, fail
    expected_risk_level: str = "write"  # read_only, write, destructive
    min_confidence: float = 0.80
    max_confidence: float = 1.00
    requires_approval: bool = False
    requires_disambiguation: bool = False


class EvaluationScenario(BaseModel):
    """Represents a standardized, self-contained migration scenario for testing."""
    scenario_id: str
    scenario_name: str
    description: str
    category: str  # planning, recovery, failure_injection, edge_case
    source_metadata: Dict[str, Any]  # Synthetic source DB reflection schema
    target_metadata: Optional[Dict[str, Any]] = None  # Synthetic target DB schema if pre-existing
    ground_truth: ScenarioGroundTruth
    baseline_ast: TransformationPlanAST
    sample_records: List[Dict[str, Any]] = Field(default_factory=list)


def build_evaluation_scenarios() -> List[EvaluationScenario]:
    """Builds and returns the comprehensive 15-scenario evaluation dataset."""
    scenarios: List[EvaluationScenario] = []

    # =========================================================================
    # 1. Simple One-to-One PostgreSQL Migration
    # =========================================================================
    s1 = EvaluationScenario(
        scenario_id="scenario_01_simple_postgres",
        scenario_name="Simple 1:1 PostgreSQL Migration",
        description="Direct copy of a standard relational table with identical types and single primary key.",
        category="planning",
        source_metadata={
            "dialect": "postgresql",
            "alias": "src_pg",
            "tables": {
                "customers": {
                    "id": {"type": "integer", "is_pk": True, "nullable": False},
                    "name": {"type": "varchar(255)", "is_pk": False, "nullable": False},
                    "email": {"type": "varchar(255)", "is_pk": False, "nullable": False},
                    "created_at": {"type": "timestamp with time zone", "is_pk": False, "nullable": False},
                }
            },
        },
        ground_truth=ScenarioGroundTruth(
            expected_table_count=1,
            expected_mappings={"customers": "customers_v2"},
            expected_type_casts={"id": "int", "name": "varchar(255)", "email": "varchar(255)"},
            expected_is_valid=True,
            min_confidence=0.95,
            expected_risk_level="write",
        ),
        baseline_ast=TransformationPlanAST(
            table_mappings=[
                TableMapping(
                    target_table_name="customers_v2",
                    source_tables=[SourceTableRef(identifier="src_pg", table_name="customers")],
                    transformation_type="direct_copy",
                    column_mappings=[
                        ColumnMapping(
                            target_column_name="id",
                            target_data_type="int",
                            is_primary_key=True,
                            transformation_type="direct_copy",
                            source_columns=[SourceColumnRef(identifier="src_pg", table_name="customers", column_name="id")],
                        ),
                        ColumnMapping(
                            target_column_name="name",
                            target_data_type="varchar(255)",
                            transformation_type="direct_copy",
                            source_columns=[SourceColumnRef(identifier="src_pg", table_name="customers", column_name="name")],
                        ),
                        ColumnMapping(
                            target_column_name="email",
                            target_data_type="varchar(255)",
                            transformation_type="direct_copy",
                            source_columns=[SourceColumnRef(identifier="src_pg", table_name="customers", column_name="email")],
                        ),
                        ColumnMapping(
                            target_column_name="created_at",
                            target_data_type="timestamptz",
                            transformation_type="direct_copy",
                            source_columns=[SourceColumnRef(identifier="src_pg", table_name="customers", column_name="created_at")],
                        ),
                    ],
                )
            ],
            pre_migration_ddl=["CREATE TABLE customers_v2 (id INT PRIMARY KEY, name VARCHAR(255), email VARCHAR(255), created_at TIMESTAMPTZ);"],
            confidence_score=0.98,
        ),
        sample_records=[{"id": 1, "name": "Acme Corp", "email": "contact@acme.com", "created_at": "2026-01-01T00:00:00Z"}],
    )
    scenarios.append(s1)

    # =========================================================================
    # 2. MySQL -> PostgreSQL Type Mapping
    # =========================================================================
    s2 = EvaluationScenario(
        scenario_id="scenario_02_mysql_to_postgres",
        scenario_name="MySQL to PostgreSQL Type Mapping",
        description="Type dialect mapping: tinyint(1) to boolean, datetime to timestamptz, int unsigned to bigint.",
        category="planning",
        source_metadata={
            "dialect": "mysql",
            "alias": "src_mysql",
            "tables": {
                "products": {
                    "id": {"type": "int unsigned", "is_pk": True, "nullable": False},
                    "is_active": {"type": "tinyint(1)", "is_pk": False, "nullable": False},
                    "description": {"type": "longtext", "is_pk": False, "nullable": True},
                    "price": {"type": "decimal(10,2)", "is_pk": False, "nullable": False},
                    "last_updated": {"type": "datetime", "is_pk": False, "nullable": False},
                }
            },
        },
        ground_truth=ScenarioGroundTruth(
            expected_table_count=1,
            expected_mappings={"products": "products"},
            expected_type_casts={
                "id": "bigint",
                "is_active": "boolean",
                "description": "text",
                "price": "numeric(10,2)",
                "last_updated": "timestamptz",
            },
            expected_is_valid=True,
            min_confidence=0.92,
        ),
        baseline_ast=TransformationPlanAST(
            table_mappings=[
                TableMapping(
                    target_table_name="products",
                    source_tables=[SourceTableRef(identifier="src_mysql", table_name="products")],
                    transformation_type="type_cast",
                    column_mappings=[
                        ColumnMapping(
                            target_column_name="id",
                            target_data_type="bigint",
                            is_primary_key=True,
                            transformation_type="type_cast",
                            source_columns=[SourceColumnRef(identifier="src_mysql", table_name="products", column_name="id")],
                        ),
                        ColumnMapping(
                            target_column_name="is_active",
                            target_data_type="boolean",
                            transformation_type="type_cast",
                            source_columns=[SourceColumnRef(identifier="src_mysql", table_name="products", column_name="is_active")],
                        ),
                        ColumnMapping(
                            target_column_name="description",
                            target_data_type="text",
                            transformation_type="type_cast",
                            source_columns=[SourceColumnRef(identifier="src_mysql", table_name="products", column_name="description")],
                        ),
                        ColumnMapping(
                            target_column_name="price",
                            target_data_type="numeric(10,2)",
                            transformation_type="direct_copy",
                            source_columns=[SourceColumnRef(identifier="src_mysql", table_name="products", column_name="price")],
                        ),
                        ColumnMapping(
                            target_column_name="last_updated",
                            target_data_type="timestamptz",
                            transformation_type="type_cast",
                            source_columns=[SourceColumnRef(identifier="src_mysql", table_name="products", column_name="last_updated")],
                        ),
                    ],
                )
            ],
            confidence_score=0.94,
        ),
    )
    scenarios.append(s2)

    # =========================================================================
    # 3. MongoDB -> Relational
    # =========================================================================
    s3 = EvaluationScenario(
        scenario_id="scenario_03_mongodb_to_relational",
        scenario_name="MongoDB to Relational",
        description="Converts NoSQL collection with _id, nested objects, and arrays to relational columns and JSONB.",
        category="planning",
        source_metadata={
            "dialect": "mongodb",
            "alias": "src_mongo",
            "tables": {
                "orders": {
                    "_id": {"type": "objectid", "is_pk": True, "nullable": False},
                    "customer_id": {"type": "string", "is_pk": False, "nullable": False},
                    "shipping_address": {"type": "object", "is_pk": False, "nullable": False},
                    "status": {"type": "string", "is_pk": False, "nullable": False},
                    "created_at": {"type": "date", "is_pk": False, "nullable": False},
                }
            },
        },
        ground_truth=ScenarioGroundTruth(
            expected_table_count=1,
            expected_mappings={"orders": "orders"},
            expected_type_casts={"id": "uuid", "shipping_address": "jsonb", "created_at": "timestamptz"},
            expected_is_valid=True,
            min_confidence=0.88,
        ),
        baseline_ast=TransformationPlanAST(
            table_mappings=[
                TableMapping(
                    target_table_name="orders",
                    source_tables=[SourceTableRef(identifier="src_mongo", table_name="orders")],
                    transformation_type="unflatten",
                    column_mappings=[
                        ColumnMapping(
                            target_column_name="id",
                            target_data_type="uuid",
                            is_primary_key=True,
                            transformation_type="type_cast",
                            source_columns=[SourceColumnRef(identifier="src_mongo", table_name="orders", column_name="_id")],
                        ),
                        ColumnMapping(
                            target_column_name="customer_id",
                            target_data_type="varchar(64)",
                            transformation_type="direct_copy",
                            source_columns=[SourceColumnRef(identifier="src_mongo", table_name="orders", column_name="customer_id")],
                        ),
                        ColumnMapping(
                            target_column_name="shipping_address",
                            target_data_type="jsonb",
                            transformation_type="type_cast",
                            source_columns=[SourceColumnRef(identifier="src_mongo", table_name="orders", column_name="shipping_address")],
                        ),
                        ColumnMapping(
                            target_column_name="status",
                            target_data_type="varchar(50)",
                            transformation_type="direct_copy",
                            source_columns=[SourceColumnRef(identifier="src_mongo", table_name="orders", column_name="status")],
                        ),
                        ColumnMapping(
                            target_column_name="created_at",
                            target_data_type="timestamptz",
                            transformation_type="type_cast",
                            source_columns=[SourceColumnRef(identifier="src_mongo", table_name="orders", column_name="created_at")],
                        ),
                    ],
                )
            ],
            confidence_score=0.90,
        ),
    )
    scenarios.append(s3)

    # =========================================================================
    # 4. Multiple Sources -> One Target
    # =========================================================================
    s4 = EvaluationScenario(
        scenario_id="scenario_04_multiple_sources_to_one",
        scenario_name="Multiple Sources into Unified Target",
        description="Merges CRM users and Billing accounts into a single target accounts table with complete source bindings.",
        category="planning",
        source_metadata={
            "dialect": "multi_source",
            "sources": {
                "crm_db": {
                    "users": {
                        "user_id": {"type": "integer", "is_pk": True},
                        "email": {"type": "varchar(255)"},
                        "full_name": {"type": "varchar(255)"},
                    }
                },
                "billing_db": {
                    "accounts": {
                        "account_id": {"type": "integer", "is_pk": True},
                        "email": {"type": "varchar(255)"},
                        "status": {"type": "varchar(50)"},
                    }
                },
            },
        },
        ground_truth=ScenarioGroundTruth(
            expected_table_count=1,
            expected_is_valid=True,
            min_confidence=0.85,
        ),
        baseline_ast=TransformationPlanAST(
            table_mappings=[
                TableMapping(
                    target_table_name="unified_accounts",
                    source_tables=[
                        SourceTableRef(identifier="crm_db", table_name="users"),
                        SourceTableRef(identifier="billing_db", table_name="accounts"),
                    ],
                    transformation_type="merge",
                    column_mappings=[
                        ColumnMapping(
                            target_column_name="id",
                            target_data_type="uuid",
                            is_primary_key=True,
                            transformation_type="new_column_added",
                        ),
                        ColumnMapping(
                            target_column_name="email",
                            target_data_type="varchar(255)",
                            transformation_type="direct_copy",
                            source_columns=[
                                SourceColumnRef(identifier="crm_db", table_name="users", column_name="email"),
                                SourceColumnRef(identifier="billing_db", table_name="accounts", column_name="email"),
                            ],
                        ),
                        ColumnMapping(
                            target_column_name="full_name",
                            target_data_type="varchar(255)",
                            transformation_type="direct_copy",
                            source_columns=[
                                SourceColumnRef(identifier="crm_db", table_name="users", column_name="full_name"),
                            ],
                        ),
                    ],
                )
            ],
            confidence_score=0.88,
        ),
    )
    scenarios.append(s4)

    # =========================================================================
    # 5. Conflicting Column Names
    # =========================================================================
    s5 = EvaluationScenario(
        scenario_id="scenario_05_conflicting_column_names",
        scenario_name="Conflicting Column Names & Disambiguation",
        description="Source contains first_name, last_name and conflicting name columns mapped to target display_name.",
        category="planning",
        source_metadata={
            "dialect": "postgresql",
            "alias": "src_conflicts",
            "tables": {
                "contacts": {
                    "id": {"type": "integer", "is_pk": True},
                    "first_name": {"type": "varchar(100)"},
                    "last_name": {"type": "varchar(100)"},
                    "name": {"type": "varchar(200)"},
                }
            },
        },
        ground_truth=ScenarioGroundTruth(
            expected_table_count=1,
            expected_is_valid=True,
            min_confidence=0.86,
        ),
        baseline_ast=TransformationPlanAST(
            table_mappings=[
                TableMapping(
                    target_table_name="contacts",
                    source_tables=[SourceTableRef(identifier="src_conflicts", table_name="contacts")],
                    transformation_type="direct_copy",
                    column_mappings=[
                        ColumnMapping(
                            target_column_name="id",
                            target_data_type="int",
                            is_primary_key=True,
                            source_columns=[SourceColumnRef(identifier="src_conflicts", table_name="contacts", column_name="id")],
                        ),
                        ColumnMapping(
                            target_column_name="display_name",
                            target_data_type="varchar(255)",
                            transformation_type="merge_concat",
                            expression_template="{first_name} || ' ' || {last_name}",
                            source_columns=[
                                SourceColumnRef(identifier="src_conflicts", table_name="contacts", column_name="first_name"),
                                SourceColumnRef(identifier="src_conflicts", table_name="contacts", column_name="last_name"),
                            ],
                        ),
                        ColumnMapping(
                            target_column_name="raw_name",
                            target_data_type="varchar(200)",
                            transformation_type="direct_copy",
                            source_columns=[SourceColumnRef(identifier="src_conflicts", table_name="contacts", column_name="name")],
                        ),
                    ],
                )
            ],
            confidence_score=0.90,
        ),
    )
    scenarios.append(s5)

    # =========================================================================
    # 6. Different Primary Keys (Composite -> Surrogate UUID)
    # =========================================================================
    s6 = EvaluationScenario(
        scenario_id="scenario_06_different_primary_keys",
        scenario_name="Different Primary Keys: Composite to UUID",
        description="Source composite PK (order_id, item_id) transformed into surrogate UUID PK with natural key uniqueness.",
        category="planning",
        source_metadata={
            "dialect": "postgresql",
            "alias": "src_pk",
            "tables": {
                "order_line_items": {
                    "order_id": {"type": "integer", "is_pk": True},
                    "item_id": {"type": "integer", "is_pk": True},
                    "quantity": {"type": "integer"},
                }
            },
        },
        ground_truth=ScenarioGroundTruth(
            expected_table_count=1,
            expected_is_valid=True,
            min_confidence=0.88,
        ),
        baseline_ast=TransformationPlanAST(
            table_mappings=[
                TableMapping(
                    target_table_name="order_line_items",
                    source_tables=[SourceTableRef(identifier="src_pk", table_name="order_line_items")],
                    transformation_type="direct_copy",
                    column_mappings=[
                        ColumnMapping(
                            target_column_name="id",
                            target_data_type="uuid",
                            is_primary_key=True,
                            transformation_type="new_column_added",
                        ),
                        ColumnMapping(
                            target_column_name="order_id",
                            target_data_type="int",
                            source_columns=[SourceColumnRef(identifier="src_pk", table_name="order_line_items", column_name="order_id")],
                        ),
                        ColumnMapping(
                            target_column_name="item_id",
                            target_data_type="int",
                            source_columns=[SourceColumnRef(identifier="src_pk", table_name="order_line_items", column_name="item_id")],
                        ),
                        ColumnMapping(
                            target_column_name="quantity",
                            target_data_type="int",
                            source_columns=[SourceColumnRef(identifier="src_pk", table_name="order_line_items", column_name="quantity")],
                        ),
                    ],
                )
            ],
            post_migration_ddl=["CREATE UNIQUE INDEX uq_order_item ON order_line_items (order_id, item_id);"],
            confidence_score=0.92,
        ),
    )
    scenarios.append(s6)

    # =========================================================================
    # 7. Nullable Mismatch (Nullable -> NOT NULL)
    # =========================================================================
    s7 = EvaluationScenario(
        scenario_id="scenario_07_nullable_mismatch",
        scenario_name="Nullable Mismatch & Default Coalesce",
        description="Source column is NULLABLE but target has NOT NULL constraint; requires COALESCE or default.",
        category="planning",
        source_metadata={
            "dialect": "postgresql",
            "alias": "src_null",
            "tables": {
                "user_profiles": {
                    "id": {"type": "integer", "is_pk": True},
                    "phone": {"type": "varchar(50)", "nullable": True},
                }
            },
        },
        ground_truth=ScenarioGroundTruth(
            expected_table_count=1,
            expected_is_valid=True,
            min_confidence=0.85,
        ),
        baseline_ast=TransformationPlanAST(
            table_mappings=[
                TableMapping(
                    target_table_name="user_profiles",
                    source_tables=[SourceTableRef(identifier="src_null", table_name="user_profiles")],
                    column_mappings=[
                        ColumnMapping(
                            target_column_name="id",
                            target_data_type="int",
                            is_primary_key=True,
                            source_columns=[SourceColumnRef(identifier="src_null", table_name="user_profiles", column_name="id")],
                        ),
                        ColumnMapping(
                            target_column_name="phone",
                            target_data_type="varchar(50)",
                            transformation_type="expression_sql",
                            expression_template="COALESCE({phone}, 'N/A')",
                            source_columns=[SourceColumnRef(identifier="src_null", table_name="user_profiles", column_name="phone")],
                        ),
                    ],
                )
            ],
            confidence_score=0.90,
        ),
    )
    scenarios.append(s7)

    # =========================================================================
    # 8. Duplicate Records (Deduplication Key Strategy)
    # =========================================================================
    s8 = EvaluationScenario(
        scenario_id="scenario_08_duplicate_records",
        scenario_name="Duplicate Records Deduplication Strategy",
        description="Source contains duplicate entries; requires conflict resolution with deduplication key.",
        category="recovery",
        source_metadata={
            "dialect": "postgresql",
            "alias": "src_dups",
            "tables": {
                "audit_events": {
                    "event_id": {"type": "varchar(64)"},
                    "user_id": {"type": "integer"},
                    "timestamp": {"type": "timestamptz"},
                }
            },
        },
        ground_truth=ScenarioGroundTruth(
            expected_table_count=1,
            expected_is_valid=True,
            expected_dedup_key="event_id",
            min_confidence=0.88,
        ),
        baseline_ast=TransformationPlanAST(
            table_mappings=[
                TableMapping(
                    target_table_name="audit_events",
                    source_tables=[SourceTableRef(identifier="src_dups", table_name="audit_events")],
                    conflict_resolution=ConflictResolution(
                        strategy="last_write_wins",
                        deduplication_key="event_id",
                        resolution_scope="table",
                    ),
                    column_mappings=[
                        ColumnMapping(
                            target_column_name="event_id",
                            target_data_type="varchar(64)",
                            is_primary_key=True,
                            source_columns=[SourceColumnRef(identifier="src_dups", table_name="audit_events", column_name="event_id")],
                        ),
                        ColumnMapping(
                            target_column_name="user_id",
                            target_data_type="int",
                            source_columns=[SourceColumnRef(identifier="src_dups", table_name="audit_events", column_name="user_id")],
                        ),
                        ColumnMapping(
                            target_column_name="timestamp",
                            target_data_type="timestamptz",
                            source_columns=[SourceColumnRef(identifier="src_dups", table_name="audit_events", column_name="timestamp")],
                        ),
                    ],
                )
            ],
            confidence_score=0.92,
        ),
    )
    scenarios.append(s8)

    # =========================================================================
    # 9. Foreign Key Dependency (Topological Ordering & Post-DDL)
    # =========================================================================
    s9 = EvaluationScenario(
        scenario_id="scenario_09_foreign_key_dependency",
        scenario_name="Foreign Key Dependency & Two-Phase Hygiene",
        description="Parent departments and child employees. FK constraint must strictly reside in post_migration_ddl.",
        category="planning",
        source_metadata={
            "dialect": "postgresql",
            "alias": "src_rel",
            "tables": {
                "departments": {
                    "id": {"type": "integer", "is_pk": True},
                    "dept_name": {"type": "varchar(100)"},
                },
                "employees": {
                    "id": {"type": "integer", "is_pk": True},
                    "name": {"type": "varchar(100)"},
                    "department_id": {"type": "integer"},
                },
            },
        },
        ground_truth=ScenarioGroundTruth(
            expected_table_count=2,
            expected_is_valid=True,
            min_confidence=0.92,
        ),
        baseline_ast=TransformationPlanAST(
            table_mappings=[
                TableMapping(
                    target_table_name="departments",
                    source_tables=[SourceTableRef(identifier="src_rel", table_name="departments")],
                    column_mappings=[
                        ColumnMapping(target_column_name="id", target_data_type="int", is_primary_key=True,
                                      source_columns=[SourceColumnRef(identifier="src_rel", table_name="departments", column_name="id")]),
                        ColumnMapping(target_column_name="dept_name", target_data_type="varchar(100)",
                                      source_columns=[SourceColumnRef(identifier="src_rel", table_name="departments", column_name="dept_name")]),
                    ],
                ),
                TableMapping(
                    target_table_name="employees",
                    source_tables=[SourceTableRef(identifier="src_rel", table_name="employees")],
                    column_mappings=[
                        ColumnMapping(target_column_name="id", target_data_type="int", is_primary_key=True,
                                      source_columns=[SourceColumnRef(identifier="src_rel", table_name="employees", column_name="id")]),
                        ColumnMapping(target_column_name="name", target_data_type="varchar(100)",
                                      source_columns=[SourceColumnRef(identifier="src_rel", table_name="employees", column_name="name")]),
                        ColumnMapping(target_column_name="department_id", target_data_type="int",
                                      source_columns=[SourceColumnRef(identifier="src_rel", table_name="employees", column_name="department_id")]),
                    ],
                ),
            ],
            post_migration_ddl=["ALTER TABLE employees ADD CONSTRAINT fk_emp_dept FOREIGN KEY (department_id) REFERENCES departments(id);"],
            confidence_score=0.95,
        ),
    )
    scenarios.append(s9)

    # =========================================================================
    # 10. Schema Drift Detection
    # =========================================================================
    s10 = EvaluationScenario(
        scenario_id="scenario_10_schema_drift",
        scenario_name="Schema Drift Midway Detection",
        description="Source schema drops or renames a mapped column; router must categorize as SCHEMA_CHANGED and trigger REPLAN.",
        category="recovery",
        source_metadata={
            "dialect": "postgresql",
            "alias": "src_drift",
            "tables": {
                "invoices": {
                    "id": {"type": "integer", "is_pk": True},
                    "invoice_status": {"type": "varchar(50)"},  # Column renamed from 'status'
                }
            },
        },
        ground_truth=ScenarioGroundTruth(
            expected_table_count=1,
            expected_is_valid=False,
            expected_error_substrings=["Source column 'status' does not exist"],
            expected_failure_category="schema_changed",
            expected_recovery_action="replan",
            min_confidence=0.50,
        ),
        baseline_ast=TransformationPlanAST(
            table_mappings=[
                TableMapping(
                    target_table_name="invoices",
                    source_tables=[SourceTableRef(identifier="src_drift", table_name="invoices")],
                    column_mappings=[
                        ColumnMapping(target_column_name="id", target_data_type="int", is_primary_key=True,
                                      source_columns=[SourceColumnRef(identifier="src_drift", table_name="invoices", column_name="id")]),
                        ColumnMapping(target_column_name="status", target_data_type="varchar(50)",
                                      source_columns=[SourceColumnRef(identifier="src_drift", table_name="invoices", column_name="status")]),
                    ],
                )
            ],
            confidence_score=0.70,
        ),
    )
    scenarios.append(s10)

    # =========================================================================
    # 11. Missing Source Table (Validation Rejection)
    # =========================================================================
    s11 = EvaluationScenario(
        scenario_id="scenario_11_missing_source_table",
        scenario_name="Missing Source Table Rejection",
        description="AST refers to a non-existent table; deterministic validator must fail with unambiguous error.",
        category="planning",
        source_metadata={
            "dialect": "postgresql",
            "alias": "src_missing",
            "tables": {
                "existing_table": {"id": {"type": "integer", "is_pk": True}}
            },
        },
        ground_truth=ScenarioGroundTruth(
            expected_table_count=1,
            expected_is_valid=False,
            expected_error_substrings=["does not exist in source database"],
            min_confidence=0.40,
        ),
        baseline_ast=TransformationPlanAST(
            table_mappings=[
                TableMapping(
                    target_table_name="nonexistent_target",
                    source_tables=[SourceTableRef(identifier="src_missing", table_name="ghost_table")],
                    column_mappings=[
                        ColumnMapping(target_column_name="id", target_data_type="int",
                                      source_columns=[SourceColumnRef(identifier="src_missing", table_name="ghost_table", column_name="id")]),
                    ],
                )
            ],
            confidence_score=0.50,
        ),
    )
    scenarios.append(s11)

    # =========================================================================
    # 12. Unsupported Data Type Detection
    # =========================================================================
    s12 = EvaluationScenario(
        scenario_id="scenario_12_unsupported_data_type",
        scenario_name="Unsupported Data Type Detection",
        description="Flags proprietary binary or spatial types and provides text/json fallback representation.",
        category="edge_case",
        source_metadata={
            "dialect": "postgresql",
            "alias": "src_unsupported",
            "tables": {
                "geodata": {
                    "id": {"type": "integer", "is_pk": True},
                    "boundary": {"type": "geometry(Polygon,4326)"},
                }
            },
        },
        ground_truth=ScenarioGroundTruth(
            expected_table_count=1,
            expected_is_valid=True,
            min_confidence=0.80,
        ),
        baseline_ast=TransformationPlanAST(
            table_mappings=[
                TableMapping(
                    target_table_name="geodata",
                    source_tables=[SourceTableRef(identifier="src_unsupported", table_name="geodata")],
                    column_mappings=[
                        ColumnMapping(target_column_name="id", target_data_type="int", is_primary_key=True,
                                      source_columns=[SourceColumnRef(identifier="src_unsupported", table_name="geodata", column_name="id")]),
                        ColumnMapping(target_column_name="boundary", target_data_type="text", transformation_type="type_cast",
                                      source_columns=[SourceColumnRef(identifier="src_unsupported", table_name="geodata", column_name="boundary")]),
                    ],
                )
            ],
            confidence_score=0.85,
        ),
    )
    scenarios.append(s12)

    # =========================================================================
    # 13. Transformation Requirement (Expression Parsing)
    # =========================================================================
    s13 = EvaluationScenario(
        scenario_id="scenario_13_transformation_requirement",
        scenario_name="Transformation Requirement & Expression Template",
        description="String address parsed into street, city, state via regex expression template.",
        category="planning",
        source_metadata={
            "dialect": "postgresql",
            "alias": "src_expr",
            "tables": {
                "locations": {
                    "id": {"type": "integer", "is_pk": True},
                    "full_address": {"type": "varchar(500)"},
                }
            },
        },
        ground_truth=ScenarioGroundTruth(
            expected_table_count=1,
            expected_is_valid=True,
            min_confidence=0.86,
        ),
        baseline_ast=TransformationPlanAST(
            table_mappings=[
                TableMapping(
                    target_table_name="locations",
                    source_tables=[SourceTableRef(identifier="src_expr", table_name="locations")],
                    column_mappings=[
                        ColumnMapping(target_column_name="id", target_data_type="int", is_primary_key=True,
                                      source_columns=[SourceColumnRef(identifier="src_expr", table_name="locations", column_name="id")]),
                        ColumnMapping(target_column_name="street", target_data_type="varchar(255)", transformation_type="expression_sql",
                                      expression_template="SPLIT_PART({full_address}, ',', 1)",
                                      source_columns=[SourceColumnRef(identifier="src_expr", table_name="locations", column_name="full_address")]),
                        ColumnMapping(target_column_name="city", target_data_type="varchar(100)", transformation_type="expression_sql",
                                      expression_template="TRIM(SPLIT_PART({full_address}, ',', 2))",
                                      source_columns=[SourceColumnRef(identifier="src_expr", table_name="locations", column_name="full_address")]),
                    ],
                )
            ],
            confidence_score=0.91,
        ),
    )
    scenarios.append(s13)

    # =========================================================================
    # 14. Destructive Target Operation (Safety Approval Required)
    # =========================================================================
    s14 = EvaluationScenario(
        scenario_id="scenario_14_destructive_target_operation",
        scenario_name="Destructive Target Operation Governance",
        description="Plan requests TRUNCATE TABLE CASCADE on target; safety classifier must classify as DESTRUCTIVE.",
        category="edge_case",
        source_metadata={
            "dialect": "postgresql",
            "alias": "src_destruct",
            "tables": {
                "logs": {"id": {"type": "integer", "is_pk": True}, "entry": {"type": "text"}}
            },
        },
        ground_truth=ScenarioGroundTruth(
            expected_table_count=1,
            expected_is_valid=True,
            expected_risk_level="destructive",
            requires_approval=True,
            min_confidence=0.88,
        ),
        baseline_ast=TransformationPlanAST(
            table_mappings=[
                TableMapping(
                    target_table_name="logs",
                    source_tables=[SourceTableRef(identifier="src_destruct", table_name="logs")],
                    cleanup_action="TRUNCATE",
                    column_mappings=[
                        ColumnMapping(target_column_name="id", target_data_type="int", is_primary_key=True,
                                      source_columns=[SourceColumnRef(identifier="src_destruct", table_name="logs", column_name="id")]),
                        ColumnMapping(target_column_name="entry", target_data_type="text",
                                      source_columns=[SourceColumnRef(identifier="src_destruct", table_name="logs", column_name="entry")]),
                    ],
                )
            ],
            pre_migration_ddl=["TRUNCATE TABLE logs CASCADE;"],
            confidence_score=0.90,
        ),
    )
    scenarios.append(s14)

    # =========================================================================
    # 15. Ambiguous Mapping (Multiple Candidate Source Tables)
    # =========================================================================
    s15 = EvaluationScenario(
        scenario_id="scenario_15_ambiguous_mapping",
        scenario_name="Ambiguous Mapping & Confidence Calibration",
        description="Multiple source tables with overlapping names; plan must calibrate confidence and raise warning.",
        category="edge_case",
        source_metadata={
            "dialect": "postgresql",
            "alias": "src_ambiguous",
            "tables": {
                "users": {"id": {"type": "int", "is_pk": True}, "email": {"type": "varchar(255)"}},
                "legacy_users": {"id": {"type": "int", "is_pk": True}, "email": {"type": "varchar(255)"}},
                "user_archives": {"id": {"type": "int", "is_pk": True}, "email": {"type": "varchar(255)"}},
            },
        },
        ground_truth=ScenarioGroundTruth(
            expected_table_count=1,
            expected_is_valid=True,
            min_confidence=0.70,
            max_confidence=0.85,
            requires_disambiguation=True,
        ),
        baseline_ast=TransformationPlanAST(
            table_mappings=[
                TableMapping(
                    target_table_name="users",
                    source_tables=[SourceTableRef(identifier="src_ambiguous", table_name="users")],
                    column_mappings=[
                        ColumnMapping(target_column_name="id", target_data_type="int", is_primary_key=True,
                                      source_columns=[SourceColumnRef(identifier="src_ambiguous", table_name="users", column_name="id")]),
                        ColumnMapping(target_column_name="email", target_data_type="varchar(255)",
                                      source_columns=[SourceColumnRef(identifier="src_ambiguous", table_name="users", column_name="email")]),
                    ],
                )
            ],
            confidence_score=0.82,  # Properly calibrated below 0.85 due to ambiguity
        ),
    )
    scenarios.append(s15)

    return scenarios


EVALUATION_DATASET: List[EvaluationScenario] = build_evaluation_scenarios()
SCENARIO_BY_ID: Dict[str, EvaluationScenario] = {s.scenario_id: s for s in EVALUATION_DATASET}
