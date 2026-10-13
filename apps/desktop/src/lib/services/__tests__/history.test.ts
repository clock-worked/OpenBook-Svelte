import { describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { createHistory } from '../history';

function fixture() {
  const history = createHistory();
  history.activate();
  history.setScope('book');
  const files = new Map<string, string>();
  const read = (key: string) => async () => files.get(key) ?? null;
  const apply = (key: string) => async (value: string | null) => {
    if (value === null) files.delete(key);
    else files.set(key, value);
  };
  const write = (key: string, value: string | null) =>
    history.write('Edit', key, value, read(key), apply(key));
  return { history, files, read, apply, write };
}

describe('session edit history', () => {
  it('persists undo and redo, including creating and deleting files', async () => {
    const { history, files, write } = fixture();
    await write('character', 'Alice');
    await history.undo();
    expect(files.has('character')).toBe(false);
    expect(get(history).canRedo).toBe(true);
    await history.redo();
    expect(files.get('character')).toBe('Alice');
    await write('character', null);
    await history.undo();
    expect(files.get('character')).toBe('Alice');
    await history.redo();
    expect(files.has('character')).toBe(false);
  });

  it('groups multi-file edits and collapses repeated writes to the same file', async () => {
    const { history, files, write } = fixture();
    files.set('old-name', 'Alice');
    files.set('voices', 'original');
    await history.batch('Rename and assign', async () => {
      await write('new-name', 'Alicia');
      await write('old-name', null);
      await write('voices', 'intermediate');
      await write('voices', 'final');
    });
    expect(get(history).undoLabel).toBe('Rename and assign');
    await history.undo();
    expect(Object.fromEntries(files)).toEqual({ 'old-name': 'Alice', voices: 'original' });
    expect(get(history).canUndo).toBe(false);
    await history.redo();
    expect(Object.fromEntries(files)).toEqual({ 'new-name': 'Alicia', voices: 'final' });
  });

  it('clears redo on a new edit but not on a no-op', async () => {
    const { history, write } = fixture();
    await write('text', 'one');
    await write('text', 'two');
    await history.undo();
    await write('text', 'one');
    expect(get(history).canRedo).toBe(true);
    await write('text', 'three');
    expect(get(history).canRedo).toBe(false);
  });

  it('keeps exactly the latest 100 actions', async () => {
    const { history, files, write } = fixture();
    files.set('text', '0');
    for (let index = 1; index <= 105; index++) await write('text', String(index));
    for (let index = 0; index < 100; index++) await history.undo();
    expect(files.get('text')).toBe('5');
    expect(get(history).canUndo).toBe(false);
    for (let index = 0; index < 100; index++) await history.redo();
    expect(files.get('text')).toBe('105');
    expect(get(history).canRedo).toBe(false);
  });

  it('clears both stacks only when the project changes', async () => {
    const { history, write } = fixture();
    await write('text', 'one');
    history.setScope('book');
    expect(get(history).canUndo).toBe(true);
    await history.undo();
    history.setScope('different-book');
    expect(get(history).canRedo).toBe(false);
    expect(get(history).canUndo).toBe(false);
  });

  it('serializes overlapping writes and waits for saves before undo', async () => {
    const { history, files, write } = fixture();
    await Promise.all([write('text', 'one'), write('text', 'two'), history.undo()]);
    expect(files.get('text')).toBe('one');
    expect(get(history).busy).toBe(false);
    await history.undo();
    expect(files.has('text')).toBe(false);
  });

  it('refuses to overwrite external changes and retains the undo entry', async () => {
    const { history, files, write } = fixture();
    await write('text', 'one');
    files.set('text', 'external');
    await history.undo();
    expect(files.get('text')).toBe('external');
    expect(get(history).error).toContain('changed outside this history');
    expect(get(history).canUndo).toBe(true);
  });

  it('rolls back an incomplete replay and allows retrying the same entry', async () => {
    const { history, files, write, read, apply } = fixture();
    files.set('first', 'original');
    files.set('second', 'original');
    let fail = false;
    await history.batch('Two files', async () => {
      await history.write('Edit', 'first', 'edited', read('first'), async (value) => {
        if (fail) throw new Error('Permission denied');
        await apply('first')(value);
      });
      await write('second', 'edited');
    });
    fail = true;
    await history.undo();
    expect(Object.fromEntries(files)).toEqual({ first: 'edited', second: 'edited' });
    expect(get(history).canUndo).toBe(true);
    expect(get(history).canRedo).toBe(false);
    expect(get(history).error).toContain('Permission denied');
    fail = false;
    await history.undo();
    expect(Object.fromEntries(files)).toEqual({ first: 'original', second: 'original' });
  });

  it('records successful portions of a failed action, but not failed writes', async () => {
    const { history, files, write, read } = fixture();
    await expect(history.batch('Partial edit', async () => {
      await write('first', 'edited');
      await history.write('Edit', 'second', 'edited', read('second'), async () => {
        throw new Error('Disk full');
      });
    })).rejects.toThrow('Disk full');
    await history.undo();
    expect(files.size).toBe(0);
  });

  it('captures backend mutations without calling the backend again on the initial edit', async () => {
    const { history, files, read, apply } = fixture();
    files.set('metadata', 'old');
    const restore = vi.fn(apply('metadata'));
    await history.capture('Metadata', 'metadata', read('metadata'), restore, async () => {
      files.set('metadata', 'new');
      return 42;
    });
    expect(restore).not.toHaveBeenCalled();
    await history.undo();
    expect(files.get('metadata')).toBe('old');
    await history.redo();
    expect(files.get('metadata')).toBe('new');
  });

  it('excludes automatic loads and notifies views after restoring data', async () => {
    const { history, files, write } = fixture();
    await history.exclude(() => write('text', 'loaded'));
    expect(get(history).canUndo).toBe(false);
    const refresh = vi.fn(async (keys: string[]) => {
      expect(keys).toEqual(['text']);
      expect(files.get('text')).toBe('loaded');
    });
    const stop = history.onRestore(refresh);
    await write('text', 'edited');
    await history.undo();
    expect(refresh).toHaveBeenCalledOnce();
    stop();
    await history.redo();
    expect(refresh).toHaveBeenCalledOnce();
  });

  it('refreshes stores before views and marks hydration as restoration', async () => {
    const { history, write } = fixture();
    const calls: string[] = [];
    history.onRestore(async () => {
      expect(history.isRestoring).toBe(true);
      calls.push('view');
    });
    history.onRestore(async () => { calls.push('stores'); }, -1);
    await write('text', 'edited');
    await history.undo();
    expect(calls).toEqual(['stores', 'view']);
    expect(history.isRestoring).toBe(false);
    expect(get(history).canRedo).toBe(true);
  });
});
