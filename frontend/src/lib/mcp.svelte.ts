/* MCP HTTP server status — toggle + reactive polling.
 *
 * Two consumers care about this:
 *   - The header status dot (always visible, lightweight read).
 *   - The Settings panel (full stats, start/stop buttons).
 *
 * Both share one source of truth here. Polling runs while at least one
 * consumer is subscribed (ref-counted) and pauses when nothing visible
 * needs the data. That keeps the request rate low when the user isn't
 * actively looking at the MCP UI.
 */

import { api } from "./api";
import type { MCPStatus } from "./types";

let status = $state<MCPStatus | null>(null);
let pending = $state(false);
let lastError = $state<string | null>(null);

let pollHandle: ReturnType<typeof setInterval> | null = null;
let subscribers = 0;

const POLL_MS = 4000;

async function refresh(): Promise<void> {
  pending = true;
  try {
    status = await api.mcpStatus();
    lastError = null;
  } catch (e) {
    lastError = e instanceof Error ? e.message : String(e);
  } finally {
    pending = false;
  }
}

function startPolling(): void {
  if (pollHandle !== null) return;
  pollHandle = setInterval(() => void refresh(), POLL_MS);
}

function stopPolling(): void {
  if (pollHandle === null) return;
  clearInterval(pollHandle);
  pollHandle = null;
}

/** Increment the subscriber count and return an unsubscribe function.
 * Wire this from `$effect(() => mcp.subscribe())` so the polling loop
 * runs while a component using mcp data is mounted. */
function subscribe(): () => void {
  subscribers += 1;
  if (subscribers === 1) {
    void refresh();
    startPolling();
  }
  return () => {
    subscribers -= 1;
    if (subscribers <= 0) {
      subscribers = 0;
      stopPolling();
    }
  };
}

async function start(): Promise<void> {
  pending = true;
  try {
    status = await api.mcpStart();
    lastError = null;
  } catch (e) {
    lastError = e instanceof Error ? e.message : String(e);
    throw e;
  } finally {
    pending = false;
  }
}

async function stop(): Promise<void> {
  pending = true;
  try {
    status = await api.mcpStop();
    lastError = null;
  } catch (e) {
    lastError = e instanceof Error ? e.message : String(e);
    throw e;
  } finally {
    pending = false;
  }
}

export const mcp = {
  get status() {
    return status;
  },
  get enabled() {
    return status?.enabled ?? false;
  },
  get pending() {
    return pending;
  },
  get lastError() {
    return lastError;
  },
  subscribe,
  refresh,
  start,
  stop,
};
