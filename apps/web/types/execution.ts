/**
 * TypeScript definitions matching execution_schemas.py in apps/api/app/modules/execution
 */

export type ExecutionStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled' | string;

export interface AIDiagnosisPayload {
  summary: string;
  root_cause_category: string;
  is_user_environment_issue: boolean;
  fix_steps: string[];
  copyable_fix_command?: string;
  raw_error_snippet?: string;
}

export interface ExecutionStartOptions {
  chunk_size?: number;
  is_dry_run?: boolean;
  truncate_target?: boolean;
}

export interface ExecutionCancelRequest {
  reason?: string;
}

export interface ExecutionJobResponse {
  id: string;
  migration_plan_id: string;
  agent_id?: string | null;
  status: ExecutionStatus;
  is_dry_run?: boolean;
  truncate_target?: boolean;
  progress: number;
  total_rows: number;
  processed_rows: number;
  successful_rows: number;
  failed_rows: number;
  skipped_rows?: number;
  current_table?: string | null;
  current_stage?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  error_message?: string | null;
  ai_diagnosis?: AIDiagnosisPayload | null;
  created_at: string;
  updated_at: string;
  target_tables_with_existing_data?: Array<{ table_name: string; existing_row_count: number }>;
}

export interface ExecutionProgressUpdate {
  status?: ExecutionStatus;
  progress?: number;
  total_rows?: number;
  processed_rows?: number;
  successful_rows?: number;
  failed_rows?: number;
  skipped_rows?: number;
  current_table?: string;
  current_stage?: string;
  error_message?: string;
}
