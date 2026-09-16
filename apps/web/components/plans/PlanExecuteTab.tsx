'use client';

import React from 'react';
import {
  PlanDetailResponse,
  TransformationPlanAST,
} from '../../types/migrationPlan';
import { ExecutionJobResponse } from '../../types/execution';
import JobExecutionBanner from './JobExecutionBanner';
import { Database, AlertTriangle, ShieldCheck, Play, ArrowLeft, Layers, ShieldAlert, Sparkles, CheckCircle2 } from 'lucide-react';

interface PlanExecuteTabProps {
  plan: PlanDetailResponse;
  ast: TransformationPlanAST;
  activeJob: ExecutionJobResponse | null;
  onActiveJobUpdated: (job: ExecutionJobResponse) => void;
  isApproving: boolean;
  isDryRunning: boolean;
  isRefining: boolean;
  isJobActive: boolean;
  onDryRun: () => Promise<void>;
  onOpenExecutionModal: () => void;
  onSwitchToOverviewTab: () => void;
}

export const PlanExecuteTab: React.FC<PlanExecuteTabProps> = ({
  plan,
  ast,
  activeJob,
  onActiveJobUpdated,
  isApproving,
  isDryRunning,
  isRefining,
  isJobActive,
  onDryRun,
  onOpenExecutionModal,
  onSwitchToOverviewTab,
}) => {
  const isApproved = plan.status === 'completed' || plan.status === 'approved';
  const isMigrationCompleted = Boolean(
    (activeJob && activeJob.status === 'completed' && !activeJob.is_dry_run) ||
    plan.status === 'completed'
  );
  const tableCount = ast?.table_mappings?.length || 0;

  return (
    <div className="space-y-8 animate-fadeIn font-sans">
      {/* 1. Readiness Gate: Validation Error Banner if Invalid */}
      {!plan.is_valid && (
        <div className="p-6 rounded-none bg-rose-950/30 border border-rose-500/60 font-mono text-xs space-y-3 shadow-[0_0_25px_rgba(244,63,94,0.15)] animate-fadeIn">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2.5">
              <ShieldAlert className="w-5 h-5 text-rose-500 shrink-0" />
              <div>
                <h4 className="font-bold text-rose-400 uppercase tracking-wider text-sm font-sans">
                  Migration Execution Blocked: Schema Feasibility Errors
                </h4>
                <p className="text-zinc-300 font-sans text-xs mt-0.5">
                  The current plan blueprint has schema validation errors that must be resolved before migration execution can proceed.
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={onSwitchToOverviewTab}
              className="py-2 px-4 bg-rose-500 hover:bg-rose-400 text-black text-xs font-mono font-bold uppercase tracking-wider transition-colors shrink-0"
            >
              View Diagnostics & Revert
            </button>
          </div>
          {plan.validation_errors?.errors && plan.validation_errors.errors.length > 0 && (
            <div className="p-3 bg-black/80 border border-rose-500/40 text-rose-400 text-[11px] font-mono">
              <span className="font-bold uppercase text-[10px] block mb-1">
                Errors ({plan.validation_errors.errors.length}):
              </span>
              <ul className="list-disc list-inside space-y-0.5">
                {plan.validation_errors.errors.map((err: string, i: number) => (
                  <li key={i}>{err}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* 2. Target Database Details Card */}
      <div className="p-6 rounded-none bg-black border border-zinc-800 space-y-4 shadow-xl font-mono text-xs">
        <div className="flex items-center justify-between border-b border-zinc-800 pb-3">
          <div className="flex items-center gap-2">
            <Database className="w-4 h-4 text-sky-400" />
            <h3 className="font-bold text-white uppercase tracking-wider text-xs">
              Target Database Destination
            </h3>
          </div>
          <span className="px-2 py-0.5 text-[10px] font-bold uppercase bg-sky-400/10 text-sky-400 border border-sky-400/30">
            {(plan.target_config?.database_type || 'postgresql').toUpperCase()}
          </span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="p-3 bg-zinc-950 border border-zinc-800/80 space-y-1">
            <span className="text-[10px] text-zinc-500 uppercase font-bold block">Engine Type</span>
            <span className="text-white font-bold text-sm">
              {(plan.target_config?.database_type || 'postgresql').toUpperCase()}
            </span>
            <span className="text-[10px] text-zinc-400 block">
              {plan.target_config?.database_type?.toLowerCase() === 'mongodb'
                ? 'NoSQL Document Store'
                : 'Relational Database (SQL)'}
            </span>
          </div>

          <div className="p-3 bg-zinc-950 border border-zinc-800/80 space-y-1">
            <span className="text-[10px] text-zinc-500 uppercase font-bold block">
              Destination Identifier
            </span>
            <span className="text-white font-bold text-sm truncate block">
              {plan.target_config?.identifier || 'Target Database'}
            </span>
            <span className="text-[10px] text-zinc-400 block">Host & Port Configured</span>
          </div>

          <div className="p-3 bg-zinc-950 border border-zinc-800/80 space-y-1">
            <span className="text-[10px] text-zinc-500 uppercase font-bold block">
              Destination Tables
            </span>
            <span className="text-white font-bold text-sm">{tableCount} Tables</span>
            <span className="text-[10px] text-zinc-400 truncate block">
              {ast?.table_mappings?.map((t) => t.target_table_name).join(', ') || 'None'}
            </span>
          </div>
        </div>
      </div>

      {/* 3. Live Job Execution Banner (Current or Most Recent) */}
      {activeJob && (
        <JobExecutionBanner
          job={activeJob}
          onJobUpdated={onActiveJobUpdated}
        />
      )}

      {/* 3b. Migration Completed Success Callout */}
      {isMigrationCompleted && (
        <div className="p-4 bg-emerald-950/40 border border-emerald-500/50 text-xs font-mono flex items-start gap-3 shadow-[0_0_25px_rgba(16,185,129,0.15)] animate-fadeIn">
          <CheckCircle2 className="w-5 h-5 text-emerald-400 shrink-0 mt-0.5" />
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <span className="font-bold text-emerald-300 uppercase tracking-wide text-xs">
                Target Migration Completed Successfully
              </span>
              <span className="px-2 py-0.5 bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 text-[10px] uppercase font-bold">
                All Data Written
              </span>
            </div>
            <p className="text-zinc-300 font-sans text-xs leading-relaxed">
              This blueprint has already executed and populated your target database. To maintain target data integrity, this agent and blueprint are locked against further migration generation.
            </p>
          </div>
        </div>
      )}

      {/* 4. Plan Approval & Agent Execution Action Section */}
      <div className="p-6 rounded-none bg-black border border-zinc-800 shadow-xl space-y-6">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 border-b border-zinc-800 pb-4">
          <div>
            <h4 className="text-sm font-bold text-white uppercase tracking-wider font-sans">
              {isMigrationCompleted
                ? 'Migration Execution Complete & Locked'
                : 'Approve Blueprint & Run Local Agent Migration'}
            </h4>
            <p className="text-xs text-zinc-400 font-mono mt-0.5">
              {isMigrationCompleted
                ? 'All records have been streamed into the target database. Execution is locked for this agent to prevent duplicate migrations.'
                : 'Approving locks the blueprint AST and dispatches the stream execution job to your Docker Agent.'}
            </p>
          </div>

          {isMigrationCompleted ? (
            <div className="flex items-center gap-2 px-4 py-2.5 bg-emerald-950/40 border border-emerald-500/40 text-emerald-300 font-mono text-xs shadow-sm">
              <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
              <span className="font-bold uppercase tracking-wider">
                MIGRATION EXECUTED & LOCKED
              </span>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-3">
              {/* Dry Run Button */}
              <button
                type="button"
                onClick={onDryRun}
                disabled={
                  isDryRunning ||
                  isApproving ||
                  isJobActive ||
                  isRefining ||
                  plan.status === 'refining' ||
                  !plan.is_valid
                }
                className="py-3.5 px-6 rounded-none text-xs font-bold font-mono uppercase tracking-wider transition-all border border-amber-400/50 bg-amber-400/10 hover:bg-amber-400/20 text-amber-300 shadow-lg disabled:opacity-50"
              >
                {isDryRunning ? 'Simulating Dry Run...' : '⚡ Run Dry Run (Simulation)'}
              </button>

              {/* Real Approve & Execute Button */}
              <button
                type="button"
                onClick={onOpenExecutionModal}
                disabled={
                  isApproving ||
                  isJobActive ||
                  isDryRunning ||
                  isRefining ||
                  plan.status === 'refining' ||
                  !plan.is_valid
                }
                className={`py-3.5 px-8 rounded-none text-xs font-bold font-mono uppercase tracking-wider transition-all shadow-lg ${
                  !plan.is_valid
                    ? 'bg-zinc-800 text-zinc-500 cursor-not-allowed border border-zinc-700'
                    : isJobActive
                    ? 'bg-zinc-800 text-zinc-500 cursor-not-allowed border border-zinc-700'
                    : isApproved
                    ? 'bg-emerald-500 hover:bg-emerald-400 text-black shadow-emerald-950/50 hover:scale-[1.01]'
                    : 'bg-sky-400 hover:bg-sky-300 text-black shadow-sky-950/50 hover:scale-[1.01]'
                } disabled:opacity-50`}
              >
                {isJobActive
                  ? 'EXECUTION IN PROGRESS...'
                  : isApproving
                  ? 'Executing on Agent...'
                  : isRefining || plan.status === 'refining'
                  ? 'REFINEMENT IN PROGRESS...'
                  : !plan.is_valid
                  ? 'EXECUTION BLOCKED (INVALID PLAN)'
                  : isApproved
                  ? '⚡ EXECUTE MIGRATION'
                  : 'APPROVE & EXECUTE MIGRATION'}
              </button>
            </div>
          )}
        </div>

        {/* Safety Note */}
        <div className="p-4 bg-zinc-950 border border-zinc-800 text-zinc-400 text-xs font-mono space-y-1">
          <div className="flex items-center gap-2 text-sky-400 font-bold uppercase text-[11px]">
            <ShieldCheck className="w-3.5 h-3.5" />
            Execution Safety Guarantees
          </div>
          <p className="text-[11px] font-sans leading-relaxed text-zinc-400">
            Migration tasks run locally inside your isolated Docker Agent container. No database credentials or customer row records leave your infrastructure. Streaming batching is throttled to maintain memory safety.
          </p>
        </div>
      </div>

      {/* Navigation Footer */}
      <div className="flex items-center justify-between pt-4 border-t border-zinc-800 font-mono">
        <button
          type="button"
          onClick={onSwitchToOverviewTab}
          className="flex items-center gap-2 text-xs font-bold text-zinc-400 hover:text-white uppercase transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to Overview
        </button>
      </div>
    </div>
  );
};

export default PlanExecuteTab;
