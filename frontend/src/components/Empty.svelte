<script lang="ts">
  let {
    icon = "▢",
    title = "Nothing here yet",
    sub = "",
    cta = null as null | { label: string; onclick: () => void },
  } = $props();
</script>

<div class="empty-state">
  <div class="icon">{icon}</div>
  <div class="title">{title}</div>
  {#if sub}<div class="sub">{sub}</div>{/if}
  {#if cta}
    <button class="btn btn-primary" type="button" onclick={cta.onclick}>{cta.label}</button>
  {/if}
</div>
