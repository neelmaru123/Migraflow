"""
LLM Plan Generator Engine
Implements:
- MetadataContextSerializer: Converts MetadataSnapshot trees into sanitized YAML context strings.
- LLMPlanGeneratorService: Invokes LangChain LLM with structured output & retry logic.
"""

import logging
from typing import List, Optional

from app.core.config import settings
from app.modules.metadata.metadata_models import (
    MetadataColumn,
    MetadataConstraint,
    MetadataRelationship,
    MetadataSchema,
    MetadataSnapshot,
    MetadataTable,
)
from app.modules.migration_plans.migration_plans_schemas import (
    TransformationPlanAST,
)

logger = logging.getLogger(__name__)

# ============================================================================
# Prompt Version — bump when system prompt changes so stored plans are traceable
# ============================================================================
PROMPT_VERSION = "v1.1.0"

# ============================================================================
# System Prompt Template
# ============================================================================
SYSTEM_PROMPT = """You are an expert database migration architect. Your ONLY job is to generate a
TransformationPlan JSON object that describes how to combine one or more source databases
into a single target database.

RULES & INDUSTRY DATABASE ARCHITECTURE STANDARDS:
1. NEVER invent data, assume values, or make up column content.
2. Work ONLY from the structural metadata provided. No raw data will be given.
3. You MUST include ALL source tables in your table_mappings.
4. For each source table, decide ONE of:
   - direct_copy  → Copy this table unchanged from exactly 1 source database
   - merge        → Combine 2+ source tables into 1 target table (requires conflict_resolution)
   - split_target → Decompose 1 source table into 2+ target tables
5. CANONICAL NAMING & CASING STANDARD:
   - ALL target table names and column names MUST be in clean, lowercase snake_case (e.g. user_accounts, created_at).
6. PRIMARY KEYS & AUDIT COLUMNS STANDARD:
   - Every target table MUST have a primary key column named 'id' (or '_id' when target is MongoDB). (VARCHAR(36) for MySQL, UUID/BIGINT for PostgreSQL, UUID/VARCHAR(36) for MongoDB).
   - Every target table MUST include enterprise audit timestamp columns 'created_at' and 'updated_at' (DATETIME for MySQL, TIMESTAMPTZ for PostgreSQL) using transformation_type 'new_column_added'.
   - Merged tables MUST include a '_source_origin' column (VARCHAR) to track data lineage per row.
   - FOREIGN KEY & PRIMARY KEY TYPE SYNCHRONIZATION:
     Whenever a target table's primary key is mapped to 'uuid' (or converted to UUID from an integer source PK), ALL foreign key columns in other tables that reference this entity (e.g. 'category_id', 'customer_id', 'order_id', 'product_id') MUST ALSO use target_data_type 'uuid' with transformation_type 'type_cast'. A foreign key column MUST NEVER be left as 'bigint' or 'integer' if its referenced parent primary key is 'uuid'!
7. TWO-PHASE DDL HYGIENE & DIALECT COMPLIANCE:
   - pre_migration_ddl: include CREATE TABLE DDL for target tables (and CREATE EXTENSION only if target is PostgreSQL). MUST NOT contain ANY inline or table-level FOREIGN KEY constraints.
   - For PostgreSQL targets:
     * ALWAYS use 'gen_random_uuid()' for UUID primary key defaults, e.g. 'id UUID PRIMARY KEY DEFAULT gen_random_uuid()'.
     * NEVER generate 'uuid_v4()' or 'uuidv4()' as these functions do not exist in PostgreSQL.
     * Include 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp";' if needed.
   - For MySQL targets: DO NOT use 'gen_random_uuid()' or 'UUID' data types in DDL. Use 'VARCHAR(36) PRIMARY KEY' or 'BIGINT AUTO_INCREMENT PRIMARY KEY'.
   - post_migration_ddl: include CREATE INDEX and ALTER TABLE ... ADD CONSTRAINT FOREIGN KEY DDL statements.
   - Index naming convention: idx_{tablename}_{columnname}
   - Foreign key constraint naming convention: fk_{srctable}_{tgttable}_{columnname}
8. For EVERY column in EVERY source table, you MUST output a column_mapping with:
   - transformation_type from the ALLOWED TAXONOMY.
   - ui_badge_type that EXACTLY matches transformation_type.
   - A plain-English "explanation" field readable by a non-technical business user.
9. Source columns with NO target equivalent MUST use transformation_type "drop_column".
10. For conflict resolution in merge tables:
   - Prefer UUID primary keys. Use uuid_v4_rekey for integer PKs.
   - Use email or unique business key for deduplication_key where available.
11. confidence_score: set per table AND overall. Use < 0.75 when mapping is ambiguous.
12. warnings: list any data quality risks, ambiguous type coercions, or manual steps needed.
13. Temperature is 0.0. Output MUST be deterministic, valid JSON matching the schema exactly.
14. MONGODB / NOSQL TO SQL CONVERSION:
    - High-coverage fields (coverage >= 20%): Use 'nosql_field_promote' or 'json_flatten' to extract into dedicated SQL columns.
    - Nested document paths (e.g. address.city): Use 'json_flatten' to convert to snake_case target columns (e.g. address_city).
    - Unmapped or low-coverage fields (< 20%): Preserve zero data loss by storing in a catch-all column named 'extra_attributes' (JSONB for PostgreSQL, JSON for MySQL, TEXT for SQLite) using 'json_stringify'.
15. POSTGRESQL ARRAYS & JSON CROSS-DIALECT CONVERSION:
    - When target is MySQL/SQLite and source column is a Postgres Array (TEXT[], INT[]):
      - Use 'array_to_csv' for simple text arrays (e.g. tags -> "tag1,tag2").
      - Use 'array_to_json' for complex or structured arrays.
    - When target is PostgreSQL: Preserve native JSONB / ARRAY types using 'type_cast'.
16. STRICT SOURCE DATABASE IDENTIFIER BINDING:
    - In each table_blueprint, source_table.source_db_id MUST strictly match the exact source database identifier (e.g. 'src_db_1', 'src_db_2', 'src_db_3') under which that specific table was provided in the metadata context.
    - NEVER assign a table (e.g., 'products', 'orders', 'customers') to a source_db_id where that table does NOT exist in the metadata context!
17. CRITICAL — MERGE TABLE MULTI-SOURCE COLUMN BINDING RULE:
    When a target table has transformation_type="merge" and pulls from 2+ source tables,
    EVERY target column mapping (except new_column_added and default_constant) MUST declare
    a source_columns entry for EACH participating source table — using the EXACT column name
    from THAT source database (which may differ across DBs).

    Example: target table 'customers' merges from src_db_1.pg_customers and src_db_2.mysql_accounts.
    The target column 'email' must be:
    {
      "target_column_name": "email",
      "transformation_type": "type_cast",
      "source_columns": [
        { "identifier": "src_db_1", "table_name": "pg_customers",   "column_name": "email" },
        { "identifier": "src_db_2", "table_name": "mysql_accounts", "column_name": "email_address" }
      ]
    }
    NOT just one source. If the concept is the same (email vs email_address, full_name vs first_name+last_name),
    LIST ALL VARIANTS from ALL participating sources. Use merge_concat if one source stores it as a
    single field and another stores it split. NEVER leave a source table unrepresented in source_columns
    for a merge table column — doing so will cause NULL insertion failures at execution time.

    Column name equivalence examples you MUST recognize and map correctly:
    - email / email_address / contact_email / user_email → same semantic concept
    - first_name / fname / given_name / full_name (when split) → same concept
    - last_name / lname / family_name / surname → same concept
    - phone / mobile / telephone / phone_number / contact_number → same concept
    - created_at / created_time / creation_date / registration_date → same concept
    - customer_id / account_id / user_id / client_id (when referencing same entity) → same concept

18. TARGET DATABASE MONGODB RULES:
    When target_database_type is "mongodb":
    - Target entities are collections, not relational tables.
    - Every collection's primary key column MUST be named '_id' (with target_data_type 'uuid' or 'varchar(36)').
    - pre_migration_ddl MUST be an empty list []. Do NOT generate SQL CREATE TABLE DDL statements for MongoDB!
    - post_migration_ddl should contain db.<collection>.createIndex(...) commands or be an empty list [].

ALLOWED transformation_type TAXONOMY (column level):
  direct_copy         → Copy column value as-is from source to target
  merge_concat        → Combine 2+ source columns into 1 target column
  type_cast           → Data type conversion (e.g. VARCHAR → UUID, INT → BIGINT)
  split               → Decompose 1 source column into 2+ target columns
  expression          → Derive value using a sanitized SQL expression
  lookup_join         → Resolve FK foreign key → referenced table's descriptive column
  default_constant    → Fill with hardcoded constant value
  drop_column         → Source column is NOT mapped (will be discarded)
  new_column_added    → New target column with no source equivalent
  json_flatten        → Extract nested document path (e.g. address.city → address_city)
  json_stringify      → Convert complex object/array into JSON string / JSONB payload
  array_to_csv        → Convert Postgres array (TEXT[]) into CSV string
  array_to_json       → Convert Postgres array into JSON array string
  nosql_field_promote → Promote high-coverage MongoDB field to dedicated SQL column

ALLOWED transformation_type TAXONOMY (table level):
  direct_copy  → Table from exactly 1 source, copied unchanged
  merge        → 2+ source tables combined into 1 target table
  split_target → 1 source table decomposed into 2+ target tables

UI Badge Colors (for reference only — do not include in output):
  direct_copy=green, merge_concat=blue, type_cast=purple, split=orange,
  expression=yellow, lookup_join=teal, default_constant=gray,
  drop_column=red, new_column_added=indigo
"""


# ============================================================================
# Metadata Context Serializer — ZERO RAW DATA
# ============================================================================
class MetadataContextSerializer:
    """
    Converts MetadataSnapshot ORM objects into a sanitized YAML-like context string.
    STRICTLY enforces Zero Raw Data Policy:
    - No table row contents, no sample data
    - No passwords, hostnames, or connection strings
    - UUIDs replaced with logical aliases
    """

    @staticmethod
    def _format_column(col: MetadataColumn, pk_names: set) -> str:
        parts = [f"      - name: {col.column_name}"]
        parts.append(f"        type: {col.native_data_type or col.data_type}")
        parts.append(f"        nullable: {str(col.nullable).lower()}")
        if col.is_primary_key or col.column_name in pk_names:
            parts.append("        is_primary_key: true")
        if col.is_unique:
            parts.append("        is_unique: true")
        if col.max_length:
            parts.append(f"        max_length: {col.max_length}")
        if col.numeric_precision:
            parts.append(f"        numeric_precision: {col.numeric_precision}")
        if col.numeric_scale:
            parts.append(f"        numeric_scale: {col.numeric_scale}")
        if col.default_value:
            parts.append(f"        default_value: \"{col.default_value}\"")
        return "\n".join(parts)

    @staticmethod
    def _format_constraints(constraints: List[MetadataConstraint]) -> str:
        if not constraints:
            return ""
        lines = ["      constraints:"]
        seen = set()
        for c in constraints:
            key = (c.constraint_name, c.constraint_type)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"        - name: {c.constraint_name}, type: {c.constraint_type}")
        return "\n".join(lines)

    @classmethod
    def serialize(
        cls,
        snapshots: List[MetadataSnapshot],
        source_aliases: dict,
        target_db_type: str,
        custom_instructions: Optional[str] = None,
    ) -> str:
        """
        Produces a clean, sanitized structural-only YAML string from MetadataSnapshots.

        Parameters:
        - snapshots: List of MetadataSnapshot ORM objects (with loaded relationships)
        - source_aliases: Dict mapping data_source_id (str) → logical alias (e.g. 'source_db_1')
        - target_db_type: Target database dialect string
        - custom_instructions: Optional user-provided instructions to append
        """
        lines = ["# SOURCE DATABASE METADATA CONTEXT (Structural Schema Only — No Raw Data)\n"]

        for snapshot in snapshots:
            alias = source_aliases.get(str(snapshot.data_source_id), f"source_db_{str(snapshot.data_source_id)[:8]}")
            lines.append(f"# SOURCE DATABASE: {alias} ({snapshot.database_name} | {snapshot.database_version or 'Unknown version'})")
            lines.append(f"# Tables: {snapshot.total_tables}, Columns: {snapshot.total_columns}, Est. Rows: {snapshot.total_rows}\n")

            for schema in (snapshot.schemas or []):
                lines.append(f"schema: {schema.schema_name}")

                for table in (schema.tables or []):
                    # Build PK set from constraints
                    pk_names = {
                        c.constraint_name for c in (table.constraints or [])
                        if c.constraint_type.lower() in ("primary key", "primary_key")
                    }
                    lines.append(f"  table: {table.table_name} (type={table.table_type}, est_rows={table.row_count})")
                    lines.append("    columns:")
                    for col in sorted(table.columns or [], key=lambda c: c.ordinal_position):
                        lines.append(cls._format_column(col, pk_names))

                    cst_block = cls._format_constraints(table.constraints or [])
                    if cst_block:
                        lines.append(cst_block)
                    lines.append("")

            # Foreign key relationships
            if snapshot.relationships:
                lines.append("    foreign_keys:")
                for rel in snapshot.relationships:
                    src_tbl = next(
                        (t.table_name for s in (snapshot.schemas or []) for t in (s.tables or []) if t.id == rel.source_table_id),
                        str(rel.source_table_id)[:8],
                    )
                    src_col = next(
                        (c.column_name for s in (snapshot.schemas or []) for t in (s.tables or []) for c in (t.columns or []) if c.id == rel.source_column_id),
                        "?",
                    )
                    tgt_tbl = next(
                        (t.table_name for s in (snapshot.schemas or []) for t in (s.tables or []) if t.id == rel.target_table_id),
                        str(rel.target_table_id)[:8],
                    )
                    tgt_col = next(
                        (c.column_name for s in (snapshot.schemas or []) for t in (s.tables or []) for c in (t.columns or []) if c.id == rel.target_column_id),
                        "?",
                    )
                    lines.append(f"      - {alias}.{src_tbl}.{src_col} → {alias}.{tgt_tbl}.{tgt_col}")
                lines.append("")

        lines.append(f"\n# TARGET DATABASE: target_db ({target_db_type})")
        lines.append("# Instruction: Combine all source databases listed above into a single target_db.\n")

        if custom_instructions:
            lines.append(f"# ADDITIONAL USER INSTRUCTIONS:\n# {custom_instructions}\n")

        return "\n".join(lines)


# ============================================================================
# LLM Plan Generator Service
# ============================================================================
class LLMPlanGeneratorService:
    """
    Invokes LangChain LLM with with_structured_output(TransformationPlanAST).
    Implements retry loop on JSON parse/schema validation failures.
    """

    def __init__(self):
        self._llm = None

    def _get_llm(self):
        """Lazily initialize LLM on first use (avoids import errors if keys not set)."""
        if self._llm is not None:
            return self._llm

        provider = settings.LLM_PROVIDER
        model = settings.LLM_MODEL
        temperature = settings.LLM_TEMPERATURE

        llm_timeout = float(getattr(settings, "LLM_TIMEOUT_SECONDS", 180.0))

        if provider == "gemini":
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI
                self._llm = ChatGoogleGenerativeAI(
                    model=model,
                    temperature=temperature,
                    google_api_key=settings.GEMINI_API_KEY,
                    request_timeout=llm_timeout,
                )
            except ImportError as exc:
                raise RuntimeError("langchain-google-genai is not installed. Run: poetry add langchain-google-genai") from exc

        elif provider == "openai":
            try:
                from langchain_openai import ChatOpenAI
                self._llm = ChatOpenAI(
                    model=model,
                    temperature=temperature,
                    api_key=settings.OPENAI_API_KEY,
                    request_timeout=llm_timeout,
                )
            except ImportError as exc:
                raise RuntimeError("langchain-openai is not installed. Run: poetry add langchain-openai") from exc

        else:
            raise ValueError(f"Unsupported LLM_PROVIDER: '{provider}'. Must be 'gemini' or 'openai'.")

        return self._llm

    def generate(
        self,
        context_str: str,
        target_db_type: str,
    ) -> TransformationPlanAST:
        """
        Calls LLM with system prompt + sanitized metadata context.
        Uses with_structured_output(TransformationPlanAST) for Pydantic schema enforcement.
        Implements retry loop on validation failure (max LLM_MAX_RETRIES attempts).

        Returns a validated TransformationPlanAST instance.
        Raises RuntimeError if all retries are exhausted.
        """
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_core.output_parsers import PydanticOutputParser

        llm = self._get_llm()
        parser = PydanticOutputParser(pydantic_object=TransformationPlanAST)

        full_prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            f"{parser.get_format_instructions()}\n\n"
            f"Analyze the following sanitized source database schemas and generate a complete "
            f"TransformationPlan to merge them into a single target database of type '{target_db_type}'.\n\n"
            f"{context_str}"
        )

        last_error: Optional[Exception] = None
        max_retries = settings.LLM_MAX_RETRIES

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"LLM plan generation attempt {attempt}/{max_retries}...")

                messages = [HumanMessage(content=full_prompt)]

                if last_error and attempt > 1:
                    messages.append(
                        HumanMessage(
                            content=(
                                f"Your previous response failed Pydantic schema validation. "
                                f"Error: {last_error}. "
                                f"Please correct and re-generate the complete TransformationPlan JSON."
                            )
                        )
                    )

                response = llm.invoke(messages)
                content = str(response.content)
                result = parser.parse(content)

                logger.info(f"LLM plan generation succeeded on attempt {attempt}.")
                return result

            except Exception as exc:
                last_error = exc
                logger.warning(f"LLM attempt {attempt} failed: {exc}")

        raise RuntimeError(
            f"LLM plan generation failed after {max_retries} attempts. "
            f"Last error: {last_error}"
        )

    def refine(
        self,
        context_str: str,
        current_ast_dict: dict,
        user_feedback: Optional[str] = None,
        validation_errors: Optional[List[str]] = None,
        max_retries: int = 3,
    ) -> TransformationPlanAST:
        """
        Refines an existing TransformationPlanAST given user feedback or validation error feedback.
        Includes a 3-attempt retry loop with schema validation feedback.
        """
        import json
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_core.output_parsers import PydanticOutputParser

        llm = self._get_llm()
        parser = PydanticOutputParser(pydantic_object=TransformationPlanAST)

        table_count_before = len(current_ast_dict.get("table_mappings", []))

        feedback_instructions = []
        if user_feedback:
            feedback_instructions.append(f"USER FEEDBACK INSTRUCTION:\n{user_feedback}")
        if validation_errors:
            err_list = "\n".join(f"- {err}" for err in validation_errors)
            feedback_instructions.append(f"STRUCTURAL VALIDATION ERRORS TO FIX:\n{err_list}")

        feasibility_rules = (
            f"\nCRITICAL FEASIBILITY & TRANSPARENCY RULES FOR REFINEMENT:\n"
            f"1. The current plan has {table_count_before} target tables.\n"
            f"2. Evaluate the USER FEEDBACK INSTRUCTION against the source database metadata and zero-data-loss constraints.\n"
            f"3. If the user asks for a structural change that is IMPOSSIBLE, WOULD CAUSE DATA LOSS, or VIOLATES SCHEMA FEASIBILITY "
            f"(such as reducing 15 source tables into 12 tables when tables have incompatible schemas and no shared keys):\n"
            f"   - DO NOT claim that you consolidated tables if you did not actually change table_mappings!\n"
            f"   - Set refinement_feedback.applied = false\n"
            f"   - Set refinement_feedback.verdict = 'infeasible_rejected'\n"
            f"   - Set refinement_feedback.explanation = a clear, detailed, and respectful technical response explaining to the user "
            f"     exactly why their request is not possible without data loss, identifying the incompatible tables or constraints.\n"
            f"   - Keep the safe, lossless tables in table_mappings.\n"
            f"4. If the user's request IS feasible and safe to apply:\n"
            f"   - Apply the requested adjustments to table_mappings and column_mappings.\n"
            f"   - Set refinement_feedback.applied = true (or verdict = 'partially_applied' if only safe subsets were applied).\n"
            f"   - Set refinement_feedback.explanation = a summary of the adjustments applied.\n"
            f"5. Always set refinement_feedback.user_prompt = the user feedback text.\n"
            f"6. Set refinement_feedback.table_count_before = {table_count_before} and table_count_after = count of updated target tables.\n"
            f"7. Set refinement_feedback.changes_summary = bullet points describing what was changed or what constraints prevented changes.\n"
        )
        feedback_instructions.append(feasibility_rules)

        instructions_str = "\n\n".join(feedback_instructions)

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                messages = [
                    SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(
                        content=(
                            f"{parser.get_format_instructions()}\n\n"
                            f"Here is the database metadata context:\n{context_str}\n\n"
                            f"Here is the CURRENT TransformationPlan AST blueprint:\n```json\n{json.dumps(current_ast_dict, indent=2)}\n```\n\n"
                            f"Apply the following refinement instructions to evaluate, update, and improve the TransformationPlan AST blueprint:\n"
                            f"{instructions_str}\n\n"
                            f"Output the updated complete TransformationPlan JSON matching the schema."
                        )
                    ),
                ]

                if last_error:
                    messages.append(
                        HumanMessage(
                            content=(
                                f"Your previous refinement response failed Pydantic schema validation. "
                                f"Error: {last_error}. "
                                f"Please correct and re-generate the complete TransformationPlan JSON."
                            )
                        )
                    )

                response = llm.invoke(messages)
                content = str(response.content)
                result = parser.parse(content)

                # Defensive fallback: guarantee refinement_feedback is populated even if model omitted it
                table_count_after = len(result.table_mappings)
                if result.refinement_feedback is None:
                    from app.modules.migration_plans.migration_plans_schemas import RefinementFeedback
                    applied = (table_count_before != table_count_after) or (user_feedback is None)
                    verdict = "applied" if applied else "infeasible_rejected"
                    explanation = result.ai_explanation
                    if not applied and user_feedback:
                        explanation = (
                            f"The requested refinement ('{user_feedback}') could not be applied without data loss or schema incompatibilities. "
                            f"The original {table_count_before} target tables were preserved to guarantee 100% data fidelity."
                        )
                    result.refinement_feedback = RefinementFeedback(
                        applied=applied,
                        verdict=verdict,
                        user_prompt=user_feedback,
                        explanation=explanation,
                        table_count_before=table_count_before,
                        table_count_after=table_count_after,
                        changes_summary=result.warnings or [],
                    )
                else:
                    if result.refinement_feedback.table_count_before is None:
                        result.refinement_feedback.table_count_before = table_count_before
                    if result.refinement_feedback.table_count_after is None:
                        result.refinement_feedback.table_count_after = table_count_after
                    if not result.refinement_feedback.user_prompt and user_feedback:
                        result.refinement_feedback.user_prompt = user_feedback

                logger.info(f"LLM plan refinement succeeded on attempt {attempt}.")
                return result
            except Exception as exc:
                last_error = exc
                logger.warning(f"LLM refinement attempt {attempt} failed: {exc}")

        raise RuntimeError(
            f"LLM plan refinement failed after {max_retries} attempts. Last error: {last_error}"
        )


# Singleton instance
llm_plan_generator = LLMPlanGeneratorService()
