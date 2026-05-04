<script lang="ts">
  import { toasts } from "$lib/toasts.svelte";
</script>

<div class="toast-stack" aria-live="polite">
  {#each toasts.items as t (t.id)}
    <div class="toast-line {t.variant}">
      <span class="badge">{t.variant === "ok" ? "ok" : t.variant === "err" ? "err" : "info"}</span>
      <span>{t.text}</span>
    </div>
  {/each}
</div>
