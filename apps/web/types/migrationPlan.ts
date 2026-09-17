/**
 * TypeScript definitions matching migration_plans_schemas.py in apps/api/app/modules/migration_plans
 */

export type ColumnTransformationType =
  | 'direct_copy'
  | 'merge_concat'
  | 'type_cast'
  | 'split'
  | 'expression'
  | 'lookup_join'
  | 'default_constant'
  | 'drop_column'
  | 'new_column_added'
  | 'json_flatten'
  | 'json_stringify'
  | 'array_to_csv'
  | 'array_to_json'
  | 'nosql_field_promote';

export type TableTransformationType = 'direct_copy' | 'merge' | 'split_target';

export interface SourceColumnRef {
  identifier: string;
  schema_name: string;
  table_name: string;
  column_name: string;
}

export interface SourceTableRef {
  identifier: string;
  schema_name: string;
  table_name: string;
  join_type?: 'primary' | 'union_merge' | 'left_join' | 'right_join';
}

export interface ConflictResolutionSpec {
  primary_key_strategy: 'uuid_v4_rekey' | 'prefix_id' | 'autoincrement_offset' | 'keep_original';
  deduplication_key?: string | null;
  deduplication_strategy?: 'first_wins' | 'last_updated_wins' | 'merge_all' | null;
}

export interface ColumnMappingSpec {
  target_column_name?: string | null;
  target_data_type?: string | null;
  nullable?: boolean | null;
  is_primary_key: boolean;
  transformation_type: ColumnTransformationType;
  ui_badge_type: ColumnTransformationType;
  source_columns: SourceColumnRef[];
  expression_template?: string | null;
  constant_value?: string | null;
  explanation: string;
}

export interface TableMappingSpec {
  target_table_name: string;
  transformation_type: TableTransformationType;
  ai_reasoning: string;
  confidence_score: number;
  conflict_resolution?: ConflictResolutionSpec | null;
  source_tables: SourceTableRef[];
  column_mappings: ColumnMappingSpec[];
}

export interface RefinementFeedback {
  applied: boolean;
  verdict: 'applied' | 'partially_applied' | 'infeasible_rejected';
  user_prompt?: string | null;
  explanation: string;
  table_count_before?: number | null;
  table_count_after?: number | null;
  changes_summary?: string[];
}

export interface TransformationPlanAST {
  target_database_type: string;
  ai_explanation: string;
  confidence_score: number;
  warnings: string[];
  table_mappings: TableMappingSpec[];
  pre_migration_ddl: string[];
  post_migration_ddl: string[];
  refinement_feedback?: RefinementFeedback | null;
}

export interface TargetDatabaseConfig {
  database_type: string;
  identifier?: string | null;
  custom_instructions?: string | null;
}

export interface PlanGenerationRequest {
  agent_id: string;
  target_config: TargetDatabaseConfig;
}

export interface PlanRefineRequest {
  user_feedback: string;
}

export interface PlanGenerationJobResponse {
  task_id: string;
  agent_id: string;
  plan_id: string;
  status: 'processing';
  message: string;
}

export interface PlanGenerationStatusResponse {
  task_id: string | null;
  agent_id: string;
  plan_id?: string | null;
  status: 'idle' | 'processing' | 'completed' | 'failed';
  target_database_type?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  elapsed_seconds?: number | null;
  error?: string | null;
  plan?: PlanDetailResponse | null;
}

export interface PlanRefinementJobResponse {
  task_id: string;
  plan_id: string;
  status: 'processing';
  message: string;
}

export interface PlanRefinementStatusResponse {
  task_id: string | null;
  plan_id: string;
  status: 'idle' | 'processing' | 'completed' | 'failed';
  user_prompt?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  elapsed_seconds?: number | null;
  error?: string | null;
  plan?: PlanDetailResponse | null;
}

export interface PlanValidationResultResponse {
  is_valid: boolean;
  errors: string[];
  warnings: string[];
  explanation: string;
}

export interface PlanResponse {
  id: string;
  agent_id?: string | null;
  status: 'draft' | 'edited' | 'completed' | 'invalid' | 'draft_failed' | string;
  ai_model?: string | null;
  confidence_score: number;
  is_valid?: boolean;
  created_at: string;
  updated_at: string;
}

export interface PlanDetailResponse extends PlanResponse {
  plan_data: TransformationPlanAST;
  target_config?: TargetDatabaseConfig | null;
  prompt_version?: string | null;
  validation_errors?: Record<string, any> | null;
  validation_warnings?: string[] | null;
}

export interface PlanVersionListItem {
  id: string;
  migration_plan_id: string;
  version_number: number;
  edit_type: 'initial_ai_generation' | 'llm_refinement' | 'manual_ast_edit' | 'version_restored' | string;
  user_feedback?: string | null;
  is_valid?: boolean;
  confidence_score?: number;
  created_at: string;
}

export interface PlanVersionDetailResponse extends PlanVersionListItem {
  plan_data: TransformationPlanAST;
  validation_errors?: Record<string, any> | null;
}

