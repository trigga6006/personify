<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "$lib/api";
  import { toasts } from "$lib/toasts.svelte";
  import type { EmbedStats } from "$lib/types";
  import StatCard from "$components/StatCard.svelte";
  import Skeleton from "$components/Skeleton.svelte";

  let stats = $state<EmbedStats | null>(null);
  let loading = $state(true);
  let error = $state<string | null>(null);

  let batchSize = $state(200);
  let running = $state(false);
  let cancelRequested = $state(false);
  let logLines = $state<{ kind: "info" | "ok" | "err"; text: string }[]>([]);
  let etaText = $state<string>("");

  async function refresh() {
    loading = true;
    try {
      stats = await api.embedStats();
      error = null;
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  onMount(refresh);

  function logLine(text: string, kind: "info" | "ok" | "err" = "info") {
    logLines = [...logLines, { kind, text }];
  }

  function fmtEta(sec: number): string {
    if (sec < 60) return `${sec}s`;
    if (sec < 3600) return `${Math.floor(sec / 60)}m ${sec % 60}s`;
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    return `${h}h ${m}m`;
  }

  async function runJob(once: boolean) {
    if (running) return;
    running = true;
    cancelRequested = false;
    etaText = "";
    const startedAt = Date.now();
    let initialEmbedded: number | null = null;
    let batchIdx = 0;
    try {
      while (!cancelRequested) {
        stats = await api.embedStats();
        if (initialEmbedded === null) initialEmbedded = stats.items_embedded;
        if (stats.items_pending === 0) {
          logLine(`All ${stats.items_embedded.toLocaleString()} items already embedded.`, "ok");
          break;
        }
        batchIdx += 1;
        const t0 = Date.now();
        logLine(`Batch ${batchIdx} · running (limit ${batchSize})…`);
        const res = await api.embed({ limit: batchSize });
        const ms = Date.now() - t0;
        logLine(
          `Batch ${batchIdx} · embedded ${res.embedded.toLocaleString()} chunks in ${(ms / 1000).toFixed(1)}s`,
          "ok",
        );
        const after = await api.embedStats();
        stats = after;
        const itemsDone = (after.items_embedded || 0) - (initialEmbedded || 0);
        if (itemsDone > 0) {
          const msPerItem = (Date.now() - startedAt) / itemsDone;
          const sec = Math.round((after.items_pending * msPerItem) / 1000);
          etaText = `~${fmtEta(sec)} remaining`;
        }
        if (res.embedded === 0) {
          logLine("Batch produced no chunks; stopping.", "err");
          break;
        }
        if (once) break;
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      logLine(`Error: ${msg}`, "err");
      toasts.err(msg);
    } finally {
      if (cancelRequested) logLine("Stopped by user.", "info");
      running = false;
      etaText = "";
      await refresh();
    }
  }

  const pct = $derived(
    stats && stats.items_with_text > 0 ? (stats.items_embedded / stats.items_with_text) * 100 : 0,
  );
  const deviceClass = $derived(
    stats?.device.device === "cuda"
      ? "pill-ok"
      : stats?.device.available
        ? "pill-info"
        : "pill-muted",
  );
</script>

<div class="page-head">
  <div class="eyebrow">Vector index</div>
  <h1>Embeddings</h1>
  <p class="lede">
    Compute vector embeddings so semantic search can match by meaning instead of literal words. Each
    text item is chunked at 1,500 chars; one item produces one or more embedding rows.
  </p>
</div>

{#if loading && !stats}
  <div class="statgrid">
    <StatCard label="Items with text" value="—" loading /><StatCard
      label="Embedded"
      value="—"
      loading
    /><StatCard label="Pending" value="—" loading /><StatCard label="Chunks" value="—" loading />
  </div>
{:else if error}
  <div class="error-box">{error}</div>
{:else if stats}
  <div class="statgrid">
    <StatCard label="Items with text" value={stats.items_with_text} />
    <StatCard label="Embedded" value={stats.items_embedded} />
    <StatCard label="Pending" value={stats.items_pending} />
    <StatCard label="Chunks stored" value={stats.total_chunks} />
  </div>

  <div class="section-label">Progress</div>
  <div class="barlist" style="margin-bottom:6px">
    <div class="barrow">
      <div class="barname">overall</div>
      <div class="bartrack"><div class="barfill" style="width:{pct.toFixed(1)}%"></div></div>
      <div class="barcount">{pct.toFixed(1)}%</div>
    </div>
  </div>
  <div class="row-gap dim" style="font-size:12px;margin-bottom:18px">
    <span
      >Model <span class="mono" style="color:var(--text-2)">{stats.model}</span> · {stats.embed_dim}-dim</span
    >
    <span>Device <span class="pill {deviceClass}">{stats.device.label}</span></span>
    {#if stats.device.torch}<span>torch {stats.device.torch}</span>{/if}
  </div>

  {#if !stats.device.available}
    <div class="error-box" style="margin-bottom:14px">
      {stats.device.note ?? "Embeddings backend not available."}
    </div>
  {/if}

  <div class="hover-tile" style="max-width:760px">
    <div class="field-row">
      <label for="e-batch">Batch size</label>
      <div class="inputs" style="max-width:160px">
        <input id="e-batch" type="number" min="1" max="5000" bind:value={batchSize} />
        <span class="help"
          >Smaller batches give snappier progress; larger ones run a touch faster.</span
        >
      </div>
    </div>
    <div class="row-gap">
      <button
        class="btn btn-primary"
        type="button"
        onclick={() => runJob(false)}
        disabled={running || stats.items_pending === 0 || !stats.device.available}
      >
        ∿ Embed all pending
      </button>
      <button
        class="btn"
        type="button"
        onclick={() => runJob(true)}
        disabled={running || stats.items_pending === 0 || !stats.device.available}
      >
        Embed one batch
      </button>
      <button
        class="btn btn-ghost"
        type="button"
        onclick={() => (cancelRequested = true)}
        disabled={!running}>Stop</button
      >
      <span class="spacer"></span>
      {#if etaText}<span class="dim mono" style="font-size:11.5px">{etaText}</span>{/if}
    </div>

    {#if logLines.length}
      <div class="process-log">
        {#each logLines as l, i (i)}
          <div class="line {l.kind}">{l.text}</div>
        {/each}
      </div>
    {/if}
  </div>
{/if}
