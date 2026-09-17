import { apiClient } from './axios';
import {
  PlanDetailResponse,
  PlanGenerationJobResponse,
  PlanGenerationStatusResponse,
  PlanRefinementJobResponse,
  PlanRefinementStatusResponse,
  PlanResponse,
  PlanValidationResultResponse,
  TargetDatabaseConfig,
} from '../types/migrationPlan';

export const planService = {
  /**
   * Create and generate an AI migration plan for an agent (synchronous legacy)
   */
  async createPlan(agentId: string, targetConfig: TargetDatabaseConfig): Promise<PlanDetailResponse> {
    const response = await apiClient.post<PlanDetailResponse>(
      '/plans/generate',
      {
        agent_id: agentId,
        target_config: targetConfig,
      },
      { timeout: 180000 } // 3 minutes timeout for complex LLM generation graph (EC-09)
    );
    return response.data;
  },

  /**
   * Create and generate an AI migration plan asynchronously in the background (returns immediately)
   */
  async startGeneration(agentId: string, targetConfig: TargetDatabaseConfig): Promise<PlanGenerationJobResponse> {
    const response = await apiClient.post<PlanGenerationJobResponse>(
      '/plans/generate-async',
      {
        agent_id: agentId,
        target_config: targetConfig,
      }
    );
    return response.data;
  },

  /**
   * Poll status of an ongoing or completed plan generation for an agent
   */
  async getGenerationStatus(agentId: string): Promise<PlanGenerationStatusResponse> {
    const response = await apiClient.get<PlanGenerationStatusResponse>(`/plans/agent/${agentId}/generation-status`);
    return response.data;
  },

  /**
   * Fetch all migration plans for the current authenticated user
   */
  async listPlans(): Promise<PlanResponse[]> {
    const response = await apiClient.get<PlanResponse[]>('/plans');
    return response.data;
  },

  /**
   * Fetch details of a specific migration plan by ID
   */
  async getPlan(planId: string): Promise<PlanDetailResponse> {
    const response = await apiClient.get<PlanDetailResponse>(`/plans/${planId}`);
    return response.data;
  },

  /**
   * Update plan AST manually
   */
  async updatePlan(planId: string, planData: Record<string, any>): Promise<PlanDetailResponse> {
    const response = await apiClient.put<PlanDetailResponse>(`/plans/${planId}`, planData);
    return response.data;
  },

  /**
   * Refine an existing migration plan using natural language prompt feedback (synchronous)
   */
  async refinePlan(planId: string, userFeedback: string): Promise<PlanDetailResponse> {
    const response = await apiClient.post<PlanDetailResponse>(
      `/plans/${planId}/refine`,
      {
        user_feedback: userFeedback,
      },
      { timeout: 360000 } // 6 minutes timeout for backward-compatibility
    );
    return response.data;
  },

  /**
   * Refine an existing migration plan asynchronously in the background (returns immediately)
   */
  async startRefinement(planId: string, userFeedback: string): Promise<PlanRefinementJobResponse> {
    const response = await apiClient.post<PlanRefinementJobResponse>(
      `/plans/${planId}/refine-async`,
      {
        user_feedback: userFeedback,
      }
    );
    return response.data;
  },

  /**
   * Poll status of an ongoing or completed plan refinement
   */
  async getRefinementStatus(planId: string): Promise<PlanRefinementStatusResponse> {
    const response = await apiClient.get<PlanRefinementStatusResponse>(`/plans/${planId}/refine/status`);
    return response.data;
  },

  /**
   * Validate plan feasibility against latest data source snapshots
   */
  async validatePlan(planId: string): Promise<PlanValidationResultResponse> {
    const response = await apiClient.post<PlanValidationResultResponse>(`/plans/${planId}/validate`);
    return response.data;
  },

  /**
   * Approve plan for execution
   */
  async approvePlan(planId: string): Promise<PlanDetailResponse> {
    const response = await apiClient.post<PlanDetailResponse>(`/plans/${planId}/approve`);
    return response.data;
  },

  /**
   * Fetch all version snapshots for a migration plan
   */
  async listPlanVersions(planId: string): Promise<import('../types/migrationPlan').PlanVersionListItem[]> {
    const response = await apiClient.get<import('../types/migrationPlan').PlanVersionListItem[]>(`/plans/${planId}/versions`);
    return response.data;
  },

  /**
   * Fetch full AST details for a specific version snapshot
   */
  async getPlanVersion(planId: string, versionNumber: number): Promise<import('../types/migrationPlan').PlanVersionDetailResponse> {
    const response = await apiClient.get<import('../types/migrationPlan').PlanVersionDetailResponse>(`/plans/${planId}/versions/${versionNumber}`);
    return response.data;
  },

  /**
   * Restore plan AST to a historical version snapshot
   */
  async restorePlanVersion(planId: string, versionNumber: number): Promise<PlanDetailResponse> {
    const response = await apiClient.post<PlanDetailResponse>(`/plans/${planId}/versions/${versionNumber}/restore`);
    return response.data;
  },
};

export default planService;
