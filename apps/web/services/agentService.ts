import apiClient from './axios';
import {
  AgentCreatePayload,
  AgentDetailResponse,
  AgentDockerCommandResponse,
} from '../types/agent';

export const agentService = {
  /**
   * Register a new agent and its data source identities
   * Returns AgentDetailResponse including ready-to-run docker commands
   */
  async createAgent(payload: AgentCreatePayload): Promise<AgentDetailResponse> {
    const response = await apiClient.post<AgentDetailResponse>('/agents', payload);
    return response.data;
  },

  /**
   * Fetch agent details by ID with linked data sources
   */
  async getAgent(agentId: string): Promise<AgentDetailResponse> {
    const response = await apiClient.get<AgentDetailResponse>(`/agents/${agentId}`);
    return response.data;
  },

  /**
   * Fetch list of user's registered agents
   */
  async listAgents(): Promise<AgentDetailResponse[]> {
    const response = await apiClient.get<AgentDetailResponse[]>('/agents');
    return response.data;
  },

  /**
   * Generate/Retrieve Docker Run Commands and .env template for an existing agent from backend API
   */
  async getAgentDockerCommand(agentId: string): Promise<AgentDockerCommandResponse> {
    const response = await apiClient.get<AgentDockerCommandResponse>(`/agents/${agentId}/docker-command`);
    return response.data;
  },

  /**
   * Regenerates an existing agent's API token. The previous token is
   * immediately invalidated -- any running container using the old token
   * will fail authentication until redeployed with the new one. Returns
   * the new token ONCE, embedded in freshly generated Docker commands.
   */
  async regenerateAgentToken(agentId: string): Promise<AgentDetailResponse> {
    const response = await apiClient.post<AgentDetailResponse>(`/agents/${agentId}/regenerate-token`);
    return response.data;
  },

  /**
   * Delete an existing agent and un-link its attached data sources
   */
  async deleteAgent(agentId: string): Promise<void> {
    await apiClient.delete(`/agents/${agentId}`);
  },

  /**
   * Initialize WebSocket subscription for live Agent heartbeats
   */
  connectAgentWebSocket(
    agentId: string,
    jwtToken: string,
    onMessage: (data: any) => void,
    onError?: (err: Event) => void
  ): WebSocket {
    let wsBaseUrl = process.env.NEXT_PUBLIC_WS_URL;
    if (!wsBaseUrl || (wsBaseUrl.includes('localhost') && typeof window !== 'undefined' && window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1')) {
      if (typeof window !== 'undefined' && window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1') {
        const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        wsBaseUrl = `${proto}//${window.location.hostname}:8000/api/v1`;
      } else {
        wsBaseUrl = wsBaseUrl || 'ws://localhost:8000/api/v1';
      }
    }
    const ws = new WebSocket(`${wsBaseUrl}/agents/ws/${agentId}`);

    ws.onopen = () => {
      // Send secure JSON auth frame on open (EC-10)
      ws.send(JSON.stringify({ type: 'auth', token: jwtToken }));
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.event === 'AUTH_SUCCESS') {
          return;
        }
        onMessage(data);
      } catch {
        // Ignored raw strings
      }
    };

    if (onError) {
      ws.onerror = onError;
    }

    return ws;
  },
};

export default agentService;
