<script lang="ts">
  import { api, ApiError } from "$lib/api";
  import { session } from "$lib/session.svelte";
  import { router } from "$lib/router.svelte";
  import { detail } from "$lib/detail.svelte";
  import { fmtRel } from "$lib/format";
  import type { SearchHit } from "$lib/types";
  import Empty from "$components/Empty.svelte";
  import Skeleton from "$components/Skeleton.svelte";

  // URL-driven so Search is sharable.
  const initialQ = router.param("q") ?? "";
  const initialMode = (router.param("mode") as "text" | "semantic" | null) ?? "text";
  const initialSource = router.param("source") ?? "";

  let q = $state(initialQ);
  let mode = $state<"text" | "semantic">(initialMode);
  let source = $state(initialSource);
  let hits = $state<SearchHit[]>([]);
  let loading = $state(false);
  let error = $state<string | null>(null);
  let ran = $state(initialQ.length > 0);

  // Auto-run if URL had a query on first mount.
  $effect(() => {
    if (initialQ && hits.length === 0 && !loading && !error) {
      void run();
    }
  });

  async function run() {
    if (!q.trim()) return;
    loading = true;
    error = null;
    router.patch({ q, mode, source: source || null });
    try {
      hits =
        mode === "semantic"
          ? await api.semanticSearch({ query: q, source: source || undefined, limit: 50 })
          : await api.search({ query: q, source: source || undefined, limit: 50 });
      ran = true;
    } catch (e) {
      error = e instanceof ApiError ? e.message : String(e);
      hits = [];
    } finally {
      loading = false;
    }
  }
</script>

<div class="page-head">
  <div class="eyebrow">Find</div>
  <h1>Search</h1>
  <p class="lede">
    Full-text or semantic, scoped or vault-wide. Click any result to open the detail panel.
  </p>
</div>

<div class="toolbar">
  <input
    type="search"
    class="grow"
    bind:value={q}
    placeholder="Search the vault…"
    onkeydown={(e) => e.key === "Enter" && run()}
    autofocus
  />
  <span class="label">Source</span>
  <select bind:value={source}>
    <option value="">all</option>
    {#each session.sources as s (s.slug)}<option value={s.slug}>{s.label}</option>{/each}
  </select>
  <div class="seg" role="tablist">
    <button
      type="button"
      role="tab"
      class:on={mode === "text"}
      aria-selected={mode === "text"}
      onclick={() => (mode = "text")}>Text</button
    >
    <button
      type="button"
      role="tab"
      class:on={mode === "semantic"}
      aria-selected={mode === "semantic"}
      onclick={() => (mode = "semantic")}>Semantic</button
    >
  </div>
  <button class="btn btn-primary" type="button" onclick={run} disabled={loading || !q.trim()}>
    {loading ? "…" : "Search"}
  </button>
</div>

{#if loading}
  <div class="hit-list">
    {#each Array(5) as _}<div class="hit">
        <Skeleton width="60px" height="20px" />
        <div class="body">
          <Skeleton width="60%" />
          <div style="height:6px"></div>
          <Skeleton width="90%" height="12px" />
        </div>
      </div>{/each}
  </div>
{:else if error}
  <div class="error-box">{error}</div>
{:else if !ran}
  <Empty
    icon="⌕"
    title="Type a query, press Enter"
    sub="Toggle Semantic to search by meaning instead of literal words. Restrict by source to narrow the scope."
  />
{:else if hits.length === 0}
  <Empty
    icon="⌕"
    title="No results"
    sub={`No matches for “${q}” ${source ? `in ${source}` : ""}.`}
  />
{:else}
  <div class="row-gap" style="margin-bottom:14px;font-size:12px;color:var(--text-3)">
    <span class="mono" style="color:var(--text-2)">{hits.length}</span> result{hits.length === 1
      ? ""
      : "s"}
    <span class="dim">· {mode === "semantic" ? "semantic similarity" : "full-text rank"}</span>
  </div>
  <div class="hit-list">
    {#each hits as h (h.id)}
      <div class="hit" role="button" tabindex="0" onclick={() => detail.openItem(h.id)}>
        <span class="pill pill-muted">{h.source}</span>
        <div class="body">
          <div class="title">{h.title ?? "(untitled)"}</div>
          <div class="snippet">{h.snippet ?? ""}</div>
        </div>
        <div class="meta">
          <div>#{h.id}</div>
          {#if h.ts}<div>{fmtRel(h.ts)}</div>{/if}
        </div>
      </div>
    {/each}
  </div>
{/if}
