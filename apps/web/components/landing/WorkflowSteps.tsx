'use client';

import React from 'react';

const steps = [
  {
    number: '01',
    title: 'Create Local Docker Agent',
    description:
      'Deploy the local Docker agent and configure your database credentials securely. The agent introspects source databases to fetch structural metadata without exposing raw data.',
  },
  {
    number: '02',
    title: 'Generate Plan',
    description:
      'The AI engine analyzes source database metadata to automatically generate an optimal target schema blueprint, including table mappings, column type conversions, and DDL.',
  },
  {
    number: '03',
    title: 'Review Plan',
    description:
      'Inspect how the target schema will look in an interactive blueprint. Suggest changes using natural language feedback or manual edits to refine and regenerate the migration plan.',
  },
  {
    number: '04',
    title: 'Execute & Monitor',
    description:
      'Execute the migration through your local Docker agent with bounded batch streaming. Track live row counts, table progress, and WebSocket telemetry in real time.',
  },
];

export default function WorkflowSteps() {
  return (
    <section id="workflow" className="py-20 bg-black relative border-t border-zinc-900 rounded-none">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 relative z-10">
        {/* Section Header */}
        <div className="max-w-2xl mb-12">
          <div className="text-xs font-mono text-sky-400 font-bold uppercase tracking-wider mb-2">
            Execution Workflow
          </div>
          <h2 className="text-2xl sm:text-4xl font-extrabold text-white tracking-tight">
            Simple 4-Step Migration Pipeline
          </h2>
        </div>

        {/* Steps Grid - Refined Dark Cards */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5">
          {steps.map((step) => (
            <div
              key={step.number}
              className="p-7 rounded-none bg-zinc-900/80 border border-zinc-800 hover:border-zinc-700 transition-colors flex flex-col justify-between"
            >
              <div>
                <div className="font-mono text-2xl font-extrabold text-sky-400 mb-4">
                  {step.number}
                </div>
                <h3 className="text-base font-bold text-white mb-2 tracking-tight">{step.title}</h3>
                <p className="text-zinc-400 text-xs leading-relaxed">{step.description}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
