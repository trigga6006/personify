<script lang="ts">
  import { api } from "$lib/api";
  import { detail } from "$lib/detail.svelte";
  import type { EntityContext } from "$lib/types";
  import Skeleton from "./Skeleton.svelte";

  let { entityId }: { entityId: number } = $props();

  let ctx = $state<EntityContext | null>(null);
  let loading = $state(true);
  let error = $state<string | null>(null);

  $effect(() => {
    void entityId;
    loading = true;
    error = null;
    ctx = null;
    api
      .entityContext(entityId)
      .then((c) => {
        ctx = c;
      })
      .catch((e) => {
        error = e instanceof Error ? e.message : String(e);
      })
      .finally(() => {
        loading = false;
      });
  });
</script>

<div class="detail-head">
  <div class="detail-title">
    {ctx?.entity.name ?? (loading ? "Loading…" : `Entity #${entityId}`)}
  </div>
  <button class="icon-btn" type="button" aria-label="Close" onclick={() => detail.close()}>✕</button
  >
</div>

<div class="detail-body">
  {#if loading}
    <Skeleton width="120px" height="10px" />
    <div style="height:14px"></div>
    <Skeleton width="80%" height="14px" />
  {:else if error}
    <div class="error-box">{error}</div>
  {:else if ctx}
    <div class="detail-section-label">Identity</div>
    <dl class="deflist">
      <dt>id</dt>
      <dd>{ctx.entity.id}</dd>
      <dt>type</dt>
      <dd class="plain">{ctx.entity.type}</dd>
      <dt>name</dt>
      <dd class="plain">{ctx.entity.name}</dd>
      {#if ctx.entity.description}<dt>summary</dt>
        <dd class="plain">{ctx.entity.description}</dd>{/if}
    </dl>

    {#if ctx.aliases.length}
      <div class="detail-section-label">Aliases</div>
      <div class="tagchips">
        {#each ctx.aliases as a}<span class="chip">{a.alias}</span>{/each}
      </div>
    {/if}

    {#if ctx.related_entities.length}
      <div class="detail-section-label">Related ({ctx.related_entities.length})</div>
      <div class="col" style="gap:6px">
        {#each ctx.related_entities as r}
          <button
            class="hover-tile row-gap"
            type="button"
            style="cursor:pointer;text-align:left"
            onclick={() => detail.openEntity(r.id)}
          >
            <span class="pill pill-muted" style="font-size:10px">{r.type}</span>
            <span style="font-size:13px">{r.name}</span>
          </button>
        {/each}
      </div>
    {/if}

    {#if ctx.evidence.length}
      <div class="detail-section-label">Evidence ({ctx.evidence.length})</div>
      <div class="col" style="gap:6px">
        {#each ctx.evidence as ev}
          <div class="hover-tile">
            <div
              class="dim mono"
              style="font-size:10.5px;letter-spacing:0.1em;text-transform:uppercase"
            >
              {ev.source_type}{ev.source_id ? ` · ${ev.source_id}` : ""}
            </div>
            {#if ev.quote}<div
                style="margin-top:4px;font-size:12.5px;color:var(--text-2);line-height:1.5"
              >
                {ev.quote}
              </div>{/if}
          </div>
        {/each}
      </div>
    {/if}

    {#if ctx.suggested_queries.length}
      <div class="detail-section-label">Suggested</div>
      <div class="col" style="gap:6px">
        {#each ctx.suggested_queries as q}
          <div class="hover-tile" style="font-size:12.5px;color:var(--text-2)">{q}</div>
        {/each}
      </div>
    {/if}
  {/if}
</div>

<style>
  .icon-btn {
    background: transparent;
    border: 0;
    color: var(--text-3);
    padding: 6px 8px;
    border-radius: 6px;
    font-size: 14px;
  }
  .icon-btn:hover {
    background: var(--bg-soft);
    color: var(--text);
  }
</style>
