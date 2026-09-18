'use client';

import React, { useState, useEffect, useCallback, Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { PlanDetailResponse } from '../../types/migrationPlan';
import planService from '../../services/planService';
import PlanBlueprintViewer from '../../components/plans/PlanBlueprintViewer';

function TransformationPlanContent() {
  const searchParams = useSearchParams();
  const planIdParam = searchParams.get('planId');

  const [plan, setPlan] = useState<PlanDetailResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handlePlanUpdated = useCallback((updated: PlanDetailResponse) => {
    setPlan(updated);
  }, []);

  useEffect(() => {
    let isMounted = true;

    const fetchPlanData = async () => {
      setLoading(true);
      setErrorMsg(null);

      try {
        let targetId = planIdParam;

        // If no planId query param, fetch latest plan from list
        if (!targetId) {
          const list = await planService.listPlans();
          if (list && list.length > 0) {
            targetId = list[0].id;
          }
        }

        if (!targetId) {
          if (isMounted) {
            setPlan(null);
            setLoading(false);
          }
          return;
        }

        const data = await planService.getPlan(targetId);
        if (isMounted) {
          setPlan(data);
        }
      } catch (err: any) {
        if (isMounted) {
          const msg =
            err.response?.data?.detail ||
            err.message ||
            'Failed to load migration transformation plan.';
          setErrorMsg(msg);
        }
      } finally {
        if (isMounted) {
          setLoading(false);
        }
      }
    };

    fetchPlanData();

    return () => {
      isMounted = false;
    };
  }, [planIdParam]);

  return (
    <div className="min-h-screen bg-black text-slate-100 flex flex-col justify-between selection:bg-sky-400 selection:text-black">
      {/* Top Background Glow */}
      <div className="fixed top-0 left-1/2 -translate-x-1/2 w-full max-w-7xl h-96 bg-gradient-to-b from-sky-400/15 via-sky-500/5 to-transparent blur-3xl pointer-events-none -z-10" />

      <main className="w-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8 flex-1 font-mono">
        {/* Navigation Breadcrumb */}
        <div className="flex items-center justify-between border-b border-zinc-800 pb-4">
          <div className="flex items-center gap-2 text-xs text-zinc-400 font-mono">
            <a href="/dashboard" className="text-sky-400 hover:text-sky-300 font-bold">
              Dashboard
            </a>
            <span className="text-zinc-600">/</span>
            <a
              href={plan?.agent_id ? `/sources?agentId=${plan.agent_id}` : '/sources'}
              className="text-sky-400 hover:text-sky-300 font-bold"
            >
              Agent Schema Catalog
            </a>
            <span className="text-zinc-600">/</span>
            <span className="text-white font-bold">Transformation Blueprint</span>
          </div>

          <a
            href={plan?.agent_id ? `/sources?agentId=${plan.agent_id}` : '/sources'}
            className="text-xs font-mono font-bold text-sky-400 hover:text-sky-300 uppercase transition-colors flex items-center gap-1 bg-sky-400/10 px-3 py-1.5 border border-sky-400/30"
          >
            ← Back to Agent Catalog
          </a>
        </div>

        {loading ? (
          <div className="p-16 text-center rounded-none bg-black border border-zinc-800 text-zinc-400 font-mono text-xs space-y-3 shadow-xl">
            <div className="w-6 h-6 border-2 border-sky-400 border-t-transparent rounded-none animate-spin mx-auto" />
            <p>Fetching AI Transformation Plan AST...</p>
          </div>
        ) : errorMsg ? (
          <div className="p-12 text-center rounded-none bg-black border border-rose-500/30 space-y-4 shadow-xl">
            <div className="text-rose-400 font-mono text-sm font-bold uppercase tracking-wider">
              🚨 Plan Load Error
            </div>
            <p className="text-zinc-400 text-xs max-w-md mx-auto">{errorMsg}</p>
            <div>
              <a
                href={plan ? `/sources?agentId=${plan.agent_id}` : '/dashboard'}
                className="inline-block py-2.5 px-6 rounded-none bg-zinc-900 hover:bg-zinc-800 text-white text-xs font-mono font-bold uppercase border border-zinc-800"
              >
                Return to Agent Catalog
              </a>
            </div>
          </div>
        ) : !plan ? (
          <div className="p-16 text-center rounded-none bg-black border border-zinc-800 space-y-4 shadow-xl">
            <h3 className="text-xl font-bold text-white uppercase tracking-wide">
              No Migration Plan Found
            </h3>
            <p className="text-zinc-400 text-xs max-w-md mx-auto leading-relaxed">
              Select an agent from the Dashboard and click "Inspect Schemas & Migration Plan" to construct a blueprint.
            </p>
            <div className="pt-2">
              <a
                href="/dashboard"
                className="inline-block py-3 px-8 rounded-none bg-sky-400 hover:bg-sky-300 text-black text-xs font-bold uppercase tracking-wider transition-colors"
              >
                Go to Dashboard
              </a>
            </div>
          </div>
        ) : (
          <PlanBlueprintViewer
            plan={plan}
            onPlanUpdated={handlePlanUpdated}
          />
        )}
      </main>
    </div>
  );
}

export default function TransformationPlanPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-black text-zinc-400 font-mono text-xs p-12 text-center">
          Loading Transformation Plan...
        </div>
      }
    >
      <TransformationPlanContent />
    </Suspense>
  );
}
