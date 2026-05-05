<script lang="ts">
  import { router } from "$lib/router.svelte";
  import { session } from "$lib/session.svelte";
  import { theme } from "$lib/theme.svelte";
  import { mcp } from "$lib/mcp.svelte";
  import { icons } from "$lib/icons";
  import { fmtDuration } from "$lib/format";

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
  const vaultName = $derived(session.activeVault?.name ?? "personal");

  // Subscribe to MCP polling for the lifetime of this component (mounted
  // for the entire app session, so polling runs while the app is open).
  $effect(() => mcp.subscribe());

  const mcpTitle = $derived.by(() => {
    const s = mcp.status;
    if (!s) return "MCP HTTP — checking…";
    if (!s.enabled) return "MCP HTTP server is stopped — click to manage";
    const up = fmtDuration(s.uptime_seconds);
    return `MCP HTTP running · up ${up} · ${s.request_count} request${s.request_count === 1 ? "" : "s"}`;
  });

  function focusSearch() {
    router.go("search");
  }

  function openMcpSettings() {
    router.go("settings");
  }
</script>

<header class="topbar">
  <div class="topbar-crumb">
    <span class="root">personify</span>
    <span class="sep">/</span>
    <span class="root">{vaultName}</span>
    <span class="sep">/</span>
    <span class="leaf">{crumb}</span>
  </div>

  <div class="topbar-tools">
    <button
      class="mcp-dot"
      class:on={mcp.enabled}
      class:off={!mcp.enabled}
      type="button"
      onclick={openMcpSettings}
      aria-label={mcpTitle}
      title={mcpTitle}
    >
      <span class="dot" aria-hidden="true"></span>
      <span class="label">MCP</span>
    </button>

    <button
      class="topbar-theme-toggle"
      type="button"
      onclick={() => theme.toggle()}
      aria-label={theme.current === "dark" ? "Switch to light theme" : "Switch to dark theme"}
      title={theme.current === "dark" ? "Switch to light" : "Switch to dark"}
    >
      <span aria-hidden="true">{theme.current === "dark" ? icons.sun : icons.moon}</span>
    </button>

    <button class="topbar-search-trigger" type="button" onclick={focusSearch} aria-label="Search">
      <span aria-hidden="true">⌕</span>
      <span>Search the vault</span>
      <span class="kbd">⌘K</span>
    </button>
  </div>
</header>

<style>
  .mcp-dot {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 5px 9px;
    border: 1px solid var(--line);
    background: var(--bg-card);
    color: var(--text-2);
    border-radius: 999px;
    font-family: var(--mono);
    font-size: 11px;
    letter-spacing: 0.04em;
    cursor: pointer;
    transition:
      background var(--t-fast),
      border-color var(--t-fast),
      color var(--t-fast);
  }
  .mcp-dot:hover {
    border-color: var(--line-strong);
    color: var(--text);
  }
  .mcp-dot .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--err);
    box-shadow: 0 0 0 2px var(--err-soft);
  }
  .mcp-dot.on .dot {
    background: var(--ok);
    box-shadow: 0 0 0 2px var(--ok-soft);
    animation: mcp-pulse 1.8s ease-in-out infinite;
  }
  .mcp-dot .label {
    font-weight: 600;
  }
  @keyframes mcp-pulse {
    0%,
    100% {
      box-shadow: 0 0 0 2px var(--ok-soft);
    }
    50% {
      box-shadow: 0 0 0 4px var(--ok-soft);
    }
  }
</style>
