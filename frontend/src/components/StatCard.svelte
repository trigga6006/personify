<script lang="ts">
  let {
    label,
    value,
    delta = null as string | null,
    loading = false,
  }: { label: string; value: string | number; delta?: string | null; loading?: boolean } = $props();
</script>

<div class="statcard">
  <div class="label">{label}</div>
  {#if loading}
    <span class="skel" style="height:28px;width:64px"></span>
  {:else}
    <div class="value">{typeof value === "number" ? value.toLocaleString() : value}</div>
  {/if}
  {#if delta}<div class="delta">{delta}</div>{/if}
</div>
