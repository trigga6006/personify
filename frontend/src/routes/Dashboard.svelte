<script lang="ts">
  import { onMount } from "svelte";
  import { api, ApiError } from "$lib/api";
  import { session } from "$lib/session.svelte";
  import { router } from "$lib/router.svelte";
  import { fmtRel } from "$lib/format";
  import type { RunSummary } from "$lib/types";
  import StatCard from "$components/StatCard.svelte";
  import StatusPill from "$components/StatusPill.svelte";
  import BarRow from "$components/BarRow.svelte";
  import Empty from "$components/Empty.svelte";
  import Skeleton from "$components/Skeleton.svelte";
  import { modal } from "$lib/modal.svelte";

  let runs = $state<RunSummary[]>([]);
  let loadingRuns = $state(true);
  let error = $state<string | null>(null);

  onMount(async () => {
    try {
      runs = await api.runs(10);
    } catch (e) {
      error = e instanceof ApiError ? e.message : String(e);
    } finally {
      loadingRuns = false;
    }
  });

  const sourceEntries = $derived(
    Object.entries(session.itemsPerSource).sort((a, b) => b[1] - a[1])
  );
  const maxSource = $derived(sourceEntries.length ? sourceEntries[0][1] : 0);
  const sourceLabels = $derived(Object.fromEntries(session.sources.map((s) => [s.slug, s.label])));
</script>

<div class="page-head">
  <div class="eyebrow">Vault overview</div>
  <h1>Dashboard</h1>
  <p class="lede">Personal data ingested from your services. The protagonists are the numbers.</p>
</div>

<div class="statgrid">
  <StatCard label="Items" value={session.totalItems} loading={!session.loaded} />
  <StatCard label="Exports" value={session.totalExports} loading={!session.loaded} />
  <StatCard label="Sources" value={Object.keys(session.itemsPerSource).length} loading={!session.loaded} />
  <StatCard label="Accounts" value={session.accounts.length} loading={!session.loaded} />
  <StatCard label="Runs" value={session.totalRuns} loading={!session.loaded} />
</div>

<div class="section-label">Items per source</div>
{#if !session.loaded}
  <div class="col" style="gap:10px">
    {#each Array(4) as _}<Skeleton height="22px" />{/each}
  </div>
{:else if sourceEntries.length === 0}
  <Empty
    icon="▤"
    title="No data yet"
    sub="Register your first export — chatgpt, gmail, twitter, anything — and it shows up here."
    cta={{ label: "+ Add export", onclick: () => modal.open("add-export") }}
  />
{:else}
  <div class="barlist">
    {#each sourceEntries as [slug, n] (slug)}
      <BarRow
        name={sourceLabels[slug] ?? slug}
        count={n}
        max={maxSource}
        onclick={() => router.go("browse", { source: slug })}
      />
    {/each}
  </div>
{/if}

<div class="section-label">Recent ingestion runs</div>
{#if loadingRuns}
  <div class="col" style="gap:8px">
    {#each Array(4) as _}<Skeleton height="36px" radius="8px" />{/each}
  </div>
{:else if error}
  <div class="error-box">{error}</div>
{:else if runs.length === 0}
  <Empty icon="∿" title="No runs yet" sub="Run history shows up here once you ingest an export." />
{:else}
  <div class="tablecard">
    <table>
      <thead>
        <tr>
          <th>id</th>
          <th>export</th>
          <th>parser</th>
          <th>status</th>
          <th class="right">seen</th>
          <th class="right">inserted</th>
          <th class="right">skipped</th>
          <th>started</th>
        </tr>
      </thead>
      <tbody>
        {#each runs as r (r.id)}
          <tr>
            <td class="mono">{r.id}</td>
            <td class="mono">{r.raw_export_id}</td>
            <td>{r.parser ?? ""}<span class="dim mono" style="font-size:11px"> · v{r.parser_version ?? ""}</span></td>
            <td><StatusPill status={r.status} /></td>
            <td class="right mono">{r.items_seen.toLocaleString()}</td>
            <td class="right mono">{r.items_inserted.toLocaleString()}</td>
            <td class="right mono">{r.items_skipped.toLocaleString()}</td>
            <td class="mono dim" title={r.started_at ?? ""}>{fmtRel(r.started_at)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
{/if}
