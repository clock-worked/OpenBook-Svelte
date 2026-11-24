import { describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';

import { audioQueue } from '../audioQueue';
import { createQueueJob } from '$lib/services/audioQueueJob';
import type { PlannedQueueJob } from '$lib/services/audioQueuePlanning';

function planned(characterId: string): PlannedQueueJob {
  return {
    jobIdBase: `${characterId}-missing`,
    displayName: characterId,
    characterId,
    characterName: characterId,
    lines: [{ id: 1, text: 'hello', chapterTitle: 'Ch1' } as any],
    cumulativeCharacterCounts: [5],
    totalCharacters: 5,
    voiceInfo: { voiceId: 'v1', provider: 'vibevoice', displayName: 'Voice', characterId },
    chapterTitle: 'Ch1',
    sourceFile: 'Ch1/chapter.txt',
  };
}

describe('audioQueue store', () => {
  it('applies transitions and refuses illegal ones without changing the queue', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const a = createQueueJob(planned('A'), { id: 'a', generationMode: 'missing', createdAt: 1 });
    const b = createQueueJob(planned('B'), { id: 'b', generationMode: 'missing', createdAt: 1 });

    expect(audioQueue.enqueue([a, b])).toBe(true);
    expect(audioQueue.next()?.id).toBe('a');
    expect(audioQueue.start('a', a.plan!)).toBe(true);

    const before = get(audioQueue);
    expect(audioQueue.start('b', b.plan!)).toBe(false); // one runner
    expect(audioQueue.remove('a')).toBe(false); // running jobs are not removable
    expect(get(audioQueue)).toBe(before);
    expect(warn).toHaveBeenCalled();

    expect(audioQueue.progress('a', { linesDone: 1 })).toBe(true);
    expect(get(audioQueue).items[0].progress.charactersDone).toBe(5);

    audioQueue.cancel();
    expect(audioQueue.isCancelRequested('a')).toBe(true);
    expect(get(audioQueue).items[1].status).toBe('canceled');
    expect(audioQueue.finish('a', { kind: 'cancel' })).toBe(true);
    expect(audioQueue.next()).toBeNull();

    audioQueue.clearFinished();
    expect(get(audioQueue).items).toEqual([]);
    warn.mockRestore();
  });
});
