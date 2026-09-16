'use client';

import React, { useState, useEffect } from 'react';
import Link from 'next/link';
import { ExecutionJobResponse } from '../../types/execution';
import executionService from '../../services/executionService';
import JobExecutionBanner from '../../components/plans/JobExecutionBanner';
import { ArrowLeft, ChevronRight, RefreshCw } from 'lucide-react';

export default function ExecutionPage() {
  const [executions, setExecutions] = useState<ExecutionJobResponse[]>([]);
  const [selectedJob, setSelectedJob] = useState<ExecutionJobResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const fetchExecutions = async (preserveSelected = false) => {
    try {
      setLoading(true);
      setErrorMsg(null);
      const list = await executionService.listUserExecutions();
      setExecutions(list);
      if (!preserveSelected && list && list.length > 0) {
        const urlParams = new URLSearchParams(window.location.search);
        const queryJobId = urlParams.get('jobId');
        const matched = queryJobId ? list.find((j) => j.id === queryJobId) : null;
        setSelectedJob(matched || list[0]);
      }
    } catch (err: any) {
      const msg = err.response?.data?.detail || err.message || 'Failed to fetch execution jobs.';
      setErrorMsg(msg);
    } finally {
      setLoading(false);
    }
  };

  // Handle job progress updates from the banner's own 2s polling loop.
  // Update the page's metrics and job list in-place WITHOUT re-fetching all jobs
  // (which would create a new prop object and restart the banner's interval).
  const handleJobUpdated = (updatedJob: ExecutionJobResponse) => {
    setSelectedJob(updatedJob);
    setExecutions((prev) =>
      prev.map((j) => (j.id === updatedJob.id ? updatedJob : j))
    );
    // Only re-fetch the full job list when a job reaches a terminal state
    // so that aggregate metrics (total rows, completed count) stay accurate.
    const terminalStatus = ['completed', 'failed', 'cancelled'];
    if (terminalStatus.includes((updatedJob.status || '').toLowerCase())) {
      fetchExecutions(true);
    }
  };

  // Fetch job list ONCE on mount — banner handles its own live 2s polling.
  useEffect(() => {
    fetchExecutions();
  }, []);

  const totalMigratedRows = executions.reduce((acc, job) => acc + (job.successful_rows || 0), 0);
  const activeJobsCount = executions.filter((job) => ['running', 'queued', 'preparing'].includes((job.status || '').toLowerCase())).length;
  const completedJobsCount = executions.filter((job) => (job.status || '').toLowerCase() === 'completed').length;

  return (
    <div className="min-h-screen bg-black text-slate-100 flex flex-col justify-between selection:bg-sky-400 selection:text-black">
      {/* Background Glow */}
      <div className="fixed top-0 left-1/2 -translate-x-1/2 w-full max-w-7xl h-96 bg-gradient-to-b from-sky-400/15 via-sky-500/5 to-transparent blur-3xl pointer-events-none -z-10" />

      <main className="w-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8 flex-1 font-mono">
        {/* Top Breadcrumb Navigation */}
        <div className="flex items-center gap-2 text-xs text-zinc-400 font-mono">
          <Link href="/dashboard" className="text-sky-400 hover:text-sky-300 flex items-center gap-1 font-bold">
            <ArrowLeft className="w-3.5 h-3.5" />
            <span>Dashboard</span>
          </Link>
          <ChevronRight className="w-3.5 h-3.5 text-zinc-600" />
          <span className="text-white font-bold">Migration Execution Monitor</span>
        </div>

        {/* Header */}
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 border-b border-zinc-800 pb-6 font-sans">
          <div>
            <div className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-none text-[11px] font-mono font-bold uppercase tracking-widest bg-sky-400/10 text-sky-400 border border-sky-400/30 mb-2">
              TARGET DB INSERTION ENGINE
            </div>
            <h1 className="text-3xl font-extrabold text-white tracking-tight sm:text-4xl uppercase font-sans">
              Migration Execution Monitor
            </h1>
            <p className="text-zinc-400 text-xs sm:text-sm max-w-2xl mt-1 leading-relaxed font-mono">
              Real-time progression monitor tracking chunked target database insertions, stream throughput, and agent execution logs.
            </p>
          </div>

          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => fetchExecutions()}
              className="py-3 px-5 rounded-none bg-zinc-900 hover:bg-zinc-800 text-sky-400 text-xs font-mono font-bold uppercase tracking-wider border border-sky-400/30 transition-colors shadow-lg flex items-center gap-2"
            >
              <RefreshCw className="w-3.5 h-3.5" />
              <span>Refresh Jobs</span>
            </button>
          </div>
        </div>

        {/* Global Execution Metrics Overview */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <div className="p-4 rounded-none bg-black border border-zinc-800 space-y-1 shadow-xl">
            <span className="text-[10px] font-mono font-bold text-zinc-500 uppercase block">TOTAL JOBS DISPATCHED</span>
            <span className="text-2xl font-extrabold text-white font-mono">{executions.length}</span>
            <span className="text-[10px] text-zinc-400 block font-mono">Platform execution history</span>
          </div>

          <div className="p-4 rounded-none bg-black border border-zinc-800 space-y-1 shadow-xl">
            <span className="text-[10px] font-mono font-bold text-zinc-500 uppercase block">ACTIVE INSERTS</span>
            <span className="text-2xl font-extrabold text-sky-400 font-mono">{activeJobsCount}</span>
            <span className="text-[10px] text-sky-400/80 block font-mono">Currently streaming</span>
          </div>

          <div className="p-4 rounded-none bg-black border border-zinc-800 space-y-1 shadow-xl">
            <span className="text-[10px] font-mono font-bold text-zinc-500 uppercase block">COMPLETED JOBS</span>
            <span className="text-2xl font-extrabold text-emerald-400 font-mono">{completedJobsCount}</span>
            <span className="text-[10px] text-zinc-400 block font-mono">100% Target verified</span>
          </div>

          <div className="p-4 rounded-none bg-black border border-zinc-800 space-y-1 shadow-xl">
            <span className="text-[10px] font-mono font-bold text-zinc-500 uppercase block">TOTAL TARGET ROWS</span>
            <span className="text-2xl font-extrabold text-sky-400 font-mono">{totalMigratedRows.toLocaleString()}</span>
            <span className="text-[10px] text-zinc-400 block font-mono">Total committed records</span>
          </div>
        </div>

        {/* Selected Job Live Banner */}
        {selectedJob && (() => {
          const isJobActive = ['running', 'queued', 'preparing'].includes((selectedJob.status || '').toLowerCase());
          return (
            <div className="space-y-3">
              <div className="text-xs font-mono font-bold text-sky-400 uppercase tracking-wider flex items-center gap-2">
                <span className={`w-2 h-2 rounded-none bg-sky-400 inline-block ${isJobActive ? 'animate-pulse' : ''}`} />
                {isJobActive ? 'Active Job Live Progression:' : 'Selected Execution Run Summary:'} {selectedJob.id}
              </div>
              <JobExecutionBanner
                job={selectedJob}
                onJobUpdated={handleJobUpdated}
              />
            </div>
          );
        })()}

        {/* Executions History Table */}
        <div className="space-y-4 font-mono">
          <div className="text-xs font-mono font-bold text-white uppercase tracking-wider flex items-center gap-2">
            <span className="w-2 h-2 rounded-none bg-sky-400 inline-block" />
            Execution Job History ({executions.length})
          </div>

          {loading ? (
            <div className="p-12 text-center bg-black border border-zinc-800 text-zinc-400 text-xs font-mono">
              Loading execution jobs history...
            </div>
          ) : errorMsg ? (
            <div className="p-6 bg-black border border-rose-500/30 text-rose-400 text-xs font-mono">
              {errorMsg}
            </div>
          ) : executions.length === 0 ? (
            <div className="p-12 text-center bg-black border border-zinc-800 text-zinc-500 text-xs font-mono">
              No target insertion jobs dispatched yet. Approve a migration plan to execute streaming ETL.
            </div>
          ) : (
            <div className="border border-zinc-800 bg-black overflow-x-auto">
              <table className="w-full text-left text-xs font-mono border-collapse">
                <thead>
                  <tr className="border-b border-zinc-800 bg-zinc-950 text-zinc-400 text-[10px] uppercase font-bold tracking-wider">
                    <th className="p-3">Job ID</th>
                    <th className="p-3">Status</th>
                    <th className="p-3">Current Stage</th>
                    <th className="p-3">Target Rows</th>
                    <th className="p-3">Failed Rows</th>
                    <th className="p-3">Dispatched At</th>
                    <th className="p-3 text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-900">
                  {executions.map((j) => {
                    const isSel = selectedJob?.id === j.id;
                    const stLower = (j.status || 'pending').toLowerCase();
                    const isDone = stLower === 'completed';
                    const isErr = stLower === 'failed';

                    return (
                      <tr
                        key={j.id}
                        className={`hover:bg-zinc-950/80 transition-colors ${
                          isSel ? 'bg-sky-400/5 border-l-2 border-l-sky-400' : ''
                        }`}
                      >
                        <td className="p-3 font-bold text-sky-400">
                          <div className="flex items-center gap-1.5" title={j.id}>
                            <span className="truncate max-w-[110px]">{j.id}</span>
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                navigator.clipboard.writeText(j.id);
                              }}
                              className="text-[9px] text-zinc-500 hover:text-sky-300 font-mono px-1 py-0.5 border border-zinc-800 hover:border-sky-400/40 uppercase"
                              title="Copy full Job ID"
                            >
                              Copy
                            </button>
                          </div>
                        </td>
                        <td className="p-3">
                          <span
                            className={`px-2 py-0.5 text-[9px] font-bold uppercase rounded-none border ${
                              isDone
                                ? 'bg-emerald-400/10 text-emerald-400 border-emerald-400/30'
                                : isErr
                                ? 'bg-rose-500/10 text-rose-400 border-rose-500/30'
                                : 'bg-sky-400/10 text-sky-400 border-sky-400/30'
                            }`}
                          >
                            {stLower.toUpperCase()}
                          </span>
                        </td>
                        <td className="p-3 text-zinc-300 text-[11px]">{j.current_stage || 'processing'}</td>
                        <td className="p-3 font-bold text-white">{(j.successful_rows || 0).toLocaleString()}</td>
                        <td className="p-3">
                          <span className={(j.failed_rows || 0) > 0 ? 'text-rose-400 font-bold' : 'text-zinc-500'}>
                            {(j.failed_rows || 0).toLocaleString()}
                          </span>
                        </td>
                        <td className="p-3 text-zinc-400 text-[11px]">
                          {j.created_at ? new Date(j.created_at).toLocaleString() : 'N/A'}
                        </td>
                        <td className="p-3 text-right">
                          <button
                            type="button"
                            onClick={() => setSelectedJob(j)}
                            className="px-3 py-1 bg-zinc-900 hover:bg-zinc-800 text-sky-400 text-[10px] font-bold uppercase border border-zinc-800"
                          >
                            View Job
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
