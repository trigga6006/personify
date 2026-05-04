// Symbol set kept in one place so we can swap them without touching every
// component. Unicode glyphs over an icon font: no extra font load, no
// missing-glyph fallback, and they sit at the right optical size in the
// monospace typography we use everywhere.

export const icons = {
  search: "⌕",
  browse: "≡",
  graph: "◇",
  dashboard: "▤",
  exports: "▢",
  repos: "⌘",
  embed: "∿",
  settings: "⚙",
  add: "+",
  refresh: "↻",
  play: "▶",
  replay: "⟳",
  close: "✕",
  chev: "⌄",
  warn: "⚠",
  dot: "·",
  arrow: "→",
  folder: "▸",
  folderOpen: "▾",
  disk: "◉",
  pin: "✦",
  file: "▢",
  sun: "☀",
  moon: "☾",
} as const;
