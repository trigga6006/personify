<script lang="ts">
  import { pillClassForStatus } from "$lib/format";
  let { status, label = null as string | null }: { status: string | null | undefined; label?: string | null } = $props();
</script>

<span class="pill {pillClassForStatus(status)}">{label ?? status ?? "—"}</span>
