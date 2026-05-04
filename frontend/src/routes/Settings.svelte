<script lang="ts">
  import { api } from "$lib/api";
  import { session } from "$lib/session.svelte";
  import { modal } from "$lib/modal.svelte";
  import { toasts } from "$lib/toasts.svelte";

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
</script>

<div class="page-head">
  <div class="eyebrow">Configuration</div>
  <h1>Settings</h1>
  <p class="lede">Vault profiles, schema, and connections. Each vault has its own database and filesystem root.</p>
</div>

<div class="section-label">Active vault</div>
{#if session.activeVault}
  <div class="hover-tile" style="max-width:720px;margin-bottom:24px">
    <dl class="deflist">
      <dt>name</dt><dd class="plain">{session.activeVault.name}</dd>
      <dt>db</dt><dd>{session.activeVault.db_url}</dd>
      <dt>vault dir</dt><dd>{session.activeVault.vault_dir}</dd>
    </dl>
  </div>
{/if}

<div class="section-label">All vaults</div>
{#if session.vaults}
  <div class="tablecard" style="margin-bottom:24px">
    <table>
      <thead><tr><th>name</th><th>db</th><th>filesystem</th><th>status</th><th class="right">actions</th></tr></thead>
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
                <button class="btn btn-sm" type="button" onclick={() => activate(v.name)}>Switch</button>
              {/if}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
{/if}

<button class="btn btn-primary" type="button" onclick={() => modal.open("create-vault")}>+ New vault</button>

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
