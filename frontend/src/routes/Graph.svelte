<script lang="ts">
  import { api, ApiError } from "$lib/api";
  import { router } from "$lib/router.svelte";
  import { detail } from "$lib/detail.svelte";
  import type { EntityNeighborhood, EntitySummary } from "$lib/types";
  import Empty from "$components/Empty.svelte";
  import Skeleton from "$components/Skeleton.svelte";

  const ENTITY_TYPES = [
    "Project", "Person", "Company", "Product", "Repository", "File", "Document",
    "Email", "Conversation", "Idea", "Task", "Decision", "Tool", "Model", "API",
    "Dataset", "Domain", "Client", "Transaction", "Event", "Location", "Topic",
  ];

  let q = $state(router.param("q") ?? "");
  let type = $state(router.param("type") ?? "");
  let hits = $state<EntitySummary[]>([]);
  let loading = $state(false);
  let error = $state<string | null>(null);
  let ran = $state(q.length > 0);

  let center = $state<EntitySummary | null>(null);
  let neighborhood = $state<EntityNeighborhood | null>(null);
  let nbhLoading = $state(false);

  $effect(() => {
    if (q && hits.length === 0 && !loading && !error) void search();
  });

  async function search() {
    if (!q.trim()) return;
    loading = true; error = null;
    router.patch({ q, type: type || null });
    try {
      hits = await api.graphSearchEntities({ q, type: type || undefined, limit: 30 });
      ran = true;
    } catch (e) {
      error = e instanceof ApiError ? e.message : String(e);
      hits = [];
    } finally {
      loading = false;
    }
  }

  async function selectEntity(e: EntitySummary) {
    center = e;
    nbhLoading = true;
    neighborhood = null;
    try {
      neighborhood = await api.entityNeighborhood(e.id, 1);
    } catch (err) {
      // ignore — just leave panel empty
    } finally {
      nbhLoading = false;
    }
  }

  // Map neighborhood nodes to a constellation circle around the center.
  interface PlottedNode { node: { id: number; type: string; name: string }; x: number; y: number }
  const plotted = $derived.by<PlottedNode[]>(() => {
    if (!neighborhood) return [];
    const others = neighborhood.nodes.filter((n) => n.id !== neighborhood!.center?.id);
    const n = others.length;
    if (!n) return [];
    return others.map((node, i) => {
      const angle = (i / n) * Math.PI * 2 - Math.PI / 2;
      const radius = 36;
      const x = 50 + Math.cos(angle) * radius;
      const y = 50 + Math.sin(angle) * radius;
      return { node, x, y };
    });
  });
</script>

<div class="page-head">
  <div class="eyebrow">Knowledge graph</div>
  <h1>Graph</h1>
  <p class="lede">Search the entities the extractor pulled out of your data, then walk the neighborhood. Click any node to dive deeper.</p>
</div>

<div class="toolbar">
  <input type="search" class="grow" bind:value={q} placeholder="Search entities by name or alias…"
         onkeydown={(e) => e.key === "Enter" && search()} autofocus />
  <span class="label">Type</span>
  <select bind:value={type}>
    <option value="">any</option>
    {#each ENTITY_TYPES as t}<option value={t}>{t}</option>{/each}
  </select>
  <button class="btn btn-primary" type="button" onclick={search} disabled={loading || !q.trim()}>
    {loading ? "…" : "Search"}
  </button>
</div>

<div class="graph-grid">
  <div class="graph-list">
    {#if loading}
      <div class="col" style="gap:8px">{#each Array(6) as _}<Skeleton height="40px" radius="8px" />{/each}</div>
    {:else if error}
      <div class="error-box">{error}</div>
    {:else if !ran}
      <Empty icon="◇" title="Search the graph" sub="Try a project name, a person's handle, or a topic. Filter by entity type to narrow the result set." />
    {:else if hits.length === 0}
      <Empty icon="◇" title="No entities" sub={`No entities for “${q}”${type ? ` of type ${type}` : ""}.`} />
    {:else}
      <div class="col" style="gap:6px">
        {#each hits as h (h.id)}
          <button
            class="hover-tile entity-row"
            type="button"
            class:active={center?.id === h.id}
            onclick={() => selectEntity(h)}
          >
            <span class="pill pill-muted" style="font-size:10px">{h.type}</span>
            <span class="name">{h.name}</span>
          </button>
        {/each}
      </div>
    {/if}
  </div>

  <div class="graph-panel">
    {#if !center}
      <Empty icon="◇" title="Pick an entity" sub="Select a result on the left to see its neighborhood, evidence, and suggested follow-up queries." />
    {:else if nbhLoading}
      <Skeleton height="240px" radius="12px" />
    {:else if neighborhood}
      <div class="constellation">
        <div class="center" style="left:50%;top:50%" onclick={() => detail.openEntity(center!.id)}
             role="button" tabindex="0">
          {center.name}
        </div>
        {#each plotted as p (p.node.id)}
          <div class="node" style="left:{p.x}%;top:{p.y}%"
               role="button" tabindex="0"
               onclick={() => selectEntity(p.node as EntitySummary)}>
            {p.node.name}
          </div>
        {/each}
        <svg viewBox="0 0 100 100" preserveAspectRatio="none">
          {#each plotted as p (p.node.id)}
            <line x1="50" y1="50" x2={p.x} y2={p.y} />
          {/each}
        </svg>
      </div>
      <div class="row-gap" style="margin-top:14px">
        <button class="btn btn-primary" type="button" onclick={() => detail.openEntity(center!.id)}>Open detail →</button>
        <span class="dim" style="font-size:12px">{neighborhood.nodes.length - 1} related · {neighborhood.edges.length} edges</span>
      </div>
    {/if}
  </div>
</div>

<style>
  .graph-grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr);
    gap: 22px;
  }
  @media (max-width: 980px) {
    .graph-grid { grid-template-columns: 1fr; }
  }
  .entity-row {
    display: flex; align-items: center; gap: 10px;
    text-align: left; cursor: pointer; padding: 10px 12px;
  }
  .entity-row .name { font-size: 13.5px; color: var(--text); letter-spacing: -0.005em; }
  .entity-row.active {
    border-color: var(--accent-line);
    background: var(--accent-soft);
  }
</style>
