/* eslint-disable @typescript-eslint/no-explicit-any */
// Hand-typed shapes mirroring the FastAPI backend's response bodies. Kept
// permissive at the field level (most things Optional) so a missing key on
// the server doesn't break TS at the call site.

export type StageStatus = "pending" | "running" | "done" | "error" | "skipped";

export interface Health { status: string; version: string }

export interface Parser { slug: string; version: string; label: string }
export interface Account { handle: string; display_name: string | null }
export interface Source { slug: string; label: string; created_at?: string }

export interface Stats {
  items: number;
  exports: number;
  runs: number;
  items_per_source: Record<string, number>;
  items_per_account: Record<string, number>;
  sources: { slug: string; label: string }[];
  accounts: { handle: string; display_name: string | null }[];
}

export interface RunSummary {
  id: number;
  raw_export_id: number;
  status: string;
  parser?: string;
  parser_version?: string;
  items_seen: number;
  items_inserted: number;
  items_skipped: number;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
}

export interface PipelineStage {
  id: number;
  raw_export_id: number;
  ingestion_run_id: number | null;
  stage: "ingest" | "embed" | "graph";
  status: StageStatus;
  started_at: string | null;
  finished_at: string | null;
  items_processed: number;
  error: string | null;
  metadata: Record<string, any>;
}

export interface ExportRow {
  id: number;
  source: string;
  account: string;
  stored_path: string;
  original_path: string;
  size_bytes: number;
  sha256: string;
  received_at: string | null;
  notes: string | null;
  items: number;
  runs: number;
  pipeline_stages: Partial<Record<PipelineStage["stage"], PipelineStage>>;
  latest_run: RunSummary | null;
}

export interface PipelineResult {
  raw_export_id: number;
  ingestion_run_id: number | null;
  stages: {
    stage: PipelineStage["stage"];
    status: StageStatus;
    items_processed: number;
    error: string | null;
    metadata: Record<string, any>;
  }[];
}

export interface VaultInfo { name: string; db_url: string; vault_dir: string; active: boolean; exists: boolean }
export interface VaultsResponse { active: VaultInfo; vaults: VaultInfo[] }

export interface ItemRow {
  id: number;
  source: string;
  account: string;
  kind: string;
  title: string | null;
  ts: string | null;
}

export interface ItemsResponse { total: number; limit: number; offset: number; items: ItemRow[] }

export interface ItemFull {
  id: number;
  source: string;
  account: string;
  raw_export_id: number;
  ingestion_run_id: number | null;
  native_id: string | null;
  kind: string;
  title: string | null;
  ts: string | null;
  content_hash: string;
  metadata: Record<string, any>;
  body: string | null;
  body_truncated?: boolean;
  body_full_chars?: number;
  media: any[];
  tags: { key: string; value: string }[];
}

export interface SearchHit {
  id: number;
  source: string;
  account?: string;
  kind: string;
  ts: string | null;
  title: string | null;
  snippet: string | null;
  score?: number;
  distance?: number;
}

export interface IngestRequest {
  export_id?: number;
  source?: string;
  all_pending?: boolean;
  replace?: boolean;
  with_embeddings?: boolean;
  with_graph?: boolean;
}
export interface PipelineRequest { export_id: number; with_embeddings?: boolean; with_graph?: boolean; replace?: boolean }
export interface RegisterExportRequest { source: string; path: string; account: string; notes?: string | null }
export interface RegisterExportResponse {
  id: number;
  source: string;
  account: string;
  sha256: string;
  size_bytes: number;
  received_at: string | null;
}
export interface IngestResponse { runs?: RunSummary[]; pipeline?: PipelineResult }

// ----- Repo intake --------------------------------------------------------

export interface RepoMeta {
  key?: string;
  name?: string;
  remote_url?: string | null;
  head_sha?: string | null;
}
export interface RepoScanRow {
  path: string;
  duplicate: boolean;
  existing_export_id: number | null;
  repo: RepoMeta | null;
}
export interface RepoScanResponse { repos: RepoScanRow[] }
export interface RepoRegisterResult {
  status: string;
  repo: RepoMeta | null;
  export_id: number | null;
  run_id: number | null;
  error: string | null;
}
export interface RepoRegisterResponse { results: RepoRegisterResult[] }

// ----- Embeddings ---------------------------------------------------------

export interface EmbedDeviceInfo { device: string; label: string; torch: string | null; available: boolean; note?: string }
export interface EmbedStats {
  model: string;
  embed_dim: number;
  items_with_text: number;
  items_embedded: number;
  items_pending: number;
  total_chunks: number;
  device: EmbedDeviceInfo;
}
export interface EmbedResponse { embedded: number }

// ----- Graph --------------------------------------------------------------

export interface EntitySummary {
  id: number;
  type: string;
  name: string;
  canonical_name: string;
  origin?: string;
}

export interface EntityFull {
  entity: { id: number; type: string; name: string; canonical_name: string; description: string | null; metadata: Record<string, any>; origin: string; confidence: number | null };
  aliases: { id: number; alias: string; normalized_alias: string; source: string | null }[];
  evidence: { id: number; source_type: string; source_id: string | null; source_uri: string | null; quote: string | null; metadata: Record<string, any> }[];
}

export interface EntityNeighborhood {
  center: { id: number; type: string; name: string; canonical_name: string } | null;
  nodes: { id: number; type: string; name: string; canonical_name: string }[];
  edges: { id: number; source_entity_id: number; target_entity_id: number; relationship_type: string }[];
}

export interface EntityContext {
  entity: { id: number; type: string; name: string; canonical_name: string; description: string | null };
  summary: string;
  aliases: { alias: string }[];
  related_entities: { id: number; type: string; name: string }[];
  relationships: { id: number; source_entity_id: number; target_entity_id: number; relationship_type: string }[];
  evidence: { id: number; source_type: string; source_id: string | null; quote: string | null }[];
  suggested_queries: string[];
}
