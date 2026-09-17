'use client';

import React from 'react';
import {
  PlanDetailResponse,
  TransformationPlanAST,
  PlanVersionListItem,
  PlanVersionDetailResponse,
  RefinementFeedback,
} from '../../types/migrationPlan';
import { PlanReadinessSignals } from './PlanReadinessSignals';
import PlanPlainLanguageSummary from './PlanPlainLanguageSummary';
import RefinementFeedbackCard from './RefinementFeedbackCard';
import { History, Sparkles, AlertTriangle, ArrowRight, RotateCcw, CheckCircle2, ShieldAlert } from 'lucide-react';

interface PlanOverviewTabProps {
  plan: PlanDetailResponse;
  ast: TransformationPlanAST;
  versions: PlanVersionListItem[];
  selectedVersionNum: number | null;
  previewVersionDetail: PlanVersionDetailResponse | null;
  isLoadingVersion: boolean;
  isRestoringVersion: boolean;
  isHistoricalPreview: boolean;
  onSelectVersion: (versionNum: number | null) => void;
  onRestoreVersion: (versionNum: number) => Promise<void>;
  refinementPrompt: string;
  setRefinementPrompt: (v: string) => void;
  isRefining: boolean;
  refiningPromptEcho: string;
  refiningElapsedSec: number;
  onRefinePlan: (e: React.FormEvent) => Promise<void>;
  activeFeedback: RefinementFeedback | null;
  diagnosticRef: React.RefObject<HTMLDivElement>;
  onNavigateToMappings: () => void;
  onNavigateToExecute: () => void;
}

export const PlanOverviewTab: React.FC<PlanOverviewTabProps> = ({
  plan,
  ast,
  versions,
  selectedVersionNum,
  previewVersionDetail,
  isLoadingVersion,
  isRestoringVersion,
  isHistoricalPreview,
  onSelectVersion,
  onRestoreVersion,
  refinementPrompt,
  setRefinementPrompt,
  isRefining,
  refiningPromptEcho,
  refiningElapsedSec,
  onRefinePlan,
  activeFeedback,
  diagnosticRef,
  onNavigateToMappings,
  onNavigateToExecute,
}) => {
  const isApproved = plan.status === 'completed' || plan.status === 'approved';

  return (
    <div className="space-y-8 animate-fadeIn font-sans">
      {/* 1. Readiness Signals Scorecard */}
      <div className="p-6 rounded-none bg-black border border-zinc-800 space-y-4 shadow-xl">
        <div className="flex items-center justify-between border-b border-zinc-800 pb-3">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-none bg-sky-400" />
            <h3 className="text-xs font-mono font-bold text-white uppercase tracking-wider">
              AI Feasibility & Quality Signals
            </h3>
          </div>
          <span className="text-[11px] font-mono text-zinc-400">
            Overall Confidence:{' '}
            <strong className="text-sky-400">
              {Math.round((plan.confidence_score || ast?.confidence_score || 0.9) * 100)}%
            </strong>
          </span>
        </div>
        <PlanReadinessSignals ast={ast} planConfidenceScore={plan.confidence_score} />
      </div>

      {/* 2. Plain Language Summary */}
      {ast && <PlanPlainLanguageSummary ast={ast} />}

      {/* 3. AI Execution Strategy Narrative */}
      {ast?.ai_explanation && (
        <div className="p-6 rounded-none bg-black border border-zinc-800 space-y-3 shadow-xl">
          <div className="flex items-center gap-2 border-b border-zinc-800 pb-3">
            <Sparkles className="w-4 h-4 text-sky-400" />
            <h4 className="text-xs font-mono font-bold text-sky-400 uppercase tracking-wider">
              AI Execution Strategy
            </h4>
          </div>
          <p className="text-xs text-zinc-300 font-sans leading-relaxed">
            {ast.ai_explanation}
          </p>
        </div>
      )}

      {/* 4. Feasibility & Diagnostic Card */}
      {plan.validation_errors && (
        <div
          ref={diagnosticRef}
          className={`p-6 rounded-none border font-mono text-xs space-y-3 shadow-xl ${
            plan.is_valid
              ? 'bg-emerald-950/20 border-emerald-500/40 text-emerald-300'
              : 'bg-rose-950/30 border-rose-500/50 text-rose-300 shadow-[0_0_20px_rgba(244,63,94,0.15)]'
          }`}
        >
          <div className="flex items-center justify-between font-bold uppercase">
            <span className="flex items-center gap-2">
              <span
                className={`w-2 h-2 rounded-none ${
                  plan.is_valid ? 'bg-emerald-400' : 'bg-rose-500 animate-ping'
                }`}
              />
              {plan.is_valid ? '✓ PLAN FEASIBILITY VERIFIED' : '🚨 INVALID PLAN EDITS DETECTED'}
            </span>
            <span className="text-[10px] text-zinc-400">
              Status: {plan.is_valid ? 'FEASIBLE' : 'EXECUTION BLOCKED'}
            </span>
          </div>

          <p className="text-zinc-300 leading-relaxed font-sans text-xs">
            {plan.validation_errors.explanation}
          </p>

          {/* Validation Errors */}
          {plan.validation_errors.errors && plan.validation_errors.errors.length > 0 && (
            <div className="p-4 bg-black/80 border border-rose-500/40 text-rose-400 space-y-2 mt-2">
              <span className="font-bold uppercase text-[10px] text-rose-400 block">
                Schema Feasibility Errors ({plan.validation_errors.errors.length}):
              </span>
              <ul className="list-disc list-inside text-[11px] space-y-1 font-mono">
                {plan.validation_errors.errors.map((err: string, i: number) => (
                  <li key={i}>{err}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Validation Warnings */}
          {plan.validation_errors.warnings && plan.validation_errors.warnings.length > 0 && (
            <div className="p-4 bg-black/80 border border-amber-500/40 text-amber-400 space-y-2 mt-2">
              <span className="font-bold uppercase text-[10px] text-amber-400 block">
                Architecture Warnings ({plan.validation_errors.warnings.length}):
              </span>
              <ul className="list-disc list-inside text-[11px] space-y-1 font-mono">
                {plan.validation_errors.warnings.map((warn: string, i: number) => (
                  <li key={i}>{warn}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Revert Action Button for Invalid Refinements / Edits */}
          {(() => {
            const lastValidVer = versions.find((v) => v.is_valid);
            if (plan.is_valid || !lastValidVer) return null;
            return (
              <div className="pt-2">
                <button
                  type="button"
                  onClick={() => onRestoreVersion(lastValidVer.version_number)}
                  disabled={isRestoringVersion}
                  className="py-2.5 px-5 rounded-none bg-rose-500 hover:bg-rose-400 text-black text-xs font-mono font-bold uppercase tracking-wider transition-colors shadow-md flex items-center gap-2"
                >
                  <RotateCcw className="w-3.5 h-3.5" />
                  <span>
                    {isRestoringVersion
                      ? 'Restoring Blueprint...'
                      : `↩ Revert to Last Valid Version (v${lastValidVer.version_number})`}
                  </span>
                </button>
              </div>
            );
          })()}
        </div>
      )}

      {/* 5. AI Refinement Feasibility & Response Card */}
      {activeFeedback && (
        <RefinementFeedbackCard
          feedback={activeFeedback}
          versionNumber={isHistoricalPreview ? selectedVersionNum : versions?.[0]?.version_number}
        />
      )}

      {/* 6. Active AI Refinement Progress Banner */}
      {isRefining && (
        <div className="p-5 rounded-none bg-zinc-950 border border-sky-400/50 space-y-3 font-mono shadow-[0_0_25px_rgba(56,189,248,0.15)] relative overflow-hidden animate-fadeIn">
          <div className="absolute top-0 left-0 right-0 h-0.5 bg-gradient-to-r from-sky-400 via-indigo-500 to-sky-400 animate-pulse" />
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-none bg-sky-400/10 border border-sky-400/40 flex items-center justify-center text-sky-400">
                <svg className="w-5 h-5 animate-spin" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
                </svg>
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <h4 className="text-xs font-mono font-bold text-white uppercase tracking-wider">
                    AI Blueprint Refinement in Progress
                  </h4>
                  <span className="px-2 py-0.5 text-[10px] font-mono font-bold rounded-none bg-sky-400/20 text-sky-300 border border-sky-400/40">
                    {refiningElapsedSec}s elapsed
                  </span>
                </div>
                <p className="text-xs text-zinc-400 font-sans mt-0.5">
                  {refiningPromptEcho
                    ? `Evaluating feedback: "${refiningPromptEcho}"`
                    : 'Analyzing multi-database schemas and re-evaluating transformation AST in background...'}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2 text-right">
              <span className="w-2 h-2 rounded-none bg-sky-400 animate-ping" />
              <span className="text-[11px] text-sky-400 font-mono font-bold uppercase tracking-wider">
                PROCESSING (BACKGROUND POLLING 2S)
              </span>
            </div>
          </div>
          <div className="text-[11px] text-zinc-500 font-mono border-t border-zinc-900 pt-2 flex items-center justify-between">
            <span>You may refresh or navigate away — your refinement task continues executing on the server without interruption.</span>
            <span className="text-zinc-400">LLM Timeout: 360s</span>
          </div>
        </div>
      )}

      {/* 7. Natural Language AI Plan Refinement Input */}
      {!isApproved && (
        <form onSubmit={onRefinePlan} className="p-6 rounded-none bg-black border border-sky-400/40 space-y-4 shadow-2xl">
          <div>
            <h4 className="text-xs font-mono font-bold text-white uppercase tracking-wider flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-sky-400" />
              Refine Blueprint with LLM Prompt Suggestion
            </h4>
            <p className="text-xs text-zinc-400 font-sans mt-0.5">
              Type custom adjustments to prompt the LLM for blueprint re-review and re-evaluation.
            </p>
          </div>

          <div className="space-y-3">
            <textarea
              required
              rows={3}
              disabled={isRefining}
              value={refinementPrompt}
              onChange={(e) => setRefinementPrompt(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  if (refinementPrompt.trim() && !isRefining) {
                    onRefinePlan(e as any);
                  }
                }
              }}
              placeholder="e.g. Map user_id to account_uuid and convert status int enum to string varchar"
              className="w-full px-4 py-3 rounded-none bg-zinc-950 border border-zinc-800 text-white text-xs placeholder-zinc-600 focus:outline-none focus:border-sky-400 font-sans transition-colors disabled:opacity-50 resize-y min-h-[80px] leading-relaxed"
            />
            <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 pt-1">
              <span className="text-[10px] text-zinc-500 font-mono">
                💡 Press <kbd className="px-1.5 py-0.5 bg-zinc-900 border border-zinc-800 rounded-none text-zinc-300">Enter</kbd> to refine, <kbd className="px-1.5 py-0.5 bg-zinc-900 border border-zinc-800 rounded-none text-zinc-300">Shift + Enter</kbd> for new line
              </span>
              <button
                type="submit"
                disabled={isRefining}
                className="py-2.5 px-6 rounded-none bg-sky-400 hover:bg-sky-300 text-black text-xs font-bold uppercase tracking-wider transition-colors shadow-lg shadow-sky-950/50 disabled:opacity-50 whitespace-nowrap font-mono inline-flex items-center gap-2 self-end sm:self-auto"
              >
                {isRefining ? (
                  <>
                    <span className="w-2 h-2 rounded-none bg-black animate-ping" />
                    <span>Refining in Background ({refiningElapsedSec}s)...</span>
                  </>
                ) : (
                  'Refine with LLM'
                )}
              </button>
            </div>
          </div>

          {isRefining && (
            <div className="flex items-center gap-2 text-xs text-sky-400 font-mono animate-pulse pt-1">
              <span className="w-2 h-2 rounded-none bg-sky-400 animate-ping" />
              <span>
                LLM background task is actively running ({refiningElapsedSec}s elapsed). The blueprint will automatically update upon completion.
              </span>
            </div>
          )}
        </form>
      )}

      {/* 8. Version History Timeline Panel */}
      {versions.length > 0 && (
        <div className="p-6 rounded-none bg-black border border-zinc-800 space-y-4 shadow-xl">
          <div className="flex items-center justify-between border-b border-zinc-800 pb-3">
            <div className="flex items-center gap-2">
              <History className="w-4 h-4 text-sky-400" />
              <h4 className="text-xs font-mono font-bold text-white uppercase tracking-wider">
                Blueprint Version History ({versions.length} versions)
              </h4>
            </div>
            {isHistoricalPreview && (
              <button
                type="button"
                onClick={() => onSelectVersion(null)}
                className="text-xs font-mono text-amber-400 hover:text-amber-300 uppercase font-bold"
              >
                ✕ Exit Preview
              </button>
            )}
          </div>

          <div className="space-y-3">
            {versions.map((ver, idx) => {
              const isLatest = idx === 0;
              const isCurrentlySelected = selectedVersionNum === ver.version_number;
              const labelType =
                ver.edit_type === 'initial_ai_generation'
                  ? 'Initial AI Generation'
                  : ver.edit_type === 'llm_refinement'
                  ? 'LLM Refinement'
                  : ver.edit_type === 'version_restored'
                  ? 'Version Restored'
                  : 'Manual Column Edit';

              return (
                <div
                  key={ver.id}
                  className={`p-4 rounded-none border transition-all flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 font-mono text-xs ${
                    isCurrentlySelected
                      ? 'bg-amber-950/20 border-amber-500/60 shadow-[0_0_15px_rgba(245,158,11,0.15)]'
                      : isLatest
                      ? 'bg-zinc-950 border-sky-400/40'
                      : 'bg-zinc-950 border-zinc-800/80 hover:border-zinc-700'
                  }`}
                >
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="font-bold text-white uppercase">v{ver.version_number}</span>
                      {isLatest && (
                        <span className="px-1.5 py-0.2 text-[9px] bg-sky-400/10 text-sky-400 border border-sky-400/30 uppercase font-bold">
                          Active Version
                        </span>
                      )}
                      <span className="px-1.5 py-0.2 text-[9px] bg-zinc-800 text-zinc-300 border border-zinc-700 uppercase">
                        {labelType}
                      </span>
                      {ver.is_valid ? (
                        <span className="px-1.5 py-0.2 text-[9px] bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 uppercase">
                          Valid
                        </span>
                      ) : (
                        <span className="px-1.5 py-0.2 text-[9px] bg-rose-500/10 text-rose-400 border border-rose-500/30 uppercase">
                          Invalid
                        </span>
                      )}
                    </div>
                    {ver.user_feedback && (
                      <p className="text-[11px] text-zinc-400 font-sans italic">
                        "{ver.user_feedback}"
                      </p>
                    )}
                    <div className="text-[10px] text-zinc-500">
                      Created: {new Date(ver.created_at).toLocaleString()}
                    </div>
                  </div>

                  <div className="flex items-center gap-2 self-end sm:self-center">
                    {!isLatest && (
                      <button
                        type="button"
                        onClick={() => onSelectVersion(isCurrentlySelected ? null : ver.version_number)}
                        disabled={isLoadingVersion}
                        className={`px-3 py-1.5 rounded-none text-xs font-bold uppercase tracking-wider border transition-colors ${
                          isCurrentlySelected
                            ? 'bg-zinc-800 text-zinc-200 border-zinc-700'
                            : 'bg-zinc-900 text-zinc-400 hover:text-white border-zinc-800'
                        }`}
                      >
                        {isCurrentlySelected ? 'Exit Preview' : 'Inspect Snapshot'}
                      </button>
                    )}
                    {!isLatest && (
                      <button
                        type="button"
                        onClick={() => onRestoreVersion(ver.version_number)}
                        disabled={isRestoringVersion}
                        className="px-3 py-1.5 rounded-none bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 text-xs font-bold uppercase tracking-wider border border-amber-500/40 transition-colors"
                      >
                        {isRestoringVersion ? 'Restoring...' : 'Restore'}
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 9. Next Steps Navigation */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-2">
        <button
          type="button"
          onClick={onNavigateToMappings}
          className="p-5 bg-zinc-950 hover:bg-zinc-900 border border-zinc-800 hover:border-sky-400/50 text-left transition-all group font-mono flex items-center justify-between shadow-lg"
        >
          <div>
            <span className="text-[10px] text-sky-400 uppercase font-bold tracking-widest block mb-1">
              Step 02
            </span>
            <h4 className="text-sm font-bold text-white group-hover:text-sky-300 uppercase">
              Inspect Table Mappings ({ast?.table_mappings?.length || 0} Tables) →
            </h4>
            <p className="text-xs text-zinc-400 font-sans mt-0.5">
              Review destination schemas, column transformations, and primary key strategies.
            </p>
          </div>
          <ArrowRight className="w-5 h-5 text-zinc-600 group-hover:text-sky-400 transition-colors shrink-0 ml-3" />
        </button>

        <button
          type="button"
          onClick={onNavigateToExecute}
          className="p-5 bg-zinc-950 hover:bg-zinc-900 border border-zinc-800 hover:border-sky-400/50 text-left transition-all group font-mono flex items-center justify-between shadow-lg"
        >
          <div>
            <span className="text-[10px] text-sky-400 uppercase font-bold tracking-widest block mb-1">
              Step 03
            </span>
            <h4 className="text-sm font-bold text-white group-hover:text-sky-300 uppercase">
              Execute & Stream Migration →
            </h4>
            <p className="text-xs text-zinc-400 font-sans mt-0.5">
              Simulate dry run or dispatch execution job to local Docker Agent.
            </p>
          </div>
          <ArrowRight className="w-5 h-5 text-zinc-600 group-hover:text-sky-400 transition-colors shrink-0 ml-3" />
        </button>
      </div>
    </div>
  );
};

export default PlanOverviewTab;
