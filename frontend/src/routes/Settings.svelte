<script lang="ts">
  import { api } from "$lib/api";
  import { session } from "$lib/session.svelte";
  import { modal } from "$lib/modal.svelte";
  import { toasts } from "$lib/toasts.svelte";
  import { mcp } from "$lib/mcp.svelte";
  import { fmtDuration, fmtNum, fmtRel } from "$lib/format";

  async function activate(name: string) {
    if (name === session.activeVault?.name) return;
    try {
      await api.activateVault(name);
      toasts.ok(`Switched to ${name}`);
      await session.refresh();
    } catch (e) {
      toasts.err(e instanceof Error ? e.message : String(e));
    }
  }

  // Subscribe so this page actively polls while open. The TopBar already
  // subscribes for the dot, but the panel benefits from reading right
  // after a start/stop click without a polling-interval delay.
  $effect(() => mcp.subscribe());

  async function toggleMcp() {
    try {
      if (mcp.enabled) {
        await mcp.stop();
        toasts.ok("MCP HTTP stopped");
      } else {
        await mcp.start();
        toasts.ok("MCP HTTP started");
      }
    } catch (e) {
      toasts.err(e instanceof Error ? e.message : String(e));
    }
  }

  // Endpoint URL is whatever the page was loaded on + the configured
  // mount path; UI shows it so the user can copy/paste into an MCP
  // client without guessing.
  const fullEndpoint = $derived.by(() => {
    const path = mcp.status?.endpoint ?? "/mcp";
    if (typeof window === "undefined") return path;
    return `${window.location.protocol}//${window.location.host}${path}`;
  });
</script>

<div class="page-head">
  <div class="eyebrow">Configuration</div>
  <h1>Settings</h1>
  <p class="lede">
    Vault profiles, schema, and connections. Each vault has its own database and filesystem root.
  </p>
</div>

<div class="section-label">Active vault</div>
{#if session.activeVault}
  <div class="hover-tile" style="max-width:720px;margin-bottom:24px">
    <dl class="deflist">
      <dt>name</dt>
      <dd class="plain">{session.activeVault.name}</dd>
      <dt>db</dt>
      <dd>{session.activeVault.db_url}</dd>
      <dt>vault dir</dt>
      <dd>{session.activeVault.vault_dir}</dd>
    </dl>
  </div>
{/if}

<div class="section-label">All vaults</div>
{#if session.vaults}
  <div class="tablecard" style="margin-bottom:24px">
    <table>
      <thead
        ><tr
          ><th>name</th><th>db</th><th>filesystem</th><th>status</th><th class="right">actions</th
          ></tr
        ></thead
      >
      <tbody>
        {#each session.vaults.vaults as v (v.name)}
          <tr>
            <td><strong>{v.name}</strong></td>
            <td class="mono dim">{v.db_url}</td>
            <td class="mono dim">{v.vault_dir}</td>
            <td>
              {#if v.active}
                <span class="pill pill-ok">active</span>
              {:else if v.exists}
                <span class="pill pill-muted">available</span>
              {:else}
                <span class="pill pill-warn">not initialized</span>
              {/if}
            </td>
            <td class="actions">
              {#if !v.active}
                <button class="btn btn-sm" type="button" onclick={() => activate(v.name)}
                  >Switch</button
                >
              {/if}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
{/if}

<button class="btn btn-primary" type="button" onclick={() => modal.open("create-vault")}
  >+ New vault</button
>

<div class="section-label">MCP server</div>
<div class="hover-tile mcp-card" style="max-width:720px;margin-bottom:24px">
  <div class="mcp-head">
    <div>
      <div class="mcp-title">
        <span class="dot" class:on={mcp.enabled} aria-hidden="true"></span>
        <strong>HTTP transport</strong>
        <span class="pill" class:pill-ok={mcp.enabled} class:pill-muted={!mcp.enabled}>
          {mcp.enabled ? "running" : "stopped"}
        </span>
      </div>
      <p class="mcp-sub">
        In-process MCP server mounted on this app. Lets browser-based agents and HTTP-capable MCP
        clients hit the same tools the stdio entry point serves to Claude Desktop.
      </p>
    </div>
    <button
      class="btn"
      class:btn-primary={!mcp.enabled}
      type="button"
      disabled={mcp.pending}
      onclick={toggleMcp}
    >
      {mcp.pending ? "…" : mcp.enabled ? "Stop" : "Start"}
    </button>
  </div>

  <dl class="deflist mcp-stats">
    <dt>endpoint</dt>
    <dd class="mono">{fullEndpoint}</dd>
    <dt>uptime</dt>
    <dd>{fmtDuration(mcp.status?.uptime_seconds ?? null)}</dd>
    <dt>requests</dt>
    <dd>{fmtNum(mcp.status?.request_count ?? 0)}</dd>
    <dt>sessions</dt>
    <dd>{fmtNum(mcp.status?.session_count ?? 0)}</dd>
    <dt>last request</dt>
    <dd>{fmtRel(mcp.status?.last_request_at ?? null)}</dd>
    {#if mcp.status?.last_error}
      <dt>last error</dt>
      <dd class="mono dim">{mcp.status.last_error}</dd>
    {/if}
  </dl>

  <p class="mcp-foot">
    Claude Desktop uses the <span class="mono">vault mcp</span> stdio entry point and is unaffected
    by this toggle. Stats above only count requests on
    <span class="mono">{mcp.status?.endpoint ?? "/mcp"}</span>.
  </p>
</div>

<div class="section-label">About</div>
<div class="hover-tile" style="max-width:720px;font-size:13px;color:var(--text-2);line-height:1.6">
  <p style="margin:0 0 8px">
    Personify is a local-first personal data vault. Raw exports are immutable; every byte is hashed.
    Items have stable dedup keys. The graph and embedding stages are opt-in and re-runnable.
  </p>
  <p style="margin:0">
    Agents can query this vault directly via the MCP server (<span class="mono">vault mcp</span>).
    See <span class="mono">docs/MCP_GUIDE.md</span> for setup.
  </p>
</div>

<style>
  .mcp-card {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  .mcp-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
  }
  .mcp-title {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 14px;
  }
  .mcp-title .dot {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: var(--err);
    box-shadow: 0 0 0 3px var(--err-soft);
  }
  .mcp-title .dot.on {
    background: var(--ok);
    box-shadow: 0 0 0 3px var(--ok-soft);
  }
  .mcp-sub {
    margin: 6px 0 0;
    color: var(--text-2);
    font-size: 12px;
    line-height: 1.55;
    max-width: 56ch;
  }
  .mcp-stats {
    margin: 0;
  }
  .mcp-foot {
    margin: 0;
    font-size: 11px;
    color: var(--text-3);
    line-height: 1.5;
  }
</style>
