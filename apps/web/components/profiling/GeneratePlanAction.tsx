'use client';

import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import planService from '../../services/planService';
import executionService from '../../services/executionService';
import { agentService } from '../../services/agentService';
import { PlanResponse } from '../../types/migrationPlan';
import { ExecutionJobResponse } from '../../types/execution';
import { CheckCircle2, Lock, ArrowRight, Activity, ShieldCheck } from 'lucide-react';
import Link from 'next/link';

interface GeneratePlanActionProps {
  agentId: string;
}

export const GeneratePlanAction: React.FC<GeneratePlanActionProps> = ({ agentId }) => {
  const router = useRouter();
  const [targetType, setTargetType] = useState<string>('postgresql');
  const [customInstructions, setCustomInstructions] = useState<string>('');
  const [isGenerating, setIsGenerating] = useState<boolean>(false);
  const [generatingElapsedSec, setGeneratingElapsedSec] = useState<number>(0);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Execution & Existing Plan State
  const [existingPlan, setExistingPlan] = useState<PlanResponse | null>(null);
  const [executionJob, setExecutionJob] = useState<ExecutionJobResponse | null>(null);
  const [checkingStatus, setCheckingStatus] = useState<boolean>(true);
  const [showReGenerateForm, setShowReGenerateForm] = useState<boolean>(false);

  useEffect(() => {
    if (!agentId) return;

    let isMounted = true;
    setCheckingStatus(true);

    const checkAgentExecutionState = async () => {
      try {
        const [plansList, executionsList, agentData, genStatus] = await Promise.all([
          planService.listPlans(),
          executionService.listUserExecutions(),
          agentService.getAgent(agentId).catch(() => null),
          planService.getGenerationStatus(agentId).catch(() => null),
        ]);

        if (!isMounted) return;

        // Check if generation is actively running for this agent (survives page refresh F5)
        if (genStatus && genStatus.status === 'processing') {
          setIsGenerating(true);
          if (typeof genStatus.elapsed_seconds === 'number') {
            setGeneratingElapsedSec(Math.round(genStatus.elapsed_seconds));
          }
        }

        // Auto-detect target database type from agent data source
        const targetDs = agentData?.data_sources?.find((ds) => ds.role === 'target' || ds.role === 'both');
        if (targetDs?.type) {
          setTargetType(targetDs.type.toLowerCase());
        }

        // Find plans linked to this agent
        const matchedPlans = plansList.filter((p) => p.agent_id === agentId);
        const latestPlan = matchedPlans.length > 0 ? matchedPlans[0] : null;
        setExistingPlan(latestPlan);

        if (latestPlan && latestPlan.status === 'generating') {
          setIsGenerating(true);
        }

        // Find executions linked to matched plans or agent
        if (matchedPlans.length > 0) {
          const planIds = new Set(matchedPlans.map((p) => p.id));
          const matchedExecution = executionsList.find((ex) => planIds.has(ex.migration_plan_id));
          setExecutionJob(matchedExecution || null);
        } else {
          setExecutionJob(null);
        }
      } catch {
        // Fallback to unlocked
      } finally {
        if (isMounted) setCheckingStatus(false);
      }
    };

    checkAgentExecutionState();

    return () => {
      isMounted = false;
    };
  }, [agentId]);

  // Polling effect when isGenerating is active (survives page reload)
  useEffect(() => {
    if (!isGenerating || !agentId) return;

    let isMounted = true;

    const poll = async () => {
      try {
        const res = await planService.getGenerationStatus(agentId);
        if (!isMounted) return;

        if (res.status === 'processing') {
          if (typeof res.elapsed_seconds === 'number') {
            setGeneratingElapsedSec(Math.round(res.elapsed_seconds));
          }
        } else if (res.status === 'completed') {
          setIsGenerating(false);
          const targetPlanId = res.plan_id || res.plan?.id;
          if (targetPlanId) {
            router.push(`/transformation-plan?planId=${targetPlanId}`);
          }
        } else if (res.status === 'failed') {
          setIsGenerating(false);
          setErrorMsg(res.error || 'Failed to generate migration plan.');
        }
      } catch {
        // network hiccup, retry next interval
      }
    };

    poll();
    const intervalId = setInterval(poll, 2000);
    const tickerId = setInterval(() => {
      setGeneratingElapsedSec((prev) => prev + 1);
    }, 1000);

    return () => {
      isMounted = false;
      clearInterval(intervalId);
      clearInterval(tickerId);
    };
  }, [isGenerating, agentId, router]);

  const handleGeneratePlan = async () => {
    if (!agentId || isGenerating) return;

    setIsGenerating(true);
    setGeneratingElapsedSec(0);
    setErrorMsg(null);

    try {
      await planService.startGeneration(agentId, {
        database_type: targetType,
        custom_instructions: customInstructions.trim() || undefined,
      });
    } catch (err: any) {
      setIsGenerating(false);
      const msg =
        err.response?.data?.detail ||
        err.message ||
        'Failed to generate migration plan. Ensure Agent has completed schema introspection.';
      setErrorMsg(msg);
    }
  };

  const isExecuted =
    executionJob &&
    (executionJob.status === 'completed' || executionJob.status === 'running' || executionJob.status === 'ddl_executing');

  if (checkingStatus) {
    return (
      <div className="p-6 rounded-none bg-black border border-zinc-800 text-center text-xs font-mono text-zinc-500">
        Checking agent execution history...
      </div>
    );
  }

  // IF AGENT HAS ALREADY EXECUTED A MIGRATION -> RENDER EXECUTED MIGRATION LOCK BANNER
  if (isExecuted) {
    const succRows = executionJob.successful_rows || 0;
    const totalRows = executionJob.total_rows || succRows;
    const isCompleted = executionJob.status === 'completed';

    return (
      <div className="p-6 rounded-none bg-black border border-emerald-500/40 backdrop-blur-xl space-y-6 shadow-[0_0_30px_rgba(52,211,153,0.15)] font-mono animate-fadeIn relative overflow-hidden">
        {/* Background Ambient Grid Accent */}
        <div className="absolute inset-0 bg-[radial-gradient(#34d399_1px,transparent_1px)] [background-size:16px_16px] opacity-5 pointer-events-none" />

        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 border-b border-zinc-800 pb-4">
          <div>
            <div className="flex flex-wrap items-center gap-2 mb-2">
              <span className="text-[10px] font-bold tracking-widest px-2.5 py-0.5 rounded-none uppercase bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 flex items-center gap-1.5">
                <ShieldCheck className="w-3.5 h-3.5" />
                MIGRATION EXECUTED & LOCKED
              </span>
              <span className="text-[10px] font-bold tracking-widest px-2.5 py-0.5 rounded-none uppercase bg-zinc-900 text-zinc-300 border border-zinc-800 flex items-center gap-1">
                <Lock className="w-3 h-3 text-amber-400" /> RE-EXECUTION PREVENTED
              </span>
            </div>
            <h3 className="text-xl font-extrabold text-white uppercase font-sans tracking-tight">
              Target Database Migration Executed
            </h3>
            <p className="text-zinc-400 text-xs max-w-2xl mt-1 leading-relaxed">
              This agent has completed its database schema translation and data insertion stream into the target database. Re-running is locked to protect target data integrity.
            </p>
          </div>

          <div className="p-3 rounded-none bg-zinc-950 border border-zinc-800 text-right font-mono">
            <div className="text-[10px] text-zinc-500 uppercase font-bold">COMMITTED ROWS</div>
            <div className="text-xl font-bold text-emerald-400">{succRows.toLocaleString()}</div>
          </div>
        </div>

        {/* Target Details Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
          <div className="p-3.5 rounded-none bg-zinc-950 border border-zinc-900 space-y-1">
            <span className="text-[10px] text-zinc-500 font-bold uppercase block">EXECUTION STATUS</span>
            <span className="text-sm font-bold text-emerald-400 flex items-center gap-1.5">
              <CheckCircle2 className="w-4 h-4 text-emerald-400" />
              {isCompleted ? '100% VERIFIED & COMPLETED' : executionJob.status.toUpperCase()}
            </span>
          </div>

          <div className="p-3.5 rounded-none bg-zinc-950 border border-zinc-900 space-y-1">
            <span className="text-[10px] text-zinc-500 font-bold uppercase block font-mono">TARGET DB INSERTIONS</span>
            <span className="text-sm font-bold text-white font-mono">
              {succRows.toLocaleString()} / {totalRows.toLocaleString()} rows
            </span>
          </div>

          <div className="p-3.5 rounded-none bg-zinc-950 border border-zinc-900 space-y-1">
            <span className="text-[10px] text-zinc-500 font-bold uppercase block font-mono">JOB IDENTIFIER</span>
            <span className="text-xs font-bold text-sky-400 font-mono truncate block">{executionJob.id}</span>
          </div>
        </div>

        {/* Action Buttons */}
        <div className="flex flex-col sm:flex-row items-center justify-end gap-3 pt-2 border-t border-zinc-900">
          {existingPlan && (
            <Link
              href={`/transformation-plan?planId=${existingPlan.id}`}
              className="w-full sm:w-auto inline-flex items-center justify-center gap-2 py-3 px-6 rounded-none bg-zinc-900 hover:bg-zinc-800 text-sky-400 text-xs font-mono font-bold uppercase border border-sky-400/30 transition-colors shadow-md"
            >
              <span>View Transformation Blueprint</span>
              <ArrowRight className="w-3.5 h-3.5" />
            </Link>
          )}

          <Link
            href="/execution"
            className="w-full sm:w-auto inline-flex items-center justify-center gap-2 py-3 px-6 rounded-none bg-emerald-500 hover:bg-emerald-400 text-black text-xs font-mono font-bold uppercase tracking-wider transition-colors shadow-lg shadow-emerald-950/50"
          >
            <Activity className="w-4 h-4" />
            <span>View Live Execution Monitor</span>
          </Link>
        </div>
      </div>
    );
  }

  // UN-EXECUTED AGENT -> RENDER EXISTING PLAN DISCOVERY BANNER (IF AVAILABLE) OR NORMAL GENERATE BOX
  const hasUnexecutedPlan = existingPlan && existingPlan.status !== 'completed' && existingPlan.status !== 'generating';

  return (
    <div className="space-y-6">
      {hasUnexecutedPlan && (
        <div className="p-6 rounded-none bg-black border border-sky-400/50 backdrop-blur-xl space-y-5 shadow-[0_0_30px_rgba(56,189,248,0.15)] font-mono animate-fadeIn relative overflow-hidden">
          {/* Background Ambient Grid Accent */}
          <div className="absolute inset-0 bg-[radial-gradient(#38bdf8_1px,transparent_1px)] [background-size:16px_16px] opacity-5 pointer-events-none" />

          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 border-b border-zinc-800 pb-4">
            <div>
              <div className="flex items-center gap-2 mb-2">
                <span className="text-[10px] font-bold tracking-widest px-2.5 py-0.5 rounded-none uppercase bg-sky-400/10 text-sky-400 border border-sky-400/30">
                  EXISTING BLUEPRINT AVAILABLE
                </span>
                <span className="text-[10px] font-bold tracking-widest px-2.5 py-0.5 rounded-none uppercase bg-zinc-900 text-zinc-300 border border-zinc-800">
                  STATUS: {existingPlan.status.toUpperCase()}
                </span>
              </div>
              <h3 className="text-xl font-extrabold text-white uppercase font-sans tracking-tight">
                Unexecuted Migration Blueprint Available
              </h3>
              <p className="text-zinc-400 text-xs max-w-2xl mt-1 leading-relaxed font-sans">
                An active transformation plan has already been generated for this agent. You can view, edit column mappings, or refine prompts without re-generating from scratch.
              </p>
            </div>

            <div className="p-3 rounded-none bg-zinc-950 border border-sky-400/30 text-right font-mono min-w-[130px]">
              <div className="text-[10px] text-zinc-500 uppercase font-bold">AI READINESS</div>
              <div className="flex items-center justify-end gap-2 mt-0.5">
                <span className="text-xl font-bold text-sky-400">
                  {Math.round((existingPlan.confidence_score || 0.9) * 100)}%
                </span>
                <span className="text-[9px] font-bold px-1.5 py-0.5 uppercase border bg-sky-400/10 text-sky-400 border-sky-400/30">
                  READY
                </span>
              </div>
            </div>
          </div>

          <div className="flex flex-col sm:flex-row items-center justify-between gap-4 pt-2">
            <div className="text-xs text-zinc-400 font-mono">
              Plan ID: <span className="text-white">{existingPlan.id}</span> • Created: {new Date(existingPlan.created_at).toLocaleDateString()}
            </div>

            <div className="flex items-center gap-3 w-full sm:w-auto">
              <button
                type="button"
                onClick={() => setShowReGenerateForm((prev) => !prev)}
                className="py-3 px-4 rounded-none bg-zinc-950 hover:bg-zinc-900 text-zinc-400 hover:text-white text-xs font-mono font-bold uppercase tracking-wider border border-zinc-800 transition-colors"
              >
                {showReGenerateForm ? 'Hide Generation Form' : 'Generate New Blueprint Instead'}
              </button>

              <Link
                href={`/transformation-plan?planId=${existingPlan.id}`}
                className="inline-flex items-center justify-center gap-2 py-3 px-6 rounded-none bg-sky-400 hover:bg-sky-300 text-black text-xs font-mono font-bold uppercase tracking-wider transition-colors shadow-lg shadow-sky-950/50"
              >
                <span>View & Edit Blueprint</span>
                <ArrowRight className="w-4 h-4" />
              </Link>
            </div>
          </div>
        </div>
      )}

      {(!hasUnexecutedPlan || showReGenerateForm) && (
        <div className="p-6 rounded-none bg-black border border-sky-400/40 backdrop-blur-xl space-y-6 shadow-[0_0_25px_rgba(56,189,248,0.15)] relative overflow-hidden font-mono">
          {/* Background Accent Grid */}
          <div className="absolute inset-0 bg-[radial-gradient(#38bdf8_1px,transparent_1px)] [background-size:16px_16px] opacity-5 pointer-events-none" />

          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 border-b border-zinc-800 pb-4">
            <div>
              <span className="text-[10px] font-mono font-bold tracking-widest px-2.5 py-0.5 rounded-none uppercase bg-sky-400/10 text-sky-400 border border-sky-400/30">
                AI PLANNING ENGINE
              </span>
              <h3 className="text-xl font-extrabold text-white uppercase font-sans tracking-wide mt-1">
                Generate Migration Transformation Blueprint
              </h3>
              <p className="text-zinc-400 text-xs max-w-2xl mt-1 leading-relaxed">
                The AI Planning Engine will analyze all profiled source schema tables, primary keys, foreign keys, and data types to construct an executable transformation AST.
              </p>
            </div>
          </div>

          {errorMsg && (
            <div className="p-4 rounded-none bg-rose-500/10 border border-rose-500/30 text-rose-400 text-xs font-mono">
              🚨 {errorMsg}
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="space-y-2 md:col-span-1">
              <label className="block text-[10px] font-mono font-semibold uppercase tracking-wider text-zinc-300">
                Target Database Engine
              </label>
              <div className="w-full px-4 py-2.5 rounded-none bg-zinc-950 border border-zinc-800 text-white text-xs font-mono flex items-center justify-between">
                <span className="font-bold text-sky-400">
                  {targetType.toUpperCase()}
                  <span className="text-zinc-400 font-normal ml-1.5">
                    {targetType.toLowerCase() === 'mongodb' ? '(NoSQL Document)' : '(Relational)'}
                  </span>
                </span>
                <span className="text-[10px] uppercase font-mono px-2 py-0.5 rounded-none bg-zinc-900 border border-zinc-700 text-zinc-400 flex items-center gap-1">
                  <Lock className="w-2.5 h-2.5 text-amber-400" />
                  Fixed by Agent
                </span>
              </div>
            </div>

            <div className="space-y-2 md:col-span-2">
              <label className="block text-[10px] font-mono font-semibold uppercase tracking-wider text-zinc-300">
                Custom AI Guidance / Tuning Instructions (Optional)
              </label>
              <textarea
                rows={2}
                value={customInstructions}
                onChange={(e) => setCustomInstructions(e.target.value)}
                placeholder="e.g. Prefer UUID primary keys, map created_on to created_at, convert enum ints to text"
                className="w-full px-4 py-2.5 rounded-none bg-zinc-950 border border-zinc-800 text-white text-xs placeholder-zinc-600 focus:outline-none focus:border-sky-400 font-sans transition-colors resize-y min-h-[50px] leading-relaxed"
              />
            </div>
          </div>

          {isGenerating && (
            <div className="p-5 rounded-none bg-zinc-950 border border-sky-400/50 space-y-3 font-mono shadow-[0_0_25px_rgba(56,189,248,0.15)] relative overflow-hidden animate-fadeIn">
              <div className="absolute top-0 left-0 right-0 h-0.5 bg-gradient-to-r from-sky-400 via-indigo-500 to-sky-400 animate-pulse" />
              <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-none bg-sky-400/10 border border-sky-400/40 flex items-center justify-center text-sky-400">
                    <div className="w-4 h-4 border-2 border-sky-400 border-t-transparent rounded-none animate-spin" />
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-mono font-bold text-white uppercase tracking-wider">
                        AI Blueprint Generation in Progress
                      </span>
                      <span className="px-2 py-0.5 text-[10px] font-mono font-bold rounded-none bg-sky-400/20 text-sky-300 border border-sky-400/40">
                        {generatingElapsedSec}s elapsed
                      </span>
                    </div>
                    <p className="text-xs text-zinc-400 font-sans mt-0.5">
                      Analyzing source schemas, building AST graph, and validating feasibility in background...
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-2 text-right">
                  <span className="w-2 h-2 rounded-none bg-sky-400 animate-ping" />
                  <span className="text-[11px] text-sky-400 font-mono font-bold uppercase tracking-wider">
                    POLLING STATUS (2S)
                  </span>
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 pt-1 text-[10px] uppercase font-bold text-zinc-400">
                <div className="p-2 bg-black/60 border border-zinc-800 text-sky-400 flex items-center gap-2">
                  <span className="w-1.5 h-1.5 bg-sky-400 rounded-none inline-block animate-pulse" />
                  1. Introspecting Metadata
                </div>
                <div className="p-2 bg-black/60 border border-zinc-800 text-sky-400 flex items-center gap-2">
                  <span className="w-1.5 h-1.5 bg-sky-400 rounded-none inline-block animate-pulse" />
                  2. Constructing AST Graph
                </div>
                <div className="p-2 bg-black/60 border border-zinc-800 text-sky-400 flex items-center gap-2">
                  <span className="w-1.5 h-1.5 bg-sky-400 rounded-none inline-block animate-pulse" />
                  3. Verifying Feasibility
                </div>
              </div>

              <div className="text-[11px] text-zinc-500 font-mono border-t border-zinc-900 pt-2 flex items-center justify-between">
                <span>You may refresh or navigate away — plan generation continues executing on the server without interruption.</span>
                <span className="text-zinc-400">Auto-redirecting on complete</span>
              </div>
            </div>
          )}

          <div className="flex items-center justify-end pt-2">
            <button
              type="button"
              onClick={handleGeneratePlan}
              disabled={isGenerating}
              className="w-full sm:w-auto inline-flex items-center justify-center gap-3 py-3.5 px-10 rounded-none bg-sky-400 hover:bg-sky-300 text-black text-xs font-bold uppercase tracking-wider transition-all shadow-lg shadow-sky-950/50 hover:scale-[1.01] disabled:opacity-50 font-mono"
            >
              {isGenerating ? (
                <>
                  <div className="w-4 h-4 border-2 border-black border-t-transparent rounded-none animate-spin" />
                  <span>Constructing AI Blueprint AST ({generatingElapsedSec}s)...</span>
                </>
              ) : (
                <>
                  <span>GENERATE AI MIGRATION PLAN</span>
                  <span className="text-base">→</span>
                </>
              )}
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

export default GeneratePlanAction;
