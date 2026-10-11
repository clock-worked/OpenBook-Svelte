// ============================================================================
// diffTree + tree-wide stale-value scan for the v3 character service-layer
// matrix (docs/character_details_test_plan.md §Strategy: "diff_tree(before,
// after) helper in both suites: every mutation test asserts changed paths ==
// expected set").
// ============================================================================

export interface TreeDiff {
  added: string[];
  deleted: string[];
  changed: string[];
}

/** Compare two in-memory-fs snapshots; every key is a book-root-relative path. */
export function diffTree(before: Record<string, string>, after: Record<string, string>): TreeDiff {
  const added: string[] = [];
  const deleted: string[] = [];
  const changed: string[] = [];

  for (const [path, content] of Object.entries(after)) {
    if (!(path in before)) added.push(path);
    else if (before[path] !== content) changed.push(path);
  }
  for (const path of Object.keys(before)) {
    if (!(path in after)) deleted.push(path);
  }

  added.sort();
  deleted.sort();
  changed.sort();
  return { added, deleted, changed };
}

/** Every path touched by a diff (added ∪ deleted ∪ changed), sorted. */
export function treePaths(diff: TreeDiff): string[] {
  return [...diff.added, ...diff.deleted, ...diff.changed].sort();
}

/**
 * Recursive walk of ALL string values (and object keys) of a parsed JSON
 * document, returning the JSON-path of every value exactly equal to one of
 * `banned` (exact, case-sensitive match — the "zero old slugs tree-wide"
 * assertion, test plan M9(b)).
 */
export function findStaleStringValues(node: unknown, banned: Iterable<string>, path = '$'): string[] {
  const bannedSet = new Set(banned);
  const hits: string[] = [];

  const walk = (value: unknown, current: string): void => {
    if (typeof value === 'string') {
      if (bannedSet.has(value)) hits.push(current);
      return;
    }
    if (Array.isArray(value)) {
      value.forEach((item, index) => walk(item, `${current}[${index}]`));
      return;
    }
    if (value && typeof value === 'object') {
      for (const [key, child] of Object.entries(value)) {
        if (bannedSet.has(key)) hits.push(`${current}.${key}`);
        walk(child, `${current}.${key}`);
      }
    }
  };

  walk(node, path);
  return hits;
}

/**
 * Tree-wide stale-value scan over an in-memory-fs snapshot: parse every file
 * and report every string value / object key exactly equal to a banned slug.
 * `excludePrefix` skips whole subtrees (e.g. `_backups/`, which legitimately
 * retains pre-images).
 */
export function staleValuesInTree(
  snapshot: Record<string, string>,
  banned: Iterable<string>,
  options: { excludePrefix?: string } = {},
): string[] {
  const hits: string[] = [];
  for (const [path, text] of Object.entries(snapshot)) {
    if (options.excludePrefix && path.startsWith(options.excludePrefix)) continue;
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      continue; // non-JSON file: nothing to scan
    }
    hits.push(...findStaleStringValues(parsed, banned, path));
  }
  return hits;
}
