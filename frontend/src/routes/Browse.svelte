<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "$lib/api";
  import { session } from "$lib/session.svelte";
  import { router } from "$lib/router.svelte";
  import { detail } from "$lib/detail.svelte";
  import { dayKey, dayLabel, fmtTs, timeOnly } from "$lib/format";
  import type { ItemRow, ItemsResponse } from "$lib/types";
  import Empty from "$components/Empty.svelte";
  import Skeleton from "$components/Skeleton.svelte";

  type ViewMode = "table" | "timeline";

  const source = $derived(router.param("source") ?? "");
  const account = $derived(router.param("account") ?? "");
  const kind = $derived(router.param("kind") ?? "");
  const view = $derived<ViewMode>(((router.param("view") as ViewMode | null) ?? "table"));

  let data = $state<ItemsResponse | null>(null);
  let loading = $state(true);
  let error = $state<string | null>(null);

  async function load() {
    loading = true; error = null;
    try {
      data = await api.items({
        source: source || undefined,
        account: account || undefined,
        kind: kind || undefined,
        limit: view === "timeline" ? 250 : 100,
      });
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  onMount(load);
  $effect(() => { void source; void account; void kind; void view; void load(); });

  function patch(p: Record<string, string | null>) { router.patch(p); }
  function clearAll() { router.go("browse"); }

  const hasFilters = $derived(!!(source || account || kind));

  // Timeline grouping
  interface DayBucket { key: string; label: string; events: ItemRow[] }
  const days = $derived.by<DayBucket[]>(() => {
    if (!data) return [];
    const groups = new Map<string, ItemRow[]>();
    for (const it of data.items) {
      const k = dayKey(it.ts);
      const arr = groups.get(k) ?? [];
      arr.push(it);
      groups.set(k, arr);
    }
    const keys = [...groups.keys()].sort((a, b) => (a < b ? 1 : -1));
    return keys.map((k) => ({
      key: k,
      label: dayLabel(k, groups.get(k)?.[0]?.ts),
      events: groups.get(k) ?? [],
    }));
  });
</script>

<div class="page-head">
  <div class="eyebrow">Items + timeline</div>
  <h1>Browse</h1>
  <p class="lede">Normalized items across every source. Filter, then choose how you want to read them. The URL captures everything — share it.</p>
</div>

<div class="toolbar">
  <span class="label">Source</span>
  <select value={source} onchange={(e) => patch({ source: (e.currentTarget as HTMLSelectElement).value || null })}>
    <option value="">all</option>
    {#each session.sources as s (s.slug)}<option value={s.slug}>{s.label}</option>{/each}
  </select>

  <span class="label">Account</span>
  <select value={account} onchange={(e) => patch({ account: (e.currentTarget as HTMLSelectElement).value || null })}>
    <option value="">all</option>
    {#each session.accounts as a (a.handle)}<option value={a.handle}>{a.handle}</option>{/each}
  </select>

  <span class="label">Kind</span>
  <input type="text" placeholder="any" value={kind}
         onchange={(e) => patch({ kind: (e.currentTarget as HTMLInputElement).value || null })} />

  {#if hasFilters}
    <button class="btn btn-ghost btn-sm" type="button" onclick={clearAll}>Clear</button>
  {/if}

  <span class="spacer"></span>

  <div class="seg" role="tablist">
    <button type="button" role="tab" class:on={view === "table"} aria-selected={view === "table"} onclick={() => patch({ view: "table" })}>≡ Table</button>
    <button type="button" role="tab" class:on={view === "timeline"} aria-selected={view === "timeline"} onclick={() => patch({ view: "timeline" })}>⌚ Timeline</button>
  </div>
</div>

{#if loading && !data}
  <div class="col" style="gap:8px">
    {#each Array(8) as _}<Skeleton height="32px" radius="6px" />{/each}
  </div>
{:else if error}
  <div class="error-box">{error}</div>
{:else if data}
  <div class="row-gap" style="margin-bottom:14px;font-size:12px;color:var(--text-3)">
    <span class="mono" style="color:var(--text-2)">{data.total.toLocaleString()}</span>
    <span>item{data.total === 1 ? "" : "s"}{hasFilters ? " match the filters" : " in vault"}</span>
    {#if data.items.length < data.total}
      <span class="dim">· showing first {data.items.length}</span>
    {/if}
  </div>

  {#if data.total === 0}
    <Empty icon="≡" title="Nothing matches" sub={hasFilters ? "Try clearing one of the filters above." : "Register an export to get started."} />
  {:else if view === "table"}
    <div class="tablecard">
      <table>
        <thead>
          <tr>
            <th>id</th><th>source</th><th>account</th><th>kind</th><th>title</th><th>when</th>
          </tr>
        </thead>
        <tbody>
          {#each data.items as it (it.id)}
            <tr class="click" onclick={() => detail.openItem(it.id)}>
              <td class="mono">{it.id}</td>
              <td>{it.source}</td>
              <td class="mono">{it.account}</td>
              <td class="dim">{it.kind}</td>
              <td>{it.title ?? ""}</td>
              <td class="mono dim">{fmtTs(it.ts)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {:else}
    <div class="timeline">
      {#each days as d (d.key)}
        <div class="day">
          <div class="day-label">{d.label}</div>
          <div class="events">
            {#each d.events as it (it.id)}
              <div class="event" role="button" tabindex="0" onclick={() => detail.openItem(it.id)}>
                <span class="time">{timeOnly(it.ts)}</span>
                <span class="source">{it.source}</span>
                <span class="title">{it.title ?? "(untitled)"}</span>
              </div>
            {/each}
          </div>
        </div>
      {/each}
    </div>
  {/if}
{/if}
