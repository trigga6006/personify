// Symbol set kept in one place so we can swap them without touching every
// component. We deliberately use unicode glyphs rather than an icon font —
// no extra font load, no missing-glyph fallback, and they sit at the right
// optical size in the warm-mono typography we use everywhere else.

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
} as const;
