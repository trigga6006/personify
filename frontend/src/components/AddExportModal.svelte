<script lang="ts">
  import { onMount } from "svelte";
  import { api, ApiError } from "$lib/api";
  import { session } from "$lib/session.svelte";
  import { modal } from "$lib/modal.svelte";
  import { toasts } from "$lib/toasts.svelte";
  import { router } from "$lib/router.svelte";
  import { pillClassForStatus } from "$lib/format";
  import { sourceBrand } from "$lib/sourceBrands";
  import type { PipelineResult } from "$lib/types";

  type Phase = "form" | "registering" | "ingesting" | "done" | "error";

  let phase = $state<Phase>("form");
  let source = $state("");
  let path = $state("");
  let account = $state("");
  let notes = $state("");
  let thenIngest = $state(true);
  let withEmbeddings = $state(false);
  let withGraph = $state(false);
  let pipelineResult = $state<PipelineResult | null>(null);
  let errorMsg = $state<string | null>(null);
  let registeredId = $state<number | null>(null);
  let sourceOpen = $state(false);
  let sourcePicker = $state<HTMLDivElement | null>(null);

  const valid = $derived(!!source && !!path.trim() && !!account.trim());
  const busy = $derived(phase === "registering" || phase === "ingesting");
  const sourceOptions = $derived(
    [...session.parsers].sort((a, b) =>
      sourceBrand(a.slug).title.localeCompare(sourceBrand(b.slug).title),
    ),
  );
  const selectedParser = $derived(session.parsers.find((p) => p.slug === source) ?? null);
  const selectedBrand = $derived(selectedParser ? sourceBrand(selectedParser.slug) : null);

  onMount(() => {
    const onPointerDown = (e: PointerEvent) => {
      if (sourcePicker && !sourcePicker.contains(e.target as Node)) sourceOpen = false;
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") sourceOpen = false;
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  });

  async function submit() {
    if (!valid || busy) return;
    errorMsg = null;
    pipelineResult = null;
    phase = "registering";
    try {
      const reg = await api.registerExport({
        source,
        path: path.trim(),
        account: account.trim(),
        notes: notes.trim() || null,
      });
      registeredId = reg.id;
      toasts.ok(`Registered export #${reg.id}`);
      if (thenIngest) {
        phase = "ingesting";
        if (withEmbeddings || withGraph) {
          const res = await api.pipeline({
            export_id: reg.id,
            with_embeddings: withEmbeddings,
            with_graph: withGraph,
          });
          pipelineResult = res.pipeline;
          const anyError = pipelineResult.stages.some((s) => s.status === "error");
          if (anyError) toasts.err("Pipeline finished with errors");
          else toasts.ok("Pipeline complete");
        } else {
          const res = await api.ingest({ export_id: reg.id });
          const run = res.runs?.[0];
          if (run) toasts.ok(`Ingest ${run.status} · ${run.items_inserted} inserted`);
        }
      }
      phase = "done";
      await session.refresh();
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e);
      errorMsg = msg;
      toasts.err(msg);
      phase = "error";
    }
  }

  function reset() {
    source = "";
    path = "";
    account = "";
    notes = "";
    pipelineResult = null;
    errorMsg = null;
    registeredId = null;
    sourceOpen = false;
    phase = "form";
  }

  function gotoExports() {
    modal.close();
    router.go("exports");
  }

  function chooseSource(slug: string) {
    source = slug;
    sourceOpen = false;
  }
</script>

<div class="modal-shell" role="dialog" aria-labelledby="add-export-title">
  <div class="head">
    <h2 id="add-export-title">Register an export</h2>
    <button class="icon-btn" type="button" aria-label="Close" onclick={() => modal.close()}
      >✕</button
    >
  </div>

  <div class="body">
    {#if phase === "form" || phase === "registering" || phase === "ingesting" || phase === "error"}
      <div class="field-row">
        <label for="ae-source">Source</label>
        <div class="inputs">
          <div class="source-picker" bind:this={sourcePicker}>
            <button
              id="ae-source"
              class="source-trigger"
              class:open={sourceOpen}
              type="button"
              disabled={busy}
              aria-haspopup="listbox"
              aria-expanded={sourceOpen}
              onclick={() => (sourceOpen = !sourceOpen)}
            >
              {#if selectedBrand && selectedParser}
                <span class="brand-mark">
                  <svg viewBox={selectedBrand.viewBox} aria-hidden="true">
                    {#each selectedBrand.paths as path}<path d={path.d} fill={path.fill}
                      ></path>{/each}
                  </svg>
                </span>
                <span class="source-copy">
                  <span class="source-title">{selectedBrand.title}</span>
                  <span class="source-sub">{selectedBrand.subtitle}</span>
                </span>
              {:else}
                <span class="brand-mark empty" aria-hidden="true"></span>
                <span class="source-copy">
                  <span class="source-title">Select source</span>
                  <span class="source-sub">Choose an export parser</span>
                </span>
              {/if}
              <span class="source-chev" aria-hidden="true">⌄</span>
            </button>

            {#if sourceOpen}
              <div class="source-menu" role="listbox" aria-labelledby="ae-source">
                {#each sourceOptions as p (p.slug)}
                  {@const brand = sourceBrand(p.slug)}
                  <button
                    class="source-option"
                    class:selected={source === p.slug}
                    type="button"
                    role="option"
                    aria-selected={source === p.slug}
                    onclick={() => chooseSource(p.slug)}
                  >
                    <span class="brand-mark">
                      <svg viewBox={brand.viewBox} aria-hidden="true">
                        {#each brand.paths as path}<path d={path.d} fill={path.fill}></path>{/each}
                      </svg>
                    </span>
                    <span class="source-copy">
                      <span class="source-title">{brand.title}</span>
                      <span class="source-sub">{brand.subtitle}</span>
                    </span>
                    <span class="source-slug">{p.slug}</span>
                  </button>
                {/each}
              </div>
            {/if}
          </div>
          <span class="help">Pick the parser that matches the export type.</span>
        </div>
      </div>

      <div class="field-row">
        <label for="ae-path">Path</label>
        <div class="inputs">
          <input
            id="ae-path"
            type="text"
            bind:value={path}
            placeholder="C:\Users\you\Downloads\twitter-2026.zip"
            disabled={busy}
          />
          <span class="help">Full path to a .zip file or extracted directory on this machine.</span>
        </div>
      </div>

      <div class="field-row">
        <label for="ae-account">Account</label>
        <div class="inputs">
          <input
            id="ae-account"
            type="text"
            bind:value={account}
            placeholder="you@example.com or @handle"
            list="ae-acct-list"
            disabled={busy}
          />
          <datalist id="ae-acct-list">
            {#each session.accounts as a (a.handle)}<option value={a.handle}></option>{/each}
          </datalist>
          <span class="help">Email, username, or any label that identifies this account.</span>
        </div>
      </div>

      <div class="field-row">
        <label for="ae-notes">Notes</label>
        <div class="inputs">
          <textarea
            id="ae-notes"
            bind:value={notes}
            placeholder="Anything to remember about this export…"
            disabled={busy}
          ></textarea>
        </div>
      </div>

      <div class="field-row">
        <label>Pipeline</label>
        <div class="inputs">
          <div class="toggle-cluster">
            <label class="checkbox-row">
              <input type="checkbox" bind:checked={thenIngest} disabled={busy} /> Ingest immediately
            </label>
            <label class="checkbox-row">
              <input type="checkbox" bind:checked={withEmbeddings} disabled={busy || !thenIngest} /> Compute
              embeddings
            </label>
            <label class="checkbox-row">
              <input type="checkbox" bind:checked={withGraph} disabled={busy || !thenIngest} /> Extract
              graph
            </label>
          </div>
          <span class="help"
            >Standard ingest is always run; embeddings and graph extraction are optional follow-on
            stages.</span
          >
        </div>
      </div>

      {#if errorMsg}
        <div class="error-box" style="margin-top:8px">{errorMsg}</div>
      {/if}
    {:else if phase === "done"}
      <div class="row-gap" style="margin-bottom:14px">
        <span class="pill pill-ok">Registered</span>
        <span class="mono dim">#{registeredId}</span>
      </div>
      {#if pipelineResult}
        <div class="section-label" style="margin-top:10px">Pipeline stages</div>
        <div class="col" style="gap:6px">
          {#each pipelineResult.stages as s (s.stage)}
            <div class="row-gap" style="font-size:12.5px">
              <span class="pill {pillClassForStatus(s.status)}">{s.stage}: {s.status}</span>
              <span class="mono dim">items={s.items_processed}</span>
              {#if s.error}<span class="dim">· {s.error}</span>{/if}
            </div>
          {/each}
        </div>
      {/if}
    {/if}
  </div>

  <div class="foot">
    {#if phase === "done"}
      <button class="btn btn-ghost" type="button" onclick={reset}>Add another</button>
      <button class="btn btn-primary" type="button" onclick={gotoExports}>View exports →</button>
    {:else}
      <button class="btn btn-ghost" type="button" onclick={() => modal.close()} disabled={busy}
        >Cancel</button
      >
      <button class="btn btn-primary" type="button" onclick={submit} disabled={!valid || busy}>
        {#if phase === "registering"}<span class="spinner spinner-xs"></span> Registering…
        {:else if phase === "ingesting"}<span class="spinner spinner-xs"></span> Ingesting…
        {:else}Register{/if}
      </button>
    {/if}
  </div>
</div>

<style>
  .spinner-xs {
    width: 11px;
    height: 11px;
    border-width: 2px;
  }
  .icon-btn {
    background: transparent;
    border: 0;
    color: var(--text-3);
    padding: 6px 8px;
    border-radius: 6px;
    font-size: 14px;
    line-height: 1;
  }
  .icon-btn:hover {
    background: var(--bg-soft);
    color: var(--text);
  }

  .source-picker {
    position: relative;
  }

  .source-trigger,
  .source-option {
    display: grid;
    grid-template-columns: 20px minmax(0, 1fr) auto;
    align-items: center;
    gap: 8px;
    width: 100%;
    border: 1px solid var(--line);
    background: var(--bg-input);
    color: var(--text);
    text-align: left;
    font: inherit;
  }

  .source-trigger {
    height: 38px;
    min-height: 38px;
    border-radius: 7px;
    padding: 6px 10px;
    overflow: hidden;
    transition:
      border-color var(--t-fast),
      background var(--t-fast),
      box-shadow var(--t-fast);
  }

  .source-trigger .brand-mark,
  .source-trigger .source-chev {
    align-self: center;
  }

  .source-trigger:hover,
  .source-trigger.open {
    border-color: var(--accent-line);
    background: var(--bg-soft);
    box-shadow: 0 0 0 3px var(--accent-soft);
  }

  .source-trigger:disabled {
    cursor: not-allowed;
    opacity: 0.58;
  }

  .brand-mark {
    width: 20px;
    height: 20px;
    display: grid;
    place-items: center;
    color: var(--text-2);
    background: transparent;
    border: 0;
    flex-shrink: 0;
  }

  .brand-mark.empty {
    opacity: 0.35;
  }

  .brand-mark svg {
    width: 18px;
    height: 18px;
    display: block;
  }

  .source-copy {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 1px;
  }

  .source-trigger .source-copy {
    display: block;
    align-self: center;
    height: 16px;
    line-height: 16px;
    overflow: hidden;
  }

  .source-title {
    color: var(--text);
    font-size: 13px;
    font-weight: 500;
    letter-spacing: -0.005em;
    line-height: 1.2;
  }

  .source-sub {
    color: var(--text-3);
    font-size: 11px;
    line-height: 1.25;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .source-trigger .source-sub {
    display: none;
  }

  .source-trigger .source-title {
    display: block;
    line-height: 16px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .source-chev {
    color: var(--text-3);
    font-size: 14px;
    transition:
      transform var(--t-fast),
      color var(--t-fast);
  }

  .source-trigger.open .source-chev {
    transform: rotate(180deg);
    color: var(--accent);
  }

  .source-menu {
    position: absolute;
    left: 0;
    right: 0;
    top: calc(100% + 5px);
    z-index: 120;
    max-height: 292px;
    overflow-y: auto;
    padding: 5px;
    border: 1px solid var(--line-strong);
    border-radius: 10px;
    background: color-mix(in srgb, var(--bg-card) 94%, black);
    box-shadow:
      0 18px 42px -16px rgba(0, 0, 0, 0.7),
      inset 0 1px 0 var(--rule-strong);
  }

  .source-option {
    min-height: 38px;
    border-color: transparent;
    border-radius: 7px;
    padding: 5px 7px;
    background: transparent;
    cursor: pointer;
    transition:
      background var(--t-fast),
      border-color var(--t-fast);
  }

  .source-option:hover,
  .source-option.selected {
    background: var(--bg-soft);
    border-color: var(--rule-strong);
  }

  .source-option.selected {
    box-shadow: inset 2px 0 0 var(--accent);
  }

  .source-slug {
    justify-self: end;
    color: var(--text-3);
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0;
    padding: 1px 5px;
    border: 1px solid var(--rule);
    border-radius: 999px;
    background: var(--bg-input);
  }
</style>
