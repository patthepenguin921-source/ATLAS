// A class split into linked semester rows (e.g. an HN-weighted S1 feeding an
// AP-weighted S2 -- see `linked_course_id`, migration 0009) is still one
// class -- group those rows together instead of showing duplicates
// wherever courses are listed (the Courses page's cards, the Documents
// page's class dividers/upload target). Shared so the grouping logic can't
// drift between the two.

export interface GroupableCourse {
  id: string;
  name: string;
  linked_course_id?: string | null;
  semester?: string;
  is_active?: boolean;
}

export interface CourseGroup<T extends GroupableCourse> {
  key: string;
  primary: T;
  members: T[];
}

export function groupCourses<T extends GroupableCourse>(list: T[]): CourseGroup<T>[] {
  const groups: CourseGroup<T>[] = [];
  const indexByKey = new Map<string, number>();
  for (const c of list) {
    const key = c.linked_course_id ?? c.id;
    const idx = indexByKey.get(key);
    if (idx === undefined) {
      indexByKey.set(key, groups.length);
      groups.push({ key, primary: c, members: [c] });
    } else {
      groups[idx].members.push(c);
      if (!c.linked_course_id) groups[idx].primary = c; // prefer the root row
    }
  }
  for (const g of groups) {
    g.members.sort((a, b) => (a.semester ?? "").localeCompare(b.semester ?? ""));
  }
  return groups;
}

// Which member of a group should be the concrete target for a new
// upload/folder -- prefer whichever semester half is still active (current),
// falling back to the group's root/primary row.
export function currentMember<T extends GroupableCourse>(g: CourseGroup<T>): T {
  return g.members.find((m) => m.is_active !== false) ?? g.primary;
}
