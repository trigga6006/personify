<script lang="ts">
  import { onMount } from "svelte";
  import { router } from "$lib/router.svelte";
  import { session } from "$lib/session.svelte";
  import { modal } from "$lib/modal.svelte";
  import Rail from "$components/Rail.svelte";
  import TopBar from "$components/TopBar.svelte";
  import Toasts from "$components/Toasts.svelte";
  import DetailPanel from "$components/DetailPanel.svelte";
  import ModalHost from "$components/ModalHost.svelte";
  import Dashboard from "$routes/Dashboard.svelte";
  import Exports from "$routes/Exports.svelte";
  import Browse from "$routes/Browse.svelte";
  import Search from "$routes/Search.svelte";
  import Graph from "$routes/Graph.svelte";
  import Repos from "$routes/Repos.svelte";
  import Embeddings from "$routes/Embeddings.svelte";
  import Settings from "$routes/Settings.svelte";

  onMount(() => {
    void session.refresh();

    // Keyboard shortcuts: ⌘K → search, N → add export. Ignored when typing
    // in inputs so they don't fight with normal text entry.
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase();
      const inField = tag === "input" || tag === "textarea" || tag === "select";
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        router.go("search");
        return;
      }
      if (!inField && e.key.toLowerCase() === "n" && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        modal.open("add-export");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
</script>

<div class="shell">
  <Rail />

  <main class="shell-main">
    <TopBar />
    <section class="shell-content">
      {#if router.route === "dashboard"}<Dashboard />
      {:else if router.route === "exports"}<Exports />
      {:else if router.route === "browse" || router.route === "items" || router.route === "timeline"}<Browse
        />
      {:else if router.route === "search"}<Search />
      {:else if router.route === "graph"}<Graph />
      {:else if router.route === "repos"}<Repos />
      {:else if router.route === "embed"}<Embeddings />
      {:else if router.route === "settings"}<Settings />
      {:else}<Dashboard />{/if}
    </section>
  </main>
</div>

<DetailPanel />
<ModalHost />
<Toasts />
