export const SYMBOL_RE = /^[A-Za-z0-9.\-]{1,12}$/;

export function slugId(name: string, used: Set<string>): string {
  let base = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "list";
  base = base.slice(0, 32).replace(/-+$/g, "") || "list";
  let id = base;
  let n = 2;
  while (used.has(id)) {
    const suffix = `-${n}`;
    id = `${base.slice(0, 32 - suffix.length)}${suffix}`;
    n += 1;
  }
  return id;
}
