<script lang="ts">
  import { onMount } from "svelte";
  import { modal } from "$lib/modal.svelte";
  import AddExportModal from "./AddExportModal.svelte";
  import CreateVaultModal from "./CreateVaultModal.svelte";

  onMount(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && modal.current) modal.close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
</script>

{#if modal.current}
  <div class="modal-host-v2">
    <div class="modal-back" onclick={() => modal.close()} role="presentation"></div>
    {#if modal.current === "add-export"}
      <AddExportModal />
    {:else if modal.current === "create-vault"}
      <CreateVaultModal />
    {/if}
  </div>
{/if}
