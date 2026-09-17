'use client';

import React, { useState, useEffect } from 'react';
import { DataSourceResponse } from '../../types/agent';
import {
  MetadataSnapshotDetailResponse,
  TableResponse,
  ColumnResponse,
} from '../../types/metadata';
import metadataService from '../../services/metadataService';
import { ArrowRight } from 'lucide-react';

interface SchemaCatalogViewerProps {
  dataSources: DataSourceResponse[];
  selectedSourceId?: string;
  onSourceSelect?: (sourceId: string) => void;
  onGeneratePlanClick?: () => void;
}

export const SchemaCatalogViewer: React.FC<SchemaCatalogViewerProps> = ({
  dataSources,
  selectedSourceId,
  onSourceSelect,
}) => {
  // Display ONLY Source Databases (target databases are excluded from catalog inspection)
  const sourceDataSources = dataSources.filter((ds) => ds.role === 'source' || ds.role === 'both');
  const displayDataSources = sourceDataSources.length > 0 ? sourceDataSources : dataSources.filter((ds) => ds.role !== 'target');

  const defaultSource = displayDataSources[0];

  const [activeSourceId, setActiveSourceId] = useState<string>(
    selectedSourceId || defaultSource?.id || ''
  );
  const [snapshot, setSnapshot] = useState<MetadataSnapshotDetailResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [selectedTable, setSelectedTable] = useState<TableResponse | null>(null);
  const [activeTab, setActiveTab] = useState<'columns' | 'constraints' | 'relationships'>('columns');
  const [searchQuery, setSearchQuery] = useState<string>('');

  const sortedDataSources = [...displayDataSources];

  const activeSource = dataSources.find((ds) => ds.id === activeSourceId) || defaultSource;

  // Handle prop changes for activeSourceId
  useEffect(() => {
    if (selectedSourceId && selectedSourceId !== activeSourceId) {
      setActiveSourceId(selectedSourceId);
    }
  }, [selectedSourceId]);

  // Fetch metadata snapshot when activeSourceId changes
  useEffect(() => {
    if (!activeSourceId) return;

    let isMounted = true;
    setLoading(true);

    metadataService
      .getLatestSnapshot(activeSourceId)
      .then((data) => {
        if (isMounted) {
          setSnapshot(data);
          // Set first table as default selected
          const firstSchema = data.schemas?.[0];
          const firstTable = firstSchema?.tables?.[0];
          if (firstTable) {
            setSelectedTable(firstTable);
          } else {
            setSelectedTable(null);
          }
        }
      })
      .catch(() => {
        if (isMounted) {
          setSnapshot(null);
          setSelectedTable(null);
        }
      })
      .finally(() => {
        if (isMounted) setLoading(false);
      });

    return () => {
      isMounted = false;
    };
  }, [activeSourceId]);

  const handleSourceTabChange = (sourceId: string) => {
    setActiveSourceId(sourceId);
    if (onSourceSelect) onSourceSelect(sourceId);
  };

  // Collect all tables across all schemas in the snapshot
  const allTables: { schemaName: string; table: TableResponse }[] = [];
  if (snapshot?.schemas) {
    snapshot.schemas.forEach((schema) => {
      schema.tables.forEach((tbl) => {
        allTables.push({ schemaName: schema.schema_name, table: tbl });
      });
    });
  }

  // Resolves a table_id/column_id pair from a RelationshipResponse into
  // human-readable "table.column" text, using data already loaded in
  // allTables -- no new API call needed.
  const resolveColumnLabel = (tableId: string, columnId: string): string => {
    for (const { table } of allTables) {
      if (table.id === tableId) {
        const col = table.columns.find((c) => c.id === columnId);
        return `${table.table_name}.${col ? col.column_name : '?'}`;
      }
    }
    return '(unknown table)';
  };

  const filteredTables = allTables.filter((item) =>
    item.table.table_name.toLowerCase().includes(searchQuery.toLowerCase()) ||
    item.schemaName.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const validHealthyStatuses = ['healthy', 'profiled', 'active', 'connected', 'ok'];

  const activeSourceHasError =
    !snapshot &&
    activeSource &&
    ((activeSource.status && !validHealthyStatuses.includes(activeSource.status.toLowerCase())) ||
      (activeSource.last_error && activeSource.last_error.length > 0));

  const isPlaceholderError =
    activeSourceHasError &&
    (activeSource.last_error || '').toLowerCase().includes('placeholder');

  return (
    <div className="w-full space-y-6">
      {/* Top Source Tabs */}
      <div className="border border-zinc-800 bg-black p-1.5 rounded-none flex items-center gap-2 overflow-x-auto font-mono">
        {sortedDataSources.map((ds) => {
          const isSelected = activeSourceId === ds.id;
          const isTarget = ds.role === 'target';
          const explicitErrorStatuses = ['failed', 'unreachable', 'error', 'invalid'];
          const hasError =
            ds.status &&
            explicitErrorStatuses.includes(ds.status.toLowerCase()) &&
            Boolean(ds.last_error && ds.last_error.trim().length > 0);

          return (
            <button
              key={ds.id}
              type="button"
              onClick={() => handleSourceTabChange(ds.id)}
              className={`px-4 py-2 rounded-none text-xs font-bold uppercase tracking-wider transition-all border whitespace-nowrap flex items-center gap-2 ${
                isSelected
                  ? isTarget
                    ? 'bg-blue-500/15 border-blue-500 text-blue-400 shadow-[0_0_15px_rgba(59,130,246,0.2)]'
                    : 'bg-sky-400/15 border-sky-400 text-sky-400 shadow-[0_0_15px_rgba(56,189,248,0.2)]'
                  : 'bg-zinc-950 border-zinc-900 text-zinc-400 hover:border-zinc-800 hover:text-white'
              }`}
            >
              <span className="text-[10px] opacity-60">[{ds.role.toUpperCase()}]</span>
              <span>{ds.name} ({ds.type.toUpperCase()})</span>
              {hasError && <span className="text-amber-400 font-bold">⚠️</span>}
            </button>
          );
        })}
      </div>

      {/* Diagnostic Warning Alert if active source has credential error or connection failure */}
      {activeSourceHasError && (
        <div className="p-5 rounded-none bg-amber-500/10 border border-amber-500/40 space-y-3 font-mono text-xs shadow-xl">
          <div className="flex items-center gap-2 font-bold text-amber-300 uppercase tracking-wider text-sm">
            <span>🚨</span>
            <span>
              {isPlaceholderError
                ? `SCHEMA PROFILING SKIPPED FOR '${activeSource?.name.toUpperCase()}': UNFILLED CREDENTIAL PLACEHOLDERS`
                : `DATABASE CONNECTION FAILURE FOR '${activeSource?.name.toUpperCase()}'`}
            </span>
          </div>

          <div className="p-3 bg-black/80 border border-amber-500/30 text-amber-200 text-xs font-mono whitespace-pre-wrap">
            {activeSource?.last_error || 'Database is unreachable. Please verify network host and login credentials.'}
          </div>

          <div className="p-3 bg-zinc-950 border border-zinc-800 text-[11px] text-sky-400 font-mono space-y-1">
            <span className="font-bold text-white uppercase block">💡 Recommended Fix:</span>
            <p className="text-zinc-300 leading-relaxed">
              Replace placeholders (e.g. <code className="text-amber-300 font-bold">&lt;SRC_SRC_DB_1_PASSWORD&gt;</code>) with actual database passwords in your <code className="text-sky-400 font-bold">docker run</code> command and re-run it.
            </p>
          </div>
        </div>
      )}

      {/* Snapshot Header Stats & Status */}
      {snapshot && (
        <div className="p-4 rounded-none bg-zinc-950 border border-zinc-800 flex flex-wrap items-center justify-between gap-4 text-xs font-mono">
          <div className="flex items-center gap-4">
            <span className="px-2.5 py-1 rounded-none bg-sky-400/10 text-sky-400 border border-sky-400/30 uppercase font-bold text-[10px]">
              SNAPSHOT v{snapshot.version}
            </span>
            <span className="text-zinc-300">
              DB: <strong className="text-white font-bold">{snapshot.database_name}</strong>
            </span>
          </div>

          <div className="flex items-center gap-6 text-zinc-400">
            <div>
              Total Tables: <strong className="text-white font-bold">{snapshot.total_tables}</strong>
            </div>
            <div>
              Total Columns: <strong className="text-white font-bold">{snapshot.total_columns}</strong>
            </div>
            <div>
              Total Rows: <strong className="text-sky-400 font-bold">{snapshot.total_rows.toLocaleString()}</strong>
            </div>
          </div>
        </div>
      )}

      {/* Main Content Area */}
      {loading ? (
        <div className="p-12 text-center rounded-none bg-black border border-zinc-800 text-zinc-400 font-mono text-xs space-y-3">
          <div className="w-5 h-5 border-2 border-sky-400 border-t-transparent rounded-none animate-spin mx-auto" />
          <p>Loading database catalog metadata...</p>
        </div>
      ) : !snapshot || allTables.length === 0 ? (
        <div className="p-12 text-center rounded-none bg-black border border-zinc-800 space-y-4 font-mono shadow-xl">
          <div className="w-12 h-12 rounded-none bg-sky-400/10 border border-sky-400/30 text-sky-400 flex items-center justify-center mx-auto text-xl font-bold">
            ⚡
          </div>
          <div className="text-sky-400 font-mono text-sm font-bold uppercase tracking-wider">
            Waiting for Agent Database Schema Introspection...
          </div>
          <p className="text-zinc-400 text-xs max-w-xl mx-auto leading-relaxed">
            Ensure your Docker Agent daemon is running on your host database server. Once connected, the agent automatically introspects schema metadata (tables, columns, constraints, relationships) and syncs the snapshot here.
          </p>
          <div className="pt-2">
            <button
              type="button"
              onClick={() => {
                if (activeSourceId) handleSourceTabChange(activeSourceId);
              }}
              className="py-2.5 px-6 rounded-none bg-zinc-900 hover:bg-zinc-800 text-sky-400 text-xs font-mono font-bold uppercase border border-sky-400/40 transition-colors"
            >
              🔄 Refresh Schema Snapshot
            </button>
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-12 gap-5 items-start font-mono">
          {/* Left Panel: Table Browser */}
          <div className="md:col-span-4 rounded-none bg-black border border-zinc-800 p-4 space-y-4 shadow-xl">
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-mono font-bold uppercase tracking-widest text-zinc-400">
                  Tables Catalog ({allTables.length})
                </span>
              </div>
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search tables..."
                className="w-full px-3 py-1.5 rounded-none bg-zinc-950 border border-zinc-800 text-white text-xs placeholder-zinc-600 focus:outline-none focus:border-sky-400 font-mono transition-colors"
              />
            </div>

            <div className="space-y-1.5 max-h-[500px] overflow-y-auto pr-1">
              {filteredTables.map(({ schemaName, table }) => {
                const isSelected = selectedTable?.id === table.id;

                return (
                  <button
                    key={table.id}
                    type="button"
                    onClick={() => setSelectedTable(table)}
                    className={`w-full p-3 rounded-none text-left border transition-all ${
                      isSelected
                        ? 'bg-sky-400/10 border-sky-400 text-white shadow-[0_0_10px_rgba(56,189,248,0.15)]'
                        : 'bg-zinc-950 border-zinc-900 text-zinc-400 hover:border-zinc-800 hover:text-white'
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-bold font-mono text-white tracking-wide">
                        {table.table_name}
                      </span>
                      <span className="text-[9px] font-mono px-1.5 py-0.5 rounded-none bg-zinc-900 text-zinc-400 border border-zinc-800">
                        {schemaName}
                      </span>
                    </div>
                    <div className="flex items-center justify-between mt-1 text-[10px] font-mono text-zinc-500">
                      <span>{table.columns.length} columns</span>
                      <span>~{table.row_count.toLocaleString()} rows</span>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Right Panel: Table Schema Details */}
          <div className="md:col-span-8 rounded-none bg-black border border-zinc-800 p-5 space-y-5 shadow-xl">
            {selectedTable ? (
              <>
                {/* Selected Table Header */}
                <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 border-b border-zinc-800 pb-4">
                  <div>
                    <span className="text-[10px] font-mono font-bold tracking-widest px-2.5 py-0.5 rounded-none uppercase bg-sky-400/10 text-sky-400 border border-sky-400/30">
                      TABLE INSPECTOR
                    </span>
                    <h3 className="text-xl font-extrabold text-white uppercase font-sans tracking-wide mt-1">
                      {selectedTable.table_name}
                    </h3>
                  </div>

                  <div className="flex items-center gap-4 text-xs font-mono text-zinc-400">
                    <div>
                      Columns: <strong className="text-white font-bold">{selectedTable.columns.length}</strong>
                    </div>
                    <div>
                      Estimated Rows: <strong className="text-sky-400 font-bold">{selectedTable.row_count.toLocaleString()}</strong>
                    </div>
                  </div>
                </div>

                {/* Details Tab Switcher */}
                <div className="flex border-b border-zinc-800">
                  <button
                    type="button"
                    onClick={() => setActiveTab('columns')}
                    className={`px-4 py-2 text-xs font-mono font-bold uppercase tracking-wider border-b-2 transition-all ${
                      activeTab === 'columns'
                        ? 'border-sky-400 text-sky-400 bg-sky-400/5'
                        : 'border-transparent text-zinc-400 hover:text-white'
                    }`}
                  >
                    Columns ({selectedTable.columns.length})
                  </button>
                  <button
                    type="button"
                    onClick={() => setActiveTab('constraints')}
                    className={`px-4 py-2 text-xs font-mono font-bold uppercase tracking-wider border-b-2 transition-all ${
                      activeTab === 'constraints'
                        ? 'border-sky-400 text-sky-400 bg-sky-400/5'
                        : 'border-transparent text-zinc-400 hover:text-white'
                    }`}
                  >
                    Constraints ({selectedTable.constraints.length})
                  </button>
                  <button
                    type="button"
                    onClick={() => setActiveTab('relationships')}
                    className={`px-4 py-2 text-xs font-mono font-bold uppercase tracking-wider border-b-2 transition-all ${
                      activeTab === 'relationships'
                        ? 'border-sky-400 text-sky-400 bg-sky-400/5'
                        : 'border-transparent text-zinc-400 hover:text-white'
                    }`}
                  >
                    Relationships ({(snapshot?.relationships || []).filter((r) => r.source_table_id === selectedTable.id || r.target_table_id === selectedTable.id).length})
                  </button>
                </div>

                {/* Tab 1: Columns Matrix */}
                {activeTab === 'columns' && (
                  <div className="overflow-x-auto">
                    <table className="w-full text-left font-mono text-xs">
                      <thead>
                        <tr className="border-b border-zinc-800 text-zinc-400 uppercase text-[10px] tracking-wider bg-zinc-950">
                          <th className="p-3">#</th>
                          <th className="p-3">Column Name</th>
                          <th className="p-3">Data Type</th>
                          <th className="p-3">Attributes</th>
                          <th className="p-3">Default Value</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-zinc-900">
                        {selectedTable.columns.map((col: ColumnResponse) => (
                          <tr key={col.id} className="hover:bg-zinc-950/60 transition-colors">
                            <td className="p-3 text-zinc-500 text-[10px]">{col.ordinal_position}</td>
                            <td className="p-3 font-bold text-white">
                              {col.column_name}
                              {col.is_primary_key && (
                                <span className="ml-2 px-1.5 py-0.5 text-[9px] rounded-none bg-sky-400/20 text-sky-400 border border-sky-400/40 uppercase font-bold">
                                  PK
                                </span>
                              )}
                            </td>
                            <td className="p-3 text-sky-400">{col.data_type}</td>
                            <td className="p-3 space-x-1.5">
                              <span
                                className={`px-1.5 py-0.5 text-[9px] rounded-none border uppercase ${
                                  col.nullable
                                    ? 'bg-zinc-900 text-zinc-400 border-zinc-800'
                                    : 'bg-amber-400/10 text-amber-400 border-amber-400/30 font-bold'
                                }`}
                              >
                                {col.nullable ? 'NULLABLE' : 'NOT NULL'}
                              </span>
                              {col.is_unique && (
                                <span className="px-1.5 py-0.5 text-[9px] rounded-none bg-purple-400/10 text-purple-400 border border-purple-400/30 uppercase font-bold">
                                  UNIQUE
                                </span>
                              )}
                            </td>
                            <td className="p-3 text-zinc-400 text-[11px]">
                              {col.default_value || <span className="text-zinc-600">—</span>}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {/* Tab 2: Constraints */}
                {activeTab === 'constraints' && (
                  <div className="space-y-3">
                    {selectedTable.constraints.length === 0 ? (
                      <p className="text-xs font-mono text-zinc-500 py-6 text-center">
                        No explicit constraints registered for this table.
                      </p>
                    ) : (
                      selectedTable.constraints.map((c) => (
                        <div
                          key={c.id}
                          className="p-3.5 rounded-none bg-zinc-950 border border-zinc-800 flex items-center justify-between font-mono text-xs"
                        >
                          <div>
                            <span className="text-white font-bold">{c.constraint_name}</span>
                            {c.definition && (
                              <p className="text-[11px] text-zinc-400 mt-1">{c.definition}</p>
                            )}
                          </div>
                          <span className="px-2 py-0.5 text-[10px] font-bold rounded-none bg-sky-400/10 text-sky-400 border border-sky-400/30 uppercase">
                            {c.constraint_type}
                          </span>
                        </div>
                      ))
                    )}
                  </div>
                )}

                {/* Tab 3: Relationships */}
                {activeTab === 'relationships' && (
                  <div className="space-y-3">
                    {(() => {
                      const related = (snapshot?.relationships || []).filter(
                        (r) => r.source_table_id === selectedTable.id || r.target_table_id === selectedTable.id
                      );
                      if (related.length === 0) {
                        return (
                          <p className="text-xs font-mono text-zinc-500 py-6 text-center">
                            No foreign-key relationships detected for this table.
                          </p>
                        );
                      }
                      return related.map((r) => (
                        <div
                          key={r.id}
                          className="p-3.5 rounded-none bg-zinc-950 border border-zinc-800 flex items-center justify-between font-mono text-xs"
                        >
                          <div className="flex items-center gap-2">
                            <span className="text-white font-bold">
                              {resolveColumnLabel(r.source_table_id, r.source_column_id)}
                            </span>
                            <ArrowRight className="w-3.5 h-3.5 text-zinc-500" />
                            <span className="text-sky-400 font-bold">
                              {resolveColumnLabel(r.target_table_id, r.target_column_id)}
                            </span>
                          </div>
                          <span className="px-2 py-0.5 text-[10px] font-bold rounded-none bg-purple-400/10 text-purple-400 border border-purple-400/30 uppercase">
                            {r.relationship_type} · {Math.round(r.confidence * 100)}%
                          </span>
                        </div>
                      ));
                    })()}
                  </div>
                )}
              </>
            ) : (
              <p className="text-xs font-mono text-zinc-500 py-12 text-center">
                Select a table from the left browser panel to inspect schema columns and constraints.
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default SchemaCatalogViewer;
