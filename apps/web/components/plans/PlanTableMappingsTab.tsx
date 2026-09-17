'use client';

import React, { useState, useMemo } from 'react';
import {
  TransformationPlanAST,
  TableTransformationType,
  ColumnMappingSpec,
  ConflictResolutionSpec,
} from '../../types/migrationPlan';
import PlanDiagramViewer from './PlanDiagramViewer';
import { TableReadinessBadge, ColumnConfidenceBadge, computeTableReadiness } from './PlanReadinessSignals';
import { Search, Filter, Layers, LayoutGrid, Check, X, ArrowLeft, ArrowRight, ShieldCheck, AlertCircle } from 'lucide-react';

interface PlanTableMappingsTabProps {
  ast: TransformationPlanAST;
  editableAst: TransformationPlanAST;
  isEditing: boolean;
  isSavingEdits: boolean;
  isApproved: boolean;
  expandedTable: string | null;
  setExpandedTable: (table: string | null) => void;
  onStartEditing: () => void;
  onCancelEditing: () => void;
  onSaveEdits: () => Promise<void>;
  onUpdateColumnField: (
    tableIndex: number,
    columnIndex: number,
    field: keyof ColumnMappingSpec,
    value: any
  ) => void;
  onUpdateTableField: (tableIndex: number, field: string, value: any) => void;
  viewMode: 'matrix' | 'diagram';
  setViewMode: (mode: 'matrix' | 'diagram') => void;
  onNavigateToOverview: () => void;
  onNavigateToExecute: () => void;
}

export const PlanTableMappingsTab: React.FC<PlanTableMappingsTabProps> = ({
  ast,
  editableAst,
  isEditing,
  isSavingEdits,
  isApproved,
  expandedTable,
  setExpandedTable,
  onStartEditing,
  onCancelEditing,
  onSaveEdits,
  onUpdateColumnField,
  onUpdateTableField,
  viewMode,
  setViewMode,
  onNavigateToOverview,
  onNavigateToExecute,
}) => {
  const [searchTerm, setSearchTerm] = useState<string>('');
  const [typeFilter, setTypeFilter] = useState<string>('all');
  const [readinessFilter, setReadinessFilter] = useState<string>('all');

  const currentAst = isEditing ? editableAst : ast;
  const tableMappings = currentAst?.table_mappings || [];

  // Filter table mappings based on search term & filters
  const filteredTables = useMemo(() => {
    return tableMappings.filter((tm) => {
      // 1. Text search
      if (searchTerm.trim()) {
        const term = searchTerm.toLowerCase();
        const matchesTableName = tm.target_table_name.toLowerCase().includes(term);
        const matchesSourceTable = tm.source_tables.some(
          (st) =>
            st.table_name.toLowerCase().includes(term) ||
            st.identifier.toLowerCase().includes(term)
        );
        const matchesColumn = tm.column_mappings.some(
          (cm) =>
            (cm.target_column_name && cm.target_column_name.toLowerCase().includes(term)) ||
            cm.source_columns.some(
              (sc) =>
                sc.column_name.toLowerCase().includes(term) ||
                sc.table_name.toLowerCase().includes(term)
            )
        );
        if (!matchesTableName && !matchesSourceTable && !matchesColumn) {
          return false;
        }
      }

      // 2. Type filter
      if (typeFilter !== 'all' && tm.transformation_type !== typeFilter) {
        return false;
      }

      // 3. Readiness filter
      if (readinessFilter !== 'all') {
        const readiness = computeTableReadiness(tm);
        if (readinessFilter === 'optimal' && !(readiness.status === 'optimal' || readiness.status === 'good')) return false;
        if (readinessFilter === 'warning' && readiness.status !== 'warning') return false;
        if (readinessFilter === 'critical' && readiness.status !== 'critical') return false;
      }

      return true;
    });
  }, [tableMappings, searchTerm, typeFilter, readinessFilter]);

  // Helper to find original index of table in editableAst
  const getOriginalTableIndex = (targetTableName: string): number => {
    return tableMappings.findIndex((tm) => tm.target_table_name === targetTableName);
  };

  return (
    <div className="space-y-6 animate-fadeIn font-sans">
      {/* Controls & Filter Bar */}
      <div className="p-5 rounded-none bg-black border border-zinc-800 space-y-4 shadow-xl">
        <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
          {/* View Mode Toggle */}
          <div className="flex items-center gap-2 font-mono">
            <button
              type="button"
              onClick={() => setViewMode('matrix')}
              className={`flex items-center gap-2 px-4 py-2 text-xs font-bold uppercase tracking-wider border transition-all ${
                viewMode === 'matrix'
                  ? 'bg-sky-400/15 border-sky-400 text-sky-400 shadow-[0_0_12px_rgba(56,189,248,0.2)]'
                  : 'bg-zinc-950 border-zinc-800 text-zinc-400 hover:border-zinc-700 hover:text-white'
              }`}
            >
              <LayoutGrid className="w-3.5 h-3.5" />
              Matrix View
            </button>
            <button
              type="button"
              onClick={() => setViewMode('diagram')}
              className={`flex items-center gap-2 px-4 py-2 text-xs font-bold uppercase tracking-wider border transition-all ${
                viewMode === 'diagram'
                  ? 'bg-sky-400/15 border-sky-400 text-sky-400 shadow-[0_0_12px_rgba(56,189,248,0.2)]'
                  : 'bg-zinc-950 border-zinc-800 text-zinc-400 hover:border-zinc-700 hover:text-white'
              }`}
            >
              <Layers className="w-3.5 h-3.5" />
              Pipeline Diagram
            </button>
          </div>

          {/* Edit Mode Buttons */}
          {!isApproved && (
            <div className="flex items-center gap-3 font-mono self-end lg:self-auto">
              {isEditing ? (
                <>
                  <button
                    type="button"
                    onClick={onCancelEditing}
                    className="flex items-center gap-1.5 py-2 px-4 rounded-none bg-zinc-900 hover:bg-zinc-800 text-zinc-400 hover:text-white text-xs font-bold uppercase border border-zinc-800 transition-colors"
                  >
                    <X className="w-3.5 h-3.5" />
                    Discard
                  </button>
                  <button
                    type="button"
                    onClick={onSaveEdits}
                    disabled={isSavingEdits}
                    className="flex items-center gap-1.5 py-2 px-5 rounded-none bg-sky-400 hover:bg-sky-300 text-black text-xs font-bold uppercase tracking-wider border border-sky-400 shadow-md shadow-sky-950/50 disabled:opacity-50"
                  >
                    <Check className="w-3.5 h-3.5" />
                    {isSavingEdits ? 'Saving & Validating...' : 'Save & Re-validate'}
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  onClick={onStartEditing}
                  className="py-2 px-5 rounded-none bg-zinc-900 hover:bg-zinc-800 text-sky-400 text-xs font-bold uppercase tracking-wider border border-sky-400/40 transition-colors"
                >
                  ✎ Edit Blueprint AST
                </button>
              )}
            </div>
          )}
        </div>

        {/* Search & Filters Row (when in Matrix View) */}
        {viewMode === 'matrix' && (
          <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3 pt-2 border-t border-zinc-900 font-mono text-xs">
            {/* Search Input */}
            <div className="relative flex-1">
              <Search className="w-3.5 h-3.5 text-zinc-500 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                placeholder="Search tables, columns, or source names..."
                className="w-full pl-9 pr-4 py-2 bg-zinc-950 border border-zinc-800 text-white placeholder-zinc-600 rounded-none focus:outline-none focus:border-sky-400 font-sans text-xs transition-colors"
              />
              {searchTerm && (
                <button
                  type="button"
                  onClick={() => setSearchTerm('')}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-zinc-500 hover:text-zinc-300 text-xs"
                >
                  ✕
                </button>
              )}
            </div>

            {/* Type Filter */}
            <div className="flex items-center gap-2">
              <span className="text-zinc-500 text-[10px] uppercase font-bold">Type:</span>
              <select
                value={typeFilter}
                onChange={(e) => setTypeFilter(e.target.value)}
                className="bg-zinc-950 border border-zinc-800 text-zinc-300 px-3 py-2 rounded-none focus:outline-none focus:border-sky-400 uppercase text-xs"
              >
                <option value="all">All Types ({tableMappings.length})</option>
                <option value="direct_copy">direct_copy</option>
                <option value="merge">merge</option>
                <option value="split_target">split_target</option>
              </select>
            </div>

            {/* Readiness Filter */}
            <div className="flex items-center gap-2">
              <span className="text-zinc-500 text-[10px] uppercase font-bold">Readiness:</span>
              <select
                value={readinessFilter}
                onChange={(e) => setReadinessFilter(e.target.value)}
                className="bg-zinc-950 border border-zinc-800 text-zinc-300 px-3 py-2 rounded-none focus:outline-none focus:border-sky-400 uppercase text-xs"
              >
                <option value="all">All Signals</option>
                <option value="optimal">Optimal (Low Risk)</option>
                <option value="warning">Warning (Medium Risk)</option>
                <option value="critical">Critical (High Risk)</option>
              </select>
            </div>
          </div>
        )}
      </div>

      {/* Diagram View */}
      {viewMode === 'diagram' ? (
        <PlanDiagramViewer ast={currentAst} />
      ) : (
        <>
          {/* Execution Sequence Pipeline */}
          {tableMappings.length > 0 && (
            <div className="p-5 rounded-none bg-black border border-zinc-800 space-y-3 font-mono shadow-xl">
              <div className="flex items-center justify-between">
                <h3 className="text-xs font-bold text-white uppercase tracking-wider flex items-center gap-2">
                  <span className="w-2 h-2 rounded-none bg-sky-400 inline-block" />
                  Execution Sequence Timeline ({tableMappings.length} tables)
                </h3>
                <span className="text-[10px] text-zinc-500">Click step to jump & expand</span>
              </div>

              <div className="flex items-center gap-3 overflow-x-auto py-2">
                {tableMappings.map((tm, idx) => {
                  const isCurExpanded = expandedTable === tm.target_table_name;
                  return (
                    <React.Fragment key={tm.target_table_name}>
                      <div
                        onClick={() => setExpandedTable(tm.target_table_name)}
                        className={`p-3 rounded-none border cursor-pointer whitespace-nowrap text-xs transition-all ${
                          isCurExpanded
                            ? 'bg-sky-400/15 border-sky-400 text-sky-400 shadow-[0_0_12px_rgba(56,189,248,0.2)]'
                            : 'bg-zinc-950 border-zinc-800 text-zinc-300 hover:border-zinc-700'
                        }`}
                      >
                        <span className="text-[10px] text-zinc-500 mr-2">STEP {idx + 1}</span>
                        <strong className="text-white font-bold">{tm.target_table_name}</strong>
                        <span className="text-[9px] text-sky-400 block mt-0.5 uppercase">
                          {tm.transformation_type} ({tm.column_mappings.length} cols)
                        </span>
                      </div>
                      {idx < tableMappings.length - 1 && (
                        <span className="text-zinc-600 font-bold text-sm">→</span>
                      )}
                    </React.Fragment>
                  );
                })}
              </div>
            </div>
          )}

          {/* Edit Mode Active Banner */}
          {isEditing && (
            <div className="p-4 bg-amber-950/30 border border-amber-500/50 text-amber-300 font-mono text-xs flex items-center justify-between gap-3 shadow-lg">
              <div className="flex items-center gap-2.5">
                <span className="px-2 py-0.5 bg-amber-500 text-black font-bold uppercase text-[10px]">
                  ACTIVE
                </span>
                <span>
                  <strong>Inline AST Editing Mode Active:</strong> You can edit destination table names, transformation types, column types, SQL formulas, and primary key strategies.
                </span>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={onCancelEditing}
                  className="px-3 py-1 bg-zinc-900 hover:bg-zinc-800 text-zinc-300 font-bold uppercase text-xs border border-zinc-700"
                >
                  Discard
                </button>
                <button
                  type="button"
                  onClick={onSaveEdits}
                  disabled={isSavingEdits}
                  className="px-4 py-1 bg-sky-400 hover:bg-sky-300 text-black font-bold uppercase text-xs tracking-wider"
                >
                  {isSavingEdits ? 'Saving...' : 'Save Edits'}
                </button>
              </div>
            </div>
          )}

          {/* Table Accordions List */}
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-xs font-mono font-bold text-white uppercase tracking-wider flex items-center gap-2">
                <span className="w-2 h-2 rounded-none bg-blue-500 inline-block" />
                Table Mapping Specifications ({filteredTables.length} of {tableMappings.length} Shown)
              </h3>
              {filteredTables.length === 0 && (
                <span className="text-xs font-mono text-rose-400">
                  No tables match your search/filter criteria.
                </span>
              )}
            </div>

            {filteredTables.map((tm) => {
              const origIdx = getOriginalTableIndex(tm.target_table_name);
              const isExpanded = expandedTable === tm.target_table_name;
              const readiness = computeTableReadiness(tm);
              
              // Status colored border
              const borderClass =
                readiness.status === 'critical'
                  ? 'border-l-4 border-l-rose-500 border-zinc-800'
                  : readiness.status === 'warning'
                  ? 'border-l-4 border-l-amber-400 border-zinc-800'
                  : 'border-l-4 border-l-emerald-400 border-zinc-800';

              return (
                <div
                  key={tm.target_table_name}
                  className={`rounded-none bg-black border ${borderClass} overflow-hidden shadow-xl transition-all`}
                >
                  {/* Table Accordion Header */}
                  <div className="p-4 bg-zinc-950 border-b border-zinc-800 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 cursor-pointer hover:bg-zinc-900/60 transition-colors font-mono">
                    <div
                      className="flex flex-wrap items-center gap-3 w-full sm:w-auto"
                      onClick={() => setExpandedTable(isExpanded ? null : tm.target_table_name)}
                    >
                      {isEditing ? (
                        <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
                          <span className="text-xs font-bold text-sky-400 uppercase">Table:</span>
                          <input
                            type="text"
                            value={tm.target_table_name || ''}
                            onChange={(e) => onUpdateTableField(origIdx, 'target_table_name', e.target.value)}
                            className="px-2 py-1 bg-zinc-900 border border-sky-400 text-white font-mono text-sm font-extrabold focus:outline-none"
                          />
                          <select
                            value={tm.transformation_type}
                            onChange={(e) => onUpdateTableField(origIdx, 'transformation_type', e.target.value)}
                            className="px-2 py-1 bg-zinc-900 border border-sky-400 text-sky-400 text-xs font-mono font-bold uppercase focus:outline-none"
                          >
                            <option value="direct_copy">direct_copy</option>
                            <option value="merge">merge</option>
                            <option value="split_target">split_target</option>
                          </select>
                        </div>
                      ) : (
                        <>
                          <span className="text-sm font-extrabold text-white uppercase tracking-wider">
                            {tm.target_table_name}
                          </span>
                          <span className="text-[10px] font-bold px-2 py-0.5 rounded-none bg-sky-400/10 text-sky-400 border border-sky-400/30 uppercase">
                            {tm.transformation_type}
                          </span>
                        </>
                      )}
                      <span className="text-[10px] text-zinc-400">
                        Sources: {tm.source_tables.map((st) => `${st.identifier}.${st.table_name}`).join(', ')}
                      </span>
                    </div>

                    <div
                      className="flex items-center gap-4 text-xs text-zinc-400 w-full sm:w-auto justify-between sm:justify-end"
                      onClick={() => setExpandedTable(isExpanded ? null : tm.target_table_name)}
                    >
                      <TableReadinessBadge tm={tm} />
                      <span className="text-sm font-bold text-white">{isExpanded ? '▲' : '▼'}</span>
                    </div>
                  </div>

                  {/* Table Details */}
                  {isExpanded && (
                    <div className="p-5 space-y-4 font-mono text-xs">
                      {/* AI Reasoning & Conflict Resolution Policy Controls */}
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                        {tm.ai_reasoning && (
                          <div className="p-3 rounded-none bg-zinc-950 border border-zinc-800/80 text-zinc-300 text-[11px]">
                            <strong className="text-sky-400 uppercase font-mono mr-2 font-bold">
                              AI Rationale:
                            </strong>
                            {tm.ai_reasoning}
                          </div>
                        )}

                        {/* Merge / Deduplication Policy Settings */}
                        <div className="p-3 rounded-none bg-zinc-950 border border-zinc-800 text-[11px] space-y-1.5">
                          <div className="flex items-center justify-between text-zinc-400 font-bold uppercase text-[10px]">
                            <span className="text-sky-400">Conflict & Merge Policy</span>
                            <span>
                              PK Strategy: {tm.conflict_resolution?.primary_key_strategy || 'uuid_v5'}
                            </span>
                          </div>
                          {isEditing ? (
                            <div className="flex flex-wrap items-center gap-3 pt-1">
                              <label className="flex items-center gap-1.5 text-zinc-300">
                                <span>Dedup Key:</span>
                                <input
                                  type="text"
                                  value={tm.conflict_resolution?.deduplication_key || ''}
                                  onChange={(e) => {
                                    const cr = tm.conflict_resolution || {
                                      deduplication_key: '',
                                      primary_key_strategy: 'uuid_v4_rekey',
                                    };
                                    onUpdateTableField(origIdx, 'conflict_resolution', {
                                      ...cr,
                                      deduplication_key: e.target.value,
                                    });
                                  }}
                                  placeholder="e.g. email"
                                  className="px-2 py-0.5 bg-zinc-900 border border-sky-400 text-white font-mono text-xs"
                                />
                              </label>
                              <label className="flex items-center gap-1.5 text-zinc-300">
                                <span>PK Strategy:</span>
                                <select
                                  value={tm.conflict_resolution?.primary_key_strategy || 'uuid_v5'}
                                  onChange={(e) => {
                                    const cr = tm.conflict_resolution || {
                                      deduplication_key: '',
                                      primary_key_strategy: 'uuid_v4_rekey',
                                    };
                                    onUpdateTableField(origIdx, 'conflict_resolution', {
                                      ...cr,
                                      primary_key_strategy: e.target.value as ConflictResolutionSpec['primary_key_strategy'],
                                    });
                                  }}
                                  className="px-2 py-0.5 bg-zinc-900 border border-sky-400 text-sky-400 font-mono text-xs uppercase"
                                >
                                  <option value="uuid_v4_rekey">uuid_v4_rekey</option>
                                  <option value="prefix_id">prefix_id</option>
                                  <option value="autoincrement_offset">autoincrement_offset</option>
                                  <option value="keep_original">keep_original</option>
                                </select>
                              </label>
                            </div>
                          ) : (
                            <div className="text-zinc-300 text-[11px]">
                              Deduplication Key:{' '}
                              <strong className="text-white">
                                {tm.conflict_resolution?.deduplication_key || 'None (Primary Key)'}
                              </strong>
                            </div>
                          )}
                        </div>
                      </div>

                      {/* Columns Matrix Table */}
                      <div className="overflow-x-auto">
                        <table className="w-full text-left font-mono text-xs">
                          <thead>
                            <tr className="border-b border-zinc-800 text-zinc-400 uppercase text-[10px] tracking-wider bg-zinc-950">
                              <th className="p-3">Target Column</th>
                              <th className="p-3">Target Data Type</th>
                              <th className="p-3">Transformation Type</th>
                              <th className="p-3">Mapping Confidence</th>
                              <th className="p-3">Source Column Ref (Read-Only)</th>
                              <th className="p-3">Explanation & Formula</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-zinc-900">
                            {tm.column_mappings.map((cm: ColumnMappingSpec, cIdx: number) => (
                              <tr key={cIdx} className="hover:bg-zinc-950/60 transition-colors">
                                {/* Target Column Name */}
                                <td className="p-3 font-bold text-white">
                                  {isEditing ? (
                                    <input
                                      type="text"
                                      value={cm.target_column_name || ''}
                                      onChange={(e) =>
                                        onUpdateColumnField(
                                          origIdx,
                                          cIdx,
                                          'target_column_name',
                                          e.target.value
                                        )
                                      }
                                      className="w-full px-2 py-1 rounded-none bg-zinc-950 border border-sky-400 text-white font-mono text-xs focus:outline-none"
                                    />
                                  ) : (
                                    <>
                                      {cm.target_column_name || (
                                        <span className="text-zinc-500 italic">[Dropped]</span>
                                      )}
                                      {cm.is_primary_key && (
                                        <span className="ml-2 px-1.5 py-0.5 text-[9px] rounded-none bg-sky-400/20 text-sky-400 border border-sky-400/40 uppercase font-bold">
                                          PK
                                        </span>
                                      )}
                                    </>
                                  )}
                                </td>

                                {/* Target Data Type */}
                                <td className="p-3 text-sky-400">
                                  {isEditing ? (
                                    <input
                                      type="text"
                                      value={cm.target_data_type || ''}
                                      onChange={(e) =>
                                        onUpdateColumnField(
                                          origIdx,
                                          cIdx,
                                          'target_data_type',
                                          e.target.value
                                        )
                                      }
                                      className="w-full px-2 py-1 rounded-none bg-zinc-950 border border-sky-400 text-sky-400 font-mono text-xs focus:outline-none"
                                    />
                                  ) : (
                                    cm.target_data_type || <span className="text-zinc-600">—</span>
                                  )}
                                </td>

                                {/* Transformation Type */}
                                <td className="p-3">
                                  {isEditing ? (
                                    <select
                                      value={cm.transformation_type}
                                      onChange={(e) =>
                                        onUpdateColumnField(
                                          origIdx,
                                          cIdx,
                                          'transformation_type',
                                          e.target.value
                                        )
                                      }
                                      className="w-full px-2 py-1 rounded-none bg-zinc-950 border border-sky-400 text-blue-400 font-mono text-xs focus:outline-none"
                                    >
                                      <option value="direct_copy">direct_copy</option>
                                      <option value="type_cast">type_cast</option>
                                      <option value="merge_concat">merge_concat</option>
                                      <option value="split">split</option>
                                      <option value="expression">expression</option>
                                      <option value="default_constant">default_constant</option>
                                      <option value="json_flatten">json_flatten</option>
                                      <option value="json_stringify">json_stringify</option>
                                      <option value="array_to_csv">array_to_csv</option>
                                      <option value="array_to_json">array_to_json</option>
                                      <option value="nosql_field_promote">nosql_field_promote</option>
                                      <option value="drop_column">drop_column</option>
                                    </select>
                                  ) : (
                                    <span className="px-2 py-0.5 text-[9px] font-bold rounded-none bg-blue-500/10 text-blue-400 border border-blue-500/30 uppercase">
                                      {cm.transformation_type}
                                    </span>
                                  )}
                                </td>

                                {/* Mapping Confidence */}
                                <td className="p-3">
                                  <ColumnConfidenceBadge col={cm} />
                                </td>

                                {/* Source Columns (Read Only) */}
                                <td className="p-3 text-zinc-300 text-[11px]">
                                  {cm.source_columns.length > 0 ? (
                                    cm.source_columns
                                      .map((sc) => `${sc.identifier}.${sc.table_name}.${sc.column_name}`)
                                      .join(', ')
                                  ) : (
                                    <span className="text-zinc-600">—</span>
                                  )}
                                </td>

                                {/* Explanation & Formula Input */}
                                <td className="p-3 text-zinc-400 text-[11px] leading-normal max-w-xs space-y-1">
                                  <div>{cm.explanation}</div>
                                  {isEditing && cm.transformation_type === 'expression' && (
                                    <div className="pt-1">
                                      <span className="text-[10px] text-sky-400 uppercase font-bold block">
                                        SQL Expression:
                                      </span>
                                      <input
                                        type="text"
                                        value={cm.expression_template || ''}
                                        onChange={(e) =>
                                          onUpdateColumnField(
                                            origIdx,
                                            cIdx,
                                            'expression_template',
                                            e.target.value
                                          )
                                        }
                                        placeholder="e.g. quantity * unit_price"
                                        className="w-full px-2 py-0.5 bg-zinc-950 border border-sky-400 text-sky-400 font-mono text-[11px]"
                                      />
                                    </div>
                                  )}
                                  {isEditing && cm.transformation_type === 'default_constant' && (
                                    <div className="pt-1">
                                      <span className="text-[10px] text-amber-400 uppercase font-bold block">
                                        Constant Value:
                                      </span>
                                      <input
                                        type="text"
                                        value={cm.constant_value || ''}
                                        onChange={(e) =>
                                          onUpdateColumnField(
                                            origIdx,
                                            cIdx,
                                            'constant_value',
                                            e.target.value
                                          )
                                        }
                                        placeholder="e.g. BATCH_2026"
                                        className="w-full px-2 py-0.5 bg-zinc-950 border border-amber-400 text-amber-400 font-mono text-[11px]"
                                      />
                                    </div>
                                  )}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}

      {/* Navigation Footer */}
      <div className="flex items-center justify-between pt-4 border-t border-zinc-800 font-mono">
        <button
          type="button"
          onClick={onNavigateToOverview}
          className="flex items-center gap-2 text-xs font-bold text-zinc-400 hover:text-white uppercase transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to Overview
        </button>

        <button
          type="button"
          onClick={onNavigateToExecute}
          className="flex items-center gap-2 py-2.5 px-6 rounded-none bg-sky-400 hover:bg-sky-300 text-black text-xs font-bold uppercase tracking-wider transition-colors shadow-lg shadow-sky-950/50"
        >
          Proceed to Execution
          <ArrowRight className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
};

export default PlanTableMappingsTab;
