'use client';

import React from 'react';
import { useRouter, useSearchParams, usePathname } from 'next/navigation';
import { LayoutDashboard, Table2, PlayCircle, AlertTriangle, Activity } from 'lucide-react';

export type PlanTabKey = 'overview' | 'mappings' | 'execute';

interface PlanTabBarProps {
  activeTab: PlanTabKey;
  onTabChange: (tab: PlanTabKey) => void;
  tableCount: number;
  isValid: boolean;
  isJobActive: boolean;
  isRefining: boolean;
}

export const PlanTabBar: React.FC<PlanTabBarProps> = ({
  activeTab,
  onTabChange,
  tableCount,
  isValid,
  isJobActive,
  isRefining,
}) => {
  const router = useRouter();
  const searchParams = useSearchParams();
  const pathname = usePathname();

  const handleSelectTab = (tabKey: PlanTabKey) => {
    onTabChange(tabKey);
    const params = new URLSearchParams(searchParams?.toString() || '');
    params.set('tab', tabKey);
    router.push(`${pathname}?${params.toString()}`);
  };

  const tabs: {
    id: PlanTabKey;
    label: string;
    icon: React.ReactNode;
    badge?: React.ReactNode;
  }[] = [
    {
      id: 'overview',
      label: 'Overview & Strategy',
      icon: <LayoutDashboard className="w-4 h-4" />,
      badge: isRefining ? (
        <span className="w-2 h-2 rounded-full bg-sky-400 animate-ping" />
      ) : !isValid ? (
        <span className="px-1.5 py-0.2 text-[9px] font-mono font-bold uppercase bg-rose-500/20 text-rose-400 border border-rose-500/40">
          Issues
        </span>
      ) : (
        <span className="px-1.5 py-0.2 text-[9px] font-mono font-bold uppercase bg-emerald-500/20 text-emerald-400 border border-emerald-500/40">
          Ready
        </span>
      ),
    },
    {
      id: 'mappings',
      label: 'Table Mappings',
      icon: <Table2 className="w-4 h-4" />,
      badge: (
        <span className="px-1.5 py-0.5 text-[10px] font-mono font-bold rounded-none bg-zinc-800 text-zinc-300 border border-zinc-700">
          {tableCount}
        </span>
      ),
    },
    {
      id: 'execute',
      label: 'Execute & Monitor',
      icon: isJobActive ? (
        <Activity className="w-4 h-4 text-sky-400 animate-spin" />
      ) : (
        <PlayCircle className="w-4 h-4" />
      ),
      badge: isJobActive ? (
        <span className="px-1.5 py-0.5 text-[9px] font-mono font-bold uppercase bg-sky-400/20 text-sky-400 border border-sky-400/40 animate-pulse">
          Running
        </span>
      ) : !isValid ? (
        <span className="flex items-center gap-1 text-[10px] font-mono text-rose-400 font-bold">
          <AlertTriangle className="w-3 h-3" /> Blocked
        </span>
      ) : (
        <span className="px-1.5 py-0.2 text-[9px] font-mono font-bold uppercase bg-sky-400/20 text-sky-400 border border-sky-400/30">
          Ready
        </span>
      ),
    },
  ];

  return (
    <div className="sticky top-0 z-30 bg-black/95 backdrop-blur-md border-b border-zinc-800 -mx-4 sm:-mx-6 lg:-mx-8 px-4 sm:px-6 lg:px-8 py-2">
      <nav className="flex space-x-2 sm:space-x-4 max-w-6xl mx-auto" aria-label="Plan Tabs">
        {tabs.map((tab) => {
          const isActive = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              type="button"
              onClick={() => handleSelectTab(tab.id)}
              className={`flex items-center gap-2.5 px-4 py-3 text-xs font-mono font-bold uppercase tracking-wider transition-all border-b-2 ${
                isActive
                  ? 'border-sky-400 text-sky-400 bg-sky-400/10 shadow-[0_4px_12px_rgba(56,189,248,0.15)]'
                  : 'border-transparent text-zinc-400 hover:text-zinc-200 hover:bg-zinc-900/60 hover:border-zinc-700'
              }`}
            >
              <span className={isActive ? 'text-sky-400' : 'text-zinc-500'}>{tab.icon}</span>
              <span>{tab.label}</span>
              {tab.badge && <span className="ml-1">{tab.badge}</span>}
            </button>
          );
        })}
      </nav>
    </div>
  );
};

export default PlanTabBar;
