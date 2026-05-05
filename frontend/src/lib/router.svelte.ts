/* Hash router with route + query state.
 *
 * Hash shape: #/route?key=value&...
 * Filters in the hash so any view is shareable as a URL.
 */

interface RouterState {
  route: string;
  query: URLSearchParams;
}

function parse(): RouterState {
  const h = window.location.hash || "#/dashboard";
  const stripped = h.replace(/^#\//, "");
  const idx = stripped.indexOf("?");
  if (idx === -1) return { route: stripped || "dashboard", query: new URLSearchParams() };
  return {
    route: stripped.slice(0, idx) || "dashboard",
    query: new URLSearchParams(stripped.slice(idx + 1)),
  };
}

let state = $state<RouterState>(parse());

window.addEventListener("hashchange", () => {
  state = parse();
});

export const router = {
  get route() {
    return state.route;
  },
  get query() {
    return state.query;
  },
  /** Get one query param. */
  param(key: string): string | null {
    return state.query.get(key);
  },
  /** Replace the current hash. */
  go(route: string, query: Record<string, string | null | undefined> = {}) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) {
      if (v !== null && v !== undefined && v !== "") qs.set(k, String(v));
    }
    const tail = qs.toString();
    const next = `#/${route}${tail ? `?${tail}` : ""}`;
    if (window.location.hash !== next) {
      window.location.hash = next;
    } else {
      state = parse(); // re-trigger derivations even if hash didn't change
    }
  },
  /** Patch the current route's query string in place. */
  patch(patch: Record<string, string | null | undefined>) {
    const next = new URLSearchParams(state.query);
    for (const [k, v] of Object.entries(patch)) {
      if (v === null || v === undefined || v === "") next.delete(k);
      else next.set(k, String(v));
    }
    this.go(state.route, Object.fromEntries(next.entries()));
  },
};
