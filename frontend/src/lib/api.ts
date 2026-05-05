import type {
  Account,
  EmbedResponse,
  EmbedStats,
  EntityContext,
  EntityFull,
  EntityNeighborhood,
  EntitySummary,
  ExportRow,
  Health,
  IngestRequest,
  IngestResponse,
  ItemFull,
  ItemRow,
  ItemsResponse,
  MCPStatus,
  Parser,
  PipelineRequest,
  PipelineResult,
  RegisterExportRequest,
  RegisterExportResponse,
  RepoRegisterResponse,
  RepoScanResponse,
  RunSummary,
  SearchHit,
  Source,
  Stats,
  VaultsResponse,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly body?: unknown,
  ) {
    super(message);
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    let body: unknown;
    let detail = res.statusText;
    try {
      body = await res.json();
      const maybe = (body as { detail?: unknown })?.detail;
      if (typeof maybe === "string") detail = maybe;
    } catch {
      /* ignore parse */
    }
    throw new ApiError(`${res.status} ${detail}`, res.status, body);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const json = (body: unknown) => ({ method: "POST", body: JSON.stringify(body) });

export const api = {
  // Lookups
  health: () => request<Health>("/health"),
  parsers: () => request<Parser[]>("/api/parsers"),
  accounts: () => request<Account[]>("/api/accounts"),
  sources: () => request<Source[]>("/sources"),
  stats: () => request<Stats>("/stats"),
  vaults: () => request<VaultsResponse>("/api/vaults"),
  createVault: (body: { name: string; activate?: boolean }) =>
    request<unknown>("/api/vaults", json(body)),
  activateVault: (name: string) =>
    request<unknown>(`/api/vaults/${encodeURIComponent(name)}/activate`, json({})),

  // MCP HTTP transport (start/stop the in-process server mounted at /mcp)
  mcpStatus: () => request<MCPStatus>("/api/mcp/status"),
  mcpStart: () => request<MCPStatus>("/api/mcp/start", json({})),
  mcpStop: () => request<MCPStatus>("/api/mcp/stop", json({})),

  // Exports
  exports: () => request<ExportRow[]>("/api/exports"),
  registerExport: (body: RegisterExportRequest) =>
    request<RegisterExportResponse>("/api/exports", json(body)),
  resetExport: (id: number) =>
    request<Record<string, number>>(`/api/exports/${id}/reset`, json({})),

  // Pipeline / ingest
  ingest: (body: IngestRequest) => request<IngestResponse>("/api/ingest", json(body)),
  pipeline: (body: PipelineRequest) =>
    request<{ pipeline: PipelineResult }>("/api/pipeline", json(body)),

  // Runs / items / search
  runs: (limit = 25) => request<RunSummary[]>(`/api/runs?limit=${limit}`),
  items: (params: {
    source?: string;
    account?: string;
    kind?: string;
    limit?: number;
    offset?: number;
  }) => {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
    }
    const tail = qs.toString();
    return request<ItemsResponse>(`/api/items${tail ? `?${tail}` : ""}`);
  },
  timeline: (params: { start?: string; end?: string; source?: string; limit?: number }) => {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
    }
    const tail = qs.toString();
    return request<ItemRow[]>(`/timeline${tail ? `?${tail}` : ""}`);
  },
  item: (id: number) => request<ItemFull>(`/items/${id}`),
  search: (body: { query: string; limit?: number; source?: string }) =>
    request<SearchHit[]>("/search", json(body)),
  semanticSearch: (body: { query: string; limit?: number; source?: string }) =>
    request<SearchHit[]>("/semantic-search", json(body)),

  // Repos
  repoScan: (body: { path: string; recursive?: boolean }) =>
    request<RepoScanResponse>("/api/repos/scan", json(body)),
  repoRegister: (body: {
    path: string;
    account: string;
    recursive?: boolean;
    ingest?: boolean;
    notes?: string | null;
  }) => request<RepoRegisterResponse>("/api/repos/register", json(body)),

  // Embeddings
  embedStats: () => request<EmbedStats>("/api/embed/stats"),
  embed: (body: { limit: number }) => request<EmbedResponse>("/api/embed", json(body)),

  // Graph
  graphSearchEntities: (params: { q: string; type?: string; limit?: number }) => {
    const qs = new URLSearchParams();
    qs.set("q", params.q);
    if (params.type) qs.set("type", params.type);
    if (params.limit) qs.set("limit", String(params.limit));
    return request<EntitySummary[]>(`/graph/entities/search?${qs}`);
  },
  entity: (id: number) => request<EntityFull>(`/graph/entities/${id}`),
  entityNeighborhood: (id: number, depth = 1) =>
    request<EntityNeighborhood>(`/graph/entities/${id}/neighborhood?depth=${depth}`),
  entityContext: (id: number) => request<EntityContext>(`/graph/entities/${id}/context`),
};
