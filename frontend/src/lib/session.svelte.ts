/* Cross-cutting session lookups: parsers, sources, accounts, stats, vaults.
 *
 * All routes can read from `session.X`; any place that mutates the vault
 * (ingest, register, reset, vault switch) calls `session.refresh()` and
 * every component re-derives automatically via runes. No manual DOM
 * synchronization, no remember-to-call patterns.
 */

import { api } from "./api";
import type { Account, Parser, Source, VaultsResponse } from "./types";

let parsers = $state<Parser[]>([]);
let accounts = $state<Account[]>([]);
let sources = $state<Source[]>([]);
let itemsPerSource = $state<Record<string, number>>({});
let vaults = $state<VaultsResponse | null>(null);
let totalItems = $state(0);
let totalExports = $state(0);
let totalRuns = $state(0);
let loaded = $state(false);
let loading = $state(false);

async function refresh() {
  loading = true;
  try {
    const [p, a, s, st, v] = await Promise.all([
      api.parsers(),
      api.accounts(),
      api.sources(),
      api.stats(),
      api.vaults().catch(() => null),
    ]);
    parsers = p;
    accounts = a;
    sources = s;
    itemsPerSource = st.items_per_source ?? {};
    totalItems = st.items;
    totalExports = st.exports;
    totalRuns = st.runs;
    vaults = v;
    loaded = true;
  } catch (e) {
    console.error("session.refresh failed", e);
  } finally {
    loading = false;
  }
}

export const session = {
  get parsers() { return parsers; },
  get accounts() { return accounts; },
  get sources() { return sources; },
  get itemsPerSource() { return itemsPerSource; },
  get vaults() { return vaults; },
  get activeVault() { return vaults?.active ?? null; },
  get totalItems() { return totalItems; },
  get totalExports() { return totalExports; },
  get totalRuns() { return totalRuns; },
  get loaded() { return loaded; },
  get loading() { return loading; },
  refresh,
};
