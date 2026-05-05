<script lang="ts">
  import { api, ApiError } from "$lib/api";
  import { session } from "$lib/session.svelte";
  import { toasts } from "$lib/toasts.svelte";
  import { shortenPath, pillClassForStatus } from "$lib/format";
  import type { RepoRegisterResult, RepoScanRow } from "$lib/types";
  import Empty from "$components/Empty.svelte";
  import StatCard from "$components/StatCard.svelte";

  let path = $state("");
  let recursive = $state(false);
  let account = $state(session.activeVault?.name ?? "code-corpus");
  let notes = $state("");
  let ingestImmediately = $state(true);

  let scanning = $state(false);
  let registering = $state(false);
  let scan = $state<RepoScanRow[]>([]);
  let didScan = $state(false);
  let results = $state<RepoRegisterResult[]>([]);
  let error = $state<string | null>(null);

  const total = $derived(scan.length);
  const dupes = $derived(scan.filter((r) => r.duplicate).length);
  const fresh = $derived(total - dupes);

  async function doScan() {
    if (!path.trim()) {
      toasts.err("Path is required.");
      return;
    }
    scanning = true;
    error = null;
    try {
      const res = await api.repoScan({ path: path.trim(), recursive });
      scan = res.repos;
      didScan = true;
      results = [];
    } catch (e) {
      error = e instanceof ApiError ? e.message : String(e);
    } finally {
      scanning = false;
    }
  }

  async function doRegister() {
    if (!fresh) return;
    registering = true;
    error = null;
    try {
      const res = await api.repoRegister({
        path: path.trim(),
        account: account.trim() || "code-corpus",
        recursive,
        ingest: ingestImmediately,
        notes: notes.trim() || null,
      });
      results = res.results;
      toasts.ok(`${results.length} repo result${results.length === 1 ? "" : "s"}`);
      await session.refresh();
      await doScan(); // refresh duplicates
    } catch (e) {
      error = e instanceof ApiError ? e.message : String(e);
    } finally {
      registering = false;
    }
  }
</script>

<div class="page-head">
  <div class="eyebrow">Bulk register</div>
  <h1>Repo intake</h1>
  <p class="lede">
    Scan a folder of cloned repos, see what's new vs already imported, then register everything in
    one shot. Designed for the code-corpus workflow.
  </p>
</div>

<div class="hover-tile" style="max-width:820px;margin-bottom:18px">
  <div class="field-row">
    <label for="ri-path">Folder</label>
    <div class="inputs">
      <input
        id="ri-path"
        type="text"
        bind:value={path}
        placeholder="C:\Users\you\Documents\repo-intake"
        onkeydown={(e) => e.key === "Enter" && doScan()}
      />
      <span class="help">Parent directory containing one or more cloned repos.</span>
    </div>
  </div>
  <div class="field-row">
    <label></label>
    <div class="inputs">
      <label class="checkbox-row">
        <input type="checkbox" bind:checked={recursive} /> Recurse into subdirectories
      </label>
    </div>
  </div>
  <div class="row-gap">
    <button class="btn btn-primary" type="button" onclick={doScan} disabled={scanning}>
      {scanning ? "Scanning…" : "Scan"}
    </button>
    {#if didScan}
      <button
        class="btn btn-ghost"
        type="button"
        onclick={() => {
          scan = [];
          results = [];
          didScan = false;
        }}>Clear</button
      >
    {/if}
  </div>
</div>

{#if error}<div class="error-box" style="margin-bottom:14px">{error}</div>{/if}

{#if didScan}
  {#if total === 0}
    <Empty
      icon="⌘"
      title="No git repos found"
      sub="Check the path or toggle “Recurse into subdirectories”."
    />
  {:else}
    <div class="statgrid" style="margin-bottom:14px">
      <StatCard label="Repos found" value={total} />
      <StatCard label="New" value={fresh} />
      <StatCard label="Already imported" value={dupes} />
    </div>

    <div class="tablecard" style="margin-bottom:18px">
      <table>
        <thead>
          <tr><th>repo</th><th>remote</th><th>head</th><th>status</th><th>path</th></tr>
        </thead>
        <tbody>
          {#each scan as r (r.path)}
            <tr>
              <td class="mono">{r.repo?.key ?? r.repo?.name ?? "?"}</td>
              <td class="mono dim">{r.repo?.remote_url ?? "—"}</td>
              <td class="mono dim">{(r.repo?.head_sha ?? "").slice(0, 9) || "—"}</td>
              <td>
                {#if r.duplicate}
                  <span class="pill pill-muted">duplicate · #{r.existing_export_id ?? "?"}</span>
                {:else}
                  <span class="pill pill-ok">new</span>
                {/if}
              </td>
              <td class="mono dim" title={r.path}>{shortenPath(r.path)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>

    {#if fresh > 0}
      <div class="hover-tile" style="max-width:820px">
        <div class="section-label" style="margin-top:0">
          Register {fresh} new repo{fresh === 1 ? "" : "s"}
        </div>
        <div class="field-row">
          <label for="ri-acct">Account</label>
          <div class="inputs">
            <input id="ri-acct" type="text" bind:value={account} />
            <span class="help">All intake repos share this account label.</span>
          </div>
        </div>
        <div class="field-row">
          <label for="ri-notes">Notes</label>
          <div class="inputs">
            <input
              id="ri-notes"
              type="text"
              bind:value={notes}
              placeholder="batch label, e.g. 2026-04 OSS pull"
            />
          </div>
        </div>
        <div class="field-row">
          <label></label>
          <div class="inputs">
            <label class="checkbox-row">
              <input type="checkbox" bind:checked={ingestImmediately} /> Ingest each repo immediately
            </label>
          </div>
        </div>
        <button class="btn btn-primary" type="button" onclick={doRegister} disabled={registering}>
          {registering ? "Registering…" : `Register ${fresh} new repo${fresh === 1 ? "" : "s"}`}
        </button>
      </div>
    {/if}
  {/if}
{/if}

{#if results.length}
  <div class="section-label">Results</div>
  <div class="tablecard">
    <table>
      <thead><tr><th>repo</th><th>status</th><th>export</th><th>run</th><th>note</th></tr></thead>
      <tbody>
        {#each results as r}
          <tr>
            <td class="mono">{r.repo?.key ?? r.repo?.name ?? "?"}</td>
            <td><span class="pill {pillClassForStatus(r.status)}">{r.status}</span></td>
            <td class="mono dim">{r.export_id ?? "—"}</td>
            <td class="mono dim">{r.run_id ?? "—"}</td>
            <td class="mono dim" style="max-width:340px;overflow:hidden;text-overflow:ellipsis"
              >{r.error ?? ""}</td
            >
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
{/if}
