// Persistent dark/light theme. The actual `data-theme` attribute is set
// synchronously by an inline boot script in index.html so the page never
// flashes the wrong palette before Svelte mounts. This store mirrors that
// state and owns toggling.

type Theme = "dark" | "light";

const STORAGE_KEY = "personify-theme";

function loadInitial(): Theme {
  if (typeof document !== "undefined") {
    const attr = document.documentElement.getAttribute("data-theme");
    if (attr === "light" || attr === "dark") return attr;
  }
  if (typeof localStorage !== "undefined") {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "dark" || saved === "light") return saved;
  }
  return "dark";
}

class ThemeStore {
  current = $state<Theme>(loadInitial());

  toggle() {
    this.current = this.current === "dark" ? "light" : "dark";
    if (typeof document !== "undefined") {
      document.documentElement.setAttribute("data-theme", this.current);
    }
    try {
      localStorage.setItem(STORAGE_KEY, this.current);
    } catch {
      /* private mode / disabled storage — ignore */
    }
  }
}

export const theme = new ThemeStore();
