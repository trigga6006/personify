/* Right-side slide-in detail panel. Holds either an item id or an entity id;
 * never both. Routes call `detail.openItem(id)` / `detail.openEntity(id)` /
 * `detail.close()`. Components derive their visibility off `detail.kind`.
 */

export type DetailKind = "item" | "entity" | null;

let kind = $state<DetailKind>(null);
let id = $state<number | null>(null);

export const detail = {
  get kind() {
    return kind;
  },
  get id() {
    return id;
  },
  get open() {
    return kind !== null;
  },
  openItem(itemId: number) {
    kind = "item";
    id = itemId;
  },
  openEntity(entityId: number) {
    kind = "entity";
    id = entityId;
  },
  close() {
    kind = null;
    id = null;
  },
};
