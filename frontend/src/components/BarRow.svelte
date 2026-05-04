<script lang="ts">
  let {
    name,
    count,
    max,
    onclick = null as null | (() => void),
  }: { name: string; count: number; max: number; onclick?: null | (() => void) } = $props();
  const pct = $derived(max > 0 ? (count / max) * 100 : 0);
</script>

<div class="barrow" role={onclick ? "button" : undefined} tabindex={onclick ? 0 : undefined} onclick={onclick ?? undefined}>
  <div class="barname">{name}</div>
  <div class="bartrack"><div class="barfill" style="width:{pct.toFixed(1)}%"></div></div>
  <div class="barcount">{count.toLocaleString()}</div>
</div>
