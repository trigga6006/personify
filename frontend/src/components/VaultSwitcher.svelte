<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "$lib/api";
  import { session } from "$lib/session.svelte";
  import { toasts } from "$lib/toasts.svelte";
  import { modal } from "$lib/modal.svelte";

  let open = $state(false);
  let trigger: HTMLElement | undefined;

  function close() { open = false; }
  function toggle() { open = !open; }

  onMount(() => {
    const onDocClick = (e: MouseEvent) => {
      if (!open) return;
      if (trigger && trigger.contains(e.target as Node)) return;
      close();
    };
    document.addEventListener("click", onDocClick);
    return () => document.removeEventListener("click", onDocClick);
  });

  async function activate(name: string) {
    if (name === session.activeVault?.name) { close(); return; }
    try {
      await api.activateVault(name);
      toasts.ok(`Switched to ${name}`);
      await session.refresh();
      close();
    } catch (e) {
      toasts.err(e instanceof Error ? e.message : String(e));
    }
  }

  function newVault() { close(); modal.open("create-vault"); }
</script>

<div class="vault-switch" bind:this={trigger}>
  <button class="vault-trigger" type="button" aria-haspopup="true" aria-expanded={open} onclick={toggle}>
    <span class="vault-mark" aria-hidden="true">
      <img src="/static/personify-mark.svg?v=5" alt="" />
    </span>
    <span class="vault-text">
      <span class="brand">Personify</span>
      <span class="name">{session.activeVault?.name ?? "personal"}</span>
    </span>
    <span class="vault-chev" aria-hidden="true">⌄</span>
  </button>

  {#if open}
    <div class="vault-menu-v2" role="menu">
      {#if session.vaults}
        {#each session.vaults.vaults as v (v.name)}
          <button type="button" onclick={() => activate(v.name)} role="menuitem">
            <span class="name">{v.name}</span>
            <span class="meta">{v.active ? "active" : (v.exists ? "—" : "missing")}</span>
          </button>
        {/each}
      {:else}
        <button type="button" disabled><span class="name muted">loading…</span></button>
      {/if}
      <div class="sep"></div>
      <button type="button" class="new" onclick={newVault} role="menuitem">
        <span class="name">＋ New vault…</span>
      </button>
    </div>
  {/if}
</div>
