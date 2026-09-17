import { apiClient } from './axios';
import { ExecutionJobResponse } from '../types/execution';

export const executionService = {
  /**
   * Trigger execution of an approved migration plan on the assigned Docker agent (supports dry run)
   */
  async startPlanExecution(
    planId: string,
    options?: { chunk_size?: number; is_dry_run?: boolean; truncate_target?: boolean }
  ): Promise<ExecutionJobResponse> {
    const response = await apiClient.post<ExecutionJobResponse>(
      `/plans/${planId}/execute`,
      options || {}
    );
    return response.data;
  },

  /**
   * Cancel an active migration or dry-run execution job
   */
  async cancelExecution(jobId: string, reason?: string): Promise<ExecutionJobResponse> {
    const response = await apiClient.post<ExecutionJobResponse>(
      `/executions/${jobId}/cancel`,
      { reason }
    );
    return response.data;
  },

  /**
   * Fetch real-time progress details of a specific execution job by ID
   */
  async getExecutionDetails(jobId: string): Promise<ExecutionJobResponse> {
    const response = await apiClient.get<ExecutionJobResponse>(`/executions/${jobId}`);
    return response.data;
  },

  /**
   * List all execution jobs for a specific migration plan
   */
  async listPlanJobs(planId: string): Promise<ExecutionJobResponse[]> {
    const response = await apiClient.get<ExecutionJobResponse[]>(`/plans/${planId}/jobs`);
    return response.data;
  },

  /**
   * List all execution jobs for the current user
   */
  async listUserExecutions(): Promise<ExecutionJobResponse[]> {
    const response = await apiClient.get<ExecutionJobResponse[]>('/executions');
    return response.data;
  },

  /**
   * Trigger AI failure diagnosis synthesis for a failed execution job
   */
  async diagnoseJobFailure(jobId: string): Promise<ExecutionJobResponse> {
    const response = await apiClient.post<ExecutionJobResponse>(`/executions/${jobId}/diagnose`);
    return response.data;
  },
};

export default executionService;
