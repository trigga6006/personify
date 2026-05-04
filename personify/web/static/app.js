"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = {
  route: "dashboard",
  parsers: [],
  accounts: [],
  sources: [],
  itemCounts: {},
  preset: null,
  vaults: [],
  activeVault: null,
};

const ROUTE_LABELS = {
  dashboard: "Dashboard",
  search: "Search",
  timeline: "Timeline",
  items: "Items",
  exports: "Exports",
  add: "Add export",
  repos: "Repo intake",
  embed: "Embeddings",
};

// ---------- API helpers ----------
async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "content-type": "application/json", accept: "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const data = await res.json();
      if (data && data.detail) msg = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch (_) {}
    throw new Error(msg);
  }
  if (res.status === 204) return null;
  return res.json();
}

// ---------- formatting ----------
function fmtBytes(n) {
  if (n == null) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = Number(n);
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
}

function fmtTs(s) {
  if (!s) return "—";
  try {
    const d = new Date(s);
    return d.toLocaleString(undefined, {
      year: "2-digit",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch (_) {
    return s;
  }
}

function fmtRel(s) {
  if (!s) return "—";
  const d = new Date(s);
  const sec = Math.round((Date.now() - d.getTime()) / 1000);
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)}h ago`;
  return `${Math.round(sec / 86400)}d ago`;
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function highlight(text, query) {
  if (!text || !query) return escapeHtml(text || "");
  const safe = escapeHtml(text);
  const terms = query.split(/\s+/).filter((t) => t.length >= 2);
  if (!terms.length) return safe;
  const re = new RegExp(`(${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "ig");
  return safe.replace(re, "<mark>$1</mark>");
}

// ---------- toasts ----------
function toast(msg, kind = "ok") {
  const host = $("#toast-host");
  const el = document.createElement("div");
  el.className = `toast toast-${kind}`;
  el.textContent = msg;
  host.appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transition = "opacity 0.2s";
    setTimeout(() => el.remove(), 220);
  }, 3200);
}

// ---------- routing ----------
const routes = {
  dashboard: renderDashboard,
  search: renderSearch,
  timeline: renderTimeline,
  items: renderItems,
  exports: renderExports,
  add: renderAddExport,
  repos: renderRepoIntake,
  embed: renderEmbed,
};

function navigate(route, preset = null) {
  if (!routes[route]) route = "dashboard";
  state.route = route;
  state.preset = preset;
  const presetSource = (preset && preset.source) || null;
  $$(".rail-link").forEach((b) => {
    const buttonSource = b.dataset.source || null;
    const active =
      b.dataset.route === route && buttonSource === presetSource;
    b.classList.toggle("active", active);
  });
  const crumb = $("#crumb");
  if (crumb) {
    crumb.textContent = preset && preset.source
      ? `${ROUTE_LABELS[route]} · ${preset.source}`
      : ROUTE_LABELS[route] || route;
  }
  if (location.hash !== `#/${route}`) {
    history.replaceState(null, "", `#/${route}`);
  }
  routes[route]();
}

window.addEventListener("hashchange", () => {
  const r = location.hash.replace(/^#\//, "") || "dashboard";
  navigate(r);
});

document.addEventListener("DOMContentLoaded", async () => {
  $$(".rail-link").forEach((b) =>
    b.addEventListener("click", () => navigate(b.dataset.route)),
  );
  $("#rail-new").addEventListener("click", () => goToSearch());
  $("#detail-close").addEventListener("click", closeDetail);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeDetail();
      closeVaultMenu();
      closeModal();
    }
    // ⌘K / Ctrl+K → focus search
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      goToSearch();
    }
  });
  setupVaultSwitcher();
  await refreshHealth();
  await refreshVaults();
  await refreshLookups();
  renderRailSources();
  const initial = location.hash.replace(/^#\//, "") || "dashboard";
  navigate(initial);
  setInterval(refreshHealth, 30000);
});

// ---------- Vault switcher ----------
function setupVaultSwitcher() {
  const trigger = $("#rail-vault");
  trigger.addEventListener("click", (e) => {
    e.stopPropagation();
    const menu = $("#vault-menu");
    const open = !menu.hidden;
    if (open) closeVaultMenu();
    else openVaultMenu();
  });
  document.addEventListener("click", (e) => {
    const menu = $("#vault-menu");
    if (menu.hidden) return;
    if (e.target.closest("#vault-menu") || e.target.closest("#rail-vault")) return;
    closeVaultMenu();
  });
  $("#vault-new").addEventListener("click", () => {
    closeVaultMenu();
    openCreateVaultModal();
  });
  $("#modal-close").addEventListener("click", closeModal);
  $("#vault-cancel").addEventListener("click", closeModal);
  $("#modal-backdrop").addEventListener("click", closeModal);
  $("#vault-create-go").addEventListener("click", submitCreateVault);
  $("#vault-name-input").addEventListener("input", updateVaultNamePreview);
  $("#vault-name-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") submitCreateVault();
  });
}

function openVaultMenu() {
  $("#vault-menu").hidden = false;
  $("#rail-vault").setAttribute("aria-expanded", "true");
}

function closeVaultMenu() {
  $("#vault-menu").hidden = true;
  $("#rail-vault").setAttribute("aria-expanded", "false");
}

async function refreshVaults() {
  try {
    const data = await api("/api/vaults");
    state.vaults = data.vaults || [];
    state.activeVault = data.active || null;
    renderVaultSwitcher();
  } catch (e) {
    /* best-effort — keep prior state */
  }
}

function renderVaultSwitcher() {
  const active = state.activeVault;
  if (active) {
    $("#rail-vault-name").textContent = active.name;
  }
  const list = $("#vault-menu-list");
  list.innerHTML = "";
  state.vaults.forEach((v) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "vault-menu-item" + (v.active ? " active" : "");
    const ready = v.dir_exists && v.db_exists;
    const marker = `<span class="vault-menu-item-marker${v.active ? "" : " dim"}"></span>`;
    const warn = !ready
      ? `<span class="vault-menu-item-warn" title="Database missing — recreate via 'New vault'">!</span>`
      : "";
    btn.innerHTML = `
      ${marker}
      <span class="vault-menu-item-name">${escapeHtml(v.name)}</span>
      <span class="vault-menu-item-meta">${escapeHtml(v.db_name)}</span>
      ${warn}
    `;
    if (!v.active) {
      btn.addEventListener("click", () => activateVault(v.name));
    }
    list.appendChild(btn);
  });
}

async function activateVault(name) {
  closeVaultMenu();
  toast(`Switching to ${name}…`);
  try {
    await api(`/api/vaults/${encodeURIComponent(name)}/activate`, {
      method: "POST",
    });
    // Hard reload — every cached state (stats, lookups, embedded counts, etc.)
    // belongs to the previous vault, so it's safest to start fresh.
    window.location.reload();
  } catch (e) {
    toast(e.message, "err");
  }
}

// ---------- Create-vault modal ----------
function openCreateVaultModal() {
  $("#modal-host").hidden = false;
  $("#vault-name-input").value = "";
  $("#vault-create-result").innerHTML = "";
  updateVaultNamePreview();
  setTimeout(() => $("#vault-name-input").focus(), 30);
}

function closeModal() {
  $("#modal-host").hidden = true;
}

function slugify(s) {
  return (s || "").toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "");
}

function updateVaultNamePreview() {
  const raw = $("#vault-name-input").value;
  const slug = slugify(raw);
  $("#vault-name-preview").textContent = slug || "—";
  const dbName = !slug
    ? "—"
    : slug === "personal"
    ? "personify"
    : `personify_${slug.replace(/-/g, "_")}`;
  $("#vault-db-preview").textContent = dbName;
}

async function submitCreateVault() {
  const name = $("#vault-name-input").value.trim();
  const slug = slugify(name);
  if (!slug) {
    $("#vault-create-result").innerHTML = `<div class="error-box">Name can't be empty.</div>`;
    return;
  }
  const activate = $("#vault-activate").checked;
  $("#vault-create-result").innerHTML = `<div class="row"><div class="spinner"></div><div class="muted">Creating database and initializing schema…</div></div>`;
  $("#vault-create-go").disabled = true;
  try {
    await api("/api/vaults", {
      method: "POST",
      body: JSON.stringify({ name: slug, activate }),
    });
    toast(`Vault "${slug}" created`);
    if (activate) {
      window.location.reload();
    } else {
      await refreshVaults();
      $("#vault-create-result").innerHTML = `<div class="pill pill-ok">created</div> Vault <span class="mono">${escapeHtml(slug)}</span> ready. Pick it from the switcher when you're ready.`;
      $("#vault-create-go").disabled = false;
    }
  } catch (e) {
    $("#vault-create-result").innerHTML = `<div class="error-box">${escapeHtml(e.message)}</div>`;
    $("#vault-create-go").disabled = false;
  }
}

function goToSearch() {
  if (state.route !== "search") navigate("search");
  setTimeout(() => {
    const q = $("#q");
    if (q) {
      q.focus();
      q.select();
    }
  }, 30);
}

function renderRailSources() {
  const host = $("#rail-sources");
  if (!host) return;
  // Keep the label child, replace everything after.
  const label = host.querySelector(".rail-section-label");
  host.innerHTML = "";
  if (label) host.appendChild(label);
  const counts = state.itemCounts || {};
  const sources = (state.sources || []).filter((s) => counts[s.slug] > 0);
  // If no item counts yet, show parsers list as a hint.
  const list = sources.length
    ? sources.map((s) => ({ slug: s.slug, label: s.label, count: counts[s.slug] }))
    : (state.parsers || []).map((p) => ({ slug: p.slug, label: p.label, count: 0 }));
  list.forEach(({ slug, label: lbl, count }) => {
    const btn = document.createElement("button");
    btn.className = "rail-link";
    btn.dataset.route = "items";
    btn.dataset.source = slug;
    btn.innerHTML = `<span class="rail-icon">·</span><span>${escapeHtml(lbl)}</span>${count ? `<span class="rail-source-count">${count.toLocaleString()}</span>` : ""}`;
    btn.addEventListener("click", () => navigate("items", { source: slug }));
    host.appendChild(btn);
  });
}

async function refreshHealth() {
  const pill = $("#health-pill");
  try {
    const h = await api("/health");
    pill.className = "pill pill-ok";
    pill.textContent = `online · v${h.version}`;
  } catch (e) {
    pill.className = "pill pill-err";
    pill.textContent = "API offline";
  }
}

async function refreshLookups() {
  try {
    const [parsers, accounts, sources, stats] = await Promise.all([
      api("/api/parsers"),
      api("/api/accounts"),
      api("/sources"),
      api("/stats"),
    ]);
    state.parsers = parsers;
    state.accounts = accounts;
    state.sources = sources;
    state.itemCounts = stats.items_per_source || {};
  } catch (e) {
    /* ignore — pages will surface their own errors */
  }
  // Sidebar Sources section is derived from state.sources + state.itemCounts;
  // re-render here so callers (post-ingest, post-reset, etc.) don't have to
  // remember to call renderRailSources() separately.
  renderRailSources();
}

// ---------- shared ----------
function setView(html) {
  $("#view").innerHTML = html;
}

function loadingView(label = "Loading") {
  setView(`<div class="row" style="padding:30px"><div class="spinner"></div><div class="muted">${label}…</div></div>`);
}

function errorView(err) {
  setView(`<div class="error-box">${escapeHtml(err.message || String(err))}</div>`);
}

function statusPill(run) {
  if (!run) return `<span class="pill pill-muted">not ingested</span>`;
  if (run.status === "ok") return `<span class="pill pill-ok">ok</span>`;
  if (run.status === "running") return `<span class="pill pill-info">running</span>`;
  if (run.status === "error") return `<span class="pill pill-err">error</span>`;
  return `<span class="pill">${escapeHtml(run.status)}</span>`;
}

// ---------- Dashboard ----------
async function renderDashboard() {
  loadingView("Loading dashboard");
  let stats, runs;
  try {
    [stats, runs] = await Promise.all([api("/stats"), api("/api/runs?limit=10")]);
  } catch (e) {
    return errorView(e);
  }

  const perSource = stats.items_per_source || {};
  const sourceEntries = Object.entries(perSource).sort((a, b) => b[1] - a[1]);
  const max = sourceEntries.length ? sourceEntries[0][1] : 0;

  setView(`
    <h1>Vault dashboard</h1>
    <div class="sub">Personal data ingested from your third-party services.</div>

    <div class="stat-grid">
      <div class="stat-card"><div class="label">Items</div><div class="value">${stats.items.toLocaleString()}</div></div>
      <div class="stat-card"><div class="label">Exports</div><div class="value">${stats.exports.toLocaleString()}</div></div>
      <div class="stat-card"><div class="label">Sources</div><div class="value">${(stats.sources || []).length}</div></div>
      <div class="stat-card"><div class="label">Accounts</div><div class="value">${(stats.accounts || []).length}</div></div>
      <div class="stat-card"><div class="label">Ingestion runs</div><div class="value">${stats.runs.toLocaleString()}</div></div>
    </div>

    <h2>Items per source</h2>
    ${
      sourceEntries.length
        ? `<div class="bars">
        ${sourceEntries
          .map(
            ([slug, n]) => `
          <div class="bar-row">
            <div class="name">${escapeHtml(slug)}</div>
            <div class="bar-track"><div class="bar-fill" style="width:${max ? (n / max) * 100 : 0}%"></div></div>
            <div class="count">${n.toLocaleString()}</div>
          </div>`,
          )
          .join("")}
      </div>`
        : `<div class="empty"><div class="ico">▤</div>No items yet — add an export to get started.</div>`
    }

    <h2>Recent ingestion runs</h2>
    ${
      runs.length
        ? `<div class="table-wrap"><table>
        <thead><tr>
          <th>id</th><th>export</th><th>parser</th><th>status</th>
          <th class="right">seen</th><th class="right">inserted</th><th class="right">skipped</th>
          <th>started</th>
        </tr></thead>
        <tbody>
          ${runs
            .map(
              (r) => `
            <tr>
              <td class="mono">${r.id}</td>
              <td class="mono">${r.raw_export_id}</td>
              <td>${escapeHtml(r.parser)} <span class="muted">v${escapeHtml(r.parser_version)}</span></td>
              <td>${statusPill(r)}</td>
              <td class="right mono">${r.items_seen}</td>
              <td class="right mono">${r.items_inserted}</td>
              <td class="right mono">${r.items_skipped}</td>
              <td class="mono" title="${escapeHtml(r.started_at || "")}">${fmtRel(r.started_at)}</td>
            </tr>`,
            )
            .join("")}
        </tbody>
      </table></div>`
        : `<div class="empty muted">No runs yet.</div>`
    }
  `);
}

// ---------- Search ----------
function renderSearch() {
  setView(`
    <h1>Search</h1>
    <div class="sub">Full-text search across every ingested item. Click a result to inspect.</div>

    <div class="search-bar">
      <input id="q" type="search" placeholder="Search your vault…" autofocus />
      <select id="src">
        <option value="">All sources</option>
        ${state.sources.map((s) => `<option value="${escapeHtml(s.slug)}">${escapeHtml(s.label)}</option>`).join("")}
      </select>
      <div class="toggle-group" id="mode">
        <button class="active" data-mode="text">Text</button>
        <button data-mode="semantic">Semantic</button>
      </div>
      <button class="btn btn-primary" id="go">Search</button>
    </div>

    <div id="results"></div>
  `);

  const q = $("#q");
  const src = $("#src");
  const go = $("#go");
  const modeGroup = $("#mode");

  modeGroup.addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    $$("#mode button").forEach((b) => b.classList.toggle("active", b === btn));
  });

  const doSearch = async () => {
    const query = q.value.trim();
    if (!query) {
      $("#results").innerHTML = "";
      return;
    }
    const mode = $("#mode .active").dataset.mode;
    const path = mode === "semantic" ? "/semantic-search" : "/search";
    $("#results").innerHTML = `<div class="row"><div class="spinner"></div><div class="muted">Searching…</div></div>`;
    try {
      const results = await api(path, {
        method: "POST",
        body: JSON.stringify({ query, limit: 50, source: src.value || null }),
      });
      if (!results.length) {
        $("#results").innerHTML = `<div class="empty muted">No results for “${escapeHtml(query)}”.</div>`;
        return;
      }
      $("#results").innerHTML = results
        .map(
          (r) => `
        <div class="result" data-id="${r.id}">
          <div class="result-head">
            <div class="result-title">${highlight(r.title || "(untitled)", query)}</div>
            <div class="result-meta">${escapeHtml(r.source)} · ${escapeHtml(r.account || "")} · ${fmtTs(r.ts)}</div>
          </div>
          <div class="result-snippet">${highlight(r.snippet || "", query)}</div>
        </div>`,
        )
        .join("");
      $$("#results .result").forEach((el) =>
        el.addEventListener("click", () => openItem(el.dataset.id, query)),
      );
    } catch (e) {
      $("#results").innerHTML = `<div class="error-box">${escapeHtml(e.message)}</div>`;
    }
  };

  go.addEventListener("click", doSearch);
  q.addEventListener("keydown", (e) => {
    if (e.key === "Enter") doSearch();
  });
}

// ---------- Timeline ----------
async function renderTimeline() {
  setView(`
    <h1>Timeline</h1>
    <div class="sub">Items in reverse chronological order. Filter by source and date range.</div>

    <div class="search-bar" style="grid-template-columns:160px 160px 200px auto;">
      <input id="start" type="text" placeholder="Start (YYYY-MM-DD)" />
      <input id="end" type="text" placeholder="End (YYYY-MM-DD)" />
      <select id="src">
        <option value="">All sources</option>
        ${state.sources.map((s) => `<option value="${escapeHtml(s.slug)}">${escapeHtml(s.label)}</option>`).join("")}
      </select>
      <button class="btn btn-primary" id="go">Apply</button>
    </div>
    <div id="timeline"></div>
  `);

  const load = async () => {
    const params = new URLSearchParams();
    if ($("#start").value) params.set("start", $("#start").value);
    if ($("#end").value) params.set("end", $("#end").value);
    if ($("#src").value) params.set("source", $("#src").value);
    params.set("limit", "300");
    $("#timeline").innerHTML = `<div class="row"><div class="spinner"></div><div class="muted">Loading…</div></div>`;
    try {
      const rows = await api(`/timeline?${params.toString()}`);
      if (!rows.length) {
        $("#timeline").innerHTML = `<div class="empty muted">No items in range.</div>`;
        return;
      }
      $("#timeline").innerHTML = `<div class="table-wrap"><table>
        <thead><tr><th>when</th><th>source</th><th>account</th><th>kind</th><th>title</th></tr></thead>
        <tbody>
          ${rows
            .map(
              (r) => `<tr data-id="${r.id}" style="cursor:pointer">
            <td class="mono">${fmtTs(r.ts)}</td>
            <td>${escapeHtml(r.source)}</td>
            <td class="mono">${escapeHtml(r.account || "")}</td>
            <td><span class="pill pill-flat pill-muted">${escapeHtml(r.kind)}</span></td>
            <td>${escapeHtml(r.title || "(untitled)")}</td>
          </tr>`,
            )
            .join("")}
        </tbody>
      </table></div>`;
      $$("#timeline tbody tr").forEach((tr) =>
        tr.addEventListener("click", () => openItem(tr.dataset.id)),
      );
    } catch (e) {
      $("#timeline").innerHTML = `<div class="error-box">${escapeHtml(e.message)}</div>`;
    }
  };

  $("#go").addEventListener("click", load);
  load();
}

// ---------- Items ----------
async function renderItems() {
  const presetSource = (state.preset && state.preset.source) || "";
  setView(`
    <h1>Items</h1>
    <div class="sub">Browse all ingested items, filtered by source or account.</div>

    <div class="search-bar" style="grid-template-columns:200px 200px 200px auto;">
      <select id="src">
        <option value="">All sources</option>
        ${state.sources.map((s) => `<option value="${escapeHtml(s.slug)}"${s.slug === presetSource ? " selected" : ""}>${escapeHtml(s.label)}</option>`).join("")}
      </select>
      <select id="acct">
        <option value="">All accounts</option>
        ${state.accounts.map((a) => `<option value="${escapeHtml(a.handle)}">${escapeHtml(a.handle)}</option>`).join("")}
      </select>
      <input id="kind" type="text" placeholder="Kind (e.g. message, email)" />
      <button class="btn btn-primary" id="go">Apply</button>
    </div>
    <div class="row-spread">
      <div class="muted" id="items-count">—</div>
      <div class="row">
        <button class="btn btn-sm" id="prev">← Prev</button>
        <button class="btn btn-sm" id="next">Next →</button>
      </div>
    </div>
    <div id="items-table"></div>
  `);

  let offset = 0;
  const limit = 50;

  const load = async () => {
    const params = new URLSearchParams({ limit, offset });
    if ($("#src").value) params.set("source", $("#src").value);
    if ($("#acct").value) params.set("account", $("#acct").value);
    if ($("#kind").value) params.set("kind", $("#kind").value);
    $("#items-table").innerHTML = `<div class="row"><div class="spinner"></div><div class="muted">Loading…</div></div>`;
    try {
      const data = await api(`/api/items?${params.toString()}`);
      $("#items-count").textContent = `${data.total.toLocaleString()} items · showing ${offset + 1}–${Math.min(offset + limit, data.total)}`;
      $("#prev").disabled = offset === 0;
      $("#next").disabled = offset + limit >= data.total;
      if (!data.items.length) {
        $("#items-table").innerHTML = `<div class="empty muted">No items match.</div>`;
        return;
      }
      $("#items-table").innerHTML = `<div class="table-wrap"><table>
        <thead><tr><th>id</th><th>when</th><th>source</th><th>account</th><th>kind</th><th>title</th></tr></thead>
        <tbody>
          ${data.items
            .map(
              (i) => `<tr data-id="${i.id}" style="cursor:pointer">
            <td class="mono">${i.id}</td>
            <td class="mono">${fmtTs(i.ts)}</td>
            <td>${escapeHtml(i.source)}</td>
            <td class="mono">${escapeHtml(i.account || "")}</td>
            <td><span class="pill pill-flat pill-muted">${escapeHtml(i.kind)}</span></td>
            <td>${escapeHtml(i.title || "(untitled)")}</td>
          </tr>`,
            )
            .join("")}
        </tbody>
      </table></div>`;
      $$("#items-table tbody tr").forEach((tr) =>
        tr.addEventListener("click", () => openItem(tr.dataset.id)),
      );
    } catch (e) {
      $("#items-table").innerHTML = `<div class="error-box">${escapeHtml(e.message)}</div>`;
    }
  };

  $("#go").addEventListener("click", () => {
    offset = 0;
    load();
  });
  $("#prev").addEventListener("click", () => {
    offset = Math.max(0, offset - limit);
    load();
  });
  $("#next").addEventListener("click", () => {
    offset += limit;
    load();
  });
  load();
}

// ---------- Exports ----------
// Persisted across renders so the user's last toggle choice sticks.
const pipelinePrefs = {
  with_embeddings: false,
  with_graph: false,
};

function pipelinePrefsToast() {
  const parts = [];
  if (pipelinePrefs.with_embeddings) parts.push("embeddings");
  if (pipelinePrefs.with_graph) parts.push("graph");
  return parts.length ? ` (+ ${parts.join(", ")})` : "";
}

function stagePill(stage) {
  if (!stage) return `<span class="pill pill-muted">—</span>`;
  const cls =
    stage.status === "done"
      ? "pill-ok"
      : stage.status === "running"
        ? "pill-info"
        : stage.status === "error"
          ? "pill-err"
          : stage.status === "skipped"
            ? "pill-muted"
            : "pill-muted";
  return `<span class="pill ${cls}" title="${escapeHtml(stage.error || "")}">${escapeHtml(stage.status)}</span>`;
}

function pipelineStagesCell(row) {
  const stages = row.pipeline_stages || {};
  const cell = (label, stage) =>
    `<div class="stage-chip"><span class="stage-chip-label">${label}</span>${stagePill(stage)}</div>`;
  return `<div class="stage-strip">
    ${cell("ingest", stages.ingest)}
    ${cell("embed", stages.embed)}
    ${cell("graph", stages.graph)}
  </div>`;
}

async function renderExports() {
  setView(`
    <h1>Exports</h1>
    <div class="sub">Raw exports registered in the vault. Standard ingest is always run; embeddings and graph extraction are opt-in follow-on stages.</div>

    <div class="row-spread">
      <div class="row">
        <button class="btn btn-primary" id="ingest-pending">▶ Ingest all pending</button>
        <button class="btn" id="refresh">↻ Refresh</button>
      </div>
      <a class="btn btn-ghost" href="#/add">+ Add new export</a>
    </div>

    <div class="pipeline-toggles" id="pipeline-toggles">
      <span class="pipeline-toggles-label">When ingesting:</span>
      <label class="checkbox-row">
        <input type="checkbox" id="opt-embeddings" ${pipelinePrefs.with_embeddings ? "checked" : ""}/>
        Compute embeddings
      </label>
      <label class="checkbox-row">
        <input type="checkbox" id="opt-graph" ${pipelinePrefs.with_graph ? "checked" : ""}/>
        Extract graph
      </label>
      <span class="pipeline-toggles-hint">Applies to per-row Ingest/Replace. "Ingest all pending" runs the standard ingest only.</span>
    </div>

    <div id="exports-table"></div>
  `);

  $("#opt-embeddings").addEventListener("change", (e) => {
    pipelinePrefs.with_embeddings = e.target.checked;
  });
  $("#opt-graph").addEventListener("change", (e) => {
    pipelinePrefs.with_graph = e.target.checked;
  });

  const load = async () => {
    $("#exports-table").innerHTML = `<div class="row"><div class="spinner"></div><div class="muted">Loading…</div></div>`;
    try {
      const rows = await api("/api/exports");
      if (!rows.length) {
        $("#exports-table").innerHTML = `<div class="empty"><div class="ico">▢</div>No exports registered yet. <a href="#/add">Add one →</a></div>`;
        return;
      }
      $("#exports-table").innerHTML = `<div class="table-wrap"><table>
        <thead><tr>
          <th>id</th><th>source</th><th>account</th>
          <th class="right">size</th><th class="right">items</th>
          <th>stages</th><th>received</th><th>actions</th>
        </tr></thead>
        <tbody>
          ${rows
            .map(
              (r) => `<tr data-id="${r.id}">
            <td class="mono">${r.id}</td>
            <td>${escapeHtml(r.source)}</td>
            <td class="mono">${escapeHtml(r.account)}</td>
            <td class="right mono">${fmtBytes(r.size_bytes)}</td>
            <td class="right mono">${r.items.toLocaleString()}</td>
            <td>${pipelineStagesCell(r)}</td>
            <td class="mono" title="${escapeHtml(r.received_at || "")}">${fmtRel(r.received_at)}</td>
            <td class="actions">
              <button class="btn btn-sm" data-act="ingest">▶ Ingest</button>
              <button class="btn btn-sm" data-act="replace" title="Reset & re-ingest">⟳ Replace</button>
              <button class="btn btn-sm btn-ghost" data-act="info">info</button>
            </td>
          </tr>`,
            )
            .join("")}
        </tbody>
      </table></div>`;

      $$("#exports-table tbody tr").forEach((tr) => {
        const id = tr.dataset.id;
        tr.querySelectorAll("button").forEach((btn) =>
          btn.addEventListener("click", (e) => {
            e.stopPropagation();
            const act = btn.dataset.act;
            if (act === "ingest") doIngestOne(id, false);
            else if (act === "replace") doIngestOne(id, true);
            else if (act === "info") showExportInfo(rows.find((x) => String(x.id) === String(id)));
          }),
        );
      });
    } catch (e) {
      $("#exports-table").innerHTML = `<div class="error-box">${escapeHtml(e.message)}</div>`;
    }
  };

  const doIngestOne = async (exportId, replace) => {
    const verb = replace ? "Replacing" : "Ingesting";
    const usePipeline = pipelinePrefs.with_embeddings || pipelinePrefs.with_graph;
    toast(`${verb} export ${exportId}${pipelinePrefsToast()}…`);
    try {
      if (usePipeline) {
        if (replace) {
          // /api/pipeline supports `replace` directly.
          const res = await api("/api/pipeline", {
            method: "POST",
            body: JSON.stringify({
              export_id: Number(exportId),
              replace,
              with_embeddings: pipelinePrefs.with_embeddings,
              with_graph: pipelinePrefs.with_graph,
            }),
          });
          summarizePipelineToast(res.pipeline);
        } else {
          const res = await api("/api/pipeline", {
            method: "POST",
            body: JSON.stringify({
              export_id: Number(exportId),
              with_embeddings: pipelinePrefs.with_embeddings,
              with_graph: pipelinePrefs.with_graph,
            }),
          });
          summarizePipelineToast(res.pipeline);
        }
      } else {
        const res = await api("/api/ingest", {
          method: "POST",
          body: JSON.stringify({ export_id: Number(exportId), replace }),
        });
        const run = res.runs[0];
        if (run && run.status === "ok") {
          toast(`Done · seen ${run.items_seen}, inserted ${run.items_inserted}`);
        } else {
          toast(`Run ${run?.status || "?"} — see Dashboard`, "err");
        }
      }
    } catch (e) {
      toast(e.message, "err");
    }
    // refreshLookups re-renders the sidebar Sources section + counts so a new
    // source becomes visible without a page reload; load() refreshes the
    // Exports table itself.
    await refreshLookups();
    load();
  };

  $("#ingest-pending").addEventListener("click", async () => {
    toast("Ingesting all pending exports…");
    try {
      const res = await api("/api/ingest", {
        method: "POST",
        body: JSON.stringify({ all_pending: true }),
      });
      if (!res.runs.length) toast("No pending exports.");
      else toast(`${res.runs.length} run(s) completed.`);
    } catch (e) {
      toast(e.message, "err");
    }
    await refreshLookups();
    load();
  });

  $("#refresh").addEventListener("click", async () => {
    await refreshLookups();
    load();
  });
  load();
}

function summarizePipelineToast(pipeline) {
  if (!pipeline || !Array.isArray(pipeline.stages)) {
    toast("Pipeline finished.");
    return;
  }
  const labels = pipeline.stages
    .map((s) => `${s.stage}=${s.status}`)
    .join(" · ");
  const anyError = pipeline.stages.some((s) => s.status === "error");
  toast(labels, anyError ? "err" : undefined);
}

function renderPipelineSummary(pipeline) {
  if (!pipeline || !Array.isArray(pipeline.stages)) return "";
  const rows = pipeline.stages
    .map((s) => {
      const cls =
        s.status === "done"
          ? "pill-ok"
          : s.status === "error"
            ? "pill-err"
            : s.status === "skipped"
              ? "pill-muted"
              : "pill-info";
      const meta =
        s.metadata && Object.keys(s.metadata).length
          ? Object.entries(s.metadata)
              .map(
                ([k, v]) =>
                  `<span class="mono" style="color:var(--text-3)">${escapeHtml(k)}=${escapeHtml(String(v))}</span>`,
              )
              .join(" ")
          : "";
      const err = s.error ? `<div class="error-box" style="margin-top:4px">${escapeHtml(s.error)}</div>` : "";
      return `<div style="margin-top:6px">
        <span class="pill ${cls}">${escapeHtml(s.stage)}: ${escapeHtml(s.status)}</span>
        <span class="mono" style="margin-left:8px">items=${s.items_processed ?? 0}</span>
        ${meta ? `<span style="margin-left:8px">${meta}</span>` : ""}
        ${err}
      </div>`;
    })
    .join("");
  return `<div style="margin-top:10px">${rows}</div>`;
}

function showExportInfo(row) {
  if (!row) return;
  $("#detail-title").textContent = `Export ${row.id} · ${row.source}`;
  $("#detail-body").innerHTML = `
    <div class="meta-grid">
      <div class="k">source</div><div class="v">${escapeHtml(row.source)}</div>
      <div class="k">account</div><div class="v">${escapeHtml(row.account)}</div>
      <div class="k">size</div><div class="v">${fmtBytes(row.size_bytes)}</div>
      <div class="k">sha256</div><div class="v">${escapeHtml(row.sha256)}</div>
      <div class="k">received</div><div class="v">${fmtTs(row.received_at)}</div>
      <div class="k">stored</div><div class="v">${escapeHtml(row.stored_path)}</div>
      <div class="k">original</div><div class="v">${escapeHtml(row.original_path)}</div>
      <div class="k">items</div><div class="v">${row.items}</div>
      <div class="k">runs</div><div class="v">${row.runs}</div>
    </div>
    ${row.notes ? `<h2>Notes</h2><pre class="body">${escapeHtml(row.notes)}</pre>` : ""}
    ${
      row.latest_run
        ? `<h2>Latest run</h2>
      <div class="meta-grid">
        <div class="k">status</div><div class="v">${escapeHtml(row.latest_run.status)}</div>
        <div class="k">seen</div><div class="v">${row.latest_run.items_seen}</div>
        <div class="k">inserted</div><div class="v">${row.latest_run.items_inserted}</div>
        <div class="k">skipped</div><div class="v">${row.latest_run.items_skipped}</div>
        <div class="k">started</div><div class="v">${fmtTs(row.latest_run.started_at)}</div>
        <div class="k">finished</div><div class="v">${fmtTs(row.latest_run.finished_at)}</div>
      </div>
      ${row.latest_run.error ? `<pre class="body">${escapeHtml(row.latest_run.error)}</pre>` : ""}`
        : ""
    }
    ${pipelineDetailSection(row.pipeline_stages)}
  `;
  $("#detail").hidden = false;
}

function pipelineDetailSection(stages) {
  if (!stages || !Object.keys(stages).length) return "";
  const order = ["ingest", "embed", "graph"];
  const blocks = order
    .map((name) => {
      const s = stages[name];
      if (!s) return "";
      const cls =
        s.status === "done"
          ? "pill-ok"
          : s.status === "error"
            ? "pill-err"
            : s.status === "skipped"
              ? "pill-muted"
              : "pill-info";
      const meta =
        s.metadata && Object.keys(s.metadata).length
          ? Object.entries(s.metadata)
              .map(
                ([k, v]) =>
                  `<div class="k">${escapeHtml(k)}</div><div class="v mono">${escapeHtml(String(v))}</div>`,
              )
              .join("")
          : "";
      return `
        <div style="margin-top:8px">
          <span class="pill ${cls}">${escapeHtml(name)}: ${escapeHtml(s.status)}</span>
          <span class="mono" style="margin-left:8px">items=${s.items_processed ?? 0}</span>
          ${s.started_at ? `<span class="mono" style="margin-left:8px;color:var(--text-3)">started ${escapeHtml(fmtTs(s.started_at) || "")}</span>` : ""}
          ${s.finished_at ? `<span class="mono" style="margin-left:8px;color:var(--text-3)">finished ${escapeHtml(fmtTs(s.finished_at) || "")}</span>` : ""}
          ${meta ? `<div class="meta-grid" style="margin-top:6px">${meta}</div>` : ""}
          ${s.error ? `<pre class="body">${escapeHtml(s.error)}</pre>` : ""}
        </div>`;
    })
    .filter(Boolean)
    .join("");
  return blocks ? `<h2>Pipeline stages</h2>${blocks}` : "";
}

// ---------- Add Export ----------
function renderAddExport() {
  const accountOptions = state.accounts
    .map((a) => `<option value="${escapeHtml(a.handle)}"></option>`)
    .join("");

  setView(`
    <h1>Add export</h1>
    <div class="sub">Register a downloaded export. The file is copied (never moved) into <span class="mono">vault/raw/</span>.</div>

    <div class="form-card">
      <div class="field">
        <label for="f-source">Source</label>
        <select id="f-source">
          <option value="">— select source —</option>
          ${state.parsers.map((p) => `<option value="${escapeHtml(p.slug)}">${escapeHtml(p.label)} <span>(${escapeHtml(p.slug)})</span></option>`).join("")}
        </select>
        <div class="hint">Pick the parser matching the export type.</div>
      </div>

      <div class="field">
        <label for="f-path">Path</label>
        <input id="f-path" type="text" placeholder="C:\\Users\\you\\Downloads\\claude-export.zip" />
        <div class="hint">Full path to a .zip file or extracted directory on this machine.</div>
      </div>

      <div class="field">
        <label for="f-account">Account</label>
        <input id="f-account" type="text" placeholder="you@example.com or @handle" list="acct-list" />
        <datalist id="acct-list">${accountOptions}</datalist>
        <div class="hint">Email, username, or any label that identifies the account this export came from.</div>
      </div>

      <div class="field">
        <label for="f-notes">Notes (optional)</label>
        <textarea id="f-notes" placeholder="Anything to remember about this export…"></textarea>
      </div>

      <div class="row" style="margin-top:8px;flex-wrap:wrap;gap:14px">
        <button class="btn btn-primary" id="f-submit">Register export</button>
        <label class="checkbox-row">
          <input type="checkbox" id="f-then-ingest" checked /> Ingest immediately
        </label>
        <label class="checkbox-row">
          <input type="checkbox" id="f-with-embeddings" ${pipelinePrefs.with_embeddings ? "checked" : ""}/> Compute embeddings
        </label>
        <label class="checkbox-row">
          <input type="checkbox" id="f-with-graph" ${pipelinePrefs.with_graph ? "checked" : ""}/> Extract graph
        </label>
      </div>
      <div class="hint" style="margin-top:6px">
        Standard ingest is always run. Embeddings and graph extraction only run when their toggles are on; either can be re-run later from the Exports page.
      </div>

      <div id="f-result" style="margin-top:14px"></div>
    </div>
  `);

  $("#f-with-embeddings").addEventListener("change", (e) => {
    pipelinePrefs.with_embeddings = e.target.checked;
  });
  $("#f-with-graph").addEventListener("change", (e) => {
    pipelinePrefs.with_graph = e.target.checked;
  });

  $("#f-submit").addEventListener("click", async () => {
    const source = $("#f-source").value;
    const path = $("#f-path").value.trim();
    const account = $("#f-account").value.trim();
    const notes = $("#f-notes").value.trim() || null;
    if (!source || !path || !account) {
      $("#f-result").innerHTML = `<div class="error-box">source, path, and account are required.</div>`;
      return;
    }
    $("#f-result").innerHTML = `<div class="row"><div class="spinner"></div><div class="muted">Registering…</div></div>`;
    try {
      const raw = await api("/api/exports", {
        method: "POST",
        body: JSON.stringify({ source, path, account, notes }),
      });
      let msg = `Registered export <span class="mono">id=${raw.id}</span> sha256 <span class="mono">${raw.sha256.slice(0, 12)}…</span>`;
      $("#f-result").innerHTML = `<div class="pill pill-ok">success</div> ${msg}`;
      toast(`Registered export ${raw.id}`);
      if ($("#f-then-ingest").checked) {
        // Dedicated container for the ingest stage so the spinner is REPLACED
        // (not appended-next-to) when the run completes. Prior behavior left
        // the "Ingesting…" spinner spinning alongside the per-stage "done"
        // pills, which made it look like ingest was still running.
        $("#f-result").insertAdjacentHTML(
          "beforeend",
          `<div id="f-ingest-progress" style="margin-top:8px" class="row">
             <div class="spinner"></div>
             <div class="muted">Ingesting${pipelinePrefsToast()}…</div>
           </div>`,
        );
        const progress = $("#f-ingest-progress");
        try {
          const usePipeline =
            pipelinePrefs.with_embeddings || pipelinePrefs.with_graph;
          if (usePipeline) {
            const res = await api("/api/pipeline", {
              method: "POST",
              body: JSON.stringify({
                export_id: raw.id,
                with_embeddings: pipelinePrefs.with_embeddings,
                with_graph: pipelinePrefs.with_graph,
              }),
            });
            summarizePipelineToast(res.pipeline);
            progress.outerHTML = renderPipelineSummary(res.pipeline);
          } else {
            const res = await api("/api/ingest", {
              method: "POST",
              body: JSON.stringify({ export_id: raw.id }),
            });
            const run = res.runs[0];
            toast(`Ingest ${run.status} · inserted ${run.items_inserted}`);
            progress.outerHTML = `<div style="margin-top:6px">Run <span class="mono">${run.id}</span> · ${statusPill(run)} · seen ${run.items_seen}, inserted ${run.items_inserted}, skipped ${run.items_skipped}</div>`;
          }
        } catch (e) {
          toast(e.message, "err");
          progress.outerHTML = `<div class="error-box">${escapeHtml(e.message)}</div>`;
        }
      }
      // refreshLookups now re-renders the sidebar Sources section too, so
      // newly-ingested sources appear without a manual page reload.
      await refreshLookups();
    } catch (e) {
      $("#f-result").innerHTML = `<div class="error-box">${escapeHtml(e.message)}</div>`;
      toast(e.message, "err");
    }
  });
}

// ---------- Repo intake ----------
let repoScanCache = [];

function renderRepoIntake() {
  const activeVault = state.activeVault ? state.activeVault.name : "personal";
  const defaultAccount = activeVault === "personal" ? "code-corpus" : activeVault;
  setView(`
    <h1>Repo intake</h1>
    <div class="sub">
      Bulk-register a folder of cloned repos. Codex's intake pipeline scans every
      subdirectory, identifies each repo by its git remote (owner/repo), skips
      duplicates already in this vault, and copies the new ones into
      <span class="mono">vault/raw/</span>. Designed for the
      <span class="mono">code-corpus</span> workflow.
    </div>

    <div class="form-card" style="max-width:820px;margin-bottom:18px">
      <div class="field">
        <label for="ri-path">Intake folder</label>
        <input id="ri-path" type="text" placeholder="C:\\Users\\you\\Documents\\repo-intake" />
        <div class="hint">Parent directory containing one or more cloned repos.</div>
      </div>
      <div class="row" style="gap:14px;align-items:center;margin-bottom:6px">
        <label class="checkbox-row">
          <input type="checkbox" id="ri-recursive" /> Recurse into subdirectories
        </label>
      </div>
      <div class="row" style="gap:8px;margin-top:10px">
        <button class="btn btn-primary" id="ri-scan">Scan</button>
        <button class="btn" id="ri-clear" disabled>Clear</button>
      </div>
    </div>

    <div id="ri-scan-result"></div>

    <div id="ri-register-card" style="display:none">
      <h2>Register</h2>
      <div class="form-card" style="max-width:820px">
        <div class="row" style="gap:14px;align-items:flex-end">
          <div class="field" style="margin-bottom:0;flex:0 0 240px">
            <label for="ri-account">Account</label>
            <input id="ri-account" type="text" value="${escapeHtml(defaultAccount)}" />
            <div class="hint">All intake repos share this account. Defaults to the active vault name.</div>
          </div>
          <div class="field" style="margin-bottom:0;flex:1">
            <label for="ri-notes">Notes (optional)</label>
            <input id="ri-notes" type="text" placeholder="batch label, e.g. 2026-04 OSS pull" />
          </div>
        </div>
        <div class="row" style="gap:14px;margin-top:14px">
          <label class="checkbox-row">
            <input type="checkbox" id="ri-ingest" checked /> Ingest each repo immediately
          </label>
        </div>
        <div class="row" style="gap:8px;margin-top:14px">
          <button class="btn btn-primary" id="ri-register-go">
            <span id="ri-register-label">Register new repos</span>
          </button>
        </div>
        <div id="ri-register-result" style="margin-top:14px"></div>
      </div>
    </div>
  `);

  $("#ri-scan").addEventListener("click", doRepoScan);
  $("#ri-clear").addEventListener("click", () => {
    repoScanCache = [];
    $("#ri-scan-result").innerHTML = "";
    $("#ri-register-card").style.display = "none";
    $("#ri-clear").disabled = true;
  });
  $("#ri-path").addEventListener("keydown", (e) => {
    if (e.key === "Enter") doRepoScan();
  });
  $("#ri-register-go").addEventListener("click", doRepoRegister);
}

async function doRepoScan() {
  const path = $("#ri-path").value.trim();
  if (!path) {
    $("#ri-scan-result").innerHTML = `<div class="error-box">Path is required.</div>`;
    return;
  }
  const recursive = $("#ri-recursive").checked;
  $("#ri-scan-result").innerHTML = `<div class="row" style="padding:18px"><div class="spinner"></div><div class="muted">Scanning…</div></div>`;
  $("#ri-register-card").style.display = "none";
  try {
    const res = await api("/api/repos/scan", {
      method: "POST",
      body: JSON.stringify({ path, recursive }),
    });
    repoScanCache = res.repos || [];
    renderRepoScanResult(repoScanCache);
    $("#ri-clear").disabled = repoScanCache.length === 0;
  } catch (e) {
    $("#ri-scan-result").innerHTML = `<div class="error-box">${escapeHtml(e.message)}</div>`;
  }
}

function renderRepoScanResult(repos) {
  const total = repos.length;
  const dupes = repos.filter((r) => r.duplicate).length;
  const fresh = total - dupes;

  if (total === 0) {
    $("#ri-scan-result").innerHTML = `<div class="empty muted">No git repos found at that path.</div>`;
    return;
  }

  $("#ri-scan-result").innerHTML = `
    <div class="stat-grid" style="margin-bottom:14px">
      <div class="stat-card">
        <div class="label">Repos found</div>
        <div class="value">${total}</div>
      </div>
      <div class="stat-card">
        <div class="label">New</div>
        <div class="value">${fresh}</div>
      </div>
      <div class="stat-card">
        <div class="label">Already imported</div>
        <div class="value">${dupes}</div>
      </div>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>repo</th>
            <th>remote</th>
            <th>head</th>
            <th>status</th>
            <th>path</th>
          </tr>
        </thead>
        <tbody>
          ${repos
            .map((r) => {
              const repo = r.repo || {};
              const status = r.duplicate
                ? `<span class="pill pill-muted">duplicate · export ${r.existing_export_id ?? "?"}</span>`
                : `<span class="pill pill-ok">new</span>`;
              return `<tr>
                <td><span class="mono" style="color:var(--text)">${escapeHtml(repo.key || repo.name || "?")}</span></td>
                <td class="mono">${escapeHtml(repo.remote_url || "—")}</td>
                <td class="mono">${escapeHtml((repo.head_sha || "").slice(0, 9) || "—")}</td>
                <td>${status}</td>
                <td class="mono" title="${escapeHtml(r.path)}">${escapeHtml(shortenPath(r.path))}</td>
              </tr>`;
            })
            .join("")}
        </tbody>
      </table>
    </div>
  `;

  if (fresh > 0) {
    $("#ri-register-card").style.display = "block";
    $("#ri-register-label").textContent = `Register ${fresh} new repo${fresh === 1 ? "" : "s"}`;
  } else {
    $("#ri-register-card").style.display = "none";
  }
}

function shortenPath(p) {
  if (!p) return "";
  const parts = p.split(/[\\/]/);
  if (parts.length <= 3) return p;
  return ".../" + parts.slice(-2).join("/");
}

async function doRepoRegister() {
  const path = $("#ri-path").value.trim();
  if (!path) return;
  const account = $("#ri-account").value.trim() || "code-corpus";
  const notes = $("#ri-notes").value.trim() || null;
  const recursive = $("#ri-recursive").checked;
  const ingest = $("#ri-ingest").checked;
  const verb = ingest ? "Registering and ingesting" : "Registering";
  $("#ri-register-result").innerHTML = `<div class="row"><div class="spinner"></div><div class="muted">${verb} new repos…</div></div>`;
  $("#ri-register-go").disabled = true;
  try {
    const res = await api("/api/repos/register", {
      method: "POST",
      body: JSON.stringify({ path, account, recursive, ingest, notes }),
    });
    renderRepoRegisterResult(res.results || []);
    toast(`${res.results.length} repo result${res.results.length === 1 ? "" : "s"}`);
    await refreshLookups();
    // Refresh the scan view so duplicates flip from "new" to imported.
    await doRepoScan();
  } catch (e) {
    $("#ri-register-result").innerHTML = `<div class="error-box">${escapeHtml(e.message)}</div>`;
  } finally {
    $("#ri-register-go").disabled = false;
  }
}

function renderRepoRegisterResult(results) {
  if (!results.length) {
    $("#ri-register-result").innerHTML = `<div class="empty muted">No repos were registered.</div>`;
    return;
  }
  const counts = results.reduce((m, r) => {
    m[r.status] = (m[r.status] || 0) + 1;
    return m;
  }, {});
  const summary = Object.entries(counts)
    .map(([k, v]) => `<span class="pill ${pillForStatus(k)}">${escapeHtml(k)}: ${v}</span>`)
    .join(" ");
  $("#ri-register-result").innerHTML = `
    <div class="row" style="margin-bottom:10px;gap:6px">${summary}</div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr><th>repo</th><th>status</th><th>export</th><th>run</th><th>note</th></tr>
        </thead>
        <tbody>
          ${results
            .map((r) => {
              const repo = r.repo || {};
              const note = r.error ? escapeHtml(r.error) : "";
              return `<tr>
                <td><span class="mono" style="color:var(--text)">${escapeHtml(repo.key || repo.name || "?")}</span></td>
                <td><span class="pill ${pillForStatus(r.status)}">${escapeHtml(r.status)}</span></td>
                <td class="mono">${r.export_id ?? "—"}</td>
                <td class="mono">${r.run_id ?? "—"}</td>
                <td class="mono" style="max-width:340px;overflow:hidden;text-overflow:ellipsis">${note}</td>
              </tr>`;
            })
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}

function pillForStatus(status) {
  const s = (status || "").toLowerCase();
  if (s === "registered" || s === "ingested" || s === "ok") return "pill-ok";
  if (s === "duplicate" || s === "skipped") return "pill-muted";
  if (s === "error" || s === "failed") return "pill-err";
  if (s === "running") return "pill-info";
  return "pill-info";
}

// ---------- Embeddings ----------
let embedJob = null;

async function renderEmbed() {
  setView(`
    <h1>Embeddings</h1>
    <div class="sub">Compute vector embeddings so semantic search can match by meaning instead of literal words. Each text item is chunked at 1,500 chars; one item produces one or more embedding rows.</div>

    <div id="embed-stats"></div>

    <h2>Run</h2>
    <div class="form-card" style="max-width:760px">
      <div class="row" style="gap:14px;align-items:flex-end;margin-bottom:16px">
        <div class="field" style="margin-bottom:0;flex:0 0 130px">
          <label for="e-batch">Batch size</label>
          <input id="e-batch" type="number" value="200" min="1" max="5000" />
        </div>
        <div class="field" style="margin-bottom:0;flex:1">
          <label>&nbsp;</label>
          <div class="hint" style="margin-top:0">Smaller batches give snappier progress; larger ones run a touch faster.</div>
        </div>
      </div>

      <div class="row" style="gap:8px">
        <button class="btn btn-primary" id="e-all">∿ Embed all pending</button>
        <button class="btn" id="e-once">Embed one batch</button>
        <button class="btn btn-danger" id="e-stop" disabled>Stop</button>
      </div>

      <div id="e-progress" style="margin-top:18px;display:none">
        <div class="bar-track" style="height:8px;margin-bottom:8px">
          <div id="e-bar" class="bar-fill" style="width:0%"></div>
        </div>
        <div class="row" style="justify-content:space-between;font-size:12.5px">
          <span id="e-status" class="muted">Idle</span>
          <span id="e-eta" class="mono muted"></span>
        </div>
      </div>

      <div id="e-log" style="margin-top:16px;display:flex;flex-direction:column;gap:4px;font-family:var(--mono);font-size:12px;color:var(--text-3);max-height:200px;overflow:auto"></div>
    </div>
  `);

  await refreshEmbedStats();

  $("#e-once").addEventListener("click", () => runEmbedJob({ once: true }));
  $("#e-all").addEventListener("click", () => runEmbedJob({ once: false }));
  $("#e-stop").addEventListener("click", () => {
    if (embedJob) embedJob.cancel = true;
  });
}

async function refreshEmbedStats() {
  try {
    const s = await api("/api/embed/stats");
    const total = s.items_with_text || 0;
    const embedded = s.items_embedded || 0;
    const pending = s.items_pending || 0;
    const pct = total > 0 ? (embedded / total) * 100 : 0;
    const dev = s.device || {};
    const deviceClass = dev.device === "cuda"
      ? "pill-ok"
      : dev.device === "cpu" || !dev.available
      ? "pill-muted"
      : "pill-info";
    $("#embed-stats").innerHTML = `
      <div class="stat-grid" style="margin-bottom:14px">
        <div class="stat-card">
          <div class="label">Items with text</div>
          <div class="value">${total.toLocaleString()}</div>
        </div>
        <div class="stat-card">
          <div class="label">Embedded</div>
          <div class="value">${embedded.toLocaleString()}</div>
        </div>
        <div class="stat-card">
          <div class="label">Pending</div>
          <div class="value">${pending.toLocaleString()}</div>
        </div>
        <div class="stat-card">
          <div class="label">Chunks stored</div>
          <div class="value">${s.total_chunks.toLocaleString()}</div>
        </div>
      </div>
      <div class="bars" style="margin-bottom:14px">
        <div class="bar-row">
          <div class="name">progress</div>
          <div class="bar-track"><div class="bar-fill" style="width:${pct.toFixed(1)}%"></div></div>
          <div class="count">${pct.toFixed(1)}%</div>
        </div>
      </div>
      <div class="row" style="gap:18px;font-size:12.5px;color:var(--text-3);margin-bottom:8px">
        <span><span class="muted">Model</span> <span class="mono" style="color:var(--text-2)">${escapeHtml(s.model)}</span> <span class="muted">· ${s.embed_dim}-dim</span></span>
        <span><span class="muted">Device</span> <span class="pill ${deviceClass}">${escapeHtml(dev.label || "unknown")}</span></span>
        ${dev.torch ? `<span class="muted">torch ${escapeHtml(dev.torch)}</span>` : ""}
      </div>
      ${
        !dev.available
          ? `<div class="error-box">${escapeHtml(dev.note || "Embeddings backend not available.")}</div>`
          : ""
      }
    `;
    return s;
  } catch (e) {
    $("#embed-stats").innerHTML = `<div class="error-box">${escapeHtml(e.message)}</div>`;
    return null;
  }
}

function embedLog(line) {
  const log = $("#e-log");
  if (!log) return;
  const row = document.createElement("div");
  row.textContent = line;
  log.appendChild(row);
  log.scrollTop = log.scrollHeight;
}

async function runEmbedJob({ once }) {
  if (embedJob) return; // already running
  const limit = Math.max(1, Number($("#e-batch").value) || 200);
  const allBtn = $("#e-all");
  const onceBtn = $("#e-once");
  const stopBtn = $("#e-stop");
  const progress = $("#e-progress");
  const bar = $("#e-bar");
  const statusEl = $("#e-status");
  const etaEl = $("#e-eta");

  allBtn.disabled = true;
  onceBtn.disabled = true;
  stopBtn.disabled = false;
  progress.style.display = "block";

  embedJob = { cancel: false };
  const startedAt = Date.now();
  let batchIdx = 0;
  let totalChunksThisRun = 0;
  let initialEmbedded = null;
  let initialTotal = null;

  try {
    while (!embedJob.cancel) {
      const stats = await api("/api/embed/stats");
      if (initialEmbedded === null) {
        initialEmbedded = stats.items_embedded;
        initialTotal = stats.items_with_text;
      }
      const pending = stats.items_pending;
      const pct =
        stats.items_with_text > 0
          ? (stats.items_embedded / stats.items_with_text) * 100
          : 100;
      bar.style.width = `${pct.toFixed(1)}%`;
      statusEl.textContent = `${stats.items_embedded.toLocaleString()} / ${stats.items_with_text.toLocaleString()} items · ${pending.toLocaleString()} pending`;

      if (pending === 0) {
        statusEl.textContent = `Done · ${stats.items_embedded.toLocaleString()} / ${stats.items_with_text.toLocaleString()} items embedded`;
        break;
      }

      batchIdx += 1;
      const batchStart = Date.now();
      embedLog(`Batch ${batchIdx} · running (limit ${limit})…`);

      const res = await api("/api/embed", {
        method: "POST",
        body: JSON.stringify({ limit }),
      });
      const elapsedMs = Date.now() - batchStart;
      totalChunksThisRun += res.embedded;
      embedLog(
        `Batch ${batchIdx} · embedded ${res.embedded.toLocaleString()} chunks in ${(elapsedMs / 1000).toFixed(1)}s`,
      );

      // ETA: based on items processed since the run started.
      const after = await api("/api/embed/stats");
      const itemsDone = (after.items_embedded || 0) - (initialEmbedded || 0);
      const totalElapsed = Date.now() - startedAt;
      if (itemsDone > 0) {
        const msPerItem = totalElapsed / itemsDone;
        const remaining = after.items_pending;
        const etaSec = Math.round((remaining * msPerItem) / 1000);
        etaEl.textContent = `~${formatEta(etaSec)} remaining`;
      }

      if (res.embedded === 0) {
        // Defensive: if a batch returns nothing but pending > 0, something's off.
        embedLog(`Batch ${batchIdx} · no chunks produced; stopping.`);
        break;
      }
      if (once) break;
    }
  } catch (e) {
    embedLog(`Error: ${e.message}`);
    toast(e.message, "err");
  } finally {
    if (embedJob && embedJob.cancel) {
      embedLog("Stopped by user.");
      statusEl.textContent = "Stopped";
    }
    embedJob = null;
    allBtn.disabled = false;
    onceBtn.disabled = false;
    stopBtn.disabled = true;
    etaEl.textContent = "";
    await refreshEmbedStats();
  }
}

function formatEta(sec) {
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) {
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m}m ${s}s`;
  }
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return `${h}h ${m}m`;
}

// ---------- Item detail panel ----------
async function openItem(id, query) {
  $("#detail-title").textContent = `Item ${id}`;
  $("#detail-body").innerHTML = `<div class="row"><div class="spinner"></div><div class="muted">Loading…</div></div>`;
  $("#detail").hidden = false;
  try {
    const i = await api(`/items/${id}`);
    $("#detail-title").textContent = i.title || `Item ${i.id}`;
    const meta = [
      ["id", i.id],
      ["source", i.source],
      ["account", i.account],
      ["kind", i.kind],
      ["when", fmtTs(i.ts)],
      ["native id", i.native_id || "—"],
      ["export", i.raw_export_id],
      ["run", i.ingestion_run_id],
      ["hash", (i.content_hash || "").slice(0, 16) + "…"],
    ];
    $("#detail-body").innerHTML = `
      <div class="meta-grid">
        ${meta.map(([k, v]) => `<div class="k">${escapeHtml(k)}</div><div class="v">${escapeHtml(String(v))}</div>`).join("")}
      </div>
      ${
        i.tags && i.tags.length
          ? `<h2>Tags</h2><div class="kv-list">${i.tags
              .map((t) => `<span class="kv-tag">${escapeHtml(t.key)}: ${escapeHtml(t.value)}</span>`)
              .join("")}</div>`
          : ""
      }
      ${
        i.media && i.media.length
          ? `<h2>Media</h2><div class="kv-list">${i.media
              .map((m) => `<span class="kv-tag">${escapeHtml(m.type)} · ${escapeHtml(m.mime || "?")} · ${escapeHtml((m.path || "").split(/[\\/]/).pop())}</span>`)
              .join("")}</div>`
          : ""
      }
      ${
        i.body
          ? `<h2>Body <span class="muted" style="text-transform:none;letter-spacing:0">(${i.body.length.toLocaleString()} chars)</span></h2>
        <pre class="body">${query ? highlight(i.body, query) : escapeHtml(i.body)}</pre>`
          : `<div class="empty muted">No text body for this item.</div>`
      }
      ${
        i.metadata && Object.keys(i.metadata).length
          ? `<h2>Metadata</h2><pre class="body">${escapeHtml(JSON.stringify(i.metadata, null, 2))}</pre>`
          : ""
      }
    `;
  } catch (e) {
    $("#detail-body").innerHTML = `<div class="error-box">${escapeHtml(e.message)}</div>`;
  }
}

function closeDetail() {
  $("#detail").hidden = true;
}
