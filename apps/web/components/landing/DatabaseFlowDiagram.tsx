'use client';

import React, { useState } from 'react';
import { Database, Cpu, ArrowRight, Zap, CheckCircle2, Server, Layers } from 'lucide-react';

interface NodeInfo {
  id: string;
  name: string;
  type: string;
  tables: string[];
  details: string;
}

const NODES: Record<string, NodeInfo> = {
  'source-1': {
    id: 'source-1',
    name: 'PostgreSQL DB 1 (Source)',
    type: 'Relational DB',
    tables: ['users (id, email, password_hash)', 'user_profiles (user_id, bio, avatar)'],
    details: 'Primary user identity & auth schema dataset. Translates UUID keys and JSONB fields.',
  },
  'source-2': {
    id: 'source-2',
    name: 'MySQL DB 2 (Source)',
    type: 'Relational DB',
    tables: ['orders (id, customer_id, total)', 'order_items (order_id, product_id, price)'],
    details: 'E-commerce transactional database. Resolves AUTO_INCREMENT BIGINT to UUID v4 target keys.',
  },
  'ai-engine': {
    id: 'ai-engine',
    name: 'Migraflow AI Engine',
    type: 'Core Translation & ETL',
    tables: ['Polars LazyFrames', 'DuckDB Arrow Memory Cursor', 'Zero-OOM Batch Chunking'],
    details: 'AI infers target database schema, aligns column data types, and streams chunked records deterministically without RAM spikes.',
  },
  'target-db': {
    id: 'target-db',
    name: 'Target Unified Warehouse',
    type: 'Target PostgreSQL',
    tables: ['dim_users', 'dim_orders', 'fact_transactions'],
    details: 'Unified target schema with foreign key constraints, indexes, and primary key validation.',
  },
};

export default function DatabaseFlowDiagram() {
  const [selectedNodeId, setSelectedNodeId] = useState<string>('ai-engine');
  const selectedNode = NODES[selectedNodeId];

  return (
    <section id="overview" className="py-20 bg-black relative border-t border-zinc-900 rounded-none overflow-hidden">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 relative z-10">
        {/* Header */}
        <div className="max-w-3xl mb-12">
          <div className="text-xs font-mono text-sky-400 font-bold uppercase tracking-wider mb-2 flex items-center gap-2">
            <Zap className="w-3.5 h-3.5" />
            <span>Interactive Data Architecture</span>
          </div>
          <h2 className="text-2xl sm:text-4xl font-extrabold text-white tracking-tight mb-3">
            Multi-Source Database Migration Topology
          </h2>
          <p className="text-zinc-400 text-sm leading-relaxed">
            Visualizing real-time schema translation and zero-OOM chunked record streaming from two independent source databases into one unified target data warehouse.
          </p>
        </div>

        {/* Frameless Dark Topology Layout */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-center">
          {/* Left Area: Flow Canvas (8 cols) */}
          <div className="lg:col-span-8 relative min-h-[440px] flex flex-col justify-between py-2">
            {/* Status Header Bar */}
            <div className="flex items-center justify-between border-b border-zinc-800/80 pb-4 mb-8 text-xs font-mono">
              <span className="text-zinc-300 uppercase tracking-wider flex items-center gap-2 font-bold">
                <Layers className="w-4 h-4 text-sky-400" />
                Live Architecture Diagram
              </span>
              <span className="text-sky-400 font-semibold flex items-center gap-2 bg-zinc-900 px-3 py-1 border border-zinc-800">
                <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                STREAM ACTIVE
              </span>
            </div>

            {/* Floating Diagram Nodes Layout */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6 sm:gap-8 items-center relative my-auto">
              {/* SVG Connecting Lines (Desktop) */}
              <div className="hidden md:block absolute inset-0 pointer-events-none z-0">
                <svg className="w-full h-full" overflow="visible">
                  <line
                    x1="28%"
                    y1="25%"
                    x2="50%"
                    y2="50%"
                    stroke="rgba(56,189,248,0.3)"
                    strokeWidth="2"
                    strokeDasharray="5 5"
                  />
                  <line
                    x1="28%"
                    y1="75%"
                    x2="50%"
                    y2="50%"
                    stroke="rgba(56,189,248,0.3)"
                    strokeWidth="2"
                    strokeDasharray="5 5"
                  />
                  <line
                    x1="50%"
                    y1="50%"
                    x2="72%"
                    y2="50%"
                    stroke="rgba(56,189,248,0.4)"
                    strokeWidth="2"
                    strokeDasharray="6 4"
                  />
                </svg>
              </div>

              {/* Column 1: Source Databases */}
              <div className="space-y-5 relative z-10">
                {/* Node: Source DB 1 */}
                <button
                  onClick={() => setSelectedNodeId('source-1')}
                  className={`w-full p-5 text-left rounded-none border transition-all duration-200 ${
                    selectedNodeId === 'source-1'
                      ? 'bg-zinc-900 border-sky-400 text-white shadow-lg'
                      : 'bg-zinc-950/80 border-zinc-800 hover:border-zinc-700 text-zinc-300'
                  }`}
                >
                  <div className="flex items-center gap-3 mb-1.5">
                    <Database className="w-5 h-5 text-sky-400 shrink-0" />
                    <span className="font-bold text-xs uppercase tracking-tight">
                      PostgreSQL DB 1
                    </span>
                  </div>
                  <div className="text-[11px] font-mono text-zinc-400">750,000 Records</div>
                </button>

                {/* Node: Source DB 2 */}
                <button
                  onClick={() => setSelectedNodeId('source-2')}
                  className={`w-full p-5 text-left rounded-none border transition-all duration-200 ${
                    selectedNodeId === 'source-2'
                      ? 'bg-zinc-900 border-sky-400 text-white shadow-lg'
                      : 'bg-zinc-950/80 border-zinc-800 hover:border-zinc-700 text-zinc-300'
                  }`}
                >
                  <div className="flex items-center gap-3 mb-1.5">
                    <Server className="w-5 h-5 text-sky-400 shrink-0" />
                    <span className="font-bold text-xs uppercase tracking-tight">
                      MySQL DB 2
                    </span>
                  </div>
                  <div className="text-[11px] font-mono text-zinc-400">500,000 Records</div>
                </button>
              </div>

              {/* Column 2: Migraflow AI Engine Node */}
              <div className="relative z-10">
                <button
                  onClick={() => setSelectedNodeId('ai-engine')}
                  className={`w-full p-6 text-center rounded-none border transition-all duration-200 ${
                    selectedNodeId === 'ai-engine'
                      ? 'bg-zinc-900 border-sky-400 shadow-xl'
                      : 'bg-zinc-950/90 border-zinc-800 hover:border-zinc-700'
                  }`}
                >
                  <div className="w-10 h-10 mx-auto mb-2.5 rounded-none bg-sky-400/10 border border-sky-400/30 flex items-center justify-center text-sky-400">
                    <Cpu className="w-5 h-5" />
                  </div>
                  <div className="font-bold text-xs text-white uppercase tracking-wider mb-1">
                    Migraflow AI Engine
                  </div>
                  <div className="text-[11px] font-mono text-sky-400 font-medium">
                    Zero-OOM Streaming
                  </div>
                </button>
              </div>

              {/* Column 3: Target Database Warehouse */}
              <div className="relative z-10">
                <button
                  onClick={() => setSelectedNodeId('target-db')}
                  className={`w-full p-5 text-left rounded-none border transition-all duration-200 ${
                    selectedNodeId === 'target-db'
                      ? 'bg-zinc-900 border-emerald-400 text-white shadow-lg'
                      : 'bg-zinc-950/80 border-zinc-800 hover:border-zinc-700 text-zinc-300'
                  }`}
                >
                  <div className="flex items-center gap-3 mb-1.5">
                    <Database className="w-5 h-5 text-emerald-400 shrink-0" />
                    <span className="font-bold text-xs uppercase tracking-tight">
                      Target Warehouse
                    </span>
                  </div>
                  <div className="text-[11px] font-mono text-emerald-400 font-semibold">
                    1.25M / 1.25M Synced
                  </div>
                </button>
              </div>
            </div>

            {/* Bottom Real-time Telemetry Bar */}
            <div className="mt-8 pt-4 border-t border-zinc-800/80 flex flex-wrap items-center justify-between gap-4 text-xs font-mono text-zinc-400">
              <div className="flex items-center gap-2">
                <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                <span className="font-bold text-zinc-300">Zero OOM Crashes</span>
              </div>
            </div>
          </div>

          {/* Right Area: Node Inspector Drawer Card */}
          <div className="lg:col-span-4 p-7 rounded-none bg-zinc-900/80 border border-zinc-800 h-full flex flex-col justify-between">
            <div>
              <div className="text-xs font-mono text-sky-400 uppercase tracking-widest font-bold mb-2">
                Node Inspector
              </div>
              <h3 className="text-lg font-bold text-white mb-2">{selectedNode.name}</h3>
              <div className="inline-block px-2.5 py-1 mb-5 rounded-none bg-zinc-950 border border-zinc-800 text-sky-400 text-xs font-mono uppercase font-semibold">
                {selectedNode.type}
              </div>

              <p className="text-zinc-400 text-xs leading-relaxed mb-6">
                {selectedNode.details}
              </p>

              {/* Schema Tables List */}
              <div className="mb-6">
                <div className="text-xs font-mono text-zinc-300 uppercase tracking-wider font-bold mb-3">
                  Schema Definitions & Entities:
                </div>
                <div className="space-y-2">
                  {selectedNode.tables.map((tbl, i) => (
                    <div
                      key={i}
                      className="p-3 rounded-none bg-black border border-zinc-800 text-xs font-mono text-zinc-300 flex items-center gap-2.5"
                    >
                      <ArrowRight className="w-3.5 h-3.5 text-sky-400 shrink-0" />
                      <span className="truncate">{tbl}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>


          </div>
        </div>
      </div>
    </section>
  );
}
