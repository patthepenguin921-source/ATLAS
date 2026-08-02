// Flattens a class's unit/subunit folder tree (see `folders.parent_folder_id`,
// migration 0024) into an ordered, indented list suitable for a plain
// <select> -- shared by anywhere a unit/subunit needs picking outside the
// full FolderPane tree UI (e.g. the assignment "Unit" field).

export interface FolderLike {
  id: string;
  name: string;
  parent_folder_id: string | null;
}

export function buildFolderOptions(folders: FolderLike[]): { id: string; label: string }[] {
  const byParent = new Map<string | null, FolderLike[]>();
  for (const f of folders) {
    const key = f.parent_folder_id ?? null;
    if (!byParent.has(key)) byParent.set(key, []);
    byParent.get(key)!.push(f);
  }
  const opts: { id: string; label: string }[] = [];
  (function walk(parentId: string | null, depth: number) {
    for (const f of byParent.get(parentId) ?? []) {
      opts.push({ id: f.id, label: `${"— ".repeat(depth)}${f.name}` });
      walk(f.id, depth + 1);
    }
  })(null, 0);
  return opts;
}
