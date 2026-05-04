/* Modal stack. One at a time is enough for this app; the API still uses a
 * stack so a modal can open another (rare but supported, e.g. confirm dialog).
 */

export type ModalName = "add-export" | "create-vault" | null;

let stack = $state<ModalName[]>([]);

export const modal = {
  get current(): ModalName { return stack[stack.length - 1] ?? null; },
  open(name: Exclude<ModalName, null>) { stack = [...stack, name]; },
  close() { stack = stack.slice(0, -1); },
  closeAll() { stack = []; },
};
