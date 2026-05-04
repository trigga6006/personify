<script lang="ts">
  import { api, ApiError } from "$lib/api";
  import { modal } from "$lib/modal.svelte";
  import { session } from "$lib/session.svelte";
  import { toasts } from "$lib/toasts.svelte";

  let name = $state("");
  let activate = $state(true);
  let busy = $state(false);
  let errorMsg = $state<string | null>(null);

  const slug = $derived(name.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/(^-+|-+$)/g, ""));
  const dbName = $derived(slug ? (slug === "personal" ? "personify" : `personify_${slug.replaceAll("-", "_")}`) : "—");
  const valid = $derived(!!slug);

  async function submit() {
    if (!valid || busy) return;
    busy = true; errorMsg = null;
    try {
      await api.createVault({ name: slug, activate });
      toasts.ok(`Vault ${slug} created${activate ? " and activated" : ""}`);
      await session.refresh();
      modal.close();
    } catch (e) {
      errorMsg = e instanceof ApiError ? e.message : String(e);
    } finally {
      busy = false;
    }
  }
</script>

<div class="modal-shell" role="dialog" aria-labelledby="create-vault-title">
  <div class="head">
    <h2 id="create-vault-title">New vault</h2>
    <button class="icon-btn" type="button" aria-label="Close" onclick={() => modal.close()}>✕</button>
  </div>

  <div class="body">
    <div class="field-row">
      <label for="cv-name">Name</label>
      <div class="inputs">
        <input id="cv-name" type="text" bind:value={name}
               placeholder="e.g. work, code-corpus, screenshots" autocomplete="off" autofocus />
        <span class="help">
          Stored as <span class="mono">{slug || "—"}</span> · DB <span class="mono">{dbName}</span>
        </span>
      </div>
    </div>

    <div class="field-row">
      <label></label>
      <div class="inputs">
        <label class="checkbox-row">
          <input type="checkbox" bind:checked={activate} /> Switch to it after creating
        </label>
      </div>
    </div>

    {#if errorMsg}<div class="error-box">{errorMsg}</div>{/if}
  </div>

  <div class="foot">
    <button class="btn btn-ghost" type="button" onclick={() => modal.close()} disabled={busy}>Cancel</button>
    <button class="btn btn-primary" type="button" onclick={submit} disabled={!valid || busy}>
      {busy ? "Creating…" : "Create vault"}
    </button>
  </div>
</div>

<style>
  .icon-btn { background: transparent; border: 0; color: var(--text-3); padding: 6px 8px; border-radius: 6px; font-size: 14px; }
  .icon-btn:hover { background: var(--bg-soft); color: var(--text); }
</style>
