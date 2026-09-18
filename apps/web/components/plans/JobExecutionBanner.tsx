'use client';

import React, { useState, useEffect, useRef } from 'react';
import Link from 'next/link';
import toast from 'react-hot-toast';
import { ExecutionJobResponse, AIDiagnosisPayload } from '../../types/execution';
import executionService from '../../services/executionService';

interface JobExecutionBannerProps {
  job: ExecutionJobResponse;
  onJobUpdated?: (updatedJob: ExecutionJobResponse) => void;
}

export const JobExecutionBanner: React.FC<JobExecutionBannerProps> = ({
  job: initialJob,
  onJobUpdated,
}) => {
  const [job, setJob] = useState<ExecutionJobResponse>(initialJob);
  const [logs, setLogs] = useState<string[]>([]);
  const [jobHistory, setJobHistory] = useState<ExecutionJobResponse[]>([]);
  const [isRetrying, setIsRetrying] = useState<boolean>(false);
  const [isCancelling, setIsCancelling] = useState<boolean>(false);
  const [showCancelModal, setShowCancelModal] = useState<boolean>(false);
  const [cancelReason, setCancelReason] = useState<string>('');
  const [isDiagnosing, setIsDiagnosing] = useState<boolean>(false);
  const [copiedJobId, setCopiedJobId] = useState<boolean>(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const handleCopyJobId = () => {
    if (!job.id) return;
    navigator.clipboard.writeText(job.id);
    setCopiedJobId(true);
    toast.success('Job ID copied to clipboard');
    setTimeout(() => setCopiedJobId(false), 2000);
  };

  // Store callback in a ref so the polling useEffect never needs it in its
  // dependency array — eliminates interval restarts caused by parent re-renders.
  const onJobUpdatedRef = useRef(onJobUpdated);
  useEffect(() => {
    onJobUpdatedRef.current = onJobUpdated;
  });

  // Synchronize internal job state whenever initialJob prop updates from parent component
  const prevJobIdRef = useRef(initialJob?.id);
  useEffect(() => {
    if (initialJob) {
      setJob(initialJob);
      if (prevJobIdRef.current !== initialJob.id) {
        prevJobIdRef.current = initialJob.id;
        setLogs([`[${new Date().toLocaleTimeString()}] Switch view to execution run: ${initialJob.id}`]);
      }
    }
  }, [initialJob?.id, initialJob?.status, initialJob?.updated_at, initialJob?.successful_rows, initialJob?.processed_rows]);

  const status = (job.status || 'queued').toLowerCase();
  const isDryRunCompleted = status === 'dry_run_completed';
  const isCancelled = status === 'cancelled';
  const isRunning =
    status === 'running' ||
    status === 'pending' ||
    status === 'ddl_executing' ||
    status === 'preparing' ||
    status === 'queued';
  const isRealCompleted = status === 'completed';
  const isCompleted = isRealCompleted || isDryRunCompleted;
  const isFailed = status === 'failed';
  const isDryRun = Boolean(job.is_dry_run || isDryRunCompleted);
  const canResume = isFailed && (job.processed_rows || 0) > 0;
  const [isExecutingReal, setIsExecutingReal] = useState<boolean>(false);

  // Fetch job history for plan
  useEffect(() => {
    let isSubscribed = true;
    if (job.migration_plan_id) {
      executionService.listPlanJobs(job.migration_plan_id)
        .then((history) => {
          if (isSubscribed) setJobHistory(history);
        })
        .catch(() => {});
    }
    return () => { isSubscribed = false; };
  }, [job.migration_plan_id, job.id, job.status]);

  // Smooth scroll into view when initialized/approved
  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, []);

  // Poll job status every 2.0 seconds ONLY while job is active
  useEffect(() => {
    let isSubscribed = true;
    let intervalId: NodeJS.Timeout | null = null;

    const fetchStatus = async () => {
      try {
        const updated = await executionService.getExecutionDetails(job.id);
        if (!isSubscribed) return;

        setJob(updated);
        // Use the ref so we never need onJobUpdated in the dependency array
        if (onJobUpdatedRef.current) onJobUpdatedRef.current(updated);

        // Append log line if stage changes or new progress
        const totalTarget = (updated.total_rows && updated.total_rows > 0) ? updated.total_rows : (updated.successful_rows || 0);
        const logMsg = `[${new Date().toLocaleTimeString()}] Stage: ${updated.current_stage || 'processing'} | Table: ${updated.current_table || 'N/A'} | Inserted: ${updated.successful_rows || 0} / ${totalTarget} rows (${Math.round(updated.progress || 0)}%)`;

        setLogs((prev) => {
          if (prev.length > 0 && prev[prev.length - 1] === logMsg) return prev;
          return [...prev.slice(-49), logMsg];
        });

        // STOP POLLING IMMEDIATELY WHEN JOB COMPLETES OR FAILS
        const updatedStatus = (updated.status || '').toLowerCase();
        if (
          updatedStatus === 'completed' ||
          updatedStatus === 'dry_run_completed' ||
          updatedStatus === 'failed' ||
          updatedStatus === 'cancelled'
        ) {
          if (intervalId) clearInterval(intervalId);
        }

      } catch {
        // Polling retry
      }
    };

    const currentStatus = (job.status || '').toLowerCase();
    const isJobActive = currentStatus === 'running' || currentStatus === 'pending' || currentStatus === 'ddl_executing' || currentStatus === 'preparing' || currentStatus === 'queued';

    if (!isJobActive) {
      return;
    }

    fetchStatus();
    intervalId = setInterval(fetchStatus, 2000);

    return () => {
      isSubscribed = false;
      if (intervalId) clearInterval(intervalId);
    };
  // NOTE: onJobUpdated intentionally omitted — stored in a ref above to prevent
  // the interval from restarting when the parent passes a new function reference.
  }, [job.id, job.status]);

  // Handle Execute For Real after Dry Run Simulation
  const handleExecuteForReal = async () => {
    if (isExecutingReal || !job.migration_plan_id) return;
    setIsExecutingReal(true);
    try {
      const newJob = await executionService.startPlanExecution(job.migration_plan_id, { is_dry_run: false });
      setJob(newJob);
      setLogs([`[${new Date().toLocaleTimeString()}] Real migration dispatched to Docker Agent: ${newJob.id}`]);
      toast.success('Real migration job queued on Docker Agent! Data will be written to target DB.');
      if (onJobUpdated) onJobUpdated(newJob);
    } catch (err: any) {
      const detail = err?.response?.data?.detail || err?.message || 'Failed to start real migration.';
      toast.error(`Execution Error: ${detail}`);
    } finally {
      setIsExecutingReal(false);
    }
  };

  // Handle Retry or Resume Execution Job
  const handleRetryJob = async () => {
    if (isRetrying || !job.migration_plan_id) return;
    setIsRetrying(true);
    try {
      const newJob = await executionService.startPlanExecution(job.migration_plan_id, {
        is_dry_run: Boolean(job.is_dry_run),
      });
      setJob(newJob);
      const actionName = canResume ? 'Resumed' : 'Re-triggered';
      setLogs([`[${new Date().toLocaleTimeString()}] ${actionName} migration job run: ${newJob.id}`]);
      toast.success(
        canResume
          ? 'Migration resumed! Checkpoints reused from last processed record.'
          : 'Migration job retried! New run queued for Docker Agent.'
      );
      if (onJobUpdated) onJobUpdated(newJob);
    } catch (err: any) {
      const detail = err?.response?.data?.detail || err?.message || (canResume ? 'Failed to resume job.' : 'Failed to retry job.');
      if (err?.response?.status === 503) {
        toast.error(`Agent Offline Warning: ${detail}`, { duration: 8000 });
      } else {
        toast.error(`${canResume ? 'Resume' : 'Retry'} Error: ${detail}`);
      }
    } finally {
      setIsRetrying(false);
    }
  };

  // Handle Job Cancellation & Agent Release
  const handleCancelJob = async () => {
    if (isCancelling || !job.id) return;
    setIsCancelling(true);
    try {
      const updated = await executionService.cancelExecution(job.id, cancelReason.trim() || 'Cancelled by user from web console.');
      setJob(updated);
      setShowCancelModal(false);
      setLogs((prev) => [
        ...prev,
        `[${new Date().toLocaleTimeString()}] Execution cancelled by user. Docker Agent unassigned & released.`
      ]);
      toast.success('Execution cancelled! Migration plan is now unlocked for new runs.');
      if (onJobUpdated) onJobUpdated(updated);
    } catch (err: any) {
      const detail = err?.response?.data?.detail || err?.message || 'Failed to cancel execution.';
      toast.error(`Cancellation Error: ${detail}`);
    } finally {
      setIsCancelling(false);
    }
  };

  // Handle AI Error Diagnosis Request
  const handleTriggerDiagnosis = async () => {
    if (isDiagnosing || !job.id) return;
    setIsDiagnosing(true);
    try {
      const updated = await executionService.diagnoseJobFailure(job.id);
      setJob(updated);
      toast.success('AI Failure Diagnosis completed!');
      if (onJobUpdated) onJobUpdated(updated);
    } catch (err: any) {
      toast.error(`Diagnosis Error: ${err.message || 'Failed to diagnose job.'}`);
    } finally {
      setIsDiagnosing(false);
    }
  };

  const progressPercent = Math.min(100, Math.max(0, Math.round(job.progress || 0)));
  const totalRows = job.total_rows || 0;
  const succRows = job.successful_rows || 0;
  const failRows = job.failed_rows || 0;
  const procRows = job.processed_rows || 0;
  const diagnosis = job.ai_diagnosis;

  // Pipeline Stepper configuration
  const stages = [
    { key: 'pre_ddl', label: '1. PRE-MIGRATION DDL', desc: 'Target Table Schema Creation' },
    { key: 'data_streaming', label: '2. TARGET DATA INSERTION', desc: 'Chunked ETL Vector Stream' },
    { key: 'post_ddl', label: '3. POST-MIGRATION DDL', desc: 'Foreign Keys & Constraints' },
    { key: 'completed', label: '4. VERIFICATION & SYNC', desc: 'Target Integrity Check' },
  ];

  const getCurrentStepIndex = () => {
    if (isCompleted) return 3;
    if (isFailed || isCancelled) return -1;
    const stage = (job.current_stage || '').toLowerCase();
    if (stage.includes('pre_ddl')) return 0;
    if (stage.includes('streaming') || stage.includes('data')) return 1;
    if (stage.includes('post_ddl')) return 2;
    return 1;
  };

  const currentStep = getCurrentStepIndex();

  return (
    <div
      ref={containerRef}
      className="p-5 sm:p-7 rounded-none bg-black/95 border border-sky-400/40 backdrop-blur-xl space-y-6 shadow-[0_0_35px_rgba(56,189,248,0.12)] font-mono animate-fadeIn"
    >
      {/* Organized Top Banner Header */}
      <div className="space-y-4 border-b border-zinc-800/80 pb-5">
        {/* Row 1: System Daemon Chip, Status Badge, Run Switcher, and Mini-Progress Pill */}
        <div className="flex flex-wrap items-center justify-between gap-3 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[10px] font-bold tracking-widest px-2.5 py-1 rounded-none uppercase bg-sky-400/10 text-sky-400 border border-sky-400/30 flex items-center gap-1.5 shadow-sm">
              <span className="w-1.5 h-1.5 rounded-none bg-sky-400 animate-pulse" />
              TARGET DB INSERTION DAEMON
            </span>

            <span
              className={`text-[10px] font-bold tracking-widest px-2.5 py-1 rounded-none uppercase border flex items-center gap-1.5 shadow-sm ${
                isDryRunCompleted
                  ? 'bg-amber-400/15 text-amber-300 border-amber-400/50 shadow-[0_0_12px_rgba(251,191,36,0.25)]'
                  : isRealCompleted
                  ? 'bg-emerald-400/15 text-emerald-400 border-emerald-400/40 shadow-[0_0_12px_rgba(52,211,153,0.25)]'
                  : isFailed
                  ? 'bg-rose-500/15 text-rose-400 border-rose-500/40 shadow-[0_0_12px_rgba(244,63,94,0.25)]'
                  : isCancelled
                  ? 'bg-zinc-800/80 text-zinc-300 border-zinc-600 shadow-[0_0_12px_rgba(161,161,170,0.15)]'
                  : 'bg-sky-400/15 text-sky-400 border-sky-400/40 animate-pulse'
              }`}
            >
              <span
                className={`w-1.5 h-1.5 rounded-none ${
                  isDryRunCompleted
                    ? 'bg-amber-400'
                    : isRealCompleted
                    ? 'bg-emerald-400'
                    : isFailed
                    ? 'bg-rose-500'
                    : isCancelled
                    ? 'bg-zinc-400'
                    : 'bg-sky-400 animate-ping'
                }`}
              />
              STATUS: {status.replace('_', ' ').toUpperCase()}
            </span>

            {/* Run Selector Dropdown */}
            {jobHistory.length > 1 && (
              <div className="flex items-center gap-1.5 px-2.5 py-1 bg-zinc-900 border border-zinc-700 text-sky-400 text-[10px] font-bold uppercase shadow-sm">
                <span className="text-zinc-500">RUN:</span>
                <select
                  value={job.id}
                  onChange={async (e) => {
                    const selectedId = e.target.value;
                    const selectedJob = jobHistory.find((j) => j.id === selectedId);
                    if (selectedJob) {
                      setJob(selectedJob);
                      if (onJobUpdated) onJobUpdated(selectedJob);
                    }
                  }}
                  className="bg-transparent text-sky-400 text-[10px] font-bold uppercase focus:outline-none cursor-pointer"
                >
                  {jobHistory.map((hJob: ExecutionJobResponse, idx: number) => (
                    <option key={hJob.id} value={hJob.id} className="bg-zinc-950 text-zinc-200">
                      Run #{jobHistory.length - idx} ({hJob.status.toUpperCase()} - {Math.round(hJob.progress)}%)
                    </option>
                  ))}
                </select>
              </div>
            )}
          </div>

          {/* Inline Progress & Throughput Pill */}
          <div className="flex items-center gap-3 px-3 py-1 bg-zinc-950 border border-zinc-800 text-[11px] font-mono shadow-sm">
            <span className="text-zinc-500 font-bold uppercase tracking-wider text-[10px]">
              {isDryRun ? 'SIMULATION' : 'PROGRESS'}:
            </span>
            <div className="flex items-center gap-2">
              <div className="w-16 h-2 bg-zinc-900 border border-zinc-800 overflow-hidden">
                <div
                  className={`h-full transition-all duration-500 ${
                    isCompleted ? 'bg-emerald-400' : isFailed ? 'bg-rose-500' : isCancelled ? 'bg-zinc-500' : 'bg-sky-400'
                  }`}
                  style={{ width: `${progressPercent}%` }}
                />
              </div>
              <span className="font-extrabold text-sky-400">{progressPercent}%</span>
            </div>
            <span className="text-zinc-700">|</span>
            <span className="text-zinc-400 text-[10px] font-mono">
              <strong className="text-white font-bold">{succRows.toLocaleString()}</strong> rows
            </span>
          </div>
        </div>

        {/* Row 2: Title, Job ID and Cleanly Aligned Actions */}
        <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4 pt-1">
          <div className="space-y-2">
            <h3 className="text-xl sm:text-2xl font-black text-white uppercase font-sans tracking-tight leading-tight">
              {isDryRun ? 'Target Database Migration Dry Run Simulation' : 'Live Target Database Insertion Stream'}
            </h3>
            <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-400 font-mono">
              <span className="text-zinc-500 font-semibold">Job ID:</span>
              <span className="text-sky-400 font-bold bg-sky-950/40 border border-sky-500/30 px-2 py-0.5 select-all">
                {job.id}
              </span>
              <button
                type="button"
                onClick={handleCopyJobId}
                className="px-2 py-0.5 bg-zinc-900 hover:bg-zinc-800 border border-zinc-700 hover:border-zinc-500 text-[10px] text-zinc-300 hover:text-white uppercase transition-colors"
                title="Copy Job ID to clipboard"
              >
                {copiedJobId ? '✓ Copied' : '📋 Copy'}
              </button>
              {isDryRun && (
                <span className="px-2 py-0.5 bg-amber-400/10 border border-amber-400/40 text-amber-300 text-[10px] font-bold uppercase">
                  Dry Run Active
                </span>
              )}
            </div>
          </div>

          {/* Action Buttons: Cleanly Grouped & Always Aligned */}
          <div className="flex flex-wrap items-center gap-2.5 self-start lg:self-center shrink-0">
            {/* Live Monitor Link */}
            <Link
              href={`/execution?jobId=${job.id}`}
              className="h-10 px-4 rounded-none bg-zinc-900 hover:bg-zinc-800 text-sky-400 hover:text-sky-300 text-xs font-bold uppercase tracking-wider border border-sky-400/40 hover:border-sky-400 transition-all font-mono inline-flex items-center gap-2 shadow-sm"
            >
              <span>🖥️</span>
              <span>Open Live Monitor</span>
            </Link>

            {/* Cancel Execution Button for Active Running Jobs */}
            {isRunning && (
              <button
                type="button"
                onClick={() => setShowCancelModal(true)}
                disabled={isCancelling}
                className="h-10 px-4 rounded-none bg-zinc-900 hover:bg-rose-950/60 text-rose-400 hover:text-rose-300 text-xs font-bold uppercase tracking-wider border border-rose-500/40 hover:border-rose-400 transition-all font-mono inline-flex items-center gap-1.5 shadow-sm"
              >
                <span>🛑</span>
                <span>{isCancelling ? 'Cancelling...' : 'Cancel Execution'}</span>
              </button>
            )}

            {/* Execute For Real Secondary Button (Dry Run) */}
            {isDryRun && (
              <button
                type="button"
                onClick={handleExecuteForReal}
                disabled={isExecutingReal || isRunning}
                className="h-10 px-4 rounded-none bg-emerald-500 hover:bg-emerald-400 text-black text-xs font-bold uppercase tracking-wider border border-emerald-400 shadow-md hover:shadow-emerald-500/20 transition-all font-mono inline-flex items-center gap-1.5 disabled:opacity-50"
              >
                <span>⚡</span>
                <span>{isExecutingReal ? 'Queuing Real Migration...' : 'Execute For Real'}</span>
              </button>
            )}

            {/* Re-run Button for Cancelled Jobs */}
            {isCancelled && (
              <button
                type="button"
                onClick={handleRetryJob}
                disabled={isRetrying}
                className="h-10 px-4 rounded-none bg-sky-400 hover:bg-sky-300 text-black text-xs font-bold uppercase tracking-wider border border-sky-400 shadow-md transition-all font-mono inline-flex items-center gap-1.5"
              >
                <span>⚡</span>
                <span>{isRetrying ? 'Starting...' : isDryRun ? 'Re-run Dry Run' : 'Re-run Migration'}</span>
              </button>
            )}

            {/* Active Retry / Resume Button for Failed Jobs */}
            {isFailed && (
              <button
                type="button"
                onClick={handleRetryJob}
                disabled={isRetrying}
                title={
                  canResume
                    ? `Checkpoints will be reused: resumes execution from ${procRows.toLocaleString()} processed rows.`
                    : 'Retries migration from the beginning.'
                }
                className="h-10 px-4 rounded-none bg-rose-500 hover:bg-rose-400 text-black text-xs font-bold uppercase tracking-wider border border-rose-400 shadow-md transition-all font-mono inline-flex items-center gap-1.5"
              >
                <span>⚡</span>
                <span>
                  {isRetrying
                    ? canResume ? 'Resuming...' : 'Queuing Retry...'
                    : canResume
                    ? isDryRun ? 'Resume Dry Run' : 'Resume Migration'
                    : isDryRun ? 'Retry Dry Run' : 'Retry Migration'}
                </span>
              </button>
            )}
          </div>
        </div>
      </div>

      {/* DRY RUN -- NO DATA WAS WRITTEN BANNER */}
      {isDryRun && (
        <div className="p-5 bg-amber-950/40 border border-amber-500/70 text-amber-300 font-mono text-xs space-y-3 shadow-[0_0_20px_rgba(251,191,36,0.15)] animate-fadeIn">
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 font-bold">
            <span className="flex items-center gap-2 text-amber-400 uppercase text-xs tracking-wider">
              <span className="w-2.5 h-2.5 bg-amber-400 rounded-none animate-pulse" />
              DRY RUN SIMULATION -- NO DATA WAS WRITTEN TO TARGET DB
            </span>
            {isDryRunCompleted && (
              <span className="text-[9px] px-2 py-0.5 bg-amber-400 text-black uppercase font-bold tracking-wider">
                SIMULATION VERIFIED ✓
              </span>
            )}
          </div>
          <p className="text-[11px] text-zinc-300 font-sans leading-relaxed">
            All source data extractions, AST column mappings, type coercions, and multi-source merge deduplications were executed on real source chunks. Destination DDL schema modifications and database write operations were safely bypassed.
          </p>
          <div className="pt-2 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 border-t border-amber-500/30">
            <span className="text-[11px] text-amber-200/90 font-mono">
              Ready to persist rows into the destination target database?
            </span>
            <button
              type="button"
              onClick={handleExecuteForReal}
              disabled={isExecutingReal || isRunning}
              className="py-2 px-4 rounded-none bg-emerald-500 hover:bg-emerald-400 text-black text-xs font-bold uppercase tracking-wider font-mono shadow-md whitespace-nowrap disabled:opacity-50"
            >
              {isExecutingReal ? 'Starting Real Run...' : '⚡ Execute For Real'}
            </button>
          </div>
        </div>
      )}

      {/* CANCELLED JOB BANNER */}
      {isCancelled && (
        <div className="p-5 bg-zinc-950/80 border border-zinc-700 text-zinc-300 font-mono text-xs space-y-3 shadow-md animate-fadeIn">
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 font-bold">
            <span className="flex items-center gap-2 text-zinc-400 uppercase text-xs tracking-wider">
              <span className="w-2.5 h-2.5 bg-zinc-500 rounded-none" />
              JOB CANCELLED / RESET BY USER
            </span>
            <span className="text-[9px] px-2 py-0.5 bg-zinc-800 text-zinc-400 border border-zinc-600 uppercase font-bold tracking-wider">
              PLAN UNLOCKED ✓
            </span>
          </div>
          <p className="text-[11px] text-zinc-400 font-sans leading-relaxed">
            {job.error_message || 'This execution job was cancelled. The Docker Agent and migration plan have been released.'}
          </p>
          <div className="pt-2 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 border-t border-zinc-800">
            <span className="text-[11px] text-zinc-400 font-mono">
              You can immediately start a new dry run or full migration below without conflicts.
            </span>
            <button
              type="button"
              onClick={handleRetryJob}
              disabled={isRetrying}
              className="py-2 px-4 rounded-none bg-sky-400 hover:bg-sky-300 text-black text-xs font-bold uppercase tracking-wider font-mono shadow-md whitespace-nowrap"
            >
              {isRetrying ? 'Starting Run...' : isDryRun ? '⚡ Re-run Dry Run' : '⚡ Re-run Migration'}
            </button>
          </div>
        </div>
      )}

      {/* Target Tables With Existing Data Advisory Warning Banner */}
      {job.target_tables_with_existing_data && job.target_tables_with_existing_data.length > 0 && (
        <div className="p-4 bg-amber-950/40 border border-amber-500/50 text-amber-300 font-mono text-xs space-y-2 animate-fadeIn">
          <div className="flex items-center justify-between font-bold uppercase">
            <span className="flex items-center gap-2">
              <span className="w-2 h-2 bg-amber-400 animate-pulse rounded-none" />
              ⚠️ Notice: Target Tables Contain Existing Data
            </span>
            <span className="text-[10px] text-amber-400/80">
              {job.target_tables_with_existing_data.length} Affected Table(s)
            </span>
          </div>
          <p className="text-[11px] text-zinc-300 font-sans leading-relaxed">
            The target database metadata snapshot indicates destination tables already contain data. Incoming rows will be appended according to your plan's primary key conflict resolution policy.
          </p>
          <div className="flex flex-wrap gap-2 pt-1">
            {job.target_tables_with_existing_data.map((tbl, idx) => (
              <span
                key={idx}
                className="px-2.5 py-1 bg-black/60 border border-amber-500/40 text-amber-300 text-[10px] font-mono"
              >
                <strong className="text-white">{tbl.table_name}</strong>: {tbl.existing_row_count.toLocaleString()} existing rows
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Main Animated Progress Bar */}
      <div className="space-y-2">
        <div className="flex items-center justify-between text-xs font-bold font-mono">
          <span className="text-zinc-300 uppercase flex items-center gap-2">
            <span
              className={`w-2 h-2 rounded-none ${
                isDryRunCompleted
                  ? 'bg-amber-400'
                  : isCompleted
                  ? 'bg-emerald-400'
                  : isFailed
                  ? 'bg-rose-500'
                  : isCancelled
                  ? 'bg-zinc-500'
                  : 'bg-sky-400 animate-ping'
              }`}
            />
            {isDryRunCompleted
              ? '✓ Dry Run Simulation Completed (No Data Was Written)'
              : isCompleted
              ? '✓ Target Database Insertion Completed'
              : isFailed
              ? '🚨 Target Insertion Failed'
              : isCancelled
              ? '🛑 Execution Cancelled by User'
              : `Processing Table: ${job.current_table || 'Initializing...'} (${job.current_stage || 'data_streaming'})`}
          </span>
          <span className="text-sky-400 font-extrabold">{progressPercent}%</span>
        </div>

        <div className="w-full h-4 bg-zinc-950 border border-zinc-800 rounded-none overflow-hidden relative p-0.5">
          <div
            className={`h-full transition-all duration-700 ${isCompleted
                ? 'bg-gradient-to-r from-emerald-500 to-teal-300 shadow-[0_0_15px_rgba(52,211,153,0.5)]'
                : isFailed
                  ? 'bg-rose-500'
                  : 'bg-gradient-to-r from-sky-500 via-blue-400 to-sky-300 shadow-[0_0_15px_rgba(56,189,248,0.5)]'
              }`}
            style={{ width: `${progressPercent}%` }}
          />
        </div>
      </div>

      {/* Real-time Target DB Insertion Metrics Cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="p-3.5 rounded-none bg-zinc-950 border border-zinc-800 space-y-1">
          <span className="text-[10px] font-bold text-zinc-500 uppercase block">
            {isDryRun ? 'SIMULATED / VALID ROWS' : 'SUCCESSFUL INSERTIONS'}
          </span>
          <span className="text-lg sm:text-xl font-extrabold text-emerald-400">{succRows.toLocaleString()}</span>
          <span className="text-[10px] text-zinc-400 block font-mono">
            {isDryRun ? 'No rows written to DB' : 'Target DB committed'}
          </span>
        </div>

        <div className="p-3.5 rounded-none bg-zinc-950 border border-zinc-800 space-y-1">
          <span className="text-[10px] font-bold text-zinc-500 uppercase block">TOTAL TARGET ROWS</span>
          <span className="text-lg sm:text-xl font-extrabold text-white">{Math.max(totalRows, succRows).toLocaleString()}</span>
          <span className="text-[10px] text-zinc-400 block font-mono">Expected total</span>
        </div>

        <div className="p-3.5 rounded-none bg-zinc-950 border border-zinc-800 space-y-1">
          <span className="text-[10px] font-bold text-zinc-500 uppercase block">PROCESSED / SKIPPED</span>
          <span className="text-lg sm:text-xl font-extrabold text-sky-400">{procRows.toLocaleString()}</span>
          <span className="text-[10px] text-zinc-400 block font-mono">Deduplicated / processed</span>
        </div>

        <div className="p-3.5 rounded-none bg-zinc-950 border border-zinc-800 space-y-1">
          <span className="text-[10px] font-bold text-zinc-500 uppercase block">FAILED ROWS</span>
          <span className={`text-lg sm:text-xl font-extrabold ${failRows > 0 ? 'text-rose-400' : 'text-zinc-400'}`}>
            {failRows.toLocaleString()}
          </span>
          <span className="text-[10px] text-zinc-400 block font-mono">Schema errors</span>
        </div>
      </div>

      {/* Step-by-Step Pipeline Timeline */}
      <div className="p-4 rounded-none bg-zinc-950 border border-zinc-900 space-y-3">
        <h4 className="text-xs font-bold text-zinc-300 uppercase tracking-wider flex items-center gap-2">
          <span className="w-1.5 h-1.5 rounded-none bg-sky-400" />
          Target Pipeline Stage Stepper
        </h4>

        <div className="grid grid-cols-1 sm:grid-cols-4 gap-2 text-xs font-mono">
          {stages.map((stg, idx) => {
            const isDone = isCompleted || (currentStep > idx);
            const isCurrent = isRunning && currentStep === idx;

            return (
              <div
                key={stg.key}
                className={`p-3 rounded-none border transition-all ${isDone
                    ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                    : isCurrent
                      ? 'bg-sky-400/15 border-sky-400 text-sky-400 shadow-[0_0_12px_rgba(56,189,248,0.2)] animate-pulse'
                      : 'bg-black border-zinc-900 text-zinc-600'
                  }`}
              >
                <div className="flex items-center justify-between text-[10px] font-bold uppercase mb-1">
                  <span>{stg.label}</span>
                  <span>{isDone ? '✓' : isCurrent ? '⚡' : '○'}</span>
                </div>
                <div className="text-[11px] font-bold truncate text-white">{stg.desc}</div>
              </div>
            );
          })}
        </div>
      </div>

      {/* AI Error Diagnosis & Remediation Card (Failed State) */}
      {isFailed && (
        <div className="p-5 rounded-none bg-rose-950/30 border border-rose-500/50 space-y-4 text-xs font-mono shadow-[0_0_25px_rgba(244,63,94,0.15)]">
          <div className="flex items-center justify-between border-b border-rose-500/30 pb-3 font-sans">
            <div className="flex items-center gap-2">
              <span className="w-2.5 h-2.5 bg-rose-500 animate-ping rounded-none" />
              <h4 className="text-sm font-extrabold text-rose-300 uppercase tracking-wider">
                🤖 AI Execution Failure Diagnosis & Self-Healing Guide
              </h4>
            </div>
            {diagnosis?.root_cause_category && (
              <span className="px-2.5 py-0.5 text-[10px] font-mono font-bold uppercase bg-rose-500/20 text-rose-300 border border-rose-500/40">
                {diagnosis.root_cause_category}
              </span>
            )}
          </div>

          {/* Diagnosis Plain English Summary */}
          {diagnosis?.summary ? (
            <div className="text-zinc-200 text-xs font-sans leading-relaxed">
              <strong className="text-rose-400 uppercase font-mono mr-2 font-bold">Explanation:</strong>
              {diagnosis.summary}
            </div>
          ) : (
            <div className="text-rose-400 text-xs leading-relaxed font-sans">
              <strong className="uppercase font-mono mr-2 font-bold">Raw Error Trace:</strong>
              {job.error_message || 'Execution error encountered during ETL streaming.'}
            </div>
          )}

          {/* Remediation Steps */}
          {diagnosis?.fix_steps && diagnosis.fix_steps.length > 0 && (
            <div className="space-y-1.5 p-3 bg-black/60 border border-rose-500/30">
              <span className="text-[10px] font-bold text-sky-400 uppercase tracking-wider block">
                🛠️ Step-by-Step Remediation Actions:
              </span>
              <ul className="list-disc list-inside text-zinc-300 text-[11px] space-y-1">
                {diagnosis.fix_steps.map((step: string, sIdx: number) => (
                  <li key={sIdx}>{step}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Copyable Fix Command (With Password Placeholders) */}
          {diagnosis?.copyable_fix_command && (
            <div className="space-y-1.5">
              <div className="flex items-center justify-between text-[10px] font-bold text-amber-400 uppercase">
                <span>📋 Copyable Container Fix Command (Password Placeholders Retained):</span>
                <button
                  type="button"
                  onClick={() => {
                    navigator.clipboard.writeText(diagnosis.copyable_fix_command || '');
                    toast.success('Remediation command copied to clipboard!');
                  }}
                  className="px-2 py-0.5 bg-amber-400/10 hover:bg-amber-400/20 text-amber-300 border border-amber-400/30 uppercase text-[9px] font-bold"
                >
                  Copy Command
                </button>
              </div>
              <pre className="p-3 bg-zinc-950 border border-zinc-800 text-amber-300/90 text-[11px] font-mono whitespace-pre-wrap overflow-x-auto">
                {diagnosis.copyable_fix_command}
              </pre>
            </div>
          )}

          {/* Trigger Diagnosis Manual Button if missing */}
          {!diagnosis && (
            <button
              type="button"
              onClick={handleTriggerDiagnosis}
              disabled={isDiagnosing}
              className="py-2 px-4 bg-rose-500 hover:bg-rose-400 text-black text-xs font-bold uppercase tracking-wider border border-rose-500 font-mono transition-colors"
            >
              {isDiagnosing ? 'Analyzing Error with AI...' : '🤖 Synthesize AI Diagnosis'}
            </button>
          )}
        </div>
      )}

      {/* Live Agent Terminal Log Output */}
      <div className="space-y-2">
        <div className="flex items-center justify-between text-xs text-zinc-400 font-bold uppercase">
          <span>Agent Real-Time Execution Log Stream</span>
          <span className="text-[10px] text-sky-400">{logs.length} Log Events</span>
        </div>

        <div className="p-4 rounded-none bg-zinc-950 border border-zinc-900 text-xs font-mono max-h-40 overflow-y-auto space-y-1">
          {logs.length === 0 ? (
            <div className="text-zinc-600 italic">Waiting for initial log events from Docker Agent...</div>
          ) : (
            logs.map((log: string, lIdx: number) => (
              <div key={lIdx} className="text-sky-300/90 hover:text-white transition-colors text-[11px]">
                {log}
              </div>
            ))
          )}
        </div>
      </div>

      {/* Cancellation Confirmation Modal */}
      {showCancelModal && (
        <div
          onClick={() => setShowCancelModal(false)}
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/85 backdrop-blur-md animate-fadeIn"
        >
          <div
            onClick={(e) => e.stopPropagation()}
            className="w-full max-w-md bg-zinc-950 border border-rose-500/50 p-6 space-y-5 shadow-[0_0_50px_rgba(244,63,94,0.3)] font-mono relative overflow-hidden"
          >
            <div className="absolute top-0 left-0 right-0 h-1 bg-gradient-to-r from-rose-500 via-amber-400 to-rose-500" />
            
            <div className="flex items-center justify-between border-b border-zinc-800 pb-3">
              <div className="flex items-center gap-2 text-rose-400 font-bold uppercase text-xs tracking-wider">
                <span className="w-2.5 h-2.5 bg-rose-500 animate-ping rounded-none" />
                Abort / Cancel Execution Job
              </div>
              <button
                type="button"
                onClick={() => setShowCancelModal(false)}
                className="text-zinc-500 hover:text-white text-base font-bold transition-colors"
              >
                ✕
              </button>
            </div>

            <div className="space-y-3 text-xs">
              <p className="text-zinc-200 font-sans leading-relaxed">
                Are you sure you want to cancel this {isDryRun ? 'Dry Run simulation' : 'Live Migration'} job?
              </p>
              <div className="p-3 bg-rose-950/30 border border-rose-900/60 text-rose-300 text-[11px] space-y-1">
                <p className="font-bold">⚠️ Notice:</p>
                <p className="text-zinc-400 font-sans leading-relaxed">
                  Cancelling will halt in-flight ETL processing, unassign the Docker Agent, and immediately unlock this migration plan so you can start a new run without conflicts.
                </p>
              </div>
              <div className="space-y-1 pt-1">
                <label className="text-[10px] uppercase font-bold text-zinc-400 block">
                  Optional Reason (Audit Log):
                </label>
                <input
                  type="text"
                  value={cancelReason}
                  onChange={(e) => setCancelReason(e.target.value)}
                  placeholder="e.g., Docker container stopped or parameter change"
                  className="w-full px-3 py-2 bg-black border border-zinc-800 focus:border-rose-400 focus:outline-none text-zinc-200 text-xs font-mono"
                />
              </div>
            </div>

            <div className="flex items-center justify-end gap-3 pt-2 border-t border-zinc-800">
              <button
                type="button"
                onClick={() => setShowCancelModal(false)}
                className="px-4 py-2 bg-zinc-900 hover:bg-zinc-800 text-zinc-300 border border-zinc-700 text-xs font-bold uppercase transition-colors"
              >
                Keep Running
              </button>
              <button
                type="button"
                onClick={handleCancelJob}
                disabled={isCancelling}
                className="px-4 py-2 bg-rose-600 hover:bg-rose-500 text-white border border-rose-400 text-xs font-bold uppercase shadow-[0_0_15px_rgba(244,63,94,0.4)] flex items-center gap-1.5 transition-all"
              >
                <span>🛑</span>
                <span>{isCancelling ? 'Cancelling...' : 'Confirm Cancel'}</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default JobExecutionBanner;
