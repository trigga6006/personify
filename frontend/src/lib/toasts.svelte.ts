export type ToastVariant = "info" | "ok" | "err";
interface Toast {
  id: number;
  text: string;
  variant: ToastVariant;
  ttl: number;
}

let items = $state<Toast[]>([]);
let nextId = 1;

function push(text: string, variant: ToastVariant = "info", ttlMs = 4000) {
  const id = nextId++;
  items = [...items, { id, text, variant, ttl: ttlMs }];
  setTimeout(() => {
    items = items.filter((t) => t.id !== id);
  }, ttlMs);
}

export const toasts = {
  get items() {
    return items;
  },
  push,
  info: (t: string) => push(t, "info"),
  ok: (t: string) => push(t, "ok"),
  err: (t: string) => push(t, "err"),
};
