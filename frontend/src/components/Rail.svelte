<script lang="ts">
  import { router } from "$lib/router.svelte";
  import { session } from "$lib/session.svelte";
  import { modal } from "$lib/modal.svelte";
  import { icons } from "$lib/icons";
  import VaultSwitcher from "./VaultSwitcher.svelte";

  // Sources for the bottom of the rail. Anything actually-ingested with a
  // count > 0 floats up; the registry-only ones are hidden — agents probe
  // the registry, humans want to see what they actually have.
  const sourcesWithItems = $derived.by(() => {
    const counts = session.itemsPerSource;
    return session.sources
      .map((s) => ({ slug: s.slug, label: s.label, count: counts[s.slug] ?? 0 }))
      .filter((s) => s.count > 0)
      .sort((a, b) => b.count - a.count);
  });

  const navGroups = [
    {
      label: null as string | null,
      items: [
        { route: "search",  label: "Search",     icon: icons.search },
        { route: "browse",  label: "Browse",     icon: icons.browse },
        { route: "graph",   label: "Graph",      icon: icons.graph  },
      ],
    },
    {
      label: "Vault",
      items: [
        { route: "dashboard", label: "Dashboard",  icon: icons.dashboard },
        { route: "exports",   label: "Exports",    icon: icons.exports   },
        { route: "repos",     label: "Repo intake",icon: icons.repos     },
        { route: "embed",     label: "Embeddings", icon: icons.embed     },
        { route: "settings",  label: "Settings",   icon: icons.settings  },
      ],
    },
  ];

  function isActive(name: string) { return router.route === name; }
</script>

<aside class="rail">
  <VaultSwitcher />

  <button class="rail-primary" type="button" onclick={() => modal.open("add-export")}>
    <span class="rail-primary-icon" aria-hidden="true">{icons.add}</span>
    <span class="rail-primary-label">Add export</span>
    <span class="kbd">N</span>
  </button>

  {#each navGroups as group}
    {#if group.label}
      <section class="rail-section">
        <div class="rail-section-label">{group.label}</div>
        {#each group.items as item}
          <button class="rail-link" class:active={isActive(item.route)} onclick={() => router.go(item.route)} type="button">
            <span class="rail-icon">{item.icon}</span><span>{item.label}</span>
          </button>
        {/each}
      </section>
    {:else}
      <nav class="rail-nav">
        {#each group.items as item}
          <button class="rail-link" class:active={isActive(item.route)} onclick={() => router.go(item.route)} type="button">
            <span class="rail-icon">{item.icon}</span><span>{item.label}</span>
          </button>
        {/each}
      </nav>
    {/if}
  {/each}

  {#if sourcesWithItems.length}
    <section class="rail-section">
      <div class="rail-section-label">Sources</div>
      {#each sourcesWithItems as src (src.slug)}
        <button
          class="source-chip"
          type="button"
          onclick={() => router.go("browse", { source: src.slug })}
        >
          <span class="row-gap">
            <span class="marker"></span>
            <span>{src.label}</span>
          </span>
          <span class="count">{src.count.toLocaleString()}</span>
        </button>
      {/each}
    </section>
  {/if}
</aside>
