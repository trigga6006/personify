<script lang="ts">
  import { router } from "$lib/router.svelte";

  const ROUTE_LABELS: Record<string, string> = {
    dashboard: "Dashboard",
    exports: "Exports",
    browse: "Browse",
    search: "Search",
    graph: "Graph",
    repos: "Repo intake",
    embed: "Embeddings",
    settings: "Settings",
  };

  const crumb = $derived(ROUTE_LABELS[router.route] ?? router.route);

  function focusSearch() { router.go("search"); }
</script>

<header class="topbar">
  <div class="topbar-crumb">
    <span class="root">personify</span>
    <span class="sep">/</span>
    <span class="leaf">{crumb}</span>
  </div>

  <div class="topbar-tools">
    <button class="topbar-search-trigger" type="button" onclick={focusSearch} aria-label="Search">
      <span aria-hidden="true">⌕</span>
      <span>Search the vault</span>
      <span class="kbd">⌘K</span>
    </button>
  </div>
</header>
