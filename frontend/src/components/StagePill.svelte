<script lang="ts">
  import { pillClassForStatus } from "$lib/format";
  import type { PipelineStage, StageStatus } from "$lib/types";

  let {
    stage,
  }: { stage: PipelineStage | { status: StageStatus; error: string | null } | undefined } =
    $props();
</script>

{#if !stage}
  <span class="pill pill-muted">—</span>
{:else}
  <span class="pill {pillClassForStatus(stage.status)}" title={stage.error ?? ""}
    >{stage.status}</span
  >
{/if}
