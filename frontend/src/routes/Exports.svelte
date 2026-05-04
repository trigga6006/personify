<script lang="ts">
  import { onMount } from "svelte";
  import { api, ApiError } from "$lib/api";
  import { session } from "$lib/session.svelte";
  import { modal } from "$lib/modal.svelte";
  import { toasts } from "$lib/toasts.svelte";
  import { fmtBytes, fmtRel } from "$lib/format";
  import type { ExportRow, PipelineResult, RunSummary } from "$lib/types";
  import StagePill from "$components/StagePill.svelte";
  import Empty from "$components/Empty.svelte";
  import Skeleton from "$components/Skeleton.svelte";

  let rows = $state<ExportRow[]>([]);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let busy = $state<Record<number, "ingest" | "replace" | null>>({});
  let withEmbeddings = $state(false);
  let withGraph = $state(false);

  async function load() {
    loading = true;
    error = null;
    try { rows = await api.exports(); }
    catch (e) { error = e instanceof Error ? e.message : String(e); }
    finally { loading = false; }
  }

  onMount(load);

  function summarizePipeline(p: PipelineResult) {
    const labels = p.stages.map((s) => `${s.stage}=${s.status}`).join(" · ");
    if (p.stages.some((s) => s.status === "error")) toasts.err(labels);
    else toasts.ok(labels);
  }

  function toastForRun(run: RunSummary) {
    if (run.status === "ok") toasts.ok(`Done · seen ${run.items_seen}, inserted ${run.items_inserted}`);
    else toasts.err(`Run ${run.status}`);
  }

  async function ingestOne(row: ExportRow, replace: boolean) {
    busy[row.id] = replace ? "replace" : "ingest";
    const verb = replace ? "Replacing" : "Ingesting";
    const usePipeline = withEmbeddings || withGraph;
    const suffix = usePipeline ? ` (+ ${[withEmbeddings && "embeddings", withGraph && "graph"].filter(Boolean).join(", ")})` : "";
    toasts.info(`${verb} export ${row.id}${suffix}…`);
    try {
      if (usePipeline) {
        const res = await api.pipeline({ export_id: row.id, replace, with_embeddings: withEmbeddings, with_graph: withGraph });
        summarizePipeline(res.pipeline);
      } else {
        const res = await api.ingest({ export_id: row.id, replace });
        const run = res.runs?.[0];
        if (run) toastForRun(run);
      }
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : String(e);
      toasts.err(msg);
    } finally {
      busy[row.id] = null;
      await Promise.all([load(), session.refresh()]);
    }
  }

  async function ingestAllPending() {
    toasts.info("Ingesting all pending exports…");
    try {
      const res = await api.ingest({ all_pending: true });
      const runs = res.runs ?? [];
      if (!runs.length) toasts.info("No pending exports.");
      else toasts.ok(`${runs.length} run(s) completed.`);
    } catch (e) { toasts.err(e instanceof Error ? e.message : String(e)); }
    await Promise.all([load(), session.refresh()]);
  }
</script>

<div class="page-head">
  <div class="eyebrow">Raw exports</div>
  <h1>Exports</h1>
  <p class="lede">Every file the vault has been told about. Standard ingest is always run; embeddings and graph extraction are opt-in stages you can re-run later.</p>
</div>

<div class="row-gap" style="margin-bottom:16px">
  <button class="btn btn-primary" type="button" onclick={() => modal.open("add-export")}>+ New export</button>
  <button class="btn" type="button" onclick={ingestAllPending}>▶ Ingest all pending</button>
  <button class="btn btn-ghost" type="button" onclick={() => Promise.all([load(), session.refresh()])}>↻ Refresh</button>
  <span class="spacer"></span>
  <span class="dim" style="font-size:11.5px">When ingesting:</span>
  <label class="checkbox-row"><input type="checkbox" bind:checked={withEmbeddings} /> embeddings</label>
  <label class="checkbox-row"><input type="checkbox" bind:checked={withGraph} /> graph</label>
</div>

{#if loading && rows.length === 0}
  <div class="col" style="gap:8px">
    {#each Array(4) as _}<Skeleton height="44px" radius="10px" />{/each}
  </div>
{:else if error}
  <div class="error-box">{error}</div>
{:else if rows.length === 0}
  <Empty
    icon="▢"
    title="No exports yet"
    sub="Register a downloaded export — the file is copied (never moved) into vault/raw/ and dedup-hashed."
    cta={{ label: "+ Add your first export", onclick: () => modal.open("add-export") }}
  />
{:else}
  <div class="tablecard">
    <table>
      <thead>
        <tr>
          <th>id</th>
          <th>source</th>
          <th>account</th>
          <th class="right">size</th>
          <th class="right">items</th>
          <th>stages</th>
          <th>received</th>
          <th class="right">actions</th>
        </tr>
      </thead>
      <tbody>
        {#each rows as r (r.id)}
          <tr>
            <td class="mono">{r.id}</td>
            <td>{r.source}</td>
            <td class="mono">{r.account}</td>
            <td class="right mono">{fmtBytes(r.size_bytes)}</td>
            <td class="right mono">{r.items.toLocaleString()}</td>
            <td>
              <div class="stagestrip">
                <div class="chip"><span class="name">ingest</span><StagePill stage={r.pipeline_stages.ingest} /></div>
                <div class="chip"><span class="name">embed</span><StagePill stage={r.pipeline_stages.embed} /></div>
                <div class="chip"><span class="name">graph</span><StagePill stage={r.pipeline_stages.graph} /></div>
              </div>
            </td>
            <td class="mono dim" title={r.received_at ?? ""}>{fmtRel(r.received_at)}</td>
            <td class="actions">
              <button class="btn btn-sm" disabled={!!busy[r.id]} onclick={() => ingestOne(r, false)}>
                {#if busy[r.id] === "ingest"}<span class="spinner spinner-xs"></span> Ingesting…{:else}▶ Ingest{/if}
              </button>
              <button class="btn btn-sm" disabled={!!busy[r.id]} onclick={() => ingestOne(r, true)} title="Reset & re-ingest">
                {#if busy[r.id] === "replace"}<span class="spinner spinner-xs"></span> Replacing…{:else}⟳ Replace{/if}
              </button>
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
{/if}

<style>
  .spinner-xs { width: 11px; height: 11px; border-width: 2px; }
</style>
