<script lang="ts">
  import { onMount } from "svelte";
  import { detail } from "$lib/detail.svelte";
  import ItemDetail from "./ItemDetail.svelte";
  import EntityDetail from "./EntityDetail.svelte";

  // Close on Escape so the panel feels snappy.
  onMount(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && detail.open) detail.close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
</script>

{#if detail.open}
  <div class="detail-overlay" onclick={() => detail.close()} role="presentation"></div>
  <aside class="detail-panel" role="dialog" aria-label="Item details">
    {#if detail.kind === "item" && detail.id != null}
      <ItemDetail itemId={detail.id} />
    {:else if detail.kind === "entity" && detail.id != null}
      <EntityDetail entityId={detail.id} />
    {/if}
  </aside>
{/if}
