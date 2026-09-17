import React, { useState } from 'react';
import { RefinementFeedback } from '../../types/migrationPlan';

interface RefinementFeedbackCardProps {
  feedback: RefinementFeedback;
  versionNumber?: number | null;
  onDismiss?: () => void;
}

export const RefinementFeedbackCard: React.FC<RefinementFeedbackCardProps> = ({
  feedback,
  versionNumber,
  onDismiss,
}) => {
  const [isExpanded, setIsExpanded] = useState<boolean>(true);

  if (!feedback) return null;

  const isRejected = feedback.verdict === 'infeasible_rejected' || !feedback.applied;
  const isPartial = feedback.verdict === 'partially_applied';

  const badgeStyles = isRejected
    ? 'bg-amber-500/10 text-amber-400 border-amber-500/40 shadow-amber-950/40'
    : isPartial
    ? 'bg-sky-500/10 text-sky-400 border-sky-500/40 shadow-sky-950/40'
    : 'bg-emerald-500/10 text-emerald-400 border-emerald-500/40 shadow-emerald-950/40';

  const badgeText = isRejected
    ? 'NOT FEASIBLE — PROTECTED FROM DATA LOSS'
    : isPartial
    ? 'PARTIALLY APPLIED WITH CONSTRAINTS'
    : 'REFINEMENT APPLIED TO BLUEPRINT';

  const beforeCount = feedback.table_count_before ?? null;
  const afterCount = feedback.table_count_after ?? null;

  return (
    <div
      id="ai-refinement-feedback-card"
      className={`relative p-5 rounded-none border transition-all duration-200 shadow-2xl ${
        isRejected
          ? 'bg-zinc-950 border-amber-500/50 shadow-amber-950/20'
          : isPartial
          ? 'bg-zinc-950 border-sky-500/50 shadow-sky-950/20'
          : 'bg-zinc-950 border-emerald-500/50 shadow-emerald-950/20'
      }`}
    >
      {/* Header Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 pb-3 border-b border-zinc-800/80">
        <div className="flex items-center gap-2.5">
          <span
            className={`px-2.5 py-1 text-[11px] font-mono font-bold tracking-wider uppercase border rounded-none shadow-sm ${badgeStyles}`}
          >
            {badgeText}
          </span>
          {versionNumber && (
            <span className="text-[11px] font-mono text-zinc-400 bg-zinc-900 px-2 py-0.5 border border-zinc-800">
              v{versionNumber}
            </span>
          )}
        </div>

        <div className="flex items-center gap-2">
          {beforeCount !== null && afterCount !== null && (
            <div className="text-[11px] font-mono px-2.5 py-1 bg-black border border-zinc-800 text-zinc-300">
              Tables: <span className="text-white font-bold">{beforeCount}</span>
              <span className="text-zinc-500 mx-1.5">→</span>
              <span className={isRejected ? 'text-amber-400 font-bold' : 'text-emerald-400 font-bold'}>
                {afterCount}
              </span>
              {isRejected && <span className="text-zinc-500 ml-1.5">(Preserved)</span>}
            </div>
          )}

          <button
            type="button"
            onClick={() => setIsExpanded((prev) => !prev)}
            className="text-xs font-mono text-zinc-400 hover:text-white px-2 py-1 bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 transition-colors"
          >
            {isExpanded ? 'Collapse' : 'Expand'}
          </button>

          {onDismiss && (
            <button
              type="button"
              onClick={onDismiss}
              className="text-xs font-mono text-zinc-500 hover:text-zinc-300 px-2 py-1 transition-colors"
              title="Dismiss note"
            >
              ✕
            </button>
          )}
        </div>
      </div>

      {isExpanded && (
        <div className="mt-3.5 space-y-3">
          {/* User Prompt Echo */}
          {feedback.user_prompt && (
            <div className="p-2.5 bg-black/60 border border-zinc-800/80 font-mono text-xs">
              <span className="text-zinc-500 uppercase text-[10px] font-bold block mb-1">
                Your Refinement Instruction:
              </span>
              <span className="text-sky-300 italic">"{feedback.user_prompt}"</span>
            </div>
          )}

          {/* AI Response Narrative */}
          <div className="space-y-1.5">
            <span className="text-[10px] font-mono uppercase font-bold tracking-wider text-zinc-400">
              AI Feasibility Analysis & Response:
            </span>
            <div
              className={`p-3.5 rounded-none text-xs leading-relaxed font-mono whitespace-pre-wrap border ${
                isRejected
                  ? 'bg-amber-950/10 text-zinc-200 border-amber-500/20'
                  : 'bg-zinc-900/60 text-zinc-200 border-zinc-800'
              }`}
            >
              {feedback.explanation}
            </div>
          </div>

          {/* Changes / Constraints Bullet Points */}
          {feedback.changes_summary && feedback.changes_summary.length > 0 && (
            <div className="pt-1">
              <span className="text-[10px] font-mono uppercase font-bold tracking-wider text-zinc-400 block mb-1.5">
                {isRejected ? 'Safety & Integrity Safeguards:' : 'Modifications Made:'}
              </span>
              <ul className="space-y-1 pl-1">
                {feedback.changes_summary.map((item, idx) => (
                  <li key={idx} className="text-xs font-mono text-zinc-300 flex items-start gap-2">
                    <span className={isRejected ? 'text-amber-400 mt-0.5' : 'text-emerald-400 mt-0.5'}>
                      {isRejected ? '⚠' : '✓'}
                    </span>
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default RefinementFeedbackCard;
