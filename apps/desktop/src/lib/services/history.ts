import { writable } from 'svelte/store';

export interface HistoryChange {
  key: string;
  before: string | null;
  after: string | null;
  read: () => Promise<string | null>;
  apply: (value: string | null) => Promise<void>;
  equivalent?: (left: string | null, right: string | null) => boolean;
}

interface HistoryEntry {
  label: string;
  changes: HistoryChange[];
}

export function createHistory(limit = 100) {
  const state = writable({
    canUndo: false, canRedo: false, busy: false,
    undoLabel: '', redoLabel: '', error: '',
  });
  const undoStack: HistoryEntry[] = [];
  const redoStack: HistoryEntry[] = [];
  const refreshers = new Map<(keys: string[]) => Promise<void>, number>();
  let active: HistoryEntry | null = null;
  let tail: Promise<unknown> = Promise.resolve();
  let pending = 0;
  let enabled = false;
  let scope: unknown;
  let generation = 0;
  let error = '';
  let excluded = 0;
  let restoring = false;

  function publish() {
    state.set({
      canUndo: undoStack.length > 0,
      canRedo: redoStack.length > 0,
      busy: pending > 0,
      undoLabel: undoStack.at(-1)?.label ?? '',
      redoLabel: redoStack.at(-1)?.label ?? '',
      error,
    });
  }

  function enqueue<T>(operation: () => Promise<T>): Promise<T> {
    pending++;
    error = '';
    publish();
    const result = tail.then(operation);
    tail = result.catch(() => {});
    return result.finally(() => {
      pending--;
      publish();
    });
  }

  function commit(entry: HistoryEntry, version: number) {
    if (!enabled || version !== generation) return;
    entry.changes = entry.changes.filter((change) =>
      !(change.equivalent?.(change.before, change.after) ?? change.before === change.after));
    if (!entry.changes.length) return;
    undoStack.push(entry);
    if (undoStack.length > limit) undoStack.shift();
    redoStack.length = 0;
    publish();
  }

  async function record(change: HistoryChange, label: string): Promise<void> {
    const version = generation;
    const previous = active?.changes.find((item) => item.key === change.key);
    await change.apply(change.after);
    if (!enabled || version !== generation) return;
    if (active) {
      if (previous) previous.after = change.after;
      else active.changes.push(change);
    } else {
      commit({ label, changes: [change] }, version);
    }
  }

  async function write(
    label: string,
    key: string,
    after: string | null,
    read: HistoryChange['read'],
    apply: HistoryChange['apply'],
    equivalent?: HistoryChange['equivalent'],
  ): Promise<void> {
    const version = generation;
    const operation = async () => {
      if (version !== generation) throw new Error('The project changed before the edit could be saved.');
      if (!enabled || excluded > 0) {
        await apply(after);
        return;
      }
      const before = await read();
      if (before === after) return;
      await record({ key, before, after, read, apply, equivalent }, label);
    };
    if (active) await operation();
    else await enqueue(operation);
  }

  async function batch<T>(label: string, operation: () => Promise<T>): Promise<T> {
    if (active) return operation();
    const version = generation;
    return enqueue(async () => {
      if (version !== generation) throw new Error('The project changed before the edit could be saved.');
      const entry: HistoryEntry = { label, changes: [] };
      active = entry;
      try {
        return await operation();
      } catch (failure) {
        error = failure instanceof Error ? failure.message : String(failure);
        console.error('[history]', error, failure);
        throw failure;
      } finally {
        active = null;
        // A partially successful edit must remain undoable even when its caller fails.
        commit(entry, version);
      }
    });
  }

  async function capture<T>(
    label: string,
    key: string,
    read: HistoryChange['read'],
    apply: HistoryChange['apply'],
    operation: () => Promise<T>,
    equivalent?: HistoryChange['equivalent'],
  ): Promise<T> {
    return batch(label, async () => {
      const version = generation;
      const before = await read();
      try {
        return await operation();
      } finally {
        const after = await read();
        if (enabled && !excluded && version === generation && active && before !== after) {
          const previous = active.changes.find((change) => change.key === key);
          if (previous) previous.after = after;
          else active.changes.push({ key, before, after, read, apply, equivalent });
        }
      }
    });
  }

  async function replay(direction: 'undo' | 'redo'): Promise<void> {
    await enqueue(async () => {
      const source = direction === 'undo' ? undoStack : redoStack;
      const destination = direction === 'undo' ? redoStack : undoStack;
      const entry = source.at(-1);
      if (!entry) return;
      const changes = direction === 'undo' ? [...entry.changes].reverse() : entry.changes;
      const applied: HistoryChange[] = [];
      try {
        // Check every file before changing any of them; never overwrite external edits.
        for (const change of changes) {
          const expected = direction === 'undo' ? change.after : change.before;
          const current = await change.read();
          if (!(change.equivalent?.(current, expected) ?? current === expected)) {
            throw new Error(`Cannot ${direction}: ${change.key} changed outside this history. Reopen the project to start fresh.`);
          }
        }
        for (const change of changes) {
          await change.apply(direction === 'undo' ? change.before : change.after);
          applied.push(change);
        }
      } catch (failure) {
        const rollbackErrors: string[] = [];
        for (const change of applied.reverse()) {
          try {
            await change.apply(direction === 'undo' ? change.after : change.before);
          } catch (rollbackError) {
            rollbackErrors.push(`${change.key}: ${String(rollbackError)}`);
          }
        }
        error = failure instanceof Error ? failure.message : String(failure);
        if (rollbackErrors.length) {
          error += ` Recovery also failed: ${rollbackErrors.join('; ')}. Check the project files before continuing.`;
          undoStack.length = 0;
          redoStack.length = 0;
        }
        console.error('[history]', error, failure);
        publish();
        return;
      }
      source.pop();
      destination.push(entry);
      error = '';
      restoring = true;
      try {
        const keys = entry.changes.map((change) => change.key);
        for (const [refresh] of [...refreshers].sort((a, b) => a[1] - b[1])) await refresh(keys);
      } catch (failure) {
        error = `Data restored, but the view could not refresh: ${String(failure)}`;
        console.error('[history]', error, failure);
      } finally {
        restoring = false;
      }
      publish();
    });
  }

  return {
    subscribe: state.subscribe,
    write,
    batch,
    capture,
    get isRestoring() { return restoring; },
    undo: () => replay('undo'),
    redo: () => replay('redo'),
    activate() { enabled = true; },
    setScope(next: unknown) {
      if (scope === next) return;
      scope = next;
      generation++;
      undoStack.length = 0;
      redoStack.length = 0;
      error = '';
      publish();
    },
    clear() {
      generation++;
      undoStack.length = 0;
      redoStack.length = 0;
      error = '';
      publish();
    },
    onRestore(refresh: (keys: string[]) => Promise<void>, priority = 0) {
      refreshers.set(refresh, priority);
      return () => { refreshers.delete(refresh); };
    },
    async exclude<T>(operation: () => Promise<T>): Promise<T> {
      return batch('', async () => {
        excluded++;
        try {
          return await operation();
        } finally {
          excluded--;
        }
      });
    },
    reportError(failure: unknown) {
      error = failure instanceof Error ? failure.message : String(failure);
      console.error('[history]', error, failure);
      publish();
    },
  };
}

export const history = createHistory();

export function historyAction<Args extends unknown[], Result>(
  label: string,
  operation: (...args: Args) => Promise<Result>,
): (...args: Args) => Promise<Result> {
  return (...args) => history.batch(label, () => operation(...args));
}
