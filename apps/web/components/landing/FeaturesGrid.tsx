'use client';

import React from 'react';

export default function FeaturesGrid() {
  return (
    <section id="features" className="py-20 bg-black relative border-t border-zinc-900 rounded-none">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 relative z-10">
        {/* Section Header */}
        <div className="max-w-2xl mb-12">
          <div className="text-xs font-mono text-sky-400 font-bold uppercase tracking-wider mb-2">
            Capabilities
          </div>
          <h2 className="text-2xl sm:text-4xl font-extrabold text-white tracking-tight">
            Built for Reliable, Deterministic Data Migration
          </h2>
        </div>

        {/* Grid Layout - Refined Dark Cards */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
          {/* Feature 1 */}
          <div className="md:col-span-2 p-8 rounded-none bg-zinc-900/80 border border-zinc-800 hover:border-zinc-700 transition-colors">
            <h3 className="text-lg font-bold text-white mb-2 tracking-tight">Schema Mapping Engine</h3>
            <p className="text-zinc-400 text-sm leading-relaxed mb-4">
              Parses target database definitions and source schemas to automatically infer column aliases, primary keys, and data type casts with zero manual script editing.
            </p>
            <div className="p-3.5 rounded-none bg-black border border-zinc-800 font-mono text-xs text-sky-300">
              UUID -&gt; VARCHAR(36) | JSONB -&gt; JSON | TIMESTAMP -&gt; DATETIME
            </div>
          </div>

          {/* Feature 2 */}
          <div className="p-8 rounded-none bg-zinc-900/80 border border-zinc-800 hover:border-zinc-700 transition-colors">
            <h3 className="text-lg font-bold text-white mb-2 tracking-tight">Constant RAM Streaming</h3>
            <p className="text-zinc-400 text-sm leading-relaxed">
              Streams data iteratively in cursor batches. RAM consumption remains constant regardless of total dataset size.
            </p>
          </div>

          {/* Feature 3 */}
          <div className="p-8 rounded-none bg-zinc-900/80 border border-zinc-800 hover:border-zinc-700 transition-colors">
            <h3 className="text-lg font-bold text-white mb-2 tracking-tight">Supported Connectors</h3>
            <p className="text-zinc-400 text-sm leading-relaxed mb-4">
              Native high-speed drivers for PostgreSQL, MySQL, and MongoDB.
            </p>
            <div className="flex flex-wrap gap-2">
              <span className="px-2.5 py-1 rounded-none bg-black border border-zinc-800 text-zinc-300 text-xs font-mono">PostgreSQL</span>
              <span className="px-2.5 py-1 rounded-none bg-black border border-zinc-800 text-zinc-300 text-xs font-mono">MySQL</span>
              <span className="px-2.5 py-1 rounded-none bg-black border border-zinc-800 text-zinc-300 text-xs font-mono">MongoDB</span>
            </div>
          </div>

          {/* Feature 4 */}
          <div className="p-8 rounded-none bg-zinc-900/80 border border-zinc-800 hover:border-zinc-700 transition-colors">
            <h3 className="text-lg font-bold text-white mb-2 tracking-tight">Real-Time Job Telemetry</h3>
            <p className="text-zinc-400 text-sm leading-relaxed">
              Monitor active migration throughput, row transfer counts, progress percentages, and error logs live.
            </p>
          </div>

          {/* Feature 5 */}
          <div className="p-8 rounded-none bg-zinc-900/80 border border-zinc-800 hover:border-zinc-700 transition-colors">
            <h3 className="text-lg font-bold text-white mb-2 tracking-tight">Token Rotation & Auth</h3>
            <p className="text-zinc-400 text-sm leading-relaxed">
              Secure HTTP-only cookie authentication with automated 401 token refresh queueing.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
