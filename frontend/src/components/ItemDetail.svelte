<script lang="ts">
  import { api } from "$lib/api";
  import { detail } from "$lib/detail.svelte";
  import { fmtTs } from "$lib/format";
  import type { ItemFull } from "$lib/types";
  import Skeleton from "./Skeleton.svelte";

  let { itemId }: { itemId: number } = $props();

  let item = $state<ItemFull | null>(null);
  let loading = $state(true);
  let error = $state<string | null>(null);

  $effect(() => {
    void itemId;
    loading = true;
    error = null;
    item = null;
    api.item(itemId)
      .then((i) => { item = i; })
      .catch((e) => { error = e instanceof Error ? e.message : String(e); })
      .finally(() => { loading = false; });
  });
</script>

<div class="detail-head">
  <div class="detail-title">
    {item?.title || (loading ? "Loading…" : `Item #${itemId}`)}
  </div>
  <button class="icon-btn" type="button" aria-label="Close" onclick={() => detail.close()}>✕</button>
</div>

<div class="detail-body">
  {#if loading}
    <Skeleton width="120px" height="10px" />
    <div style="height:14px"></div>
    <Skeleton width="80%" height="14px" />
    <div style="height:8px"></div>
    <Skeleton width="60%" height="14px" />
  {:else if error}
    <div class="error-box">{error}</div>
  {:else if item}
    <div class="detail-section-label">Identity</div>
    <dl class="deflist">
      <dt>id</dt><dd>{item.id}</dd>
      <dt>source</dt><dd class="plain">{item.source}</dd>
      <dt>account</dt><dd>{item.account}</dd>
      <dt>kind</dt><dd class="plain">{item.kind}</dd>
      <dt>when</dt><dd class="plain">{fmtTs(item.ts)}</dd>
      <dt>native</dt><dd>{item.native_id ?? "—"}</dd>
      <dt>export</dt><dd>{item.raw_export_id}</dd>
      <dt>run</dt><dd>{item.ingestion_run_id ?? "—"}</dd>
      <dt>hash</dt><dd>{(item.content_hash || "").slice(0, 16)}…</dd>
    </dl>

    {#if item.tags && item.tags.length}
      <div class="detail-section-label">Tags</div>
      <div class="tagchips">
        {#each item.tags as t}
          <span class="chip"><span class="k">{t.key}</span><span>{t.value}</span></span>
        {/each}
      </div>
    {/if}

    {#if item.media && item.media.length}
      <div class="detail-section-label">Media</div>
      <div class="tagchips">
        {#each item.media as m}
          <span class="chip"><span class="k">{m.media_type ?? m.type}</span><span>{m.mime ?? ""}</span></span>
        {/each}
      </div>
    {/if}

    {#if item.body}
      <div class="detail-section-label">
        Body <span class="dim" style="text-transform:none;letter-spacing:0;font-weight:400">({item.body.length.toLocaleString()} chars)</span>
      </div>
      <pre class="body-pre">{item.body}</pre>
    {:else}
      <div class="detail-section-label">Body</div>
      <div class="dim" style="font-size:13px">No text body.</div>
    {/if}

    {#if item.metadata && Object.keys(item.metadata).length}
      <div class="detail-section-label">Metadata</div>
      <pre class="body-pre">{JSON.stringify(item.metadata, null, 2)}</pre>
    {/if}
  {/if}
</div>

<style>
  .icon-btn { background: transparent; border: 0; color: var(--text-3); padding: 6px 8px; border-radius: 6px; font-size: 14px; }
  .icon-btn:hover { background: var(--bg-soft); color: var(--text); }
</style>
