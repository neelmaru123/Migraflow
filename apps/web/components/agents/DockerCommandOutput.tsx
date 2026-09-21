'use client';

import React, { useState, useEffect } from 'react';
import { AgentDetailResponse, AgentDockerCommandResponse } from '../../types/agent';
import {
  Check,
  Copy,
  Terminal,
  FileCode,
  ArrowRight,
  RefreshCw,
  Monitor,
  Code,
} from 'lucide-react';
import toast from 'react-hot-toast';
import agentService from '../../services/agentService';
import { ConnectionDetails, substituteConnectionPlaceholders } from '../../lib/dockerCommandUtils';

interface DockerCommandOutputProps {
  agent: AgentDetailResponse;
  dockerCmdData?: AgentDockerCommandResponse | null;
  onReset: () => void;
  sources?: { identifier: string }[];
  destination?: { identifier: string } | null;
  connectionDetailsByIdentifier?: Record<string, ConnectionDetails>;
}

export const DockerCommandOutput: React.FC<DockerCommandOutputProps> = ({
  agent,
  dockerCmdData,
  onReset,
  sources = [],
  destination = null,
  connectionDetailsByIdentifier = {},
}) => {
  const [activeTab, setActiveTab] = useState<'bash' | 'powershell' | 'oneline' | 'env'>('bash');
  const [copied, setCopied] = useState(false);
  const [agentStatus, setAgentStatus] = useState<string>(agent.status || 'offline');

  // Resolved commands from props or agent detail
  const rawBashCmd =
    dockerCmdData?.docker_command ||
    agent.docker_command ||
    `docker run -d --name agent_${agent.agent_identifier} -e AGENT_TOKEN="${
      agent.api_token || '<YOUR_AGENT_TOKEN>'
    }" -e BACKEND_URL="${typeof window !== 'undefined' ? `${window.location.protocol}//${window.location.hostname}:8000` : 'http://localhost:8000'}" data-migration-agent:latest`;

  const rawPowershellCmd =
    dockerCmdData?.docker_command_powershell ||
    agent.docker_command_powershell ||
    rawBashCmd;

  const rawOnelineCmd =
    dockerCmdData?.docker_command_oneline ||
    agent.docker_command_oneline ||
    rawBashCmd.replace(/\\\n\s*/g, ' ');

  const rawEnvTemplate =
    dockerCmdData?.env_template ||
    agent.env_template ||
    `AGENT_TOKEN=${agent.api_token || '<YOUR_AGENT_TOKEN>'}\nBACKEND_URL=http://localhost:8000`;

  const bashCmd = substituteConnectionPlaceholders(rawBashCmd, sources, destination, connectionDetailsByIdentifier);
  const powershellCmd = substituteConnectionPlaceholders(rawPowershellCmd, sources, destination, connectionDetailsByIdentifier);
  const onelineCmd = substituteConnectionPlaceholders(rawOnelineCmd, sources, destination, connectionDetailsByIdentifier);
  const envTemplate = substituteConnectionPlaceholders(rawEnvTemplate, sources, destination, connectionDetailsByIdentifier);

  // Subscribe to real-time WebSocket for live heartbeat ping
  useEffect(() => {
    const token = agent.api_token || localStorage.getItem('access_token');
    if (!agent.id) return;

    let ws: WebSocket | null = null;
    try {
      if (token) {
        ws = agentService.connectAgentWebSocket(agent.id, token, (eventData) => {
          if (eventData.status) {
            setAgentStatus(eventData.status);
            if (eventData.status === 'online') {
              toast.success('🎉 Agent container connected and online!');
            }
          }
        });
      }
    } catch {
      // WebSocket fallback
    }

    // Polling fallback every 4 seconds to check status
    const pollInterval = setInterval(async () => {
      try {
        const updated = await agentService.getAgent(agent.id);
        if (updated && updated.status !== agentStatus) {
          setAgentStatus(updated.status);
          if (updated.status === 'online') {
            toast.success('🎉 Agent container connected and online!');
          }
        }
      } catch {
        // Ignored
      }
    }, 4000);

    return () => {
      if (ws) ws.close();
      clearInterval(pollInterval);
    };
  }, [agent.id, agent.api_token]);

  const getCommandToDisplay = (): string => {
    switch (activeTab) {
      case 'powershell':
        return powershellCmd;
      case 'oneline':
        return onelineCmd;
      case 'env':
        return envTemplate;
      case 'bash':
      default:
        return bashCmd;
    }
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(getCommandToDisplay());
    setCopied(true);
    toast.success('Copied to clipboard!');
    setTimeout(() => setCopied(false), 2000);
  };

  const isOnline = agentStatus === 'online';

  return (
    <div className="w-full max-w-5xl mx-auto space-y-8 animate-fadeIn">
      {/* Header */}
      <div className="text-center space-y-3">
        <div className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-none text-[11px] font-mono font-bold uppercase tracking-widest bg-sky-400/10 text-sky-400 border border-sky-400/30">
          STEP 3 OF 3: DEPLOY AGENT CONTAINER
        </div>
        <h2 className="text-3xl font-extrabold text-white tracking-tight sm:text-4xl uppercase font-sans">
          Agent Registered Successfully!
        </h2>
        <p className="text-zinc-400 text-xs sm:text-sm max-w-2xl mx-auto leading-relaxed">
          Copy and run the Docker command below on your local machine or server to boot up your agent process.
        </p>
      </div>

      {/* Connection Status Card */}
      <div className="p-6 rounded-none bg-black border border-zinc-800 backdrop-blur-xl flex flex-col md:flex-row items-center justify-between gap-6 shadow-xl">
        <div className="flex items-center gap-4">
          <div>
            <div className="flex items-center gap-2.5">
              <h3 className="text-base font-bold text-white uppercase tracking-wider">{agent.name}</h3>
              <span
                className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-none text-[10px] font-mono font-bold uppercase tracking-wider ${
                  isOnline
                    ? 'bg-sky-400/20 text-sky-400 border border-sky-400/30'
                    : 'bg-amber-500/20 text-amber-400 border border-amber-500/30 animate-pulse'
                }`}
              >
                <span
                  className={`w-1.5 h-1.5 rounded-none ${
                    isOnline ? 'bg-sky-400' : 'bg-amber-400'
                  }`}
                ></span>
                {isOnline ? 'ONLINE' : 'WAITING FOR CONTAINER PING'}
              </span>
            </div>

            <p className="text-xs text-zinc-400 mt-1 font-mono">
              ID: {agent.id} | Identifier: {agent.agent_identifier}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <span className="text-xs text-zinc-400 flex items-center gap-1.5 font-mono">
            {agent.data_sources?.length || 0} Linked Data Sources
          </span>
        </div>
      </div>

      {/* Terminal Box */}
      <div className="rounded-none border border-zinc-800 bg-black shadow-2xl overflow-hidden">
        {/* Terminal Header Bar */}
        <div className="px-5 py-3 bg-zinc-950 border-b border-zinc-800/80 flex flex-wrap items-center justify-between gap-3">
          {/* Tabs */}
          <div className="flex items-center gap-1.5 flex-wrap">
            <button
              onClick={() => setActiveTab('bash')}
              className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-none text-xs font-mono font-semibold uppercase tracking-wider transition-colors ${
                activeTab === 'bash'
                  ? 'bg-sky-400/20 text-sky-400 border border-sky-400/30'
                  : 'text-zinc-400 hover:text-white hover:bg-zinc-900'
              }`}
            >
              <Terminal className="w-3.5 h-3.5" /> Bash / Linux / macOS
            </button>

            <button
              onClick={() => setActiveTab('powershell')}
              className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-none text-xs font-mono font-semibold uppercase tracking-wider transition-colors ${
                activeTab === 'powershell'
                  ? 'bg-sky-400/20 text-sky-400 border border-sky-400/30'
                  : 'text-zinc-400 hover:text-white hover:bg-zinc-900'
              }`}
            >
              <Monitor className="w-3.5 h-3.5" /> PowerShell
            </button>

            <button
              onClick={() => setActiveTab('oneline')}
              className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-none text-xs font-mono font-semibold uppercase tracking-wider transition-colors ${
                activeTab === 'oneline'
                  ? 'bg-sky-400/20 text-sky-400 border border-sky-400/30'
                  : 'text-zinc-400 hover:text-white hover:bg-zinc-900'
              }`}
            >
              <Code className="w-3.5 h-3.5" /> Single Line
            </button>

            <button
              onClick={() => setActiveTab('env')}
              className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-none text-xs font-mono font-semibold uppercase tracking-wider transition-colors ${
                activeTab === 'env'
                  ? 'bg-sky-400/20 text-sky-400 border border-sky-400/30'
                  : 'text-zinc-400 hover:text-white hover:bg-zinc-900'
              }`}
            >
              <FileCode className="w-3.5 h-3.5" /> .env File
            </button>
          </div>

          {/* Copy Button */}
          <button
            onClick={handleCopy}
            className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-none bg-sky-400 hover:bg-sky-300 text-black text-xs font-bold uppercase tracking-wider transition-all hover:scale-105"
          >
            {copied ? (
              <>
                <Check className="w-3.5 h-3.5 text-black" /> Copied!
              </>
            ) : (
              <>
                <Copy className="w-3.5 h-3.5 text-black" /> Copy Command
              </>
            )}
          </button>
        </div>

        {/* Code Content */}
        <div className="p-5 font-mono text-xs text-zinc-200 overflow-x-auto leading-relaxed bg-black">
          <pre className="text-sky-300 whitespace-pre-wrap break-all">{getCommandToDisplay()}</pre>
        </div>
      </div>

      {/* Step-by-Step Instructions */}
      <div className="p-6 rounded-none bg-black border border-zinc-800 backdrop-blur-xl space-y-4">
        <h4 className="text-xs font-bold text-white uppercase tracking-wider">
          Zero-Credential Deployment Instructions
        </h4>

        <ol className="list-decimal list-inside text-xs text-zinc-400 space-y-2 leading-relaxed font-sans">
          <li>
            Paste the command into your local shell terminal. Replace the placeholder parameters (such as <code className="text-sky-400 font-mono">&lt;SRC_..._HOST&gt;</code>, <code className="text-sky-400 font-mono">&lt;SRC_..._PORT&gt;</code>, <code className="text-sky-400 font-mono">&lt;SRC_..._USER&gt;</code>, <code className="text-sky-400 font-mono">&lt;SRC_..._PASSWORD&gt;</code>, <code className="text-sky-400 font-mono">&lt;SRC_..._NAME&gt;</code>) with your actual database connection credentials.
          </li>
          <li>
            The control plane <strong className="text-white">never</strong> receives or stores your database credentials or passwords.
          </li>
          <li>
            The container will automatically execute the startup handshake using the generated <code className="text-sky-400 font-mono">AGENT_TOKEN</code>.
          </li>
          <li>
            Once booted, the live connection badge above will automatically transition to{' '}
            <span className="text-sky-400 font-semibold">ONLINE</span>.
          </li>
        </ol>
      </div>

      {/* Navigation Footer */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-4 pt-4">
        <button
          type="button"
          onClick={onReset}
          className="w-full sm:w-auto inline-flex items-center justify-center gap-2 py-3 px-6 rounded-none bg-zinc-900 hover:bg-zinc-800 text-zinc-300 text-xs font-bold uppercase tracking-wider border border-zinc-800 transition-colors"
        >
          <RefreshCw className="w-4 h-4" /> Create Another Agent
        </button>

        <a
          href={`/sources?agentId=${agent.id}`}
          className="w-full sm:w-auto inline-flex items-center justify-center gap-2 py-3 px-8 rounded-none bg-sky-400 hover:bg-sky-300 text-black text-xs font-bold uppercase tracking-wider transition-colors shadow-lg shadow-sky-950/50 hover:scale-[1.01]"
        >
          <span>Proceed to Schema Inspection</span>
          <ArrowRight className="w-4 h-4" />
        </a>
      </div>
    </div>
  );
};

export default DockerCommandOutput;
